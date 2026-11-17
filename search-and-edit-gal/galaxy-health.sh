#!/bin/sh
# galaxy-health.sh - tell whether a Galaxy server is actually serving, and if
# not, why. Written for the failure mode systemd hides: gravity's supervisord
# restarts a Galaxy that never finishes booting, so `systemctl status` keeps
# reporting active (running) with NRestarts=0 while every request 502s.
#
# Usage: sh galaxy-health.sh [galaxy_root] [url]
#   galaxy_root  default: derived from the running processes, else /server/galaxy
#   url          default: http://localhost:8080
#
# Read-only. Uses sudo only when it is already passwordless, so it degrades to
# whatever the calling user can see rather than prompting.

ROOT=$1
URL=${2:-http://localhost:8080}

say()  { printf '%s\n' "$*"; }
hr()   { say "------------------------------------------------------------"; }
warn() { say "  ! $*"; WARNINGS=$((WARNINGS + 1)); }
bad()  { say "  X $*"; PROBLEMS=$((PROBLEMS + 1)); }
ok()   { say "  . $*"; }

WARNINGS=0
PROBLEMS=0

# sudo only if it needs no password and no tty
SUDO=""
if [ "$(id -u)" != 0 ] && sudo -n true 2>/dev/null; then
    SUDO="sudo -n"
fi
# Read a file as ourselves if we can, else via sudo. Keeps every later read from
# having to care which case it is in.
cat_file() {
    if [ -r "$1" ]; then cat "$1" 2>/dev/null
    elif [ -n "$SUDO" ] || [ "$(id -u)" = 0 ]; then $SUDO cat "$1" 2>/dev/null
    fi
}
have_file() {
    [ -r "$1" ] && return 0
    { [ -n "$SUDO" ] || [ "$(id -u)" = 0 ]; } && $SUDO test -r "$1" 2>/dev/null
}

PROCS=$(ps -eo pid,etimes,times,args 2>/dev/null \
  | grep -E 'bin/gunicorn|bin/galaxyctl|bin/supervisord|lib/galaxy/main\.py' \
  | grep -vE 'grep|tail -f')

is_galaxy_root() {
    [ -d "$1/lib/galaxy" ] || return 1
    [ -e "$1/run.sh" ] || [ -d "$1/config" ]
}

if [ -z "$ROOT" ]; then
    ROOT=$(printf '%s\n' "$PROCS" | tr ' =' '\n\n' \
             | sed -n 's#^\(/[^:,]*\)/\(venv/bin\|config\|lib\|database\)\(/.*\)\{0,1\}$#\1#p' \
             | sort -u | head -1)
    [ -n "$ROOT" ] || ROOT=/server/galaxy
fi

say "== Galaxy health on $(hostname 2>/dev/null) =="
say "root: $ROOT   url: $URL   date: $(date 2>/dev/null)"
hr

# A wrong root makes every later phase lie, so say so once and plainly instead of
# reporting a stream of problems about a directory that is not Galaxy.
if ! is_galaxy_root "$ROOT"; then
    say "[0] Galaxy root"
    bad "$ROOT does not look like a Galaxy root (no lib/galaxy with run.sh or config/)"
    say "        pass the right path as the first argument, or run find-galaxy.sh"
    hr
    say "VERDICT: cannot check - $PROBLEMS problem(s)."
    exit 1
fi

CONF="$ROOT/config/galaxy.yml"

say "[1] Is it serving?"
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL/api/version" 2>/dev/null)
SERVING=0
case "$CODE" in
    200)     ok "GET /api/version -> 200"; SERVING=1 ;;
    401|403) ok "GET /api/version -> $CODE (up; auth required)"; SERVING=1 ;;
    502|503) bad "GET /api/version -> $CODE - a proxy is up but Galaxy is not answering it" ;;
    000|"")  bad "GET /api/version -> no response (nothing listening, or blocked)" ;;
    *)       warn "GET /api/version -> $CODE" ;;
