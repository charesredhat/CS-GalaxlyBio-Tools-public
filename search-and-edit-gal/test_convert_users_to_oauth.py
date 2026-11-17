#!/usr/bin/env python3
"""
Contract tests for the Galaxy OAuth/OIDC conversion tool.

Run with:  python3 search-and-edit-gal/test_convert_users_to_oauth.py
(stdlib unittest only -- no pytest, no SQLAlchemy, no running Galaxy.)

The point of this file
----------------------
The tool's real contract is one sentence:

    a row this tool inserts must be findable by Galaxy's own login lookup.

Galaxy's lookup is `UserAuthnzToken.get_social_auth(provider, uid)`
(lib/galaxy/model/__init__.py:11714), which is an exact, case-sensitive
`filter_by(provider=..., uid=...)` where `provider` comes from
`backend.name` (social_core/pipeline/social_auth.py, social_user()).

`galaxy_login_lookup` below reimplements exactly that, and the tests assert the
tool's output against it. Every historical bug in this tool -- the short
provider name, the mixed-case uid -- is a violation of that one sentence, and
each has a regression test here.

Galaxy's password hash format (lib/galaxy/security/passwords.py) is likewise
reimplemented in `galaxy_check_password`, so the invalidation path is checked
against the real verifier rather than against itself.
"""

import contextlib
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from base64 import b64encode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import convert_users_to_oauth as tool  # noqa: E402
from galaxy_db import (  # noqa: E402
    EMAIL_UID_PROVIDERS,
    LEGACY_BACKEND_NAMES,
    PRELINK_ASSOC_TYPE,
    RETIRED_PROVIDERS,
    SUPPORTED_PROVIDERS,
    GalaxyDB,
    backend_to_provider_name,
    hash_password_pbkdf2,
    provider_name_to_backend,
    unusable_password_hash,
)

# ---------------------------------------------------------------------------
# Reimplementations of Galaxy's own behaviour, used as the oracle
# ---------------------------------------------------------------------------

# Minimal subset of the Galaxy 26.1 schema these scripts touch. Column types and
# the UNIQUE(provider, uid) constraint mirror lib/galaxy/model/__init__.py.
SCHEMA = """
CREATE TABLE galaxy_user (
    id INTEGER PRIMARY KEY,
    create_time TIMESTAMP,
    update_time TIMESTAMP,
    email VARCHAR(255) NOT NULL,
    username VARCHAR(255),
    password VARCHAR(255) NOT NULL,
    last_password_change TIMESTAMP,
    external BOOLEAN DEFAULT 0,
    form_values_id INTEGER,
    preferred_object_store_id VARCHAR(255),
    deleted BOOLEAN DEFAULT 0,
    purged BOOLEAN DEFAULT 0,
    active BOOLEAN DEFAULT 1,
    activation_token VARCHAR(64),
    disk_usage NUMERIC
);

CREATE TABLE role (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255),
    description TEXT
);

CREATE TABLE user_role_association (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES galaxy_user(id),
    role_id INTEGER REFERENCES role(id)
);

CREATE TABLE oidc_user_authnz_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES galaxy_user(id),
    uid VARCHAR(255),
    provider VARCHAR(32),
    extra_data TEXT,
    lifetime INTEGER,
    assoc_type VARCHAR(64),
    UNIQUE(provider, uid)
);

CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY,
    create_time TIMESTAMP,
    user_id INTEGER REFERENCES galaxy_user(id),
    key VARCHAR(32) UNIQUE,
    deleted BOOLEAN DEFAULT 0
);
"""


