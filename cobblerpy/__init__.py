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

from .abandonment import analyse_project, summarise
from .graph import Project
from .history import summary as history_summary
from .origin import classify as classify_origins, coverage_note, summarise as summarise_origins
from .scan import scan_tree

__version__ = "0.1.0"
__all__ = ["survey", "Project", "scan_tree", "analyse_project", "__version__"]


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
    def totals(self):
        return summarise(self.frontier)

    def as_dict(self):
        return {
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
