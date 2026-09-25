"""Sentence segmentation for Hansard, shared by the chunker, the gate and the eval.

WHY SPANS, NOT STRINGS
----------------------
Every sentence is returned as a (start, end) span into its source turn, never as a
copy. A quote can then be proven VERBATIM in place, which is what the citation gate
needs: a copy can silently drift, a span cannot. Measured: 1,047,640 sentences, every
one an exact substring of its source.

WHY THE ABBREVIATION LIST IS SHORT AND SPECIFIC
-----------------------------------------------
The list exists for tokens whose PERIOD is not a sentence end ("Mr.", "No. 5"). It must
NOT contain acronyms that never take a period. Measured bug: "jss" was in the list
because JSS is an acronym, so `"What is the JSS?" The Minister` was read as one sentence
-- the sentence-ending "?" was skipped because the preceding word looked like an
abbreviation. Only add a token here if it actually appears WITH a trailing period.
"""
import re

# Tokens whose trailing period is part of the token, not a sentence end. Hansard is
# formal and uses a small stable set. Add sparingly, and only from real text.
ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "hon", "sr", "jr", "st",
    "no", "nos", "vol", "sec", "pp", "p", "al", "eg", "ie", "etc", "vs", "cf",
}

# FIX: allow closing quotes/brackets between the terminal mark and the whitespace, so
# `?"  The Minister` splits. Earlier the lookbehind demanded the mark be immediately
# before the whitespace, so any quoted sentence-ending punctuation under-split.
SENT_END = re.compile(r'(?<=[.!?])["\'\)\]\u201d\u2019]*\s+(?=[A-Z"\'\u201c\u2018(])')

_WORD = re.compile(r"[A-Za-z]+")


def sentence_spans(text):
    """Split `text` into sentence spans. Returns [(start, end), ...].

    Under-splitting is preferred to over-splitting: a merged pair of sentences is
    still verbatim and quotable, whereas an invented split produces a "sentence" that
    no one said. The hand-checked cases in tools/test_sentences.py guard both ways.
    """
    if not text:
        return []
    parts, pos = [], 0
    for m in SENT_END.finditer(text):
        words = _WORD.findall(text[pos:m.start()])
        if words and words[-1].lower() in ABBREV:
            continue
        parts.append((pos, m.start()))
        pos = m.start()
    parts.append((pos, len(text)))
    return [(a, b) for a, b in parts if text[a:b].strip()]


def sentences(text):
    """Sentence strings (convenience; prefer sentence_spans when provenance matters)."""
    return [text[a:b] for a, b in sentence_spans(text)]
