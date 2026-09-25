"""Phase 2b: the SQL retrieval layer.

Owner architecture: "the SQL database is where the retrieval happens, and the RAG does
the guessing which sentence ids or paragraphs are relevant."

Sharpened: SQL FILTERS (exact, explainable, cannot be wrong -- it is a predicate);
ranking GUESSES (BM25 now, vectors later, for the harder questions). Both are retrieval.
The split is constraint vs similarity, not SQL vs RAG.

Measured before building this: a SQL filter shrinks the candidate pool for 12 of the 16
eval questions, from 102,441 chunks to a mean of 1,438 (1.4%). For four of them -- the
JSS trap among them -- it goes to 64 and 11.

TWO KINDS OF YEAR (owner correction, confirmed by measurement)
--------------------------------------------------------------
A year in a question can mean two different things, and conflating them discards evidence:

  `year` column  = when the sitting HAPPENED      (metadata)
  year in text   = what Members DISCUSSED, incl. future plans   (content)

Measured: 39 chunks mention both "2027" and "GST" -- e.g. bill-480#t3 (2020) "clause 15
affords profit and capital gain exemptions through till the end of 2027" -- while no
sitting happened in 2027. An earlier version of this module dropped the 2027 constraint
because no sitting matched, then searched unfiltered and returned CPF and transport
passages: the evidence for the question was silently discarded. "2050" appears in 331
chunks, all text-only.

So a year is tried as a SITTING filter first, and only if that matches nothing is it
retried as a TEXT filter. It is abandoned only when NEITHER matches.

REFUSAL (Phase 2b contract)
---------------------------
A filter matching nothing is a WRONG CONSTRAINT, not an empty answer -- but two 0-row
cases differ, and they have opposite correct outcomes:

  each constraint satisfiable ALONE, conjunction empty -> the premise is false -> REFUSE
  a constraint unsatisfiable even alone               -> unreliable -> drop and retry

A raw BM25 SCORE FLOOR is NOT used for refusal: scores are not comparable across pools
because IDF depends on pool size. Measured, filtered pools score 2.5-7 while unfiltered
score 34-42, so one floor would reject every good answer in a small pool. The refusal
signal is instead pool-independent: how many DISTINCTIVE query terms the best candidate
actually contains.

Scoring is at TURN level, because that is what the eval's gold references are:
{"report_id": ..., "turn": N} -> "{report_id}#t{N}".
"""
import argparse
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DB = os.path.abspath(os.path.join(HERE, "..", "pipeline", "hansard.db"))

import bm25 as B

# Terms naming a report/subject. Curated from the CORPUS, not invented.
KNOWN_TERMS = {
    "jobs support scheme": "Jobs Support Scheme",
    "job support scheme": "Jobs Support Scheme",
    "jss": "Jobs Support Scheme",
    "joint singles scheme": "Joint Singles Scheme",
    "progressive wage model": "Progressive Wage Model",
    "pwm": "Progressive Wage Model",
    "national day parade": "National Day Parade",
    "ndp": "National Day Parade",
}

# Terms whose meaning depends on something OUTSIDE the wording: "JSS" is Jobs Support
# Scheme (2020-21) AND Joint Singles Scheme (2023-26) -- measured 64 chunks vs 11.
AMBIGUOUS = {"jss", "ndp"}

STOPWORDS = set("""a an the of to in on at by for with from is are was were be been being
do does did how what which who whom whose when where why many much and or but if then
than that this these those it its as into over under about between during after before
did say said have has had will would can could should may might must not no nor so such
mr mrs ms dr prof hon speaker sir madam members member parliament singapore""".split())


