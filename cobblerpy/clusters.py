"""Which of this directory's code is actually the project.

A working tree is rarely just one program. It collects libraries somebody
copied in to try, experiments that were never wired up, and standalone
utilities that happen to live alongside. Ranking all of it together produces a
frontier report full of somebody else's unfinished business.

Connected components of the import graph separate them cleanly, and three
questions decide which components belong:

    does anything start here?      an entry point means it runs
    does the project reach it?     an inbound import means it is used
    does the project mention it?   named in a README, Dockerfile, requirements
                                   or config means somebody meant it to be here

A component that answers no to all three is present but unclaimed. On a real
project this told apart two copied-in libraries, 10,000 lines between them,
from the 6,800-line application -- without knowing anything about either.

The verdict is a RECOMMENDATION with its reasons attached, never an automatic
deletion. Getting this wrong in the confident direction would hide the very
code somebody needs.
"""

import os
from collections import defaultdict, deque

# Files that say what a project considers part of itself.
_MANIFEST_NAMES = (".md", ".txt", ".toml", ".cfg", ".ini", ".yml", ".yaml",
                   ".json", ".sh", ".bat", ".ps1")
_MANIFEST_EXACT = ("Dockerfile", "Makefile", "Procfile", "requirements.txt")
_MANIFEST_SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  "dist", "build", ".mypy_cache", ".pytest_cache"}


def _project_prose(root, max_bytes=4_000_000):
    """Everything the project says about itself, lowercased."""
    chunks, total = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _MANIFEST_SKIP]
        for name in filenames:
            if not (name.endswith(_MANIFEST_NAMES) or name in _MANIFEST_EXACT):
                continue
            try:
                with open(os.path.join(dirpath, name), encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read(200_000)
            except OSError:
                continue
            chunks.append(text.lower())
            total += len(text)
            if total > max_bytes:
                return "".join(chunks)
    return "".join(chunks)


def components(project):
    """Connected components of the import graph, treated as undirected.

    Undirected on purpose: a library the project imports and a library that
    imports the project are equally part of the same conversation.
    """
    adjacency = defaultdict(set)
    for source, targets in project.imports.items():
        for target in targets:
            adjacency[source].add(target)
            adjacency[target].add(source)

    seen, found = set(), []
    for name in sorted(project.by_dotted):
        if name in seen:
            continue
        queue, group = deque([name]), set()
        while queue:
            current = queue.popleft()
            if current in group:
                continue
            group.add(current)
            seen.add(current)
            queue.extend(adjacency[current] - group)
        found.append(group)
    return found


def analyse(project, root, modules_by_key=None, origins=None):
    """Classify each component as part of the project, or not.

    Returns a list of dicts sorted by size, each carrying the evidence behind
    its verdict so a reader can overrule it in seconds.
    """
    modules_by_key = modules_by_key or {}
    origins = origins or {}
    prose = _project_prose(root)
    entry_names = {n for n, _ in project.entry_points}

    out = []
    for group in components(project):
        loc = sum(getattr(modules_by_key.get(m), "loc", 0) for m in group)
        entries = sorted(group & entry_names)
        roots = {m.split(".")[0] for m in group}
        mentioned = sorted(r for r in roots if r.lower() in prose)
        untracked = sum(1 for m in group
                        if origins.get(m, {}).get("origin") == "untracked")

        reasons = []
        if entries:
            reasons.append(f"{len(entries)} entry point(s): {', '.join(entries[:3])}")
        if mentioned:
            reasons.append(f"named in the project's own files: {', '.join(mentioned[:3])}")
        belongs = bool(entries or mentioned)

        if not belongs:
            why = ("nothing starts here, nothing outside imports it, and the "
                   "project's own README, config and scripts never mention it")
        else:
            why = "; ".join(reasons)

        out.append({
            "modules": sorted(group),
            "label": max(group, key=lambda m: getattr(modules_by_key.get(m), "loc", 0)),
            "size": len(group),
            "loc": loc,
            "entry_points": entries,
            "mentioned_as": mentioned,
            "untracked": untracked,
            "belongs": belongs,
            "why": why,
        })
    out.sort(key=lambda c: -c["loc"])
    return out


def split(clusters):
    """(kept, set_aside) module-name sets, from the recommendations."""
    kept, aside = set(), set()
    for cluster in clusters:
        (kept if cluster["belongs"] else aside).update(cluster["modules"])
    return kept, aside
