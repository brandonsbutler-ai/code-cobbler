"""Tests for cobblerpy.

    python3 -m unittest discover -s tests -v

This tool makes claims about somebody else's code, and a reader acts on those
claims. A false orphan sends them to read a file that is fine; a missed one
hides the file they needed. So the tests here are mostly about PRECISION --
proving the analysis says nothing it cannot support.

Fixtures are built as real directories of real Python, because every bug found
so far came from running against real trees rather than from unit-level
reasoning: relative imports that resolved to the wrong module, git history that
stopped at a rename, and a comment classifier that flagged section dividers.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from html.parser import HTMLParser
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cobblerpy import survey                                    # noqa: E402
from cobblerpy.abandonment import analyse_module, score         # noqa: E402
from cobblerpy.clusters import analyse as analyse_clusters, split  # noqa: E402
from cobblerpy.graph import Project                             # noqa: E402
from cobblerpy.layout import compute, state_of                  # noqa: E402
from cobblerpy.origin import classify                           # noqa: E402
from cobblerpy.scan import _looks_like_code, scan_file, scan_tree  # noqa: E402


def write(root, relpath, text):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(text).lstrip("\n"))
    return path


class Page(HTMLParser):
    """The generated map as an element tree, so tests ask what it IS.

    The map embeds the surveyed project's own source. A substring search over
    the file therefore searches the CONTENT as much as the markup: a project
    containing the text `data-name="run"` in a docstring would satisfy a check
    about the graph's nodes. Parsing separates the two.
    """

    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.text_parts = []
        self.script = []
        self._stack = []
        self._located = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        self._stack.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag in [t for t, _a in self._stack]:
            while self._stack and self._stack.pop()[0] != tag:
                pass

    def handle_data(self, data):
        top = self._stack[-1][0] if self._stack else ""
        if top == "script":
            self.script.append(data)
        elif top != "style":
            self.text_parts.append(data)
            self._located.append((list(self._stack), data))

    def text_in(self, tag, **attrs):
        """The text inside one element, joined with nothing between.

        Whole-document text searches are how a check about the HEADER passes
        because the same word appears in the body. This narrows it. Nothing is
        inserted between parts, so a wordmark split across two spans for colour
        still reads as one word.
        """
        return "".join(
            data for stack, data in self._located
            if any(t == tag and all(a.get(k) == v for k, v in attrs.items())
                   for t, a in stack))

    def find(self, tag, **attrs):
        return [a for t, a in self.elements
                if t == tag and all(a.get(k) == v for k, v in attrs.items())]

    def has(self, tag, **attrs):
        return bool(self.find(tag, **attrs))

    def tags(self):
        return [t for t, _a in self.elements]

    def attr_values(self, name):
        return {a[name] for _t, a in self.elements if name in a}

    @property
    def text(self):
        return " ".join(self.text_parts)

    @property
    def script_text(self):
        return "".join(self.script)


class Tree:
    """A throwaway directory of Python files, optionally a git repo."""

    def __init__(self, files, git=False):
        self.dir = tempfile.mkdtemp()
        for relpath, text in files.items():
            write(self.dir, relpath, text)
        if git:
            self._git("init", "-q")
            self._git("config", "user.email", "t@example.com")
            self._git("config", "user.name", "Tester")
            self._git("add", "-A")
            self._git("commit", "-q", "-m", "initial")

    def _git(self, *args):
        subprocess.run(("git", "-C", self.dir) + args, capture_output=True)

    def survey(self, **kw):
        kw.setdefault("with_history", False)
        return survey(self.dir, **kw)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestScan(unittest.TestCase):
    def test_records_definitions_imports_and_calls(self):
        t = Tree({"m.py": '''
            import os
            from pathlib import Path

            def run(a, b=1, *rest, **kw):
                """Does a thing."""
                return os.path.join(a, b)

            class Thing:
                def method(self):
                    pass
        '''})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "m.py"), t.dir)
        names = {d.name: d for d in m.definitions}
        self.assertEqual(names["run"].args, ["a", "b", "*rest", "**kw"])
        self.assertTrue(names["run"].returns)
        self.assertEqual(names["method"].kind, "method")
        self.assertEqual(names["method"].parent, "Thing")
        self.assertEqual(names["method"].body_kind, "pass")
        self.assertIn(("os", "os", 1, 0, "os"), m.imports)

    def test_a_file_that_does_not_parse_is_a_finding_not_a_crash(self):
        t = Tree({"broken.py": "def f(:\n    pass\n"})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "broken.py"), t.dir)
        self.assertIn("syntax error", m.error)

    def test_body_kinds_are_distinguished(self):
        t = Tree({"m.py": '''
            def a(): pass
            def b(): ...
            def c(): raise NotImplementedError
            def d(): raise ValueError("real")
            def e(): return 1
        '''})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "m.py"), t.dir)
        kinds = {d.name: d.body_kind for d in m.definitions}
        self.assertEqual(kinds["a"], "pass")
        self.assertEqual(kinds["b"], "ellipsis")
        self.assertEqual(kinds["c"], "raise")
        self.assertEqual(kinds["d"], "code")      # a real raise is not a stub
        self.assertEqual(kinds["e"], "code")

    def test_import_time_side_effects_are_separated_from_constants(self):
        t = Tree({"m.py": '''
            import os
            NAME = "x"
            SIZES = [1, 2]
            DATA = open("f").read()
            print("runs on import")
            def f(): pass
        '''})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "m.py"), t.dir)
        kinds = [k for k, _ln in m.toplevel_effects]
        self.assertIn("Expr", kinds)              # the print
        self.assertIn("Assign", kinds)            # DATA = open(...)
        # plain constant assignment is configuration, not an effect
        self.assertLessEqual(len([k for k in kinds if k == "Assign"]), 1)

    def test_scan_tree_skips_the_directories_nobody_means(self):
        t = Tree({"a.py": "x = 1\n",
                  "__pycache__/b.py": "x = 1\n",
                  ".venv/lib/c.py": "x = 1\n",
                  "node_modules/d.py": "x = 1\n"})
        self.addCleanup(t.close)
        mods, _skipped, excluded = scan_tree(t.dir)
        self.assertEqual([m.relpath for m in mods], ["a.py"])
        # Excluding them is right; being silent about it is not. Pointed at a
        # real project this walked past 9,219 files -- a .claude directory and
        # a virtualenv's site-packages -- surveyed 975, and reported
        # "0 skipped", which is true of the file LIMIT and says nothing at all
        # about the rest.
        self.assertEqual(sorted(excluded), [".venv", "__pycache__",
                                            "node_modules"])
        self.assertEqual(sum(excluded.values()), 3)


class TestCommentClassifier(unittest.TestCase):
    """The rule measured against 48,482 real comments. See scan.py."""
    def test_rejects_prose_dividers_pragmas_and_examples(self):
        for text in ("# --- DOCX", "# =============", "# noqa: BLE001",
                     "# type: ignore", "# pylint: disable=no-member",
                     "# This is an ordinary sentence about behaviour.",
                     "#     T = TypeVar('T')", "# tamper", "# x == y"):
            self.assertFalse(_looks_like_code(text), text)

    def test_accepts_genuinely_disabled_statements(self):
        for text in ("# result = compute(x, y)", "# import zlib",
                     "# print(value)", "# return None", "#i_count = 0",
                     "# self.cache = {}", "# raise ValueError('x')"):
            self.assertTrue(_looks_like_code(text), text)

    def test_the_statement_rule_is_the_load_bearing_clause(self):
        """Removing it must change verdicts; it guarded 1143 of 48,482."""
        self.assertFalse(_looks_like_code("# handler"))       # bare name
        self.assertTrue(_looks_like_code("# handler()"))      # a call


class TestTruncation(unittest.TestCase):
    """A survey that did not read every file must say so in the artifact."""

    def _capped_map(self, cap):
        import tempfile, os
        from cobblerpy.report import write_map
        files = {"pkg/__init__.py": ""}
        for i in range(8):
            files[f"pkg/m{i}.py"] = f"from . import m{(i + 1) % 8} as nxt\n"
        t = Tree(files)
        self.addCleanup(t.close)
        s = survey(t.dir, with_history=False, max_files=cap)
        out = os.path.join(tempfile.mkdtemp(), "map.html")
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            return s, fh.read()

    def test_a_truncated_survey_says_so_in_the_html(self):
        """The terminal warned; the file that gets sent to someone did not.

        With the survey capped, modules are reported as orphans because their
        importer was never read. The map showed those orphans with no hint the
        survey was partial.
        """
        s, doc = self._capped_map(3)
        self.assertTrue(s.skipped, "the fixture did not actually hit the cap")
        self.assertIn("This survey is incomplete", doc)
        self.assertIn("--max-files", doc)

    def test_the_warning_names_the_number_not_read(self):
        s, doc = self._capped_map(3)
        self.assertIn(f"{s.skipped:,} Python file", doc)

    def test_a_complete_survey_carries_no_warning(self):
        """The band must not appear when nothing was skipped."""
        s, doc = self._capped_map(5000)
        self.assertEqual(s.skipped, 0)
        self.assertNotIn("This survey is incomplete", doc)
        self.assertNotIn("NOT READ", doc)


class TestGraph(unittest.TestCase):
    def test_relative_imports_resolve_to_the_module_not_the_package(self):
        """`from .fmt import rtf` names the PACKAGE in the module slot."""
        t = Tree({
            "pkg/__init__.py": "",
            "pkg/fmt/__init__.py": "",
            "pkg/fmt/rtf.py": "def read(): return 1\n",
            "pkg/dispatch.py": "from .fmt import rtf\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        self.assertIn("pkg.fmt.rtf", s.project.imports["pkg.dispatch"])
        self.assertIn("pkg.dispatch", s.project.imported_by["pkg.fmt.rtf"])
        self.assertNotIn("pkg.fmt.rtf", s.project.orphans)

    def test_an_aliased_relative_import_is_not_an_orphan(self):
        """`from . import snippets as snip` binds a name the package lacks.

        Resolving the BOUND name looks for pkg.snip, which does not exist, so
        the edge is dropped and an actively-imported module is reported as an
        orphan. cobblerpy did exactly this to its own snippets module.
        """
        t = Tree({
            "pkg/__init__.py": "",
            "pkg/helper.py": "def go():\n    return 1\n",
            "pkg/main.py": "from . import helper as h\n\nprint(h.go())\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        self.assertIn("pkg.helper", s.project.imports["pkg.main"],
                      "the aliased import produced no edge")
        self.assertNotIn("pkg.helper", s.project.orphans)

    def test_a_plain_dotted_import_still_resolves(self):
        """The `import pkg.module` candidate has to survive every change here.

        Removing it reports two modules in vanilla-extract and six in cobblerpy
        as orphans, and until this test existed both suites stayed green while
        it was gone.
        """
        t = Tree({
            "pkg/__init__.py": "",
            "pkg/helper.py": "VALUE = 1\n",
            "pkg/main.py": "import pkg.helper\n\nprint(pkg.helper.VALUE)\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        self.assertIn("pkg.helper", s.project.imports["pkg.main"])
        self.assertNotIn("pkg.helper", s.project.orphans)
    def test_entry_points_record_why_they_qualify(self):
        t = Tree({
            "run.py": 'import argparse\nif __name__ == "__main__":\n    pass\n',
            "lib.py": "def helper(): return 1\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        reasons = dict(s.project.entry_points)
        self.assertIn("run", reasons)
        self.assertTrue(any("__main__" in r for r in reasons["run"]))
        self.assertNotIn("lib", reasons)

    def test_import_cycles_are_reported(self):
        t = Tree({"a.py": "import b\n", "b.py": "import a\n"})
        self.addCleanup(t.close)
        s = t.survey()
        self.assertTrue(s.project.cycles)

    def test_orphans_exclude_tests_and_package_inits(self):
        t = Tree({
            "run.py": 'if __name__ == "__main__":\n    import used\n',
            "used.py": "x = 1\n",
            "stranded.py": "y = 2\n",
            "pkg/__init__.py": "",
            "test_thing.py": "def test_x(): pass\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        self.assertIn("stranded", s.project.orphans)
        self.assertNotIn("test_thing", s.project.orphans)
        self.assertNotIn("pkg", s.project.orphans)

    def test_unreferenced_definitions_carry_their_caveat(self):
        t = Tree({"m.py": '''
            def plain_unused(): pass

            @app.route("/x")
            def routed(): pass
        '''})
        self.addCleanup(t.close)
        s = t.survey()
        byname = {u["name"]: u for u in s.project.unreferenced}
        self.assertIn("plain_unused", byname)
        if "routed" in byname:
            self.assertIn("framework", byname["routed"]["caveat"] or "")


class TestAbandonment(unittest.TestCase):
    def test_signals_are_found_with_line_numbers(self):
        t = Tree({"m.py": '''
            import os
            import json

            def stub():
                pass

            def real():
                # TODO: finish the retry path
                try:
                    return os.getcwd()
                except OSError:
                    pass
        '''})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "m.py"), t.dir)
        sig = analyse_module(m)
        self.assertIn("stub_pass", sig)
        self.assertIn("todo", sig)
        self.assertIn("empty_except", sig)
        self.assertIn("unused_import", sig)       # json is never used
        self.assertTrue(all(ln > 0 for _t, ln in sig["todo"]))

    def test_init_files_are_not_blamed_for_unused_imports(self):
        """Re-exporting is the whole point of a package __init__."""
        t = Tree({"pkg/__init__.py": "from .thing import Thing\n",
                  "pkg/thing.py": "class Thing: pass\n"})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "pkg/__init__.py"), t.dir)
        self.assertNotIn("unused_import", analyse_module(m))

    def test_score_has_diminishing_returns(self):
        """Forty TODOs in one file is one situation, not forty."""
        one = score({"todo": [("x", 1)]})
        forty = score({"todo": [("x", i) for i in range(40)]})
        self.assertLess(forty, one * 40)
        self.assertGreater(forty, one)

    def test_a_clean_module_scores_zero(self):
        t = Tree({"m.py": '''
            """A module."""


            def documented(value):
                """Returns the value."""
                return value
        '''})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "m.py"), t.dir)
        self.assertEqual(score(analyse_module(m)), 0)


class TestOrigin(unittest.TestCase):
    def test_untracked_files_are_named_as_such(self):
        t = Tree({"tracked.py": "x = 1\n"}, git=True)
        self.addCleanup(t.close)
        write(t.dir, "never_committed.py", "y = 2\n")
        s = survey(t.dir)
        origins = s.origins
        self.assertEqual(origins["tracked"]["origin"], "tracked")
        self.assertEqual(origins["never_committed"]["origin"], "untracked")

    def test_vendor_directories_are_recognised(self):
        t = Tree({"app.py": "x = 1\n",
                  "vendor/lib/thing.py": "y = 2\n"}, git=True)
        self.addCleanup(t.close)
        s = survey(t.dir)
        self.assertEqual(s.origins["vendor.lib.thing"]["origin"], "vendored")

    def test_a_subtree_with_its_own_licence_is_vendored(self):
        t = Tree({"app.py": "x = 1\n",
                  "extern_pkg/mod.py": "y = 2\n",
                  "extern_pkg/LICENSE": "MIT\n"}, git=True)
        self.addCleanup(t.close)
        s = survey(t.dir)
        self.assertEqual(s.origins["extern_pkg.mod"]["origin"], "vendored")

    def test_no_repository_gives_unknown_not_a_guess(self):
        t = Tree({"a.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = survey(t.dir)
        self.assertEqual(s.origins["a"]["origin"], "unknown")


class TestClusters(unittest.TestCase):
    def test_an_unclaimed_cluster_is_set_aside(self):
        t = Tree({
            "README.md": "The app lives in app/.\n",
            "app/__init__.py": "",
            "app/main.py": 'from . import core\nif __name__ == "__main__":\n    core.go()\n',
            "app/core.py": "def go(): return 1\n",
            "borrowed/__init__.py": "",
            "borrowed/engine.py": "from . import util\ndef run(): return util.x()\n",
            "borrowed/util.py": "def x(): return 2\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        clusters = analyse_clusters(s.project, s.root, s.modules_by_key, s.origins)
        kept, aside = split(clusters)
        self.assertTrue(any(m.startswith("app") for m in kept))
        self.assertTrue(any(m.startswith("borrowed") for m in aside))
        self.assertFalse(any(m.startswith("borrowed") for m in kept))

    def test_a_cluster_named_in_the_readme_is_kept(self):
        t = Tree({
            "README.md": "Helpers live in helpers/.\n",
            "app.py": 'if __name__ == "__main__":\n    pass\n',
            "helpers/__init__.py": "",
            "helpers/thing.py": "def t(): return 1\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        kept, aside = split(analyse_clusters(s.project, s.root,
                                             s.modules_by_key, s.origins))
        self.assertTrue(any(m.startswith("helpers") for m in kept))

    def test_every_verdict_carries_its_reasons(self):
        t = Tree({"app.py": 'if __name__ == "__main__":\n    pass\n'})
        self.addCleanup(t.close)
        s = t.survey()
        for cluster in analyse_clusters(s.project, s.root, s.modules_by_key,
                                        s.origins):
            self.assertTrue(cluster["why"])


class TestLayout(unittest.TestCase):
    def test_every_module_gets_a_position(self):
        t = Tree({
            "run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
            "lib.py": "def go(): return 1\n",
            "stranded.py": "x = 1\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        g = compute(s.project, {r["module"]: r for r in s.frontier})
        for name in s.project.by_dotted:
            self.assertIn(name, g["nodes"], name)
        self.assertGreater(g["width"], 0)
        self.assertGreater(g["height"], 0)

    def test_unreached_modules_are_placed_not_dropped(self):
        """'Nothing reaches this' is a finding; it must still appear."""
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "stranded.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        g = compute(s.project, {r["module"]: r for r in s.frontier})
        self.assertIn("stranded", g["nodes"])

    def test_inference_and_proof_get_different_colours(self):
        """Grey (no static path) must not look like red (broken)."""
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "stranded.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        g = compute(s.project, {r["module"]: r for r in s.frontier})
        state, why = state_of(g["nodes"]["stranded"])
        self.assertEqual(state, "maybe")
        self.assertTrue(why)


class TestEndToEnd(unittest.TestCase):
    def test_survey_of_a_realistic_tree_holds_together(self):
        t = Tree({
            "README.md": "app/ holds the service.\n",
            "app/__init__.py": "",
            "app/main.py": 'from . import svc\nif __name__ == "__main__":\n    svc.run()\n',
            "app/svc.py": "import json\n\ndef run():\n    # TODO: retries\n    pass\n",
            "orphan.py": "def never_called(): pass\n",
        }, git=True)
        self.addCleanup(t.close)
        s = survey(t.dir)
        self.assertTrue(s.project.entry_points)
        self.assertIn("orphan", s.project.orphans)
        self.assertTrue(s.totals)
        self.assertTrue(s.origin_totals)
        self.assertIsInstance(s.as_dict(), dict)

    def test_the_cli_runs_and_writes_a_map(self):
        t = Tree({"a.py": 'if __name__ == "__main__":\n    pass\n'})
        self.addCleanup(t.close)
        out = os.path.join(t.dir, "map.html")
        r = subprocess.run([sys.executable, "-m", "cobblerpy", t.dir,
                            "--map", out, "--no-history"],
                           capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(
                               os.path.abspath(__file__))))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(out))
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        page = Page(doc)
        remote = [a for _t, a in page.elements
                  if str(a.get("src", "")).startswith("http")
                  or str(a.get("href", "")).startswith("http")]
        self.assertEqual(remote, [], "an element loads a remote URL")
        # In the page's TEXT, not its source -- the map embeds the project's
        # own code, which could itself contain the word.
        self.assertIn("Proven", page.text)

    def test_an_empty_directory_fails_clearly(self):
        t = Tree({"notes.txt": "no python here\n"})
        self.addCleanup(t.close)
        r = subprocess.run([sys.executable, "-m", "cobblerpy", t.dir],
                           capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(
                               os.path.abspath(__file__))))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no Python files", r.stderr)


class TestDeadEnds(unittest.TestCase):
    def test_a_called_stub_is_a_dead_end(self):
        from cobblerpy.deadends import find
        t = Tree({"m.py": """
            def caller():
                return helper()

            def helper():
                pass
        """})
        self.addCleanup(t.close)
        s = t.survey()
        found = find(s.project, s.modules_by_key, s.origins)
        names = {d["name"] for d in found}
        self.assertIn("helper", names)

    def test_an_exception_class_is_not_a_dead_end(self):
        """An empty class body is idiomatic. This was a live false positive."""
        from cobblerpy.deadends import find
        t = Tree({"m.py": """
            class DimensionError(Exception):
                pass

            def go():
                raise DimensionError()
        """})
        self.addCleanup(t.close)
        s = t.survey()
        self.assertEqual(find(s.project, s.modules_by_key, s.origins), [])

    def test_an_abstract_method_is_not_a_dead_end(self):
        from cobblerpy.deadends import find
        t = Tree({"m.py": """
            import abc

            class Thing:
                @abc.abstractmethod
                def run(self):
                    ...

            def go(t):
                return t.run()
        """})
        self.addCleanup(t.close)
        s = t.survey()
        self.assertEqual(find(s.project, s.modules_by_key, s.origins), [])

    def test_direction_reports_shape_never_motive(self):
        from cobblerpy.deadends import find
        t = Tree({"m.py": """
            def report(finding):
                return str(finding)

            def assess(finding):
                return 1

            def remediate(finding):
                pass

            def go(f):
                return remediate(f)
        """})
        self.addCleanup(t.close)
        s = t.survey()
        found = {d["name"]: d for d in find(s.project, s.modules_by_key, s.origins)}
        self.assertIn("remediate", found)
        direction = found["remediate"]["direction"] or ""
        self.assertIn("finding", direction)        # what it takes
        self.assertIn("report", direction)         # what it sits beside
        # no motive anywhere in the record
        blob = " ".join(str(v) for v in found["remediate"].values()).lower()
        for word in ("because", "gave up", "abandoned", "why they"):
            self.assertNotIn(word, blob)

    def test_vendored_stubs_are_not_your_frontier(self):
        from cobblerpy.deadends import find
        t = Tree({"app.py": "from vendor.lib import go\nx = go()\n",
                  "vendor/__init__.py": "",
                  "vendor/lib.py": "def go():\n    pass\n"}, git=True)
        self.addCleanup(t.close)
        s = survey(t.dir, with_history=False)
        found = find(s.project, s.modules_by_key, s.origins)
        self.assertFalse([d for d in found if d["module"].startswith("vendor")])


class TestMap(unittest.TestCase):
    def test_the_map_is_self_contained_and_interactive(self):
        import re
        t = Tree({"run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
                  "lib.py": "def go():\n    return 1\n",
                  "stranded.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        # Strip the embedded source payload first: the map carries the project's
        # own code, so a naive string search matches CONTENT, not markup. That
        # exact false positive fired on the first run of this check.
        markup = re.sub(r"const DATA = .*?;\n", "", doc, flags=re.S)
        page = Page(doc)
        external = [a for t, a in page.elements
                    if t in ("script", "link", "img", "iframe")
                    and str(a.get("src", "") or a.get("href", "")).startswith("http")]
        self.assertEqual(external, [])
        self.assertTrue(page.has("svg", id="graph"),
                        f"svg ids present: {[a.get('id') for t, a in page.elements if t == 'svg']}")
        nodes = [a for _t, a in page.elements if "data-name" in a]
        self.assertIn("run", {a["data-name"] for a in nodes})
        for node in nodes:
            self.assertIn("tabindex", node,
                          f"node {node.get('data-name')!r} is not keyboard reachable")

    def test_every_module_appears_as_a_node(self):
        import re
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "stranded.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        for name in s.project.by_dotted:
            self.assertIn(f'data-name="{name}"', doc)

    def test_the_payload_cannot_break_out_of_the_script_block(self):
        """Source containing </script> must not close the data block."""
        # The module must carry a SIGNAL, or its source is never embedded and
        # this test asserts nothing. The previous fixture was an entry point
        # with no signals: its source did not reach the map at all, so the
        # check stayed green with both escapes removed from svgmap.
        t = Tree({
            "main.py": "import mod\n\nif __name__ == '__main__':\n    mod.go()\n",
            "mod.py": '# TODO: </script><img src=x onerror=alert(1)>\n'
                      'def go():\n    pass\n',
        })
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn("alert(1)", doc,
                      "the fixture's source never reached the map, so nothing "
                      "about escaping is being tested")
        page = Page(doc)
        self.assertEqual(page.find("img"), [],
                         "the surveyed source supplied an element that survived parsing")
        self.assertLessEqual(page.tags().count("script"), 2,
                             "the surveyed source opened a script block")
        # Note for anyone mutating svgmap: the `<` and `>` escapes are each
        # independently sufficient to stop `</script>` closing the block, so
        # removing one changes nothing. Remove both and this goes red.

    def test_the_title_bar_names_product_tool_and_subject_separately(self):
        """Three different names, three different places on the page.

        The page used to open on a single heading, "Codebase map -- <folder>",
        which reads as though the product were called that. CodeCobbler is the
        product, cobblerpy is the tool that wrote the file (and the name on the
        command, the package and the import), and the folder is the subject.
        """
        import cobblerpy
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n'})
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        page = Page(doc)
        self.assertTrue(page.has("header", **{"class": "topbar"}),
                        f"no title bar; headers: {page.find('header')}")
        # The wordmark is two spans so half of it can carry the accent colour;
        # join without a separator or the assertion tests the markup's spacing.
        bar = page.text_in("header", **{"class": "topbar"})
        self.assertIn(cobblerpy.report.BRAND, bar)
        self.assertIn(f"cobblerpy {cobblerpy.__version__}", bar,
                      "the bar does not carry the real tool version")
        subject = os.path.basename(t.dir)
        self.assertIn(subject, bar,
                      "the surveyed folder is not named in the bar itself")
        # And the old mashed-together heading is gone.
        self.assertNotIn("Codebase map --", doc)

    def test_the_persistent_key_matches_the_colours_actually_drawn(self):
        """The key and the picture come from one table, or they drift.

        A key written next to a palette is a key that goes stale the first
        time a colour moves -- this project already shipped a legend that
        explained nine colours while the chart drew six.
        """
        from cobblerpy import svgmap
        t = Tree({"run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
                  "lib.py": "def go():\n    pass  # TODO: finish\n",
                  "stranded.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        page = Page(doc)
        chips = {a["data-state"] for a in page.find("button", **{"class": "chip"})}
        self.assertEqual(chips, set(svgmap._PALETTE),
                         "the key and the palette disagree")
        drawn = {a["data-state"] for _t, a in page.elements
                 if a.get("class") == "node" and "data-state" in a}
        self.assertTrue(drawn, "no nodes were drawn, so nothing is being compared")
        self.assertEqual(drawn - chips, set(),
                         f"drawn with no chip to explain them: {drawn - chips}")
        # And it is pinned, not merely present -- the whole point is that it
        # survives a twelve-thousand-pixel scroll.
        self.assertIn(".keybar{position:sticky", doc.replace("\n", ""))

    def test_picking_a_colour_steps_the_other_modules_back(self):
        """Run the map's own script and check which nodes it turns off.

        The stub DOM in the claim verifier returns [] from querySelectorAll,
        which is enough to prove the script parses and nothing more: every
        handler in it is a no-op under that stub. This one hands the script
        real nodes and reads back what it did to them.
        """
        import json
        import re
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        t = Tree({"run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
                  "lib.py": "def go():\n    pass  # TODO: finish\n",
                  "stranded.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        js = re.findall(r"<script>(.*?)</script>", doc, re.S)[-1]
        page = Page(doc)
        states = [a["data-state"] for _tag, a in page.elements
                  if a.get("class") == "node" and "data-state" in a]
        self.assertGreater(len(set(states)), 1,
                           "the fixture draws one colour, so a filter that did "
                           "nothing would look identical to one that worked")
        target = sorted(set(states))[0]
        stub = """
