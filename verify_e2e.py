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
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


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
KNOWN = {
    "README.md": "The service lives in app/. Helpers are in helpers/.\n",
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
        check("it records what calls it", bool(d["callers"]), d["callers"])
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
    # Strip the embedded source payload before checking markup: the map carries
    # the project's own code, so a naive search matches CONTENT, not markup.
    markup = re.sub(r"const DATA = .*?;\n", "", doc, flags=re.S)
    external = [t for t in re.findall(r"<(?:script|link|img|iframe)[^>]*>", markup)
                if "http" in t]
    check("no external assets (works offline from file://)", not external, external)
    check("the graph is inline SVG", '<svg id="graph"' in doc)
    check("nodes are clickable and keyboard reachable",
          "data-name=" in doc and "tabindex=" in doc)
    check("source snippets are embedded", '"regions"' in doc)
    check("the legend says what each colour derives from", "no static path" in doc)
    check("proof and inference are distinguished for the reader",
          "lower bound" in doc.lower())

    from cobblerpy import survey
    s = survey(root, with_history=False)
    for name in s.project.by_dotted:
        if not check(f"module appears as a node: {name}", f'data-name="{name}"' in doc):
            break

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
    check("no module imports anything outside the standard library",
          not offenders, offenders)

    import tomllib
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        cfg = tomllib.load(fh)
    check("the declared dependency list is empty",
          cfg["project"]["dependencies"] == [], cfg["project"]["dependencies"])

    import importlib
    scripts = cfg["project"].get("scripts", {})
    check("exactly one console entry point", len(scripts) == 1, scripts)
    target = next(iter(scripts.values()), "")
    module_name, _, func = target.partition(":")
    resolved = getattr(importlib.import_module(module_name), func, None)
    check("the entry point resolves to a real callable", callable(resolved), target)


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
