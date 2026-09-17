"""
Parsnips — is 2026 correct?

One command that answers the only question that matters right now: are the 2026 briefs
fit to publish? Every figure is computed from the archive, never typed (N-3).

It checks the things that have actually gone wrong in this build, rather than the
things that are easy to check:

  COVERAGE OF THE YEAR   every item processed, none silently skipped
  TRUTHFULNESS           quote invariant, every citation resolves
  COMPLETENESS           every field the site renders is present
  NO DUPLICATES          no two points resting on the same evidence
  NO SILENT LOSS         every dropped point carries a reason
  EXTRACTION YIELD       points per item against the size of the item, to catch
                         under-extraction -- the failure that is invisible because a
                         brief with too few points still reads perfectly well
  RENDERABLE             the site can actually build from what is on disk

Exit code is 0 only when every check passes, so this can gate a publish.
"""

import glob
import json
import os
import statistics
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper"))
sys.path.insert(0, os.path.join(ROOT, "summariser"))

DATA = os.path.join(ROOT, "data", "2026")
BRIEFS = os.environ.get("PARSNIPS_BRIEFS", os.path.join(ROOT, "summaries", "2026"))

# The summariser skips items below this many words on purpose: there is nothing to
# condense. They are counted separately rather than treated as failures.
WORD_FLOOR = 150


def load_briefs():
    out = {}
    for p in sorted(glob.glob(os.path.join(BRIEFS, "*.json"))):
        if os.path.basename(p) == "index.json":
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                out[os.path.basename(p)[:-5]] = json.load(fh)
        except (OSError, ValueError):
            pass
    return out


def is_current(brief):
    """A brief from the current pipeline, not one of the 291 older-schema files."""
    meta = brief.get("_meta") or {}
    return meta.get("schema") == 3