esac

# The app server socket is the ground truth for a unix bind: it is only created
# once the app has finished loading. A TCP bind has no socket file, so the check
# does not apply and must not be treated as a failure.
BIND=$(cat_file "$CONF" | sed -n "s/^[[:space:]]*bind:[[:space:]]*['\"]\{0,1\}\([^'\"]*\)['\"]\{0,1\}[[:space:]]*$/\1/p" | head -1)
case "$BIND" in
    unix:*)
        SOCK=${BIND#unix:}
        if [ -S "$SOCK" ] || { [ -n "$SUDO" ] && $SUDO test -S "$SOCK" 2>/dev/null; }; then
            ok "app server socket exists: $SOCK"
        else
            bad "no app server socket at $SOCK - Galaxy has not finished starting"
        fi
        ;;
    "")
        # No explicit bind configured; gravity's default is a unix socket.
        SOCK="$ROOT/var/gunicorn.sock"
        if [ -S "$SOCK" ] || { [ -n "$SUDO" ] && $SUDO test -S "$SOCK" 2>/dev/null; }; then
            ok "app server socket exists: $SOCK"
        elif [ "$SERVING" = 1 ]; then
            say "    (no socket at $SOCK; serving over a TCP bind)"
        else
            bad "no app server socket at $SOCK - Galaxy has not finished starting"
        fi
        ;;
    *)
        say "    app server uses a TCP bind ($BIND) - no socket to check"
        ;;
esac
say ""

say "[2] Process manager view"
GALAXY_USER=$(stat -c %U "$ROOT" 2>/dev/null)
[ -n "$GALAXY_USER" ] || GALAXY_USER=galaxy
RUNAS=""
if [ "$(id -un 2>/dev/null)" != "$GALAXY_USER" ]; then
    if [ "$(id -u)" = 0 ] || [ -n "$SUDO" ]; then
        RUNAS="sudo -n -u $GALAXY_USER"
    else
        RUNAS="no"
    fi
fi
if [ ! -x "$ROOT/venv/bin/galaxyctl" ]; then
    say "    galaxyctl not found at $ROOT/venv/bin/galaxyctl"
elif [ "$RUNAS" = no ]; then
    say "    skipping galaxyctl: needs to run as $GALAXY_USER"
else
    # galaxyctl resolves config relative to the working directory, so it must be
    # run from the Galaxy root or it fails on an unrelated import error.
    GSTATUS=$(cd "$ROOT" 2>/dev/null && $RUNAS env PATH="$ROOT/venv/bin:/usr/bin:/bin" \
                "$ROOT/venv/bin/galaxyctl" status 2>&1)
    GRC=$?
    if [ $GRC -ne 0 ]; then
        warn "galaxyctl status failed (exit $GRC):"
        printf '%s\n' "$GSTATUS" | tail -3 | sed 's/^/      /'
    else
        printf '%s\n' "$GSTATUS" | sed 's/^/    /'
        # Distinguish "could not ask" from "asked, and it is not running".
        if printf '%s\n' "$GSTATUS" | grep -qE 'FATAL|STOPPED|BACKOFF|EXITED'; then
            bad "the process manager reports a process not running (see above)"
        fi
    fi
fi
# systemd's view is about the gravity parent only - say so, because it is the
# single most misleading signal in this failure mode.
if command -v systemctl >/dev/null 2>&1; then
    ACT=$(systemctl is-active galaxy.service 2>/dev/null)
    NR=$(systemctl show galaxy.service -p NRestarts --value 2>/dev/null)
    [ -n "$ACT" ] && say "    systemd: galaxy.service is-active=$ACT NRestarts=${NR:-?} (parent only)"
fi
say ""

say "[3] Restart churn (the part systemd does not show)"
SLOG=""
for c in "$ROOT/database/gravity/supervisor/supervisord.log" \
         "$ROOT/database/gravity/supervisord.log"; do
    have_file "$c" && { SLOG=$c; break; }
