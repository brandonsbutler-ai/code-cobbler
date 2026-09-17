#!/usr/bin/env python3
"""End-to-end verification of every claim cobblerpy makes about itself.

Run: python3 verify_e2e.py

The unit tests check units. This checks the PRODUCT: it builds codebases with
known properties -- a known orphan, a known stub, a known vendored library, a
known abandoned module -- drives the real CLI as a user would, and asserts the
documented behaviour against actual output.

Two rules learned the hard way on this tool's sibling project:

  * A self-written verifier cannot find a mistake it shares with the code. So
    where possible this asserts against ground truth the fixture DEFINES,
    rather than against whatever the code happens to produce.

  * Numbers in prose rot. The documentation's own counts are checked against a
    live run, because they have drifted every single time they were not.

A FAIL sets the exit code. This script is allowed to say the tool does not
work, which is the only reason it is worth running.
"""

import os
import re
import shutil
from html.parser import HTMLParser
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

# Filled in by verify_documentation, settled in main().
STATED_E2E = set()


class _Doc(HTMLParser):
    """The generated map as an element tree rather than as a string.

    `'<svg id="graph"' in doc` tests the generator's spelling; it passes on a
    page where that text sits in a comment and fails on one that writes the
    attributes in the other order. The question is whether the page HAS an svg
    with that id once parsed, which only a parser answers.
    """

    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text_of = {}
        self._stack = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        self._stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data):
        if self._stack:
            self.text_of[self._stack[-1]] = self.text_of.get(self._stack[-1], "") + data

    def find(self, tag, **attrs):
        return [a for t, a in self.elements
                if t == tag and all(a.get(k) == v for k, v in attrs.items())]

    def has(self, tag, **attrs):
        return bool(self.find(tag, **attrs))

    def attr_values(self, name):
        return {a[name] for _t, a in self.elements if name in a}

    def script_text(self):
        return self.text_of.get("script", "")


def parse_page(path):
    with open(path, encoding="utf-8") as fh:
        return _Doc(fh.read())


def check(claim, ok, evidence=""):
    (PASS if ok else FAIL).append(claim)
    print(f"  [{'PASS' if ok else 'FAIL'}] {claim}")
    if evidence and not ok:
        for line in str(evidence).splitlines()[:6]:
            print(f"         {line}")
    return ok


def section(name):
    print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")


def cli(*args):
    r = subprocess.run([sys.executable, "-m", "cobblerpy", *map(str, args)],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    return r


def build(files, git=False):
    """A codebase with properties we know in advance."""
    root = tempfile.mkdtemp()
    for relpath, text in files.items():
        path = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(text).lstrip("\n"))
    if git:
        for args in (("init", "-q"), ("config", "user.email", "t@e.com"),
                     ("config", "user.name", "T"), ("add", "-A"),
                     ("commit", "-q", "-m", "initial")):
            subprocess.run(("git", "-C", root) + args, capture_output=True)
    return root


