#!/usr/bin/env python3
"""Verify a selection-schema brief directory against the four things that matter.

The verbatim invariant makes fabrication impossible in principle; this checks the
principle actually holds on disk, plus the three failures that are still reachable.

Usage: python3 tools/check_selection.py <briefs-dir> [year]
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                "summariser"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    d = sys.argv[1]
    year = sys.argv[2] if len(sys.argv) > 2 else "2026"
    import build_dataset as BD

    files = sorted(glob.glob(os.path.join(d, "*.json")))
    if not files:
        sys.exit(f"no briefs in {d}")

    rec_cache = {}

    def record(item_id):
        if item_id not in rec_cache:
            p = os.path.join(ROOT, "pipeline", "dataset", year, f"{item_id}.json")
            rec_cache[item_id] = ({s["sid"]: s for s in (BD.load_item(p) or [])}
                                  if os.path.exists(p) else {})
        return rec_cache[item_id]

    n_sec = n_pub = 0
    mismatch, unresolved, inferred, dups, empty_sec, no_summary = [], [], [], [], [], []
    covs = []

    for f in files:
        b = json.load(open(f, encoding="utf-8"))
        iid = os.path.basename(f)[:-5]
        rec = record(iid)
        seen = set()
        for si, sec in enumerate(b.get("sections") or []):
            n_sec += 1
            ss = sec.get("sentences") or []
            if not ss:
                empty_sec.append((iid, si))
            if not (sec.get("summary") or "").strip():
                no_summary.append((iid, si))
            for x in ss:
                n_pub += 1
                sid = x.get("sid")
                if sid in seen:
                    dups.append((iid, sid))
                seen.add(sid)
                r = rec.get(sid)
                if r is None:
                    unresolved.append((iid, sid))
                elif (r.get("text") or "").strip() != (x.get("text") or "").strip():
                    mismatch.append((iid, sid))
                if x.get("attributed") and not (x.get("speaker") or "").strip():
                    inferred.append((iid, sid))
        c = ((b.get("_meta") or {}).get("gate") or {}).get("coverage")
        if c is not None:
            covs.append(c)

    covs.sort()
    print(f"briefs {len(files)} | sections {n_sec:,} | published sentences {n_pub:,}")
    print(f"\n  1. VERBATIM INVARIANT   text mismatches vs the record : {len(mismatch)}")
    print(f"  2. SID RESOLUTION       ids with no record entry      : {len(unresolved)}")
    print(f"  3. SPEAKERS             inferred (attributed, blank)  : {len(inferred)}")
    print(f"  4. DUPLICATES           same sentence twice           : {len(dups)}")
    print(f"     empty sections                                     : {len(empty_sec)}")
    print(f"     sections without a summary                          : {len(no_summary)}")
    if covs:
        print(f"\n  coverage  min {covs[0]:.3f}  median {covs[len(covs) // 2]:.3f}  "
              f"max {covs[-1]:.3f}")
    for label, items in (("mismatch", mismatch), ("unresolved", unresolved),
                         ("inferred", inferred), ("duplicate", dups),
                         ("empty section", empty_sec), ("no summary", no_summary)):
        for it in items[:5]:
            print(f"    {label}: {it}")
    bad = len(mismatch) + len(unresolved) + len(inferred) + len(dups) + len(empty_sec)
    print(f"\n{'PASS' if not bad else 'FAIL'} — {bad} defect(s)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