def galaxy_login_lookup(conn, provider_backend_name, uid):
    """
    Galaxy's login-time association lookup, reimplemented exactly.

    model/__init__.py:11714:
        stmt = select(cls).filter_by(provider=provider, uid=uid).limit(1)

    That is an exact match on both columns -- no lowercasing, no collation
    fudging. `provider` is `backend.name`, e.g. 'google-openidconnect'.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id FROM oidc_user_authnz_tokens WHERE provider = ? AND uid = ? LIMIT 1",
        (provider_backend_name, uid),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def galaxy_check_password(guess, hashed):
    """galaxy.security.passwords.check_password, reimplemented."""
    if hashed.startswith("PBKDF2"):
        _name, hash_function, cost_factor, salt, encoded_original = hashed.split("$", 5)
        hashed_guess = hashlib.pbkdf2_hmac(
            hash_function, guess.encode("utf-8"), salt.encode("ascii"), int(cost_factor), 24
        )
        return b64encode(hashed_guess).decode("ascii") == encoded_original
    return hashlib.sha1(guess.encode("utf-8")).hexdigest() == hashed


class GalaxyDBTestCase(unittest.TestCase):
    """Builds a throwaway Galaxy-shaped SQLite database for each test."""

    def setUp(self):
        handle, self.db_file = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.raw = sqlite3.connect(self.db_file)
        self.raw.executescript(SCHEMA)
        self.raw.commit()
        self.db = GalaxyDB(GalaxyDB.sqlite_url_from_path(self.db_file))
        self.db.connect()

    def tearDown(self):
        self.db.close()
        self.raw.close()
        os.unlink(self.db_file)

    def add_user(self, email, username="user", password="hunter2", external=False, deleted=False):
        cursor = self.raw.cursor()
        cursor.execute(
            "INSERT INTO galaxy_user (email, username, password, external, deleted, purged, active) "
            "VALUES (?, ?, ?, ?, ?, 0, 1)",
            (email, username, hash_password_pbkdf2(password), external, deleted),
        )
        self.raw.commit()
        return cursor.lastrowid

    def add_api_key(self, user_id, key):
        self.raw.execute(
            "INSERT INTO api_keys (user_id, key, deleted) VALUES (?, ?, 0)", (user_id, key)
        )
        self.raw.commit()

    def reread(self):
        """Fresh connection, so we read what was actually committed."""
        return sqlite3.connect(self.db_file)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class TestLoginContract(GalaxyDBTestCase):
    """A row this tool writes must be findable by Galaxy's login lookup."""

    def test_google_prelink_is_found_by_galaxy(self):
        user_id = self.add_user("alice@example.org")
        tool.convert_user_to_oauth(self.db, "alice@example.org", "google", confirm_non_email_uid=True)

        # Galaxy looks up by backend name, which is what the IdP callback supplies.
        found = galaxy_login_lookup(self.reread(), "google-openidconnect", "alice@example.org")
        self.assertEqual(found, user_id, "Galaxy's login lookup must find the pre-linked row")

    def test_regression_short_provider_name_is_never_written(self):
        """
        The blocker this test exists for: the tool used to INSERT provider='google'.

        Galaxy stores and looks up 'google-openidconnect', so every such row was
        dead on arrival and no user could log in with it.
        """
        self.add_user("bob@example.org")
        tool.convert_user_to_oauth(self.db, "bob@example.org", "google", confirm_non_email_uid=True)

        cursor = self.reread().cursor()
        cursor.execute("SELECT provider FROM oidc_user_authnz_tokens")
        stored = [row[0] for row in cursor.fetchall()]
        self.assertEqual(stored, ["google-openidconnect"])
        self.assertNotIn("google", stored)
        self.assertIsNone(
            galaxy_login_lookup(self.reread(), "google", "bob@example.org"),
            "the short name must not be what Galaxy would match on",
        )

    def test_regression_email_shaped_uid_is_lowercased(self):
        """
        Galaxy lowercases the incoming email (FORCE_EMAIL_LOWERCASE defaults True)
        but compares uid exactly, so a mixed-case uid never matches.

        This holds for any email-shaped uid, whatever the provider -- it is no
        longer conditional on EMAIL_UID_PROVIDERS, which is now empty.
        """
        user_id = self.add_user("Carol.Jones@Example.ORG")
        tool.convert_user_to_oauth(self.db, "carol.jones@example.org", "google", confirm_non_email_uid=True)

        self.assertEqual(
            galaxy_login_lookup(self.reread(), "google-openidconnect", "carol.jones@example.org"),
            user_id,
        )
        self.assertIsNone(
            galaxy_login_lookup(self.reread(), "google-openidconnect", "Carol.Jones@Example.ORG"),
            "the mixed-case spelling is what Galaxy would NOT send",
        )

    def test_user_lookup_is_case_insensitive(self):
        """Galaxy matches user email case-insensitively (26.1.0, #21885)."""
        user_id = self.add_user("Dave@Example.ORG")
        found = tool.find_user_by_email(self.db, "dave@example.org")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], user_id)


class TestProviderMapping(unittest.TestCase):
    def test_known_translations(self):
        self.assertEqual(provider_name_to_backend("google"), "google-openidconnect")
        self.assertEqual(provider_name_to_backend("okta"), "okta-openidconnect")
        self.assertEqual(provider_name_to_backend("azure"), "azuread-v2-tenant-oauth2")
        self.assertEqual(provider_name_to_backend("keycloak"), "keycloak")
        self.assertEqual(provider_name_to_backend("auth0"), "auth0")

    def test_case_and_whitespace_tolerant(self):
        self.assertEqual(provider_name_to_backend("  GOOGLE "), "google-openidconnect")

    def test_unknown_returns_none(self):
        self.assertIsNone(provider_name_to_backend("definitely-not-a-provider"))

    def test_round_trip(self):
        for short in ("google", "okta", "azure", "keycloak", "cilogon", "auth0", "oidc"):
            self.assertEqual(backend_to_provider_name(provider_name_to_backend(short)), short)


class TestNonEmailUidGate(GalaxyDBTestCase):
    def test_non_email_provider_refuses_without_confirmation(self):
        """Okta keys on `sub`, so an email-keyed pre-link must be refused outright."""
        self.add_user("erin@example.org")
        with self.assertRaises(SystemExit) as caught:
            tool.convert_user_to_oauth(self.db, "erin@example.org", "okta")
        self.assertEqual(caught.exception.code, 1)

        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens")
        self.assertEqual(cursor.fetchone()[0], 0, "nothing should have been written")

    def test_google_refuses_without_confirmation(self):
        """
        The regression this test exists for: 'google' used to sit in
        EMAIL_UID_PROVIDERS, so a Google pre-link skipped the gate entirely and
        wrote uid=email with no warning. Galaxy loads Google as
        'google-openidconnect', whose ID_KEY resolves to 'sub' on social-core
        4.x, so every such row was dead on arrival -- the same silent-dead-row
        failure as the old provider='google' bug, one column over.
        """
        self.add_user("gwen@example.org")
        with self.assertRaises(SystemExit) as caught:
            tool.convert_user_to_oauth(self.db, "gwen@example.org", "google")
        self.assertEqual(caught.exception.code, 1)

        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens")
        self.assertEqual(cursor.fetchone()[0], 0, "nothing should have been written")

    def test_no_provider_is_email_keyed_by_default(self):
        """
        Pins the premise. No stock social-core backend keys OIDC identity on
        email, so this set must stay empty unless an operator has explicitly
        reconfigured a backend and verified ID_KEY themselves.
        """
        self.assertEqual(EMAIL_UID_PROVIDERS, set())
        for short in SUPPORTED_PROVIDERS:
            self.assertNotIn(short, EMAIL_UID_PROVIDERS)

    def test_bulk_conversion_refuses_google_without_confirmation(self):
        """The gate must cover --convert-all too, not just the single-user path."""
        self.add_user("hugh@example.org")
        with self.assertRaises(SystemExit):
            tool.convert_all_users_to_oauth(self.db, "google")

        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens")
        self.assertEqual(cursor.fetchone()[0], 0, "nothing should have been written")

    def test_non_email_provider_proceeds_with_confirmation(self):
        user_id = self.add_user("frank@example.org")
        tool.convert_user_to_oauth(self.db, "frank@example.org", "okta", confirm_non_email_uid=True)
        self.assertEqual(
            galaxy_login_lookup(self.reread(), "okta-openidconnect", "frank@example.org"), user_id
        )


