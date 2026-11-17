# Installing the report form on a shared, public Galaxy

The installer one level up (`../install-report-form.sh`) is written for a Galaxy
on your own desktop. Two of its assumptions break on a public, multi-user
instance such as `sandbox.galaxytrakr.org`. Use this folder's pieces instead.

## What differs, and why

| | Local desktop | Public shared server |
|---|---|---|
| Welcome page | replaced with Galaxy's sample page + the box | **merge** the box into the existing page (`inject-report-box.py`) |
| Endpoint exposure | localhost only, no limit needed | **rate limited** (`report-problem-ratelimit.conf`) |
| File ownership | your user | must be readable by the `galaxy` user |
| Restart cost | seconds, affects you | minutes of 502 for everyone |

### 1. The welcome page must be merged, not replaced

`install-report-form.sh` overwrites `static/welcome.html` with Galaxy's stock
"Hello, Galaxy is running!" page plus the box. That is the right move on a
desktop, where the page being replaced is that same placeholder. On an instance
with real branding and real links it throws them away, and the timestamped
backup does not help once users are looking at a generic page.

`inject-report-box.py` adds only the box and leaves everything else
byte-for-byte:

```sh
python3 inject-report-box.py /server/galaxy --dry-run                      # show, change nothing
python3 inject-report-box.py /server/galaxy --position top --style bootstrap
python3 inject-report-box.py /server/galaxy --remove                       # take it back out
```

Idempotent, and backs up before every change.

- `--position top` (default) puts the box before the first content container,
  where a problem-report prompt is actually useful. `bottom` appends before
  `</body>`, i.e. below the funding footer where nobody scrolls.
- `--style inline` (default) depends on no stylesheet, so it renders on any
  page. `--style bootstrap` reproduces the reference markup and matches
  Galaxy's own buttons, but renders correctly **only** if the page loads
  `dist/base.css` — check before choosing it.

**As it turned out on this sandbox (2026-10-22), the welcome page was Galaxy's
stock sample page**, untouched since 2026-04-15, not a branded one. A `diff`
against the upstream replacement showed the *only* difference was the box
itself, so the replace would have been harmless here after all. The merge was
still the right default — that is knowable only after looking, and the injector
also brings idempotency, a marker and a clean `--remove`. Because that page does
load `dist/base.css`, `--style bootstrap --position top` was used, which
reproduces the upstream look exactly.

### 2. The endpoint is an open write endpoint

`/api/webhooks/report_problem/data` is an unauthenticated **GET** that appends up
to ~5.7 KB per call to `database/problem_reports/reports.jsonl`. The upstream
README is explicit:

> Anyone who can reach Galaxy can submit. Fine for a localhost-only install; on a
> shared server, put the endpoint behind authentication or rate limiting first.

On this instance that file lives on the EFS volume Galaxy also uses for datasets
and jobs, so an unbounded writer is a disk-fill risk against Galaxy itself, not
just a spam problem. `report-problem-ratelimit.conf` is the rate-limiting half:
5 requests/minute/client, `429` on refusal.

Behind a load balancer nginx sees the ALB as the peer, so an uncorrected per-IP
limit lumps the whole internet into one bucket — throttling real users while
barely inconveniencing an abuser. Both halves of the fix are now **verified on
this host** rather than assumed:

| Checked | Result |
|---|---|
| `nginx -V \| grep http_realip` | present (nginx 1.24.0 Ubuntu) |
| Which LB serves the name | confirm your Galaxy hostname and your LB's own DNS resolve to the same IPs |
| Its subnets | derive per-AZ CIDRs with the `aws elbv2` / `aws ec2` commands in the conf file |

The config must trust those per-AZ /24s specifically, not the whole VPC CIDR — a
host elsewhere in the VPC then cannot spoof `X-Forwarded-For` past the limit. **If
the ALB gains a subnet, add it,** or that AZ's traffic all shares one bucket.

The shipped conf has `SUBNET_AZ_n/24` placeholders; fill them in from your own
environment before `nginx -t`. Real subnet CIDRs, VPC ids and host addresses are
deliberately not committed.

One side effect to know: the `real_ip_*` directives sit in `http` context (they
have to — `limit_req_zone` does, and POST_READ-phase rewriting is the only
reliable ordering), so `$remote_addr` becomes the true client IP for the **whole
server**, not just this endpoint. That improves the access log and the
`X-Real-IP` Galaxy receives, but it is a global change, not a scoped one.

`add-ratelimit-location.py` inserts the matching `location` block:

```sh
sudo python3 add-ratelimit-location.py --from-location --dry-run
sudo python3 add-ratelimit-location.py --from-location
sudo nginx -t && sudo systemctl reload nginx
sudo python3 add-ratelimit-location.py --remove        # to undo
```

`--from-location` copies the `proxy_set_header` and `proxy_pass` lines out of the
live `location /` instead of trusting the defaults. That matters because an
exact-match `location =` **replaces** the prefix location for its URL rather
than extending it — nginx does not inherit `proxy_set_header` between sibling
locations, so a block that omitted them would reach Galaxy with no `Host` or
`X-Forwarded-*` and Galaxy would build wrong absolute URLs for this one endpoint.
This vhost also uses a custom `$forwarded_proto`, which scraping picks up for
free.

