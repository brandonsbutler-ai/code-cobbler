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
    "stub_pass": (4, "function body is only `pass`, and says so in the body",
                  "the marker may be a standing note rather than a promise"),
    "stub_ellipsis": (4, "function body is only `...`",
                      "normal in Protocol and @overload declarations"),
    "not_implemented": (4, "raises NotImplementedError, and says so in the body",
                        "the marker may be a standing note rather than a promise"),
    "unused_import": (4, "imported but never used in the file",
                      "may be imported for a side effect; `# noqa: F401` marks "
                      "one the author meant to keep"),
    "commented_code": (3, "a block of code left commented out",
                       "sometimes kept as a worked example"),
    "empty_except": (3, "exception caught and ignored",
                     "occasionally intentional, but usually unfinished"),
    "no_docstring": (0, "an undocumented public function in a module that "
                        "documents its others",
                     "a note about readability, not evidence of unfinished "
                     "work -- it carries no weight and ranks nothing"),
    "promised_return": (3, "docstring describes a return value the code never returns",
                        "the docstring may simply be stale"),
    "syntax_error": (6, "file does not parse",
                     "may target a different Python version"),
    # A file the tool could not read at all. Without this, the only error that
    # raised a signal was "syntax error ...", so a file that defeated the
    # PARSER rather than the grammar -- or one that could not be opened --
    # produced no signal, scored zero, and sorted in with the healthy modules
    # at the clean end of the ranking. "I could not read this" is the one
    # finding a survey must never lose.
    "unreadable": (6, "file could not be read",
                   "the file may not be Python, or not meant to be read here"),
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

# Whole identifiers inside a collected string, so a name is matched as a
# name and never as a fragment of a longer one. `[^\W\d]` is a word
# character that is not a digit, which is what a Python identifier may
# begin with -- and it reads Unicode, because identifiers are Unicode
# (PEP 3131). An ASCII-only class splits `café` into `caf` and loses the
# export it was meant to find.
_IDENTIFIERS = re.compile(r"[^\W\d]\w*")


# An empty body is evidence of nothing. A `pass` is how Python spells "this
# block is deliberately empty", and `raise NotImplementedError` is how it
# spells "a subclass supplies this" -- both are finished code in the shape the
# language gives them.
#
# Measured, by opening findings against the source on two corpora of real
# Python: of 100 judged `stub_pass` findings, one was work somebody had
# started and left; of 100 judged `not_implemented` findings, eight. At weight
# 4 and 5 those two signals were ranking the frontier.
#
# What separates the true positives from the rest is not the body -- it is
# that somebody SAID SO. Keeping only the findings whose own body carries a
# marker leaves 1 stdlib and 2 dist-packages `stub_pass`, and 0 and 3
# `not_implemented`; every one of the six is a place an inheritor should go
# and finish something (tkinter's `file_dialog`, setuptools' function marked
# for removal, python-debian's two unimplemented extractors, apport's
# `package_name_glob`).
#
# Two instruments were measured against each other for `not_implemented` and
# both were rejected, because a false finding dismissed in five seconds
# teaches the reader to dismiss the rest:
#
#   an abc.ABC / ABCMeta exemption   removes 0 of 93 stdlib and 3 of 188
#                                    dist findings -- almost no real abstract
#                                    base in either corpus inherits abc.ABC
#   a "subclasses must override"     removes 68 of 93 and 114 of 188, and 0
#   prose exemption                  of the 25 stdlib survivors sampled was
#                                    genuine: it moves volume, not precision
_UNFINISHED_MARKER = re.compile(
    r"\b(TODO|FIXME)\b|\bnot\s+\(?yet\)?\s+implemented\b"
    r"|\bnot\s+implemented\s+yet\b", re.IGNORECASE)


def _says_unfinished(module, definition):
    """True when the definition's OWN BODY carries an unfinished marker.

    The signature is not part of the body: a function called `todo` is not a
    marked stub, and a marker in the line ABOVE a `def` belongs to the `todo`
    signal, which reports it at its own weight. Counting it here as well would
    let one comment rank a module as two findings.

    The first line is kept from its first colon onwards, because a one-line
    def puts the whole body after that colon -- `def hook(row): pass  # TODO`.
    """
    lines = (getattr(module, "source", "") or "").split("\n")
    first = lines[definition.lineno - 1] if definition.lineno <= len(lines) else ""
    _head, colon, rest = first.partition(":")
    body = (rest if colon else "") + "\n" + "\n".join(
        lines[definition.lineno:definition.end_lineno])
    return bool(_UNFINISHED_MARKER.search(body))


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
    for target, alias, lineno, level, _imported in module.imports:
        if alias == "*":
            continue
        # A `__future__` directive is a compiler flag, not a name: nothing in
        # the file can ever "use" it, whichever of the nine it is. The
        # exemption used to read the IMPORTED NAME, and so let through
        # `annotations` alone -- the only directive whose name is also a
        # plausible identifier -- while `print_function`, `unicode_literals`
        # and the rest were reported. 106 of them on the larger corpus. The
        # module is what identifies the statement, so that is what it reads.
        if target == "__future__" and not level:
            continue
        head = alias.split(".")[0]
        if head in module.names_used:
            continue
        # Named in a string that names symbols: an `__all__` entry, or a
        # forward-reference annotation. The scan collects only those two
        # positions, and the name must be a WHOLE identifier inside one --
        # `ratio_helper` is not a use of `io`, and `Position` is not a use
        # of `os`. A substring test hid a real dead import in pygments.
        if any(head in _IDENTIFIERS.findall(s) for s in module.strings):
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
                # A redefinition that hands the call up with super() still
                # lands on the base, so it does not count as replacing it.
                up = any(name == f"super.{d.name}"
                         and d.lineno <= line <= d.end_lineno
                         for name, line in module.calls)
                mine = methods.setdefault((module.dotted, d.parent), set())
                if not up:
                    mine.add(d.name)
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