class TestCredentialRetirement(GalaxyDBTestCase):
    def test_mark_external_alone_leaves_password_working(self):
        """
        The documented gap: `external` blocks the web form only, so the old
        password still authenticates on /api/authenticate/baseauth.
        """
        self.add_user("gina@example.org", password="correct horse")
        tool.convert_user_to_oauth(self.db, "gina@example.org", "google", confirm_non_email_uid=True, mark_external=True)

        cursor = self.reread().cursor()
        cursor.execute("SELECT external, password FROM galaxy_user WHERE email = 'gina@example.org'")
        external, stored = cursor.fetchone()
        self.assertTrue(external)
        self.assertTrue(
            galaxy_check_password("correct horse", stored),
            "this is the gap --mark-external does not close",
        )

    def test_invalidate_password_actually_retires_the_credential(self):
        self.add_user("hank@example.org", password="correct horse")
        tool.convert_user_to_oauth(
            self.db, "hank@example.org", "google", confirm_non_email_uid=True, mark_external=True, invalidate_password=True
        )

        cursor = self.reread().cursor()
        cursor.execute("SELECT password FROM galaxy_user WHERE email = 'hank@example.org'")
        stored = cursor.fetchone()[0]
        self.assertFalse(galaxy_check_password("correct horse", stored))
        self.assertTrue(stored.startswith("PBKDF2$sha256$"), "must stay a well-formed Galaxy hash")

    def test_unusable_hash_is_well_formed_but_unguessable(self):
        stored = unusable_password_hash()
        self.assertEqual(len(stored.split("$")), 5)
        for guess in ("", "password", "admin", stored):
            self.assertFalse(galaxy_check_password(guess, stored))

    def test_hash_round_trips_against_galaxys_verifier(self):
        """Our hash writer must be byte-compatible with Galaxy's reader."""
        stored = hash_password_pbkdf2("s3cret-passphrase")
        self.assertTrue(galaxy_check_password("s3cret-passphrase", stored))
        self.assertFalse(galaxy_check_password("s3cret-passphras", stored))

    def test_rotate_api_keys_revokes_existing_keys(self):
        user_id = self.add_user("iris@example.org")
        self.add_api_key(user_id, "key-one")
        self.add_api_key(user_id, "key-two")

        tool.convert_user_to_oauth(self.db, "iris@example.org", "google", confirm_non_email_uid=True, rotate_api_keys=True)

        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND deleted = 0", (user_id,))
        self.assertEqual(cursor.fetchone()[0], 0)


class TestAuditAndRollback(GalaxyDBTestCase):
    def test_rows_are_stamped_for_audit(self):
        self.add_user("jane@example.org")
        tool.convert_user_to_oauth(self.db, "jane@example.org", "google", confirm_non_email_uid=True)

        cursor = self.reread().cursor()
        cursor.execute("SELECT assoc_type FROM oidc_user_authnz_tokens")
        self.assertEqual(cursor.fetchone()[0], PRELINK_ASSOC_TYPE)

    def test_verify_flags_a_broken_short_name_row(self):
        """A row written by the old tool must be reported as broken."""
        user_id = self.add_user("ken@example.org")
        self.raw.execute(
            "INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data) VALUES (?, ?, 'google', '{}')",
            (user_id, "ken@example.org"),
        )
        self.raw.commit()
        self.assertEqual(tool.verify_links(self.db), 1)

    def test_verify_passes_on_a_correctly_written_row(self):
        self.add_user("liz@example.org")
        tool.convert_user_to_oauth(self.db, "liz@example.org", "google", confirm_non_email_uid=True)
        self.assertEqual(tool.verify_links(self.db), 0)

    def test_verify_flags_mixed_case_email_uid_for_any_provider(self):
        """
        The uid-case check used to fire only for providers in
        EMAIL_UID_PROVIDERS. With that set empty it would have stopped firing
        altogether, so it now keys off the shape of the uid instead.
        """
        user_id = self.add_user("oscar@example.org")
        self.raw.execute(
            "INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data) "
            "VALUES (?, 'Oscar@Example.ORG', 'keycloak', '{}')",
            (user_id,),
        )
        self.raw.commit()
        self.assertEqual(tool.verify_links(self.db), 1, "a mixed-case email uid must be reported")

    def test_verify_leaves_opaque_subject_uids_alone(self):
        """
        A `sub` is an opaque IdP string and may legitimately carry uppercase.
        Lowercasing it would break the very login the check exists to protect,
        so only email-shaped uids are judged on case.
        """
        user_id = self.add_user("pete@example.org")
        self.raw.execute(
            "INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data) "
            "VALUES (?, 'AbC123-MixedCaseSubject', 'keycloak', '{}')",
            (user_id,),
        )
        self.raw.commit()
        self.assertEqual(tool.verify_links(self.db), 0, "an opaque sub must not be flagged on case")

    def test_rollback_removes_only_our_rows(self):
        our_user = self.add_user("mia@example.org")
        galaxys_user = self.add_user("nick@example.org")
        tool.convert_user_to_oauth(self.db, "mia@example.org", "google", confirm_non_email_uid=True)
        # A row Galaxy created itself: no assoc_type marker.
        self.raw.execute(
            "INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data) "
            "VALUES (?, 'nick@example.org', 'google-openidconnect', '{}')",
            (galaxys_user,),
        )
        self.raw.commit()

        tool.remove_all_prelinked(self.db, "google")

        cursor = self.reread().cursor()
        cursor.execute("SELECT user_id FROM oidc_user_authnz_tokens")
        remaining = [row[0] for row in cursor.fetchall()]
        self.assertEqual(remaining, [galaxys_user], "Galaxy's own row must survive")
        self.assertNotIn(our_user, remaining)


