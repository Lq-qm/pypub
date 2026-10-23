"""Preparação dos capítulos para renderização no WebEngine:

- inline de recursos locais do EPUB (imagens, CSS, fontes) como ``data:`` URIs
  (o PySide6 não expõe ``QWebEngineUrlReply``, então não usamos esquema
  customizado — inlinear é mais simples e robusto para o protótipo);
- CSS de leitura dirigido pelo zoom: a FONTE é controlada pelo zoom
  (18px em 100%) e o ENQUADRAMENTO é FIXO (sempre 1 coluna). A página nunca
  escala fora do enquadramento (o zoomFactor da view fica em 1.0): o zoom —
  tanto para mais quanto para menos — muda SOMENTE o tamanho da fonte, sem
  alterar o enquadramento.
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
html { -webkit-text-size-adjust: 100%; height: 100%; overflow: hidden; }
html, body { margin: 0; padding: 0; }
body, body[class] {
    margin: 0 auto;
    padding: 28px 32px 72px;
    /* Fonte dirigida pelo zoom (18px em 100%): --pypub-zoom é definido em
       :root (valor inicial, conforme a bridge) e atualizado em runtime por
       pypub_setZoom() — sem recarregar a página (posição de leitura intacta) */
    font-size: calc(18px * var(--pypub-zoom, 1));
    max-width: 44em;  /* enquadramento fixo (1 coluna) — o zoom muda só a fonte */
    /* UMA PÁGINA POR VEZ: o body é o clip do conteúdo — tem exatamente a
       altura do viewport e corta o excedente (sem barra de rolagem, sem
       rolagem livre, sem seleção além da página visível; o documento não
       transborda o viewport). A troca de página desloca o scroll box do
       body (body.scrollTop) — ver pypub_nextPage/prevPage no script. */
    height: 100%;
    box-sizing: border-box;
    overflow: hidden;
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
"""

_DARK = """
:root { color-scheme: dark; }
body, body[class] { color: #dfe3e8; background: #1f2328; }
a { color: #79b8ff; }
blockquote { border-color: rgba(255, 255, 255, 0.35); }
"""

#: Enquadramento fixo: o leitor sempre exibe **1 coluna**. O zoom muda apenas
#: o tamanho da fonte (18px × z) — tanto para zoom+ quanto para zoom- — sem
#: alterar o enquadramento (não há mais troca automática de coluna).
def columns_for_zoom(zoom: float) -> int:
    """Enquadramento fixo: sempre 1 coluna (o ``zoom`` muda só a fonte).

    Mantido por compatibilidade de API (a bridge expõe ``columnCount``);
    o argumento é ignorado de propósito — o enquadramento não depende mais do
    zoom.
    """
    del zoom  # intencionalmente não utilizado
    return 1

_PAGE_BG_LIGHT = "#fbfaf7"
_PAGE_BG_DARK = "#1f2328"

