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

    sys.argv is ["-m", *rest] while ANY -m program is being located, so it
    cannot say which; sys.orig_argv can, from 3.10, so it is present on every
    interpreter this package supports (the floor is 3.11). Parsing the options
    missed combined flags (-Bm) and options that take a value, so the target
    is found by position instead: it is the element just before `rest`
    (checked on 3.12 for -m, -Bm, -Om, -mNAME, -W/-X values and
    --check-hash-based-pycs). Written joined, as -mNAME or -BmNAME, it is
    the text after the m. The getattr below stays anyway: an embedded or
    re-entrant interpreter can leave orig_argv absent, and answering None
    there stands the guard down rather than raising.
    """
    orig = getattr(sys, "orig_argv", None)
    if not orig or len(orig) <= len(sys.argv):
        return None
    target = orig[len(orig) - len(sys.argv)]
    if target.startswith("-"):
        target = target[target.index("m") + 1:] if "m" in target else ""
    return target or None


def _drop_the_current_directory():
    try:
        cwd = os.getcwd()
    except OSError:
        return              # deleted: nothing can be imported from it anyway
    sys.path[:] = [p for p in sys.path if os.path.abspath(p) != cwd]


# Our own programs, recognised by WHAT they are, never by name: somebody's
# tools/cobble.py that imports this as a library is theirs, and the guard took
# their deliberate PYTHONPATH=. away. Ours are the entry scripts in this
# checkout's packaging/ (the `cobble` shim runs one of them), and the wrappers
# pip writes for the console scripts, read back through __main__'s own loader
# -- which also reads the copy a Windows .exe launcher carries inside it
# (reasoned from how that launcher runs its script; not run on Windows). An
# empty PYTHONPATH element (":" or "/x:") is the current directory, and pip's
# `cobblerpy` then imported the project's ast.py.
_ENTRIES = ("cobblerpy.__main__", "cobblerpy.launch", "cobblerpy.gui.qt_app")


def _started_as_ours():
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    if not path:
        return False
    packaging = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "packaging")
    if os.path.dirname(os.path.realpath(path)) == os.path.realpath(packaging):
        return True
    # get_data, not get_source: get_source decodes through `tokenize`, which
    # is imported from sys.path -- i.e. from the very directory this is about
    # to remove. It ran the project's tokenize.py.
    try:
        source = main.__loader__.get_data(path).decode("latin-1")
    except (AttributeError, ImportError, OSError):
        return False
    return "sys.exit(main())" in source and any(
        f"from {entry} import main" in source for entry in _ENTRIES)


if ((sys.argv[:1] == ["-m"] and (_m_target() or "").split(".")[0] == "cobblerpy")
        or _started_as_ours()):
    _drop_the_current_directory()

from .abandonment import analyse_project, summarise
from .graph import Project
from .history import summary as history_summary
from .origin import classify as classify_origins, coverage_note, summarise as summarise_origins
from .scan import scan_tree

__version__ = "0.1.3"

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
