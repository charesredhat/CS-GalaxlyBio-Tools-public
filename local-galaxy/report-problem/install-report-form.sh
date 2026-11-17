#!/usr/bin/env bash
# install-report-form.sh - add a "Report a problem" form to a Galaxy home page.
#
# Usage: install-report-form.sh GALAXY_ROOT
#
# Copies the welcome page, the form page, the admin viewer page and both
# webhooks into GALAXY_ROOT. An existing static/welcome.html is backed up
# first. Restart Galaxy afterwards: webhooks are only loaded at startup.
# Safe to re-run.
#
# Two pieces get installed:
#   report_problem - the public submission endpoint, linked from the home page.
#   report_admin   - a read-only JSON API for static/report-admin.html, refused
#                    to anyone who is not a Galaxy admin. Not linked from the
#                    public welcome page on purpose.
#
# Reports also reach admins' notification bell when galaxy.yml has
# enable_notification_system: true (the script tells you if it is missing).

set -eu

case "${1:-}" in
    ""|-h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

GALAXY_ROOT=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)

[ -d "$GALAXY_ROOT/static" ] && [ -d "$GALAXY_ROOT/config" ] \
    || { echo "$GALAXY_ROOT does not look like a Galaxy root (no static/ or config/)" >&2; exit 1; }

WEBHOOKS="$GALAXY_ROOT/config/plugins/webhooks"
WELCOME="$GALAXY_ROOT/static/welcome.html"

if [ -f "$WELCOME" ] && ! cmp -s "$WELCOME" "$HERE/static/welcome.html"; then
    backup="$WELCOME.bak.$(date +%Y%m%d-%H%M%S)"
    cp "$WELCOME" "$backup"
    echo "Backed up existing welcome page to $backup"
fi

install -m 644 "$HERE/static/welcome.html" "$WELCOME"
install -m 644 "$HERE/static/report-problem.html" "$GALAXY_ROOT/static/report-problem.html"
install -m 644 "$HERE/static/report-admin.html" "$GALAXY_ROOT/static/report-admin.html"

for hook in report_problem report_admin; do
    mkdir -p "$WEBHOOKS/$hook"
    install -m 644 "$HERE/webhooks/$hook/config.yml" "$WEBHOOKS/$hook/config.yml"
    install -m 644 "$HERE/webhooks/$hook/__init__.py" "$WEBHOOKS/$hook/__init__.py"
done

echo "Installed the report form and the admin viewer into $GALAXY_ROOT"
if ! grep -qE '^[[:space:]]+enable_notification_system:[[:space:]]*true' "$GALAXY_ROOT/config/galaxy.yml" 2>/dev/null; then
    echo "NOTE: to get reports in admins' Galaxy notification bell, add under 'galaxy:' in"
    echo "      $GALAXY_ROOT/config/galaxy.yml:   enable_notification_system: true"
fi
grep -qE '^[[:space:]]+admin_users:[[:space:]]*[^[:space:]#]' "$GALAXY_ROOT/config/galaxy.yml" 2>/dev/null \
    || echo "NOTE: no admin_users set in galaxy.yml - notifications need at least one admin account"

cat <<'EOF'

Restart Galaxy, then verify both halves. They need different checks, because
report_admin sets activate: false and so never appears in the webhook listing:

  # the submission endpoint - should be listed
  curl -s http://127.0.0.1:<port>/api/webhooks | grep report_problem

  # the admin API - loaded and correctly gated if an anonymous caller is refused
  curl -s http://127.0.0.1:<port>/api/webhooks/report_admin/data
  #   expected: {"ok": false, "error": "Administrator access required."}
  #   a 404 instead means the webhook did not load; an "ok": true means the
  #   admin gate is not working - stop and investigate before exposing it.

Then open /static/report-admin.html in a browser, signed in as an admin.
EOF
echo "Reports are written to: $GALAXY_ROOT/database/problem_reports/reports.jsonl"
