#!/usr/bin/env python3
"""
Galaxy User OAuth/OIDC Conversion and Audit Tool

Pre-links existing Galaxy users to an OIDC provider by inserting rows into
`oidc_user_authnz_tokens`, and audits the rows that are already there.

READ THIS FIRST: pre-linking is the fallback, not the default
-------------------------------------------------------------
Galaxy 26.1 associates existing accounts with an IdP identity on its own, via
the `associate_by_email_if_logged_in` pipeline step (psa_authnz.py:985):

  * fixed_delegated_auth ON  -> first OIDC login auto-associates the identity
    with the existing account, matched case-insensitively on email.
  * fixed_delegated_auth OFF -> the user is redirected to
    `login/start?connect_external_provider=...` to log in once and link.

`fixed_delegated_auth` is NOT a galaxy.yml setting -- it is computed at startup
(lib/galaxy/app/__init__.py:973):

    fixed_delegated_auth = (len(config.oidc) == 1 and len(auth_manager.authenticators) == 0)

so you turn it on by configuring exactly ONE OIDC provider and pointing
`auth_config_file` at an auth_conf.xml holding zero authenticators (`<auth></auth>`).
That same empty file is what actually retires password login instance-wide:
with no authenticators, AuthManager.check_password() iterates nothing and always
returns False, which closes /api/authenticate/baseauth too.

Both paths write the association through Galaxy itself, with the correct backend
name and the IdP's *real* subject identifier. That is strictly better than
pre-linking, which has to guess the uid ahead of time -- and since no stock
backend keys identity on email any more, that guess cannot be made correctly
for any provider. Prefer the config route; see OAUTH_CONVERSION_GUIDE.md.

Use this tool when you need to audit what is in the table (`--verify`), or when
the pipeline route genuinely cannot cover a case.

How pre-linking works
---------------------
1. Insert a row in `oidc_user_authnz_tokens` binding `user_id` to (provider, uid).
2. `provider` MUST be the python-social-auth backend name, not the short name
   from oidc_backends_config.xml -- 'google-openidconnect', not 'google'. Galaxy
   looks the row up with an exact match on (provider, uid)
   (model/__init__.py:11714), and writes it that way itself (psa_authnz.py:551).
   This tool takes short names and translates them; see galaxy_db.BACKENDS_NAME.
3. `uid` is the identifier the IdP will send, and on a modern social-core NO
   backend keys that on email -- Google included. Galaxy loads Google as
   `google-openidconnect`, whose ID_KEY resolves to `sub` (verified against
   social-core 4.8.3; see galaxy_db.EMAIL_UID_PROVIDERS for the measurement).
   Others use `sub` or `preferred_username` likewise. So every email-keyed
   pre-link now needs --confirm-non-email-uid AND a backend you have explicitly
   reconfigured to key on email -- otherwise the row can never match.
4. Rows created here are stamped assoc_type='prelinked' so they can be audited
   and rolled back (--verify, --remove-all-prelinked).

Retiring passwords
------------------
`--mark-external` sets galaxy_user.external, which blocks the *web login form*
only (controllers/user.py:176). It does NOT stop `/api/authenticate/baseauth`
from trading the old password for an API key -- nothing in lib/galaxy/auth/
consults `external`. To actually retire a credential use
`--invalidate-password`, and use `--rotate-api-keys` to revoke keys that would
otherwise outlive the migration.

Usage:
    # Audit what is already in the table (read-only; start here)
    python convert_users_to_oauth.py --verify --galaxy-config /srv/galaxy/config/galaxy.yml

    # List users without OIDC links
    python convert_users_to_oauth.py --list-unlinked --db-url postgresql://galaxy@db/galaxy

    # Preview conversion (dry run)
    python convert_users_to_oauth.py --preview --provider google

    # Convert all unlinked users to Google
    python convert_users_to_oauth.py --convert-all --provider google

    # Convert one user and genuinely retire their password
    python convert_users_to_oauth.py --convert --email user@example.com --provider google \
        --mark-external --invalidate-password --rotate-api-keys

    # Roll back every pre-link this tool created
    python convert_users_to_oauth.py --remove-all-prelinked --provider google
"""

import argparse
import json
import sys

