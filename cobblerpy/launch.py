"""Start the tool without an incantation.

`python3 -m cobblerpy <dir> --map <path>` is four decisions before anything
happens: which directory, which flag, where the output goes, and then finding
the file to open it. This module is the version with none of them -- point it
at a folder and a map opens.

It exists to be called from places that have NO TERMINAL: a desktop icon you
drop a folder on, or a right-click entry in the file manager. Nothing printed
to stderr is ever seen there, so EVERY outcome -- including every failure --
goes through `notify`. A launcher that fails silently is worse than no
launcher, because the folder was dropped and nothing happened.
"""

import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser

from . import __version__, survey
from .report import write_map

USAGE = """usage: cobble [FOLDER | FILE.py] | --shelf | --help | --version

  cobble               survey the current folder, write its map beside it, open it
  cobble FOLDER        the same for FOLDER; a .py file means the project it is in
  cobble --shelf       open the shelf: every map already made, newest first
  cobble --version     print the version

The map is written beside the folder, never inside it. The single executable
opens the shelf when it is given nothing, because it is double-clicked."""


# The shelf, and the register behind it. Maps are written BESIDE the projects
# they describe, which is right and also leaves them scattered -- seven files
# named cobblerpy_map_*.html in one directory, no way to tell which was current.
#
# W:\ is the shelf's home because it is the one path visible from both Linux
# and Windows on this machine, so one bookmark works from either side.
# This machine dual-boots, and the projects live on an NTFS partition that is
# a mount point under Linux and a drive letter under Windows -- the SAME disk.
# The shelf and its register go there so both sides list the same maps. Under ~
# each OS would keep its own and neither would see the other's; a hardcoded
# /media/... path does not exist under Windows at all.
# NOT a hardcoded path. An earlier draft of this listed one developer's mount
# point, username and all, in a repository that is public -- the same leak that
# had to be scrubbed out of a sibling project's history. The shared volume is
# named by the environment; the installer writes it into the launcher shim,
# where a machine-specific value belongs.
SHARED = tuple(p for p in (os.environ.get("CODECOBBLER_HOME"), "W:\\", "W:/") if p)
FALLBACK = os.path.expanduser("~/.local/share/codecobbler")


def shared_root(candidates=SHARED):
    """The first shared volume that exists, or None on a machine without one."""
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def shelf_path(candidates=SHARED):
    root = shared_root(candidates)
    return (os.path.join(root, "CodeCobbler.html") if root
            else os.path.join(FALLBACK, "CodeCobbler.html"))


def registry_path(candidates=SHARED):
    root = shared_root(candidates)
    return (os.path.join(root, "CodeCobbler.maps.json") if root
            else os.path.join(FALLBACK, "maps.json"))


