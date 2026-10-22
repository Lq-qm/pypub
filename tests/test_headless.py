"""Teste headless: parser, bridge, WebEngine (renderização) e QML Kirigami.

Roda com QT_QPA_PLATFORM=offscreen.
"""

import json
import os
import sys
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

SAMPLE = "/tmp/pypub_sample.epub"
SCREENSHOT = "/tmp/pypub_shot.png"

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
    html = prepare_chapter(book.read("OEBPS/chapter1.xhtml").decode("utf-8"), "OEBPS/chapter1.xhtml", book, False)
    assert "data:image/png;base64," in html, "imagem não foi inlineada"
    assert "data:text/css;base64," in html, "CSS interno não foi inlineado"
    assert "columns: 2" in html, "CSS responsivo (2 colunas) ausente"
    book.close()

    # EPUB inválido
    try:
        Epub.open("/tmp/nao_existe.epub")
        raise AssertionError("deveria falhar")
    except EpubError:
        pass
    with open("/tmp/falso.epub", "wb") as f:
        f.write(b"isto nao e um zip")
    try:
        Epub.open("/tmp/falso.epub")
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
        html = prepare_chapter(book.read(first.path).decode("utf-8", "replace"), first.path, book, False)
        assert "data:image/" in html, "capa não foi inlineada"
        assert "columns: 2" in html, "CSS responsivo ausente"

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
        except Exception:
            pass
        if time.time() >= deadline:
            return last
        time.sleep(interval)


def build_qml_view(bridge, app, width=1400, height=900):
    """Cria uma QWebEngineView (QML) ligada ao contentChanged da bridge."""
    from PySide6.QtQml import QQmlEngine, QQmlComponent

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
        zoomFactor: bridge ? bridge.zoom : 1.0
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
    cols = wait_js(root, "getComputedStyle(document.body).columnCount", lambda c: str(c) == "2")
    assert str(cols) == "2", f"esperado 2 colunas (largura 1400), obtido: {cols!r}"

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

    res = wait_js(root, "typeof pypub_goToPage==='function'?pypub_goToPage(3):null",
                  lambda v: v is not None and v.get("current") == 3)
    assert res and res.get("current") == 3, f"pypub_goToPage(3) falhou: {res!r}"

    # clamp: não passa da última nem fica antes da primeira
    res = wait_js(root,
                  "(function(){if(typeof pypub_nextPage!=='function')return null;"
                  "pypub_nextPage();pypub_nextPage();pypub_nextPage();return pypub_info();})()",
                  lambda v: v is not None)
    assert res and res.get("current") == info["total"], f"clamp superior falhou: {res!r}"
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

    # zoom (a view QML segue bridge.zoom via binding)
    # NB: o PySide6 tipa o @Property como `Property`; o acesso é dinâmico em runtime.
    bridge.zoomIn()
    assert abs(float(bridge.zoom) - 1.1) < 1e-6  # type: ignore[call-arg]
    bridge.resetZoom()
    assert abs(float(bridge.zoom) - 1.0) < 1e-6  # type: ignore[call-arg]

    # erro ao abrir livro inexistente
    assert not bridge.openFile("/tmp/nao_existe.epub")
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
    from PySide6.QtQml import QQmlEngine, QQmlComponent
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

    t0 = time.time()
    while (win.property("pageCount") or 1) < 2 and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.1)
    assert (win.property("pageCount") or 1) >= 2, "indicador de página não populado na UI"
    assert (win.property("currentPage") or 0) == 1, "currentPage deveria começar em 1"

    # caminho das funções da UI (o mesmo usado pelos botões da toolbar)
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

    # --- ToolButton (regressão: K.ActionToolBar/K.Action não renderizam neste
    # ambiente — botões plain do QtQuick.Controls são o caminho validado) ---
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    def find_button(text):
        for o in win.findChildren(QQuickItem):
            if o.metaObject().className().startswith("ToolButton") and o.property("text") == text:
                return o
        return None

    def click_item(item):
        x = float(item.property("width")) / 2
        y = float(item.property("height")) / 2
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y), QPointF(x, y),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(x, y), QPointF(x, y), QPointF(x, y),
                              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(item, press)
        app.processEvents()
        app.sendEvent(item, release)
        app.processEvents()

    next_btn = find_button("Pág. próxima")
    assert next_btn is not None, "ToolButton 'Pág. próxima' não encontrado na toolbar"
    assert bool(next_btn.property("visible")), "ToolButton deveria ser visível"
    assert bool(next_btn.property("enabled")), "ToolButton deveria estar habilitado"
    click_item(next_btn)
    t0 = time.time()
    while (win.property("currentPage") or 0) != 2 and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.05)
    assert (win.property("currentPage") or 0) == 2, "clique no ToolButton não trocou a página"
    print("OK ToolButton clicável (Pág. próxima: 1 → 2)")

    # --- Teclado na página (keydown do documento: a view tem o foco na leitura) ---
    def page_js(script):
        # hook dbgScript/dbgResult no Main.qml (runJavaScript a partir do Python
        # não entrega callback nesta view — QQuickWebEngineView)
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

    before = json.loads(page_js("JSON.stringify(pypub_info())"))
    page_js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'PageDown', bubbles: true}))")
    time.sleep(0.5)
    app.processEvents()
    after = json.loads(page_js("JSON.stringify(pypub_info())"))
    assert after["current"] == before["current"] + 1, f"PageDown não navegou: {before} -> {after}"
    page_js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'PageUp', bubbles: true}))")
    time.sleep(0.5)
    app.processEvents()
    back = json.loads(page_js("JSON.stringify(pypub_info())"))
    assert back["current"] == before["current"], f"PageUp não voltou: {after} -> {back}"
    print("OK teclado na página (PageDown/PageUp:", before["current"], "→", after["current"], "→", back["current"], ")")

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
    print("OK canal JS→Python (Ctrl+→ : capítulo", i0, "→", i1, ")")

    print("OK QML Kirigami (janela criada, livro aberto na UI, título =", title,
          "| indicador p.", win.property("currentPage"), "/", win.property("pageCount"), ")")
    bridge.shutdown()
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
        app.processEvents()


if __name__ == "__main__":
    sys.exit(main())
