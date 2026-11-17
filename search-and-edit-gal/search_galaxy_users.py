#!/usr/bin/env python3
"""
Galaxy User Search Script

Search all users in a Galaxy database using two methods:
1. Galaxy API (requires Galaxy to be running)
2. Direct database access (works when Galaxy is offline)

Direct access works against both SQLite and PostgreSQL via galaxy_db.GalaxyDB;
production Galaxy instances run PostgreSQL, so point this at one with --db-url
or --galaxy-config rather than the SQLite default.

Usage:
    python search_galaxy_users.py --method api
    python search_galaxy_users.py --method db --galaxy-config /srv/galaxy/config/galaxy.yml
    python search_galaxy_users.py --method db --db-url postgresql://galaxy@db/galaxy
    python search_galaxy_users.py --method db --email user@example.com
    python search_galaxy_users.py --method db --username admin
"""

import argparse
import os
import sys
from datetime import datetime

from galaxy_db import (
    add_db_arguments,
    backend_to_provider_name,
    open_db,
)

# Try importing bioblend for API access
try:
    from bioblend.galaxy import GalaxyInstance
    BIOBLEND_AVAILABLE = True
except ImportError:
    BIOBLEND_AVAILABLE = False

# Default paths and settings
DEFAULT_GALAXY_URL = os.environ.get("GALAXY_URL", "http://localhost:8080")
DEFAULT_API_KEY = os.environ.get("GALAXY_API_KEY", "")


def search_users_api(galaxy_url: str, api_key: str, email_filter: str = None,
                     username_filter: str = None, show_deleted: bool = False):
    """
    Search users via Galaxy API.
    Requires admin API key and Galaxy to be running.
    """
    if not BIOBLEND_AVAILABLE:
        print("Error: bioblend is not installed. Install it with: pip install bioblend")
        sys.exit(1)

    if not api_key:
        print("Error: API key required. Set GALAXY_API_KEY environment variable or use --api-key")
        sys.exit(1)

    print(f"Connecting to Galaxy at {galaxy_url}...")
    gi = GalaxyInstance(galaxy_url, key=api_key)

    try:
        # NOTE: Galaxy's f_email/f_name filters (which get_users() can push
        # server-side) compile to a plain SQL LIKE, which is case-sensitive
        # on a typical PostgreSQL-backed Galaxy install. Passing our filters
        # through server-side would silently drop matches that only differ
        # in case (e.g. --email Alice missing alice@example.org), which this
        # script has never done -- its documented behavior is a
        # case-insensitive partial match, same as the --method db path. So
        # filtering stays client-side here; bioblend doesn't expose a
        # case-insensitive filter primitive to push down safely instead.
        users = gi.users.get_users(deleted=show_deleted)

        if email_filter:
            users = [u for u in users if email_filter.lower() in u.get('email', '').lower()]
        if username_filter:
            users = [u for u in users if username_filter.lower() in u.get('username', '').lower()]

        print(f"\nFound {len(users)} user(s):\n")
        print("=" * 90)

        for user in users:
            # Fetch full detail to get all metadata columns
            detail = gi.users.show_user(user['id'])
            external = detail.get('external', False)
            auth_type = "OAuth/OIDC" if external else "Traditional"

            print(f"ID:                    {detail.get('id')}")
            print(f"Email:                 {detail.get('email', 'N/A')}")
            print(f"Username:              {detail.get('username', 'N/A')}")
            print(f"Auth Type:             {auth_type}")
            print(f"External Flag:         {external}")
            print(f"Active:                {detail.get('active', 'N/A')}")
            print(f"Deleted:               {detail.get('deleted', False)}")
            print(f"Purged:                {detail.get('purged', False)}")
            print(f"Last Password Change:  {detail.get('last_password_change', 'N/A')}")
            print(f"Disk Usage:            {detail.get('total_disk_usage', 'N/A')}")
            print(f"Preferred Object Store:{detail.get('preferred_object_store_id', 'N/A')}")
            print("-" * 90)

        oauth_count = sum(1 for u in users if u.get('external', False))
        local_count = len(users) - oauth_count
        print(f"\nSummary: {len(users)} total — {oauth_count} OAuth/OIDC, {local_count} traditional")

        return users

    except Exception as e:
        print(f"Error accessing Galaxy API: {e}")
        print("Make sure Galaxy is running and you have admin privileges.")
        sys.exit(1)


