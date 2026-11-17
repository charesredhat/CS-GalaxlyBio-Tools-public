#!/usr/bin/env python3
"""
Link existing Galaxy accounts to an OIDC identity through Galaxy's own login flow.

Why this exists
---------------
`convert_users_to_oauth.py` pre-links by writing `oidc_user_authnz_tokens` rows
directly, which means guessing the uid the IdP will send. On a modern
social-core that guess cannot be made correctly for ANY provider -- every
backend keys identity on its own subject identifier (`sub`), not email. See
galaxy_db.EMAIL_UID_PROVIDERS.

This script takes the route Galaxy itself endorses instead: it drives a real
authorization-code login, so Galaxy's `associate_by_email_if_logged_in`
pipeline step records the IdP's REAL subject, with the correct backend name,
through Galaxy's own code. Nothing is guessed and nothing is written to the
database by hand.

How the association actually happens
------------------------------------
`fixed_delegated_auth` is computed, not configured:

    fixed_delegated_auth = (len(config.oidc) == 1 and len(authenticators) == 0)
    -- lib/galaxy/app/__init__.py

Turning it ON requires zero authenticators, which retires password login for
the WHOLE instance. That is the wrong trade when you want some accounts on SSO
and some still on passwords, so this script assumes it is OFF and uses the
other branch of the pipeline: if the user is already logged in when the OIDC
callback lands, the identity is associated with that logged-in account.

So each user is linked in two steps:

    1. log in with the existing password  -> establishes `trans.user`
    2. complete an OIDC authorization     -> pipeline associates the identity

This is exactly what a human does when Galaxy redirects them to
`login/start?connect_external_provider=...`; the password is needed once, to
prove ownership of the account being linked, and can be retired afterwards.

Scope
-----
Written for a MOCK provider (oidc-provider-mock) whose authorization endpoint
accepts a plain form POST naming the subject, so no browser is involved. A real
IdP presents an interactive consent screen and cannot be driven this way --
there, send the user to `login/start?connect_external_provider=<provider>` and
let them log in once themselves. The verification this script performs is
provider-independent and useful either way.

Usage
-----
    python3 link_users_via_oidc.py --csv test_users_credentials.csv \
        --users testuser01,testuser02,testuser03,testuser04,testuser05 \
        --galaxy-url http://127.0.0.1:8080 \
        --idp-url http://127.0.0.1:9400 \
        --provider oidc

    python3 link_users_via_oidc.py ... --dry-run     # show the plan, touch nothing
"""

import argparse
import csv
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

CSRF_RE = re.compile(r'session_csrf_token\s*=\s*"([0-9a-fA-F]+)"')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Capture a redirect instead of following it.

    The OIDC flow is a chain of redirects whose intermediate Location headers
    are the payload -- the authorization URL, then the callback URL carrying
    the code. Following them silently would hide exactly what we need to see,
    and would also let the IdP's redirect reach Galaxy without the cookie jar
    we are deliberately managing.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """One user's browser: a cookie jar plus openers that do and do not redirect."""

    def __init__(self, timeout: int = 30):
        self.jar = CookieJar()
        self.timeout = timeout
        self.follow = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.nofollow = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect
        )

    def request(self, url, data=None, json_body=None, follow=True):
        """Return (status, headers, body-text). A 3xx is a result, not an error."""
        headers = {"Accept": "application/json, text/html;q=0.9"}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=body, headers=headers)
        opener = self.follow if follow else self.nofollow
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # urllib raises on 3xx once redirects are suppressed, and on 4xx/5xx.
            return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def galaxy_login(client: Client, galaxy_url: str, login: str, password: str) -> None:
    """Establish an authenticated galaxysession cookie, or raise RuntimeError."""
    status, _, body = client.request(galaxy_url + "/")
    if status != 200:
        raise RuntimeError(f"GET / returned {status}")

    match = CSRF_RE.search(body)
    if not match:
        raise RuntimeError("no session_csrf_token in the Galaxy index page")
    csrf = match.group(1)

    status, _, body = client.request(
        galaxy_url + "/user/login",
        json_body={"login": login, "password": password, "session_csrf_token": csrf},
    )
    if status != 200:
        raise RuntimeError(f"POST /user/login returned {status}: {body[:200]}")

    # Galaxy answers 200 with an error payload rather than a 4xx on a bad login,
    # so the status code alone does not mean we are authenticated.
    try:
        payload = json.loads(body)
    except ValueError:
        payload = {}
    if payload.get("err_msg"):
        raise RuntimeError(f"login rejected: {payload['err_msg']}")

    status, _, body = client.request(galaxy_url + "/api/users/current")
    if status != 200:
        raise RuntimeError(f"session is not authenticated: /api/users/current -> {status}")
    whoami = json.loads(body)
    if not whoami.get("email"):
        raise RuntimeError(f"session resolved to an anonymous user: {body[:200]}")
    if whoami["email"].lower() != login.lower():
        raise RuntimeError(f"logged in as {whoami['email']}, expected {login}")


