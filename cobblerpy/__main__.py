r"""CLI: python3 -m cobblerpy <directory>

    cobblerpy ./inherited-project                      summary to the terminal
    cobblerpy ./inherited-project --map map.html       the full map
    cobblerpy ./inherited-project --json survey.json   everything, machine-readable
    cobblerpy ./inherited-project --frontier           just where the work stopped

Exit status is 1 only when the directory cannot be read or holds no Python.
Finding unfinished work is a result, not a failure.
"""

import argparse
import json
import os
import sys

from . import __version__, survey
from .abandonment import SIGNALS


def _bar(value, top, width=22):
    filled = 0 if not top else max(1, round(width * value / top))
    return "#" * filled + "." * (width - filled)


def _clip(text, width):
    """Shorten at a word, never through one: "src.itsda" read as a name."""
    if len(text) <= width:
        return text
    return text[:width - 4].rsplit(" ", 1)[0] + " ..."


def h_available(s):
    return bool(s.history and s.history.get("available"))


def _print_summary(s, limit=12):
    p, out = s.project, sys.stdout
    mods = p.modules
    loc = sum(m.loc for m in mods)
    errors = [m for m in mods if m.error]

    print(f"\n{s.root}")
    print(f"{len(mods)} modules, {loc:,} lines"
          + (f", {s.skipped} skipped (file limit)" if s.skipped else ""))
    _excluded = getattr(s.project, "excluded", {}) or {}
    if _excluded:
        _total = sum(_excluded.values())
        _named = ", ".join(f"{n} ({c:,})" for n, c in
                           sorted(_excluded.items(), key=lambda kv: -kv[1])[:3])
        print(f"  {_total:,} more .py not read, in directories a survey does "
              f"not walk into: {_named}")
    if not mods:
        return

    totals = s.origin_totals
    if len(totals) > 1 or "tracked" not in totals:
        print("\nWHERE THE CODE CAME FROM")
        for kind, t in totals.items():
            note = {
                "untracked": "on disk, never committed",
                "vendored": "third-party provenance",
                "tracked": "committed to this repository",
                "unknown": "no repository to ask",
            }.get(kind, "")
            print(f"  {kind:<10} {t['modules']:>4} modules  {t['lines']:>7,} lines"
                  f"   {note}")
        untracked = totals.get("untracked", {}).get("lines", 0)
        total_lines = sum(t["lines"] for t in totals.values()) or 1
        if untracked:
            print(f"  -> {100 * untracked / total_lines:.0f}% of the lines here were "
                  f"never committed: no review, no commit message, no way back.")

    print("\nWHERE IT STARTS")
    if p.entry_points:
        for name, why in p.entry_points:
            print(f"  {name:<44} {', '.join(why)}")
    else:
        print("  no entry point found -- a library, or its driver is elsewhere")

    layers = p.layers()
    if layers:
        print("\nDISTANCE FROM A START POINT")
        for depth in sorted(layers):
            label = "entry" if depth == 0 else f"depth {depth}"
            print(f"  {label:<9} {len(layers[depth]):>3}  "
                  + ", ".join(sorted(layers[depth])[:6])
                  + (" ..." if len(layers[depth]) > 6 else ""))

    depended = [(name, out_n, in_n) for name, out_n, in_n in p.fan()[:6] if in_n]
    if depended:
        print("\nMOST DEPENDED UPON")
        for name, out_n, in_n in depended:
            print(f"  {name:<44} used by {in_n}, uses {out_n}")

    if p.cycles:
        print(f"\nIMPORT CYCLES ({len(p.cycles)})")
        for cycle in p.cycles[:5]:
            print("  " + " -> ".join(cycle))

    if p.orphans:
        print(f"\nNOTHING IMPORTS THESE ({len(p.orphans)})")
        for name in p.orphans[:10]:
            print(f"  {name}")
        print("  (often where the author was working when they stopped)")

    if errors:
        print(f"\nWILL NOT PARSE ({len(errors)})")
        for m in errors[:8]:
            print(f"  {m.relpath}: {m.error}")

    hot = [r for r in s.frontier if r["score"] > 0
           and s.origins.get(r["module"], {}).get("origin") != "vendored"]
    if hot:
        top = hot[0]["score"]
        vendored = {k for k, r in s.origins.items() if r["origin"] == "vendored"}
        if vendored:
            print(f"\n  ({len(vendored)} vendored module(s) excluded from the "
                  f"ranking below -- somebody else's TODOs are not your frontier)")
        print(f"\nWHERE THE WORK STOPPED  (read in this order)")
        for r in hot[:limit]:
            kinds = ", ".join(f"{k.replace('_', ' ')} {v}"
                              for k, v in sorted(r["counts"].items(),
                                                 key=lambda kv: -kv[1])[:4])
            print(f"  {_bar(r['score'], top)} {r['score']:>5}  {r['module']}")
            print(f"  {' ' * 22} {kinds}")
        if len(hot) > limit:
            print(f"  ... and {len(hot) - limit} more")
        print("\n  totals: " + ", ".join(f"{k.replace('_', ' ')} {v}"
                                         for k, v in s.totals.items()))

    # Competing attempts first: on the codebase this was built for, "these
    # four files are the same job" is the sentence that decides whether the
    # next hour is spent reading or rewriting.
    from .attempts import (EFFORT_SCOPE, copies, copy_sentence,
                           effort_sentence, find as find_attempts, restarts,
                           stake_sentence)
    groups = find_attempts(s.project, s.modules_by_key, s.origins)
    restarted, copied = restarts(groups), copies(groups)
    if restarted:
        print("\nTHE SAME JOB, STARTED OVER  (most work at stake first)")
        if any(effort_sentence(g) for g in restarted[:3]):
            print(f"  ({EFFORT_SCOPE})")
        for group in restarted[:3]:
            print(f"  {len(group['attempts'])} attempts share "
                  f"{', '.join(group['shared'][:4])}")
            # The components, never a bare score: a reader has to be able to
            # disagree with the ranking without reading the code that made it.
            print(f"      {stake_sentence(group)}")
            # Dropped when there is nothing unwritten, rather than printed as
            # "nothing left unwritten by this measure": on a finished tree
            # that was 12 rows out of 22 carrying a sentence that separates
            # none of them.
            effort = effort_sentence(group)
            if effort:
                print(f"      {effort}")
            for attempt in group["attempts"]:
                mark = "->" if attempt["module"] == group["resume_at"] else "  "
                print(f"   {mark} {attempt['percent']:>3}%  {attempt['relpath']}")
            print(f"      resume at {group['resume_relpath']} "
                  f"({group['resume_percent']}% of what it started)")
            for fact in group["resume_facts"]:
                print(f"        {fact}")
            for module, extra in group["elsewhere"].items():
                has = ", ".join(extra) if extra else "the only tests for this job"
                print(f"        {module} has {has}")
        if len(restarted) > 3:
            print(f"  ... and {len(restarted) - 3} more groups")

    # Copies get their own heading and their own verdict. Ranked below the
    # restarts they would still have been read as restarts, and on a working
    # tree they are the majority of what this detector finds -- 13 of 22 on
    # the tree it was measured against.
    if copied:
        print(f"\nTHE SAME FILE, IN MORE THAN ONE PLACE ({len(copied)})")
        print("  (not restarts: nothing here was started over and left)")
        for group in copied[:3]:
            print(f"  {group['resume_relpath']} "
                  f"and {len(group['attempts']) - 1} other"
                  f"{'s' if len(group['attempts']) > 2 else ''}")
            print(f"      {copy_sentence(group)}")
        if len(copied) > 3:
            print(f"  ... and {len(copied) - 3} more")

    forks = []
    if h_available(s):
        from .diversion import find as find_forks
        forks = find_forks(s.project, s.modules_by_key, s.history, s.frontier)
    if forks:
        print("\nWHERE THE EFFORT WENT INSTEAD")
        print("  (a hypothesis from similarity and timing, not a fact)")
        if s.history.get("shallow"):
            print("  (timed from a shallow clone -- see below)")
        for f in forks[:5]:
            print(f"  {f['stopped']} stopped after \"{_clip(f['last_subject'], 44)}\"")
            for c in f["continued_as"][:2]:
                print(f"      -> {c['module']:<38} {_clip(c['why'], 56)}")

    h = s.history
    if h.get("available"):
        print("\nWHAT THE HISTORY SAYS")
        if h.get("shallow"):
            from .history import SHALLOW_NOTE
            print(f"  {SHALLOW_NOTE}")
        note = s.history_coverage
        if note:
            print(f"  {note}")
        print(f"  authors: {', '.join(h['authors'][:6])}")
        months = h["timeline"]
        if months:
            first, last = min(months), max(months)
            print(f"  built between {first} and {last}")
        if h["co_change"]:
            print("  files that keep changing together:")
            for a, b, n in h["co_change"][:5]:
                print(f"    {n:>3}x  {a}  +  {b}")
        if h["stalled"]:
            print(f"  started and left alone ({len(h['stalled'])}):")
            for r in h["stalled"][:5]:
                print(f"    {r['path']:<44} {r['commits']} commit(s), "
                      f"quiet {r['days_quiet']}d")
    else:
        print(f"\nNO VERSION HISTORY -- {h.get('reason', 'unavailable')}")

    print("\n  Structure above is parsed from the syntax. Reachability and")
    print("  'nothing uses this' are lower bounds: Python dispatches through")
    print("  registries, decorators and getattr, which no parser can follow.\n")


