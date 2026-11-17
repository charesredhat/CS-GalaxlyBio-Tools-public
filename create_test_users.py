#!/usr/bin/env python3
"""
Create (or remove) local test user accounts in a Galaxy instance via the API.

Requires an admin API key. Users are named <prefix>01..<prefix>NN with email
<prefix>NN@<domain>. Existing accounts are skipped, so re-running is safe.
Generated passwords are written to a CSV (mode 600, gitignored), never printed.
"""

from bioblend import galaxy
import argparse
import csv
import os
import secrets
import sys

# Galaxy configuration
GALAXY_URL = os.environ.get("GALAXY_URL", "http://127.0.0.1:8080/")
API_KEY = os.environ.get("GALAXY_API_KEY", "")


def test_accounts(prefix, domain, count):
    for i in range(1, count + 1):
        username = f"{prefix}{i:02d}"
        yield username, f"{username}@{domain}"


def existing_users(gi, deleted=False):
    return {u["email"].lower(): u for u in gi.users.get_users(deleted=deleted)}


def open_credentials(path):
    """Append-only handle on the password CSV, created 600 with a header."""
    new_file = not os.path.exists(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    fh = os.fdopen(fd, "a", newline="")
    writer = csv.writer(fh)
    if new_file:
        writer.writerow(["username", "email", "password", "galaxy_id"])
    return fh, writer


def create_users(gi, args):
    current = existing_users(gi)
    created = []
    fh = writer = None
    try:
        for username, email in test_accounts(args.prefix, args.domain, args.count):
            if email.lower() in current:
                print(f"  = {email} already exists, skipped")
                continue
            password = secrets.token_urlsafe(12)
            try:
                user = gi.users.create_local_user(username, email, password)
            except Exception as e:
                print(f"  ✗ {email}: {e}")
                continue
            # Recorded per account, not in one batch at the end: an interrupt
            # after this point would otherwise leave the account live in Galaxy
            # with a password nobody has, and a re-run skips it as existing.
            if writer is None:
                fh, writer = open_credentials(args.credentials)
            writer.writerow((username, email, password, user["id"]))
            fh.flush()
            created.append((username, email, password, user["id"]))
            print(f"  + {email} (ID: {user['id']})")
    finally:
        if fh is not None:
            fh.close()

    if created:
        print(f"\nPasswords for {len(created)} new account(s) appended to {args.credentials}")
    return created


def delete_users(gi, args):
    current = existing_users(gi)
    removed = 0
    for _, email in test_accounts(args.prefix, args.domain, args.count):
        user = current.get(email.lower())
        if not user:
            continue
        gi.users.delete_user(user["id"], purge=args.purge)
        removed += 1
        print(f"  - {email} {'purged' if args.purge else 'deleted'}")
    print(f"\n{removed} account(s) removed")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", "-n", type=int, default=10, help="number of accounts (default: 10)")
    parser.add_argument("--prefix", default="testuser", help="username prefix (default: testuser)")
    parser.add_argument("--domain", default="example.org", help="email domain (default: example.org)")
    parser.add_argument("--credentials", default="test_users_credentials.csv",
                        help="CSV the generated passwords are written to")
    parser.add_argument("--delete", action="store_true", help="delete the test accounts instead")
    parser.add_argument("--purge", action="store_true",
                        help="with --delete, also purge (needs allow_user_deletion: true)")
    args = parser.parse_args()

    if args.purge and not args.delete:
        parser.error("--purge only applies with --delete")

    if not API_KEY:
        sys.exit("GALAXY_API_KEY is not set (load it with: set -a; . ./.env; set +a)")

    gi = galaxy.GalaxyInstance(url=GALAXY_URL, key=API_KEY)
    if not gi.users.get_current_user().get("is_admin"):
        sys.exit("This API key is not an admin's; add your email to admin_users in galaxy.yml")

    print(f"Galaxy: {GALAXY_URL}")
    if args.delete:
        delete_users(gi, args)
    else:
        create_users(gi, args)


if __name__ == "__main__":
    main()