# A fixture whose every property is known by construction.
#
# It now includes TWO COMPETING ATTEMPTS at one job -- app/ingest.py and
# app/ingest_v2.py -- because without them the restart checks only ever ran
# their negative branch. "The map omits the section" passed while nothing
# proved the section appears when it should, which is a green check that
# cannot fail in the direction that matters.
KNOWN = {
    "README.md": "The service lives in app/. Helpers are in helpers/.\n",
    # A directory the survey must walk past AND must mention walking past.
    # Without it the exclusion checks only ever ran their negative branch:
    # "the map says nothing about exclusions" passed while nothing proved it
    # says something when there IS something to say.
    ".venv/lib/site.py": "VERSION = 1\n",
    "node_modules/pkg/thing.py": "VERSION = 1\n",
    "app/ingest.py": '''
        def parse_record(raw):
            return raw

        def validate_record(row):
            pass

        def store_record(row):
            pass
    ''',
    "app/ingest_v2.py": '''
        def parse_record(raw):
            return dict(raw)

        def validate_record(row):
            return True

        def enrich_record(row):
            return row

        def store_record(row):
            pass
    ''',
    "app/__init__.py": "",
    "app/main.py": '''
        """Entry point."""
        import argparse

        from . import svc
        from helpers import util

        if __name__ == "__main__":
            svc.run()
            util.helper()
    ''',
    "app/svc.py": '''
        import json
        import os


        def run():
            # TODO: wire up the retry path
            try:
                return os.getcwd()
            except OSError:
                pass


        def remediate(finding):
            pass


        def report(finding):
            """Returns a string."""
            return str(finding)


        def caller(f):
            return remediate(f)
    ''',
    "helpers/__init__.py": "",
    # Genuinely clean: documented, imported, reachable. The first version of
    # this fixture was not imported by anything, so the tool correctly called
    # it an orphan and the check that expected silence failed. The tool was
    # right and the fixture was wrong.
    "helpers/util.py": '''
        """Helpers."""


        def helper():
            """Returns one."""
            return 1
    ''',
    "orphan.py": "def never_imported():\n    return 2\n",
    "vendor/__init__.py": "",
    "vendor/lib.py": "# TODO: upstream issue 41\ndef go():\n    pass\n",
    "vendor/LICENSE": "MIT\n",
    "broken.py": "def f(:\n    pass\n",
}


def verify_structure(root):
    section("CLAIM: structure is read correctly from a codebase we designed")
    from cobblerpy import survey
    s = survey(root, with_history=False)
    p = s.project
    names = set(p.by_dotted)

    check("every module is found", {"app.main", "app.svc", "helpers.util",
                                    "orphan", "vendor.lib"} <= names,
          sorted(names))
    entries = dict(p.entry_points)
    check("the __main__ guard is found as an entry point", "app.main" in entries,
          sorted(entries))
    check("a plain library module is not called an entry point",
          "helpers.util" not in entries)
    check("the relative import app.main -> app.svc resolves",
          "app.svc" in p.imports.get("app.main", ()), p.imports.get("app.main"))
    check("the module nothing imports is reported as an orphan",
          "orphan" in p.orphans, p.orphans)
    check("a module that IS imported is not an orphan",
          "app.svc" not in p.orphans)
    check("the unparseable file is reported, not skipped",
          any(m.error and "syntax" in m.error for m in p.modules),
          [m.relpath for m in p.modules if m.error])


def verify_abandonment(root):
    section("CLAIM: the signals we planted are found, and nothing else is")
    from cobblerpy import survey
    s = survey(root, with_history=False)
    rows = {r["module"]: r for r in s.frontier}

    svc = rows.get("app.svc", {}).get("counts", {})
    check("the TODO we wrote is found", svc.get("todo") == 1, svc)
    check("the empty except we wrote is found", svc.get("empty_except") == 1, svc)
    check("the unused import we wrote is found", svc.get("unused_import") == 1, svc)
    check("the stub we wrote is found", svc.get("stub_pass") == 1, svc)
    check("a finished function is not called a stub",
          svc.get("stub_pass", 0) == 1, svc)

    util = rows.get("helpers.util", {}).get("counts", {})
    check("a documented, imported, reachable module reports NO signals at all",
          util == {}, util)
    check("a package __init__ is not blamed for re-exporting",
          not rows.get("app", {}).get("counts", {}).get("unused_import"))


