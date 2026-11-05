"""Throwaway: open the app on the Schematic tab with a schematic and save a screenshot.

Run:  .venv/Scripts/python.exe scratch/schematic_demo/shot.py OUT.png [rc|big]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtCore import QObject, QTimer  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.__main__ import create_engine  # noqa: E402
from core.schematic.model import Instance, Schematic, Wire  # noqa: E402


def big(n: int = 300) -> Schematic:
    s = Schematic()
    for i in range(n):
        x, y = (i % 20) * 160, (i // 20) * 192
        s.instances.append(Instance("res", f"R{i + 1}", x, y, attrs={"Value": "1k"}))
        s.wires.append(Wire(x + 16, y, x + 16, y - 48))
        s.wires.append(Wire(x + 16, y + 96, x + 176, y + 96))
    return s


def main() -> int:
    out = sys.argv[1]
    which = sys.argv[2] if len(sys.argv) > 2 else "rc"
    QQuickStyle.setStyle("Fusion")
    app = QApplication(sys.argv[:1])
    engine = create_engine()
    window = engine.rootObjects()[0]
    ctl = engine.singletonInstance("QtSpice", "SchematicController")
    if which == "rc":
        data = json.loads((ROOT / "tests/fixtures/schematics/rc/rc.json").read_text("utf-8"))
        schematic, _ = Schematic.from_dict(data)
    else:
        schematic = big()
    ctl.load_schematic(schematic)
    window.findChild(QObject, "leftTabs").setProperty("currentIndex", 2)

    def fit() -> None:
        window.findChild(QObject, "schematicCanvas").zoomToFit()
        QTimer.singleShot(400, grab)

    def grab() -> None:
        window.grabWindow().save(out)
        print("saved", out, flush=True)
        app.quit()

    QTimer.singleShot(400, fit)
    QTimer.singleShot(8000, app.quit)  # never hang
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
