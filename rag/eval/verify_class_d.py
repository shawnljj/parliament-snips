"""Verify class-D questions the RIGHT way: corpus-wide, not turn-specific.

What makes a question class D is a property of the QUESTION and the CORPUS, not of one chosen
turn: NO SINGLE TURN contains all the required facts. That is what forces the system to combine
two passages.

The first version of this check asserted something weaker and wrong -- that each fact was unique
to its own designated gold turn. Measured consequence: both D1 facts turned out to exist in ~7
turns corpus-wide, so retrieval returning a DIFFERENT valid turn was scored as a failure while
it was in fact a correct answer. That is the third time an expectation of mine was narrower than
the corpus (A1 retracted figure, B3 eight speakers, now D gold).

This asserts, per class-D question:
  (a) EVERY fact group has at least one turn in the corpus  -- the question is answerable
  (b) NO turn satisfies ALL groups at once                  -- the question is really class D
  (c) each group is reported with its corpus turn count      -- so ambiguity is visible, not hidden

Usage:  python verify_class_d.py     (exit 1 on violation)
"""
import json
import sqlite3
import sys

DB = '/Users/shawnlin/parsnips/pipeline/hansard.db'
EVAL = '/Users/shawnlin/parsnips/rag/eval/questions.json'

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

spec = json.load(open(EVAL))
bad = 0
checked = 0

for q in spec['questions']:
    if q.get('cat') != 'D':
        continue
    checked += 1
    groups = q.get('one_per_turn') or []
    print("=" * 92)
    print(f"{q['id']}: {q['q'][:88]}")

    counts = []
    for i, group in enumerate(groups, 1):
        # how many turns contain ANY member of this group
        where = " OR ".join(["text LIKE ?"] * len(group))
        params = [f"%{m}%" for m in group]
        n = db.execute(f"SELECT COUNT(*) FROM turn WHERE {where}", params).fetchone()[0]
        counts.append(n)
        print(f"  group {i}: {group}")
        print(f"      turns in corpus containing any member: {n}"
              + ("   <- NOT ANSWERABLE" if n == 0 else ""))
        if n == 0:
            bad += 1

    # (b) is there a single turn containing a member of EVERY group?
    clauses = ["(" + " OR ".join(["text LIKE ?"] * len(g)) + ")" for g in groups]
    params = []
    for g in groups:
        params += [f"%{m}%" for m in g]
    n_all = db.execute(
        "SELECT COUNT(*) FROM turn WHERE " + " AND ".join(clauses), params).fetchone()[0]
    print(f"  turns containing a member of EVERY group: {n_all}")
    if n_all > 0:
        bad += 1
        print("  -> NOT CLASS D: one passage carries all the facts, so no combination is needed")
        for r in db.execute("SELECT key FROM turn WHERE " + " AND ".join(clauses), params
                            ).fetchall()[:5]:
            print(f"       counterexample: {r['key']}")
    else:
        print("  -> VALID class D: no single turn carries every fact, both must be combined")

    # (c) ambiguity of the FACTS themselves -- a fact in many turns admits many right answers
    for i, (g, n) in enumerate(zip(groups, counts), 1):
        if n > 3:
            print(f"  NOTE group {i} is carried by {n} turns -- several passages answer it. "
                  f"The scorer accepts any of them, so this is not a defect; it only means the "
                  f"gold turn recorded in `gold` is one of several valid sources.")
    print()

print("=" * 92)
print(f"class-D questions checked: {checked}   violations: {bad}")
sys.exit(1 if bad else 0)
