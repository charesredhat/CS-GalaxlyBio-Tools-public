# Galaxy Tools - Personal Automation Toolkit

A collection of Python scripts and utilities for automating [Galaxy](https://galaxyproject.org/) bioinformatics workflows using **Bioblend**.

## 🚀 Quick Start

```bash
# Set up virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Load your API key (see Configuration below) and test the connection
set -a; . ./.env; set +a
python test_galaxy_connection.py

# Run a quick QC test
python quick_qc_test.py

# Find the Galaxy install on this host
sh search-and-edit-gal/find-galaxy.sh

# Check whether it is actually serving (and why not)
sh search-and-edit-gal/galaxy-health.sh

# Audit OIDC account links (read-only)
python3 search-and-edit-gal/convert_users_to_oauth.py --verify \
    --galaxy-config /srv/galaxy/config/galaxy.yml

# Desktop shortcut that starts a local Galaxy, and a "Report a problem" form
bash local-galaxy/launcher/install-launcher.sh /path/to/galaxy 8090
bash local-galaxy/report-problem/install-report-form.sh /path/to/galaxy
```

## 📁 Project Structure

### Core Automation Scripts

| Script | Description |
|--------|-------------|
| `galaxy_automation.py` | Core Bioblend automation class with history, upload, and tool execution methods |
| `test_galaxy_connection.py` | Connection tester - validates API key and lists available tools/histories |
| `quick_qc_test.py` | Quick FastQC test utility for rapid workflow validation |
| `create_test_users.py` | Creates (or deletes) local test accounts via the admin API; passwords go to a gitignored CSV |

### MicrorunQC Testing Framework

| Script | Description |
|--------|-------------|
| `complete_microrunqc_test.py` | Full-featured test framework with history selection and database checks |
| `enhanced_microrunqc_test.py` | Enhanced tester with detailed parameter inspection |
| `microrunqc_bioblend_test.py` | Basic MicrorunQC automation; finds existing FASTQ via dataset index with explicit pagination (faster on large instances) |
| `create_test_fastq_history.py` | Creates test histories with paired-end FASTQ sample data |
| `run_microrunqc_test.sh` | Convenience wrapper script (activates venv automatically) |

### Local Galaxy Desktop Additions

For a Galaxy running on your own Linux desktop. Full instructions: [`local-galaxy/README.md`](local-galaxy/README.md).

| File | Description |
|------|-------------|
| `local-galaxy/launcher/install-launcher.sh` | Installs a **Galaxy** desktop shortcut that starts `run.sh` and opens the browser once Galaxy answers |
| `local-galaxy/launcher/galaxy-start` | The launcher script the shortcut runs (template; the installer fills in the Galaxy root and port) |
| `local-galaxy/report-problem/install-report-form.sh` | Adds a **Report a problem** button to the Galaxy home page, the form, and its handler |
| `local-galaxy/report-problem/static/` | `welcome.html` (home page with the button) and `report-problem.html` (the form) |
| `local-galaxy/report-problem/webhooks/report_problem/` | Galaxy webhook whose handler saves each report to `database/problem_reports/reports.jsonl` and sends it to every admin's Galaxy notification bell |

### SSO / OIDC Authentication and User Migration

Tooling for moving a Galaxy instance from password logins to OIDC single sign-on.
Verified against `galaxyproject/galaxy` **v26.1.1** by reading the source.

| File | Description |
|------|-------------|
| `search-and-edit-gal/find-galaxy.sh` | **Run this first** — locates the Galaxy root, version and configs on a host |
| `search-and-edit-gal/galaxy-health.sh` | Diagnoses whether a Galaxy is serving — catches the boot-timeout restart loop that `systemctl` reports as healthy |
| `setup_galaxy_oidc.sh` | Enables OIDC in `galaxy.yml`, writes the OIDC config files and an inactive SSO cutover template |
| `configure_oidc_provider.sh` | Interactive per-provider setup (Google, Azure, Keycloak, Auth0, generic OIDC) |
| `search-and-edit-gal/convert_users_to_oauth.py` | Audits and converts user accounts between password and OIDC auth |
| `search-and-edit-gal/search_galaxy_users.py` | Searches and lists users, via the API or directly against the database |
| `search-and-edit-gal/set_user_password.py` | Sets a user's password from a `getpass` prompt, stamps `last_password_change`, and invalidates that user's other sessions |
| `search-and-edit-gal/galaxy_db.py` | Shared DB layer (SQLite + PostgreSQL), provider-name translation, password hashing |
| `search-and-edit-gal/test_convert_users_to_oauth.py` | Regression tests — stdlib only, no pytest or running Galaxy |
| `search-and-edit-gal/link_users_via_oidc.py` | Links accounts by driving Galaxy's own login flow, so Galaxy records the IdP's real subject instead of a guessed uid |
| `search-and-edit-gal/sandbox-oidc/` | Mock OpenID Provider setup for end-to-end SSO testing without a real IdP |
| `search-and-edit-gal/OAUTH_CONVERSION_GUIDE.md` | **Start here** — the full migration guide |
| `search-and-edit-gal/GALAXY_USER_DATABASE_REFERENCE.md` | Database schema reference |
| `info/oauth-tool-galaxy-26.1.1-review.md` | Source-verified review behind these changes |

#### How these scripts handle secrets

Nothing here takes a password or client secret as a command-line argument: an
argument is visible in your shell history and in `ps` output to every user on the
box for as long as the process runs.

- `configure_oidc_provider.sh` reads client secrets with `read -rs`, so they are
  never echoed to the terminal or left in an SSH scrollback. `-r` matters as much
  as `-s` -- without it `read` treats a backslash in a secret as an escape and
  silently corrupts it.
- `set_user_password.py` reads passwords with `getpass` and confirms by retyping.
- The generated `oidc_backends_config.xml` and `oidc_config.xml` are written
  `0600`. They hold client secrets, and at the default umask they would land
  `0644` -- readable by every account on a shared Galaxy host. `.gitignore` keeps
  them out of the repo; the mode is what keeps them off other accounts.

#### Audit before you migrate

`--verify` is read-only and exits non-zero if it finds rows that cannot match at login:

```bash
python3 search-and-edit-gal/convert_users_to_oauth.py --verify \
    --galaxy-config /srv/galaxy/config/galaxy.yml
```

It catches four conditions: a `provider` holding a config short name rather than a
backend name, a uid whose case will never match, duplicate `(provider, uid)`, and
rows pointing at deleted or missing users. Backend names from older Galaxy releases
are reported as INFO, not broken — those are working logins on an upgraded instance.

#### Locating the Galaxy install

Every other script here needs the Galaxy root. `setup_galaxy_oidc.sh` resolves its
sample config as `../lib/galaxy/config/sample/galaxy.yml.sample`, so it only works
when run from `<galaxy_root>/config/`; `convert_users_to_oauth.py` needs the path to
`galaxy.yml`. On a host you did not build yourself, start here:

```sh
sh search-and-edit-gal/find-galaxy.sh
```

POSIX `sh`, no root, no dependencies beyond coreutils — it runs over a bare SSH
session on anything from Alpine to RHEL. Pass extra directories to search if the
install is somewhere unusual:

```sh
sh search-and-edit-gal/find-galaxy.sh /mnt/apps /nfs/shared
```

An install on a network mount is found without being told where to look. The root
is read from the running processes' arguments first, so a live Galaxy is located
even when it sits on NFS, EFS or another filesystem that `find -xdev` will not
cross — which is what a bare directory scan misses. A root on a network
filesystem is flagged in the output, because it is the usual reason a server is
slow to start.

**What it checks, in order:**

| Phase | Looks for |
|-------|-----------|
| 1. Processes | `galaxy`, `gravity`, `galaxyctl` in `ps` — finds a *running* instance wherever it lives |
| 2. Ports | Listeners on 80/8000/8080/9090 via `ss`, falling back to `netstat` |
| 3. Services | `systemd` units, `docker` containers, `supervisorctl` entries |
| 4. Filesystem | The root is first read out of the running processes' own arguments, then `/srv/galaxy`, `/opt/galaxy`, `/home/galaxy`, `/usr/local/galaxy`, `/galaxy`, `/data/galaxy`, `/export/galaxy`, `/srv/www/galaxy`, `/server/galaxy`, `$HOME`, every mount point, plus any paths you pass — then a per-mount scan if none hit |
| 5. Packages | `pip list` and `conda env list` entries starting with `galaxy` |

**Sample output:**

```
== Galaxy detection on galaxy-prod-01 ==
user: deploy   date: Mon Sep 28 15:06:53 UTC 2026
------------------------------------------------------------
[1] Running Galaxy processes
  2841 galaxy  /srv/galaxy/.venv/bin/python -m galaxy.webapps.galaxy.fast_factory

[2] Listening ports (8080/8000 are Galaxy defaults)
  LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*

[4] Filesystem search for Galaxy roots
  *** GALAXY ROOT: /srv/galaxy   (version 26.1)
        config: config/galaxy.yml
        config: config/auth_conf.xml
        config: config/oidc_backends_config.xml
        db driver: postgresql
```

### Check whether a Galaxy is actually serving

`find-galaxy.sh` tells you where Galaxy is; `galaxy-health.sh` tells you whether
it works, and if not, why:

```sh
sh search-and-edit-gal/galaxy-health.sh                      # derives the root
sh search-and-edit-gal/galaxy-health.sh /server/galaxy http://localhost:8080
```

Read-only, POSIX `sh`, and it uses `sudo` only where that is already
passwordless — otherwise it reports what the calling user can see. Exits non-zero
when it finds a problem, so it works as a preflight check in a script.

It is built around a failure mode that standard tooling reports as healthy. When
gravity's `supervisord` restarts a Galaxy that never finishes booting,
`systemctl status galaxy.service` keeps saying `active (running)` with
`NRestarts=0` — because systemd only supervises the `galaxyctl` parent, which is
fine. Meanwhile every request 502s. `galaxyctl status` is no better: it shows
each process `RUNNING`, since they *are* running, just for the few minutes before
they are killed and replaced.

**What it checks:**

| Phase | Looks for |
|-------|-----------|
| 1. Serving | `GET /api/version`, and whether the app server socket exists at all — the socket is only created once startup completes |
| 2. Process manager | `galaxyctl status` next to systemd's view, so the disagreement between them is visible |
| 3. Restart churn | Repeated app-server spawns in `supervisord.log`, the interval between them, and whether that interval tracks the configured `timeout` |
| 4. Progress | Wall-clock versus CPU time per app-server process, and the age of the last log write — a boot burning no CPU is waiting on I/O, not working |
| 5. Startup cost | Whether the Galaxy root is on a network filesystem, and how many tool entries the toolbox configs declare |
| 6. Dependencies | Database reachability via `pg_isready` (host only; the connection string is never printed) |

A boot that takes longer than `gravity.gunicorn.timeout` is killed before it can
finish, every time, which looks like a crash loop with no crash in the logs. That
is what phase 3 is for — if the restart interval sits just above the timeout, the
timeout is the problem:

```
[3] Restart churn (the part systemd does not show)
    last 5 gunicorn spawns:
      2026-10-09 15:50:24
      2026-10-09 15:56:12
      2026-10-09 16:02:00
      2026-10-09 16:07:34
      2026-10-09 16:13:22
      most recent interval: 348s
      gunicorn timeout: 300s
  X restart interval tracks the 300s timeout: startup is being killed before it completes

[5] Startup cost factors
  X Galaxy root is on nfs
        Every tool XML and .loc file read at startup is a network round trip.
        This alone can push boot past the app server timeout.

VERDICT: not healthy - 4 problem(s), 0 warning(s) above.
```

The fix in that case is to raise `gravity.gunicorn.timeout` in `galaxy.yml` above
the real boot time and `galaxyctl restart`. On a network-mounted root, boot time
is dominated by toolbox parsing, so measure it rather than guessing.

Feed that root straight into the rest of the workflow:

```sh
ROOT=/srv/galaxy
python3 search-and-edit-gal/convert_users_to_oauth.py --verify \
    --galaxy-config "$ROOT/config/galaxy.yml"
(cd "$ROOT/config" && sh /path/to/setup_galaxy_oidc.sh)
```

**Detection is deliberately compound** — a directory counts as a Galaxy root only if
it has `lib/galaxy/` *and* a sibling `run.sh` or `config/`. Matching `run.sh` or a
directory merely named `galaxy` produces large numbers of false positives (kernel
headers ship dozens of `run.sh`; the kernel tree has a `sound/isa/galaxy` driver).

**It never prints credentials.** Phase 4 reports the database *driver* parsed out of
`database_connection` (`postgresql`, `sqlite`) and never the connection string, so the
output is safe to paste into a ticket or issue.

**Known limitations:**

- The fallback scan uses `find / -xdev`, which **does not cross filesystem
  boundaries**. If Galaxy sits on a separate mount or NFS share and is not in the
  common-paths list, pass the mount point explicitly as an argument.
- Unprivileged runs see only what your user can read — `ss -tlnp` hides other users'
  process names, and unreadable directories are skipped silently.
- The script always exits `0`. It is a report, not a test; parse the output for
  `GALAXY ROOT` rather than checking `$?`.

#### Two things that are easy to get wrong

**The `provider` column takes the python-social-auth *backend* name**, not the
`name=` attribute from `oidc_backends_config.xml`. Galaxy matches
`(provider, uid)` exactly, so a short name is a row that can never work:

| `oidc_backends_config.xml` | stored in `oidc_user_authnz_tokens.provider` |
|---|---|
| `google` | `google-openidconnect` |
| `okta` | `okta-openidconnect` |
| `azure` | `azuread-v2-tenant-oauth2` |
| `keycloak`, `cilogon`, `auth0`, `oidc` | unchanged |

`search-and-edit-gal/convert_users_to_oauth.py` takes the short names and translates them.

**Setting `galaxy_user.external` does not disable password authentication.** It is
checked only by the web login form; `/api/authenticate/baseauth` will still trade
the old password for an API key. Only an `auth_conf.xml` containing zero
authenticators closes every path:

| Mechanism | Web login form | `/api/authenticate/baseauth` |
|---|---|---|
| `external = true` (`--mark-external`) | Blocked | **Still works** |
| `disable_local_accounts: true` | Hides UI / blocks registration | **Still works** |
| Empty `auth_conf.xml` | Blocked | Blocked |

Use `--invalidate-password` to genuinely retire a credential (irreversible), and
`--rotate-api-keys` — API keys bypass password auth entirely and otherwise outlive
the migration. See the guide for the full cutover sequence.

**Prefer Galaxy's own association pipeline over bulk pre-linking.** With one OIDC
provider and zero authenticators, Galaxy auto-associates existing accounts by email
on first login, writing the correct backend name and the IdP's real subject. Bulk
pre-linking has to guess the uid ahead of time and is only safe for Google.

**OIDC config notes**
- `configure_oidc_provider.sh` updates a single `<provider>` entry inside `oidc_backends_config.xml` instead of overwriting the whole file, so configuring multiple providers is safe.
- Provider replacement is case-insensitive (prevents duplicates like `Google` vs `google`).
- User-entered values are XML-escaped before writing the provider fragment (so values containing `&`, `<`, `>` do not break the config).
- The merge preserves existing XML comments, including the instructional header block `setup_galaxy_oidc.sh` writes above `<OIDC>` in a fresh config.
- Every emitted `<provider>` fragment is validated against Galaxy's XSD element set. This matters: Galaxy validates this file with `schema.assertValid()` over an `<xs:all>` model, so a single undeclared element makes it **refuse to start**. The Keycloak IdP hint is `<idphint>` (not `kc_idp_hint`, which is the query parameter derived from it), and Auth0 needs `<domain>` rather than `<url>`.
- `setup_galaxy_oidc.sh` merges into the existing `galaxy:` mapping in `galaxy.yml` rather than appending a second one, leaves settings you have already configured alone, and is safe to re-run. It never overwrites a live config or an existing backup.

### Tool Installation & Configuration

| File | Description |
|------|-------------|
| `install-galaxy-tools.yml` | Ansible playbook for bulk tool installation |
| `galaxtrakr/tool-menu.yml` | Tool list (~5900+ tools) from GalaxyTrakr |
| `galaxtrakr/tool_conf.xml` | Galaxy tool panel configuration |

## ⚙️ Configuration

### Make your account an admin (fresh local Galaxy)

A fresh Galaxy has no `config/galaxy.yml` and no admins. Register an account in the
web UI, stop Galaxy, then from the Galaxy root:

```bash
cp config/galaxy.yml.sample config/galaxy.yml
# edit config/galaxy.yml — uncomment admin_users under the galaxy: section
#   galaxy:
#     admin_users: you@example.com
```

Restart Galaxy; an **Admin** tab appears when you log in.

### API key

Create a key in Galaxy under **User → Preferences → Manage API Key**. Every script
reads `GALAXY_API_KEY` and `GALAXY_URL` (default `http://127.0.0.1:8080/`) from the
environment.

`export` only applies to the terminal it was run in, so the recommended setup is a
`.env` file in the project root (it is in `.gitignore`):

```bash
export GALAXY_API_KEY="your-api-key-here"     # once, in any terminal
printf 'GALAXY_API_KEY=%s\n' "$GALAXY_API_KEY" > .env
chmod 600 .env
```

Load it in any new terminal before running scripts:

```bash
set -a; . ./.env; set +a
```

`run_microrunqc_test.sh` loads `.env` automatically. Check the key works, and that
you are an admin, with:

```bash
curl -s -H "x-api-key: $GALAXY_API_KEY" http://127.0.0.1:8080/api/users/current
# ... "is_admin": true ...
```

### VS Code as a Flatpak (one `.venv`, two Pythons)

If VS Code is installed as a Flatpak, its integrated terminal runs in a sandbox with
the Flatpak SDK's Python (3.13), while a normal terminal uses the system Python
(e.g. 3.14). A `.venv` built in one fails in the other with
`No module named 'bioblend'`.

One `.venv` can serve both: its `bin/python3` links to `/usr/bin/python3`, which
resolves to a different interpreter on each side, and each interpreter reads its own
`lib/python3.X/site-packages`. Install the requirements from both sides:

```bash
# from the VS Code terminal
.venv/bin/python -m ensurepip --default-pip
.venv/bin/pip install -r requirements.txt                        # sandbox (3.13)
flatpak-spawn --host .venv/bin/pip install -r requirements.txt   # host (3.14)
```

Any package added later must be installed on both sides. Also note that inside the
sandbox `ps` cannot see host processes and `ss` is not installed, so run
`find-galaxy.sh` from a normal terminal (or via `flatpak-spawn --host`) to get its
process and port phases.

## 🧬 Example Usage

### Run FastQC on Sample Data
```bash
python quick_qc_test.py
```

### Test MicrorunQC with Existing History
```bash
./run_microrunqc_test.sh --history-name "My FASTQ History"
./run_microrunqc_test.sh --interactive
```

### Create Test User Accounts
Needs an admin API key. Creates `testuser01@example.org` … `testuser10@example.org`;
existing accounts are skipped, so it is safe to re-run. Generated passwords are never
printed — each one is appended to `test_users_credentials.csv` (mode 600, gitignored)
as its account is created, so an interrupted run still leaves you the passwords for
the accounts it got through.
```bash
python create_test_users.py                    # 10 accounts
python create_test_users.py -n 25 --prefix qa  # qa01..qa25
python create_test_users.py --delete           # remove them again
python create_test_users.py --delete --purge   # also purge (needs allow_user_deletion)
```
`--purge` only applies with `--delete`; on its own it is rejected rather than ignored.

### Test SSO logins for converted users

Checks that accounts converted to OIDC can actually authenticate. Written for the
sandbox's mock provider (`search-and-edit-gal/sandbox-oidc/`), but the audit steps
apply to any IdP.

**A row in `oidc_user_authnz_tokens` is not proof of anything.** It shows Galaxy
wrote something; it does not show Galaxy will *match* it at login. Galaxy compares
`(provider, uid)` exactly, so a uid that is off by a case or keyed on the wrong
claim fails silently. Test the login, not the row.

#### 1. Authenticate with no password at all

The strongest single check. Starts a fresh session, skips the password, and
requires Galaxy to come back as the expected user:

```bash
cat test_users_credentials.csv | ssh sandbox \
  "python3 /opt/oidc-mock/link_users_via_oidc.py --csv - \
     --users testuser01,testuser02,testuser03,testuser04,testuser05 \
     --galaxy-url http://127.0.0.1:8080 --provider oidc --verify-login"
# testuser01@example.org via SSO only ... authenticated
# ...
# 5/5 authenticated with no password.
```

`sandbox` and `bastion` here are **your own SSH aliases** for the Galaxy host and
whatever you reach it through; this repo does not record the real hostnames. The
command has to run somewhere that can reach the Galaxy host's `127.0.0.1`, because
that is where the mock provider is bound — so if your Galaxy sits behind a jump
host, the alias needs to resolve there, not on your workstation.

`--csv -` reads from stdin so the cleartext passwords never land on that host's
disk, and in `--verify-login` mode they are not used at all — the CSV only
supplies the email mapping. Exit status is non-zero if any user fails.

#### 2. Audit what Galaxy stored

```bash
export GALAXY_DB_URL="postgresql://…"      # or --galaxy-config /path/to/galaxy.yml
python3 search-and-edit-gal/convert_users_to_oauth.py --verify         # read-only
python3 search-and-edit-gal/convert_users_to_oauth.py --list-linked
python3 search-and-edit-gal/convert_users_to_oauth.py --list-unlinked
```

Three things to read in `--list-linked`:

| Column | Expect | Why |
|---|---|---|
| Stored backend | the PSA backend name (`oidc`, `google-openidconnect`) | a short name like `google` is a dead row |
| UID | the IdP's subject (`mock-sub-…`) | an email here means the uid was guessed — see `OAUTH_CONVERSION_GUIDE.md` |
| assoc_type | `-` | `-` means Galaxy's pipeline wrote it; `prelinked` means this toolkit did |

#### 3. In a browser

The mock provider is localhost-only, so forward both ports and browse to
`http://127.0.0.1:8080` — **not** the public hostname, or the ports will not match
the configured `redirect_uri` and `oidc_endpoint`:

```bash
# nested because the inner hop's key may live on the jump host, not your laptop;
# collapse to a single ssh -L if you can reach the Galaxy host directly
ssh -L 8080:127.0.0.1:18080 -L 9400:127.0.0.1:19400 bastion \
    "ssh -L 18080:127.0.0.1:8080 -L 19400:127.0.0.1:9400 -N sandbox"
```

Click the SSO button, enter a subject from `sandbox-oidc/mock-users.yaml`
(e.g. `mock-sub-00000001-testuser01`), click **Authorize**. You land in Galaxy as
that user with no password. The identity is also listed under
**User → Preferences → Manage Third-Party Identities**.

#### 4. Negative tests — and one not to run

Safe, and each proves something:

- Click **Deny access** on the provider form; Galaxy should handle the rejection.
- `--list-unlinked` should show exactly the accounts you did *not* convert.
- Password login should still work for converted users, unless you retired it with
  `--invalidate-password`. (`--mark-external` alone does not retire anything —
  `/api/authenticate/baseauth` keeps accepting the old password.)

**Do not type an unregistered subject into the provider form.** `--user-claims-file`
predefines *claims*; it does not restrict *who may log in*. The mock provider will
happily authenticate any subject, and with `allow_user_creation: true` Galaxy then
auto-provisions a new account — using the subject string as the email, since an
unregistered subject carries no email claim. That leaves junk accounts which
`allow_user_deletion: false` will not let you purge. The same openness is why the
provider must stay bound to `127.0.0.1`: its `PUT /users/<sub>` endpoint is
unauthenticated, so anything that can reach it can mint an identity with an
arbitrary email claim.

### Install Tools via Ansible
```bash
ansible-playbook install-galaxy-tools.yml
```

## 🧪 Tests

The OIDC conversion tooling has a regression suite that needs no pytest, no
SQLAlchemy and no running Galaxy — it builds a throwaway Galaxy-shaped SQLite
database and asserts the tool's output against reimplementations of Galaxy's own
login lookup and password verifier:

```bash
python3 search-and-edit-gal/test_convert_users_to_oauth.py
# Ran 65 tests: 0 failure(s), 0 error(s)
```

## 📋 Requirements

- Python 3.8+
- [Bioblend](https://bioblend.readthedocs.io/) — for the workflow automation scripts; `pip install -r requirements.txt` installs the pinned, tested versions (bioblend 1.9.0, PyYAML 6.0.3)
- Running Galaxy instance with valid API key
- Ansible (optional, for the tool installation playbook)
- PyYAML (optional) — lets the OIDC scripts read `database_connection` from `galaxy.yml` and verify their own edits
- `psycopg[binary]` or `psycopg2-binary` (optional) — only needed to point the user tooling at a PostgreSQL Galaxy; SQLite works with the standard library alone

## 📂 GalaxyTrakr Integration

The `galaxtrakr/` folder contains tool configurations from [GalaxyTrakr](https://galaxytrakr.org/) (FDA's foodborne pathogen genomics platform):

- `galaxtrakr/tool-menu.yml` - Complete tool list for installation
- `galaxtrakr/compare_files_xmltocsv.py` - Utility to compare tool configurations
- Various CSV exports for tool analysis

## 🔧 Notes

- MicrorunQC requires MLST databases to be configured in Galaxy
- FastQC is used as a fallback when database-dependent tools fail
- Scripts generate reports in `microrunqc_test_report.txt`
- The user-migration tooling reads and writes the Galaxy database directly. Take a
  backup before any write, and run `--verify` (read-only) first. `--invalidate-password`
  cannot be undone.

## 📄 License

Personal toolkit - use at your own discretion.
