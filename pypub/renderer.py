"""Preparação dos capítulos para renderização no WebEngine:

- inline de recursos locais do EPUB (imagens, CSS, fontes) como ``data:`` URIs
  (o PySide6 não expõe ``QWebEngineUrlReply``, então não usamos esquema
  customizado — inlinear é mais simples e robusto para o protótipo);
- CSS de leitura dirigido pelo zoom: a FONTE é controlada pelo zoom
  (18px em 100%) e o ENQUADRAMENTO por colunas — zoom < 100% → 2 colunas
  (2 páginas, o enquadramento mínimo), zoom ≥ 100% → 1 coluna (1 página, o
  máximo). A página nunca escala fora do enquadramento (o zoomFactor da
  view fica em 1.0): além dos limites de enquadramento, só a fonte muda.
"""

from __future__ import annotations

import base64
import posixpath
import re

from .epub import Epub

# --------------------------------------------------------------------------- CSS
# Centralização do bloco de leitura — por que os seletores "body, body[class]"?
# EPUBs exportados do Calibre (e afins) colocam classes no <body> (ex.: <body
# class="calibre">) e o CSS do próprio livro define
# ".calibre { margin: 0 5pt; padding-left: 0; padding-right: 0 }". Classe
# (especificidade 0-1-0) VENCE regra de elemento (body: 0-0-1) — a antiga
# "body { margin: 0 auto }" era desconsiderada e a página ia para o canto
# superior esquerdo, esticada até a borda da janela. "body[class]" (0-1-1)
# vence a classe do livro e, por vir depois no cascade, garante o layout do
# leitor para <body> simples e com classe.
_BASE_CSS = """
html { -webkit-text-size-adjust: 100%; }
html, body { margin: 0; padding: 0; }
body, body[class] {
    margin: 0 auto;
    padding: 28px 32px 72px;
    /* Fonte dirigida pelo zoom (18px em 100%): --pypub-zoom é definido em
       :root (valor inicial, conforme a bridge) e atualizado em runtime por
       pypub_setZoom() — sem recarregar a página (posição de leitura intacta) */
    font-size: calc(18px * var(--pypub-zoom, 1));
    max-width: 44em;  /* 1 coluna (zoom ≥ 100%) */
}
body {
    font-family: "Noto Sans", "Segoe UI", "DejaVu Sans", sans-serif;
    line-height: 1.75;
    text-align: justify;
    -webkit-hyphens: auto;
    hyphens: auto;
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
body, body[class] { color: #21252b; background: #fbfaf7; }
/* 2 colunas (zoom < 100%): classe pypub-2col trocada em runtime por
   pypub_setZoom() (ou já presente no <html> se a renderização inicial
   já estava com zoom < 100%) */
html.pypub-2col body, html.pypub-2col body[class] {
    columns: 2;
    column-gap: 44px;
    column-rule: 1px solid rgba(0, 0, 0, 0.14);
    max-width: 70em;
}
"""

_DARK = """
:root { color-scheme: dark; }
body, body[class] { color: #dfe3e8; background: #1f2328; }
a { color: #79b8ff; }
blockquote { border-color: rgba(255, 255, 255, 0.35); }
/* 2 colunas (zoom < 100%) — ver _LIGHT */
html.pypub-2col body, html.pypub-2col body[class] {
    columns: 2;
    column-gap: 44px;
    column-rule: 1px solid rgba(255, 255, 255, 0.16);
    max-width: 70em;
}
"""

#: ponto de quebra do enquadramento: zoom abaixo → 2 colunas (2 páginas);
#: acima/igual → 1 coluna (1 página). A página nunca sai desses dois
#: enquadramentos — além deles, só o tamanho da fonte muda.
ZOOM_COLUMN_BREAK = 1.0


def columns_for_zoom(zoom: float) -> int:
    """Enquadramento por zoom: <100% → 2 colunas; ≥100% → 1 coluna."""
    return 2 if zoom < ZOOM_COLUMN_BREAK else 1

_PAGE_BG_LIGHT = "#fbfaf7"
_PAGE_BG_DARK = "#1f2328"

