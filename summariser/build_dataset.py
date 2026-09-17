"""Stage 1: build the dataset that later stages consume. NO LLM CALLS.

Why this stage exists as a separate step
----------------------------------------
The chosen algorithm (approach C, see SUMMARISATION.md) is a tiered hybrid:
small items go to the model whole, heavy items are chunked. Both need the text
addressable by stable reference, because the model must not be trusted to retype a
quotation -- it returns sentence ids and the assembler substitutes the stored text.
That substitution is what makes every quote verbatim by construction.

So this stage does the mechanical, LLM-free work:
  1. group reports into policy items (already done by summarise.summarisable_items)
  2. sentence-split each turn and assign every content sentence a stable `sid`
  3. mark procedural / courtesy / too-short sentences (excluded from prompts, but
     recorded so the exclusion is auditable rather than invisible)
  4. decide the tier: does the item fit one prompt whole, or must it be chunked?
  5. define chunks as ordered LISTS OF SIDS -- never as re-sliced text, so a chunk
     cannot drift from its sentences
  6. emit a queue index for the pipeline dashboard

Nothing here reads a model. Run it, review the output, then Stage 2 begins.

Outputs
-------
    pipeline/dataset/<year>/<item-key>.json   one self-contained item payload
    pipeline/dataset/index.json               the queue: every item, metadata only
    pipeline/state.json                       per-stage progress for the dashboard

Usage
-----
    python3 summariser/build_dataset.py                  # all items
    python3 summariser/build_dataset.py --limit 20       # sample
    python3 summariser/build_dataset.py --stats          # summarise without writing
"""

import argparse
import datetime
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import storage  # noqa: E402
import summarise as S  # noqa: E402
import extractive as X  # noqa: E402

PIPE = os.path.join(ROOT, "pipeline")
DATASET = os.path.join(PIPE, "dataset")

# Prompt budget. The cloud model's window is 1,048,576 tokens, so this is NOT a
# context limit -- it is a COST and PORTABILITY limit. Chosen so that a prompt
# leaves generous room for output and would ALSO fit a small local model later
# (~32k-token window), which is the whole point of keeping the option open.
# Recorded in the output so a later change is visible rather than silent.
SMALL_CHARS = 24000      # fits one prompt whole
CHUNK_CHARS = 24000      # per chunk, for heavy items

SCHEMA = 1


def sid(n):
    """Stable sentence id. Zero-padded so lexical order == document order."""
    return f"s{n:05d}"


