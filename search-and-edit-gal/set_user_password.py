#!/usr/bin/env python3
"""
Set a Galaxy user's password, reading it from a prompt rather than the command line.

What it does, and why each part
-------------------------------
Mirrors what Galaxy itself does in `User.set_password_cleartext`
(lib/galaxy/model/__init__.py) plus `UserManager.__set_password`
(lib/galaxy/managers/users.py), so the result is indistinguishable from a
password changed through the web UI:

  1. hashes with PBKDF2 in Galaxy's stored format (galaxy_db.hash_password_pbkdf2)
  2. stamps `last_password_change`, which Galaxy's own setter does and which
     `password_expiration_period` is measured against
  3. invalidates the user's OTHER active sessions -- Galaxy does this too. Without
     it, anyone already logged in as that user stays logged in, so the change
     would not actually cut off existing access.

Why a prompt and not an argument
--------------------------------
A password passed as `--password` lands in your shell history, in `ps` output
for every user on the box while the process runs, and in this repo's transcripts
if it was ever typed to an assistant. It is read with getpass here, confirmed by
retyping, and never printed or logged.

Galaxy's own rule is the only strength rule applied: at least
PASSWORD_MIN_LEN = 6 characters (security/validate_user_input.py). That is a low
bar; for an account with admin rights pick something much longer.

This does NOT force a later change
----------------------------------
Galaxy has no per-user "must change at next login" flag. The only mechanism is
`password_expiration_period` in galaxy.yml, which is instance-wide and measured
by password age -- enabling it affects every account, and a value low enough to
catch a freshly-set password catches everything older too. `--age-days` exists
for that case: it backdates `last_password_change` so this one account reads as
expired once a period is enabled, without having to pick a period low enough to
sweep up everyone else.

Usage
-----
    python3 set_user_password.py --email someone@example.org \
        --galaxy-config /server/galaxy/config/galaxy.yml

    python3 set_user_password.py --email someone@example.org \
        --galaxy-config /server/galaxy/config/galaxy.yml --age-days 400

Run it where the database is reachable; on a VPC-only Aurora that means on the
Galaxy host itself.
"""

import argparse
import getpass
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from galaxy_db import (  # noqa: E402
    add_db_arguments,
    hash_password_pbkdf2,
    open_db,
)

PASSWORD_MIN_LEN = 6  # security/validate_user_input.py


def read_password(confirm: bool = True) -> str:
    """Prompt twice, never echo, never return something Galaxy would reject."""
    if not sys.stdin.isatty():
        sys.exit(
            "Error: refusing to read a password from a pipe.\n"
            "  Run this interactively. Piping it in puts the cleartext in your shell\n"
            "  history or in the calling script, which is the thing this avoids."
        )
    while True:
        first = getpass.getpass("New password (not shown): ")
        if len(first) < PASSWORD_MIN_LEN:
            print(f"  Too short -- Galaxy requires at least {PASSWORD_MIN_LEN} characters.")
            continue
        if not confirm:
            return first
        again = getpass.getpass("Retype it: ")
        if first != again:
            print("  Those do not match; try again.")
            continue
        return first


