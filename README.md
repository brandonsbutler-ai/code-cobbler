# cobblerpy

**Pick up where someone else left off in a Python codebase.**

**CodeCobbler** is the product. **cobblerpy** is the command, the package and the import name.
**CobblerPy** is the desktop window. Same thing.

Point it at a directory and it shows where the previous developer's train of thought was going,
finds the dead ends they already discovered and abandoned, and marks where they stopped, with the
file and the line. Structure, execution flow and what the version history says they were doing
come with it.

```bash
cobblerpy ./inherited-project                    # summary to the terminal
cobblerpy ./inherited-project --map map.html     # the full interactive map
cobblerpy ./inherited-project --frontier         # just where the work stopped
cobblerpy ./inherited-project --json survey.json # everything, machine-readable
```

No third-party dependencies. It never imports or executes the code it reads, which matters:
a codebase you are trying to understand is usually one you do not yet trust.

That includes running it from inside the project, where a file named `ast.py` or
`json.py` would otherwise be imported in place of Python's own. Before it imports
anything else, the tool takes the current directory off the import path whenever it is
the program being run: `cobblerpy`, `cobble` and `cobblerpy-gui` from pip, the desktop
launchers, the `cobble` the launcher installer writes, and `python3 -m cobblerpy`. The
single executable never has the current directory on its path.

Two things happen before any line of this tool runs, and no package can stop them:

- `python3 -m` puts the current directory first, and Python imports some of its own
  modules from there to find the package. On 3.12 those are `importlib`, `types`,
  `warnings`, `threading`, `functools`, `collections`, `keyword`, `operator` and
  `reprlib` -- measured on 3.12.3 on 2026-09-22, and `runpy` is not among them. The
  exact list moves between releases; that it is not empty is the part that matters.
- An empty element in your own `PYTHONPATH` (`PYTHONPATH=:` or `/x:`) also means the
  current directory, and Python imports `encodings`, `sitecustomize.py` and
  `usercustomize.py` from it at startup (an `encodings.py` there runs, then the
  interpreter fails); pip's `cobblerpy` wrapper then imports `re`.

So inside a project you do not trust, use `cobblerpy .` or `cobble` with no empty
`PYTHONPATH` element, or `python3 -P -m cobblerpy .`, or run it
from outside and name the folder.

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
at one piece of work rather than separate pieces, ranked by the **work at stake** in each group,
with the gaps named:

```
4 attempts share normalise_codes, parse_claim, submit_claim, validate_claim
    ~573 lines at stake -- 1,224 in the furthest attempt, 78% complete, 40% of its definitions exist elsewhere
    ~204 lines left to finish it -- 1 definition with no body yet
 ->  78%  app/claims_v3.py
     42%  app/claims_ingest.py
     35%  app/intake_new.py
     33%  app/intake.py
    resume at app/claims_v3.py (78% of what it started)
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

**A copy is not a restart.** Most of what this finds on a working tree is one file kept in more
than one place -- a backup folder, a re-dated variant, a generated twin -- and telling a reader
"somebody restarted this four times" about a backup folder is a false sentence, not a low-ranked
one. So a pair of files that define exactly the same things is reported under its own heading,
with its own verdict and the one file you can check it against:

```
THE SAME FILE, IN MORE THAN ONE PLACE (15)
  (not restarts: nothing here was started over and left)
  app/claims.py and 3 others
      this is a copy, not a restart: every definition in it is also in app/claims_source.py
  app/intake.py and 1 other
      this is a copy, not a restart: the same file name in two places, one of them under backups/ -- the other is backups/2026-08/app/intake.py
