"""Teste headless: parser, bridge, WebEngine (renderização) e QML Kirigami.

Roda com QT_QPA_PLATFORM=offscreen.
"""

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PySide6

# Wheel do PySide6 primeiro (mesma build das libs), depois o QML do sistema (Kirigami).
os.environ["QML2_IMPORT_PATH"] = os.pathsep.join(
    [
        str(Path(PySide6.__file__).resolve().parent / "Qt" / "qml"),
        "/usr/lib/qt6/qml",
    ]
)

_TMP = tempfile.gettempdir()
SAMPLE = os.path.join(_TMP, "pypub_sample.epub")
SCREENSHOT = os.path.join(_TMP, "pypub_shot.png")

_LOG = logging.getLogger("pypub.test")

#: objetos C++ que precisam sobreviver ao fim das funções de teste
KEEP_ALIVE = []


def make_sample():
    import subprocess

    subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), "..", "tools", "make_sample_epub.py"), SAMPLE],
        check=True,
    )


def test_parser():
    from pypub.epub import Epub, EpubError, chapter_title
    from pypub.renderer import prepare_chapter

    book = Epub.open(SAMPLE)
    assert book.title == "EPUB de Exemplo", book.title
    assert book.creator == "Autore de Teste"
    assert len(book.chapters) == 3, book.chapters
    assert book.chapters[0].path == "OEBPS/chapter1.xhtml"
    assert book.has("OEBPS/cover.png")
    assert book.has("/OEBPS/cover.png")  # normalização
    t1 = chapter_title(book.read("OEBPS/chapter1.xhtml"))
    assert "Capítulo Um" in t1, t1

    # recursos locais inlineados (imagem + CSS interno do EPUB)
    html = prepare_chapter(book.read("OEBPS/chapter1.xhtml").decode("utf-8"), "OEBPS/chapter1.xhtml", book, dark=False)
    assert "data:image/png;base64," in html, "imagem não foi inlineada"
    assert "data:text/css;base64," in html, "CSS interno não foi inlineado"
    assert "max-width: 44em" in html, "CSS do leitor (enquadramento fixo) ausente"
    assert "columns: 2" not in html, "2 colunas não deve mais existir (enquadramento fixo)"
    book.close()

    # EPUB inválido
    try:
        Epub.open(os.path.join(_TMP, "nao_existe.epub"))
        raise AssertionError("deveria falhar")
    except EpubError:
        pass
    with open(os.path.join(_TMP, "falso.epub"), "wb") as f:
        f.write(b"isto nao e um zip")
    try:
        Epub.open(os.path.join(_TMP, "falso.epub"))
        raise AssertionError("deveria falhar")
    except EpubError:
        pass
    print("OK parser")


def test_epub2_real_file():
    """EPUB 2 real (namespace 2007/opf) — regredia com 'OPF sem <manifest>'."""
    import glob

    from pypub.epub import Epub, chapter_title
    from pypub.renderer import prepare_chapter

    hits = glob.glob(os.path.join(os.path.dirname(__file__), "*Žižek*.epub"))
    assert hits, "EPUB de teste (Žižek) não encontrado em tests/"
    path = hits[0]

    book = Epub.open(path)
    try:
        assert book.title == "O sublime objeto da ideologia", book.title
        assert book.creator == "Slavoj Žižek", book.creator
        assert len(book.chapters) == 18, len(book.chapters)

        # capa (1º item do spine) com imagem embutida
        first = book.chapters[0]
        html = prepare_chapter(book.read(first.path).decode("utf-8", "replace"), first.path, book, dark=False)
        assert "data:image/" in html, "capa não foi inlineada"
        assert "max-width: 44em" in html, "CSS do leitor (enquadramento fixo) ausente"

        # capítulo 2 (página de título) tem texto legível
        ch2 = book.chapters[1]
        t2 = chapter_title(book.read(ch2.path), fallback="?")
        assert "sublime objeto" in t2.lower(), t2
    finally:
        book.close()
    print("OK EPUB 2 (arquivo real, namespace 2007/opf)")


