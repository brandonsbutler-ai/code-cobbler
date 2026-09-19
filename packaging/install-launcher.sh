#!/bin/sh
# Install the desktop front doors for CodeCobbler: a `cobble` command, an
# application entry you can drag a folder onto, and a file-manager right-click.
#
# Nothing here is built or compiled and no dependency is added -- the logic all
# lives in cobblerpy/launch.py. These are the three ways of reaching it without
# typing an incantation. Re-running is safe; it overwrites its own files.
#
#   sh packaging/install-launcher.sh              # uses this checkout
#   sh packaging/install-launcher.sh --uninstall  # removes what it installed
#
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons"
SCRIPTS="$HOME/.local/share/nautilus/scripts"
DESK=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")

# Every file the install below writes carries this mark, and uninstall removes
# only files that do. Removing by PATH deleted `pip install --user`'s own
# ~/.local/bin/cobble, which lives at exactly the same place.
MARK=X-CodeCobbler-Installer

# A path written into a generated file is DATA. The shelf's path can be a
# volume label, which is somebody else's text: written inside double quotes, a
# label with $(...) in it ran on every `cobble`. Single quotes are the one
# quoting nothing inside can escape, once each ' is written as '\''.
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
# The Desktop Entry spec's quoting for Exec: inside double quotes, \ " ` $ take
# a backslash, and then every backslash is doubled again because the value is
# itself an escaped string. Unquoted, a space in $HOME split the command.
dq_exec() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/[\\"`$]/\\&/g' -e 's/\\/\\\\/g')"; }

# Exactly the files the install below writes, and nothing else. The shelf, its
# register and the maps are the person's work, not the launcher's, so they
# stay; where they are is said instead.
if [ "${1:-}" = "--uninstall" ]; then
  KEPT="$HOME/.local/share/codecobbler"
  if [ -f "$BIN/cobble" ] && grep -q "$MARK" "$BIN/cobble"; then
    # The shim's own single-quoted assignment, read back by the shell.
    RECORDED=$(eval "$(grep '^SHELF=' "$BIN/cobble")"; printf '%s' "${SHELF:-}")
    [ -n "$RECORDED" ] && KEPT="$RECORDED"
  fi
  for f in "$BIN/cobble" "$APPS/codecobbler.desktop" "$ICONS/codecobbler.svg" \
           "$SCRIPTS/Map with CodeCobbler" "$DESK/CodeCobbler.desktop"; do
    [ -e "$f" ] || continue
    if grep -q "$MARK" "$f"; then rm -f "$f" && echo "removed $f"
    else echo "left alone: not ours: $f"; fi
  done
  update-desktop-database "$APPS" 2>/dev/null || true
  echo "kept: the shelf and its register in $KEPT, and every"
  echo "      <project>-map-YYYYMMDD-HHMMSS.html beside the projects you mapped"
  exit 0
fi

mkdir -p "$BIN" "$APPS" "$ICONS" "$SCRIPTS"

# Where the shelf and its register live. ASKED, not guessed: this used to take
# the first writable mount it found without a word. The offer is the default,
# ~/.local/share/codecobbler, then every writable volume under /media,
# /run/media and /mnt -- found here, on the machine it is for, because a mount
# point with somebody's username in it has no business inside the repository.
# CODECOBBLER_HOME already set is the answer; with no terminal to ask on (piped,
# CI) it takes the default and says so rather than waiting for nobody.
DEFAULT_SHELF="$HOME/.local/share/codecobbler"
if [ -n "${CODECOBBLER_HOME:-}" ]; then
  SHELF="$CODECOBBLER_HOME"
  if [ -d "$SHELF" ]; then
    echo "shelf: $SHELF  (CODECOBBLER_HOME was already set)"
  else
    # launch.py passes over a CODECOBBLER_HOME that is not a folder, so this is
    # recorded but no map goes there until it exists.
    echo "CODECOBBLER_HOME is $SHELF, which does not exist; the shelf goes in"
    echo "  $DEFAULT_SHELF until it does"
  fi
