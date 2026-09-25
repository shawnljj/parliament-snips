# SPRINT 02 — READING MODE

**Started 2026-09-25.** Shawn's direction: reading mode is the original product objective — read
the sittings alongside the full transcript, highlighted and summarized — and the summaries belong
in SQLite with real FK relations.

## Why this sprint exists

Sprint 01 built the ask side: a question goes in, cited answers or refusals come out. The product's
*first* objective was always the other direction — a reader opening a sitting and reading it. The
existing site has 3,863 summarised briefs across 332 pages, so the content existed; what was
missing was the **bridge from a summary to its exact place in the record**, and the record itself
at reading length (the shipped pages run 77,000–178,000px tall).

## Decision 1 — the summaries go in SQLite, with FKs

Shawn's instruction, verbatim: *"Store the summaries on SQLite directly and use FK relations to
relate them to the turns, sittings, etc."*

The FK to `turn(key)` is the load-bearing part. It makes the one relationship that did not exist
anywhere — **which transcript turn a summary sentence came from, and where inside it** — both
explicit and enforced. A dangling anchor becomes impossible to insert.

Five tables, 3,863 items, 68,963 sections, 134,500 sentences. See `rag/READING-MODE.md`.

## Decision 2 — anchor by CONTENT, never by the stored index

The Stage-1 dataset stores a per-turn `t`. It reads as a clean `t = turn - 1` on the first
single-report item (twelve turns in a row), and that is a trap: measured across 508 turns, only
378 follow one rule. **225 of 3,910 items (5.8%) span up to 14 reports**, so their `t` accumulates
across report boundaries and the offsets 4, 16, 19, 22, 24 appear.

A formula would have landed the reader on a *neighbouring speech* with nothing reporting a problem
— the third surfacing of one root cause in this project (Sprint 01 lost a day to the same class).

**Resolution by content: 134,393 of 134,500 = 99.92%**, in ~40 seconds for the whole corpus. The
105 ambiguous (repeated procedural lines — one occurs 18×) and 2 unresolved are **recorded with
their reason**, never guessed.

## Decision 3 — render the WHOLE record, highlight the selected part

The summary layer is a **selection**: 21.5M of 138.7M characters corpus-wide = **15.5%** of the
transcript (per sitting: 14.7%–26.2%). "Read alongside the full transcript" therefore means
rendering every turn with the summarised passages marked inside it. Rendering only the selected
sentences would show a seventh of what was said and call it the record.

## Decision 4 — the turn set is a UNION, and both halves are load-bearing

Measured, both directions:

| set | meaning | 2024-02-07 | 2026-08-05 |
|---|---|---|---|
| A | turns of reports dated the sitting | 387 | 558 |
| B | turns of reports the day's items reference | 375 | 263 |
| **A ∪ B** | correct | **387** | **558** |

- **A alone** loses 275 of 882 highlights on 2024-02-07 (items span sittings).
- **B alone** loses 295 turns on 2026-08-05 (items don't cover every report — mostly written
  answers).

The first shipped version used B and lost the 295; the second used A and lost the 275. The gate now
asserts exact equality with A ∪ B.

## Exit criteria

| # | criterion | required | measured |
|---|---|---|---|
| 1 | anchor coverage | ≥ 99% | **99.92%** (134,393/134,500) |
| 2 | FK integrity | 0 violations | **0** |
| 3 | FK enforcement | every dangling ref rejected | **10/10 probes** |
| 4 | offsets reconstruct the sentence | 100% | **5,000 checked, 0 bad** |
| 5 | render emits every highlight | exact | **4 sittings, exact** |
| 6 | mobile ergonomics | 0 overflow, ≥44px taps | **360/390/414px clean** |
| 7 | RAG eval unchanged | no regression | **16/17, 0 flips across 5 runs** |
| 8 | the ask box is real | runs the pipeline, links into the record | **answer 2.2–2.9s, refusal 5.6s** |
| 9 | a citation lands on the quoted words | marked text EQUALS the quote | **60/60 chunk, 40/40 quote** |
| 10 | rank-skip changes no decision | identical at the caller's k | **17/17, 6.1x faster** |

## Sprint 02b — the ask box and the citation bridge (built 2026-09-25)

Shawn's original objective was one product: *ask* Hansard questions and *read* sittings side by
side. The halves were built separately, so this closed the seam.

- The index's search box is **wired**: `/ask?q=` runs the real pipeline and renders server-side, so
  answers work without JavaScript and every answer is linkable.
- Every quote links into the record — `/read/<date>?turn=<key>&span=<a>-<b>` — landing on the
  **exact sentence quoted**, inside the full transcript (563 turns on the A1 date).
- The span comes from the answer's own quote text narrowed inside the cited chunk. Marking the
  whole chunk instead was the first implementation: 812–6,000 characters, i.e. a different
  sentence from the one quoted, and the page still looked correct.

Two defects found and fixed on the way, both invisible from the outside:

1. **Every question paid 13s for an ordering that was thrown away.** `answer.py` called
   `retrieve(..., k=10**7)` then re-ranked by vector, so the BM25 pass over 91,263 turns was pure
   cost — and an unconstrained question (a refusal) fell back to the whole corpus, costing 22.0s.
   `retrieve(rank=False)` skips it: **6.1x faster on retrieval, decisions identical at the caller's
   own k**, eval still 16/17.
2. **The first span implementation marked the wrong words** — 5 of 60 exact, because it could not
   split a span across a summary highlight. Replaced with a per-character pass; **60/60**.

The visible payoff is the A1 supersession: one continuous transcript showing both the retracted
`$15.5 billion` and the corrected `$10.5 billion`, with only the correction marked. The bridge makes
the project's central claim — that the record corrects itself and the reader can see it — checkable
by the reader rather than asserted by the tool.

## The teaching point

Sprint 01's spine was "the only genuinely new thing is the test oracle problem." Sprint 02 adds its
counterpart: **a relationship the data model never recorded cannot be recovered by arithmetic.**
The summary and the transcript were both present and both correct; only their *linkage* was
missing, and three separate attempts to derive it by position produced a plausible, silent,
wrong answer. The fix was to resolve it by content and store it — turning an implicit assumption
into a checkable fact.

The other lesson is about a gate's *reach*: the first render gate passed at 607/607 while
31% of highlights were missing, because the gate asked the same wrong question the render
did. A gate is only evidence against the question it actually asks.

## Files

- `rag/build_summaries.py` — schema + loader + verifier
- `rag/test_summary_fk.py` — 10 FK/cascade injection probes
- `rag/test_reading_query.py` — traversal, offsets, citation chain
- `rag/test_read_render.py` — render completeness + exact turn set
- `rag/read_server.py` — the reading view (port 8444)
- `rag/READING-MODE.md` — the schema and its numbers
- `rag/eval/spread_report.py` — per-item outcome across runs, counting `pass` only

## Still open

- No per-sentence URL fragment yet (the page scrolls to the turn; it does not deep-link a single
  sid — the `?turn=&span=` link is turn-granular, which is what a citation needs).
- `read_sitting()` / `/api/read/` returns the older per-item shape; the HTML uses
  `full_transcript()`. Consolidate when that JSON is next needed.
- The **old static site** (port 8442, built by `site/build_site.py`) predates reading mode and has
  not been re-pointed at it; its 211-screen index still has the pre-Fold design.
- The design sprint's remaining surface: Fold covers the read view and now the ask view; the index
  and the old sitting pages have not had their pass.
