"""Receptor local: o browser (sessão logada) faz POST dos candles aqui. Só escuta em 127.0.0.1."""
import json, pathlib
from http.server import BaseHTTPRequestHandler, HTTPServer

ALLOWED = {"https://app.hezilex.com", "https://www.forexfactory.com"}
DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

class H(BaseHTTPRequestHandler):
    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        name = "".join(c for c in body["name"] if c.isalnum() or c in "_-")
        (DATA / f"{name}.json").write_text(json.dumps(body["data"]))
        self.send_response(200); self._cors(); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 8765), H).serve_forever()