done
if [ -z "$SLOG" ]; then
    warn "supervisord.log not readable under $ROOT/database/gravity - cannot check for a restart loop"
else
    SPAWN_LINES=$(cat_file "$SLOG" | grep -a "spawned: 'gunicorn'" | tail -5)
    NLINES=$(printf '%s' "$SPAWN_LINES" | grep -c . 2>/dev/null)
    SPAWNS=$(printf '%s\n' "$SPAWN_LINES" | sed -n 's/^\([0-9][0-9-]* [0-9][0-9:]*\),.*/\1/p')
    NSTAMPS=$(printf '%s' "$SPAWNS" | grep -c . 2>/dev/null)
    if [ "${NLINES:-0}" -lt 2 ]; then
        ok "no repeated app-server spawns recorded"
    elif [ "${NSTAMPS:-0}" -lt 2 ]; then
        # Do not report an all-clear just because the timestamps did not parse -
        # this is the one check that must never produce a false negative.
        warn "found $NLINES app-server spawns but could not parse their timestamps"
        printf '%s\n' "$SPAWN_LINES" | tail -3 | cut -c1-120 | sed 's/^/      /'
    else
        say "    last $NSTAMPS gunicorn spawns:"
        printf '%s\n' "$SPAWNS" | sed 's/^/      /'
        PREV=$(printf '%s\n' "$SPAWNS" | tail -2 | head -1)
        LAST=$(printf '%s\n' "$SPAWNS" | tail -1)
        PE=$(date -d "$PREV" +%s 2>/dev/null)
        LE=$(date -d "$LAST" +%s 2>/dev/null)
        if [ -n "$PE" ] && [ -n "$LE" ] && [ "$LE" -gt "$PE" ]; then
            IVAL=$((LE - PE))
            say "      most recent interval: ${IVAL}s"
            # Read `timeout:` from inside the gravity gunicorn block only - the
            # key name is common enough to appear elsewhere in galaxy.yml.
            TMO=$(cat_file "$CONF" | awk '
                /^[[:space:]]*gunicorn:[[:space:]]*$/ { in_g=1; ind=match($0,/[^ ]/); next }
                in_g && /^[[:space:]]*[a-z_]+:/ {
                    this=match($0,/[^ ]/)
                    if (this <= ind) { in_g=0 }
                }
                in_g && /^[[:space:]]*timeout:[[:space:]]*[0-9]+/ {
                    gsub(/[^0-9]/,""); print; exit
                }' )
            # gravity defaults gunicorn --timeout to 300 when unset; the running
            # process is the authority, so prefer what it was actually given.
            PTMO=$(printf '%s\n' "$PROCS" | sed -n 's/.*--timeout[ =]\([0-9]\{1,\}\).*/\1/p' | head -1)
            [ -n "$PTMO" ] && TMO=$PTMO
            [ -n "$TMO" ] || TMO=300
            say "      gunicorn timeout: ${TMO}s$([ -n "$PTMO" ] || printf ' (gravity default)')"
            if [ "$IVAL" -gt "$TMO" ] && [ "$IVAL" -lt $((TMO + 180)) ]; then
                bad "restart interval tracks the ${TMO}s timeout: startup is being killed before it completes"
                say "        fix: raise gravity.gunicorn.timeout in $CONF above real boot time,"
                say "             then 'galaxyctl restart' (see [5] for why boot is slow)"
            elif [ "$SERVING" != 1 ]; then
                bad "app server respawned ${IVAL}s ago and is not serving - startup is not completing"
            fi
        fi
        cat_file "$SLOG" | grep -aE "exited: (gunicorn|handler)" | tail -3 \
          | cut -c1-120 | sed 's/^/      /'
    fi
fi
say ""

say "[4] Is startup progressing or stalled?"
printf '%s\n' "$PROCS" | grep 'bin/gunicorn' | while read -r pid etimes times _rest; do
    case "$pid$etimes$times" in *[!0-9]*) continue ;; esac
    say "    gunicorn pid=$pid wall=${etimes}s cpu=${times}s"
    # A loading Galaxy is CPU-bound on parsing. Lots of wall time against almost
    # no CPU time means it is waiting on I/O, not working.
    if [ "$etimes" -gt 60 ] && [ "$times" -lt $((etimes / 8)) ]; then
        say "      ! mostly idle - startup is waiting on I/O, not computing"
    fi
