#!/usr/bin/env python3
"""Build standalone executables for the host platform.

    cobblerpy   the command line, one file, no Python needed
    CobblerPy   the desktop application -- double-click it, drop a project on
                it, no terminal and no install step

The application is the one that matters here. The people who inherit an
abandoned codebase are not always the people who set up its toolchain, and
"pip install cobblerpy[gui]" is a step between having the problem and seeing
the answer. An icon they can drop a folder onto removes it.

PyInstaller is a BUILD-time tool. It is not a runtime dependency and nothing
it adds changes the library's promise: `import cobblerpy` still pulls in
nothing but the standard library. The desktop build additionally bundles Qt,
which is the window and nothing else.

    python3 -m pip install pyinstaller "PySide6-Essentials"
    python3 packaging/build_standalone.py          # both
    python3 packaging/build_standalone.py --cli    # just the command line
    python3 packaging/build_standalone.py --app    # just the application
"""

import os
import platform
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WINDOWS = platform.system() == "Windows"
MACOS = platform.system() == "Darwin"

CLI_NAME = "cobblerpy"
APP_NAME = "CobblerPy"
LAUNCHER_NAME = "CodeCobbler"


def _run(cmd):
    print("running:", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT).returncode


def _built(name):
    candidates = [os.path.join(ROOT, "dist", name)]
    if WINDOWS:
        candidates.insert(0, os.path.join(ROOT, "dist", name + ".exe"))
    if MACOS:
        candidates.insert(0, os.path.join(ROOT, "dist", name + ".app"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def build_cli():
    cmd = [
        "pyinstaller",
        "--onefile",
        "--name", CLI_NAME,
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        "--console",
        # The library reads source and shells out to git; there is nothing to
        # bundle beyond the package itself.
        "--exclude-module", "PySide6",
        "--exclude-module", "tkinter",
        # The root has to be importable for the launcher's absolute import.
        "--paths", ROOT,
        os.path.join(ROOT, "packaging", "cli_entry.py"),
    ]
    code = _run(cmd)
    if code:
        return code
    built = _built(CLI_NAME)
    if not built:
        print(f"expected dist/{CLI_NAME} but it was not produced", file=sys.stderr)
        return 1
    print(f"\nbuilt {built}  ({os.path.getsize(built) / 1048576:.0f} MB)")
    print(f"Smoke-test it:\n    {built} --version")
    return 0


def build_launcher():
    """The one-file program: double-click for the shelf, drop a folder to map.

    No Qt, so it is a few MB rather than sixty -- this is the artifact to hand
    somebody who has neither Python nor a toolchain.
    """
    cmd = [
        "pyinstaller",
        "--onefile",
        "--name", LAUNCHER_NAME,
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        "--console",
        "--exclude-module", "PySide6",
        "--exclude-module", "tkinter",
        "--paths", ROOT,
        os.path.join(ROOT, "packaging", "launcher_entry.py"),
    ]
    code = _run(cmd)
    if code:
        return code
    built = _built(LAUNCHER_NAME)
    if not built:
        print(f"expected dist/{LAUNCHER_NAME} but it was not produced",
              file=sys.stderr)
        return 1
    print(f"\nbuilt {built}  ({os.path.getsize(built) / 1048576:.0f} MB)")
    print(f"Smoke-test it:\n    {built} <a folder with python in it>")
    return 0


def build_app():
    try:
        import PySide6                                    # noqa: F401
    except ImportError:
        print("PySide6 not found; the application needs it. Install with:\n"
              '    python3 -m pip install "PySide6-Essentials"', file=sys.stderr)
        return 2
    cmd = [
        "pyinstaller",
        "--onefile",
        "--name", APP_NAME,
        # No console window: on Windows a console build flashes a black box
        # behind the application and leaves it open underneath. This is the
        # flag that makes it an app rather than a command that draws a window.
        "--windowed",
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        # Qt ships far more than one window needs, and the difference between
        # excluding the unused parts and not is roughly 200 MB against 60 --
        # which is the difference between a download somebody waits for and
        # one they abandon.
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        # The root has to be importable or the launcher's absolute
        # `from cobblerpy.gui...` cannot resolve during analysis.
        "--paths", ROOT,
        os.path.join(ROOT, "packaging", "app_entry.py"),
    ]
    code = _run(cmd)
    if code:
        return code
    built = _built(APP_NAME)
    if not built:
        print(f"expected dist/{APP_NAME} but it was not produced", file=sys.stderr)
        return 1
    size = os.path.getsize(built) / 1048576 if os.path.isfile(built) else 0
    print(f"\nbuilt {built}  ({size:.0f} MB)")
    if not WINDOWS and not MACOS:
        _write_desktop_entry(built)
    return 0


def _write_desktop_entry(binary):
    """A .desktop file, so Linux treats it as an application.

    Without one the build is a large file in a folder: double-clicking it in a
    file manager offers to open it in a text editor, and it appears in no menu
    and in no launcher. The entry declares inode/directory, so a project
    folder can be dropped straight onto the icon.
    """
    path = os.path.join(ROOT, "dist", "cobblerpy.desktop")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_NAME}\n"
            "GenericName=Python codebase map\n"
            "Comment=Make sense of a Python codebase somebody else left behind\n"
            f"Exec=\"{os.path.abspath(binary)}\" %f\n"
            "Terminal=false\n"
            "Categories=Development;IDE;\n"
            "MimeType=inode/directory;\n"
            "StartupNotify=true\n")
    print(f"wrote {path}")
    print("  install it for the current user with:")
    print("    cp dist/cobblerpy.desktop ~/.local/share/applications/")
    return path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if shutil.which("pyinstaller") is None:
        print("pyinstaller not found. Install it with:\n"
              "    python3 -m pip install pyinstaller", file=sys.stderr)
        return 2
    # --launcher on its own is the common case: the one-file program to hand
    # somebody, without dragging Qt in for a window they did not ask for.
    picked = [f for f in ("--cli", "--app", "--launcher") if f in argv]
    want_cli = not picked or "--cli" in argv
    want_app = not picked or "--app" in argv
    want_launcher = not picked or "--launcher" in argv
    code = 0
    if want_launcher:
        code = build_launcher() or code
    if want_cli:
        code = build_cli() or code
    if want_app:
        code = build_app() or code
    if not code:
        print("\nSelf-contained: the target machine needs no Python and no pip.\n"
              "dist/CodeCobbler is the one to hand somebody -- double-click for\n"
              "the shelf, or drop a project folder onto it.")
    return code


if __name__ == "__main__":
    sys.exit(main())