# --------------------------------------------------------------------------- JS
# Mecanismo de paginação: uma “página” é um viewport de altura (innerHeight).
# Exposto na janela do documento como pypub_info / pypub_goToPage /
# pypub_nextPage / pypub_prevPage; o QML chama e lê os resultados via
# runJavaScript. Funciona com 1 ou 2 colunas (a página é sempre o que cabe
# na tela, independente do fluxo de colunas).
#
# Atalhos de teclado: durante a leitura a view tem o foco de teclado e os
# ``Shortcut`` do QML (janela) ficam inativos (``enabled: !reader.activeFocus``)
# — PageUp/PageDown, Ctrl+←/→ (capítulos) e Ctrl+= / Ctrl+- / Ctrl+0 (zoom)
# são tratados AQUI (keydown do documento).
# Ctrl+←/→ (troca de capítulo) atravessa o canal JS→Python: o QWebChannel não
# funciona neste wheel do PySide6 (o tipo QML não carrega e a propriedade
# webChannel da view exige QQmlWebChannel, que não tem binding Python), então
# usamos o fragmento da URL — ``history.replaceState("#pypub:cmd:<nome>")``
# dispara o sinal ``urlChanged`` na view, que o QML interpreta e executa.
_PAGE_JS = """
(function () {
  "use strict";
  // Página atual (0-based) é um ESTADO, não só lido de scrollY:
  // dentro de uma mesma avaliação JS, scrollY ainda não reflete o último
  // scrollTo, então navegação baseada só no scroll ficaria defasada.
  // Rolagem manual do usuário anda "à frente" do state — aí seguimos o scroll.
  var state = { page: 0 };
  function h() {
    return window.innerHeight || document.documentElement.clientHeight || 1;
  }
  function total() {
    var sh = document.documentElement.scrollHeight ||
             (document.body ? document.body.scrollHeight : 0);
    return Math.max(1, Math.ceil(sh / h()));
  }
  function scrollPage() { return Math.max(0, Math.floor(window.scrollY / h())); }
  function info() {
    var sp = scrollPage();
    if (sp > state.page) state.page = sp;  // rolagem manual anda à frente
    state.page = Math.min(total() - 1, state.page);
    // src = URL deste documento: o QML descarta respostas de documentos
    // antigos (durante a navegação a URL da view já é a nova, mas o DOM
    // ainda é o anterior — e responderia com estado velho)
    return { current: state.page + 1, total: total(), src: location.href };
  }
  function go(n) {
    var t = total();
    var target = Math.max(0, Math.min(t - 1, n));
    state.page = target;
    window.scrollTo(0, target * h());
    return { current: target + 1, total: t };
  }
  function prevPage() {
    var off = window.scrollY - state.page * h();
    // no meio da página → volta ao topo; no topo → página anterior
    return go(off > 5 ? state.page : state.page - 1);
  }
  // canal JS→Python via fragmento da URL (ver comentário no topo): o QML
  // observa urlChanged, lê #pypub:cmd:<nome> e dispara a ação correspondente
  function cmd(name) {
    try {
      history.replaceState(null, "", "#pypub:cmd:" + name);
    } catch (err) { /* sem history API: sem troca de capítulo via teclado */ }
  }
  function nextChapter() { cmd("next"); }
  function prevChapter() { cmd("prev"); }
  function zoomIn() { cmd("zoomin"); }
  function zoomOut() { cmd("zoomout"); }
  function zoomReset() { cmd("zoomreset"); }

  // Zoom: a página NUNCA escala (o zoomFactor da view fica 1.0 — o
  // enquadramento é preservado); muda só a fonte (18px * z, via
  // --pypub-zoom) e o enquadramento (z < 1 → 2 colunas; z ≥ 1 → 1 coluna).
  // Não há recarga: a posição de leitura (página atual) é mantida.
  function setZoom(z) {
    z = Number(z);
    if (!(z > 0)) z = 1;
    z = Math.max(0.6, Math.min(2.5, z));
    var de = document.documentElement;
    if (!de) return false;
    de.style.setProperty("--pypub-zoom", String(z));
    de.classList.toggle("pypub-2col", z < 1.0);
    return true;
  }

  if (window.addEventListener) {
    window.addEventListener("scroll", function () {
      var sp = scrollPage();
      if (sp > state.page) state.page = sp;
    });
  }

  // atalhos dentro da página (disparam com a view focada — o estado normal
  // de leitura); preventDefault/stopPropagation evitam rolagem nativa dupla
  if (document.addEventListener) {
    document.addEventListener("keydown", function (e) {
      if (!e) return;
      var k = e.key || "";
      if (k === "PageDown") {
        go(state.page + 1);
        e.preventDefault(); e.stopPropagation();
        return;
      }
      if (k === "PageUp") {
        prevPage();
        e.preventDefault(); e.stopPropagation();
        return;
      }
      if (e.ctrlKey || e.metaKey) {
        if (k === "ArrowRight") {
          nextChapter();
          e.preventDefault(); e.stopPropagation();
        } else if (k === "ArrowLeft") {
          prevChapter();
          e.preventDefault(); e.stopPropagation();
        } else if (k === "=" || k === "+") {
          zoomIn();
          e.preventDefault(); e.stopPropagation();
        } else if (k === "-") {
          zoomOut();
          e.preventDefault(); e.stopPropagation();
        } else if (k === "0") {
          zoomReset();
          e.preventDefault(); e.stopPropagation();
        }
      }
    }, true);
  }

  window.pypub_info = info;
  window.pypub_goToPage = function (n) { return go(n - 1); };
  window.pypub_nextPage = function () { return go(state.page + 1); };
  window.pypub_prevPage = prevPage;
  // zoom: aplica fonte + enquadramento sem recarga (ver setZoom acima)
  window.pypub_setZoom = setZoom;
  // diagnóstico/teste: o zoom efetivamente aplicado no documento
  window.pypub_zoomInfo = function () {
    var v = getComputedStyle(document.documentElement).getPropertyValue("--pypub-zoom");
    var z = parseFloat(v);
    if (!(z > 0)) z = 1;
    var b = document.body;
    return {
      zoom: z,
      columns: b ? (parseInt(getComputedStyle(b).columnCount, 10) || 1) : 1,
      fontSize: b ? parseFloat(getComputedStyle(b).fontSize) : 18
    };
  };
  // diagnóstico/teste: estado da paginação
  window.pypub_debug = function () {
    return { page: state.page + 1, total: total(), scrollY: window.scrollY };
  };
})();
"""

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
def _set_html_class(html: str, cls: str) -> str:
    """Adiciona ``cls`` à tag ``<html>`` (usado para o estado inicial pypub-2col)."""
    m = re.search(r"<html\b[^>]*>", html, re.IGNORECASE)
    if not m:
        return html
    tag = m.group(0)
    if re.search(r"\bclass\s*=", tag, re.IGNORECASE):
        new_tag = re.sub(
            r"""(class\s*=\s*["'])([^"']*)""",
            rf"\1\2 {cls}",
            tag,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        new_tag = tag[: -1] + f' class="{cls}>"'
    return html[: m.start()] + new_tag + html[m.end() :]


def inject_css(html: str, dark: bool = False, zoom: float = 1.0) -> str:
    """Injeta o CSS do leitor no HTML do capítulo (``zoom`` = escala inicial da fonte)."""
    zoom_rule = f":root {{ --pypub-zoom: {zoom:g}; }}"
    style = f"<style>{_BASE_CSS}{zoom_rule}{_DARK if dark else _LIGHT}</style>"
    m = re.search(r"</head\s*>", html, re.IGNORECASE)
    if m:
        return html[: m.start()] + style + html[m.start() :]
    m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if m:
        return html[: m.end()] + style + html[m.end() :]
    if m := re.search(r"<html[^>]*>", html, re.IGNORECASE):
        return html[: m.end()] + f"<head>{style}</head>" + html[m.end() :]
    return style + html


def inject_page_js(html: str) -> str:
    """Injeta o script de paginação no fim do <body> (ou no fim do HTML)."""
    script = f"<script>{_PAGE_JS}</script>"
    m = re.search(r"</body\s*>", html, re.IGNORECASE)
    if m:
        return html[: m.start()] + script + html[m.start():]
    m = re.search(r"</html\s*>", html, re.IGNORECASE)
    if m:
        return html[: m.start()] + script + html[m.start():]
    return html + script


def prepare_chapter(
    raw_html: str, chapter_path: str, epub: Epub, dark: bool = False, zoom: float = 1.0
) -> str:
    """Prepara o HTML do capítulo: recursos inline + CSS do leitor + paginação.

    ``zoom`` fixa o estado INICIAL de fonte/enquadramento (o ajuste em
    runtime é feito por ``pypub_setZoom`` no documento, sem recarga).
    """
    html = inline_local_resources(raw_html, chapter_path, epub)
    if columns_for_zoom(zoom) == 2:
        html = _set_html_class(html, "pypub-2col")
    html = inject_css(html, dark, zoom)
    return inject_page_js(html)
