"""Ponte QObject entre o núcleo Python (parser EPUB + preparação HTML) e a
interface QML/Kirigami.

A view de renderização é a ``QWebEngineView`` do QML (módulo ``QtWebEngine``);
a ponte emite ``contentChanged(html)`` sempre que o conteúdo a exibir muda.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Property, Signal, Slot

from .epub import Epub, EpubError, chapter_title
from .renderer import empty_html, page_background, prepare_chapter

_ZOOM_MIN = 0.6
_ZOOM_MAX = 2.5
_ZOOM_STEP = 0.1


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
        self._dark = False
        self._last_error = ""
        self._last_dir = ""

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

    @Property(str, constant=True)
    def emptyHtml(self) -> str:
        return empty_html(self._dark)

    @Property(str, notify=darkChanged)
    def pageBackground(self) -> str:
        return page_background(self._dark)

    @property
    def zoom(self) -> float:
        return self._zoom

    @zoom.setter
    def zoom(self, value: float) -> None:
        self._set_zoom(float(value))

    @Property(float, notify=zoomChanged)
    def zoom(self) -> float:  # type: ignore[no-redef]
        return self._zoom

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
        self.contentChanged.emit(empty_html(self._dark))

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
        """HTML pronto do capítulo atual (ou estado vazio) para carga inicial."""
        if self._epub is None:
            return empty_html(self._dark)
        ch = self._epub.chapters[self._index]
        try:
            raw = self._epub.read(ch.path)
        except KeyError:
            return empty_html(self._dark)
        return prepare_chapter(raw.decode("utf-8", "replace"), ch.path, self._epub, self._dark)

    @Slot()
    def zoomIn(self) -> None:
        self._set_zoom(self._zoom + _ZOOM_STEP)

    @Slot()
    def zoomOut(self) -> None:
        self._set_zoom(self._zoom - _ZOOM_STEP)

    @Slot()
    def resetZoom(self) -> None:
        self._set_zoom(1.0)

    @Slot(bool)
    def setDark(self, dark: bool) -> None:
        if dark == self._dark:
            return
        self._dark = bool(dark)
        self.darkChanged.emit()
        self._emit_content()

    # ------------------------------------------------------------------ utils
    def _set_zoom(self, value: float) -> None:
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
        """Emite o HTML pronto do capítulo atual (ou estado vazio)."""
        if self._epub is not None:
            ch = self._epub.chapters[self._index]
            try:
                self._epub.read(ch.path)
            except KeyError:
                self._set_error(f"Capítulo não encontrado no arquivo: {ch.path}")
                self.contentChanged.emit(empty_html(self._dark))
                return
        self.contentChanged.emit(self.currentHtml())
