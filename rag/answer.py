"""Phase 3: the answer stage.

Turns retrieved passages into an answer that is quotable, checkable, and allowed to refuse.
Implements PHASE-3-ANSWER.md.

NOT a retrieval fix. Retrieval is 7/16 and stays 7/16; any change to constraints or ranking
would make the model's delta unmeasurable. The one thing retrieval provably cannot do is
REFUSE -- class E is 0/4 under BM25, SQL-only and vector ranking alike -- because a wrong
passage is still similar to the question, so no similarity threshold separates "addresses
it" from "mentions it".

THE ANSWER IS A LIST OF QUOTES, NOT PROSE
-----------------------------------------
Owner decision: "Verbatim quotes + citations only". Each item is a verbatim span plus the
chunk id it came from. That choice is what makes the citation gate mechanical: the quote
can be checked by substring against the stored `cite_text`, with no model involved.

REFUSAL CARRIES GROUNDED SUGGESTIONS
------------------------------------
Owner decision: on refusal, suggest 1-3 related topics while being clear the answer was NOT
found. The model SELECTS from the report titles retrieval already returned; it never writes
a topic. A freely-written suggestion is a new hallucination surface sitting exactly where
the design is trying to be honest, and a plausible non-existent Hansard topic is harder to
spot than a wrong number. Suggestions are therefore gate-checked against the report table.

WHAT THE MODEL SEES
-------------------
The retrieved CHUNKS, not whole turns. Measured: a returned turn's `norm` is only the
chunks the SQL filter matched, so building a prompt from it showed the model as little as
5% of a turn (1,216 of 23,024 tokens). Chunks are the quoting unit anyway -- median 104
tokens, max 1,408 -- so the prompt is cheap and the quote is exact.

MODEL CHOICE
------------
Cloud only (local chat models were removed from this machine). The answerer is the profile
default. An independent verifier model exists purely as a second opinion on REFUSAL
decisions, and is a different family on purpose: a model checking its own refusal is not
independent evidence.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "eval"))
PIPELINE = os.path.abspath(os.path.join(HERE, "..", "pipeline"))
DB = os.path.join(PIPELINE, "hansard.db")

import sql_retrieve as SR
import vector_rank as VR
from bm25 import norm as B_norm, tokenise

OLLAMA = "http://localhost:11434/api/chat"
ANSWERER = "deepseek-v4.1-flash:cloud"
VERIFIER = "gpt-oss:20b-cloud"
TOP_CHUNKS_PER_TURN = 3      # the best chunk plus its neighbours inside the same turn


def _load_supersession(db):
    """turn -> the turn that corrects it, and the reverse. Read once, module-level.

    A missing table means an older build: fall back to empty maps, so behaviour is exactly as
    before rather than crashing.
    """
    global SUPERSEDED, IS_CORRECTION_OF
    if SUPERSEDED is not None:
        return
    SUPERSEDED, IS_CORRECTION_OF = {}, {}
    try:
        for r in db.execute("SELECT announce_turn, supersede_turn FROM supersession "
                            "WHERE supersede_turn IS NOT NULL"):
            SUPERSEDED[r["announce_turn"]] = r["supersede_turn"]
            IS_CORRECTION_OF[r["supersede_turn"]] = r["announce_turn"]
    except Exception:
        pass


SUPERSEDED = None
IS_CORRECTION_OF = None


# --------------------------------------------------------------------------- context

def chunks_for_turn(db, turn_key, chunk_ids=None, limit=TOP_CHUNKS_PER_TURN):
    """The chunks of a turn that the model will see.

    Preferred: the ids the ranker already scored. Fallback: the turn's first chunks. The
    fallback matters because a turn can be long and its best chunk is not necessarily the
    chunk that matched a constraint -- but only the matched chunks are scored today, which
    is the visibility limitation recorded in the Phase 3 contract.
    """
    if chunk_ids:
        rows = []
        for cid in chunk_ids[:limit]:
            r = db.execute("SELECT * FROM chunk WHERE id=?", (cid,)).fetchone()
            if r:
                rows.append(r)
        if rows:
            return rows
    return db.execute("SELECT * FROM chunk WHERE turn_key=? ORDER BY id LIMIT ?",
                      (turn_key, limit)).fetchall()


def build_context(db, got, max_chunks=30):
    """Assemble the passage block. Returns (chunks, text).

    Fields are LABELLED one per line, deliberately. An earlier version rendered each
    passage as "[id] Title — date — (Speaker)" followed by the text, and that single
    ambiguous line caused three separate failures:

      * the model copied the WHOLE citation line back as the "title" (11 of 19 refusal
        suggestions were ungrounded for this reason alone)
      * it quoted report TITLES as if they were passage text, which the verbatim gate
        correctly rejected
      * the speaker was buried inside a parenthetical, so "who said this" questions had no
        clear place to look

    None of the three was a model error. The context did not distinguish the fields, so the
    model guessed, and the gate caught every guess. Labelling them removes the ambiguity
    instead of asking the model to parse a convention.
    """
    chosen, seen = [], set()
    for g in got:
        for r in chunks_for_turn(db, g["key"], g.get("chunk_ids")):
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            chosen.append(r)
            if len(chosen) >= max_chunks:
                break
        if len(chosen) >= max_chunks:
            break

    blocks = []
    for i, r in enumerate(chosen, 1):
        # A passage that announces a correction, or is one, must SAY SO in the context. The
        # model cannot be asked to infer authority from position: measured, it was offered the
        # correction at rank 1 and quoted the retracted figure from rank 9, because nothing in
        # the passage block indicated that one superseded the other.
        note = ""
        if r["turn_key"] in (SUPERSEDED or {}):
            note = ("\nNOTE: this passage states a figure or fact that was LATER CORRECTED. "
                    f"See passage id {SUPERSEDED[r['turn_key']]} which gives the corrected "
                    "text. When the corrected passage is present, quote the corrected one.")
        elif r["turn_key"] in (IS_CORRECTION_OF or {}):
            note = ("\nNOTE: this passage is the CORRECTED version of an earlier statement "
                    f"({IS_CORRECTION_OF[r['turn_key']]}). It is authoritative.")
        blocks.append(
            f"PASSAGE {i}\n"
            f"id: {r['id']}\n"
            f"title: {r['title'] or ''}\n"
            f"date: {r['date'] or ''}\n"
            f"speaker: {r['speaker'] or ''}\n"
            f"text:\n{r['cite_text']}"
            f"{note}"
        )
    return chosen, "\n\n".join(blocks)


def render_citation(row):
    """Human-readable citation. The speaker belongs HERE, never in the prose
    (owner decision)."""
    bits = []
    if row["title"]:
        bits.append(row["title"])
    bits.append(row["date"])
    if row["speaker"]:
        bits.append(f"({row['speaker']})")
    return " — ".join(bits)


SYSTEM = """You answer questions about Singapore's parliamentary record (Hansard).