```

The verdict is about a **pair of files**, and a copy is taken out of its group rather than used
to condemn it. Back up the attempt the tool told you to resume and you get both findings: the
backup is reported as a copy, and the restart is still reported -- with its remaining attempts,
their percentages and its resume line, re-ranked on what is left. A group is called a copy end
to end only when every file in it is the same file as every other.

Run against a 979-module tree, the matcher found 22 groups: 12 copies end to end, 7 with no
duplicate in them at all, and 3 holding both, which report as 25 groups -- 9 restarts and 13
copies before the split, 10 and 15 after. The cut is not a round number: across those 22 groups
the share of the named file's definitions that a sibling also holds came out 12, 13, 22, 29, 50,
53, 60, 60, 80, and then 100 thirteen times, so the cut sits where the gap is. Two files under
the same name in a `backups/` or `archive/` directory count as a copy as well, because a backup
taken before the last edit is one definition behind the live file; the directory name **on its
own** is not evidence, and an archived script that shares only three names with its sibling is
still reported as a restart.

**Work at stake** is what you get back for resuming a group: the lines in its furthest-along
attempt, taken down by how much of that file is written, and taken down again by the share of
its definitions a sibling attempt already holds. All three numbers are printed beside it, so the
order can be argued with without reading the code that produced it. How many times the job was
restarted only breaks ties -- a restart count says how often somebody gave up, never how much
code is sitting there. Where two attempts are equally far along, the bigger file is the one
named: completeness is a proportion of what each file itself started, so a 2,767-line copy and
the 3,527-line file it came from score the same, and "resume here" pointed at the copy.

The lines left to finish ride along beside it and are deliberately **not** part of the order, so
a small group that is nearly done still reads as a quick win from wherever it sorts. That line
counts **only definitions with no body yet**, and says nothing about whether what is written
works. It used to be the lines the completeness score said were missing, which also folds in
whether anything imports the file and whether a test names it -- so a file with a body on every
definition that nothing imported was reported as having 281 lines left to write. Where there is
nothing unwritten the line is dropped rather than softened; on the 979-module tree every one of
the 67 files in those 22 groups has a body on every definition, so it prints nowhere.

**Where the work stopped** -- modules ranked by weighted signals of unfinished work, so reading
top-down gives you an order to look at the code in:

| Signal | Why it matters |
|---|---|
| unused imports | sometimes a library somebody started wiring up and left. A deliberate re-export looks identical to a parser, so read it as a place to look, not a verdict |
| `pass` / `NotImplementedError` bodies **that say so in the body** | a stub somebody marked and left. An empty body on its own is not one: measured against the source on two real trees, 1 `pass` in 100 and 8 `NotImplementedError` in 100 were abandoned work and the rest were declarations -- including the abstract base class that says "subclasses must implement this" in prose rather than in a decorator. A TODO/FIXME inside the body is what tells them apart. Deliberate no-ops are exempt as well: `@overload`, `Protocol`, `@abstractmethod`, a class whose every method is empty, and a base method every subclass replaces |
| `...` bodies | the same minus the marker, and the one stub signal still reported as written: it is a declaration in every typed library, so read it as a place to look |
| `TODO` / `FIXME` / `XXX` | what the author knew they had to come back to |
| commented-out code | a decision that was never finished |
| `except: pass` | an error being swallowed. Often deliberate -- an optional import, a best-effort cleanup -- so read it as a place to look |
| orphan modules | nothing imports them and nothing starts from them |
| files that will not parse, or will not open | left mid-edit, written for another Python, or unreadable here. A file the tool could not read is never sorted in with the healthy ones |
| an undocumented public function in a module that documents its others | **weight 0**: it cannot rank anything. Measured at 0 true positives in 128 findings opened against the source, so it is a readability note rather than evidence of unfinished work. Gated to the one public function a module forgot, which is what makes it worth printing at all |
| a docstring promising a return the code never makes | the docstring may simply be stale |
| unreached modules | no import path reaches them from any entry point -- an inference, not a verdict |

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

## How the commented-out-code rule was measured

Five detectors have now been measured this way -- findings opened one at a time and judged
against the source on CPython's standard library and this machine's `dist-packages`. Four of
them were wrong most of the time and were narrowed or demoted; `except: pass` was measured,
found to be wrong in all 20 of its narrowest defensible subset, and left as it is pending a
decision to drop it. The rest are still rules of thumb, and on mature code most of what they
flag turns out to be deliberate -- which is why every row in the table above carries the
caveat that might explain it.

Deciding whether a comment is disabled code or ordinary prose is the sort of heuristic that
quietly floods a report with noise. "It parses as Python" is far too weak a test -- an
astonishing amount of prose is syntactically valid.

The rule was built by writing the near-misses first, proving *which clause* rejects each one, and
then measuring against the **48,482 comments** of CPython's own standard library. That measurement
deleted two clauses outright: a minimum-length check and a divider-stripping check (added
specifically to stop `# --- DOCX`, which parses as `-(-(-DOCX))`) each changed **zero** verdicts,
because the statement rule was already handling them. They were post-hoc suppression hiding which
mechanism was load-bearing.

