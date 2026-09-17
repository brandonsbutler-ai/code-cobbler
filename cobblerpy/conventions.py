"""Code that something loads without ever importing it.

"Nothing imports this" is the most useful thing the graph says and the easiest
thing to get wrong. A large part of a real Python tree is reached by a named
tool following a named rule: pytest collects `conftest.py` by sitting in the
directory, `python -m pkg` runs `__main__.py`, a web server imports `wsgi.py`
because its own configuration says to, and pip installs whatever
`[project.scripts]` points at. None of that is an import statement, so a
parser that only reads import statements calls all of it abandoned.

Reporting a file as unused when a documented rule says otherwise is worse than
saying nothing: it is wrong in a way the reader can check in five seconds, and
once they have checked one they stop believing the rest.

Two tiers, kept apart on purpose:

  DECLARED     the project's own packaging metadata names the module. This is
               proven -- it is read out of pyproject.toml or setup.cfg, not
               guessed. `cobblerpy = "cobblerpy.cli:main"` is a fact about
               this repository.

  CONVENTIONAL a named tool loads files with this name or in this directory,
               by a rule that tool documents. Strong, but still an inference:
               a file called `conftest.py` in a tree with no pytest is just a
               file. The record says which tool, so the reader can dismiss it.

Anything matching neither is a genuine "no path found", and the salvage read
in abandonment.py is about those.
"""

import os
import re

from . import metadata


class Convention:
    """One rule, the tool that applies it, and how sure we are."""

    __slots__ = ("name", "loader", "why", "proven")

    def __init__(self, name, loader, why, proven=False):
        self.name = name
        self.loader = loader
        self.why = why
        self.proven = proven

    def as_dict(self):
        return {"name": self.name, "loader": self.loader, "why": self.why,
                "proven": self.proven}

    def __repr__(self):                                    # pragma: no cover
        return f"<Convention {self.name} by {self.loader}>"


def _basename(rule):
    """Match on the file's own name, exactly."""
    return lambda relpath: os.path.basename(relpath) == rule


def _basename_re(pattern):
    """Match the file's own name against an anchored pattern.

    Anchored at both ends deliberately. `test_` as a prefix test matches
    `test_helpers.py`, which is right, and also `testing_utils.py`, which is
    not -- and unanchored `_test` matches `latest_run.py`.
    """
    rx = re.compile(pattern + r"\.py\Z")
    return lambda relpath: bool(rx.match(os.path.basename(relpath)))


def _in_directory(folder):
    """Match any file inside a directory with this name, at any depth."""
    return lambda relpath: folder in relpath.replace("\\", "/").split("/")[:-1]


# Ordered: the first match wins, so the most specific rules come first. Every
# entry names a real tool and a rule that tool documents -- a convention
# nobody implements is a guess with a confident voice.
_RULES = [
    (_basename("conftest.py"),
     Convention("conftest", "pytest",
                "pytest loads conftest.py from the directory it is collecting, "
                "with no import anywhere")),
    (_basename_re(r"test_[^/\\]*"),
     Convention("test module", "a test runner",
                "collected by name, not imported by the code under test")),
    (_basename_re(r"[^/\\]*_test"),
     Convention("test module", "a test runner",
                "collected by name, not imported by the code under test")),
    (_basename("__init__.py"),
     Convention("package marker", "the import system",
                "imported as the package itself whenever anything under it is")),
    (_basename("__main__.py"),
     Convention("module entry", "python -m",
                "run by `python -m <package>`")),
    (_basename("setup.py"),
     Convention("build script", "pip / setuptools",
                "executed by the installer, never imported")),
    (_basename("manage.py"),
     Convention("management CLI", "Django",
                "the project's own command entry point")),
    (_basename("wsgi.py"),
     Convention("server entry", "a WSGI server",
                "named in the server's configuration, not imported by the app")),
    (_basename("asgi.py"),
     Convention("server entry", "an ASGI server",
                "named in the server's configuration, not imported by the app")),
    (_basename("noxfile.py"),
     Convention("task file", "nox", "read by the tool that runs the sessions")),
    (_basename("tasks.py"),
     Convention("task file", "invoke", "read by the tool that runs the tasks")),
    (_basename("fabfile.py"),
     Convention("task file", "fabric", "read by the tool that runs the tasks")),
    (_basename("dodo.py"),
     Convention("task file", "doit", "read by the tool that runs the tasks")),
    (_basename("sitecustomize.py"),
     Convention("interpreter hook", "CPython",
                "imported at startup if it is on the path")),
    (_basename("usercustomize.py"),
     Convention("interpreter hook", "CPython",
                "imported at startup if it is on the path")),
    (_in_directory("migrations"),
     Convention("migration", "a migration runner",
                "discovered and applied in order by the framework")),
    (_in_directory("plugins"),
     Convention("plugin", "a plugin loader",
                "directory named plugins -- loaded by scan, if anything loads it")),
]


def conventional(relpath):
    """The convention that loads this file, or None. Never raises."""
    if not relpath:
        return None
    for matches, convention in _RULES:
        if matches(relpath):
            return convention
    return None


def reached(root, modules):
    """Map module key -> Convention for everything something else loads.

    Declared beats conventional: a module named in the packaging metadata is
    proven to be an entry point, and saying "pytest probably loads it" over
    the top of that would be a downgrade.
    """
    declared = metadata.declared_modules(root)
    out = {}
    for module in modules:
        key = module.dotted or module.relpath
        named = declared.get(module.dotted or "")
        if named:
            out[key] = Convention(
                "declared entry point", named["by"],
                f"named in {named['source']} as {named['spec']}", proven=True)
            continue
        found = conventional(module.relpath)
        if found:
            out[key] = found
    return out
