"""Phase 2c: rank the SQL candidate pool by embedding similarity.

Role: STRICTLY a ranker. SQL has already chosen the candidates; this reorders them and
can never re-introduce a row the filter excluded. Measured basis for adding it -- over the
12 answerable eval questions the gold was IN the SQL pool for 11 but at rank 1 for only 3,
so the bottleneck is ordering, not filtering. A ranker is the thing that fixes ordering.

Two reasons a ranker beats BM25 here, both measured on this corpus:

  1. Lexical near-neighbours. The "Jobs Support Scheme" pool holds 261 chunks that all
     share the scheme's name, so BM25 cannot separate the passage that ANSWERS the
     question from the 260 that merely mention the subject. F1's gold sat at rank 7 for
     exactly this reason.

  2. Pool-size dependence. BM25's IDF makes scores incomparable between a 63-row pool
     (2.5-7) and an unfiltered one (34-42), which is what killed the score-threshold
     approach to refusal. Cosine similarity has no such behaviour, so a similarity
     threshold is at least meaningful -- though it is NOT used for refusal here.

WHY RRF FOR THE HYBRID
----------------------
Reciprocal rank fusion combines rankings by POSITION, not by score:
    fused(turn) = sum over rankers of 1 / (K + rank)
That is precisely what this corpus needs: BM25 scores are not comparable across pools,
and cosine similarity is not on the same scale as BM25 at all. Fusing ranks avoids
inventing a normalisation, which would have to be tuned and would then be a hidden
parameter. K=60 is the conventional damping constant.

Deduplication note: this corpus contains 1,286 duplicate turns ("Ms Sun Xueling." appears
at two turns of one report), so identical vectors tie exactly. Ties are broken by turn key
so the ordering is deterministic and a run is reproducible.
"""
import json
import os
import sys
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PIPELINE = os.path.abspath(os.path.join(HERE, "..", "pipeline"))
NPY = os.path.join(PIPELINE, "vectors", "vectors.f16.npy")
IDS = os.path.join(PIPELINE, "vectors", "ids.jsonl")
EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"
RRF_K = 60


def embed_one(text, model=EMBED_MODEL):
    body = json.dumps({"model": model, "input": [text]}).encode()
    req = urllib.request.Request(EMBED_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return np.asarray(json.loads(r.read())["embeddings"][0], dtype=np.float32)


class VectorRanker:
    """Cosine ranker over the precomputed chunk vectors.

    Turns are scored by the MAX cosine over their own chunks, not the mean: a turn is
    long and may cover several subjects, so an answer sitting in one paragraph must not be
    diluted by the surrounding ones. Max also matches the retrieval unit's intent -- one
    relevant paragraph is enough to make the turn worth returning.
    """

    def __init__(self, npy=NPY, ids=IDS):
        self.vecs = np.load(npy, mmap_mode="r")
        self.key_of = {}          # row -> chunk id
        self.rows_by_turn = {}    # turn_key -> [rows]
        with open(ids) as fh:
            for i, line in enumerate(fh):
                d = json.loads(line)
                self.key_of[i] = d["id"]
                self.rows_by_turn.setdefault(d["turn_key"], []).append(i)
        self._norm = None

    def _norms(self):
        """Row-normalise once, lazily. float32 in RAM: ~315 MB for the full corpus."""
        if self._norm is None:
            v = np.asarray(self.vecs, dtype=np.float32)
            self._norm = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12)
        return self._norm

    def score_turns(self, qvec, turn_keys):
        """Max cosine per turn. Returns {turn_key: score} for the turns that have rows."""
        v = self._norms()
        q = qvec / (np.linalg.norm(qvec) + 1e-12)
        out = {}
        for t in turn_keys:
            rows = self.rows_by_turn.get(t)
            if not rows:
                continue
            out[t] = float(np.max(v[rows] @ q))
        return out

    def rank(self, question, turn_keys):
        """Order turn_keys by cosine. Deterministic tie-break by key."""
        if not turn_keys:
            return []
        scores = self.score_turns(embed_one(question), turn_keys)
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def rrf(*rankings, k=RRF_K):
    """Reciprocal rank fusion of several ordered key lists -> one ordered list."""
    acc = {}
    for r in rankings:
        for rank, key in enumerate(r, 1):
            acc[key] = acc.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))
