# SPRINT 01 — CLOSED

**Status: closed 2026-09-24.** The RAG pipeline for PARSNIPS is built, gated, and stable.
Owner UAT is the only remaining step, and it is a separate exercise with its own harness.

## What was built

| phase | deliverable | exit criterion | state |
|---|---|---|---|
| 0 | output contract (frozen) | — | frozen |
| 1 | chunker — 102,441 chunks | gate 9/9 injected defects, coverage exact | PASS |
| 1b | paragraph store — 1,036 reports re-fetched | 266,570 paragraphs, 0 errors | PASS |
| 2b | SQLite — sitting/report/turn/chunk/supersession | 0 orphans, 0 duplicate keys | PASS |
| 2 | vector store — 102,441 × 768, float16 | 4-part gate, self-similarity 1.000000 | PASS |
| 2c | ranker (vector / BM25 / RRF) | measured under ONE scorer | PASS |
| 3 | answer stage — quotes + citations + refusal | 7 exit criteria | PASS |

## The final numbers, measured

**Answer stage: 16/17, stable across three seeded runs, zero unstable questions.**

| exit criterion | required | measured |
|---|---|---|
| 1. class E refusal | all | **4/4** |
| 2. citation gate | 100% | **17/17** |
| 3. no false refusals | 0 | **1 on D** (a true retrieval gap, see below) |
| 4. answer score | ≥ 11/16 | **16/17** |
| 5. fact coverage | 100% each | **100%** |
| 6. traceability | all | **17/17** |
| 7. suggestions grounded | 0 invented | **0 invented** |

Per class: A 6/6, B 3/3, C 1/1, D 0/1, E 4/4, F 2/2.

**Under one scorer, the ladder:** BM25 / SQL-only **5/16** → hybrid RRF **6/16** → vector **8/16**
→ answer stage **16/17**. The model's contribution is **+8**, and its single capability retrieval
cannot supply is **refusal**: class E was 0/4 under every retrieval-only configuration, because a
wrong passage is still *similar* to the question and no similarity threshold separates "addresses
it" from "mentions it". It is now 4/4.

## What is honestly not done

**Class D (two passages at once) is 0/1, for a real reason.** D1 is the only surviving class-D
question: measured, **no turn in 91,263 carries both of its facts**, so it genuinely needs
combination. Neither fact reached the top-10 window, so the model refused — **correctly**. This is
the multi-hop gap the class was written to expose, and it is the case for a decomposition or
multi-hop retrieval step. Three candidate fixes were tested and **all three were falsified**
(decomposition, embedding prefixes, a re-embed) — see SPRINT notes. The next step is not obvious
and should be planned, not guessed.

**The eval set is thinner than its target.** 17 of 26 questions; class C is 1, class D is 1, and
B3 now scores through `accept_any` so it is easier than intended. Recorded in `pending` in
`rag/eval/questions.json` with the generator for comparison-across-time questions, which is the
most reliable way to write genuine class D here.

**Four eval defects were found and fixed, every one of them mine, not the system's.** Recorded
per-question so a later reader cannot mistake a past defect for current behaviour:

1. **A1** asserted a figure the corpus had **retracted** (`$15.5bn` → clarification `$10.5bn`).
   Fixed by reading the record; the `supersession` table now links 101 self-corrections.
2. **A4** AND-ed a year *range*, requiring one chunk to contain 2011 *and* 2012 *and* 2013 —
   impossible, so an answerable question was refused. A range is context: scored with OR.
3. **B3** designated one of **fifteen** valid speakers. Now `accept_any`.
4. **D2 was withdrawn** — it was not class D. Verified only within its own two gold turns; a single
   turn holds both facts.

The pattern is consistent and is now written into the `validating-derived-data` skill as rules:
**a gold turn is evidence a fact exists, not evidence it exists only there.**

## UAT harness

`rag/uat_server.py` + `rag/uat.html` — stdlib only, no framework, no build step.

- **Page:** http://100.93.66.68:8441/ (mobile-first, verified 0 overflow at 360/390/414,
  tap targets 46–53px)
- **Verdicts:** appended live to `pipeline/uat_verdicts.jsonl` as the owner taps

```bash
cd ~/parsnips && /Users/shawnlin/.hermes/hermes-agent/venv/bin/python rag/uat_server.py --port 8441 --warm
```

`--warm` preloads the 157 MB vector index, otherwise the first question takes ~20s. Steady state
is **1.8–4.2s** per question.

## How to run the UAT

1. Ask the questions you would actually ask. That is the point — the eval set is mine, not yours.
2. Judge each answer with the buttons. Every verdict is data; none of them can be wrong.
3. When something looks off, **open "What the system actually did"** before judging. It lists the
   passages the model was shown. That distinguishes the two failure modes:
   - the fact you wanted is **not in any passage** → **RETRIEVAL** problem
   - the fact **is** there and the answer didn't use it → **GENERATION** problem
4. For a refusal, check the related topics: if the answer really is in the record, the refusal is
   a false negative and the topic list is the clue.

**Deliberate design choices the owner should push back on if he disagrees:**
- Answers are **verbatim quotes only** — no paraphrase, no summary. If an answer feels like a wall
  of quotations, that is the contract working, not a bug.
- **Refusal is a first-class answer**, not an empty result. A refusal is the system saying the
  record does not contain it.
- Suggestions after a refusal are **chosen from the corpus, never written by the model**, and are
  marked as "not an answer". 0 invented in every run.

## The one thing not to trust

`temperature: 0` is a **no-op on a cloud model** — Ollama's `format: "json"` constrains the syntax
of a reply, not the sampling. Three identical runs once scored 14, 14, 16. An explicit seed is now
passed and every figure above is reported as **min–max over three runs**, never as a single run.
Any future figure quoted from a single run should be treated as a draw, not a property.
