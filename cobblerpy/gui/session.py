"""What the window does, without the window.

Every decision the desktop application makes lives here, and none of it
imports a toolkit. The library's claim is that it has no dependencies; keeping
the logic on this side means the claim holds for everything except the window,
and that the behaviour can be driven by a test with no display attached.

The ORDER of what comes back is the product. Somebody opening this has
inherited a codebase and is deciding whether to read it or rewrite it, so the
first thing they are shown is whether the work has already been attempted more
than once, and which attempt got furthest. Structure comes after that.
"""

import os
import time

from .. import survey
from ..attempts import find as find_attempts


class ProjectError(Exception):
    """A dropped thing this application cannot survey."""


def folders_from_drop(payload):
    """Every project folder a drag-and-drop payload refers to.

    Qt hands over a list of local paths. A file resolves to the folder holding
    it, because dropping one module out of a project is an obvious thing to do
    and refusing it would be pedantry.
    """
    if isinstance(payload, str):
        payload = [payload]
    out, seen = [], set()
    for raw in payload:
        raw = str(raw).strip()
        if not raw:
            continue
        if not os.path.exists(raw):
            raise ProjectError(f"{raw} does not exist")
        full = os.path.abspath(raw)
        folder = os.path.dirname(full) if os.path.isfile(full) else full
        if folder not in seen:
            seen.add(folder)
            out.append(folder)
    if not out:
        raise ProjectError("nothing was dropped")
    return out


class Index:
    """What is in a project folder, without parsing anything.

    Cheap on purpose: the window shows it the instant something is dropped, so
    a person can see the tool understood what they gave it before committing
    to a survey that takes a moment on a large tree.
    """

    def __init__(self, folder):
        self.folder = folder
        self.files = []
        self.lines = 0
        self.unreadable = 0
        self.packages = set()
        self.has_git = os.path.isdir(os.path.join(folder, ".git"))
        self._walk()

    def _walk(self):
        for root, dirs, names in os.walk(self.folder):
            dirs[:] = [d for d in dirs
                       if not d.startswith(".") and d != "__pycache__"]
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                self.files.append(path)
                if name == "__init__.py":
                    self.packages.add(root)
                try:
                    with open(path, "rb") as fh:
                        self.lines += sum(1 for _ in fh)
                except OSError:
                    # Counted, not swallowed. A permission error used to
                    # vanish here and the window reported a line total that
                    # was quietly short, with nothing saying by how much.
                    self.unreadable += 1

    @property
    def count(self):
        return len(self.files)

    def summary(self):
        if not self.files:
            return "no Python in this folder"
        parts = [f"{self.count} modules", f"{self.lines:,} lines"]
        if self.unreadable:
            # Said out loud. The total is short by whatever these hold.
            parts.append(f"{self.unreadable} could not be read")
        if self.packages:
            parts.append(f"{len(self.packages)} packages")
        parts.append("git history" if self.has_git else "no git history")
        return "  ·  ".join(parts)


class Result:
    """What a survey found, in the order somebody inheriting code needs it."""

    def __init__(self, folder):
        self.folder = folder
        self.survey = None
        self.attempts = []
        self.frontier = []
        self.seconds = 0.0
        self.map_path = None

    @property
    def modules(self):
        return len(self.survey.project.modules) if self.survey else 0

    @property
    def orphans(self):
        return len(self.survey.project.orphans) if self.survey else 0

    @property
    def entry_points(self):
        return len(self.survey.project.entry_points) if self.survey else 0

    @property
    def unreached(self):
        if not self.survey:
            return 0
        project = self.survey.project
        entry = {n for n, _ in project.entry_points}
        return sum(1 for m in project.modules
                   if (m.dotted or m.relpath) not in project.reachable
                   and (m.dotted or m.relpath) not in entry)

    def headline(self):
        """The one sentence worth reading first.

        Restarts lead when there are any, because that is the finding that
        decides whether the next hour is spent reading or rewriting.
        """
        if self.attempts:
            total = sum(len(g["attempts"]) for g in self.attempts)
            groups = len(self.attempts)
            return (f"{total} files are {groups} job"
                    f"{'s' if groups != 1 else ''} started over")
        if self.frontier:
            return f"{len(self.frontier)} modules carry unfinished-work signals"
        return f"{self.modules} modules, nothing obviously unfinished"

    def duration(self):
        if self.seconds < 1:
            return f"{self.seconds * 1000:.0f} ms"
        if self.seconds < 90:
            return f"{self.seconds:.1f} seconds"
        return f"{self.seconds / 60:.1f} minutes"


class Options:
    def __init__(self):
        self.history = True          # read git, for who and when
        self.write_map = True        # produce the interactive HTML map

    def as_dict(self):
        return dict(vars(self))


class Session:
    """One project folder, surveyed."""

    def __init__(self, out_dir=None):
        self.folder = None
        self.index = None
        self.options = Options()
        self.out_dir = out_dir
        self.result = None

    def load(self, folder):
        if isinstance(folder, (list, tuple)):
            folder = folder[0]
        if not os.path.isdir(folder):
            raise ProjectError(f"{folder} is not a folder")
        self.folder = os.path.abspath(folder)
        self.index = Index(self.folder)
        self.result = None
        return self.index

    def map_destination(self):
        """Where the map goes: beside the project, never inside it.

        Writing an HTML file into the tree being surveyed means the next
        survey reads its own output -- and on a repository, that the map turns
        up as an untracked file in somebody's `git status`.
        """
        if self.out_dir:
            return os.path.join(self.out_dir, "map.html")
        parent = os.path.dirname(self.folder.rstrip(os.sep)) or "."
        base = os.path.basename(self.folder.rstrip(os.sep)) or "project"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return os.path.join(parent, f"{base}-map-{stamp}.html")

    def run(self, on_stage=None):
        """Survey the project. Returns a Result.

        `on_stage(text)` is called as it moves between phases; there is no
        per-file progress because the survey's phases are not per-file and
        pretending otherwise would be a fake progress bar.
        """
        if not self.folder:
            raise ProjectError("no project loaded")
        started = time.time()
        result = Result(self.folder)

        if on_stage:
            on_stage("reading the source")
        result.survey = survey(self.folder, with_history=self.options.history)

        if on_stage:
            on_stage("looking for restarts")
        result.attempts = find_attempts(result.survey.project,
                                        result.survey.modules_by_key,
                                        result.survey.origins)
        result.frontier = result.survey.frontier

        if self.options.write_map:
            if on_stage:
                on_stage("drawing the map")
            from ..report import write_map
            destination = self.map_destination()
            os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
            write_map(result.survey.project, result.survey.frontier,
                      result.survey.history, destination,
                      origins=result.survey.origins,
                      modules_by_key=result.survey.modules_by_key,
                      attempts=result.attempts)
            result.map_path = destination

        result.seconds = time.time() - started
        self.result = result
        return result