def main():
    parser = argparse.ArgumentParser(
        description="Set a Galaxy user's password from an interactive prompt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--email", "-e", required=True, help="the account to change")
    parser.add_argument("--age-days", type=int, default=0, metavar="N",
                        help="backdate last_password_change by N days, so the account reads as "
                             "expired once password_expiration_period is enabled instance-wide. "
                             "Default 0 (now), matching Galaxy's own behaviour.")
    parser.add_argument("--keep-sessions", action="store_true",
                        help="do NOT invalidate the user's other active sessions. Galaxy's own "
                             "password change invalidates them; keeping them means anyone already "
                             "logged in as this user stays logged in.")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would change, prompt for nothing, write nothing")
    add_db_arguments(parser)
    args = parser.parse_args()

    db = open_db(args)
    print(f"Database: {db.describe()}")

    rows = db.query(
        "SELECT id, email, deleted, purged, external, last_password_change "
        "FROM galaxy_user WHERE lower(email) = lower(?)",
        (args.email,),
    )
    if not rows:
        sys.exit(f"Error: no account with email {args.email!r}")
    user = rows[0]

    print(f"\nAccount: {user['email']} (id {user['id']})")
    print(f"  last_password_change : {user['last_password_change']}")
    print(f"  deleted / purged     : {bool(user['deleted'])} / {bool(user['purged'])}")
    print(f"  external flag        : {bool(user['external'])}")

    if user["deleted"] or user["purged"]:
        sys.exit("Error: refusing to set a password on a deleted or purged account.")
    if user["external"]:
        # Galaxy's web login refuses an external account outright
        # (controllers/user.py), so a password set here could not be used to log in.
        print(
            "\nWarning: this account has external=True, so Galaxy's web login form will\n"
            "         refuse it regardless of the password. Clear that flag first, or the\n"
            "         new password will only be usable against /api/authenticate/baseauth."
        )

    sessions = db.query(
        "SELECT COUNT(*) AS n FROM galaxy_session WHERE user_id = ? AND is_valid = ?",
        (user["id"], True),
    )
    n_sessions = sessions[0]["n"] if sessions else 0

    # Naive UTC, matching what Galaxy stores: galaxy.model.orm.now writes a naive
    # UTC timestamp, and the existing column values carry no offset. Built from an
    # aware object because datetime.utcnow() is deprecated on 3.12, then stripped
    # back to naive so it compares correctly against Galaxy's own rows.
    stamp = (datetime.now(timezone.utc) - timedelta(days=args.age_days)).replace(tzinfo=None)
    print(f"  would set last_password_change -> {stamp.isoformat(timespec='seconds')}"
          + (f"  ({args.age_days} days back)" if args.age_days else "  (now)"))
    print(f"  active sessions to invalidate  -> {0 if args.keep_sessions else n_sessions}"
          + ("  (--keep-sessions)" if args.keep_sessions else ""))

    if args.dry_run:
        print("\n[DRY RUN] nothing was prompted for and nothing was changed")
        return 0

    password = read_password()

    try:
        db.execute(
            "UPDATE galaxy_user SET password = ?, last_password_change = ? WHERE id = ?",
            (hash_password_pbkdf2(password), stamp, user["id"]),
        )
        invalidated = 0
        if not args.keep_sessions and n_sessions:
            db.execute(
                "UPDATE galaxy_session SET is_valid = ? WHERE user_id = ? AND is_valid = ?",
                (False, user["id"], True),
            )
            invalidated = n_sessions
        db.commit()
    except Exception as exc:
        db.rollback()
        sys.exit(f"Error: {exc} (rolled back; no changes made)")

    # Read the stored hash back and verify it accepts the password that was typed.
    # Writing a hash is not evidence that Galaxy will accept it -- this is.
    check = db.query("SELECT password, last_password_change FROM galaxy_user WHERE id = ?",
                     (user["id"],))[0]
    ok = verify(password, check["password"])
    del password

    print(f"\nPassword updated for {user['email']}.")
    print(f"  last_password_change : {check['last_password_change']}")
    print(f"  sessions invalidated : {invalidated}")
    print(f"  hash verifies        : {'yes' if ok else 'NO -- investigate before relying on this'}")
    if not ok:
        return 1
    if args.age_days:
        print(f"\nBackdated {args.age_days} days. This only forces a change if galaxy.yml sets\n"
              f"password_expiration_period to fewer than {args.age_days} days, which is an\n"
              f"instance-wide setting and needs a Galaxy restart.")
    return 0


def verify(cleartext: str, stored: str) -> bool:
    """Re-implements galaxy.security.passwords.check_password for the PBKDF2 form."""
    import hashlib
    from base64 import b64encode

    if not stored or not stored.startswith("PBKDF2$"):
        return False
    try:
        _, hash_function, cost, salt, encoded = stored.split("$", 4)
        expected = hashlib.pbkdf2_hmac(
            hash_function, cleartext.encode("utf-8"), salt.encode("ascii"), int(cost), 24
        )
    except (ValueError, TypeError):
        return False
    return b64encode(expected).decode("ascii") == encoded


if __name__ == "__main__":
    sys.exit(main())
