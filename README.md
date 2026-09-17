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

### The shape this was built against

A software shop writing Python for healthcare customers, with a turnover rate high enough that
developers routinely leave mid-stream. Each departure leaves a half-finished effort behind, in
whatever style that developer preferred. The next developer picks it up, cannot reconstruct the
reasoning, loses confidence that continuing is the right call -- and starts again.

**Four fresh restarts of the same half-completed work, stacked on top of each other.** Not four
abandoned features: four attempts at one feature, each abandoned at a different point, each
written to a different taste.

That is the case this tool is aimed at, and it sets the bar:

- **show the previous train of thought**, so a stranger can follow a decision they were not
  present for
- **find the restarts** -- say which files are competing attempts at the same job, and which
  attempt got furthest
- **recommend where to resume**, with the evidence attached, so the developer can judge the
  recommendation rather than take it on faith
- **give them grounds for confidence**, because a rewrite is usually not a technical decision.
  It is what happens when nobody can tell whether continuing is safe

The cost of getting this wrong is measured in rewrites. A rewrite of work that was 70% done is
the most expensive thing on this list, and it happens because the 70% is invisible.

## What it tells you

**Where it starts** -- entry points, with the reason each one qualifies, because the evidence
varies in strength. A `__main__` guard is near-certain; a suggestive filename is not.

**What depends on what** -- the import graph, how far each module sits from a start point, which
modules everything leans on, and any import cycles.

**The same job, started over** -- modules that define enough of the same things to be attempts
at one piece of work rather than separate pieces, ranked by how much of what each one started is
filled in, with the gaps named:

```
4 attempts share normalise_codes, parse_claim, submit_claim, validate_claim
 ->  75%  app/claims_v3.py
     42%  app/claims_ingest.py
     35%  app/intake_new.py
     33%  app/intake.py
    resume at app/claims_v3.py (75% of what it started)
      5 of 6 definitions have bodies
      still stubbed: submit_claim
      reachable from an entry point
      app.claims_ingest has audit_claim
      app.intake has the only tests for this job
```

Matching is on shared definition names and **never on style**. Each attempt was written to a
different developer's taste, so anything keyed on naming conventions, formatting or docstring
habits would rank the tidiest author rather than the furthest-advanced work. Documentation is
not scored at all, and there is a test that documenting a weaker attempt does not make it win.

The percentage is a proportion of what that file itself started, not of an imagined finished
feature. It answers "how much of this attempt is filled in", which is the question that decides
whether continuing beats starting over.

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

## The desktop application

```bash
pip install cobblerpy[gui]
cobblerpy-gui
```

Drop a project folder on the window. It counts what is there, surveys it, and
puts the findings beside the controls in the order somebody inheriting a
codebase needs them: **whether the work has already been attempted more than
once, and which attempt got furthest**, then where the work stopped. The full
interactive map is a button away.

Built to sit beside an editor, because that is where you already are.

**The window is the only part of this project with a dependency.**
`pip install cobblerpy` installs a library and a command line that import
nothing outside the standard library, and the verifier asserts both halves of
that separately on every run — so a stray import in the library cannot hide
behind the window's exemption.

## Options

Every option the command accepts. `--help` prints the same list.

| Option | What it does |
|---|---|
| `--map PATH` | write the full HTML map (self-contained) |
| `--json PATH` | write everything as JSON |
| `--mermaid PATH` | write a Mermaid flowchart (renders on GitHub, in VS Code and most wikis) |
| `--drawio PATH` | write a .drawio diagram, editable in diagrams.net and exportable to Visio from there |
| `--frontier` | print only where the work stopped, with evidence |
| `--no-history` | skip git history (faster, or for a non-repository) |
| `--max-files N` | stop after N .py files (default 5000). The survey is truncated, not sampled, so raise it rather than trust a partial map of a tree that hit the cap |
| `--version` | show program's version number and exit |

## Tests

```bash
python3 -m unittest discover -s tests -v     # 113 tests, no pytest required
python3 verify_e2e.py                        # 94 end-to-end claim checks
```

`verify_e2e.py` checks the PRODUCT rather than its units. It builds a codebase whose every
property is known by construction -- a planted TODO, a planted stub, a module nothing imports, a
subtree with its own LICENSE, a file that will not parse -- drives the real CLI, and asserts the
documented behaviour against that ground truth. It prints PASS or FAIL per claim and exits
non-zero on any failure, because a verifier that cannot fail is decoration.

It caught something on its first run, though not in the tool: the "clean module" fixture was not
imported by anything, so cobblerpy correctly reported it as an orphan and the check expecting
silence failed. **The tool was right and the fixture was wrong** -- which is the failure mode a
self-written verifier is most prone to, and the reason this one asserts against properties the
fixture DEFINES rather than against whatever the code happens to produce.

Fixtures are real directories of real Python, because every bug found so far came from running
against real trees rather than from unit-level reasoning: relative imports that resolved to the
wrong module, git history that stopped at a rename, and a comment classifier that flagged section
dividers.

The suite is mutation-checked. Breaking the comment classifier, the relative-import resolution,
the score's diminishing returns, the untracked-file detection and the cluster verdict each makes
the specific test that guards it go red -- and one mutation had to be rewritten after the first
attempt was caught by an IndentationError rather than by the test, which proves nothing.

## The map

`--map out.html` writes one self-contained page. Left to right is distance from a start point.

Click any module for its source, its signals, and what it connects to. The source shown is only
the regions that matter -- the lines a signal points at, with context, plus every definition
header -- and gaps between regions are marked rather than closed up, because a snippet that looks
continuous but is not would mislead anyone reading line numbers. Hovering a module dims everything
it has nothing to do with.

A doubled bar instead of an arrowhead marks a **dead-end**: a call that reaches a body with
nothing in it. That is the boundary where somebody stopped -- the caller exists, the callee does
not -- and the panel shows both sides at once, because neither half explains the situation alone.
Each one reports where it stands and what it was shaped to do, never why anybody stopped. Motive
is not recoverable and guessing at it would poison the rest.

Colour states what it is derived from, and grey is deliberately not a shade of red: "no static
path reaches this" is an inference, while the others are read from the syntax.

## Status

Working: scanning, the import and call graph, entry points and flow, the abandonment analysis,
dead-end detection, origin classification, cluster separation, git history with rename-following,
and the interactive HTML map.

`--mermaid` writes a flowchart that renders natively on GitHub and in VS Code, so the map can
live in the repository it describes. `--drawio` writes a diagram diagrams.net opens and edits,
and exports to Visio from there.

`--map` also reports **forks**: a module that went quiet while a similar one carried on. This is
a hypothesis, labelled as one, with its evidence attached -- and it took three attempts to stop
it producing confident nonsense. [DESIGN_NOTES.md](DESIGN_NOTES.md) records all three, including
the limitation that neither available codebase contains a known fork, so it has been tuned
against absence rather than validated against a positive.

Everything described above is verified end to end. What is not done: the attachment question --
where set-aside code *would* have fitted -- which [DESIGN_NOTES.md](DESIGN_NOTES.md) records as
probably the boundary where deterministic analysis ends and narration begins.

## License

MIT -- see [LICENSE](LICENSE).
