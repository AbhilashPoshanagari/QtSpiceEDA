"""Entry point of the D6 walkthrough: ``python scratch/phase5/d6_walkthrough/walk_main.py OUT``
from the repository root (OUT: a folder for screenshots, walk.json and test files).

walk.py runs from here under ``if __name__ == "__main__"`` because the grid runs start
spawned child processes, which import the main module again.
"""

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parents[2]))
    runpy.run_path(str(here / "walk.py"), run_name="walk")
