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
/* CAIXA DE LEITURA: o body é o aro fixo — a borda uniforme (32px) NÃO rola
   com o conteúdo; o texto fica sempre enquadrado, mesmo durante a troca de
   página (a rolagem acontece no .pypub-clip, abaixo). */
body, body[class] {
    margin: 0 auto;
    padding: 32px;  /* bordas uniformes: topo, laterais e base (o aro) */
    /* Fonte dirigida pelo zoom (18px em 100%): --pypub-zoom é definido em
       :root (valor inicial, conforme a bridge) e atualizado em runtime por
       pypub_setZoom() — sem recarregar a página (posição de leitura intacta) */
    font-size: calc(18px * var(--pypub-zoom, 1));
    line-height: 1.75;
    max-width: 44em;  /* enquadramento fixo (1 coluna) — o zoom muda só a fonte */
    height: 100%;
    box-sizing: border-box;
    overflow: hidden;
}
/* Janela de corte (scroll box) dentro da caixa de leitura: tem exatamente a
   altura do aro (viewport menos 2×32px) e corta o excedente (sem barra de
   rolagem, sem rolagem livre, sem seleção além do visível; o documento não
   transborda o viewport). A troca de página desloca o clip (clip.scrollTop)
   — ver pypub_nextPage/prevPage no script. As QUEBRAS de página alinham às
   fronteiras de linha — nenhuma linha fica cortada (ver _PAGE_JS). */
