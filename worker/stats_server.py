"""
集計結果の配信と設定受け取りを行う小さなローカルHTTPサーバー。

  GET  /stats   … 最新の集計をJSONで返す（Overwolfオーバーレイが表示に使う）
  POST /config  … ホーム画面から送られたチーム設定を反映する

最新の集計はメモリ上の値を返す（ファイル読み込みの競合を避ける）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start_stats_server(port, get_stats, on_config=None):
    """
    バックグラウンドでHTTPサーバーを起動する。
    get_stats:  最新の集計dictを返す呼び出し可能オブジェクト。
    on_config:  POST /config で受け取った設定dictを処理する関数（任意）。
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
            if self.path.rstrip("/") in ("", "/stats"):
                body = json.dumps(get_stats(), ensure_ascii=False).encode("utf-8")
                self._send(200, body)
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if self.path.rstrip("/") != "/config":
                self._send(404, b'{"error":"not found"}')
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if on_config:
                    on_config(data)
                self._send(200, b'{"ok":true}')
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))

        def log_message(self, *args):
            pass  # アクセスログは抑制

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
