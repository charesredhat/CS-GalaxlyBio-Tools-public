#!/usr/bin/env python3
"""
Insert the rate-limited `location` block into a Galaxy nginx server block.

Pairs with report-problem-ratelimit.conf, which supplies the limit_req_zone from
http context. This adds the exact-match location that uses it.

Why the block copies proxy_set_header lines
-------------------------------------------
An exact-match `location =` outranks the prefix `location /`, which means it
REPLACES it for this URL rather than adding to it -- nginx does not inherit
proxy_set_header from a sibling location. A block that omitted them would reach
Galaxy without Host/X-Forwarded-* set, so Galaxy would build wrong absolute URLs
for exactly this endpoint. The defaults below mirror a stock Galaxy vhost,
including the custom $forwarded_proto variable; pass --from-location to copy
whatever the live `location /` actually has instead of trusting these.

Where the backup goes, and why it matters
-----------------------------------------
NOT next to the site file. Debian/Ubuntu nginx does
`include /etc/nginx/sites-enabled/*;` with no extension filter, so a backup
written beside the original is parsed as a SECOND server block and nginx fails
with "a duplicate default server for 0.0.0.0:<port>". Backups go to
/etc/nginx/backups/, which nothing includes.

Usage
-----
    sudo python3 add-ratelimit-location.py                       # default site path
    sudo python3 add-ratelimit-location.py --site /etc/nginx/sites-enabled/galaxy
    sudo python3 add-ratelimit-location.py --from-location       # copy live headers
    sudo python3 add-ratelimit-location.py --remove
    nginx -t && systemctl reload nginx                           # always, afterwards

Idempotent. Run `nginx -t` before reloading -- it catches exactly the class of
mistake described above.
"""

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

BEGIN = "    # BEGIN report-problem rate limit (managed by add-ratelimit-location.py)"
END = "    # END report-problem rate limit"
ENDPOINT = "/api/webhooks/report_problem/data"
# Derived from the site path, not hardcoded: a hardcoded /etc/nginx/backups
# makes the script unusable (and untestable) against a site file anywhere else,
# and silently needs root even for a --dry-run-adjacent run. Override with
# --backup-dir. The default lands on /etc/nginx/backups for the normal
# /etc/nginx/sites-enabled/<site> layout.
def default_backup_dir(site: Path) -> Path:
    return site.parent.parent / "backups"

DEFAULT_HEADERS = """        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $forwarded_proto;
        proxy_set_header X-Forwarded-Host $http_host;
        proxy_set_header Upgrade $http_upgrade;"""


def build_block(headers: str, proxy_pass: str) -> str:
    return f"""{BEGIN}
    # Exact match outranks the prefix `location /`, so this captures only this
    # one URL. Headers must be repeated here: nginx does not inherit them.
    location = {ENDPOINT} {{
        limit_req zone=report_problem burst=3 nodelay;
{headers}
        {proxy_pass}
    }}
{END}
"""


def scrape_location_slash(text: str):
    """Pull the proxy_set_header and proxy_pass lines out of `location / {...}`."""
    m = re.search(r"^[ \t]*location / \{(.*?)^[ \t]*\}", text, re.M | re.S)
    if not m:
        return None, None
    body = m.group(1)
    headers = [ln.rstrip() for ln in body.splitlines() if "proxy_set_header" in ln]
    passes = [ln.strip() for ln in body.splitlines() if "proxy_pass" in ln]
    if not passes:
        return None, None
    return "\n".join(headers), passes[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="/etc/nginx/sites-enabled/galaxy")
    ap.add_argument("--from-location", action="store_true",
                    help="copy proxy_set_header/proxy_pass from the live `location /`")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--backup-dir", default=None,
                    help="where to write the backup (default: <site>/../../backups). It must NOT "
                         "be the directory holding the site file: nginx includes sites-enabled/* "
                         "wholesale, so a backup there is parsed as a second server block and "
                         "nginx fails with 'a duplicate default server'.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    site = Path(args.site)
    if not site.is_file():
        sys.exit(f"not found: {site}")
    text = site.read_text()

    if args.remove:
        if BEGIN not in text:
            print("rate-limit location not present; nothing to do")
            return 0
        start, end = text.index(BEGIN), text.index(END) + len(END)
        new = text[:start] + text[end:].lstrip("\n")
        action = "removed"
    else:
        if BEGIN in text:
            print(f"rate-limit location already present in {site}; nothing to do")
            return 0
        headers, proxy_pass = DEFAULT_HEADERS, "proxy_pass http://unix:/server/galaxy/var/gunicorn.sock;"
        if args.from_location:
            got_h, got_p = scrape_location_slash(text)
            if not got_p:
                sys.exit("--from-location: could not parse `location / { ... }`")
            headers, proxy_pass = got_h, got_p
            print(f"copied {len(headers.splitlines())} header line(s) and proxy_pass from `location /`")
        m = re.search(r"^[ \t]*location / \{", text, re.M)
        if not m:
            sys.exit("could not find `location / {` to insert before")
        new = text[:m.start()] + build_block(headers, proxy_pass) + "\n" + text[m.start():]
        action = "inserted before `location /`"

    if args.dry_run:
        print(f"[DRY RUN] would be {action} in {site} ({len(text)} -> {len(new)} bytes)")
        return 0

    # Never beside the original: sites-enabled/* is included wholesale.
    backup_dir = Path(args.backup_dir) if args.backup_dir else default_backup_dir(site)
    if backup_dir.resolve() == site.parent.resolve():
        sys.exit(f"refusing to back up into {backup_dir}: nginx would parse the backup as config")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"{site.name}.bak-ratelimit-{stamp}"
    seq = 1
    while backup.exists():
        seq += 1
        backup = backup_dir / f"{site.name}.bak-ratelimit-{stamp}-{seq}"
    shutil.copy2(site, backup)
    site.write_text(new)
    print(f"backup: {backup}")
    print(f"{site}: {action}")
    print("now run:  nginx -t && systemctl reload nginx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
