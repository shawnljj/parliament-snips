"""Exact token counting for nomic-embed-text, so chunk sizing is measured, not guessed.

WHY THIS EXISTS
---------------
nomic-embed-text silently truncates its input: it returns HTTP 200 with a valid
768-dim vector for text it did not fully read, and gives no warning. Measured, the
limit is 2,048 tokens (the model card's `nomic-bert.context_length`), confirmed by 11
independent real-speech measurements of the cut landing at 2,041-2,055 tokens.

A CHARACTER threshold cannot express this safely. The same limit is reached at 3,296
chars for dense figure-heavy text and 12,256 chars for plain prose -- a ~3.7x spread.
Chunking by characters is therefore only safe at the worst density, and Budget
speeches are exactly the dense case. So chunks are sized and checked in TOKENS.

The tokenizer is the model's real WordPiece vocab (rag/nomic-tokenizer.json),
implemented here to avoid a heavyweight dependency. It is validated by reproducing the
independently measured cut points (see tools/check_tokenizer.py).
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOKENIZER_PATH = os.path.join(_HERE, "nomic-tokenizer.json")

_TOK = None
_VOCAB = None
_UNK = "[UNK]"

# nomic-embed-text's context window, from the model card. Verified against 11
# measured truncation points.
TOKEN_LIMIT = 2048


def _load():
    global _TOK, _VOCAB, _UNK
    if _VOCAB is not None:
        return
    with open(_TOKENIZER_PATH, "r", encoding="utf-8") as fh:
        _TOK = json.load(fh)
    _VOCAB = _TOK["model"]["vocab"]
    _UNK = _TOK["model"].get("unk_token", "[UNK]")


def _normalize(s):
    """BertNormalizer with lowercase + clean_text as declared in the tokenizer JSON."""
    s = s.lower()
    # clean_text: drop control chars, collapse whitespace (incl. \xa0, which \s misses)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = re.sub(r"[\s\u00a0]+", " ", s)
    return s.strip()


def _pre_split(s):
    """BertPreTokenizer: whitespace, then split punctuation off as its own tokens."""
    out = []
    for chunk in s.split():
        parts = re.findall(r"[^\w\s]+|\w+", chunk, flags=re.UNICODE)
        out.extend(parts if parts else [chunk])
    return out


def _wordpiece(word):
    """Greedy longest-match-first WordPiece."""
    if not word:
        return []
    if len(word) > 100:
        return [_UNK]
    toks, start = [], 0
    while start < len(word):
        end = len(word)
        cur = None
        while start < end:
            sub = word[start:end]
            if start:
                sub = "##" + sub
            if sub in _VOCAB:
                cur = sub
                break
            end -= 1
        if cur is None:
            return [_UNK]
        toks.append(cur)
        start = end
    return toks


def count_tokens(text):
    """Tokens nomic-embed-text will actually read, including [CLS] and [SEP].

    Returns the FULL count (not clamped), so callers can detect overflow. Use
    within_limit() for the boolean question.
    """
    _load()
    if not text:
        return 0
    s = _normalize(text)
    n = sum(len(_wordpiece(w)) for w in _pre_split(s))
    return n + 2      # [CLS] + [SEP]


def within_limit(text, limit=TOKEN_LIMIT):
    return count_tokens(text) <= limit


def fits_budget(text, fraction=1.0, limit=TOKEN_LIMIT):
    """True if text fits in `fraction` of the model's window."""
    return count_tokens(text) <= int(limit * fraction)


if __name__ == "__main__":
    sample = "Mr Speaker, Sir, I beg to move."
    print(f"{count_tokens(sample)} tokens: {sample!r}")
    print(f"token limit: {TOKEN_LIMIT}")
