"""
OCRワーカー本体

常駐してTFTの起動を待ち受け、TFTウィンドウをWGCでキャプチャしながら：
  1. 名前画像テンプレート照合で各行が誰かを特定（初回のみEasyOCRで登録）
  2. 各プレイヤーのHPをtesseractで読み取り（一括読みで高速）
  3. 「誰が・画面のどのY位置で・何HPか」を http://127.0.0.1:<port>/stats で配信
名前リストはホーム画面から POST /config で受け取る。
チーム分け・色・合計・生存判定はすべてOverwolf側が担う。

実行:
    python worker.py     （Ctrl+Cで終了。自動起動はREADME参照）
"""

import json
import os
import sys
import time
from datetime import datetime

from PIL import Image
from windows_capture import WindowsCapture, Frame, InternalCaptureControl

import ocr_engine
from stats_server import start_stats_server

# list_windows.py で確認した正確なタイトルに合わせる
WINDOW_NAME = "League of Legends (TM) Client"

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
OUTPUT_PATH = os.path.join(HERE, "output.json")
LOG_PATH = os.path.join(HERE, "worker.log")

EXPECTED_SIZE = (1920, 1080)

# set DEBUG=1 で、フレーム保存と生OCR結果の表示を有効化
DEBUG = os.environ.get("DEBUG") == "1"


class _Tee:
    """print出力をコンソールとログファイルの両方へ流す。"""

    def __init__(self, stream, logfile):
        self.stream = stream
        self.logfile = logfile

    def write(self, data):
        if self.stream:  # pythonw起動時は stdout が None のことがある
            self.stream.write(data)
        self.logfile.write(data)
        self.logfile.flush()

    def flush(self):
        if self.stream:
            self.stream.flush()
        self.logfile.flush()


def setup_logging():
    """標準出力・標準エラーをworker.logにも記録する（毎回上書き）。"""
    logfile = open(LOG_PATH, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, logfile)
    sys.stderr = _Tee(sys.stderr, logfile)
    print(f"ログ出力先: {LOG_PATH}")


DEFAULT_CONFIG = {
    # 照合対象の全プレイヤー名（チーム区別なし）。チーム分け・色はOverwolf側が持つ。
    "names": [],
    "interval_seconds": 3,
    "fast_interval_seconds": 0.5,
    "http_port": 17653,
}


def extract_names(payload):
    """設定ペイロードから名前リストを取り出す。
    新形式の {"names": [...]} を優先し、旧形式の teamA/teamB.members にも対応する。"""
    if isinstance(payload.get("names"), list):
        return [str(n) for n in payload["names"] if n]
    names = []
    for key in ("teamA", "teamB"):
        team = payload.get(key)
        if isinstance(team, dict):
            for m in team.get("members", []):
                # members は文字列 or {"name": ...} のどちらもあり得る
                name = m.get("name") if isinstance(m, dict) else m
                if name:
                    names.append(str(name))
    return names


def load_config():
    """config.json を読み込む。なければ既定値（名前空）で起動し、ホーム画面からの設定を待つ。"""
    if not os.path.exists(CONFIG_PATH):
        print("config.json がありません。ホーム画面からの設定を待ちます。")
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    config = {**DEFAULT_CONFIG, **raw}
    # 旧形式（teamA/teamB）のconfig.jsonからも名前を拾えるようにする
    if not config.get("names"):
        config["names"] = extract_names(raw)
    return config


