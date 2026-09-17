"""
Deterministic-first summarisation: test the core claim.

CLAIM: most of what the reader needs is already in the structured data or is
extractable verbatim. An LLM should only be used where rewriting genuinely
adds value, and when it does, it should be given pre-selected verbatim
sentences rather than a 900k-char transcript.

WHY THIS FIXES CORRECTNESS
Extractive selection picks sentences FROM the transcript. The "quote" field is
therefore the sentence itself -- it cannot fail the verbatim gate, because it
already IS the transcript text. The gate stops being a filter for model honesty
and becomes an invariant. That removes the single largest source of
incorrectness: a model rewording a quotation.

WHAT IS MEASURED HERE (no LLM involved)
  1. How much can be derived with zero LLM calls?
  2. Sentence selection quality: do the selected sentences carry the substance?
  3. How big does the LLM's input become? (the chunking problem disappears if
     we only ever send pre-selected sentences)

Usage: python3 extractive.py
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = "/Users/shawnlin/parsnips"
sys.path.insert(0, os.path.join(ROOT, "summariser"))
import summarise as S  # noqa: E402

DATA = os.path.join(ROOT, "data")

# ---------------------------------------------------------------- text signals
# Cue phrases that mark substance rather than rhetoric. Kept deliberately
# conservative: a false positive here puts an empty sentence in the summary.
SUBSTANCE_CUES = [
    r"\bwill\b", r"\bfrom \d{4}\b", r"\bby \d{4}\b", r"\beffective\b",
    r"\bintroduce[ds]?\b", r"\bimplement", r"\ballocat", r"\bfund",
    r"\bcommitt", r"\bentitle", r"\beligib", r"\bsubsid", r"\bgrant",
    r"\brelief\b", r"\bcap(?:ped)?\b", r"\bceiling\b", r"\bthreshold\b",
    r"\bper cent\b", r"\bpercent\b", r"[$£]\d", r"\b\d[\d,.]*\s*(?:million|billion|m|k)\b",
    r"\bwe (?:are|will|have|intend|plan)\b", r"\bthe (?:Ministry|Government|Board)\b",
]
SUBSTANCE_RE = re.compile("|".join(SUBSTANCE_CUES), re.I)

# Rhetorical / procedural noise that should never reach a summary.
NOISE_RE = re.compile(
    r"^(?:thank|i thank|mr speaker|sir|madam|may i|i beg|with your permission|"
    r"i am happy|i would like to|let me|firstly|secondly|finally|in conclusion)\b", re.I)

SPEAKER_ROLE_RE = re.compile(r"\b(?:Minister|Senior Minister|Parliamentary Secretary|"
                             r"Speaker|Deputy Speaker|Mr|Ms|Mrs|Dr|Prof|Assoc Prof)\b")


def sentences(text, min_words=8, max_words=60):
    """Split into sentences, dropping fragments and overlong runs."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\[(])", text)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        n = len(p.split())
        if n < min_words or n > max_words:
            continue
        if NOISE_RE.match(p):
            continue
        out.append(p)
    return out


def score(sent, position_frac, total_turns):
    """Deterministic sentence salience. No model, reproducible.

    Deliberately simple and explainable: substance cues dominate, then figures,
    then early position (the mover's framing), with a small penalty for very
    long sentences that are usually clause-heavy formalities.
    """
    s = 0.0
    hits = len(SUBSTANCE_RE.findall(sent))
    s += min(hits, 4) * 2.0
    if re.search(r"[$£]\d|\b\d[\d,.]*\s*(?:million|billion)\b", sent):
        s += 3.0
    if re.search(r"\b(?:will|shall|commit|intend)\b", sent, re.I):
        s += 2.5
    # earlier content is usually the substantive framing
    s += (1.0 - position_frac) * 2.0
    # prefer mid-length: very short is often a courtesy, very long is often procedural
    n = len(sent.split())
    if 12 <= n <= 40:
        s += 1.0
    if n > 50:
        s -= 1.0
    # a named speaker role adds authority but not substance; keep it neutral
    return s