class TestBulkConversion(GalaxyDBTestCase):
    def test_converts_all_and_respects_excludes(self):
        self.add_user("one@example.org")
        self.add_user("two@example.org")
        admin = self.add_user("admin@example.org")

        converted = tool.convert_all_users_to_oauth(
            self.db, "google", confirm_non_email_uid=True, exclude_emails=["admin@example.org"]
        )
        self.assertEqual(converted, 2)

        cursor = self.reread().cursor()
        cursor.execute("SELECT user_id FROM oidc_user_authnz_tokens")
        linked = {row[0] for row in cursor.fetchall()}
        self.assertNotIn(admin, linked)

    def test_a_failure_does_not_discard_earlier_successes(self):
        """
        Regression: the old code committed once at the end, so any error partway
        through a batch threw away every conversion that had already succeeded.
        """
        self.add_user("aaa@example.org")
        self.add_user("bbb@example.org")
        self.add_user("ccc@example.org")

        real_create = tool.create_oauth_link
        calls = {"n": 0}

        def flaky(db, user_id, uid, backend, extra_data=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3.OperationalError("simulated mid-batch failure")
            return real_create(db, user_id, uid, backend, extra_data)

        tool.create_oauth_link = flaky
        try:
            tool.convert_all_users_to_oauth(self.db, "google", confirm_non_email_uid=True)
        finally:
            tool.create_oauth_link = real_create

        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens")
        self.assertEqual(cursor.fetchone()[0], 2, "the two healthy users must stay committed")

    def test_skips_deleted_users(self):
        self.add_user("live@example.org")
        self.add_user("gone@example.org", deleted=True)
        self.assertEqual(tool.convert_all_users_to_oauth(self.db, "google", confirm_non_email_uid=True), 1)

    def test_preview_writes_nothing(self):
        self.add_user("dry@example.org")
        tool.convert_all_users_to_oauth(self.db, "google", confirm_non_email_uid=True, dry_run=True)
        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens")
        self.assertEqual(cursor.fetchone()[0], 0)


class TestConflictHandling(GalaxyDBTestCase):
    def test_uid_already_linked_to_another_user_is_refused(self):
        first = self.add_user("shared@example.org", username="first")
        second = self.add_user("Shared@Example.org", username="second")
        tool.convert_user_to_oauth(self.db, "shared@example.org", "google", confirm_non_email_uid=True)

        # The second account normalises to the same uid; it must not steal the link.
        tool.convert_user_to_oauth(self.db, "Shared@Example.org", "google", confirm_non_email_uid=True)
        self.assertEqual(
            galaxy_login_lookup(self.reread(), "google-openidconnect", "shared@example.org"), first
        )
        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens WHERE user_id = ?", (second,))
        self.assertEqual(cursor.fetchone()[0], 0)

    def test_converting_twice_is_idempotent(self):
        self.add_user("twice@example.org")
        tool.convert_user_to_oauth(self.db, "twice@example.org", "google", confirm_non_email_uid=True)
        tool.convert_user_to_oauth(self.db, "twice@example.org", "google", confirm_non_email_uid=True)
        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens")
        self.assertEqual(cursor.fetchone()[0], 1)


class TestRemoval(GalaxyDBTestCase):
    def test_removing_last_link_clears_external(self):
        self.add_user("solo@example.org")
        tool.convert_user_to_oauth(self.db, "solo@example.org", "google", confirm_non_email_uid=True, mark_external=True)
        tool.remove_oauth_link(self.db, "solo@example.org", "google")

        cursor = self.reread().cursor()
        cursor.execute("SELECT external FROM galaxy_user WHERE email = 'solo@example.org'")
        self.assertFalse(cursor.fetchone()[0])

    def test_removing_one_of_two_links_leaves_external_set(self):
        """Clearing `external` here would silently defeat OIDC-only enforcement."""
        self.add_user("multi@example.org")
        tool.convert_user_to_oauth(self.db, "multi@example.org", "google", confirm_non_email_uid=True, mark_external=True)
        tool.convert_user_to_oauth(
            self.db, "multi@example.org", "keycloak", confirm_non_email_uid=True, mark_external=True
        )
        tool.remove_oauth_link(self.db, "multi@example.org", "google")

        cursor = self.reread().cursor()
        cursor.execute("SELECT external FROM galaxy_user WHERE email = 'multi@example.org'")
        self.assertTrue(cursor.fetchone()[0])


class TestSQLPortability(unittest.TestCase):
    """
    The SQL in these scripts has to run on PostgreSQL, where production Galaxy
    lives. COLLATE NOCASE and GROUP_CONCAT are SQLite-only -- the first is a
    syntax error on PostgreSQL, not a behaviour difference.
    """

    @staticmethod
    def _sql_literals(path):
        """
        Every string literal in the file that actually looks like SQL.

        Parsing with `ast` rather than grepping the raw text means prose in
        docstrings and comments -- which necessarily *names* the banned
        constructs to explain why they are banned -- cannot trip the check.
        """
        import ast

        with open(path) as handle:
            tree = ast.parse(handle.read())
        statements = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "PRAGMA ")
        literals = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(kw in node.value.upper() for kw in statements):
                    literals.append(node.value)
        return literals

    def test_no_sqlite_only_sql_remains(self):
        here = os.path.dirname(os.path.abspath(__file__))
        checked = 0
        for name in ("convert_users_to_oauth.py", "galaxy_db.py", "search_galaxy_users.py"):
            path = os.path.join(here, name)
            if not os.path.exists(path):
                continue
            for sql in self._sql_literals(path):
                checked += 1
                upper = sql.upper()
                self.assertNotIn("COLLATE NOCASE", upper,
                                 f"{name} still uses SQLite-only COLLATE NOCASE in: {sql[:80]}")
                self.assertNotIn("GROUP_CONCAT", upper,
                                 f"{name} still uses SQLite-only GROUP_CONCAT in: {sql[:80]}")
        self.assertGreater(checked, 0, "no SQL literals found -- the check would pass vacuously")

    def test_placeholder_translation(self):
        pg = GalaxyDB("postgresql://user@host/galaxy")
        self.assertEqual(pg._sql("SELECT ? , ?"), "SELECT %s , %s")
        lite = GalaxyDB("sqlite:////tmp/x.sqlite")
        self.assertEqual(lite._sql("SELECT ? , ?"), "SELECT ? , ?")

    def test_rejects_unknown_scheme(self):
        from galaxy_db import GalaxyDBError

        with self.assertRaises(GalaxyDBError):
            GalaxyDB("mysql://user@host/galaxy")

    def test_describe_redacts_password(self):
        described = GalaxyDB("postgresql://galaxy:sup3rs3cret@db.internal:5432/galaxy").describe()
        self.assertNotIn("sup3rs3cret", described)
        self.assertIn("db.internal", described)


