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

from PySide6.QtCore import QObject, Property, Signal, Slot

from .epub import Epub, EpubError, chapter_title
from .renderer import (
    THEMES,
    columns_for_zoom,
    empty_html,
    page_background,
    prepare_chapter,
    theme_ui_font,
    ui_colors,
)
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


class EpubBridge(QObject):
    """Estado e ações expostos ao QML (context property ``bridge``)."""

    bookChanged = Signal()
    positionChanged = Signal()
    zoomChanged = Signal()
    darkChanged = Signal()
    errorChanged = Signal()
    #: mudaram tema/cores (escolha do usuário OU mudança do sistema com
    #: tema "default") — notifica emptyHtml/pageBackground/ui* etc.
    themeChanged = Signal()
    #: emitido com o HTML pronto para renderização (incluindo estado vazio)
    contentChanged = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._epub: Epub | None = None
        self._index = 0
        self._zoom = 1.0
        # tema de leitura ("default" segue o **sistema** — ver _system_dark();
        # "beige" e "gray" são paletas fixas com fontes próprias — ver THEMES)
        self._theme = "default"
        self._dark = _system_dark()
        self._last_error = ""
        self._last_dir = ""
        self._server = ChapterServer()
        self._rev = 0
        self._last_url = ""
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

    @Property(str, notify=themeChanged)
    def theme(self) -> str:
        """Tema de leitura ativo: "default" | "beige" | "gray" (ver THEMES)."""
        return self._theme

    @Property(str, notify=themeChanged)
    def emptyHtml(self) -> str:
        return empty_html(self._theme, self._dark)

    @Property(str, notify=themeChanged)
    def pageBackground(self) -> str:
        return page_background(self._theme, self._dark)

    # Cores da APLICAÇÃO (toolbar/rodapé/sumário/janela) para o tema ativo —
    # vazias no tema "default" (o QML cai no padrão do Kirigami nesse caso).
    @Property(str, notify=themeChanged)
    def uiBackground(self) -> str:
        return ui_colors(self._theme)["background"]

    @Property(str, notify=themeChanged)
    def uiForeground(self) -> str:
        return ui_colors(self._theme)["foreground"]

    @Property(str, notify=themeChanged)
    def uiAccent(self) -> str:
        return ui_colors(self._theme)["accent"]

    @Property(str, notify=themeChanged)
    def uiFont(self) -> str:
        """Fonte da UI para o tema (vazia no default = fonte do Kirigami)."""
        return theme_ui_font(self._theme)

    @Property(float, notify=zoomChanged)
    def zoom(self) -> float:
        return self._zoom

    @Property(int, notify=zoomChanged)
    def columnCount(self) -> int:
        """Enquadramento FIXO: sempre 1 coluna (o zoom muda só a fonte)."""
        return columns_for_zoom(self._zoom)

    @Property(str, notify=bookChanged)
    def chaptersJson(self) -> str:
        """Lista de capítulos (``index`` + ``title``) em JSON — sumário (sidebar).

        PySide6 → QML não entrega lista de objetos de forma simples; JSON é o
        formato mais robusto (o QML faz ``JSON.parse(bridge.chaptersJson)``).
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
        """HTML pronto do capítulo atual (ou estado vazio) — no tema ativo."""
        if self._epub is None:
            return empty_html(self._theme, self._dark)
        ch = self._epub.chapters[self._index]
        try:
            raw = self._epub.read(ch.path)
        except KeyError:
            return empty_html(self._theme, self._dark)
        return prepare_chapter(
            raw.decode("utf-8", "replace"),
            ch.path,
            self._epub,
            theme=self._theme,
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

    @Slot(str, result=bool)
    def setTheme(self, theme: str) -> bool:
        """Aplica um tema de leitura: ``default`` (segue o sistema), ``beige``
        (bege escuro + Mulish) ou ``gray`` (cinza escuro + Bitstream Charter).

        Re-renderiza o conteúdo atual (mesmo capítulo; a página volta ao
        início) e repinta a interface (via uiBackground/uiForeground/uiFont —
        o QML re-evalua as bindings ao sinalizar ``themeChanged``).
        Tema inválido → ``False`` (estado intacto).
        """
        if theme not in THEMES:
            self._set_error(f"tema desconhecido: {theme!r}")
            return False
        if theme == self._theme:
            return True
        self._theme = theme
        self.themeChanged.emit()
        self._emit_content()  # novo HTML (mesmo capítulo, nova paleta/fonte)
        return True

    def _on_system_color_scheme(self) -> None:
        """O esquema do OS mudou.

        Tema "default" → re-renderiza (segue o sistema). Temas fixos
        (beige/cinza) ignoram a mudança — a paleta é própria, não segue o SO.
        """
        value = _system_dark()
        if value == self._dark:
            return
        self._dark = value
        self.darkChanged.emit()
        if self._theme == "default":
            self.themeChanged.emit()
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
        de novo não recarrega a view, então a revisão garante o reload —
        inclusive para troca de tema no mesmo capítulo (zoom NÃO recarrega:
        é aplicado no documento via pypub_setZoom).
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