It also caught a false-positive class nobody would invent: **worked examples inside explanatory
comment blocks**, which CPython's own `typing.py` is full of. It flags 0.30% of the comments in
that corpus. On third-party code the rate is roughly half again as high.

## Running it, on Linux and on Windows

Python 3.11 or newer, and nothing else. The library and the command line import
only the standard library, so there is no install step you can get wrong and
nothing to pin.

### The short way: `cobble`

```bash
cobble                      # the current directory
cobble ../some-project      # any folder
cobble path/to/module.py    # a file means the project it is in
cobble --shelf              # the maps you have already made
cobble --help               # also --version
```

Surveys, writes the map **beside** the project rather than inside it, and opens
it. A file's project is the nearest folder above it holding `.git`,
`pyproject.toml`, `setup.py` or `setup.cfg`; failing that, the folder holding
its top-level package. No flags, no output path, nothing to find afterwards. It is also wired to a
desktop entry and a file-manager right-click on this machine, so a folder can be
dropped on an icon instead -- and because a launcher started that way has no
terminal, every outcome including every failure comes back as a desktop
notification rather than vanishing.

One folder at a time: several are **refused by name and count**, not quietly
reduced to the first one.

Install the front doors with `sh packaging/install-launcher.sh` -- the `cobble`
command, an application entry, a desktop icon and a file-manager right-click. It
adds no dependency and is safe to re-run. (If GNOME shows the desktop icon
greyed out, right-click it once and choose Allow Launching.)

It will not overwrite a file at one of those paths that it did not write --
`pip install --user` puts its own `cobble` in `~/.local/bin` -- and says which;
remove it, or run it again with `--force` to replace it. An install made before
the installer marked its files needs `--force` once. `--force` and `--uninstall`
together are refused: uninstall only ever removes files the installer wrote. Any
other argument is refused with the usage line.

It asks where the shelf of maps should live: `~/.local/share/codecobbler`, the
default, or any writable shared volume it finds under `/media`, `/run/media` or
`/mnt`. With `CODECOBBLER_HOME` already set it uses that and does not ask; run
with no terminal (piped, CI) it takes the default and prints which it chose.

**The shelf.** Clicking the icon with nothing selected opens an index of the maps
you have already made, newest first, each row the project name, its module count
and when it was mapped. A desktop launch inherits an arbitrary working directory,
so surveying "here" because somebody clicked an icon would be a guess; the shelf
is the honest answer to a bare click. Rows are dated by the map file rather than
by the moment they were listed, or newest-first sorts on a fiction.

On a machine that dual-boots, choose the volume both systems can see and one
bookmark serves either side. `CODECOBBLER_HOME` is the only thing that moves the
shelf: no drive letter is built into the package, on Windows or anywhere else,
and a run that writes a shelf outside the default says where it put it.
`packaging/CodeCobbler.bat`
is the Windows equivalent of the launcher: double-click for the shelf, or drag a
folder onto it.

### Straight from the source tree

No install at all. From the directory holding `cobblerpy/`:

| | |
|---|---|
| Linux, macOS | `python3 -m cobblerpy ./inherited-project --map map.html` |
| Windows (PowerShell or cmd) | `py -m cobblerpy .\inherited-project --map map.html` |

`py` is the launcher that ships with python.org installs; `python` works too
where it is on PATH. Everything after `-m cobblerpy` is identical on both.

### Installed

The name on PyPI is `cobblerpy`, and 0.1.3 -- this source -- is the release
going there. Every route below installs that same 0.1.3, and none of them
brings anything else with it: the package has no runtime dependencies, so there
is nothing for pip to resolve.

**From PyPI**, the ordinary case:

