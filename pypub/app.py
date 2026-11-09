"""Bootstrap da aplicação: QApplication + QQmlEngine + contexto ``bridge``."""

from __future__ import annotations  # noqa: I001

import os
import sys
from pathlib import Path
from typing import cast

import PySide6
from PySide6.QtCore import QEvent, QObject, Qt, QUrl, QTimer
from PySide6.QtGui import QWindow
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtWidgets import QApplication

from .bridge import EpubBridge
from .icon import make_window_icon

# Prioriza os módulos QML embutidos no wheel do PySide6 (mesma build das
# libs Qt que os bindings Python usam); o do sistema entra depois (Kirigami/KF6).
_WHEEL_QML_DIR = str(Path(PySide6.__file__).resolve().parent / "Qt" / "qml")
_SYSTEM_QML_DIR = "/usr/lib/qt6/qml"


class _RestoreRepaintFilter(QObject):
    """Força um repaint do QtWebEngine quando a janela volta para a tela

    (restaurada da minimização / de oclusão).

    O conteúdo web é renderizado num processo separado. Quando o KWin
    minimiza a janela, a superfície do conteúdo é descartada; ao restaurar,
    o WebEngine NÃO gera um novo frame por conta própria → "tela preta" até
    que algo force um repaint. Os sinais QML ``onActiveChanged`` /
    ``onVisibleChanged`` podem não disparar em KWin/Wayland; o gatilho mais
    confiável é o **evento nativo de janela** (``WindowDeactivate`` /
    ``WindowActivate`` / ``Expose`` / ``WindowStateChange``), que chega via
    ``eventFilter`` mesmo quando os sinais QML não chegam.

    Mecânica (ao detectar o "restore"):
      1. **resize** (–24px → +24px) — força re-layout + re-render do GL
         (o compositor gera um novo frame ao redimensionar a janela);
      2. **hide/show** do ``WebEngineView`` — re-anexa a superfície GL
         descartada;
      3. **``requestUpdate()``** — pede um repaint do QML scene graph;
      4. **2 passes** — repete o ciclo para garantir que o conteúdo
         reapareceu.
    A posição da leitura é preservada (nenhum destes recarrega a página).
    """

    _MAX_PASSES = 2

    def __init__(self, window: QObject) -> None:
        super().__init__(window)
        # em runtime é QQuickWindow (K.ApplicationWindow); tipamos como
        # QWindow para o acesso a width/resize/windowState
        self._win: QWindow = cast(QWindow, window)
        self._pending = False
        self._busy = False
        self._pass = 0
        self._ow = 0
        self._oh = 0

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        t = event.type()
        if t == QEvent.Type.WindowDeactivate:
            # perdeu o foco — minimizar desativa; ao voltar, repaint
            self._pending = True
        elif t == QEvent.Type.WindowStateChange:
            st = self._win.windowState()
            if st & Qt.WindowState.WindowMinimized:
                self._pending = True
        elif t in (QEvent.Type.WindowActivate, QEvent.Type.Expose) and self._pending:
            # voltou para a tela (restaurou) → força o repaint agora
            self._do_repaint()
        return False

    def forceNow(self) -> None:
        """Força o repaint manualmente (atalho Ctrl+Shift+R), mesmo sem um
        "pending" de minimização — útil como fallback garantido."""
        if self._busy:
            return
        self._busy = True
        self._pass = 0
        self._repaint_pass()

    def _do_repaint(self) -> None:
        if self._busy or not self._pending:
            return
        self._pending = False
        self._busy = True
        self._pass = 0
        self._repaint_pass()

    def _repaint_pass(self) -> None:
        """Passo 1: encolhe a janela (–24px) para forçar re-layout +
        re-render do GL (o compositor gera um novo frame ao redimensionar
        a janela)."""
        win = self._win
        self._ow, self._oh = win.width(), win.height()
        mw, mh = win.minimumWidth(), win.minimumHeight()
        win.resize(max(mw, self._ow - 24), max(mh, self._oh - 24))
        QTimer.singleShot(50, self._restore_pass)

    def _restore_pass(self) -> None:
        """Passo 2: esconde o ``WebEngineView`` (re-anexa a superfície GL
        descartada), restaura o tamanho original, e 30 ms depois mostra o
        reader de novo — ``requestUpdate()`` pede o repaint do scene graph.
        Repete o ciclo até completar os ``_MAX_PASSES``."""
        win = self._win
        reader = win.findChild(QQuickItem, "reader")
        if reader is not None:
            reader.setVisible(False)
        win.resize(self._ow, self._oh)
        win.requestUpdate()

        def _show() -> None:
            if reader is not None:
                reader.setVisible(True)
            win.requestUpdate()
            self._pass += 1
            if self._pass < self._MAX_PASSES:
                QTimer.singleShot(200, self._repaint_pass)
            else:
                self._busy = False

        QTimer.singleShot(30, _show)


def _ensure_qml_import_path() -> None:
    current = os.environ.get("QML2_IMPORT_PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    if _WHEEL_QML_DIR not in parts:
        parts.insert(0, _WHEEL_QML_DIR)
    if _SYSTEM_QML_DIR not in parts:
        parts.append(_SYSTEM_QML_DIR)
    os.environ["QML2_IMPORT_PATH"] = os.pathsep.join(parts)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _ensure_qml_import_path()

    app = QApplication(argv)
    app.setApplicationName("PyPub")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("pypub")

    engine = QQmlEngine(app)
    bridge = EpubBridge()
    engine.rootContext().setContextProperty("bridge", bridge)

    # Ícone da janela (livro aberto — identidade do leitor), na cor do tema:
    # escuro na titlebar clara, claro na titlebar escura. Atualiza ao vivo
    # se o modo claro/escuro do SO mudar com o app aberto.
    def _apply_window_icon() -> None:
        # bridge.dark é @Property (tipagem do PySide6); em runtime é bool
        app.setWindowIcon(make_window_icon(bool(bridge.dark)))

    _apply_window_icon()
    bridge.darkChanged.connect(_apply_window_icon)

    root_qml = Path(__file__).resolve().parent / "qml" / "Main.qml"
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(root_qml)))
    if component.isError():
        print(f"Erro ao carregar QML: {component.errorString()}", file=sys.stderr)
        for err in component.errors():
            print(f"  - {err}", file=sys.stderr)
        return 1

    root = component.create()
    if root is None:
        print("Falha ao criar a janela principal.", file=sys.stderr)
        return 1

    # Força um repaint do QtWebEngine quando a janela volta da minimização /
    # oclusão (evita a "tela preta"). O filtro de evento nativo é mais
    # confiável que os sinais QML em KWin/Wayland (ver _RestoreRepaintFilter).
    _restore_filter = _RestoreRepaintFilter(root)  # parent=root → não é GC'd
    root.installEventFilter(_restore_filter)
    # atalho Ctrl+Shift+R → repaint forçado (fallback garantido)
    bridge.repaintRequested.connect(_restore_filter.forceNow)

    # `pypub caminho/para/livro.epub`
    args = argv[1:]
    for arg in args:
        if not arg.startswith("-") and Path(arg).is_file():
            bridge.openFile(arg)
            break

    rc = app.exec()
    bridge.shutdown()  # encerra o servidor local (127.0.0.1)
    return rc
