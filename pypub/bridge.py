"""Ponte QObject entre o núcleo Python (parser EPUB + preparação HTML) e a
interface QML/Kirigami.

A view de renderização é a ``QWebEngineView`` do QML (módulo ``QtWebEngine``);
a ponte emite ``contentChanged(url)`` sempre que o conteúdo a exibir muda.

O HTML pronto é servido por um servidor HTTP local (``pypub.server``) em
``127.0.0.1``: ``loadHtml`` é instável para conteúdo grande no Qt 6.12 e a
view QML não expõe ``load(url)``, mas atribuir a propriedade ``url`` da view
navega normalmente — sem limite de tamanho.
"""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot

from .epub import Epub, EpubError, chapter_title
from .renderer import columns_for_zoom, empty_html, page_background, prepare_chapter
from .server import ChapterServer

_log = logging.getLogger(__name__)

_ZOOM_MIN = 0.6
_ZOOM_MAX = 2.5
_ZOOM_STEP = 0.1


def _system_dark() -> bool:
    """Esquema de cor do **sistema operacional** (via tema de plataforma do Qt).

    ``QStyleHints.colorScheme()`` reflete o esquema global do OS (no KDE, a
    ``KColorScheme`` — ex.: Breeze/BreezeDark; no GTK, a variante dark). Em
    plataformas que não expõem (ex.: ``offscreen``, usada nos testes), o valor
    é ``Unknown`` → padrão claro.

    NB: o PySide6 não expõe as constantes do enum ``ColorScheme`` (nem na
    classe nem no módulo), então a comparação é feita no nome (``str`` devolve
    ``"ColorScheme.Dark"`` / ``"ColorScheme.Light"`` / ``"ColorScheme.Unknown"``).
    """
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            # o linter tipa QApplication.instance() como QCoreApplication
            # (sem styleHints); em runtime é QApplication (QtWidgets)
            hints = app.styleHints()  # type: ignore[union-attr]
            return "Dark" in str(hints.colorScheme())
    except Exception:
        _log.debug("falha ao ler o esquema de cor do sistema; padrão claro", exc_info=True)
    return False


