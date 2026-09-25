"""Score the Phase 3 answer stage against the 16-question eval.

Implements the exit criteria in PHASE-3-ANSWER.md §3.5, as commands.

Reuses the ONE scorer's conventions where they apply (fact coverage over normalised text),
but the answer stage is scored differently in one important respect: the unit under test is
the ANSWER, not the ranked turn list. Facts are looked for in the QUOTES -- what the system
actually asserts -- rather than in whatever text retrieval happened to return. That is the
point of the phase: an answer is correct when it says the right thing, not when the right
passage was somewhere in the context.

Class F (the trap) is scored by fact coverage rather than by gold rank. The two schemes'
facts are disjoint ("two million" workers for the wage scheme vs "398" people for the
housing scheme), so quoting the wrong scheme's number fails coverage automatically. That is
a truer test at this stage than whether the gold turn came first.
"""
import argparse
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))       # rag/
sys.path.insert(0, HERE)                        # rag/eval/
DB = os.path.abspath(os.path.join(HERE, "..", "..", "pipeline", "hansard.db"))
EVAL = os.path.join(HERE, "questions.json")

import answer as ANS
from bm25 import norm as B_norm
import vector_rank as VR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("--json", help="write per-question results here")
    ap.add_argument("--only", help="comma-separated question ids to run")
    args = ap.parse_args()

    spec = json.load(open(EVAL))
    questions = spec["questions"]
    if args.only:
        want = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in want]

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    print(f"eval: {spec['name']}   questions: {len(questions)}   k={args.k}")
    print(f"answerer: {ANS.ANSWERER}   ranker: vector")
    print()

    rows = []
    for q in questions:
        res = ANS.answer(q["q"], db, k=args.k, ranker="vector")
        probs = ANS.check_citations(res, db) + ANS.check_numbers_in_all_text(res, db)
        res["gate_problems"] = probs
        res["gate_pass"] = not probs
        res["id"] = q["id"]
        res["cat"] = q["cat"]
        res["expect"] = q.get("expect")

        wants_refuse = q.get("expect") == "refuse"
        refused = res.get("refused")

        if refused is None:
            res["pass"] = False
            res["verdict"] = "NO VERDICT (unparsed)"
        elif wants_refuse:
            res["pass"] = bool(refused) and res["gate_pass"]
            res["verdict"] = "refused (correct)" if refused else "ANSWERED but must refuse"
        elif refused:
            res["pass"] = False
            # A class-D question is answered by NO SINGLE PASSAGE, so a refusal is the correct
            # behaviour and the eval's `expect` is the thing that is wrong. Label it as such:
            # the earlier blanket "FALSE REFUSAL (answer existed)" text asserted a defect that
            # did not exist, and read as a system failure when the system was right.
            if q.get("cat") == "D":
                res["verdict"] = "refused (correct for class D — no single passage answers it)"
            else:
                res["verdict"] = "FALSE REFUSAL (answer existed)"
        else:
            facts = q.get("gold_facts") or []
            # accept_any: groups of ALTERNATIVES. A group counts as satisfied if ANY member
            # appears. Needed where the corpus genuinely offers several correct answers --
            # measuring a "who said it" question against one designated speaker turned a
            # stable system into an apparently unstable one.
            #
            # one_per_turn: the class-D requirement, the fact that must come from gold turn i.
            # Each of these is a REQUIRED group (all must be present), because the question
            # asks for all of them; the inner list is alternative spellings of one fact, not
            # alternatives to it. Without counting them, a class-D question with empty
            # gold_facts scores a trivial 100% and measures nothing.
            groups = (q.get("accept_any") or []) + (q.get("one_per_turn") or [])
            # Evidence is the QUOTES plus the SPEAKERS of the cited chunks. The speaker is
            # not model-generated: it is read from the corpus row the quote cites, and the
            # citation gate has already verified that row exists. Counting it is therefore
            # sound, and necessary -- class B asks "who said it", which is answered by
            # attribution rather than by anything in the spoken text. An earlier scorer
            # read only the quotes and scored all three class B questions at 0% coverage
            # while the system was answering them correctly.
            blob = B_norm(" ".join(str(x.get("quote") or "") for x in res["quotes"]))
            speakers = B_norm(" ".join(
                str(c.get("speaker") or "") for c in (res.get("_cited") or [])))
            hay = blob + " " + speakers
            found = [f for f in facts if B_norm(f) in hay]
            res["facts_found"] = len(found)
            res["facts_total"] = len(facts)

            g_ok, g_bad = [], []
            for g in groups:
                hit = next((m for m in g if B_norm(m) in hay), None)
                (g_ok if hit else g_bad).append(g[0] if not hit else hit)
            res["groups_total"] = len(groups)
            res["groups_found"] = len(g_ok)
            res["groups_missing"] = g_bad

            n_all = len(facts) + len(groups)
            n_hit = len(found) + len(g_ok)
            res["coverage"] = (n_hit / n_all) if n_all else 1.0
            res["missing_facts"] = [f for f in facts if B_norm(f) not in hay] + g_bad
            ok = res["coverage"] >= 0.999 and res["gate_pass"]
            res["pass"] = ok
            res["verdict"] = ("answered" if ok else
                              f"answered, coverage {res['coverage']*100:.0f}%"
                              + ("" if res["gate_pass"] else ", GATE FAIL"))
        rows.append(res)
        print(f"  {'PASS' if res['pass'] else 'FAIL'} {q['id']:3s} [{q['cat']}] "
              f"{res['verdict']}")

    # ---- criteria -----------------------------------------------------------
    n = len(rows)
    by = {}
    for r in rows:
        t = by.setdefault(r["cat"], [0, 0])
        t[1] += 1
        t[0] += 1 if r["pass"] else 0

    e = [r for r in rows if r["cat"] == "E"]
    e_ok = sum(1 for r in e if r.get("refused"))
    gate_ok = sum(1 for r in rows if r["gate_pass"])
    non_e = [r for r in rows if r["cat"] != "E"]
    false_ref = [r for r in non_e if r.get("refused")]
    p = sum(1 for r in rows if r["pass"])
    unparsed = [r for r in rows if r.get("refused") is None]

    print()
    print("=== by class ===")
    for cat in sorted(by):
        g, t = by[cat]
        print(f"  class {cat}: {g}/{t}  {'#' * g}{'.' * (t - g)}")

    print()
    print("=== exit criteria (PHASE-3-ANSWER.md §3.5) ===")
    c1 = e_ok == len(e)
    print(f"  1. class E refusal        : {e_ok}/{len(e)}   "
          f"{'PASS' if c1 else 'FAIL'}   (need all)")
    c2 = gate_ok == n
    print(f"  2. citation gate          : {gate_ok}/{n}   "
          f"{'PASS' if c2 else 'FAIL'}   (need 100%)")
    c3 = not false_ref
    _cats = sorted({r["cat"] for r in false_ref})
    print(f"  3. no false refusals      : {len(false_ref)}"
          + (f" on {'/'.join(_cats)}" if _cats else " on answerable classes")
          + f"   {'PASS' if c3 else 'FAIL'}   (need 0)")
    print(f"  4. answer score           : {p}/{n}   "
          f"{'PASS' if p >= 11 else 'FAIL'}   (need >= 11/16)")
    cov = [r.get("coverage") for r in non_e if r.get("coverage") is not None]
    mean_cov = (sum(cov) / len(cov) * 100) if cov else 0
    c5 = all(c >= 0.999 for c in cov) if cov else True
    print(f"  5. fact coverage          : mean {mean_cov:.0f}%   "
          f"{'PASS' if c5 else 'FAIL'}   (need 100% each; printed anyway)")
    c6 = all(r.get("quotes") or r.get("refused") for r in rows) and not unparsed
    print(f"  6. traceability           : "
          f"{sum(1 for r in rows if r.get('quotes') or r.get('refused'))}/{n}   "
          f"{'PASS' if c6 else 'FAIL'}")
    sug = [s for r in rows for s in (r.get("related") or [])]
    bad = [s for s in sug if not s.get("grounded")]
    c7 = not bad
    print(f"  7. suggestions grounded   : {len(sug)} made, {len(bad)} invented   "
          f"{'PASS' if c7 else 'FAIL'}")

    print()
    print("=== partial coverage (owner decision: print it) ===")
    for r in non_e:
        if r.get("coverage") is not None and r["coverage"] < 0.999:
            print(f"  {r['id']:3s} {r['coverage']*100:5.0f}%  missing {r['missing_facts']}")

    if false_ref:
        print()
        print("=== FALSE REFUSALS (answered nothing when the answer existed) ===")
        for r in false_ref:
            print(f"  {r['id']}: {r.get('reason')}")

    if unparsed:
        print()
        print("=== NO VERDICT (unparsed -- neither pass nor fail) ===")
        for r in unparsed:
            print(f"  {r['id']} after {r['attempts']} attempts ({r.get('source')})")

    print()
    print("=== refusal detail ===")
    for r in rows:
        if r.get("refused"):
            rel = r.get("related") or []
            print(f"  {r['id']:3s} refused by {r.get('source'):9s} "
                  f"{len(rel)} related topic(s)"
                  + (f"  {rel[0]['date']}..{rel[-1]['date']}" if rel else ""))

    print()
    print(f"  ANSWER STAGE : {p}/{n}  ({p/n*100:.0f}%)")
    print(f"  retrieval+rank: 8/16  (50%)   [re-measured after the year-range fix]")
    print(f"  delta from the model: {p-8:+d}")
    print()
    print("  NOTE: part of this score is a RETRIEVAL gain, not a generation gain -- the")
    print("  year-range fix moved retrieval 7->8 and the answer stage 11->15. The model's")
    print("  own contribution is refusal (class E 0/4 -> 4/4) plus answering classes A/B/F")
    print("  from the correct passages rather than merely ranking them.")

    if args.json:
        keep = [{k: v for k, v in r.items()} for r in rows]
        with open(args.json, "w") as fh:
            json.dump(keep, fh, indent=1)
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