const mk = (state) => ({dataset:{state, name:state}, cls:new Set(),
  classList:{toggle(c,on){on?this.o.cls.add(c):this.o.cls.delete(c)},
             remove(c){this.o.cls.delete(c)}, contains(c){return this.o.cls.has(c)}},
  setAttribute(k,v){this.attrs[k]=v}, attrs:{}, addEventListener(t,f){this.on[t]=f},
  on:{}, scrollIntoView(){}, closest(){return null}});
const NODES = %s.map(s => { const n = mk(s); n.classList.o = n; return n; });
const CHIPS = %s.map(s => { const c = mk(s); c.classList.o = c; return c; });
const made = {};
function el(i){ if(!made[i]) made[i] = {id:i, textContent:'', innerHTML:'x',
  dataset:{}, classList:{toggle(){},remove(){},contains(){return false}},
  addEventListener(){}, scrollTop:0, scrollIntoView(){}, closest(){return null}};
  return made[i]; }
global.CSS = {escape: s => s};
global.window = {addEventListener(){}, removeEventListener(){}};
global.document = {
  getElementById: i => el(i),
  querySelectorAll: sel => sel.indexOf('chip') >= 0 ? CHIPS : NODES,
  querySelector: () => null,
  addEventListener(){}};
""" % (json.dumps(sorted(set(states))), json.dumps(sorted(set(states))))
        probe = """
