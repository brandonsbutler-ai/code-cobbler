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
from cobblerpy import origin, history                           # noqa: E402
from cobblerpy.abandonment import analyse_module, score         # noqa: E402
from cobblerpy.clusters import analyse as analyse_clusters, split  # noqa: E402
from cobblerpy.layout import compute, state_of                  # noqa: E402
from cobblerpy.scan import _looks_like_code, scan_file, scan_tree  # noqa: E402


def write(root, relpath, text):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(text).lstrip("\n"))
    return path


def _unlink_quietly(path):
    """Remove a file if it is there. Used to clean maps a test caused to exist."""
    try:
        os.unlink(path)
    except OSError:
        pass


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

    def test_a_file_that_defeats_the_parser_is_a_finding_not_a_crash(self):
        # A huge unary chain overflows the parser stack (MemoryError), which is
        # not a SyntaxError. One such file must not abort a survey of the rest.
        t = Tree({"bad.py": "x = " + "-" * 20000 + "1\n",
                  "fine.py": "def ok():\n    return 1\n"})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "bad.py"), t.dir)
        self.assertTrue(m.error, "a parser-defeating file should record an error")
        # and the whole survey completes rather than raising
        s = survey(t.dir)
        self.assertIn("ok", {d.name for d in s.project.by_dotted["fine"].definitions})

    def test_a_file_python_will_not_decode_is_a_file_that_will_not_parse(self):
        """Not UTF-8 and no coding line: Python refuses to compile it, and
        the scanner quietly decoded it as cp1252 and reported no error."""
        t = Tree({})
        self.addCleanup(t.close)
        with open(os.path.join(t.dir, "raw.py"), "wb") as fh:
            fh.write(b'x = 1\nNAME = "caf\xe9"\n')
        with open(os.path.join(t.dir, "declared.py"), "wb") as fh:
            fh.write(b'# -*- coding: latin-1 -*-\nNAME = "caf\xe9"\n')
        raw = scan_file(os.path.join(t.dir, "raw.py"), t.dir)
        self.assertTrue(raw.error and raw.error.startswith("syntax error at line 2"),
                        raw.error)
        self.assertIn("encoding", raw.error)
        declared = scan_file(os.path.join(t.dir, "declared.py"), t.dir)
        self.assertIsNone(declared.error)

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

    def _flagged(self, source):
        """Line numbers scan() reports as disabled code, for real source."""
        t = Tree({"m.py": source})
        self.addCleanup(t.close)
        module = next(m for m in t.survey().project.modules
                      if m.relpath == "m.py")
        return module.commented_code

    def test_a_trailing_comment_is_an_annotation_not_disabled_code(self):
        """Nobody disables a statement by appending it to a live one.

        `candidates.append(dotted)  # import pkg.module` is a note about the
        line it sits on. All five of this project's own commented-out-code
        findings were of that shape, and 7 of the corpus's 22 -- a finding the
        reader can dismiss in five seconds teaches them to dismiss the rest.
        """
        self.assertEqual(
            self._flagged("candidates = []\n"
                          "candidates.append(dotted)  # import pkg.module\n"),
            [], "a trailing annotation was reported as disabled code")
        # The SAME text, owning its line, is disabled code.
        self.assertEqual(
            self._flagged("candidates = []\n"
                          "# import pkg.module\n"),
            [2], "a whole-line disabled import was not reported")

    def test_the_whole_line_rule_is_what_rejects_the_annotation(self):
        """Prove the clause, not just the outcome.

        The text after the `#` is identical in both cases above, so
        _looks_like_code cannot be what tells them apart -- and if some other
        clause were doing the work, loosening this one would change nothing
        and the test above would stay green for the wrong reason.
        """
        self.assertTrue(_looks_like_code("# import pkg.module"),
                        "the text alone reads as code in both placements, so "
                        "only the position can be deciding it")
        indented = self._flagged("def go():\n"
                                 "    x = 1\n"
                                 "    # x = 2\n"
                                 "    return x\n")
        self.assertEqual(indented, [3],
                         "an indented whole-line comment is still whole-line")


class TestPromisedReturn(unittest.TestCase):
    """A docstring that says THIS function returns something.

    The old rule was the word "returns" anywhere in the text. It fired on 165
    functions in the corpus and every one was a paragraph about what something
    else returns -- and it fed the score that ranks where the work stopped.
    """

    PROMISES = [
        "Return the parsed record.",
        "Returns the parsed record.",
        "Do the thing.\n\nReturns:\n    The parsed record.\n",
        "Do the thing.\n\n:return: the parsed record\n",
        "Do the thing.\n\n:rtype: dict\n",
        "Returns\n-------\nrecord : dict\n",
    ]

    # Prose that mentions a return without promising one.
    NEAR_MISSES = [
        "Run the map's script.\n\nThe stub returns [] from querySelectorAll, "
        "which proves the script parses and nothing more.",
        "Capture the state before the command runs, so rollback returns it.",
        "The previous version returned a bare list; this one writes a file.",
        "Whatever this returns to the caller is ignored by the worker.",
        "Turn the handle.",
    ]

    def test_the_documented_shapes_are_recognised(self):
        from cobblerpy.abandonment import _promises_a_return
        for text in self.PROMISES:
            self.assertTrue(_promises_a_return(text), repr(text[:40]))

    def test_prose_about_returning_is_not_a_promise(self):
        from cobblerpy.abandonment import _promises_a_return
        wrong = [t[:46] for t in self.NEAR_MISSES if _promises_a_return(t)]
        self.assertEqual(wrong, [], f"prose taken as a promise: {wrong}")

    def test_which_clause_rejects_the_prose(self):
        """Loosen it back and the fixtures must start matching.

        Otherwise the near-misses could be failing for some unrelated reason
        and the rule could be loosened without anything going red.
        """
        loose = lambda d: any(w in d.lower()
                              for w in ("returns ", "return:", ":return", "-> "))
        testify = [t for t in self.NEAR_MISSES if loose(t)]
        self.assertGreaterEqual(
            len(testify), 3,
            "too few near-misses reach the new clause; the rest are rejected "
            "by their shape and say nothing about it")
        for text in testify:
            self.assertFalse(
                __import__("cobblerpy.abandonment", fromlist=["x"])
                ._promises_a_return(text),
                f"{text[:40]!r} matched the old rule AND the new one")

    def test_end_to_end_on_real_source(self):
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "m.py": 'def promises():\n'
                          '    """Return the count."""\n'
                          '    print(1)\n\n'
                          'def mentions():\n'
                          '    """Do it.\n\n    The helper returns a list, '
                          'which this ignores.\n    """\n'
                          '    print(2)\n\n'
                          'def keeps_its_word():\n'
                          '    """Return the count."""\n'
                          '    return 3\n'})
        self.addCleanup(t.close)
        s = t.survey()
        row = next(r for r in s.frontier if r["relpath"] == "m.py")
        flagged = [name for name, _line
                   in (row.get("signals") or {}).get("promised_return", [])]
        self.assertEqual(flagged, ["promises"])


class TestTagMarkers(unittest.TestCase):
    """A tag is a marker, not a substring.

    `tag in text.upper()` reported 19 of this project's 20 tagged comments and
    585 of the corpus's 641. Every one of these words hides inside an ordinary
    one, and the findings fed the score that ranks where the work stopped --
    so the ranking was being driven by the word "attempts".
    """

    REAL = [
        ("# TODO: wire up the retry path", "TODO"),
        ("# FIXME(alice): off by one", "FIXME"),
        ("# XXX this cannot be right", "XXX"),
        ("# todo: lower case, but labelled", "TODO"),
        ("# hack(bob): temporary shim", "HACK"),
        ("#TEMP: no space after the hash", "TEMP"),
        # A label (capitals and a colon) after a `--` separator or a pragma.
        ("# Fix later -- TODO: handle unicode", "TODO"),
        ("# -- TODO: wire the retry", "TODO"),
        ("# noqa: E501  TODO: drop when py3.8 gone", "TODO"),
        ("# type: ignore[attr-defined]  FIXME(ann): real stubs", "FIXME"),
        # A list number, a bracket or a Sphinx `#:` before it is not prose.
        ("# 1. TODO finish parsing", "TODO"),
        ("# (TODO) remove this shim", "TODO"),
        ("#: TODO document", "TODO"),
    ]

    # Each of these contains a tag as a SUBSTRING and is not a marker.
    NEAR_MISSES = [
        "# attempts at one job. Two is too loose",     # at-temp-ts
        "# the first attempted fix",                   # at-temp-ted
        "# only WARNING+ unless --debug",              # de-bug
        "# see DESIGN_NOTES.md for the table",         # note-s, no boundary
        "# a note about the line it sits on",          # lower case, no colon
        "# bug wearing a different hat",               # lower case, no colon
        "# swipe left to dismiss",                     # s-wip-e
        "# contemporary style",                        # con-temp-orary
        "# forty TODOs in one file is one situation",  # todo-s, no boundary
        "# see KNOWN_BUGS.md for the list",            # _bug-s, no boundary
        # A NOTE records a decision. It is not work anybody left undone, and
        # it was 56 of the larger corpus's 75 "TODO" findings.
        "# NOTE: gated by `command -v zypper` in the shell",
        "# NOTE the disclosure's open handler",
        # Prose ABOUT a tag. The marker convention puts it first.
        "# a TODO in otherwise complete code is a note, not a hole.",
        "# 301 distinct FIX-XXX tickets and many feature additions",
        "# THE TEMP FILE IS GONE. Nothing survived this handler.",
        # Prose that MENTIONS a label: a label counts only where a comment
        # starts, after `--`, or after a pragma.
        "# WARNING: TODO: comments are counted by the linter",
        "# Format of a marker line is 'TODO: <text>'",
        "# The HACK: prefix is reserved for the build",
        "# see FIX-XXX: ticket 301",
        "# see FIXME(ann): the offset",
        "# a TODO in the docstring says so",
        "# the todo: list lives elsewhere",
        "# see the NOTE: above",
        "# ── 7. BUG FIX SUMMARY ──",
    ]

    def test_the_real_markers_are_found(self):
        from cobblerpy.scan import _tag_of
        for text, tag in self.REAL:
            self.assertEqual(_tag_of(text), tag, text)

    def test_the_near_misses_are_rejected(self):
        from cobblerpy.scan import _tag_of
        found = {t: _tag_of(t) for t in self.NEAR_MISSES}
        wrong = {t: v for t, v in found.items() if v}
        self.assertEqual(wrong, {}, f"substrings taken as markers: {wrong}")

    def test_which_clause_rejects_each_near_miss(self):
        """Remove the guard and the fixture must start matching.

        Two clauses do the work and they reject different fixtures. Without
        this, a fixture rejected by the OTHER one proves nothing about the
        clause it was written for, and loosening that clause goes unnoticed.
        """
        import re
        from cobblerpy.scan import _TODO_TAGS
        loose = re.compile("|".join(_TODO_TAGS), re.IGNORECASE)   # no \b
        bounded = re.compile(r"\b(" + "|".join(_TODO_TAGS) + r")\b", re.IGNORECASE)

        # Rejected by the WORD BOUNDARY: they match without it and not with it.
        for text in ("# attempts at one job. Two is too loose",
                     "# only WARNING+ unless --debug",
                     "# see KNOWN_BUGS.md for the list",
                     "# swipe left to dismiss"):
            self.assertTrue(loose.search(text), text)
            self.assertIsNone(bounded.search(text),
                              f"{text!r} is rejected by something other than "
                              "the word boundary, so it does not test it")

        # Rejected by the CAPITALS-or-colon clause: they survive the boundary.
        for text in ("# bug wearing a different hat",):
            self.assertTrue(bounded.search(text),
                            f"{text!r} never gets as far as the second clause")

        # Rejected by the FIRST-WORD clause: bounded, in capitals, and prose.
        for text in ("# a TODO in otherwise complete code is a note, not a hole.",
                     "# 301 distinct FIX-XXX tickets and many feature additions",
                     "# THE TEMP FILE IS GONE. Nothing survived this handler."):
            m = bounded.search(text)
            self.assertTrue(m and m.group(1).isupper(),
                            f"{text!r} never gets as far as the first-word clause")

    def test_the_evidence_quotes_the_comment_as_written(self):
        """It printed "TODO: TODO: wire up..." -- the tag, then the comment
        that already begins with it -- for a line that says it once."""
        t = Tree({"m.py": "x = 1\n# TODO(ann): wire up the retry path\n"})
        self.addCleanup(t.close)
        m = scan_file(os.path.join(t.dir, "m.py"), t.dir)
        self.assertEqual(analyse_module(m)["todo"],
                         [("TODO(ann): wire up the retry path", 2)])

    def test_a_marker_in_source_is_reported_and_a_substring_is_not(self):
        """End to end, because the rule is only worth what the scan does."""
        t = Tree({"m.py": "# attempts at one job, and a debug helper\n"
                          "x = 1\n"
                          "# TODO: wire up the retry path\n",
                  "run.py": 'if __name__ == "__main__":\n    pass\n'})
        self.addCleanup(t.close)
        s = t.survey()
        module = next(m for m in s.project.modules if m.relpath == "m.py")
        self.assertEqual([(tag, line) for tag, _note, line in module.todos],
                         [("TODO", 3)])