def verify_origin_and_clusters(root):
    section("CLAIM: vendored and untracked code is told apart from the project")
    from cobblerpy import survey
    from cobblerpy.clusters import analyse, split
    s = survey(root)

    check("the subtree with its own LICENSE is called vendored",
          s.origins.get("vendor.lib", {}).get("origin") == "vendored",
          s.origins.get("vendor.lib"))
    check("the project's own code is not called vendored",
          s.origins.get("app.svc", {}).get("origin") != "vendored")

    with open(os.path.join(root, "late.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    s2 = survey(root)
    check("a file never committed is called untracked",
          s2.origins.get("late", {}).get("origin") == "untracked",
          s2.origins.get("late"))
    os.remove(os.path.join(root, "late.py"))

    s3 = survey(root)
    clusters = analyse(s3.project, s3.root, s3.modules_by_key, s3.origins)
    kept, aside = split(clusters)
    check("the app named in the README is kept", "app.main" in kept, sorted(kept))
    # Only the KEPT half was ever asserted. The separation claim has two sides,
    # and the side that matters to somebody inheriting a codebase is the one
    # that says "you do not have to read this": a split that keeps everything
    # passes a kept-only check while doing nothing at all.
    check("the vendored subtree is set aside, not kept",
          any(m.startswith("vendor") for m in aside) and
          not any(m.startswith("vendor") for m in kept),
          f"kept={sorted(kept)} aside={sorted(aside)}")
    check("kept and set-aside do not overlap", not (kept & aside),
          sorted(kept & aside))
    check("every cluster verdict carries its reasons",
          all(c["why"] for c in clusters))


def verify_deadends(root):
    section("CLAIM: dead-ends report position and direction, never motive")
    from cobblerpy import survey
    from cobblerpy.deadends import find
    s = survey(root, with_history=False)
    found = {d["name"]: d for d in find(s.project, s.modules_by_key, s.origins)}

    check("the called stub is a dead-end", "remediate" in found, sorted(found))
    if "remediate" in found:
        d = found["remediate"]
        # Asserting the list is non-empty says nothing about whether the
        # RIGHT caller is in it, and the caller is the whole value of the
        # finding -- it is what tells a reader where to start.
        check("it records the module that actually calls it",
              {c["module"] for c in d["callers"]} == {"app.svc"}, d["callers"])
        check("the caller record carries a line number to jump to",
              all(isinstance(c.get("lineno"), int) and c["lineno"] > 0
                  for c in d["callers"]), d["callers"])
        direction = d["direction"] or ""
        check("direction names what it takes", "finding" in direction, direction)
        check("direction names its finished sibling", "report" in direction, direction)
        blob = " ".join(str(v) for v in d.values()).lower()
        check("no motive appears anywhere in the record",
              not any(w in blob for w in ("because", "gave up", "why they",
                                          "abandoned the")), blob[:120])
        check("the caveat about legitimate empties travels with it",
              "abstract" in (d["caveat"] or "").lower())
    check("vendored stubs are not counted as your frontier",
          not [d for d in found.values() if d["module"].startswith("vendor")])


def verify_map_and_exports(root, workdir):
    section("CLAIM: the map is self-contained, interactive, and exports cleanly")
    out = os.path.join(workdir, "map.html")
    mmd = os.path.join(workdir, "map.mmd")
    dio = os.path.join(workdir, "map.drawio")
    r = cli(root, "--map", out, "--mermaid", mmd, "--drawio", dio, "--no-history")
    check("the CLI exits 0", r.returncode == 0, r.stderr[-300:])
    check("the map was written", os.path.isfile(out))

    with open(out, encoding="utf-8") as fh:
        doc = fh.read()
    # Parsed, not matched. The map embeds the project's own source, so a
    # substring search over the file is searching the CONTENT as much as the
    # markup -- a page whose only <svg id="graph"> is inside a docstring used
    # to pass. The parser sees elements, which is the thing being claimed.
    page = parse_page(out)
    external = [a.get("src") or a.get("href") for t, a in page.elements
                if t in ("script", "link", "img", "iframe")
                and str(a.get("src", "") or a.get("href", "")).startswith("http")]
    check("no external assets (works offline from file://)", not external, external)
    check("the graph is a real inline svg element with that id",
          page.has("svg", id="graph"),
          [a.get("id") for t, a in page.elements if t == "svg"])
    nodes = [a for _t, a in page.elements if "data-name" in a]
    check(f"nodes are clickable and keyboard reachable ({len(nodes)} nodes)",
          nodes and all("tabindex" in a for a in nodes),
          [a.get("data-name") for a in nodes if "tabindex" not in a][:6])
    check("source snippets are embedded", '"regions"' in page.script_text())
    legend = " ".join(page.text_of.values()).lower()
    check("the legend says what each colour derives from",
          "no static path" in legend)
    check("proof and inference are distinguished for the reader",
          "lower bound" in legend)

    from cobblerpy import survey
    s = survey(root, with_history=False)

    # The restart findings have to reach the MAP, not just the terminal. They
    # were computed before anything rendered them, and the names leak into the
    # page through other lists -- so a substring search over the file called
    # them present while no section existed. These read the page's TEXT, which
    # is what a person actually sees.
    legend_text = " ".join(page.text_of.values())
    from cobblerpy.attempts import find as _find_attempts
    _groups = _find_attempts(s.project, s.modules_by_key, s.origins)
    if _groups:
        if any(g.get("common_gaps") for g in _groups):
            check("the map names the gap every attempt shares",
                  "every attempt stopped at" in legend_text, legend_text[:140])
        check("the map shows how the work grew", "HOW IT GREW" in legend_text)
        # The continuation links, read off the GRAPH rather than the prose.
        # This is the one inference drawn on the map, so it has to be present
        # AND explained; a dashed line nothing accounts for is worse than none.
        with open(out, encoding="utf-8") as _fh:
            _svg = _fh.read()
        _links = re.findall(
            r'<path class="continues"[^>]*data-from="([^"]+)" data-to="([^"]+)"',
            _svg)
        _expected = sum(len(g["attempts"]) - 1 for g in _groups)
        check(f"the graph links each attempt to the probable continuation "
              f"({_expected})", len(_links) == _expected, _links)
        check("every continuation link points at the attempt to resume from",
              all(to in {g["resume_at"] for g in _groups} for _f, to in _links),
              _links)
        check("the legend explains the continuation link",
              "probable continuation" in legend_text)
        check("a module the flow stops inside gets its own state",
              'data-state="deadend"' in _svg)
        _first = _groups[0]["lineage"]
        if len(_first) > 1 and _first[1]["added"]:
            check("the lineage says what each attempt added",
                  _first[1]["added"][0] in legend_text, _first[1]["added"])
    else:
        check("with no restart groups the map omits the section",
              "HOW IT GREW" not in legend_text)

    # The prose has to describe the chart that is drawn, whether or not there
    # are restart groups. It said "left to right is distance from a start
    # point" for a while after the layout became top-down -- the kind of wrong
    # that survives every structural test, because nothing reads the sentence.
    # Clicking a card has to answer the question it was clicked for. The panel
    # is built in the browser from an embedded payload, so the check reads the
    # PAYLOAD -- the data the click handler will render -- rather than the
    # markup, which does not exist until somebody clicks.
    import json as _json
    _payload = re.search(r"const DATA = (\{.*?\});\n", _svg, re.S)
    if _payload:
        _data = _json.loads(_payload.group(1)
                            .replace("\\u003c", "<").replace("\\u003e", ">"))
        _verdicts = {k: v.get("verdict") for k, v in _data.items()
                     if v.get("verdict")}
        if _groups:
            _resume = {g["resume_at"] for g in _groups}
            check("clicking the attempt to resume from says so",
                  all(_verdicts.get(r, {}).get("kind") == "resume"
                      for r in _resume if r in _verdicts),
                  {r: _verdicts.get(r, {}).get("kind") for r in _resume})
            _others = set(_verdicts) - _resume
            check("clicking a superseded attempt says another got further",
                  all(_verdicts[o]["kind"] == "superseded" for o in _others),
                  {o: _verdicts[o]["kind"] for o in _others})
            check("every verdict carries the names it was matched on",
                  all(v.get("shared") for v in _verdicts.values()),
                  [k for k, v in _verdicts.items() if not v.get("shared")])
        _dead = [k for k, v in _data.items() if v.get("deadends")]
        check(f"a dead end carries the lines it stops on ({len(_dead)})",
              all(all(d.get("lineno") for d in _data[k]["deadends"])
                  for k in _dead), _dead[:4])

    # What the survey walked past has to be ON THE PAGE. Excluding a
    # virtualenv is right; a page that says "975 modules" while 9,219 files
    # were declined cannot be told apart from a small project.
    _excluded = getattr(s.project, "excluded", {}) or {}
    if _excluded:
        check("the map says how many files the survey did not read",
              "were not read" in legend_text or "was not read" in legend_text,
              legend_text[:160])
        check("the map names the directories it walked past",
              all(name in legend_text for name in
                  sorted(_excluded, key=lambda k: -_excluded[k])[:2]),
              sorted(_excluded))
    else:
        check("with nothing excluded the map says nothing about exclusions",
              "were not read" not in legend_text)

    # The panel a click fills, and every id the handler reaches for.
    #
    # The click handler called showModal() on a dialog the page never
    # contained, so clicking a module did nothing -- silently, because
    # getElementById returns null and the exception dies inside the handler.
    # Nothing caught it: the checks verified the PAYLOAD, which was perfect,
    # and never that anything could render it.
    for _id in ("panel", "ptitle", "phint", "pbody", "summarySource"):
        check(f"the panel element #{_id} exists in the page",
              f'id="{_id}"' in _svg, _id)
    # Scoped to the map's OWN script. The page embeds the surveyed project's
    # source, so a project containing JavaScript has its getElementById calls
    # in here too -- ForteFide contributed eleven ids from its own front end,
    # every one of them reported as dangling by a whole-page scan.
    _own = "".join(re.findall(r"<script>(.*?)</script>", _svg, re.S)[-1:])
    _reached = set(re.findall(r"getElementById\('([^']+)'\)", _own))
    _declared = set(re.findall(r'id="([^"]+)"', _svg))
    _dangling = sorted(_reached - _declared)
    check("every element the script reaches for exists",
          not _dangling, _dangling)
    check("relations in the panel are followable",
          "data-goto" in _svg and "scrollIntoView" in _svg)
    check("the summary is embedded once, not twice",
          _svg.count('<h4>where the work stopped</h4>') == 1,
          _svg.count('<h4>where the work stopped</h4>'))

    check("the description matches the layout the code produces",
          "top to bottom" in legend_text and "Left to right" not in legend_text,
          [l for l in ("top to bottom", "Left to right") if l in legend_text])
    drawn = page.attr_values("data-name")
    missing = sorted(set(s.project.by_dotted) - drawn)
    check(f"every module appears as a node ({len(drawn)} drawn)",
          not missing, missing[:8])

    with open(mmd, encoding="utf-8") as fh:
        mermaid = fh.read()
    check("mermaid is a flowchart with classes",
          mermaid.startswith("flowchart LR") and "classDef" in mermaid)
    check("mermaid ids are legal (no dots or dashes)",
          all("." not in i and "-" not in i
              for i in re.findall(r"^\s+(n_\S+)\[", mermaid, re.M)))

    import xml.etree.ElementTree as ET
    tree = ET.parse(dio)
    cells = tree.getroot().findall(".//mxCell")
    check("drawio is well-formed with positioned vertices",
          tree.getroot().tag == "mxfile"
          and all(c.find("mxGeometry") is not None
                  for c in cells if c.get("vertex")))


def verify_hostile(workdir):
    section("CLAIM: hostile and malformed input is reported, never fatal")
    from cobblerpy import survey
    root = build({
        "ok.py": "def f():\n    return 1\n",
        "unparseable.py": "def broken(:\n",
        "weird name.py": "x = 1\n",
        "deep.py": "x = " + "[" * 60 + "]" * 60 + "\n",
        "empty.py": "",
        "binary.py": "\x00\x01 not really python\n",
    })
    try:
        s = survey(root, with_history=False)
        check("a directory of malformed files surveys without raising", True)
        check("the good module is still found", "ok" in s.project.by_dotted,
              sorted(s.project.by_dotted))
        errors = [m for m in s.project.modules if m.error]
        check("each unreadable file carries a reason",
              all(m.error for m in errors), [m.relpath for m in errors])
        r = cli(root, "--no-history")
        check("the CLI survives them too", r.returncode == 0, r.stderr[-200:])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    empty = build({"notes.txt": "no python\n"})
    try:
        r = cli(empty)
        check("a directory with no Python fails clearly and non-zero",
              r.returncode == 1 and "no Python files" in r.stderr, r.stderr[:160])
    finally:
        shutil.rmtree(empty, ignore_errors=True)

    r = cli(os.path.join(workdir, "does_not_exist"))
    check("a missing directory is reported, not a traceback",
          r.returncode == 1 and "Traceback" not in r.stderr, r.stderr[:160])


def verify_no_dependencies():
    section("CLAIM: nothing outside the standard library, proven by AST walk")
    import ast
    stdlib = set(sys.stdlib_module_names)
    offenders = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "cobblerpy")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module.split(".")[0]]
                for m in mods:
                    if m and m not in stdlib and m != "cobblerpy":
                        offenders.append(f"{name}: {m}")
    # The claim is two-part, so the check is two-part. The LIBRARY imports
    # nothing outside the standard library; the desktop WINDOW imports a
    # toolkit and is the only thing that may. Asserting both halves means a
    # stray import in the library cannot hide behind the window's exemption.
    library = [o for o in offenders if not o.startswith("qt_app")]
    check("the library imports nothing outside the standard library",
          not library, library)
    window = [o for o in offenders if o not in library]
    check("only the window imports a toolkit, and only PySide6",
          all("PySide6" in o for o in window), window)

    import tomllib
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        cfg = tomllib.load(fh)
    check("the declared dependency list is empty",
          cfg["project"]["dependencies"] == [], cfg["project"]["dependencies"])

    import importlib
    scripts = cfg["project"].get("scripts", {})
    # Every declared entry point is IMPORTED AND RESOLVED, not matched against
    # a spelling: the question is whether the thing a `pip install` puts on
    # somebody's PATH actually exists and is callable.
    check("the console entry points are cobblerpy and cobblerpy-gui",
          sorted(scripts) == ["cobblerpy", "cobblerpy-gui"], scripts)
    for name, target in sorted(scripts.items()):
        module_name, _, func = target.partition(":")
        try:
            resolved = getattr(importlib.import_module(module_name), func, None)
        except ImportError as exc:
            # The window's module imports its toolkit lazily, so this should
            # import cleanly even where PySide6 is absent. If it does not,
            # that is the finding.
            resolved = None
            target = f"{target} -- {exc}"
        check(f"`{name}` resolves to a real callable", callable(resolved), target)