from galaxy_db import (
    EMAIL_UID_PROVIDERS,
    LEGACY_BACKEND_NAMES,
    PRELINK_ASSOC_TYPE,
    RETIRED_PROVIDERS,
    SUPPORTED_PROVIDERS,
    add_db_arguments,
    backend_to_provider_name,
    known_backend_names,
    open_db,
    provider_name_to_backend,
    unusable_password_hash,
)

TOKEN_TABLE = "oidc_user_authnz_tokens"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def resolve_provider(provider: str) -> str:
    """Translate a short provider name to the backend name Galaxy stores, or exit."""
    key = (provider or "").strip().lower()
    if key in RETIRED_PROVIDERS:
        print(f"Error: provider '{key}' cannot be pre-linked.\n  {RETIRED_PROVIDERS[key]}")
        sys.exit(1)
    backend = provider_name_to_backend(provider)
    if backend is None:
        print(f"Error: unknown provider '{provider}'. Known: {', '.join(sorted(SUPPORTED_PROVIDERS))}")
        sys.exit(1)
    return backend


def normalize_uid(email: str, provider: str) -> str:
    """
    The uid to store for an email-keyed pre-link.

    Galaxy defaults FORCE_EMAIL_LOWERCASE to True (psa_authnz.py:186) and the
    lookup at login is case-sensitive (get_social_auth uses an exact match), so
    the uid has to be stored lowercased or it will simply never match.

    This keys off the shape of the value rather than off EMAIL_UID_PROVIDERS,
    which is now empty: when an operator pre-links an email with
    --confirm-non-email-uid, the lowercase invariant still has to hold, and
    gating it on the provider set would silently have stopped applying.
    """
    uid = email.strip()
    return uid.lower() if "@" in uid else uid


def confirm_email_uid_strategy(provider: str, confirm_non_email_uid: bool, dry_run: bool = False) -> None:
    """
    Verify it is safe to pre-link using email as the uid for this provider.

    A dry run warns and continues so `--preview` stays useful; a real conversion
    exits non-zero so callers checking the exit code see the refusal.
    """
    if provider in EMAIL_UID_PROVIDERS or confirm_non_email_uid:
        return

    message = (
        f"provider '{provider}' is not known to key OIDC identity on email.\n"
        "  Pre-linking with uid=email will never match at login, because this backend\n"
        "  identifies users by its own subject identifier ('sub' for Google, generic\n"
        "  OIDC, Okta, Keycloak, CILogon and Auth0; 'preferred_username' for Azure V2),\n"
        "  not email. This includes Google: Galaxy loads it as 'google-openidconnect',\n"
        "  whose ID_KEY resolves to 'sub' on social-core 4.x -- the email-keyed\n"
        "  BaseGoogleAuth behaviour the older docs described is no longer reachable.\n"
        "  Prefer fixed_delegated_auth, which lets Galaxy record the real subject itself.\n"
        "  If you have explicitly configured this backend to use email as the UID, re-run\n"
        "  with --confirm-non-email-uid."
    )

    if dry_run:
        print(f"Warning: {message}")
        print("  Continuing preview since this is a dry run; the real conversion will refuse")
        print("  to run without --confirm-non-email-uid.")
        return

    print(f"Error: {message}")
    sys.exit(1)


def print_credential_warning(mark_external: bool, invalidate_password: bool) -> None:
    """Spell out the gap between `external` and an actually-retired password."""
    if mark_external and not invalidate_password:
        print(
            "\nNote: --mark-external blocks the web login form only. The user's existing password\n"
            "      still works against /api/authenticate/baseauth, which never checks the external\n"
            "      flag. Add --invalidate-password to genuinely retire the credential."
        )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def _provider_map(db):
    """user_id -> sorted list of linked backend names (avoids GROUP_CONCAT/string_agg)."""
    links = db.query(f"SELECT user_id, provider FROM {TOKEN_TABLE} WHERE user_id IS NOT NULL")
    mapping = {}
    for link in links:
        mapping.setdefault(link["user_id"], []).append(link["provider"] or "?")
    for providers in mapping.values():
        providers.sort()
    return mapping


