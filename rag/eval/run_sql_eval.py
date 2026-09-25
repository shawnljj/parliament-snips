"""Score the SQL retrieval layer against the 16-question eval.

Reuses score_question() from run_eval.py -- ONE scorer. A second scorer would drift, and
a "fix" could then raise the score here while doing nothing on the real eval.

All retrieval logic lives in rag/sql_retrieve.py; this file only drives it and reports
the result by class, so any number here can be traced back to the retriever.

Per question it reports: the constraints derived, whether a year matched as METADATA or
as TEXT, whether the query refused, and the gold's rank. That traceability is a Phase 2b
contract requirement -- a wrong answer must be attributable to the constraint or to the
ranking rather than just "it failed".
"""
import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                           # rag/eval/
sys.path.insert(0, os.path.dirname(HERE))          # rag/
DB = os.path.abspath(os.path.join(HERE, "..", "..", "pipeline", "hansard.db"))
EVAL = os.path.join(HERE, "questions.json")

import sql_retrieve as SR
from run_eval import score_question
from bm25 import norm as B_norm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", help="write per-question results to this path")
    ap.add_argument("--ranker", choices=["bm25", "vector", "hybrid"], default="bm25",
                    help="how to order the SQL candidate pool")
    args = ap.parse_args()

    spec = json.load(open(EVAL))
    questions = spec["questions"]
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    vr = None
    if args.ranker in ("vector", "hybrid"):
        import vector_rank as VR
        vr = VR.VectorRanker()

    print(f"eval: {spec['name']}   questions: {len(questions)}   k={args.k}")
    print(f"corpus: 102,441 chunks / 91,263 turns (SQLite)")
    print()

    rows_out = []
    for q in questions:
        # --ranker bm25 is the existing retriever, untouched: the new rankers must be
        # measured as a delta against exactly what it produces, not a reimplementation.
        if args.ranker == "bm25":
            got, info = SR.retrieve(q["q"], k=args.k, db=db)
        else:
            import vector_rank as VR
            raw, info = SR.retrieve(q["q"], k=10 ** 7, db=db, with_norm=False)
            if info.get("refused") or not raw:
                got, info = raw, info
            else:
                keys = [d["key"] for d in raw]
                by_key = {d["key"]: d for d in raw}
                vrank = [t for t, _ in VR.VectorRanker.rank(vr, q["q"], keys)]
                if args.ranker == "vector":
                    order = vrank
                else:
                    order = [t for t, _ in VR.rrf(keys, vrank)]
                got = [by_key[t] for t in order[:args.k]]
                # backfill norms for only the turns actually returned, so score_question
                # sees exactly the same shape as the bm25 path
                if any(not g["norm"] for g in got):
                    conn = db
                    for g in got:
                        if g["norm"]:
                            continue
                        rows = conn.execute(
                            "SELECT cite_text FROM chunk WHERE turn_key=? ORDER BY id",
                            (g["key"],)).fetchall()
                        g["norm"] = B_norm(" ".join(r[0] for r in rows))
                info["ranker"] = args.ranker
                info["n_ranked"] = len(keys)
        r = score_question(q, got, args.k)
        r["info"] = {k: v for k, v in info.items() if k != "notes"}
        rows_out.append(r)

        tag = "PASS" if r["pass"] else "FAIL"
        why = ""
        if r.get("refused"):
            why = "  (refused)"
        elif r.get("rank"):
            why = f"  rank={r['rank']}"
        elif r.get("recall") is False:
            why = f"  gold NOT in top-{args.k}"
        if r.get("facts_total"):
            why += f"  facts={r.get('facts_found')}/{r.get('facts_total')}"

        if args.verbose or not r["pass"] or r.get("refused"):
            cons = info.get("constraints") or {}
            print(f"  {tag} {r['id']:3s} [{r['cat']}] {r['q'][:52]}{why}")
            if cons:
                print(f"        constraints: {cons}")
            for y in info.get("year_as", []):
                print(f"        year {y['year']} matched as TEXT ({y['chunks']:,} chunks)")
            for d in info.get("dropped", []):
                print(f"        dropped: {d}")
            if info.get("refused"):
                print(f"        REFUSED: {info['reason'][:108]}")
            elif not r["pass"] and got:
                print(f"        top: {got[0]['key']}  {(got[0]['title'] or '')[:40]}")
                print(f"             {got[0]['norm'][:96]!r}")

    print()
    print("=== by class ===")
    tot = {}
    for r in rows_out:
        t = tot.setdefault(r["cat"], [0, 0])
        t[1] += 1
        t[0] += 1 if r["pass"] else 0
    for cat in sorted(tot):
        g, n = tot[cat]
        print(f"  class {cat}: {g}/{n}  {'#' * g}{'.' * (n - g)}")

    p = sum(1 for r in rows_out if r["pass"])
    n = len(rows_out)
    print()
    print(f"  SQL+BM25 : {p}/{n}  ({p / n * 100:.0f}%)")
    print(f"  baseline : 5/16  (31%)")
    print(f"  delta    : {p - 5:+d}")
    print()
    print("=== refusal behaviour (class E must refuse) ===")
    for r in rows_out:
        if r["cat"] == "E":
            print(f"  {r['id']}: {'REFUSED' if r.get('refused') else 'answered':9s} "
                  f"-> {'PASS' if r['pass'] else 'FAIL'}")
    print()
    print("=== trap behaviour (class F must not be fooled) ===")
    for r in rows_out:
        if r["cat"] == "F":
            print(f"  {r['id']}: rank={r.get('rank')} trap_safe={r.get('trap_safe')} "
                  f"-> {'PASS' if r['pass'] else 'FAIL'}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump([{k: v for k, v in r.items() if k != "info"} for r in rows_out],
                      fh, indent=1)
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