def authorize(client: Client, galaxy_url: str, provider: str, sub: str) -> str:
    """Complete one authorization-code flow. Returns the final callback body."""
    # 1. Ask Galaxy where to send the user.
    status, headers, body = client.request(
        f"{galaxy_url}/authnz/{provider}/login", follow=False
    )
    auth_url = None
    if status in (301, 302, 303, 307, 308):
        auth_url = headers.get("Location")
    else:
        try:
            auth_url = json.loads(body).get("redirect_uri")
        except ValueError:
            pass
    if not auth_url:
        raise RuntimeError(
            f"/authnz/{provider}/login gave no authorization URL (status {status}): {body[:200]}"
        )

    # 2. Authorize at the IdP. The mock provider takes the subject as a form
    #    field in place of an interactive login, and answers with a redirect
    #    back to Galaxy carrying ?code=&state=.
    status, headers, body = client.request(auth_url, data={"sub": sub}, follow=False)
    if status not in (301, 302, 303, 307, 308):
        raise RuntimeError(
            f"IdP did not redirect after authorize (status {status}): {body[:300]}"
        )
    callback_url = headers.get("Location")
    if not callback_url or "code=" not in callback_url:
        raise RuntimeError(f"IdP redirect carries no authorization code: {callback_url}")

    # 3. Hand the code back to Galaxy on the SAME session, so the pipeline sees
    #    a logged-in user and associates rather than prompting.
    status, headers, body = client.request(callback_url, follow=False)
    if status not in (200, 301, 302, 303, 307, 308):
        raise RuntimeError(f"callback returned {status}: {body[:300]}")

    location = headers.get("Location", "")
    if "connect_external_provider" in location:
        raise RuntimeError(
            "Galaxy redirected to the account-linking prompt, which means the callback "
            "did not see a logged-in session -- the association did not happen"
        )
    return location or body


def verify_sso_login(galaxy_url: str, provider: str, sub: str, expect_email: str) -> str:
    """
    Prove the link is usable: authenticate with the IdP alone, no password.

    This is the check that matters. A row in oidc_user_authnz_tokens only means
    Galaxy wrote something; it does not prove Galaxy will MATCH it at login.
    Here a brand-new session (no password step) completes an authorization and
    must come back as the expected user -- which exercises
    `get_social_auth()`'s exact match on (provider, uid), the lookup that a
    wrongly-guessed uid silently fails.
    """
    client = Client()
    # Establish an anonymous session, then authorize as the IdP subject only.
    client.request(galaxy_url + "/")
    authorize(client, galaxy_url, provider, sub)

    status, _, body = client.request(galaxy_url + "/api/users/current")
    if status != 200:
        raise RuntimeError(f"/api/users/current -> {status}")
    whoami = json.loads(body)
    email = whoami.get("email")
    if not email:
        raise RuntimeError("SSO login left the session anonymous; the link did not match")
    if email.lower() != expect_email.lower():
        raise RuntimeError(f"SSO logged in as {email}, expected {expect_email}")
    return email


