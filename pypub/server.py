"""Servidor HTTP local (127.0.0.1) que serve o HTML dos capítulos.

Por que um servidor local?
--------------------------
- No Qt 6.12/PySide6, a view QML do WebEngine **não expõe** ``load(url)``;
- ``loadHtml(html)`` é **instável para conteúdo grande** (limite de IPC por
  volta de ~500 KB — páginas maiores carregam em branco);
- Navegar para ``http://127.0.0.1:PORT/...`` usa o pipeline normal de
  carregamento do Chromium, **sem limite de tamanho**.

O servidor:
- escuta apenas em ``127.0.0.1`` (nunca em interface pública);
- porta aleatória escolhida pelo SO;
- serve sempre o "conteúdo atual" definido pela bridge (um HTML
  autocontido, com recursos embutidos como ``data:`` URIs);
- roda em thread daemon (morre junto com o processo).
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    server_version = "PyPub/0.1"

    def do_GET(self) -> None:  # noqa: N802 (API do http.server)
        content = self.server.pypub_content  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args: object) -> None:  # silêncio
        pass


class _HTTPD(ThreadingHTTPServer):
    """ThreadingHTTPServer que guarda o conteúdo a servir."""

    pypub_content: bytes = b""


class ChapterServer:
    """Serve o HTML atual em ``http://127.0.0.1:<porta>/<caminho>``."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._lock = threading.Lock()
        self._httpd = _HTTPD((host, port), _Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="pypub-http", daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------- conteúdo
    @property
    def content(self) -> bytes:
        with self._lock:
            return self._httpd.pypub_content

    def set_content(self, html: str) -> None:
        data = html.encode("utf-8")
        with self._lock:
            self._httpd.pypub_content = data

    # ---------------------------------------------------------------- urls
    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str = "") -> str:
        return f"{self.base}/{path.lstrip('/')}"

    # -------------------------------------------------------------- ciclo de vida
    def shutdown(self) -> None:
        with self._lock:
            try:
                self._httpd.shutdown()
            finally:
                self._httpd.server_close()
