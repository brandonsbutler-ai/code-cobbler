"""Where each file came from -- which decides how to read everything else.

The abandonment analysis ranks modules by signs of unfinished work. That
ranking is only meaningful for code the project's own author wrote. Point it at
a vendored library and it faithfully reports someone else's TODOs, sending the
reader off to study 11,000 lines of a dependency.

Worse, a file can be present and not tracked at all. Running this tool on a
real project turned up two whole packages, 49 files and roughly 11,000 lines,
that git had never heard of -- while the history layer cheerfully reported
"history available" because the OTHER 60 files had plenty. A summary that
averages over that is lying by omission.

So every module gets an origin, and the origins are kept apart:

    tracked     committed, with history -- the project's own work
    untracked   on disk, never committed -- the most fragile code in the tree
    vendored    signs of third-party provenance
    unknown     no repository to ask

Untracked is not a lesser finding than abandoned; it is often a larger one.
Nothing reviewed it, nothing can restore it, and no commit message explains it.
"""

import os
import subprocess

# Directory names that conventionally hold somebody else's code.
_VENDOR_DIRS = {"vendor", "vendored", "_vendor", "third_party", "thirdparty",
                "3rdparty", "extern", "external", "deps", "contrib",
                "site-packages", "node_modules"}

# Files that mark a subtree as a distributable package in its own right.
_PACKAGE_MARKERS = ("setup.py", "setup.cfg", "pyproject.toml", "PKG-INFO",
                    "LICENSE", "LICENSE.txt", "LICENCE", "COPYING")


# git reads the TARGET repo's own .git/config, and several config keys name a
# command git will execute -- core.fsmonitor is one, and it fires on `ls-files`.
# A hostile repo could then run code merely by being surveyed, which is exactly
# what this tool promises never happens. A `-c` value on the command line wins
# over the repo's config, so this neutralises the vector for every call.
# (history.py carries the same constant; both run against untrusted repos.)
_GIT = ("git", "-c", "core.fsmonitor=")


def _git(root, *args, timeout=60):
    try:
        r = subprocess.run(_GIT + ("-C", root) + args, capture_output=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None


def tracked_paths(root):
    """Every path git knows about, relative to `root`. None when not a repo."""
    out = _git(root, "ls-files")
    if out is None:
        return None
    return {line.strip() for line in out.splitlines() if line.strip()}


def _vendor_evidence(root, relpath):
    """Reasons to believe this file came from outside, most specific first."""
    parts = relpath.replace("\\", "/").split("/")
    reasons = []
    for part in parts[:-1]:
        if part.lower() in _VENDOR_DIRS:
            reasons.append(f"sits under a vendor directory ({part})")
            break
    # A package marker inside the subtree, but ABOVE the project root, means
    # this directory is distributable on its own.
    for depth in range(len(parts) - 1, 0, -1):
        directory = os.path.join(root, *parts[:depth])
        try:
            present = set(os.listdir(directory))
        except OSError:
            continue
        found = [m for m in _PACKAGE_MARKERS if m in present]
        if found:
            reasons.append(f"{'/'.join(parts[:depth])} carries {', '.join(found)}"
                           f" -- a package in its own right")
            break
    return reasons


def classify(root, modules, history=None):
    """Origin for every module. Returns {module_key: {...}}."""
    root = os.path.abspath(root)
    tracked = tracked_paths(root)
    history_files = (history or {}).get("files", {}) if history else {}

    out = {}
    for module in modules:
        key = module.dotted or module.relpath
        rel = module.relpath.replace("\\", "/")
        vendor = _vendor_evidence(root, rel)

        if tracked is None:
            kind, why = "unknown", "no git repository to ask"
        elif rel not in tracked:
            kind = "untracked"
            why = ("present on disk but never committed -- nothing reviewed it, "
                   "no commit message explains it, and it cannot be restored")
        elif vendor:
            kind, why = "vendored", vendor[0]
        else:
            kind, why = "tracked", "committed to this repository"

        if kind == "tracked" and vendor:
            kind, why = "vendored", vendor[0]

        out[key] = {
            "origin": kind,
            "why": why,
            "evidence": vendor,
            "commits": history_files.get(rel, {}).get("commits", 0),
        }
    return out


def summarise(origins, modules_by_key=None):
    """Counts and line totals per origin, for a summary that cannot mislead."""
    modules_by_key = modules_by_key or {}
    totals = {}
    for key, rec in origins.items():
        bucket = totals.setdefault(rec["origin"], {"modules": 0, "lines": 0})
        bucket["modules"] += 1
        module = modules_by_key.get(key)
        bucket["lines"] += getattr(module, "loc", 0) if module else 0
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]["lines"]))


def coverage_note(origins, history):
    """A sentence about how much of the tree the history actually covers.

    Reported because "history available: True" over a tree where half the files
    are untracked is the kind of true-but-useless statement this tool exists to
    avoid.
    """
    total = len(origins)
    if not total:
        return None
    tracked = sum(1 for r in origins.values() if r["origin"] == "tracked")
    vendored = sum(1 for r in origins.values() if r["origin"] == "vendored")
    untracked = sum(1 for r in origins.values() if r["origin"] == "untracked")
    if not history or not history.get("available"):
        return (f"No version history. {total} modules, none with commit history "
                f"to draw on.")
    if untracked:
        return (f"Version history covers {tracked + vendored} of {total} modules. "
                f"{untracked} are untracked -- present on disk, never committed -- "
                f"so nothing below says anything about them.")
    return f"Version history covers all {total} modules."
