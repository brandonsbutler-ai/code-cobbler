"""Competing attempts: the same job, started over more than once.

The situation this exists for is a developer leaving mid-stream, the next one
failing to reconstruct the reasoning, and starting again -- four times over,
each attempt abandoned at a different point and written to a different taste.

Everything else in this package looks at one module, or at one module and its
successor. Nothing said "these four files are the same job, attempted four
times", which is the most useful sentence available to a reader in that
position.

WHAT MAKES TWO FILES THE SAME JOB

Shared DEFINITION NAMES, and nothing softer. If two modules both define
`parse_claim`, `validate_claim` and `submit_claim`, they are attempts at the
same thing regardless of who wrote them or how. Generic names are excluded,
because every module defines `main` and `run`.

Style is deliberately not a signal, and not because it would be hard. The whole
premise is that each attempt was written to a different developer's preference,
so anything keyed on naming conventions, formatting or docstring habits reads
four AUTHORS as four qualities of work. Every signal here is structural.

HOW FAR ALONG AN ATTEMPT IS

The fraction of its definitions that have real bodies, adjusted by whether
anything reaches it and whether tests exercise it. Docstrings are not counted:
one developer documents everything and the next documents nothing, and scoring
that would rank the tidiest author rather than the furthest-advanced work.

The number is a proportion of what that file itself started, not a percentage
of some imagined finished feature. It answers "how much of this attempt is
filled in", which is the question that decides whether continuing beats
starting over.
"""

from collections import defaultdict

# Definition names too common to mean anything. Sharing `main` is not evidence.
_GENERIC = {
    "main", "run", "start", "stop", "setup", "teardown", "init", "__init__",
    "get", "set", "load", "save", "read", "write", "open", "close", "parse",
    "format", "handle", "process", "execute", "call", "send", "receive",
    "create", "update", "delete", "list", "find", "search", "build", "make",
    "to_dict", "as_dict", "from_dict", "serialize", "deserialize", "validate",
    "check", "test", "helper", "wrapper", "factory", "__repr__", "__str__",
    "__enter__", "__exit__", "configure", "connect", "disconnect", "reset",
}

# How many specific definition names two modules must share to be called
# attempts at one job. Two is too loose -- a pair of files can share
# `parse_claim` and `submit_claim` by both being claim-shaped without being
# the same effort. Three has held on every tree this was run against.
MIN_SHARED = 3

# Bodies that are not filled in.
_EMPTY = {"pass", "ellipsis", "raise"}


def _specific_definitions(module):
    """Top-level definition names worth comparing between modules."""
    out = set()
    for definition in module.definitions:
        if definition.parent:
            continue                      # a method's name repeats everywhere
        name = definition.name
        if name.lower() in _GENERIC or name.startswith("_"):
            continue
        if len(name) < 4:
            continue
        out.add(name)
    return out


def _is_test_module(key):
    parts = str(key).lower().replace("-", "_").split(".")
    return any(p in ("tests", "test", "conftest") or p.startswith("test_")
               or p.endswith("_test") for p in parts)


def completeness(module, reachable, tested):
    """(score, facts) for how far along one attempt is.

    `score` is 0.0-1.0 and `facts` says how it was reached, because a number
    nobody can check is a number nobody will act on.
    """
    tops = [d for d in module.definitions if not d.parent and d.kind != "class"]
    filled = [d for d in tops if d.body_kind not in _EMPTY]
    stubs = [d for d in tops if d.body_kind in _EMPTY]

    if not tops:
        body_ratio = 0.0
    else:
        body_ratio = len(filled) / len(tops)

    score = body_ratio * 0.7
    facts = [f"{len(filled)} of {len(tops)} definitions have bodies"]
    if stubs:
        facts.append("still stubbed: " + ", ".join(sorted(d.name for d in stubs)))

    if reachable:
        score += 0.2
        facts.append("reachable from an entry point")
    else:
        facts.append("nothing reaches it")

    if tested:
        score += 0.1
        facts.append(f"{tested} test{'s' if tested != 1 else ''} refer to it")
    else:
        facts.append("no tests refer to it")

    # Unfinished-work markers pull it back, but never below what the bodies
    # alone earned: a TODO in otherwise complete code is a note, not a hole.
    todos = len(getattr(module, "todos", []) or [])
    if todos:
        score = max(body_ratio * 0.7, score - min(todos * 0.03, 0.12))
        facts.append(f"{todos} TODO marker{'s' if todos != 1 else ''}")

    return round(min(score, 1.0), 3), facts


def _lineage(ranked):
    """The attempts in the order the work actually grew, with what each added.

    Ordered by what each attempt DEFINES rather than by when it was committed.
    Dates say when a file was last touched, which on a squashed or rebased
    history is one timestamp for everything; the definition sets survive that,
    and on the shape this exists for they nest -- each developer kept the core
    and reached for one more thing. Read in this order the group stops being
    four rows and becomes a sequence somebody can follow.

    Ties break on completeness, so a pair that defines exactly the same names
    still comes out in a stable, defensible order.
    """
    order = sorted(ranked, key=lambda a: (len(a["defines"]), a["score"]))
    out, seen = [], set()
    for attempt in order:
        defines = set(attempt["defines"])
        out.append({
            "module": attempt["module"],
            "relpath": attempt["relpath"],
            "percent": attempt["percent"],
            "added": sorted(defines - seen),
            "dropped": sorted(seen - defines),
            "carried": sorted(defines & seen),
        })
        seen |= defines
    return out


