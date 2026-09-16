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
        self.assertIn(("os", "os", 1, 0), m.imports)

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
        mods, _ = scan_tree(t.dir)
        self.assertEqual([m.relpath for m in mods], ["a.py"])


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
        self.assertIn(state, ("orphan", "unreached"))
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
        self.assertNotIn('src="http', doc)        # self-contained
        self.assertIn("Proven", doc)              # the proof/inference legend

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
        external = [tag for tag in
                    re.findall(r"<(?:script|link|img|iframe)[^>]*>", markup)
                    if "http" in tag]
        self.assertEqual(external, [])
        self.assertIn('<svg id="graph"', doc)
        self.assertIn('data-name="run"', doc)
        self.assertIn("tabindex=", doc)           # keyboard reachable

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
        t = Tree({"run.py": 'MARKER = "</script><img src=x onerror=alert(1)>"\n'
                            'if __name__ == "__main__":\n    pass\n'})
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        self.assertNotIn("</script><img", doc)
        self.assertNotIn("<img src=x", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