else
  set -- "$DEFAULT_SHELF"
  for c in /media/"${USER:-}"/* /run/media/"${USER:-}"/* /mnt/*; do
    [ -d "$c" ] && [ -w "$c" ] && set -- "$@" "$c"
  done
  SHELF="$DEFAULT_SHELF"
  if [ -t 0 ]; then
    echo "Where should the shelf of maps you make live?"
    i=1
    for c in "$@"; do
      if [ "$i" = 1 ]; then echo "  1) $c   (default)"; else echo "  $i) $c"; fi
      i=$((i + 1))
    done
    while :; do
      printf 'Choose 1-%s [1]: ' "$#"
      read -r answer || answer=""
      # Digits only, no leading zero, at most four of them, 1..count: "0" was
      # read as $0 -- this script's own path -- and a twenty-digit number
      # reached the shell's arithmetic.
      case "$answer" in
        ""|1) break ;;
        *[!0-9]*|0*|?????*) ;;
        *) if [ "$answer" -le "$#" ]; then eval "SHELF=\${$answer}"; break; fi ;;
      esac
      echo "  not one of the choices"
    done
  else
    echo "no terminal to ask on, so the shelf goes in the default: $SHELF"
  fi
fi
# The default is recorded as EMPTY: launch.py's own fallback is that folder,
# with the register name it has always used there.
SHARED="$SHELF"
[ "$SHELF" = "$DEFAULT_SHELF" ] && SHARED=""

cat > "$BIN/cobble" <<EOF
#!/bin/sh
# $MARK
# CodeCobbler launcher. All behaviour is in cobblerpy/launch.py; this only says
# where the package is (the checkout is not pip-installed) and, if this machine
# has a volume both OSes can see, where the shared shelf lives.
# PYTHONPATH is the checkout and nothing else, and the entry runs as a SCRIPT:
# an inherited or empty PYTHONPATH entry, or -m, puts the current directory on
# sys.path, and the current directory is usually the project being read.
# Both paths are single-quoted data; nothing below re-reads them as code.
REPO=$(sq "$REPO")
SHELF=$(sq "$SHARED")
PYTHONPATH="\$REPO" \\
CODECOBBLER_HOME="\${CODECOBBLER_HOME:-\$SHELF}" \\
exec python3 "\$REPO/packaging/launcher_entry.py" "\$@"
EOF
chmod +x "$BIN/cobble"

cat > "$ICONS/codecobbler.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <!-- X-CodeCobbler-Installer -->
  <rect x="2" y="2" width="60" height="60" rx="14" fill="#0d1117" stroke="#58a6ff" stroke-width="2"/>
  <text x="32" y="41" font-family="DejaVu Sans, sans-serif" font-size="26"
        font-weight="bold" text-anchor="middle" fill="#ffffff">C<tspan fill="#58a6ff">C</tspan></text>
</svg>
EOF

cat > "$SCRIPTS/Map with CodeCobbler" <<'EOF'
#!/bin/sh
# X-CodeCobbler-Installer
# Right-click a folder -> Scripts -> Map with CodeCobbler.
# Nautilus passes the selection in NAUTILUS_SCRIPT_SELECTED_FILE_PATHS, one
# path per line; the argument list is the fallback.
if [ -n "${NAUTILUS_SCRIPT_SELECTED_FILE_PATHS:-}" ]; then
  IFS='
'
  set -- $NAUTILUS_SCRIPT_SELECTED_FILE_PATHS
fi
[ "$#" -eq 0 ] && set -- "$PWD"
exec "$HOME/.local/bin/cobble" "$@"
EOF
chmod +x "$SCRIPTS/Map with CodeCobbler"

cat > "$APPS/codecobbler.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=CodeCobbler
GenericName=Codebase map
Comment=Map a Python codebase somebody else left behind
Exec=$(dq_exec "$BIN/cobble") --shelf %F
Icon=$(printf '%s' "$ICONS/codecobbler.svg" | sed 's/\\/\\\\/g')
Terminal=false
Categories=Development;
MimeType=inode/directory;
StartupNotify=true
$MARK=1
EOF
chmod +x "$APPS/codecobbler.desktop"
update-desktop-database "$APPS" 2>/dev/null || true

# A copy ON the desktop, which is where he asked for it. GNOME will not launch
# a .desktop from ~/Desktop until it is marked trusted; gio is not installed
# here, so the fallback is the one right-click ("Allow Launching") named below.
if [ -d "$DESK" ]; then
  cp "$APPS/codecobbler.desktop" "$DESK/CodeCobbler.desktop"
  chmod +x "$DESK/CodeCobbler.desktop"
  gio set "$DESK/CodeCobbler.desktop" metadata::trusted true 2>/dev/null || true
fi

echo "installed:"
echo "  $BIN/cobble"
echo "  $APPS/codecobbler.desktop      (app menu + drag a folder onto it)"
echo "  $SCRIPTS/Map with CodeCobbler  (right-click in the file manager)"
echo "  $ICONS/codecobbler.svg"
if [ -d "$SHELF" ]; then echo "  shelf: $SHELF"
else echo "  shelf: $DEFAULT_SHELF, until $SHELF exists"; fi
[ -d "$DESK" ] && echo "  $DESK/CodeCobbler.desktop   (if GNOME shows it greyed: right-click -> Allow Launching)"
echo
echo "\$HOME/.local/bin must be on your PATH. Try:  cobble --help"