def list_all_users(db):
    """List all users with their OIDC link status."""
    users = db.query(
        """
        SELECT id, email, username, create_time, active, external, deleted
        FROM galaxy_user
        WHERE deleted = ? OR deleted IS NULL
        ORDER BY create_time DESC
        """,
        (False,),
    )
    providers = _provider_map(db)

    print("\n" + "=" * 110)
    print("All Galaxy Users")
    print("=" * 110)
    print(f"{'ID':<6} {'Email':<38} {'Username':<18} {'Linked backends':<32} {'External'}")
    print("-" * 110)
    for user in users:
        linked = ", ".join(providers.get(user["id"], [])) or "None"
        print(
            f"{user['id']:<6} {user['email']:<38} {(user['username'] or 'N/A'):<18} "
            f"{linked:<32} {'Yes' if user['external'] else 'No'}"
        )
    print(f"\nTotal: {len(users)} user(s)")
    return users


def list_unlinked_users(db):
    """List users with no OIDC link at all (traditional login only)."""
    users = db.query(
        f"""
        SELECT u.id, u.email, u.username, u.create_time
        FROM galaxy_user u
        LEFT JOIN {TOKEN_TABLE} t ON u.id = t.user_id
        WHERE (u.deleted = ? OR u.deleted IS NULL) AND t.id IS NULL
        ORDER BY u.create_time DESC
        """,
        (False,),
    )

    print("\n" + "=" * 95)
    print("Users WITHOUT OIDC Links (Traditional Login Only)")
    print("=" * 95)
    print(f"{'ID':<6} {'Email':<45} {'Username':<20} {'Created'}")
    print("-" * 95)
    for user in users:
        created = str(user["create_time"])[:19] if user["create_time"] else "N/A"
        print(f"{user['id']:<6} {user['email']:<45} {(user['username'] or 'N/A'):<20} {created}")
    print(f"\nTotal: {len(users)} user(s) without OIDC links")
    return users


