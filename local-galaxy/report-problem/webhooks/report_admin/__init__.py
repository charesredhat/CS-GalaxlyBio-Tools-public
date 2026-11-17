"""
Admin-only read API over the problem reports, for static/report-admin.html.

Access control
--------------
`trans.user_is_admin` is the gate. That works because Galaxy builds a full
`GalaxyWebTransaction` for webhook calls, so the login session IS available
here -- `trans.user` is the signed-in user, or None for an anonymous caller.
(An earlier revision of this toolkit claimed webhooks never see the session.
That is wrong: `logged_in_as` is null only when nobody is logged in.)

This matters because the reports contain reporter email addresses and free text.
The submission endpoint is deliberately open; this one must not be.

Why the data is returned, not rendered
--------------------------------------
The handler emits JSON and the page renders it with textContent, never innerHTML.
Report text is attacker-controlled -- anyone who can reach Galaxy can submit
5,000 characters of anything -- so it must never be interpolated into markup.

Counting, and why `total` is not `len(rows)`
-------------------------------------------
Two independent caps apply, and conflating them hides reports:

* `MAX_SCAN_BYTES` bounds how much of the file is *parsed* (memory safety).
* `limit` bounds how many records are *returned* (response size).

`total` is therefore counted separately, by scanning the whole file for newlines
without parsing any JSON -- cheap even at hundreds of megabytes. Without that,
`total` would report the size of the 8 MB window and an admin would be told
"500 report(s)" while 400 sat unseen. Every response carries `total`, `scanned`
and `returned` so the page can say which of the three it is showing.
"""

import json
import os

MAX_LIMIT = 500
DEFAULT_LIMIT = 50
# Guards against reading an unbounded file into memory if reports pile up.
MAX_SCAN_BYTES = 8 * 1024 * 1024
# Read size for the newline count; large enough to keep syscalls down.
COUNT_CHUNK = 1024 * 1024


def _reports_file(trans):
    data_dir = getattr(trans.app.config, "data_dir", None) or os.path.join(trans.app.config.root, "database")
    return os.path.join(data_dir, "problem_reports", "reports.jsonl")


def _as_int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _count_records(path):
    """Total records in the file, counted without parsing JSON.

    The writer appends exactly one newline-terminated line per report, so
    counting newlines is the record count. A final line with no trailing
    newline (a partial write) still counts as one record.
    """
    total = 0
    tail = b""
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(COUNT_CHUNK)
            if not chunk:
                break
            total += chunk.count(b"\n")
            tail = chunk[-1:]
    if tail and tail != b"\n":
        total += 1
    return total


def _empty_row(line, lineno):
    """Placeholder for a line that is not valid JSON.

    Keyed by line number so several corrupt lines stay distinguishable -- a
    shared "(unparseable)" id makes them look like one repeated record.
    """
    return {
        "id": "(unparseable line %d)" % lineno,
        "time": "",
        "email": "",
        "logged_in_as": None,
        "summary": "",
        "problem": line[:500],
        "page": "",
    }


def main(trans, webhook, params):
    if not trans.user_is_admin:
        # Same message whether the caller is anonymous or a non-admin user: no
        # reason to tell an unauthenticated prober that the endpoint exists and
        # merely needs a better account.
        return {"ok": False, "error": "Administrator access required."}

    limit = _as_int(params.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _as_int(params.get("offset"), 0, 0, 10_000_000)
    path = _reports_file(trans)

    # One response shape, always -- a caller that has to branch on which keys
    # came back is a caller that will eventually forget to.
    result = {
        "ok": True,
        "total": 0,
        "scanned": 0,
        "returned": 0,
        "offset": offset,
        "limit": limit,
        "file_truncated": False,
        "reports": [],
    }
    if not os.path.exists(path):
        return result

    size = os.path.getsize(path)
    truncated = size > MAX_SCAN_BYTES
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lineno = 0
        if truncated:
            # Keep the newest records: seek near the end and drop the partial line.
            fh.seek(size - MAX_SCAN_BYTES)
            fh.readline()
        for line in fh:
            lineno += 1
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                # One corrupt line must not hide every other report.
                rows.append(_empty_row(line, lineno))

    rows.reverse()  # newest first
    page = rows[offset:offset + limit]
    result.update(
        total=_count_records(path),
        scanned=len(rows),
        returned=len(page),
        file_truncated=truncated,
        reports=page,
    )
    return result
