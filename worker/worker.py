"""
OCRワーカー本体（案Y: 毎回フルOCR・3秒間隔）

TFTウィンドウをWGCで継続キャプチャし、一定間隔で：
  1. 最新フレームから8行の名前・HPを読み取り
  2. 登録プレイヤー（config.json）とファジー照合
  3. チームごとの合計HP・生存人数を集計
  4. 結果を output.json に書き出し（後でOverwolfがポーリング）
  5. 1サイクルの所要時間を表示（実機が3秒に間に合うかの確認用）

実行:
    python worker.py
"""

import json
import os
import sys
import time
from datetime import datetime

from PIL import Image
from windows_capture import WindowsCapture, Frame, InternalCaptureControl

import ocr_engine

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
        self.stream.write(data)
        self.logfile.write(data)
        self.logfile.flush()

    def flush(self):
        self.stream.flush()
        self.logfile.flush()


def setup_logging():
    """標準出力・標準エラーをworker.logにも記録する（毎回上書き）。"""
    logfile = open(LOG_PATH, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, logfile)
    sys.stderr = _Tee(sys.stderr, logfile)
    print(f"ログ出力先: {LOG_PATH}")


def load_config():
    """config.json を読み込む。なければ説明を出して終了。"""
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(
            "config.json がありません。\n"
            "→ config.example.json を config.json にコピーして、登録プレイヤー名を編集してください。"
        )
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


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
        self.config = config
        self.init_score = config.get("init_match_score", INIT_MATCH_SCORE)
        self.all_members = (
            config["teamA"]["members"] + config["teamB"]["members"]
        )
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
            "teamA": self._team_stat(self.config["teamA"]),
            "teamB": self._team_stat(self.config["teamB"]),
        }

    def _init_templates(self, img, masks, non_empty):
        """EasyOCRで名前を読み、未取得プレイヤーのテンプレートを保存する。"""
        self.init_frames += 1
        for slot in non_empty:
            raw = ocr_engine.read_name(img, ocr_engine.ROW_CENTERS[slot])
            cand, score = ocr_engine.fuzzy_match(raw, self.all_members)
            if score >= self.init_score and cand and cand not in self.templates:
                self.templates[cand] = masks[slot]
                print(f"  テンプレート登録: {cand}（OCR:[{raw}] 類似{score:.2f}）")

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
            hp = self.last_hp[m]
            if hp is not None:
                total_hp += hp
                known += 1
            members.append({"name": m, "hp": hp})
        return {
            "name": team["name"],
            "totalHp": total_hp,
            "hpKnown": known,  # HPが取得できている人数（生存数ではない）
            "members": members,
        }


def frame_to_image(frame: Frame) -> Image.Image:
    """WGCフレーム(BGRA numpy)をPIL RGB画像に変換。"""
    buf = frame.frame_buffer  # (H, W, 4) BGRA
    rgb = buf[:, :, [2, 1, 0]]  # BGR→RGB（アルファ除外）
    return Image.fromarray(rgb)


def main():
    setup_logging()
    ocr_engine.setup_tessdata()  # tesseractとtessdataを解決（ログに記録される）
    config = load_config()
    # 初期化中（EasyOCRで重い）と高速モードで間隔を分ける
    init_interval = config.get("interval_seconds", 3)
    fast_interval = config.get("fast_interval_seconds", 0.5)
    print(f"設定読み込み完了。初期化中{init_interval}秒 / 高速モード{fast_interval}秒間隔。")
    print(f"ウィンドウ: '{WINDOW_NAME}'")

    capture = WindowsCapture(
        cursor_capture=None,
        draw_border=None,
        monitor_index=None,
        window_name=WINDOW_NAME,
    )

    state = {"last": 0.0, "warned_size": False}
    tracker = PlayerTracker(config)

    @capture.event
    def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
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

        # デバッグモード（set DEBUG=1）: 最初のフレームを保存し、生OCR結果を表示
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

    @capture.event
    def on_closed():
        print("キャプチャを終了しました")

    print("OCR開始（Ctrl+Cで停止）...")
    capture.start()


if __name__ == "__main__":
    main()