def _common_gaps(ranked, stub_names):
    """Definitions that EVERY attempt left unfinished.

    The most useful sentence a survey of four restarts can produce, and a list
    cannot say it: the reader has to intersect four separate "still stubbed"
    lines themselves to notice that four developers all stopped in the same
    place. Where they all stopped is where the actual problem is -- it is
    rarely the code, and a fifth attempt will stop there too.
    """
    sets = [stub_names.get(a["module"], set()) for a in ranked]
    if not sets or any(s is None for s in sets):
        return []
    common = set.intersection(*sets) if sets else set()
    return sorted(common)


def find(project, modules_by_key, origins=None):
    """Groups of modules that are competing attempts at one job.

    Returns a list of groups, each ordered with the furthest-along attempt
    first, and each carrying what that attempt has, what it lacks, and what
    the others hold that it does not.
    """
    origins = origins or {}
    defs_by_key = {}
    for key, module in modules_by_key.items():
        if origins.get(key, {}).get("origin") == "vendored":
            continue                      # somebody else's code is not a restart
        if _is_test_module(key):
            continue                      # a test is not an attempt at the job
        names = _specific_definitions(module)
        if len(names) >= MIN_SHARED:
            defs_by_key[key] = names

    # Which modules do tests mention? Attributed to the module, not the test.
    tested = defaultdict(int)
    for key, module in modules_by_key.items():
        if not _is_test_module(key):
            continue
        mentioned = {t.split(".")[0] for t in getattr(module, "names_used", set())}
        for other in defs_by_key:
            tail = other.split(".")[-1]
            if tail in mentioned:
                tested[other] += sum(
                    1 for d in module.definitions if not d.parent)

    # Pair up, then merge pairs that share a member into one group: four
    # attempts at one job are six pairs, and reporting six pairs would bury
    # the one fact worth having.
    groups = []
    keys = sorted(defs_by_key)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = defs_by_key[a] & defs_by_key[b]
            if len(shared) < MIN_SHARED:
                continue
            for group in groups:
                if a in group["modules"] or b in group["modules"]:
                    group["modules"].update((a, b))
                    break
            else:
                groups.append({"modules": {a, b}})

    reachable = set(getattr(project, "reachable", ()) or ())
    entry = {n for n, _ in project.entry_points}

    out = []
    for group in groups:
        members = sorted(group["modules"])
        if len(members) < 2:
            continue
        # What the group shares, recomputed from its final membership: names
        # defined by at least two of them. Intersecting pairwise sets as the
        # group grew emptied it -- A and B can share three names, B and C
        # another three, and the intersection of all three be nothing, which
        # printed a group with no evidence attached to it at all.
        seen = defaultdict(int)
        for key in members:
            for name in defs_by_key[key]:
                seen[name] += 1
        shared_names = sorted(n for n, count in seen.items() if count > 1)
        ranked = []
        for key in members:
            module = modules_by_key[key]
            score, facts = completeness(
                module, key in reachable or key in entry, tested.get(key, 0))
            ranked.append({
                "module": key,
                "relpath": module.relpath,
                "score": score,
                "percent": int(round(score * 100)),
                "facts": facts,
                "defines": sorted(defs_by_key[key]),
                "tests": tested.get(key, 0),
            })
        ranked.sort(key=lambda r: (-r["score"], r["module"]))
        best = ranked[0]
        stub_names = {}
        for attempt in ranked:
            module = modules_by_key[attempt["module"]]
            stub_names[attempt["module"]] = {
                d.name for d in module.definitions
                if not d.parent and d.kind != "class" and d.body_kind in _EMPTY}
        others = ranked[1:]

        elsewhere = {}
        for other in others:
            extra = set(other["defines"]) - set(best["defines"])
            if extra:
                elsewhere[other["module"]] = sorted(extra)
            if other["tests"] and not best["tests"]:
                elsewhere.setdefault(other["module"], [])

        out.append({
            "shared": shared_names,
            "attempts": ranked,
            "lineage": _lineage(ranked),
            "common_gaps": _common_gaps(ranked, stub_names),
            "resume_at": best["module"],
            "resume_relpath": best["relpath"],
            "resume_percent": best["percent"],
            "resume_facts": best["facts"],
            "lacks": [f for f in best["facts"] if f.startswith("still stubbed")],
            "elsewhere": elsewhere,
            "caveat": ("attempts are matched on shared definition names, not on "
                       "style -- two files can share names and be unrelated, and "
                       "the names are listed so you can see which"),
        })
    out.sort(key=lambda g: -len(g["attempts"]))
    return out
