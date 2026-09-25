# Reading mode: the summaries in SQLite

**Built 2026-09-25.** The summary corpus now lives in `hansard.db` with real foreign keys, so a
reader can traverse from a sitting to a highlighted sentence in one query.

## Why this shape

Shawn's direction: *"Store the summaries on SQLite directly and use FK relations to relate them to
the turns, sittings, etc."* The FK to `turn(key)` is the load-bearing part — it makes the anchoring
relationship explicit and enforced rather than implicit, and that relationship was the one thing
missing from the data model.

## Tables

```
summary_item            one row per brief                      3,863
  └─ summary_item_report   item → report  (FK report.report_id)    4,231
  └─ summary_item_sitting  item → sitting (FK sitting.date)        4,010
  └─ summary_section       one row per section                    68,963
        └─ summary_sentence   one row per verbatim sentence      134,500
              turn_key        FK turn(key)  ← the anchor
              char_start/end  offsets into turn.text
```

`summary_sentence` is keyed `(section_id, ord)` — **not** by `sid`. Measured: `sid` is item-local
(`s00002` occurs 2,205 times across the corpus), so it cannot be a key on its own. It is kept as
text for reference.

## The anchor

Each summary sentence is matched to its transcript turn **by its own verbatim text**, then the
exact character span is computed and stored.

Measured over the full corpus:

- **134,393 of 134,500 anchored = 99.92%**
- 105 ambiguous (repeated procedural lines — *"I will proceed to declare the voting results now."*
  occurs 18×), recorded as `ambiguous`, never guessed
- 2 unresolved, recorded as such
- whole corpus anchors in **~40 seconds**

A sentence that cannot be resolved to exactly one turn is **left unanchored with a reason** rather
than assigned a near-miss. The FK then makes a dangling anchor impossible to insert at all.

### Why not the dataset's own index

The Stage-1 dataset stores a per-turn `t`. It looks like a clean offset (`t = turn - 1`) on the
first single-report item, but measured across 508 turns only 378 follow one rule — **225 of 3,910
items (5.8%) span up to 14 reports**, so their `t` accumulates across report boundaries. Converting
it by arithmetic produces a *different turn's text*, which looks plausible and passes every gate.
Resolving by content and storing the result is the fix.

## The turn set for a reading page

The page must render the **union** of two sets, and either alone is wrong — both measured:

| set | meaning | 2024-02-07 | 2026-08-05 |
|---|---|---|---|
| A | turns of reports dated this sitting | 387 | 558 |
| B | turns of reports the day's items reference | 375 | 263 |
| **A ∪ B** | the correct set | **387** | **558** |

- A alone loses 275 of 882 highlights on 2024-02-07, because items span sittings.
- B alone loses 295 turns on 2026-08-05 (mostly written answers), because items do not cover every
  report.

## Coverage — what the page actually shows

The summary layer is a **selection**, not a replacement:

```
summary layer, corpus-wide: 21.5M chars of 138.7M  =  15.5% of the transcript
per sitting:  2024-02-07 26.2%   2016-01-15 23.0%   2026-08-05 14.7%
```

So the reading view renders the **whole record** with the summarised passages highlighted inside
it — which is what "read the sitting alongside the full transcript, highlighted and summarized"
requires. Rendering only the selected sentences would show a seventh of what was said.

## Commands

```bash
python3 build_summaries.py --dry-run     # measure, write nothing
python3 build_summaries.py --rebuild     # drop + recreate + load + verify
python3 build_summaries.py --verify      # assertions only
python3 test_summary_fk.py               # 10 FK/cascade injection probes
python3 test_reading_query.py            # traversal, offsets, citation chain
python3 test_read_render.py [date ...]   # render emits every highlight, exact turn set
python3 test_read_span.py                # the citation bridge marks the exact quoted words
python3 test_retrieve_rank_equiv.py      # rank-skip is decision-equivalent to the BM25 path
python3 read_server.py --port 8444 --warm  # the reading view (+ the ask box)
```

Last run: **FK probes 10/10 · query gate PASS · render gate PASS · span gate PASS (60/60 chunk,
40/40 quote) · rank-equivalence PASS (17/17) · verify ALL CHECKS PASS**.

## The ask box (2026-09-25)

Shawn's original objective was to *ask* Hansard questions **and** read sittings, side by side. The
two now share one server and one database: `read_server.py` hosts the answer stage as well as the
reading view, so the home page's search box is wired rather than decorative.

- `/ask?q=...` runs the real pipeline (`answer.py`, vector ranker) and renders the answer
  **server-side**, so it works with JavaScript off and every answer is linkable.
- Every quote carries a **link into the record**: `/read/<date>?turn=<key>&span=<a>-<b>`.
- The span is the **quote's own span**, not the whole chunk's — the reader lands on the sentence
  the answer used, marked in amber, in the middle of the full 563-turn transcript.
- Refusal is first class: the page says *"Not in the record"* and lists related passages
  **oldest first**, labelled *"Not an answer to your question"*.

