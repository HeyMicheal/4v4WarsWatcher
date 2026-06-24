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

EXPECTED_SIZE = (1920, 1080)


def load_config():
    """config.json を読み込む。なければ説明を出して終了。"""
    if not os.path.exists(CONFIG_PATH):
        raise SystemExit(
            "config.json がありません。\n"
            "→ config.example.json を config.json にコピーして、登録プレイヤー名を編集してください。"
        )
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_stats(rows, config):
    """
    OCR結果(rows)を登録チームと照合し、チーム集計を返す。
    rows: [(name_raw, hp), ...]（8行）
    """
    threshold = config.get("match_threshold", 0.45)
    all_members = config["teamA"]["members"] + config["teamB"]["members"]

    # 各行を最も近い登録名に割り当て: {登録名: hp}
    matched = {}
    for name_raw, hp in rows:
        cand, score = ocr_engine.fuzzy_match(name_raw, all_members)
        if score >= threshold and cand is not None:
            matched[cand] = hp

    def team_stat(team):
        members = []
        total_hp = 0
        alive = 0
        for m in team["members"]:
            hp = matched.get(m)
            is_alive = hp is not None and hp > 0
            if hp is not None and hp > 0:
                total_hp += hp
            if is_alive:
                alive += 1
            members.append({"name": m, "hp": hp, "alive": is_alive})
        return {
            "name": team["name"],
            "totalHp": total_hp,
            "alive": alive,
            "members": members,
        }

    return {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "teamA": team_stat(config["teamA"]),
        "teamB": team_stat(config["teamB"]),
    }


def frame_to_image(frame: Frame) -> Image.Image:
    """WGCフレーム(BGRA numpy)をPIL RGB画像に変換。"""
    buf = frame.frame_buffer  # (H, W, 4) BGRA
    rgb = buf[:, :, [2, 1, 0]]  # BGR→RGB（アルファ除外）
    return Image.fromarray(rgb)


def main():
    config = load_config()
    interval = config.get("interval_seconds", 3)
    print(f"設定読み込み完了。{interval}秒間隔でOCRします。")
    print(f"ウィンドウ: '{WINDOW_NAME}'")

    capture = WindowsCapture(
        cursor_capture=None,
        draw_border=None,
        monitor_index=None,
        window_name=WINDOW_NAME,
    )

    state = {"last": 0.0, "warned_size": False}

    @capture.event
    def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
        # フレームは高頻度で届くので、interval秒に1回だけ処理する
        now = time.time()
        if now - state["last"] < interval:
            return
        state["last"] = now

        img = frame_to_image(frame)
        if img.size != EXPECTED_SIZE and not state["warned_size"]:
            print(f"警告: 解像度が {img.size} です（{EXPECTED_SIZE}前提）。座標がずれる可能性。")
            state["warned_size"] = True

        # OCR処理は例外で全体を落とさず、詳細をログに残して継続する
        try:
            t0 = time.time()
            rows = ocr_engine.read_rows(img)
            stats = build_stats(rows, config)
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
            f"| {a['name']}: HP{a['totalHp']} 生存{a['alive']}/4  "
            f"| {b['name']}: HP{b['totalHp']} 生存{b['alive']}/4"
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
