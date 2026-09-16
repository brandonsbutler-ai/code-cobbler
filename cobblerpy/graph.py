"""Assemble scanned modules into the shapes a reader needs.

Four questions, in the order someone picking up an unfamiliar codebase asks
them:

    what depends on what          -> the import graph
    where does it start           -> entry points
    what runs when it starts      -> reachability from those entry points
    what is nobody using          -> orphans and unreferenced definitions

A WORD ON CERTAINTY, because it decides how much weight any of this can carry.
The import graph is close to exact: imports are syntax. Reachability is a LOWER
BOUND -- Python reaches code through dispatch tables, getattr, decorators,
plugin registries and framework callbacks, none of which a parser can follow.
So "not reached" means "no static path was found", never "dead". Every consumer
of these results gets told which of the two it is holding.
"""

import os
from collections import defaultdict, deque

# Names that mean the definition is reached by something other than a call.
_LIVE_DECORATORS = ("route", "app.", "task", "fixture", "command", "click.",
                    "celery", "receiver", "register", "hook", "event",
                    "property", "setter", "validator", "cached", "api.")

# Method names Python itself calls.
_DUNDER_PREFIX = "__"


class Project:
    """Every module, plus the relationships between them."""

    def __init__(self, root, modules, skipped=0):
        self.root = os.path.abspath(root)
        self.modules = modules
        self.skipped = skipped
        self.by_dotted = {m.dotted: m for m in modules if m.dotted}
        self.by_relpath = {m.relpath: m for m in modules}
        self.imports = defaultdict(set)      # module -> internal modules it imports
        self.imported_by = defaultdict(set)
        self.external = defaultdict(set)     # module -> third-party/stdlib roots
        self.entry_points = []
        self.reachable = set()
        self.orphans = []
        self.cycles = []
        self.unreferenced = []
        self._build()

    # -- import graph -----------------------------------------------------
    def _resolve(self, module, target, level, alias=None):
        """Resolve an import to an internal module, or None if it is external.

        `from .formats import rtf` inside vanilla_extract.dispatch names the
        PACKAGE in `target` and the MODULE in `alias`, so resolving the target
        alone lands on the package and the real edge is missed -- which
        previously reported four actively-imported modules as orphans. Both
        forms are tried, most specific first.
        """
        if level:
            # For `from . import x` in package.sub.mod, level 1 means the
            # package that CONTAINS mod, i.e. drop `level` trailing parts.
            parts = module.dotted.split(".") if module.dotted else []
            base = parts[:-level] if level <= len(parts) else []
            dotted = ".".join([p for p in base + ([target] if target else []) if p])
        else:
            dotted = target
        if not dotted:
            return None

        candidates = []
        if alias:
            candidates.append(f"{dotted}.{alias}")     # from pkg import module
        candidates.append(dotted)                      # import pkg.module
        candidates.append(dotted.rsplit(".", 1)[0])    # from pkg.module import name
        for candidate in candidates:
            if candidate and candidate in self.by_dotted:
                return candidate

        # A top-level script imported by bare filename rather than package path.
        tail = dotted.split(".")[-1]
        for dot in self.by_dotted:
            if dot == tail or dot.endswith("." + tail):
                return dot
        return None

    def _build(self):
        for module in self.modules:
            for target, alias, lineno, level in module.imports:
                resolved = self._resolve(module, target, level, alias)
                if resolved and resolved != module.dotted:
                    self.imports[module.dotted].add(resolved)
                    self.imported_by[resolved].add(module.dotted)
                elif not resolved and target:
                    self.external[module.dotted].add(target.split(".")[0])

        self._find_entry_points()
        self._walk_reachable()
        self._find_orphans()
        self._find_cycles()
        self._find_unreferenced()

    # -- entry points -----------------------------------------------------
    def _find_entry_points(self):
        """Where execution can begin, with the reason recorded.

        The reason matters: `__main__` guard and CLI parsing are strong
        evidence, while "nobody imports it" is weak and may equally mean the
        file was abandoned. The difference is the caller's to judge, so it is
        preserved rather than flattened into a boolean.
        """
        for module in self.modules:
            reasons = []
            if module.has_main_guard:
                reasons.append("__main__ guard")
            names = {c[0] for c in module.calls}
            if any(n.startswith(("argparse.", "ArgumentParser", "click.",
                                 "typer.")) or n == "ArgumentParser"
                   for n in names):
                reasons.append("parses command-line arguments")
            if module.relpath.endswith(("__main__.py", "main.py", "cli.py",
                                        "run.py", "app.py", "manage.py",
                                        "wsgi.py", "asgi.py")):
                reasons.append(f"named {os.path.basename(module.relpath)}")
            if reasons:
                self.entry_points.append((module.dotted or module.relpath, reasons))

    def _walk_reachable(self):
        """Modules reachable by import from any entry point."""
        queue = deque(name for name, _ in self.entry_points)
        seen = set(queue)
        while queue:
            current = queue.popleft()
            for nxt in self.imports.get(current, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        self.reachable = seen

    def _find_orphans(self):
        """Modules nothing imports and nothing starts from.

        In a half-finished codebase these are the most informative files in the
        tree: they are usually where the author was working when they stopped,
        or an experiment they never wired in.
        """
        entry_names = {name for name, _ in self.entry_points}
        for module in self.modules:
            key = module.dotted or module.relpath
            if key in entry_names:
                continue
            if self.imported_by.get(key):
                continue
            if os.path.basename(module.relpath).startswith("test_"):
                continue                  # tests are run by a runner, not imported
            if module.relpath.endswith("__init__.py"):
                continue
            self.orphans.append(key)

    def _find_cycles(self):
        """Import cycles, which are usually a sign of a design that drifted."""
        colour = {}
        stack = []

        def walk(node):
            colour[node] = 1
            stack.append(node)
            for nxt in sorted(self.imports.get(node, ())):
                if colour.get(nxt) == 1:
                    cycle = stack[stack.index(nxt):] + [nxt]
                    if cycle not in self.cycles:
                        self.cycles.append(cycle)
                elif not colour.get(nxt):
                    walk(nxt)
            stack.pop()
            colour[node] = 2

        for name in sorted(self.by_dotted):
            if not colour.get(name):
                walk(name)

    # -- unreferenced definitions ----------------------------------------
    def _find_unreferenced(self):
        """Definitions no other code in this project appears to mention.

        Stated as an OBSERVATION, not a verdict. A name can be reached by a
        decorator, a registry, a template, an entry-point declaration or a
        caller outside this tree, so the honest claim is "no reference found
        here", and every record says why it might still be live.
        """
        referenced = set()
        for module in self.modules:
            referenced |= module.names_used
            referenced |= {c[0].rsplit(".", 1)[-1] for c in module.calls}
            referenced |= {alias for _, alias, _, _ in module.imports}

        for module in self.modules:
            for d in module.definitions:
                if d.name.startswith(_DUNDER_PREFIX):
                    continue
                if d.name in referenced:
                    continue
                caveat = None
                if any(any(k in dec for k in _LIVE_DECORATORS)
                       for dec in d.decorators):
                    caveat = ("decorated -- may be registered by a framework "
                              "rather than called")
                elif d.name.startswith("test_"):
                    caveat = "a test, collected by a runner rather than called"
                elif d.name.startswith("_"):
                    caveat = "private by convention; unused inside its own module"
                self.unreferenced.append({
                    "module": module.dotted or module.relpath,
                    "name": d.qualname, "kind": d.kind, "lineno": d.lineno,
                    "caveat": caveat,
                })

    # -- reporting helpers ------------------------------------------------
    def layers(self):
        """Modules grouped by import depth from the entry points.

        Depth is the shortest import path from a start point, which is a rough
        but useful stand-in for "how close is this to the top of the program".
        """
        depth = {}
        queue = deque((name, 0) for name, _ in self.entry_points)
        for name, _ in self.entry_points:
            depth[name] = 0
        while queue:
            current, d = queue.popleft()
            for nxt in self.imports.get(current, ()):
                if nxt not in depth or depth[nxt] > d + 1:
                    depth[nxt] = d + 1
                    queue.append((nxt, d + 1))
        grouped = defaultdict(list)
        for name, d in sorted(depth.items()):
            grouped[d].append(name)
        return dict(grouped)

    def fan(self):
        """(module, imports_out, imported_by) sorted by how central it is."""
        rows = []
        for module in self.modules:
            key = module.dotted or module.relpath
            rows.append((key, len(self.imports.get(key, ())),
                         len(self.imported_by.get(key, ()))))
        return sorted(rows, key=lambda r: (-(r[2]), -r[1]))
