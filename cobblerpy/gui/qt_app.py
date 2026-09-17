"""The desktop window.

Built to sit beside an editor, because that is where the person using it
already is. Dark, monospaced, and colour used only where it carries meaning:
this is a tool for reading code, and it should not look like a document
processor.

Everything it DECIDES lives in session.py, which imports nothing outside the
standard library. This file is chrome and the project's only dependency.
"""

import os
import sys
import webbrowser

from .session import ProjectError, Session, folders_from_drop

# An editor's palette, cool rather than warm, and borrowed from the syntax
# colours a Python developer already reads without thinking:
#
#   green    this one is furthest along -- start here
#   amber    signals of unfinished work
#   coral    will not parse
#   violet   nothing imports it
#   blue     the action to take
#
# Nothing here is decorative. A reader scanning the window should be able to
# tell the state of a module from its colour before reading its name.
BG      = "#0f1116"      # the editor ground
PANEL   = "#161920"      # a panel on it
SUNK    = "#1b1f28"      # a row inside a panel
LINE    = "#262b36"
LINE2   = "#1f242e"
INK     = "#d6dae3"
MUTED   = "#8b93a3"
FAINT   = "#5e6675"
GREEN   = "#7ee787"      # furthest along
AMBER   = "#d8a657"      # unfinished
CORAL   = "#f4796b"      # will not parse
VIOLET  = "#bc8cff"      # nothing imports it
BLUE    = "#58a6ff"      # the action

MONO = ('"JetBrains Mono", "Cascadia Mono", "SF Mono", "Ubuntu Mono", '
        '"DejaVu Sans Mono", "Liberation Mono", monospace')
SANS = ('"Inter", "Segoe UI", "SF Pro Text", "Ubuntu", "Noto Sans", '
        '"DejaVu Sans", sans-serif')

