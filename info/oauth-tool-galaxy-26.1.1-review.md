# OAuth Conversion Tool Review vs. Galaxy 26.1.1

Date: 2026-09-24 (supersedes the 2026-09-17 revision)

Everything below was verified against `galaxyproject/galaxy` at tag/branch `release_26.1`
by reading the source, not from memory. Source citations are `path:line` in that tree.

## Scope note: usegalaxy-tools is the wrong repo for this

`galaxyproject/usegalaxy-tools` maintains the *toolset* for usegalaxy.org — curated
Tool Shed YAML plus `.lock` files, CI, and per-instance directories. It contains no
authentication, OIDC, user-management or credential code, and publishes no releases
(2,532 commits on `main`, no tags). Nothing in it bears on user auth migration.

The repos that actually govern this work are:

| Concern | Where |
|---|---|
| Auth/OIDC implementation, DB schema, migrations | `galaxyproject/galaxy` (`lib/galaxy/authnz/`, `lib/galaxy/model/`) |
| Config keys (`disable_local_accounts`, `enable_oidc`, …) | `galaxyproject/galaxy` `lib/galaxy/config/schemas/config_schema.yml` |
| Deployed OIDC config for the public instances | `galaxyproject/infrastructure-playbook` |

Future release checks should track `galaxyproject/galaxy` releases only.

## What the local tool does

`search-and-edit-gal/convert_users_to_oauth.py` batch-converts Galaxy users from
password auth to OAuth by inserting rows directly into `oidc_user_authnz_tokens`,
pre-linking `user_id` ↔ `provider`/`uid` with the user's email as the UID. It supports
list/preview/convert/convert-all/remove, dedups, detects UID conflicts, gates
non-email-UID providers behind `--confirm-non-email-uid`, and can set `external=True`
to disable password login.

## Galaxy 26.1.1 release check