def list_linked_users(db):
    """List users with OIDC links, showing the stored backend name."""
    links = db.query(
        f"""
        SELECT u.id, u.email, t.provider, t.uid, t.assoc_type
        FROM galaxy_user u
        JOIN {TOKEN_TABLE} t ON u.id = t.user_id
        WHERE u.deleted = ? OR u.deleted IS NULL
        ORDER BY u.email, t.provider
        """,
        (False,),
    )

    print("\n" + "=" * 118)
    print("Users WITH OIDC Links")
    print("=" * 118)
    print(f"{'ID':<6} {'Email':<34} {'Stored backend':<28} {'Short name':<12} {'assoc_type':<12} {'UID'}")
    print("-" * 118)
    for link in links:
        short = backend_to_provider_name(link["provider"]) or "UNKNOWN"
        print(
            f"{link['id']:<6} {link['email']:<34} {(link['provider'] or '?'):<28} "
            f"{short:<12} {(link['assoc_type'] or '-'):<12} {link['uid'] or 'N/A'}"
        )
    print(f"\nTotal: {len(links)} OIDC link(s)")
    return links


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_links(db):
    """
    Read-only audit of every row in oidc_user_authnz_tokens.

    Checks, in order of how badly each one breaks login:

      1. provider is a real PSA backend name. A short name like 'google' here is
         a dead row -- Galaxy will never match it. This is what earlier versions
         of this tool wrote.
      2. uid case. An email-shaped uid must be lowercase or it never matches,
         because Galaxy lowercases the incoming email but compares uid exactly.
      3. duplicate (provider, uid) -- violates the table's unique constraint and
         means one of the rows is unreachable.
      4. the row points at a deleted/purged or missing user.
    """
    rows = db.query(f"SELECT id, user_id, uid, provider, assoc_type FROM {TOKEN_TABLE} ORDER BY id")
    users = {
        u["id"]: u
        for u in db.query("SELECT id, email, deleted, purged, external FROM galaxy_user")
    }
    valid_backends = known_backend_names()

    print("\n" + "=" * 100)
    print("OIDC Link Verification")
    print("=" * 100)
    print(f"Rows in {TOKEN_TABLE}: {len(rows)}")

    bad_provider, bad_uid_case, orphaned, deleted_user, duplicates = [], [], [], [], []
    legacy_provider = []

    seen = {}
    for row in rows:
        key = (row["provider"], row["uid"])
        if key in seen:
            duplicates.append((seen[key], row["id"], key))
        else:
            seen[key] = row["id"]

        provider = row["provider"]
        if provider in valid_backends:
            uid = row["uid"] or ""
            # Any email-shaped uid must be lowercase to match, whoever wrote it
            # and whatever the provider -- see normalize_uid. A `sub`-shaped uid
            # is opaque and must be left exactly as the IdP sent it.
            if "@" in uid and uid != uid.lower():
                bad_uid_case.append(row)
        elif provider in LEGACY_BACKEND_NAMES:
            # A backend name from an older Galaxy. These are working rows on an
            # upgraded instance -- reporting them as BROKEN would invite an
            # operator to "repair" logins that are fine.
            legacy_provider.append(row)
        else:
            bad_provider.append(row)

        user = users.get(row["user_id"])
        if user is None:
            orphaned.append(row)
        elif user.get("deleted") or user.get("purged"):
            deleted_user.append(row)

    def report(title, items, formatter):
        if not items:
            return
        print(f"\n{title} ({len(items)})")
        print("-" * 100)
        for item in items:
            print(f"  {formatter(item)}")

    report(
        "BROKEN: provider is not a python-social-auth backend name -- these rows can never match",
        bad_provider,
        lambda r: (
            f"row id={r['id']} user_id={r['user_id']} provider={r['provider']!r} uid={r['uid']!r}"
            + (
                f"  -> should be {provider_name_to_backend(r['provider'])!r}"
                if provider_name_to_backend(r["provider"])
                else "  -> unrecognised; check oidc_backends_config.xml"
            )
        ),
    )
    report(
        "BROKEN: email-shaped uid is not lowercase -- will not match at login",
        bad_uid_case,
        lambda r: f"row id={r['id']} user_id={r['user_id']} uid={r['uid']!r} -> should be {(r['uid'] or '').lower()!r}",
    )
    report(
        "BROKEN: duplicate (provider, uid) -- one of these rows is unreachable",
        duplicates,
        lambda d: f"rows id={d[0]} and id={d[1]} share provider/uid {d[2]!r}",
    )
    report(
        "WARNING: row references a user that does not exist",
        orphaned,
        lambda r: f"row id={r['id']} user_id={r['user_id']}",
    )
    report(
        "WARNING: row belongs to a deleted or purged user",
        deleted_user,
        lambda r: f"row id={r['id']} user_id={r['user_id']} email={users[r['user_id']]['email']}",
    )
    report(
        "INFO: backend name from an older Galaxy release -- valid, do NOT 'repair'",
        legacy_provider,
        lambda r: f"row id={r['id']} user_id={r['user_id']} provider={r['provider']!r}",
    )

    prelinked = [r for r in rows if r["assoc_type"] == PRELINK_ASSOC_TYPE]
    migrated = [r for r in rows if r["assoc_type"] == "custos_migrated"]
    print("\n" + "-" * 100)
    print(f"Rows created by this tool (assoc_type={PRELINK_ASSOC_TYPE!r}): {len(prelinked)}")
    print(f"Rows migrated from Custos by Galaxy (assoc_type='custos_migrated'): {len(migrated)}")

    problems = len(bad_provider) + len(bad_uid_case) + len(duplicates)
    print("=" * 100)
    if problems:
        print(f"RESULT: {problems} row(s) will not work at login. Repair them before cutover.")
    else:
        print("RESULT: no broken rows found.")
    print("=" * 100)
    return problems


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def find_user_by_email(db, email: str):
    """
    Look a user up by email, case-insensitively.

    Galaxy compares user email case-insensitively (26.1.0, #21885 --
    model/__init__.py:11710 uses func.lower), so a case-sensitive lookup here
    would miss users Galaxy itself would match. `lower()` works on both SQLite
    and PostgreSQL; `COLLATE NOCASE` is SQLite-only and a syntax error on
    PostgreSQL.
    """
    rows = db.query(
        "SELECT id, email, username, external FROM galaxy_user WHERE lower(email) = lower(?)",
        (email,),
    )
    return rows[0] if rows else None


def link_exists(db, user_id: int, backend: str) -> bool:
    return bool(
        db.query(f"SELECT id FROM {TOKEN_TABLE} WHERE user_id = ? AND provider = ?", (user_id, backend))
    )


def uid_taken(db, backend: str, uid: str):
    """
    Return the user_id already holding this (provider, uid), if any.

    Compared case-insensitively: Galaxy matches exactly, so a differing-case row
    would not collide at login, but it WOULD collide with the table's
    UNIQUE(provider, uid) constraint on some collations. Over-detecting is the
    safe direction for a conflict check.
    """
    rows = db.query(
        f"SELECT user_id FROM {TOKEN_TABLE} WHERE provider = ? AND lower(uid) = lower(?)",
        (backend, uid),
    )
    return rows[0]["user_id"] if rows else None


