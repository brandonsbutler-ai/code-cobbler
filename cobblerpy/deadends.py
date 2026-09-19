"""Places execution can reach where nothing happens.

A dead-end is the most informative thing on the map, because it marks the exact
boundary where somebody stopped: the caller exists, the callee does not. Read
together, the two say what was being reached for.

Each one carries three things, and deliberately not a fourth:

    POSITION   what calls it, and where it sits
    DIRECTION  what it was shaped to do, taken from its own name and from what
               its FINISHED siblings in the same module already do
    EVIDENCE   the lines, both sides

There is no motive. "Execution reaches here and stops" is a fact; why somebody
stopped is not recoverable and guessing at it would poison the rest.

The honest limit, stated wherever a dead-end is shown: an empty body is often
correct. Abstract base classes, Protocol definitions, exception classes and
deliberate no-op hooks are all legitimately empty, and a plugin interface is
SUPPOSED to raise NotImplementedError.
"""

from collections import defaultdict

from .abandonment import overridden_methods

_STUB_KINDS = {"pass": "body is only `pass`",
               "ellipsis": "body is only `...`",
               "raise": "raises NotImplementedError"}

# Names whose emptiness is idiomatic rather than unfinished.
_EXPECTED_EMPTY = ("Error", "Exception", "Warning", "Base", "Abstract",
                   "Protocol", "Interface", "Mixin", "Meta")

# Base classes that make an empty METHOD body a declaration rather than a gap.
# Checking only the definition's own name missed every one of these, because
# the name being checked was the method's -- `get`, `collect`, `run` -- while
# the thing that makes it idiomatic is the class it sits in. On one real
# project that put six Protocol methods above every actual stub, at the top of
# a list whose whole purpose is to say where to start reading.
_DECLARATIVE_BASES = ("Protocol", "ABC", "ABCMeta", "Interface",
                      "TypedDict", "NamedTuple", "Generic")


def _is_test_module(key):
    """True for a module whose job is testing.

    Measured, not assumed: across four real codebases, 45 of the 49 dead-ends
    reported were in test modules. They are fakes, doubles and no-op handlers
    written on purpose -- a reader inheriting a codebase should never be told
    to start reading at one, and with them in the list they were most of it.
    """
    parts = str(key).lower().replace("-", "_").split(".")
    return any(p == "tests" or p == "test" or p.startswith("test_")
               or p.endswith("_test") or p == "conftest" for p in parts)


def _is_expected_empty(definition, module, overridden=()):
    """True when this definition is empty for a normal reason."""
    if definition.kind == "class":
        return True                       # an empty class body is idiomatic
    if (definition.parent, definition.name) in overridden:
        return True                       # every call dispatches past it
    if any(definition.name.endswith(s) for s in _EXPECTED_EMPTY):
        return True
    # A method of a Protocol, an ABC or a TypedDict is declaring a shape, not
    # leaving a gap. The base names are taken as written, so an alias or a
    # generic subscript (Protocol[T] resolves to Protocol) still matches, and
    # an unrecognised base is treated as a real class -- erring towards
    # reporting, because a missed stub is recoverable and a flooded list is not
    # read at all.
    # A class with nothing to initialise writes `def __init__(self): pass`.
    # Nine of the survivors were exactly that, and none of them was a gap.
    if definition.name == "__init__" and definition.body_kind == "pass":
        return True
    for base in getattr(definition, "bases", ()):
        head = str(base).split("[", 1)[0].rsplit(".", 1)[-1]
        if head in _DECLARATIVE_BASES:
            return True
    for decorator in definition.decorators:
        if any(k in decorator for k in ("abstract", "abc.", "overload",
                                        "singledispatch", "property")):
            return True
    if definition.parent:
        # A method on a class that looks like an interface or a base.
        for other in module.definitions:
            if other.name == definition.parent and other.kind == "class":
                if any(other.name.endswith(s) for s in _EXPECTED_EMPTY):
                    return True
    return False


def _siblings(module, definition):
    """Finished definitions alongside this one -- what it was heading towards."""
    out = []
    for other in module.definitions:
        if other is definition or other.kind == "class":
            continue
        if other.parent != definition.parent:
            continue
        if other.body_kind == "code":
            out.append(other)
    return out


def _direction(module, definition):
    """A sentence on what this was shaped to do. No motive, only shape."""
    parts = []
    if definition.args:
        taking = [a for a in definition.args if a not in ("self", "cls")]
        if taking:
            parts.append(f"takes {', '.join(taking[:4])}")
    finished = _siblings(module, definition)
    if finished:
        names = [d.name for d in finished[:4]]
        parts.append(f"sits beside finished {', '.join(names)}")
    if not parts:
        return None
    return "; ".join(parts)


def find(project, modules_by_key, origins=None):
    """Every dead-end in the project, most-called first."""
    origins = origins or {}

    overridden = overridden_methods(modules_by_key.values())
    stubs = {}
    for key, module in modules_by_key.items():
        if origins.get(key, {}).get("origin") == "vendored":
            continue                      # somebody else's stubs are not yours
        if _is_test_module(key):
            continue                      # a stub in a test is a test double
        for definition in module.definitions:
            if definition.body_kind not in _STUB_KINDS:
                continue
            if _is_expected_empty(definition, module, overridden):
                continue
            stubs.setdefault(definition.name, []).append((key, module, definition))

    callers = defaultdict(list)
    for key, module in modules_by_key.items():
        for name, lineno in module.calls:
            tail = name.rsplit(".", 1)[-1]
            if tail in stubs:
                callers[tail].append((key, lineno, name))

    out = []
    for name, sites in stubs.items():
        reached = callers.get(name, [])
        # A stub nothing calls is not a dead-end -- it is just unfinished, and
        # the abandonment report already has it.
        external = [c for c in reached if c[0] != sites[0][0]]
        for key, module, definition in sites:
            out.append({
                "name": name,
                "qualname": definition.qualname,
                "module": key,
                "lineno": definition.lineno,
                "kind": _STUB_KINDS[definition.body_kind],
                "callers": [{"module": c[0], "lineno": c[1], "as": c[2]}
                            for c in reached],
                "called_from_elsewhere": bool(external),
                "direction": _direction(module, definition),
                "caveat": ("an empty body is often correct -- abstract bases, "
                           "protocols and no-op hooks are legitimately empty"),
            })
    out.sort(key=lambda d: (-len(d["callers"]), d["module"], d["lineno"]))
    return out


def by_module(deadends):
    grouped = defaultdict(list)
    for d in deadends:
        grouped[d["module"]].append(d)
    return dict(grouped)
