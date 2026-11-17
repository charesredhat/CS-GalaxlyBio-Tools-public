import fcntl
import json
import logging
import os
import re
import uuid
from datetime import datetime

log = logging.getLogger(__name__)

MAX_EMAIL = 254
MAX_SUMMARY = 200
MAX_PROBLEM = 5000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(value, limit):
    return CONTROL_RE.sub("", str(value or "")).strip()[:limit]


def _reports_file(trans):
    data_dir = getattr(trans.app.config, "data_dir", None) or os.path.join(trans.app.config.root, "database")
    report_dir = os.path.join(data_dir, "problem_reports")
    os.makedirs(report_dir, exist_ok=True)
    return os.path.join(report_dir, "reports.jsonl")


def _as_code_block(text):
    # Indented code block: user text can't add links, images or HTML to the message.
    return "\n".join("    " + line for line in (text or "-").splitlines()) or "    -"


def _notify_admins(trans, report):
    """Send the report to every admin's Galaxy notification bell. Returns the count notified."""
    manager = getattr(trans.app, "notification_manager", None)
    if manager is None or not manager.notifications_enabled:
        return 0
    admins = trans.app.user_manager.admins()
    if not admins:
        return 0

    from galaxy.schema.notifications import (
        MessageNotificationContent,
        NotificationCreateRequest,
        NotificationRecipients,
        NotificationVariant,
        PersonalNotificationCategory,
    )

    try:
        from galaxy.schema.notifications import InternalNotificationCreateData as CreateData
    except ImportError:  # Galaxy before 26.2 (same fields, older name)
        from galaxy.schema.notifications import NotificationCreateData as CreateData

    subject = "Problem report: " + (report["summary"] or report["problem"].splitlines()[0])[:80]
    message = "\n\n".join(
        [
            f"**Reference:** {report['id']}  \n**Time:** {report['time']}",
            "**Reporter email:**",
            _as_code_block(report["email"]),
            "**Problem:**",
            _as_code_block(report["problem"]),
            "_Full record: database/problem_reports/reports.jsonl_",
        ]
    )
    request = NotificationCreateRequest(
        recipients=NotificationRecipients(user_ids=[u.id for u in admins]),
        notification=CreateData(
            source="report_problem",
            category=PersonalNotificationCategory.message,
            variant=NotificationVariant.warning,
            content=MessageNotificationContent(subject=subject, message=message),
        ),
    )
    _, sent = manager.send_notification_to_recipients(request)
    return sent


def main(trans, webhook, params):
    email = _clean(params.get("email"), MAX_EMAIL)
    summary = _clean(params.get("summary"), MAX_SUMMARY)
    problem = _clean(params.get("problem"), MAX_PROBLEM)

    if not problem:
        return {"ok": False, "error": "Please describe the problem."}
    if email and not EMAIL_RE.match(email):
        return {"ok": False, "error": "That email address does not look valid."}

    user = getattr(trans, "user", None)
    report = {
        "id": uuid.uuid4().hex[:12],
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "email": email,
        "logged_in_as": user.email if user else None,
        "summary": summary,
        "problem": problem,
        "page": _clean(params.get("page"), 500),
    }

    with open(_reports_file(trans), "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")

    # The file is the record of truth; a notification failure must not lose the report.
    try:
        notified = _notify_admins(trans, report)
    except Exception:
        log.exception("report_problem: saved report %s but could not notify admins", report["id"])
        notified = 0

    return {"ok": True, "id": report["id"], "admins_notified": notified}