def other_links_exist(db, user_id: int, exclude_backend: str) -> bool:
    return bool(
        db.query(
            f"SELECT id FROM {TOKEN_TABLE} WHERE user_id = ? AND provider != ?", (user_id, exclude_backend)
        )
    )


# Outcomes of an attempted pre-link. The distinction matters: "already linked"
# means the user IS federated and retiring their password is safe, whereas
# "conflict" means they are NOT, and retiring it would lock them out for good.
LINK_CREATED = "created"
LINK_EXISTS = "exists"
LINK_CONFLICT = "conflict"


def create_oauth_link(db, user_id: int, uid: str, backend: str, extra_data: dict = None) -> str:
    """
    Insert one pre-link row.

    Returns LINK_CREATED, LINK_EXISTS (the user already has a usable link for
    this backend) or LINK_CONFLICT (the uid belongs to somebody else, so this
    user has no link and must not have their credentials retired).

    extra_data holds the real tokens once the user logs in; it starts empty.
    assoc_type marks the row as ours so it can be audited and rolled back.
    """
    if link_exists(db, user_id, backend):
        return LINK_EXISTS

    holder = uid_taken(db, backend, uid)
    if holder is not None and holder != user_id:
        print(f"    Warning: uid {uid!r} is already linked to user_id {holder} for {backend}")
        return LINK_CONFLICT

    db.execute(
        f"""
        INSERT INTO {TOKEN_TABLE} (user_id, uid, provider, extra_data, lifetime, assoc_type)
        VALUES (?, ?, ?, ?, NULL, ?)
        """,
        (user_id, uid, backend, json.dumps(extra_data or {}), PRELINK_ASSOC_TYPE),
    )
    return LINK_CREATED


def retire_credentials(db, user_id: int, mark_external: bool, invalidate_password: bool, rotate_api_keys: bool):
    """Apply the requested credential changes to one user; returns a list of what was done."""
    done = []
    if mark_external:
        db.execute("UPDATE galaxy_user SET external = ? WHERE id = ?", (True, user_id))
        done.append("marked external (web login form blocked)")
    if invalidate_password:
        db.execute(
            "UPDATE galaxy_user SET password = ? WHERE id = ?",
            (unusable_password_hash(), user_id),
        )
        done.append("password invalidated (irreversible)")
    if rotate_api_keys:
        if db.table_exists("api_keys") and db.column_exists("api_keys", "deleted"):
            count = db.execute(
                "UPDATE api_keys SET deleted = ? WHERE user_id = ? AND deleted = ?", (True, user_id, False)
            )
            done.append(f"revoked {max(count, 0)} API key(s)")
        else:
            print("    Warning: api_keys table has no 'deleted' column; skipping key rotation")
    return done


def convert_user_to_oauth(db, email: str, provider: str, mark_external=False, invalidate_password=False,
                          rotate_api_keys=False, dry_run=False, confirm_non_email_uid=False):
    """Pre-link a single user."""
    backend = resolve_provider(provider)
    confirm_email_uid_strategy(provider, confirm_non_email_uid, dry_run=dry_run)

    user = find_user_by_email(db, email)
    if not user:
        print(f"Error: User with email '{email}' not found")
        return False

    uid = normalize_uid(user["email"], provider)
    print(f"\nConverting user: {user['email']} (ID: {user['id']})")
    print(f"  provider (config name): {provider}")
    print(f"  provider (stored)     : {backend}")
    print(f"  uid                   : {uid}")

    if dry_run:
        print("  [DRY RUN] Would create the OIDC link above")
        for flag, label in (
            (mark_external, "set external=True"),
            (invalidate_password, "invalidate the password"),
            (rotate_api_keys, "revoke existing API keys"),
        ):
            if flag:
                print(f"  [DRY RUN] Would {label}")
        return True

    try:
        outcome = create_oauth_link(db, user["id"], uid, backend)
        if outcome == LINK_CREATED:
            print(f"  Created OIDC link ({backend})")
        elif outcome == LINK_EXISTS:
            print(f"  OIDC link already exists ({backend})")
        else:
            # The uid belongs to another account, so this user has NO link.
            # Retiring their credentials here would lock them out permanently
            # with no way back in -- refuse the whole operation instead.
            db.rollback()
            print(f"  REFUSED: no OIDC link for this user ({backend}); credentials left untouched.")
            print("           Resolve the uid conflict above, then re-run.")
            return False

        for action in retire_credentials(db, user["id"], mark_external, invalidate_password, rotate_api_keys):
            print(f"  {action}")
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"  Error: {e} (rolled back; no changes made for this user)")
        return False

    print("  Conversion complete.")
    print_credential_warning(mark_external, invalidate_password)
    return True