def wait_view_idle(reader, timeout=25.0):
    """Roda o event loop até a view parar de carregar (ou timeout)."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(50)
    deadline = time.time() + timeout

    def tick():
        if not bool(reader.property("loading")) or time.time() >= deadline:
            loop.quit()

    timer.timeout.connect(tick)
    timer.start()
    loop.exec()
    timer.stop()
    timer.deleteLater()
    return not bool(reader.property("loading"))


def view_js(win, script, timeout=20.0):
    """Executa JS na view (via função QML exposta no root) e retorna o resultado.

    O root do componente de teste expõe ``pendingScript``/``jsResult``;
    definir ``pendingScript`` dispara ``reader.runJavaScript`` no QML.
    """
    from PySide6.QtCore import QEventLoop, QTimer

    root = win
    root.setProperty("jsResult", None)
    # reset antes: garante que onPendingScriptChanged dispare mesmo com o mesmo script
    root.setProperty("pendingScript", "")
    root.setProperty("pendingScript", script)

    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(50)
    deadline = time.time() + timeout

    def tick():
        if root.property("jsResult") is not None or time.time() >= deadline:
            loop.quit()

    timer.timeout.connect(tick)
    timer.start()
    loop.exec()
    timer.stop()
    timer.deleteLater()
    return root.property("jsResult")


def wait_js(win, script, predicate, timeout=25.0, interval=0.1):
    """Repete ``view_js`` (processando o event loop) até ``predicate(result)``.

    Robusto à corrida entre o disparo do load (sinal -> QML) e a leitura da
    página: em vez de depender do estado ``loading``, aguarda o conteúdo.
    """
    deadline = time.time() + timeout
    last = None
    while True:
        last = view_js(win, script, timeout=3.0)
        try:
            if predicate(last):
                return last
        except Exception as exc:
            _LOG.debug("wait_js: predicate falhou para %r: %s", last, exc)
        if time.time() >= deadline:
            return last
        time.sleep(interval)


def build_qml_view(bridge, app, width=1400, height=900):
    """Cria uma QWebEngineView (QML) ligada ao contentChanged da bridge."""
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    src = f"""
import QtQuick
import QtWebEngine
Window {{
    id: rootItem
    width: {width}
    height: {height}
    property string pendingScript: ""
    property var jsResult: null
    property bool grabRequested: false
    property var grabbedImage: null

    onGrabRequestedChanged: {{
        if (grabRequested) {{
            grabRequested = false
            reader.grabToImage(function(img) {{ grabbedImage = img }})
        }}
    }}

    onPendingScriptChanged: {{
        if (pendingScript !== "") {{
            jsResult = null
            reader.runJavaScript(pendingScript, function(result) {{ jsResult = result }})
        }}
    }}

    WebEngineView {{
        id: reader
        objectName: "reader"
        anchors.fill: parent
        // zoom NÃO escala a view (enquadramento fixo em 1 coluna) — a fonte
        // é dirigida por pypub_setZoom no documento (ver applyZoomToPage no
        // Main.qml)
        zoomFactor: 1.0
        backgroundColor: bridge ? bridge.pageBackground : "white"
        Component.onCompleted: {{
            function onContent(url) {{
                reader.url = Qt.url(url)
            }}
            bridge.contentChanged.connect(onContent)
            if (bridge.contentUrl().length > 0)
                reader.url = Qt.url(bridge.contentUrl())
        }}
    }}
}}
"""
    engine = QQmlEngine(app)
    engine.rootContext().setContextProperty("bridge", bridge)
    from PySide6.QtCore import QUrl as _QUrl

    comp = QQmlComponent(engine)
    comp.setData(src.encode("utf-8"), _QUrl())
    if comp.isError():
        raise AssertionError(f"QML de teste inválido: {comp.errorString()}")
    root = comp.create()
    root.setProperty("visible", True)
    from PySide6.QtQuick import QQuickItem

    reader = root.findChild(QQuickItem, "reader")
    assert reader is not None, "reader não encontrado"
    # mantém engine/componente vivos (a árvore QML depende deles)
    KEEP_ALIVE.extend([engine, comp, root, reader])
    return root, reader


def test_bridge_and_render(app):
    from pypub.bridge import EpubBridge

    bridge = EpubBridge()
    assert not bridge.hasBook
    assert bridge.chapterCount == 0
    assert bridge.emptyHtml != ""

    # --- Tema único: segue o sistema operacional (claro/escuro) ---
    from pypub import renderer as _renderer
    from pypub.epub import Epub as _Epub

    # renderer: prepare_chapter no tema do sistema (fallback Noto Sans + paleta)
    b = _Epub.open(SAMPLE)
    try:
        raw = b.read("OEBPS/chapter1.xhtml").decode("utf-8")
        path = "OEBPS/chapter1.xhtml"
        html_def = _renderer.prepare_chapter(raw, path, b, dark=False)
        assert '"Noto Sans"' in html_def and "#fbfaf7" in html_def
    finally:
        b.close()

    # tema segue o sistema (sem alternância manual): offscreen → Unknown → claro
    assert isinstance(bridge.dark, bool)
    assert bridge.pageBackground in ("#fbfaf7", "#1f2328"), bridge.pageBackground

    # o handler do sinal colorSchemeChanged re-renderiza com o novo tema
    # (simula a troca no OS, sem depender da plataforma)
    import pypub.bridge as _bmod

    orig = _bmod._system_dark
    _bmod._system_dark = lambda: not bridge.dark
    try:
        before_url = bridge.contentUrl()
        bridge._on_system_color_scheme()
        assert bridge.dark != orig(), "tema não inverteu"
        assert bridge.pageBackground in ("#fbfaf7", "#1f2328")
        assert bridge.contentUrl() != before_url, "conteúdo não foi re-servido após troca de tema"
    finally:
        _bmod._system_dark = orig
        bridge._on_system_color_scheme()  # volta ao valor real do sistema

    # abre o livro com a view QML já ligada
    root, reader = build_qml_view(bridge, app)
    assert bridge.openFile(SAMPLE)
    assert bridge.hasBook
    assert bridge.chapterCount == 3
    assert bridge.currentIndex == 0
    assert "Capítulo Um" in str(bridge.currentChapterTitle or "")
    assert bridge.lastError == ""

    BODY_JS = "document.body ? document.body.innerHTML : ''"

    html = wait_js(root, BODY_JS, lambda h: "Capítulo Um" in (h or ""))
    assert "Capítulo Um" in (html or ""), "conteúdo não renderizado"
    assert "Parágrafo 1" in (html or ""), "texto do capítulo ausente"
    img_ok = wait_js(root, "var i = document.querySelector('img'); i ? (i.naturalWidth > 0) : false",
                     lambda v: v in (True, "true", "True"))
    assert img_ok in (True, "true", "True"), f"imagem não renderizou: {img_ok!r}"
    # zoom: 100% padrão → enquadramento fixo (1 coluna); a view não escala
    zi = wait_js(root, "typeof pypub_zoomInfo==='function' ? pypub_zoomInfo() : null",
                 lambda v: v is not None and v.get("zoom") == 1.0)
    assert zi and zi.get("columns") == 1, f"enquadramento fixo deveria ter 1 coluna: {zi!r}"
    assert abs(float(zi.get("fontSize", 0)) - 18.0) < 0.5, f"fonte base 18px: {zi!r}"

    # zoom out (0.9) → enquadramento FIXO (1 coluna) + fonte diminui
    zi = wait_js(root, "pypub_setZoom(0.9) ? pypub_zoomInfo() : null",
                 lambda v: v is not None and v.get("columns") == 1 and v.get("zoom") == 0.9)
    assert zi.get("zoom") == 0.9, f"zoom não aplicado: {zi!r}"
    assert abs(zi.get("fontSize", 0) - 16.2) < 0.5, f"fonte 16.2px em 90%: {zi!r}"

    # zoom in (1.1) → enquadramento fixo + fonte cresce
    zi = wait_js(root, "pypub_setZoom(1.1) ? pypub_zoomInfo() : null",
                 lambda v: v is not None and v.get("columns") == 1 and v.get("zoom") == 1.1)
    assert abs(zi.get("fontSize", 0) - 19.8) < 0.5, f"fonte 19.8px em 110%: {zi!r}"

    # clamps no JS: abaixo de 60% e acima de 250% o zoom é limitado
    # (o enquadramento é fixo — sempre 1 coluna — em qualquer faixa)
    zi = wait_js(root, "pypub_setZoom(0.3) ? pypub_zoomInfo() : null", lambda v: v is not None)
    assert zi.get("zoom") == 0.6 and zi.get("columns") == 1, f"clamp inferior: {zi!r}"
    zi = wait_js(root, "pypub_setZoom(9) ? pypub_zoomInfo() : null", lambda v: v is not None)
    assert zi.get("zoom") == 2.5 and zi.get("columns") == 1, f"clamp superior: {zi!r}"
    wait_js(root, "pypub_setZoom(1.0)", lambda v: v is True)

    # paginação (uma “página” = uma viewport de altura)
    info = wait_js(root, "typeof pypub_info === 'function' ? pypub_info() : null",
                   lambda v: v is not None and v.get("total", 0) >= 1)
    assert info and info.get("current") == 1, f"pypub_info() falhou: {info!r}"
    assert info.get("total", 0) >= 2, f"capítulo do exemplo deveria ter >= 2 páginas: {info!r}"

    res = wait_js(root, "typeof pypub_nextPage==='function'?pypub_nextPage():null",
                  lambda v: v is not None and v.get("current") == 2)
    assert res and res.get("current") == 2, f"pypub_nextPage() falhou: {res!r}"

    res = wait_js(root, "typeof pypub_prevPage==='function'?pypub_prevPage():null",
                  lambda v: v is not None and v.get("current") == 1)
    assert res and res.get("current") == 1, f"pypub_prevPage() falhou: {res!r}"

    # UMA PÁGINA POR VEZ: o body é o clip (overflow hidden + height 100%), o
    # documento NÃO transborda o viewport (sem rolagem livre/scrollbar) e a
    # troca de página desloca body.scrollTop — o viewport do documento fica
    # parado (window.scrollY = 0). Verificação geométrica: na página 2, o
    # primeiro bloco do capítulo está ACIMA do visível (foi cortado).
    assert wait_js(root, "window.getComputedStyle(document.body).overflowY",
                   lambda v: v in ("hidden",))
    assert wait_js(root, "document.documentElement.scrollHeight <= window.innerHeight + 2",
                   lambda v: v is True)
    wait_js(root, "typeof pypub_nextPage==='function'?pypub_nextPage():null",
            lambda v: v is not None and v.get("current") == 2)
    geo = wait_js(
        root,
        "JSON.stringify({"
        "winY: window.scrollY, "
        "clip: document.body ? document.body.scrollTop : -1, "
        "first: (function(){var el=document.querySelector('h1,h2,h3,p');"
        "return el ? el.getBoundingClientRect().top : null;})()"
        "})",
        lambda v: v is not None)
    g = json.loads(geo)
    assert g["winY"] == 0, f"o documento não deveria rolar (winY={g['winY']})"
    assert g["clip"] > 0, f"body.scrollTop deveria estar deslocado (clip={g['clip']})"
    assert g["first"] is None or g["first"] < 0, (
        f"na página 2 o primeiro bloco deveria estar acima do visível: {g!r}")
    wait_js(root, "typeof pypub_prevPage==='function'?pypub_prevPage():null",
            lambda v: v is not None and v.get("current") == 1)

    res = wait_js(root, "typeof pypub_goToPage==='function'?pypub_goToPage(3):null",
                  lambda v: v is not None and v.get("current") == 3)
    assert res and res.get("current") == 3, f"pypub_goToPage(3) falhou: {res!r}"

    # zoom NÃO reseta a posição de leitura (não há recarga: só a fonte muda;
    # enquadramento fixo) — em 0.8 o conteúdo encolhe e a página atual é
    # mantida (ou, no limite, clampada à última)
    wait_js(root, "pypub_setZoom(0.8)", lambda v: v is True)
    res = wait_js(root, "pypub_info()",
                  lambda v: v is not None and (v.get("current") == 3
                                               or v.get("current") == v.get("total")))
    assert res.get("current") == 3 or res.get("current") == res.get("total"), (
        f"zoom deslocou a leitura (esperado p.3 ou clamp, obtido "
        f"p.{res.get('current')} de {res.get('total')})")
    wait_js(root, "pypub_setZoom(1.0)", lambda v: v is True)

    # clamp: não passa da última nem fica antes da primeira
    # (autocontido: vai à última página e tenta avançar — o total depende do
    # zoom/enquadramento atual, então é lido em runtime)
    res = wait_js(root,
                  "(function(){if(typeof pypub_nextPage!=='function')return null;"
                  "pypub_goToPage(pypub_info().total);pypub_nextPage();return pypub_info();})()",
                  lambda v: v is not None)
    assert res and res.get("current") == res.get("total"), f"clamp superior falhou: {res!r}"
    res = wait_js(root,
                  "(function(){if(typeof pypub_goToPage!=='function')return null;"
                  "pypub_goToPage(1);pypub_prevPage();return pypub_info();})()",
                  lambda v: v is not None and v.get("current") == 1)
    assert res and res.get("current") == 1, f"clamp inferior falhou: {res!r}"

    # navegação
    bridge.next()
    assert bridge.currentIndex == 1
    html2 = wait_js(root, BODY_JS, lambda h: "Capítulo Dois" in (h or ""))
    assert "Capítulo Dois" in (html2 or "")

    bridge.previous()
    assert bridge.currentIndex == 0
    wait_js(root, BODY_JS, lambda h: "Capítulo Um" in (h or ""))

    # limites
    bridge.goTo(-5)
    assert bridge.currentIndex == 0
    bridge.goTo(99)
    assert bridge.currentIndex == 2
    wait_js(root, "document.body ? document.body.innerText : ''",
            lambda h: "Capítulo Três" in (h or ""))

    # zoom (bridge): faixa 60%–250%; o enquadramento é FIXO (sempre 1 coluna)
    # e o zoom muda só a fonte. NB: o PySide6 tipa o @Property como `Property`;
    # o acesso é dinâmico em runtime.
    bridge.zoomIn()  # 1.1 (enquadramento fixo)
    assert abs(float(bridge.zoom) - 1.1) < 1e-6  # type: ignore[call-arg]
    assert int(bridge.columnCount) == 1, "enquadramento fixo → 1 coluna"  # type: ignore[call-arg]
    bridge.zoomOut()  # 1.0 (enquadramento fixo)
    bridge.zoomOut()  # 0.9 (enquadramento fixo)
    assert int(bridge.columnCount) == 1, "enquadramento fixo → 1 coluna"  # type: ignore[call-arg]
    bridge.resetZoom()
    assert abs(float(bridge.zoom) - 1.0) < 1e-6  # type: ignore[call-arg]
    assert int(bridge.columnCount) == 1  # type: ignore[call-arg]

    # erro ao abrir livro inexistente
    assert not bridge.openFile(os.path.join(_TMP, "nao_existe.epub"))
    assert bridge.lastError != ""

    # livro real (EPUB 2): capítulo longo via servidor local + paginação
    import glob

    zizek = glob.glob(os.path.join(os.path.dirname(__file__), "*Žižek*.epub"))
    if zizek:
        assert bridge.openFile(zizek[0])
        # capítulo 4 é longo (~37 mil caracteres) → várias páginas
        bridge.goTo(4)
        info = wait_js(root, "typeof pypub_info === 'function' ? pypub_info() : null",
                       lambda v: v is not None and v.get("total", 0) >= 2, timeout=40.0)
        assert info and info.get("current") == 1, f"pypub_info (livro real) falhou: {info!r}"
        assert info.get("total", 0) >= 2, f"livro real deveria ter >= 2 páginas: {info!r}"
        res = wait_js(root, "typeof pypub_nextPage==='function'?pypub_nextPage():null",
                      lambda v: v is not None and v.get("current") == 2, timeout=40.0)
        assert res and res.get("current") == 2, f"nextPage (livro real) falhou: {res!r}"
        res = wait_js(root, "typeof pypub_prevPage==='function'?pypub_prevPage():null",
                      lambda v: v is not None and v.get("current") == 1, timeout=40.0)
        assert res and res.get("current") == 1, f"prevPage (livro real) falhou: {res!r}"

        # REGRESSÃO (centralização): o <body> deste livro tem class="calibre"
        # e o CSS do EPUB define ".calibre { margin: 0 5pt; padding: 0 }" —
        # classe (0-1-0) VENCE a antiga "body { margin: 0 auto }" (0-0-1) e a
        # página ficava colada à borda esquerda (left ≈ 6,7px = 5pt). O leitor
        # usa "body[class]" (0-1-1) para manter a centralização.
        cent = wait_js(
            root,
            "(function(){ var b = document.body.getBoundingClientRect(); "
            "return JSON.stringify({left: b.left, width: b.width, vw: window.innerWidth}); })()",
            lambda v: v is not None, timeout=10.0)
        c = json.loads(cent)
        right_margin = c["vw"] - c["left"] - c["width"]
        assert c["left"] > 10, f"página colada à borda esquerda (left={c['left']}px)"
        assert right_margin > 10, (f"página colada à borda direita "
                                  f"(margem={right_margin}px)")

    # screenshot da view renderizada (via função QML -> grabToImage)
    from PySide6.QtCore import QEventLoop, QTimer

    root.setProperty("grabbedImage", None)
    root.setProperty("grabRequested", True)

    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(50)
    deadline = time.time() + 25

    def wait_img():
        if root.property("grabbedImage") is not None or time.time() >= deadline:
            loop.quit()

    timer.timeout.connect(wait_img)
    timer.start()
    loop.exec()
    timer.stop()
    timer.deleteLater()

    from PySide6.QtGui import QImage

    grab = root.property("grabbedImage")
    assert grab is not None, "falha ao capturar screenshot"
    _img = grab.image
    img = _img() if callable(_img) else _img
    assert isinstance(img, QImage)
    assert img is not None and not img.isNull(), "imagem capturada nula"
    assert img.save(SCREENSHOT), "falha ao salvar screenshot"
    print("OK bridge + render (screenshot:", SCREENSHOT, ")")

    # fechar o livro emite estado vazio
    bridge.closeBook()
    assert not bridge.hasBook
    html_empty = wait_js(root, "document.body ? document.body.innerHTML : ''",
                         lambda h: "epub" in (h or "").lower())
    assert "epub" in (html_empty or "").lower(), "estado vazio não renderizado"
    root.deleteLater()
    bridge.shutdown()


def test_qml(app):
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    from pypub.bridge import EpubBridge

    engine = QQmlEngine(app)
    bridge = EpubBridge()
    engine.rootContext().setContextProperty("bridge", bridge)

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pypub", "qml", "Main.qml")
    comp = QQmlComponent(engine, QUrl.fromLocalFile(root))
    if comp.isError():
        print("ERRO QML:", comp.errorString())
        for e in comp.errors():
            print("  -", e)
        return 1
    from PySide6.QtQuick import QQuickWindow

    win = comp.create()
    assert win is not None
    assert isinstance(win, QQuickWindow)
    win.show()

    # abre um livro via bridge com a UI viva
    bridge.openFile(SAMPLE)
    # espera a view interna terminar de carregar
    from PySide6.QtQuick import QQuickItem

    reader = win.findChild(QQuickItem, "reader")
    assert reader is not None, "reader não encontrado na UI"
    wait_view_idle(reader)
    app.processEvents()

    def page_js(script):
        # hook dbgScript/dbgResult no Main.qml (runJavaScript a partir do
        # Python não entrega callback nesta view — QQuickWebEngineView)
        win.setProperty("dbgResult", None)
        win.setProperty("dbgScript", script)
        t0 = time.time()
        while time.time() - t0 < 10:
            app.processEvents()
            time.sleep(0.05)
            if win.property("dbgResult") is not None:
                break
        r = win.property("dbgResult")
        win.setProperty("dbgScript", "")
        return r

    def wait_current_document(timeout=30.0):
        # espera o documento carregado corresponder à URL atual da bridge;
        # sem isso, navegação em curso deixa o teste operando sobre o
        # documento anterior (a URL da view já é a nova, mas o DOM ainda é
        # o antigo — e history.replaceState nele não dispara urlChanged na
        # view, pois a URL dela já é a do documento novo)
        target = str(bridge.contentUrl() or "")
        if not target:
            return True
        t0 = time.time()
        while time.time() - t0 < timeout:
            href = str(page_js("location.href") or "")
            if href.split("#")[0] == target.split("#")[0]:
                return True
            app.processEvents()
            time.sleep(0.1)
        return False

    title = win.property("title") or ""
    assert "EPUB de Exemplo" in str(title), f"título inesperado: {title}"

    # REGRESSÃO (UI real): o indicador de página ("p. N/M") precisa ser
    # populado. A view termina "loading" antes do <script> de paginação
    # executar; o refresh único em onLoadingChanged ficava em 1/1. A correção
    # é o polling (pageInfoTimer) — se ele quebrar, pageCount não sobe de 1.
    import glob

    zizek = glob.glob(os.path.join(os.path.dirname(__file__), "*Žižek*.epub"))
    book = zizek[0] if zizek else SAMPLE
    if book != SAMPLE:
        bridge.openFile(book)
        bridge.goTo(4)  # capítulo longo

    # o documento novo precisa estar de fato carregado na view antes de
    # operar sobre ele (senão o teste age no documento anterior — a URL da
    # view já é a nova, mas o DOM ainda é o antigo)
    assert wait_current_document(), "documento do capítulo atual não carregou na view"

    t0 = time.time()
    while (win.property("pageCount") or 1) < 2 and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.1)
    assert (win.property("pageCount") or 1) >= 2, "indicador de página não populado na UI"
    t0 = time.time()
    while (win.property("currentPage") or 0) != 1 and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.05)
    assert (win.property("currentPage") or 0) == 1, "currentPage deveria começar em 1"

    # --- Indicador de CAPÍTULO no rodapé (mesmo padrão do "p. N/M") ---
    chap_label = win.findChild(QQuickItem, "chapterLabel")
    assert chap_label is not None, "indicador de capítulo não encontrado no rodapé"
    import re as _re

    m = _re.match(r"cap\. (\d+)/(\d+)", str(chap_label.property("text") or ""))
    assert m, f"indicador de capítulo com formato inesperado: {chap_label.property('text')!r}"
    assert int(m.group(1)) == int(bridge.currentIndex) + 1, (  # type: ignore[call-arg]
        f"nº do capítulo no rodapé ({m.group(1)}) != bridge ({int(bridge.currentIndex) + 1})"  # type: ignore[call-arg]
    )
    assert int(m.group(2)) == int(bridge.chapterCount), (  # type: ignore[call-arg]
        f"total de capítulos no rodapé ({m.group(2)}) != bridge ({int(bridge.chapterCount)})"  # type: ignore[call-arg]
    )

    # funções de página da UI (invocadas também pelos atalhos de janela)
    from PySide6.QtCore import QMetaObject, Qt

    QMetaObject.invokeMethod(win, "nextPage", Qt.ConnectionType.DirectConnection)
    t0 = time.time()
    while (win.property("currentPage") or 0) != 2 and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.05)
    assert (win.property("currentPage") or 0) == 2, "nextPage() (UI) não trocou a página"

    QMetaObject.invokeMethod(win, "prevPage", Qt.ConnectionType.DirectConnection)
    t0 = time.time()
    while (win.property("currentPage") or 0) != 1 and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.05)
    assert (win.property("currentPage") or 0) == 1, "prevPage() (UI) não trocou a página"

    # --- Setas na página (keydown do documento: a view tem o foco na leitura) ---
    # → próxima página, ← anterior (a troca de capítulo segue em Ctrl+seta)
    def cur_page():
        return json.loads(page_js("JSON.stringify(pypub_info())"))["current"]

    ab = cur_page()
    page_js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true, cancelable: true}))")
    time.sleep(0.4)
    app.processEvents()
    af = cur_page()
    assert af == ab + 1, f"ArrowRight não navegou: {ab} -> {af}"
    page_js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true, cancelable: true}))")
    time.sleep(0.4)
    app.processEvents()
    abk = cur_page()
    assert abk == ab, f"ArrowLeft não voltou: {af} -> {abk}"
    print("OK setas (ArrowRight/ArrowLeft:", ab, "→", af, "→", abk, ")")

    # O CONTADOR da UI (rodapé "p. N/M") tem que seguir a troca feita DENTRO
    # da página (setas/roda com foco): o canal é o fragmento da URL
    # (#pypub:cmd:page:N → onUrlChanged → refreshPageInfo). Sem ele o
    # indicador ficaria travado em "p. 1/N" (regressão reportada).
    t0 = time.time()
    while (win.property("currentPage") or 0) != abk and time.time() - t0 < 15:
        app.processEvents()
        time.sleep(0.05)
    assert (win.property("currentPage") or 0) == abk, (
        f"contador da UI não seguiu a troca na página "
        f"(UI p.{win.property('currentPage')} vs documento p.{abk})")

    # --- Roda do mouse: rolar = troca de página (não rolagem livre) ---
    # O evento de wheel vai à view e o <script> da página acumula o gesto
    # (120px/nota ≥ limiar → 1 página; cooldown de 350 ms por gesto).
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    def wheel_once(dy):
        pos = QPointF(float(reader.property("width")) / 2, 60.0)
        ev = QWheelEvent(pos, pos, QPoint(0, dy), QPoint(0, 0),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.ScrollUpdate, False)
        app.sendEvent(reader, ev)
        app.processEvents()
        time.sleep(0.1)
        app.processEvents()

    def wheel_and_wait(predicate, dy, timeout=20.0):
        # retry (a entrega de eventos sintéticos é instável no offscreen);
        # se a plataforma não encaminhar o wheel à página, valida o handler
        # direto no documento (mesma lógica: wheel → página)
        t0 = time.time()
        while time.time() - t0 < timeout:
            wheel_once(dy)
            deadline = time.time() + 1.5
            while time.time() < deadline:
                app.processEvents()
                time.sleep(0.05)
                if predicate():
                    return True
        page_js(f"window.dispatchEvent(new WheelEvent('wheel', {{deltaY: {dy}, deltaMode: 0, bubbles: true, cancelable: true}}))")
        deadline = time.time() + 3
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)
            if predicate():
                return True
        return predicate()

    wp = cur_page()
    assert wheel_and_wait(lambda: cur_page() > wp, +120), \
        f"roda do mouse (baixo) não avançou a página (p. {wp})"
    wp2 = cur_page()
    assert wheel_and_wait(lambda: cur_page() <= wp, -120), \
        f"roda do mouse (cima) não voltou a página (p. {wp2} -> esperado ≤ {wp})"
    print("OK roda do mouse (página", wp, "→", wp2, "→", cur_page(), ")")

    # o contador segue a roda do mouse também (o gesto principal de troca)
    wfin = cur_page()
    t0 = time.time()
    while (win.property("currentPage") or 0) != wfin and time.time() - t0 < 15:
        app.processEvents()
        time.sleep(0.05)
    assert (win.property("currentPage") or 0) == wfin, (
        f"contador da UI não seguiu a roda do mouse "
        f"(UI p.{win.property('currentPage')} vs documento p.{wfin})")

    # --- Roda do mouse UNIFICADA: nas bordas do capítulo, o gesto segue para
    # o capítulo vizinho (leitor de documento: um único fluxo de navegação) ---
    # fim do capítulo + roda para baixo → próximo capítulo (página 1);
    # topo do capítulo + roda para cima → capítulo anterior.
    chap0 = int(bridge.currentIndex)  # type: ignore[call-arg]
    n_chap = int(bridge.chapterCount)  # type: ignore[call-arg]

    def chap_idx():
        return int(bridge.currentIndex)  # type: ignore[call-arg]

    if chap0 < n_chap - 1:
        ti = json.loads(page_js("JSON.stringify(pypub_info())"))
        page_js(f"pypub_goToPage({int(ti['total'])})")  # vai à ÚLTIMA página
        t0 = time.time()
        while json.loads(page_js("JSON.stringify(pypub_info())"))["current"] != int(ti["total"]) \
                and time.time() - t0 < 10:
            app.processEvents()
            time.sleep(0.05)
        assert wheel_and_wait(lambda: chap_idx() == chap0 + 1, +120), \
            f"roda no FIM do capítulo {chap0} não avançou para o capítulo {chap0 + 1}"
        assert wait_current_document(), "capítulo seguinte não carregou na view"
        t0 = time.time()
        while (win.property("currentPage") or 0) != 1 and time.time() - t0 < 20:
            app.processEvents()
            time.sleep(0.05)
        assert (win.property("currentPage") or 0) == 1, \
            f"capítulo {chap0 + 1} não começou na página 1 (p.{win.property('currentPage')})"
        # topo + roda para cima → volta ao capítulo anterior
        assert wheel_and_wait(lambda: chap_idx() == chap0, -120), \
            f"roda no TOPO do capítulo {chap0 + 1} não voltou ao capítulo {chap0}"
        assert wait_current_document(), "capítulo anterior não recarregou na view"
        print("OK roda unificada (fim do cap.", chap0, "→ cap.", chap0 + 1, "p.1; "
              "topo do cap.", chap0 + 1, "→ cap.", chap0, ")")
    else:
        print("OK roda unificada (pulada: o livro está no capítulo final)")

    # --- Roda PRECISA: o avanço é PROPORCIONAL à rolagem (nada engessado) ---
    # • flick rápido (1 evento com 2 dentes, 2×120 px) → avança EXATAMENTE 4
    #   páginas (PAGES_PER_NOTCH = 2; não há trava de "1 página por gesto");
    # • frações de página acumulam DENTRO do mesmo gesto: 2× meia página
    #   (2×30 px) → 1 página no total (controle sub-página, nada perdido).
    # NB: o wheel é despachado DENTRO do documento (o offscreen não entrega
    # o QWheelEvent sintético à view — validado por probe; é o mesmo
    # fallback do wheel_and_wait acima). A LÓGICA testada é a da página JS.
    def wheel_js(dy):
        page_js(f"window.dispatchEvent(new WheelEvent('wheel', {{deltaY: {dy}, deltaMode: 0, bubbles: true, cancelable: true}}))")

    page_js("pypub_goToPage(1)")
    ti = json.loads(page_js("JSON.stringify(pypub_info())"))
    if int(ti["total"]) >= 4:
        pp = int(ti["current"])
        wheel_js(+240)  # 1 evento com 2 dentes (2 × 120 px)
        t0 = time.time()
        while cur_page() != pp + 4 and time.time() - t0 < 10:
            app.processEvents()
            time.sleep(0.05)
        assert cur_page() == pp + 4, \
            f"flick de 2 dentes não andou exatamente 4 páginas (p. {pp} -> {cur_page()})"
        ph = cur_page()
        wheel_js(+30)  # meia página (30 px) — não completa → nada anda
        time.sleep(0.15)  # < WHEEL_GAP (500 ms): mesmo gesto
        app.processEvents()
        assert cur_page() == ph, \
            f"meia página (30 px) não deveria completar uma página (p. {ph} -> {cur_page()})"
        wheel_js(+30)  # + meia página → completa 1 (resto acumulado)
        t0 = time.time()
        while cur_page() != ph + 1 and time.time() - t0 < 10:
            app.processEvents()
            time.sleep(0.05)
        assert cur_page() == ph + 1, \
            f"2× meia página não completaram 1 página (p. {ph} -> {cur_page()})"
        print("OK roda precisa (flick 2 dentes = 4 páginas; 2× meia página = 1 página)")
    else:
        print("OK roda precisa (pulada: capítulo curto, < 4 páginas)")

    # --- Ctrl+roda é intencionalmente IGNORADO (zoom é só Ctrl+=/-/0) ---
    # Não há atalho de roda para zoom: o handler da roda no DOCUMENTO ignora
    # eventos com Ctrl/Meta (preventDefault + return) — nem pagina, nem
    # dispara o zoom nativo de página do Chromium. O zoom de fonte continua
    # acessível por Ctrl+= / Ctrl+- / Ctrl+0 (testado logo abaixo).
    from PySide6.QtCore import QPoint, QPointF  # noqa: F811 (já importado acima)
    from PySide6.QtGui import QWheelEvent  # noqa: F811

    def ctrl_wheel(dy):
        pos = QPointF(float(reader.property("width")) / 2, 60.0)
        ev = QWheelEvent(pos, pos, QPoint(0, dy), QPoint(0, dy),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                         Qt.ScrollPhase.ScrollUpdate, False)
        app.sendEvent(reader, ev)
        deadline = time.time() + 5
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)

    bridge.resetZoom()
    page_js("pypub_goToPage(1)")
    time.sleep(0.3)
    app.processEvents()

    ctrl_wheel(-120)   # ↑: não deve zoomar (zoom é só por Ctrl+=/-/0)
    z = float(bridge.zoom)  # type: ignore[call-arg]
    assert abs(z - 1.0) < 1e-6, f"Ctrl+roda (cima) não deveria zoomar (zoom={bridge.zoom})"
    assert (win.property("currentPage") or 0) == 1, \
        f"Ctrl+roda (cima) trocou de página (p.{win.property('currentPage')})"

    ctrl_wheel(+120)   # ↓: também não deve zoomar
    z = float(bridge.zoom)  # type: ignore[call-arg]
    assert abs(z - 1.0) < 1e-6, f"Ctrl+roda (baixo) não deveria zoomar (zoom={bridge.zoom})"
    assert (win.property("currentPage") or 0) == 1, \
        f"Ctrl+roda (baixo) trocou de página (p.{win.property('currentPage')})"

    # no documento: Ctrl+wheel que chegue à página não pagina
    page_js("pypub_goToPage(1)")
    time.sleep(0.3)
    app.processEvents()
    page_js("window.dispatchEvent(new WheelEvent('wheel', {deltaY: 120, deltaMode: 0, ctrlKey: true, bubbles: true, cancelable: true}))")
    time.sleep(0.5)
    app.processEvents()
    ti = json.loads(page_js("JSON.stringify(pypub_info())"))
    assert int(ti["current"]) == 1, \
        f"Ctrl+roda no documento trocou de página (p.{ti['current']})"

    bridge.resetZoom()
    print("OK Ctrl+roda é ignorado (nem zoom, nem página); zoom só por Ctrl+=/-/0")

    # --- Canal JS→Python via fragmento de URL (Ctrl+→ no documento) ---
    # O QWebChannel não funciona neste wheel do PySide6 (tipo QML quebrado;
    # webChannel exige QQmlWebChannel, sem binding Python). A página escreve
    # #pypub:cmd:next na URL (history.replaceState) e o onUrlChanged do QML
    # executa a ação — testando o caminho completo de troca de capítulo.
    # NB: bridge.currentIndex é um Property dinâmico do PySide6 — cast para int
    i0 = int(bridge.currentIndex)  # type: ignore[call-arg]
    page_js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', ctrlKey: true, bubbles: true}))")
    t0 = time.time()
    while int(bridge.currentIndex) == i0 and time.time() - t0 < 20:  # type: ignore[call-arg]
        app.processEvents()
        time.sleep(0.1)
    i1 = int(bridge.currentIndex)  # type: ignore[call-arg]
    assert i1 == i0 + 1, f"Ctrl+→ não trocou o capítulo ({i0} -> {i1})"
    # o capítulo novo ainda está carregando — o próximo keydown (Ctrl+=)
    # precisa chegar ao documento novo; no documento antigo, replaceState
    # não dispara urlChanged na view (a URL dela já é a do capítulo novo)
    assert wait_current_document(), "documento do capítulo novo não carregou na view"
    print("OK canal JS→Python (Ctrl+→ : capítulo", i0, "→", i1, ")")

    # --- Zoom via teclado (Ctrl+= no documento) → canal hash → bridge ---
    # A página escreve #pypub:cmd:zoomin na URL; o onUrlChanged do QML chama
    # bridge.zoomIn(); o onZoomChanged aplica pypub_setZoom no documento
    # (só a fonte muda; enquadramento fixo; sem recarga).
    z0 = float(bridge.zoom)  # type: ignore[call-arg]
    page_js("document.dispatchEvent(new KeyboardEvent('keydown', {key: '=', ctrlKey: true, bubbles: true}))")
    t0 = time.time()
    while abs(float(bridge.zoom) - z0) < 1e-9 and time.time() - t0 < 20:  # type: ignore[call-arg]
        app.processEvents()
        time.sleep(0.1)
    z1 = float(bridge.zoom)  # type: ignore[call-arg]
    assert z1 > z0, f"Ctrl+= não deu zoom in ({z0} -> {z1})"
    zi = json.loads(page_js("JSON.stringify(pypub_zoomInfo())"))
    assert abs(zi["zoom"] - z1) < 0.01, f"documento ({zi['zoom']}) != bridge ({z1})"
    assert zi["columns"] == 1, f"enquadramento fixo → 1 coluna: {zi!r}"
    bridge.resetZoom()
    app.processEvents()
    print("OK zoom via teclado (Ctrl+= :", z0, "→", z1, "; documento:", zi["zoom"], ")")

    # --- Fonte via teclado (Ctrl+2 no documento) → canal hash → bridge ---
    # Ctrl+1..4 troca a família da fonte (padrão/serif/sans/mono): a página
    # escreve #pypub:cmd:fontN na URL; o onUrlChanged do QML chama
    # bridge.setFontIndex(); o onFontChanged aplica pypub_setFontIndex no
    # documento (estilo inline no <body>; sem recarga). Ctrl+1 volta à
    # tipografia do próprio livro (limpa o inline).
    f0 = int(bridge.fontIndex)  # type: ignore[call-arg]
    page_js("document.dispatchEvent(new KeyboardEvent('keydown', {key: '2', ctrlKey: true, bubbles: true}))")
    t0 = time.time()
    while int(bridge.fontIndex) == f0 and time.time() - t0 < 20:  # type: ignore[call-arg]
        app.processEvents()
        time.sleep(0.1)
    f1 = int(bridge.fontIndex)  # type: ignore[call-arg]
    assert f1 == 1, f"Ctrl+2 não trocou a fonte (índice {f0} -> {f1})"
    fi = json.loads(page_js("JSON.stringify(pypub_fontInfo())"))
    assert "Serif" in fi["computed"] or "Charter" in fi["computed"], (
        f"documento sem fonte serifada: {fi!r}")
    # a paginação continua funcionando com a fonte nova
    ti2 = json.loads(page_js("JSON.stringify(pypub_info())"))
    assert int(ti2["total"]) >= 2, f"paginação quebrou com a fonte nova: {ti2!r}"
    # Ctrl+1 → padrão (limpa o estilo inline; tipografia do livro volta)
    page_js("document.dispatchEvent(new KeyboardEvent('keydown', {key: '1', ctrlKey: true, bubbles: true}))")
    t0 = time.time()
    while int(bridge.fontIndex) == f1 and time.time() - t0 < 20:  # type: ignore[call-arg]
        app.processEvents()
        time.sleep(0.1)
    f2 = int(bridge.fontIndex)  # type: ignore[call-arg]
    assert f2 == 0, f"Ctrl+1 não voltou à fonte padrão (índice {f1} -> {f2})"
    fi = json.loads(page_js("JSON.stringify(pypub_fontInfo())"))
    assert fi["family"] == "", f"Ctrl+1 deveria limpar a fonte inline: {fi!r}"
    print("OK fonte via teclado (Ctrl+2: serif no documento; Ctrl+1: volta ao padrão)")

    print("OK QML Kirigami (janela criada, livro aberto na UI, título =", title,
          "| indicador p.", win.property("currentPage"), "/", win.property("pageCount"), ")")
    # Teardown do WebEngine: se o renderer ainda estiver ocupado com a última
    # navegação, a destruição dos objetos segfaulta no offscreen (validado:
    # crash 3/3 sem settling; limpo 3/3 com). Garante que o documento responde
    # e dá tempo para o compositor assentar antes de desligar.
    try:
        page_js("typeof pypub_info==='function' ? JSON.stringify(pypub_info()) : null")
    except Exception as exc:
        _LOG.debug("settling: ping do documento falhou: %s", exc)
    t0 = time.time()
    while time.time() - t0 < 2.0:
        app.processEvents()
        time.sleep(0.1)
    bridge.shutdown()
    # fecha a JANELA antes do fim do processo: o teardown do WebEngine com a
    # view ainda viva segfaulta no offscreen (teardown mínimo com close limpa)
    win.close()
    app.processEvents()
    # mantém os objetos C++ vivos até o fim do processo (mesmo padrão do
    # test_bridge_and_render): liberá-los ao retornar da função faz o
    # teardown do WebEngine colidir com o shutdown do interpretador
    KEEP_ALIVE.extend([engine, comp, win, bridge])
    return 0


def main():
    make_sample()
    test_parser()
    test_epub2_real_file()

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    try:
        test_bridge_and_render(app)
        rc = test_qml(app)
        return rc
    finally:
        # NB: sem processEvents aqui — test_qml já esvaziou a fila após
        # win.close(); chamá-la de novo com o WebEngine em teardown
        # segfaulta no offscreen (validado: crash em main/finally).
        pass


if __name__ == "__main__":
    sys.exit(main())
