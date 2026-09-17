"""Read a directory of Python source into a structured model.

Everything here is derived from the abstract syntax tree, so it is a fact about
the code rather than a guess about it. Nothing in this module executes, imports
or evaluates the source it reads -- a codebase you are trying to understand is
usually one you do not yet trust.

The model each file produces is deliberately flat and boring: names, line
numbers, and what refers to what. The interesting work happens later, when
those facts are assembled into a graph and read for signs of where the author
stopped.
"""

import ast
import io
import os
import re
import tokenize

# Calls that tell you what a module reaches out and touches. Grouped by the
# question a reader is actually asking: does this thing read my disk, talk to
# the network, run commands, or reach a database?
_EFFECTS = {
    "filesystem": {"open", "read_text", "write_text", "read_bytes", "write_bytes",
                   "remove", "unlink", "rename", "replace", "mkdir", "makedirs",
                   "rmtree", "copy", "copy2", "move", "glob", "walk", "listdir",
                   "chmod", "chown", "symlink", "touch"},
    "network": {"urlopen", "request", "get", "post", "put", "delete", "head",
                "connect", "socket", "create_connection", "sendall", "recv",
                "urlretrieve", "Session"},
    "subprocess": {"run", "Popen", "call", "check_call", "check_output",
                   "system", "spawn", "execv", "execve", "fork"},
    "database": {"execute", "executemany", "cursor", "commit", "rollback",
                 "connect_db", "query", "fetchall", "fetchone"},
    "serialization": {"loads", "load", "dumps", "dump", "unpickle", "pickle"},
    "dynamic": {"eval", "exec", "compile", "__import__", "getattr", "setattr",
                "globals", "locals", "importlib"},
}

# Import names that mark a module as reaching outside the process, used to
# qualify the call-name evidence above (a bare `get` means little; a bare `get`
# in a module that imported requests means a lot).
_EFFECT_IMPORTS = {
    "network": {"requests", "urllib", "http", "socket", "httpx", "aiohttp",
                "ftplib", "smtplib", "paramiko", "websockets"},
    "subprocess": {"subprocess", "os", "pty", "commands", "sh"},
    "database": {"sqlite3", "psycopg2", "pymysql", "sqlalchemy", "pymongo",
                 "redis", "asyncpg", "MySQLdb"},
    "filesystem": {"os", "shutil", "pathlib", "glob", "tempfile", "zipfile"},
}


class Definition:
    """A function or class defined in a module."""

    __slots__ = ("name", "kind", "lineno", "end_lineno", "args", "docstring",
                 "decorators", "is_async", "parent", "body_kind", "returns", "bases")

    def __init__(self, name, kind, lineno, end_lineno, args, docstring,
                 decorators, is_async, parent, body_kind, returns, bases=()):
        self.name = name
        self.kind = kind                  # "function" | "method" | "class"
        self.lineno = lineno
        self.end_lineno = end_lineno
        self.args = args
        self.docstring = docstring
        self.decorators = decorators
        self.is_async = is_async
        self.parent = parent              # enclosing class, if any
        self.body_kind = body_kind        # "code" | "pass" | "ellipsis" | "raise"
        self.returns = returns            # True when any return carries a value
        # Base class names as written: its own for a class, its enclosing
        # class's for a method. An empty body inside a Protocol or an ABC is
        # idiomatic, and without this the dead-end report put six Protocol
        # methods above every real stub in the project.
        self.bases = tuple(bases)

    @property
    def qualname(self):
        return f"{self.parent}.{self.name}" if self.parent else self.name

    def as_dict(self):
        return {s: getattr(self, s) for s in self.__slots__} | {
            "qualname": self.qualname}