def search_users_db(db, email_filter: str = None,
                    username_filter: str = None, show_deleted: bool = False,
                    show_all_fields: bool = False):
    """
    Search users directly from the Galaxy database (SQLite or PostgreSQL).
    Works even when Galaxy is not running.
    """
    # Build query
    query = """
        SELECT
            id,
            email,
            username,
            create_time,
            update_time,
            active,
            deleted,
            purged,
            disk_usage,
            external
        FROM galaxy_user
        WHERE 1=1
    """
    params = []

    if not show_deleted:
        query += " AND (deleted = ? OR deleted IS NULL)"
        params.append(False)

    # lower(...) on both sides keeps the documented case-insensitive partial
    # match working on PostgreSQL, where bare LIKE is case-sensitive. This is
    # the same behaviour the --method api path applies client-side.
    if email_filter:
        query += " AND lower(email) LIKE lower(?)"
        params.append(f"%{email_filter}%")

    if username_filter:
        query += " AND lower(username) LIKE lower(?)"
        params.append(f"%{username_filter}%")

    query += " ORDER BY create_time DESC"

    users = db.query(query, params)

    print(f"\nFound {len(users)} user(s):\n")
    print("=" * 90)

    for user in users:
        print(f"ID:          {user['id']}")
        print(f"Email:       {user['email']}")
        print(f"Username:    {user['username'] or 'N/A'}")
        print(f"Created:     {user['create_time']}")
        print(f"Active:      {bool(user['active'])}")
        print(f"Deleted:     {bool(user['deleted'])}")

        if show_all_fields:
            print(f"Updated:     {user['update_time']}")
            print(f"Purged:      {bool(user['purged'])}")
            print(f"Disk Usage:  {user['disk_usage'] or 0} bytes")
            print(f"External:    {bool(user['external'])}")

        print("-" * 90)

    # Show summary
    active_count = db.query(
        "SELECT COUNT(*) AS n FROM galaxy_user WHERE deleted = ? OR deleted IS NULL", (False,)
    )[0]["n"]
    deleted_count = db.query(
        "SELECT COUNT(*) AS n FROM galaxy_user WHERE deleted = ?", (True,)
    )[0]["n"]

    print(f"\nSummary: {active_count} active user(s), {deleted_count} deleted user(s)")
    return users


def list_users_auth_type(db, auth_filter: str = None):
    """
    List all users with their authentication type (OAuth/OIDC or traditional)
    and all metadata columns from galaxy_user.

    auth_filter: 'oauth' to show only OAuth users, 'local' for traditional only,
                 None to show all.
    """
    # Collect OIDC-linked user IDs, and the backends each user is linked to.
    oauth_user_ids = set()
    linked_backends = {}

    for row in db.query("SELECT DISTINCT user_id, provider FROM oidc_user_authnz_tokens"):
        if row["user_id"] is None:
            continue
        oauth_user_ids.add(row["user_id"])
        short = backend_to_provider_name(row["provider"])
        label = short or f"{row['provider']} (UNKNOWN BACKEND)"
        linked_backends.setdefault(row["user_id"], set()).add(label)

    # custos_authnz_token was DROPPED by Galaxy 26.0's migration 724237cc4cf0,
    # which first copies its tokens into oidc_user_authnz_tokens (stamped
    # assoc_type='custos_migrated'). So on any instance at 26.0 or later this
    # table is simply gone and the rows above already include its users. This
    # probe exists only for instances still running Galaxy < 26.0.
    if db.table_exists("custos_authnz_token"):
        print("Note: legacy custos_authnz_token table present -- this instance predates Galaxy 26.0.")
        for row in db.query("SELECT DISTINCT user_id FROM custos_authnz_token"):
            if row["user_id"] is not None:
                oauth_user_ids.add(row["user_id"])
                linked_backends.setdefault(row["user_id"], set()).add("custos (legacy)")

    # Fetch all metadata columns
    query = """
        SELECT
            id,
            email,
            username,
            create_time,
            update_time,
            active,
            deleted,
            purged,
            external,
            disk_usage,
            last_password_change,
            preferred_object_store_id
        FROM galaxy_user
        ORDER BY create_time DESC
    """
    users = db.query(query)

    # Apply auth filter
    def is_oauth(user):
        return user["id"] in oauth_user_ids or bool(user["external"])

    if auth_filter == "oauth":
        users = [u for u in users if is_oauth(u)]
    elif auth_filter == "local":
        users = [u for u in users if not is_oauth(u)]

    label = {"oauth": "OAuth/OIDC", "local": "Traditional/Local"}.get(auth_filter, "All")
    print(f"\n{label} Users — {len(users)} found:\n")
    print("=" * 90)

    for user in users:
        auth_type = "OAuth/OIDC" if is_oauth(user) else "Traditional"
        disk = user["disk_usage"] or 0
        backends = ", ".join(sorted(linked_backends.get(user["id"], []))) or "None"
        print(f"ID:                    {user['id']}")
        print(f"Email:                 {user['email']}")
        print(f"Username:              {user['username'] or 'N/A'}")
        print(f"Auth Type:             {auth_type}")
        print(f"Linked Providers:      {backends}")
        print(f"External Flag:         {bool(user['external'])}")
        print(f"Active:                {bool(user['active'])}")
        print(f"Deleted:               {bool(user['deleted'])}")
        print(f"Purged:                {bool(user['purged'])}")
        print(f"Created:               {user['create_time']}")
        print(f"Last Updated:          {user['update_time']}")
        print(f"Last Password Change:  {user['last_password_change'] or 'N/A'}")
        print(f"Disk Usage:            {disk} bytes")
        print(f"Preferred Object Store:{user['preferred_object_store_id'] or 'N/A'}")
        print("-" * 90)

    oauth_count = sum(1 for u in users if is_oauth(u))
    local_count = len(users) - oauth_count
    print(f"\nSummary: {oauth_count} OAuth/OIDC user(s), {local_count} traditional user(s)")


