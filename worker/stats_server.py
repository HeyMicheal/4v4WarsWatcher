"""
集計結果の配信と設定受け取りを行う小さなローカルHTTPサーバー。

  GET  /stats   … 最新の集計をJSONで返す（Overwolfオーバーレイが表示に使う）
  GET  /rows    … 各行の名前画像とOCR下書きを返す（ホームの手動対応用）
  POST /config  … ホーム画面から送られたチーム設定を反映する
  POST /assign  … ホーム画面で手動指定した「行ID→プレイヤー名」を反映する
  POST /reocr   … OCRをやり直す（スクショのタイミング不良のリカバリ）

最新の集計はメモリ上の値を返す（ファイル読み込みの競合を避ける）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start_stats_server(port, get_stats, on_config=None, get_rows=None, on_assign=None,
                       on_reocr=None):
    """
    バックグラウンドでHTTPサーバーを起動する。
    get_stats:  最新の集計dictを返す呼び出し可能オブジェクト。
    on_config:  POST /config で受け取った設定dictを処理する関数（任意）。
    get_rows:   GET /rows で返す行データ(list)を返す呼び出し可能オブジェクト（任意）。
    on_assign:  POST /assign で受け取った手動対応dictを処理する関数（任意）。
    on_reocr:   POST /reocr でOCR再実行を要求された時に呼ぶ関数（任意）。
    戻り値: ThreadingHTTPServer（停止したい場合は shutdown() を呼ぶ）。
    """

    class Handler(BaseHTTPRequestHandler):
        def _headers(self, code, length=0):
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            # Overwolfから取得・更新できるようにCORSを許可
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(length))
            self.end_headers()

        def _send(self, code, body=b""):
            self._headers(code, len(body))
            if body:
                self.wfile.write(body)

        def do_OPTIONS(self):
            self._headers(204)  # CORSプリフライト

        def do_GET(self):
            path = self.path.rstrip("/")
            if path in ("", "/stats"):
                body = json.dumps(get_stats(), ensure_ascii=False).encode("utf-8")
                self._send(200, body)
            elif path == "/rows":
                rows = get_rows() if get_rows else []
                body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
                self._send(200, body)
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            path = self.path.rstrip("/")
            if path == "/config":
                handler = on_config
            elif path == "/assign":
                handler = on_assign
            elif path == "/reocr":
                handler = on_reocr
            else:
                self._send(404, b'{"error":"not found"}')
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if handler:
                    handler(data)
                self._send(200, b'{"ok":true}')
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))

        def log_message(self, *args):
            pass  # アクセスログは抑制

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
