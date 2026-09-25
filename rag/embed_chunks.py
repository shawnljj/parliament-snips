"""Phase 2: embed every chunk, with a round-trip gate.

Vectorises `embed_text` (speaker label + verbatim text) for all chunks, storing vectors
next to their chunk ids so a hit can always be resolved back to its text and citation.

Why `embed_text` and not `cite_text`: measured earlier, Class B "who said it" failed 0/3
because the speaker lives in metadata rather than in the text. The label is prefixed so
the vector carries attribution; `cite_text` stays the untouched verbatim form that the
citation gate checks against.

RESUMABLE BY DESIGN
-------------------
~102k chunks at the measured rate is under an hour, but a run that dies at 90% must not
restart from zero. Progress is committed to disk in batches (ids.jsonl is the row order
for the .npy), so an interrupted run resumes from its last flushed batch. Re-running is
safe: it appends only what is missing.

THE GATE (must pass as a command, per the phase contract)
---------------------------------------------------------
  1. vector count == chunk count exactly (no chunk silently skipped)
  2. every vector is 768-dimensional
  3. no vector is all-zero / non-finite (a failed call returning a zero vector would
     otherwise pass a count check while being unusable)
  4. ROUND TRIP: re-embed a sample and confirm each sample's nearest neighbour is ITSELF.
     This is the check that catches a whole class of silent failures at once -- dimension
     mismatch, truncated writes, row misalignment between ids and vectors, and a vector
     store that is internally inconsistent. A count check catches none of them.

Measured limit this respects: nomic-embed-text silently truncates past 2,048 tokens, so
chunks are already sized to fit (max 1,408 tokens measured). This script asserts that
too, because a future change to chunking must not quietly reintroduce truncation.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PIPELINE = os.path.abspath(os.path.join(HERE, "..", "pipeline"))
CHUNKS = os.path.join(PIPELINE, "chunks.jsonl")
VECDIR = os.path.join(PIPELINE, "vectors")
IDS = os.path.join(VECDIR, "ids.jsonl")
NPY = os.path.join(VECDIR, "vectors.f16.npy")
DB = os.path.join(PIPELINE, "hansard.db")

from tokens import count_tokens, TOKEN_LIMIT

MODEL = "nomic-embed-text"
DIM = 768
BATCH = 64
URL = "http://localhost:11434/api/embed"


def embed_batch(texts, tries=4):
    """Embed a batch, retrying with backoff. Returns list of vectors or None."""
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                URL, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                out = json.loads(r.read())
            embs = out.get("embeddings")
            if embs and len(embs) == len(texts):
                return embs
            last = f"got {len(embs) if embs else 0} vectors for {len(texts)} inputs"
        except Exception as e:
            last = str(e)[:120]
            time.sleep(2 ** attempt)
    print(f"    batch FAILED after {tries} tries: {last}")
    return None


def load_chunks():
    with open(CHUNKS) as fh:
        return [json.loads(line) for line in fh]


def done_count():
    if not os.path.exists(IDS):
        return 0
    with open(IDS) as fh:
        return sum(1 for _ in fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="embed only the first N")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--sample", type=int, default=200,
                    help="round-trip sample size")
    args = ap.parse_args()

    os.makedirs(VECDIR, exist_ok=True)
    chunks = load_chunks()
    total = len(chunks) if args.limit is None else min(args.limit, len(chunks))
    print(f"chunks: {len(chunks):,}   target: {total:,}   batch: {BATCH}")
    print(f"model : {MODEL}  dim {DIM}")
    print()

    # ---- gate part 3: assert every chunk fits the model's window -------------
    over = [c["id"] for c in chunks[:total]
            if count_tokens(c["embed_text"]) > TOKEN_LIMIT]
    if over:
        print(f"FAIL -- {len(over)} chunk(s) exceed the {TOKEN_LIMIT}-token window and "
              f"would be SILENTLY TRUNCATED: {over[:5]}")
        return 1
    print(f"pre-check: all {total:,} chunks are inside the {TOKEN_LIMIT}-token window")

    if not args.verify_only:
        start = done_count()
        if start > total:
            print(f"ids.jsonl has {start:,} rows > target {total:,}; refusing to mix runs")
            return 1
        if start:
            print(f"resuming from chunk {start:,}")
        # preallocate the vector file (float16 keeps it ~157 MB instead of ~315 MB)
        if not os.path.exists(NPY):
            np.lib.format.open_memmap(NPY, mode="w+", dtype=np.float16,
                                      shape=(total, DIM))
        vecs = np.lib.format.open_memmap(NPY, mode="r+")
        if vecs.shape[0] < total:
            print(f"vector file has {vecs.shape[0]:,} rows but {total:,} are needed; "
                  f"delete {NPY} and rerun")
            return 1

        t0 = time.time()
        n_done = start
        with open(IDS, "a") as fids:
            while n_done < total:
                batch = chunks[n_done:n_done + BATCH]
                texts = [c["embed_text"] for c in batch]
                embs = embed_batch(texts)
                if embs is None:
                    print(f"stopping at {n_done:,} (batch failed); rerun to resume")
                    break
                arr = np.asarray(embs, dtype=np.float16)
                if arr.shape != (len(batch), DIM):
                    print(f"FAIL -- batch at {n_done:,} returned shape {arr.shape}, "
                          f"expected ({len(batch)}, {DIM})")
                    return 1
                vecs[n_done:n_done + len(batch)] = arr
                # flush vectors THEN ids: if a crash lands between the two, the id line is
                # missing and the chunk is re-embedded, which is safe. The reverse order
                # would mark a row done with an unwritten (zero) vector.
                vecs.flush()
                for c in batch:
                    fids.write(json.dumps({"id": c["id"], "turn_key": c["key"]}) + "\n")
                fids.flush()
                n_done += len(batch)
                el = time.time() - t0
                if n_done % (BATCH * 20) == 0 or n_done >= total:
                    rate = (n_done - start) / el if el else 0
                    eta = (total - n_done) / rate if rate else 0
                    print(f"  {n_done:,}/{total:,}  {el/60:.1f} min  "
                          f"{rate:.1f} chunks/s  eta {eta/60:.1f} min", flush=True)

        print()
        print(f"embedded {n_done:,} of {total:,} in {(time.time()-t0)/60:.1f} min")

    # ---- GATE ---------------------------------------------------------------
    print()
    print("=== gate ===")
    n_ids = done_count()
    print(f"1. count      : {n_ids:,} ids written vs {total:,} chunks "
          f"{'OK' if n_ids == total else 'MISMATCH'}")
    ids = [json.loads(l)["id"] for l in open(IDS)]
    chunk_ids = [c["id"] for c in chunks[:total]]
    same_order = ids == chunk_ids
    print(f"   row order  : ids match chunk order exactly: {same_order}")
    vecs = np.lib.format.open_memmap(NPY, mode="r")
    print(f"2. shape      : {vecs.shape}  "
          f"{'OK' if vecs.shape == (total, DIM) else 'MISMATCH'}")

    sub = np.asarray(vecs[:n_ids], dtype=np.float32)
    finite = np.isfinite(sub).all()
    zero_rows = int((np.abs(sub).sum(axis=1) == 0).sum())
    print(f"3. finiteness : all finite: {finite}   all-zero rows: {zero_rows}")

    # 4. ROUND TRIP -- the check that catches misalignment and truncation together.
    #
    #    The question is NOT "does each sample find itself as nearest neighbour": this
    #    corpus is FULL of duplicate text (measured, only 505 distinct embed_texts across
    #    512 chunks -- procedural turns like "Mr Louis Ng." repeat verbatim), so identical
    #    vectors tie at similarity 1.0 and the winner is arbitrary. An earlier version of
    #    this gate failed on that and reported a defect that did not exist.
    #
    #    The correct, duplicate-proof test is the SELF-SIMILARITY: the stored vector for a
    #    chunk must be nearly identical to a fresh embedding of that same chunk. Row
    #    misalignment, a truncated write, or a wrong-dimension vector all push this well
    #    below 1; a genuine duplicate leaves it at exactly 1.
    rng = np.random.default_rng(7)
    idxs = rng.choice(n_ids, size=min(args.sample, n_ids), replace=False)
    self_sims = []
    t0 = time.time()
    for j in range(0, len(idxs), BATCH):
        sl = idxs[j:j + BATCH]
        embs = embed_batch([chunks[i]["embed_text"] for i in sl])
        if embs is None:
            continue
        q = np.asarray(embs, dtype=np.float32)
        qn = q / (np.linalg.norm(q, axis=1, keepdims=True) + 1e-12)
        stored = sub[sl]
        sn = stored / (np.linalg.norm(stored, axis=1, keepdims=True) + 1e-12)
        for k in range(len(sl)):
            self_sims.append(float(qn[k] @ sn[k]))
    worst = min(self_sims) if self_sims else 0.0
    mean = sum(self_sims) / len(self_sims) if self_sims else 0.0
    n_perfect = sum(1 for s in self_sims if s > 0.999)
    print(f"4. round trip : {len(self_sims)} samples re-embedded  [{(time.time()-t0):.0f}s]")
    print(f"                self-similarity  mean {mean:.6f}  min {worst:.6f}")
    print(f"                > 0.999: {n_perfect}/{len(self_sims)}")
    print(f"                (a misaligned or truncated store would sit far below 1; "
          f"float16 storage costs ~1e-4)")

    good = (n_ids == total and same_order and vecs.shape == (total, DIM)
            and finite and zero_rows == 0 and worst > 0.995)
    print()
    print(f"storage: {os.path.getsize(NPY)/1e6:.0f} MB")
    print("PASS" if good else "FAIL -- see the failing part above")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