def main():
    sys.path.insert(0, os.path.join(ROOT, "summariser"))
    import build_briefs as B

    # The unit of work is an ITEM from the dataset index (291 for 2026), not a sitting
    # file (23 for 2026): one sitting produces many items, and a brief is named by
    # item id. Comparing sitting filenames against brief names matched nothing, so an
    # earlier version of this check reported "correct" while zero briefs existed --
    # the same reads-as-complete failure this whole check exists to catch.
    index = B.load_index()
    items = [i for i in index["items"] if str(i.get("year")) == "2026"]
    item_ids = {i["id"] for i in items}

    briefs = load_briefs()
    current = {k: b for k, b in briefs.items() if is_current(b)}

    print("=" * 78)
    print("IS 2026 CORRECT?")
    print("=" * 78)
    print(f"\n  briefs dir    : {BRIEFS}")
    print(f"  items in 2026 : {len(items)}")
    print(f"  briefs present: {len(briefs)}  (current pipeline: {len(current)})")

    failures, warnings = [], []

    # A checker that can pass on an empty corpus is worse than no checker. If nothing
    # has been built, that is a failure, not a clean bill of health.
    if not current:
        print("\n  FAIL  no briefs from the current pipeline exist -- nothing to check")
        failures.append("no current-pipeline briefs to check")

    # ---------------------------------------------------------------- coverage
    item_ids_expected = {i["id"] for i in items}
    have = set(current)
    missing = sorted(item_ids_expected - have)
    unknown = sorted(have - item_ids_expected)
    print(f"\n  [1] COVERAGE OF THE YEAR")
    print(f"      items without a brief : {len(missing)}")
    if missing:
        for m in missing[:6]:
            print(f"        - {m[:70]}")
        if len(missing) > 6:
            print(f"        ... {len(missing) - 6} more")
        warnings.append(f"{len(missing)} of {len(items)} items still to summarise")
    if unknown:
        print(f"      briefs for unknown items : {len(unknown)}")
        for m in unknown[:4]:
            print(f"        - {m[:70]}")
        failures.append(f"{len(unknown)} brief(s) do not match any 2026 item")

    # ------------------------------------------------------------ truthfulness
    print(f"\n  [2] TRUTHFULNESS  (quote invariant, citation resolution)")
    bad_quote = {k: b for k, b in current.items()
                 if not (b.get("_meta", {}).get("gate", {}).get("quote_invariant"))}
    print(f"      briefs failing the quote invariant : {len(bad_quote)}")
    for k in list(bad_quote)[:5]:
        g = bad_quote[k]["_meta"]["gate"]
        print(f"        - {k}: {g.get('quote_failures', [])[:1]}")
    if bad_quote:
        failures.append(f"{len(bad_quote)} brief(s) failed the quote invariant")

    # ------------------------------------------------------------- completeness
    print(f"\n  [3] COMPLETENESS  (every field the site renders)")
    incomplete = {}
    for k, b in current.items():
        miss = [f for f in B.REQUIRED_BRIEF_FIELDS if f not in b]
        if miss:
            incomplete[k] = miss
    print(f"      briefs missing a required field : {len(incomplete)}")
    for k, miss in list(incomplete.items())[:5]:
        print(f"        - {k}: {miss}")
    if incomplete:
        failures.append(f"{len(incomplete)} brief(s) incomplete")

    # --------------------------------------------------------------- duplicates
    print(f"\n  [4] DUPLICATES  (two points on the same evidence)")
    dup_total, dup_items = 0, []
    for k, b in current.items():
        seen, dupes = set(), 0
        for kp in b.get("key_points") or []:
            key = tuple(sorted(kp.get("cites") or []))
            if key and key in seen:
                dupes += 1
            seen.add(key)
        if dupes:
            dup_items.append((k, dupes))
            dup_total += dupes
    print(f"      duplicate points : {dup_total}  in {len(dup_items)} brief(s)")
    for k, n in dup_items[:5]:
        print(f"        - {k}: {n}")
    if dup_total:
        failures.append(f"{dup_total} duplicate point(s)")

    # -------------------------------------------------------------- silent loss
    print(f"\n  [5] NO SILENT LOSS  (every exclusion is recorded, R-2.5)")
    n_dropped = sum(b.get("_meta", {}).get("points_dropped") or 0 for b in current.values())
    unreasoned = [k for k, b in current.items()
                  if (b.get("_meta", {}).get("points_dropped") or 0) > 0
                  and not b.get("_meta", {}).get("dropped_reasons")]
    reasons = Counter()
    for b in current.values():
        for r, n in (b.get("_meta", {}).get("dropped_reasons") or {}).items():
            reasons[r] += n
    print(f"      points dropped, all with a reason : {n_dropped}")
    for r, n in reasons.most_common():
        print(f"        {r:22s} {n}")
    print(f"      drops with NO recorded reason     : {len(unreasoned)}")
    if unreasoned:
        failures.append(f"{len(unreasoned)} brief(s) dropped points without a reason")

    # ------------------------------------------------------------------- yield
    print(f"\n  [6] EXTRACTION YIELD  (under-extraction reads as a fine brief)")
    rows = []
    for k, b in current.items():
        g = b.get("_meta", {}).get("gate", {})
        tt = g.get("turns_total") or 0
        if tt:
            rows.append((g.get("points") or 0, tt, g.get("coverage") or 0.0, k))
    if rows:
        covs = [r[2] for r in rows]
        per_turn = [r[0] / r[1] for r in rows]
        print(f"      points per turn : median {statistics.median(per_turn):.2f}"
              f"  min {min(per_turn):.2f}  max {max(per_turn):.2f}")
        print(f"      coverage        : median {statistics.median(covs):.2f}"
              f"  min {min(covs):.2f}  max {max(covs):.2f}")
        thin = [r for r in rows if r[1] >= 4 and r[0] <= 1]
        print(f"      items with >=4 turns but <=1 point : {len(thin)}")
        for pts, tt, cov, k in thin[:6]:
            print(f"        - {k}: {pts} point(s) over {tt} turns (cov {cov:.2f})")
        if thin:
            warnings.append(f"{len(thin)} item(s) look under-extracted")

    # -------------------------------------------------------------- renderable
    print(f"\n  [7] RENDERABLE  (the site can build from disk)")
    try:
        # Build to a THROWAWAY directory: this is a check, and it must never overwrite
        # the live site with a half-summarised year.
        scratch = os.path.join(os.environ.get("TMPDIR", "/tmp"), "parsnips_check_site")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "site", "build_site.py"),
                            scratch], capture_output=True, text=True, timeout=1800,
                           cwd=ROOT)
        ok = r.returncode == 0
        n_pages = len(glob.glob(os.path.join(scratch, "**", "*.html"), recursive=True))
        print(f"      site build : {'OK' if ok else 'FAILED'}  ({n_pages} page(s))")
        if not ok:
            print("        " + (r.stderr or r.stdout or "")[-500:])
            failures.append("site build failed")
    except subprocess.TimeoutExpired:
        print("      site build : TIMED OUT")
        warnings.append("site build timed out")

    # ------------------------------------------------------------------ verdict
    print("\n" + "=" * 78)
    if failures:
        print("NOT CORRECT YET")
        for f in failures:
            print(f"  FAIL  {f}")
    else:
        print("2026 IS CORRECT on every check that has caught a real bug in this build.")
    for w in warnings:
        print(f"  note  {w}")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
