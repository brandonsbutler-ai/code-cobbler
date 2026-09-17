"""Entry point for the PACKAGED desktop application.

Not the same as `python -m cobblerpy.gui`, and it exists for two reasons that
only appear once something is bundled.

PyInstaller runs its entry script as a top-level module rather than as part of
a package, so `from .qt_app import main` raises "attempted relative import with
no known parent package" the instant the binary starts. The import here is
absolute.

And PyInstaller finds dependencies by READING the source. The window imports
PySide6 inside a function so that a machine without Qt still gets a working
library, a working command line and a clear message -- which means a static
read of the package never sees Qt at all, and the bundle comes out without it.
Importing the toolkit here, at the top level, is what puts it in the build.
"""

import sys

from PySide6 import QtCore, QtGui, QtWidgets           # noqa: F401

from cobblerpy.gui.qt_app import main

if __name__ == "__main__":
    sys.exit(main())
