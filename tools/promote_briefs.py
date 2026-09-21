"""
Parsnips — promote verified briefs from a scratch run into summaries/.

The pipeline writes to a scratch directory (PARSNIPS_OUT) so a test can never clobber
the published archive. This is the deliberate step that moves them across, and it is
conservative on purpose:

  - only briefs that PASSED the gate are promoted. A withheld brief is not published;
    it is reported so the item can be looked at.
  - only briefs from the CURRENT schema are promoted. Schema 3 (paraphrase) and schema 4
    (selection) are different products; mixing them in one directory would show a reader
    model-written claims and verbatim sentences side by side with no way to tell them
    apart.
  - briefs from the current pipeline replace same-named older ones, and the older file
    is moved to a backup directory rather than deleted. The 291 schema-2 briefs took
    real money and time to produce, and this run's output should be reversible.
  - it never touches another year.

    python3 tools/promote_briefs.py --from /tmp/briefs_2026 --dry-run
    python3 tools/promote_briefs.py --from /tmp/briefs_2026
"""

import argparse
import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARIES = os.path.join(ROOT, "summaries")


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default=os.path.join(ROOT, "out"),
                    help="scratch directory a run wrote to (the PARSNIPS_OUT value)")
    ap.add_argument("--year", default="2026")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup", default=None,
                    help="where to move replaced files (default: <scratch>/replaced)")
    ap.add_argument("--no-status", action="store_true",
                    help="skip refreshing status.json after promoting")
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        raise SystemExit(f"no such source directory: {args.src}")

    # briefs live under <scratch>/<year>/ when the run used --year
    src_dir = os.path.join(args.src, args.year)
    if not os.path.isdir(src_dir):
        src_dir = args.src

    dest_dir = os.path.join(SUMMARIES, args.year)
    backup = args.backup or os.path.join(args.src, "replaced")

    cand = sorted(glob.glob(os.path.join(src_dir, "*.json")))
    cand = [p for p in cand if os.path.basename(p) != "index.json"]
    if not cand:
        raise SystemExit(f"no briefs found in {src_dir}")

    promote, withheld, stale = [], [], []
    for p in cand:
        b = load(p)
        if b is None:
            withheld.append((os.path.basename(p), "unreadable"))
            continue
        meta = b.get("_meta") or {}
        # SCHEMA 4 = the selection pipeline. A schema-3 brief is a paraphrase brief and
        # is skipped rather than promoted: mixing the two in one directory would put a
        # page of model-written claims beside a page of verbatim sentences with no way
        # for a reader to tell which is which.
        if meta.get("schema") != 4:
            stale.append((os.path.basename(p), f"schema {meta.get('schema')}"))
            continue
        gate = meta.get("gate") or {}
        if not gate.get("passed"):
            withheld.append((os.path.basename(p),
                             gate.get("withheld_reason") or "gate did not pass"))
            continue
        if not b.get("sections"):
            withheld.append((os.path.basename(p), "no sections"))
            continue
        promote.append(p)

    print(f"source      : {src_dir}")
    print(f"destination : {dest_dir}")
    print(f"\n  to promote : {len(promote)}")
    print(f"  withheld   : {len(withheld)}  (not published)")
    for n, why in withheld[:10]:
        print(f"      - {n}: {why}")
    if len(withheld) > 10:
        print(f"      ... {len(withheld) - 10} more")
    print(f"  skipped    : {len(stale)}  (not from the current pipeline)")
    for n, why in stale[:5]:
        print(f"      - {n}: {why}")

    # what would be replaced?
    replacing = []
    for p in promote:
        d = os.path.join(dest_dir, os.path.basename(p))
        if os.path.exists(d):
            old = load(d)
            old_schema = ((old or {}).get("_meta") or {}).get("schema")
            replacing.append((os.path.basename(p), old_schema))
    print(f"\n  replacing existing files : {len(replacing)}")
    from collections import Counter
    for schema, n in Counter(s for _, s in replacing).most_common():
        print(f"      schema {schema}: {n}")

    if args.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    os.makedirs(dest_dir, exist_ok=True)
    os.makedirs(backup, exist_ok=True)
    moved = wrote = 0
    for p in promote:
        name = os.path.basename(p)
        d = os.path.join(dest_dir, name)
        if os.path.exists(d):
            shutil.move(d, os.path.join(backup, name))
            moved += 1
        # copy rather than move: the scratch run stays intact for inspection
        shutil.copy2(p, d)
        wrote += 1

    print(f"\npromoted {wrote} brief(s); {moved} replaced file(s) moved to {backup}")

    # Promotion changes the corpus, so the status artifact is now stale. This is the
    # ONLY step that adds briefs without build_briefs.py's status refresh, which is
    # exactly how status.json came to report 2017 as empty while 343 briefs sat on
    # disk. Same best-effort contract as build_briefs.py: a failure to write status
    # must not fail a promotion that published fine.
    if wrote and not args.no_status:
        _refresh_status()
    return 0


def _refresh_status():
    import subprocess
    script = os.path.join(ROOT, "tools", "write_status.py")
    if not os.path.exists(script):
        print("status artifact NOT refreshed: tools/write_status.py missing")
        return
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                           timeout=1800)
        if r.returncode == 0:
            print("status artifact refreshed")
        else:
            print(f"status artifact NOT refreshed: {(r.stderr or '')[:120]}")
    except Exception as exc:                                  # noqa: BLE001
        print(f"status artifact NOT refreshed: {type(exc).__name__}")


if __name__ == "__main__":
    sys.exit(main())