# --------------------------------------------------------------------------- JS
# Mecanismo de paginação — **uma página por vez**: o body é o clip do
# conteúdo (height 100% + overflow: hidden no CSS do leitor), então o que se
# vê é sempre exatamente uma página (uma viewport de altura) — o documento
# NÃO rola livre (sem scrollbar, sem momentum, sem seleção além do visível).
# A troca de página desloca o scroll box do body (body.scrollTop = n * H);
# o viewport do documento permanece parado (window.scrollY sempre 0).
# Exposto na janela do documento como pypub_info / pypub_goToPage /
# pypub_nextPage / pypub_prevPage; o QML chama e lê os resultados via
# runJavaScript. O enquadramento é fixo (1 coluna); a página é sempre o que
# cabe na tela (uma viewport de altura).
#
# Navegação de página: a leitura é PÁGINA A PÁGINA (uma página = um viewport
# de altura), e a troca de página acontece por:
# - **rodo do mouse** (wheel): o gesto é acumulado (com cooldown) e, ao
#   atingir o limiar, avança/recua uma página — o conteúdo não rola livre;
# - **setas do teclado** (←/→, sem Ctrl): página anterior/próxima (Ctrl+←/→
#   reserva para capítulos); PageUp/PageDown continuam funcionando;
# - **Ctrl+= / Ctrl+- / Ctrl+0**: zoom (canal hash → bridge).
# Durante a leitura a view tem o foco de teclado e os ``Shortcut`` do QML
# (janela) ficam inativos (``enabled: !reader.activeFocus``) — os eventos são
# tratados AQUI (keydown/wheel do documento); sem foco, os Shortcut de janela
# disparam a mesma API do QML.
# Ctrl+←/→ (troca de capítulo) atravessa o canal JS→Python: o QWebChannel não
# funciona neste wheel do PySide6 (o tipo QML não carrega e a propriedade
# webChannel da view exige QQmlWebChannel, que não tem binding Python), então
# usamos o fragmento da URL — ``history.replaceState("#pypub:cmd:<nome>")``
# dispara o sinal ``urlChanged`` na view, que o QML interpreta e executa.
_PAGE_JS = """
(function () {
  "use strict";
  // Página atual (0-based) é um ESTADO, não só lido do offset do clip:
  // dentro de uma mesma avaliação JS, o offset ainda não reflete a última
  // troca, então navegação baseada só no offset ficaria defasada.
  // (Não existe rolagem manual: o body é o clip — ver CSS do leitor.)
  var state = { page: 0 };
  function h() {
    return window.innerHeight || document.documentElement.clientHeight || 1;
  }
  // O scroll box é o BODY (clip de uma página por vez), não o viewport do
  // documento — documentElement.scrollHeight seria só a altura da tela.
  function total() {
    var b = document.body;
    var sh = (b && b.scrollHeight) || document.documentElement.scrollHeight || 0;
    return Math.max(1, Math.ceil(sh / h()));
  }
  function scrollPage() {
    var b = document.body;
    return Math.max(0, Math.floor(((b && b.scrollTop) || 0) / h()));
  }
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
    // Desloca o CLIP (scroll box do body): mostra a página (target+1) e
    // corta o resto — o viewport do documento não rola (window.scrollY=0).
    var b = document.body;
    if (b) b.scrollTop = target * h();
    return { current: target + 1, total: t };
  }
  function prevPage() {
    var b = document.body;
    var off = (b ? b.scrollTop : 0) - state.page * h();
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

  // Zoom: a página NUNCA escala (o zoomFactor da view fica 1.0); o
  // enquadramento é FIXO (sempre 1 coluna) e muda SOMENTE o tamanho da fonte
  // (18px * z, via --pypub-zoom) — tanto para zoom+ quanto para zoom-.
  // Não há recarga: a posição de leitura (página atual) é mantida.
  function setZoom(z) {
    z = Number(z);
    if (!(z > 0)) z = 1;
    z = Math.max(0.6, Math.min(2.5, z));
    var de = document.documentElement;
    if (!de) return false;
    de.style.setProperty("--pypub-zoom", String(z));
    return true;
  }

  if (window.addEventListener) {
    // Guarda de segurança: o usuário NÃO rola livre (body é o clip), mas
    // qualquer deslocamento do clip que ande "à frente" do state é seguido.
    window.addEventListener("scroll", function () {
      var sp = scrollPage();
      if (sp > state.page) state.page = sp;
    });

    // Roda do mouse = troca de página (rolar livre não existe neste leitor):
    // o deltaY do gesto é acumulado — normalizado pelo deltaMode (0=px,
    // 1=linhas, 2=páginas) — e, ao atingir o limiar, avança uma página.
    // Cooldown de 350 ms separa gestos (um giro rápido da roda = várias
    // páginas; um toque de trackpad = uma). Sempre preventDefault: o
    // conteúdo NÃO rola livre, ele muda de página.
    var wheelAcc = 0;
    var wheelLast = 0;
    var WHEEL_UNIT = 60;  // unidades acumuladas por página (ver normalização)
    window.addEventListener("wheel", function (e) {
      if (!e) return;
      var d = e.deltaY || 0;
      if (d !== 0) {
        var now = Date.now();
        if (now - wheelLast > 350) wheelAcc = 0;   // gesto novo
        var unit = (e.deltaMode === 1) ? 10 : (e.deltaMode === 2) ? 100 : 1;
        wheelAcc += d * unit;
        wheelLast = now;
        if (wheelAcc >= WHEEL_UNIT) { wheelAcc = 0; go(state.page + 1); }
        else if (wheelAcc <= -WHEEL_UNIT) { wheelAcc = 0; go(state.page - 1); }
      }
      e.preventDefault();
      e.stopPropagation();
    }, true);
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
      // Setas simples (sem Ctrl): → próxima página, ← anterior. (Ctrl+seta
      // é capítulo — ver abaixo.) Se há seleção de texto ativa, as setas
      // estendem a seleção e não navegam.
      if (!e.ctrlKey && !e.metaKey && (k === "ArrowRight" || k === "ArrowLeft")) {
        var sel = window.getSelection ? window.getSelection() : null;
        var selActive = !!(sel && sel.rangeCount > 0 && !sel.isCollapsed);
        if (!selActive) {
          go(k === "ArrowRight" ? state.page + 1 : state.page - 1);
          e.preventDefault(); e.stopPropagation();
          return;
        }
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
    return {
      page: state.page + 1,
      total: total(),
      clipTop: document.body ? document.body.scrollTop : 0,
      docScrollY: window.scrollY
    };
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

    ``zoom`` fixa o estado INICIAL da FONTE (enquadramento fixo em 1 coluna);
    o ajuste em runtime é feito por ``pypub_setZoom`` no documento, sem recarga.
    """
    html = inline_local_resources(raw_html, chapter_path, epub)
    html = inject_css(html, dark, zoom)
    return inject_page_js(html)
