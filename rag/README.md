# `rag/` — ask the Hansard, and read it

The retrieval and answer layer over Singapore's Hansard, plus the reading view that puts an
answer's source back in front of you.

Two halves, one database:

- **Ask** — a question goes in, quoted passages with citations come out, or a refusal.
- **Read** — the full record of a sitting, with the summarised passages highlighted in place.

They are connected: every quote carries a link into the transcript at the exact sentence it
came from.

```bash
cd ~/parsnips/rag
python3 read_server.py --port 8444 --warm     # -> http://localhost:8444/
```

Then open `/` and type a question, or tap a sitting to read it.

---

## What it runs on

Nothing. Python 3.9+ and the standard library, except `numpy` for the vector store. No
framework, no server dependency, no build step.

---

## The data

Everything is in `../pipeline/hansard.db` (SQLite, 620 MB):

| table | rows | what it is |
|---|---|---|
| `sitting` | 331 | one Parliament sitting |
| `report` | 20,340 | one Hansard record (a speech, a written answer) |
| `turn` | 91,263 | one speaker's uninterrupted turn — **the retrieval unit** |
| `chunk` | 102,441 | a turn, or part of one, sized to the embedding window |
| `supersession` | 101 | turns that retract an earlier statement |
| `summary_item` | 3,863 | one published brief |
| `summary_section` | 68,963 | a section of a brief |
| `summary_sentence` | 134,500 | a verbatim sentence, anchored to its turn |
| `summary_item_report` | 4,231 | brief → report |
| `summary_item_sitting` | 4,010 | brief → sitting |

`summary_sentence.turn_key` is a **foreign key to `turn(key)`**, and that is the load-bearing
part: it makes "which sentence of the record does this summary sentence come from" an enforced
relationship instead of an assumption. `char_start`/`char_end` are the offsets into `turn.text`.

**Anchoring is 99.92% (134,393 of 134,500).** The 105 ambiguous and 2 unresolved are recorded
with a reason, never guessed.

The corpus data files (the DB, `chunks.jsonl`, `turns.jsonl`, `vectors/`, `para_cache/`) are
**gitignored** — ~1.4 GB of derived, regenerable data. The recipes are versioned; the artifacts
are not.

---

## How a question is answered

```
question
   │
   ├─ SQL        filter the 102,441 chunks by constraint (year, subject term)
   │             → the candidate pool. This is constraint, not similarity.
   │
   ├─ vectors    rank INSIDE that pool by cosine similarity (nomic-embed-text, 768-dim)
   │             → the top k chunks
   │
   └─ model      read the chunks, return verbatim quotes + citations, or refuse
                 → refusal is a first-class outcome, not an error
```

**SQL filters; vectors rank.** The split is *constraint vs similarity*, not *SQL vs RAG*.

**The answer stage is sent chunks, not turns.** A chunk is median 104 tokens, so the quote
*is* the chunk text and the citation check is exact.

### Three decisions that were measured, not assumed

**A year in a question is ambiguous.** "In 2020, how many…" is the year of the *sitting*. "GST
will rise in 2027" is a year *named in the speech*. They live in different places — a metadata
column and the text. A year absent from the metadata is not evidence the corpus cannot answer;
`year = 2027` matches 0 rows, while `cite_text LIKE '%2027%'` matches **407**.

**Subject terms match CONTENT, not `title`.** Filtering on `title LIKE` looks precise and
*excludes the gold*: the report that discusses a subject often does not name it in its title.
Measured — "Joint Singles Scheme": 11 chunks by title, 57 by content, and the gold is in the
content set only.

**A lexical+semantic fusion LOST to pure vector** (6/16 vs 7/16). This corpus is the case where
the lexical signal misleads: 261 candidates for one question all literally contain the scheme
name, so term overlap cannot separate the passage that *answers* from the 260 that merely
mention it. "Combine lexical and semantic" is a hypothesis, not an improvement.

---

## The reading view

`/read/<date>` renders the **full transcript** of a sitting with the summarised passages
highlighted and the brief's summary beside them.

The summary layer covers only **15.5%** of the transcript (21.5M of 138.7M characters), so
rendering only the summarised sentences would show a seventh of the record. Every turn is
rendered; the selected parts are marked inside it.

**The turn set is the UNION of two sets, and each alone is wrong:**

- **A** — turns of reports dated this sitting. Alone, this drops **275 of 882** highlights,
  because a brief can span sittings.
- **B** — turns of reports the sitting's briefs reference. Alone, this drops **295 turns** on a
  busy day, because briefs do not cover every report (mostly written answers).

Only A ∪ B is both complete and fully highlighted. The gate asserts exact equality.

**Highlights are computed server-side** by slicing `turn.text` at the stored offsets. Doing it
in the browser would mean reimplementing the loader's normalisation (unicode dashes, curly
quotes, `\xa0`) in JavaScript, and any drift puts the highlight under the wrong words.

### The bridge from an answer to the record

Every quote links to `/read/<date>?turn=<key>&span=<a>-<b>`, which scrolls to the turn and
marks the **exact sentence quoted** — not the whole passage.

A chunk's `cite_text` is a verbatim span of its turn in **400 of 400** sampled chunks, so the
span is found by content, never guessed. But a chunk runs 812–6,000 characters, so marking the
chunk would show a different sentence than the one quoted. Measured: **60/60 chunk spans and
40/40 quote spans exact**.

