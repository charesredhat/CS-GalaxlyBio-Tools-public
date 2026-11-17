#!/usr/bin/env python3
"""
Add the "Report a problem" box to an EXISTING Galaxy welcome page.

Why not just use install-report-form.sh
---------------------------------------
That installer *replaces* static/welcome.html with Galaxy's sample page plus the
box. On a local desktop Galaxy that is harmless -- the page being replaced is the
stock "Hello, Galaxy is running!" placeholder. On a shared server whose welcome
page carries real branding and real links, replacing it throws that away, and the
timestamped backup is cold comfort once users are looking at a generic page.

This script injects only the box, leaving the rest of the page exactly as it was.

Self-contained styling
----------------------
The box in the reference page uses Bootstrap classes (`alert alert-info`,
`btn btn-primary`) that only render if the page happens to load Galaxy's
`dist/base.css`. A customised welcome page may not. So the markup here carries
inline styles and depends on no stylesheet, and uses colours that stay legible
on a light or dark page background.

Usage
-----
    python3 inject-report-box.py /server/galaxy                  # inject
    python3 inject-report-box.py /server/galaxy --dry-run        # show, change nothing
    python3 inject-report-box.py /server/galaxy --remove         # take it back out

Idempotent: a page that already has the box is left alone. Always writes a
timestamped backup before changing anything.
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

BEGIN = "<!-- BEGIN report-problem box (managed by inject-report-box.py) -->"
END = "<!-- END report-problem box -->"

# Bootstrap-classed variant, matching the markup in ../static/welcome.html.
# Use it only on a page you have confirmed loads Galaxy's dist/base.css -- then
# the box matches the page's other buttons exactly instead of merely sitting
# near them. The sandbox welcome page does load it.
BOX_BOOTSTRAP = f'''{BEGIN}
<div class="container">
    <div class="alert alert-info d-flex align-items-center justify-content-between flex-wrap">
        <span><strong>Having a problem with this Galaxy?</strong> Tell the administrator what went wrong.</span>
        <a href="/static/report-problem.html" class="btn btn-primary btn-sm mt-1 mt-sm-0">Report a problem &raquo;</a>
    </div>
</div>
{END}'''

BOX = f'''{BEGIN}
<div style="max-width:960px;margin:1rem auto;padding:0 1rem;box-sizing:border-box">
  <div style="display:flex;flex-wrap:wrap;gap:.75rem;align-items:center;
              justify-content:space-between;padding:.75rem 1rem;
              border:1px solid #9ec5e8;border-left:4px solid #2c7fc3;
              border-radius:6px;background:#eaf4fc;color:#17334a;
              font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
              font-size:.95rem;line-height:1.4">
    <span><strong>Having a problem with this Galaxy?</strong>
          Tell the administrator what went wrong.</span>
    <a href="/static/report-problem.html"
       style="flex:none;padding:.4rem .9rem;border-radius:4px;background:#2c7fc3;
              color:#fff;text-decoration:none;font-weight:600;white-space:nowrap">
      Report a problem &raquo;</a>
  </div>
</div>
{END}'''


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("galaxy_root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--position", choices=("top", "bottom"), default="top",
                    help="top (default) puts the box at the start of the page content, which is "
                         "where a problem-report prompt is actually useful; bottom appends it "
                         "before </body>, after any footer. Falls back to bottom if the page has "
                         "no recognisable content container.")
    ap.add_argument("--style", choices=("inline", "bootstrap"), default="inline",
                    help="inline (default) depends on no stylesheet and is safe on any page; "
                         "bootstrap matches Galaxy's own buttons but renders correctly ONLY if "
                         "the page loads dist/base.css -- check before choosing it")
    args = ap.parse_args()

    page = Path(args.galaxy_root) / "static" / "welcome.html"
    if not page.is_file():
        sys.exit(f"not found: {page}")

    html = page.read_text(encoding="utf-8", errors="surrogateescape")
    present = BEGIN in html

    if args.remove:
        if not present:
            print("box is not present; nothing to remove")
            return 0
        start, end = html.index(BEGIN), html.index(END) + len(END)
        # also swallow one trailing newline the injection added
        new = html[:start] + html[end:].lstrip("\n")
        action = "removed"
    else:
        if present:
            print(f"box already present in {page}; nothing to do")
            return 0
        box = BOX_BOOTSTRAP if args.style == "bootstrap" else BOX
        lower = html.lower()

        # `top` means the first content container, so the prompt sits with the
        # page's own content instead of below the funding footer where nobody
        # scrolls. Matched case-insensitively on the opening tag only, so a page
        # using single quotes or extra classes still hits.
        idx = -1
        if args.position == "top":
            for needle in ('<div class="container"', "<div class='container'"):
                idx = lower.find(needle)
                if idx != -1:
                    where = "inserted before the first content container"
                    break

        if idx == -1:
            # Prefer just inside </body>; fall back to appending so a fragment
            # without a body tag still works.
            idx = lower.rfind("</body>")
            if idx == -1:
                new = html.rstrip("\n") + "\n" + box + "\n"
                where = "appended (no </body> found)"
                idx = None
            else:
                where = "inserted before </body>"
        if idx is not None:
            new = html[:idx] + box + "\n" + html[idx:]
        action = f"injected ({args.style} style), {where}"

    if args.dry_run:
        print(f"[DRY RUN] would be {action} in {page}")
        print(f"[DRY RUN] page is currently {len(html)} bytes, would become {len(new)}")
        return 0

    # Second-resolution stamps collide when inject and --remove run in the same
    # second, which would silently overwrite the only copy of the original.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = page.with_name(f"welcome.html.bak.{stamp}")
    seq = 1
    while backup.exists():
        seq += 1
        backup = page.with_name(f"welcome.html.bak.{stamp}-{seq}")
    shutil.copy2(page, backup)
    page.write_text(new, encoding="utf-8", errors="surrogateescape")
    print(f"backup: {backup}")
    print(f"{page}: {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