def select_turns(item, max_sentences=24):
    """Pick the highest-scoring verbatim sentences across the item's turns.

    Round-robins across turns first so one long speech cannot monopolise the
    summary, then fills by score.
    """
    picked = []
    turns = []
    for r in item["reports"]:
        for t in r.get("turns", []):
            txt = (t.get("text") or "").strip()
            if not txt or t.get("is_procedural"):
                continue
            ss = sentences(txt)
            if ss:
                turns.append((t.get("speaker"), ss))

    # pass 1: best sentence from each turn, in order (preserves the debate shape)
    for spk, ss in turns:
        best, bs = None, -1
        for i, s in enumerate(ss):
            sc = score(s, i / max(1, len(ss)), len(turns))
            if sc > bs:
                best, bs = s, sc
        if best and bs > 2.0:
            picked.append({"speaker": spk, "sentence": best, "score": round(bs, 2)})

    # pass 2: fill with remaining high scorers if we are short
    if len(picked) < max_sentences:
        seen = {p["sentence"] for p in picked}
        rest = []
        for spk, ss in turns:
            for i, s in enumerate(ss):
                if s in seen:
                    continue
                sc = score(s, i / max(1, len(ss)), len(turns))
                if sc > 3.0:
                    rest.append({"speaker": spk, "sentence": s, "score": round(sc, 2)})
        rest.sort(key=lambda x: -x["score"])
        picked.extend(rest[: max_sentences - len(picked)])

    return picked[:max_sentences]


def main():
    sittings = []
    for year in sorted(os.listdir(DATA)):
        d = os.path.join(DATA, year)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.startswith("sitting_") and fn.endswith(".json"):
                sittings.append(json.load(open(os.path.join(d, fn))))

    items = S.summarisable_items(sittings)
    items.sort(key=lambda i: i.get("words", 0))
    sample = [items[0], items[len(items) // 2], items[-1]]

    print("=" * 88)
    print("DETERMINISTIC-ONLY YIELD")
    print("=" * 88)
    for it in sample:
        full, _ = S.merge_transcript(it)
        sel = select_turns(it)
        sel_chars = sum(len(x["sentence"]) for x in sel)
        print(f"\n{it.get('words'):>8,d}w  {it.get('group'):10s} {str(it.get('title'))[:50]}")
        print(f"    transcript      : {len(full):>9,} chars")
        print(f"    LLM input needed: {sel_chars:>9,} chars   "
              f"({sel_chars/max(1,len(full))*100:.1f}% of transcript)")
        print(f"    sentences picked: {len(sel)}")

        # the correctness claim: every picked sentence is verbatim by construction
        t_norm = S.norm(full)
        ok = sum(1 for x in sel if S.norm(x["sentence"]) in t_norm)
        print(f"    verbatim by construction: {ok}/{len(sel)}")

        print("    top picks:")
        for x in sorted(sel, key=lambda y: -y["score"])[:4]:
            print(f"      [{x['score']:>5.2f}] {str(x['speaker'])[:26]:28s} {x['sentence'][:74]}")

    # whole-corpus sizing
    print("\n" + "=" * 88)
    print("WHOLE CORPUS: how much would an LLM ever need to read?")
    print("=" * 88)
    tot_full = tot_sel = 0
    over_ctx = 0
    for it in items:
        full, _ = S.merge_transcript(it)
        sel = select_turns(it)
        sc = sum(len(x["sentence"]) for x in sel)
        tot_full += len(full)
        tot_sel += sc
        if len(full) > 60000:
            over_ctx += 1
    print(f"  items                     : {len(items):,}")
    print(f"  transcript chars total    : {tot_full:>12,}")
    print(f"  LLM input if pre-selected : {tot_sel:>12,}  ({tot_sel/tot_full*100:.1f}%)")
    print(f"  items over 60k chars      : {over_ctx:,}  (these broke the small models)")
    print(f"  ...but their SELECTED text fits a small window, so chunking is not needed")


if __name__ == "__main__":
    main()
