"""登录态抓字幕回归检查：python3 test_transcript_auth.py。"""
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pipeline


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != "test-token" or not self.headers.get("Cookie"):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Auth Forbidden")
            return
        data = {"code": 0, "list": [{"all_content": [
            {"BeginSec": 65, "Text": "第二段"},
            {"BeginSec": 0, "Text": "第一段"}]}]}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cookie = '_token=%7Bi%3A0%3Bs%3A6%3A%22_token%22%3Bi%3A1%3Bs%3A10%3A%22test-token%22%3B%7D'
    try:
        with patch.object(pipeline, "BASE", f"http://127.0.0.1:{server.server_port}"), \
                tempfile.TemporaryDirectory() as root:
            result = pipeline.run_pipeline(
                "https://classroom.zju.edu.cn/livingroom?course_id=87485&sub_id=1968066",
                cookie=cookie, mode="transcript", out_root=root)
            assert Path(result["transcript_path"]).read_text() == \
                "[00:00:00] 第一段\n[00:01:05] 第二段"
            assert result["segments"] == 2
            try:
                pipeline.fetch_transcript("1968066")
            except pipeline.PipelineError as e:
                assert "403" in str(e) and "登录" in str(e)
            else:
                raise AssertionError("未登录的请求应提示重新登录")
    finally:
        server.shutdown()
        server.server_close()
    print("字幕登录态检查通过")


if __name__ == "__main__":
    main()
