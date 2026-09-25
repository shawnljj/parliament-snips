"""Phase 1 final: build the chunk plan with paragraph structure, verify, persist.

This is the artifact Phase 2 embeds, so it is written to disk and its properties are
asserted, not described.

Key measured facts this run must reproduce:
  - 1,036 reports had their paragraph structure re-fetched (266,570 paragraphs).
  - 123 of them do NOT align by position with the stored turn list, so the paragraph
    store is matched BY CONTENT. Indexing by position would have attached one turn's
    paragraphs to a different turn in ~1 report in 8.
  - Only turns EXCEEDING the embed budget are split, so most recovered paragraphs are
    not used -- a short turn is already a single complete unit. This run reports how
    many paragraphs are actually used.

Output: pipeline/chunks.jsonl, one chunk per line, streamable for the embed run.
"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chunk as C
from tokens import count_tokens, TOKEN_LIMIT

OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "pipeline", "chunks.jsonl"))
SUMMARY = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "pipeline", "chunk_summary.json"))


def load_para_store(reports):
    """Load the cached paragraph structure. No network: reads the cache files."""
    store = {}
    for rid in reports:
        got = C.fetch_paragraphs(rid)      # cache-hit => no network
        store[rid] = got
    return store


def main():
    t0 = time.time()
    turns = C.load_turns()
    over = [t for t in turns if not t["empty"]
            and count_tokens(t["clean"]) > TOKEN_LIMIT]
    reports = sorted({t["report_id"] for t in over})

    print(f"turns                : {len(turns):,}")
    print(f"over-budget turns    : {len(over):,}")
    print(f"reports with paras   : {len(reports):,}")

    store = load_para_store(reports)
    errs = [r for r, g in store.items() if g.get("error")]
    print(f"paragraph store      : {len(store):,} reports "
          f"({len(errs)} errors) [{time.time()-t0:.0f}s]")
    print()

    chunks, cov, ctext = C.plan_chunks(turns, para_store=store)

    print("=== coverage ===")
    for k, v in cov.items():
        print(f"  {k:>20}: {v}")
    print()

    # --- how many of the over-budget turns actually got paragraph structure? -------
    ps_turns = {c["key"] for c in chunks if c.get("para_sourced")}
    ps_chunks = [c for c in chunks if c.get("para_sourced")]
    over_keys = {t["key"] for t in over}
    got_paras = ps_turns & over_keys
    print("=== paragraph structure actually used ===")
    print(f"  over-budget turns            : {len(over_keys):,}")
    print(f"  turns USING paragraph splits : {len(got_paras):,} "
          f"({len(got_paras)/len(over_keys)*100:.1f}%)")
    print(f"  turns falling back to sentences: {len(over_keys)-len(got_paras):,}")
    print(f"  chunks from paragraph splits : {len(ps_chunks):,}")
    print()

    # --- gate --------------------------------------------------------------------
    problems = C.verify_chunks(chunks, turns, chunked_text=ctext)
    print("=== gate ===")
    if problems:
        print(f"  FAIL -- {len(problems)} problem(s):")
        for p in problems[:15]:
            print(f"    - {p[:170]}")
    else:
        print("  PASS -- every turn chunked, every piece verbatim, coverage complete,")
        print("          partials flagged, metadata present, embed_text canonical,")
        print("          token budget enforced.")
    print()

    # --- size profile ------------------------------------------------------------
    toks = [count_tokens(c["cite_text"]) for c in chunks]
    toks.sort()
    print("=== chunk size (tokens) ===")
    print(f"  chunks    : {len(chunks):,}")
    print(f"  median    : {toks[len(toks)//2]:,}")
    print(f"  p90       : {toks[int(len(toks)*.9)]:,}")
    print(f"  p99       : {toks[int(len(toks)*.99)]:,}")
    print(f"  max       : {max(toks):,}  (limit {TOKEN_LIMIT:,})")
    print(f"  over limit: {sum(1 for x in toks if x > TOKEN_LIMIT)}")
    print(f"  partial   : {sum(1 for c in chunks if c['is_partial']):,}")
    print()

    # --- persist -----------------------------------------------------------------
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        for c in chunks:
            fh.write(json.dumps(c) + "\n")
    size = os.path.getsize(OUT)
    print(f"written: {OUT}  ({size/1e6:.1f} MB)")

    # --- turns.jsonl: the `turn` table's source -----------------------------------
    #
    # Written from chunked_text -- the EXACT text that was chunked -- not from the
    # stored text. If the two ever differ (the paragraph path re-joins text and changes
    # its length), the turn row and its chunks would disagree, and a citation would
    # resolve against text the chunks were not built from.
    #
    # content_hash is the turn's identity: sha1(report_id || 0x1f || normalised text).
    # Identity must be CONTENT-derived, because two parses disagree about where turns
    # begin -- measured, 123 of 1,036 reports had a different turn count between the
    # store and a re-fetch. A positional key points at a DIFFERENT turn after a
    # re-parse; a content hash does not.
    TURNS = os.path.join(os.path.dirname(OUT), "turns.jsonl")
    seen_hash = {}
    n_turns = 0
    with open(TURNS, "w") as fh:
        for t in turns:
            if t["empty"]:
                continue
            if t["key"] not in ctext:
                continue          # turn produced no chunk (should not happen; gate asserts)
            txt = ctext[t["key"]]
            norm_txt = C.norm(C.CLEAN_FOR_MATCH(txt))
            h = hashlib.sha1(
                (t["report_id"] + "\x1f" + norm_txt).encode("utf-8")).hexdigest()[:16]
            # position disambiguates a genuine repeat: measured, 946 content hashes
            # collide over 91,263 turns, and every one checked was the SAME text said
            # twice ("Ms Sun Xueling." at turns 6 and 43 of one report), not a hash flaw.
            pos = seen_hash.get((t["report_id"], h), 0)
            seen_hash[(t["report_id"], h)] = pos + 1
            rec = {
                "key": t["key"], "report_id": t["report_id"], "turn": t["turn"],
                "content_hash": h, "repeat_index": pos,
                "text": txt, "raw": t["raw"],
                "date": t["date"], "year": t["year"], "group": t["group"],
                "title": t.get("title") or "", "report_type": t.get("report_type"),
                "parliament_no": t.get("parliament_no"),
                "volume_no": t.get("volume_no"), "sitting_no": t.get("sitting_no"),
                "speaker": t.get("speaker"), "time": t.get("time"),
                "is_procedural": t.get("is_procedural"), "lang": t.get("lang"),
                "words": t.get("words"),
            }
            fh.write(json.dumps(rec) + "\n")
            n_turns += 1
    print(f"written: {TURNS}  ({n_turns:,} turns, "
          f"{os.path.getsize(TURNS)/1e6:.1f} MB)")

    summ = dict(cov)
    summ.update({
        "over_budget_turns": len(over_keys),
        "turns_using_paragraphs": len(got_paras),
        "chunks_from_paragraphs": len(ps_chunks),
        "gate_problems": len(problems),
        "max_tokens": max(toks),
        "partial_chunks": sum(1 for c in chunks if c["is_partial"]),
        "elapsed_s": round(time.time() - t0, 1),
    })
    with open(SUMMARY, "w") as fh:
        json.dump(summ, fh, indent=1)
    print(f"written: {SUMMARY}")
    print()
    print(f"total elapsed: {time.time()-t0:.0f}s")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