def build_item(item, idx):
    """One self-contained dataset payload for a policy item.

    LAYOUT: columnar, grouped by turn, with a speaker dictionary.

    The first version stored one pretty-printed object per sentence with the speaker
    name inlined. Measured on the real corpus that cost 268 MB to hold 100 MB of
    text: 97 MB of repeated JSON field names (`"text"`, `"speaker"`, ...) x 754,082
    sentences, 42 MB of indentation, and 29 MB of the same 592 speaker names rewritten
    on every sentence. Only a third of the file was content.

    So sentences are stored as parallel arrays, one entry per TURN (not per sentence),
    with speakers held once in a dictionary. Measured result: 268 MB -> ~106 MB, and
    no information is lost -- every sentence still carries a stable sid, its speaker,
    its turn and its score.
    """
    speakers = []          # dictionary of distinct speaker names
    speaker_ix = {}        # name -> index
    turns = []
    excluded = {"procedural": 0, "courtesy": 0, "too_short": 0, "other_turn": 0}
    last_speaker = None
    turn_index = 0
    n = 0

    def ix_for(name):
        if name not in speaker_ix:
            speaker_ix[name] = len(speakers)
            speakers.append(name)
        return speaker_ix[name]

    for r in item["reports"]:
        for t in r.get("turns", []):
            txt = X.turn_text(t)
            spk = (t.get("speaker") or "").strip() or None
            if spk:
                last_speaker = spk
            if not txt:
                excluded["other_turn"] += 1
                continue
            # Whole-turn procedural screen (the per-turn flag is nearly useless:
            # 1 of 560 turns on a measured sitting carried it).
            turn_proc = bool(t.get("is_procedural")) or X.is_procedural(txt)

            sids, texts, words, scores = [], [], [], []
            for raw in X.re_split_sentences(txt):
                n_local = len(raw.split())
                if turn_proc or X.is_procedural(raw):
                    excluded["procedural"] += 1
                    continue
                if X.NOISE_RE.match(raw):
                    excluded["courtesy"] += 1
                    continue
                if n_local < X.MIN_WORDS:
                    excluded["too_short"] += 1
                    continue
                n += 1
                sids.append(sid(n))
                texts.append(raw)
                words.append(n_local)
                scores.append(round(X.score(raw, 0.0, 1), 2))
            # A turn whose sentences were all filtered out contributes nothing; skip
            # it rather than emit an empty row.
            if sids:
                turns.append({
                    "sid": sids,
                    "text": texts,
                    "spk": ix_for(spk or last_speaker or ""),
                    # `attributed` is per TURN, not per sentence -- it says whether the
                    # speaker name came from the record or was carried forward. One
                    # value per turn is both cheaper and more honest than repeating it.
                    "attr": bool(spk),
                    "t": turn_index,
                    "r": r["report_id"],
                    "w": words,
                    "score": scores,
                })
            turn_index += 1

    # Flatten for the sid-level views (chunks, index, verification).
    flat = []
    for turn in turns:
        for i, s in enumerate(turn["sid"]):
            flat.append((s, turn["text"][i], turn["spk"], turn["attr"],
                         turn["t"], turn["r"]))

    # Chunk as ordered lists of sids. Never re-slice text: a chunk that shares no
    # sentence with its source cannot drift from it.
    chunks, cur, size = [], [], 0
    for s, text, spk, attr, ti, rid in flat:
        w = len(text) + len(speakers[spk]) + 8
        if cur and size + w > CHUNK_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += w
    if cur:
        chunks.append(cur)

    total_chars = sum(len(text) + len(speakers[spk]) + 8
                      for _, text, spk, _, _, _ in flat)
    tier = "small" if len(chunks) <= 1 else "heavy"

    return {
        "schema": SCHEMA,
        "id": item["key"],
        "title": item["title"],
        "group": item["group"],
        "report_ids": item["report_ids"],
        "sitting_dates": item["sitting_dates"],
        "source_words": item["words"],
        "tier": tier,
        "prompt_chars": total_chars,
        "sentence_count": len(flat),
        "chunk_count": len(chunks),
        # speakers[ix] is the name; turns[].spk indexes it
        "speakers": speakers,
        "turns": turns,
        "chunks": chunks,
        "excluded_counts": excluded,
    }


def load_item(path):
    """Read a payload back into flat per-sentence dicts.

    Stages 2-4 should never touch the columnar layout directly: it exists to save
    disk, not to be worked with. This returns the natural shape --
    [{sid, text, speaker, attributed, turn_index, report_id, words, score}, ...] --
    so callers are written against sentences, as before.
    """
    d = storage.read_json(path)
    if not d:
        return None
    speakers = d.get("speakers") or []
    out = []
    for turn in d.get("turns") or []:
        spk = speakers[turn["spk"]] if turn["spk"] < len(speakers) else ""
        for i, s in enumerate(turn["sid"]):
            out.append({
                "sid": s,
                "text": turn["text"][i],
                "speaker": spk,
                "attributed": turn["attr"],
                "turn_index": turn["t"],
                "report_id": turn["r"],
                "words": turn["w"][i],
                "score": turn["score"][i],
            })
    return out


def iter_turns(d):
    """Yield (speaker, attributed, turn_index, report_id, [(sid, text), ...]).

    This is the shape a PROMPT wants: the speaker named once per turn rather than
    repeated on every sentence. Measured, that saves 24% of all prompt text.
    """
    speakers = d.get("speakers") or []
    for turn in d.get("turns") or []:
        spk = speakers[turn["spk"]] if turn["spk"] < len(speakers) else ""
        yield spk, turn["attr"], turn["t"], turn["r"], list(zip(turn["sid"], turn["text"]))