const chip = CHIPS.find(c => c.dataset.state === %s);
chip.on.click();
console.log(JSON.stringify({
  off: NODES.filter(n => n.cls.has('off')).map(n => n.dataset.state),
  pressed: CHIPS.filter(c => c.attrs['aria-pressed'] === 'true')
                .map(c => c.dataset.state),
  hint: made['keyhint'] ? made['keyhint'].textContent : null,
}));
chip.on.click();
console.log(JSON.stringify({
  off: NODES.filter(n => n.cls.has('off')).map(n => n.dataset.state)}));
""" % json.dumps(target)
        script = os.path.join(t.dir, "run.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(stub + "\n" + js + probe)
        r = subprocess.run(["node", script], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        picked, cleared = [json.loads(l) for l in r.stdout.strip().splitlines()]
        others = sorted(set(states) - {target})
        self.assertEqual(sorted(picked["off"]), others,
                         "the wrong modules stepped back")
        self.assertEqual(picked["pressed"], [target],
                         "the chip does not show which colour is picked")
        self.assertIn(target, picked["hint"] or "",
                      "the key does not say what it is showing")
        self.assertEqual(cleared["off"], [],
                         "clicking the same colour again did not clear it")

    def test_the_key_drops_its_definitions_only_once_it_has_pinned(self):
        """Full size while it is in place, swatch and word once it rides.

        There is ONE key. It used to be two -- a strip of chips and a block of
        definitions below it -- which printed every state name twice in the
        same place and left the reader checking whether the two agreed.
        """
        import json
        import re
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        t = Tree({"run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
                  "lib.py": "def go():\n    pass  # TODO: finish\n"})
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        page = Page(doc)
        # The definitions are IN the key, not in a second block beside it.
        self.assertEqual(page.find("div", **{"class": "legend"}), [],
                         "the separate definitions block is still being emitted")
        keytext = page.text_in("div", **{"class": "keybar"})
        flat = "".join(page.text_parts)
        from cobblerpy import svgmap
        for state, (_stroke, _fill, desc) in svgmap._PALETTE.items():
            self.assertIn(desc, keytext, f"{state} lost its definition")
            # Once on the page, full stop. Counting the WORD would not work --
            # "unfinished" legitimately appears inside live's sentence too --
            # so the definition itself is what must not be repeated.
            self.assertEqual(flat.count(desc), 1,
                             f"{state}'s definition is printed twice")
            self.assertEqual(
                len(page.find("button", **{"data-state": state})), 1,
                f"{state} has more than one chip")
        js = re.findall(r"<script>(.*?)</script>", doc, re.S)[-1]
        stub = """
const kb = {cls:new Set(),
  classList:{toggle(c,on){on?kb.cls.add(c):kb.cls.delete(c)},
             remove(c){kb.cls.delete(c)}, contains(c){return kb.cls.has(c)}}};
const chartHandlers = [], windowHandlers = [];
const box = {scrollTop: 0,
             addEventListener(t,f){ if(t === 'scroll') chartHandlers.push(f); }};
const made = {};
function el(i){ if(!made[i]) made[i] = {id:i, textContent:'', innerHTML:'x',
  dataset:{}, classList:{toggle(){},remove(){},contains(){return false}},
  addEventListener(){}, scrollTop:0, scrollIntoView(){}, closest(){return null}};
  return made[i]; }
global.CSS = {escape: s => s};
global.window = {addEventListener(t,f){ if(t === 'scroll') windowHandlers.push(f); },
                 removeEventListener(){}};
global.document = {
  getElementById: i => el(i),
  querySelectorAll: () => [],
  querySelector: sel => sel === '.keybar' ? kb
                      : sel === '.mapwrap' ? box : null,
  addEventListener(){}};
"""
        probe = """
