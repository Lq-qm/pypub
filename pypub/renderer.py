"""Preparação dos capítulos para renderização no WebEngine:

- inline de recursos locais do EPUB (imagens, CSS, fontes) como ``data:`` URIs
  (o PySide6 não expõe ``QWebEngineUrlReply``, então não usamos esquema
  customizado — inlinear é mais simples e robusto para o protótipo);
- CSS responsivo: 1 coluna em janelas estreitas e, no máximo, 2 colunas
  quando a janela é larga o suficiente (media query reavaliada a cada
  redimensionamento).
"""

from __future__ import annotations

import base64
import posixpath
import re

from .epub import Epub

# --------------------------------------------------------------------------- CSS
_BASE_CSS = """
html { -webkit-text-size-adjust: 100%; }
html, body { margin: 0; padding: 0; }
body {
    font-family: "Noto Sans", "Segoe UI", "DejaVu Sans", sans-serif;
    font-size: 18px;
    line-height: 1.75;
    text-align: justify;
    -webkit-hyphens: auto;
    hyphens: auto;
    padding: 28px 32px 72px;
    max-width: 44em;
    margin: 0 auto;
}
h1, h2, h3, h4 { line-height: 1.25; margin: 1.3em 0 0.5em; }
h1:first-child, h2:first-child { margin-top: 0.2em; }
p { margin: 0 0 0.9em; }
img { max-width: 100%; height: auto; }
a { color: #2b6cb0; }
pre, code { font-family: "DejaVu Sans Mono", monospace; white-space: pre-wrap; word-wrap: break-word; }
table { max-width: 100%; }
blockquote { margin: 0 0 1em; padding-left: 1em; border-left: 3px solid; opacity: .85; }
"""

_LIGHT = """
:root { color-scheme: light; }
body { color: #21252b; background: #fbfaf7; }
@media (min-width: 980px) {
    body {
        columns: 2;
        column-gap: 44px;
        column-rule: 1px solid rgba(0, 0, 0, 0.14);
        max-width: 70em;
    }
}
"""

_DARK = """
:root { color-scheme: dark; }
body { color: #dfe3e8; background: #1f2328; }
a { color: #79b8ff; }
blockquote { border-color: rgba(255, 255, 255, 0.35); }
@media (min-width: 980px) {
    body {
        columns: 2;
        column-gap: 44px;
        column-rule: 1px solid rgba(255, 255, 255, 0.16);
        max-width: 70em;
    }
}
"""

_PAGE_BG_LIGHT = "#fbfaf7"
_PAGE_BG_DARK = "#1f2328"

_EMPTY_HTML = """
<html><head><meta charset="utf-8"></head>
<body style="font-family:sans-serif;text-align:center;padding-top:18vh;color:#666;">
<h2>PyPub</h2>
<p>Abra um arquivo <b>.epub</b> para começar a ler.</p>
</body></html>
"""


def page_background(dark: bool) -> str:
    return _PAGE_BG_DARK if dark else _PAGE_BG_LIGHT


def empty_html(dark: bool = False) -> str:
    return _EMPTY_HTML


# -------------------------------------------------- recursos locais (data: URI)
_MIME_BY_EXT = {
    ".xhtml": "text/html",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".xml": "application/xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}


def mime_for(path: str) -> str:
    lower = path.lower()
    for ext, mime in _MIME_BY_EXT.items():
        if lower.endswith(ext):
            return mime
    return "application/octet-stream"


_ATTR_RE = re.compile(
    r"""\b(?P<attr>src|href|data)\s*=\s*(?P<q>["'])(?P<val>[^"']*)(?P=q)""",
    re.IGNORECASE,
)
_CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+?)['"]?\s*\)""", re.IGNORECASE)


def _is_external(value: str) -> bool:
    v = value.strip()
    return (
        v.startswith(("http://", "https://", "data:", "mailto:", "javascript:", "blob:", "#"))
        or "://" in v
    )


def _data_uri(path: str, data: bytes) -> str:
    mime = mime_for(path).split(";")[0]
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _inline_css(css: str, css_path: str, epub: Epub) -> str:
    """Rewrite ``url(...)`` relativos do CSS para data URIs (base = dir do CSS)."""
    css_dir = posixpath.dirname(css_path)

    def sub(m: re.Match) -> str:
        url = m.group(1).strip()
        if not url or _is_external(url):
            return m.group(0)
        resolved = posixpath.normpath(
            posixpath.join(css_dir, url.split("#", 1)[0].split("?", 1)[0])
        )
        if not epub.has(resolved):
            return m.group(0)
        try:
            data = epub.read(resolved)
        except KeyError:
            return m.group(0)
        return m.group(0).replace(url, _data_uri(resolved, data))

    return _CSS_URL_RE.sub(sub, css)


def inline_local_resources(html: str, chapter_path: str, epub: Epub) -> str:
    """Substitui src/href/data locais por ``data:`` URIs embutidos."""
    base_dir = posixpath.dirname(chapter_path)
    seen: set[str] = set()

    for m in _ATTR_RE.finditer(html):
        value = m.group("val").strip()
        if not value or _is_external(value):
            continue
        clean = value.split("#", 1)[0].split("?", 1)[0]
        if not clean:
            continue
        resolved = posixpath.normpath(posixpath.join(base_dir, clean))
        if resolved in seen or not epub.has(resolved):
            continue
        seen.add(resolved)
        try:
            data = epub.read(resolved)
        except KeyError:
            continue

        if resolved.lower().endswith(".css"):
            css_text = data.decode("utf-8", "replace")
            css_text = _inline_css(css_text, resolved, epub)
            replacement = _data_uri(resolved, css_text.encode("utf-8"))
        else:
            replacement = _data_uri(resolved, data)

        quoted = f"{m.group('q')}{value}{m.group('q')}"
        html = html.replace(quoted, f"{m.group('q')}{replacement}{m.group('q')}")

    return html


# --------------------------------------------------------------------- injeção
def inject_css(html: str, dark: bool = False) -> str:
    """Injeta o CSS do leitor no HTML do capítulo."""
    style = f"<style>{_BASE_CSS}{_DARK if dark else _LIGHT}</style>"
    m = re.search(r"</head\s*>", html, re.IGNORECASE)
    if m:
        return html[: m.start()] + style + html[m.start() :]
    m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if m:
        return html[: m.end()] + style + html[m.end() :]
    if m := re.search(r"<html[^>]*>", html, re.IGNORECASE):
        return html[: m.end()] + f"<head>{style}</head>" + html[m.end() :]
    return style + html


def prepare_chapter(raw_html: str, chapter_path: str, epub: Epub, dark: bool = False) -> str:
    """Prepara o HTML do capítulo: recursos inline + CSS do leitor."""
    html = inline_local_resources(raw_html, chapter_path, epub)
    return inject_css(html, dark)
