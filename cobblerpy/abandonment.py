"""Where the previous developer stopped, and what they meant to come back to.

This is the part of the tool that earns its keep. Structure can be read from
any codebase; what someone inheriting an unfinished one actually needs is a map
of the frontier -- the places where work was started and left.

Every signal here is a FACT ABOUT THE SOURCE, not a judgement. A `pass` body is
a `pass` body; whether it is a deliberate no-op or an abandoned stub is for the
reader to decide, and the tool's job is to put it in front of them with enough
context to decide quickly. Signals carry a weight so they can be ranked, and a
note saying what would make the signal innocent.
"""

import re
import warnings

from collections import defaultdict

# weight, label, and the innocent explanation that must be offered alongside
SIGNALS = {
    "todo": (3, "explicit TODO/FIXME left in a comment",
             "some teams use these as permanent annotations"),
    "stub_pass": (4, "function body is only `pass`",
                  "may be a deliberate no-op or an interface placeholder"),
    "stub_ellipsis": (4, "function body is only `...`",
                      "normal in .pyi stubs and Protocol definitions"),
    "not_implemented": (5, "raises NotImplementedError",
                        "expected in an abstract base class"),
    "unused_import": (4, "imported but never used in the file",
                      "may be re-exported deliberately, especially in __init__.py"),
    "commented_code": (3, "a block of code left commented out",
                       "sometimes kept as a worked example"),
    "empty_except": (3, "exception caught and ignored",
                     "occasionally intentional, but usually unfinished"),
    "no_docstring": (1, "public definition with no docstring",
                     "common in code that was never meant to be read by others"),
    "promised_return": (3, "docstring describes a return value the code never returns",
                        "the docstring may simply be stale"),
    "syntax_error": (6, "file does not parse",
                     "may target a different Python version"),
    "orphan": (4, "nothing imports it and nothing starts from it",
               "may be run directly, or reached by a tool outside this tree"),
    "unreached": (2, "no import path reaches it from any entry point",
                  "reachability is static; frameworks and plugins are invisible to it"),
}


# A docstring PROMISES a return in one of three shapes, and prose that
# happens to contain the word is not one of them.
#
# The old rule was `"returns " in docstring.lower()`, which fired on 165
# functions in the corpus and 1 here -- and every one of them was a paragraph
# mentioning what something ELSE returns. The test it caught in this project
# says "the stub returns [] from querySelectorAll"; two it caught over there
# are four-paragraph design notes. A signal wrong 96% of the time still feeds
# the score that ranks where the work stopped.
_RETURN_SECTION = re.compile(r"^\s*(returns?|:returns?|:rtype)\b[:\s]",
                             re.IGNORECASE | re.MULTILINE)


def _promises_a_return(docstring):
    """True when the docstring says THIS function returns something.

    Three shapes, all of them conventions rather than guesses:
      - the summary line begins "Return ..." / "Returns ..."
      - a `Returns:` section, Google or NumPy style
      - a Sphinx `:return:` or `:rtype:` field
    """
    text = (docstring or "").strip()
    if not text:
        return False
    first = text.split("\n", 1)[0].strip()
    if re.match(r"^returns?\b", first, re.IGNORECASE):
        return True
    return bool(_RETURN_SECTION.search(text))


def _unused_imports(module):
    """Imported names the file never mentions again.

    The single most reliable trace of interrupted work: someone pulls in a
    library, writes the import, and stops before using it.
    """
    out = []
    if module.relpath.endswith("__init__.py"):
        return out                        # re-exporting is the point there
    for target, alias, lineno, _level, _imported in module.imports:
        if alias == "*":
            continue
        head = alias.split(".")[0]
        if head in module.names_used:
            continue
        # A module imported for its side effects, or used only in a string
        # (type annotations under `from __future__ import annotations`).
        if any(head in s for s in module.strings):
            continue
        if head in ("annotations", "__future__"):
            continue
        # The author said they meant it. An import that exists so a bundler
        # can see the package is real and deliberate, and reporting it as
        # abandoned work is a finding the reader dismisses in five seconds --
        # after which they start dismissing the others too.
        if lineno in getattr(module, "kept_imports", ()):
            continue
        out.append((alias, target, lineno))
    return out