STYLE = f"""
* {{ font-family: {SANS}; color: {INK}; }}
QWidget#root {{ background: {BG}; }}

QLabel#wordmark  {{ font-family: {MONO}; font-size: 20px; font-weight: 700;
                    color: {GREEN}; letter-spacing: -0.5px; }}
QLabel#tagline   {{ font-size: 12.5px; color: {MUTED}; }}
QLabel#cap       {{ font-family: {MONO}; font-size: 10.5px; color: {FAINT};
                    font-weight: 700; letter-spacing: 1.2px; }}
QLabel#path      {{ font-family: {MONO}; font-size: 11.5px; color: {MUTED}; }}
QLabel#stat      {{ font-family: {MONO}; font-size: 14px; color: {INK}; }}
QLabel#headline  {{ font-family: {MONO}; font-size: 17px; font-weight: 700;
                    color: {GREEN}; }}
QLabel#quiet     {{ font-size: 12px; color: {MUTED}; }}
QLabel#status    {{ font-family: {MONO}; font-size: 11.5px; color: {MUTED}; }}

QFrame#drop {{
    background: {PANEL}; border: 1px dashed {LINE};
    border-radius: 6px;
}}
QFrame#dropActive {{
    background: #12202a; border: 1px dashed {BLUE};
    border-radius: 6px;
}}
QLabel#dropTitle {{ font-family: {MONO}; font-size: 15px; color: {INK}; }}
QLabel#dropHint  {{ font-size: 11.5px; color: {FAINT}; }}

QFrame#panel {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: 6px;
}}
QFrame#row {{
    background: {SUNK}; border: 1px solid {LINE2}; border-radius: 5px;
}}
/* The attempt that is furthest along. A left bar rather than a fill, so the
   monospaced text on it stays as legible as every other row. */
QFrame#rowLead {{
    background: {SUNK}; border: 1px solid {LINE2};
    border-left: 3px solid {GREEN}; border-radius: 5px;
}}
QLabel#pct      {{ font-family: {MONO}; font-size: 15px; font-weight: 700;
                   color: {MUTED}; }}
QLabel#pctLead  {{ font-family: {MONO}; font-size: 15px; font-weight: 700;
                   color: {GREEN}; }}
QLabel#modname  {{ font-family: {MONO}; font-size: 12.5px; color: {INK}; }}
QLabel#fact     {{ font-family: {MONO}; font-size: 11px; color: {MUTED}; }}
QLabel#gap      {{ font-family: {MONO}; font-size: 11px; color: {AMBER}; }}
QLabel#elsewhere{{ font-family: {MONO}; font-size: 11px; color: {VIOLET}; }}
QLabel#resume   {{ font-family: {MONO}; font-size: 11px; color: {GREEN};
                   font-weight: 700; }}

QPushButton {{
    background: {SUNK}; border: 1px solid {LINE}; border-radius: 5px;
    padding: 8px 15px; font-size: 12.5px; font-family: {MONO};
}}
QPushButton:hover    {{ background: #222835; border-color: {BLUE}; }}
QPushButton:disabled {{ color: {FAINT}; background: {PANEL}; }}
QPushButton#primary {{
    background: {BLUE}; border: 1px solid {BLUE}; color: #08121e;
    font-weight: 700; padding: 9px 22px;
}}
QPushButton#primary:hover    {{ background: #79b8ff; border-color: #79b8ff; }}
QPushButton#primary:disabled {{ background: #1e3247; border-color: #1e3247;
                                color: #4a5f77; }}

QCheckBox {{ font-size: 12.5px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {LINE};
    border-radius: 3px; background: {SUNK};
}}
QCheckBox::indicator:checked {{ background: {BLUE}; border-color: {BLUE}; }}
QProgressBar {{ background: {SUNK}; border: none; border-radius: 2px;
                height: 4px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {BLUE}; border-radius: 2px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent;
                                                border: none; }}
QScrollBar:vertical {{ background: transparent; width: 9px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px;
                               min-height: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def build(qt, session=None):
    QtCore, QtGui, QtWidgets = qt

    class Worker(QtCore.QThread):
        stage = QtCore.Signal(str)
        done = QtCore.Signal(object)
        failed = QtCore.Signal(str)

        def __init__(self, session):
            super().__init__()
            self.session = session

        def run(self):
            try:
                self.done.emit(self.session.run(on_stage=self.stage.emit))
            except Exception as exc:                      # noqa: BLE001
                self.failed.emit(f"{type(exc).__name__}: {exc}")

    class DropZone(QtWidgets.QFrame):
        dropped = QtCore.Signal(list)

        def __init__(self):
            super().__init__()
            self.setObjectName("drop")
            self.setAcceptDrops(True)
            self.setMinimumHeight(120)
            lay = QtWidgets.QVBoxLayout(self)
            lay.setAlignment(QtCore.Qt.AlignCenter)
            lay.setSpacing(3)
            self.title = QtWidgets.QLabel("drop a project folder")
            self.title.setObjectName("dropTitle")
            self.title.setAlignment(QtCore.Qt.AlignCenter)
            self.hint = QtWidgets.QLabel("the one somebody left half-finished")
            self.hint.setObjectName("dropHint")
            self.hint.setAlignment(QtCore.Qt.AlignCenter)
            self.button = QtWidgets.QPushButton("open…")
            self.button.setCursor(QtCore.Qt.PointingHandCursor)
            row = QtWidgets.QHBoxLayout()
            row.addStretch(1); row.addWidget(self.button); row.addStretch(1)
            lay.addWidget(self.title); lay.addWidget(self.hint)
            lay.addSpacing(6); lay.addLayout(row)

        def _restyle(self, name):
            self.setObjectName(name)
            self.style().unpolish(self); self.style().polish(self)

        def dragEnterEvent(self, event):
            if event.mimeData().hasUrls():
                event.acceptProposedAction(); self._restyle("dropActive")

        def dragLeaveEvent(self, _event):
            self._restyle("drop")

        def dropEvent(self, event):
            self._restyle("drop")
            urls = event.mimeData().urls()
            if urls:
                self.dropped.emit([u.toLocalFile() for u in urls])
                event.acceptProposedAction()

    class Window(QtWidgets.QWidget):
        def __init__(self, session):
            super().__init__()
            self.session = session
            self.worker = None
            self.setObjectName("root")
            self.setWindowTitle("cobblerpy")
            # Geared for 1920x1080: the findings sit beside the controls, and
            # a survey of a real project is a long list, so the right-hand
            # column takes the width rather than the reader taking the scroll.
            self.resize(1560, 940)
            self.setMinimumSize(760, 620)
            self.setStyleSheet(STYLE)
            self._build()

        def _label(self, text, name, wrap=False):
            label = QtWidgets.QLabel(text)
            label.setObjectName(name)
            label.setWordWrap(wrap)
            return label

        def _build(self):
            outer = QtWidgets.QHBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)

            left = QtWidgets.QWidget(); left.setObjectName("root")
            left.setFixedWidth(430)
            col = QtWidgets.QVBoxLayout(left)
            col.setContentsMargins(26, 24, 18, 24); col.setSpacing(0)
            outer.addWidget(left)

            col.addWidget(self._label("cobblerpy", "wordmark"))
            col.addSpacing(2)
            col.addWidget(self._label(
                "Point it at a codebase somebody left behind.", "tagline", True))
            col.addSpacing(18)

            self.zone = DropZone()
            self.zone.dropped.connect(self._load)
            self.zone.button.clicked.connect(self._choose)
            col.addWidget(self.zone)
            col.addSpacing(14)

            self.path = self._label("", "path", True)
            self.stat = self._label("", "stat", True)
            col.addWidget(self.path); col.addSpacing(3); col.addWidget(self.stat)
            col.addSpacing(18)

            self.boxes = {}
            opts = QtWidgets.QFrame(); opts.setObjectName("panel")
            ol = QtWidgets.QVBoxLayout(opts)
            ol.setContentsMargins(15, 13, 15, 14); ol.setSpacing(9)
            for key, text in (("history", "read git history"),
                              ("write_map", "write the interactive map")):
                box = QtWidgets.QCheckBox(text)
                box.setChecked(getattr(self.session.options, key))
                box.setCursor(QtCore.Qt.PointingHandCursor)
                self.boxes[key] = box
                ol.addWidget(box)
            col.addWidget(opts)
            col.addSpacing(16)

            row = QtWidgets.QHBoxLayout(); row.setSpacing(8)
            self.run_btn = QtWidgets.QPushButton("survey")
            self.run_btn.setObjectName("primary")
            self.run_btn.setCursor(QtCore.Qt.PointingHandCursor)
            self.run_btn.setEnabled(False)
            self.run_btn.clicked.connect(self._run)
            self.map_btn = QtWidgets.QPushButton("open map ↗")
            self.map_btn.setEnabled(False)
            self.map_btn.clicked.connect(self._open_map)
            row.addWidget(self.run_btn); row.addWidget(self.map_btn)
            row.addStretch(1)
            col.addLayout(row)
            col.addSpacing(12)

            self.bar = QtWidgets.QProgressBar()
            self.bar.setRange(0, 0); self.bar.setTextVisible(False); self.bar.hide()
            col.addWidget(self.bar)
            col.addSpacing(6)
            self.status = self._label("", "status", True)
            col.addWidget(self.status)
            col.addStretch(1)

            # -- right: the findings
            self.rightScroll = QtWidgets.QScrollArea()
            self.rightScroll.setWidgetResizable(True)
            self.rightScroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            holder = QtWidgets.QWidget(); holder.setObjectName("root")
            self.rightScroll.setWidget(holder)
            self.findings = QtWidgets.QVBoxLayout(holder)
            self.findings.setContentsMargins(18, 24, 26, 24)
            self.findings.setSpacing(0)
            self.findings.addStretch(1)
            self.rightScroll.hide()
            outer.addWidget(self.rightScroll, 1)

            # A hidden widget claims no space, so with the findings panel away
            # the layout had nothing to give the leftover width to and centred
            # the fixed-width controls in the middle of the window. This takes
            # that space instead, and steps aside when there are findings.
            # Rather than an empty half-window on first launch, the space says
            # what the survey will produce. It is the same three findings the
            # right-hand column will hold, so the empty state teaches the
            # layout instead of looking like something failed to load.
            self.filler = QtWidgets.QWidget()
            self.filler.setObjectName("root")
            fl = QtWidgets.QVBoxLayout(self.filler)
            fl.setContentsMargins(18, 26, 26, 24)
            fl.setSpacing(0)
            fl.addWidget(self._label("WHAT A SURVEY TELLS YOU", "cap"))
            fl.addSpacing(14)
            for heading, body in (
                    ("the same job, started over",
                     "which files are competing attempts at one piece of work, "
                     "how much of each is filled in, and which one to carry on "
                     "from"),
                    ("where the work stopped",
                     "modules ranked by signals of unfinished work, with the "
                     "evidence attached so you can disagree with the ranking"),
                    ("the map",
                     "every module, what reaches it, and the source behind each "
                     "finding \u2014 one self-contained HTML file, nothing "
                     "uploaded")):
                panel = QtWidgets.QFrame()
                panel.setObjectName("row")
                pl = QtWidgets.QVBoxLayout(panel)
                pl.setContentsMargins(14, 11, 14, 12)
                pl.setSpacing(4)
                pl.addWidget(self._label(heading, "modname"))
                pl.addWidget(self._label(body, "quiet", True))
                fl.addWidget(panel)
                fl.addSpacing(9)
            fl.addStretch(1)
            outer.addWidget(self.filler, 1)

        # -- loading
        def _choose(self):
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Open a project folder")
            if chosen:
                self._load([chosen])

        def _load(self, raw):
            try:
                folders = folders_from_drop(raw)
                index = self.session.load(folders[0])
            except ProjectError as exc:
                self._say(str(exc), CORAL)
                return
            self.path.setText(index.folder)
            self.stat.setText(index.summary())
            self.zone.title.setText("drop another to start over")
            self.run_btn.setEnabled(bool(index.count))
            self.map_btn.setEnabled(False)
            self._clear()
            self._say("" if index.count else "no Python in that folder", CORAL)

        # -- running
        def _run(self):
            for key, box in self.boxes.items():
                setattr(self.session.options, key, box.isChecked())
            self.run_btn.setEnabled(False); self.run_btn.setText("working…")
            self.map_btn.setEnabled(False)
            self._clear(); self.bar.show()
            self.worker = Worker(self.session)
            self.worker.stage.connect(lambda t: self._say(t, MUTED))
            self.worker.done.connect(self._finish)
            self.worker.failed.connect(self._failed)
            self.worker.start()

        def _failed(self, message):
            self.bar.hide()
            self.run_btn.setEnabled(True); self.run_btn.setText("survey")
            self._say(message, CORAL)

        def _finish(self, result):
            self.bar.hide()
            self.run_btn.setEnabled(True); self.run_btn.setText("survey again")
            self.map_btn.setEnabled(bool(result.map_path))
            self._say(f"{result.duration()}"
                      + (f"   ·   {os.path.basename(result.map_path)}"
                         if result.map_path else ""), MUTED)
            self._show(result)

        # -- findings
        def _clear(self):
            while self.findings.count():
                item = self.findings.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            self.findings.addStretch(1)
            self.rightScroll.hide()
            self.filler.show()

        def _add(self, widget):
            self.findings.insertWidget(self.findings.count() - 1, widget)

        def _show(self, result):
            self._add(self._label(result.headline(), "headline", True))
            self._add(self._spacer(4))
            self._add(self._label(
                f"{result.modules} modules   ·   {result.entry_points} entry "
                f"points   ·   {result.orphans} nothing imports   ·   "
                f"{result.unreached} unreached", "quiet", True))
            self._add(self._spacer(20))

            if result.attempts:
                self._add(self._label("THE SAME JOB, STARTED OVER", "cap"))
                self._add(self._spacer(9))
                for group in result.attempts[:6]:
                    self._add(self._attempt_panel(group))
                    self._add(self._spacer(12))
                if len(result.attempts) > 6:
                    self._add(self._label(
                        f"and {len(result.attempts) - 6} more groups, in the map",
                        "quiet"))
                    self._add(self._spacer(12))

            if result.frontier:
                self._add(self._spacer(8))
                self._add(self._label("WHERE THE WORK STOPPED", "cap"))
                self._add(self._spacer(9))
                self._add(self._frontier_panel(result.frontier[:8]))
            self.rightScroll.show()
            self.filler.hide()

        def _spacer(self, height):
            widget = QtWidgets.QWidget()
            widget.setFixedHeight(height)
            widget.setObjectName("root")
            return widget

        def _attempt_panel(self, group):
            panel = QtWidgets.QFrame(); panel.setObjectName("panel")
            lay = QtWidgets.QVBoxLayout(panel)
            lay.setContentsMargins(15, 13, 15, 14); lay.setSpacing(7)
            lay.addWidget(self._label(
                f"{len(group['attempts'])} attempts   ·   sharing "
                + ", ".join(group["shared"][:5]), "quiet", True))
            for attempt in group["attempts"]:
                lead = attempt["module"] == group["resume_at"]
                row = QtWidgets.QFrame()
                row.setObjectName("rowLead" if lead else "row")
                rl = QtWidgets.QVBoxLayout(row)
                rl.setContentsMargins(12, 9, 12, 10); rl.setSpacing(3)
                head = QtWidgets.QHBoxLayout(); head.setSpacing(11)
                pct = self._label(f"{attempt['percent']:>3}%",
                                  "pctLead" if lead else "pct")
                head.addWidget(pct)
                head.addWidget(self._label(attempt["relpath"], "modname"))
                head.addStretch(1)
                if lead:
                    head.addWidget(self._label("RESUME HERE", "resume"))
                rl.addLayout(head)
                for fact in attempt["facts"]:
                    style = "gap" if fact.startswith("still stubbed") else "fact"
                    rl.addWidget(self._label("  " + fact, style, True))
                lay.addWidget(row)
            for module, extra in group["elsewhere"].items():
                has = ", ".join(extra) if extra else "the only tests for this job"
                lay.addWidget(self._label(f"{module} has {has}", "elsewhere", True))
            return panel

        def _frontier_panel(self, rows):
            panel = QtWidgets.QFrame(); panel.setObjectName("panel")
            lay = QtWidgets.QVBoxLayout(panel)
            lay.setContentsMargins(15, 13, 15, 14); lay.setSpacing(6)
            for row in rows:
                line = QtWidgets.QHBoxLayout(); line.setSpacing(11)
                score = self._label(f"{row['score']:>5}", "pct")
                score.setStyleSheet(f"color: {AMBER};")
                line.addWidget(score)
                line.addWidget(self._label(row["relpath"], "modname"))
                line.addStretch(1)
                kinds = ", ".join(f"{k.replace('_', ' ')} {v}"
                                  for k, v in sorted(row["counts"].items(),
                                                     key=lambda kv: -kv[1])[:3])
                line.addWidget(self._label(kinds, "fact"))
                holder = QtWidgets.QWidget(); holder.setObjectName("root")
                holder.setLayout(line)
                lay.addWidget(holder)
            return panel

        def _open_map(self):
            result = self.session.result
            if result and result.map_path and os.path.exists(result.map_path):
                webbrowser.open("file://" + os.path.abspath(result.map_path))

        def _say(self, text, colour=MUTED):
            self.status.setText(text)
            self.status.setStyleSheet(f"color: {colour};")

    return Window(session or Session())


def main(argv=None):
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError:
        raise SystemExit(
            "cobblerpy: the desktop window needs PySide6.\n"
            '    pip install "PySide6-Essentials"\n'
            "The library and the command line do not: run `cobblerpy --help`.")
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("cobblerpy")
    window = build((QtCore, QtGui, QtWidgets))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
