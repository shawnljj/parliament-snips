"""
BM25 over the Hansard corpus — pure standard library, no dependencies.

This module is deliberately two things at once:

  1. The honest v0 baseline. A keyword search a competent engineer would actually
     build. Scoring the eval against a deliberately broken search would "prove"
     the eval works while proving nothing, so this implements real BM25 (k1=1.2,
     b=0.75) with a real stopword list.

  2. The lexical half of the hybrid retriever in stage 5. The RAG pipeline needs
     exact-token matching for figures, names and abbreviations, and this is it.

Why BM25 and not embeddings alone: the corpus is full of figures ("$15.5 billion",
"26,000 flats") and abbreviations ("JSS", "BTO", "PWM", "ASSIST"). Embeddings blur
exact strings; BM25 preserves them. Neither is sufficient — see the trap in class F.

Usage:
    python3 rag/bm25.py --query "how many employers got Jobs Support Scheme payouts"
    python3 rag/bm25.py --query "JSS" --k 10
"""
import argparse
import glob
import html as htmlmod
import json
import math
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# A real stopword list. Without it, "the/what/how/and" dominate any score and the
# ranking becomes length-normalised stopword noise -- measured: the gold passage for
# "how many employers received JSS payouts?" ranked 371st of 40,161, and the top
# results were speeches on unrelated bills.
STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "but", "not", "you", "all", "any",
    "can", "had", "has", "have", "her", "his", "how", "its", "may", "our", "out",
    "she", "that", "them", "then", "there", "these", "they", "this", "those",
    "what", "when", "where", "which", "while", "who", "whom", "why", "will",
    "with", "would", "about", "been", "from", "into", "more", "most", "must",
    "such", "than", "too", "very", "also", "did", "does", "doing", "done",
    "each", "few", "other", "some", "only", "own", "same", "so", "being",
    "under", "over", "between", "during", "before", "after", "above", "below",
    "did", "should", "could", "ought", "under", "upon", "whether", "said",
    "made", "make", "many", "much", "mr", "mrs", "madam", "speaker", "sir",
    "member", "members", "parliament", "house", "government", "singapore",
    "question", "answer", "asked", "whether", "given", "give", "get", "got",
}

# abbreviations and figures must survive tokenisation intact
TOKEN_RE = re.compile(r"\$?\d[\d,.]*(?:\s*(?:billion|million|thousand))?|[a-z][a-z0-9]{1,}")


def norm(text):
    """Same normalisation the existing parsnips gates use."""
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
    return re.sub(r"\[[^\]]{0,200}\]", " ", text or "").strip()


def tokenise(text, drop_stop=True):
    """Tokenise, keeping abbreviations and figures whole.

    Uppercase abbreviations are expanded to a lowercased token so 'JSS' and 'jss'
    match: measured, Hansard writes it both ways.
    """
    toks = TOKEN_RE.findall(text.lower())
    if drop_stop:
        return [t for t in toks if t not in STOPWORDS and len(t) > 1]
    return toks


def load_corpus(years=None):
    """Every non-empty turn, as a flat record list."""
    turns = []
    for f in sorted(glob.glob(os.path.join(DATA, "*", "sitting_*.json"))):
        year = int(os.path.basename(os.path.dirname(f)))
        if years and year not in years:
            continue
        d = json.load(open(f))
        date = (d.get("coverage") or {}).get("date")
        for r in (d.get("reports") or []):
            rid = r.get("report_id")
            for i, t in enumerate(r.get("turns") or []):
                txt = (t.get("text") or "").strip()
                if not txt:
                    continue
                clean = strip_speaker_labels(txt)
                turns.append({
                    "key": f"{rid}#t{i}",
                    "report_id": rid,
                    "turn": i,
                    "year": year,
                    "date": date,
                    "group": r.get("group"),
                    "speaker": t.get("speaker"),
                    "text": txt,
                    "norm": norm(clean),
                    "tokens": tokenise(norm(clean)),
                })
    return turns


class BM25:
    """Okapi BM25. k1=1.2, b=0.75 -- the standard defaults."""

    def __init__(self, docs, k1=1.2, b=0.75):
        """docs: list of dicts with a 'tokens' list."""
        self.k1, self.b = k1, b
        self.docs = docs
        self.N = len(docs)
        self.tf = []
        self.len = []
        df = Counter()
        for d in docs:
            c = Counter(d["tokens"])
            self.tf.append(c)
            self.len.append(len(d["tokens"]))
            for term in c:
                df[term] += 1
        self.df = df
        self.avgdl = (sum(self.len) / self.N) if self.N else 0.0
        # precompute idf
        self.idf = {}
        for term, n in df.items():
            self.idf[term] = math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def score(self, query, doc_idx):
        tf = self.tf[doc_idx]
        dl = self.len[doc_idx]
        s = 0.0
        for term in tokenise(norm(query)):
            if term not in tf:
                continue
            f = tf[term]
            idf = self.idf.get(term, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            s += idf * (f * (self.k1 + 1)) / denom
        return s

    def search(self, query, k=10):
        scored = []
        for i in range(self.N):
            s = self.score(query, i)
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], self.docs[x[1]]["key"]))
        return [(s, self.docs[i]) for s, i in scored[:k]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("-k", type=int, default=10)
    ap.add_argument("--years", type=int, nargs="*")
    ap.add_argument("--build-cache", action="store_true",
                    help="report index statistics and exit")
    args = ap.parse_args()

    turns = load_corpus(set(args.years) if args.years else None)
    if args.build_cache:
        print(f"docs: {len(turns):,}")
        print(f"vocabulary: {len(set(t for d in turns for t in d['tokens'])):,}")
        return 0

    bm = BM25(turns)
    print(f"corpus: {bm.N:,} docs   avg tokens/doc: {bm.avgdl:.0f}")
    print(f"query : {args.query!r}")
    print(f"tokens: {tokenise(norm(args.query))}")
    print()
    for rank, (s, d) in enumerate(bm.search(args.query, k=args.k), 1):
        print(f"{rank:>3}. {s:7.3f}  {d['key']:30s} {d['date']} {d['group']:9s}")
        print(f"      {d['speaker']}")
        print(f"      {d['text'][:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
