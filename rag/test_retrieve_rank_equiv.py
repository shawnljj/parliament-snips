#!/usr/bin/env python3
"""Gate: `rank=False` must produce the SAME refusal decisions as the ranked path.

The optimisation skips the BM25 build, which `answer.py` was paying for and then discarding (it
re-ranks by vector). The risk is that skipping the scoring also skips a DECISION -- the refusal
rules live in the same function.

So this asserts equivalence on the things that decide behaviour, over every eval question:
  * info['refused'] identical
  * the candidate pool (set of turn keys) identical
  * the refusal REASON identical
and reports the speed difference.

Exit non-zero on any mismatch.

Run: python3 test_retrieve_rank_equiv.py
"""
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sql_retrieve as SR

DB = os.path.join(HERE, '..', 'pipeline', 'hansard.db')
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

qs = json.load(open(os.path.join(HERE, 'eval', 'questions.json')))
qs = qs if isinstance(qs, list) else qs.get('questions', [])

# The k the ANSWER STAGE actually uses. Equivalence only holds at k >= pool size, because the
# ranked path's `term_hits` is a maximum over the returned slice. Testing at a smaller k measures
# a configuration nobody calls.
K = 10 ** 7

print(f"questions: {len(qs)}   k={K:,} (the answer stage's own k)")
ok = True
t_rank = t_skip = 0.0

for q in qs:
    text = q['q']
    t0 = time.time()
    got_r, info_r = SR.retrieve(text, k=K, db=db, with_norm=False, rank=True)
    t_rank += time.time() - t0

    t0 = time.time()
    got_s, info_s = SR.retrieve(text, k=K, db=db, with_norm=False, rank=False)
    t_skip += time.time() - t0

    same_ref = bool(info_r.get('refused')) == bool(info_s.get('refused'))
    same_reason = (info_r.get('reason') or '') == (info_s.get('reason') or '')
    same_pool = {d['key'] for d in got_r} == {d['key'] for d in got_s} if not info_r.get('refused') \
        else True
    same_cand = info_r.get('candidate_turns') == info_s.get('candidate_turns')
    same_hits = info_r.get('term_hits') == info_s.get('term_hits')

    bad = [n for n, v in (('refused', same_ref), ('reason', same_reason), ('pool', same_pool),
                          ('candidates', same_cand), ('term_hits', same_hits)) if not v]
    if bad:
        ok = False
        print(f"  [FAIL] {q['id']:<4} differs on: {bad}")
        print(f"         ranked: refused={info_r.get('refused')} cand={info_r.get('candidate_turns')} "
              f"hits={info_r.get('term_hits')}")
        print(f"         skip  : refused={info_s.get('refused')} cand={info_s.get('candidate_turns')} "
              f"hits={info_s.get('term_hits')}")
    else:
        flag = ' (refused)' if info_s.get('refused') else ''
        print(f"  [PASS] {q['id']:<4} pool {info_s.get('candidate_turns'):>6,} "
              f"hits {info_s.get('term_hits')}/{info_s.get('n_terms')}{flag}")

print()
print(f"  ranked total   {t_rank:6.1f}s")
print(f"  rank-skipped   {t_skip:6.1f}s")
if t_skip > 0:
    print(f"  speedup        {t_rank/t_skip:.1f}x")
print()
print(f"  NOTE: equivalence is asserted at k={K:,} only -- at a smaller k the ranked path's")
print("        term_hits is a max over the returned slice and the two legitimately differ.")
print()
print("RESULT:", "RANK-SKIP IS DECISION-EQUIVALENT" if ok else "DECISIONS DIFFER — DO NOT SHIP")
sys.exit(0 if ok else 1)