class Module:
    """One .py file, reduced to the facts worth graphing."""

    def __init__(self, path, root):
        self.path = path
        self.relpath = os.path.relpath(path, root)
        self.dotted = self._dotted(self.relpath)
        self.error = None
        self.source = ""
        self.loc = 0
        self.blank = 0
        self.comment_lines = 0
        self.docstring = None
        # (module, BOUND name, lineno, level, IMPORTED name).
        # The bound and imported names differ under `as`, and the two
        # are needed for opposite jobs: the bound name says whether the
        # import is used in the body, the imported name is what has to
        # be resolved against the package. Collapsing them made
        # `from . import snippets as snip` resolve to `pkg.snip`, and
        # cobblerpy reported its own snippets module as an orphan.
        self.imports = []
        self.definitions = []
        self.calls = []                   # (name, lineno)
        self.names_used = set()
        self.toplevel_effects = []        # statements that run on import
        self.has_main_guard = False
        # `#!/usr/bin/env python` on line 1. The author is saying this file is
        # run as a program, which is the strongest statement available about a
        # module nothing imports -- stronger than any guess from its name.
        self.shebang = None
        self.effects = {}                 # category -> [(name, lineno)]
        self.todos = []                   # (tag, text, lineno)
        self.commented_code = []          # linenos that look like disabled code
        # Lines the author marked as a deliberately unused import. `# noqa:
        # F401` is what every Python linter reads, so it is what a Python
        # author will already have written when they meant it.
        self.kept_imports = set()
        # Set when the comment scan stopped early, so "no TODOs here" can be
        # told apart from "the comments could not be read".
        self.comments_incomplete = None
        self.strings = []

    @staticmethod
    def _dotted(relpath):
        stem = relpath[:-3] if relpath.endswith(".py") else relpath
        parts = [p for p in stem.replace("\\", "/").split("/") if p]
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def as_dict(self):
        return {
            "path": self.path, "relpath": self.relpath, "module": self.dotted,
            "error": self.error, "loc": self.loc, "blank": self.blank,
            "comment_lines": self.comment_lines, "docstring": self.docstring,
            "imports": self.imports,
            "definitions": [d.as_dict() for d in self.definitions],
            "calls": self.calls, "toplevel_effects": self.toplevel_effects,
            "has_main_guard": self.has_main_guard, "shebang": self.shebang,
            "effects": self.effects,
            "todos": self.todos, "commented_code": self.commented_code,
        }


class _Visitor(ast.NodeVisitor):
    """Walks one module's tree, recording what it finds."""

    def __init__(self, module):
        self.m = module
        self._class_stack = []
        self._base_stack = []

    # -- imports ---------------------------------------------------------
    def visit_Import(self, node):
        for alias in node.names:
            self.m.imports.append((alias.name, alias.asname or alias.name,
                                   node.lineno, 0, alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        base = node.module or ""
        for alias in node.names:
            self.m.imports.append((base, alias.asname or alias.name,
                                   node.lineno, node.level or 0, alias.name))
        self.generic_visit(node)

    # -- definitions -----------------------------------------------------
    def _body_kind(self, node):
        """Classify a body so an unfinished one is visible later."""
        body = [n for n in node.body
                if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                        and isinstance(n.value.value, str))]
        if not body:
            return "pass"                 # docstring only
        if len(body) == 1:
            only = body[0]
            if isinstance(only, ast.Pass):
                return "pass"
            if (isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant)
                    and only.value.value is Ellipsis):
                return "ellipsis"
            if isinstance(only, ast.Raise):
                name = ""
                exc = only.exc
                if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                    name = exc.func.id
                elif isinstance(exc, ast.Name):
                    name = exc.id
                return "raise" if "NotImplemented" in name else "code"
        return "code"

    def _record_def(self, node, kind, is_async=False):
        args = []
        if hasattr(node, "args"):
            a = node.args
            args = [x.arg for x in (a.posonlyargs + a.args + a.kwonlyargs)]
            if a.vararg:
                args.append("*" + a.vararg.arg)
            if a.kwarg:
                args.append("**" + a.kwarg.arg)
        returns = any(isinstance(n, ast.Return) and n.value is not None
                      for n in ast.walk(node))
        parent = self._class_stack[-1] if self._class_stack else None
        self.m.definitions.append(Definition(
            name=node.name,
            kind="method" if (kind == "function" and parent) else kind,
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            args=args,
            docstring=ast.get_docstring(node),
            decorators=[_name_of(d) for d in node.decorator_list],
            is_async=is_async,
            parent=parent,
            # For a class, its own bases. For a method, the bases of the class
            # that contains it -- which is what decides whether an empty body
            # is idiomatic.
            bases=([_name_of(b) for b in node.bases]
                   if isinstance(node, ast.ClassDef)
                   else (self._base_stack[-1] if self._base_stack else [])),
            body_kind=self._body_kind(node),
            returns=returns,
        ))

    def visit_FunctionDef(self, node):
        self._record_def(node, "function")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self._record_def(node, "function", is_async=True)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._record_def(node, "class")
        self._class_stack.append(node.name)
        self._base_stack.append([_name_of(b) for b in node.bases])
        self.generic_visit(node)
        self._base_stack.pop()
        self._class_stack.pop()

    # -- references ------------------------------------------------------
    def visit_Call(self, node):
        name = _name_of(node.func)
        if name:
            self.m.calls.append((name, node.lineno))
        self.generic_visit(node)

    def visit_Name(self, node):
        self.m.names_used.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        self.m.names_used.add(node.attr)
        self.generic_visit(node)


