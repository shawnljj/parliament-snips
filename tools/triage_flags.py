#!/usr/bin/env python3
"""Print every flagged section with its own sentences, for hand-triage.

A verifier's flag is a HYPOTHESIS, not a finding. The earlier citation verifier
scored 6/7 on a ground-truth set, and the first case this tool flagged was a
false positive: it called "at the workplace" an added location when the phrase
appears verbatim in an adjacent sentence of the same section. So every flag has
to be read against the source before it is reported to anyone as a defect.

This prints, per flag, everything needed to decide: the claim, the model's
reason, the summary, and the section's actual sentences with their ids.

Usage:
  python3 tools/triage_flags.py /tmp/vs_400.json
  python3 tools/triage_flags.py /tmp/vs_400.json --only unsupported
  python3 tools/triage_flags.py /tmp/vs_400.json --limit 40
"""
import argparse
import collections
import json
import re
import sys


def words(s):
    return set(re.findall(r"[a-z0-9']+", (s or "").lower()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--brief", default="")
    a = ap.parse_args()

    data = json.load(open(a.path, encoding="utf-8"))

    # ---- summary of what was checked
    verdicts = collections.Counter(d["result"]["verdict"] for d in data)
    modes = collections.Counter()
    for d in data:
        for f in d["result"].get("failures") or []:
            if not a.only or f == a.only:
                modes[f] += 1
    print(f"checked {len(data):,}  |  " +
          "  ".join(f"{k} {v}" for k, v in verdicts.most_common()))
    print("modes: " + ", ".join(f"{k} {v}" for k, v in modes.most_common()))
    print()

    flagged = [d for d in data if d["result"]["verdict"] != "ok"]
    if a.only:
        flagged = [d for d in flagged
                   if a.only in (d["result"].get("failures") or [])]
    if a.brief:
        flagged = [d for d in flagged if a.brief in d["brief"]]
    if a.limit:
        flagged = flagged[:a.limit]

    SUSPECT = ("at the workplace", "location", "where")

    for i, d in enumerate(flagged, 1):
        r = d["result"]
        print(f"{'=' * 70}")
        print(f"{i}. [{d['year']}/{d['brief']} §{d['idx']}]  why={d['why']}")
        print(f"   label   : {d['label']}")
        print(f"   failures: {', '.join(r.get('failures') or [r['verdict']])}")
        print(f"   reason  : {r.get('reason', '')}")
        print(f"   summary : {d['summary']}")
        if r.get("evidence"):
            print(f"   at fault: {r['evidence']}")
        # is the "added" material actually present in the source?
        ev = words(r.get("evidence", ""))
        src = words(" ".join(t for _, _, t, _ in d["sentences"]))
        if ev:
            missing = {w for w in ev if w not in src and len(w) > 3}
            print(f"   evidence words NOT in source: "
                  f"{sorted(missing) if missing else '(none -- SUSPECT false positive)'}")
        print(f"   sentences ({len(d['sentences'])}):")
        for sid, sp, t, ctx in d["sentences"]:
            tag = " [context]" if ctx else ""
            print(f"     [{sid}] {sp or '(no speaker)'}{tag}: {t[:220]}")
        print()

    print(f"--- {len(flagged)} flagged case(s) shown")


if __name__ == "__main__":
    main()
