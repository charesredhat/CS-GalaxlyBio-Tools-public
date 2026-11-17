#!/usr/bin/env python3
"""
Shared Galaxy database access layer for the OAuth/OIDC admin scripts.

Why this module exists
----------------------
The scripts in this directory used to talk to `sqlite3` directly and default to
`~/galaxy/database/universe.sqlite`. Every production Galaxy (galaxtrakr
included) runs PostgreSQL, so none of that code could run where it mattered --
and SQLite-only SQL (`COLLATE NOCASE`, `GROUP_CONCAT`) is a *syntax error* on
PostgreSQL rather than a silent behaviour difference.

This layer is deliberately thin: a DB-API connection plus a `?` -> `%s`
placeholder translation, so there is exactly one SQL dialect to maintain. It
needs no third-party package for SQLite (stdlib `sqlite3`); PostgreSQL needs
`psycopg2` or `psycopg` installed, and says so clearly if it is missing.

Provider naming
---------------
`provider_name_to_backend()` is the single most important function here. Galaxy
does NOT store the short provider name from `oidc_backends_config.xml` in
`oidc_user_authnz_tokens.provider` -- it stores the python-social-auth *backend*
name. Writing the short name produces a row that can never match at login. See
`provider_name_to_backend` for the full reference chain.
"""

import hashlib
import os
import re
import sys
from base64 import b64encode
from os import urandom
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Provider naming
# ---------------------------------------------------------------------------

# Mirrors BACKENDS_NAME in lib/galaxy/authnz/psa_authnz.py (Galaxy 26.1).
#
# Key   = the `name` attribute used in oidc_backends_config.xml (lowercased),
#         which is also what users type at `--provider`.
# Value = the python-social-auth backend name, which is what Galaxy actually
#         stores in and reads from `oidc_user_authnz_tokens.provider`.
#
# Verified chain (galaxyproject/galaxy @ release_26.1):
#   * psa_authnz.py:94       BACKENDS_NAME, this mapping
#   * psa_authnz.py:551      writes provider=BACKENDS_NAME[config["provider"]]
#   * psa_authnz.py:482      reads it back the same way
#   * social_core/pipeline/social_auth.py  social_user(): provider = backend.name
#   * model/__init__.py:11714 get_social_auth() -> filter_by(provider=..., uid=...)
#                             i.e. an exact, case-sensitive match on both columns
BACKENDS_NAME = {
    "google": "google-openidconnect",
    "globus": "globus",
    "elixir": "elixir",
    "lifescience": "life_science",
    "einfracz": "e-infra_cz",
    "nfdi": "infraproxy",
    "okta": "okta-openidconnect",
    "azure": "azuread-v2-tenant-oauth2",
    "egi_checkin": "egi-checkin",
    "oidc": "oidc",
    "tapis": "tapis",
    "keycloak": "keycloak",
    "cilogon": "cilogon",
    "auth0": "auth0",
}

# Short names this tool offers at --provider. Restricted to the providers the
# helper scripts in this repo can actually configure; BACKENDS_NAME above is
# the full set Galaxy supports, and is what verification checks against.
#
# 'custos' is deliberately NOT here. It cannot be pre-linked on any Galaxy
# version: on >= 26.0 the backend no longer exists, and on < 26.0 it stored
# tokens in the separate `custos_authnz_token` table rather than in
# oidc_user_authnz_tokens, which is the only table this tool writes. Offering it
# as a choice only produced a confusing "unknown provider" failure after the
# deprecation warning had already printed.
SUPPORTED_PROVIDERS = ["google", "azure", "okta", "keycloak", "cilogon", "auth0", "oidc"]

# Names a user might reasonably still pass, with the reason they cannot be used.
# Kept separate from SUPPORTED_PROVIDERS so the error explains itself instead of
# just rejecting the name.
RETIRED_PROVIDERS = {
    "custos": (
        "Galaxy 26.0.0 removed the standalone Custos backend ('Reimplement keycloak/cilogon "
        "as PSA providers and remove custos'), and migration 724237cc4cf0 migrated its tokens "
        "into oidc_user_authnz_tokens before dropping custos_authnz_token.\n"
        "  On Galaxy >= 26.0 use 'keycloak' or 'cilogon' instead.\n"
        "  On Galaxy < 26.0 Custos kept its tokens in custos_authnz_token, a table this tool "
        "does not write, so pre-linking could not work there either."
    ),
}

