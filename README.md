# cobblerpy

**Make sense of a Python codebase somebody else left behind.**

Point it at a directory and it reports the structure, the execution flow, where the previous
developer stopped, and what the version history says they were doing.

```bash
cobblerpy ./inherited-project                    # summary to the terminal
cobblerpy ./inherited-project --map map.html     # the full interactive map
cobblerpy ./inherited-project --frontier         # just where the work stopped
cobblerpy ./inherited-project --json survey.json # everything, machine-readable
```

No third-party dependencies. It never imports or executes the code it reads, which matters:
a codebase you are trying to understand is usually one you do not yet trust.

## The problem it exists for

A developer left halfway through. The comments are thin, the docstrings are missing, and the
commit messages say "wip". You have to work out not just what the code does, but what they were
*trying* to do, and where they got to.

Structure is the easy half. The hard half is the frontier.

## What it tells you

**Where it starts** -- entry points, with the reason each one qualifies, because the evidence
varies in strength. A `__main__` guard is near-certain; a suggestive filename is not.

**What depends on what** -- the import graph, how far each module sits from a start point, which
modules everything leans on, and any import cycles.

**Where the work stopped** -- modules ranked by weighted signals of unfinished work, so reading
top-down gives you an order to look at the code in:

| Signal | Why it matters |
|---|---|
| unused imports | the strongest tell: someone pulled in a library, started wiring it up, stopped |
| `pass` / `...` bodies, `NotImplementedError` | a stub that was never filled in |
| `TODO` / `FIXME` / `XXX` | what the author knew they had to come back to |
| commented-out code | a decision that was never finished |
| `except: pass` | an error somebody deferred |
| orphan modules | nothing imports them and nothing starts from them |
| files that will not parse | left mid-edit, or written for another Python |

**What the history says** -- if it is a git repository: when each file first appeared, which files
keep changing together (the code's real seams, which the directory layout often hides), and which
were created and then left alone. **Renames are followed**, so history does not stop at the last
time a file moved -- which in an inherited codebase is usually several times.

## Proven and inferred are kept apart

Structure is parsed from the syntax and is close to exact.

Reachability and "nothing uses this" are **lower bounds**. Python reaches code through dispatch
tables, `getattr`, decorators, plugin registries and framework callbacks, none of which a parser
can follow. So "not reached" means *no static path was found*, never *dead*. Every such finding
carries the caveat that might explain it, and the map colours them differently.

A tool that blurs those two makes its confident half untrustworthy.

## The detection rules were measured, not guessed

Deciding whether a comment is disabled code or ordinary prose is the sort of heuristic that
quietly floods a report with noise. "It parses as Python" is far too weak a test -- an
astonishing amount of prose is syntactically valid.

The rule was built by writing the near-misses first, proving *which clause* rejects each one, and
then measuring against **48,482 real comments** from this machine's Python. That measurement
deleted two clauses outright: a minimum-length check and a divider-stripping check (added
specifically to stop `# --- DOCX`, which parses as `-(-(-DOCX))`) each changed **zero** verdicts,
because the statement rule was already handling them. They were post-hoc suppression hiding which
mechanism was load-bearing.

It also caught a false-positive class nobody would invent: **worked examples inside explanatory
comment blocks**, which CPython's own `typing.py` is full of. The flag rate on real code is 0.30%.

## Status

Working: scanning, the import and call graph, entry points and flow, the abandonment analysis,
git history with rename-following, and the HTML map.

Not yet done: a test suite, an end-to-end verifier of the kind its sibling project carries, and
the graphical layout described in [DESIGN_NOTES.md](DESIGN_NOTES.md) -- an interactive SVG with
click-through to source, plus `.drawio` and Mermaid export.

## License

MIT -- see [LICENSE](LICENSE).
