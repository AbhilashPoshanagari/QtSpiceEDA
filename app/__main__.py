"""QtSpice entry point: ``python -m app``.

``python -m app --smoke-test [NETLIST] [--report FILE]`` checks an installation without
showing a window (see app/smoke.py); the packaged app takes the same options.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

import bridge.diagram_controller  # noqa: F401  registers the QtSpice QML types
import bridge.grid_controller  # noqa: F401
import bridge.project_manager  # noqa: F401
import bridge.schematic_controller  # noqa: F401
import bridge.simulator  # noqa: F401
import bridge.waveform_controller  # noqa: F401
import bridge.waveform_model  # noqa: F401
from app import __version__

ROOT = Path(__file__).resolve().parents[1]  # the bundle folder in a packaged app
QML_DIR = ROOT / "ui" / "qml"
MAIN_QML = QML_DIR / "Main.qml"
EXAMPLE_NETLIST = ROOT / "examples" / "rc_lowpass.cir"


def create_engine() -> QQmlApplicationEngine:
    """Load Main.qml into a new engine. A QGuiApplication must already exist."""
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.load(MAIN_QML.as_uri())
    return engine


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Our options; everything else is left for Qt."""
    parser = argparse.ArgumentParser(prog="qtspice", add_help=False)
    parser.add_argument("--smoke-test", nargs="?", const=str(EXAMPLE_NETLIST), default=None)
    parser.add_argument("--report", default=None)
    return parser.parse_known_args(argv[1:])


def main(argv: list[str] | None = None) -> int:
    # Block runs start a child process with "spawn"; in a packaged app that child is
    # this executable again, and freeze_support() turns it into the worker.
    multiprocessing.freeze_support()
    argv = list(sys.argv if argv is None else argv)
    options, qt_args = parse_args(argv)
    if options.smoke_test is not None:
        from app.smoke import run_smoke_test

        return run_smoke_test(Path(options.smoke_test), options.report, [argv[0], *qt_args])

    QQuickStyle.setStyle("Fusion")
    app = QGuiApplication([argv[0], *qt_args])
    app.setApplicationName("QtSpice")
    app.setOrganizationName("QtSpice")
    app.setApplicationVersion(__version__)
    engine = create_engine()
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
