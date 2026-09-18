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

cat > "$BIN/cobble" <<EOF
#!/bin/sh
# CodeCobbler launcher. All behaviour is in cobblerpy/launch.py; this only says
# where the package is, for a checkout that is not pip-installed.
PYTHONPATH="$REPO:\$PYTHONPATH" exec python3 -m cobblerpy.launch "\$@"
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
Exec=$BIN/cobble %F
Icon=$ICONS/codecobbler.svg
Terminal=false
Categories=Development;
MimeType=inode/directory;
StartupNotify=true
EOF
chmod +x "$APPS/codecobbler.desktop"
update-desktop-database "$APPS" 2>/dev/null || true

echo "installed:"
echo "  $BIN/cobble"
echo "  $APPS/codecobbler.desktop      (app menu + drag a folder onto it)"
echo "  $SCRIPTS/Map with CodeCobbler  (right-click in the file manager)"
echo "  $ICONS/codecobbler.svg"
echo
echo "\$HOME/.local/bin must be on your PATH. Try:  cobble --help-ish  (any folder)"