class TestDeliberateImports(unittest.TestCase):
    """`# noqa: F401` means the author meant it.

    An import that exists so a bundler can see the package is real and
    deliberate. Four of this project's own eleven unused-import findings were
    already marked that way and reported anyway -- a finding the reader can
    dismiss in five seconds, after which they start dismissing the others.
    """

    def _unused(self, source):
        t = Tree({"m.py": source, "run.py": 'if __name__ == "__main__":\n    pass\n'})
        self.addCleanup(t.close)
        s = t.survey()
        row = next(r for r in s.frontier if r["relpath"] == "m.py")
        return [text for text, _line
                in (row.get("signals") or {}).get("unused_import", [])]

    def test_an_unmarked_unused_import_is_still_reported(self):
        self.assertEqual(self._unused("import zlib\nx = 1\n"),
                         ["zlib (from zlib)"])

    def test_the_marked_one_is_not(self):
        self.assertEqual(self._unused("import zlib  # noqa: F401\nx = 1\n"), [])
        self.assertEqual(self._unused("import zlib  # noqa\nx = 1\n"), [],
                         "a bare noqa silences everything, so it covers F401")
        self.assertEqual(
            self._unused("from PySide6 import QtCore, QtGui  # noqa: F401\nx = 1\n"),
            [], "the real shape: a bundler import, marked")

    def test_a_different_code_does_not_silence_it(self):
        """The near-miss, and the clause that rejects it.

        `# noqa: E501` is a line-length waiver. If the code list were not
        read, it would silence the import too -- and the test above would
        stay green, because it never asks about any code but F401.
        """
        self.assertEqual(self._unused("import zlib  # noqa: E501\nx = 1\n"),
                         ["zlib (from zlib)"])
        from cobblerpy.scan import _keeps_import
        self.assertTrue(_keeps_import("# noqa: F401"))
        self.assertTrue(_keeps_import("# noqa: E501, F401"))
        self.assertTrue(_keeps_import("# noqa"))
        self.assertFalse(_keeps_import("# noqa: E501"))
        self.assertFalse(_keeps_import("# nothing to do with noqa codes here"),
                         "prose that happens to contain the word")
        self.assertFalse(_keeps_import("# TODO: add noqa here later"))

    def test_the_mark_only_covers_its_own_line(self):
        """Otherwise one waiver anywhere silences the whole file."""
        self.assertEqual(
            self._unused("import zlib  # noqa: F401\nimport gzip\nx = 1\n"),
            ["gzip (from gzip)"])


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


