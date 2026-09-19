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

import shutil
import subprocess
import sys


def _say(message):
    """launch._notify, for when the package that holds it could not load.

    The `cobble` shim and the desktop icon start here, with no terminal: a
    traceback on stderr is a failure nobody sees.
    """
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "CodeCobbler", message],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass          # stderr still gets the traceback
    print(message, file=sys.stderr)


def argv_for(argv, frozen):
    """What the launcher is asked to do.

    The single executable is double-clicked, from whatever working directory
    the desktop hands it, so given nothing it opens the shelf -- which is what
    the README promises. The `cobble` shim also starts here, and a bare `cobble`
    typed in a terminal still means "here".
    """
    return argv if argv or not frozen else ["--shelf"]


if __name__ == "__main__":
    try:
        from cobblerpy.launch import main
        code = main(argv_for(sys.argv[1:], getattr(sys, "frozen", False)))
    except Exception as exc:                              # noqa: BLE001
        _say(f"CodeCobbler failed before it could finish: "
             f"{type(exc).__name__}: {exc}")
        raise
    sys.exit(code)
