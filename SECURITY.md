# Security

## Reporting a vulnerability

Please open a private security advisory through GitHub's **Security → Report a
vulnerability** flow rather than a public issue.

## How this repository handles secrets

Nothing in this repository should ever contain a credential, a real hostname,
or an internal network address. The tools here administer Galaxy instances, so
they necessarily *handle* secrets — they just never store them.

**Credentials come from the environment, never from source.**

```bash
# .env is gitignored; create it locally
printf 'GALAXY_API_KEY=%s\n' "$YOUR_KEY" > .env
chmod 600 .env
set -a; . ./.env; set +a
```

Every script reads `os.environ.get("GALAXY_API_KEY", "")`. The Ansible playbook
uses `lookup('env', 'GALAXY_API_KEY')`. There are no defaults and no fallbacks
to a committed file.

**Secrets are never passed as command-line arguments.** They would be visible
in `ps` output to every user on the host, and in shell history.
`set_user_password.py` deliberately refuses piped input and prompts instead —
please do not "fix" this.

**Environment-specific values are placeholders.** Load balancer subnets
(`SUBNET_AZ_n/24`), bastion addresses (`$BASTION_HOST`), OAuth client IDs
(`<YOUR_CLIENT_ID>`) must be supplied locally. `report-problem-ratelimit.conf`
fails `nginx -t` if its placeholders are left unedited — that is intentional,
so an unconfigured copy cannot silently trust the wrong address ranges.

## Pre-commit secret scanning

A scanner is bundled. Enable it after cloning:

```bash
git config core.hooksPath .githooks
```

It blocks staged changes containing provider tokens, AWS access keys, private
key headers, 32-hex API keys, private IP addresses, VPC/subnet ids and EC2
hostnames. Bypass with `--no-verify` only for a reviewed false positive.

It is a safety net, not an audit. Two lessons are worth repeating, because both
produced a *clean result that was wrong* during this project:

- **A scanner that has never caught a planted secret proves nothing.** An early
  version of this hook silently passed a real key: `/usr/bin/grep` on the
  development workstation is `ugrep`, which rejects `^\+\+\+` as invalid BRE.
  The filter errored, the scanned text came back empty, and everything passed.
  Test every scanner against a deliberately planted positive.
- **`grep -I` skips binary files.** A spreadsheet in this repository held six
  colleagues' email addresses that every text scan missed. Archives and Office
  documents are zipped XML — unzip and scan them, and check document metadata
  (`docProps/core.xml`) for author names.

## Security properties of the problem-report webhooks

`local-galaxy/report-problem/` exposes two endpoints with deliberately
different access models:

| | `report_problem` | `report_admin` |
|---|---|---|
| Access | open — anyone who can reach Galaxy | `trans.user_is_admin` only |
| Why | users must be able to report a problem | reports contain reporter emails |

Properties that must survive future edits:

- **The admin endpoint returns an identical error to anonymous and non-admin
  callers.** A prober learns nothing about whether the endpoint exists.
- **Report text is attacker-controlled** — up to 5,000 characters from anyone.
  It is rendered with `textContent` and `createTextNode` only. Never introduce
  `innerHTML`, `insertAdjacentHTML` or a template string into these pages.
- Before entering an admin notification, user text is wrapped in an indented
  code block, so it cannot inject links, images or HTML into the message.
- Input is length-capped and stripped of control characters on arrival.
- No response contains a filesystem path; a test asserts this.
- `MAX_SCAN_BYTES` bounds how much of the report file is parsed, so an
  unbounded file cannot exhaust memory.
- Rate limiting keys on a `real_ip` derived from trusted LB subnets. **Do not**
  fall back to keying on `$http_x_forwarded_for` — it is client-supplied on a
  direct connection and trivially varied, which makes the limit decorative.

Run the suites before changing any of this:

```bash
python3 -m unittest discover -s local-galaxy/report-problem/webhooks  # 20 tests
python3 search-and-edit-gal/test_convert_users_to_oauth.py            # 65 tests
```

## A note on OIDC identity

Galaxy keys external identities on `(provider, uid)` where **`uid` is the
provider's `sub` claim, not the email address** — Google included. Pre-linking
a user by email creates a row that silently never matches. Verify a converted
account by completing an actual SSO login, not by inspecting the database row.