done
LOGF="$ROOT/log/gunicorn.log"
if have_file "$LOGF"; then
    MT=$($SUDO stat -c %Y "$LOGF" 2>/dev/null)
    [ -n "$MT" ] && say "    last log write: $(( $(date +%s) - MT ))s ago"
    say "    last line:"
    cat_file "$LOGF" | tail -1 | cut -c1-150 | sed 's/^/      /'
fi
say ""

say "[5] Startup cost factors"
FSTYPE=$(stat -f -c %T "$ROOT" 2>/dev/null)
case "$FSTYPE" in
    nfs*|cifs|smb*|fuse*|glusterfs|lustre|ceph)
        bad "Galaxy root is on $FSTYPE"
        say "        Every tool XML and .loc file read at startup is a network round trip."
        say "        This alone can push boot past the app server timeout."
        findmnt -no SOURCE,TARGET --target "$ROOT" 2>/dev/null | sed 's/^/        mount: /'
        ;;
    "") warn "could not determine filesystem type of $ROOT" ;;
    *)  ok "Galaxy root filesystem: $FSTYPE" ;;
esac
for cf in tool_conf.xml shed_tool_conf.xml; do
    have_file "$ROOT/config/$cf" || continue
    say "    $cf: $(cat_file "$ROOT/config/$cf" | grep -c '<tool ') tool entries"
done
say ""

say "[6] Dependencies"
# Anchor to a real setting: galaxy.yml ships commented-out examples, and probing
# one of those reports a failure for a database nobody uses.
DBLINE=$(cat_file "$CONF" | sed -n 's/^[[:space:]]*database_connection:[[:space:]]*//p' | head -1)
if [ -n "$DBLINE" ]; then
    DBDRV=$(printf '%s\n' "$DBLINE" | sed -n 's#^\([a-z+]*\)://.*#\1#p')
    DBHOSTPORT=$(printf '%s\n' "$DBLINE" | sed -n 's#^[a-z+]*://[^@]*@\([^/?]*\).*#\1#p')
    DBHOST=${DBHOSTPORT%%:*}
    case "$DBHOSTPORT" in *:*) DBPORT=${DBHOSTPORT##*:} ;; *) DBPORT=5432 ;; esac
    if [ -z "$DBHOST" ]; then
        ok "database driver: ${DBDRV:-unknown} (local)"
    elif command -v pg_isready >/dev/null 2>&1 && [ "${DBDRV%%+*}" = postgresql ]; then
        # Never print the connection string - it carries the password.
        if PGCONNECT_TIMEOUT=8 pg_isready -h "$DBHOST" -p "$DBPORT" -q 2>/dev/null; then
            ok "database ($DBDRV) reachable at $DBHOST:$DBPORT"
        else
            bad "database ($DBDRV) NOT reachable at $DBHOST:$DBPORT"
        fi
    else
        say "    database: ${DBDRV:-unknown} at $DBHOST:$DBPORT"
    fi
else
    warn "no database_connection set in $CONF"
fi
say ""

hr
if [ "$PROBLEMS" -gt 0 ]; then
    say "VERDICT: not healthy - $PROBLEMS problem(s), $WARNINGS warning(s) above."
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    say "VERDICT: serving, with $WARNINGS warning(s) above."
    exit 0
fi
say "VERDICT: healthy."
exit 0