def _every_method_is_empty(definition, module):
    """True when NO method of this class has a body: it is an interface.

    The same thing `_DECLARING_BASES` says about Protocol, said from the
    evidence instead of from a base class name. A class in which every method
    is empty is a shape somebody is declaring, whatever it inherits from --
    and that is how most of them are written: 19 of 166 stdlib and 48 of 345
    dist-packages `stub_pass` findings sit in one.

    Two methods at least, because "sibling" means there is another one. A lone
    empty method in a class is not an interface; it is the only evidence there
    is, and it stays a finding.
    """
    if definition.kind != "method" or module is None:
        return False
    siblings = [d for d in module.definitions
                if d.kind == "method" and d.parent == definition.parent
                and not d.nested]
    return len(siblings) >= 2 and all(
        d.body_kind in ("pass", "ellipsis", "raise") for d in siblings)


def declares_a_shape(definition, module=None, replaced=()):
    """True when an empty body is the definition's whole job."""
    if any(_tail(x) in _DECLARING_DECORATORS for x in definition.decorators):
        return True
    if definition.kind != "method":
        return False
    return (any(_tail(b) in _DECLARING_BASES for b in definition.bases)
            or _every_method_is_empty(definition, module)
            or (getattr(module, "dotted", None), definition.parent,
                definition.name) in replaced)


# What the `no_docstring` signal needs before it says anything at all.
#
# It is not an abandonment signal: 0 true positives in 128 findings opened
# against the source, and 0 in all 18 survivors of the tightest gate proposed
# for it. It carries weight 0 for that reason and cannot rank a module. What
# it can still do is point at the one public function a module forgot, which
# is worth saying only where the module documents the others -- a module that
# documents nothing is not saying anything about any single definition in it.
_DOCSTRING_MIN_BODY = 10          # lines; nobody wants a docstring on four
_DOCUMENTS_ITS_OTHERS = 0.8       # of its public top-level definitions


def _documented_share(module):
    """Share of public top-level definitions that carry a docstring."""
    public = [d for d in module.definitions
              if d.parent is None and not d.nested
              and not d.name.startswith("_")]
    if not public:
        return 0.0
    return sum(1 for d in public if d.docstring) / len(public)


def analyse_module(module, replaced=()):
    """Every abandonment signal in one module, with line numbers."""
    found = defaultdict(list)

    if module.error:
        if module.error.startswith("syntax error"):
            found["syntax_error"].append((module.error, 0))
        else:
            # Anything else the reader could not be given: a file that
            # defeated the parser, or one that would not open. It used to
            # fall through here silently and score 0, which put an unreadable
            # file in among the healthy ones.
            found["unreadable"].append((module.error, 0))
        return dict(found)

    for _tag, text, lineno in module.todos:
        found["todo"].append((text, lineno))    # the comment begins with its tag

    documented_share = _documented_share(module)
    for d in module.definitions:
        if d.kind == "class":
            continue
        declaring = any(_tail(x) in _DECLARING_DECORATORS for x in d.decorators)
        if d.body_kind != "code" and declares_a_shape(d, module, replaced):
            pass                          # a declaration, not a gap
        elif d.body_kind == "pass" and _says_unfinished(module, d):
            found["stub_pass"].append((d.qualname, d.lineno))
        elif d.body_kind == "ellipsis":
            found["stub_ellipsis"].append((d.qualname, d.lineno))
        elif d.body_kind == "raise" and _says_unfinished(module, d):
            found["not_implemented"].append((d.qualname, d.lineno))
        # A closure is not public: a helper defined inside a function body
        # cannot be imported, called or subclassed from outside it, so no
        # reader is ever left without its docstring. The guard excluded
        # methods but not nested definitions, and 210 of 942 stdlib findings
        # -- 558 of 3,524 on the larger corpus -- were local helpers.
        # An @overload signature is a declaration and the implementation
        # under it is what carries the documentation; 34 of the larger
        # corpus's findings were overloads.
        if (not d.docstring and not d.name.startswith("_")
                and d.kind != "method" and not d.nested and not declaring
                and d.end_lineno - d.lineno >= _DOCSTRING_MIN_BODY
                and documented_share >= _DOCUMENTS_ITS_OTHERS):
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
