"""D9: make the sample design examples/ieee13_design.qgrid + ieee13_design.qsp.

The design is the IEEE 13 fixture (tests/fixtures/grid/ieee13/IEEE13Nodeckt.dss)
opened as a design exactly as the app does it: GridDiagramController.openDssAsDesign
(OpenDSS compiles the file in a GridRunner child, the model is read back through the
API, the diagram comes from the file's bus coordinates), then Save. The project lists
it as its one, active design with the default PowerFlowConfig. Never edit the two
files by hand; run this again (needs dss-python):

    python scratch/phase5/make_ieee13_design.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from bridge.grid_diagram_controller import GridDiagramController  # noqa: E402
from core.project.io import GridDesignSettings, Project, save_project  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "grid" / "ieee13" / "IEEE13Nodeckt.dss"
QGRID = ROOT / "examples" / "ieee13_design.qgrid"
QSP = ROOT / "examples" / "ieee13_design.qsp"


def main() -> int:
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    designer = GridDiagramController()
    try:
        loop = QEventLoop()
        designer.designOpened.connect(loop.quit)
        designer.openFailed.connect(loop.quit)
        QTimer.singleShot(60_000, loop.quit)
        if not designer.openDssAsDesign(str(FIXTURE)):
            print(designer.statusText)
            return 1
        loop.exec()
        if not designer.hasDesign:
            print(designer.consoleText)
            return 1
        if designer.errorCount:
            print(designer.consoleText)
            return 1
        if not designer.save(str(QGRID)):
            print(designer.statusText)
            return 1
    finally:
        designer.shutdown()
    save_project(QSP, Project(grid_designs=[GridDesignSettings(QGRID.name, True)]))
    print(f"wrote {QGRID.relative_to(ROOT)} and {QSP.relative_to(ROOT)}")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