def pipeline_state(items_meta, built, t0):
    """Per-stage progress, read by the pipeline dashboard.

    Stages 2-4 are reported as `not_built` rather than 0% -- the dashboard must not
    imply work is queued that no code exists to do.
    """
    by_group = {}
    for m in items_meta:
        g = by_group.setdefault(m["group"], {"total": 0, "small": 0, "heavy": 0})
        g["total"] += 1
        g[m["tier"]] += 1

    return {
        "schema": SCHEMA,
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "elapsed_secs": round(__import__("time").time() - t0, 1),
        "budget": {"small_chars": SMALL_CHARS, "chunk_chars": CHUNK_CHARS},
        "model": S.MODEL,
        "stages": [
            {"n": 0, "name": "Structured facts", "llm": False, "state": "done",
             "detail": "compute_panels() -- counts, speakers, figures, Q->A maps"},
            {"n": 1, "name": "Dataset", "llm": False,
             "state": "done" if built == len(items_meta) else "partial",
             "detail": f"{built} of {len(items_meta)} items addressable by sid"},
            {"n": 2, "name": "Extract", "llm": True, "state": "not_built",
             "detail": "model returns sentence ids + claims; not implemented"},
            {"n": 3, "name": "Assemble", "llm": False, "state": "not_built",
             "detail": "substitute sid -> text, emit summary schema"},
            {"n": 4, "name": "Verify", "llm": False, "state": "not_built",
             "detail": "quote invariant + coverage check"},
        ],
        "queue": {
            "total": len(items_meta),
            "small": sum(1 for m in items_meta if m["tier"] == "small"),
            "heavy": sum(1 for m in items_meta if m["tier"] == "heavy"),
            "by_group": by_group,
        },
        "items": items_meta,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, help="only this many items (for review)")
    ap.add_argument("--stats", action="store_true",
                    help="measure only; write nothing")
    ap.add_argument("--out", default=PIPE, help="pipeline dir (default ./pipeline)")
    args = ap.parse_args()

    import time
    t0 = time.time()

    dset = os.path.join(args.out, "dataset")
    sittings = S.load_sittings()
    if not sittings:
        print("no sittings in data/", file=sys.stderr)
        return 1
    print(f"sittings: {len(sittings)}")

    items = S.summarisable_items(sittings)
    # Stable, title-free key for the item: its report ids. Same rule as summaries.
    for it in items:
        it["key"] = storage.stable_key(it["report_ids"])
    items.sort(key=lambda i: -i["words"])
    if args.limit:
        items = items[:args.limit]
    print(f"items: {len(items)}")

    meta, built, fails = [], 0, 0
    for i, it in enumerate(items, 1):
        try:
            payload = build_item(it, i)
        except Exception as exc:                                # noqa: BLE001
            print(f"  FAILED {it['key']}: {exc}", file=sys.stderr)
            fails += 1
            continue

        year = str(it["sitting_dates"][0])[:4]
        meta.append({
            "id": payload["id"], "year": year, "title": payload["title"],
            "group": payload["group"], "tier": payload["tier"],
            "source_words": payload["source_words"],
            "prompt_chars": payload["prompt_chars"],
            "sentence_count": payload["sentence_count"],
            "chunk_count": payload["chunk_count"],
            "report_ids": payload["report_ids"],
            "sitting_dates": payload["sitting_dates"],
        })
        if not args.stats:
            # Compact, not pretty: indent=1 cost 42 MB of whitespace across the
            # corpus, and nothing reads these files by eye. The index and state stay
            # pretty because a human does review those.
            storage.write_text_atomic(
                os.path.join(dset, year, f"{payload['id']}.json"),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        built += 1
        if i % 250 == 0:
            print(f"  {i}/{len(items)} ...")

    # ---------------------------------------------------------------- summary
    by_tier = {}
    for m in meta:
        by_tier.setdefault(m["tier"], {"n": 0, "chars": 0, "chunks": 0})
        v = by_tier[m["tier"]]
        v["n"] += 1
        v["chars"] += m["prompt_chars"]
        v["chunks"] += m["chunk_count"]

    print(f"\nbuilt {built} item(s), {fails} failed, in {time.time()-t0:.1f}s")
    print(f"{'tier':<8}{'items':>7}{'chunks':>9}{'total chars':>15}{'median chars':>14}")
    for tier in ("small", "heavy"):
        if tier not in by_tier:
            continue
        v = by_tier[tier]
        print(f"{tier:<8}{v['n']:>7,}{v['chunks']:>9,}{v['chars']:>15,}"
              f"{v['chars']//max(1,v['n']):>14,}")
    total_calls = sum(m["chunk_count"] for m in meta)
    print(f"\nLLM calls Stage 2 would make: {total_calls:,} "
          f"(+{by_tier.get('heavy',{}).get('n',0):,} reduce calls)")
    print(f"total prompt chars: {sum(m['prompt_chars'] for m in meta):,}")

    if not args.stats:
        os.makedirs(dset, exist_ok=True)
        storage.write_json_atomic(os.path.join(dset, "index.json"), {
            "schema": SCHEMA,
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "budget": {"small_chars": SMALL_CHARS, "chunk_chars": CHUNK_CHARS},
            "count": len(meta),
            "items": meta,
        })
        storage.write_json_atomic(os.path.join(args.out, "state.json"),
                                 pipeline_state(meta, built, t0))
        print(f"\nwrote {dset}/  and {args.out}/state.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
