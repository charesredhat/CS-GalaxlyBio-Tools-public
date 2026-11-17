# Mock OIDC SSO on the galaxytrakr sandbox

End-to-end SSO login for test accounts, without a real IdP and without real
email addresses. Everything here is **sandbox-only** — see [Why not Google](#why-not-google)
and [Security](#security).

## What this sets up

| Piece | Where | Purpose |
|---|---|---|
| `oidc-provider-mock` 0.4.8 | `/opt/oidc-mock`, venv | the IdP, bound to `127.0.0.1:9400` |
| `oidc-mock.service` | `/etc/systemd/system/` | keeps it running, `User=ubuntu` |
| `mock-users.yaml` | `/opt/oidc-mock/users.yaml` | the 5 subjects the IdP will vouch for |
| `oidc_config.xml.sample` | `/server/galaxy/config/oidc_config.xml` | Galaxy's general OIDC settings |
| `oidc_backends_config.xml.sample` | `/server/galaxy/config/oidc_backends_config.xml` | points Galaxy at the mock IdP |
| 3 keys in `galaxy.yml` | `/server/galaxy/config/` | `enable_oidc` + the two file paths |
| `link_users_via_oidc.py` | repo root of `search-and-edit-gal/` | drives the login flow headlessly |

Result: `testuser01`–`05` have an OIDC identity **and** their original password.
`testuser06`–`10` are untouched, password-only.

## Prerequisite found the hard way: `pkce`

On this sandbox the generic `oidc` backend could not even be imported:

```
galaxy.authnz.managers ERROR An error occurred when authenticating a user on `oidc` identity provider
KeyError: 'oidc'
  File "lib/galaxy/authnz/psa_authnz.py", line 236, in _load_backend
  File "lib/galaxy/authnz/oidc.py", line 16, in <module>
```

`lib/galaxy/authnz/oidc.py:16` is `from pkce import generate_pkce_pair`, and
`pkce` was **not installed in Galaxy's venv** — though Galaxy declares it at
`pyproject.toml:134`. The import failure surfaces as a bare `KeyError: 'oidc'`
from the backend registry, which points nowhere near the real cause.

This affects **every** `galaxy.authnz.*` OIDC backend, not just the generic one:
`oidc`, `keycloak` and `cilogon` all subclass `GalaxyOpenIdConnect` from that
module. Pure `social_core` backends (`google`, `okta`, `azure`, …) are unaffected.

Fix — installing a dependency Galaxy already declares, so this restores intended
state rather than patching around it:

```sh
sudo su - galaxy -c 'cd /server/galaxy && source venv/bin/activate && pip install pkce'
```

No restart was needed: the failed import is not cached as a success, so the next
request retried it. Installed version here was `pkce` 1.0.3.

## Why not Google

The original request was Google as the auth broker. Three things make that
impossible for these accounts, and the first two are worth knowing generally:

1. **Google cannot authenticate `@example.org`.** No such Google accounts exist,
   so no amount of correct configuration produces a login.
2. **Pre-linking Google by email does not work either.** Galaxy loads Google as
   `google-openidconnect`, whose `ID_KEY` resolves to `sub`, not email
   (social-core 4.8.3). A pre-linked `uid=<email>` row can never match. This is
   what PR #10 fixes — the tool used to write exactly that row and report success.
3. **A real Google client needs a browser.** Creating the OAuth client and
   completing consent both require one, which the host does not have.

A mock IdP sidesteps all three: it vouches for whatever subjects you list, over
localhost, with no browser anywhere in the flow.

## Security

**The mock provider issues a valid ID token for any subject in `users.yaml`
with no credential check at all.** Exposed publicly it would let anyone mint a
Galaxy session for any email address — including an address in `admin_users`.
Its only protection is the `127.0.0.1` bind, so:

- Do not add an nginx `location` for it, and do not change `--host`.
- Because it is localhost-only, the login flow cannot be driven from a browser
  on your laptop. That is the trade-off, and it is the right one here.
- `VERIFY_SSL` is `False` in `oidc_config.xml` (as in Galaxy's own sample).
  That is instance-wide; a production instance wants `True` and an HTTPS IdP.

## How the association actually happens

`fixed_delegated_auth` is computed, never configured:

```python
fixed_delegated_auth = (len(config.oidc) == 1 and len(authenticators) == 0)
# lib/galaxy/app/__init__.py
```

Turning it on needs **zero** authenticators, and zero authenticators retires
password login for the whole instance — all 16 accounts, yours included. Since
the goal was 5 accounts on SSO and 5 still on passwords, `auth_config_file` is
deliberately left unset, so:

- `fixed_delegated_auth` is `False`
- password login keeps working
- association takes the *other* pipeline branch: **if the user is already
  logged in when the OIDC callback lands, the identity is bound to that account**

Hence `link_users_via_oidc.py` does two steps per user — log in with the
existing password, then complete an authorization — which is exactly what a
human does when Galaxy sends them to `login/start?connect_external_provider=…`.
Galaxy writes the row itself, with the real `sub` and the correct backend name.
Nothing is guessed; nothing is inserted by hand.

## Running it

```sh
# on the Galaxy host, as a user who can reach both ports
python3 link_users_via_oidc.py \
    --csv ../test_users_credentials.csv \
    --users testuser01,testuser02,testuser03,testuser04,testuser05 \
    --dry-run                      # plan only, contacts nothing

python3 link_users_via_oidc.py --csv ../test_users_credentials.csv \
    --users testuser01,testuser02,testuser03,testuser04,testuser05
```

Then confirm what Galaxy wrote — not what the script claims:

```sh
python3 ../convert_users_to_oauth.py --verify
python3 ../convert_users_to_oauth.py --list-linked
```

`--verify` is the real check: it flags a short provider name, an email-shaped
uid that is not lowercase, duplicate `(provider, uid)`, and rows pointing at
deleted users.

## What was verified, and how

Verified on 2026-10-19 against the live sandbox — not inferred from the
scripts' own output:

| Check | Result |
|---|---|
| Rows Galaxy wrote | 5, `provider='oidc'`, `uid=mock-sub-…`, `assoc_type` NULL |
| uid is the IdP's real subject, not the email | yes — this is the point |
| `assoc_type` NULL | confirms Galaxy's pipeline wrote it, not a hand pre-link |
| `--verify` against live PostgreSQL | `no broken rows found`, 0 prelinked |
| SSO login with **no password** | 5/5 authenticated (`--verify-login`) |
| `testuser06`–`10` | 0 links, password-only, untouched |
| Password login after linking | still 200 for both a linked and an unlinked user |

`--verify-login` is the check worth repeating. A row in
`oidc_user_authnz_tokens` only proves Galaxy wrote *something*; it does not
prove Galaxy will **match** it. That mode starts a fresh session, skips the
password entirely, and must come back as the expected user — which exercises
`get_social_auth()`'s exact match on `(provider, uid)`, the lookup a
wrongly-guessed uid fails silently.

## Retiring the passwords (not done here)

Left deliberately undone, so a misbehaving link cannot lock anyone out. When
you do want it:

```sh
python3 ../convert_users_to_oauth.py --convert --provider oidc \
    --email testuser01@example.org --invalidate-password
```

`--mark-external` alone is **not** enough — it blocks the web login form while
`/api/authenticate/baseauth` keeps accepting the old password. Note also that
`external=True` makes `/user/login` refuse the account, so re-linking via this
script stops working once set.

## Teardown

```sh
# 1. the mock IdP
sudo systemctl disable --now oidc-mock
sudo rm /etc/systemd/system/oidc-mock.service && sudo systemctl daemon-reload
sudo rm -rf /opt/oidc-mock

# 2. the OIDC links (audit first, then roll back)
python3 ../convert_users_to_oauth.py --verify
python3 ../convert_users_to_oauth.py --remove --provider oidc --email testuser01@example.org

# 3. Galaxy config — restore the backup the patch script made
sudo ls /server/galaxy/config/galaxy.yml.bak-oidc-*
sudo -u galaxy cp /server/galaxy/config/galaxy.yml.bak-oidc-<stamp> \
                  /server/galaxy/config/galaxy.yml
sudo su - galaxy -c 'cd /server/galaxy && source venv/bin/activate && galaxyctl restart'
```

Rows created by Galaxy's pipeline carry **no** `assoc_type` marker, so
`--remove-all-prelinked` will not touch them — that flag only removes rows this
toolkit pre-linked. Remove pipeline-created rows per user with `--remove`.

## Gotchas found while building this

- **`galaxyctl` needs the right cwd.** Run it from `/server/galaxy` with the
  venv active, or logfire fails to resolve its config and raises a
  `pydantic_settings` traceback that looks like a broken venv.
- **`pkill -f oidc-provider-mock` over SSH kills your own session**, because the
  pattern matches the remote command string. Use `systemctl stop oidc-mock`.
- **A restart is ~4–5 minutes** on this host; the sandbox serves 502 throughout.
- **XML comments cannot contain `--`.** Both config files here were first
  written using `--` as a prose dash, which makes them malformed; Galaxy's
  config loader reads them with `ElementTree`, which refuses to parse. Validate
  with `python3 -c 'import xml.etree.ElementTree as ET; ET.parse("f.xml")'`
  BEFORE installing, not after.
- **`oidc_config_file` and `oidc_backends_config_file` are resolved relative to
  `config_dir`**, so the value is `oidc_config.xml`, not `config/oidc_config.xml`.
  The `config/` prefix still works but logs a deprecation warning on every boot.
- **`galaxy.yml` must not gain a second `galaxy:` block.** YAML keeps only the
  last occurrence of a duplicate key, so appending one silently discards the
  entire live configuration. The patch inserts under the existing mapping and
  then diffs every pre-existing key to prove nothing moved.

## Why the `.sample` suffix

`.gitignore` lines 41-42 ignore `oidc_backends_config.xml` and `oidc_config.xml`
by basename, because on a real instance those files hold the IdP client secret.
That rule is worth keeping, so the copies here carry a `.sample` suffix —
matching Galaxy's own convention for the same files. Install them under their
real names:

```sh
sudo install -m 640 -o galaxy -g galaxy \
    oidc_backends_config.xml.sample /server/galaxy/config/oidc_backends_config.xml
sudo install -m 640 -o galaxy -g galaxy \
    oidc_config.xml.sample /server/galaxy/config/oidc_config.xml
```

The `client_secret` in the sample is deliberately a non-secret placeholder: the
mock provider accepts any client ID and secret, so there is nothing real to
leak. Do not add a real secret to a file tracked under these names.
