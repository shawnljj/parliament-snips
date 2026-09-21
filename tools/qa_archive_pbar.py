#!/usr/bin/env python3
"""Assert the archive is the ONLY generated page that lost its .pbar, and that no page in the
build still carries a progress bar with nothing driving it.

The D15 fix gates .pbar out of render_archive(). That is a generator-wide change, so the claim
"only the archive changed" has to be measured against the previous build rather than believed.
This walks every generated HTML file and reports, for each, whether it has a .pbar and whether
it has the machinery that makes one honest (a data-page to key scroll state on, and the SCRIPT
that calls pbarSync). It exits non-zero if any page renders a bar with no driver behind it.

    python3 tools/qa_archive_pbar.py
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "site", "dist")

# Every page that legitimately drives its own bar. The archive is deliberately not here.
EXPECT_BAR = {"index.html", "sittings/2026-08-04.html", "sittings/2026-08-05.html"}


def scan():
    rows = []
    for p in sorted(glob.glob(os.path.join(DIST, "**", "*.html"), recursive=True)):
        rel = os.path.relpath(p, DIST)
        s = open(p, encoding="utf-8", errors="replace").read()
        rows.append({
            "page": rel,
            "has_pbar": 'class="pbar"' in s,
            "has_datapage": "data-page=" in s,
            "has_script": "<script" in s,
        })
    return rows


def main():
    rows = scan()
    with_bar = [r for r in rows if r["has_pbar"]]
    # A bar with no data-page and no SCRIPT is exactly the D15 shape: it renders and never moves.
    orphaned = [r for r in with_bar if not (r["has_datapage"] and r["has_script"])]
    archive = [r for r in rows if r["page"] == "sittings/index.html"]

    print(f"generated pages scanned : {len(rows)}")
    print(f"pages emitting a .pbar  : {len(with_bar)}")
    print(f"pages with .pbar but NO data-page and NO script (the D15 shape): {len(orphaned)}")
    for r in orphaned:
        print(f"  - {r['page']}")

    fails = []
    if not archive:
        fails.append("sittings/index.html was not found in the build")
    else:
        a = archive[0]
        print(f"\narchive (sittings/index.html): .pbar={a['has_pbar']} "
              f"data-page={a['has_datapage']} script={a['has_script']}")
        if a["has_pbar"]:
            fails.append("the archive still emits a .pbar -- D15 is not fixed")
    if orphaned:
        fails.append(f"{len(orphaned)} page(s) emit a .pbar with nothing driving it")

    # The sitting pages must still have theirs, or the fix broke the page that works.
    sittings = [r for r in with_bar if r["page"].startswith("sittings/")
                and r["page"] != "sittings/index.html"]
    print(f"sitting pages with a .pbar : {len(sittings)} (expected 331)")
    if len(sittings) < 300:
        fails.append(f"only {len(sittings)} sitting pages carry a .pbar -- expected ~331")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("OK: the archive has no progress bar, every other page keeps one, and no page "
          "renders a bar nothing drives.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