class TestSearchGalaxyUsers(GalaxyDBTestCase):
    """
    The sibling script shares this DB layer, so its queries are covered here too.

    These also pin the galaxy_user column list against the real 26.1 model: the
    fixture schema above mirrors it, so a query naming a column Galaxy does not
    have (or that this fixture forgot) fails loudly rather than only in production.
    """

    def test_auth_type_listing_runs_and_classifies(self):
        import search_galaxy_users

        linked = self.add_user("linked@example.org")
        self.add_user("local@example.org")
        tool.convert_user_to_oauth(self.db, "linked@example.org", "google", confirm_non_email_uid=True)

        search_galaxy_users.list_users_auth_type(self.db, None)
        search_galaxy_users.list_users_auth_type(self.db, "oauth")
        search_galaxy_users.list_users_auth_type(self.db, "local")

        cursor = self.reread().cursor()
        cursor.execute("SELECT user_id FROM oidc_user_authnz_tokens")
        self.assertEqual([r[0] for r in cursor.fetchall()], [linked])

    def test_search_is_case_insensitive_on_both_sides(self):
        import search_galaxy_users

        self.add_user("Olivia@Example.ORG", username="Olivia")
        found = search_galaxy_users.search_users_db(self.db, email_filter="olivia")
        self.assertEqual(len(found), 1)
        found = search_galaxy_users.search_users_db(self.db, username_filter="OLIVIA")
        self.assertEqual(len(found), 1)

    def test_roles_listing_runs(self):
        import search_galaxy_users

        user_id = self.add_user("roled@example.org")
        self.raw.execute("INSERT INTO role (id, name, description) VALUES (1, 'admin', 'Admins')")
        self.raw.execute(
            "INSERT INTO user_role_association (user_id, role_id) VALUES (?, 1)", (user_id,)
        )
        self.raw.commit()
        self.assertEqual(len(search_galaxy_users.list_user_roles(self.db, user_id)), 1)

    def test_missing_legacy_custos_table_is_not_an_error(self):
        """custos_authnz_token is dropped at Galaxy 26.0; its absence is normal."""
        import search_galaxy_users

        self.assertFalse(self.db.table_exists("custos_authnz_token"))
        self.add_user("nocustos@example.org")
        search_galaxy_users.list_users_auth_type(self.db, None)


