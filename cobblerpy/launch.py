"""Start the tool without an incantation.

`python3 -m cobblerpy <dir> --map <path>` is four decisions before anything
happens: which directory, which flag, where the output goes, and then finding
the file to open it. This module is the version with none of them -- point it
at a folder and a map opens.

It exists to be called from places that have NO TERMINAL: a desktop icon you
drop a folder on, or a right-click entry in the file manager. Nothing printed
to stderr is ever seen there, so EVERY outcome -- including every failure --
goes through `notify`. A launcher that fails silently is worse than no
launcher, because the folder was dropped and nothing happened.
"""

import os
import shutil
import subprocess
import sys
import time
import webbrowser

from . import survey
from .report import write_map


def _notify(message):
    """Say something when there is no terminal to say it in.

    Desktop notification when one is available, and stderr regardless, so the
    same launcher is usable from a shell and from an icon.
    """
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "CodeCobbler", message],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass          # the message still reaches stderr below
    print(message, file=sys.stderr)


def map_destination(folder, stamp=None):
    """Where the map goes: beside the project, never inside it.

    Writing it into the surveyed tree means the next survey reads its own
    output, and on a repository that the map turns up as an untracked file in
    somebody's `git status`. The GUI already decided this; same rule here.
    """
    folder = folder.rstrip(os.sep)
    parent = os.path.dirname(folder) or "."
    base = os.path.basename(folder) or "project"
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(parent, f"{base}-map-{stamp}.html")


def main(argv=None, notify=None, open_url=None):
    """Survey one folder and open its map. Returns an exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    notify = notify or _notify
    open_url = open_url or webbrowser.open

    paths = [a for a in argv if not a.startswith("-")]
    if not paths:
        paths = [os.getcwd()]

    if len(paths) > 1:
        # Refused BY NAME AND COUNT rather than reduced. The window used to
        # parse every dropped folder and then keep folders[0], discarding the
        # rest without a word -- input vanishing silently is the one behaviour
        # this tool exists to argue against.
        notify(f"{len(paths)} folders given, and this takes one folder at a "
               f"time. Surveying several as a single map is not built yet, so "
               f"rather than pick one of them silently: "
               f"{', '.join(os.path.basename(p.rstrip(os.sep)) for p in paths)}")
        return 2

    target = os.path.abspath(paths[0])
    if not os.path.exists(target):
        notify(f"{target} does not exist")
        return 1
    if os.path.isfile(target):
        # Dropping one module out of a project is an obvious thing to do, and
        # refusing it would be pedantry. Same rule the window's drop uses.
        target = os.path.dirname(target)

    surveyed = survey(target)
    if not surveyed.project.modules:
        notify(f"no Python found under {target}")
        return 1

    destination = map_destination(target)
    write_map(surveyed.project, surveyed.frontier, surveyed.history,
              destination, origins=surveyed.origins,
              modules_by_key=surveyed.modules_by_key)
    count = len(surveyed.project.modules)
    notify(f"{count:,} module{'s' if count != 1 else ''} mapped -- "
           f"opening the map")
    open_url("file://" + destination)
    return 0


if __name__ == "__main__":
    sys.exit(main())
