"""
OCRワーカー本体

常駐してTFTの起動を待ち受け、TFTウィンドウをWGCでキャプチャしながら：
  1. 名前画像テンプレート照合で各行が誰かを特定（初回のみEasyOCRで登録）
  2. 各プレイヤーのHPをtesseractで読み取り（一括読みで高速）
  3. チームごとの合計HPを集計
  4. http://127.0.0.1:<port>/stats で配信（Overwolfオーバーレイが取得）
チーム設定はホーム画面から POST /config で受け取る。生存判定はOverwolf GEP側。

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
    "teamA": {"name": "Team A", "members": [], "color": "#4a90d9"},
    "teamB": {"name": "Team B", "members": [], "color": "#d9604a"},
    "match_threshold": 0.45,
    "interval_seconds": 3,
    "fast_interval_seconds": 0.5,
    "http_port": 17653,
}


def load_config():
    """config.json を読み込む。なければ既定値（メンバー空）で起動し、ホーム画面からの設定を待つ。"""
    if not os.path.exists(CONFIG_PATH):
        print("config.json がありません。ホーム画面からの設定を待ちます。")
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return {**DEFAULT_CONFIG, **json.load(f)}


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
    名前画像テンプレート照合で各行が誰かを特定し、HPを保持する。

    - 初期化フェーズ: EasyOCRで名前を読んで登録名と照合し、各プレイヤーの
      「名前画像」をテンプレートとして記憶する（全員揃うまで毎フレーム試行）。
    - 通常フェーズ: 各行の名前画像をテンプレートと照合（OCR不要・高速）。
      並び替えに対応するため、行とプレイヤーを1対1で割り当てる。
    生存判定はOverwolf GEPに任せ、ここはHP読み取りに専念する。
    """

    def __init__(self, config):
        self.init_score = config.get("init_match_score", INIT_MATCH_SCORE)
        # 設定はスナップショットを持つ（外部のconfig辞書が後で書き換わっても影響を受けない）
        self.team_a = {"name": config["teamA"]["name"],
                       "members": list(config["teamA"]["members"]),
                       "color": config["teamA"].get("color", "#4a90d9")}
        self.team_b = {"name": config["teamB"]["name"],
                       "members": list(config["teamB"]["members"]),
                       "color": config["teamB"].get("color", "#d9604a")}
        self.all_members = self.team_a["members"] + self.team_b["members"]
        self.last_hp = {m: None for m in self.all_members}   # member -> HP
        self.templates = {}        # member -> 名前画像マスク
        self.initialized = False   # 全員のテンプレートが揃ったか
        self.init_frames = 0       # 初期化を試したフレーム数

    def update(self, img):
        """1フレームを処理し、チームごとのHP集計を返す。"""
        # 各スロットの名前マスクと中身の有無
        masks = [ocr_engine.name_mask(img, cy) for cy in ocr_engine.ROW_CENTERS]
        non_empty = [
            i for i, m in enumerate(masks) if not ocr_engine.mask_is_empty(m)
        ]

        if not self.initialized:
            self._init_templates(img, masks, non_empty)

        # 行→プレイヤーの割り当て（テンプレートがある分だけ）
        assign = self._assign(masks, non_empty)

        # 割り当てた行のHPを一括で読んで更新
        hps = ocr_engine.read_hps(img, list(assign.keys()))
        for slot, member in assign.items():
            if hps.get(slot) is not None:
                self.last_hp[member] = hps[slot]

        return assign, {
            "updated": datetime.now().isoformat(timespec="seconds"),
            "initialized": self.initialized,
            "teamA": self._team_stat(self.team_a),
            "teamB": self._team_stat(self.team_b),
        }

    def _init_templates(self, img, masks, non_empty):
        """EasyOCRで名前を読み、未取得プレイヤーのテンプレートを保存する。"""
        self.init_frames += 1
        claimed = set()  # OCRでいずれかのメンバーに照合できた行
        for slot in non_empty:
            raw = ocr_engine.read_name(img, ocr_engine.ROW_CENTERS[slot])
            cand, score = ocr_engine.fuzzy_match(raw, self.all_members)
            if score >= self.init_score and cand:
                claimed.add(slot)
                if cand not in self.templates:
                    self.templates[cand] = masks[slot]
                    print(f"  テンプレート登録: {cand}（OCR:[{raw}] 類似{score:.2f}）")

        # 消去法: 未登録メンバーと、どれにも照合しなかった行がそれぞれ1つなら確定
        # （OCRが苦手な名前＝例: 短い英字 でも、他が揃えば埋められる）
        missing = [m for m in self.all_members if m not in self.templates]
        free = [s for s in non_empty if s not in claimed]
        if len(missing) == 1 and len(free) == 1:
            self.templates[missing[0]] = masks[free[0]]
            print(f"  テンプレート登録(消去法): {missing[0]}")

        if len(self.templates) >= len(self.all_members):
            self.initialized = True
            print("テンプレート照合の準備完了。以降は高速モードで動作します。")
        elif self.init_frames >= MAX_INIT_FRAMES:
            self.initialized = True
            missing = [m for m in self.all_members if m not in self.templates]
            print(f"テンプレート照合を開始します（未登録: {missing}）。")
            print("  ※ 未登録プレイヤーはHP未取得になります。config.jsonの名前を確認してください。")

    def _assign(self, masks, non_empty):
        """非空行をテンプレートに1対1で貪欲割り当て。{slot: member} を返す。"""
        pairs = []
        for slot in non_empty:
            for member, tmpl in self.templates.items():
                pairs.append((ocr_engine.ncc(masks[slot], tmpl), slot, member))
        pairs.sort(reverse=True)

        used_slots, used_members, assign = set(), set(), {}
        for sim, slot, member in pairs:
            if sim < MIN_MATCH_NCC:
                break
            if slot in used_slots or member in used_members:
                continue
            assign[slot] = member
            used_slots.add(slot)
            used_members.add(member)

        # 残った行とプレイヤーがそれぞれ1つだけなら、消去法で確定させる
        # （金枠などで1人だけ照合が閾値を割っても、候補が1つなので埋められる）
        rest_slots = [s for s in non_empty if s not in used_slots]
        rest_members = [m for m in self.templates if m not in used_members]
        if len(rest_slots) == 1 and len(rest_members) == 1:
            assign[rest_slots[0]] = rest_members[0]

        return assign

    def _team_stat(self, team):
        members = []
        total_hp = 0
        known = 0
        for m in team["members"]:
            hp = self.last_hp.get(m)
            if hp is not None:
                total_hp += hp
                known += 1
            members.append({"name": m, "hp": hp})
        return {
            "name": team["name"],
            "color": team["color"],
            "totalHp": total_hp,
            "hpKnown": known,  # HPが取得できている人数（生存数ではない）
            "members": members,
        }


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

    latest = {"stats": {"initialized": False}}
    state = {"last": 0.0, "warned_size": False, "tracker": PlayerTracker(config)}

    def on_config(team_config):
        """ホーム画面から送られたチーム設定を反映し、config.jsonを更新する。"""
        config["teamA"] = team_config.get("teamA", config["teamA"])
        config["teamB"] = team_config.get("teamB", config["teamB"])
        save_config(config)
        # 新しいロスターでトラッカーを作り直す（テンプレートを取り直す）
        state["tracker"] = PlayerTracker(config)
        n = len(config["teamA"]["members"]) + len(config["teamB"]["members"])
        print(f"設定を更新しました（{n}人）。テンプレートを再取得します。")
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

        a, b = stats["teamA"], stats["teamB"]
        print(
            f"[{stats['updated']}] サイクル {dt:.2f}秒  "
            f"| {a['name']}: HP{a['totalHp']} (読取{a['hpKnown']}/4)  "
            f"| {b['name']}: HP{b['totalHp']} (読取{b['hpKnown']}/4)"
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