const state = () => kb.classList.contains('pinned');
const seen = {atRest: state(), chartListeners: chartHandlers.length};
box.scrollTop = 1500;            // the chart scrolled, so the key is stuck
chartHandlers.forEach(f => f());
seen.pinned = state();
box.scrollTop = 0;               // back to the top of the chart
chartHandlers.forEach(f => f());
seen.releasedAgain = state();
console.log(JSON.stringify(seen));
"""
        script = os.path.join(t.dir, "pin.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(stub + "\n" + js + probe)
        r = subprocess.run(["node", script], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        seen = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(
            seen, {"atRest": False, "pinned": True, "releasedAgain": False,
                   "chartListeners": 1},
            f"the key does not change size when it pins: {seen}")
        # And the collapse is a real rule, not a class nothing styles.
        self.assertIn(".keybar.pinned .chip .def{display:none}",
                      doc.replace("\n", ""))

    def test_no_two_css_rules_claim_the_same_bare_class(self):
        """A second `.name{}` silently overrides the first.

        The title bar shipped as `.bar` for one render. So did a 7px accent
        progress bar 150 lines further down the same stylesheet, and the later
        rule won: `height:7px;background:var(--accent)` painted a blue band
        straight through the wordmark. Nothing failed -- the page rendered, the
        markup parsed, and only a screenshot showed it.
        """
        import re
        from cobblerpy.report import _CSS
        body = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
        # @media blocks are a DIFFERENT cascade context: `.wrap` is declared
        # once at top level and again under max-width:1180px on purpose. Drop
        # them, brace-balanced, or the check reports the intended override.
        while True:
            at = re.search(r"@media[^{]*\{", body)
            if not at:
                break
            depth, i = 1, at.end()
            while i < len(body) and depth:
                depth += (body[i] == "{") - (body[i] == "}")
                i += 1
            body = body[:at.start()] + body[i:]
        # ANY selector, not just a bare class -- the first version of this
        # check looked at bare classes only and sat green over three more
        # duplicates: `#graph .node{cursor:pointer}` written out twice
        # verbatim, and `.marks` and `.badge` each declared at one font size
        # and silently redeclared at another a hundred lines later.
        #
        # Counted only where the selector stands ALONE. `th,td{...}` followed
        # by `th{...}` is the ordinary shared-then-specific idiom, not a
        # collision, and flagging it would train the reader to ignore this.
        seen = {}
        for selectors, _decls in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
            parts = [" ".join(p.split()) for p in selectors.split(",") if p.strip()]
            if len(parts) == 1:
                seen[parts[0]] = seen.get(parts[0], 0) + 1
        dupes = sorted(k for k, n in seen.items() if n > 1)
        self.assertEqual(dupes, [], f"one selector, two rules: {dupes}")
        self.assertIn(".topbar", seen,
                      "the scan found no selectors at all, so it is not "
                      "looking at the stylesheet")
        self.assertIn("th,td", [" ".join(x.split()) for x in
                                re.findall(r"([^{}]+)\{", body)],
                      "the shared-then-specific case is gone from the "
                      "stylesheet, so this no longer proves it is allowed")


class TestFolderOverview(unittest.TestCase):
    """The overview groups by folder and sizes by lines."""

    def _map(self, files):
        t = Tree(files)
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            return s, fh.read()

    def test_a_longer_module_gets_a_longer_size_bar(self):
        """Every card is one size; the BAR carries the line count.

        Cards used to be sized individually. It encoded the right fact and
        drew a badly built brick wall -- no column lined up with the one
        above it -- and unaligned areas are the weaker comparison anyway:
        people read lengths off a shared baseline far more accurately than
        they judge rectangles that share nothing.
        """
        from cobblerpy.layout import size_share
        self.assertEqual(size_share(0), 0.0)
        self.assertEqual(size_share(5), 0.0)
        self.assertEqual(size_share(999999), 1.0)
        shares = [size_share(n) for n in (60, 200, 700, 2000)]
        self.assertEqual(shares, sorted(shares), shares)
        self.assertEqual(len(set(shares)), len(shares),
                         f"different sizes drew the same bar: {shares}")
        # Log, not linear: ten times the lines is nowhere near ten times the
        # bar, or the 25,556-line module in the corpus draws the median one
        # as a sliver.
        self.assertLess(size_share(2000) - size_share(200),
                        (size_share(200) - size_share(20)) * 4)

    def test_every_card_is_one_size_and_sits_on_an_even_column(self):
        from cobblerpy.layout import compute_folders, CARD_W, CARD_H
        files = {f"pkg/m{i}.py": "x = 1\n" * (i * 53 % 1400 + 3) for i in range(30)}
        files["run.py"] = 'if __name__ == "__main__":\n    pass\n'
        t = Tree(files)
        self.addCleanup(t.close)
        s = t.survey()
        nodes = compute_folders(s.project, s.modules_by_key)["nodes"]
        self.assertEqual({(n["w"], n["h"]) for n in nodes.values()},
                         {(CARD_W, CARD_H)}, "the cards are not all one size")
        pkg = [n for n in nodes.values() if n["folder"] == "pkg"]
        columns = sorted({n["x"] for n in pkg})
        self.assertGreater(len(columns), 1, "everything landed in one column")
        gaps = {round(b - a) for a, b in zip(columns, columns[1:])}
        self.assertEqual(len(gaps), 1,
                         f"the columns are not evenly spaced: {sorted(gaps)}")
        self.assertGreater(len({round(n["share"], 3) for n in pkg}), 1,
                           "the bar does not vary, so nothing carries size")

    def test_every_module_is_inside_its_own_folder_box(self):
        from cobblerpy.layout import compute_folders
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "lab/one.py": "x = 1\n" * 80,
                  "lab/two.py": "y = 2\n" * 300,
                  "docs/three.py": "z = 3\n"})
        self.addCleanup(t.close)
        s = t.survey()
        g = compute_folders(s.project, s.modules_by_key)
        self.assertEqual({b["folder"] for b in g["boxes"]},
                         {"the project root", "lab", "docs"})
        by_folder = {b["folder"]: b for b in g["boxes"]}
        for key, node in g["nodes"].items():
            box = by_folder[node["folder"]]
            self.assertGreaterEqual(node["x"], box["x"], key)
            self.assertGreaterEqual(node["y"], box["y"], key)
            self.assertLessEqual(node["x"] + node["w"], box["x"] + box["w"], key)
            self.assertLessEqual(node["y"] + node["h"], box["y"] + box["h"], key)
        self.assertEqual(g["boxes"][0]["folder"], "lab",
                         "boxes are not ordered by how much code is in them")
        self.assertEqual(by_folder["lab"]["loc"],
                         sum(s.modules_by_key[k].loc for k in ("lab.one", "lab.two")))

    @staticmethod
    def _overlap(a, b):
        return not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
                    or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])

    def test_no_two_cards_overlap(self):
        """Packing is the whole layout, so an overlap is the whole bug."""
        from cobblerpy.layout import compute_folders
        files = {f"pkg/m{i}.py": "x = 1\n" * (i * 37 % 900 + 3) for i in range(40)}
        files["run.py"] = 'if __name__ == "__main__":\n    pass\n'
        t = Tree(files)
        self.addCleanup(t.close)
        s = t.survey()
        placed = list(compute_folders(s.project, s.modules_by_key)["nodes"].values())
        self.assertGreater(len(placed), 30)
        for i, a in enumerate(placed):
            for b in placed[i + 1:]:
                self.assertFalse(self._overlap(a, b), f"two cards overlap: {a} {b}")

    def test_small_folders_share_a_shelf_without_running_into_each_other(self):
        """Enough folders that the row has to wrap.

        Sixteen of the corpus's thirty folders hold three modules or fewer, so
        a layout that gave each one a full-width band would spend most of the
        chart on empty space. They sit side by side and wrap -- and the first
        version of the overlap test used three folders, which fit on one shelf,
        so removing the wrap entirely left it green.
        """
        from cobblerpy.layout import compute_folders, CHART_W
        files = {f"d{i}/only.py": "x = 1\n" * 20 for i in range(14)}
        files["run.py"] = 'if __name__ == "__main__":\n    pass\n'
        t = Tree(files)
        self.addCleanup(t.close)
        s = t.survey()
        g = compute_folders(s.project, s.modules_by_key)
        boxes = g["boxes"]
        self.assertGreaterEqual(len(boxes), 15)
        self.assertGreater(len({b["y"] for b in boxes}), 1,
                           "every box landed on one shelf, so nothing wrapped")
        first_shelf = [b for b in boxes if b["y"] == boxes[0]["y"]]
        self.assertGreater(len(first_shelf), 1,
                           "no two boxes share a shelf, so each small folder "
                           "still takes a full-width band")
        for box in boxes:
            self.assertLessEqual(box["x"] + box["w"], CHART_W,
                                 f"{box['folder']} runs off the chart")
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertFalse(self._overlap(a, b),
                                 f"two folder boxes overlap: "
                                 f"{a['folder']} and {b['folder']}")

    def test_the_overview_draws_folders_and_no_connections(self):
        s, doc = self._map({
            "run.py": "import lab.one\nif __name__ == '__main__':\n    lab.one.go()\n",
            "lab/__init__.py": "",
            "lab/one.py": "def go():\n    pass\n",
        })
        page = Page(doc)
        self.assertTrue(page.find("rect", **{"class": "fbox"}),
                        "no folder boxes were drawn")
        edges = [a for tag, a in page.elements
                 if tag == "path" and str(a.get("class", "")).startswith("edge")]
        self.assertEqual(edges, [],
                         "the overview still draws connections, which is the "
                         "thing that had to be followed by eye")
        # And the import it is not drawing is still in the data, for the trace.
        import json, re
        payload = json.loads(
            re.search(r"const DATA = (\{.*?\});\n", doc, re.S).group(1)
            .replace("\\u003c", "<").replace("\\u003e", ">"))
        self.assertIn("lab.one", payload["run"]["uses"])

    def test_hidden_really_hides(self):
        """`hidden` has to beat a class, or the trace bar draws over the map.

        [hidden]{display:none} in the UA stylesheet is one attribute selector
        and loses to .tracebar{display:flex}. The bar was on screen, saying
        "tracing", on a map nobody had clicked.
        """
        s, doc = self._map({"run.py": 'if __name__ == "__main__":\n    pass\n'})
        css = doc[doc.index("<style>"):doc.index("</style>")]
        self.assertIn("[hidden]{display:none!important}", css.replace("\n", ""))
        page = Page(doc)
        self.assertIn("hidden", page.find("div", **{"class": "tracebar"})[0],
                      "the trace bar is not hidden to begin with")


class TestTrace(unittest.TestCase):
    """Clicking a module removes the rest of the map and lays out what is left.

    Run against the map's own script, because every part of this is decided in
    the browser: which modules survive, what row each one lands in, and what
    the bar says was left out.
    """

    FIXTURE = {
        # a -> b -> c, plus d beside c, and an island nothing touches.
        "a.py": 'import b\nif __name__ == "__main__":\n    b.go()\n',
        "b.py": "import c\nimport d\ndef go():\n    c.run(); d.run()\n",
        # Real bodies. `def run(): pass` is a DEAD END -- execution reaches
        # it and stops inside it -- and the trace leaves those out, so the
        # first version of this fixture tested the filter by accident and
        # nothing else.
        "c.py": "def run():\n    return 1\n",
        "d.py": "def run():\n    return 2\n",
        "island.py": "x = 1\n",
    }

    def _run(self, probe, graph_nodes=()):
        import json, re, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        t = Tree(self.FIXTURE)
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        js = re.findall(r"<script>(.*?)</script>", doc, re.S)[-1]
        stub = """
const made = {};
// `hidden` reads the ATTRIBUTE here, exactly as it does on a real <svg>:
// SVGElement has no `hidden` property, so assigning one changes nothing that
// CSS can see. The old stub made `.hidden = true` work, and the map shipped
// with a trace bar announcing a trace over an overview that never went away.
function fake(id){
  return {id: id, textContent: '', innerHTML: '', dataset: {},
          scrollTop: 0, attrs: {}, kids: [],
          get hidden(){ return 'hidden' in this.attrs; },
          setAttribute(k, v){ this.attrs[k] = v; },
          removeAttribute(k){ delete this.attrs[k]; },
          hasAttribute(k){ return k in this.attrs; },
          addEventListener(){}, scrollIntoView(){}, closest(){ return null; },
          classList:{toggle(){}, remove(){}, contains(){ return false; }},
          querySelectorAll(){ return parseNodes(this.innerHTML); }};
}
function el(id){ if(!made[id]) made[id] = fake(id); return made[id]; }
// Read back what the trace actually drew, from the markup it produced.
function parseNodes(markup){
  const out = [];
  const re = /<g class="node([^"]*)" data-name="([^"]+)" data-state="([^"]+)"/g;
  let m;
  while((m = re.exec(markup))) out.push({cls: m[1], name: m[2], state: m[3],
                                         dataset:{name: m[2]},
                                         addEventListener(){}});
  return out;
}
// Stand-ins for the overview's cards, so the listeners the page wires up
// at load can be fired and the result read off the nodes themselves.
const GRAPH_NODES = %s.map(name => {
  const n = {dataset: {name: name}, cls: new Set(), on: {},
             addEventListener(type, fn){ this.on[type] = fn; },
             setAttribute(){}, scrollIntoView(){}, closest(){ return null; }};
  n.classList = {toggle(c, want){ want ? n.cls.add(c) : n.cls.delete(c); },
                 remove(c){ n.cls.delete(c); },
                 contains(c){ return n.cls.has(c); }};
  return n;
});
global.GRAPH_NODES = GRAPH_NODES;
global.CSS = {escape: s => s};
global.window = {addEventListener(){}, removeEventListener(){}};
global.document = {
  getElementById: id => el(id),
  querySelectorAll: sel => sel.indexOf('#graph .node') >= 0 ? GRAPH_NODES : [],
  querySelector: sel => sel === '.mapwrap' ? el('mapwrap') : null,
  addEventListener(){}};
""" % json.dumps(list(graph_nodes))
        script = os.path.join(t.dir, "trace.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(stub + "\n" + js + probe)
        r = subprocess.run(["node", script], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-900:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def _rows(self, markup):
        """Row index per module, read out of the y of each card."""
        import re
        rows = {}
        for block in re.findall(r'<g class="node[^>]*data-name="([^"]+)"[^>]*>'
                                r'.*?<rect class="card" x="[\d.]+" y="([\d.]+)"',
                                markup, re.S):
            rows.setdefault(block[0], float(block[1]))
        return rows

    def test_tracing_a_module_keeps_only_what_it_connects_to(self):
        seen = self._run("""
const counted = drawTrace('b');
console.log(JSON.stringify({
  counted: counted,
  markup: document.getElementById('trace').innerHTML}));
""")
        markup = seen["markup"]
        rows = self._rows(markup)
        self.assertEqual(sorted(rows), ["a", "b", "c", "d"],
                         "the trace kept the wrong modules")
        self.assertNotIn("island", rows,
                         "a module with no connection to b survived")
        self.assertEqual(seen["counted"], {"shown": 4, "above": 1, "below": 2,
                                           "left_out": 0})
        # Laid out by distance from the module that was clicked: its importer
        # above it, the two it imports below, on one row between them.
        self.assertLess(rows["a"], rows["b"])
        self.assertLess(rows["b"], rows["c"])
        self.assertEqual(rows["c"], rows["d"],
                         "two modules at the same distance are on two rows")
        self.assertIn('class="node seed" data-name="b"', markup,
                      "the module being traced is not marked")

    def test_a_module_nothing_connects_to_traces_to_itself(self):
        seen = self._run("""
const counted = drawTrace('island');
console.log(JSON.stringify({
  counted: counted,
  markup: document.getElementById('trace').innerHTML}));
""")
        self.assertEqual(seen["counted"], {"shown": 1, "above": 0, "below": 0,
                                           "left_out": 0})
        self.assertEqual(sorted(self._rows(seen["markup"])), ["island"])

    def test_a_wide_level_wraps_instead_of_running_off_the_chart(self):
        """One module imported by many put them all on one row.

        `_license` in the corpus has 147 modules above it. They were laid out
        on a single row two and a half thousand pixels wide, off the side of
        the page, while the levels above sat empty in the middle of it.
        """
        import json, re, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        files = {f"u{i}.py": "import core\ndef go():\n    return core.run()\n"
                 for i in range(30)}
        files["core.py"] = "def run():\n    return 1\n"
        files["main.py"] = ("import u0\nif __name__ == '__main__':\n"
                            "    u0.go()\n")
        t = Tree(files)
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        js = re.findall(r"<script>(.*?)</script>", doc, re.S)[-1]
        stub = """
const made = {};
function fake(id){ return {id: id, textContent: '', innerHTML: '',
  dataset: {}, scrollTop: 0, attrs: {},
  get hidden(){ return 'hidden' in this.attrs; },
  setAttribute(k,v){ this.attrs[k]=v; }, removeAttribute(k){ delete this.attrs[k]; },
  addEventListener(){}, scrollIntoView(){}, closest(){ return null; },
  classList:{toggle(){}, remove(){}, contains(){ return false; }},
  querySelectorAll(){ return []; }}; }
function el(id){ if(!made[id]) made[id] = fake(id); return made[id]; }
global.CSS = {escape: s => s};
global.window = {addEventListener(){}, removeEventListener(){}};
global.document = {getElementById: id => el(id), querySelectorAll: () => [],
  querySelector: sel => sel === '.mapwrap' ? el('mapwrap') : null,
  addEventListener(){}};
"""
        probe = """
drawTrace('core');
const svg = document.getElementById('trace');
const cards = [];
const re = /<rect class="card" x="([\\d.]+)" y="([\\d.]+)" width="([\\d.]+)" height="([\\d.]+)"/g;
let m; while((m = re.exec(svg.innerHTML))) cards.push([+m[1], +m[2], +m[3], +m[4]]);
console.log(JSON.stringify({
  cards: cards.length,
  rects: cards,
  rightmost: Math.max.apply(null, cards.map(c => c[0] + c[2])),
  declared: +svg.attrs.width,
  rows: new Set(cards.map(c => c[1])).size}));