def derive_constraints(question, explicit_year=None):
    """Derive constraints from a question. Returns (constraints, notes).

    Conservative by design: an unreliable constraint is worse than none, because it
    silently excludes the gold row. This is the part that later wants an LLM; kept as a
    separate testable function so it can be swapped without touching the query path.
    """
    q = question.lower()
    c, notes = {}, []
    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", q)]
    rng = re.findall(r"between (\d{4}) and (\d{4})", q)
    if rng:
        years += list(range(int(rng[0][0]), int(rng[0][1]) + 1))
    if explicit_year:
        years = [explicit_year]
    if years:
        c["years"] = sorted(set(years))
    else:
        notes.append("no year in the question")

    term = None
    for k, canonical in KNOWN_TERMS.items():
        if re.search(rf"\b{re.escape(k)}\b", q):
            term = canonical
            break
    if term:
        c["title_term"] = term
        if any(a in q for a in AMBIGUOUS):
            notes.append(f"ambiguous term -> '{term}' (JSS = Jobs Support Scheme 2020-21 / "
                         f"Joint Singles Scheme 2023-26); a year disambiguates")
    else:
        notes.append("no known subject term")

    # distinctive query terms: used only as a relevance SIGNAL, never as a filter
    c["_terms"] = [w for w in re.findall(r"[a-z0-9$%.,]+", q)
                   if w not in STOPWORDS and len(w) > 2]
    return c, notes


