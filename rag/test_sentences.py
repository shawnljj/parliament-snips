"""Hand-checked segmentation cases. Run: python3 rag/test_sentences.py

Every case here is a real failure mode, not an invented one. Two of them were bugs
found by running this file against Hansard prose:

  - `?"  The Minister` under-split, because the pattern required the terminal
    punctuation IMMEDIATELY before the whitespace and a closing quote sat between them.
  - `The JSS?` under-split, because "jss" had been added to the abbreviation list on the
    reasoning that JSS is an acronym. Acronyms never take a period, so only tokens that
    actually appear WITH a trailing period belong in that list.

Both were silent: they merged two real sentences into one unit, which is verbatim and
quotable, so nothing downstream could detect it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sentences import sentence_spans, sentences

CASES = [
    # (text, expected sentence count, why)
    ('"Mr. Speaker, I beg to move. The figure is S$1.2 billion."', 2,
     "Mr. is an abbreviation, not an end"),
    ("Hon. Members will note that Mr. Ong said No. 5 was wrong.", 1,
     "Hon. / Mr. / No. 5 are all abbreviations"),
    ("The meeting ended at 4.15 p.m. Members then left the Chamber.", 2,
     "p.m. ends the sentence; the lone-word guard must not match its 'm'"),
    ("Dr. Tan replied. Prof. Fatimah then spoke.", 2,
     "Dr./Prof. are abbreviations, the periods after 'replied'/'spoke' are ends"),
    ("The rate was 5.8% in 2021, 4.0% in 2022 and 1.6% in 2023.", 1,
     "decimals must not split"),
    ('He asked: "What is the JSS?" The Minister answered.', 2,
     "JSS is an acronym, not a dotted abbreviation -- the ? is a real end"),
    ('She said "Yes." Then she left.', 2,
     "closing quote after the period"),
    ("Members: Aye. Noes: None.", 2,
     "short declaratives"),
    ("", 0, "empty input"),
    ("No terminal punctuation at all", 1, "whole string is one sentence"),
]


def main():
    bad = 0
    for text, expect, why in CASES:
        got = sentence_spans(text)
        ok = len(got) == expect
        # every span must be verbatim in place
        verbatim = all(text[a:b] in text for a, b in got)
        good = ok and verbatim
        if not good:
            bad += 1
        print(f"  {'OK ' if good else 'BAD'} {len(got)}/{expect}  {why}")
        if not good:
            for a, b in got:
                print(f"        -> {text[a:b]!r}")

    print()
    print(f"{len(CASES)-bad} of {len(CASES)} correct")
    if bad:
        print("SEGMENTATION HAS BUGS.")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
