#!/bin/sh
# find-galaxy.sh - locate a Galaxy installation. POSIX sh, no root needed.
# Usage: sh find-galaxy.sh [extra_search_path ...]

say() { printf '%s\n' "$*"; }
hr()  { say "------------------------------------------------------------"; }

say "== Galaxy detection on $(hostname 2>/dev/null) =="
say "user: $(id -un 2>/dev/null)   date: $(date 2>/dev/null)"
hr

PROCS=$(ps -eo pid,user,args 2>/dev/null \
  | grep -Ei 'galaxy|gravity|galaxyctl' \
  | grep -vE 'grep|find-galaxy')

say "[1] Running Galaxy processes"
if [ -n "$PROCS" ]; then
    printf '%s\n' "$PROCS" | head -15
else
    say "  none"
fi
say ""

say "[2] Listening ports (8080/8000 are Galaxy defaults)"
{ ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null; } \
  | grep -E ':(80|8080|8000|9090)\b' | head -10
say ""

say "[3] Services / containers"
systemctl list-units --all --no-pager --no-legend 2>/dev/null | grep -i galaxy | head -5
docker ps -a --format '{{.Names}}  {{.Image}}  {{.Status}}' 2>/dev/null | grep -i galaxy | head -5
supervisorctl status 2>/dev/null | grep -i galaxy | head -5
say ""

say "[4] Filesystem search for Galaxy roots"

SEEN=""
FOUND=0

emit_root() {
    root=$1
    case " $SEEN " in *" $root "*) return 0 ;; esac
    SEEN="$SEEN $root"
    ver=""
    # 26.2 moved lib/galaxy/version.py to a package, lib/galaxy/version/__init__.py.
    vf=""
    for f in "$root/lib/galaxy/version.py" "$root/lib/galaxy/version/__init__.py"; do
        [ -f "$f" ] && { vf=$f; break; }
    done
    if [ -n "$vf" ]; then
        ver=$(sed -n 's/^VERSION_MAJOR *= *"\([^"]*\)".*/\1/p' "$vf" 2>/dev/null | head -1)
        min=$(sed -n 's/^VERSION_MINOR *= *"\([^"]*\)".*/\1/p' "$vf" 2>/dev/null | head -1)
        [ -n "$min" ] && ver="$ver.$min"
    fi
    say "  *** GALAXY ROOT: $root   (version ${ver:-unknown})"
    # A root on a network filesystem is worth calling out: every tool XML read at
    # startup becomes a round trip, which is enough on its own to push boot past
    # the gunicorn timeout. See galaxy-health.sh.
    fstype=$(stat -f -c %T "$root" 2>/dev/null)
    case "$fstype" in
        nfs*|cifs|smb*|fuse*|glusterfs|lustre|ceph)
            say "        NOTE: on $fstype - expect slow startup (see galaxy-health.sh)" ;;
    esac
    for c in config/galaxy.yml config/galaxy.ini universe_wsgi.ini \
             config/auth_conf.xml config/tool_conf.xml config/oidc_config.xml \
             config/oidc_backends_config.xml; do
        [ -e "$root/$c" ] && say "        config: $c"
    done
    # DB target only - never print credentials
    if [ -r "$root/config/galaxy.yml" ]; then
        sed -n 's/.*database_connection: *\([a-z+]*\):.*/        db driver: \1/p' \
            "$root/config/galaxy.yml" 2>/dev/null | head -1
    fi
    FOUND=1
}

is_root() {
    [ -d "$1/lib/galaxy" ] || return 1
    [ -e "$1/run.sh" ] || [ -d "$1/config" ]
}

# Fast path 1: derive the root from the running processes. This is the most
# reliable source - a live Galaxy names its own paths in argv - and it costs
# nothing. It also finds roots the directory walk below cannot reach, such as
# an install under a network mount.
if [ -n "$PROCS" ]; then
    for cand in $(printf '%s\n' "$PROCS" \
                    | tr ' =' '\n\n' \
                    | sed -n 's#^\(/[^:,]*\)/\(venv/bin\|config\|lib\|scripts\|database\)\(/.*\)\{0,1\}$#\1#p' \
                    | sort -u); do
        is_root "$cand" && emit_root "$cand"
    done
fi

# Fast path 2: common install locations, plus any mount point (an install on a
# dedicated or network volume is normal) and any path given on the command line.
MOUNTS=$(awk '$2 ~ /^\// && $2 != "/" {print $2}' /proc/mounts 2>/dev/null \
           | grep -vE '^/(proc|sys|dev|run|snap|boot)(/|$)' | head -40)
COMMON="/srv/galaxy /opt/galaxy /home/galaxy /usr/local/galaxy /galaxy /data/galaxy \
        /export/galaxy /srv/www/galaxy /server/galaxy /server $HOME $MOUNTS $*"

for base in $COMMON; do
    [ -d "$base" ] || continue
    for cand in "$base" "$base"/*; do
        is_root "$cand" && emit_root "$cand"
    done
done

if [ "$FOUND" -eq 0 ]; then
    say "  not in common paths; scanning filesystem..."
    # A Galaxy root is never deeply nested, so cap the depth: an unbounded walk
    # of a network mount can run for many minutes. -xdev keeps each pass on one
    # filesystem, so every mount point is scanned separately rather than skipped.
    SCAN=$(for start in / $MOUNTS; do
               [ -d "$start" ] || continue
               find "$start" -xdev -maxdepth 7 -type d -name galaxy -path '*/lib/galaxy' 2>/dev/null
           done)
    # Not piped into a loop: a subshell could not report back whether it found
    # anything, which made the closing message contradict the output above it.
    for d in $SCAN; do
        root=${d%/lib/galaxy}
        is_root "$root" && emit_root "$root"
    done
fi
say ""

say "[5] Packaged installs"
{ pip list 2>/dev/null || pip3 list 2>/dev/null; } | grep -iE '^galaxy' | head -10
conda env list 2>/dev/null | grep -i galaxy | head -5
say ""
hr
if [ "$FOUND" -eq 0 ]; then
    if [ -n "$PROCS" ]; then
        say "done. Galaxy processes are running but no root was identified -"
        say "re-run with the install path as an argument, e.g. sh find-galaxy.sh /server/galaxy"
    else
        say "done. no GALAXY ROOT line above = no installation found."
    fi
else
    say "done."
fi
