"""Bootstrap da aplicação: QApplication + QQmlEngine + contexto ``bridge``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import PySide6

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlEngine, QQmlComponent
from PySide6.QtWidgets import QApplication

from .bridge import EpubBridge

# Prioriza os módulos QML embutidos no wheel do PySide6 (mesma build das
# libs Qt que os bindings Python usam); o do sistema entra depois (Kirigami/KF6).
_WHEEL_QML_DIR = str(Path(PySide6.__file__).resolve().parent / "Qt" / "qml")
_SYSTEM_QML_DIR = "/usr/lib/qt6/qml"


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

    # `pypub caminho/para/livro.epub`
    args = argv[1:]
    for arg in args:
        if not arg.startswith("-") and Path(arg).is_file():
            bridge.openFile(arg)
            break

    rc = app.exec()
    bridge.shutdown()  # encerra o servidor local (127.0.0.1)
    return rc