"""
        script = os.path.join(t.dir, "wrap.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(stub + "\n" + js + probe)
        r = subprocess.run(["node", script], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-900:])
        seen = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertGreaterEqual(seen["cards"], 31,
                                "the fixture is not wide enough to wrap")
        self.assertLessEqual(seen["rightmost"], seen["declared"],
                             "a card is drawn past the edge of the chart")
        self.assertGreater(seen["rows"], 2,
                           "thirty cards on one level did not wrap onto "
                           "several rows")
        # And nothing sits on top of anything else. Counting rows is not
        # enough: a level that stops advancing lands its rows on the level
        # below, and a level drawn on one line piles every card in it on the
        # same spot -- both leave the row count exactly as it was.
        rects = seen["rects"]
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                apart = (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
                         or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])
                self.assertTrue(apart, f"two cards overlap: {a} {b}")

    def test_a_dead_end_is_left_out_of_the_trace_and_counted(self):
        """A path that stops inside a module is not a route to anywhere.

        Kept separate from the fixture above precisely because that one used
        to contain dead ends without meaning to: a filter tested only by
        accident is a filter nobody has tested.
        """
        import json, re, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        t = Tree({
            "a.py": 'import b\nif __name__ == "__main__":\n    b.go()\n',
            "b.py": "import c\nimport stub\ndef go():\n    return c.run()\n",
            "c.py": "def run():\n    return 1\n",
            "stub.py": "def later():\n    pass\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        payload = json.loads(
            re.search(r"const DATA = (\{.*?\});\n", doc, re.S).group(1)
            .replace("\\u003c", "<").replace("\\u003e", ">"))
        self.assertEqual(payload["stub"]["state"], "deadend",
                         "the fixture has no dead end in it, so the filter is "
                         "not being tested")
        self.assertIn("stub", payload["b"]["uses"])
        js = re.findall(r"<script>(.*?)</script>", doc, re.S)[-1]
        stub_dom = """
const made = {};
function fake(id){ return {id: id, textContent: '', innerHTML: '',
  dataset: {}, scrollTop: 0, attrs: {},
  get hidden(){ return 'hidden' in this.attrs; },
  setAttribute(k,v){ this.attrs[k]=v; }, removeAttribute(k){ delete this.attrs[k]; },
  hasAttribute(k){ return k in this.attrs; },
  addEventListener(){}, scrollIntoView(){}, closest(){ return null; },
  classList:{toggle(){}, remove(){}, contains(){ return false; }},
  querySelectorAll(){ return []; }}; }
function el(id){ if(!made[id]) made[id] = fake(id); return made[id]; }
global.CSS = {escape: s => s};
global.window = {addEventListener(){}, removeEventListener(){}};
global.document = {getElementById: id => el(id), querySelectorAll: () => [],
  querySelector: sel => sel === '.mapwrap' ? el('mapwrap') : null,
  addEventListener(){}};
"""
        probe = """
const counted = drawTrace('b');
console.log(JSON.stringify({counted: counted,
  markup: document.getElementById('trace').innerHTML}));
"""
        script = os.path.join(t.dir, "deadend.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(stub_dom + "\n" + js + probe)
        r = subprocess.run(["node", script], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-900:])
        seen = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(seen["counted"]["left_out"], 1,
                         "the dead end was not left out")
        self.assertNotIn('data-name="stub"', seen["markup"])
        self.assertIn('data-name="c"', seen["markup"],
                      "the filter took a live module with it")

    def test_only_a_click_opens_the_flowchart(self):
        """Run the handlers the page actually wires up.

        Hover used to draw the trace, which REPLACES the chart -- and on a
        grid you have to mouse across in order to scroll it, that meant the
        overview was gone the moment the pointer touched a card, with nothing
        to bring it back. Hover greys out the unrelated cards; the flowchart
        is a click.

        Checked by firing the real listeners against real stand-in nodes and
        reading what happened to them, not by looking for words in the
        script: `toggle('dim', false)` contains the word dim and greys out
        nothing, and an assertion that reads the source cannot tell.
        """
        seen = self._run("""
// The overview's nodes, wired by the page at load. GRAPH_NODES is what
// document.querySelectorAll('#graph .node') handed it.
const by = {};
GRAPH_NODES.forEach(n => { by[n.dataset.name] = n; });
by['b'].on.mouseenter();
const dimmed = GRAPH_NODES.filter(n => n.cls.has('dim')).map(n => n.dataset.name);
const tracedOnHover = !document.getElementById('graph').hidden;
by['b'].on.mouseleave();
const afterLeave = GRAPH_NODES.filter(n => n.cls.has('dim')).map(n => n.dataset.name);
by['b'].on.click();
console.log(JSON.stringify({
  dimmed: dimmed.sort(), afterLeave: afterLeave,
  overviewSurvivedHover: tracedOnHover,
  overviewGoneAfterClick: document.getElementById('graph').hidden,
  of: document.getElementById('traceof').textContent}));
""", graph_nodes=["a", "b", "c", "d", "island"])
        # b imports c and d and is imported by a, so only the island greys.
        self.assertEqual(seen["dimmed"], ["island"],
                         "hovering greyed out the wrong cards")
        self.assertEqual(seen["afterLeave"], [],
                         "the grey did not lift when the pointer left")
        self.assertTrue(seen["overviewSurvivedHover"],
                        "hovering still replaces the whole map")
        self.assertTrue(seen["overviewGoneAfterClick"],
                        "clicking no longer opens the flowchart")
        self.assertEqual(seen["of"], "b")

    def test_the_flowchart_keeps_only_the_associated_modules(self):
        seen = self._run("""
enterTrace('b', true);
const shown = [];
const re = /data-name="([^"]+)"/g; let m;
const markup = document.getElementById('trace').innerHTML;
while((m = re.exec(markup))) shown.push(m[1]);
console.log(JSON.stringify({shown: shown.sort(),
  graph: document.getElementById('graph').hidden,
  of: document.getElementById('traceof').textContent}));
""")
        self.assertEqual(seen["shown"], ["a", "b", "c", "d"],
                         "the flowchart kept something unassociated, or lost "
                         "something associated")
        self.assertEqual(seen["graph"], True,
                         "the rest of the map is still drawn behind it")
        self.assertEqual(seen["of"], "b")

    def test_entering_a_trace_hides_the_overview_and_says_what_it_left_out(self):
        seen = self._run("""
enterTrace('b');
const after = {graph: document.getElementById('graph').hidden,
               trace: document.getElementById('trace').hidden,
               bar: document.getElementById('tracebar').hidden,
               of: document.getElementById('traceof').textContent,
               count: document.getElementById('tracecount').textContent};
leaveTrace();
const back = {graph: document.getElementById('graph').hidden,
              trace: document.getElementById('trace').hidden,
              bar: document.getElementById('tracebar').hidden};
console.log(JSON.stringify({after: after, back: back}));
""")
        self.assertEqual(seen["after"]["graph"], True,
                         "the overview is still drawn behind the trace")
        self.assertEqual(seen["after"]["trace"], False)
        self.assertEqual(seen["after"]["bar"], False)
        self.assertEqual(seen["after"]["of"], "b")
        self.assertIn("out of 5 modules", seen["after"]["count"],
                      "the bar does not say what was left out")
        self.assertEqual(seen["back"], {"graph": False, "trace": True,
                                        "bar": True},
                         "leaving the trace did not restore the whole map")

    def test_the_trace_draws_the_same_card_text_as_the_overview(self):
        """One truncation rule, applied in Python, used by both views."""
        seen = self._run("""
drawTrace('b');
console.log(JSON.stringify({markup: document.getElementById('trace').innerHTML,
                            card: DATA['b'].card}));
""")
        self.assertIsNotNone(seen["card"], "the card text never reached the payload")
        self.assertIn(seen["card"]["name"], seen["markup"])
        self.assertIn(seen["card"]["meta"], seen["markup"])
        self.assertEqual(seen["card"]["name"], "b.py")


class TestConventions(unittest.TestCase):
    """Files a named tool loads without importing them.

    Every near-miss here has been checked the hard way: the clause that is
    supposed to reject it is removed, and the fixture must then start
    matching. A negative corpus that was never seen to go red proves nothing
    about the pattern it claims to guard.
    """

    POSITIVE = [
        ("tests/conftest.py", "conftest", "pytest"),
        ("tests/test_map.py", "test module", "a test runner"),
        ("tests/map_test.py", "test module", "a test runner"),
        ("pkg/__init__.py", "package marker", "the import system"),
        ("pkg/__main__.py", "module entry", "python -m"),
        ("setup.py", "build script", "pip / setuptools"),
        ("site/wsgi.py", "server entry", "a WSGI server"),
        ("site/asgi.py", "server entry", "an ASGI server"),
        ("manage.py", "management CLI", "Django"),
        ("noxfile.py", "task file", "nox"),
        ("app/migrations/0004_add_column.py", "migration", "a migration runner"),
        ("sitecustomize.py", "interpreter hook", "CPython"),
    ]

    # Each of these looks like one of the rules above and is not one.
    NEAR_MISSES = [
        "conftest_helpers.py",      # conftest is an exact name, not a prefix
        "tests/testing_utils.py",   # `test_` prefix, not `testing`
        "src/latest_run.py",        # contains "test" but does not end `_test`
        "src/protest.py",           # ends in "test" without the underscore
        "docs/manage_users.py",     # manage.py is an exact name
        "src/migrations_helper.py", # a file NAMED migrations..., not one IN it
        "src/setup_logging.py",
        "wsgi_config.py",
    ]

    def test_every_rule_matches_the_file_it_describes(self):
        from cobblerpy import conventions
        for relpath, name, loader in self.POSITIVE:
            found = conventions.conventional(relpath)
            self.assertIsNotNone(found, f"{relpath} matched nothing")
            self.assertEqual((found.name, found.loader), (name, loader), relpath)

    def test_the_near_misses_are_rejected(self):
        from cobblerpy import conventions
        matched = {p: conventions.conventional(p) for p in self.NEAR_MISSES}
        wrong = {p: c.name for p, c in matched.items() if c}
        self.assertEqual(wrong, {}, f"near-misses that matched: {wrong}")

    def test_each_near_miss_is_rejected_by_the_clause_it_is_testing(self):
        """Remove the guard and the fixture must start matching.

        Without this the negative corpus can be green for the wrong reason:
        a fixture excluded by some OTHER rule proves nothing about the clause
        it was written for, and the day that clause is loosened nothing here
        goes red.
        """
        import re
        from cobblerpy import conventions
        # (fixture, the anchored pattern that rejects it, the same pattern
        #  with its anchor removed -- under which it must match)
        cases = [
            ("tests/testing_utils.py", r"test_[^/\\]*\.py\Z", r"test"),
            ("src/latest_run.py",      r"[^/\\]*_test\.py\Z",  r"test"),
            ("src/protest.py",         r"[^/\\]*_test\.py\Z",  r"test"),
            ("conftest_helpers.py",    r"conftest\.py\Z",        r"conftest"),
        ]
        for relpath, guard, loosened in cases:
            base = os.path.basename(relpath)
            self.assertIsNone(re.match(guard, base),
                              f"{relpath}: the guard {guard} does not reject it, "
                              "so this fixture is testing something else")
            self.assertTrue(re.search(loosened, base),
                            f"{relpath}: it does not match even loosened, so "
                            "the guard is not what rejects it")

    def test_a_directory_rule_needs_the_file_to_be_inside_it(self):
        from cobblerpy import conventions
        self.assertIsNotNone(conventions.conventional("app/migrations/0001.py"))
        self.assertIsNone(conventions.conventional("app/migrations.py"))
        self.assertIsNone(conventions.conventional("migrations.py"),
                          "a file NAMED migrations is not a file IN migrations")

    def test_a_declared_entry_point_beats_a_guess(self):
        """Packaging metadata is read, not inferred."""
        from cobblerpy import conventions
        t = Tree({
            "pyproject.toml": '[project]\nname = "demo"\nversion = "1"\n'
                              '[project.scripts]\n'
                              'demo = "demo.cli:main"\n'
                              '[project.entry-points."demo.plugins"]\n'
                              'alpha = "demo.plug_alpha"\n',
            "demo/__init__.py": "",
            "demo/cli.py": "def main():\n    pass\n",
            "demo/plug_alpha.py": "def go():\n    pass\n",
            "demo/nobody.py": "def go():\n    pass\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        held = s.project.convention_reached
        self.assertIn("demo.cli", held)
        self.assertTrue(held["demo.cli"].proven,
                        "a console script is read from the file, not guessed")
        self.assertIn("pyproject.toml", held["demo.cli"].why)
        self.assertIn("demo.plug_alpha", held)
        self.assertIn("demo.plugins", held["demo.plug_alpha"].loader,
                      "the plugin group that will import it is not named")
        # And the module nothing declares is still reported.
        self.assertIn("demo.nobody", s.project.orphans)

    def test_setup_cfg_entry_points_are_read_too(self):
        t = Tree({
            "setup.cfg": "[options.entry_points]\nconsole_scripts =\n"
                         "    demo = demo.run:main\n",
            "demo/__init__.py": "",
            "demo/run.py": "def main():\n    pass\n",
            "demo/spare.py": "x = 1\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        self.assertIn("demo.run", s.project.convention_reached)
        self.assertNotIn("demo.run", s.project.orphans)
        self.assertIn("demo.spare", s.project.orphans)

    def test_a_malformed_pyproject_says_nothing_rather_than_failing(self):
        """A survey of somebody else's tree must survive their broken files."""
        t = Tree({"pyproject.toml": "[project\nthis is not toml at all",
                  "demo/__init__.py": "", "demo/spare.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()                     # must not raise
        self.assertIn("demo.spare", s.project.orphans)

    def test_conftest_is_not_an_orphan_and_says_who_loads_it(self):
        """The case that started this: pytest loads it, nothing imports it."""
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "tests/conftest.py": "import pytest\n"})
        self.addCleanup(t.close)
        s = t.survey()
        key = next(k for k in s.modules_by_key if k.endswith("conftest"))
        self.assertNotIn(key, s.project.orphans)
        self.assertEqual(s.project.convention_reached[key].loader, "pytest")

    def test_nothing_is_both_held_back_and_reported_as_an_orphan(self):
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "tests/conftest.py": "x = 1\n",
                  "tests/test_it.py": "x = 1\n",
                  "pkg/__init__.py": "",
                  "pkg/stranded.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        held = set(s.project.convention_reached)
        self.assertEqual(held & set(s.project.orphans), set())
        self.assertTrue(held, "nothing was held back, so this compares nothing")
        self.assertIn("pkg.stranded", s.project.orphans,
                      "a genuine orphan was swallowed by the exclusions")


