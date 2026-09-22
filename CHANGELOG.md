# Changelog

What changed in each release, in terms of what it means for somebody using it.
The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
version numbers are [semantic](https://semver.org/spec/v2.0.0.html).

The product is CodeCobbler; the package, the import name and the command are
`cobblerpy`. Releases before 0.1.3 were the source and a standalone binary
attached to a GitHub release.

## [0.1.3] - 2026-09-22

The first release packaged for PyPI, as `cobblerpy`, with `[gui]` for the
window. Nothing about how the tool runs has changed for that: it still imports
only the standard library, and installing it brings nothing else with it.

### Added

- The source distribution carries the tests, the claim checker and the
  packaging scripts, so whoever downloads it can re-run the claims the README
  makes instead of believing them. The README says how.
- `packaging/verify_dist.py` checks a built wheel and source distribution
  rather than the checkout: that each installs into an empty environment, pulls
  nothing in with it, puts working commands on PATH, and carries no file that
  should never have left this machine.
- A file the parser cannot read, or cannot open, now carries an `unreadable`
  signal and draws as broken on the map. It used to score nothing and sort in
  among the healthy modules, which read as a clean bill of health for a file
  nobody had looked at.
- A pre-push guard in `.githooks/` refuses to push a machine path, a credential
  or a private name. It reads the commits being pushed, their messages, the tip
  tree and tag messages rather than a diff, because the case that leaked before
  was a new branch and a tag, not a change.

### Changed

- The minimum Python is 3.11, not 3.9. The package declared `>=3.9` and the
  README repeated it, but nothing here had ever been run below 3.11, and the
  project's own checkers cannot start there: `verify_e2e.py`,
  `packaging/verify_dist.py` and one unit test import `tomllib`, which arrived
  in 3.11. Raised 2026-09-22 rather than ship a compatibility claim that had
  never been tested and could not be checked.
- Restart groups are ranked by the work at stake in them rather than by how
  many attempts are in the group: the lines in the furthest-along attempt,
  taken down by how much of it is written and by the share of its definitions
  a sibling attempt already holds. The three numbers and the lines left to
  finish are printed on every row, in the terminal and on the map, and travel
  in `--json` as `at_stake_lines`, `resume_lines`, `resume_percent`,
  `elsewhere_percent` and `remaining_lines`. Restart count is now the
  tie-break. Measured on a 979-module tree with 22 restart groups: in two of
  the three groups the old order put on top, every definition of the file it
  named also sits in a sibling attempt -- copies of one script under different
  names -- so resuming either returns nothing new.
- A copy is no longer reported as a restart. A group whose named file defines
  nothing a sibling has not got already -- or that is the same file name kept
  under a backup or archive directory -- is reported under its own heading with
  its own verdict, naming the one file you can check it against, and is kept
  out of the ranked restart list. It carries `verdict` and `copy_of` in
  `--json`. Measured on the 979-module tree: 13 of its 22 groups came out
  copies (backup folders, re-dated variants, generated twins) and 9 were left
  as restarts -- 15 and 10 once the scope fix below splits a copy out of the
  group it sits in rather than condemning the group with it. The cut came from the distribution -- the share of the named file's
  definitions a sibling also holds runs 12, 13, 22, 29, 50, 53, 60, 60, 80 and
  then 100 thirteen times -- not from a round number. A backup directory on its
  own is not evidence and is never used alone: it called a genuinely distinct
  archived script a copy.
- Where two attempts are equally far along, the bigger file is now the one
  named to resume from; the module name is still the last resort so two runs
  over one tree agree. Completeness is a proportion of what each file itself
  started, so two copies of one module score the same however far apart their
  sizes are. On the 979-module tree the name picked the smaller file in 7 of
  the 15 groups with a tie at the top -- in one, a 2,767-line copy under a
  backup directory over the 3,527-line live file it came from, which also made
  the work-at-stake figure measure the wrong file.
- `remaining_lines` counts only definitions with no body. It was the lines the
  completeness score said were missing, and that score also folds in whether
  anything reaches the file and whether a test names it -- so a 2,809-line file
  with a body on every definition, which nothing imported, was reported as
  having ~281 lines left to finish. The sentence now names the count of
  unwritten definitions (`unwritten_definitions` in `--json`), and where there
  are none the line is dropped rather than printing "nothing left unwritten by
  this measure". What the measure does and does not cover is stated once above
  the groups. Measured across all 67 attempts in those 22 groups, every one has
  a body on every definition, so the line prints nowhere on that tree.
- The detectors report far less that was never unfinished work. A stub or a
  `NotImplementedError` is reported only where a TODO or FIXME in the same body
  says somebody meant to come back to it, and a class whose every method is
  empty is read as an interface. A missing docstring no longer ranks anything.
  An unused import is not reported when the name is used in a `# type:` comment,
  in `__all__` or in an annotation, when it is a `__future__` directive, or when
  the author waived it with `# noqa: F401` or `# pylint: disable=unused-import`.
  Each change was measured finding by finding against two installed Python
  trees: reports went away in bulk and none was added.
- Names are read the way Python reads them, as Unicode. A re-export in a
  codebase that does not write in English used to be split mid-word or missed
  entirely, and then reported as abandoned work.
- A helper defined inside a documented function is no longer reported as an
  undocumented public definition.
- The README's account of the detectors says what each one is worth, which of
  them are rules of thumb rather than measured rules, and what the stub signal
  misses. The ribbon figure it quoted contradicted the code and is gone: the
  map states its own figures, and the README points at those.
- The measurements are described by what the corpora are rather than by naming
  two private codebases, and the examples no longer carry function names lifted
  out of a private tree.

### Fixed

- The package can be imported on the 3.11 it says it needs. `report.py` put a
  backslash inside an f-string expression in two places, which only parses from
  3.12 (PEP 701); on 3.11 it is a SyntaxError at import, so `cobblerpy.report`
  and everything that reaches it -- `cobblerpy.launch`, `cobblerpy.__main__`,
  both console scripts -- would not load at all. Measured on CPython 3.11.16:
  the suite went 21 failures and 83 errors out of 369, and `verify_e2e.py`
  exited 1; on 3.12 everything was green, which is how it got this far. The
  markup is now lifted into a name and out of the expression, and the rendered
  HTML is byte-for-byte what it was. The floor is checked three ways now, in
  the suite: `ast.parse(feature_version=...)`, which is what had been trusted
  and which accepts all four of PEP 701's f-string relaxations; a detector for
  those four, pinned against what a real 3.11 does; and, where the machine has
  a 3.11, importing every shipped module under it. Only the last is conclusive,
  and CI runs the whole suite on 3.11 for that reason.
- A backup of the attempt the tool told you to resume no longer deletes the
  restart it belongs to. The copy verdict was read off the group's named file
  alone, so four genuine competing attempts plus a copy of the winning one
  under `backups/` printed no restart at all -- the attempts, the percentages
  and the resume line were replaced by a single copy row. Backing up a losing
  attempt was harmless; backing up the winner swallowed the group, and every
  inherited codebase has a backup folder in it. The verdict is about a pair of
  files, so it is now asked between every pair in a group: a copy is taken out
  and reported on its own, and the restart survives with its remaining
  attempts, re-ranked on what is left. A group is a copy end to end only when
  every file in it is the same file as every other. On the 979-module tree the
  matcher still finds 22 groups -- 12 copies end to end, 7 with no duplicate,
  3 holding both -- reported as 25: 10 restarts and 15 copies, against 9 and
  13. The restart recovered had been hidden behind a generated twin of one of
  its files, and was being called a copy of a file 8% alike by text; the three
  copies split out are 87.7%, 99.7% and 100% the same text as what they were
  split from.
- A mistyped output path is refused before the survey runs, not after it is
  thrown away. `--map`, `--json`, `--mermaid` and `--drawio` raised a
  traceback out of the writer when the folder did not exist, was unwritable,
  or was a folder; `cobble` did the same when the project's parent is
  read-only, which a mounted share often is. Every output path is checked
  first, every bad one is named with its flag and the reason, and the exit
  status is 2: `cobblerpy: --json out/s.json: cannot write here (its folder
  does not exist)`.
- `CODECOBBLER_HOME` is the only thing that moves the shelf. A drive letter
  was hardcoded ahead of the documented fallback, so on Windows any mapped
  drive on that letter -- commonly a corporate network share -- silently
  became the shelf root, with nothing in the output saying so. The letter is
  gone, and a run that writes the shelf anywhere but the default now says
  where it put it.
- Links to maps are built as URLs instead of being pasted together.
  `file://` plus a Windows path parses with the whole path as the hostname, so
  every `cobble` run there left a dead link on the shelf; on any platform a
  project path holding `#`, `?` or `%` broke the same way and a space went
  through unencoded.

- Working notes and generated maps are ignored by git. A map embeds the source
  of whatever it surveyed, so one made inside the checkout and committed would
  have published somebody else's code.

### Security

- The push guard stores its rule tables as hashes rather than as plain text.
  The rule whose job is to remove a developer's account name, drive label and
  project directory had been publishing all three in the file that carries it.
  Matching is unchanged; a blocked finding now prints the file and line and
  withholds the text. What this does not do is written down beside it: anyone
  who guesses a name can still confirm it, and nothing here un-publishes
  anything already pushed.

## [0.1.2] - 2026-09-19

### Added

- The standalone binary answers `--help` and `--version`, and opens the shelf
  when it is given nothing. In v0.1.1 both of those mapped the current folder
  instead of answering.
- The shelf: an index of the maps you have already made, newest first, with the
  project name, its module count and when it was mapped. A desktop launch
  inherits an arbitrary working directory, so a bare click now opens the shelf
  instead of guessing that you meant "here".
- `packaging/install-launcher.sh` installs the front doors -- the `cobble`
  command, an application entry, a desktop icon and a file-manager right-click
  -- and asks where the shelf of maps should live.
- `--json` carries the restarts, the dead ends and the forks, so everything the
  map shows can be read by another program.

### Changed

- It never imports or executes the code it reads, even when run from inside the
  project being read. Every program of ours takes the current directory, and an
  empty `PYTHONPATH` entry, off the import path before anything else loads. The
  README says plainly what Python has already imported by then, because no
  package can stop that.
- The installer refuses to overwrite a file at one of its paths that it did not
  write, and names the file. It marks everything it does write, and `--uninstall`
  removes only what carries that mark. `--force` together with `--uninstall` is
  refused, and so is any argument it does not know.
- The map answers Enter as it answers a click, and colours each key swatch the
  way the cards it stands for are coloured.

### Fixed

- A comment tag is counted where the comment starts with one, or after a
  separator or a pragma -- not mid-prose -- and NOTE is not one of them.
- A subclass that hands the call up with `super()` counts as reaching the base,
  so the base is no longer reported as replaced everywhere and dead.
- A file Python cannot decode is counted as one that will not parse, rather than
  vanishing from the survey.
- A file dropped on the icon is mapped as the project it belongs to, beside that
  project, under its own row on the shelf.
- Two maps made in the same second get their own names. The shelf's register is
  swapped in whole so a failed write cannot leave half of it, keeps its file
  mode, and says so when it cannot be updated.
- History read from a shallow clone is dated as a shallow clone's, wherever it
  appears.
- The desktop entry writes a literal percent as `%%`, so the launcher is not
  handed a mangled command line.
- The version is written in one place, in the package, and pyproject reads it
  from there. The project Homepage points at the repository that exists.

## [0.1.1] - 2026-09-18

### Fixed

- The launcher opens the map first and reports afterwards: the module count and
  "opening the map" when a browser took it, and where the file is when none
  could be found. It used to announce success before trying, so anyone running
  headless, over ssh or in a container was told it had worked and was never
  given the path to the file that had just been written. The shelf had the same
  flaw and got the same fix.

## [0.1.0] - 2026-09-18

First tagged release, and the first single-file build -- one executable you can
hand to a machine with no Python on it.

### Added

- Point it at a directory and it reports the structure, the execution flow, what
  the version history says was being worked on, where the previous developer
  stopped, and the dead ends they had already found and abandoned.
- `--map` writes one self-contained HTML page: an org chart of folders, cards
  sized by how long each module is, ribbons for what imports what, a trace you
  can follow, and a detail panel with the evidence behind every claim it makes.
  No CDN, no fonts, no network; it opens from a `file://` URL and survives being
  e-mailed.
- `--frontier` for where the work stopped, `--json` for everything in machine
  form, `--mermaid` and `--drawio` for diagrams other tools can open.
- Restart and fork detection: the same job attempted more than once, and which
  attempt got furthest -- labelled as a hypothesis, with its evidence attached.
- The desktop window (`cobblerpy-gui`, the `[gui]` extra) and the `cobble`
  launcher: drop a project folder, read the findings beside the controls.
- A test suite that needs no pytest, and `verify_e2e.py`, which drives the real
  command line against a codebase whose properties are known by construction and
  asserts the README's claims against what came back.