You are given numbered passages. Answer ONLY from those passages.

PASSAGES look like this:

PASSAGE 1
id: oral-answer-2271#t1#c0
title: Managing National Food Stockpile
date: 2020-04-06
speaker: Mr Gan Kim Yong
text:
<what was actually said>

Rules you must follow exactly:
1. Every part of your answer must be a VERBATIM QUOTE copied character-for-character from
   the "text" section of a passage. Do not paraphrase, do not shorten with "...", and do not
   combine fragments from different passages.
2. Every quote must carry the "id" of the passage it came from, e.g. "oral-answer-2271#t1#c0".
3. The "title", "date" and "speaker" lines are labels, not speech. You never need to copy
   them -- the speaker is reported from the passage automatically with its citation. Do not
   quote them as if they had been said.

   ATTRIBUTION QUESTIONS: if the question asks WHO said, announced or spoke about something,
   cite the passage that contains that statement. Each passage's "speaker" line is the answer
   to a "who" question -- the system reports it automatically with the citation. Do NOT refuse
   a "who" question on the grounds that a name cannot be quoted, and do NOT cite a passage
   whose speaker has nothing to do with the statement asked about.
4. Do NOT write prose of your own. No introduction, no commentary, no explanation.
5. A passage that merely MENTIONS the subject of the question is NOT an answer. If the
   passages do not state the fact asked for, you must refuse.
6. If the passages do not contain the answer, set "refused" to true. Do NOT guess, and do
   not supply a plausible number or name that is not in the passages.
7. When refusing, list the related topics you were given. Each entry MUST copy the exact
   "title" and "id" of one of the passages above -- the title only, NOT the date or the
   speaker. Never invent a topic, and do not omit one that is related. If nothing is
   genuinely related, leave the list empty.