Measured: an answer costs **2.2–2.9s**, a refusal **5.6s** (see the ranking note below);
0 overflow at 360/390, minimum tap 44px, quote contrast 15.5:1, refusal 17.3:1.

### A measured performance defect, fixed

The answer stage called `sql_retrieve.retrieve(..., k=10**7)` and then re-ranked the pool by vector
similarity — which meant **every question paid for a BM25 index over 91,263 turns (13s) and then
discarded the ordering**. Refusals were the worst case: 22.0s, because an unconstrained question
falls back to the whole corpus. `retrieve(rank=False)` skips the scoring the caller is about to
overwrite.

The risk was that the refusal rules live in the same function as the scoring, so skipping one could
skip the other. `test_retrieve_rank_equiv.py` asserts, over all 17 eval questions at the answer
stage's own `k`, that `refused`, `reason`, the candidate pool and `term_hits` are identical —
measured **6.1x faster, decisions unchanged**, and the eval still reads **16/17, zero flips**.

## Effect on the RAG eval

The schema change did not touch retrieval or answering, and the eval confirms it: **16/17 strict,
zero items flipping across five runs**. The one failure is D1, and its refusal is **correct** —
class D means no single passage answers the question, so refusal is the designed behaviour and the
eval's `expect` is what is wrong, not the system.

A note on reading that number: `run_answer_eval.py` writes both `pass` (did the expectation hold)
and `gate_pass` (did the citations check out). Counting `pass or gate_pass` reports **17/17** and
is **wrong** — it counts a failed expectation as a pass whenever the citations happened to check
out, which is exactly what masked D1. `eval/spread_report.py` counts `pass` only.


## Render notes

The highlight is computed **server-side** by slicing `turn.text` at the stored offsets. Doing it in
the browser would need the loader's normalisation (unicode dashes, curly quotes, `\xa0`)
re-implemented in JS, and any drift puts the highlight under the wrong words. Measured contrast of
the amber highlight on its own background: **16.4:1**.

## The fold (added 2026-09-25) — collapsed by default, complete by construction

The sitting opens folded, twice over:

| level | unit | default | measured (2024-02-07) |
|---|---|---|---|
| **topic** | one per REPORT — a debate, or a question and its answers | all closed | **57** topics |
| **stretch** | a run of unsummarised text inside a turn | collapsed, inline | **906** toggles |

**A report, not a brief, is the topic.** 57 reports on that sitting against 23 briefs: the 34
reports no brief summarises (mostly written answers) would otherwise sit outside every fold — a part
of the record the reader could not open at all. It also means a topic carries a real name
("Advancing Mental Health"), because a report's title is the subject.

**The fold hides text; it may not drop it.** Every turn is still rendered, and the concatenation of
its runs still equals `turn.text` byte for byte — the same assertion `test_read_span.py` makes for
one turn, now made for all of them by `test_read_fold.py`. Collapsed stretches carry their exact
sentence count (`[+15 sentences]`, counted with `sentences.sentence_spans`, the same splitter the
chunker uses) and are revealed **in place** by an inline `<details>`: verified that a `details` with
no block box flows at the highlight's baseline, so the reveal rejoins the paragraph instead of
escaping into a block of its own.

**Where it lands:** 835,491 characters → 142,684 open (17.1%) on 2024-02-07, 14.7% on 2026-08-05,
23.0% on 2016-01-15. The page still states both numbers — the highlight coverage it always stated,
and what it folded — because a page that hides most of the record owes the reader an account of how
much.

**Arriving from a citation opens its way in.** The server opens the topic holding the cited turn,
and the page opens the collapsed stretch the citation lands in — the topic fold alone can hide the
passage, which would undo the bridge.

Verified at 360/390/414px by the design mockup pass: 0 horizontal overflow, minimum tap target
44px. **The topic summaries and the inline chips are the one thing here a headless pass could not
re-measure** — headless Chrome wedged on this machine, so the chips were sized by construction
(padding + line box) and confirmed visually; re-check the chip's hit area on a real phone.

## Still open

- No per-sentence deep link yet — the read page anchors by scroll, not by URL fragment.
- `read_sitting()` / `/api/read/` still returns the per-item view from before the full-transcript
  work; the HTML page uses `full_transcript()`. Consolidate when the JSON shape is next needed.
- The RAG search box is on the read index and the read page. The **old static site** (port 8442)
  was built by `site/build_site.py` before reading mode existed and has not been re-pointed at it.

## What the bridge teaches (worth keeping)

Marking the exact quoted words is not cosmetic. On the A1 question the page shows both the original
answer (`$15.5 billion`) and the correction (`$10.5 billion`) in the same transcript, and only the
corrected one is marked — the reader can see the supersession happen instead of taking a number on
trust. The first implementation marked whole 2,000-character chunks, which technically satisfied
"link to the source" while showing the reader the wrong sentence; the gate that asserts the marked
text **equals** the quote is what forced the narrower span.
