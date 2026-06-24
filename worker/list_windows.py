"""
ウィンドウ一覧表示ツール

TFT（LoLクライアント）の正確なウィンドウタイトルを確認するためのスクリプト。
capture_test.py で window_name に指定する文字列を、この出力から探す。

使い方:
    python list_windows.py
"""

import pygetwindow as gw


def main():
    print("=== 現在開いているウィンドウ一覧 ===\n")
    titles = sorted(set(t for t in gw.getAllTitles() if t.strip()))
    for t in titles:
        print(repr(t))  # repr で空白や特殊文字も正確に表示

    print("\n--- ヒント ---")
    print("TFT起動中なら 'League of Legends (TM) Client' などが表示されるはず。")
    print("その文字列を capture_test.py の WINDOW_NAME に貼り付けてください。")


if __name__ == "__main__":
    main()
