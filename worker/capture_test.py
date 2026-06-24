"""
ウィンドウキャプチャ検証スクリプト（ステップ1）

Windows Graphics Capture (WGC) を使い、指定ウィンドウだけを撮影して画像保存する。
他アプリが上に重なっていても、ゲームがGPU描画でも、ウィンドウの中身を直接取得できる。

目的:
    - TFTウィンドウが正しく映るか（真っ黒にならないか）を確認する
    - 映ったら capture.png にプレイヤーリストが写っているかを目視する

使い方:
    1. TFTを起動して観戦画面を表示しておく
    2. python capture_test.py
    3. 生成された capture.png を開いて中身を確認

うまくいかない場合:
    - "ウィンドウが見つかりません" → list_windows.py で正確なタイトルを確認
    - capture.png が真っ黒 → WINDOW_NAME が別ウィンドウを指している可能性
"""

from windows_capture import WindowsCapture, Frame, InternalCaptureControl

# list_windows.py で確認した正確なタイトルに書き換える
WINDOW_NAME = "League of Legends (TM) Client"

OUTPUT_PATH = "capture.png"


# 注意: cursor_capture / draw_border を True/False で明示指定すると、
# 一部のWindowsバージョンで「Toggling ... is not supported」エラーになる。
# None にしてOS既定の挙動に任せると回避できる。
capture = WindowsCapture(
    cursor_capture=None,
    draw_border=None,
    monitor_index=None,
    window_name=WINDOW_NAME,
)


@capture.event
def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
    # 最初の1フレームを保存して即終了（検証用なので1枚で十分）
    print(f"フレーム取得: {frame.width}x{frame.height}")
    frame.save_as_image(OUTPUT_PATH)
    print(f"保存しました → {OUTPUT_PATH}")
    capture_control.stop()


@capture.event
def on_closed():
    print("キャプチャを終了しました")


if __name__ == "__main__":
    print(f"ウィンドウ '{WINDOW_NAME}' をキャプチャします...")
    try:
        capture.start()
    except Exception as e:
        print(f"エラー: {e}")
        print("→ list_windows.py でウィンドウタイトルを確認してください")