def convert_all_users_to_oauth(db, provider: str, mark_external=False, invalidate_password=False,
                               rotate_api_keys=False, dry_run=False, exclude_emails=None,
                               confirm_non_email_uid=False):
    """Pre-link every user that has no link for this provider."""
    backend = resolve_provider(provider)
    confirm_email_uid_strategy(provider, confirm_non_email_uid, dry_run=dry_run)

    # Every live user, linked or not. Filtering out already-linked users (as this
    # used to) meant a follow-up run with --invalidate-password/--rotate-api-keys
    # silently retired nothing for exactly the users who were safest to retire,
    # and behaved differently from the single-user path.
    users = db.query(
        f"""
        SELECT u.id, u.email, u.username,
               CASE WHEN t.id IS NULL THEN 0 ELSE 1 END AS already_linked
        FROM galaxy_user u
        LEFT JOIN {TOKEN_TABLE} t ON u.id = t.user_id AND t.provider = ?
        WHERE u.deleted = ? OR u.deleted IS NULL
        ORDER BY u.email
        """,
        (backend, False),
    )
    retiring = mark_external or invalidate_password or rotate_api_keys
    unlinked_count = sum(1 for u in users if not u["already_linked"])

    exclude = {e.lower() for e in (exclude_emails or [])}

    print(f"\n{'=' * 80}")
    print(f"Converting Users to OIDC Provider: {provider}  (stored as: {backend})")
    print(f"{'=' * 80}")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE'}")
    print(f"Mark external: {mark_external}   Invalidate password: {invalidate_password}   "
          f"Rotate API keys: {rotate_api_keys}")
    print(f"Found {len(users)} live user(s); {unlinked_count} without a {backend} link")
    print("-" * 80)

    converted = already = skipped = failed = refused = 0

    for user in users:
        if user["email"].lower() in exclude:
            print(f"  SKIP: {user['email']} (in exclude list)")
            skipped += 1
            continue

        # Nothing to do for a user who is already linked when we are not also
        # retiring credentials; stay quiet rather than listing every account.
        if user["already_linked"] and not retiring:
            already += 1
            continue

        uid = normalize_uid(user["email"], provider)
        print(f"\n  User: {user['email']} (ID: {user['id']}) uid={uid}")

        if dry_run:
            if user["already_linked"]:
                print("    [DRY RUN] Already linked; would retire credentials only")
            else:
                print("    [DRY RUN] Would create OIDC link")
            converted += 1
            continue

        # Commit per user rather than once at the end: a failure partway through
        # a large batch used to discard every successful conversion before it.
        try:
            outcome = create_oauth_link(db, user["id"], uid, backend)
            if outcome == LINK_CONFLICT:
                # No link for this user -- retiring credentials would lock them
                # out permanently. See the single-user path for the rationale.
                db.rollback()
                print("    REFUSED: uid conflict; credentials left untouched")
                refused += 1
                continue

            if outcome == LINK_CREATED:
                print("    Created OIDC link")
                converted += 1
            else:
                print("    OIDC link already exists")
                already += 1

            for action in retire_credentials(
                db, user["id"], mark_external, invalidate_password, rotate_api_keys
            ):
                print(f"    {action}")
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"    FAILED: {e} (rolled back this user; continuing)")
            failed += 1

    print(f"\n{'=' * 80}")
    print(f"Summary: {converted} converted, {already} already linked, "
          f"{skipped} skipped, {refused} refused, {failed} failed")
    if refused:
        print(f"WARNING: {refused} user(s) refused on a uid conflict and were left entirely alone.")
    if dry_run:
        print("(Dry run - no changes made)")
    print("=" * 80)
    print_credential_warning(mark_external, invalidate_password)
    return converted


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