```bash
python3 -m pip install cobblerpy            # Linux, macOS
py -m pip install cobblerpy                 # Windows
python3 -m pip install "cobblerpy[gui]"     # and the desktop window
```

**With pipx**, when you want the commands on PATH without them sharing an
environment with anything else you have installed -- which is usually what you
want for a tool you point at other people's projects:

```bash
pipx install cobblerpy
pipx install "cobblerpy[gui]"
```

Both of those need 0.1.3 to be on the index. Until it is, and on any machine
that cannot reach an index at all, the routes below install the same 0.1.3 from
source.

**From a clone**, when the source is already out and you want the commit you
have just read:

```bash
python3 -m pip install .            # Linux, macOS
py -m pip install .                 # Windows
pipx install .                      # or in an environment of its own
```

**Straight from the repository**, when you would rather not keep a clone:

```bash
python3 -m pip install "cobblerpy @ git+https://github.com/brandonsbutler-ai/code-cobbler"
pipx install "cobblerpy @ git+https://github.com/brandonsbutler-ai/code-cobbler"
```

Any of them puts `cobblerpy`, `cobble` and `cobblerpy-gui` on PATH, so the
command is just `cobblerpy <folder>`. Add `[gui]` -- `pip install ".[gui]"` from
a clone -- for the window, the one part that pulls a dependency (PySide6).