class TestCredentialRetirementSafety(GalaxyDBTestCase):
    """
    Retiring a credential is only safe once the user actually has a link.

    Destroying a password for a user who ended up with NO OIDC link locks them
    out permanently and irreversibly -- there is no route back in.
    """

    def _make_uid_conflict(self):
        """
        Set up a genuine uid conflict.

        `winner` holds a link whose uid is `loser`'s email -- which is what an
        admin linking an account by hand, or a since-corrected email change,
        leaves behind. Converting `loser` then collides on (provider, uid).

        Two accounts differing only in email case would NOT work here: Galaxy
        compares email case-insensitively and `galaxy_user.email` is unique, so
        find_user_by_email() would match the same row twice.
        """
        winner = self.add_user("winner@example.org", username="winner")
        loser = self.add_user("loser@example.org", username="loser")
        self.raw.execute(
            "INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data, assoc_type) "
            "VALUES (?, 'loser@example.org', 'google-openidconnect', '{}', ?)",
            (winner, PRELINK_ASSOC_TYPE),
        )
        self.raw.commit()
        return winner, loser

    def test_single_user_conflict_leaves_credentials_untouched(self):
        _winner, loser = self._make_uid_conflict()

        result = tool.convert_user_to_oauth(
            self.db, "loser@example.org", "google", confirm_non_email_uid=True,
            mark_external=True, invalidate_password=True, rotate_api_keys=True,
        )
        self.assertFalse(result, "a conflict must report failure, not 'Conversion complete'")

        cursor = self.reread().cursor()
        cursor.execute("SELECT external, password FROM galaxy_user WHERE id = ?", (loser,))
        external, stored = cursor.fetchone()
        self.assertTrue(
            galaxy_check_password("hunter2", stored),
            "password destroyed for a user who got no OIDC link -- permanent lockout",
        )
        self.assertFalse(external)
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens WHERE user_id = ?", (loser,))
        self.assertEqual(cursor.fetchone()[0], 0)

    def test_bulk_conflict_leaves_credentials_untouched(self):
        _winner, loser = self._make_uid_conflict()
        self.add_api_key(loser, "loser-key")

        tool.convert_all_users_to_oauth(
            self.db, "google", confirm_non_email_uid=True, mark_external=True, invalidate_password=True, rotate_api_keys=True,
        )

        cursor = self.reread().cursor()
        cursor.execute("SELECT password FROM galaxy_user WHERE id = ?", (loser,))
        self.assertTrue(galaxy_check_password("hunter2", cursor.fetchone()[0]))
        cursor.execute("SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND deleted = 0", (loser,))
        self.assertEqual(cursor.fetchone()[0], 1, "API key revoked for an unlinked user")

    def test_bulk_retires_credentials_for_already_linked_users(self):
        """
        A follow-up run must still retire credentials for users linked earlier.

        The bulk query used to filter already-linked users out entirely, so
        re-running with --invalidate-password silently did nothing for exactly
        the users it was safe to do it for.
        """
        user_id = self.add_user("linked@example.org", password="correct horse")
        self.add_api_key(user_id, "old-key")
        tool.convert_all_users_to_oauth(self.db, "google", confirm_non_email_uid=True)          # link only
        tool.convert_all_users_to_oauth(                            # retire later
            self.db, "google", confirm_non_email_uid=True, mark_external=True, invalidate_password=True, rotate_api_keys=True,
        )

        cursor = self.reread().cursor()
        cursor.execute("SELECT external, password FROM galaxy_user WHERE id = ?", (user_id,))
        external, stored = cursor.fetchone()
        self.assertTrue(external)
        self.assertFalse(galaxy_check_password("correct horse", stored))
        cursor.execute("SELECT COUNT(*) FROM api_keys WHERE user_id = ? AND deleted = 0", (user_id,))
        self.assertEqual(cursor.fetchone()[0], 0)

    def test_link_outcomes_are_distinguished(self):
        winner, loser = self._make_uid_conflict()
        self.assertEqual(
            tool.create_oauth_link(self.db, winner, "winner@example.org", "google-openidconnect"),
            tool.LINK_EXISTS,
        )
        self.assertEqual(
            tool.create_oauth_link(self.db, loser, "loser@example.org", "google-openidconnect"),
            tool.LINK_CONFLICT,
        )


class TestRetiredAndLegacyProviders(GalaxyDBTestCase):
    def test_custos_is_not_offered_as_a_choice(self):
        """
        It cannot work on any version: removed at 26.0, and before that Custos
        used custos_authnz_token, which this tool never writes.
        """
        self.assertNotIn("custos", SUPPORTED_PROVIDERS)
        self.assertIn("custos", RETIRED_PROVIDERS)

    def test_retired_provider_fails_with_an_explanation(self):
        with self.assertRaises(SystemExit) as caught:
            tool.resolve_provider("custos")
        self.assertEqual(caught.exception.code, 1)

    def test_every_supported_provider_actually_resolves(self):
        """Guards the mismatch that made --provider custos die on 'unknown provider'."""
        for name in SUPPORTED_PROVIDERS:
            self.assertIsNotNone(
                provider_name_to_backend(name),
                f"'{name}' is offered at --provider but has no backend mapping",
            )

    def test_legacy_backend_names_are_not_reported_as_broken(self):
        """
        An upgraded instance legitimately holds older backend names. Calling
        them BROKEN invites an operator to 'repair' working logins.
        """
        user_id = self.add_user("legacy@example.org")
        self.raw.execute(
            "INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data) "
            "VALUES (?, 'legacy@example.org', 'azuread-tenant-oauth2', '{}')",
            (user_id,),
        )
        self.raw.commit()
        self.assertIn("azuread-tenant-oauth2", LEGACY_BACKEND_NAMES)
        self.assertEqual(tool.verify_links(self.db), 0, "legacy names must not fail verification")


