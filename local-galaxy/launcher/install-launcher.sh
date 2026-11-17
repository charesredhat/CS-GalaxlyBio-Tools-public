#!/usr/bin/env bash
# install-launcher.sh - add a desktop shortcut that starts a local Galaxy.
#
# Usage: install-launcher.sh GALAXY_ROOT [PORT]      (default port 8080)
#
# Installs ~/.local/bin/galaxy-start, a Galaxy icon, and Galaxy.desktop on the
# Desktop and in the application menu. PORT must match the gunicorn "bind" in
# GALAXY_ROOT/config/galaxy.yml. Safe to re-run.

set -eu

case "${1:-}" in
    ""|-h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

GALAXY_ROOT=$(cd "$1" && pwd)
PORT=${2:-8080}
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)

[ -x "$GALAXY_ROOT/run.sh" ] || { echo "No run.sh in $GALAXY_ROOT - is that a Galaxy root?" >&2; exit 1; }
case "$PORT" in *[!0-9]*|"") echo "PORT must be a number" >&2; exit 2 ;; esac

BIN="$HOME/.local/bin"
ICONS="$HOME/.local/share/icons"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
mkdir -p "$BIN" "$ICONS" "$APPS"

sed -e "s#@GALAXY_DIR@#$GALAXY_ROOT#" -e "s#@GALAXY_URL@#http://127.0.0.1:$PORT#" \
    "$HERE/galaxy-start" > "$BIN/galaxy-start"
chmod +x "$BIN/galaxy-start"

ICON=utilities-terminal
if [ -f "$GALAXY_ROOT/static/favicon.svg" ]; then
    cp "$GALAXY_ROOT/static/favicon.svg" "$ICONS/galaxy.svg"
    ICON="$ICONS/galaxy.svg"
fi

entry="[Desktop Entry]
Type=Application
Name=Galaxy
Comment=Start the local Galaxy server and open it in the browser
Exec=$BIN/galaxy-start
Icon=$ICON
Terminal=true
Categories=Science;"

printf '%s\n' "$entry" > "$APPS/galaxy.desktop"
if [ -d "$DESKTOP_DIR" ]; then
    printf '%s\n' "$entry" > "$DESKTOP_DIR/Galaxy.desktop"
    chmod +x "$DESKTOP_DIR/Galaxy.desktop"
fi

echo "Installed $BIN/galaxy-start  (Galaxy root: $GALAXY_ROOT, URL: http://127.0.0.1:$PORT)"
echo "Shortcut:  $DESKTOP_DIR/Galaxy.desktop  and the application menu"
grep -qE "^[[:space:]]*bind:[[:space:]]*[^#]*:$PORT\b" "$GALAXY_ROOT/config/galaxy.yml" 2>/dev/null \
    || [ "$PORT" = 8080 ] \
    || echo "NOTE: set 'bind: localhost:$PORT' under gravity: gunicorn: in $GALAXY_ROOT/config/galaxy.yml"