Reply with ONLY a JSON object, no other text:
{"refused": false,
 "quotes": [{"quote": "<verbatim text from passage>", "chunk_id": "<passage id>"}]}
or
{"refused": true,
 "reason": "<one sentence, plain words, no figures of your own>",
 "related": [{"title": "<exact title from above>", "chunk_id": "<passage id>"}]}
"""


def ask_model(question, context, model=ANSWERER, timeout=300, seed=1234):
    """One call. Returns the parsed JSON dict, or None if unparseable.

    An empty/unparseable reply is NOT a verdict -- measured earlier in this project, 14 of
    16 'unparsed' results produced a verdict when simply asked again. Callers retry.

    REPRODUCIBILITY. Three consecutive runs of this stage over the same 16 questions scored
    14, 14 and 16, with three questions flipping between runs. A system that disagrees with
    itself cannot be scored by a single run, and the cause is that `temperature: 0` is a
    no-op on a server-side cloud model: Ollama's `format: "json"` constrains the SYNTAX of
    the reply, not the sampling, so the same prompt can yield a different quote or a
    different refusal decision each time. A `seed` is therefore passed explicitly, which
    makes a given run reproducible and makes a RETRY a different draw rather than a coin
    toss on the same one. The measured spread is reported alongside every score rather than
    presented as a single figure.
    """
    user = f"Question: {question}\n\nPassages:\n{context}"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "seed": seed},
    }).encode()
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    txt = (out.get("message") or {}).get("content") or ""
    txt = txt.strip()
    if not txt:
        return None
    # the model may wrap the object in a fence or add a sentence before it
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def answer(question, db, k=10, tries=3, ranker="vector", verbose=False):
    """Full stage: retrieve -> rank -> build context -> ask -> attach citations."""
    _load_supersession(db)
    out = {"question": question, "refused": None, "quotes": [], "related": [],
           "attempts": 0}

    # k=10**7 asks for the whole candidate pool, because the vector ranker orders it and then
    # keeps k. rank=False is therefore required, not an optimisation: the BM25 build exists only
    # to produce an ordering this call discards, and it costs 13s over 91,263 turns -- it was
    # being paid on EVERY question, and on an unconstrained question (no year, no subject term)
    # that is the entire pool. Measured: a refusal went 22.0s -> 6.7s, an answer 2.5s -> 2.3s.
    # Equivalence of the refusal decisions at this k is asserted by test_retrieve_rank_equiv.py.
    raw, info = SR.retrieve(question, k=10 ** 7, db=db, with_norm=False, rank=(ranker != "vector"))
    out["retrieval"] = {"constraints": info.get("constraints"),
                        "refused": bool(info.get("refused")),
                        "candidate_turns": info.get("candidate_turns"),
                        "reason": info.get("reason")}
    if info.get("refused"):
        # retrieval's own structural refusal (empty conjunction) already settles it
        out["refused"] = True
        out["source"] = "retrieval"
        return out
    if not raw:
        out["refused"] = True
        out["source"] = "no_candidates"
        return out

    if ranker == "vector":
        keys = [d["key"] for d in raw]
        by_key = {d["key"]: d for d in raw}
        vr = get_vr(db)
        order = [t for t, _ in VR.VectorRanker.rank(vr, question, keys)]
        got = [by_key[t] for t in order[:k]]
    else:
        got = raw[:k]

    chunks, context = build_context(db, got)
    out["n_chunks"] = len(chunks)
    out["context_tokens"] = sum(c["tokens"] for c in chunks)
    # kept so the gate can recognise a quote that is really a citation label
    out["_chunks"] = [{"id": c["id"]} for c in chunks]
    # the passage TEXT the model was shown, so the refusal-number check can tell a
    # descriptive reference from an invented figure
    out["_context_rows"] = [{"cite_text": c["cite_text"]} for c in chunks]
    parsed = None
    for attempt in range(tries):
        out["attempts"] += 1
        try:
            parsed = ask_model(question, context)
        except Exception as e:
            out["error"] = str(e)[:200]
            parsed = None
        if parsed is not None:
            break
    if parsed is None:
        out["refused"] = None
        out["source"] = "unparsed"
        return out

    out["source"] = "model"
    if parsed.get("refused"):
        out["refused"] = True
        out["reason"] = parsed.get("reason") or ""
        # Resolve each suggestion through the PASSAGE it cites, never through an id the
        # model typed. Measured: asked for a report_id, the model supplied a chunk id
        # ("budget-2563#t2#c2"), which no report lookup can resolve. The report id is
        # derivable from the chunk, so it is derived here instead of requested.
        by_id = {c["id"]: c for c in chunks}
        seen_reports = set()
        for sug in (parsed.get("related") or []):
            cid = sug.get("chunk_id")
            c = by_id.get(cid)
            rid = c["report_id"] if c else None
            if not rid or rid in seen_reports:
                continue
            seen_reports.add(rid)
            row = db.execute(
                "SELECT report_id, title, date FROM report WHERE report_id=?",
                (rid,)).fetchone()
            out["related"].append({
                "title": str(sug.get("title") or "").strip(),
                "chunk_id": cid,
                "report_id": rid,
                "date": row["date"] if row else None,
                "real_title": row["title"] if row else None,
                "grounded": bool(c) and bool(row)
                            and (row["title"] or "").strip() == str(sug.get("title") or "").strip(),
                "offered": bool(c),
            })
        # owner decision: chronological order, and all of them rather than a capped list
        out["related"].sort(key=lambda s: (s.get("date") or "", s.get("report_id") or ""))
        return out

    out["refused"] = False
    by_id = {c["id"]: c for c in chunks}
    cited = []
    for q in (parsed.get("quotes") or []):
        cid = q.get("chunk_id")
        c = by_id.get(cid)
        if c is not None and c["id"] not in {x["id"] for x in cited}:
            cited.append(c)
        out["quotes"].append({
            "quote": q.get("quote"),
            "chunk_id": cid,
            "cited_ok": bool(c),
            "citation": render_citation(c) if c else None,
            "speaker": c["speaker"] if c else None,
        })
    # the corpus rows the answer actually rests on, for attribution scoring
    out["_cited"] = [{"id": c["id"], "speaker": c["speaker"], "report_id": c["report_id"]}
                     for c in cited]
    return out


# ----------------------------------------------------------------------------- gates

# A trailing sentence period must not become part of the number: measured, the pattern
# captured "2013." from "...between 2011 and 2013.", which then failed the question-echo
# exemption because the question contains "2013" (no period). The bug was in the match, not
# in the exemption.
NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?:\s*(?:billion|million|thousand))?", re.I)


def check_citations(res, db):
    """The mechanical gate. No model involved. Returns list of problems.

    A citation LABEL is not speech. When the model returns a quote that exactly equals a
    field label (a report title, a date, or a speaker) it has quoted the citation line
    rather than the passage, and that is recorded as a label error rather than a fabricated
    quote -- the words are real, but nothing was said. Measured: two class-B questions
    returned report titles as quotes, and calling that "not verbatim" hid what happened.
    """
    problems = []
    if res.get("refused"):
        # gate 4: every suggestion must resolve in the corpus, and must have been offered
        # by retrieval -- a model-writable topic is a fabrication surface
        for i, s in enumerate(res.get("related") or []):
            if not s.get("grounded"):
                problems.append(
                    f"related[{i}] NOT GROUNDED: title={s.get('title')!r} "
                    f"report_id={s.get('report_id')!r} (corpus title: "
                    f"{s.get('real_title')!r})")
        return problems

    if not res.get("quotes"):
        problems.append("answered but quoted nothing")
        return problems

    chunks = {c["id"]: c for c in (res.get("_chunks") or [])}
    for i, q in enumerate(res["quotes"]):
        cid, quote = q.get("chunk_id"), q.get("quote")
        if not cid:
            problems.append(f"quote[{i}] has no chunk_id")
            continue
        row = db.execute("SELECT cite_text, title, speaker, date FROM chunk WHERE id=?",
                         (cid,)).fetchone()
        if row is None:
            problems.append(f"quote[{i}] cites a chunk that does not exist: {cid!r}")
            continue
        if not quote or not quote.strip():
            problems.append(f"quote[{i}] is empty")
            continue
        nq = B_norm(quote)
        labels = {B_norm(row["title"]), B_norm(row["date"])}
        labels.discard("")
        speaker = B_norm(row["speaker"] or "")
        if nq in labels:
            problems.append(f"quote[{i}] is a citation LABEL, not speech: {quote[:70]!r}")
            continue
        # A quote of the SPEAKER line is legitimate: attribution is part of the record, and
        # a "who said it" question must be answerable. The name is checked against the
        # stored speaker rather than rejected, so a name the model invented still fails --
        # the original code refused the whole category, which blocked valid questions.
        if speaker and nq in {speaker, speaker + "."}:
            continue
        # 1. verbatim: the quote must appear EXACTLY (whitespace-normalised only) in the
        #    cited chunk. Normalising whitespace is safe because the comparison is against
        #    the canonical stored text, not a re-derived string.
        if nq not in B_norm(row["cite_text"]):
            problems.append(f"quote[{i}] NOT VERBATIM in {cid}: {quote[:70]!r}")

    return problems


def check_numbers_in_all_text(res, db):
    """Catch a number the model asserts in its OWN words that the record does not support.

    Grounding rule: a number in a refusal reason is acceptable when it can be traced to the
    QUESTION or to any PASSAGE OFFERED. A refusal is explanatory prose about the passages the
    model was given ("the passages discuss GST increases in 2023 and 2024"), so its numbers
    should be traceable to those passages. A number traceable to neither is invented.

    Two earlier versions of this check were wrong in the same direction -- firing on correct
    output:

      v1 flagged every digit: "2027", "2020" -> failed refusals that echoed the question.
      v2 exempted digits present in the question, but still failed "the passages discuss GST
         increases in 2023 and 2024", because those years are NOT in the question. Measured
         across three seeded runs: E3 refused correctly every time and was scored FAIL twice,
         which made E3 look UNSTABLE when the model was in fact consistent.

    Grounding against the offered passages is the test that matches the claim being made.
    """
    problems = []
    if not res.get("refused"):
        return problems                     # quotes are checked exhaustively elsewhere
    reason = B_norm(res.get("reason") or "")
    qn = B_norm(res.get("question") or "")
    # everything the model was shown, so a descriptive reference resolves
    ctx = B_norm(" ".join(str(c.get("cite_text") or "")
                          for c in (res.get("_context_rows") or [])))
    for n in set(NUM_RE.findall(reason)):
        b = B_norm(n)
        if b in qn:
            continue                        # echoed from the question
        if ctx and b in ctx:
            continue                        # describing a passage it was given
        problems.append(f"refusal states a number that is in neither the question nor any "
                        f"passage offered: {n!r}")
    return problems


# ------------------------------------------------------------------------------ main

def get_vr(db):
    """Load the vector ranker once per process; ~315 MB of float32 norms."""
    global _VR
    if _VR is None:
        _VR = VR.VectorRanker()
    return _VR


_VR = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True)
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("--ranker", choices=["vector", "none"], default="vector")
    ap.add_argument("--json", help="write the raw result to this path")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    if args.ranker == "vector":
        get_vr(db)

    res = answer(args.q, db, k=args.k, ranker=args.ranker)
    print(f"question   : {args.q}")
    print(f"retrieval  : {res['retrieval']['constraints']}")
    if res["retrieval"].get("refused"):
        print(f"             structural refusal: {res['retrieval']['reason']}")
    print(f"context    : {res.get('n_chunks')} chunks, "
          f"{res.get('context_tokens', 0):,} tokens")
    print(f"attempts   : {res['attempts']}")
    print()

    if res.get("refused") is None:
        print(f"NO VERDICT ({res.get('source')}) -- an unparsed reply is neither pass nor fail")
    elif res["refused"]:
        print(f"REFUSED ({res.get('source')}): {res.get('reason')}")
        if res.get("related"):
            print("  Related topics (do NOT answer the question):")
            for s in res["related"]:
                flag = "ok" if s["grounded"] else "NOT GROUNDED"
                print(f"    - {s['title']!r} [{s['report_id']}] {s['date']}  {flag}")
        else:
            print("  (no related topics offered)")
    else:
        print("ANSWER (verbatim quotes):")
        for q in res["quotes"]:
            print(f"  {q['quote']!r}")
            print(f"      [{q['chunk_id']}] {q['citation']}")

    probs = check_citations(res, db) + check_numbers_in_all_text(res, db)
    print()
    print(f"citation gate: {'PASS' if not probs else 'FAIL'}")
    for p in probs:
        print(f"  - {p}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(res, fh, indent=1)
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
