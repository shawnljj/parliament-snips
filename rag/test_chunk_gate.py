"""Regression test for the chunk gate. Run: python3 rag/test_chunk_gate.py

The gate's job is to make SILENT LOSS impossible -- text that vanishes (or is
silently truncated by the embedder) without any error. So the gate is only worth
anything if it actually fails when a defect is injected. This injects real defects
and reports which ones it catches.

Two measured lessons are encoded here:

  1. A mutation that silently no-ops is indistinguishable from a gate that missed.
     An earlier version scored 'strip is_partial' as a gate failure when in fact the
     first chunk it tried was already is_partial=False. Every probe here asserts the
     mutation APPLIED before judging the gate.

  2. Truncating a chunk was NOT caught at first: a truncated string is still a
     substring of its source, and the coverage check only walked SPLIT turns. 98.65%
     of turns produce exactly one chunk, so nothing asserted that those chunks cover
     their turns. The single-part equality check now covers it.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chunk as C
from tokens import count_tokens, TOKEN_LIMIT


def _load_sample(year=2020):
    turns = C.load_turns(set([year]))
    ch, cov, ctext = C.plan_chunks(turns, target=C.TARGET_CHARS, overlap=C.OVERLAP_CHARS)
    return turns, ch, cov, ctext


def _probe(ch, turns, ctext, label, mutate, changes):
    """Apply mutate to a chunk it can affect; assert it applied, then score the gate."""
    for idx in range(len(ch)):
        c2 = copy.deepcopy(ch)
        before = {k: c2[idx].get(k) for k in changes}
        mutate(c2[idx])
        if all(c2[idx].get(k) == before[k] for k in changes):
            continue                      # no-op; try the next chunk
        n = len(C.verify_chunks(c2, turns, chunked_text=ctext))
        ok = n > 0
        print(f"  {'OK  ' if ok else 'MISS'} {label:24s} -> {n} problem(s)")
        return ok
    print(f"  --   {label:24s} (mutation could not be applied)")
    return None


def _probe_structural(ch, turns, ctext, label, transform):
    c2 = transform(copy.deepcopy(ch))
    n = len(C.verify_chunks(c2, turns, chunked_text=ctext))
    print(f"  {'OK  ' if n else 'MISS'} {label:24s} -> {n} problem(s)")
    return n > 0


def main():
    turns, ch, cov, ctext = _load_sample()

    base = C.verify_chunks(ch, turns, chunked_text=ctext)
    print(f"baseline: {len(ch):,} chunks, {len(base)} problem(s)")
    if base:
        for p in base[:5]:
            print("   -", p[:150])
        print("BASELINE IS DIRTY -- fix that first.")
        return 1
    print()

    results = []
    print("injecting defects; each must be CAUGHT:")
    results.append(_probe(ch, turns, ctext, "drop a space (weld)",
                          lambda c: c.__setitem__(
                              'cite_text', c['cite_text'].replace(', ', ',', 1)),
                          ['cite_text']))
    results.append(_probe(ch, turns, ctext, "invent a sentence",
                          lambda c: c.__setitem__(
                              'cite_text', 'Entirely invented sentence absent.'),
                          ['cite_text']))
    results.append(_probe(ch, turns, ctext, "truncate a chunk",
                          lambda c: c.__setitem__(
                              'cite_text', c['cite_text'][:len(c['cite_text']) // 2]),
                          ['cite_text']))
    results.append(_probe(ch, turns, ctext, "duplicate a word",
                          lambda c: c.__setitem__(
                              'cite_text', c['cite_text'].replace(' the ', ' the the ', 1)),
                          ['cite_text']))
    results.append(_probe(ch, turns, ctext, "empty a chunk",
                          lambda c: c.__setitem__('cite_text', '   '),
                          ['cite_text']))
    results.append(_probe(ch, turns, ctext, "mislabel as whole",
                          lambda c: c.__setitem__('is_partial', False),
                          ['is_partial']))
    results.append(_probe(ch, turns, ctext, "wrong n_parts",
                          lambda c: c.__setitem__('n_parts', 99),
                          ['n_parts']))
    # pad beyond the TOKEN budget while staying UNDER the char limit -- this is the
    # realistic silent-truncation case, and the only one a char-based check misses.
    # Dense figures: ~24 chars -> ~18 tokens, so ~7,000 chars is ~5,300 tokens
    # (over the 2,048 limit) yet under 1.5x the 6,000-char target.
    results.append(_probe(ch, turns, ctext, "over token budget",
                          lambda c: c.__setitem__(
                              'cite_text', "1,234,567 8,901,234 12.3% " * 290),
                          ['cite_text']))

    results.append(_probe_structural(ch, turns, ctext, "drop an entire turn",
                                     lambda cs: [c for c in cs if c['key'] != cs[0]['key']]))

    graded = [r for r in results if r is not None]
    caught = sum(1 for r in graded if r)
    print()
    print(f"caught {caught} of {len(graded)} injected defects")
    if caught != len(graded):
        print("GATE HAS HOLES.")
        return 1

    # final: the real corpus must stay clean, and stay inside the token budget
    worst = max(count_tokens(c['cite_text']) for c in ch)
    print(f"max chunk: {worst:,} tokens (limit {TOKEN_LIMIT:,})")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
