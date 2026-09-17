"""Entry point for the PACKAGED command line.

`cobblerpy/__main__.py` uses relative imports, which is correct for a module
run as `python -m cobblerpy` and impossible for a script PyInstaller runs as a
top-level module: the binary died on its first line with "attempted relative
import with no known parent package".

The bundled CLI therefore starts here, where the import is absolute. The
module itself is left alone -- rewriting a package's own imports to suit a
build tool would be the tail wagging the dog.
"""

import sys

from cobblerpy.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