def list_user_roles(db, user_id: int = None):
    """List roles for a specific user or all user-role associations."""
    if user_id:
        results = db.query("""
            SELECT u.email, u.username, r.name as role_name, r.description
            FROM galaxy_user u
            JOIN user_role_association ura ON u.id = ura.user_id
            JOIN role r ON ura.role_id = r.id
            WHERE u.id = ?
        """, (user_id,))
    else:
        results = db.query("""
            SELECT u.email, u.username, r.name as role_name, r.description
            FROM galaxy_user u
            JOIN user_role_association ura ON u.id = ura.user_id
            JOIN role r ON ura.role_id = r.id
            ORDER BY u.email, r.name
        """)

    print(f"\nUser Role Associations ({len(results)} total):\n")
    print("-" * 80)

    for row in results:
        print(f"User: {row['email']} ({row['username'] or 'no username'})")
        print(f"Role: {row['role_name']}")
        print(f"Description: {row['description'] or 'N/A'}")
        print("-" * 80)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Search Galaxy users via API or direct database access",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search all users via API
  python search_galaxy_users.py --method api --api-key YOUR_KEY

  # Search all users directly from database (SQLite default)
  python search_galaxy_users.py --method db

  # Search a production PostgreSQL Galaxy
  python search_galaxy_users.py --method db --galaxy-config /srv/galaxy/config/galaxy.yml
  python search_galaxy_users.py --method db --db-url postgresql://galaxy@db/galaxy

  # Search by email
  python search_galaxy_users.py --method db --email gmail.com

  # Search by username
  python search_galaxy_users.py --method db --username admin

  # Show deleted users
  python search_galaxy_users.py --method db --show-deleted

  # List user roles
  python search_galaxy_users.py --method db --roles
        """
    )

    parser.add_argument(
        "--method", "-m",
        choices=["api", "db"],
        default="db",
        help="Search method: 'api' (Galaxy API) or 'db' (direct database)"
    )
    parser.add_argument(
        "--galaxy-url",
        default=DEFAULT_GALAXY_URL,
        help=f"Galaxy URL (default: {DEFAULT_GALAXY_URL})"
    )
    parser.add_argument(
        "--api-key",
        default=DEFAULT_API_KEY,
        help="Galaxy API key (or set GALAXY_API_KEY env var)"
    )
    add_db_arguments(parser)
    parser.add_argument(
        "--email", "-e",
        help="Filter users by email (partial match)"
    )
    parser.add_argument(
        "--username", "-u",
        help="Filter users by username (partial match)"
    )
    parser.add_argument(
        "--show-deleted",
        action="store_true",
        help="Include deleted users in results"
    )
    parser.add_argument(
        "--all-fields",
        action="store_true",
        help="Show all database fields (db method only)"
    )
    parser.add_argument(
        "--roles",
        action="store_true",
        help="List user roles (db method only)"
    )
    parser.add_argument(
        "--user-id",
        type=int,
        help="Specific user ID for role lookup"
    )
    parser.add_argument(
        "--auth-type",
        choices=["oauth", "local", "all"],
        const="all",
        nargs="?",
        help="List users by auth type: 'oauth', 'local', or 'all' (db method only)"
    )

    args = parser.parse_args()

    print("=" * 90)
    print("Galaxy User Search")
    print("=" * 90)

    # The API path needs no database connection; only open one when we will use it.
    if args.auth_type is None and not args.roles and args.method == "api":
        search_users_api(
            args.galaxy_url,
            args.api_key,
            email_filter=args.email,
            username_filter=args.username,
            show_deleted=args.show_deleted
        )
        return

    db = open_db(args)
    print(f"Database: {db.describe()}")
    try:
        if args.auth_type is not None:
            auth_filter = None if args.auth_type == "all" else args.auth_type
            list_users_auth_type(db, auth_filter)
        elif args.roles:
            list_user_roles(db, args.user_id)
        else:
            search_users_db(
                db,
                email_filter=args.email,
                username_filter=args.username,
                show_deleted=args.show_deleted,
                show_all_fields=args.all_fields
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