def remove_oauth_link(db, email: str, provider: str, dry_run=False):
    """Remove one user's link for a provider, re-enabling password login if it was their last."""
    backend = resolve_provider(provider)
    user = find_user_by_email(db, email)
    if not user:
        print(f"Error: User with email '{email}' not found")
        return False

    if not link_exists(db, user["id"], backend):
        print(f"No OIDC link found for {email} with provider {provider} ({backend})")
        return False

    print(f"\nRemoving OIDC link for: {email}")
    print(f"  Provider: {provider} (stored as {backend})")

    if dry_run:
        print("  [DRY RUN] Would remove the OIDC link")
        return True

    try:
        db.execute(f"DELETE FROM {TOKEN_TABLE} WHERE user_id = ? AND provider = ?", (user["id"], backend))

        # Only clear `external` if no other link remains, so this doesn't quietly
        # defeat OIDC-only enforcement for a user still linked elsewhere.
        if other_links_exist(db, user["id"], backend):
            print("  Link removed. User still has other OIDC link(s); external flag left set.")
        else:
            db.execute("UPDATE galaxy_user SET external = ? WHERE id = ?", (False, user["id"]))
            print("  Link removed, external flag cleared (web login form re-enabled).")
            print("  Note: if the password was invalidated, clearing `external` does not restore it --")
            print("        the user must go through password reset.")
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"  Error: {e} (rolled back)")
        return False
    return True


def remove_all_prelinked(db, provider=None, dry_run=False):
    """Roll back every row this tool created (assoc_type='prelinked')."""
    if provider:
        backend = resolve_provider(provider)
        rows = db.query(
            f"SELECT id, user_id, uid, provider FROM {TOKEN_TABLE} WHERE assoc_type = ? AND provider = ?",
            (PRELINK_ASSOC_TYPE, backend),
        )
        scope = f"provider {provider} ({backend})"
    else:
        rows = db.query(
            f"SELECT id, user_id, uid, provider FROM {TOKEN_TABLE} WHERE assoc_type = ?",
            (PRELINK_ASSOC_TYPE,),
        )
        scope = "all providers"

    print(f"\nPre-linked rows created by this tool ({scope}): {len(rows)}")
    for row in rows:
        print(f"  row id={row['id']} user_id={row['user_id']} provider={row['provider']} uid={row['uid']}")

    if not rows:
        return 0
    if dry_run:
        print("[DRY RUN] Would delete the rows above. Rows Galaxy created itself are untouched.")
        return len(rows)

    # Delete by the same predicate used to select, rather than by an IN list of
    # every id: one bind parameter per row hits SQLite's variable cap (999 or
    # 32766, build-dependent) and psycopg2's 65535 on a large instance, which
    # would leave no working rollback exactly when one is most needed.
    try:
        if provider:
            deleted = db.execute(
                f"DELETE FROM {TOKEN_TABLE} WHERE assoc_type = ? AND provider = ?",
                (PRELINK_ASSOC_TYPE, backend),
            )
        else:
            deleted = db.execute(
                f"DELETE FROM {TOKEN_TABLE} WHERE assoc_type = ?", (PRELINK_ASSOC_TYPE,)
            )
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error: {e} (rolled back; nothing deleted)")
        return 0

    print(f"Deleted {deleted} pre-linked row(s). Rows Galaxy created itself were untouched.")
    print("Note: this does not restore invalidated passwords or revoked API keys.")
    return deleted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Audit and convert Galaxy users between password and OIDC authentication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Audit existing links (read-only; run this first)
  python convert_users_to_oauth.py --verify --galaxy-config /srv/galaxy/config/galaxy.yml

  # List users and their link status
  python convert_users_to_oauth.py --list
  python convert_users_to_oauth.py --list-unlinked
  python convert_users_to_oauth.py --list-linked

  # Preview, then convert
  python convert_users_to_oauth.py --preview --provider google
  python convert_users_to_oauth.py --convert-all --provider google

  # Convert one user and actually retire their password
  python convert_users_to_oauth.py --convert --email user@example.com --provider google \\
      --mark-external --invalidate-password --rotate-api-keys

  # Roll back this tool's own pre-links
  python convert_users_to_oauth.py --remove-all-prelinked --provider google --preview-only