def _print_frontier(s):
    print(f"\n{s.root}\n")
    for r in s.frontier:
        # A signal that weighs nothing still has something to say. Skipping by
        # score alone meant that giving `no_docstring` weight 0 -- so it could
        # not rank a module it has no evidence about -- would have deleted it
        # from the report instead of reclassifying it. Rows with no findings
        # at all are still silent.
        if not r["score"] and not r["signals"]:
            continue
        print(f"{r['score']:>6}  {r['module']}  ({r['loc']} lines)")
        for kind, hits in sorted(r["signals"].items()):
            weight, label, innocent = SIGNALS.get(kind, (1, kind, ""))
            print(f"        {label} -- {len(hits)}")
            for text, lineno in hits[:5]:
                where = f"line {lineno}" if lineno else "module"
                print(f"          {where}: {text}")
            if len(hits) > 5:
                print(f"          ... and {len(hits) - 5} more")
            if innocent:
                print(f"          (innocent explanation: {innocent})")
        print()


def build_parser():
    """The argument parser, as a value.

    Separate from main() so a test can read the real options rather
    than scrape --help: six flags once shipped documented nowhere but
    the help text, and scraping would not have caught it either.
    """

    parser = argparse.ArgumentParser(
        prog="cobblerpy",
        description="Make sense of a Python codebase somebody else left behind.")
    parser.add_argument("directory")
    parser.add_argument("--map", metavar="PATH",
                        help="write the full HTML map (self-contained)")
    parser.add_argument("--json", metavar="PATH",
                        help="write everything as JSON")
    parser.add_argument("--mermaid", metavar="PATH",
                        help="write a Mermaid flowchart (renders on GitHub, "
                             "in VS Code and most wikis)")
    parser.add_argument("--drawio", metavar="PATH",
                        help="write a .drawio diagram, editable in diagrams.net "
                             "and exportable to Visio from there")
    parser.add_argument("--frontier", action="store_true",
                        help="print only where the work stopped, with evidence")
    parser.add_argument("--no-history", action="store_true",
                        help="skip git history (faster, or for a non-repository)")
    parser.add_argument("--max-files", type=int, default=5000, metavar="N",
                        help="stop after N .py files (default 5000). The survey is "
                             "truncated, not sampled, so raise it rather than trust a "
                             "partial map of a tree that hit the cap")
    parser.add_argument("--version", action="version",
                        version=f"cobblerpy {__version__}")
    
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not os.path.exists(args.directory):
        print(f"cobblerpy: {args.directory}: does not exist", file=sys.stderr)
        return 1
    if os.path.isfile(args.directory):
        # A file means the project it is in -- the rule `cobble` uses.
        from .launch import project_for
        args.directory = project_for(args.directory)
        print(f"cobblerpy: surveying {args.directory}, the project that file is in",
              file=sys.stderr)
    if not os.path.isdir(args.directory):
        print(f"cobblerpy: {args.directory}: not a directory", file=sys.stderr)
        return 1

    # Every output path is checked BEFORE a single file is read. A mistyped
    # --map used to surface as a FileNotFoundError traceback out of the
    # writer, with the whole survey already spent and then discarded; the
    # sentence a person needs there is which flag they mistyped and why, in
    # the same voice the shelf uses when it cannot be updated.
    from .launch import unwritable
    refused = []
    for flag, target in (("--map", args.map), ("--json", args.json),
                         ("--mermaid", args.mermaid), ("--drawio", args.drawio)):
        why = target and unwritable(target)
        if why:
            refused.append(f"cobblerpy: {flag} {target}: cannot write here "
                           f"({why})")
    if refused:
        # Every one of them, not just the first: a person fixing two mistyped
        # paths one run at a time pays for the refusal twice.
        for line in refused:
            print(line, file=sys.stderr)
        return 2

    s = survey(args.directory, with_history=not args.no_history,
               max_files=args.max_files)
    if not s.project.modules:
        print(f"cobblerpy: no Python files under {args.directory}",
              file=sys.stderr)
        return 1

    if args.frontier:
        _print_frontier(s)
    else:
        _print_summary(s)

    if args.map:
        from .report import write_map
        write_map(s.project, s.frontier, s.history, args.map,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        print(f"map -> {args.map}", file=sys.stderr)
    if args.mermaid or args.drawio:
        from .deadends import by_module as deadends_by_module, find as find_deadends
        from .export import to_drawio, to_mermaid
        from .layout import compute as compute_layout
        graph = compute_layout(s.project, {r["module"]: r for r in s.frontier})
        dead = deadends_by_module(find_deadends(s.project, s.modules_by_key,
                                                s.origins))
        if args.mermaid:
            with open(args.mermaid, "w", encoding="utf-8") as fh:
                fh.write(to_mermaid(graph, s.project, dead))
            print(f"mermaid -> {args.mermaid}", file=sys.stderr)
        if args.drawio:
            with open(args.drawio, "w", encoding="utf-8") as fh:
                fh.write(to_drawio(graph, s.project, dead))
            print(f"drawio -> {args.drawio}", file=sys.stderr)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(s.as_dict(), fh, indent=2, default=str)
        print(f"json -> {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