- **v26.1.1** — confirmed via the release API: four PRs (#23249, #23251, #23206,
  #23254), all npm/PyPI packaging plus a duplicate-private-roles fix. No auth or
  OIDC changes. Nothing to pull in.
- **v26.1.0** — the release 26.1.1 patches; carries the auth-relevant items
  (case-insensitive user email, Auth0 support, the Keycloak IdP hint fix,
  configurable OIDC activation).
- **v26.0.0** — "Reimplement keycloak/cilogon as PSA providers and remove custos".

---

## BLOCKER: the `provider` value written by the tool never matches

**Every Google, Okta and Azure pre-link this tool has written is dead.** Galaxy does
not store the short provider name from `oidc_backends_config.xml` in
`oidc_user_authnz_tokens.provider`; it stores the *python-social-auth backend name*.

Verified chain:

1. `lib/galaxy/authnz/psa_authnz.py:94-110` — `BACKENDS_NAME` maps the config name to
   the backend name:

   | config `name=` | stored `provider` |
   |---|---|
   | `google` | `google-openidconnect` |
   | `okta` | `okta-openidconnect` |
   | `azure` | `azuread-v2-tenant-oauth2` |
   | `keycloak`, `cilogon`, `auth0`, `oidc`, `globus`, `tapis` | unchanged |

2. `social_core/pipeline/social_auth.py` `social_user()` — `provider = backend.name`,
   then `storage.user.get_social_auth(provider, uid)`.
3. `lib/galaxy/model/__init__.py:11714-11721` — `get_social_auth()` is
   `filter_by(provider=provider, uid=uid)`: an **exact, case-sensitive** match on both
   columns.
4. Galaxy's own writes agree: `psa_authnz.py:551` creates the token with
   `provider=BACKENDS_NAME[self.config["provider"]]`, and `psa_authnz.py:482` reads it
   back the same way.
5. Corroborating evidence from our own instance: the "Current User Status" table in
   `OAUTH_CONVERSION_GUIDE.md` records the one genuinely-linked user as
   `google-openidconnect` — while the tool inserts `google`.

**Fix.** Keep the friendly short names on the CLI, translate before the INSERT.
Galaxy exports exactly this helper — mirror it rather than hand-rolling a second map:

```python
# lib/galaxy/authnz/util.py
def provider_name_to_backend(provider):
    for k, v in BACKENDS_NAME.items():
        if k.lower() == provider:
            return v
    return None
```

Then `--list*` should display both values, and a `--verify` mode should flag any
existing row whose `provider` is a config short name rather than a backend name, so
the rows already written can be found and repaired.

---

## Corrections to the previous revision of this review

### 1. The case-insensitivity claim was half right

PR #21885 made **`galaxy_user.email`** comparison case-insensitive — confirmed at
`model/__init__.py:11710` (`func.lower(User.email) == email.lower()`) and
`psa_authnz.py:1020`. It did **not** touch the `uid` lookup:
`get_social_auth()` is still an exact match, and the table carries
`UniqueConstraint("provider", "uid")` (`model/__init__.py:11592`).

So of the two `COLLATE NOCASE` changes made last revision:

- On the `galaxy_user` lookup (`--convert`/`--remove`): **correct, keep it.**
- On the UID-conflict check: harmless (it over-detects, which is the safe direction)
  but it must not be described as "matching Galaxy's behaviour" — Galaxy will not
  make that match at login.

The real requirement is that `uid` be stored in the exact case the IdP sends. Galaxy
defaults `FORCE_EMAIL_LOWERCASE` to `True` (`psa_authnz.py:186`), so the tool should
insert `email.lower()` as the uid, not the raw `galaxy_user.email` spelling.

### 2. `<kc_idp_hint>` is not a real config element — and it breaks Galaxy startup

The element added to `configure_oidc_provider.sh`'s Keycloak flow does not exist.

- The XSD (`lib/galaxy/authnz/xsd/oidc_backends_config.xsd`) declares **`<idphint>`**
  ("Hint to preselect the IdP during authentication"). There is no `kc_idp_hint`
  element.
- `psa_authnz.py:249` reads `oidc_backend_config.get("idphint")` into `IDPHINT`.
- `lib/galaxy/authnz/keycloak.py` `auth_params()` is what turns that into the
  `kc_idp_hint` **query parameter** sent to Keycloak — and it defaults to `"oidc"`
  when unset. `kc_idp_hint` was always the wire parameter, never the config key.

This is worse than a no-op. `_parse_oidc_backends_config` validates the file against
the XSD (`managers.py:129-130`), and `galaxy.util.parse_xml` calls
`schema.assertValid(tree)` (`util/__init__.py:424`), which raises. The provider's
content model is `<xs:all>`, so an undeclared child element is invalid: writing
`<kc_idp_hint>` produces a config Galaxy refuses to parse.

**Fix:** emit `<idphint>`; drop the "requires Galaxy >= 26.1.0" wording from the
prompt (the element is accepted by older releases too — #21992 fixed the plumbing
downstream of it).

### 3. The Auth0 flow writes `<url>`; Auth0 requires `<domain>`

`GalaxyAuth0OpenIdConnect(GalaxyOpenIdConnect, Auth0OpenIdConnectAuth)`
(`lib/galaxy/authnz/auth0.py`). Neither Galaxy class overrides `oidc_endpoint()`, so
resolution lands on `Auth0OpenIdConnectAuth.oidc_endpoint()` → `api_path()` →
`normalized_domain()` → `self.setting("DOMAIN")`, which raises
`AuthMissingParameter(DOMAIN)` when unset. `_setup_idp` populates `DOMAIN` from the
`<domain>` element (`psa_authnz.py:244`), and the XSD documents `<domain>` as
"Provider domain (e.g. for Auth0)".

`<url>` is schema-valid, so this one passes startup and fails at first login instead.

**Fix:** write `<domain>your-tenant.us.auth0.com</domain>` (bare host, no scheme —
`normalized_domain()` strips one, but bare is what the XSD documents), not `<url>`.

### 4. The open question is now answered: the custos table **is** dropped

Last revision deferred this. It is settled by
`lib/galaxy/model/migrations/alembic/versions_gxy/724237cc4cf0_migrate_custos_to_psa_tokens.py`:

```python
def upgrade():
    with transaction():
        connection = op.get_bind()
        migrate_custos_tokens_to_psa(connection)
        drop_table(CUSTOS_TABLE)
```

So on any instance upgraded to ≥26.0: existing Custos tokens are copied into
`oidc_user_authnz_tokens` (stamped `assoc_type='custos_migrated'`, per
`migrations/data_fixes/custos_to_psa.py`) and `custos_authnz_token` is **dropped**.
`downgrade()` recreates and restores it.

Actions now unblocked:

- `search_galaxy_users.py:211` — the `custos_authnz_token` probe can stay only as a
  `<26.0` compatibility path; say so in the comment instead of "Table doesn't exist in
  this Galaxy version".
- `GALAXY_USER_DATABASE_REFERENCE.md:329` — mark the table **removed in 26.0**, and
  drop the dead `lib/galaxy/authnz/custos_authnz.py` row from the Key Files table
  (that module no longer exists). The `UserAuthnzToken` line number is also stale:
  it is `model/__init__.py:11590` in 26.1, not 10549.
- `HOW_TO_SEARCH_GALAXY_USERS.md:146` — same qualification.
- `assoc_type='custos_migrated'` is a useful precedent: see improvement #4 below.

---

## New findings

### 5. `--disable-password` does not actually disable password authentication

`external=True` is checked in exactly one place — the web login controller,
`lib/galaxy/webapps/galaxy/controllers/user.py:176`. It is **not** checked on the
API path:

```
GET /api/authenticate/baseauth
  → services/authenticate.py  AuthenticationService.get_api_key()
  → auth/__init__.py:74       AuthManager.check_password()
  → auth/providers/localdb.py LocalDB.authenticate_user()
  → user.check_password()
```

There is no reference to `external` anywhere in `lib/galaxy/auth/`. After a
"conversion with password disabled", anyone holding a user's old Galaxy password can
still mint a working API key over basic auth.

`disable_local_accounts` (the config-level switch) has the same gap in 26.1: I found
it enforced only at `api/users.py:827` (hides profile editing) and in
`config/__init__.py:1169-1176` (forces `allow_local_account_creation=False`). Neither
reaches `check_password`.

**The only reliable way to retire a password is to invalidate the credential**, e.g.
`user.set_random_password()` equivalent — overwrite the hash — alongside setting
`external=1`. This should be an explicit, separately-flagged, loudly-warned operation
(`--invalidate-password`), because it is irreversible and locks the user out entirely
if their OIDC link is wrong.

### 6. Existing API keys survive conversion, silently

`OAUTH_CONVERSION_GUIDE.md` lists "API keys" under *preserved data* as a benefit. For
an SSO migration it is a hole: pre-existing API keys keep working regardless of the
user's status at the IdP, so offboarding at the IdP does not revoke Galaxy access.
Add a `--rotate-api-keys` option and say plainly in the guide that keys outlive the
migration unless rotated.

### 7. Galaxy 26.1 has a supported path that makes bulk pre-linking mostly unnecessary

This is the most important architectural point, and it reframes the whole tool.

`AUTH_PIPELINE` (`psa_authnz.py:111`) includes Galaxy's own
`associate_by_email_if_logged_in` (`psa_authnz.py:985`), which replaces social-core's
`associate_by_email`. Its behaviour on first OIDC login by a user who already has a
Galaxy account with the same email:

- **`fixed_delegated_auth` ON** → auto-associates the OIDC identity with the
  existing account, matched case-insensitively on email (`psa_authnz.py:1034-1038`).
- **`fixed_delegated_auth` OFF** → redirects to
  `login/start?connect_external_provider=…`, prompting the user to log in once and
  link.

Both paths write the association through Galaxy, with the correct backend name and
the IdP's real subject/uid. That is strictly better than pre-linking, which:

- must guess the uid ahead of time, and that guess cannot be made correctly for
  **any** provider on a modern social-core: Galaxy loads Google as
  `google-openidconnect`, whose `ID_KEY` resolves to `sub` (verified against
  social-core 4.8.3 — `GoogleOAuth2.ID_KEY = "email"`, cited in an earlier
  revision of this review, no longer applies to the backend Galaxy uses);
  generic OIDC/Okta/Keycloak also use `sub`, Azure V2 `preferred_username`, and
- is what produced the provider-name bug above.

**`fixed_delegated_auth` is computed, not configured.** There is no such key in
galaxy.yml — a full-tree grep of v26.1.1 finds exactly one assignment
(`lib/galaxy/app/__init__.py:973`):

```python
self.config.fixed_delegated_auth = (
    len(list(self.config.oidc)) == 1 and len(list(self.auth_manager.authenticators)) == 0
)
```

It turns on when exactly one OIDC provider is configured **and** zero authenticators
are loaded from `auth_config_file`. Galaxy's default is one `localdb` authenticator
(`AUTH_CONF_XML` in `lib/galaxy/auth/util.py`), so condition two needs an explicit
`auth_conf.xml` containing `<auth></auth>`. Galaxy's own integration tests configure
it exactly this way (`test/integration/oidc/auth_conf_empty.xml`).

**That same empty file also closes the `baseauth` hole from #5.** With zero
authenticators, `AuthManager.check_password` iterates nothing and returns `False`, so
password authentication fails on *every* path including
`/api/authenticate/baseauth`. It is the only mechanism in 26.1 that does — neither
`external` nor `disable_local_accounts` reaches the password check.

**Recommendation: invert the tool's default.** Make the primary documented migration
one OIDC provider + `prefer_oidc_login`, then an empty `auth_conf.xml` plus
`disable_local_accounts` at cutover, and demote bulk pre-linking to a fallback for
the cases the pipeline cannot cover. The tool's highest-value role then becomes
*audit and verification*, not bulk INSERT — see #1 below.

Related knobs worth documenting, all per-provider in `oidc_backends_config.xml`:
`require_user_activation` (this is the 26.1.0 activation setting the last revision
referred to — it is a provider element, not a global), `require_create_confirmation`,
`require_session_refresh`, `pkce_support`.

### 8. SQLite-only — the tool cannot run where it matters

Both scripts hardcode `sqlite3` and default to `~/galaxy/database/universe.sqlite`.
Every production Galaxy, galaxtrakr included, runs PostgreSQL. Beyond the driver:

- `COLLATE NOCASE` is SQLite-specific; it is a **syntax error** on PostgreSQL. The
  fix applied last revision therefore cannot run against a real instance.
- `GROUP_CONCAT` in `list_all_users` is SQLite; PostgreSQL needs `string_agg`.

**Fix:** take a SQLAlchemy URL (`GALAXY_DB_URL`, or read `database_connection` out of
`galaxy.yml`), and use `func.lower(...)` instead of `COLLATE NOCASE` so one code path
serves both backends.

---

## Suggested improvements, in priority order

1. **Fix the provider name, then add `--verify`.** Translate via `BACKENDS_NAME`
   before every INSERT and every lookup. Then add a read-only `--verify` that reports,
   per row: provider is a known backend name; uid case matches what the IdP will send;
   no duplicate `(provider, uid)`; the user is not deleted/purged. Run it against the
   current DB first — it will identify the rows that need repairing.
2. **Move to SQLAlchemy + PostgreSQL** (#8). Without this nothing here reaches production.
3. **Re-document the migration around `fixed_delegated_auth`** (#7), with pre-linking
   as the fallback rather than the headline.
4. **Stamp `assoc_type`** on rows the tool creates (e.g. `'prelinked'`). Galaxy sets
   `assoc_type='custos_migrated'` on its own migrated rows, so the column is safe to
   use this way — it is an OpenID 1.0 vestige unused by the OIDC path. This gives a
   precise `--remove-all-prelinked` rollback and a clean audit query, which the tool
   currently has no way to express (it cannot distinguish its own rows from real ones).
5. **Split `--disable-password` into honest flags** (#5): `--mark-external` (current
   behaviour, web login only) and `--invalidate-password` (overwrite the hash), with
   the API-key gap stated in `--help` and in the guide.
6. **Add `--rotate-api-keys`** (#6).
7. **Make `--convert-all` resumable.** It commits once at the end, so any unexpected
   error mid-run discards the whole batch. Commit per user, or catch `IntegrityError`
   per user and continue with a tally.
8. **Fix `setup_galaxy_oidc.sh`'s galaxy.yml handling.** It copies
   `galaxy.yml.sample` (which defines `galaxy:` at line 381) then **appends a second
   top-level `galaxy:` block**. YAML keeps the last — confirmed:
   `yaml.safe_load("galaxy:\n  a: 1\ngalaxy:\n  c: 3\n")` → `{'galaxy': {'c': 3}}`.
   Every setting the admin uncommented in the sample is silently discarded. Merge into
   the existing key instead. While there: `allow_user_creation` is a deprecated alias
   for `allow_local_account_creation` in 26.1, and `VERIFY_SSL=False` should not be
   the shipped default outside localhost.
9. **Add a test fixture.** Everything above is a schema/contract bug that a single
   test — build a throwaway DB from Galaxy's own models, insert via the tool, assert
   `UserAuthnzToken.get_social_auth(backend_name, uid)` returns the row — would have
   caught. That assertion is the tool's real contract; it should be pinned in CI.

## Still unverified

- Whether the Galaxy **client** hides the username/password form under
  `disable_local_accounts`. Only the Python paths were checked; the backend password
  check is reachable regardless (#5), so this is cosmetic either way.
- Behaviour on an instance upgraded 25.x → 26.1 with live Custos rows. The migration
  logic is clear, but no upgraded database was available to confirm the row counts
  and `assoc_type` stamping in practice.
- Side effects of running with zero authenticators beyond password login — e.g. what
  `check_registration_allowed` does with an empty authenticator list. Pair the empty
  `auth_conf.xml` with `disable_local_accounts: true` rather than relying on it to
  suppress registration.
- The changes below were verified against a synthetic Galaxy-shaped SQLite database,
  not a live PostgreSQL instance. The PostgreSQL path is exercised only at the SQL
  level (no `COLLATE NOCASE`/`GROUP_CONCAT`, placeholder translation); it needs one
  read-only `--verify` run against a real instance before any write.

---

## Implementation status (2026-09-24)

All nine improvements are implemented on branch
`feat/oauth-tool-galaxy-26.1-updates`. `python3 search-and-edit-gal/test_convert_users_to_oauth.py`
runs 42 stdlib-only tests, all passing; each fix below has a regression test, and
every test was mutation-checked by reintroducing the original bug.

| # | Item | Status |
|---|---|---|
| 1 | Provider-name translation + `--verify` audit | Done — `galaxy_db.provider_name_to_backend()`, `--verify` |
| 2 | SQLAlchemy/PostgreSQL support | Done — `galaxy_db.GalaxyDB` (DB-API; no new hard dependency) |
| 3 | Re-document around the association pipeline | Done — `OAUTH_CONVERSION_GUIDE.md` rewritten |
| 4 | Stamp `assoc_type` | Done — `'prelinked'` + `--remove-all-prelinked` |
| 5 | Split `--disable-password` | Done — `--mark-external` / `--invalidate-password`, alias warns |
| 6 | `--rotate-api-keys` | Done |
| 7 | Resumable `--convert-all` | Done — commits per user |
| 8 | `setup_galaxy_oidc.sh` galaxy.yml handling | Done — merges into the existing mapping, idempotent, verified |
| 9 | Test fixture | Done — `test_convert_users_to_oauth.py` |

Plus the three config corrections: `<idphint>` (was `<kc_idp_hint>`), Auth0
`<domain>` (was `<url>`), and `VERIFY_SSL` defaulting to `True`. All emitted
`<provider>` fragments were checked against the real XSD element set; every one is
now schema-valid.