What rate limiting does **not** fix: anyone who can reach Galaxy can still
submit, and the email field is whatever the submitter typed, verified by nothing.
Treat report contents as untrusted input — they are free text from the public
internet, so anything that renders them must escape them.

**Correction to an earlier claim here:** this file used to say Galaxy's webhook
endpoint never loads the login session and `logged_in_as` is always `null`. That
is wrong. Galaxy builds a full `GalaxyWebTransaction` for webhook calls, so
`trans.user` is available and `logged_in_as` records the signed-in account;
`null` means the submitter was genuinely anonymous. Verified on 26.0.1.dev1 by
submitting a report on an authenticated session. This is what makes the
admin-gated viewer below possible — the gate is `trans.user_is_admin`.

## Viewing the reports: `/static/report-admin.html`

A read-only page listing submitted reports, newest first, with a text filter.
Two pieces:

| File | Role |
|---|---|
| `webhooks/report_admin/` | JSON API at `/api/webhooks/report_admin/data`, **refused unless `trans.user_is_admin`** |
| `static/report-admin.html` | the page; fetches that endpoint with `credentials: same-origin` |

Reach it at `https://<your-galaxy>/static/report-admin.html` while signed in as
an admin. It is deliberately **not linked from the welcome page** — the welcome
page is public, and advertising the endpoint there invites probing. A non-admin
or anonymous visitor gets `Administrator access required.` and no data; the
message is identical for both, so a prober learns nothing about whether a better
account would help.

**The page never uses `innerHTML`.** Report text is attacker-controlled — anyone
who can reach Galaxy can submit 5,000 characters of anything — so every value is
written with `textContent`, and the submitter-supplied `page` URL is rendered as
plain text rather than a clickable link, because it could be a `javascript:`
URI or a plausible-looking phishing destination.

### Installing it

`install-report-form.sh GALAXY_ROOT` installs both webhooks and all three
static pages, then prints the verification commands. Restart Galaxy afterwards
— webhooks load only at startup.

`report_admin/config.yml` sets **`activate: false`** on purpose: the handler
has no Galaxy UI to contribute, but its `main()` stays reachable at
`/api/webhooks/report_admin/data`, which is all the page needs. That means it
never appears in `curl /api/webhooks`, so verify it by its own endpoint
instead — an anonymous caller should be refused:

```bash
curl -s http://127.0.0.1:<port>/api/webhooks/report_admin/data
# {"ok": false, "error": "Administrator access required."}
```

> **Re-test this after any Galaxy upgrade.** That a deactivated webhook still
> serves its API endpoint is observed behaviour (verified on 26.0.1.dev1), not
> a documented contract. A `404` after an upgrade means Galaxy started
> filtering deactivated webhooks out of the API too, and the page will need
> `activate: true` plus an accepted masthead entry. An `"ok": true` from an
> anonymous caller is the serious case — stop and investigate.

### Reading all of them, and the three numbers

Two independent caps apply, and the page reports both rather than quietly
rounding them off:

| Cap | What it bounds | Why |
|---|---|---|
| `limit` (max 500, page requests 200) | records per response | response size |
| `MAX_SCAN_BYTES` (8 MB) | how much of the file is parsed, newest end first | a large file can't become a memory-exhaustion lever |

So each response carries three counts — `total` (records in the file, counted
by newlines without parsing any JSON), `scanned` (records inside the 8 MB
window) and `returned` (records in this page) — and the page shows them as
*"200 of 900 on file"* with a **Load older reports** button that walks
backwards by `offset`.

This replaces earlier behaviour where `total` was the size of the parse window:
with 900 reports an admin was shown `500 report(s)` and the remaining 400 were
unreachable from the page. The text filter searches **loaded** reports only,
which is why the box says so and the count distinguishes loaded from on file.

`python3 webhooks/test_report_admin.py` covers the gate, the three counts, the
paging walk and corrupt-line handling (20 tests, stdlib `unittest`).

## Notifications to admins

The submission handler also pushes each report to every admin's Galaxy
notification bell. **This needs `enable_notification_system: true` in
galaxy.yml** — it defaults to `false`, and with it off `NotificationManager`
reports `notifications_enabled` False, so the handler notifies nobody and says
so only in its `admins_notified: 0` response. That silence is the failure mode
to watch for.

Notification failures are caught and logged: the file is the record of truth, so
a broken notification must never lose a report.

## Runbook

The sandbox runs **11:00–22:00 UTC, Mon–Fri** (`shutdown=true` tag +
`utils-ec2-toggleFlaggedInstances-*` EventBridge rules). Outside that window the
host is stopped and none of this applies.