def _empty_excepts(module):
    """`except: pass` -- an error someone decided not to deal with yet."""
    import ast
    if module.error or not module.source:
        return []
    out = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # the surveyed code's, not ours
            tree = ast.parse(module.source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = [n for n in node.body if not isinstance(n, ast.Pass)]
        if not body:
            out.append(node.lineno)
    return out


# An empty body under these is a DECLARATION, not a gap: typed libraries write
# `...` for every @overload signature and Protocol method, and an abstract
# method is supposed to raise NotImplementedError. On itsdangerous they were 11
# of its 11 "stub" signals and put two finished modules at the top. An ABC is
# NOT on this list: a method on one without @abstractmethod is a concrete
# method, and its `pass` is as unfinished as anybody else's.
_DECLARING_DECORATORS = {"overload", "abstractmethod", "abstractproperty",
                         "abstractclassmethod", "abstractstaticmethod"}
_DECLARING_BASES = {"Protocol"}


def _tail(name):
    return str(name).split("[", 1)[0].rsplit(".", 1)[-1]


def _classes_in(module):
    return {d.name for d in module.definitions
            if d.kind == "class" and d.parent is None}


def _class_named(project, module, written):
    """(module, class) that a name written in `module` refers to, or None.

    Resolved through the import graph's own resolver, so `Runner` in one
    module and `Runner` in another are two classes -- matching bare names
    exempted a stub because an unrelated class of the same name had a
    subclass somewhere else.
    """
    written = str(written).split("[", 1)[0]
    if "." not in written and written in _classes_in(module):
        return module.dotted, written
    prefix, _dot, name = written.rpartition(".")
    for target, alias, _ln, level, imported in module.imports:
        if not prefix and alias == written:            # from x import Name
            found = project._resolve(module, target, level, alias, imported)
            if found and imported in _classes_in(project.by_dotted[found]):
                return found, imported
        elif prefix and alias == prefix:               # import x; x.Name
            found = project._resolve(module, target, level, alias, imported)
            if found and name in _classes_in(project.by_dotted[found]):
                return found, name
    return None


def replaced_methods(project):
    """(module, class, method) for each base method that EVERY subclass the
    project defines replaces, on a base nothing calls directly.

    That is a method abstract in all but the decorator: no call can land on
    it. One subclass that inherits it, or one `Base()` somewhere, and a call
    can -- so it stays a stub.
    """
    subclasses, methods, called = {}, {}, set()
    for module in project.modules:
        for d in module.definitions:
            if d.kind == "class" and d.parent is None:
                mine = (module.dotted, d.name)
                methods.setdefault(mine, set())
                for base in d.bases:
                    key = _class_named(project, module, base)
                    if key:
                        subclasses.setdefault(key, []).append(mine)
            elif d.kind == "method":
                methods.setdefault((module.dotted, d.parent), set()).add(d.name)
        for name, _ln in module.calls:
            for written in (name, name.rpartition(".")[0]):
                key = written and _class_named(project, module, written)
                if key:
                    called.add(key)
    out = set()
    for base, subs in subclasses.items():
        if base in called:
            continue
        for method in methods.get(base, ()):
            if all(method in methods.get(sub, ()) for sub in subs):
                out.add((base[0], base[1], method))
    return out


def declares_a_shape(definition, module=None, replaced=()):
    """True when an empty body is the definition's whole job."""
    if any(_tail(x) in _DECLARING_DECORATORS for x in definition.decorators):
        return True
    if definition.kind != "method":
        return False
    return (any(_tail(b) in _DECLARING_BASES for b in definition.bases)
            or (getattr(module, "dotted", None), definition.parent,
                definition.name) in replaced)


def analyse_module(module, replaced=()):
    """Every abandonment signal in one module, with line numbers."""
    found = defaultdict(list)

    if module.error:
        if module.error.startswith("syntax error"):
            found["syntax_error"].append((module.error, 0))
        return dict(found)

    for _tag, text, lineno in module.todos:
        found["todo"].append((text, lineno))    # the comment begins with its tag

    for d in module.definitions:
        if d.kind == "class":
            continue
        if d.body_kind != "code" and declares_a_shape(d, module, replaced):
            pass                          # a declaration, not a gap
        elif d.body_kind == "pass":
            found["stub_pass"].append((d.qualname, d.lineno))
        elif d.body_kind == "ellipsis":
            found["stub_ellipsis"].append((d.qualname, d.lineno))
        elif d.body_kind == "raise":
            found["not_implemented"].append((d.qualname, d.lineno))
        if not d.docstring and not d.name.startswith("_") and d.kind != "method":
            found["no_docstring"].append((d.qualname, d.lineno))
        if (d.docstring and not d.returns and d.body_kind == "code"
                and _promises_a_return(d.docstring)):
            found["promised_return"].append((d.qualname, d.lineno))

    for alias, target, lineno in _unused_imports(module):
        found["unused_import"].append((f"{alias} (from {target or 'import'})", lineno))

    for lineno in module.commented_code:
        found["commented_code"].append(("commented-out code", lineno))

    for lineno in _empty_excepts(module):
        found["empty_except"].append(("exception silently ignored", lineno))

    return dict(found)


def score(signals):
    """Rank a module's frontier by weighted signal count."""
    total = 0
    for kind, hits in signals.items():
        weight = SIGNALS.get(kind, (1,))[0]
        # Diminishing returns: forty TODOs in one file is one situation, not
        # forty, and without this a single noisy module buries everything else.
        total += weight * (len(hits) ** 0.6)
    return round(total, 1)


def analyse_project(project):
    """Abandonment signals for every module, ranked.

    Returns a list of dicts sorted by score, highest first -- read top-down,
    that is the order in which an inheritor should look at the code.
    """
    entry_names = {name for name, _ in project.entry_points}
    replaced = replaced_methods(project)
    rows = []
    for module in project.modules:
        key = module.dotted or module.relpath
        signals = analyse_module(module, replaced)
        if key in project.orphans:
            signals["orphan"] = [("nothing imports this module", 0)]
        elif key not in project.reachable and key not in entry_names:
            signals["unreached"] = [("no static path from an entry point", 0)]
        rows.append({
            "module": key,
            "relpath": module.relpath,
            "loc": module.loc,
            "score": score(signals),
            "signals": signals,
            "counts": {k: len(v) for k, v in signals.items()},
        })
    rows.sort(key=lambda r: (-r["score"], r["relpath"]))
    return rows


def summarise(rows):
    """Totals per signal type across the project."""
    totals = defaultdict(int)
    for row in rows:
        for kind, hits in row["signals"].items():
            totals[kind] += len(hits)
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))