# Retained for callers that still import it; see RETIRED_PROVIDERS for the detail.
DEPRECATED_PROVIDERS = set(RETIRED_PROVIDERS)

# Backend names that were valid in earlier Galaxy releases but are no longer in
# BACKENDS_NAME. Rows carrying these are legacy, not broken -- an instance
# upgraded from an older release will legitimately hold them, and "repairing"
# them would break working logins.
LEGACY_BACKEND_NAMES = {
    "azuread-oauth2",          # pre-V2 Azure backend
    "azuread-tenant-oauth2",   # V1 tenant backend, superseded by azuread-v2-tenant-oauth2
    "google-oauth2",           # non-OIDC Google backend
    "auth0_openidconnect",     # social-core's own name, before Galaxy's auth0 subclass
    "custos",                  # pre-26.0 Custos rows migrated into this table
}

# Providers whose backend keys OIDC identity on the user's email address, so
# pre-linking with uid=email is safe.
#
# This set is EMPTY, and that is not an oversight. It used to hold "google" on
# the strength of `BaseGoogleAuth.ID_KEY = "email"`, which was true of older
# social-core releases. It is not true of the backend Galaxy actually loads.
#
# Measured on the galaxytrakr sandbox (Galaxy 26.0.1.dev1, social-core 4.8.3),
# in that instance's own venv:
#
#   >>> from social_core.backends.google_openidconnect import GoogleOpenIdConnect as G
#   >>> G.name, G.ID_KEY
#   ('google-openidconnect', 'sub')
#   >>> [c.__name__ for c in G.__mro__[:6]]
#   ['GoogleOpenIdConnect', 'GoogleOAuth2', 'BaseGoogleOAuth2API',
#    'BaseGoogleAuth', 'OpenIdConnectAuth', 'BaseOAuth2']
#
# Neither google.py nor google_openidconnect.py defines ID_KEY any more, so it
# resolves down the MRO to OpenIdConnectAuth (open_id_connect.py:66) -- `sub`.
# `google` in psa_authnz.BACKENDS_NAME maps to `google-openidconnect`, i.e. the
# OIDC backend, never the plain OAuth2 one, so the email-keyed inheritance from
# BaseGoogleAuth is unreachable from Galaxy regardless.
#
# So on any modern social-core EVERY backend keys on its own subject identifier
# -- `sub` for Google/generic OIDC/Okta/Keycloak/CILogon/Auth0,
# `preferred_username` for Azure V2 (AzureADV2TenantOAuth2.ID_KEY) -- and an
# email-keyed pre-link silently never matches. There is no uid an operator can
# know ahead of time; use the `associate_by_email_if_logged_in` pipeline route
# instead and let Galaxy record the real subject itself.
#
# Add a provider here only for a backend you have explicitly reconfigured to
# key on email, and verify it the way shown above rather than from the docs.
EMAIL_UID_PROVIDERS: set = set()

# Marker written into oidc_user_authnz_tokens.assoc_type for rows this tool
# creates, so pre-links can be audited and rolled back without guessing.
#
# assoc_type is an OpenID 1.0 vestige unused by the OIDC path, and Galaxy sets
# it the same way for its own migrated rows: migrations/data_fixes/custos_to_psa.py
# stamps CUSTOS_ASSOC_TYPE = "custos_migrated". Column is VARCHAR(64).
PRELINK_ASSOC_TYPE = "prelinked"


def provider_name_to_backend(provider: str):
    """
    Translate a config/CLI provider name into the backend name Galaxy stores.

    'google' -> 'google-openidconnect', 'keycloak' -> 'keycloak', etc.
    Returns None for an unknown name. Mirrors provider_name_to_backend() in
    lib/galaxy/authnz/util.py.
    """
    if provider is None:
        return None
    return BACKENDS_NAME.get(provider.strip().lower())


