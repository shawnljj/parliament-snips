"""
Parsnips RAG — eval runner.

Two modes, and the FIRST one is the one that matters:

  --baseline   run the eval against a naive keyword search (v0) and show it FAILING.
               An eval set that has never failed is not measuring anything. Run this
               before building the pipeline, so the pipeline has something to beat.

  --system PATH  run against a real retrieval system.

Scores, per the eval spec:
  recall@k        did a gold passage reach the top k
  fact_coverage   how many of the question's gold facts appear in returned passages
  refusal         on class E, did the system correctly return nothing
  trap_safe       on class F, did the system avoid the wrong-meaning passage

Deterministic. No model needed for baseline scoring.
"""
import argparse
import glob
import html as htmlmod
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bm25 as BM  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
EVAL = os.path.join(ROOT, "rag", "eval", "questions.json")


# ------------------------------------------------------------------ normalising
def norm(text):
    """Same normalisation the existing gates use (summariser/summarise.py:norm).

    Deliberately forgiving about formatting, strict about wording.
    """
    if not text:
        return ""
    t = htmlmod.unescape(text)
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u2013", "-").replace("\u2014", "-")
    t = t.replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def strip_speaker_labels(text):
    """Remove [Please refer to Vernacular Speech.] style bracket asides."""
    return re.sub(r"\[[^\]]{0,200}\]", " ", text or "").strip()


# ------------------------------------------------------------------- the corpus
def load_corpus(years=None):
    """Return every non-empty turn as a flat record.

    Delegates to rag/bm25.py so the eval and the retriever share ONE corpus
    definition. Two loaders drifting apart is how an eval starts measuring a
    different corpus than the system it claims to test.
    """
    return BM.load_corpus(years)


# ------------------------------------------------------------ v0: BM25 baseline
def v0_search(query, turns, k=10, bm=None):
    """Baseline: real BM25 keyword search.

    Deliberately the strongest reasonable keyword-only system, not a straw man.
    Measured why this matters: an earlier, sloppier baseline (raw query-word count
    with a length>2 filter, no stopwords) ranked the gold passage for "how many
    employers received JSS payouts?" at 371st of 40,161 and returned speeches about
    unrelated bills -- it failed 16/16, which proved nothing about the eval.
    """
    if bm is None:
        bm = BM.BM25(turns)
    return [d for _, d in bm.search(query, k=k)], bm


# --------------------------------------------------------------------- scoring
def score_question(q, got, k):
    """Score one question. Returns a dict of outcomes, all deterministic."""
    gold_keys = {f"{g['report_id']}#t{g['turn']}" for g in q.get("gold", [])}
    got_keys = [t["key"] for t in got]

    out = {"id": q["id"], "cat": q["cat"], "q": q["q"]}

    if q.get("expect") == "refuse":
        # class E: correct behaviour is to return nothing usable.
        out["refused"] = len(got) == 0
        out["pass"] = out["refused"]
        return out

    # recall@k
    rank = None
    for i, key in enumerate(got_keys[:k], 1):
        if key in gold_keys:
            rank = i
            break
    out["rank"] = rank
    out["recall"] = rank is not None

    # fact coverage across everything returned
    blob = " ".join(t["norm"] for t in got)
    facts = q.get("gold_facts", [])
    found = [f for f in facts if norm(f) in blob]
    out["facts_found"] = len(found)
    out["facts_total"] = len(facts)
    out["fact_coverage"] = (len(found) / len(facts)) if facts else 0.0

    # trap check: for class F, did a wrong-meaning passage OUTRANK the gold?
    # recall@k=1 is not enough: measured, the gold for "how many people under the
    # JSS?" came back at rank 4 with recall@10=1 while housing-scheme passages sat
    # above it. The retriever "succeeded" and the answer would still be wrong.
    if "trap" in q:
        above = got_keys[:rank - 1] if rank else got_keys
        # a wrong-meaning passage is one from the other scheme's reports
        out["trap_safe"] = (rank == 1)
        out["wrong_above"] = len(above)
        if "trap" in q and rank and rank > 1:
            out["pass"] = False

    out["pass"] = out["recall"] and out["fact_coverage"] >= 0.999
    if "trap" in q and not out.get("trap_safe"):
        out["pass"] = False
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="score naive keyword search (v0)")
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("--years", type=int, nargs="*", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    spec = json.load(open(EVAL))
    questions = spec["questions"]

    print(f"eval set: {spec['name']}   questions: {len(questions)}")
    years = set(args.years) if args.years else None
    turns = load_corpus(years)
    print(f"corpus: {len(turns):,} turns"
          + (f" (years {sorted(years)})" if years else " (all years)"))
    print()

    if not args.baseline:
        print("Only --baseline is implemented so far. The pipeline it must beat "
              "does not exist yet — that is the point of running this first.")
        return 0

    results = []
    bm = BM.BM25(turns)
    for q in questions:
        got, bm = v0_search(q["q"], turns, k=args.k, bm=bm)
        results.append(score_question(q, got, args.k))

    # ---------------------------------------------------------------- report
    by_cat = Counter()
    passed = 0
    for r in results:
        tag = "PASS" if r["pass"] else "FAIL"
        if r["pass"]:
            passed += 1
        by_cat[r["cat"]] += 1
        if args.verbose or not r["pass"]:
            detail = ""
            if r.get("expect") == "refuse":
                detail = f"refused={r.get('refused')} (wanted True)"
            else:
                detail = (f"rank={r.get('rank')} recall={r.get('recall')} "
                          f"facts={r.get('facts_found')}/{r.get('facts_total')}")
            print(f"  [{tag}] {r['id']:<3} {r['q'][:52]:<52} {detail}")

    print()
    print("=" * 68)
    print(f"  v0 BASELINE (naive keyword search, k={args.k})")
    print("=" * 68)
    n = len(results)
    print(f"  passed            : {passed}/{n}  ({passed/n*100:.0f}%)")
    print(f"  failed            : {n-passed}/{n}")
    print()
    print("  by category:")
    for cat, total in sorted(by_cat.items()):
        got_n = sum(1 for r in results if r["cat"] == cat and r["pass"])
        label = spec["categories"][cat]
        print(f"    {cat}  {got_n}/{total}   {label}")

    recall_cases = [r for r in results if "recall" in r]
    if recall_cases:
        rc = sum(1 for r in recall_cases if r["recall"])
        print()
        print(f"  recall@{args.k}        : {rc}/{len(recall_cases)}")
        fc = sum(r["fact_coverage"] for r in recall_cases) / len(recall_cases)
        print(f"  mean fact coverage: {fc*100:.0f}%")

    refusals = [r for r in results if r.get("expect") == "refuse"]
    if refusals:
        rf = sum(1 for r in refusals if r["refused"])
        print(f"  correct refusals  : {rf}/{len(refusals)}")
        print("     (a keyword search never refuses — it always returns something,")
        print("      which is exactly the behaviour class E exists to catch)")

    print()
    if passed == n:
        print("  !! The baseline passed everything. That means the eval set is not")
        print("     discriminating, not that keyword search is good. Fix the eval set.")
        return 1
    print("  Baseline fails as expected. The pipeline now has a target to beat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
