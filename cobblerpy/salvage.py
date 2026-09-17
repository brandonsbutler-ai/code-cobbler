"""What is in the code nothing reaches, and whether any of it is worth taking.

A survey that ends at "111 modules are unreachable" has done the easy half.
The question the person who inherited this actually has is narrower and
harder: *is there anything in there I should not throw away* -- a capability
that exists in no other file, a branch that was built against the real system
and abandoned before it landed, the direction the product was going in before
it turned.

Everything here is evidence, never a verdict on worth. The tool reports how
much was spent, how long it was worked on, what it is still wired to, and what
it holds that nothing else holds. Whether that adds up to a gem is a judgement
about a business, and a tool that says "valuable" is a tool that gets switched
off the first time it is wrong.

One inference IS made, and it is stated as such: where an unreachable module
and a live one define enough of the same specific names to be doing the same
job, the dates say which came first. Older means the live one replaced it --
that is the original direction, and it is the only claim here that needs the
history to be trustworthy. Newer means it was a later attempt that stopped.

Dates come from git where the file is tracked, and from a date in the
FILENAME where it is not -- `_combined_e2e_prepfirst_20260619.py` is dated by
whoever named it, and a tree full of untracked dated scratch files is exactly
the tree this question gets asked about. The record says which source was
used, because one of those is a commit and the other is a habit.
"""

import os
import re

from .attempts import MIN_SHARED, _specific_definitions

# A date somebody put in a filename: 20260619, 2026-06-19, 2026_06_19.
_STAMP = re.compile(r"(20\d{2})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])")

# Ordered most specific first. The first verdict whose evidence is present
# wins, so a module that both predates its replacement and holds unique work
# is reported as the former -- it is the stronger statement about the same
# file, and the rest of the evidence travels with it either way.
ORIGINAL_DIRECTION = "original direction"
LATER_ATTEMPT = "a later attempt that stopped"
UNIQUE_AND_WIRED = "built on the live code, and holds work found nowhere else"
WIRED = "built on the live code"
UNIQUE = "holds work found nowhere else"
NEVER_COMMITTED = "never committed"
ISOLATED = "isolated"

_CAVEAT = {
    ORIGINAL_DIRECTION:
        "an inference from dates and shared definition names, not a verdict: "
        "two files can define the same names without being the same job",
    LATER_ATTEMPT:
        "an inference from dates and shared definition names, not a verdict",
    UNIQUE_AND_WIRED:
        "\"nowhere else\" means nowhere else in what was surveyed -- an "
        "excluded directory or a skipped file could hold it too",
    WIRED:
        "it imports live modules, which means it was written against this "
        "codebase; it does not mean anything still calls it",
    UNIQUE:
        "\"nowhere else\" means nowhere else in what was surveyed",
    NEVER_COMMITTED:
        "the project has a history and this file is not in it, so there is no "
        "record of how long it was worked on -- it may also not be yours",
    ISOLATED:
        "nothing imports it, it imports nothing internal, and it defines "
        "nothing unique -- which is what scratch work looks like",
}


def _stamped(relpath):
    """The date in a filename, or None. Not the date the file was written."""
    found = _STAMP.search(os.path.basename(relpath))
    return "-".join(found.groups()) if found else None


def _earliest(relpath, files):
    """(date, where it came from). None when nothing can be proved."""
    record = files.get(relpath) or {}
    tracked = record.get("first_seen")
    stamped = _stamped(relpath)
    if tracked and stamped:
        return (min(tracked, stamped),
                "git" if tracked <= stamped else "the filename")
    if tracked:
        return tracked, "git"
    if stamped:
        return stamped, "the filename"
    return None, None


def find(project, modules_by_key, history=None, limit=None):
    """One record per unreachable module, ranked by the work in it.

    Ranked by lines rather than by how interesting the verdict sounds: the
    reader is deciding where to spend an afternoon, and that is bounded by how
    much there is to read.
    """
    files = (history or {}).get("files") or {}
    # "Never committed" is only worth saying when there is a history to be
    # absent from. Surveyed without git -- or on a directory that is not a
    # repository -- it was the verdict on every single module, which is true
    # of the whole project and tells the reader nothing about any file in it.
    has_history = bool((history or {}).get("available") and files)
    orphans = [k for k in project.orphans if k in modules_by_key]
    live = set(modules_by_key) - set(orphans)

    live_defs = {}
    for key in live:
        for name in _specific_definitions(modules_by_key[key]):
            live_defs.setdefault(name, set()).add(key)

    records = []
    for key in orphans:
        module = modules_by_key[key]
        mine = _specific_definitions(module)
        unique = sorted(mine - set(live_defs))
        uses_live = sorted(set(project.imports.get(key, ())) & live)

        shared = {}
        for name in mine:
            for other in live_defs.get(name, ()):
                shared.setdefault(other, []).append(name)
        counterpart = None
        if shared:
            best, names = max(shared.items(), key=lambda kv: len(kv[1]))
            if len(names) >= MIN_SHARED:
                mine_when, mine_src = _earliest(module.relpath, files)
                their = modules_by_key[best]
                their_when, their_src = _earliest(their.relpath, files)
                counterpart = {
                    "module": best,
                    "shared": sorted(names),
                    "mine": mine_when, "mine_from": mine_src,
                    "theirs": their_when, "theirs_from": their_src,
                    "older": bool(mine_when and their_when
                                  and mine_when < their_when),
                    "datable": bool(mine_when and their_when),
                }

        record = files.get(module.relpath) or {}
        verdict = _verdict(counterpart, bool(uses_live), bool(unique),
                           committed=bool(record), has_history=has_history)
        records.append({
            "module": key,
            "relpath": module.relpath,
            "loc": module.loc,
            "verdict": verdict,
            "caveat": _CAVEAT[verdict],
            "unique": unique[:12],
            "unique_count": len(unique),
            "uses_live": uses_live[:8],
            "uses_live_count": len(uses_live),
            "commits": record.get("commits") or 0,
            "first_seen": record.get("first_seen"),
            "last_touched": record.get("last_touched"),
            "counterpart": counterpart,
        })

    records.sort(key=lambda r: (-r["loc"], r["module"]))
    return records[:limit] if limit else records


def _verdict(counterpart, wired, unique, committed, has_history):
    if counterpart and counterpart["datable"]:
        return ORIGINAL_DIRECTION if counterpart["older"] else LATER_ATTEMPT
    if has_history and not committed:
        return NEVER_COMMITTED
    if wired and unique:
        return UNIQUE_AND_WIRED
    if wired:
        return WIRED
    if unique:
        return UNIQUE
    return ISOLATED


def summarise(records):
    """Counts per verdict, plus the lines sitting behind each one."""
    out = {}
    for record in records:
        row = out.setdefault(record["verdict"], {"modules": 0, "lines": 0})
        row["modules"] += 1
        row["lines"] += record["loc"]
    return out