class TestConventionsOnTheMap(unittest.TestCase):
    """The map has to justify every exclusion it makes."""

    def _write(self, tree):
        s = tree.survey()
        out = os.path.join(tree.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            return s, fh.read()

    def test_the_map_says_how_many_it_held_back_and_who_loads_them(self):
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "tests/conftest.py": "x = 1\n",
                  "tests/test_a.py": "x = 1\n",
                  "tests/test_b.py": "x = 1\n"})
        self.addCleanup(t.close)
        s, doc = self._write(t)
        page = Page(doc)
        text = " ".join(page.text_parts)
        held = s.project.convention_reached
        self.assertEqual(len(held), 3, f"fixture held back {sorted(held)}")
        self.assertIn("3 modules are loaded by something other than an import",
                      text)
        self.assertIn("pytest", text, "the tool doing the loading is not named")
        self.assertIn("a test runner", text)

    def test_the_note_is_absent_when_nothing_was_held_back(self):
        """The negative branch, which is where a check like this goes stale."""
        t = Tree({"run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
                  "lib.py": "def go():\n    pass\n"})
        self.addCleanup(t.close)
        s, doc = self._write(t)
        self.assertEqual(s.project.convention_reached, {})
        self.assertNotIn("loaded by something other than an import",
                         " ".join(Page(doc).text_parts))

    def test_a_held_back_module_carries_its_rule_into_the_panel(self):
        """Run the map's script and read what clicking conftest renders."""
        import json, re, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        t = Tree({
            "pyproject.toml": '[project]\nname = "demo"\nversion = "1"\n'
                              '[project.scripts]\ndemo = "demo.cli:main"\n',
            "demo/__init__.py": "",
            "demo/cli.py": "def main():\n    pass\n",
            "demo/spare.py": "x = 1\n",
            "tests/conftest.py": "x = 1\n",
        })
        self.addCleanup(t.close)
        s, doc = self._write(t)
        payload = json.loads(
            re.search(r"const DATA = (\{.*?\});\n", doc, re.S).group(1)
            .replace("\\u003c", "<").replace("\\u003e", ">"))
        declared = payload["demo.cli"]["loaded_by"]
        self.assertIsNotNone(declared, "the declared entry point lost its rule")
        self.assertTrue(declared["proven"])
        self.assertIn("pyproject.toml", declared["why"])
        conf = next(v for k, v in payload.items() if k.endswith("conftest"))
        self.assertEqual(conf["loaded_by"]["loader"], "pytest")
        self.assertFalse(conf["loaded_by"]["proven"],
                         "a convention is an inference and must not read as proof")
        # A module nothing loads carries no claim at all -- otherwise the
        # check above passes on a map that stamps a loader onto everything.
        self.assertIsNone(payload["demo.spare"]["loaded_by"],
                          "a genuine orphan was given a loader")


class TestDesktopSession(unittest.TestCase):
    """The window's decisions, driven with no window attached.

    This is the half that imports nothing outside the standard library, which
    is why it can be tested in the ordinary suite.
    """

    def _session(self, files, git=False):
        from cobblerpy.gui.session import Session
        t = Tree(files, git=git)
        self.addCleanup(t.close)
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        s = Session(out_dir=out)
        return s, t

    SMALL = {
        "app/__init__.py": "",
        "app/main.py": "from app import store\n\n"
                       "if __name__ == '__main__':\n    store.persist({})\n",
        "app/store.py": "def persist(row):\n    return row\n",
    }

    def test_the_index_reads_a_folder_without_parsing_it(self):
        s, t = self._session(self.SMALL)
        index = s.load(t.dir)
        self.assertEqual(index.count, 3)
        self.assertGreater(index.lines, 0)
        self.assertIn("3 modules", index.summary())
        self.assertIn("no git history", index.summary())

    def test_a_git_project_says_so(self):
        s, t = self._session(self.SMALL, git=True)
        self.assertIn("git history", s.load(t.dir).summary())

    def test_the_map_never_lands_inside_the_project(self):
        """Otherwise the next survey reads its own output.

        On a repository it would also turn up as an untracked file in
        somebody's `git status`, which is a rude thing for a read-only tool to
        do to a tree it was pointed at.
        """
        from cobblerpy.gui.session import Session
        t = Tree(self.SMALL)
        self.addCleanup(t.close)
        s = Session()                      # no out_dir: the default path
        s.load(t.dir)
        destination = os.path.abspath(s.map_destination())
        self.assertFalse(
            destination.startswith(os.path.abspath(t.dir) + os.sep),
            f"the map would be written inside the project at {destination}")

    def test_a_survey_produces_the_findings_in_reading_order(self):
        s, t = self._session(TestCompetingAttempts.FOUR_ATTEMPTS)
        s.load(t.dir)
        stages = []
        result = s.run(on_stage=stages.append)
        self.assertEqual(stages[0], "reading the source")
        self.assertIn("looking for restarts", stages)
        self.assertTrue(result.attempts, "the restarts were not found")
        self.assertTrue(os.path.isfile(result.map_path))

    def test_the_headline_leads_with_the_restarts(self):
        """Somebody inheriting a codebase is deciding read-or-rewrite.

        Whether the work has already been attempted more than once is what
        decides that, so it goes first -- ahead of module counts.
        """
        s, t = self._session(TestCompetingAttempts.FOUR_ATTEMPTS)
        s.load(t.dir)
        headline = s.run().headline()
        self.assertIn("started over", headline)

    def test_the_headline_falls_back_when_there_are_no_restarts(self):
        s, t = self._session(self.SMALL)
        s.load(t.dir)
        headline = s.run().headline()
        self.assertNotIn("started over", headline)

    def test_turning_the_map_off_writes_no_map(self):
        s, t = self._session(self.SMALL)
        s.load(t.dir)
        s.options.write_map = False
        self.assertIsNone(s.run().map_path)

    def test_a_dropped_file_resolves_to_its_project(self):
        from cobblerpy.gui.session import folders_from_drop
        t = Tree(self.SMALL)
        self.addCleanup(t.close)
        one = os.path.join(t.dir, "app", "store.py")
        self.assertEqual(folders_from_drop([one]),
                         [os.path.abspath(os.path.join(t.dir, "app"))])

    def test_a_fast_survey_does_not_report_zero_seconds(self):
        from cobblerpy.gui.session import Result
        r = Result("/x")
        r.seconds = 0.019
        self.assertIn("ms", r.duration())
        r.seconds = 5.0
        self.assertIn("seconds", r.duration())