class _CtrlWheelGuard(QObject):
    """Intercepta **Ctrl+roda** na view ANTES do Chromium e da ``<script>`` da página.

    Sem isso, dois atalhos disparam juntos: o Chromium aplica o seu zoom
    nativo de página (fora do modelo "zoom muda só a fonte" e que resetaria
    a cada capítulo) e a ``<script>`` da página troca de página. Com o
    filter, Ctrl+roda vira o MESMO zoom de ``Ctrl+=/-/0`` (fonte, com o %
    do rodapé) e a roda simples continua paginando no documento.

    O filter roda em ``QObject::notify`` — SEMPRE antes de
    ``QQuickWebEngineView.event()`` (que entrega o wheel ao web content),
    então vale tanto para eventos reais quanto para testes offscreen
    (``app.sendEvent(view, QWheelEvent(...))``).
    """

    def __init__(self, bridge: EpubBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge

    def eventFilter(self, obj: QObject, ev: object) -> bool:  # type: ignore[override]
        try:
            from PySide6.QtGui import QWheelEvent

            if isinstance(ev, QWheelEvent):
                mods = ev.modifiers()
                if mods & (Qt.KeyboardModifier.ControlModifier
                           | Qt.KeyboardModifier.MetaModifier):
                    dy = ev.angleDelta().y()
                    if dy != 0 and self._bridge.hasBook:
                        if dy < 0:
                            self._bridge.zoomIn()
                        else:
                            self._bridge.zoomOut()
                    # consumido: nem o zoom nativo do Chromium, nem a
                    # troca de página da <script> da página acontecem
                    return True
        except Exception:
            _log.debug("falha no event filter de Ctrl+roda", exc_info=True)
        return False


class EpubBridge(QObject):
    """Estado e ações expostos ao QML (context property ``bridge``)."""

    bookChanged = Signal()
    positionChanged = Signal()
    zoomChanged = Signal()
    darkChanged = Signal()
    errorChanged = Signal()
    #: emitido com o HTML pronto para renderização (incluindo estado vazio)
    contentChanged = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._epub: Epub | None = None
        self._index = 0
        self._zoom = 1.0
        # tema de leitura: único, segue o **sistema** (claro/escuro)
        self._dark = _system_dark()
        self._last_error = ""
        self._last_dir = ""
        self._server = ChapterServer()
        self._rev = 0
        self._last_url = ""
        # event filter Ctrl+roda (instalado em attachWheelGuard, pelo QML)
        self._wheel_guard: _CtrlWheelGuard | None = None
        # estado inicial: página vazia já servida e URL válida para o QML
        self._emit_content()
        # tema do OS pode mudar com o app aberto (ex.: alternar Breeze/BreezeDark
        # no Plasma): o sinal é emitido ao vivo e a gente re-renderiza
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                # type: ignore[union-attr] — ver _system_dark()
                app.styleHints().colorSchemeChanged.connect(self._on_system_color_scheme)  # type: ignore[union-attr]
        except Exception:
            _log.debug("sem sinal colorSchemeChanged; tema fixo no valor inicial", exc_info=True)

    # ------------------------------------------------------------ properties
    @Property(bool, notify=bookChanged)
    def hasBook(self) -> bool:
        return self._epub is not None

    @Property(str, notify=bookChanged)
    def bookTitle(self) -> str:
        return self._epub.title if self._epub else ""

    @Property(str, notify=bookChanged)
    def bookCreator(self) -> str:
        return self._epub.creator if self._epub else ""

    @Property(int, notify=positionChanged)
    def chapterCount(self) -> int:
        return len(self._epub.chapters) if self._epub else 0

    @Property(int, notify=positionChanged)
    def currentIndex(self) -> int:
        return self._index

    @Property(str, notify=positionChanged)
    def currentChapterTitle(self) -> str:
        if self._epub is None:
            return ""
        ch = self._epub.chapters[self._index]
        try:
            return chapter_title(self._epub.read(ch.path), fallback=ch.path.rsplit("/", 1)[-1])
        except KeyError:
            return ch.path.rsplit("/", 1)[-1]

    @Property(str, notify=errorChanged)
    def lastError(self) -> str:
        return self._last_error

    @Property(bool, notify=darkChanged)
    def dark(self) -> bool:
        return self._dark

    @Property(str, notify=darkChanged)
    def emptyHtml(self) -> str:
        return empty_html(self._dark)

    @Property(str, notify=darkChanged)
    def pageBackground(self) -> str:
        return page_background(self._dark)

    @Property(float, notify=zoomChanged)
    def zoom(self) -> float:
        return self._zoom

    @Property(int, notify=zoomChanged)
    def columnCount(self) -> int:
        """Enquadramento FIXO: sempre 1 coluna (o zoom muda só a fonte)."""
        return columns_for_zoom(self._zoom)

    @Property(str, notify=bookChanged)
    def chaptersJson(self) -> str:
        """Lista de capítulos (``index`` + ``title``) em JSON.

        PySide6 → QML não entrega lista de objetos de forma simples; JSON é o
        formato mais robusto (consumo QML faz ``JSON.parse(bridge.chaptersJson)``).
        """
        if self._epub is None:
            return "[]"
        out: list[dict[str, object]] = []
        for i, ch in enumerate(self._epub.chapters):
            fallback = ch.path.rsplit("/", 1)[-1]
            try:
                title = chapter_title(self._epub.read(ch.path), fallback=fallback)
            except KeyError:
                title = fallback
            out.append({"index": i, "title": title})
        return json.dumps(out, ensure_ascii=False)

    # ----------------------------------------------------------------- slots
    @Slot(str, result=bool)
    def openFile(self, path: str) -> bool:
        try:
            epub = Epub.open(path)
        except EpubError as e:
            self._set_error(str(e))
            return False
        self._set_error("")
        self._epub = epub
        self._index = 0
        self.bookChanged.emit()
        self.positionChanged.emit()
        self._emit_content()
        return True

    @Slot()
    def closeBook(self) -> None:
        if self._epub is None:
            return
        epub, self._epub = self._epub, None
        epub.close()
        self._index = 0
        self.bookChanged.emit()
        self.positionChanged.emit()
        self._emit_content()

    @Slot()
    def chooseFile(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None,
            "Abrir EPUB",
            self._last_dir,
            "EPUB (*.epub);;Todos os arquivos (*)",
        )
        if path:
            self._last_dir = path.rsplit("/", 1)[0]
            self.openFile(path)

    @Slot()
    def previous(self) -> None:
        self.goTo(self._index - 1)

    @Slot()
    def next(self) -> None:
        self.goTo(self._index + 1)

    @Slot(int)
    def goTo(self, index: int) -> None:
        if self._epub is None:
            return
        index = max(0, min(len(self._epub.chapters) - 1, int(index)))
        if index == self._index:
            return
        self._index = index
        self.positionChanged.emit()
        self._emit_content()

    @Slot(result=str)
    def currentHtml(self) -> str:
        """HTML pronto do capítulo atual (ou estado vazio) — tema do sistema."""
        if self._epub is None:
            return empty_html(self._dark)
        ch = self._epub.chapters[self._index]
        try:
            raw = self._epub.read(ch.path)
        except KeyError:
            return empty_html(self._dark)
        return prepare_chapter(
            raw.decode("utf-8", "replace"),
            ch.path,
            self._epub,
            dark=self._dark,
            zoom=self._zoom,
        )

    @Slot(result=str)
    def contentUrl(self) -> str:
        """URL local do conteúdo atual (para navegação inicial da view)."""
        return self._last_url

    @Slot()
    def zoomIn(self) -> None:
        self._set_zoom(self._zoom + _ZOOM_STEP)

    @Slot()
    def zoomOut(self) -> None:
        self._set_zoom(self._zoom - _ZOOM_STEP)

    @Slot()
    def resetZoom(self) -> None:
        self._set_zoom(1.0)

    @Slot(QObject)
    def attachWheelGuard(self, view: QObject) -> None:
        """Instala o guard de Ctrl+roda na ``WebEngineView`` (chamado do QML).

        O guard (``_CtrlWheelGuard``) consome o wheel com Ctrl/Meta ANTES
        do Chromium (zoom nativo de página) e ANTES da ``<script>`` da
        página (troca de página), e o converte no zoom de fonte do app —
        os dois atalhos (página vs. zoom) deixam de disparar juntos.
        """
        if self._wheel_guard is not None:
            view.removeEventFilter(self._wheel_guard)
        self._wheel_guard = _CtrlWheelGuard(self, view)
        view.installEventFilter(self._wheel_guard)

    def _on_system_color_scheme(self) -> None:
        """O esquema do OS mudou → re-renderiza (o tema segue o sistema)."""
        value = _system_dark()
        if value == self._dark:
            return
        self._dark = value
        self.darkChanged.emit()
        self._emit_content()

    # ------------------------------------------------------------------ utils
    def _set_zoom(self, value: float) -> None:
        # A mudança chega ao documento via QML → pypub_setZoom (só a FONTE;
        # enquadramento fixo em 1 coluna) SEM recarregar a página; o estado
        # inicial de uma renderização nova já sai certo (currentHtml passa o zoom).
        value = max(_ZOOM_MIN, min(_ZOOM_MAX, round(value, 2)))
        if abs(value - self._zoom) < 1e-9:
            return
        self._zoom = value
        self.zoomChanged.emit()

    def _set_error(self, message: str) -> None:
        if message == self._last_error:
            return
        self._last_error = message
        self.errorChanged.emit()

    def _emit_content(self) -> None:
        """Serve o HTML pronto atual e emite a URL para a view navegar.

        A URL muda a cada emissão (``?r=<revisão>``): atribuir a mesma URL
        de novo não recarrega a view, então a revisão garante o reload
        (zoom NÃO recarrega: é aplicado no documento via pypub_setZoom).
        """
        if self._epub is not None:
            ch = self._epub.chapters[self._index]
            try:
                self._epub.read(ch.path)
            except KeyError:
                self._set_error(f"Capítulo não encontrado no arquivo: {ch.path}")
        html = self.currentHtml()
        self._server.set_content(html)
        self._rev += 1
        path = (
            f"chapter/{self._index}?r={self._rev}"
            if self._epub is not None
            else f"empty?r={self._rev}"
        )
        self._last_url = self._server.url(path)
        self.contentChanged.emit(self._last_url)

    # ------------------------------------------------------------------ ciclo
    def shutdown(self) -> None:
        """Encerra o servidor local (chamar ao sair do app / nos testes)."""
        self._server.shutdown()