.pypub-clip { height: 100%; overflow: hidden; }
body {
    /* Fonte base do leitor (fallback — se o EPUB declarar a própria tipografia,
       ela vale; Noto Sans é a família de reserva). */
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

# ---------------------------------------------------------------------------
# Tema de leitura — único: SEGUE O SISTEMA OPERACIONAL (claro/escuro)
# ---------------------------------------------------------------------------
# A bridge lê QStyleHints.colorScheme() (no KDE, a KColorScheme do Plasma;
# offscreen → Unknown → claro) e re-renderiza ao vivo se o esquema do OS
# mudar com o app aberto. A tipografia do próprio livro vale (se ele
# declarar fonte); Noto Sans é o fallback do leitor. A interface (toolbar,
# rodapé, janela) usa as cores do Kirigami/KDE — integração nativa.
# Aspas SIMPLES de propósito: essa constante é embutida em atributos
# style="..." (empty_html) — aspas duplas ali fechavam o atributo e
# invalidavam o CSS inteiro. Em blocos <style> (theme_css) aspas simples
# valem igual em CSS.
_UI_FONT_STACK = "'Noto Sans', 'Segoe UI', 'DejaVu Sans', sans-serif"


def theme_css(dark: bool = False) -> str:
    """Bloco de <style> do tema (segue o modo do sistema — ``dark``)."""
    base = _DARK if dark else _LIGHT
    return f"{base}\nbody {{ font-family: {_UI_FONT_STACK}; }}"


def page_background(dark: bool = False) -> str:
    """Cor de fundo da área de leitura (pinta a view QML)."""
    return _PAGE_BG_DARK if dark else _PAGE_BG_LIGHT


def empty_html(dark: bool = False) -> str:
    """Página "abra um EPUB" — já com o tema (cores conforme o sistema).

    Texto centralizado na interface (horizontal e vertical, via flexbox)
    e com a MESMA cor do texto de leitura (o cinza do tema) — visível em
    modo claro e escuro.
    """
    fg, bg = ("#dfe3e8", _PAGE_BG_DARK) if dark else ("#21252b", _PAGE_BG_LIGHT)
    style = (
        f"font-family:{_UI_FONT_STACK};"
        "margin:0;"
        "display:flex;flex-direction:column;"
        "align-items:center;justify-content:center;"
        "min-height:100vh;box-sizing:border-box;text-align:center;"
        f"color:{fg};background:{bg};"
    )
    return (
        '<html><head><meta charset="utf-8"></head>'
        f'<body style="{style}">'
        "<h2>PyPub</h2>"
        "<p>Abra um arquivo <b>.epub</b> para começar a ler.</p>"
        "</body></html>"
    )

# --------------------------------------------------------------------------- JS
# Mecanismo de paginação — **uma página por vez**, dentro da CAIXA DE
# LEITURA: o body é o aro fixo (borda uniforme 32px, height 100% +
# overflow: hidden) e o .pypub-clip é a janela de corte (height 100% do aro
# + overflow: hidden), então o que se vê é sempre exatamente uma página (a
# altura da caixa) — o documento NÃO rola livre (sem scrollbar, sem
# momentum, sem seleção além do visível) e o texto fica SEMPRE enquadrado na
# borda de 32px, mesmo durante a troca de página. A troca de página desloca
# o clip (clip.scrollTop = n * H); o viewport do documento permanece parado
# (window.scrollY sempre 0).
# Exposto na janela do documento como pypub_info / pypub_goToPage /
# pypub_nextPage / pypub_prevPage; o QML chama e lê os resultados via
# runJavaScript. O enquadramento é fixo (1 coluna); a página é sempre o que
# cabe na caixa de leitura (a tela menos as bordas de 32px).
#
# Navegação de página: a leitura é PÁGINA A PÁGINA (uma página = as linhas
# que cabem na caixa de leitura — a quebra alinha ao topo da primeira linha
# que NÃO coube, então nenhuma linha fica cortada na borda), e a troca de
# página acontece por:
# - **rodo do mouse** (wheel SEM Ctrl): a quantidade rolada vira PÁGINAS
#   acumuladas (proporcional; resto fracionário carrega no gesto) — o
#   conteúdo não rola livre;
# - **setas do teclado** (←/→, sem Ctrl): página anterior/próxima (Ctrl+←/→
#   reserva para capítulos); PageUp/PageDown continuam funcionando;
# - **Ctrl+= / Ctrl+- / Ctrl+0**: zoom de fonte (canal hash → bridge).
# Ctrl+roda não tem função no PyPub (zoom é só por teclado):
# o handler
# da roda IGNORA eventos com Ctrl/Meta (preventDefault + return) — isso
# também suprime o zoom nativo de página do Chromium; só a roda simples
# pagina.
# Cada troca (go()) também AVISA o QML pelo fragmento da URL
# (#pypub:cmd:page:N → onUrlChanged → refreshPageInfo): assim o contador
# "p. N/M" do rodapé segue a leitura em tempo real — inclusive nas trocas
# feitas DENTRO da página (roda/setas com foco), que o QML não veria de
# outra forma. O número na fragment só existe para a URL MUDAR a cada
# evento (replaceState idêntico não dispara urlChanged); o valor de
# referência continua sendo pypub_info().
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
  // (Não existe rolagem manual: o clip é a janela de corte — ver CSS do
  // leitor.)
  var state = { page: 0, anchors: null, dirty: true, rebases: 0 };
  // CAIXA DE LEITURA: o body é o aro (borda uniforme de 32px que NÃO rola);
  // o .pypub-clip é a janela de corte (scroll box). A página = o que cabe
  // no clip (a viewport menos 2×32px) — a borda da caixa permanece
  // constante durante a troca de página.
  function clipEl() {
    return document.querySelector(".pypub-clip") || document.body;
  }
  // Altura da caixa (viewport menos 2×32px): a página é o que cabe aqui.
  function h() {
    var c = clipEl();
    return (c && c.clientHeight) || window.innerHeight ||
           document.documentElement.clientHeight || 1;
  }
  // -------------------------------------------------------------- quebra de
  // linha — QUEBRA DE PÁGINA POR LINHA (nenhuma linha cortada): as páginas
  // NÃO têm altura fixa — cada página vai da âncora (topo da primeira
  // linha visível) até o topo da PRIMEIRA LINHA QUE NÃO CABE na caixa
  // (próxima âncora). Assim nenhuma linha cruza a borda de uma página:
  // na página em que não cabe, ela simplesmente não aparece; na página
  // seguinte, ela aparece INTEIRA no topo. A altura de cada página varia
  // (±1 linha) conforme as quebras de linha do texto. Imagens/vídeos/
  // <hr> são itens ATÔMICOS: se não couberem inteiros, a página termina
  // antes deles (também não cortam).
  //
  // Refinamentos de precisão (linhas que "pareciam cortadas"):
  // 1. **pixels inteiros**: o navegador arredonda ``scrollTop`` para
  //    inteiro — as âncoras são calculadas em inteiros (topo ``floor``,
  //    base ``ceil``) para nenhuma fração de pixel cortar o topo/base de
  //    uma linha;
  // 2. **respiro nas bordas** (EDGE px): a última linha visível termina
  //    pelo menos EDGE px acima da base da caixa e a primeira linha
  //    começa no máximo EDGE px abaixo do topo (sempre acima da base da
  //    linha anterior — a trava que impede cortá-la) — a linha nunca
  //    encosta no aro;
  // 3. **linhas visuais**: itens que se sobrepõem verticalmente (ex.:
  //    imagem inline + texto da mesma linha) viram UMA unidade — andam
  //    juntos de página (sem a imagem cortar na fronteira);
  // 4. **re-fluxo tardio**: fontes do livro (``document.fonts``) e
  //    recursos que chegam depois (``load``) trocam as métricas →
  //    âncoras recomputadas e página mantida.
  // Respiro nas bordas da página (px): ver item 2 acima.
  var EDGE = 3;
  function lineItems(c) {
    var clipRect = c.getBoundingClientRect();
    var off = c.scrollTop;
    var items = [];
    var walker = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
    var n;
    while ((n = walker.nextNode())) {
      if (!n.nodeValue || !n.nodeValue.trim()) continue;
      var r = document.createRange();
      r.selectNodeContents(n);
      var rects = r.getClientRects();
      for (var i = 0; i < rects.length; i++) {
        if (!rects[i] || rects[i].height <= 0) continue;
        items.push({
          top: rects[i].top - clipRect.top + off,
          bottom: rects[i].bottom - clipRect.top + off
        });
      }
    }
    var els = c.querySelectorAll("img, video, canvas, iframe, hr");
    for (var j = 0; j < els.length; j++) {
      var er = els[j].getBoundingClientRect();
      if (er.height <= 0) continue;
      items.push({ top: er.top - clipRect.top + off,
                   bottom: er.bottom - clipRect.top + off });
    }
    items.sort(function (x, y) { return (x.top - y.top) || (x.bottom - y.bottom); });
    // Pixels inteiros (o navegador arredonda o ``scrollTop``): topo para
    // baixo, base para cima — a faixa cobre a linha por FORA (seguro nas
    // duas bordas; nenhuma fração de pixel corta o texto).
    for (var q = 0; q < items.length; q++) {
      items[q].top = Math.floor(items[q].top);
      items[q].bottom = Math.ceil(items[q].bottom);
    }
    // Linhas visuais: itens que se sobrepõem verticalmente (ex.: imagem
    // inline + texto da MESMA linha) viram UMA unidade — a unidade anda
    // inteira de página (senão a fronteira poderia cortar a sobreposição).
    var merged = [];
    for (var m = 0; m < items.length; m++) {
      var last = merged[merged.length - 1];
      if (last && items[m].top < last.bottom) {
        if (items[m].bottom > last.bottom) last.bottom = items[m].bottom;
      } else {
        merged.push({ top: items[m].top, bottom: items[m].bottom });
      }
    }
    return merged;
  }
  // Âncoras (pixels inteiros): a[0] = 0 (topo da página 1); a[k] = o topo
  // da primeira linha que NÃO cabe na página k (com até EDGE px de respiro
  // acima desse topo), ou o fim do conteúdo, se tudo cabe →
  // página k = [a[k-1], a[k]) — todas as linhas desta faixa cabem inteiras
  // e com respiro nas bordas.
  function recomputeAnchors() {
    var c = clipEl();
    var H = c ? c.clientHeight : 0;
    var totalH = c ? c.scrollHeight : 0;
    if (!H || !totalH) {
      state.anchors = [0, 1];
      state.dirty = false;
      return;
    }
    var items = lineItems(c);
    var a = [0];
    var i = 0;
    var cur = 0;
    var guard = 0;
    while (cur < totalH - 0.5 && guard++ < 4000) {
      // base útil da página: H menos o respiro — a última linha visível
      // termina pelo menos EDGE px acima da base da caixa
      var bottom = cur + H - EDGE;
      // linhas que cabem: base <= base útil
      while (i < items.length && items[i].bottom <= bottom) i++;
      if (i < items.length) {
        // a primeira que NÃO cabe começa a próxima página — com respiro
        // no topo (até EDGE px acima do topo da linha) e SEMPRE acima da
        // base da linha anterior (a trava que impede cortá-la)
        var raw = items[i].top - EDGE;
        var floorA = (i > 0) ? items[i - 1].bottom + 1 : 0;
        // nunca acima do topo da linha (senão cortaria no topo) — o
        // respiro cede, o corte nunca
        var nxt = Math.min(Math.max(raw, floorA), items[i].top);
        if (nxt <= cur) {
          // item mais alto que a caixa (raro): avança a altura da caixa
          // (ele corta — inevitável para algo maior que a página)
          nxt = cur + H;
          i++;
        }
        cur = nxt;
      } else {
        cur = Math.ceil(totalH);  // sobra que coube toda: última página
      }
      a.push(cur);
    }
    if (a[a.length - 1] < totalH) a.push(Math.ceil(totalH));
    state.anchors = a;
    state.dirty = false;
  }
  function anchors() {
    if (state.dirty || !state.anchors) recomputeAnchors();
    return state.anchors;
  }
  function total() { return anchors().length - 1; }
  // Página (0-based) que contém o offset: maior k com a[k-1] <= off.
  function pageAt(off) {
    var a = anchors();
    var idx = 0;
    for (var i = a.length - 1; i >= 1; i--) {
      if (off >= a[i - 1] - 0.5) { idx = i - 1; break; }
    }
    return idx;
  }
  function info() {
    var c = clipEl();
    var sp = pageAt(c ? c.scrollTop : 0);
    if (sp > state.page) state.page = sp;  // qualquer deslocamento avança
    state.page = Math.min(total() - 1, state.page);
    // src = URL deste documento: o QML descarta respostas de documentos
    // antigos (durante a navegação a URL da view já é a nova, mas o DOM
    // ainda é o anterior — e responderia com estado velho)
    return { current: state.page + 1, total: total(), src: location.href };
  }
  function go(n) {
    var a = anchors();
    var t = a.length - 1;
    var target = Math.max(0, Math.min(t, Math.round(Number(n)) || 0));
    state.page = target;
    // Desloca o CLIP (janela de corte da caixa de leitura): mostra a
    // página (target+1) e corta o resto — o aro (borda de 32px) e o
    // viewport do documento não rolam (window.scrollY=0). O topo da
    // página é a âncora a[target]: uma fronteira de linha (nenhuma linha
    // cortada no topo; e nenhuma linha da página anterior foi cortada na
    // base, porque a âncora é o topo da primeira linha que NÃO coube).
    var c = clipEl();
    if (c) c.scrollTop = a[target];
    // Avisa o QML (contador "p. N/M" do rodapé): a troca aconteceu DENTRO
    // da página (roda/setas com foco) e o QML não veria sem este aviso.
    // Canal JS→Python = fragmento da URL (mesmo transporte de Ctrl+←/→ —
    // ver cmd() abaixo); o nº na fragment garante que a URL MUDA a cada
    // troca (replaceState idêntico não dispara urlChanged). O QML lê o
    // evento e re-sincroniza com pypub_info().
    cmd("page:" + (target + 1));
    return { current: target + 1, total: t };
  }
  function prevPage() {
    var c = clipEl();
    var off = (c ? c.scrollTop : 0) - (anchors()[state.page] || 0);
    // no meio da página → volta ao topo; no topo → página anterior
    return go(off > 5 ? state.page : state.page - 1);
  }
  // Re-fluxo (zoom / fonte / redimensionamento / fontes do livro):
  // as quebras de linha mudaram → recomputa as âncoras e MANTÉM a página
  // atual (clamp, se o conteúdo encolheu) — sem recarregar.
  function rebase() {
    state.rebases++;
    var a = anchors();
    if (state.page > a.length - 1) state.page = a.length - 1;
    var c = clipEl();
    if (c) c.scrollTop = a[state.page];
  }
  // Navegação UNIFICADA (como em leitor de documento): anda UMA etapa —
  // próxima/anterior página; nas bordas do capítulo, segue para o capítulo
  // vizinho (canal JS→Python via fragmento da URL; o QML troca o capítulo e
  // o novo documento começa na página 1 / o anterior na última vista).
  // delta: +1 (adiante) / −1 (atrás). Cooldown de 800 ms evita que o
  // mesmo gesto (ou a rolagem contínua no documento antigo, que segue vivo
  // enquanto o novo carrega) dispare duas trocas de capítulo seguidas.
  var chapterSentAt = 0;
  function step(delta) {
    delta = delta > 0 ? 1 : (delta < 0 ? -1 : 0);
    if (!delta) return null;
    var now = Date.now();
    if (delta > 0) {
      if (state.page < total() - 1) return go(state.page + 1);
      if (now - chapterSentAt < 800) return null;
      chapterSentAt = now;
      nextChapter();
      return { cmd: "next" };
    }
    if (state.page > 0) return go(state.page - 1);
    if (now - chapterSentAt < 800) return null;
    chapterSentAt = now;
    prevChapter();
    return { cmd: "prev" };
  }
  // Andar N etapas (N pode ser > 1: flick rápido da roda = várias páginas de
  // uma vez). Se alguma etapa atravessa a borda do capítulo (retorna
  // {cmd}), para logo: o documento novo já vem a caminho e este (que segue
  // vivo durante o carregamento) não pode disparar outra troca de capítulo.
  function stepMany(count) {
    var dir = count > 0 ? 1 : -1;
    var n = Math.abs(count);
    var i, r;
    for (i = 0; i < n; i++) {
      r = step(dir);
      if (r && r.cmd) break;
    }
    return r;
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
    // re-fluxo: as quebras de linha mudaram → novas âncoras; a página
    // atual é mantida (clamp) — ver rebase().
    state.dirty = true;
    rebase();
    return true;
  }

  // Fonte (Ctrl+1..9): troca a família da fonte do corpo SEM recarregar —
  // estilo inline no <body> (vence o CSS do livro, que é o ponto da escolha
  // explícita; o zoom continua dirigindo o TAMANHO via --pypub-zoom). O
  // re-embalinhamento muda o nº de páginas → o QML reavalia (refreshPageInfo).
  //   0 = padrão (a tipografia do próprio livro / fallback do leitor —
  //       limpa o estilo inline e devolve o comportamento original)
  //   1 = serifada (Charter)  2 = sans (Cantarell)  3 = mono (DejaVu)
  //   4 = Open Sans  5 = Mulish  6 = Vera Serif
  //   7 = JetBrains Mono  8 = Fira Code
  var FONTS = [
    null,
    '"Bitstream Charter", "Liberation Serif", "DejaVu Serif", "Noto Serif", serif',
    '"Cantarell", "Noto Sans", "Liberation Sans", "DejaVu Sans", sans-serif',
    '"DejaVu Sans Mono", "Liberation Mono", monospace',
    '"Open Sans", "Noto Sans", "Liberation Sans", sans-serif',
    '"Mulish", "Open Sans", "Noto Sans", sans-serif',
    '"Bitstream Vera Serif", "Liberation Serif", "DejaVu Serif", serif',
    '"JetBrains Mono", "JetBrainsMono Nerd Font", "Fira Code", "FiraCode Nerd Font", monospace',
    '"Fira Code", "FiraCode Nerd Font", "JetBrains Mono", "JetBrainsMono Nerd Font", monospace'
  ];
  function setFontIndex(i) {
    i = Math.floor(Number(i));
    if (!(i >= 0) || i >= FONTS.length) return false;
    var b = document.body;
    if (!b) return false;
    var f = FONTS[i];
    if (f) b.style.setProperty("font-family", f);
    else b.style.removeProperty("font-family");
    state.dirty = true;
    rebase();
    return true;
  }

  if (window.addEventListener) {
    // Guarda de segurança: o usuário NÃO rola livre (o clip é a janela de
    // corte), mas qualquer deslocamento que ande "à frente" do state é
    // seguido.
    window.addEventListener("scroll", function () {
      var c = clipEl();
      var sp = pageAt(c ? c.scrollTop : 0);
      if (sp > state.page) state.page = sp;
    });

    // Redimensionar a janela muda a altura da caixa → recomputa as âncoras
    // (com debounce) e mantém a página atual.
    var resizeT = 0;
    window.addEventListener("resize", function () {
      state.dirty = true;
      clearTimeout(resizeT);
      resizeT = setTimeout(rebase, 120);
    });

    // Re-fluxo TARDO (as âncoras podem ter sido medidas antes destes
    // eventos — com métricas de fallback — e linhas poderiam ficar
    // cortadas):
    //  • fontes do próprio livro (@font-face embutido) terminam de
    //    carregar → o texto re-quebra;
    //  • ``load``: recursos que ainda estavam pendentes (imagens
    //    externas, CSS) assentam o layout.
    // Nos dois casos: âncoras novas + página atual mantida (sem recarga).
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        state.dirty = true;
        rebase();
      });
    }
    window.addEventListener("load", function () {
      state.dirty = true;
      rebase();
    });

    // Roda do mouse = navegação UNIFICADA e PRECISA (como em leitor de
    // documento): a quantidade rolada vira PÁGINAS e é acumulada COM RESTO
    // fracionário entre os eventos do mesmo gesto — o avanço é PROPORCIONAL
    // ao que se rolou:
    //   • 1 dente padrão (120 px / 3 linhas) = 2 páginas (sensibilidade
    //     alta: 60 px ≈ 1 página);
    //   • flick rápido = várias páginas de uma vez (stepMany);
    //   • rolagem fina (trackpad) = fração de página que acumula até
    //     completar uma — controle sub-página, nada é "perdido".
    // O resto só carrega DENTRO do gesto (eventos a até WHEEL_GAP ms de
    // distância); um resto antigo não cancela o gesto novo. Nas bordas do
    // capítulo a etapa segue para o capítulo vizinho (mesmo step() das
    // teclas). O ratio páginas/dente é PAGES_PER_NOTCH (ajuste ao gosto).
    // Sempre preventDefault: o conteúdo NÃO rola livre.
    var PAGES_PER_NOTCH = 2;   // 1 dente padrão = 2 páginas (1 notch = 120 px
                               // = 3 linhas = 2 páginas, em qualquer deltaMode)
    var WHEEL_GAP = 500;       // ms: intervalo máximo dentro do MESMO gesto
    var wheelAcc = 0;          // rolagem acumulada, em PÁGINAS (com sinal)
    var wheelLast = 0;
    function deltaYtoPages(d, mode) {
      var perPage = (mode === 1) ? (1 / 3) : (mode === 2) ? 1 : (1 / 120);
      return d * perPage * PAGES_PER_NOTCH;
    }
    window.addEventListener("wheel", function (e) {
      if (!e) return;
      // Ctrl/Cmd+roda não tem função no PyPub (zoom é só por teclado:
      // Ctrl+=/-/0):
      // preventDefault suprime o zoom nativo do Chromium e o return impede
      // a paginação — só a roda simples pagina.
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      var d = e.deltaY || 0;
      if (d !== 0) {
        var now = Date.now();
        if (now - wheelLast > WHEEL_GAP) wheelAcc = 0;  // gesto novo: recomeça
        wheelAcc += deltaYtoPages(d, e.deltaMode || 0);
        wheelLast = now;
        if (wheelAcc >= 1) {
          var n = Math.floor(wheelAcc);
          stepMany(n);
          wheelAcc -= n;   // o resto fracionário carrega para o próximo evento
        } else if (wheelAcc <= -1) {
          var m = Math.floor(-wheelAcc);
          stepMany(-m);
          wheelAcc += m;
        }
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
      // PageUp/PageDown e setas simples = a mesma etapa unificada da roda
      // (página, ou capítulo na borda). (Ctrl+seta é salto direto de
      // capítulo — ver abaixo.) Se há seleção de texto ativa, as setas
      // estendem a seleção e não navegam.
      if (k === "PageDown") {
        step(1);
        e.preventDefault(); e.stopPropagation();
        return;
      }
      if (k === "PageUp") {
        step(-1);
        e.preventDefault(); e.stopPropagation();
        return;
      }
      if (!e.ctrlKey && !e.metaKey && (k === "ArrowRight" || k === "ArrowLeft")) {
        var sel = window.getSelection ? window.getSelection() : null;
        var selActive = !!(sel && sel.rangeCount > 0 && !sel.isCollapsed);
        if (!selActive) {
          step(k === "ArrowRight" ? 1 : -1);
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
        } else if (k === "=") {
          // tecla "=" → zoom IN (tamanho da fonte)
          zoomIn();
          e.preventDefault(); e.stopPropagation();
        } else if (k === "-") {
          // tecla "-" → zoom OUT (tamanho da fonte)
          zoomOut();
          e.preventDefault(); e.stopPropagation();
        } else if (k === "0") {
          zoomReset();
          e.preventDefault(); e.stopPropagation();
        } else if (k >= "1" && k <= "9") {
          // fonte (Ctrl+1..9): padrão / serif / sans / mono / Open Sans /
          // Mulish / Vera Serif / JetBrains Mono / Fira Code
          cmd("font" + k);
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
  // fonte: aplica a família escolhida (Ctrl+1..9) sem recarga
  window.pypub_setFontIndex = setFontIndex;
  // diagnóstico/teste: a fonte efetivamente aplicada no documento
  window.pypub_fontInfo = function () {
    var b = document.body;
    return {
      family: b ? (b.style.fontFamily || "") : "",
      computed: b ? getComputedStyle(b).fontFamily : ""
    };
  };
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
  // diagnóstico/teste: estado da paginação (bounds = âncoras do topo de
  // cada página, em coordenadas do documento; a última = fim do conteúdo)
  window.pypub_debug = function () {
    var c = clipEl();
    var a = anchors();
    return {
      page: state.page + 1,
      total: a.length - 1,
      clipTop: c ? c.scrollTop : 0,
      docScrollY: window.scrollY,
      bounds: a,
      rebases: state.rebases
    };
  };
})();
"""

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
    """Injeta o CSS do leitor (tema + zoom inicial) no capítulo."""
    zoom_rule = f":root {{ --pypub-zoom: {zoom:g}; }}"
    style = f"<style>{_BASE_CSS}{zoom_rule}{theme_css(dark)}</style>"
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


def inject_clip(html: str) -> str:
    """Envolve o conteúdo do capítulo na CAIXA DE LEITURA (``.pypub-clip``).

    O ``body`` vira o aro fixo (borda uniforme de 32px, que NÃO rola); o
    ``div.pypub-clip`` é a janela de corte (a página tem a altura do aro).
    Assim o texto fica sempre enquadrado nas bordas — mesmo durante a troca
    de página (rola o clip, não o aro).
    """
    m = re.search(r"<body[^>]*>", html, re.IGNORECASE)
    if not m:
        return html
    html = html[: m.end()] + '<div class="pypub-clip">' + html[m.end():]
    m2 = re.search(r"</body\s*>", html, re.IGNORECASE)
    if m2:
        html = html[: m2.start()] + "</div>" + html[m2.start():]
    return html


def prepare_chapter(
    raw_html: str,
    chapter_path: str,
    epub: Epub,
    dark: bool = False,
    zoom: float = 1.0,
) -> str:
    """Prepara o HTML do capítulo: recursos inline + CSS do leitor + paginação.

    O tema SEGUE o sistema operacional (``dark`` = modo escuro do OS);
    ``zoom`` fixa o estado INICIAL da fonte (enquadramento fixo em 1 coluna);
    os ajustes em runtime são feitos por ``pypub_setZoom`` no documento, sem
    recarga. O conteúdo fica dentro da CAIXA DE LEITURA (``.pypub-clip``
    dentro do ``body``): a borda uniforme de 32px não rola — a troca de
    página desloca o clip, mantendo o texto sempre enquadrado.
    """
    html = inline_local_resources(raw_html, chapter_path, epub)
    html = inject_css(html, dark, zoom)
    html = inject_page_js(html)
    return inject_clip(html)
