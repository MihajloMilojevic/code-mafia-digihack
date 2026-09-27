"""
Deljen, minimalan HTTP MJPEG-stil preview server - radi svuda ukljucujuci
headless SSH (bez X11/Qt), gleda se u browseru. Izdvojeno iz hand_tracker.py
da bi i point_at_object.py mogao da ga koristi bez dupliranja koda.

Upotreba:
    from http_preview import start_http_preview, publish_frame
    start_http_preview(8093)   # jednom, na pocetku
    ...
    publish_frame(frame)       # u petlji, frame je BGR numpy niz (OpenCV)
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

_latest_jpeg: bytes | None = None
_jpeg_lock = threading.Lock()


class _PreviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # ne zagusuj konzolu HTTP access logom

    def do_GET(self):
        if self.path.startswith("/frame.jpg"):
            with _jpeg_lock:
                data = _latest_jpeg
            if data is None:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"jos nema frejma")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        html = """<html><body style="background:#111;color:#eee;font-family:sans-serif;text-align:center">
<img id="f" src="/frame.jpg" style="max-width:95vw" />
<script>setInterval(()=>{document.getElementById('f').src='/frame.jpg?'+Date.now();}, 200);</script>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def start_http_preview(port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), _PreviewHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[http_preview] na 0.0.0.0:{port} - otvori / u browseru "
          f"(preko istog SSH tunela kao za arm_control_app)")


def publish_frame(frame) -> None:
    global _latest_jpeg
    ok, jpeg = cv2.imencode(".jpg", frame)
    if ok:
        with _jpeg_lock:
            _latest_jpeg = jpeg.tobytes()