def backend_to_provider_name(backend: str):
    """Inverse of provider_name_to_backend(); None if the backend is unknown."""
    if backend is None:
        return None
    backend = backend.strip()
    for short, full in BACKENDS_NAME.items():
        if full == backend:
            return short
    return None


def known_backend_names():
    """Every backend name Galaxy 26.1 can write to oidc_user_authnz_tokens."""
    return set(BACKENDS_NAME.values())


# ---------------------------------------------------------------------------
# Password hashing (mirrors lib/galaxy/security/passwords.py)
# ---------------------------------------------------------------------------

SALT_LENGTH = 12
KEY_LENGTH = 24
HASH_FUNCTION = "sha256"
COST_FACTOR = 100000


def hash_password_pbkdf2(password: str) -> str:
    """
    Produce a password hash in Galaxy's stored format.

    Byte-for-byte compatible with galaxy.security.passwords.hash_password_PBKDF2:
    the salt fed to PBKDF2 is the *base64 text* of the random bytes, not the raw
    bytes, and that same text is what goes into the stored string.
    """
    salt = b64encode(urandom(SALT_LENGTH))
    hashed = hashlib.pbkdf2_hmac(HASH_FUNCTION, password.encode("utf-8"), salt, COST_FACTOR, KEY_LENGTH)
    return f"PBKDF2${HASH_FUNCTION}${COST_FACTOR}${salt.decode('ascii')}${b64encode(hashed).decode('ascii')}"


def unusable_password_hash() -> str:
    """
    A well-formed PBKDF2 hash of a random secret that is discarded immediately.

    This is what makes a password genuinely unusable. Setting galaxy_user.external
    alone does NOT: `external` is checked only in the web login controller
    (lib/galaxy/webapps/galaxy/controllers/user.py:176) and never on the API path
    (/api/authenticate/baseauth -> AuthManager.check_password -> LocalDB), so the
    old password keeps minting API keys. Overwriting the hash closes that.
    """
    return hash_password_pbkdf2(b64encode(urandom(48)).decode("ascii"))


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

DEFAULT_SQLITE_PATH = os.path.expanduser("~/galaxy/database/universe.sqlite")


class GalaxyDBError(Exception):
    """Raised for connection/configuration problems worth reporting cleanly."""