def _name_of(node):
    """Dotted name for a call target or decorator, as written in the source."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    return ""


# Tags that mark work the author knew was unfinished.
_TODO_TAGS = ("TODO", "FIXME", "XXX", "HACK", "BUG", "NOTE", "WIP", "TEMP",
              "REVISIT", "REFACTOR")

# A tag is a MARKER, not a substring.
#
# Matching `tag in text.upper()` reported 19 of this project's 20 tagged
# comments and 585 of the corpus's 641, because every one of these words hides
# inside an ordinary one: the temporary tag inside "attempted" and
# "attempts", the bug tag inside "debug", the note tag inside
# "DESIGN_NOTES" and inside the word "note", the wip tag inside
# "swipe". Those
# findings fed the score that ranks where the work stopped, so the ranking was
# being driven by the word "attempts" appearing in a comment.
#
# Two things make a tag a tag, and either is enough:
#   - it is written in CAPITALS, which is what the convention is for, or
#   - it is immediately followed by ":" or "(", as in a lower-case
#     tag with a colon, or a tag with an owner in brackets.
#
# A word boundary is required either way, which is what rejects "debug".
_TAG_WORD = re.compile(r"\b(" + "|".join(_TODO_TAGS) + r")\b", re.IGNORECASE)


def _tag_of(text):
    """The marker tag in this comment, uppercased, or None.

    Near-misses this must reject, each checked in the tests by removing the
    clause that rejects it: "attempted" and "attempts" (no boundary before
    TEMP), "debug" (none before BUG), "DESIGN_NOTES" (none after NOTE),
    "# bug wearing a different hat" (lower case, no colon).
    """
    for found in _TAG_WORD.finditer(text or ""):
        word = found.group(1)
        after = (text[found.end():] or "")[:1]
        if word.isupper() or after in (":", "("):
            return word.upper()
    return None

# Deciding whether a comment is disabled CODE or ordinary prose.
#
# "It parses as Python" is far too weak a test: an astonishing amount of prose
# is syntactically valid. This rule was built by the regex-ninja method --
# write the near-misses, then prove WHICH CLAUSE rejects each one -- and
# measured against 48,482 real comments from this machine's Python.
#
# That measurement deleted two clauses. A minimum-length check and a
# divider-stripping check (added specifically to stop `# --- DOCX`, which
# parses as -(-(-DOCX))) each changed ZERO verdicts across the whole corpus,
# because the statement rule below was already rejecting those cases. They were
# post-hoc suppression hiding which mechanism was load-bearing, so they are
# gone. What remains is what measurably does the work:
#
#   pragma guard      3 verdicts   `# pylint: disable=x` parses as an
#                                  annotated assignment WITH a value, so the
#                                  statement rule alone would accept it
#   example guard   117 verdicts   payload indented past the '#' is a worked
#                                  example in an explanatory block, not code
#                                  someone disabled -- CPython's typing.py is
#                                  full of these
#   statement rule 1143 verdicts   the real guard: a bare name, an expression
#                                  or an unvalued annotation is not code
#                                  anybody disabled
#
# Net effect on the corpus: 0.30% of real comments flagged, and the survivors
# read like genuine leftovers (`# return il`, `# import types, weakref`).
_CODE_STATEMENTS = (ast.Assign, ast.AugAssign, ast.Return, ast.Assert,
                    ast.Import, ast.ImportFrom, ast.FunctionDef,
                    ast.AsyncFunctionDef, ast.ClassDef, ast.For, ast.AsyncFor,
                    ast.While, ast.If, ast.With, ast.AsyncWith, ast.Try,
                    ast.Delete, ast.Global, ast.Nonlocal, ast.Raise)

_PRAGMA_PREFIXES = ("noqa", "type", "pragma", "pylint", "mypy", "ruff",
                    "flake8", "nosec", "fmt", "isort", "coverage", "skip")

# Payload indented this far past the '#' is a formatted example, not a
# disabled line. Real commented-out code carries its indentation OUTSIDE the
# '#', so it starts within a space or two of it.
_EXAMPLE_INDENT = 3


# `# noqa: F401`, `# noqa:F401,E501`, or a bare `# noqa`. F401 is flake8's
# code for an imported-but-unused name; a bare noqa silences everything and
# so covers it too. Anchored on the word so `# not a noqa thing` is prose.
_KEEPS_IMPORT = re.compile(r"#\s*noqa(?::\s*(?P<codes>[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*))?",
                           re.IGNORECASE)


def _keeps_import(text):
    """True when this comment marks an unused import as deliberate."""
    found = _KEEPS_IMPORT.search(text or "")
    if not found:
        return False
    codes = found.group("codes")
    return not codes or "F401" in codes.upper()


def _looks_like_code(text):
    """True when a comment is a disabled STATEMENT, not prose that happens to parse."""
    after_hash = text[1:] if text.startswith("#") else text
    if len(after_hash) - len(after_hash.lstrip(" ")) >= _EXAMPLE_INDENT:
        return False                      # worked example inside a comment block
    body = text.lstrip("#").strip()
    if body.split(":", 1)[0].strip().lower() in _PRAGMA_PREFIXES:
        return False                      # linter or type pragma
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False
    if not tree.body:
        return False
    node = tree.body[0]
    if isinstance(node, _CODE_STATEMENTS):
        return True
    if isinstance(node, ast.Expr):
        # A bare expression only counts when it actually calls something.
        return isinstance(node.value, (ast.Call, ast.Await, ast.Yield))
    if isinstance(node, ast.AnnAssign):
        return node.value is not None
    return False


def _read_comments(module, source):
    """Collect TODO-style tags and commented-out code from the token stream.

    Comments are invisible to the AST, and they are where an author records
    what they meant to come back to -- which makes them the single richest
    source of intent in an undocumented codebase.
    """
    source_lines = source.split("\n")
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            module.comment_lines += 1
            text = tok.string
            if _keeps_import(text):
                module.kept_imports.add(tok.start[0])
            tag = _tag_of(text)
            if tag:
                note = text.lstrip("#").strip()
                module.todos.append((tag, note[:200], tok.start[0]))
            else:
                # Only a comment that OWNS its line can be disabled code.
                # `candidates.append(dotted)  # import pkg.module` is a note
                # about the line it sits on, and nobody disables a statement
                # by appending it to a live one. Every one of this project's
                # own five "commented-out code" findings was a trailing
                # annotation, and 7 of the larger corpus's 22 were; a finding a reader
                # can dismiss in five seconds teaches them to dismiss the rest.
                # tok.start[1] is the column the comment starts at, so a zero
                # prefix once whitespace is removed means it stands alone.
                prefix = source_lines[tok.start[0] - 1][:tok.start[1]] \
                    if tok.start[0] - 1 < len(source_lines) else ""
                if not prefix.strip() and _looks_like_code(text):
                    module.commented_code.append(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        # Recorded, not swallowed. Half a file's comments read and the rest
        # dropped looks exactly like a file with no TODOs in it, and the map
        # would have shown it as the cleanest module in the project.
        module.comments_incomplete = f"{type(exc).__name__}: {exc}"[:120]


def _classify_effects(module):
    """Which outside systems this module reaches, and on what evidence."""
    imported_roots = {imp[0].split(".")[0] for imp in module.imports if imp[0]}
    imported_roots |= {imp[1].split(".")[0] for imp in module.imports}
    found = {}
    for name, lineno in module.calls:
        last = name.rsplit(".", 1)[-1]
        root = name.split(".", 1)[0]
        for category, names in _EFFECTS.items():
            if last not in names:
                continue
            # A bare, common verb only counts when the module imported
            # something that makes it plausible. `get` alone is noise;
            # `requests.get`, or `get` in a module importing requests, is not.
            gate = _EFFECT_IMPORTS.get(category)
            qualified = "." in name
            if gate and not qualified and not (imported_roots & gate):
                continue
            if gate and qualified and root not in imported_roots and root not in (
                    "self", "cls"):
                if root not in gate:
                    continue
            found.setdefault(category, []).append((name, lineno))
    module.effects = found


def _toplevel_effects(tree):
    """Statements that run merely because the module was imported.

    Import-time side effects are the usual reason a codebase is hard to pick
    up: reading a file or opening a connection at import turns every later
    decision into a constraint.
    """
    out = []
    benign = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
              ast.ClassDef, ast.Pass)
    for node in tree.body:
        if isinstance(node, benign):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue                      # module docstring
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            # A plain constant assignment is configuration, not an effect.
            value = getattr(node, "value", None)
            if value is None or isinstance(value, (ast.Constant, ast.Tuple,
                                                   ast.List, ast.Dict, ast.Set,
                                                   ast.Name, ast.Attribute,
                                                   ast.JoinedStr, ast.UnaryOp,
                                                   ast.BinOp)):
                continue
        if isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                continue                  # the main guard is handled separately
        out.append((type(node).__name__, node.lineno))
    return out


def _shebang(source):
    """The interpreter line, if the file has one.

    Only a `#!` on the very first line counts -- that is the only place the
    kernel looks, so a `#!` anywhere else is an ordinary comment and treating
    it as an execution signal would be inventing one.
    """
    first = (source or "").split("\n", 1)[0].strip()
    return first if first.startswith("#!") else None


def _has_main_guard(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"):
            return True
    return False


def scan_file(path, root):
    """Parse one file into a Module. A syntax error is recorded, never raised."""
    module = Module(path, root)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        module.error = f"unreadable: {exc}"
        return module

    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            source = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        source = raw.decode("latin-1", errors="replace")

    module.source = source
    lines = source.splitlines()
    module.loc = len(lines)
    module.blank = sum(1 for line in lines if not line.strip())

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        # A file that does not parse is itself a finding: it is either for a
        # different Python version, or it was left mid-edit.
        module.error = f"syntax error at line {exc.lineno}: {exc.msg}"
        _read_comments(module, source)
        return module
    except (ValueError, MemoryError, RecursionError) as exc:
        # A pathological file defeats the parser rather than the grammar: a huge
        # unary chain overflows the parser stack (MemoryError), deep nesting
        # exhausts recursion, a null byte is rejected outright (ValueError). One
        # such file must not abort a survey of a thousand others -- it is a
        # finding, recorded like any unparsable file. (_looks_like_code catches
        # the same set for the same reason.)
        module.error = f"could not parse: {type(exc).__name__}"
        _read_comments(module, source)
        return module

    module.shebang = _shebang(source)
    module.docstring = ast.get_docstring(tree)
    _Visitor(module).visit(tree)
    module.toplevel_effects = _toplevel_effects(tree)
    module.has_main_guard = _has_main_guard(tree)
    _read_comments(module, source)
    _classify_effects(module)
    return module


# Directories that are never the codebase you are trying to understand.
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
             ".pytest_cache", "node_modules", "site-packages", ".eggs", "build",
             "dist", ".idea", ".vscode", "htmlcov", ".ruff_cache"}


def scan_tree(root, skip_dirs=None, max_files=5000):
    """Scan every .py under `root`.

    Returns (modules, skipped_count, excluded) where `excluded` maps a
    directory name to how many .py files were passed over because of it.

    The third value exists because the second was not enough. Pointed at a
    real project this walked past 9,219 Python files -- 5,974 in a `.claude`
    directory and 3,245 in a virtualenv's site-packages -- surveyed 975, and
    reported "0 skipped", which is true of the file LIMIT and silent about
    everything else. Excluding a virtualenv is right; saying nothing about it
    is the behaviour this tool exists to argue against.
    """
    skip = set(skip_dirs or ()) | SKIP_DIRS
    root = os.path.abspath(root)
    modules, skipped = [], 0
    excluded = {}
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            if d in skip or d.startswith("."):
                # Count what is inside before dropping it, or the number is
                # unknowable by the time anybody asks.
                buried = sum(1 for _r, _d, fs in os.walk(os.path.join(dirpath, d))
                             for f in fs if f.endswith(".py"))
                if buried:
                    excluded[d] = excluded.get(d, 0) + buried
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            if len(modules) >= max_files:
                skipped += 1
                continue
            modules.append(scan_file(os.path.join(dirpath, name), root))
    return modules, skipped, excluded
