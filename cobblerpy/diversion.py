"""Where the effort went when it stopped going here.

Work rarely stops; it forks. Somebody pursues an idea, hits something that does
not suit, and starts a similar effort by another route. The abandoned side is
visible in the code, the new side is visible in the commits, and the pairing is
the most useful thing the history can say about a dead patch.

A first attempt at this failed instructively. Asking "what changed after this
file went quiet" returns whichever files always change -- the test module, the
CLI, the central service -- for every quiet file, regardless. That is
popularity, not diversion.

The discriminator (Brandon, 2026-09-16) is SIMILARITY:

    "diversion is a fork ... if the function is what they have persued, and
     suddenly they move to a similar but alternate effort in the PR's, that
     gives us a pretty good idea this is where they decided to go with a new
     path"

So a candidate must be doing the SAME KIND OF WORK by another route, and
timing alone proves nothing. Four similarity signals are computed, and a fork
is only reported when at least two agree:

    vocabulary   the identifiers overlap beyond generic verbs
    package      siblings in the same package
    co-change    they used to be edited together
    effects      they touch the same outside systems

Every finding is a HYPOTHESIS with its evidence attached. Two modules can look
alike and have nothing to do with each other, and the reader can dismiss a
wrong one in seconds if the reasons are visible.
"""

import re
from collections import Counter, defaultdict

# Verbs and nouns so common in Python that sharing them means nothing.
_STOPWORDS = {
    "get", "set", "run", "init", "main", "new", "make", "create", "build",
    "add", "remove", "delete", "update", "list", "load", "save", "read",
    "write", "open", "close", "start", "stop", "check", "test", "handle",
    "process", "parse", "format", "to", "from", "is", "has", "do", "call",
    "value", "name", "data", "item", "result", "self", "cls", "args", "kwargs",
    "file", "path", "line", "text", "str", "dict", "type", "key", "id",
}

MIN_SIGNALS = 2          # how many similarity signals must agree
QUIET_COMMITS = 3        # a file untouched for this many commits has gone quiet
MIN_ABANDON = 4          # the quiet side must show signs of being UNFINISHED

# At least one signal must be SPECIFIC to these two modules. "Both in the same
# package" and "both touch the network" are true of half a codebase, and two
# generic signals agreeing is still generic -- deploy.config paired with
# deploy.01-create-vms on nothing but those two.
_SPECIFIC = {"vocabulary", "co-change"}

# The discriminator the second attempt lacked: FINISHED CODE IS ALSO QUIET.
# Requiring similarity and timing alone produced plausible output that meant
# nothing -- a settled module beside its own dependency, two deploy scripts
# that resemble each other. A fork needs the abandoned side to be abandoned,
# so the quiet module must carry real unfinished-work signals before any
# candidate is considered.


def _is_test(key, module):
    """Test modules follow effort; they never receive it."""
    tail = key.rsplit(".", 1)[-1]
    return (tail.startswith("test_") or tail.endswith("_test")
            or "test" in module.relpath.replace("\\", "/").split("/")[:-1]
            or module.relpath.replace("\\", "/").startswith("tests/"))


def _vocab(module):
    """Distinctive words in a module's own identifiers."""
    words = Counter()
    for definition in module.definitions:
        for word in re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1_\2",
                                                 definition.name).lower()):
            if len(word) > 2 and word not in _STOPWORDS:
                words[word] += 1
    return words


def _similarity(a_key, a_mod, b_key, b_mod, co_change_pairs):
    """Signals that two modules are doing the same kind of work."""
    signals = []

    va, vb = _vocab(a_mod), _vocab(b_mod)
    shared = set(va) & set(vb)
    if shared:
        # Weight by how distinctive the shared words are within this project.
        strength = sum(min(va[w], vb[w]) for w in shared)
        if strength >= 2:
            signals.append(("vocabulary",
                            f"shares {', '.join(sorted(shared)[:4])}"))

    a_pkg = a_key.rsplit(".", 1)[0] if "." in a_key else ""
    b_pkg = b_key.rsplit(".", 1)[0] if "." in b_key else ""
    if a_pkg and a_pkg == b_pkg:
        signals.append(("package", f"both in {a_pkg}"))

    pair = tuple(sorted((a_mod.relpath, b_mod.relpath)))
    if pair in co_change_pairs:
        signals.append(("co-change",
                        f"edited together {co_change_pairs[pair]} times before"))

    shared_effects = set(a_mod.effects) & set(b_mod.effects)
    if shared_effects:
        signals.append(("effects",
                        f"both touch {', '.join(sorted(shared_effects))}"))

    return signals


def find(project, modules_by_key, history, frontier=None):
    """Forks: a module that went quiet, and the similar one that took over.

    Returns [] when there is no usable history, rather than guessing from the
    code alone -- without commits there is no 'afterwards'.
    """
    if not history or not history.get("available"):
        return []
    files = history.get("files", {})
    if len(files) < 4:
        return []

    co_change = {tuple(sorted((a, b))): n for a, b, n in history.get("co_change", [])}
    by_relpath = {m.relpath: (k, m) for k, m in modules_by_key.items()}
    scores = {r["module"]: r.get("score", 0) for r in (frontier or [])}
    entry_names = {n for n, _ in project.entry_points}

    # Order files by when they were last touched; the tail is what went quiet.
    ordered = sorted(files.items(), key=lambda kv: kv[1]["_last_epoch"])
    newest = ordered[-1][1]["_last_epoch"]

    out = []
    for relpath, record in ordered:
        if relpath not in by_relpath:
            continue
        quiet_for = sum(1 for _r, other in files.items()
                        if other["_last_epoch"] > record["_last_epoch"])
        if quiet_for < QUIET_COMMITS or record["_last_epoch"] == newest:
            continue
        a_key, a_mod = by_relpath[relpath]

        # Quiet AND unfinished. Without this the tool reports settled modules
        # as abandoned, which is a confident answer to a question nobody asked.
        unfinished = (scores.get(a_key, 0) >= MIN_ABANDON
                      or a_key in project.orphans
                      or (a_key not in project.reachable
                          and a_key not in entry_names))
        if not unfinished:
            continue

        candidates = []
        for other_rel, other_record in files.items():
            if other_rel == relpath or other_rel not in by_relpath:
                continue
            # The fork must have carried on AFTER this one stopped.
            if other_record["_last_epoch"] <= record["_last_epoch"]:
                continue
            b_key, b_mod = by_relpath[other_rel]
            if _is_test(b_key, b_mod):
                # A test module shares vocabulary with everything it tests and
                # changes alongside everything. It is never where the effort
                # went; it is the thing that follows the effort.
                continue
            signals = _similarity(a_key, a_mod, b_key, b_mod, co_change)
            kinds = {s[0] for s in signals}
            if len(signals) >= MIN_SIGNALS and (kinds & _SPECIFIC):
                candidates.append({
                    "module": b_key,
                    "relpath": other_rel,
                    "signals": [s[0] for s in signals],
                    "why": "; ".join(s[1] for s in signals),
                    "commits_after": other_record["commits"],
                    "last_touched": other_record["last_touched"],
                })

        if not candidates:
            continue
        candidates.sort(key=lambda c: (-len(c["signals"]), -c["commits_after"]))
        out.append({
            "stopped": a_key,
            "relpath": relpath,
            "last_touched": record["last_touched"],
            "last_subject": record["last_subject"],
            "commits": record["commits"],
            "continued_as": candidates[:3],
            "caveat": ("a hypothesis from similarity and timing -- two modules "
                       "can resemble each other and be unrelated"),
        })
    out.sort(key=lambda d: -len(d["continued_as"]))
    return out
