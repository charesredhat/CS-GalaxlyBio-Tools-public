# Galaxy SSO Migration Guide (Galaxy 26.1)

**Updated:** 2026-09-24
**Verified against:** `galaxyproject/galaxy` v26.1.1 (source read, not recalled)

How to move an existing Galaxy instance from password logins to OIDC single
sign-on without losing user data.

---

## Read this first

**Prefer Galaxy's own account association over bulk database pre-linking.**

Earlier versions of this guide recommended inserting rows into
`oidc_user_authnz_tokens` for every user. That approach is fragile, and the rows
it wrote were wrong in a way that silently prevented login (see
[Appendix A](#appendix-a-what-was-broken-before)). Galaxy 26.1 can do the
association itself, correctly, with no database writes at all.

| | Galaxy's association pipeline | Bulk DB pre-linking |
|---|---|---|
| Writes the right `provider` value | Always | Only if the tool translates it |
| Knows the user's real IdP subject | Yes — reads it from the token | No — has to guess |
| Works for non-email-keyed IdPs | Yes | No — and no stock backend is email-keyed any more |
| Requires DB access | No | Yes |
| Can strand users on a bad row | No | Yes |

Use this tool to **audit** (`--verify`) and to handle exceptions. Use the
configuration below as the actual migration.

---

## Part 1 — The recommended migration

### How Galaxy associates accounts

`AUTH_PIPELINE` (`lib/galaxy/authnz/psa_authnz.py:111`) includes Galaxy's own
`associate_by_email_if_logged_in` step (`psa_authnz.py:985`), which replaces
social-core's `associate_by_email`. When someone logs in via the IdP and a Galaxy
account already exists with the same email:

- **`fixed_delegated_auth` ON** → Galaxy auto-associates the IdP identity with
  that account, matched case-insensitively on email (`psa_authnz.py:1034-1038`).
  The user simply lands in their existing account.
- **`fixed_delegated_auth` OFF** → Galaxy redirects to
  `login/start?connect_external_provider=…`, asking the user to log in once and
  link the accounts themselves.

Either way Galaxy writes the association row itself, with the correct backend
name and the IdP's real subject identifier.

### `fixed_delegated_auth` is computed, not configured

There is **no `fixed_delegated_auth:` key in galaxy.yml.** It is derived at
startup (`lib/galaxy/app/__init__.py:973`):

```python
self.config.fixed_delegated_auth = (
    len(list(self.config.oidc)) == 1 and len(list(self.auth_manager.authenticators)) == 0
)
```

It becomes `True` when **both** hold:

1. exactly **one** `<provider>` is configured in `oidc_backends_config.xml`, and
2. **zero** authenticators are loaded from `auth_config_file`.

By default Galaxy loads one `localdb` authenticator (`AUTH_CONF_XML` in
`lib/galaxy/auth/util.py`), so condition 2 requires an explicit `auth_conf.xml`
containing an empty `<auth></auth>`.

### The empty `auth_conf.xml` is also what retires passwords

This is the key point, and it is the only mechanism that fully works:

```
AuthManager.check_password()            lib/galaxy/auth/__init__.py:74
  for provider, options in self.active_authenticators(...):
      ...
  return False                          # no authenticators -> always False
```

With zero authenticators, **every** password check fails — the web login form
*and* `/api/authenticate/baseauth`. Nothing else in Galaxy 26.1 closes both:

| Mechanism | Web login form | `/api/authenticate/baseauth` |
|---|---|---|
| `galaxy_user.external = true` | Blocked (`controllers/user.py:176`) | **Still works** |
| `disable_local_accounts: true` | Hides UI / blocks registration | **Still works** |
| Empty `auth_conf.xml` | Blocked | Blocked |

API keys bypass password authentication entirely and are unaffected by all
three — rotate them separately.

### Steps

```bash
# 1. Configure exactly ONE provider.
./configure_oidc_provider.sh

# 2. Audit before changing anything. Read-only; exits non-zero if broken rows exist.
python3 search-and-edit-gal/convert_users_to_oauth.py --verify \
    --galaxy-config /srv/galaxy/config/galaxy.yml
```

Fix any mismatch between Galaxy emails and IdP emails **before** cutover — that
match is what the whole migration rests on.

```bash
# 3. Soft launch: leave passwords working, let users sign in via the IdP.
#    In galaxy.yml:
#      enable_oidc: true
#      prefer_oidc_login: true
```

At this stage each OIDC login associates that user's account automatically. Watch
how many users have linked:

```bash
python3 search-and-edit-gal/convert_users_to_oauth.py --list-linked --galaxy-config ...
```

```bash
# 4. Cut over, once enough users have linked AND an admin can log in via the IdP.
#    setup_galaxy_oidc.sh writes config/auth_conf.xml.sso-template for this.
mv config/auth_conf.xml.sso-template config/auth_conf.xml
#    In galaxy.yml:
#      auth_config_file: auth_conf.xml
#      disable_local_accounts: true

# 5. Rotate API keys — they survive everything above.
python3 search-and-edit-gal/convert_users_to_oauth.py --convert-all \
    --provider <name> --rotate-api-keys --galaxy-config ...
```

> **Lockout warning.** Activating `auth_conf.xml` disables password login for
> *everyone*, including admins. Confirm at least one admin account authenticates
> through the IdP first. To roll back, remove `auth_config_file` from galaxy.yml
> and restart — the default `localdb` authenticator returns.

---

## Part 2 — Pre-linking (the fallback)

Use this only where Part 1 cannot reach: an IdP that will not send a matching
email, an account whose email must change as part of the move, or a scripted
bulk migration you must complete before users next log in.

### What a correct row looks like

```sql
INSERT INTO oidc_user_authnz_tokens (user_id, uid, provider, extra_data, assoc_type)
VALUES (
    42,                       -- existing galaxy_user.id
    'user@example.com',       -- the identifier the IdP will send, lowercased
    'google-openidconnect',   -- the BACKEND name, not 'google'
    '{}',                     -- tokens are filled in on first real login
    'prelinked'               -- marker so this row can be audited and rolled back
);
```

### The provider column takes the backend name

Galaxy stores the python-social-auth **backend** name, not the `name=` attribute
from `oidc_backends_config.xml`:

| `oidc_backends_config.xml` | stored in `provider` |
|---|---|
| `google` | `google-openidconnect` |
| `okta` | `okta-openidconnect` |
| `azure` | `azuread-v2-tenant-oauth2` |
| `keycloak`, `cilogon`, `auth0`, `oidc`, `globus`, `tapis` | unchanged |

Chain: `BACKENDS_NAME` (`psa_authnz.py:94`) → Galaxy writes
`provider=BACKENDS_NAME[...]` (`psa_authnz.py:551`) → social-core's `social_user()`
looks up with `provider = backend.name` → `get_social_auth()` does an exact
`filter_by(provider=…, uid=…)` (`model/__init__.py:11714`).

The tool takes short names and translates them for you. `--list-linked` shows
both, and `--verify` flags any row storing a short name.

### The uid must match what the IdP sends

**No stock social-core backend keys identity on email — Google included.**

| Backend | uid claim |
|---|---|
| Google (`google-openidconnect`) | `sub` |
| Generic OIDC, Okta, Keycloak, CILogon, Auth0 | `sub` |
| Azure AD V2 | `preferred_username` |

Earlier versions of this guide claimed Google was the exception, on the strength
of `BaseGoogleAuth.ID_KEY = "email"`. That was true of older social-core
releases but is not true of the backend Galaxy loads. Measured in the
galaxytrakr sandbox venv (Galaxy 26.0.1.dev1, social-core 4.8.3):

```python
>>> from social_core.backends.google_openidconnect import GoogleOpenIdConnect as G
>>> G.name, G.ID_KEY
('google-openidconnect', 'sub')
>>> [c.__name__ for c in G.__mro__[:6]]
['GoogleOpenIdConnect', 'GoogleOAuth2', 'BaseGoogleOAuth2API',
 'BaseGoogleAuth', 'OpenIdConnectAuth', 'BaseOAuth2']
```

Neither `google.py` nor `google_openidconnect.py` defines `ID_KEY` any more, so
it resolves down the MRO to `OpenIdConnectAuth` (`open_id_connect.py:66`) —
`sub`. And `BACKENDS_NAME["google"]` is `google-openidconnect`, the OIDC
backend, so the email-keyed `BaseGoogleAuth` behaviour is unreachable from
Galaxy either way.

Consequence: **an email-keyed pre-link never matches for any provider**, and the
tool now refuses every one of them unless you pass `--confirm-non-email-uid`,
which you should only do for a backend you have explicitly reconfigured to key
on email and verified the way shown above. There is otherwise no uid you can
know ahead of time — use the association pipeline and let Galaxy record the real
subject.

An email-shaped uid is still stored lowercased, whatever the provider: Galaxy
lowercases the incoming email (`FORCE_EMAIL_LOWERCASE` defaults `True`,
`psa_authnz.py:186`) but compares `uid` exactly. An opaque `sub` is stored
byte-for-byte as the IdP sent it.

### Commands

```bash
# Audit first — always
python3 convert_users_to_oauth.py --verify --galaxy-config /srv/galaxy/config/galaxy.yml

# Listing
python3 convert_users_to_oauth.py --list            # all users + link status
python3 convert_users_to_oauth.py --list-unlinked   # conversion candidates
python3 convert_users_to_oauth.py --list-linked     # shows stored backend names

# Preview, then convert
python3 convert_users_to_oauth.py --preview     --provider google
python3 convert_users_to_oauth.py --convert-all --provider google --exclude admin@example.org
python3 convert_users_to_oauth.py --convert     --provider google --email user@example.org

# Retire credentials (see the table in Part 1 for what each one really does)
python3 convert_users_to_oauth.py --convert-all --provider google \
    --mark-external --invalidate-password --rotate-api-keys

# Roll back
python3 convert_users_to_oauth.py --remove --email user@example.org --provider google
python3 convert_users_to_oauth.py --remove-all-prelinked --provider google --preview-only
python3 convert_users_to_oauth.py --remove-all-prelinked --provider google
```

`--remove-all-prelinked` deletes only rows stamped `assoc_type='prelinked'`, so
associations Galaxy created itself are never touched.

Re-running `--convert-all` with credential flags also covers users who were
linked on an earlier run, so you can link first and retire credentials later as
a separate, deliberate step.

**`custos` is not a valid `--provider`.** It cannot be pre-linked on any Galaxy
version: on ≥ 26.0 the backend no longer exists, and on < 26.0 Custos stored its
tokens in `custos_authnz_token` — a different table this tool never writes. Use
`keycloak` or `cilogon`. Existing migrated rows are recognised by `--verify`.

### Database selection

Production Galaxy runs PostgreSQL. Precedence — every flag you pass explicitly
outranks the environment, so an ambient `GALAXY_DB_URL` cannot silently redirect
a destructive run:

1. `--db-url postgresql://user:pass@host/galaxy`
2. `--galaxy-config /srv/galaxy/config/galaxy.yml` (reads `galaxy.database_connection`)
3. `--db-path /path/to/universe.sqlite`
4. `--galaxy-config` whose `galaxy.yml` sets no `database_connection`: that
   Galaxy's own default, `<galaxy root>/<data_dir>/universe.sqlite` (`data_dir`
   defaults to `database`). The chosen path is printed before the run.
5. `GALAXY_DB_URL`
6. `GALAXY_DB_PATH`
7. `~/galaxy/database/universe.sqlite`

Step 4 sits below `--db-path` deliberately: a `galaxy.yml` with no
`database_connection` is a weaker statement of intent than a path you typed, so
passing both uses the path, and nothing is printed about the config's default.

PostgreSQL needs a driver: `pip install 'psycopg[binary]'` or `psycopg2-binary`.

> Take a database backup before any write operation. `--invalidate-password` is
> irreversible; affected users must go through password reset even if you later
> remove the OIDC link.

---

## Part 3 — Reference

### Credential flags

Credentials are only ever retired for a user who actually ends up with a link.
If the uid already belongs to another account, the tool refuses that user
outright and leaves their password and API keys untouched — destroying a
password for someone with no working OIDC link would lock them out permanently.
Such users are counted separately as `refused` in the summary.

| Flag | Effect | Reversible |
|---|---|---|
| `--mark-external` | Sets `galaxy_user.external`. **Web login form only.** | Yes |
| `--invalidate-password` | Overwrites the hash with PBKDF2 of a discarded random secret. | **No** |
| `--rotate-api-keys` | Marks the user's `api_keys` rows deleted. | No |
| `--disable-password` | Deprecated alias for `--mark-external`; warns. | Yes |

### Per-provider options in `oidc_backends_config.xml`

Validated against `lib/galaxy/authnz/xsd/oidc_backends_config.xsd` — an
undeclared element makes Galaxy **refuse to start**, since
`_parse_oidc_backends_config` calls `schema.assertValid()`.

| Element | Notes |
|---|---|
| `client_id`, `client_secret`, `redirect_uri` | Required |
| `idphint` | Keycloak upstream IdP hint. **Not** `kc_idp_hint` — that is the query parameter Galaxy derives from this. |
| `domain` | Auth0's endpoint comes from here; `<url>` does not work for Auth0. |
| `require_user_activation` | Requires email activation for accounts created via this provider. Per-provider, not global. |
| `require_create_confirmation` | Shows a confirmation page before creating a new account. |
| `require_session_refresh` | Forces reauthentication when a token refresh fails. |
| `pkce_support`, `accepted_audiences`, `username_key`, `label`, `icon` | Optional |

### Galaxy 26.0 removed Custos

Galaxy 26.0.0 ("Reimplement keycloak/cilogon as PSA providers and remove
custos") removed the standalone backend. Migration
`724237cc4cf0_migrate_custos_to_psa_tokens.py` copies existing tokens into
`oidc_user_authnz_tokens` (stamped `assoc_type='custos_migrated'`) and then
**drops `custos_authnz_token`**. On Galaxy ≥ 26.0 use `keycloak` or `cilogon`;
pre-linking `custos` warns and matches nothing.

---

## Troubleshooting

**A converted user cannot log in.** Run `--verify` first; it checks exactly the
things that break this:

1. `provider` holds a short name instead of a backend name — the row is dead.
2. `uid` is email-shaped but not lowercase (any provider).
3. Duplicate `(provider, uid)` — one row is unreachable.
4. The row points at a deleted or missing user.

Rows carrying a backend name from an older Galaxy release (`azuread-tenant-oauth2`,
`google-oauth2`, and similar) are reported as **INFO**, not broken. On an upgraded
instance those are working logins — do not "repair" them.

Then check the user's IdP email actually matches `galaxy_user.email`, and whether
`require_user_activation` is set for that provider.

**Galaxy will not start after editing the OIDC config.** Almost certainly an
undeclared element in `oidc_backends_config.xml`. Check the log for a schema
validation error and compare against the element table above.

**Users still authenticating with passwords after cutover.** `external` and
`disable_local_accounts` do not close `/api/authenticate/baseauth`. Activate the
empty `auth_conf.xml`, and rotate API keys.

---

## Appendix A — What was broken before

Recorded so the same mistakes are not reintroduced. Full analysis:
`info/oauth-tool-galaxy-26.1.1-review.md`.

1. **The tool wrote `provider='google'`.** Galaxy stores and matches
   `google-openidconnect`, so every Google, Okta and Azure pre-link was dead on
   arrival. Run `--verify` to find rows still in this state.
2. **`--disable-password` did not disable password authentication.** It set
   `external`, which the API path never consults.
3. **The Keycloak flow wrote `<kc_idp_hint>`**, an element the XSD does not
   declare — enough to stop Galaxy from starting.
4. **The Auth0 flow wrote `<url>`** where Auth0 requires `<domain>`, failing at
   first login.
5. **`setup_galaxy_oidc.sh` appended a second top-level `galaxy:` key**, and YAML
   keeps only the last — silently discarding every other setting in the file.
6. **SQLite-only.** The scripts could not run against a production instance, and
   the `COLLATE NOCASE` used for case-insensitive matching is a syntax error on
   PostgreSQL.

Each of these now has a regression test in
`search-and-edit-gal/test_convert_users_to_oauth.py`:

```bash
python3 search-and-edit-gal/test_convert_users_to_oauth.py
```

## Files

| File | Description |
|------|-------------|
| `galaxy_db.py` | Shared DB layer (SQLite + PostgreSQL), provider-name translation, password hashing |
| `convert_users_to_oauth.py` | Audit and conversion tool |
| `search_galaxy_users.py` | Search and list users |
| `test_convert_users_to_oauth.py` | Regression tests (stdlib only) |
| `GALAXY_USER_DATABASE_REFERENCE.md` | Database reference |
| `OAUTH_CONVERSION_GUIDE.md` | This guide |

## References

- Galaxy OIDC docs: https://docs.galaxyproject.org/en/latest/admin/authentication.html
- Python Social Auth: https://python-social-auth.readthedocs.io/
- Galaxy source: `lib/galaxy/authnz/`, `lib/galaxy/auth/`, `lib/galaxy/model/__init__.py`
