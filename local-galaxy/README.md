# Local Galaxy: desktop launcher and "Report a problem" form

Two additions for a Galaxy you run on your own Linux desktop:

| Folder | What it adds |
|--------|--------------|
| `launcher/` | A **Galaxy** desktop shortcut that starts the server and opens the browser once it answers |
| `report-problem/` | A **Report a problem** button on the Galaxy home page, a form, and a handler that saves each report to a file |

Both were built and tested against Galaxy **26.2.dev0** on Nobara 44 (KDE Plasma).
Neither changes Galaxy's own code.

## Desktop launcher

```bash
bash local-galaxy/launcher/install-launcher.sh /path/to/galaxy          # Galaxy on port 8080
bash local-galaxy/launcher/install-launcher.sh /path/to/galaxy 8090     # Galaxy on another port
```

This installs:

- `~/.local/bin/galaxy-start` — opens a terminal, runs Galaxy's `run.sh`, and opens
  `http://127.0.0.1:<port>` in the browser as soon as `/api/version` answers. If Galaxy is
  already running it just opens the browser.
- `Galaxy.desktop` on the Desktop and in the application menu, with Galaxy's own icon.

**Closing the terminal window stops Galaxy.** The first start can take several minutes.

### Changing Galaxy's port

The port you give the installer must be the one Galaxy listens on. Galaxy's default is
8080; if something else already uses it (on the machine this was built on, the
NemoClaw/OpenShell gateway does), move Galaxy in `config/galaxy.yml`:

```yaml
gravity:
  gunicorn:
    bind: localhost:8090
```

Find what holds a port with `ss -ltnp | grep :8080`. The installer warns when the port you
pass is not in `galaxy.yml`.

## "Report a problem" form

```bash
bash local-galaxy/report-problem/install-report-form.sh /path/to/galaxy
# then restart Galaxy - webhooks are only loaded at startup
```

The installer copies four files into the Galaxy root. It backs up an existing
`static/welcome.html` with a timestamp before replacing it, and is safe to re-run.

To have each report also appear in the admins' **notification bell** inside Galaxy, set
these under `galaxy:` in `config/galaxy.yml` before restarting (the installer reminds you if
either is missing):

```yaml
galaxy:
  admin_users: you@example.com          # must be a registered account
  enable_notification_system: true
```

After the restart, the home page shows a blue **"Having a problem with this Galaxy?"** box
with a **Report a problem »** button. If you don't see it, force-refresh the browser
(Ctrl+Shift+R) — the home page box is a separate page and is often cached.

### How it works

| File (installed into the Galaxy root) | Role |
|---------------------------------------|------|
| `static/welcome.html` | Galaxy's sample home page plus the "Report a problem" box. Galaxy shows whatever `welcome_url` points to (default `/static/welcome.html`) in the middle panel |
| `static/report-problem.html` | The form: optional email, short summary, and the problem (required, up to 5,000 characters). Its script sends the fields to the handler and shows the reply |
| `config/plugins/webhooks/report_problem/config.yml` | Registers a Galaxy *webhook* called `report_problem`. `activate: false` means it adds no button to the Galaxy UI, but Galaxy still loads its handler |
| `config/plugins/webhooks/report_problem/__init__.py` | The handler. Galaxy runs its `main()` for every call to `/api/webhooks/report_problem/data` |
| `static/report-admin.html` | The admin viewer: every report, newest first, with a filter and a **Load older reports** button. Not linked from the home page — the home page is public |
| `config/plugins/webhooks/report_admin/` | A read-only JSON API at `/api/webhooks/report_admin/data`, **refused to anyone who is not a Galaxy admin**. Also `activate: false`, so it never shows in `curl /api/webhooks` |

A report flows like this:

```
home page box -> report-problem.html -> GET /api/webhooks/report_problem/data?email=..&summary=..&problem=..
              -> __init__.py main()  -> appends one line to database/problem_reports/reports.jsonl
                                     -> sends a Galaxy notification to every admin (if enabled)
              -> {"ok": true, "id": "...", "admins_notified": N} -> form shows "Thanks ... Reference: ..."
```

The handler strips control characters, limits lengths (email 254, summary 200,
problem 5,000), rejects an empty problem or an email that doesn't look like one, and locks
the file while writing so two reports at once can't interleave.

The notification uses Galaxy's built-in `message` category (the same one admins use to
message users), sent with the `warning` style to every account returned by Galaxy's own
admin lookup. The file is written first; if sending the notification fails, the report is
still saved and the error goes to Galaxy's log.

### Reading the reports in Galaxy

