"""Entry script of the packaged app (PyInstaller freezes this file).

``app/__main__.py`` stays a module so that its ``__file__`` points into the bundle,
where it finds ui/qml and the examples.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    # First thing: a block-runner child process is this executable started again.
    multiprocessing.freeze_support()
    from app.__main__ import main

    sys.exit(main())