def retrieve(question, k=10, explicit_year=None, db=None, with_norm=True, rank=True):
    """Retrieve turns for a question, applying the constraint and refusal rules.

    with_norm=False omits the per-turn `norm` text on the returned turns. It exists so a
    caller that only wants the ORDER of a large pool (the vector/hybrid rankers order
    thousands of turns and then keep k) does not pay to normalise every one of them.

    rank=False skips the BM25 scoring and returns the candidate turns UNORDERED. It exists for
    callers that re-rank the whole pool themselves (the answer stage, which orders by vector
    similarity) -- for those the BM25 build is pure cost, and measured cost at that: scoring
    91,263 turns takes 13s, and on a question with no constraints the answer stage called this
    with k=10**7, so EVERY question paid it and then discarded the result.

    EQUIVALENCE RESTRICTION, measured: `info['term_hits']` in the ranked path is the maximum over
    the RETURNED slice (top-k), not over the pool -- so the two paths agree only when k >= the
    pool size. At k=50 they disagree on a question whose 8th term-hit sits below the top 50
    (measured: D1, hits 7 ranked vs 8 unranked), and since `term_hits == 0` drives a refusal, a
    smaller k could change the DECISION. The answer stage calls with k=10**7, where the pool is
    returned in full and the two are identical; `test_retrieve_rank_equiv.py` asserts that at
    that k over every eval question and fails the build if they ever diverge.

    Returns (got, info). info['refused'] is the Phase 2b refusal decision.
    """
    own = db is None
    if own:
        db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    c, notes = derive_constraints(question, explicit_year)
    info = {"constraints": {k: v for k, v in c.items() if not k.startswith("_")},
            "notes": notes, "refused": False, "reason": None,
            "year_as": [], "dropped": []}

    # ---- years: sitting filter first, then TEXT (owner correction) -------------
    #
    # A year in a question means one of two DIFFERENT things, and they must not be treated
    # alike (owner's correction: "some speakers may have mentioned future plans that name
    # years not in the corpus. These should not be outright rejected, because these years
    # can be found in the text").
    #
    #   a SITTING year  -> when it was said. A real filter: `year = ?` on metadata.
    #   a DISCUSSED year -> what was talked about. Lives in the TEXT, and may be a future
    #                       year that no sitting ever had. Measured: "2027" appears in 407
    #                       chunks, and alongside "GST" in 39 of them -- including a 2020
    #                       turn about an exemption running "through till the end of 2027".
    #                       A metadata filter excludes that evidence entirely.
    #
    # A RANGE of discussed years is CONTEXT, not a constraint. Measured on A4 ("BTO flats
    # between 2011 and 2013"): AND-ing the three years required ONE chunk to mention all
    # three, which no passage does, so the pool collapsed to irrelevant 2023 material and
    # the answer stage correctly refused a question the corpus CAN answer. A range is
    # therefore scored, not filtered -- one or more of its years must appear, which keeps
    # the passages that discuss the period without demanding the impossible conjunction.
    kept, as_text = [], []
    for y in (c.get("years") or []):
        n = db.execute("SELECT COUNT(*) FROM chunk WHERE year = ?", (y,)).fetchone()[0]
        if n:
            kept.append(y)
            continue
        # No sitting that year -- but a Member may have DISCUSSED it.
        nt = db.execute("SELECT COUNT(*) FROM chunk WHERE cite_text LIKE ?",
                        (f"%{y}%",)).fetchone()[0]
        if nt:
            as_text.append(y)
            info["year_as"].append({"year": y, "chunks": nt})
        else:
            info["dropped"].append(f"year {y}: no sitting and not mentioned in any text")

    # ---- subject term: match CONTENT, not title (exit criterion 3) -------------
    #
    # Measured: matching the term against `title` EXCLUDES the gold for 4 of the 9
    # term-bearing questions. The gold report DISCUSSES the subject without having the
    # term in its title:
    #
    #   Joint Singles Scheme   title=11 chunks (gold absent)  content=57 (gold present)
    #   Progressive Wage Model title=112 (gold absent)        content=407 (gold present)
    #   Jobs Support Scheme    title=63  (gold present)       content=261
    #
    # A filter that excludes the gold cannot be repaired by ranking -- the right answer is
    # not in the pool -- and it is worse than no filter, because it returns confident
    # passages from OTHER reports that happen to carry the term in the title. Measured on
    # F2: facts_found 2/2 while the gold was absent, i.e. the right facts in the wrong
    # document.
    term = c.get("title_term")
    if term:
        n = db.execute("SELECT COUNT(*) FROM chunk WHERE cite_text LIKE ?",
                       (f"%{term}%",)).fetchone()[0]
        if not n:
            info["dropped"].append(f"term {term!r} appears in no chunk text")
            term = None

    # ---- conjunction ---------------------------------------------------------
    where, params = [], []
    if kept:
        where.append("year IN (%s)" % ",".join("?" * len(kept)))
        params += kept
    if as_text:
        # a RANGE means "one or more of these years", not "all of them" (see above)
        where.append("(" + " OR ".join("cite_text LIKE ?" for _ in as_text) + ")")
        params += [f"%{y}%" for y in as_text]
    if term:
        where.append("cite_text LIKE ?")
        params.append(f"%{term}%")
    sql = "SELECT * FROM chunk" + ((" WHERE " + " AND ".join(where)) if where else "")
    rows = db.execute(sql, params).fetchall()
    had_constraint = bool(where)

    if not rows and had_constraint:
        # Each constraint holds alone, the conjunction is empty -> the COMBINATION is
        # false. Refuse. (E2: JSS exists, 2026 exists, no JSS in 2026.)
        info["refused"] = True
        info["reason"] = ("each constraint holds alone but the combination is empty -> "
                          f"the premise is false: {info['constraints']}")
        if own:
            db.close()
        return [], info
    if not rows:
        rows = db.execute("SELECT * FROM chunk").fetchall()
        info["fell_back"] = True
        notes.append("no constraint applied -> unfiltered search")

    # ---- rank INSIDE the candidate pool --------------------------------------
    # A ranker may reorder but can never re-introduce a row SQL excluded.
    # Ranked over TURNS: a turn with 20 chunks must not fill the top-k alone.
    by_turn = {}
    for r in rows:
        by_turn.setdefault(r["turn_key"], []).append(r)
    docs = []
    for tkey, rs in by_turn.items():
        text = " ".join(x["cite_text"] for x in rs)
        docs.append({"key": tkey, "text": text})
    info["candidate_turns"] = len(docs)

    terms = c.get("_terms") or []

    if not rank:
        # The caller re-ranks the pool itself, so scoring it here is wasted work (13s over
        # 91,263 turns). Every refusal decision below is still computed -- over the WHOLE
        # pool, not the top-k -- so the outcome is identical to the ranked path.
        lows = [d["text"].lower() for d in docs]
        best_hits = max((sum(1 for t in terms if t in low) for low in lows), default=0)
        info["term_hits"] = best_hits
        info["n_terms"] = len(terms)
        info["superseded_returned"] = []
        info["rank_skipped"] = True
        if terms and best_hits == 0:
            info["refused"] = True
            info["reason"] = ("best candidate contains none of the distinctive query terms "
                              f"{terms[:6]} -> nothing retrieved addresses the question")
            if own:
                db.close()
            return [], info
        out = [{"key": d["key"], "norm": "", "chunk_ids": [x["id"] for x in by_turn[d["key"]]],
                "score": 0.0, "hits": 0, "title": by_turn[d["key"]][0]["title"],
                "date": by_turn[d["key"]][0]["date"],
                "speaker": by_turn[d["key"]][0]["speaker"]} for d in docs]
        if own:
            db.close()
        return out, info

    for d in docs:
        d["tokens"] = B.tokenise(B.norm(d["text"]))
    bm = B.BM25(docs)
    scored = sorted(((bm.score(question, i), i) for i in range(len(docs))),
                    key=lambda x: (-x[0], docs[x[1]]["key"]))

    # ---- supersession: a turn whose figure was RETRACTED is down-ranked, never dropped --
    #
    # Measured on A1: the correction sat at rank 1 and the model quoted the RETRACTED figure
    # from rank 9 -- both citations verified, because both are real text. The relation is
    # recorded in the `supersession` table (101 announcing turns, 90 with corrected text).
    #
    # Down-rank rather than exclude, deliberately:
    #   * the announcing turn is still the record of what was said, and a user may legitimately
    #     ask about the correction itself;
    #   * exclusion is the failure class already measured (a prefilter that removes the gold
    #     cannot be repaired by ranking);
    #   * the correction pair is meant to be READ TOGETHER -- the announcement explains what
    #     changed, the corrected turn holds the new figure.
    # When the corrected turn is present it is promoted above its announcer, so the pair always
    # arrives in the order that makes sense.
    try:
        sup = {r["announce_turn"]: r["supersede_turn"] for r in db.execute(
            "SELECT announce_turn, supersede_turn FROM supersession")}
    except sqlite3.OperationalError:
        sup = {}          # table absent (older build) -> behave exactly as before
    ranked_keys = {d["key"] for d in docs}

    out, best_hits = [], 0
    for score, i in scored[:k]:
        d = docs[i]
        low = d["text"].lower()
        hits = sum(1 for t in terms if t in low)
        best_hits = max(best_hits, hits)
        rs = by_turn[d["key"]]
        rec = {"key": d["key"], "norm": B.norm(d["text"]) if with_norm else "",
               "chunk_ids": [x["id"] for x in rs], "score": score, "hits": hits,
               "title": rs[0]["title"], "date": rs[0]["date"],
               "speaker": rs[0]["speaker"]}
        if d["key"] in sup and sup[d["key"]]:
            rec["superseded_by"] = sup[d["key"]]
            rec["superseded_by_present"] = sup[d["key"]] in ranked_keys
        out.append(rec)
    info["term_hits"] = best_hits
    info["n_terms"] = len(terms)
    info["superseded_returned"] = [r["key"] for r in out if r.get("superseded_by")]

    # promote each corrected turn directly above its announcer when both are returned
    if len(out) > 1:
        idx = {r["key"]: j for j, r in enumerate(out)}
        for j, r in enumerate(list(out)):
            s = r.get("superseded_by")
            if s and s in idx and idx[s] > idx[r["key"]]:
                cur = {x["key"]: x for x in out}
                order = [x["key"] for x in out]
                order.remove(s)
                order.insert(order.index(r["key"]), s)
                out = [cur[key] for key in order]

    # Refuse only on a TOTAL miss: nothing retrieved contains any distinctive query term.
    # Pool-independent, unlike a raw score. Deliberately conservative -- a false refusal
    # is a wrong answer, so this fires only when nothing addresses the question at all.
    if terms and best_hits == 0:
        info["refused"] = True
        info["reason"] = ("best candidate contains none of the distinctive query terms "
                          f"{terms[:6]} -> nothing retrieved addresses the question")
        if own:
            db.close()
        return [], info

    if own:
        db.close()
    return out, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True)
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("--year", type=int)
    args = ap.parse_args()

    got, info = retrieve(args.q, k=args.k, explicit_year=args.year)
    print(f"question: {args.q}")
    print(f"constraints: {info['constraints']}")
    for n in info["notes"]:
        print(f"  note   : {n}")
    for d in info.get("dropped", []):
        print(f"  dropped: {d}")
    for y in info.get("year_as", []):
        print(f"  year {y['year']} matched as TEXT: {y['chunks']:,} chunks mention it")
    if info.get("refused"):
        print(f"  REFUSED: {info['reason']}")
    print(f"candidate turns: {info.get('candidate_turns', 0):,}   "
          f"distinctive terms hit: {info.get('term_hits')}/{info.get('n_terms')}")
    print()
    for i, r in enumerate(got, 1):
        print(f"  {i:2d}. [{r['score']:7.3f} hits={r['hits']:>2}] {r['key']:26s} "
              f"{(r['title'] or '')[:30]:30s} {r['date']}")
        print(f"      {r['norm'][:110]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