1. Log in to Galaxy with an admin account (one listed in `admin_users`).
2. Click the **bell** (Notifications) in the left-hand activity bar. New reports show as
   unread, titled **"Problem report: <summary>"**.
3. Open one to see the reference, time, reporter email and problem. Mark it read or delete
   it there; that only affects your notification list, never the reports file.

Each admin gets their own copy. The reporter's text is shown inside code blocks, so a report
can't add clickable links, images or formatting to the message — Galaxy also sanitizes
notification HTML.

Notification preferences are per user: an admin who has turned off the *message*
category in **User → Preferences → Manage Notifications** won't receive reports, but they're still
written to the file. Galaxy also removes expired notifications over time, so the file is
the permanent record.

### Reading the reports file

Each line of `<galaxy root>/database/problem_reports/reports.jsonl` is one report:

```json
{"id": "d5cfc9d9eae1", "time": "2026-10-20T19:59:47-04:00", "email": "user@example.com",
 "logged_in_as": null, "summary": "Upload stuck", "problem": "...", "page": "http://127.0.0.1:8090/static/welcome.html"}
```

```bash
tail -n 5 /path/to/galaxy/database/problem_reports/reports.jsonl
python3 -c 'import json,sys; [print(r["time"], r["email"] or "-", r["summary"] or r["problem"][:60]) for r in map(json.loads, open(sys.argv[1]))]' \
    /path/to/galaxy/database/problem_reports/reports.jsonl
```

`database/` is ignored by Galaxy's git, so updating Galaxy never touches the reports.

### Reading them in a browser instead

Open **`/static/report-admin.html`** signed in as a Galaxy admin. It lists every
report newest-first with a text filter, shows whether Galaxy itself vouched for
the sender (a *signed in* badge) or the typed email is the only contact detail
(*anonymous*), and pages backwards through older reports.

Anyone who is not an admin — anonymous or a signed-in non-admin — gets
`Administrator access required.` and no data. The message is identical for both,
so probing it tells you nothing about whether a better account would help.

The page never assigns `innerHTML`. Report text is attacker-controlled, so every
value is written with `textContent`, and the submitter-supplied `page` URL is
shown as plain text rather than a link — it could be a `javascript:` URI or a
convincing phishing destination. Tests: `python3
local-galaxy/report-problem/webhooks/test_report_admin.py`.

### Testing it without a browser

```bash
curl -s http://127.0.0.1:8090/api/webhooks | grep -o '"report_problem"'      # handler loaded?
curl -s -G http://127.0.0.1:8090/api/webhooks/report_problem/data \
     --data-urlencode email=me@example.com --data-urlencode "problem=test report"
# {"ok": true, "id": "...", "admins_notified": 1}     (0 = notifications off or no admins)

# what landed in an admin's bell (API key of an admin account)
curl -s -H "x-api-key: $GALAXY_API_KEY" "http://127.0.0.1:8090/api/notifications?limit=5"
curl -s -G http://127.0.0.1:8090/api/webhooks/report_problem/data --data-urlencode "problem= "
# {"ok": false, "error": "Please describe the problem."}

# the admin API: loaded and correctly gated if an anonymous caller is refused.
# It is activate: false, so it never appears in /api/webhooks - check it directly.
curl -s http://127.0.0.1:8090/api/webhooks/report_admin/data
# {"ok": false, "error": "Administrator access required."}
curl -s -H "x-api-key: $GALAXY_API_KEY" "http://127.0.0.1:8090/api/webhooks/report_admin/data?limit=5"
# {"ok": true, "total": N, "scanned": N, "returned": 5, ...}   (admin key)
```

### Limits

- **The sender is identified only if they are signed in.** Galaxy builds a full
  `GalaxyWebTransaction` for webhook calls, so `trans.user` *is* available and
  `logged_in_as` records the signed-in account. It is `null` only for an anonymous
  visitor — which is the case worth supporting, since "I can't log in" is a problem
  worth reporting. For those, the only contact detail is the email they type, and
  nothing verifies it.
  (An earlier revision of this file claimed the session was never available. That was
  wrong; measured on Galaxy 26.0.1.dev1 by submitting on a logged-in session.)
- **Anyone who can reach Galaxy can submit.** Fine for a localhost-only install; on a shared
  server, put the endpoint behind authentication or rate limiting first.
- **No email.** Admins see reports in Galaxy's notification bell (when enabled) and in the
  file; nothing is emailed.
- **Galaxy updates.** `static/welcome.html` and `database/` are gitignored by Galaxy. The form
  page and webhook folder show as untracked files, which a `git pull` leaves alone.