class TestDesktopWindow(unittest.TestCase):
    """The window itself, by building it and asking the widget tree.

    Skipped where there is no Qt or no display. What it does NOT do is search
    the source for widget names: it constructs the real thing, drives the real
    handlers and reads the real objects back, because a test that matches
    strings tells you how the file is spelled and nothing about what it builds.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except ImportError:
            raise unittest.SkipTest("PySide6 not installed")
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise unittest.SkipTest("no display")
        cls.qt = (QtCore, QtGui, QtWidgets)
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _window(self, files):
        from cobblerpy.gui.qt_app import build
        from cobblerpy.gui.session import Session
        t = Tree(files)
        self.addCleanup(t.close)
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        window = build(self.qt, Session(out_dir=out))
        # SHOWN, because isVisible() is False for every widget in a window
        # that was never shown -- so an assertion about what a person can see
        # passes on an empty window and proves nothing.
        window.show()
        self.app.processEvents()
        self.addCleanup(window.close)
        self.addCleanup(window.deleteLater)
        return window, t

    def test_survey_is_refused_until_a_project_is_loaded(self):
        window, _t = self._window(TestDesktopSession.SMALL)
        self.assertFalse(window.run_btn.isEnabled())

    def test_loading_a_project_enables_the_survey_and_shows_the_count(self):
        window, t = self._window(TestDesktopSession.SMALL)
        window._load([t.dir])
        self.assertTrue(window.run_btn.isEnabled())
        self.assertIn("3 modules", window.stat.text())
        self.assertEqual(window.path.text(), os.path.abspath(t.dir))

    def test_a_bad_drop_is_reported_and_changes_nothing(self):
        window, _t = self._window(TestDesktopSession.SMALL)
        window._load(["/definitely/not/here"])
        self.assertFalse(window.run_btn.isEnabled())
        self.assertIn("does not exist", window.status.text())

    def test_the_findings_panel_stays_hidden_until_there_is_something(self):
        window, t = self._window(TestDesktopSession.SMALL)
        window._load([t.dir])
        self.assertFalse(window.rightScroll.isVisible())

    def test_a_finished_survey_puts_the_restarts_in_the_widget_tree(self):
        """Read the labels Qt actually built, not the source that built them."""
        from PySide6 import QtWidgets
        window, t = self._window(TestCompetingAttempts.FOUR_ATTEMPTS)
        window._load([t.dir])
        result = window.session.run()
        window._finish(result)
        texts = [w.text() for w in window.findChildren(QtWidgets.QLabel)]
        joined = "\n".join(texts)
        self.assertIn("THE SAME JOB, STARTED OVER", joined)
        self.assertIn("RESUME HERE", joined)
        self.assertTrue(any("claims_v3" in t for t in texts),
                        "the winning attempt is not on screen")
        self.assertTrue(window.map_btn.isEnabled())

    def test_starting_over_clears_the_previous_findings(self):
        from PySide6 import QtWidgets
        window, t = self._window(TestCompetingAttempts.FOUR_ATTEMPTS)
        window._load([t.dir])
        window._finish(window.session.run())
        self.assertIn("RESUME HERE",
                      "\n".join(w.text() for w in
                                window.findChildren(QtWidgets.QLabel)))
        window._clear()
        # deleteLater() is deferred: without draining that queue the widgets
        # are still in the tree and this asserts nothing. isHidden() is also
        # the wrong question -- it is False for a child whose PARENT is
        # hidden, so the panel can be off-screen while every label in it
        # reports itself as shown. isVisible() is the one that means "a person
        # can see this".
        from PySide6.QtCore import QEvent
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        remaining = "\n".join(w.text() for w in
                              window.findChildren(QtWidgets.QLabel)
                              if w.isVisible())
        self.assertNotIn("RESUME HERE", remaining)
        self.assertFalse(window.rightScroll.isVisible())
        # NOT covered: that the old widgets are actually destroyed. Hiding the
        # panel makes every child invisible, so this passes with the
        # deleteLater() removed. That is the right answer for what a PERSON
        # sees, and leaking them would be a memory problem rather than a
        # visible one -- but the distinction is worth stating rather than
        # leaving somebody to assume this test covers both.


class TestCompetingAttempts(unittest.TestCase):
    """Four restarts of one job, which is the case this was built for."""

    FOUR_ATTEMPTS = {
        "app/__init__.py": "",
        "app/main.py": "from app import claims_v3\n\n"
                       "if __name__ == '__main__':\n    claims_v3.serve()\n",
        # oldest, barely started, and holds the only tests
        "app/intake.py": (
            "import json\n\n"
            "def parse_claim(raw):\n    return json.loads(raw)\n\n"
            "def validate_claim(claim):\n    pass\n\n"
            "def submit_claim(claim):\n    pass\n"),
        # a restart, further along
        "app/intake_new.py": (
            "import json\n\n"
            "def parse_claim(raw):\n    return json.loads(raw)\n\n"
            "def validate_claim(claim):\n    return True\n\n"
            "def normalise_codes(claim):\n    pass\n\n"
            "def submit_claim(claim):\n    raise NotImplementedError\n"),
        # the furthest along, and the one main actually imports
        "app/claims_v3.py": (
            "import json\n\n"
            "def parse_claim(raw):\n    return json.loads(raw)\n\n"
            "def validate_claim(claim):\n    return True\n\n"
            "def normalise_codes(claim):\n    return claim\n\n"
            "def price_claim(claim):\n    return claim\n\n"
            "def submit_claim(claim):\n    pass\n\n"
            "def serve():\n    return True\n"),
        "tests/__init__.py": "",
        "tests/test_intake.py": (
            "from app import intake\n\n"
            "def test_parse():\n    assert intake.parse_claim('{}') == {}\n\n"
            "def test_validate():\n    assert intake.validate_claim({}) is None\n"),
    }

    def _groups(self, files):
        from cobblerpy.attempts import find
        t = Tree(files)
        self.addCleanup(t.close)
        s = t.survey()
        return find(s.project, s.modules_by_key, s.origins)

    def test_the_restarts_are_found_as_one_group(self):
        groups = self._groups(self.FOUR_ATTEMPTS)
        self.assertEqual(len(groups), 1, "the restarts were not grouped")
        self.assertEqual(len(groups[0]["attempts"]), 3,
                         [a["module"] for a in groups[0]["attempts"]])
        self.assertIn("submit_claim", groups[0]["shared"])

    def test_the_furthest_along_attempt_is_named(self):
        """The one main imports, with the most bodies filled in."""
        groups = self._groups(self.FOUR_ATTEMPTS)
        self.assertEqual(groups[0]["resume_at"], "app.claims_v3")
        percents = [a["percent"] for a in groups[0]["attempts"]]
        self.assertEqual(percents, sorted(percents, reverse=True),
                         "attempts are not ordered by how far along they are")

    def test_the_gap_in_the_best_attempt_is_named(self):
        """It has to say what is still missing, or it is only a ranking."""
        groups = self._groups(self.FOUR_ATTEMPTS)
        facts = " ".join(groups[0]["resume_facts"])
        self.assertIn("submit_claim", facts)
        self.assertIn("reachable from an entry point", facts)

    def test_what_the_others_have_is_carried_across(self):
        """intake.py holds the only tests; that must not be lost with it."""
        groups = self._groups(self.FOUR_ATTEMPTS)
        self.assertIn("app.intake", groups[0]["elsewhere"])
        intake = next(a for a in groups[0]["attempts"]
                      if a["module"] == "app.intake")
        self.assertGreater(intake["tests"], 0)

    def test_docstrings_do_not_change_the_ranking(self):
        """Each attempt was written to a different taste.

        Scoring documentation habits ranks the tidiest author rather than the
        furthest-advanced work, which is the opposite of what is wanted.

        The two attempts here are deliberately CLOSE -- four definitions each,
        one stub apart. An earlier version of this test used the four-attempt
        fixture, where the leader is forty points clear and no documentation
        bonus could have flipped it, so it passed with docstrings scored.
        """
        close = {
            "app/__init__.py": "",
            # BOTH are reachable, so reachability cannot separate them and the
            # only thing left between the two scores is how many bodies are
            # filled in -- which is small enough for a documentation bonus to
            # overturn, if one existed.
            "app/main.py": ("from app import ledger\nfrom app import ledger_v2\n\n"
                            "if __name__ == '__main__':\n"
                            "    ledger.post_journal(1)\n"
                            "    ledger_v2.settle_batch(1)\n"),
            "app/ledger.py": (
                '"""Ledger, first pass."""\n\n'
                'def reconcile_ledger(x):\n    """Reconcile it."""\n    return x\n\n'
                'def post_journal(x):\n    """Post it."""\n    return x\n\n'
                'def settle_batch(x):\n    """Settle it."""\n    pass\n\n'
                'def audit_trail(x):\n    """Audit it."""\n    pass\n'),
            "app/ledger_v2.py": (
                "def reconcile_ledger(x):\n    return x\n\n"
                "def post_journal(x):\n    return x\n\n"
                "def settle_batch(x):\n    return x\n\n"
                "def audit_trail(x):\n    pass\n"),
        }
        groups = self._groups(close)
        self.assertEqual(len(groups), 1)
        winner = groups[0]["resume_at"]
        self.assertEqual(winner, "app.ledger_v2",
                         "the fully documented but less finished attempt won")
        spread = (groups[0]["attempts"][0]["percent"]
                  - groups[0]["attempts"][1]["percent"])
        self.assertLess(spread, 25,
                        f"the attempts are {spread} points apart, so this "
                        f"fixture cannot detect a documentation bonus")

    def test_the_wall_every_attempt_hit_is_named(self):
        """Four developers stopped at the same function.

        A list cannot say this: it shows four separate "still stubbed" lines
        and leaves the reader to intersect them. Where they all stopped is
        where the problem actually is, and a fifth attempt will stop there too.
        """
        groups = self._groups(self.FOUR_ATTEMPTS)
        self.assertEqual(groups[0]["common_gaps"], ["submit_claim"])

    def test_a_gap_in_only_some_attempts_is_not_common(self):
        """Otherwise the finding degrades into 'somebody did not finish this'."""
        files = dict(self.FOUR_ATTEMPTS)
        # give the leading attempt a body for the one everybody stubbed
        files["app/claims_v3.py"] = files["app/claims_v3.py"].replace(
            "def submit_claim(claim):\n    pass\n",
            "def submit_claim(claim):\n    return True\n")
        groups = self._groups(files)
        self.assertEqual(groups[0]["common_gaps"], [],
                         "a gap one attempt closed is still being called common")

    def test_the_lineage_reads_as_a_sequence(self):
        """Each attempt kept the core and reached for one more thing."""
        groups = self._groups(self.FOUR_ATTEMPTS)
        lineage = groups[0]["lineage"]
        self.assertEqual([step["module"] for step in lineage],
                         ["app.intake", "app.intake_new", "app.claims_v3"])
        self.assertIn("parse_claim", lineage[0]["added"])
        self.assertEqual(lineage[1]["added"], ["normalise_codes"])
        self.assertIn("price_claim", lineage[2]["added"])

    def test_the_lineage_reports_what_a_later_attempt_dropped(self):
        """A newer attempt abandoning something an older one had is a fact
        worth seeing, not a gap to smooth over."""
        files = dict(self.FOUR_ATTEMPTS)
        files["app/intake_new.py"] += "\ndef audit_claim(claim):\n    return 1\n"
        groups = self._groups(files)
        dropped = [step["dropped"] for step in groups[0]["lineage"]]
        self.assertTrue(any("audit_claim" in d for d in dropped),
                        f"nothing recorded as dropped: {dropped}")

    def test_the_lineage_follows_what_each_attempt_DEFINES_not_its_score(self):
        """The two orderings usually agree, which is why this fixture is odd.

        `narrow` reaches for three things and finishes none of them; `wide`
        does six and finishes them all. Ordered by completeness `wide` comes
        first and the sequence reads backwards -- the work did not shrink from
        six ideas to three. Ordered by what each one defines, it reads the way
        it grew.

        Without a fixture where the two disagree, the ordering clause is
        untested: an earlier version of this passed with it replaced by a
        sort on score.
        """
        files = {
            "app/__init__.py": "",
            "app/main.py": "from app import wide\n\n"
                           "if __name__ == '__main__':\n    wide.settle_batch(1)\n",
            # narrow: THREE definitions, all finished  -> score 0.70
            # wide:   SIX definitions, mostly stubs     -> score 0.20
            #
            # Measured, because the obvious fixture does not work: with narrow
            # stubbed and wide finished, sorting by score ascending and sorting
            # by definition count ascending produce the SAME order, and the
            # mutation survives. These numbers are the ones that separate them.
            "app/narrow.py": ("def reconcile_ledger(x):\n    return x\n\n"
                              "def post_journal(x):\n    return x\n\n"
                              "def settle_batch(x):\n    return x\n"),
            "app/wide.py": ("def reconcile_ledger(x):\n    pass\n\n"
                            "def post_journal(x):\n    pass\n\n"
                            "def settle_batch(x):\n    pass\n\n"
                            "def audit_trail(x):\n    pass\n\n"
                            "def price_lines(x):\n    pass\n\n"
                            "def dispatch_remit(x):\n    return x\n"),
        }
        groups = self._groups(files)
        self.assertEqual(len(groups), 1)
        attempts = groups[0]["attempts"]
        by_lineage = [step["module"] for step in groups[0]["lineage"]]
        # The guard has to compare the lineage against the ordering the WRONG
        # implementation would produce -- ascending score -- not against the
        # attempts list, which is sorted descending and coincides by accident.
        by_score_ascending = [a["module"]
                              for a in sorted(attempts, key=lambda a: a["score"])]
        self.assertNotEqual(by_score_ascending, by_lineage,
                            f"this fixture cannot separate the two orderings: "
                            f"{[(a['module'], a['score'], len(a['defines'])) for a in attempts]}")
        self.assertEqual(by_lineage, ["app.narrow", "app.wide"])

    def test_the_lineage_does_not_depend_on_commit_dates(self):
        """A squashed or rebased history is one timestamp for every file.

        Ordering on dates would collapse the sequence to an arbitrary one;
        ordering on what each attempt DEFINES survives it.
        """
        groups = self._groups(self.FOUR_ATTEMPTS)     # built with no git at all
        self.assertTrue(groups[0]["lineage"],
                        "no lineage without history, so it is date-dependent")

    def test_a_clean_project_reports_nothing(self):
        """The failure that would make this noise rather than a finding."""
        groups = self._groups({
            "app/__init__.py": "",
            "app/main.py": "from app import store\n\n"
                           "if __name__ == '__main__':\n    store.persist({})\n",
            "app/store.py": "def persist(row):\n    return row\n\n"
                            "def fetch_row(key):\n    return key\n\n"
                            "def drop_table(name):\n    return name\n",
            "app/report.py": "def render_html(rows):\n    return rows\n\n"
                             "def render_csv(rows):\n    return rows\n\n"
                             "def summarise(rows):\n    return rows\n",
        })
        self.assertEqual(groups, [])

    GENERIC_TWINS = {
        # FIVE shared names, all of them generic and all long enough to pass
        # the length filter -- so only the generic list itself can reject this.
        # An earlier version shared main/run/setup, of which `run` is too short
        # and the rest too few, so the test passed with the list deleted.
        "a.py": ("def configure():\n    pass\n\ndef validate():\n    pass\n\n"
                 "def process():\n    pass\n\ndef serialize():\n    pass\n\n"
                 "def teardown():\n    pass\n"),
        "b.py": ("def configure():\n    return 1\n\ndef validate():\n    return 1\n\n"
                 "def process():\n    return 1\n\ndef serialize():\n    return 1\n\n"
                 "def teardown():\n    return 1\n"),
    }

    def test_generic_names_alone_do_not_group_two_files(self):
        """Every module configures, validates and processes something."""
        self.assertEqual(self._groups(self.GENERIC_TWINS), [])

    def test_two_shared_names_are_not_enough(self):
        """Two files can share a pair of names by being the same SHAPE.

        Three is the threshold, so a fixture sharing exactly two must produce
        nothing and the same fixture with a third must produce a group --
        otherwise nothing here tests the threshold at all.
        """
        two = {
            "a.py": ("def reconcile_ledger(x):\n    return x\n\n"
                     "def post_journal(x):\n    return x\n\n"
                     "def only_in_a(x):\n    return x\n"),
            "b.py": ("def reconcile_ledger(x):\n    return x\n\n"
                     "def post_journal(x):\n    return x\n\n"
                     "def only_in_b(x):\n    return x\n"),
        }
        self.assertEqual(self._groups(two), [], "two shared names grouped")
        three = dict(two)
        for name in ("a.py", "b.py"):
            three[name] += "\ndef settle_batch(x):\n    return x\n"
        self.assertEqual(len(self._groups(three)), 1,
                         "three shared names did not group")

    def test_a_group_always_carries_the_names_that_made_it_one(self):
        """A merged group used to print with no evidence attached.

        Intersecting pairwise sets as the group grew emptied them: A and B can
        share three names, B and C another three, and all three share nothing.
        """
        groups = self._groups(self.FOUR_ATTEMPTS)
        for group in groups:
            self.assertTrue(group["shared"],
                            "a group was reported with no shared names")


class TestDiversionNoise(unittest.TestCase):
    """A fork is specific to one abandoned effort, or it is not a fork."""

    def _forks(self, files, commits):
        """Build a repo, commit it in stages, and run the fork detector."""
        from cobblerpy.diversion import find as find_forks
        t = Tree(files, git=True)
        self.addCleanup(t.close)
        for stage in commits:
            for name, text in stage.items():
                write(t.dir, name, text)
            t._git("add", "-A")
            t._git("commit", "-q", "-m", "stage")
        s = t.survey(with_history=True)
        return s, find_forks(s.project, s.modules_by_key, s.history, s.frontier)

    def test_a_destination_proposed_for_everything_is_suppressed(self):
        """85% of one repository's stopped modules pointed at one file.

        The similarity gates do not stop a hub: a big file shares vocabulary
        with everything and touches every outside system. Only its SHARE of
        the findings exposes it.
        """
        from cobblerpy.diversion import _HUB_SHARE, _HUB_FLOOR
        self.assertLess(_HUB_SHARE, 0.5, "a hub gate that loose suppresses nothing")
        self.assertGreaterEqual(_HUB_FLOOR, 2,
                                "without a floor, one destination on a small "
                                "project is a hub by arithmetic alone")

    # NOT TESTED HERE: that find() consults the test filter.
    #
    # No synthetic repository built in this file produces a fork at all -- the
    # similarity gates need vocabulary overlap, co-change history and matching
    # effects, which a fixture small enough to write inline does not generate.
    # A test that drove find() over such a fixture asserted "the test module is
    # not in the results" against an empty list, and passed with the filter
    # deleted. A vacuous test is worse than none, so it is gone.
    #
    # The evidence for the filter is a measurement on real repositories, in the
    # commit that added it: on AI_Sec the findings went from 20 to 7 and every
    # one of the removed entries had a test module as its abandoned side.

    def test_the_test_filter_recognises_the_shapes_it_must(self):
        from cobblerpy.diversion import _is_test_module
        for key in ("tests.test_api", "test_thing", "pkg.tests.helpers",
                    "conftest", "pkg.api_test"):
            self.assertTrue(_is_test_module(key), key)
        for key in ("app.main", "pkg.contest", "pkg.latest", "attest"):
            self.assertFalse(_is_test_module(key), key)


class TestDeadEndNoise(unittest.TestCase):
    """What the detector must NOT report, each measured against real code."""

    def _ends(self, files):
        from cobblerpy.deadends import find
        t = Tree(files)
        self.addCleanup(t.close)
        s = t.survey()
        return {d["qualname"] for d in find(s.project, s.modules_by_key, s.origins)}

    def test_a_protocol_method_is_not_a_dead_end(self):
        """An empty body inside a Protocol declares a shape.

        The check looked at the DEFINITION's name -- `get`, `collect`, `run` --
        when the thing that makes it idiomatic is the class it sits in. On one
        real project six Protocol methods ranked above every actual stub.
        """
        found = self._ends({
            "main.py": "import api\n\nif __name__ == '__main__':\n    api.go()\n",
            "api.py": ("from typing import Protocol\n\n"
                       "class Transport(Protocol):\n"
                       "    def get(self, url): ...\n\n"
                       "def go():\n    return 1\n"),
        })
        self.assertNotIn("Transport.get", found)

    def test_an_abstract_method_is_not_a_dead_end(self):
        found = self._ends({
            "main.py": "import api\n\nif __name__ == '__main__':\n    api.go()\n",
            "api.py": ("from abc import ABC, abstractmethod\n\n"
                       "class Base(ABC):\n"
                       "    @abstractmethod\n"
                       "    def collect(self): ...\n\n"
                       "def go():\n    return 1\n"),
        })
        self.assertNotIn("Base.collect", found)

    def test_a_stub_in_a_test_module_is_not_a_dead_end(self):
        """45 of 49 dead-ends across four real codebases were in tests.

        They are fakes and doubles written on purpose. A reader inheriting a
        codebase should never be told to start reading at one, and with them
        in the list they WERE the list.
        """
        found = self._ends({
            "main.py": "import api\n\nif __name__ == '__main__':\n    api.go()\n",
            "api.py": "def go():\n    return 1\n",
            "tests/test_api.py": ("class FakeClient:\n"
                                  "    def send(self):\n        pass\n"),
        })
        self.assertNotIn("FakeClient.send", found)

    def test_an_empty_init_is_not_a_dead_end(self):
        """A class with nothing to initialise writes exactly this."""
        found = self._ends({
            "main.py": "import api\n\nif __name__ == '__main__':\n    api.go()\n",
            "api.py": ("class Holder:\n    def __init__(self):\n        pass\n\n"
                       "def go():\n    return Holder()\n"),
        })
        self.assertNotIn("Holder.__init__", found)

    def test_a_real_stub_is_still_reported(self):
        """Tightening creates false negatives; this is the guard against it."""
        found = self._ends({
            "main.py": "import api\n\nif __name__ == '__main__':\n    api.go()\n",
            "api.py": ("def go():\n    return remediate(1)\n\n"
                       "def remediate(finding):\n    pass\n"),
        })
        self.assertIn("remediate", found)


class TestEvidenceCompleteness(unittest.TestCase):

    def _render_panel(self, page_html, module):
        """Run the map's own script and return what the panel renders.

        The detail is built in the browser now, so a test that reads the
        static HTML tests nothing about it. Skipped where node is absent.
        """
        import json, re as _re, shutil as _sh, subprocess as _sp, tempfile as _tf
        if not _sh.which("node"):
            self.skipTest("node not installed")
        js = _re.findall(r"<script>(.*?)</script>", page_html, _re.S)[-1]
        ids = list(set(_re.findall(r'id="([^"]+)"', page_html)))
        stub = ("const made={};function el(i){if(!made[i])made[i]={id:i,"
                "textContent:'',innerHTML:'',dataset:{},classList:{toggle(){},"
                "remove(){},contains(){return false}},addEventListener(){},"
                "scrollTop:0,scrollIntoView(){},closest(){return null}};"
                "return made[i];}"
                f"const KNOWN=new Set({ids});"
                "global.CSS={escape:s=>s};"
                # A browser always has `window`. Three hand-written stubs is
                # how a new global gets added to the script and two of them
                # start failing on the next unrelated change.
                "global.window={addEventListener(){},removeEventListener(){}};"
                "global.document={getElementById:i=>KNOWN.has(i)?el(i):null,"
                "querySelectorAll:()=>[],querySelector:()=>null,"
                "addEventListener(){}};")
        probe = (f"\nopenModule({json.dumps(module)});"
                 "\nprocess.stdout.write(document.getElementById('pbody').innerHTML);")
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False,
                                    encoding="utf-8") as fh:
            fh.write(stub + "\n" + js + probe)
            path = fh.name
        try:
            r = _sp.run(["node", path], capture_output=True, text=True, timeout=300)
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 0, r.stderr[:400])
        return r.stdout

    """A chip's count and the evidence behind it must not disagree silently."""

    def test_a_truncated_evidence_list_says_how_many_it_left_out(self):
        """The list is capped at six per kind; the chip shows the true count.

        A reader who opens the evidence to check a chip reading "commented
        code 7" counted six lines and could not tell whether the count was
        wrong or the list was short.
        """
        body = "\n".join(f"# x = {i}" for i in range(9))
        t = Tree({
            "main.py": "import mod\n\nif __name__ == '__main__':\n    mod.go()\n",
            "mod.py": f"{body}\n\ndef go():\n    return 1\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            page = Page(fh.read())
        # The evidence moved onto the module's own panel when the tables
        # below the chart went away; it is rendered by the click handler from
        # the payload, so the payload is where it can be verified.
        import json as _json, re as _re
        with open(out, encoding="utf-8") as fh:
            raw = fh.read()
        data = _json.loads(_re.search(r"const DATA = (\{.*?\});\n", raw, _re.S)
                           .group(1).replace("\\u003c", "<").replace("\\u003e", ">"))
        hits = data["mod"]["signals"].get("commented_code", [])
        self.assertEqual(len(hits), 9, "the fixture changed")
        # Rendered by the click handler, so the handler is what gets run. The
        # count is in the payload either way; what needs proving is that the
        # panel SAYS how many it left out.
        rendered = self._render_panel(raw, "mod")
        # Counted by CLASS, not by the signal's name: the source snippet
        # below carries a marker on each annotated line, so counting the name
        # counted the evidence lines plus every marker in the code.
        # Scoped to THIS signal kind. Counting every evidence line counted the
        # module's lone no_docstring hit as well, which is a seventh line and
        # nothing to do with the cap being tested here.
        self.assertEqual(rendered.count('class="ln ev">commented_code:'), 6,
                         "the cap itself changed; update this test")
        self.assertIn("and 3 more commented code", rendered)

    def test_an_untruncated_list_says_nothing_extra(self):
        t = Tree({
            "main.py": "import mod\n\nif __name__ == '__main__':\n    mod.go()\n",
            "mod.py": "# x = 1\n# y = 2\n\ndef go():\n    return 1\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            page = Page(fh.read())
        self.assertNotIn("more commented code", page.text)


class TestExport(unittest.TestCase):
    def _graph(self, tree):
        from cobblerpy.layout import compute
        s = tree.survey()
        return compute(s.project, {r["module"]: r for r in s.frontier}), s

    def test_mermaid_is_valid_flowchart_source(self):
        from cobblerpy.export import to_mermaid
        t = Tree({"run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
                  "lib.py": "def go(): return 1\n"})
        self.addCleanup(t.close)
        graph, s = self._graph(t)
        out = to_mermaid(graph, s.project)
        self.assertTrue(out.startswith("flowchart LR"))
        self.assertIn("-->", out)
        self.assertIn("classDef", out)
        # ids must not carry dots or dashes, which mermaid rejects
        import re
        for node_id in re.findall(r"^\s+(n_\S+)\[", out, re.M):
            self.assertNotIn(".", node_id)
            self.assertNotIn("-", node_id)

    def test_drawio_is_wellformed_xml_with_geometry(self):
        import xml.etree.ElementTree as ET
        from cobblerpy.export import to_drawio
        t = Tree({"run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
                  "lib.py": "def go(): return 1\n"})
        self.addCleanup(t.close)
        graph, s = self._graph(t)
        root = ET.fromstring(to_drawio(graph, s.project))
        self.assertEqual(root.tag, "mxfile")
        cells = root.findall(".//mxCell")
        vertices = [c for c in cells if c.get("vertex")]
        self.assertTrue(vertices)
        for cell in vertices:
            self.assertIsNotNone(cell.find("mxGeometry"))

    def test_mermaid_truncates_rather_than_emitting_something_unreadable(self):
        from cobblerpy.export import to_mermaid
        files = {f"m{i}.py": f"x = {i}\n" for i in range(30)}
        files["run.py"] = 'if __name__ == "__main__":\n    pass\n'
        t = Tree(files)
        self.addCleanup(t.close)
        graph, s = self._graph(t)
        out = to_mermaid(graph, s.project, max_nodes=5)
        self.assertIn("%%", out)              # says it truncated
        self.assertIn("of 31 total", out.replace("of  31", "of 31"))


class TestDiversion(unittest.TestCase):
    def test_no_history_means_no_guessing(self):
        from cobblerpy.diversion import find
        t = Tree({"a.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        self.assertEqual(find(s.project, s.modules_by_key, s.history), [])

    def test_a_settled_module_is_not_reported_as_abandoned(self):
        """Finished code is also quiet. This was the second attempt's flaw."""
        from cobblerpy.diversion import find
        t = Tree({
            "run.py": 'import done\nif __name__ == "__main__":\n    done.go()\n',
            "done.py": '"""Complete."""\n\n\ndef go():\n    """Returns one."""\n    return 1\n',
        }, git=True)
        self.addCleanup(t.close)
        s = survey(t.dir)
        forks = find(s.project, s.modules_by_key, s.history, s.frontier)
        self.assertFalse([f for f in forks if f["stopped"] == "done"])

    def test_test_modules_are_never_the_destination(self):
        """A test shares vocabulary with everything and follows all effort."""
        from cobblerpy.diversion import _is_test
        t = Tree({"tests/test_thing.py": "def test_x(): pass\n"})
        self.addCleanup(t.close)
        s = t.survey()
        for key, module in s.modules_by_key.items():
            self.assertTrue(_is_test(key, module), key)

    def test_generic_signals_alone_are_not_enough(self):
        """package+effects is true of half a codebase; it cannot carry a claim."""
        from cobblerpy.diversion import _SPECIFIC
        self.assertEqual(_SPECIFIC, {"vocabulary", "co-change"})

    def test_every_fork_carries_its_caveat(self):
        from cobblerpy.diversion import find
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n'}, git=True)
        self.addCleanup(t.close)
        s = survey(t.dir)
        for fork in find(s.project, s.modules_by_key, s.history, s.frontier):
            self.assertIn("hypothesis", fork["caveat"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