def save_config(config):
    """config を config.json に書き出す。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# テンプレート照合で行をプレイヤーに割り当てる最低類似度
MIN_MATCH_NCC = 0.30
# 初期化時にEasyOCRの照合を採用する最低スコア（誤テンプレート防止のため高め）
INIT_MATCH_SCORE = 0.55
# この回数フレームを試しても全員揃わなければ、揃った分だけで高速モードに移る
MAX_INIT_FRAMES = 6


class PlayerTracker:
    """
    名前画像テンプレート照合で各行が誰かを特定し、HPと表示位置を保持する。

    - 初期化フェーズ: EasyOCRで名前を読んで登録名と照合し、各プレイヤーの
      「名前画像」をテンプレートとして記憶する（全員揃うまで毎フレーム試行）。
    - 通常フェーズ: 各行の名前画像をテンプレートと照合（OCR不要・高速）。
      並び替えに対応するため、行とプレイヤーを1対1で割り当てる。

    出力は「誰が・画面のどのY位置で・何HPか」だけ。チーム分け・色・合計・
    生存判定はOverwolf側が担うため、ここでは一切扱わない。
    """

    def __init__(self, config):
        self.init_score = config.get("init_match_score", INIT_MATCH_SCORE)
        # 名前リストはスナップショットを持つ（外部のconfig辞書が後で書き換わっても影響を受けない）
        self.names = list(config.get("names", []))
        self.last_hp = {m: None for m in self.names}   # name -> HP
        self.last_y = {m: None for m in self.names}    # name -> 画面上のY中心
        self.templates = {}        # name -> 名前画像マスク
        self.initialized = False   # 全員のテンプレートが揃ったか
        self.init_frames = 0       # 初期化を試したフレーム数

    def inherit(self, prev):
        """前のトラッカーから、引き続き存在する名前のテンプレート・HP・位置を引き継ぐ。
        設定更新（メンバー追加など）のたびに全員分のEasyOCRをやり直さないため。"""
        if not prev:
            return
        for name in self.names:
            if name in prev.templates:
                self.templates[name] = prev.templates[name]
            if prev.last_hp.get(name) is not None:
                self.last_hp[name] = prev.last_hp[name]
            if prev.last_y.get(name) is not None:
                self.last_y[name] = prev.last_y[name]
        if self.names and len(self.templates) >= len(self.names):
            self.initialized = True  # 全員のテンプレが揃っていれば即高速モード

    def update(self, img):
        """1フレームを処理し、プレイヤーごとの位置・HPを返す。"""
        # 各スロットの名前マスクと中身の有無
        masks = [ocr_engine.name_mask(img, cy) for cy in ocr_engine.ROW_CENTERS]
        non_empty = [
            i for i, m in enumerate(masks) if not ocr_engine.mask_is_empty(m)
        ]

        if not self.initialized:
            self._init_templates(img, masks, non_empty)

        # 行→プレイヤーの割り当て（テンプレートがある分だけ）
        assign = self._assign(masks, non_empty)

        # 割り当てた行のHPを一括で読んで更新（位置も記録）
        hps = ocr_engine.read_hps(img, list(assign.keys()))
        for slot, name in assign.items():
            self.last_y[name] = ocr_engine.ROW_CENTERS[slot]
            if hps.get(slot) is not None:
                self.last_hp[name] = hps[slot]

        return assign, {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "initialized": self.initialized,
            "players": self._players(),
        }

    def _players(self):
        """全プレイヤーの最新の位置・HPを返す。
        y は画面上のY中心(1920x1080基準)で、未取得なら null。HPも未取得なら null。"""
        return [
            {"name": name, "y": self.last_y.get(name), "hp": self.last_hp.get(name)}
            for name in self.names
        ]

    def _init_templates(self, img, masks, non_empty):
        """EasyOCRで名前を読み、未取得プレイヤーのテンプレートを保存する。"""
        self.init_frames += 1

        # 既に登録済みテンプレートにNCCで一致する行は、EasyOCRを省略する。
        # （init中は毎フレーム全行をOCRすると激遅。登録済みの行は読み直さない）
        known_slots = set()
        for slot in non_empty:
            for tmpl in self.templates.values():
                if ocr_engine.ncc(masks[slot], tmpl) >= MIN_MATCH_NCC:
                    known_slots.add(slot)
                    break

        claimed = set(known_slots)  # OCR/テンプレでいずれかのメンバーに対応した行
        for slot in non_empty:
            if slot in known_slots:
                continue  # 既知の行はOCR不要
            raw = ocr_engine.read_name(img, ocr_engine.ROW_CENTERS[slot])
            cand, score = ocr_engine.fuzzy_match(raw, self.names)
            if score >= self.init_score and cand and cand not in self.templates:
                claimed.add(slot)
                self.templates[cand] = masks[slot]
                print(f"  テンプレート登録: {cand}（OCR:[{raw}] 類似{score:.2f}）")
            else:
                # 不一致の理由を診断できるよう、生OCRと最良候補を残す
                print(f"  未照合 slot{slot}: OCR=[{raw}] 最良=[{cand}] 類似{score:.2f}")

        # 消去法: 未登録メンバーと、どれにも照合しなかった行がそれぞれ1つなら確定
        # （OCRが苦手な名前＝例: 短い英字 でも、他が揃えば埋められる）
        missing = [m for m in self.names if m not in self.templates]
        free = [s for s in non_empty if s not in claimed]
        if len(missing) == 1 and len(free) == 1:
            self.templates[missing[0]] = masks[free[0]]
            print(f"  テンプレート登録(消去法): {missing[0]}")

        if len(self.templates) >= len(self.names):
            self.initialized = True
            print("テンプレート照合の準備完了。以降は高速モードで動作します。")
        elif self.init_frames >= MAX_INIT_FRAMES:
            self.initialized = True
            missing = [m for m in self.names if m not in self.templates]
            print(f"テンプレート照合を開始します（未登録: {missing}）。")
            print("  ※ 未登録プレイヤーはHP未取得になります。名前設定を確認してください。")

    def _assign(self, masks, non_empty):
        """非空行をテンプレートに1対1で貪欲割り当て。{slot: name} を返す。"""
        pairs = []
        for slot in non_empty:
            for name, tmpl in self.templates.items():
                pairs.append((ocr_engine.ncc(masks[slot], tmpl), slot, name))
        pairs.sort(reverse=True)

        used_slots, used_members, assign = set(), set(), {}
        for sim, slot, name in pairs:
            if sim < MIN_MATCH_NCC:
                break
            if slot in used_slots or name in used_members:
                continue
            assign[slot] = name
            used_slots.add(slot)
            used_members.add(name)

        # 残った行とプレイヤーがそれぞれ1つだけなら、消去法で確定させる
        # （金枠などで1人だけ照合が閾値を割っても、候補が1つなので埋められる）
        rest_slots = [s for s in non_empty if s not in used_slots]
        rest_members = [m for m in self.templates if m not in used_members]
        if len(rest_slots) == 1 and len(rest_members) == 1:
            assign[rest_slots[0]] = rest_members[0]

        return assign


def frame_to_image(frame: Frame) -> Image.Image:
    """WGCフレーム(BGRA numpy)をPIL RGB画像に変換。"""
    buf = frame.frame_buffer  # (H, W, 4) BGRA
    rgb = buf[:, :, [2, 1, 0]]  # BGR→RGB（アルファ除外）
    return Image.fromarray(rgb)


def window_exists(name):
    """指定タイトルを含むウィンドウが存在するか。"""
    try:
        import pygetwindow as gw
        return any(name in t for t in gw.getAllTitles() if t)
    except Exception:
        return False


def wait_for_window(name, poll=2):
    """指定ウィンドウが現れるまで待つ（Ctrl+Cで中断可能）。"""
    while not window_exists(name):
        time.sleep(poll)


def main():
    setup_logging()
    ocr_engine.setup_tessdata()  # tesseractとtessdataを解決（ログに記録される）
    config = load_config()
    # 初期化中（EasyOCRで重い）と高速モードで間隔を分ける
    init_interval = config.get("interval_seconds", 3)
    fast_interval = config.get("fast_interval_seconds", 0.5)
    http_port = config.get("http_port", 17653)
    print(f"設定読み込み完了。初期化中{init_interval}秒 / 高速モード{fast_interval}秒間隔。")
    print(f"ウィンドウ: '{WINDOW_NAME}'")

    # EasyOCRのモデル読み込みは重い。初回フレームの待ち時間にならないよう、
    # TFT待ちの間に先にロードしておく。
    print("EasyOCRを準備中…（初回のみ時間がかかります）")
    t0 = time.time()
    ocr_engine.get_reader()
    print(f"EasyOCR準備完了（{time.time() - t0:.1f}秒）")

    latest = {"stats": {"initialized": False}}
    state = {"last": 0.0, "warned_size": False, "tracker": PlayerTracker(config)}

    def on_config(payload):
        """ホーム画面から送られた名前リストを反映し、config.jsonを更新する。"""
        new_names = extract_names(payload)
        if new_names == config.get("names"):
            return config  # 同じ内容の再送はトラッカーを作り直さない（再OCR回避）
        config["names"] = new_names
        # チーム分け・色はOverwolf側が持つので、ここには残さない
        config.pop("teamA", None)
        config.pop("teamB", None)
        save_config(config)
        # 新しい名前リストでトラッカーを作り直すが、続投する名前のテンプレ等は引き継ぐ
        tracker = PlayerTracker(config)
        tracker.inherit(state["tracker"])
        state["tracker"] = tracker
        print(f"設定を更新しました（{len(new_names)}人）。"
              f"テンプレート保持: {len(tracker.templates)}人")
        return config

    # 最新の集計をHTTPで配信し、設定POSTも受け付ける（Overwolfと連携）
    start_stats_server(http_port, lambda: latest["stats"], on_config)
    print(f"HTTP配信: http://127.0.0.1:{http_port}/stats （POST /config で設定更新）")

    def process_frame(frame):
        """1フレームを処理して集計・配信する。"""
        tracker = state["tracker"]
        # フレームは高頻度で届くので、一定間隔に1回だけ処理する
        # 高速モード（テンプレ準備完了後）は短い間隔で回す
        interval = fast_interval if tracker.initialized else init_interval
        now = time.time()
        if now - state["last"] < interval:
            return
        state["last"] = now

        img = frame_to_image(frame)
        if img.size != EXPECTED_SIZE and not state["warned_size"]:
            print(f"警告: 解像度が {img.size} です（{EXPECTED_SIZE}前提）。座標がずれる可能性。")
            state["warned_size"] = True

        # デバッグモード（set DEBUG=1）: 最初のフレームを保存し、割り当てを表示
        if DEBUG and not state.get("saved_frame"):
            dbg_path = os.path.join(HERE, "debug_frame.png")
            img.save(dbg_path)
            print(f"デバッグ: フレームを保存しました → {dbg_path}（解像度 {img.size}）")
            state["saved_frame"] = True

        # OCR処理は例外で全体を落とさず、詳細をログに残して継続する
        try:
            t0 = time.time()
            assign, stats = tracker.update(img)
            if DEBUG:
                print("--- 行→プレイヤー割り当て ---")
                for slot in range(len(ocr_engine.ROW_CENTERS)):
                    member = assign.get(slot, "（なし）")
                    hp = tracker.last_hp.get(member) if slot in assign else None
                    print(f"  slot{slot}: {member}  HP=[{hp}]")
            dt = time.time() - t0
        except Exception:
            import traceback
            tb = traceback.format_exc()
            print("OCR処理でエラーが発生しました（詳細は worker_error.log）:")
            print(tb)
            with open(os.path.join(HERE, "worker_error.log"), "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().isoformat()} ---\n{tb}\n")
            return

        # HTTP配信用に最新の集計を更新し、ファイルにも残す
        latest["stats"] = stats
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        players = stats["players"]
        known = sum(1 for p in players if p["hp"] is not None)
        print(
            f"[{stats['updated']}] サイクル {dt:.2f}秒  "
            f"| HP読取 {known}/{len(players)}人"
        )
        if dt > interval:
            print(f"  ※ サイクル({dt:.2f}秒)が間隔({interval}秒)を超過しています")

    def run_session():
        """TFTウィンドウをキャプチャする（TFTが閉じるまでブロックする）。"""
        capture = WindowsCapture(
            cursor_capture=None,
            draw_border=None,
            monitor_index=None,
            window_name=WINDOW_NAME,
        )

        @capture.event
        def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
            process_frame(frame)

        @capture.event
        def on_closed():
            print("キャプチャを終了しました")

        capture.start()

    # 常駐ループ: TFTを待ち受け、起動中はキャプチャ、終了したら待機に戻る
    print("TFTの起動を待っています…（Ctrl+Cで終了）")
    try:
        while True:
            wait_for_window(WINDOW_NAME)
            print("TFTを検出。キャプチャを開始します。")
            # 新しい試合用に状態をリセット（テンプレートを取り直す）
            state["last"] = 0.0
            state["warned_size"] = False
            state.pop("saved_frame", None)
            state["tracker"] = PlayerTracker(config)
            try:
                run_session()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # Ctrl+Cはライブラリ内で別例外に包まれることがあるので文字列で判定
                if "KeyboardInterrupt" in str(e):
                    raise KeyboardInterrupt
                print(f"キャプチャが停止しました: {e}")
            print("TFTの終了を待っています…")
            time.sleep(3)
    except KeyboardInterrupt:
        print("終了します。")


if __name__ == "__main__":
    main()