def read_credentials(path: str, wanted: list) -> list:
    """Pull (username, email, password) for the requested usernames, in order.

    `path` may be "-" to read the CSV from stdin. That is the preferred way to
    run this on a remote Galaxy host:

        cat test_users_credentials.csv | ssh host 'python3 link_users_via_oidc.py --csv - ...'

    so the cleartext passwords are never written to that host's disk.
    """
    if path == "-":
        rows = {row["username"]: row for row in csv.DictReader(sys.stdin)}
    else:
        with open(path, newline="") as fh:
            rows = {row["username"]: row for row in csv.DictReader(fh)}
    missing = [u for u in wanted if u not in rows]
    if missing:
        raise SystemExit(f"Error: not in {path}: {', '.join(missing)}")
    return [(u, rows[u]["email"], rows[u]["password"]) for u in wanted]


def main():
    parser = argparse.ArgumentParser(
        description="Link Galaxy accounts to an OIDC identity through Galaxy's own login flow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv", required=True,
                        help='credentials CSV (username,email,password,...); "-" reads stdin, '
                             'which keeps passwords off a remote host\'s disk')
    parser.add_argument("--users", required=True,
                        help="comma-separated usernames to link, e.g. testuser01,testuser02")
    parser.add_argument("--galaxy-url", default="http://127.0.0.1:8080")
    parser.add_argument("--idp-url", default="http://127.0.0.1:9400",
                        help="mock provider base URL (checked for reachability only)")
    parser.add_argument("--provider", default="oidc",
                        help="provider name from oidc_backends_config.xml (default: oidc)")
    parser.add_argument("--sub-template", default="mock-sub-{index:08d}-{username}",
                        help="how to derive each user's IdP subject; must match mock-users.yaml")
    parser.add_argument("--dry-run", action="store_true", help="show the plan and exit")
    parser.add_argument("--verify-login", action="store_true",
                        help="do not link; instead prove each user can log in via the IdP alone, "
                             "with no password, which is the only real test that the link matches")
    args = parser.parse_args()

    wanted = [u.strip() for u in args.users.split(",") if u.strip()]
    users = read_credentials(args.csv, wanted)

    print("=" * 78)
    print("Link Galaxy users to OIDC via Galaxy's association pipeline")
    print("=" * 78)
    print(f"Galaxy  : {args.galaxy_url}")
    print(f"IdP     : {args.idp_url}")
    print(f"Provider: {args.provider}\n")

    plan = []
    for username, email, password in users:
        index = int(re.sub(r"\D", "", username) or 0)
        plan.append((username, email, password,
                     args.sub_template.format(index=index, username=username)))

    for username, email, _pw, sub in plan:
        print(f"  {username:<12} {email:<26} sub={sub}")

    if args.dry_run:
        print("\n[DRY RUN] nothing was contacted or changed")
        return 0

    if args.verify_login:
        print()
        ok, bad = [], []
        for username, email, _pw, sub in plan:
            print(f"{email} via SSO only ... ", end="", flush=True)
            try:
                verify_sso_login(args.galaxy_url, args.provider, sub, email)
                print("authenticated")
                ok.append(email)
            except Exception as exc:
                print(f"FAILED: {exc}")
                bad.append((email, str(exc)))
        print(f"\n{len(ok)}/{len(plan)} authenticated with no password.")
        for email, err in bad:
            print(f"  {email}: {err}")
        return 1 if bad else 0

    print()
    linked, failed = [], []
    for username, email, password, sub in plan:
        print(f"{email} ... ", end="", flush=True)
        try:
            client = Client()
            galaxy_login(client, args.galaxy_url, email, password)
            authorize(client, args.galaxy_url, args.provider, sub)
            print("linked")
            linked.append(email)
        except Exception as exc:
            print(f"FAILED: {exc}")
            failed.append((email, str(exc)))

    print(f"\nLinked {len(linked)}/{len(plan)}.")
    if failed:
        print("\nFailures:")
        for email, err in failed:
            print(f"  {email}: {err}")
        print("\nNothing was written to the database by this script directly; a failure here\n"
              "means Galaxy did not create the association, not that it created a bad one.\n"
              "Confirm with: python3 convert_users_to_oauth.py --verify")
        return 1

    print("\nConfirm the rows Galaxy wrote with:")
    print("  python3 convert_users_to_oauth.py --verify")
    print("  python3 convert_users_to_oauth.py --list-linked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
