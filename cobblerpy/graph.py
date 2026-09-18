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

from . import conventions

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
        # Module key -> the Convention that loads it. Held OUT of orphans and
        # kept, because "held back, and here is the rule and the tool" is a
        # finding; silently dropping the file is how the reader ends up
        # believing the tree is smaller than it is.
        self.convention_reached = {}
        self.cycles = []
        self.unreferenced = []
        self._build()

    # -- import graph -----------------------------------------------------
    def _resolve(self, module, target, level, alias=None, imported=None):
        """Resolve an import to an internal module, or None if it is external.

        `from .formats import rtf` inside vanilla_extract.dispatch names the
        PACKAGE in `target` and the MODULE in `imported`, so resolving the
        target alone lands on the package and the real edge is missed -- which
        previously reported four actively-imported modules as orphans.

        `imported` is the name as WRITTEN in the source; `alias` is what it is
        bound to. Under `from . import snippets as snip` those differ, and
        resolving the bound name looks for `pkg.snip`, which does not exist --
        cobblerpy reported its own snippets module as an orphan on that path.
        All forms are tried, most specific first.
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
        if imported:
            candidates.append(f"{dotted}.{imported}")  # from pkg import module
        if alias and alias != imported:
            candidates.append(f"{dotted}.{alias}")     # bound name, as a fallback
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

    def evidence_for(self, source, target):
        """The lines in `source` that show how it reaches `target`.

        The map named its connections and nothing else: `uses: a, b, c`. But a
        reader asking how two modules are related wants the line that joins
        them, and the scanner already records both halves -- every import
        carries its lineno, and every call site carries the name as written
        plus its own lineno.

        Two kinds of line count as evidence. The import that brings the target
        into scope, and any call made through a name that import bound. Under
        `from b import go` the call reads `go()`, not `b.go()`, so matching on
        the target's own name finds nothing -- the BOUND names are what the
        call sites mention. Resolution goes through _resolve, the same function
        the import graph itself is built from, so an edge shown on the chart
        and the evidence for it can never disagree.

        Returns [] for an edge the source does not have. A relationship the
        code does not show is not one to illustrate.
        """
        module = self.by_dotted.get(source)
        if module is None or target not in self.by_dotted:
            return []

        bound, linenos = set(), set()
        for raw, alias, lineno, level, imported in module.imports:
            if self._resolve(module, raw, level, alias, imported) == target:
                linenos.add(lineno)
                if alias:
                    bound.add(alias)
        if not linenos:
            return []

        for name, lineno in module.calls:
            if name in bound or name.split(".")[0] in bound:
                linenos.add(lineno)

        lines = (module.source or "").splitlines()
        return [{"line": n, "text": lines[n - 1].rstrip()}
                for n in sorted(linenos) if 1 <= n <= len(lines)]

    def _build(self):
        for module in self.modules:
            for target, alias, lineno, level, imported in module.imports:
                resolved = self._resolve(module, target, level, alias,
                                         imported)
                if resolved and resolved != module.dotted:
                    self.imports[module.dotted].add(resolved)
                    self.imported_by[resolved].add(module.dotted)
                elif not resolved and target:
                    self.external[module.dotted].add(target.split(".")[0])

        self._find_entry_points()
        # Conventions are read BEFORE the reachability walk, because a module
        # something else loads is a place execution begins. Held back from the
        # orphan list but not used as a root, the tool said "pytest loads
        # conftest.py" and then reported everything conftest imports as
        # unreached -- two statements about the same file that cannot both be
        # true. On the corpus that left 23 modules unreachable whose only
        # importer was a file we had just said gets loaded.
        self.convention_reached = conventions.reached(self.root, self.modules)
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
            if getattr(module, "shebang", None):
                # The author wrote an interpreter line. Nothing imports a
                # script and nothing is meant to: a build script with fifty
                # commits behind it was being reported as unreachable code
                # because the only evidence it was ever run sat on line one,
                # which nothing read.
                reasons.append("has a #! line, so it is run directly")
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

    def roots(self):
        """Every place the code is run from, in one list.

        Two kinds, and the difference is recorded elsewhere rather than
        flattened here: an entry point begins execution on its own evidence,
        and a convention-reached module is one a NAMED tool begins it in.
        Both are places the program starts.

        ONE list, because reachability was computed twice from two different
        root sets -- _walk_reachable() here and layers() below -- and fixing
        the first left the second disagreeing with it. The map colours a
        module from layers(); the totals count it from reachable.
        """
        seen, out = set(), []
        for name, _why in self.entry_points:
            if name not in seen:
                seen.add(name)
                out.append(name)
        for name in self.convention_reached:
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def _walk_reachable(self):
        """Modules reachable by import from anything that gets run."""
        queue = deque(self.roots())
        seen = set(queue)
        while queue:
            current = queue.popleft()
            for nxt in self.imports.get(current, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        self.reachable = seen

    def _find_orphans(self):
        """Modules nothing imports, nothing starts from, and no tool loads.

        In a half-finished codebase these are the most informative files in the
        tree: usually where the author was working when they stopped, or an
        experiment they never wired in.

        The exclusions used to be two conditions written into this loop -- a
        `test_` prefix and `__init__.py` -- which was right about those two
        cases, wrong about every other thing a tool loads without importing
        (conftest.py, __main__.py, wsgi.py, a declared console script), and
        silent either way: a file matching them simply vanished, and the count
        of orphans meant "orphans, minus a couple we do not print". Every
        exclusion now comes from conventions.py, names the tool that does the
        loading, and is KEPT so the map can say what it held back and why.
        """
        entry_names = {name for name, _ in self.entry_points}
        for module in self.modules:
            key = module.dotted or module.relpath
            if key in entry_names:
                continue
            if self.imported_by.get(key):
                continue
            if key in self.convention_reached:
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
            referenced |= {alias for _, alias, _, _, _ in module.imports}

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
        """Modules grouped by import depth from the places code is run from.

        Depth is the shortest import path from a start point, which is a rough
        but useful stand-in for "how close is this to the top of the program".
        """
        depth = {}
        starts = self.roots()
        queue = deque((name, 0) for name in starts)
        for name in starts:
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
