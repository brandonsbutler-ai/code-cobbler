"""What the repository remembers about how the code was built.

Static analysis reads the code as it stands. Version history reads the code as
it HAPPENED, which is the closest surviving record of a departed developer's
train of thought: the order files appeared, which ones changed together, what
the author said they were doing, and -- most usefully -- where work stopped.

Most code-analysis tools ignore this entirely because they analyse a snapshot.
A snapshot cannot tell you that four files were created in one afternoon and
never touched again, which is exactly the shape of an abandoned effort.

Everything here degrades quietly: no git, a shallow clone, or a directory that
is not a repository all produce an empty result rather than an error, because
the rest of the tool is still useful without it.
"""

import datetime
import os
import subprocess
from collections import defaultdict

_SEP = "\x1f"          # unit separator: safe inside commit subjects


def _git(root, *args, timeout=60):
    """Run git, returning stdout or None when git or the repo is unavailable."""
    try:
        r = subprocess.run(("git", "-C", root) + args, capture_output=True,
                           timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def is_repo(root):
    return _git(root, "rev-parse", "--git-dir") is not None


def rename_map(root, max_commits=4000):
    """Map every historical path to the name the file goes by today.

    Without this, history stops at the last rename. A file that moved -- a
    package renamed, a module promoted out of a folder, a typo fixed in a
    filename -- appears to have been created on the day it moved, and every
    commit before that is invisible. In an inherited codebase files move
    constantly, so the history layer would be quietly wrong about exactly the
    files most worth understanding.

    git reports renames as `R100\told\tnew`; walking the log oldest-first and
    chaining those gives each old path its modern name.
    """
    out = _git(root, "log", f"--max-count={max_commits}", "--name-status",
               "-M", "--reverse", "--pretty=format:", "--no-merges")
    if not out:
        return {}
    aliases = {}
    for line in out.splitlines():
        if not line.startswith("R"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        old, new = parts[1], parts[2]
        # Anything already pointing at `old` must now point at `new`.
        for key, value in list(aliases.items()):
            if value == old:
                aliases[key] = new
        aliases[old] = new
    return aliases


def _canonical(path, aliases):
    """Follow the rename chain to the file's present-day path."""
    seen = set()
    while path in aliases and path not in seen:
        seen.add(path)
        path = aliases[path]
    return path


def _iso(epoch):
    return datetime.datetime.fromtimestamp(
        int(epoch), datetime.timezone.utc).strftime("%Y-%m-%d")


def file_history(root, relpaths, max_commits=4000):
    """Per-file first commit, last commit, number of commits, and authors.

    One `git log` over the whole repo rather than one per file: a thousand
    subprocess calls is slower than reading a thousand lines.
    """
    out = _git(root, "log", f"--max-count={max_commits}", "--name-only",
               f"--pretty=format:%H{_SEP}%at{_SEP}%an{_SEP}%s", "--no-merges")
    if not out:
        return {}

    wanted = set(relpaths)
    aliases = rename_map(root)
    records = defaultdict(lambda: {"commits": 0, "first": None, "last": None,
                                   "authors": set(), "subjects": []})
    commit = None
    for line in out.splitlines():
        if _SEP in line:
            sha, when, author, subject = (line.split(_SEP) + ["", "", ""])[:4]
            commit = (sha, int(when) if when.isdigit() else 0, author, subject)
            continue
        path = _canonical(line.strip(), aliases)
        if not path or commit is None or path not in wanted:
            continue
        rec = records[path]
        rec["commits"] += 1
        rec["authors"].add(commit[2])
        # git log walks newest first, so the first sighting is the last change.
        if rec["last"] is None:
            rec["last"] = commit[1]
            rec["last_subject"] = commit[3]
        rec["first"] = commit[1]
        rec["first_subject"] = commit[3]
        if len(rec["subjects"]) < 8:
            rec["subjects"].append(commit[3])

    result = {}
    for path, rec in records.items():
        result[path] = {
            "commits": rec["commits"],
            "first_seen": _iso(rec["first"]) if rec["first"] else None,
            "last_touched": _iso(rec["last"]) if rec["last"] else None,
            "first_subject": rec.get("first_subject", ""),
            "last_subject": rec.get("last_subject", ""),
            "authors": sorted(rec["authors"]),
            "subjects": rec["subjects"],
            "_last_epoch": rec["last"] or 0,
            "_first_epoch": rec["first"] or 0,
        }
    return result


def co_change(root, relpaths, max_commits=2000, min_pairs=2):
    """Files that keep changing in the same commit.

    Co-change is a statement about the code's real seams that imports cannot
    make: two modules edited together a dozen times belong to one idea,
    whatever the directory layout says.
    """
    out = _git(root, "log", f"--max-count={max_commits}", "--name-only",
               f"--pretty=format:%H{_SEP}", "--no-merges")
    if not out:
        return []
    wanted = set(relpaths)
    aliases = rename_map(root)
    pairs = defaultdict(int)
    current = []
    for line in out.splitlines() + [f"end{_SEP}"]:
        if line.endswith(_SEP):
            if 1 < len(current) <= 25:        # a 300-file commit says nothing
                for i, a in enumerate(current):
                    for b in current[i + 1:]:
                        pairs[tuple(sorted((a, b)))] += 1
            current = []
            continue
        path = _canonical(line.strip(), aliases)
        if path in wanted and path not in current:
            current.append(path)
    return sorted(((a, b, n) for (a, b), n in pairs.items() if n >= min_pairs),
                  key=lambda t: -t[2])


def timeline(history):
    """Files grouped by the month they first appeared, oldest first.

    Read top to bottom this is the order the codebase was built, which is
    usually the order it should be read.
    """
    grouped = defaultdict(list)
    for path, rec in history.items():
        if rec["first_seen"]:
            grouped[rec["first_seen"][:7]].append(path)
    return {month: sorted(paths) for month, paths in sorted(grouped.items())}


def stalled(history, quiet_days=60):
    """Files created and then left alone -- the shape of abandoned work.

    A file with one or two commits that has not been touched since is a
    different thing from a stable file that was finished long ago, and the
    distinction is commit COUNT: finished code gets revisited; abandoned code
    does not get started again.
    """
    if not history:
        return []
    newest = max(r["_last_epoch"] for r in history.values())
    cutoff = quiet_days * 86400
    out = []
    for path, rec in history.items():
        age = newest - rec["_last_epoch"]
        if age >= cutoff and rec["commits"] <= 2:
            out.append({
                "path": path,
                "commits": rec["commits"],
                "first_seen": rec["first_seen"],
                "last_touched": rec["last_touched"],
                "days_quiet": int(age / 86400),
                "last_subject": rec["last_subject"],
            })
    return sorted(out, key=lambda r: -r["days_quiet"])


def summary(root, relpaths):
    """Everything the history layer can offer, or an empty shell without git."""
    if not is_repo(root):
        return {"available": False,
                "reason": "not a git repository, or git is not installed"}
    history = file_history(root, relpaths)
    if not history:
        return {"available": False,
                "reason": "no commit history covering these files"}
    return {
        "available": True,
        "files": history,
        "timeline": timeline(history),
        "co_change": co_change(root, relpaths)[:40],
        "stalled": stalled(history),
        "authors": sorted({a for r in history.values() for a in r["authors"]}),
    }
