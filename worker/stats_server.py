"""
集計結果をローカルHTTPで配信する小さなサーバー。

Overwolfオーバーレイが http://127.0.0.1:<port>/stats を取得してHPを表示する。
最新の集計はメモリ上の値を返す（ファイル読み込みの競合を避ける）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start_stats_server(port, get_stats):
    """
    バックグラウンドでHTTPサーバーを起動する。
    get_stats: 最新の集計dictを返す呼び出し可能オブジェクト。
    戻り値: ThreadingHTTPServer（停止したい場合は shutdown() を呼ぶ）。
    """

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body=b""):
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")  # Overwolfから取得可能に
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") in ("", "/stats"):
                body = json.dumps(get_stats(), ensure_ascii=False).encode("utf-8")
                self._send(200, body)
            else:
                self._send(404, b'{"error":"not found"}')

        def log_message(self, *args):
            pass  # アクセスログは抑制

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