class TestShallowHistory(unittest.TestCase):
    """A shallow clone holds only the newest commits, so every date the
    history layer states -- first seen, the range it was built between, what
    went quiet -- is about what was fetched, not the project. It said so
    nowhere."""

    @classmethod
    def setUpClass(cls):
        cls.full = Tree({"app.py": "x = 1\n", "run.py": "import app\n"}, git=True)
        for n, date in enumerate(("2025-02-01T12:00:00", "2026-03-01T12:00:00")):
            write(cls.full.dir, "app.py", f"x = {n + 2}\n")
            env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
            subprocess.run(["git", "-C", cls.full.dir, "commit", "-qam", f"c{n}"],
                           env=env, capture_output=True, check=True)
        cls.clone = tempfile.mkdtemp()
        subprocess.run(["git", "clone", "-q", "--depth", "1",
                        "file://" + cls.full.dir, cls.clone],
                       capture_output=True, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.full.close()
        shutil.rmtree(cls.clone, ignore_errors=True)

    def test_a_shallow_clone_is_detected_and_a_full_one_is_not(self):
        self.assertTrue(history.summary(self.clone, ["app.py", "run.py"])["shallow"])
        self.assertFalse(history.summary(self.full.dir, ["app.py", "run.py"])["shallow"])

    def test_the_summary_says_the_dates_come_from_a_shallow_clone(self):
        r = subprocess.run([sys.executable, "-m", "cobblerpy", self.clone],
                           capture_output=True, text=True, cwd=REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shallow clone", r.stdout)
        r = subprocess.run([sys.executable, "-m", "cobblerpy", self.full.dir],
                           capture_output=True, text=True, cwd=REPO)
        self.assertNotIn("shallow clone", r.stdout)

    def test_the_map_says_so_too(self):
        from cobblerpy.report import write_map
        s = survey(self.clone)
        out = os.path.join(tempfile.mkdtemp(), "m.html")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        write_map(s.project, s.frontier, s.history, out, origins=s.origins,
                  modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("shallow clone", Page(fh.read()).text)


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

    def test_a_hostile_repo_config_cannot_run_a_command(self):
        # A repo carries its own .git/config, and core.fsmonitor names a program
        # git runs to enumerate changes -- it fires on `ls-files`. Surveying a
        # repo must never run code the repo chose. This is the whole promise.
        t = Tree({"a.py": "x = 1\n"}, git=True)
        self.addCleanup(t.close)
        marker = os.path.join(t.dir, "PWNED")
        config = os.path.join(t.dir, ".git", "config")
        # An explicit [core] header -- Tree() has already written a [user]
        # section, and a bare key appended after it would land under [user]
        # (git ignores user.fsmonitor) and prove nothing.
        with open(config, "a", encoding="utf-8") as fh:
            fh.write('[core]\n\tfsmonitor = "touch %s"\n' % marker)
        # both code paths that shell out to git against the surveyed repo
        origin.tracked_paths(t.dir)
        history.is_repo(t.dir)
        history.rename_map(t.dir)
        survey(t.dir, with_history=True)
        self.assertFalse(os.path.exists(marker),
                         "surveying the repo executed its fsmonitor command")


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

    def test_json_carries_the_restarts_dead_ends_and_forks_the_map_shows(self):
        """--json is "everything, machine-readable", and it had none of the
        three findings the map and the summary lead with."""
        import json
        from cobblerpy.attempts import find as find_attempts
        from cobblerpy.deadends import find as find_deadends
        from cobblerpy.diversion import find as find_forks
        # A history with a FORK in it: the invoice renderer is started once
        # and left, while a writer doing the same work carries on for three
        # more commits. Without one, "forks" could be missing from the JSON
        # and this test would still have passed on an empty list.
        t = Tree({
            "main.py": "import svc\nfrom pkg import invoice_writer\n\n"
                       "if __name__ == '__main__':\n    svc.caller(1)\n"
                       "    invoice_writer.write_invoice_pdf([])\n",
            "svc.py": "def remediate(f):\n    pass\n\n\ndef report(f):\n"
                      "    return str(f)\n\n\ndef caller(f):\n    return remediate(f)\n",
            "ingest.py": "def parse_record(r):\n    return r\n\n\n"
                         "def validate_record(r):\n    pass\n\n\n"
                         "def store_record(r):\n    pass\n",
            "ingest_v2.py": "def parse_record(r):\n    return dict(r)\n\n\n"
                            "def validate_record(r):\n    return True\n\n\n"
                            "def store_record(r):\n    pass\n",
            "pkg/__init__.py": "",
            "pkg/invoice_render.py": "def render_invoice_pdf(rows):\n    pass\n\n\n"
                                     "def invoice_pdf_layout(rows):\n    pass\n",
            "pkg/invoice_writer.py": "def write_invoice_pdf(rows):\n    return rows\n",
        }, git=True)
        for n, extra in enumerate(("pages", "totals", "footer"), start=2):
            write(t.dir, "pkg/invoice_writer.py",
                  f"def write_invoice_pdf(rows):\n    return rows\n\n\n"
                  f"def invoice_pdf_{extra}(rows):\n    return rows\n")
            write(t.dir, f"pkg/util_{extra}.py", "X = 1\n")
            when = f"2030-01-{n:02d}T12:00:00"
            subprocess.run(["git", "-C", t.dir, "add", "-A"], check=True)
            subprocess.run(["git", "-C", t.dir, "commit", "-qm", f"writer: {extra}"],
                           env=dict(os.environ, GIT_AUTHOR_DATE=when,
                                    GIT_COMMITTER_DATE=when), check=True)
        self.addCleanup(t.close)
        out = os.path.join(t.dir, "s.json")
        r = subprocess.run([sys.executable, "-m", "cobblerpy", t.dir, "--json", out],
                           capture_output=True, text=True, cwd=REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as fh:
            data = json.load(fh)
        s = t.survey(with_history=True)
        expect = {
            "attempts": find_attempts(s.project, s.modules_by_key, s.origins),
            "deadends": find_deadends(s.project, s.modules_by_key, s.origins),
            "forks": find_forks(s.project, s.modules_by_key, s.history, s.frontier),
        }
        self.assertTrue(expect["attempts"] and expect["deadends"] and expect["forks"],
                        "the fixture no longer has the findings it is for")
        self.assertEqual([f["stopped"] for f in data["forks"]], ["pkg.invoice_render"])
        for key, value in expect.items():
            self.assertEqual(data.get(key), json.loads(json.dumps(value, default=str)),
                             key)

    def test_an_empty_directory_fails_clearly(self):
        t = Tree({"notes.txt": "no python here\n"})
        self.addCleanup(t.close)
        r = subprocess.run([sys.executable, "-m", "cobblerpy", t.dir],
                           capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(
                               os.path.abspath(__file__))))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no Python files", r.stderr)


class TestCommandLineEdges(unittest.TestCase):
    """Small things the command line got wrong when it was used for real."""

    def _cli(self, *args):
        return subprocess.run([sys.executable, "-m", "cobblerpy", *args],
                              capture_output=True, text=True, cwd=REPO, timeout=120)

    def test_a_missing_path_is_called_missing(self):
        r = self._cli(os.path.join(tempfile.gettempdir(), "no-such-project-xyz"),
                      "--no-history")
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not exist", r.stderr)

    def test_a_file_surveys_the_project_it_is_in_as_cobble_does(self):
        t = Tree({"pyproject.toml": "", "pkg/__init__.py": "",
                  "pkg/a.py": "x = 1\n", "b.py": "import pkg.a\n"})
        self.addCleanup(t.close)
        r = self._cli(os.path.join(t.dir, "pkg", "a.py"), "--no-history")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3 modules", r.stdout)

    def test_an_empty_heading_is_not_printed(self):
        t = Tree({"only.py": "x = 1\n"})
        self.addCleanup(t.close)
        r = self._cli(t.dir, "--no-history")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("MOST DEPENDED UPON", r.stdout)

    def test_long_text_is_shortened_at_a_word_not_through_one(self):
        from cobblerpy.__main__ import _clip
        text = "shares sign, signer, unsign, validate; both in src.itsdangerous"
        self.assertEqual(_clip(text, 56), "shares sign, signer, unsign, validate; both in ...")
        self.assertEqual(_clip("short", 56), "short")

    def test_the_windows_launcher_finds_its_checkout_from_where_it_is(self):
        """It named one machine's W:\\...\\cobblerpy. Read, not run: there
        is no cmd.exe here, so this is the only instrument available."""
        import re
        with open(os.path.join(REPO, "packaging", "CodeCobbler.bat"),
                  encoding="utf-8") as fh:
            bat = fh.read()
        self.assertEqual(re.findall(r"\b[A-Za-z]:\\\S+", bat), [])
        self.assertIn("%~dp0", bat)

    def test_the_code_states_rules_not_who_made_them(self):
        """A public repository: a rule is written as the rule. The design
        notes attribute decisions on purpose and are not checked here."""
        name, asked = "Bran" + "don", "he " + "asked"
        found = []
        for d in ("cobblerpy", "packaging", "tests"):
            for dirpath, dirs, files in os.walk(os.path.join(REPO, d)):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for f in files:
                    if f.endswith((".py", ".sh", ".bat")):
                        with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                            for n, line in enumerate(fh, 1):
                                if name in line or asked in line:
                                    found.append(f"{f}:{n}")
        self.assertEqual(found, [])

    def test_what_pip_install_leaves_in_the_clone_is_ignored(self):
        """`pip install .` writes build/ and cobblerpy.egg-info/ into the
        checkout; the second showed up in git status."""
        if subprocess.run(["git", "-C", REPO, "rev-parse", "--git-dir"],
                          capture_output=True).returncode != 0:
            self.skipTest("not a git checkout (a source tarball has no .gitignore to ask)")
        for leftover in ("build/lib/cobblerpy/__init__.py",
                         "cobblerpy.egg-info/PKG-INFO"):
            r = subprocess.run(["git", "-C", REPO, "check-ignore", "-q", leftover])
            self.assertEqual(r.returncode, 0, f"{leftover} is not ignored")

    def test_warnings_from_the_surveyed_code_stay_out_of_the_output(self):
        """Parsing somebody's `"\\d"` raises THEIR SyntaxWarning, not ours."""
        t = Tree({"m.py": 'PATTERN = "\\d+"\n# OTHER = "\\w"\n'})
        self.addCleanup(t.close)
        r = self._cli(t.dir, "--no-history")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Warning", r.stderr)


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

    def test_each_key_swatch_is_the_colour_its_cards_carry(self):
        """The swatches were the palette's near-black FILL with a 1px edge --
        in the light theme a row of black squares -- while the cards carry
        their state in the edge colour. And the continuation chip used the
        dead end's exact fill and edge, so the two read as one thing."""
        from cobblerpy import svgmap
        page = Page(svgmap.KEYBAR)
        swatches = {}
        chip = None
        for tag, attrs in page.elements:
            if tag in ("button", "span") and "chip" in attrs.get("class", ""):
                chip = attrs.get("data-state", "continuation")
            elif tag == "i" and chip:
                swatches[chip] = attrs.get("style", "")
        for state, (stroke, _fill, _desc) in svgmap._PALETTE.items():
            self.assertIn(f"background:{stroke}", swatches[state].replace(" ", ""),
                          f"{state}: {swatches[state]}")
        self.assertNotEqual(swatches["continuation"], swatches["deadend"],
                            "continuation and dead end have the same swatch")

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

    def test_the_untracked_badge_does_not_sit_on_the_size_line(self):
        """Measured in Chromium: on 18 of atlas's 55 cards the "untracked"
        badge was drawn over "624 lines · no history". This is the markup
        half of that check, with the widths Chromium measured per character
        (6.0px for the 11px line, 7.0px for the 8.5px spaced badge)."""
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n'}, git=True)
        self.addCleanup(t.close)
        # Five digits: "12,345 lines" alone runs under the badge, so a zero
        # reserve for it fails here rather than passing on a short size.
        write(t.dir, "a_module_nobody_committed.py", "x = 1\n" * 12345)
        # Two uncommitted attempts at one job: the one that stops carries the
        # continuation mark in the same corner as its badge.
        write(t.dir, "ingest.py", "def parse_record(r):\n    return r\n\n\n"
              "def validate_record(r):\n    pass\n\n\ndef store_record(r):\n    pass\n")
        write(t.dir, "ingest_v2.py", "def parse_record(r):\n    return dict(r)\n\n\n"
              "def validate_record(r):\n    return True\n\n\n"
              "def store_record(r):\n    return r\n")
        s = t.survey(with_history=True)
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)

        class Cards(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.cards, self._text = [], None

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "g" and a.get("class") == "node":
                    self.cards.append({})
                elif tag == "text" and self.cards:
                    self._text = a
                    self.cards[-1][a.get("class")] = [a, ""]

            def handle_endtag(self, tag):
                if tag == "text":
                    self._text = None

            def handle_data(self, data):
                if self._text is not None and self.cards:
                    self.cards[-1][self._text.get("class")][1] += data

        with open(out, encoding="utf-8") as fh:
            parsed = Cards()
            parsed.feed(fh.read())
        badged = [c for c in parsed.cards if "badge" in c]
        self.assertTrue(badged, "the fixture drew no badge, so this proves nothing")
        for card in badged:
            (owner, text), (badge, word) = card["meta owner"], card["badge"]
            right_of_text = float(owner["x"]) + 6.0 * len(text)
            left_of_badge = float(badge["x"]) - 7.0 * len(word)
            self.assertLessEqual(right_of_text, left_of_badge,
                                 f"{text!r} runs under {word!r}")
            self.assertNotIn("\u2026", text, "the size was cut through a word")
        both = [c for c in badged if "continues-mark" in c]
        self.assertTrue(both, "no card has a badge AND a continuation mark")
        for card in both:
            badge, mark = card["badge"][0], card["continues-mark"][0]
            self.assertLessEqual(float(badge["x"]), float(mark["x"]) - 20,
                                 "the badge is drawn under the continuation mark")

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


class TestLaunch(unittest.TestCase):
    """Starting the tool without an incantation.

    Dropping folders on an icon means there is no terminal: nothing prints, so
    every outcome -- including every failure -- has to come back through a
    notification or it is silent. That is the hazard this covers.
    """

    def _run(self, argv, fixture=None):
        """Call the launcher with the browser and notifier captured."""
        from cobblerpy import launch
        t = Tree(fixture if fixture is not None
                 else {"pkg/a.py": "import pkg.b\n", "pkg/b.py": "x = 1\n"})
        self.addCleanup(t.close)
        said, opened = [], []
        # open_url returns TRUTHY on success -- webbrowser.open returns False
        # when it cannot find a browser, and the launcher has to believe it.
        def _open(url):
            opened.append(url)
            return True
        argv = [a.replace("<TREE>", t.dir) for a in argv]
        # Registry and shelf point INTO the fixture. Without this the suite
        # writes to the real shared shelf: a run left nine rows named after
        # temp directories on the shelf a person actually opens.
        code = launch.main(argv, notify=said.append, open_url=_open,
                           registry=os.path.join(t.dir, "maps.json"),
                           shelf=os.path.join(t.dir, "shelf.html"))
        # The map is written BESIDE the tree, so rmtree(t.dir) never reaches
        # it. 126 orphan files accumulated in /tmp before anybody looked.
        for url in opened:
            self.addCleanup(_unlink_quietly, url.replace("file://", ""))
        return code, said, opened, t

    def test_a_run_writes_only_to_the_registry_it_was_given(self):
        """The suite must not touch the shelf a person opens.

        It did: nine rows named after temp directories turned up on the real
        shared shelf because this harness overrode notify and open_url but not
        the registry.
        """
        from cobblerpy import launch
        real = launch.registry_path()
        before = os.path.getmtime(real) if os.path.exists(real) else None
        _code, said, _opened, t = self._run(["<TREE>"])
        after = os.path.getmtime(real) if os.path.exists(real) else None
        self.assertEqual(before, after,
                         f"the run touched the real registry at {real}")
        self.assertTrue(os.path.isfile(os.path.join(t.dir, "maps.json")),
                        f"it did not use the registry it was given; said {said}")

    def test_when_no_browser_can_be_opened_it_says_where_the_map_is(self):
        """webbrowser.open returns False rather than raising.

        On a headless machine, over ssh, or in a container there is no browser
        to find. Announcing "opening the map" and discarding that False leaves
        somebody told it worked, with no path to the thing that was written.
        """
        from cobblerpy import launch
        t = Tree({"only.py": "x = 1\n"})
        self.addCleanup(t.close)
        said = []
        code = launch.main([t.dir], notify=said.append,
                           open_url=lambda _u: False,
                           registry=os.path.join(t.dir, "maps.json"),
                           shelf=os.path.join(t.dir, "shelf.html"))
        joined = " ".join(said)
        self.assertEqual(code, 0, joined)
        self.assertNotIn("opening the map", joined,
                         "it claimed to open a map it could not open")
        self.assertIn("-map-", joined, f"the map path was never shown: {said}")
        for m in said:
            if "-map-" in m:
                self.addCleanup(_unlink_quietly,
                                m.split()[-1].replace("file://", ""))

    def test_one_folder_becomes_a_map_beside_it_and_is_opened(self):
        code, said, opened, t = self._run(["<TREE>"])
        self.assertEqual(code, 0, said)
        self.assertEqual(len(opened), 1, f"nothing was opened; said {said}")
        path = opened[0].replace("file://", "")
        self.assertTrue(os.path.isfile(path), f"no map at {path}")
        # beside the project, never inside it: a map written into the tree is
        # read by the next survey and shows up in somebody's git status
        self.assertFalse(os.path.abspath(path).startswith(
            os.path.abspath(t.dir) + os.sep), "map was written INSIDE the tree")
        self.assertTrue(any("map" in m.lower() for m in said), said)

    def test_several_folders_are_refused_by_count_never_silently_reduced(self):
        """The bug this replaces: the GUI parsed every dropped folder and then
        kept folders[0], discarding the rest without a word."""
        code, said, opened, t = self._run(["<TREE>/pkg", "<TREE>"])
        self.assertNotEqual(code, 0, "two folders were accepted silently")
        self.assertEqual(opened, [], "a map was opened for an unsupported input")
        joined = " ".join(said)
        self.assertIn("2", joined, f"the count was not reported: {said}")
        self.assertRegex(joined.lower(), r"one folder|a single folder",
                         f"did not say what it can take: {said}")


    def test_one_module_is_not_reported_as_1_modules(self):
        _code, said, _opened, _t = self._run(
            ["<TREE>"], fixture={"only.py": "x = 1\n"})
        joined = " ".join(said)
        self.assertIn("1 module mapped", joined, joined)

    def test_a_path_that_does_not_exist_is_reported_not_swallowed(self):
        code, said, opened, _t = self._run(["<TREE>/nope"])
        self.assertEqual(code, 1)
        self.assertEqual(opened, [])
        self.assertRegex(" ".join(said), r"does not exist")

    def test_a_folder_with_no_python_says_so(self):
        code, said, opened, _t = self._run(
            ["<TREE>"], fixture={"README.md": "nothing to parse here\n"})
        self.assertEqual(code, 1)
        self.assertEqual(opened, [])
        self.assertRegex(" ".join(said).lower(), r"no python")

    def test_a_dropped_file_resolves_to_the_folder_holding_it(self):
        """Dropping one module out of a project is an obvious thing to do."""
        code, said, opened, _t = self._run(["<TREE>/pkg/a.py"])
        self.assertEqual(code, 0, said)
        self.assertEqual(len(opened), 1, said)
        self.assertIn("pkg-map-", opened[0])

    def test_a_dropped_file_maps_its_project_and_writes_beside_it(self):
        """`cobble proj/pkg/a.py` surveyed proj/pkg and wrote proj/pkg-map-*.html:
        INSIDE the project, where its git status sees it and the next survey
        reads it. A file means the project it belongs to."""
        code, said, opened, t = self._run(["<TREE>/pkg/a.py"], fixture={
            "pyproject.toml": "[project]\nname = 'p'\n",
            "pkg/__init__.py": "", "pkg/a.py": "from pkg import b\n",
            "pkg/b.py": "x = 1\n", "tools/run.py": "import pkg.a\n"})
        self.assertEqual(code, 0, said)
        self.assertEqual(len(opened), 1, said)
        path = opened[0].replace("file://", "")
        self.assertFalse(os.path.abspath(path).startswith(t.dir + os.sep),
                         f"map written inside the project: {path}")
        self.assertTrue(os.path.basename(path).startswith(
            os.path.basename(t.dir) + "-map-"), path)
        self.assertIn("4 modules mapped", " ".join(said))

    def test_a_file_in_a_package_with_no_project_file_maps_what_holds_the_package(self):
        code, said, opened, t = self._run(["<TREE>/app/core/x.py"], fixture={
            "app/__init__.py": "", "app/core/__init__.py": "",
            "app/core/x.py": "x = 1\n"})
        self.assertEqual(code, 0, said)
        self.assertIn(t.dir + "-map-", opened[0])

    def test_two_maps_in_the_same_second_do_not_overwrite_each_other(self):
        """The stamp is to the second; the second run silently replaced the
        first map, which the shelf may still be pointing at."""
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        project = os.path.join(d, "proj")
        os.makedirs(project)
        first = launch.map_destination(project, stamp="20260919-120000")
        open(first, "w", encoding="utf-8").close()
        second = launch.map_destination(project, stamp="20260919-120000")
        self.assertNotEqual(first, second)
        self.assertFalse(os.path.exists(second))
        self.assertEqual(os.path.dirname(second), d)

    def _printed(self, argv):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, said, opened, _t = self._run(argv)
        return code, out.getvalue(), said, opened

    def test_help_prints_usage_and_surveys_nothing(self):
        """The binary is this launcher; `CodeCobbler --help` surveyed the
        current folder, because every argument starting with '-' was dropped."""
        code, out, said, opened = self._printed(["--help"])
        self.assertEqual(code, 0, said)
        self.assertEqual(opened, [], "--help surveyed a folder")
        self.assertIn("--shelf", out)

    def test_version_prints_the_version_and_surveys_nothing(self):
        from cobblerpy import __version__
        code, out, said, opened = self._printed(["--version"])
        self.assertEqual(code, 0, said)
        self.assertEqual(opened, [], "--version surveyed a folder")
        self.assertIn(__version__, out)

    def test_an_unknown_option_is_refused_not_dropped(self):
        code, _out, said, opened = self._printed(["--frobnicate", "<TREE>"])
        self.assertEqual(code, 2)
        self.assertEqual(opened, [])
        self.assertIn("--frobnicate", " ".join(said))

    def test_the_single_executable_opens_the_shelf_when_given_nothing(self):
        """Double-clicking the binary opens the shelf, as the README says.

        A bare `cobble` in a terminal still means "here"; the binary is the
        thing people double-click, from an arbitrary working directory.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_launcher_entry", os.path.join(REPO, "packaging", "launcher_entry.py"))
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        self.assertEqual(entry.argv_for([], frozen=True), ["--shelf"])
        self.assertEqual(entry.argv_for([], frozen=False), [])
        self.assertEqual(entry.argv_for(["x"], frozen=True), ["x"])


REPO =os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules the tool imports after it starts, plus the two files Python runs at
# startup from anything on sys.path. Each one, if EXECUTED, leaves a marker.
SHADOWED = ("ast", "tokenize", "json", "argparse", "textwrap", "html",
            "subprocess", "webbrowser", "sitecustomize", "usercustomize")


class TestNeverRunsTheCodeItReads(unittest.TestCase):
    """"It never imports or executes the code it reads" -- from INSIDE it.

    The obvious place to run the tool is the project's own folder. `-m` puts
    that folder first on sys.path, and the `cobble` shim appended an empty
    PYTHONPATH entry, which means the same. A project holding ast.py or
    sitecustomize.py was then imported in place of the standard library: its
    code ran, and a harmless tokenize.py crashed the launcher outright.
    """

    def setUp(self):
        self.markers = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.markers, True)
        files = {f"{n}.py": f"open({os.path.join(self.markers, n)!r}, 'w')"
                            f".write('ran')\n" for n in SHADOWED}
        files["app.py"] = "def run():\n    return 1\n"
        self.tree = Tree(files)
        self.addCleanup(self.tree.close)
        # Scratch HOME: the launcher writes a registry and a shelf, and they
        # must never be the ones a person opens.
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        base = os.path.basename(self.tree.dir)
        parent = os.path.dirname(self.tree.dir)
        self.addCleanup(lambda: [_unlink_quietly(os.path.join(parent, f))
                                 for f in os.listdir(parent)
                                 if f.startswith(base + "-map-")])

    def _env(self, **extra):
        # No DISPLAY, no DBUS: nothing may open a browser or a notification.
        env = {"HOME": self.home, "CODECOBBLER_HOME": self.home,
               "PATH": "/usr/bin:/bin", "USER": os.environ.get("USER", "nobody")}
        env.update(extra)
        return env

    def _ran(self):
        return sorted(os.listdir(self.markers))

    def test_python_m_from_inside_the_project_runs_none_of_it(self):
        r = subprocess.run([sys.executable, "-m", "cobblerpy", ".", "--no-history"],
                           cwd=self.tree.dir, env=self._env(PYTHONPATH=REPO),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(self._ran(), [], "the project's own code was executed")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])

    # What Python's -m machinery imports before any package code runs, on
    # 3.12 (measured one module at a time). The shim runs its entry as a
    # SCRIPT, which imports none of them from the current directory; a shim
    # that went back to -m would run all of these, whatever the package does.
    RUNPY_FIRST = ("collections", "functools", "importlib", "keyword",
                   "operator", "reprlib", "threading", "types", "warnings")

    def test_every_spelling_of_python_m_cobblerpy_is_recognised(self):
        """Combined short options (-Bm, -Om) and options that take a value
        (--check-hash-based-pycs always) hid the -m target from the guard."""
        for opts in (["-m", "cobblerpy"], ["-Bm", "cobblerpy"], ["-Om", "cobblerpy"],
                     ["-mcobblerpy"], ["-Bmcobblerpy"],
                     ["--check-hash-based-pycs", "always", "-m", "cobblerpy"],
                     ["-W", "ignore", "-m", "cobblerpy"], ["-X", "dev", "-m", "cobblerpy"]):
            for f in os.listdir(self.markers):
                os.unlink(os.path.join(self.markers, f))
            r = subprocess.run([sys.executable, *opts, ".", "--no-history"],
                               cwd=self.tree.dir, env=self._env(PYTHONPATH=REPO),
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(self._ran(), [], f"{' '.join(opts)} ran the project's code")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])

    def test_the_installed_cobble_command_runs_none_of_it(self):
        """The shim the installer writes, run exactly as a terminal would."""
        for n in self.RUNPY_FIRST:
            write(self.tree.dir, f"{n}.py",
                  f"open({os.path.join(self.markers, n)!r}, 'w').write('ran')\n")
        subprocess.run(["sh", os.path.join(REPO, "packaging", "install-launcher.sh")],
                       env=self._env(), capture_output=True, text=True,
                       timeout=120, check=True, stdin=subprocess.DEVNULL)
        cobble = os.path.join(self.home, ".local", "bin", "cobble")
        r = subprocess.run([cobble, "."], cwd=self.tree.dir, env=self._env(),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(self._ran(), [], "the project's own code was executed")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("modules mapped", r.stderr)

    # What Python itself does with an empty PYTHONPATH element, before any
    # line of this tool runs: the directory goes on sys.path at startup, and
    # site imports sitecustomize and usercustomize from it. No package can
    # prevent that; the README says so. Everything after is the tool's.
    AT_STARTUP = {"sitecustomize", "usercustomize"}

    def test_console_scripts_drop_an_empty_pythonpath_entry(self):
        """PYTHONPATH=":" (or "/x:") is the current directory, and pip's
        `cobblerpy` then imported the project's ast.py in place of Python's."""
        bindir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, bindir, True)
        for name, target in (("cobblerpy", "cobblerpy.__main__"),
                             ("cobble", "cobblerpy.launch"),
                             ("cobblerpy-gui", "cobblerpy.gui.qt_app")):
            # The wrapper pip writes, byte for byte in what matters.
            write(bindir, name, f"#!{sys.executable}\nimport re\nimport sys\n"
                                f"from {target} import main\n"
                                "if __name__ == '__main__':\n"
                                "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
                                "    sys.exit(main())\n")
            os.chmod(os.path.join(bindir, name), 0o755)
        for name, args in (("cobblerpy", [".", "--no-history"]), ("cobble", ["."]),
                           ("cobblerpy-gui", [])):
            for pythonpath in (REPO + os.pathsep, os.pathsep + REPO):
                for f in os.listdir(self.markers):
                    os.unlink(os.path.join(self.markers, f))
                subprocess.run([os.path.join(bindir, name), *args], cwd=self.tree.dir,
                               env=self._env(PYTHONPATH=pythonpath),
                               capture_output=True, text=True, timeout=120)
                self.assertEqual(set(self._ran()) - self.AT_STARTUP, set(),
                                 f"{name} with PYTHONPATH={pythonpath!r} ran the "
                                 f"project's code")

    def test_the_launcher_script_drops_an_empty_pythonpath_entry(self):
        r = subprocess.run([sys.executable, os.path.join(REPO, "packaging",
                                                         "launcher_entry.py"), "."],
                           cwd=self.tree.dir, env=self._env(PYTHONPATH=REPO + os.pathsep),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(set(self._ran()) - self.AT_STARTUP, set(), r.stderr[-300:])
        self.assertEqual(r.returncode, 0, r.stderr[-300:])

    def test_another_program_run_with_m_keeps_its_own_directory(self):
        """The -m guard fired for ANY `python -m app` that imported cobblerpy,
        and took away the directory that app's own imports come from."""
        # Imported while -m is still LOCATING the package, which is when
        # sys.argv is ["-m"] for every program, not just this one.
        app = Tree({"someapp/__init__.py": "import cobblerpy\n",
                    "someapp/__main__.py": "import helper\nprint(helper.VALUE)\n",
                    "helper.py": "VALUE = 'found'\n"})
        self.addCleanup(app.close)
        r = subprocess.run([sys.executable, "-m", "someapp"], cwd=app.dir,
                           env=self._env(PYTHONPATH=REPO), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("found", r.stdout)

    def test_a_users_own_script_with_one_of_our_names_keeps_its_path(self):
        """The guard matched programs by NAME, so somebody's tools/cobble.py
        that imports cobblerpy as a library lost their PYTHONPATH=. and
        could not import their own helpers."""
        app = Tree({"helpers.py": "VALUE = 'found'\n",
                    **{f"tools/{n}.py": "import cobblerpy\nimport helpers\n"
                                        "print(helpers.VALUE)\n"
                       for n in ("cobble", "cli_entry", "app_entry")}})
        self.addCleanup(app.close)
        # (Not tools/cobblerpy.py: beside the others it would BE the
        # `cobblerpy` they import, and the guard would never run.)
        for n in ("cobble", "cli_entry", "app_entry"):
            r = subprocess.run([sys.executable, os.path.join("tools", f"{n}.py")],
                               cwd=app.dir, env=self._env(PYTHONPATH=REPO + os.pathsep + "."),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, f"tools/{n}.py: {r.stderr[-300:]}")
            self.assertIn("found", r.stdout)

    def test_a_deleted_working_directory_does_not_crash_it(self):
        gone = tempfile.mkdtemp()
        r = subprocess.run(["sh", "-c", f"cd '{gone}' && rmdir '{gone}' && "
                            f"exec '{sys.executable}' -m cobblerpy --version"],
                           env=self._env(PYTHONPATH=REPO), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("cobblerpy", r.stdout)

    def test_a_failure_before_the_launcher_starts_still_reaches_the_user(self):
        """A crash on import happens before launch.main and its notifications.

        A harmless tokenize.py did exactly that: the traceback went to stderr,
        and from a desktop icon stderr is nowhere. The packaged entry has to
        say it itself.
        """
        broken = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, broken, True)
        write(broken, "cobblerpy/__init__.py",
              "raise RuntimeError('the package is broken')\n")
        said = os.path.join(broken, "said.txt")
        write(broken, "bin/notify-send",
              f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> {said!r}\n")
        os.chmod(os.path.join(broken, "bin", "notify-send"), 0o755)
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "packaging", "launcher_entry.py"), "."],
            cwd=self.tree.dir, capture_output=True, text=True, timeout=120,
            env=self._env(PYTHONPATH=broken,
                          PATH=os.path.join(broken, "bin") + ":/usr/bin:/bin"))
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isfile(said), f"no notification; stderr {r.stderr[-300:]}")
        with open(said, encoding="utf-8") as fh:
            self.assertIn("the package is broken", fh.read())


class TestInstaller(unittest.TestCase):
    """packaging/install-launcher.sh, run into a scratch HOME.

    It used to put the shelf on the first writable mount it found without a
    word. The rule now: it ASKS, offering ~/.local/share/codecobbler (the
    default) and every writable shared mount; with no terminal it takes the
    default and says so; CODECOBBLER_HOME already set skips the question.
    """

    SCRIPT = os.path.join(REPO, "packaging", "install-launcher.sh")

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.env = {"HOME": self.home, "PATH": "/usr/bin:/bin",
                    "USER": os.environ.get("USER", "nobody")}

    def _shim_default(self, home=None):
        """What the installed `cobble` falls back to for CODECOBBLER_HOME --
        asked of the shell, which is what will read it."""
        shim = os.path.join(home or self.home, ".local", "bin", "cobble")
        r = subprocess.run(["sh", "-c", 'eval "$(grep "^SHELF=" "$1")" && '
                            'printf %s "$SHELF"', "sh", shim],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, f"the shim has no SHELF line: {r.stderr}")
        return r.stdout

    def _interactive(self, answer):
        """Run it on a pseudo-terminal, as a person at a shell would. A list
        answers each prompt in turn."""
        answers = list(answer) if isinstance(answer, list) else [answer]
        import pty
        import select
        master, slave = pty.openpty()
        p = subprocess.Popen(["sh", self.SCRIPT], stdin=slave, stdout=slave,
                             stderr=slave, env=self.env, close_fds=True)
        os.close(slave)
        out, sent = b"", 0
        while True:
            ready, _w, _x = select.select([master], [], [], 30)
            if not ready:
                p.kill()
                self.fail(f"the installer hung; it said {out.decode()[-300:]}")
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if sent < len(answers) and out.count(b"Choose") > sent:
                os.write(master, answers[sent].encode() + b"\n")
                sent += 1
        p.wait(timeout=30)
        os.close(master)
        self.assertEqual(p.returncode, 0, out.decode()[-400:])
        return out.decode()

    def test_with_no_terminal_it_takes_the_default_and_says_so(self):
        r = subprocess.run(["sh", self.SCRIPT], env=self.env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        default = os.path.join(self.home, ".local", "share", "codecobbler")
        self.assertIn(default, r.stdout)
        # Empty means launch.py's own fallback, which IS that folder.
        self.assertEqual(self._shim_default(), "")

    def test_a_preset_codecobbler_home_skips_the_question(self):
        chosen = os.path.join(self.home, "shelf-here")
        os.makedirs(chosen)
        self.env["CODECOBBLER_HOME"] = chosen
        out = self._interactive("2")
        self.assertNotIn("Choose", out)
        self.assertEqual(self._shim_default(), chosen)

    def test_it_asks_and_the_default_is_the_home_folder(self):
        out = self._interactive("")
        self.assertIn("1) " + os.path.join(self.home, ".local", "share", "codecobbler"),
                      out)
        self.assertEqual(self._shim_default(), "")

    def _files(self):
        return sorted(os.path.relpath(os.path.join(d, f), self.home)
                      for d, _dirs, files in os.walk(self.home) for f in files
                      # the desktop database's cache, rebuilt by both halves
                      if f != "mimeinfo.cache")

    def test_uninstall_removes_what_install_wrote_and_keeps_the_maps(self):
        os.makedirs(os.path.join(self.home, "Desktop"))
        write(self.home, ".local/bin/someone-elses-tool", "#!/bin/sh\n")
        write(self.home, ".local/share/codecobbler/maps.json", "[]\n")
        before = self._files()
        run = lambda *a: subprocess.run(["sh", self.SCRIPT, *a], env=self.env,
                                        stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, timeout=60)
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreater(len(self._files()), len(before), "nothing was installed")
        r = run("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._files(), before)
        self.assertIn(".local/share/codecobbler", r.stdout,
                      "it did not say where the kept maps are")

    def test_uninstall_leaves_alone_files_it_did_not_write(self):
        """`pip install --user` puts its own `cobble` at ~/.local/bin/cobble,
        and uninstall deleted it by path. Only files carrying the installer's
        mark are the installer's."""
        os.makedirs(os.path.join(self.home, "Desktop"))
        foreign = {
            ".local/bin/cobble": "#!/usr/bin/python3\nfrom cobblerpy.launch import main\n",
            "Desktop/CodeCobbler.desktop": "[Desktop Entry]\nName=Someone else's\n",
            ".local/share/applications/codecobbler.desktop": "[Desktop Entry]\n",
            ".local/share/icons/codecobbler.svg": "<svg/>\n",
            ".local/share/nautilus/scripts/Map with CodeCobbler": "#!/bin/sh\n",
        }
        for rel, text in foreign.items():
            write(self.home, rel, text)
        before = self._files()
        r = subprocess.run(["sh", self.SCRIPT, "--uninstall"], env=self.env,
                           stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._files(), before, r.stdout)
        self.assertEqual(r.stdout.count("left alone: not ours"), len(foreign), r.stdout)

    def test_install_refuses_to_overwrite_a_file_it_did_not_write(self):
        """It overwrote pip's own ~/.local/bin/cobble, marked it as its own,
        and then uninstall deleted it."""
        pips = "#!/usr/bin/python3\nfrom cobblerpy.launch import main\n"
        write(self.home, ".local/bin/cobble", pips)
        run = lambda *a: subprocess.run(["sh", self.SCRIPT, *a], env=self.env,
                                        stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, timeout=60)
        before = self._files()
        r = run()
        self.assertNotEqual(r.returncode, 0, "it installed over a file it did not write")
        self.assertIn(".local/bin/cobble", r.stdout + r.stderr)
        self.assertIn("--force", r.stdout + r.stderr)
        self.assertEqual(self._files(), before, "it wrote something before refusing")
        run("--uninstall")
        with open(os.path.join(self.home, ".local/bin/cobble"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), pips)
        r = run("--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.home, ".local/bin/cobble"), encoding="utf-8") as fh:
            self.assertIn("X-CodeCobbler-Installer", fh.read())

    def test_an_answer_that_is_not_a_choice_is_asked_again(self):
        """"0" made the shelf "$0" -- the installer's own path -- and a
        twenty-digit number reached the shell's arithmetic."""
        out = self._interactive(["0", "00", "99999999999999999999", "x", "-1", ""])
        self.assertEqual(out.count("not one of the choices"), 5, out[-600:])
        self.assertNotIn("Illegal number", out)
        self.assertEqual(self._shim_default(), "")

    def test_paths_are_written_as_data_never_as_code(self):
        """A volume label is somebody else's text. The shim wrote the shelf
        path and the checkout inside double quotes, so `$(...)` in either ran
        on every `cobble`; the .desktop Exec broke on a space."""
        nasty = 'a b $(touch PWN1) "q" `touch PWN2` 100%'
        top = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, top, True)
        checkout = os.path.join(top, "checkout " + nasty)
        shutil.copytree(os.path.join(REPO, "cobblerpy"), os.path.join(checkout, "cobblerpy"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(REPO, "packaging"), os.path.join(checkout, "packaging"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        # HOME gets a space and quotes but no $( ) or backtick: xdg-user-dir,
        # which the installer asks for the desktop folder, evals $HOME itself.
        # That is the system tool's, and a HOME like that has worse problems.
        home = os.path.join(top, 'home a b "q" 100%')
        shelf = os.path.join(top, "shelf " + nasty)
        os.makedirs(os.path.join(home, "Desktop"))
        os.makedirs(shelf)
        env = dict(self.env, HOME=home, CODECOBBLER_HOME=shelf)
        r = subprocess.run(["sh", os.path.join(checkout, "packaging", "install-launcher.sh")],
                           env=env, cwd=top, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        run_env = {"HOME": home, "PATH": "/usr/bin:/bin"}
        r = subprocess.run([os.path.join(home, ".local", "bin", "cobble"), "--version"],
                           env=run_env, cwd=top, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cobblerpy", r.stdout)
        self.assertEqual(self._shim_default(home), shelf)
        pwned = [f for d, _s, fs in os.walk(top) for f in fs if f.startswith("PWN")]
        self.assertEqual(pwned, [], "a path was executed as a command")
        # The desktop entry's Exec, read back by the spec's own rules.
        with open(os.path.join(home, ".local/share/applications/codecobbler.desktop"),
                  encoding="utf-8") as fh:
            value = [l for l in fh.read().splitlines() if l.startswith("Exec=")][0][5:]
        value = value.replace("\\\\", "\\")          # string-level escapes
        self.assertTrue(value.startswith('"'), value)
        arg, i = "", 1
        while value[i] != '"':
            if value[i] == "\\":
                i += 1
            elif value[i] == "%":         # a literal percent is written %%
                i += 1
                self.assertEqual(value[i], "%", f"a lone % in Exec: {value}")
            arg += value[i]
            i += 1
        self.assertEqual(arg, os.path.join(home, ".local", "bin", "cobble"))
        if shutil.which("desktop-file-validate"):
            v = subprocess.run(["desktop-file-validate", os.path.join(
                home, ".local/share/applications/codecobbler.desktop")],
                capture_output=True, text=True)
            self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

    def test_uninstall_names_the_maps_as_they_are_really_named(self):
        import re
        from cobblerpy import launch
        real = os.path.basename(launch.map_destination("/x/proj", stamp="20260919-120000"))
        self.assertRegex(real, r"^proj-map-\d{8}-\d{6}\.html$")
        r = subprocess.run(["sh", self.SCRIPT, "--uninstall"], env=self.env,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=60)
        self.assertIn("<project>-map-YYYYMMDD-HHMMSS.html", r.stdout)

    def test_a_preset_shelf_that_does_not_exist_is_said_to_be_missing(self):
        """launch.py skips a CODECOBBLER_HOME that is not a folder, so echoing
        it as "the shelf" promised a place no map would go."""
        self.env["CODECOBBLER_HOME"] = os.path.join(self.home, "not-there")
        r = subprocess.run(["sh", self.SCRIPT], env=self.env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("does not exist", r.stdout)
        self.assertNotIn("(CODECOBBLER_HOME was already set)", r.stdout)

    def test_a_shared_mount_it_offers_is_recorded_when_chosen(self):
        import re
        out = self._interactive("")
        offered = re.findall(r"^\s*2\) (\S+)", out, re.M)
        if not offered:
            self.skipTest("no writable shared mount on this machine to offer")
        out = self._interactive("2")
        self.assertEqual(self._shim_default(), offered[0])
        # The shim is written; nothing is written ON the mount by installing.


class TestShelf(unittest.TestCase):
    """The place a person goes to find the maps they already made.

    Maps land beside the projects they describe, which is right, and leaves
    them scattered: seven files called cobblerpy_map_*.html in one directory
    with no way to tell which was current. The shelf is the index, refreshed
    every time a map is written.
    """

    def test_the_shelf_lives_on_the_volume_both_operating_systems_can_see(self):
        """One shelf, not one per OS.

        This machine dual-boots and the projects live on an NTFS partition that
        is a mount point under Linux and a drive letter under Windows. A shelf
        under ~ would give each side its own, so neither would list the other's
        maps -- and a hardcoded /media/... path simply does not exist under
        Windows.
        """
        from cobblerpy import launch
        shared = launch.shared_root()
        if shared is None:
            self.skipTest("no shared volume on this machine")
        for path in (launch.shelf_path(), launch.registry_path()):
            self.assertTrue(path.startswith(shared),
                            f"{path} is not on the shared volume {shared}")

    def test_without_a_shared_volume_it_falls_back_instead_of_failing(self):
        """A checkout on somebody else's machine has no W: drive."""
        from cobblerpy import launch
        self.assertIsNone(launch.shared_root(candidates=("/nonexistent-xyz",)))
        fallback = launch.shelf_path(candidates=("/nonexistent-xyz",))
        self.assertTrue(fallback)
        self.assertNotIn("nonexistent-xyz", fallback)

    def test_a_written_map_is_recorded_on_the_shelf(self):
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        reg = os.path.join(d, "maps.json")
        m = os.path.join(d, "atlas-map-1.html")
        open(m, "w", encoding="utf-8").close()
        launch.record_map(reg, "atlas", m, 55)
        entries = launch.shelf_entries(reg)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["project"], "atlas")
        self.assertEqual(entries[0]["modules"], 55)
        self.assertIn("when", entries[0])

    def test_the_shelf_dates_a_map_by_the_map_not_by_the_moment_it_was_listed(self):
        """Otherwise "newest first" sorts on a fiction.

        Seeding seven existing maps in one pass stamped all seven with the same
        minute, which said two maps from 01:20 were as current as one from
        23:44. The map file's own mtime is the honest answer.
        """
        import time
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        reg = os.path.join(d, "maps.json")
        old_map = os.path.join(d, "old.html")
        with open(old_map, "w", encoding="utf-8") as fh:
            fh.write("<html></html>")
        long_ago = time.time() - 60 * 60 * 24 * 30
        os.utime(old_map, (long_ago, long_ago))
        launch.record_map(reg, "ancient", old_map, 5)
        entry = launch.shelf_entries(reg)[0]
        self.assertEqual(entry["when"],
                         time.strftime("%Y-%m-%d %H:%M", time.localtime(long_ago)),
                         "the shelf dated it now, not when the map was written")

    def test_a_map_that_no_longer_exists_drops_off_the_shelf(self):
        """A shelf row is a link. A link to a deleted file is a broken promise.

        Also the cleanup for a real mess: temp-directory surveys left nine
        rows on the live shelf, every one of them pointing at a path that had
        already been removed.
        """
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        reg = os.path.join(d, "maps.json")
        kept = os.path.join(d, "kept.html")
        with open(kept, "w", encoding="utf-8") as fh:
            fh.write("<html></html>")
        launch.record_map(reg, "real", kept, 3)
        launch.record_map(reg, "vanished", os.path.join(d, "gone.html"), 9)
        names = [e["project"] for e in launch.shelf_entries(reg)]
        self.assertEqual(names, ["real"], names)

    def test_re_mapping_a_project_replaces_its_row_rather_than_stacking(self):
        """Nine rows for one project is the scatter it exists to fix."""
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        reg = os.path.join(d, "maps.json")
        a, b, c = (os.path.join(d, n) for n in ("a.html", "b.html", "c.html"))
        for f in (a, b, c):
            open(f, "w", encoding="utf-8").close()
        launch.record_map(reg, "atlas", a, 55)
        launch.record_map(reg, "atlas", b, 57)
        launch.record_map(reg, "ledger", c, 977)
        entries = launch.shelf_entries(reg)
        self.assertEqual(len(entries), 2, entries)
        fed = [e for e in entries if e["project"] == "atlas"][0]
        self.assertEqual(fed["modules"], 57)
        self.assertEqual(fed["map"], b)

    def test_two_projects_with_the_same_folder_name_keep_their_own_rows(self):
        """Rows were keyed by folder NAME: mapping b/src replaced a/src."""
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        reg = os.path.join(d, "maps.json")
        one, two = (os.path.join(d, n) for n in ("one.html", "two.html"))
        for f in (one, two):
            open(f, "w", encoding="utf-8").close()
        launch.record_map(reg, "src", one, 3, folder="/work/a/src")
        launch.record_map(reg, "src", two, 5, folder="/work/b/src")
        self.assertEqual(sorted(e["map"] for e in launch.shelf_entries(reg)),
                         sorted([one, two]))
        launch.record_map(reg, "src", two, 6, folder="/work/b/src")
        self.assertEqual(len(launch.shelf_entries(reg)), 2)

    def test_a_write_that_dies_halfway_leaves_the_old_register(self):
        """open(..., "w") truncated first; a crash inside json.dump left a
        register that no longer parsed, and the shelf showed nothing."""
        from unittest import mock
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        reg = os.path.join(d, "maps.json")
        kept = os.path.join(d, "kept.html")
        open(kept, "w", encoding="utf-8").close()
        launch.record_map(reg, "kept", kept, 3, folder="/work/kept")

        def dies(rows, fh, **kw):
            fh.write('[{"project": "half')
            raise OSError("disk full")
        with mock.patch.object(launch.json, "dump", dies):
            with self.assertRaises(OSError):
                launch.record_map(reg, "new", kept, 4, folder="/work/new")
        self.assertEqual([e["project"] for e in launch.shelf_entries(reg)], ["kept"])
        self.assertEqual(sorted(os.listdir(d)), ["kept.html", "maps.json"],
                         "the temporary file was left behind")

    def test_the_register_keeps_its_permissions(self):
        """mkstemp creates 0600, so every write narrowed the register."""
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        m = os.path.join(d, "m.html")
        open(m, "w", encoding="utf-8").close()
        reg = os.path.join(d, "maps.json")
        launch.record_map(reg, "p", m, 1, folder="/w/p")
        mask = os.umask(0)
        os.umask(mask)
        self.assertEqual(os.stat(reg).st_mode & 0o777, 0o666 & ~mask)
        os.chmod(reg, 0o640)
        launch.record_map(reg, "q", m, 1, folder="/w/q")
        self.assertEqual(os.stat(reg).st_mode & 0o777, 0o640)

    def test_a_register_that_cannot_be_replaced_is_reported_not_raised(self):
        """On Windows os.replace fails while another program holds the file.
        The map is already written; the run says the shelf was not updated."""
        from unittest import mock
        from cobblerpy import launch
        t = Tree({"only.py": "x = 1\n"})
        self.addCleanup(t.close)
        said, opened = [], []
        with mock.patch.object(launch.os, "replace",
                               side_effect=PermissionError(13, "held open")):
            code = launch.main([t.dir], notify=said.append,
                               open_url=lambda u: opened.append(u) or True,
                               registry=os.path.join(t.dir, "maps.json"),
                               shelf=os.path.join(t.dir, "shelf.html"))
        for u in opened:
            self.addCleanup(_unlink_quietly, u.replace("file://", ""))
        joined = " ".join(said)
        self.assertEqual(code, 0, joined)
        self.assertEqual(len(opened), 1, "the map was not opened")
        self.assertIn("shelf", joined)
        self.assertIn("held open", joined)
        self.assertEqual([f for f in os.listdir(t.dir) if f.endswith(".tmp")], [])

    def test_a_legacy_row_for_another_folder_survives_the_first_keyed_write(self):
        """Rows written before folders were recorded have only a name. The
        first write for b/src dropped the old row for a/src."""
        import json
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for sub in ("a", "b"):
            os.makedirs(os.path.join(d, sub, "src"))
        a_map = os.path.join(d, "a", "src-map-20260101-000000.html")
        b_old = os.path.join(d, "b", "src-map-20260101-000000.html")
        b_new = os.path.join(d, "b", "src-map-20260202-000000.html")
        for f in (a_map, b_old, b_new):
            open(f, "w", encoding="utf-8").close()
        reg = os.path.join(d, "maps.json")
        with open(reg, "w", encoding="utf-8") as fh:
            json.dump([{"project": "src", "map": a_map, "modules": 1, "when": "x"},
                       {"project": "src", "map": b_old, "modules": 2, "when": "x"}], fh)
        launch.record_map(reg, "src", b_new, 3, folder=os.path.join(d, "b", "src"))
        self.assertEqual(sorted(e["map"] for e in launch.shelf_entries(reg)),
                         sorted([a_map, b_new]))

    def test_the_shelf_lists_every_project_with_a_link_to_its_map(self):
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        reg = os.path.join(d, "maps.json")
        for n, mods in (("fed.html", 55), ("fide.html", 977)):
            f = os.path.join(d, n)
            open(f, "w", encoding="utf-8").close()
            launch.record_map(reg, n.split(".")[0].replace("fed", "atlas")
                              .replace("fide", "ledger"), f, mods)
        out = os.path.join(d, "shelf.html")
        launch.write_shelf(reg, out)
        page = Page(open(out, encoding="utf-8").read())
        hrefs = page.attr_values("href")
        self.assertTrue(any("fed.html" in h for h in hrefs), hrefs)
        self.assertTrue(any("fide.html" in h for h in hrefs), hrefs)
        text = open(out, encoding="utf-8").read()
        self.assertIn("atlas", text)
        self.assertIn("977", text)

    def test_the_shelf_path_says_where_it_is_when_it_cannot_be_opened(self):
        """Same flaw as the map path, and it had it too."""
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        shelf = os.path.join(d, "shelf.html")
        said = []
        code = launch.main(["--shelf"], notify=said.append,
                           open_url=lambda _u: False,
                           registry=os.path.join(d, "maps.json"), shelf=shelf)
        self.assertEqual(code, 0, said)
        self.assertIn(shelf, " ".join(said),
                      f"the shelf was written and never named: {said}")

    def test_launching_with_no_folder_opens_the_shelf_not_the_current_directory(self):
        """A desktop icon inherits an ARBITRARY working directory.

        Measured: a gtk-launch from this session handed the launcher
        /media/.../CP/resume. Surveying whatever that happens to be, because
        somebody clicked an icon, is not a reasonable thing to do.
        """
        from cobblerpy import launch
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        opened, said = [], []
        code = launch.main(["--shelf"], notify=said.append, open_url=opened.append,
                           registry=os.path.join(d, "maps.json"),
                           shelf=os.path.join(d, "shelf.html"))
        self.assertEqual(code, 0, said)
        self.assertEqual(len(opened), 1, f"nothing opened; said {said}")
        self.assertIn("shelf.html", opened[0])

class TestConnectionEvidence(unittest.TestCase):
    """The lines that show HOW one module reaches another.

    The panel used to render `uses: a, b, c` as bare names and throw away the
    line numbers, even though the scanner records both the import's lineno and
    every call site's. "How are these two related" is answerable in the source
    the map already carries.
    """

    FIXTURE = {
        "a.py": ("from b import go\n"
                 "import c\n"
                 "def run():\n"
                 "    return go() + c.x\n"),
        "b.py": "def go():\n    return 1\n",
        "c.py": "x = 2\n",
    }

    def _project(self, fixture=None):
        t = Tree(fixture or self.FIXTURE)
        self.addCleanup(t.close)
        return t.survey().project

    def test_the_import_line_and_the_call_site_are_both_evidence(self):
        p = self._project()
        got = p.evidence_for("a", "b")
        self.assertEqual([e["line"] for e in got], [1, 4],
                         f"expected the import and the call, got {got}")
        self.assertEqual(got[0]["text"], "from b import go")
        self.assertIn("go()", got[1]["text"])

    def test_a_module_reached_only_by_import_shows_just_that_line(self):
        p = self._project()
        got = p.evidence_for("a", "c")
        self.assertEqual([e["line"] for e in got], [2], got)
        self.assertEqual(got[0]["text"], "import c")

    def test_the_payload_carries_the_evidence_for_each_connection(self):
        """The map has to answer it without another round trip to the source."""
        import json, re
        from cobblerpy.report import write_map
        t = Tree(self.FIXTURE)
        self.addCleanup(t.close)
        sv = t.survey()
        out = os.path.join(t.dir, "map.html")
        write_map(sv.project, sv.frontier, sv.history, out,
                  origins=sv.origins, modules_by_key=sv.modules_by_key)
        doc = open(out, encoding="utf-8").read()
        data = json.loads(re.search(r"const DATA = (\{.*?\});\n", doc, re.S).group(1)
                          .replace("\\u003c", "<").replace("\\u003e", ">"))
        links = data["a"]["links"]
        self.assertIn("b", links, links)
        self.assertEqual([e["line"] for e in links["b"]["lines"]], [1, 4])
        self.assertEqual(links["b"]["lines"][0]["text"], "from b import go")
        self.assertEqual(links["b"]["more"], 0)

    def test_the_panel_shows_the_line_that_joins_two_modules(self):
        """Clicking a module answers "how is this related", in code.

        Native <details> on purpose: the `uses:` names are already links that
        navigate to the other module, and hanging a second click behaviour off
        them would break the one that exists.
        """
        import json, re, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        from cobblerpy.report import write_map
        t = Tree(self.FIXTURE)
        self.addCleanup(t.close)
        sv = t.survey()
        out = os.path.join(t.dir, "map.html")
        write_map(sv.project, sv.frontier, sv.history, out,
                  origins=sv.origins, modules_by_key=sv.modules_by_key)
        doc = open(out, encoding="utf-8").read()
        js = re.findall(r"<script>(.*?)</script>", doc, re.S)[-1]
        stub = """
const made = {};
function fake(id){ return {id:id, textContent:'', innerHTML:'', dataset:{}, attrs:{},
  scrollTop:0, get hidden(){ return 'hidden' in this.attrs; },
  setAttribute(k,v){ this.attrs[k]=v; }, removeAttribute(k){ delete this.attrs[k]; },
  hasAttribute(k){ return k in this.attrs; }, addEventListener(){},
  scrollIntoView(){}, closest(){ return null; },
  classList:{toggle(){}, remove(){}, add(){}, contains(){ return false; }},
  querySelectorAll(){ return []; }}; }
function el(id){ if(!made[id]) made[id]=fake(id); return made[id]; }
global.CSS = {escape: s => s};
global.window = {addEventListener(){}, removeEventListener(){}};
global.document = {getElementById: id => el(id), querySelectorAll: () => [],
  querySelector: sel => sel === '.mapwrap' ? el('mapwrap') : null,
  addEventListener(){}};
"""
        probe = """
openModule('a');
const ids = Object.keys(made);
console.log(JSON.stringify({panes: ids.map(i => made[i].innerHTML || '')}));
"""
        script = os.path.join(t.dir, "panel.js")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(stub + "\n" + js + probe)
        r = subprocess.run(["node", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-900:])
        html = " ".join(json.loads(r.stdout.strip().splitlines()[-1])["panes"])
        self.assertIn("<details", html, "no disclosure for the connection evidence")
        self.assertIn("from b import go", html, "the import line is not shown")
        self.assertRegex(html, r">\s*1\s*<", "the line number is not shown")

    def test_a_much_used_connection_is_capped_and_says_how_many_it_held_back(self):
        """Truncating in silence is the behaviour this tool argues against.

        A test file calling its subject 99 times is real -- the corpus has one.
        The panel shows the first few and states the remainder.
        """
        body = "import b\n" + "".join(f"b.go()  # {i}\n" for i in range(20))
        p = self._project({"a.py": body, "b.py": "def go():\n    return 1\n"})
        full = p.evidence_for("a", "b")
        self.assertEqual(len(full), 21, "evidence_for must stay COMPLETE")
        from cobblerpy.svgmap import cap_evidence
        capped = cap_evidence(full)
        self.assertEqual(len(capped["lines"]), 8)
        self.assertEqual(capped["more"], 13)

    def test_an_edge_that_does_not_exist_has_no_evidence(self):
        """Never invent a relationship the source does not show."""
        p = self._project()
        self.assertEqual(p.evidence_for("b", "c"), [])
        self.assertEqual(p.evidence_for("a", "nosuchmodule"), [])


class TestFolderRibbons(unittest.TestCase):
    """What the overview draws BETWEEN folders.

    Module-level edges were cut from the overview because every one of them
    crossed the whole chart and had to be followed by eye. A ribbon is the
    aggregate instead: one per ordered folder pair, whatever number of imports
    it carries. On the 977-module corpus that is 41 ribbons rather than 1104
    lines, which is the entire reason this exists.
    """

    FIXTURE = {
        # app -> lib twice, lib -> app once, tests -> app once. No pair inside
        # a folder, so a within-folder import cannot be mistaken for a ribbon.
        "app/main.py": 'import lib.core\nif __name__ == "__main__":\n    lib.core.go()\n',
        "app/util.py": "import lib.core\ndef u():\n    return lib.core.go()\n",
        "lib/core.py": "def go():\n    # TODO: finish\n    return 1\n",
        "lib/helper.py": "import app.util\ndef h():\n    return app.util.u()\n",
        "tests/test_x.py": "import app.main\ndef test_m():\n    assert app.main\n",
    }

    def _ribbons(self, fixture=None, states=None):
        from cobblerpy.layout import compute_folders
        from cobblerpy.svgmap import folder_ribbons
        t = Tree(fixture or self.FIXTURE)
        self.addCleanup(t.close)
        s = t.survey()
        folders = compute_folders(s.project, modules_by_key=s.modules_by_key)
        if states is None:
            states = {name: "live" for name in s.project.by_dotted}
        return folder_ribbons(s.project, folders, states), s, folders

    def test_a_ribbon_arrowhead_is_in_proportion_to_the_ribbon(self):
        """A marker in strokeWidth units is scaled by the ribbon's own width,
        so a 7-unit arrowhead on a 4px ribbon was drawn 28px across."""
        t = Tree(self.FIXTURE)
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            page = Page(fh.read())
        markers = {a["id"]: a for a in page.find("marker")}
        ribbons = page.find("path", **{"class": "ribbon"})
        self.assertTrue(ribbons, "no ribbons drawn, so nothing is measured")
        for r in ribbons:
            m = markers[r["marker-end"][5:-1]]
            self.assertEqual(m.get("markerunits", "strokeWidth"), "strokeWidth")
            head = float(m["markerwidth"]) * float(r["stroke-width"])
            self.assertLessEqual(head, 3 * float(r["stroke-width"]),
                                 f"{head:.0f}px head on a {r['stroke-width']}px ribbon")

    def test_a_ribbon_carries_the_condition_at_each_of_its_ends(self):
        """The colour describes the EDGE, not the folder.

        A folder holds modules in several conditions at once, so colouring a
        ribbon by the folder's overall mood would describe something the ribbon
        is not. The ends take the condition of the modules that actually
        participate in those imports.
        """
        ribbons, _s, _f = self._ribbons(states={
            "app.main": "live", "app.util": "live",
            "lib.core": "unfinished",          # the only lib module imported
            "lib.helper": "deadend",           # imports OUT, never imported in
            "tests.test_x": "tested",
        })
        by_pair = {(r["src"], r["dst"]): r for r in ribbons}
        # app -> lib: live modules importing the one unfinished module
        self.assertEqual(by_pair[("app", "lib")]["src_state"], "live")
        self.assertEqual(by_pair[("app", "lib")]["dst_state"], "unfinished")
        # lib -> app: the dead-end module is the one doing the importing, and
        # lib.core's condition must not leak into a ribbon it takes no part in
        self.assertEqual(by_pair[("lib", "app")]["src_state"], "deadend")
        self.assertEqual(by_pair[("lib", "app")]["dst_state"], "live")

    def _map(self, fixture=None):
        """The generated overview, as text."""
        from cobblerpy.report import write_map
        t = Tree(fixture or self.FIXTURE)
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            return fh.read()

    def test_every_ribbon_is_painted_by_an_inline_condition_gradient(self):
        """Inline style, not a stroke attribute.

        The trace veins shipped flat grey for exactly this reason: a
        presentation attribute loses to any stylesheet declaration, and these
        live inside class="chart" too.
        """
        import re
        doc = self._map()
        # The paint is a property of each parsed <path>, so it is read off that
        # element's own style attribute instead of pattern-matched against the
        # whole document -- which also searches the project source this page
        # embeds.
        paths = [a for t, a in Page(doc).elements
                 if t == "path" and a.get("class") == "ribbon"]
        self.assertEqual(len(paths), 3,
                         f"expected a ribbon per folder pair, got {len(paths)}")
        painted = [re.fullmatch(r"stroke:url\(#ribbon_[a-z]+_[a-z]+_\d+\)",
                                a.get("style", "")) for a in paths]
        self.assertTrue(all(painted), "a ribbon is not coloured by condition: "
                        + str([a.get("style") for a, m in zip(paths, painted) if not m]))

    def test_the_prose_quotes_the_ribbons_it_actually_drew(self):
        """Per-corpus numbers in generic prose are a lie waiting for a reader.

        The first draft of this sentence hardcoded "41 ribbons rather than 1,104
        lines" -- true of one corpus and false of every other, rendered into
        every map. The figures are counted off the drawn SVG instead.
        """
        import re
        doc = self._map()
        lede = re.search(r'<p class="lede">(.*?)</p>', doc, re.S)
        self.assertIsNotNone(lede)
        text = re.sub(r"\s+", " ", lede.group(1))
        # the fixture draws 3 ribbons carrying 4 imports
        self.assertIn("3 ribbons", text)
        self.assertIn("4 lines", text)
        self.assertNotIn("Nothing is connected up here", text)

    def test_a_ribbon_is_a_stroked_curve_and_never_a_filled_shape(self):
        """A path defaults to fill:black. A bowed ribbon would be a blob."""
        import re
        doc = self._map()
        style = re.search(r"#graph \.ribbon\{([^}]*)\}", doc)
        self.assertIsNotNone(style, "no #graph .ribbon rule in the document")
        self.assertIn("fill:none", style.group(1).replace(" ", ""))

    def test_ribbons_are_drawn_over_the_boxes_but_under_the_cards(self):
        """Above the folder RECTS, below the CARDS. Both halves matter.

        Drawn under the folders they are invisible: `.fbox` has an opaque fill,
        so a ribbon only showed in the ~14px gap between two boxes. The first
        render of this feature produced 41 correct, gradient-stroked, totally
        unseeable ribbons on the corpus. Drawn over the cards they would bury
        the thing the reader is here to read. Between the two is the only
        position that works.
        """
        # PARSED document order. doc.index() finds the first place a spelling
        # appears, and on this page that can be inside the embedded source.
        page = Page(self._map())
        order = [a.get("class") for t, a in page.elements
                 if t == "g" and a.get("class") in ("folders", "ribbons", "nodes")]
        self.assertEqual(order[:3], ["folders", "ribbons", "nodes"],
                         f"layer order is {order[:3]}")

    def test_one_ribbon_per_ordered_folder_pair_that_carries_imports(self):
        ribbons, _s, _f = self._ribbons()
        got = sorted((r["src"], r["dst"], r["count"]) for r in ribbons)
        self.assertEqual(got, [("app", "lib", 2),
                               ("lib", "app", 1),
                               ("tests", "app", 1)])


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

    def _run(self, probe, graph_nodes=(), fixture=None):
        import json, re, shutil, subprocess
        if not shutil.which("node"):
            self.skipTest("node not installed")
        t = Tree(fixture or self.FIXTURE)
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        # Kept for tests that must assert about the STYLESHEET, not just the
        # markup -- a vein's paint is decided by the cascade, not by the tag.
        self._doc = doc
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

    def test_enter_on_a_focused_card_does_what_a_click_does(self):
        """Enter opened the panel and never the trace, so from the keyboard
        the flowchart could not be reached at all."""
        seen = self._run("""
const by = {};
GRAPH_NODES.forEach(n => { by[n.dataset.name] = n; });
by['b'].on.keydown({key: 'Enter', preventDefault(){}});
console.log(JSON.stringify({
  overviewGone: document.getElementById('graph').hidden,
  of: document.getElementById('traceof').textContent}));
""", graph_nodes=["a", "b", "c", "d", "island"])
        self.assertTrue(seen["overviewGone"], "Enter did not open the flowchart")
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

    def test_trace_veins_carry_the_condition_of_the_nodes_they_join(self):
        """A vein is coloured by a gradient from its source node's condition to
        its destination's -- live green flowing into unfinished amber -- so the
        path is read through the conditions, matching the legend the nodes use.

        The destination here is UNFINISHED, not a dead end, and that is
        deliberate: a dead end is filtered out of the trace entirely
        (TRACE_HIDE, and test_a_dead_end_is_left_out_of_the_trace_and_counted
        above), so a fixture built on one would be asserting against a node the
        trace is never going to draw.
        """
        import re
        from cobblerpy.svgmap import _PALETTE
        # lib (live) -> work (unfinished: reached, and carrying a TODO)
        fixture = {
            "run.py": 'import lib\nif __name__ == "__main__":\n    lib.go()\n',
            "lib.py": "import work\ndef go():\n    return work.run()\n",
            "work.py": "def run():\n    # TODO: finish the second half\n    return 1\n",
        }
        seen = self._run("""
drawTrace('lib');
console.log(JSON.stringify({markup: document.getElementById('trace').innerHTML}));
""", fixture=fixture)
        markup = seen["markup"]
        states = dict(re.findall(r'data-name="([^"]+)" data-state="([^"]+)"', markup))
        self.assertEqual(states.get("lib"), "live")
        self.assertEqual(states.get("work"), "unfinished")
        # Every vein carries a condition gradient as an INLINE STYLE, not as a
        # presentation attribute -- and that distinction is the whole test.
        #
        # A presentation attribute loses to ANY stylesheet declaration, and the
        # trace <svg> itself carries class="chart", so `.chart .edge{stroke:...}`
        # matched every vein and repainted it flat grey. The markup was right and
        # the render was wrong: getComputedStyle in a real browser returned
        # rgb(38,43,54) on a path whose stroke attribute was url(#vein_...).
        # An inline style beats a stylesheet rule, so the paint survives the
        # cascade wherever the svg is nested.
        veins = re.findall(
            r'<path class="edge"[^>]*style="stroke:url\(#(vein_[a-z]+_[a-z]+_\d+)\)"',
            markup)
        self.assertTrue(veins, "no vein carries a condition gradient")
        flat = re.findall(
            r'<path class="edge"(?![^>]*style="stroke:url\(#vein_)', markup)
        self.assertEqual(flat, [], "a vein is not coloured by condition")
        # ...and no stylesheet rule may out-rank it with !important.
        self.assertNotRegex(
            self._doc, r"\.(chart|edge)[^{}]*\{[^{}]*stroke:[^;}]*!important",
            "an !important stroke rule would beat the inline gradient")
        # the lib->work vein flows live -> unfinished
        crossing = [v for v in veins if v.startswith("vein_live_unfinished_")]
        self.assertTrue(crossing, f"no live->unfinished vein among {veins}")
        # and that gradient's stops are exactly the two palette colours, in order
        grad = re.search(
            r'<linearGradient id="' + crossing[0]
            + r'"([^>]*)>(.*?)</linearGradient>', markup, re.S)
        self.assertIsNotNone(grad, "the live->unfinished gradient was not defined")
        stops = re.findall(r'stop-color="([^"]+)"', grad.group(2))
        self.assertEqual(stops, [_PALETTE["live"][0], _PALETTE["unfinished"][0]])
        # One gradient per vein, in user space, running DOWN the vein.
        # objectBoundingBox is not usable here and this asserts so: two cards
        # in the same column are joined by a perfectly vertical path whose
        # bounding box has zero width, and SVG drops an objectBoundingBox paint
        # on a degenerate box -- headless Chromium painted 0 of 181 pixels on
        # exactly that line while the diagonal beside it graded correctly.
        attrs = grad.group(1)
        self.assertIn('gradientUnits="userSpaceOnUse"', attrs)
        y1 = float(re.search(r' y1="([-\d.]+)"', attrs).group(1))
        y2 = float(re.search(r' y2="([-\d.]+)"', attrs).group(1))
        self.assertGreater(y2, y1, "the gradient does not run down the vein")


class TestSalvage(unittest.TestCase):
    """What is in the code nothing reaches, and what the evidence says of it.

    Every verdict here is reachable from a purpose-built tree. A verdict that
    no fixture produces is a branch nobody has run -- and `original
    direction`, the one that matters most, fires on none of the corpus, so
    without a fixture for it there would be no evidence it works at all.
    """

    def _repo(self, commits, extra=None):
        """A repository with dated commits, then optional untracked files."""
        import subprocess
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)

        def git(*args):
            subprocess.run(("git", "-C", root) + args, capture_output=True)

        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "Tester")
        for when, files in commits:
            for relpath, text in files.items():
                write(root, relpath, text)
            git("add", "-A")
            subprocess.run(
                ("git", "-C", root, "commit", "-q", "-m", when, "--date", when),
                capture_output=True,
                env={**os.environ, "GIT_COMMITTER_DATE": when})
        for relpath, text in (extra or {}).items():
            write(root, relpath, text)
        return root

    SHARED = ("def reconcile_ledger():\n    return 1\n"
              "def settle_batch():\n    return 2\n"
              "def post_journal():\n    return 3\n")

    def test_every_verdict_is_reachable_and_carries_its_caveat(self):
        from cobblerpy import survey as do_survey, salvage
        root = self._repo(
            [("2024-01-01T00:00:00",
              {"main.py": "import keeper\nif __name__ == '__main__':\n"
                          "    keeper.go()\n",
               # Here FIRST, and superseded later by keeper.py.
               "keeper_old.py": self.SHARED}),
             ("2025-06-01T00:00:00",
              {"keeper.py": "def go():\n    return reconcile_ledger()\n" + self.SHARED,
               "wired.py": "import keeper\ndef unique_capability_here():\n"
                           "    return keeper.go()\n",
               "lonely.py": "x = 1\n"})],
            extra={"scratch.py": "import keeper\ndef scratch_only():\n"
                                 "    return keeper.go()\n"})
        s = do_survey(root)
        rows = {r["module"]: r for r in salvage.find(
            s.project, s.modules_by_key, s.history)}
        self.assertEqual(rows["keeper_old"]["verdict"], salvage.ORIGINAL_DIRECTION)
        self.assertEqual(rows["scratch"]["verdict"], salvage.NEVER_COMMITTED)
        self.assertEqual(rows["wired"]["verdict"], salvage.UNIQUE_AND_WIRED)
        self.assertEqual(rows["lonely"]["verdict"], salvage.ISOLATED)
        for row in rows.values():
            self.assertTrue(row["caveat"], f"{row['module']} has no caveat")
            self.assertNotEqual(row["caveat"], row["verdict"])

    def test_the_original_direction_is_the_one_that_came_first(self):
        """The whole claim is chronological, so it has to flip with the dates.

        On the corpus this verdict fires on nothing: of the pairs whose dates
        are knowable, the unreachable one is newer far more often than older.
        Which is exactly why it needs a fixture where it is TRUE and one where
        it is false.
        """
        from cobblerpy import survey as do_survey, salvage
        for label, old_first in (("the old one was there first", True),
                                 ("the old one came later", False)):
            with self.subTest(label):
                dates = (("2024-01-01T00:00:00", "2025-06-01T00:00:00")
                         if old_first else
                         ("2025-06-01T00:00:00", "2024-01-01T00:00:00"))
                root = self._repo([
                    (dates[1] if old_first else dates[0],
                     {"main.py": "import keeper\nif __name__ == '__main__':\n"
                                 "    keeper.go()\n"}),
                ] + [])
                # Two commits in the order the subtest wants.
                import subprocess
                def commit(when, relpath, text):
                    write(root, relpath, text)
                    subprocess.run(("git", "-C", root, "add", "-A"),
                                   capture_output=True)
                    subprocess.run(
                        ("git", "-C", root, "commit", "-q", "-m", when,
                         "--date", when), capture_output=True,
                        env={**os.environ, "GIT_COMMITTER_DATE": when})
                first, second = (("keeper_old.py", self.SHARED),
                                 ("keeper.py",
                                  "def go():\n    return reconcile_ledger()\n"
                                  + self.SHARED))
                if not old_first:
                    commit(dates[1], second[0], second[1])
                    commit(dates[0], first[0], first[1])
                else:
                    commit(dates[0], first[0], first[1])
                    commit(dates[1], second[0], second[1])
                s = do_survey(root)
                rows = {r["module"]: r for r in salvage.find(
                    s.project, s.modules_by_key, s.history)}
                self.assertIn("keeper_old", rows)
                self.assertEqual(
                    rows["keeper_old"]["verdict"],
                    salvage.ORIGINAL_DIRECTION if old_first
                    else salvage.LATER_ATTEMPT,
                    rows["keeper_old"]["counterpart"])

    def test_without_a_history_nothing_is_called_never_committed(self):
        """True of every file, so it is a fact about the survey, not the file."""
        from cobblerpy import salvage
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "spare.py": "x = 1\n"})
        self.addCleanup(t.close)
        s = t.survey()                        # with_history=False
        rows = salvage.find(s.project, s.modules_by_key, s.history)
        self.assertTrue(rows, "the fixture left nothing behind")
        self.assertNotIn(salvage.NEVER_COMMITTED,
                         {r["verdict"] for r in rows})

    def test_a_date_in_the_filename_counts_when_git_does_not_know(self):
        """Untracked dated scratch is exactly the tree this gets asked about."""
        from cobblerpy import salvage
        self.assertEqual(salvage._stamped("x/_combined_e2e_20260619.py"),
                         "2026-06-19")
        self.assertEqual(salvage._stamped("attest_canon_2026-08-27.py"),
                         "2026-08-27")
        self.assertIsNone(salvage._stamped("build_deb.py"))
        self.assertIsNone(salvage._stamped("v1500_run2.py"),
                          "a version number is not a date")
        self.assertIsNone(salvage._stamped("port_29999.py"))
        # Eight digits that are not a date. A pattern of \d{4}\d{2}\d{2}
        # takes all of these, and the two fixtures above do not catch it --
        # they are rejected by their SHAPE, not by the month and day ranges.
        self.assertIsNone(salvage._stamped("report_20261332.py"),
                          "month 13 is not a month")
        self.assertIsNone(salvage._stamped("chunk_20260000.py"),
                          "month 00 and day 00 are not a date")
        self.assertIsNone(salvage._stamped("build_20260732.py"),
                          "day 32 is not a day")
        self.assertIsNone(salvage._stamped("run_19991231.py"),
                          "the century is anchored at 20xx")

    def test_an_untracked_dated_file_is_dated_by_its_name(self):
        """The date in the filename has to actually be USED, not just parsed.

        _stamped() can be perfect and _earliest() can still ignore it: the
        untracked file is the one with no git date, which is precisely the
        case the filename is there to cover. Checking the regex alone left
        that unproven -- and this fixture is the only thing in the suite where
        the date comes from anywhere but git.
        """
        from cobblerpy import survey as do_survey, salvage
        root = self._repo(
            [("2025-06-01T00:00:00",
              {"main.py": "import keeper\nif __name__ == '__main__':\n"
                          "    keeper.go()\n",
               "keeper.py": "def go():\n    return reconcile_ledger()\n"
                            + self.SHARED})],
            # Never committed, and named with a date two years EARLIER.
            extra={"keeper_20240101.py": self.SHARED})
        s = do_survey(root)
        rows = {r["module"]: r for r in salvage.find(
            s.project, s.modules_by_key, s.history)}
        row = rows["keeper_20240101"]
        self.assertEqual(row["counterpart"]["mine_from"], "the filename",
                         "the date in the name was not used")
        self.assertEqual(row["counterpart"]["mine"], "2024-01-01")
        self.assertEqual(row["counterpart"]["theirs_from"], "git")
        self.assertEqual(row["verdict"], salvage.ORIGINAL_DIRECTION,
                         "a file dated only by its name cannot be placed in "
                         "time, so it fell through to another verdict")

    def test_the_earlier_of_the_two_dates_wins(self):
        """A file can be dated twice, and the dates can disagree.

        A dated scratch file committed months after it was written has a git
        date that says when it was swept into the repository, not when the
        work happened. The earlier of the two is the one that bears on "which
        came first", and the record says which source it came from.
        """
        from cobblerpy import salvage
        files = {"keep_20230101.py": {"first_seen": "2025-06-01"}}
        self.assertEqual(salvage._earliest("keep_20230101.py", files),
                         ("2023-01-01", "the filename"))
        # And the other way round: committed before somebody renamed it with
        # a later date on it.
        files = {"keep_20260101.py": {"first_seen": "2025-06-01"}}
        self.assertEqual(salvage._earliest("keep_20260101.py", files),
                         ("2025-06-01", "git"))
        self.assertEqual(salvage._earliest("plain.py",
                                           {"plain.py": {"first_seen": "2025-06-01"}}),
                         ("2025-06-01", "git"))
        self.assertEqual(salvage._earliest("dated_20240202.py", {}),
                         ("2024-02-02", "the filename"))
        self.assertEqual(salvage._earliest("nothing.py", {}), (None, None))

    def test_ranked_by_the_work_in_it(self):
        from cobblerpy import salvage
        # Named so that alphabetical order is the REVERSE of size order.
        # With small/middle/big the two agreed, and ranking by name passed.
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "a_smallest.py": "x = 1\n",
                  "c_biggest.py": "y = 2\n" * 300,
                  "b_middling.py": "z = 3\n" * 40})
        self.addCleanup(t.close)
        s = t.survey()
        rows = salvage.find(s.project, s.modules_by_key, s.history)
        self.assertEqual([r["module"] for r in rows],
                         ["c_biggest", "b_middling", "a_smallest"])

    def test_the_map_carries_what_was_left_behind(self):
        t = Tree({"run.py": 'if __name__ == "__main__":\n    pass\n',
                  "stranded.py": "def only_here():\n    return 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            doc = fh.read()
        page = Page(doc)
        text = " ".join(page.text_parts)
        self.assertIn("what was left behind", text.lower())
        self.assertIn("stranded.py", text)
        # And it is a LINK to the module's own card, not a dead mention.
        self.assertTrue(page.find("a", **{"data-goto": "stranded"}),
                        "the module named in the summary cannot be opened")

    def test_the_section_says_so_when_nothing_was_left_behind(self):
        """The negative branch, which is where a check like this goes stale."""
        t = Tree({"run.py": "import lib\nif __name__ == '__main__':\n"
                            "    lib.go()\n",
                  "lib.py": "def go():\n    return 1\n"})
        self.addCleanup(t.close)
        s = t.survey()
        self.assertEqual(s.project.orphans, [])
        out = os.path.join(t.dir, "map.html")
        from cobblerpy.report import write_map
        write_map(s.project, s.frontier, s.history, out,
                  origins=s.origins, modules_by_key=s.modules_by_key)
        with open(out, encoding="utf-8") as fh:
            text = " ".join(Page(fh.read()).text_parts)
        self.assertIn("Nothing was left behind", text)


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

    def test_what_a_tool_loads_is_a_place_the_code_runs_from(self):
        """Held back from the orphan list, and a reachability root too.

        Saying "pytest loads conftest.py" and then reporting everything
        conftest imports as unreached is two statements about one file that
        cannot both be true. On the corpus it left 312 modules -- a third of
        the project -- coloured "no static path reaches it".
        """
        t = Tree({
            "run.py": 'if __name__ == "__main__":\n    pass\n',
            "tests/conftest.py": "import helper\n",
            "helper.py": "def fixture_support():\n    return 1\n",
            "stranded.py": "def nothing_calls_me():\n    return 2\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        self.assertIn("helper", s.project.reachable,
                      "the module conftest imports is still called unreachable")
        self.assertNotIn("stranded", s.project.reachable,
                         "everything became reachable, so this proves nothing")
        # And the map agrees, because it colours from layers(), not reachable.
        placed = {n for names in s.project.layers().values() for n in names}
        self.assertIn("helper", placed)
        self.assertNotIn("stranded", placed)

    def test_both_reachability_walks_use_the_same_roots(self):
        """Two walks, one answer.

        reachable and layers() each did their own breadth-first search from
        their own idea of a starting point. Fixing one left the other
        disagreeing with it, and nothing failed: the totals counted a module
        one way while the chart coloured it the other.
        """
        t = Tree({
            "run.py": 'if __name__ == "__main__":\n    pass\n',
            "tests/conftest.py": "import helper\n",
            "helper.py": "def go():\n    return 1\n",
            "tests/test_thing.py": "import fixtures_only\n",
            "fixtures_only.py": "def make():\n    return 2\n",
            "stranded.py": "x = 1\n",
        })
        self.addCleanup(t.close)
        s = t.survey()
        placed = {n for names in s.project.layers().values() for n in names}
        self.assertEqual(placed, s.project.reachable,
                         "the depth layering and the reachable set disagree")
        self.assertIn("fixtures_only", placed)
        self.assertTrue(s.project.roots(), "there are no roots at all")

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


try:
    import PySide6  # noqa: F401
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False
_HAVE_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@unittest.skipUnless(_HAVE_QT, "PySide6 not installed")
@unittest.skipUnless(_HAVE_DISPLAY, "no display")
class TestDesktopWindow(unittest.TestCase):
    """The window itself, by building it and asking the widget tree.

    Skipped where there is no Qt or no display -- but the six methods are always
    COLLECTED, so `Ran N tests` does not move with the optional gui extra (a
    setUpClass SkipTest drops them from the count; a class skipUnless keeps them
    counted-and-skipped). What it does NOT do is search the source for widget
    names: it constructs the real thing, drives the real handlers and reads the
    real objects back, because a test that matches strings tells you how the
    file is spelled and nothing about what it builds.
    """

    @classmethod
    def setUpClass(cls):
        from PySide6 import QtCore, QtGui, QtWidgets
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
    # commit that added it: on a second private project the findings went from 20 to 7 and every
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


class TestTypedLibraryDeclarations(unittest.TestCase):
    """Empty bodies a typed library writes on purpose, measured on itsdangerous.

    Its @overload signatures and a `t.Protocol[T]` class topped the frontier as
    "stub ellipsis", and painted two finished modules as dead ends. A base
    class whose NotImplementedError every subclass replaces painted a third.
    """

    LIB = {
        "main.py": ("import lib\nimport impl\n\nif __name__ == '__main__':\n"
                    "    lib.P().dumps(1)\n    impl.Algo().sign(b'')\n"
                    "    lib.Base().run()\n"),
        "lib.py": (
            "import typing as t\nimport abc\nfrom abc import ABC, abstractmethod\n"
            "from typing import overload\n\n"
            "T = t.TypeVar('T')\n\n\n"
            "class P(t.Protocol[T]):\n"
            "    def dumps(self, obj: T) -> str: ...\n\n\n"
            "@overload\ndef coerce(x: int) -> int: ...\n"
            "@t.overload\ndef coerce(x: str) -> str: ...\n"
            "def coerce(x):\n    return x\n\n\n"
            "class Base(ABC):\n"
            "    @abstractmethod\n"
            "    def run(self):\n        raise NotImplementedError\n\n"
            "    @abc.abstractmethod\n"
            "    def stop(self): ...\n\n\n"
            "class Algorithm:\n"
            "    def sign(self, key):\n        raise NotImplementedError()\n\n\n"
            "def unfinished(x):\n    ...\n\n\n"
            "class Store:\n"
            "    def save(self, row):\n        raise NotImplementedError\n"),
        "impl.py": ("from lib import Algorithm, unfinished, Store\n\n\n"
                    "class Algo(Algorithm):\n"
                    "    def sign(self, key):\n        return unfinished(key)\n\n\n"
                    "def keep(row):\n    return Store().save(row)\n"),
    }

    def setUp(self):
        from cobblerpy.deadends import find
        t = Tree(self.LIB)
        self.addCleanup(t.close)
        self.s = t.survey()
        row = [r for r in self.s.frontier if r["module"] == "lib"][0]
        self.stubs = {name for kind in ("stub_ellipsis", "stub_pass",
                                        "not_implemented")
                      for name, _ln in row["signals"].get(kind, [])}
        self.ends = {d["qualname"] for d in
                     find(self.s.project, self.s.modules_by_key, self.s.origins)}

    def test_a_subscripted_protocol_method_is_a_declaration(self):
        self.assertNotIn("P.dumps", self.stubs)
        self.assertNotIn("P.dumps", self.ends)

    def test_overload_signatures_are_declarations(self):
        self.assertNotIn("coerce", self.stubs)

    def test_abstract_methods_are_declarations(self):
        self.assertNotIn("Base.run", self.stubs)
        self.assertNotIn("Base.stop", self.stubs)

    def test_a_not_implemented_that_a_subclass_replaces_is_a_declaration(self):
        self.assertNotIn("Algorithm.sign", self.stubs)
        self.assertNotIn("Algorithm.sign", self.ends)

    def test_real_stubs_are_still_reported(self):
        self.assertIn("unfinished", self.stubs)
        self.assertIn("Store.save", self.stubs)
        self.assertIn("Store.save", self.ends)


class TestSubclassExemptionIsNarrow(unittest.TestCase):
    """The reviewer's fixture. The first cut of "a subclass replaces it"
    fired when ANY class ANYWHERE with the same NAME redefined the method, and
    exempted every `pass` on an ABC: 4 real signals became 0."""

    FIXTURE = {
        "pkg/__init__.py": "",
        "pkg/other.py": ("class Runner:\n    def run(self):\n        return 1\n"
                         "class Fast(Runner):\n    def run(self):\n        return 2\n"),
        "pkg/base.py": (
            "from abc import ABC\n"
            "class Exporter:\n    def export(self, data):\n        raise NotImplementedError\n"
            "class Csv(Exporter):\n    def export(self, data):\n        return ','.join(data)\n"
            "class Json(Exporter):\n    pass\n"
            "class Xml(Exporter):\n    pass\n\n"
            "class Plugin(ABC):\n    def setup(self):\n        pass\n"
            "    def teardown(self):\n        pass\n\n"
            "class Runner:\n    def run(self):\n        raise NotImplementedError\n"),
        "pkg/use.py": ("from pkg.base import Json, Plugin, Runner\n"
                       "def go():\n    Json().export([])\n    Plugin().setup()\n"
                       "    Runner().run()\n"),
        # Every subclass replaces it, but the base itself is called.
        "pkg/tool.py": ("class Tool:\n    def use(self):\n        raise NotImplementedError\n"
                        "class Saw(Tool):\n    def use(self):\n        return 1\n"
                        "def pick():\n    return Tool().use()\n"),
        # Every subclass replaces it, and nothing calls the base itself.
        "pkg/shapes.py": ("class Shape:\n    def area(self):\n        raise NotImplementedError\n"
                          "class Sq(Shape):\n    def area(self):\n        return 1\n"),
        "pkg/draw.py": ("from pkg import shapes\n"
                        "class Circle(shapes.Shape):\n    def area(self):\n        return 3\n"
                        "def total():\n    return shapes.Sq().area() + Circle().area()\n"),
    }

    def setUp(self):
        t = Tree(self.FIXTURE)
        self.addCleanup(t.close)
        s = t.survey()
        self.stubs = {(r["module"], name) for r in s.frontier
                      for kind in ("stub_pass", "stub_ellipsis", "not_implemented")
                      for name, _ln in r["signals"].get(kind, [])}

    def test_the_real_stubs_are_kept(self):
        self.assertEqual(self.stubs - {("pkg.shapes", "Shape.area")},
                         {("pkg.base", "Exporter.export"), ("pkg.base", "Plugin.setup"),
                          ("pkg.base", "Plugin.teardown"), ("pkg.base", "Runner.run"),
                          ("pkg.tool", "Tool.use")})

    def test_a_generic_or_abc_class_is_still_a_real_class_for_dead_ends(self):
        """`Generic` only became visible once `Generic[T]` resolved to its
        name, and it exempted every method of every generic class."""
        from cobblerpy.deadends import find
        t = Tree({"main.py": "import box\nif __name__ == '__main__':\n"
                             "    box.Box().put(1)\n    box.Hook().fire()\n",
                  "box.py": "import typing as t\nfrom abc import ABC\nT = t.TypeVar('T')\n"
                            "class Box(t.Generic[T]):\n    def put(self, x):\n        pass\n"
                            "class Hook(ABC):\n    def fire(self):\n        pass\n"})
        self.addCleanup(t.close)
        s = t.survey()
        ends = {d["qualname"] for d in find(s.project, s.modules_by_key, s.origins)}
        self.assertEqual(ends, {"Box.put", "Hook.fire"})

    def test_a_subclass_that_calls_super_does_not_replace_the_base(self):
        """`return super().handle(x)` redefines the method and still lands
        on the base, which raises. That is not a replacement."""
        from cobblerpy.deadends import find
        t = Tree({"main.py": "import h\nif __name__ == '__main__':\n    h.A().handle(1)\n",
                  "h.py": ("class Handler:\n    def handle(self, x):\n"
                           "        raise NotImplementedError\n"
                           "class A(Handler):\n    def handle(self, x):\n"
                           "        return super().handle(x)\n"
                           "class B(Handler):\n    def handle(self, x):\n"
                           "        return super(B, self).handle(x)\n")})
        self.addCleanup(t.close)
        s = t.survey()
        row = [r for r in s.frontier if r["module"] == "h"][0]
        self.assertIn("Handler.handle",
                      {n for n, _l in row["signals"].get("not_implemented", [])})
        self.assertIn("Handler.handle", {d["qualname"] for d in
                      find(s.project, s.modules_by_key, s.origins)})

    def test_a_base_every_subclass_replaces_is_still_exempt(self):
        self.assertNotIn(("pkg.shapes", "Shape.area"), self.stubs)


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
