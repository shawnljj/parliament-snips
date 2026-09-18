"""
Parsnips — does the citation verifier actually work?

A verifier is only worth trusting if it catches known-bad citations AND does not flag
known-good ones. This is the test that decides whether its verdicts mean anything,
because a checker that says "supported" to everything looks identical to a checker that
works, right up until you read the output.

Cases are REAL, taken from the 2026 archive and from defects this build actually
produced, not invented to be easy:

  BAD   the question-as-fact claim that started this (oral-answer-4089)
  BAD   claims that assert a fact from a subordinate "while ..." clause
  GOOD  the same claims re-framed correctly in the asking register
  GOOD  ordinary verbatim-backed points from bills, motions and oral answers
  BAD   deliberately inverted and overstated variants

Each case carries the expected verdict class. The test passes when the verifier gets
them right; a single miss is reported rather than smoothed into a score.

    python3 tools/test_verifier.py --verifier gpt-oss:20b-cloud
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_citations as V  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "summariser"))
sys.path.insert(0, os.path.join(ROOT, "scraper"))


def real_case(brief_name, point_index, expect, note=""):
    """Pull a real claim+source pair out of the archive."""
    import build_briefs as B
    p = os.path.join("/tmp/briefs_2026/2026", f"{brief_name}.json")
    if not os.path.exists(p):
        p = os.path.join(ROOT, "summaries", "2026", f"{brief_name}.json")
    brief = json.load(open(p, encoding="utf-8"))
    kp = brief["key_points"][point_index]
    item = B.load_item(f"2026/{brief_name}.json")
    by = {s["sid"]: s for s in item["sentences"]}
    src = [f"[{s}] {by[s]['text'].strip()}" for s in kp["cites"] if s in by]
    return {"brief": brief_name, "expected": expect, "note": note,
            "claim": kp["point"], "sources": src}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifier", default=V.DEFAULT_VERIFIER)
    args = ap.parse_args()

    cases = []

    # ---- the defect that motivated this agent: a question reported as a fact
    cases.append({
        "brief": "oral-answer-4089 (the original defect)",
        "expected": "bad",
        "note": "source is a question; the claim states what the Government is doing",
        "claim": "The Government is assessing how rising fuel, utilities and logistics "
                 "costs are passed through to businesses and consumers.",
        "sources": ["[s00012] Mr Sharael Taha: asked the Deputy Prime Minister and "
                    "Minister for Trade and Industry in light of rising global energy "
                    "prices and resulting increases in fuel, utilities and logistics "
                    "costs, (a) whether the Government is monitoring how these cost "
                    "increases are being passed through to businesses and consumers;"]})

    # ---- the same underlying material, correctly framed
    cases.append({
        "brief": "oral-answer-4089 (correctly re-framed)",
        "expected": "good",
        "note": "same source, claim stays in the asking register",
        "claim": "A Member asked whether the Government is monitoring how rising fuel, "
                 "utilities and logistics costs are passed through to businesses and "
                 "consumers.",
        "sources": ["[s00012] Mr Sharael Taha: asked the Deputy Prime Minister and "
                    "Minister for Trade and Industry in light of rising global energy "
                    "prices and resulting increases in fuel, utilities and logistics "
                    "costs, (a) whether the Government is monitoring how these cost "
                    "increases are being passed through to businesses and consumers;"]})

    # ---- dropped qualifier: a "while ..." clause read as a plain assertion
    cases.append({
        "brief": "bill-780+781 (dropped qualifier)",
        "expected": "bad",
        "note": "the source hedges with 'while ... how would', the claim asserts the fact",
        "claim": "The amendments introduce a Certificate of Medical Need system for PMA "
                 "users.",
        "sources": ["[s00031] While the amendments introduce the Certificate of Medical "
                    "Need system, how would trying to catch users without the requisite "
                    "certification be enforced on the ground?"]})

    # ---- plainly supported: a verbatim-backed factual point
    cases.append({
        "brief": "injected: plainly supported",
        "expected": "good",
        "note": "source states the figure directly",
        "claim": "Road fatalities in Singapore reached a record high of 149 last year.",
        "sources": ["[s00022] Ms Sim Ann: Last year we had a record high of 149 "
                    "fatalities - almost one death every two days on the roads."]})

    # ---- reversed: the claim negates the source
    cases.append({
        "brief": "injected: reversed",
        "expected": "bad",
        "note": "source says the limit was lowered; the claim says it was raised",
        "claim": "The alcohol limit for drivers was raised.",
        "sources": ["[s00040] The amendment lowers the prescribed alcohol limit for "
                    "drivers from 80mg to 50mg of alcohol per 100ml of blood."]})

    # ---- overstates: source says "considering", claim says "will"
    cases.append({
        "brief": "injected: overstates",
        "expected": "bad",
        "note": "source is a possibility under consideration; the claim makes it a plan",
        "claim": "The Ministry will ban the sale of all vaporisers from January.",
        "sources": ["[s00055] The Ministry is considering whether further restrictions "
                    "on vaporisers are needed, and will study the evidence before "
                    "deciding."]})

    # ---- wrong subject: unrelated source
    cases.append({
        "brief": "injected: wrong subject",
        "expected": "bad",
        "note": "the source is about housing, the claim is about transport fares",
        "claim": "Bus and train fares will be frozen for two years.",
        "sources": ["[s00061] The Housing Board will launch 8,000 new flats in the "
                    "next exercise across five estates."]})

    # ---- real archive points, expected good
    for name, idx in (("bill-772", 0), ("motion-3008+3010", 3),
                      ("oral-answer-4165", 1)):
        try:
            cases.append(real_case(name, idx, "good", "real published point"))
        except Exception as exc:                                    # noqa: BLE001
            print(f"  (skipping real case {name}: {exc})")

    print(f"test cases: {len(cases)}  (good {sum(1 for c in cases if c['expected']=='good')}"
          f" / bad {sum(1 for c in cases if c['expected']=='bad')})")
    print(f"verifier  : {args.verifier}\n")

    batch = cases
    prompt = V.VERIFY_TMPL.format(n=len(batch), blocks=V.build_blocks(batch))
    raw, usage = V.ask(prompt, V.VERIFIER_SYSTEM, args.verifier)
    got = V.parse_json(raw)
    by_i = {v.get("i"): v for v in (got or {}).get("verdicts") or []}

    ok = miss = 0
    print("=" * 78)
    for i, c in enumerate(cases):
        v = by_i.get(i) or {}
        verdict = (v.get("verdict") or "unjudged").lower()
        defect = (v.get("defect") or "?")
        flagged = verdict in ("weak", "unsupported", "reversed")
        correct = (flagged if c["expected"] == "bad" else not flagged)
        ok, miss = (ok + 1, miss) if correct else (ok, miss + 1)
        mark = "PASS" if correct else "MISS"
        print(f"  [{mark}] expected={c['expected']:4s} got={verdict:11s} {defect}")
        print(f"         {c['brief']}")
        if not correct:
            print(f"         claim : {c['claim'][:104]}")
            print(f"         why   : {(v.get('why') or '')[:104]}")
    print("=" * 78)
    print(f"\n  {ok}/{len(cases)} correct  ({miss} miss)"
          f"   tokens {usage['in']:,} in / {usage['out']:,} out")
    return 1 if miss else 0


if __name__ == "__main__":
    sys.exit(main())
