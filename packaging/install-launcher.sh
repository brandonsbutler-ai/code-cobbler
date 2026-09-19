#!/bin/sh
# Install the desktop front doors for CodeCobbler: a `cobble` command, an
# application entry you can drag a folder onto, and a file-manager right-click.
#
# Nothing here is built or compiled and no dependency is added -- the logic all
# lives in cobblerpy/launch.py. These are the three ways of reaching it without
# typing an incantation. Re-running is safe; it overwrites its own files.
#
#   sh packaging/install-launcher.sh          # uses this checkout
#
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons"
SCRIPTS="$HOME/.local/share/nautilus/scripts"
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
  echo "shelf: $SHELF  (CODECOBBLER_HOME was already set)"
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
      case "$answer" in
        ""|1) break ;;
        *[!0-9]*) ;;
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
# CodeCobbler launcher. All behaviour is in cobblerpy/launch.py; this only says
# where the package is (the checkout is not pip-installed) and, if this machine
# has a volume both OSes can see, where the shared shelf lives.
# PYTHONPATH is the checkout and nothing else, and the entry runs as a SCRIPT:
# an inherited or empty PYTHONPATH entry, or -m, puts the current directory on
# sys.path, and the current directory is usually the project being read.
PYTHONPATH="$REPO" \\
CODECOBBLER_HOME="\${CODECOBBLER_HOME:-$SHARED}" \\
exec python3 "$REPO/packaging/launcher_entry.py" "\$@"
EOF
chmod +x "$BIN/cobble"

cat > "$ICONS/codecobbler.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <rect x="2" y="2" width="60" height="60" rx="14" fill="#0d1117" stroke="#58a6ff" stroke-width="2"/>
  <text x="32" y="41" font-family="DejaVu Sans, sans-serif" font-size="26"
        font-weight="bold" text-anchor="middle" fill="#ffffff">C<tspan fill="#58a6ff">C</tspan></text>
</svg>
EOF

cat > "$SCRIPTS/Map with CodeCobbler" <<'EOF'
#!/bin/sh
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
Exec=$BIN/cobble --shelf %F
Icon=$ICONS/codecobbler.svg
Terminal=false
Categories=Development;
MimeType=inode/directory;
StartupNotify=true
EOF
chmod +x "$APPS/codecobbler.desktop"
update-desktop-database "$APPS" 2>/dev/null || true

# A copy ON the desktop, which is where he asked for it. GNOME will not launch
# a .desktop from ~/Desktop until it is marked trusted; gio is not installed
# here, so the fallback is the one right-click ("Allow Launching") named below.
DESK=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
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
echo "  shelf: $SHELF"
[ -d "$DESK" ] && echo "  $DESK/CodeCobbler.desktop   (if GNOME shows it greyed: right-click -> Allow Launching)"
echo
echo "\$HOME/.local/bin must be on your PATH. Try:  cobble --help"