```sh
# 0. host is up?
curl -s -o /dev/null -w '%{http_code}\n' https://sandbox.galaxytrakr.org/api/version

# 1. look at the current welcome page BEFORE touching it
sudo -u galaxy head -40 /server/galaxy/static/welcome.html

# 2. form page + webhook handler (not the welcome page)
sudo install -m 644 -o galaxy -g galaxy static/report-problem.html \
    /server/galaxy/static/report-problem.html
sudo install -d -m 755 -o galaxy -g galaxy \
    /server/galaxy/config/plugins/webhooks/report_problem
sudo install -m 644 -o galaxy -g galaxy webhooks/report_problem/config.yml \
    /server/galaxy/config/plugins/webhooks/report_problem/config.yml
sudo install -m 644 -o galaxy -g galaxy webhooks/report_problem/__init__.py \
    /server/galaxy/config/plugins/webhooks/report_problem/__init__.py

# 3. merge the box into the real welcome page
sudo -u galaxy python3 inject-report-box.py /server/galaxy --dry-run
sudo -u galaxy python3 inject-report-box.py /server/galaxy

# 4. rate limit, before the endpoint is live
sudo install -m 644 report-problem-ratelimit.conf \
    /etc/nginx/conf.d/report-problem-ratelimit.conf
sudo python3 add-ratelimit-location.py --from-location
sudo nginx -t && sudo systemctl reload nginx

# 5. restart Galaxy -- webhooks load only at startup (~4-5 min of 502)
sudo su - galaxy -c 'cd /server/galaxy && source venv/bin/activate && galaxyctl restart'

# 6. verify
curl -s https://sandbox.galaxytrakr.org/api/webhooks | grep -o report_problem
curl -s -G https://sandbox.galaxytrakr.org/api/webhooks/report_problem/data \
     --data-urlencode "problem=installation smoke test, please ignore"
#   -> {"ok": true, "id": "..."}
curl -s -G https://sandbox.galaxytrakr.org/api/webhooks/report_problem/data \
     --data-urlencode "problem= "
#   -> {"ok": false, "error": "Please describe the problem."}
for i in $(seq 1 10); do          # rate limit actually biting?
  curl -s -o /dev/null -w "%{http_code} " -G \
    https://sandbox.galaxytrakr.org/api/webhooks/report_problem/data \
    --data-urlencode "problem=ratelimit probe $i"
done; echo
#   -> expect 200s then 429s
sudo -u galaxy tail -3 /server/galaxy/database/problem_reports/reports.jsonl
```

Remove the smoke-test lines from `reports.jsonl` afterwards, or they sit in the
admin's queue as real reports. If the server has `enable_notification_system: true`,
each smoke test (including every rate-limit probe that gets a 200) also lands in
every admin's notification bell — delete those there too, or turn notifications on
only after testing.

## Rollback

```sh
sudo -u galaxy python3 inject-report-box.py /server/galaxy --remove
sudo rm -rf /server/galaxy/config/plugins/webhooks/report_problem
sudo rm -f /server/galaxy/static/report-problem.html
sudo rm -f /etc/nginx/conf.d/report-problem-ratelimit.conf
sudo python3 add-ratelimit-location.py --remove
sudo nginx -t && sudo systemctl reload nginx
sudo su - galaxy -c 'cd /server/galaxy && source venv/bin/activate && galaxyctl restart'
```

Reports already collected stay in `database/problem_reports/reports.jsonl`;
nothing above deletes them.

## Version note

Built and tested against Galaxy **26.2.dev0**; this sandbox runs **26.0.1.dev1**.
Webhooks are long-standing API. Saving to `reports.jsonl` uses nothing
version-specific; the optional admin notification uses Galaxy's notification
schema, which renamed its create class in 26.2 — the handler imports
`InternalNotificationCreateData` and falls back to 26.0's `NotificationCreateData`
(same fields, checked against the `release_26.0` source, not run on 26.0). If a
notification still fails, the report is saved anyway and the error is logged as
`report_problem: saved report ... but could not notify admins`. Either way,
confirm the handler actually loaded (step 6's `/api/webhooks` grep) rather than
assuming it did — a webhook that fails to import is reported as a bare
`KeyError` on the plugin id, which points nowhere near the cause. The same class
of failure cost an afternoon on the OIDC work: see `../../search-and-edit-gal/sandbox-oidc/README.md`.

## Gotchas hit while installing this for real

- **Never back up an nginx site file beside the original.** Debian/Ubuntu does
  `include /etc/nginx/sites-enabled/*;` with no extension filter, so
  `galaxy.bak-…` is parsed as a second server block and `nginx -t` fails with
  *"a duplicate default server for 0.0.0.0:8080"*. Backups belong in
  `/etc/nginx/backups/`. `add-ratelimit-location.py` now refuses a backup
  directory equal to the site's own, and `nginx -t` catches it before a reload
  either way — which is exactly why the reload comes after the test, never
  before.
- **`/api/webhooks/<id>/data` answers `{"err_msg": "Uncaught exception in
  exposed API method"}` until Galaxy restarts.** Webhooks load at startup only,
  so this is the expected pre-restart response, not a broken handler.
- **The welcome page and form page are static**, served straight by nginx, so
  the box appears immediately with no restart. Only the handler needs the
  restart. Force-refresh (Ctrl+Shift+R) if an old copy is cached.
