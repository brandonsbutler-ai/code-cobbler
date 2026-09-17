"""What the project's own packaging files declare.

This is the only place in cobblerpy where "nothing imports it" can be answered
with a fact rather than an inference. `[project.scripts]` in pyproject.toml
names a module and a function; a plugin entry point names a module a framework
will import at run time. Both are declarations by the people who wrote the
code, sitting in the repository, and reading them costs one file open.

Nothing here executes anything. setup.py is a Python program and running it to
find out what it declares is exactly the thing this tool promises not to do,
so setup.py is read only through setup.cfg, which is data.
"""

import configparser
import os
import re

try:
    import tomllib                                  # Python 3.11+
except ModuleNotFoundError:                         # pragma: no cover
    tomllib = None


# "pkg.module:function" or "pkg.module" -- the module is everything before the
# first colon. Anchored, because an entry point value with a space or a path
# separator in it is not a module path and guessing at it would invent a name.
_SPEC = re.compile(r"\A(?P<module>[A-Za-z_][\w.]*)(?::[\w.]+)?"
                   r"(?:\s*\[[^\]]*\])?\Z")


def _module_of(spec):
    match = _SPEC.match((spec or "").strip())
    return match.group("module") if match else None


def _from_pyproject(path):
    if tomllib is None:
        return {}
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError):
        return {}                      # unreadable or malformed: say nothing
    project = data.get("project") or {}
    found = {}

    def take(table, by):
        for name, spec in (table or {}).items():
            module = _module_of(spec if isinstance(spec, str) else "")
            if module:
                found[module] = {"spec": f"{name} = {spec}", "by": by,
                                 "source": "pyproject.toml"}

    take(project.get("scripts"), "a console script")
    take(project.get("gui-scripts"), "a GUI script")
    for group, table in (project.get("entry-points") or {}).items():
        take(table, f"the {group} plugin group")
    return found


def _from_setup_cfg(path):
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, UnicodeDecodeError, configparser.Error):
        return {}
    found = {}
    if not parser.has_section("options.entry_points"):
        return found
    for group, block in parser.items("options.entry_points"):
        for line in (block or "").splitlines():
            if "=" not in line:
                continue
            name, _, spec = line.partition("=")
            module = _module_of(spec)
            if module:
                found[module] = {
                    "spec": f"{name.strip()} = {spec.strip()}",
                    "by": ("a console script" if group == "console_scripts"
                           else f"the {group} plugin group"),
                    "source": "setup.cfg"}
    return found


def declared_modules(root):
    """Dotted module name -> how the packaging metadata names it.

    A module declared as `pkg.cli:main` is reachable through `pkg.cli`, and so
    is anything `pkg.cli` imports -- that second part is the graph's job, not
    this one's.
    """
    found = {}
    cfg = os.path.join(root, "setup.cfg")
    if os.path.isfile(cfg):
        found.update(_from_setup_cfg(cfg))
    toml = os.path.join(root, "pyproject.toml")
    if os.path.isfile(toml):
        found.update(_from_pyproject(toml))     # pyproject wins where both exist
    return found
