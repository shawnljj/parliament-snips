# Sprint review findings — 2026-09-21

Captured because both cards' stated causes turned out to be partly wrong. Each card
was written from a summary of earlier work; running the actual check produced a
different shape.

## 2016's 672 defects are TWO defects, not one

The card says "text shifted exactly one sid" across 24 briefs. Measured: 672
mismatches in 24 briefs, split into two classes with different causes.

**Class A — a genuine one-sid shift: 641 mismatches in 3 briefs.**

    bill-223+224+225+226+227+228+229+230    601
    bill-236                                 32
    oral-answer-1392                          8

Each published sentence matches the record of its NEIGHBOUR, so ids were renumbered
after those briefs were built. Regenerating them is the fix, and it is safe: the
content is intact and only the position is wrong.

**Class B — whitespace only: 31 mismatches in 22 briefs.**

    published : "In the case Page: 10 of PCP, we do track after that."
    record    : "In the case \t\tPage: 10 of PCP, we do track after that."

    published : "(In Mandarin):   I support MAS' decision..."
    record    : "(In Mandarin):  \xa0I support MAS' decision..."

Tabs and non-breaking spaces were normalised when those briefs were written, or
during a later dataset rebuild. The words are identical.

**Why the split matters.** `check_selection` compares with `.strip()` only, so any
internal whitespace difference counts as a verbatim violation. Class B is therefore
a real A-1 failure by the strict rule (D-3: byte-identical or not published) but it
is NOT a stale-brief problem, and regenerating is not obviously the right fix: the
stored dataset itself contains the tabs and `\xa0`, so a byte-exact brief would have
to publish control characters. The decision is the owner's — normalise on publish
and loosen the gate, or keep the gate strict and accept that some items cannot be
published byte-exactly.

Regenerating all 24 as the card says would fix Class A and silently reproduce
Class B.

## The 9 unaccounted items: confirmed, with corrected word counts

Recomputed against `pipeline/withheld/` (a FLAT directory, `<item>.json`, no year
subdirectory — the first attempt at this check looked for `withheld/<year>/` and
reported 0 records, which is why the count looked wrong).

    published 3,863 | withheld 7 | empty 33 | UNACCOUNTED 9

    year  item                  sentences  words
    2017  oral-answer-1819             9    228
    2018  president-address-14        37    806
    2019  motion-1178+1202             1      8
    2020  motion-1366+1407             1     12
    2020  motion-1511                  1     12
    2023  motion-2162                  1     20
    2025  motion-2759                  1      8
    2026  motion-2799                  1      8
    2026  motion-2839                  2     21

The count of 9 is right. The word figures in the earlier review were taken from a
different source and are wrong for several rows; the values above are from
`load_item` on the dataset items themselves.

Note `president-address-14` is 806 words, not 833.
