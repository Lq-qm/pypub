#!/usr/bin/env python3
"""Valida drag & drop de .epub num DESKTOP REAL (não offscreen).

Abre a janela do PyPub, garante um .epub de exemplo para arrastar, e imprime
quando o livro abrir (sucesso) — assim você confirma o caminho completo
(DropArea → bridge.openDropped → openFile) com um gesto real do SO.

Uso:
    uv run python tools/dnd_probe.py
    # opcional: abre já com um livro  uv run python tools/dnd_probe.py /caminho/livro.epub

Depois: ARRASTE um arquivo .epub (ex.: o de exemplo que o probe imprime) para
a janela. O probe mostra ✅ quando o livro abrir, e o erro se rejeitar.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtQml import QQmlComponent, QQmlEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pypub.app import _ensure_qml_import_path  # noqa: E402
from pypub.bridge import EpubBridge  # noqa: E402

SAMPLE = os.path.join(tempfile.gettempdir(), "pypub_sample.epub")


def _ensure_sample() -> str:
    if not os.path.exists(SAMPLE):
        subprocess.run(
            [sys.executable, str(_ROOT / "tools" / "make_sample_epub.py"), SAMPLE],
            check=True,
        )
    return SAMPLE


def main(argv: list[str]) -> int:
    _ensure_qml_import_path()
    sample = _ensure_sample()

    app = QApplication(argv)
    app.setApplicationName("PyPub")
    app.setOrganizationName("pypub")

    engine = QQmlEngine(app)
    bridge = EpubBridge()
    engine.rootContext().setContextProperty("bridge", bridge)

    # feedback ao vivo: o probe mostra quando o drop abre o livro / rejeita
    def on_book() -> None:
        if bool(bridge.hasBook):
            print("\n✅  SUCESSO: livro abriu via drag & drop →", str(bridge.bookTitle))
        else:
            print("\n(livro fechado)")

    def on_error() -> None:
        msg = str(bridge.lastError or "")
        if msg:
            print("⚠️  erro:", msg)

    bridge.bookChanged.connect(on_book)
    bridge.errorChanged.connect(on_error)

    root_qml = _ROOT / "pypub" / "qml" / "Main.qml"
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(root_qml)))
    if comp.isError():
        print("ERRO QML:", comp.errorString())
        for e in comp.errors():
            print("  -", e)
        return 1

    win = comp.create()
    if win is None or not isinstance(win, QQuickWindow):
        print("Falha ao criar a janela.")
        return 1
    win.show()
    app.processEvents()

    drop = win.findChild(QQuickItem, "dropArea")
    print("DropArea encontrado:", drop is not None)
    if drop is not None:
        print("  visível:", drop.property("visible"),
              "| tamanho:", drop.property("width"), "x", drop.property("height"))

    print("\n=== ARRASTE um arquivo .epub para a janela ===")
    print("arquivo de exemplo:", sample)
    print("o probe imprime '✅ SUCESSO' quando o livro abrir.")
    print("(pressione Ctrl+Q ou feche a janela para sair)\n")

    # abre um livro via argumento, se fornecido (para comparação)
    for a in argv[1:]:
        if not a.startswith("-") and Path(a).is_file():
            bridge.openFile(a)
            break

    rc = app.exec()
    bridge.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
