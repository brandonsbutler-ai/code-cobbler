"""Entry point for the PACKAGED launcher -- the one-file program.

`cobblerpy/launch.py` uses relative imports, which is right for a module run as
`python -m cobblerpy.launch` and impossible for a script PyInstaller runs as a
top-level module: the binary dies on its first line with "attempted relative
import with no known parent package". Same reason cli_entry.py exists.

The LAUNCHER is what gets packaged rather than the command line, because a
program somebody was emailed gets double-clicked, and the command line's first
act on no arguments is to print usage and exit. The launcher's is to show the
shelf -- which on a machine that has never run it says so, and says to drop a
folder on it.
"""

import sys

from cobblerpy.launch import main

if __name__ == "__main__":
    sys.exit(main())