Prefer Galaxy's own association pipeline (fixed_delegated_auth) over bulk
pre-linking where you can -- see OAUTH_CONVERSION_GUIDE.md.
        """,
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="List all users with link status")
    action.add_argument("--list-unlinked", action="store_true", help="List users without OIDC links")
    action.add_argument("--list-linked", action="store_true", help="List users with OIDC links")
    action.add_argument("--verify", action="store_true",
                        help="Audit every row in oidc_user_authnz_tokens for rows that cannot match at login")
    action.add_argument("--preview", action="store_true", help="Preview bulk conversion (dry run)")
    action.add_argument("--convert", action="store_true", help="Convert a specific user")
    action.add_argument("--convert-all", action="store_true", help="Convert all unlinked users")
    action.add_argument("--remove", action="store_true", help="Remove a user's OIDC link")
    action.add_argument("--remove-all-prelinked", action="store_true",
                        help="Delete every row this tool created (assoc_type='prelinked')")

    add_db_arguments(parser)

    # Retired names are accepted here only so resolve_provider() can explain WHY
    # they cannot be used; argparse's bare "invalid choice" gives the operator
    # nothing to act on.
    parser.add_argument("--provider", "-p", choices=SUPPORTED_PROVIDERS + sorted(RETIRED_PROVIDERS),
                        metavar="{" + ",".join(SUPPORTED_PROVIDERS) + "}",
                        help="Provider name as it appears in oidc_backends_config.xml (translated "
                             "to the backend name Galaxy stores)")
    parser.add_argument("--email", "-e", help="User email for single-user operations")
    parser.add_argument("--exclude", nargs="+", metavar="EMAIL", help="Emails to exclude from bulk conversion")
    parser.add_argument("--preview-only", action="store_true",
                        help="With --remove-all-prelinked: show what would be deleted without deleting")

    creds = parser.add_argument_group("credential retirement")
    creds.add_argument("--mark-external", action="store_true",
                       help="Set galaxy_user.external -- blocks the WEB LOGIN FORM ONLY. The old password "
                            "still works against /api/authenticate/baseauth.")
    creds.add_argument("--invalidate-password", action="store_true",
                       help="Overwrite the password hash with an unusable value. IRREVERSIBLE; this is the "
                            "only way to actually retire the credential.")
    creds.add_argument("--rotate-api-keys", action="store_true",
                       help="Revoke the user's existing API keys, which otherwise outlive the migration.")
    creds.add_argument("--disable-password", action="store_true",
                       help="DEPRECATED alias for --mark-external (kept for existing scripts).")

    parser.add_argument("--confirm-non-email-uid", action="store_true",
                        help="Required for EVERY provider, Google included -- no stock social-core backend "
                             "keys identity on email any more. Confirms you have verified this backend is "
                             "configured to key identity on email rather than its own subject identifier, "
                             "so email-based pre-linking will actually match.")

    args = parser.parse_args()

    print("=" * 80)
    print("Galaxy User OAuth/OIDC Conversion Tool")
    print("=" * 80)

    if args.disable_password:
        print(
            "\nWarning: --disable-password is deprecated and renamed to --mark-external, because it never\n"
            "         disabled password authentication -- it blocks the web login form only, while\n"
            "         /api/authenticate/baseauth keeps accepting the old password. Use\n"
            "         --invalidate-password to genuinely retire it."
        )
        args.mark_external = True

    db = open_db(args)
    print(f"Database: {db.describe()}")

    try:
        if args.list:
            list_all_users(db)
            return
        if args.list_unlinked:
            list_unlinked_users(db)
            return
        if args.list_linked:
            list_linked_users(db)
            return
        if args.verify:
            sys.exit(1 if verify_links(db) else 0)
        if args.remove_all_prelinked:
            remove_all_prelinked(db, args.provider, dry_run=args.preview_only)
            return

        if not args.provider:
            parser.error("--provider is required for this action")

        common = dict(
            mark_external=args.mark_external,
            invalidate_password=args.invalidate_password,
            rotate_api_keys=args.rotate_api_keys,
            confirm_non_email_uid=args.confirm_non_email_uid,
        )

        if args.preview:
            convert_all_users_to_oauth(db, args.provider, dry_run=True, exclude_emails=args.exclude, **common)
        elif args.convert:
            if not args.email:
                parser.error("--email is required for --convert")
            convert_user_to_oauth(db, args.email, args.provider, **common)
        elif args.convert_all:
            convert_all_users_to_oauth(db, args.provider, exclude_emails=args.exclude, **common)
        elif args.remove:
            if not args.email:
                parser.error("--email is required for --remove")
            remove_oauth_link(db, args.email, args.provider)
    finally:
        db.close()


if __name__ == "__main__":
    main()