**Nothing installed at all.** [Straight from the source
tree](#straight-from-the-source-tree) needs no install step, no index and no
network: the package runs where it sits.

### Verify what you downloaded

The source distribution carries more than the package: the tests, the claim
checker they drive, and the packaging scripts that some of those tests
exercise. So the claims this README makes can be re-run rather than believed,
by whoever downloaded it, on their own machine:

```bash
python3 -m pip download --no-binary :all: --no-deps cobblerpy==0.1.3
tar xzf cobblerpy-0.1.3.tar.gz
cd cobblerpy-0.1.3
python3 -m unittest discover -s tests
python3 verify_e2e.py
```

`verify_e2e.py` is the file described under [Tests](#tests). It reads the README
sitting beside it, drives the real command line against a codebase whose
properties are known by construction, prints PASS or FAIL for each claim and
exits non-zero if any of them is not true of the code it shipped with. Both
commands were run from an unpacked 0.1.3 archive on 2026-09-21 and both came
back green, so nothing either one needs is left out of it.

### Paths and quoting, which differ

- A Windows path with spaces needs quotes: `py -m cobblerpy "C:\Work\Some Project"`.
- A trailing backslash before a closing quote escapes it. Write
  `"C:\Work\Project"`, not `"C:\Work\Project\"`.
- The map is written wherever `--map` says. `--map map.html` lands in the
  current directory on both; open it by double-clicking, or with
  `xdg-open map.html` on Linux and `start map.html` on Windows.

### The map opens anywhere

One HTML file with nothing outside it -- no CDN, no fonts, no network. It
opens from a `file://` URL, survives being e-mailed, and renders the same in
Chrome, Edge and Firefox on either platform. The chart is inline SVG and draws
without JavaScript. The trace, the detail panel and hover-dimming need it;
with JavaScript off you get a readable static map and no interaction.

Geared for a 1920-wide window. Narrower than about 1180 and the detail panel
moves below the chart rather than beside it.

### A single executable, for a machine with no Python

Download `CodeCobbler-linux-x86_64.tar.gz` from the
[Releases page](https://github.com/brandonsbutler-ai/code-cobbler/releases)
(Linux x86-64 only), then:

```bash
tar xzf CodeCobbler-linux-x86_64.tar.gz
./CodeCobbler                       # the shelf of maps you have made
./CodeCobbler /path/to/project      # map that folder and open the map
./CodeCobbler --help                # also --version
```

The single executable is the **launcher**: a folder in, a map out. The flags in
[Options](#options) belong to the `cobblerpy` command line, which you get from `pip install`
or from `build_standalone.py --cli`. This binary refuses them.

The released binary is v0.1.2. Take it: v0.1.0 and v0.1.1 could run the code
they were reading, and their `--help` and `--version` mapped the current folder
instead of answering.

This source is 0.1.3, which is ahead of that release. To build it yourself:

```bash
python3 -m pip install pyinstaller
python3 packaging/build_standalone.py --launcher   # dist/CodeCobbler, ~7 MB
```

One file. Double-click it for the shelf, or drop a project folder on it. The
machine it runs on needs **no Python, no pip and nothing installed** -- measured
on the Linux build: `ldd` lists only libc, libz, libpthread, libdl and the
loader, and it was verified by copying the binary elsewhere, clearing the
environment with `env -i`, and mapping a tree with it.

Built on glibc 2.39 it requires only **GLIBC_2.14** (2011), so it runs on
anything reasonably current. It is architecture- and OS-specific, though:
PyInstaller does not cross-compile, so the Windows `.exe` has to be built on
Windows and the Linux binary on Linux. Ship it as a `.tar.gz` -- the execute bit
survives, which saves the recipient a `chmod +x`.

```bash
python3 -m pip install pyinstaller
python3 packaging/build_standalone.py
```

Built on the machine it is for -- PyInstaller does not cross-compile, so a
Windows .exe has to be built on Windows. It produces, in `dist/`:

| | Linux | Windows |
|---|---|---|
| the launcher, the one to hand somebody | `dist/CodeCobbler` | `dist\CodeCobbler.exe` |
| command line -- the only one with `--json`, `--map`, `--frontier` | `dist/cobblerpy` | `dist\cobblerpy.exe` |
| window | `dist/CobblerPy` | `dist\CobblerPy.exe` |

A bare run builds all three, so it needs PySide6 installed for the window. Pass
`--launcher`, `--cli` or `--app` to build just one.

On Linux it also writes `dist/cobblerpy.desktop`; copy it to
`~/.local/share/applications/` for a menu entry. On Windows the `.exe` runs
from wherever you put it -- there is no installer and nothing is written to
the registry.

The window build needs PySide6 present at build time, because PyInstaller
works out what to bundle by reading the imports. `packaging/app_entry.py`
imports it at the top level for exactly that reason, and that import is marked
`# noqa: F401` because it is deliberate.

### Uninstalling

Each way in leaves different things behind, and none of them touches the
projects it read except by writing a map beside them.

| Installed with | What it left | How to remove it |
|---|---|---|
| `pip install` | the package and the `cobblerpy`, `cobble` and `cobblerpy-gui` commands; in the clone, `build/` and `cobblerpy.egg-info/` | `pip uninstall cobblerpy`, then delete those two folders from the clone. With `[gui]`, also `pip uninstall PySide6-Essentials shiboken6` |
| `packaging/install-launcher.sh` | `~/.local/bin/cobble`, `~/.local/share/applications/codecobbler.desktop`, `~/.local/share/icons/codecobbler.svg`, `~/.local/share/nautilus/scripts/Map with CodeCobbler`, and `CodeCobbler.desktop` on the desktop | `sh packaging/install-launcher.sh --uninstall` removes those it wrote, each of which carries an `X-CodeCobbler-Installer` mark, and rebuilds the desktop database's `mimeinfo.cache`. A file at one of those paths without the mark, such as the `cobble` that `pip install --user` puts in `~/.local/bin`, is left alone and named |
| the single executable | the file you extracted | delete it |

What `cobble`, the desktop icon and the window write is your work, so no
uninstall removes it: a `<project>-map-YYYYMMDD-HHMMSS.html` beside every project
mapped, and the shelf, `CodeCobbler.html` plus its register -- `maps.json` in
`~/.local/share/codecobbler`, or `CodeCobbler.maps.json` on the shared volume
you chose. `cobblerpy` itself writes only the files its options name.

## The desktop application

```bash
pip install ".[gui]"          # from a clone
cobblerpy-gui
```

Drop a project folder on the window. It counts what is there, surveys it, and
puts the findings beside the controls in the order somebody inheriting a
codebase needs them: **whether the work has already been attempted more than
once, and which attempt got furthest**, then where the work stopped. The full
interactive map is a button away.

Built to sit beside an editor, because that is where you already are.

**The window is the only part of this project with a dependency.**
Installed without `[gui]`, it is a library and a command line that import
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

Every path given to `--map`, `--json`, `--mermaid` or `--drawio` is checked
**before any file is read**. One that cannot be written is one sentence naming
the flag and the reason, and exit 2 -- nothing is surveyed and nothing is
written:

```
cobblerpy: --json out/reports/survey.json: cannot write here (its folder does not exist)
```

Otherwise exit status is 1 only when the directory cannot be read or holds no
Python. Finding unfinished work is a result, not a failure, so it exits 0.
`cobble` refuses the same way when the map has nowhere to go: it is written
beside the project, so a read-only parent -- a mounted share, most often -- is
said and nothing is surveyed.

## Tests

```bash
python3 -m unittest discover -s tests -v     # 369 tests, no pytest required
                                             # 8 need PySide6 or a shared volume and skip
python3 verify_e2e.py                        # 116 end-to-end claim checks
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

Five mutations were run by hand against the suite: the comment classifier, the relative-import
resolution, the score's diminishing returns, the untracked-file detection and the cluster verdict.
Each made the specific test that guards it go red. One had to be rewritten after the first attempt
was caught by an IndentationError rather than by the test, which proves nothing. There is no
mutation harness in the repository, so re-running this means reapplying them by hand.

## The map

`--map out.html` writes one self-contained page.

**One box per folder, biggest first**, so the top of the chart is where the work went. Inside a
box a card is as wide as its module is long, and every box starts at the left edge -- the order
is top to bottom, by how much code is in it, not left to right by depth.

**Ribbons join folders, not modules.** One per ordered folder pair, however many imports it
carries, thicker the more that passes through it, and coloured by a gradient from the condition at
one end to the condition at the other. A ribbon per *import* is deliberately not drawn: on a chart
this size every one of them had to be followed by eye. The aggregate is what a reader can follow
-- on a large tree that is tens of ribbons rather than a thousand crossing lines. The map states
its own figures: the page says how many ribbons it drew and how many imports they carry, so the
numbers you read are the ones from your survey rather than from ours. Ribbons are drawn over the
folder boxes but under the cards, so they can never cover the thing you are reading.

Click any module for its source, its signals, and what it connects to, plus a top-to-bottom
flowchart of just that module's path. **How they connect** answers the relationship in code
rather than by name: for every module this one uses, the import that brings it into scope and
each call site that goes through it, with line numbers. Under `from . import snippets as snip`
the calls read `snip.for_module(...)`, so the bound name is what gets matched, not the target's.
A connection used more than eight times shows the first eight and states how many it held back.
Resolution runs through the same function the import graph is built from, so an edge drawn on
the chart and the evidence for it cannot disagree — and the verifier asserts that every edge
on the chart is evidenceable. The source shown is only the regions that matter -- the
lines a signal points at, with context, plus every definition header -- and gaps between regions
are marked rather than closed up, because a snippet that looks continuous but is not would mislead
anyone reading line numbers. Hovering a module dims everything it has nothing to do with.

A **dead-end** -- a call that reaches a body with nothing in it -- is the boundary where somebody
stopped: the caller exists, the callee does not. Its card carries the dead-end colour, and the
panel shows both sides at once, because neither half explains the situation alone. A dead end is
terminal, so it is left out of the flowchart a click opens and counted in the bar instead; it is
not a route to anywhere. Each one reports where it stands and what it was shaped to do, never why
anybody stopped. Motive is not recoverable and guessing at it would poison the rest.

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

The behaviour in "What it tells you" and "The map" is verified end to end by 116 checks
against a fixture whose properties are known by construction. What they do **not** cover: how
often a signal is a false positive on real code, the launcher installer, the desktop window, and
anything platform-specific.

Nor is the attachment question answered -- where set-aside code *would* have fitted -- which
[DESIGN_NOTES.md](DESIGN_NOTES.md) records as probably the boundary where deterministic analysis
ends and narration begins.

## Changelog

[CHANGELOG.md](CHANGELOG.md) says what changed in each release, in terms of
what it means for somebody using it.

## License

MIT -- see [LICENSE](LICENSE) in this repository. The release tarball currently ships the
executable alone, so if the binary is your only copy, the licence text is here rather than
beside it.