class TestRollbackScale(GalaxyDBTestCase):
    def test_rollback_handles_more_rows_than_the_bind_parameter_limit(self):
        """
        Deleting via `id IN (...)` needed one bind parameter per row, blowing
        SQLite's variable cap (999 on some builds) and psycopg2's 65535 -- so
        rollback failed on exactly the large instances that need it.
        """
        # Insert directly rather than via add_user(): this test is about the bind
        # parameter count, and hashing 1500 passwords at 100k PBKDF2 iterations
        # would dominate the whole suite's runtime for no added coverage.
        rows = 1500
        cursor = self.raw.cursor()
        cursor.executemany(
            "INSERT INTO galaxy_user (email, username, password, external, deleted, purged, active) "
            "VALUES (?, ?, 'x', 0, 0, 0, 1)",
            [(f"bulk{i}@example.org", f"bulk{i}") for i in range(rows)],
        )
        cursor.executemany(
            "INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data, assoc_type) "
            "SELECT id, email, 'google-openidconnect', '{}', ? FROM galaxy_user WHERE email = ?",
            [(PRELINK_ASSOC_TYPE, f"bulk{i}@example.org") for i in range(rows)],
        )
        self.raw.commit()

        self.assertEqual(tool.remove_all_prelinked(self.db, "google"), rows)
        cursor = self.reread().cursor()
        cursor.execute("SELECT COUNT(*) FROM oidc_user_authnz_tokens")
        self.assertEqual(cursor.fetchone()[0], 0)