class GalaxyDB:
    """
    A thin DB-API wrapper over a Galaxy database, SQLite or PostgreSQL.

    Write all SQL with `?` placeholders and plain `lower(...)` for
    case-insensitive comparison; both work on either backend once placeholders
    are translated. Do not use COLLATE NOCASE or GROUP_CONCAT.
    """

    def __init__(self, url: str):
        self.url = url
        self.scheme = urlparse(url).scheme.split("+")[0].lower()
        if self.scheme in ("postgresql", "postgres"):
            self.dialect = "postgresql"
        elif self.scheme == "sqlite":
            self.dialect = "sqlite"
        else:
            raise GalaxyDBError(
                f"Unsupported database URL scheme '{self.scheme}'. "
                "Expected a sqlite:/// or postgresql:// URL."
            )
        self._conn = None

    # -- construction -------------------------------------------------------

    @staticmethod
    def sqlite_url_from_path(db_path: str) -> str:
        return "sqlite:///" + os.path.abspath(os.path.expanduser(db_path))

    @staticmethod
    def url_from_galaxy_config(config_path: str):
        """
        Read `galaxy.database_connection` out of a galaxy.yml.

        Returns None if the key is absent, which is how Galaxy itself signals
        "fall back to the default SQLite database".
        """
        try:
            import yaml
        except ImportError:
            raise GalaxyDBError(
                "Reading --galaxy-config requires PyYAML. Install it with: pip install PyYAML"
            )
        path = Path(os.path.expanduser(config_path))
        if not path.exists():
            raise GalaxyDBError(f"Galaxy config not found: {path}")
        with path.open() as handle:
            config = yaml.safe_load(handle) or {}
        return (config.get("galaxy") or {}).get("database_connection")

    @staticmethod
    def default_sqlite_path_for_config(config_path: str) -> str:
        """
        The SQLite file Galaxy itself uses when database_connection is unset:
        <data_dir>/universe.sqlite, with data_dir defaulting to "database"
        relative to the Galaxy root (the parent of the config/ directory).
        """
        import yaml  # url_from_galaxy_config has already checked it is installed

        path = Path(os.path.expanduser(config_path)).resolve()
        with path.open() as handle:
            config = yaml.safe_load(handle) or {}
        data_dir = (config.get("galaxy") or {}).get("data_dir") or "database"
        root = path.parent.parent if path.parent.name == "config" else path.parent
        return str(root / os.path.expanduser(data_dir) / "universe.sqlite")

    @classmethod
    def resolve(cls, db_url=None, galaxy_config=None, db_path=None):
        """
        Work out which database to use, in documented precedence order:

          1. --db-url
          2. --galaxy-config's galaxy.database_connection
          3. --db-path (a SQLite file)
          4. --galaxy-config with no database_connection: that Galaxy's own
             default, <galaxy root>/<data_dir>/universe.sqlite
          5. GALAXY_DB_URL
          6. GALAXY_DB_PATH
          7. ~/galaxy/database/universe.sqlite

        Every flag the operator passed explicitly outranks the environment. An
        ambient GALAXY_DB_URL silently overriding an explicit --galaxy-config
        would point a destructive run (--convert-all --invalidate-password) at
        the wrong database while echoing the operator's own argument back.
        """
        if db_url:
            return cls(db_url)
        config_default = None
        if galaxy_config:
            from_config = cls.url_from_galaxy_config(galaxy_config)
            if from_config:
                return cls(from_config)
            # Galaxy with no database_connection set uses its default SQLite file.
            config_default = cls.default_sqlite_path_for_config(galaxy_config)
        if db_path:
            path = os.path.expanduser(db_path)
            if not Path(path).exists():
                raise GalaxyDBError(f"Database not found at {path}")
            return cls(cls.sqlite_url_from_path(path))
        if config_default:
            if not Path(config_default).exists():
                raise GalaxyDBError(f"Database not found at {config_default}")
            # Announced only once this is the database actually chosen: --db-path
            # outranks it, and naming a database the run will not touch is how an
            # operator ends up trusting a destructive run against the wrong one.
            print(
                f"Note: {galaxy_config} sets no galaxy.database_connection; "
                f"using Galaxy's default SQLite database at {config_default}."
            )
            return cls(cls.sqlite_url_from_path(config_default))
        env_url = os.environ.get("GALAXY_DB_URL")
        if env_url:
            return cls(env_url)
        path = os.environ.get("GALAXY_DB_PATH") or DEFAULT_SQLITE_PATH
        path = os.path.expanduser(path)
        if not Path(path).exists():
            raise GalaxyDBError(
                f"Database not found at {path}.\n"
                "  Point at a real database with one of:\n"
                "    --db-url postgresql://user:pass@host/galaxy\n"
                "    --galaxy-config /srv/galaxy/config/galaxy.yml\n"
                "    --db-path /path/to/universe.sqlite"
            )
        return cls(cls.sqlite_url_from_path(path))

    # -- connection ---------------------------------------------------------

    def connect(self):
        if self._conn is not None:
            return self._conn
        if self.dialect == "sqlite":
            import sqlite3

            path = self.url[len("sqlite:///"):] if self.url.startswith("sqlite:///") else self.url
            if not path or path == ":memory:":
                raise GalaxyDBError("An in-memory SQLite database has no Galaxy schema to act on.")
            if not Path(path).exists():
                raise GalaxyDBError(f"Database not found at {path}")
            self._conn = sqlite3.connect(path)
        else:
            driver = None
            try:
                import psycopg2  # noqa: F401

                driver = "psycopg2"
            except ImportError:
                try:
                    import psycopg  # noqa: F401

                    driver = "psycopg"
                except ImportError:
                    pass
            if driver is None:
                raise GalaxyDBError(
                    "PostgreSQL support needs a driver. Install one with:\n"
                    "    pip install 'psycopg[binary]'      # or: pip install psycopg2-binary"
                )
            # SQLAlchemy-style URLs carry a +driver suffix that DB-API drivers
            # don't understand; strip it before connecting.
            dsn = re.sub(r"^postgresql\+\w+://", "postgresql://", self.url)
            dsn = re.sub(r"^postgres\+\w+://", "postgresql://", dsn)
            if driver == "psycopg2":
                import psycopg2

                self._conn = psycopg2.connect(dsn)
            else:
                import psycopg

                self._conn = psycopg.connect(dsn)
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        # Roll back rather than commit on the way out of a failed block; callers
        # commit explicitly when a unit of work has actually succeeded.
        if self._conn is not None and exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()
        return False

    # -- queries ------------------------------------------------------------

    def _sql(self, sql: str) -> str:
        """Translate the `?` placeholders used throughout this repo to the dialect's style."""
        if self.dialect == "postgresql":
            return sql.replace("?", "%s")
        return sql

    def query(self, sql: str, params=()):
        """Run a SELECT and return a list of dicts (backend-independent rows)."""
        cursor = self.connect().cursor()
        try:
            cursor.execute(self._sql(sql), tuple(params))
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def execute(self, sql: str, params=()):
        """Run an INSERT/UPDATE/DELETE and return the affected row count."""
        cursor = self.connect().cursor()
        try:
            cursor.execute(self._sql(sql), tuple(params))
            return cursor.rowcount
        finally:
            cursor.close()

    def commit(self):
        if self._conn is not None:
            self._conn.commit()

    def rollback(self):
        if self._conn is not None:
            self._conn.rollback()

    def table_exists(self, table: str) -> bool:
        """
        Does this table exist on the connection's own search path?

        Deliberately NOT hardcoded to `public`: Galaxy can be installed into a
        named schema, and a false negative here is dangerous rather than merely
        wrong -- `--rotate-api-keys` would silently skip while
        `--invalidate-password` still destroyed the credential. `to_regclass`
        with a bare name resolves through search_path, matching how every other
        query in this module reaches its tables.
        """
        if self.dialect == "sqlite":
            rows = self.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
            return bool(rows)
        rows = self.query("SELECT to_regclass(?) AS reg", (table,))
        return bool(rows and rows[0].get("reg"))

    def column_exists(self, table: str, column: str) -> bool:
        """Companion to table_exists(); resolves through search_path the same way."""
        if self.dialect == "sqlite":
            rows = self.query(f"PRAGMA table_info({table})")
            return any(r.get("name") == column for r in rows)
        # to_regclass resolves the table via search_path; ::regclass::oid then
        # scopes the column lookup to that exact relation, so this agrees with
        # table_exists() instead of matching a same-named table in any schema.
        rows = self.query(
            """
            SELECT 1 AS present
            FROM pg_attribute
            WHERE attrelid = to_regclass(?)
              AND attname = ?
              AND attnum > 0
              AND NOT attisdropped
            """,
            (table, column),
        )
        return bool(rows)

    def describe(self) -> str:
        """A safe one-line description of the target, with any password redacted."""
        if self.dialect == "sqlite":
            return self.url
        parsed = urlparse(self.url)
        host = parsed.hostname or "localhost"
        port = f":{parsed.port}" if parsed.port else ""
        user = f"{parsed.username}@" if parsed.username else ""
        return f"{self.dialect}://{user}{host}{port}{parsed.path}"


def add_db_arguments(parser):
    """Register the standard database-selection flags on an argparse parser."""
    group = parser.add_argument_group("database selection")
    group.add_argument("--db-url", help="SQLAlchemy-style URL, e.g. postgresql://user:pass@host/galaxy")
    group.add_argument("--galaxy-config", help="Path to galaxy.yml; reads galaxy.database_connection from it")
    group.add_argument("--db-path", help=f"Path to a SQLite Galaxy database (default: {DEFAULT_SQLITE_PATH})")
    return group


def open_db(args) -> GalaxyDB:
    """Resolve and open the database described by the standard flags, or exit cleanly."""
    try:
        db = GalaxyDB.resolve(
            db_url=getattr(args, "db_url", None),
            galaxy_config=getattr(args, "galaxy_config", None),
            db_path=getattr(args, "db_path", None),
        )
        db.connect()
        return db
    except GalaxyDBError as e:
        print(f"Error: {e}")
        sys.exit(1)