The payoff is visible on the A1 question: the page shows both the retracted `$15.5 billion` and
the corrected `$10.5 billion` in one continuous transcript, with only the correction marked.

---

## Scoreboard

One scorer, the same 17 questions (`eval/questions.json`, classes A–F):

| stage | score |
|---|---|
| BM25 (SQL only) | 5/16 |
| hybrid (reciprocal rank fusion) | 6/16 |
| vector | 7/16 |
| **answer stage** | **16/17** |

**Zero items flip across six runs.** The model's contribution is mostly *refusal* — class E
(questions that must be refused) goes 0/4 → 4/4.

**D1 fails by design and its refusal is CORRECT.** Class D means no single passage answers the
question, so refusing is the designed behaviour and the eval's `expect` is the thing that is
wrong, not the system.

> **When you quote that score, count `pass` only.** `run_answer_eval.py` also writes
> `gate_pass` (did the citations resolve), and `pass or gate_pass` reports **17/17** — a wrong
> number that masks D1. `eval/spread_report.py` counts `pass` and tags archives older than the
> source as STALE.

---

## Files

### The pipeline

| file | role |
|---|---|
| `tokens.py` + `nomic-tokenizer.json` | the real WordPiece tokenizer, `TOKEN_LIMIT = 2048` |
| `sentences.py` | span-based sentence splitter |
| `chunk.py` | chunk plan + `verify_chunks()` gate |
| `bm25.py` | lexical baseline |
| `build_para_store.py` | re-fetch the paragraph structure the scraper flattened |
| `build_chunks.py` | turns → chunks |
| `build_db.py` | load the tables into SQLite |
| `embed_chunks.py` | resumable embed run (nomic-embed-text) + 4-part gate |
| `vector_rank.py` | cosine ranker over the candidate pool |
| `sql_retrieve.py` | constraint filtering + refusal rules |
| `supersession.py` | find turns that retract an earlier statement |
| `answer.py` | the answer stage: quotes, citations, refusal |

### The reading mode

| file | role |
|---|---|
| `build_summaries.py` | summary corpus → SQLite with FKs (`--dry-run` / `--rebuild` / `--verify`) |
| `read_server.py` | the reading view **and** the ask box (port 8444) |
| `uat_server.py` | the Sprint 01 UAT harness (port 8441) |

### Gates — all of these pass

```bash
python3 test_chunk_gate.py          # chunk plan: no truncation, coverage exact
python3 test_sentences.py           # the splitter's boundary cases
python3 test_summary_fk.py          # 10 FK/cascade injection probes
python3 test_reading_query.py       # traversal, offsets, citation chain
python3 test_read_render.py         # render emits every highlight, exact turn set
python3 test_read_span.py           # the citation bridge marks the exact quoted words
python3 test_retrieve_rank_equiv.py # rank-skip is decision-equivalent to the BM25 path
python3 test_server_error_path.py   # an exception returns 500, not a dead connection
python3 build_summaries.py --verify # 0 FK violations, 0 offset mismatches
python3 eval/verify_eval.py         # the eval set's own consistency
python3 eval/verify_class_d.py      # class D is a property of the corpus, not of a chosen turn
python3 eval/test_expect_logic.py   # the expectation logic itself
python3 eval/test_spread_report.py  # the spread report is honest about stale archives
```

```bash
python3 eval/run_answer_eval.py --json /tmp/run.json
python3 eval/spread_report.py
```

---

## Rebuilding from scratch

```bash
cd ~/parsnips/rag
python3 build_para_store.py     # cached re-fetch of paragraph structure (~200s, resumable)
python3 build_chunks.py         # turns -> chunks  (writes ../pipeline/chunks.jsonl)
python3 build_db.py             # load SQLite    (writes ../pipeline/hansard.db)
python3 embed_chunks.py         # embed (~46 chunks/sec, resumable, 150 MB output)
python3 build_summaries.py --rebuild
```

The embed run is **resumable and flush-ordered**: payload before index, so a crash loses work
rather than correctness.

---

## Two traps worth knowing

**The embed limit is TOKENS, not characters.** `nomic-embed-text` silently truncates past 2,048
tokens and still returns HTTP 200 with a valid vector. The same limit is reached at ~3,296
characters of dense figures but ~12,256 of plain prose — a **3.7× spread**. A synthetic probe
suggested 16,000 characters: wrong by 3.7×, in the unsafe direction. Always use
`tokens.count_tokens()`.

**Never infer a database position from a stored index.** The summarisation dataset stores a
per-turn `t` that is **local to the item**, not to the database. It looks like a clean `turn − 1`
on the first item and fits only 74% of turns; 225 of 3,910 items (5.8%) span up to 14 reports, so
their `t` accumulates across boundaries. Resolve position by **content**, then store it — which
is what `summary_sentence.turn_key` is.

---

## Where the numbers are written down

- `READING-MODE.md` — the summaries schema, the anchor resolution, the read-page turn set
- `UAT.md` — the Sprint 01 UAT harness
- `../SPRINT-01-CLOSED.md`, `../SPRINT-02-READING-MODE.md` — what was built and what was proved
- `../docs/card-verification-chain.md` — the Hansard completeness check (source vs our copy)

Every number in this README is MEASURED. If you change the corpus, re-measure rather than trust
these figures.