def record_map(registry, project, map_path, modules, folder=None):
    """Note that `project` now has a map, replacing any earlier row for it.

    Keyed by project, not appended: nine rows for one project is the scatter
    this exists to fix. The key is the FOLDER when there is one -- keyed by
    name alone, mapping b/src replaced the row for a/src. A row written before
    folders were recorded has only its name to go on.
    """
    rows = shelf_entries(registry)
    rows = [r for r in rows if not _same_project(r, project, folder)]
    # Dated by the MAP, not by the moment it was listed. Seeding several
    # existing maps in one pass otherwise stamps them all with the same minute
    # and "newest first" sorts on a fiction.
    try:
        stamp = time.localtime(os.path.getmtime(map_path))
    except OSError:
        stamp = time.localtime()
    row = {"project": project, "map": map_path, "modules": modules,
           "when": time.strftime("%Y-%m-%d %H:%M", stamp)}
    if folder:
        row["folder"] = folder
    rows.append(row)
    # Written beside it and swapped in whole. Opening the register with "w"
    # emptied it first, so a crash mid-write left a register that no longer
    # parsed -- and a shelf with nothing on it.
    where = os.path.dirname(registry) or "."
    os.makedirs(where, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=where, prefix=".maps-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        os.replace(temporary, registry)
    except BaseException:
        os.unlink(temporary)
        raise
    return rows


def _same_project(row, project, folder):
    """Whether an existing row is the one this map replaces.

    By folder when both have one. A row from before folders were recorded
    has only a name, so its folder is read back from its map's name --
    `<parent>/<name>-map-<stamp>.html` is written beside `<parent>/<name>`.
    A legacy row that cannot be placed is kept: dropping another project's
    row is worse than one row too many.
    """
    if not folder:
        return row.get("project") == project
    if "folder" in row:
        return row.get("folder") == folder
    mapped = str(row.get("map", ""))
    if row.get("project") != project or not os.path.basename(mapped).startswith(
            f"{project}-map-"):
        return False
    return os.path.join(os.path.dirname(mapped), project) == folder


def shelf_entries(registry):
    """Every recorded map, newest first. A missing or unreadable register is
    an empty shelf, never an error: it is a convenience, not the product."""
    try:
        with open(registry, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    # A row is a LINK. One pointing at a map that has been deleted is a broken
    # promise, so the shelf shows what is actually there.
    rows = [r for r in rows
            if isinstance(r, dict) and os.path.isfile(str(r.get("map", "")))]
    return sorted(rows, key=lambda r: r.get("when", ""), reverse=True)


def write_shelf(registry, path=None):
    """Render the shelf. Returns where it was written."""
    path = path or shelf_path()
    rows = shelf_entries(registry)
    if rows:
        body = "".join(
            '<a class="row" href="{href}" title="{f}"><span class="p">{p}</span>'
            '<span class="m">{m} modules</span><span class="w">{w}</span></a>'.format(
                href=html.escape("file://" + str(r.get("map", "")), quote=True),
                f=html.escape(str(r.get("folder", "")), quote=True),
                p=html.escape(str(r.get("project", "?"))),
                m=html.escape(str(r.get("modules", "?"))),
                w=html.escape(str(r.get("when", ""))))
            for r in rows)
    else:
        body = ('<p class="empty">No maps yet. Drop a project folder on the '
                'CodeCobbler icon, or run <code>cobble &lt;folder&gt;</code>.</p>')
    page = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>CodeCobbler</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--bg:#0d1117;--card:#161b22;--line:#30363d;--fg:#e6edf3;--mut:#8b949e;--acc:#58a6ff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:28px}}
h1{{font-size:20px;margin:0 0 2px}} h1 span{{color:var(--acc)}}
.sub{{color:var(--mut);font-size:13px;margin:0 0 20px}}
.row{{display:flex;gap:14px;align-items:baseline;text-decoration:none;color:inherit;
  background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:11px 14px;margin:0 0 8px}}
.row:hover{{border-color:var(--acc)}}
.p{{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;flex:1}}
.m,.w{{color:var(--mut);font-size:12px;white-space:nowrap}}
.empty{{color:var(--mut)}} code{{color:var(--acc)}}
.foot{{color:var(--mut);font-size:12px;margin-top:22px;border-top:1px solid var(--line);padding-top:12px}}
</style>
<h1>Code<span>Cobbler</span></h1>
<p class="sub">{n} mapped. Newest first.</p>
{body}
<p class="foot">Drop a folder on the CodeCobbler icon to map it, or run
<code>cobble &lt;folder&gt;</code>. Each map is written beside the project it
describes, never inside it.</p>
</html>""".format(n=len(rows), body=body)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path


def _notify(message):
    """Say something when there is no terminal to say it in.

    Desktop notification when one is available, and stderr regardless, so the
    same launcher is usable from a shell and from an icon.
    """
    if shutil.which("notify-send"):
        try:
            subprocess.run(["notify-send", "CodeCobbler", message],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass          # the message still reaches stderr below
    print(message, file=sys.stderr)


# What makes a folder the top of a project rather than a folder in one.
PROJECT_MARKERS = (".git", "pyproject.toml", "setup.py", "setup.cfg")


def project_for(path):
    """The project a dropped FILE belongs to.

    The folder holding it was the answer, and for proj/pkg/a.py that is
    proj/pkg -- so the map, written beside it, landed inside proj. The nearest
    folder above it with a project file is the project; with none, the folder
    holding the top of the package it sits in; with no package either, the
    folder holding it. A home directory kept in git (dotfiles) is not a
    project anybody means, and surveying all of it would be a surprise.
    """
    folder = os.path.dirname(os.path.abspath(path))
    never = {os.path.abspath(os.path.expanduser("~")), os.path.abspath(os.sep)}
    probe = folder
    while True:
        if probe not in never and any(os.path.exists(os.path.join(probe, m))
                                      for m in PROJECT_MARKERS):
            return probe
        up = os.path.dirname(probe)
        if up == probe:
            break
        probe = up
    while os.path.isfile(os.path.join(folder, "__init__.py")):
        up = os.path.dirname(folder)
        if up == folder:
            break
        folder = up
    return folder


def map_destination(folder, stamp=None):
    """Where the map goes: beside the project, never inside it.

    Writing it into the surveyed tree means the next survey reads its own
    output, and on a repository that the map turns up as an untracked file in
    somebody's `git status`. The GUI already decided this; same rule here.
    """
    folder = folder.rstrip(os.sep)
    parent = os.path.dirname(folder) or "."
    base = os.path.basename(folder) or "project"
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(parent, f"{base}-map-{stamp}.html")
    # The stamp is to the second, and two runs inside one second otherwise
    # share a name -- the second silently replacing the first.
    n = 2
    while os.path.exists(path):
        path = os.path.join(parent, f"{base}-map-{stamp}-{n}.html")
        n += 1
    return path


def main(argv=None, notify=None, open_url=None, registry=None, shelf=None):
    """Survey one folder and open its map, or open the shelf. Exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    notify = notify or _notify
    open_url = open_url or webbrowser.open
    registry = registry or registry_path()
    shelf = shelf or shelf_path()

    # The single executable IS this launcher, so these are its only way to
    # answer --help or --version; dropped, they surveyed the current folder.
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    if "--version" in argv:
        print(f"cobblerpy {__version__}")
        return 0
    unknown = [a for a in argv if a.startswith("-") and a != "--shelf"]
    if unknown:
        notify(f"unknown option {', '.join(unknown)} -- `cobble --help` lists "
               f"what this takes")
        return 2

    paths = [a for a in argv if not a.startswith("-")]

    # A desktop icon inherits an ARBITRARY working directory -- measured: a
    # launch from this machine handed the launcher the directory of whatever
    # started the session. Surveying that because somebody clicked an icon is
    # not a reasonable thing to do, so the icon asks for the shelf instead and
    # a bare `cobble` in a terminal still means "here".
    if "--shelf" in argv and not paths:
        written = write_shelf(registry, shelf)
        if not open_url("file://" + written):
            notify(f"No browser to open it with; the shelf is at {written}")
        return 0

    if not paths:
        paths = [os.getcwd()]

    if len(paths) > 1:
        # Refused BY NAME AND COUNT rather than reduced. The window used to
        # parse every dropped folder and then keep folders[0], discarding the
        # rest without a word -- input vanishing silently is the one behaviour
        # this tool exists to argue against.
        notify(f"{len(paths)} folders given, and this takes one folder at a "
               f"time. Surveying several as a single map is not built yet, so "
               f"rather than pick one of them silently: "
               f"{', '.join(os.path.basename(p.rstrip(os.sep)) for p in paths)}")
        return 2

    target = os.path.abspath(paths[0])
    if not os.path.exists(target):
        notify(f"{target} does not exist")
        return 1
    if os.path.isfile(target):
        # Dropping one module out of a project is an obvious thing to do, and
        # refusing it would be pedantry.
        target = project_for(target)

    surveyed = survey(target)
    if not surveyed.project.modules:
        notify(f"no Python found under {target}")
        return 1

    destination = map_destination(target)
    write_map(surveyed.project, surveyed.frontier, surveyed.history,
              destination, origins=surveyed.origins,
              modules_by_key=surveyed.modules_by_key)
    count = len(surveyed.project.modules)
    record_map(registry, os.path.basename(target.rstrip(os.sep)) or target,
               destination, count, folder=target)
    write_shelf(registry, shelf)
    # Try FIRST, then say what happened. webbrowser.open returns False rather
    # than raising when it cannot find a browser -- headless, over ssh, inside a
    # container -- and announcing "opening the map" before checking left
    # somebody told it worked with no path to the file that was written.
    mapped = f"{count:,} module{'s' if count != 1 else ''} mapped"
    if open_url("file://" + destination):
        notify(f"{mapped} -- opening the map")
    else:
        notify(f"{mapped}. No browser to open it with; the map is at "
               f"{destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
