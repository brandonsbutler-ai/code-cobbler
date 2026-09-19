"""cobblerpy -- make sense of a Python codebase somebody else left behind.

    from cobblerpy import survey
    result = survey("path/to/project")

Reads a directory of Python source and reports its structure, its execution
flow, where the previous author stopped, and what the version history says they
were doing. Uses nothing outside the standard library, and never imports or
executes the code it is reading -- a codebase you are trying to understand is
usually one you do not yet trust.

What it proves and what it guesses are kept apart deliberately. Structure comes
from the syntax and is close to exact. Reachability and "nothing uses this" are
lower bounds, because Python dispatches through registries, decorators and
getattr in ways no parser can follow.
"""

import os
import sys

# `python -m cobblerpy` puts the CURRENT DIRECTORY first on sys.path, and the
# obvious place to run this tool is inside the project it reads. A project
# holding ast.py or tokenize.py was then imported in place of the standard
# library -- the tool executing the code it came to read. So when the program
# being run is THIS one, the directory is dropped before anything else is
# imported; `import cobblerpy` from somebody else's program, including one run
# with -m, leaves their sys.path alone. runpy has already imported what IT
# needs by now, so this narrows the -m form rather than closing it; the README
# says which forms are safe inside a project.


def _m_target():
    """The module `python -m` was asked to run, or None.

    sys.argv is ["-m"] while ANY -m program is being located, so it cannot say
    which; sys.orig_argv can, from 3.10. On 3.9 this answers None and the guard
    stands down rather than guessing.
    """
    args = iter(getattr(sys, "orig_argv", [])[1:])
    for arg in args:
        if arg == "-m":
            return next(args, None)
        if arg.startswith("-m"):
            return arg[2:]
        if arg in ("-W", "-X"):
            next(args, None)              # these take a value
        elif arg == "-c" or not arg.startswith("-"):
            return None
    return None


def _drop_the_current_directory():
    try:
        cwd = os.getcwd()
    except OSError:
        return              # deleted: nothing can be imported from it anyway
    sys.path[:] = [p for p in sys.path if os.path.abspath(p) != cwd]


# Our own programs, by the name they are started as: pip's console scripts
# (`cobblerpy.exe` and `cobblerpy-script.py` on Windows) and the packaging
# entries. An empty PYTHONPATH element -- PYTHONPATH=":" or "/x:" -- is the
# current directory, and pip's `cobblerpy` then imported the project's ast.py.
_PROGRAMS = {"cobblerpy", "cobble", "cobblerpy-gui",
             "launcher_entry", "cli_entry", "app_entry"}


def _program_name():
    name = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    for suffix in ("-script.pyw", "-script.py", ".exe", ".py"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


if ((sys.argv[:1] == ["-m"] and (_m_target() or "").split(".")[0] == "cobblerpy")
        or _program_name() in _PROGRAMS):
    _drop_the_current_directory()

from .abandonment import analyse_project, summarise
from .graph import Project
from .history import summary as history_summary
from .origin import classify as classify_origins, coverage_note, summarise as summarise_origins
from .scan import scan_tree

__version__ = "0.1.2"

# The product name, for anything a person looks at -- the map's title bar, the
# desktop window. The TOOL is cobblerpy and stays cobblerpy: the command, the
# package, the import and every script anyone has already written.
BRAND = "CodeCobbler"

__all__ = ["survey", "Project", "scan_tree", "analyse_project",
           "__version__", "BRAND"]


class Survey:
    """Everything cobblerpy learned about one directory."""

    def __init__(self, root, project, frontier, history, skipped=0, origins=None):
        self.root = root
        self.project = project
        self.frontier = frontier
        self.history = history
        self.skipped = skipped
        self.origins = origins or {}

    @property
    def modules_by_key(self):
        return {(m.dotted or m.relpath): m for m in self.project.modules}

    @property
    def origin_totals(self):
        return summarise_origins(self.origins, self.modules_by_key)

    @property
    def history_coverage(self):
        return coverage_note(self.origins, self.history)

    @property
    def left_behind(self):
        """What nothing reaches, ranked by the work in it."""
        from .salvage import find
        return find(self.project, self.modules_by_key, self.history)

    @property
    def totals(self):
        return summarise(self.frontier)

    def as_dict(self):
        # The three findings the map and the summary lead with, from the same
        # functions they call -- "everything" without them was not.
        from .attempts import find as find_attempts
        from .deadends import find as find_deadends
        from .diversion import find as find_forks
        forks = (find_forks(self.project, self.modules_by_key, self.history,
                            self.frontier)
                 if self.history.get("available") else [])
        return {
            "attempts": find_attempts(self.project, self.modules_by_key,
                                      self.origins),
            "deadends": find_deadends(self.project, self.modules_by_key,
                                      self.origins),
            "forks": forks,
            "root": self.root,
            "modules": [m.as_dict() for m in self.project.modules],
            "entry_points": self.project.entry_points,
            "imports": {k: sorted(v) for k, v in self.project.imports.items()},
            "imported_by": {k: sorted(v)
                            for k, v in self.project.imported_by.items()},
            "external": {k: sorted(v) for k, v in self.project.external.items()},
            "layers": self.project.layers(),
            "orphans": self.project.orphans,
            "cycles": self.project.cycles,
            "unreferenced": self.project.unreferenced,
            "frontier": self.frontier,
            "totals": self.totals,
            "history": {k: v for k, v in self.history.items()
                        if k != "files"} if self.history.get("available")
                       else self.history,
            "skipped_files": self.skipped,
            "excluded_directories": getattr(self.project, "excluded", {}),
            "left_behind": self.left_behind,
            "loaded_by_convention": {
                k: c.as_dict()
                for k, c in getattr(self.project, "convention_reached", {}).items()},
            "origins": self.origins,
            "origin_totals": self.origin_totals,
            "history_coverage": self.history_coverage,
        }


def survey(root, with_history=True, max_files=5000):
    """Scan, graph, and read a project for signs of unfinished work."""
    root = os.path.abspath(root)
    modules, skipped, excluded = scan_tree(root, max_files=max_files)
    project = Project(root, modules, skipped)
    project.excluded = excluded
    frontier = analyse_project(project)
    history = ({"available": False, "reason": "history not requested"}
               if not with_history
               else history_summary(root, [m.relpath for m in modules]))
    origins = classify_origins(root, modules, history)
    return Survey(root, project, frontier, history, skipped, origins)