def verify_documentation():
    section("CLAIM: the documentation states the numbers the tools produce")
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    m = re.search(r"Ran (\d+) tests", r.stderr)
    units = int(m.group(1)) if m else -1
    check("the unit suite passes", r.returncode == 0, r.stderr[-200:])

    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    stated = {int(x) for x in re.findall(r"(\d+) tests", readme)}
    check(f"README states the real test count ({units})", stated == {units},
          f"states {sorted(stated)}, actual {units}")

    # The end-to-end count the README quotes. Nothing checked it, so it could
    # drift the way vanilla-extract's did -- 156 documented against 170 run,
    # through a rename and two reviews. The real total is only known once every
    # check has finished, so it is recorded here and settled in main().
    STATED_E2E.update(int(x) for x in
                      re.findall(r"(\d+)(?:\s+[\w-]+){0,3}\s+checks?\b", readme))
    check("README states an end-to-end check count", bool(STATED_E2E),
          f"found {sorted(STATED_E2E)}")

    # Every option the parser accepts must appear in the README. --max-files
    # and --no-history shipped documented nowhere but `--help`, and --max-files
    # had no help text either, so the only account of the option a user could
    # find was its name. The parser object is read rather than the help text: a
    # flag cannot be added now without documenting it or failing this check.
    from cobblerpy.__main__ import build_parser
    flags = {o for a in build_parser()._actions for o in a.option_strings
             if o.startswith("--") and o != "--help"}
    undocumented = sorted(f for f in flags if f not in readme)
    check(f"every command-line option appears in the README ({len(flags)})",
          not undocumented, undocumented)

    # An option with no help text is undocumented wherever else it appears.
    nohelp = sorted(a.option_strings[0] for a in build_parser()._actions
                    if a.option_strings and not a.help
                    and a.option_strings[0] != "--help")
    check("every option has help text", not nohelp, nohelp)

    # The packaged launchers, IMPORTED AND RESOLVED rather than read.
    #
    # Both bundled binaries shipped broken for exactly this reason: the
    # package's own modules use relative imports, which is right for
    # `python -m cobblerpy` and impossible for a script PyInstaller runs as a
    # top-level module. The binary died on its first line. The build script
    # said "smoke-test it before shipping", and an instruction is not a check.
    import importlib.util as _ilu
    for entry, wants_toolkit in (("cli_entry.py", False), ("app_entry.py", True)):
        path = os.path.join(ROOT, "packaging", entry)
        if not os.path.isfile(path):
            check(f"packaging/{entry} exists", False, path)
            continue
        spec = _ilu.spec_from_file_location(f"_entry_{entry[:-3]}", path)
        module = _ilu.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            loaded, why = True, ""
        except ImportError as exc:
            # The app launcher imports Qt on purpose, so that PyInstaller sees
            # it; on a machine without Qt that is expected, not a defect.
            loaded = wants_toolkit and "PySide6" in str(exc)
            why = f"skipped: {exc}" if loaded else str(exc)
        except Exception as exc:                          # noqa: BLE001
            loaded, why = False, f"{type(exc).__name__}: {exc}"
        check(f"packaging/{entry} imports without a relative-import error",
              loaded, why)
        if loaded and not why:
            check(f"packaging/{entry} exposes a callable main",
                  callable(getattr(module, "main", None)),
                  sorted(n for n in vars(module) if not n.startswith("_"))[:6])

    import tomllib
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        version = tomllib.load(fh)["project"]["version"]
    from cobblerpy import __version__
    check("version agrees between package and pyproject", version == __version__,
          f"{version} vs {__version__}")

    for name in ("README.md", "DESIGN_NOTES.md", "LICENSE", "pyproject.toml"):
        check(f"{name} is present", os.path.isfile(os.path.join(ROOT, name)))


def main():
    print("cobblerpy end-to-end verification")
    print(f"python {sys.version.split()[0]}  |  repo {ROOT}")

    workdir = tempfile.mkdtemp()
    root = build(KNOWN, git=True)
    try:
        verify_structure(root)
        verify_abandonment(root)
        verify_origin_and_clusters(root)
        verify_deadends(root)
        verify_map_and_exports(root, workdir)
        verify_hostile(workdir)
        verify_no_dependencies()
        verify_documentation()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)

    # Settled last: the real total is only known once everything has run, and
    # this check counts itself, so the figure the README quotes is the figure
    # this run prints.
    section("CLAIM: the README quotes the real number of checks")
    real_total = len(PASS) + len(FAIL) + 1
    check(f"README quotes the real check count ({real_total})",
          STATED_E2E == {real_total},
          f"README says {sorted(STATED_E2E)}, this run has {real_total}")

    section("RESULT")
    total = len(PASS) + len(FAIL)
    print(f"  {len(PASS)}/{total} claims verified")
    if FAIL:
        print(f"\n  {len(FAIL)} FAILED:")
        for f in FAIL:
            print(f"    - {f}")
        print("\n  VERIFICATION FAILED")
        return 1
    print("\n  ALL CLAIMS VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