class TestDatabaseResolutionPrecedence(unittest.TestCase):
    """Explicit flags must outrank the ambient environment."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("GALAXY_DB_URL", "GALAXY_DB_PATH")}
        handle, self.db_file = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        sqlite3.connect(self.db_file).close()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.unlink(self.db_file)

    def test_explicit_db_path_beats_ambient_env_url(self):
        """
        An ambient GALAXY_DB_URL used to win over an explicitly passed path,
        silently pointing a destructive run at the wrong database.
        """
        os.environ["GALAXY_DB_URL"] = "postgresql://galaxy@wrong-host/wrong-db"
        db = GalaxyDB.resolve(db_path=self.db_file)
        self.assertEqual(db.dialect, "sqlite")
        self.assertIn(os.path.basename(self.db_file), db.url)

    def test_explicit_db_url_still_wins(self):
        os.environ["GALAXY_DB_URL"] = "postgresql://galaxy@wrong-host/wrong-db"
        db = GalaxyDB.resolve(db_url="postgresql://galaxy@right-host/right-db")
        self.assertIn("right-host", db.url)

    def test_env_url_used_when_nothing_explicit_is_given(self):
        os.environ["GALAXY_DB_URL"] = "postgresql://galaxy@host/galaxy"
        self.assertEqual(GalaxyDB.resolve().dialect, "postgresql")

    def test_config_without_connection_uses_that_galaxys_database(self):
        """
        A galaxy.yml with no database_connection used to fall through to
        ~/galaxy/database/universe.sqlite instead of <root>/database/.
        """
        os.environ["GALAXY_DB_URL"] = "postgresql://galaxy@wrong-host/wrong-db"
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "config"))
            os.makedirs(os.path.join(root, "database"))
            config = os.path.join(root, "config", "galaxy.yml")
            with open(config, "w") as handle:
                handle.write("galaxy:\n  admin_users: admin@example.org\n")
            sqlite3.connect(os.path.join(root, "database", "universe.sqlite")).close()
            with contextlib.redirect_stdout(io.StringIO()):
                db = GalaxyDB.resolve(galaxy_config=config)
            self.assertEqual(db.dialect, "sqlite")
            self.assertTrue(db.url.endswith(os.path.join(root, "database", "universe.sqlite")))

    def test_db_path_wins_over_config_default_without_announcing_the_config(self):
        """
        --db-path outranks a config's default SQLite file, so the run must not
        print a note naming a database it is not about to touch.
        """
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "config"))
            os.makedirs(os.path.join(root, "database"))
            config = os.path.join(root, "config", "galaxy.yml")
            with open(config, "w") as handle:
                handle.write("galaxy:\n  admin_users: admin@example.org\n")
            unused = os.path.join(root, "database", "universe.sqlite")
            sqlite3.connect(unused).close()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                db = GalaxyDB.resolve(galaxy_config=config, db_path=self.db_file)
            self.assertIn(os.path.basename(self.db_file), db.url)
            self.assertNotIn(unused, out.getvalue())


class TestSchemaQualifiedLookups(GalaxyDBTestCase):
    def test_table_and_column_existence_agree(self):
        self.assertTrue(self.db.table_exists("api_keys"))
        self.assertTrue(self.db.column_exists("api_keys", "deleted"))
        self.assertFalse(self.db.table_exists("definitely_not_a_table"))
        self.assertFalse(self.db.column_exists("api_keys", "definitely_not_a_column"))

    def test_postgres_existence_checks_are_not_pinned_to_public(self):
        """
        Hardcoding `public.` gives a false negative on a Galaxy installed into a
        named schema -- and a false negative here is dangerous: --rotate-api-keys
        would silently skip while --invalidate-password still destroyed the
        password. Both checks must resolve through search_path instead.
        """
        import inspect

        from galaxy_db import GalaxyDB as Cls

        for method in (Cls.table_exists, Cls.column_exists):
            self.assertNotIn("public.", inspect.getsource(method))


class TestSetupScriptDoesNotClobberConfig(unittest.TestCase):
    """setup_galaxy_oidc.sh must never destroy a live galaxy.yml."""

    def test_sample_is_not_copied_unconditionally(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "setup_galaxy_oidc.sh",
        )
        with open(path) as handle:
            lines = handle.read().splitlines()

        copies = [
            line for line in lines
            if "cp ../lib/galaxy/config/sample/galaxy.yml.sample galaxy.yml" in line
            and not line.strip().startswith("#")
        ]
        self.assertTrue(copies, "the script should still be able to create a missing galaxy.yml")
        for line in copies:
            self.assertTrue(
                line.startswith("    "),
                f"unconditional copy would destroy a live config: {line.strip()!r}",
            )

    def test_backup_is_not_overwritten(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "setup_galaxy_oidc.sh",
        )
        with open(path) as handle:
            body = handle.read()
        self.assertNotIn("cp galaxy.yml galaxy.yml.backup", body,
                         "a second run would overwrite the only copy of the original")


class TestOidcBackendsConfigFragments(unittest.TestCase):
    """
    The <provider> fragments configure_oidc_provider.sh writes must be valid
    against Galaxy's XSD.

    This is not cosmetic. AuthnzManager._parse_oidc_backends_config() calls
    parse_xml(config_file, schemafname=OIDC_BACKEND_SCHEMA), and
    galaxy.util.parse_xml runs schema.assertValid(tree), which raises. The
    provider content model is <xs:all>, so ANY undeclared child element makes
    Galaxy refuse to parse the file at startup.

    The element set below is transcribed from
    lib/galaxy/authnz/xsd/oidc_backends_config.xsd at v26.1.1. Note it declares
    `idphint` and `domain` -- there is no `kc_idp_hint` element (that is the
    query parameter KeycloakOpenIdConnect.auth_params() derives from idphint),
    and Auth0 needs `domain` rather than `url`.
    """

    DECLARED = {
        "accepted_audiences", "allowed_idp", "api_url", "ca_bundle", "client_id",
        "client_secret", "custom_button_text", "domain", "enable_idp_logout",
        "end_user_registration_endpoint", "extra_scopes", "icon", "idphint", "label",
        "oidc_endpoint", "pkce_support", "profile_url", "prompt", "redirect_uri",
        "require_create_confirmation", "require_session_refresh", "require_user_activation",
        "tenant_id", "url", "username_key", "well_known_oidc_config_uri",
    }
    REQUIRED = {"client_id", "client_secret", "redirect_uri"}

    @classmethod
    def _fragments(cls):
        import re

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configure_oidc_provider.sh",
        )
        with open(path) as handle:
            body = handle.read()
        for match in re.finditer(r'<provider name="([^"]+)">(.*?)</provider>', body, re.S):
            yield match.group(1), set(re.findall(r"<([a-z_0-9]+)>", match.group(2)))

    def test_every_fragment_uses_only_declared_elements(self):
        fragments = list(self._fragments())
        self.assertGreater(len(fragments), 0, "no provider fragments found in the script")
        for name, emitted in fragments:
            undeclared = emitted - self.DECLARED
            self.assertEqual(
                undeclared, set(),
                f"provider '{name}' emits element(s) the XSD does not declare: {sorted(undeclared)} "
                "-- Galaxy would refuse to start",
            )

    def test_every_fragment_has_the_required_elements(self):
        for name, emitted in self._fragments():
            missing = self.REQUIRED - emitted
            self.assertEqual(missing, set(), f"provider '{name}' is missing required {sorted(missing)}")

    def test_regression_keycloak_uses_idphint_not_kc_idp_hint(self):
        keycloak = [emitted for name, emitted in self._fragments() if name == "keycloak"]
        self.assertTrue(keycloak, "no keycloak fragment found")
        self.assertTrue(any("idphint" in e for e in keycloak), "keycloak should offer <idphint>")
        for emitted in keycloak:
            self.assertNotIn("kc_idp_hint", emitted, "<kc_idp_hint> is not a real config element")

    def test_regression_auth0_uses_domain_not_url(self):
        auth0 = [emitted for name, emitted in self._fragments() if name == "auth0"]
        self.assertTrue(auth0, "no auth0 fragment found")
        for emitted in auth0:
            self.assertIn("domain", emitted,
                          "Auth0 resolves its endpoint from DOMAIN; <url> raises AuthMissingParameter")
            self.assertNotIn("url", emitted)


class TestSetupScript(unittest.TestCase):
    """Regressions in setup_galaxy_oidc.sh that silently corrupt a config."""

    @classmethod
    def _body(cls):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "setup_galaxy_oidc.sh",
        )
        with open(path) as handle:
            return handle.read()

    def test_does_not_append_a_second_galaxy_mapping(self):
        """
        Appending `galaxy:` to a file that already has one discards the first:
        YAML keeps the last occurrence of a duplicate key.
        """
        import yaml

        self.assertEqual(
            yaml.safe_load("galaxy:\n  a: 1\n  b: 2\ngalaxy:\n  c: 3\n"),
            {"galaxy": {"c": 3}},
            "this is the YAML behaviour the script must avoid",
        )
        body = self._body()
        self.assertNotIn("cat >> galaxy.yml", body,
                         "settings must be merged into the existing galaxy: mapping, not appended")

    def test_uses_the_current_config_key_name(self):
        body = self._body()
        self.assertIn("allow_local_account_creation", body)
        # `allow_user_creation` survives only inside the comment explaining the rename.
        for line in body.splitlines():
            if "allow_user_creation" in line:
                self.assertIn("#", line, f"deprecated alias used as a real setting: {line.strip()}")

    def test_ssl_verification_is_not_disabled_by_default(self):
        body = self._body()
        self.assertNotIn('Property="VERIFY_SSL" Value="False"', body,
                         "shipping VERIFY_SSL=False lets an attacker impersonate the IdP")
        self.assertIn('Property="VERIFY_SSL" Value="True"', body)


if __name__ == "__main__":
    # The tool prints progress to stdout; quieten it so test output stays readable.
    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = unittest.main(exit=False, verbosity=0).result

    print(f"Ran {result.testsRun} tests: "
          f"{len(result.failures)} failure(s), {len(result.errors)} error(s)")
    for case, trace in result.failures + result.errors:
        print(f"\n--- {case} ---\n{trace}")
    sys.exit(1 if (result.failures or result.errors) else 0)
