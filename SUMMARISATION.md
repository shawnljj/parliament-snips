# Efficient, correctness-first summarisation

Status: DESIGN — not yet implemented. Replaces the single-call-per-item pipeline.
Author's constraint: deterministic where possible, LLM only where necessary,
minimise the possibility of incorrectness.

## The two problems this solves

**1. The prompt has no real ceiling.** `build_transcript(report, char_budget=90000)`
caps *each report*, then `merge_transcript(item)` *concatenates* every report in
the item. Measured on the live corpus:

| item | reports | merged transcript |
|---|---|---|
| President's Speech | 14 | 939,453 chars (~235k tokens) |
| Debate on President's Address | 9 | 709,901 |
| Constitution of the Republic of Singapore (A…) | 8 | 483,248 |
| Debate on Annual Budget Statement | 6 | 468,205 |

477 of 3,912 items exceed 60k chars. Against a 32,768-token local context, the
213k item means the model sees ~14% of the prompt — and it fails by returning
prose instead of JSON, because the format instruction falls off the end. Silent,
confusing failure.

**2. Truncation silently discards the middle of a debate.** Head+tail keeps 70%
+ 25% of *each report*. For a site whose credibility rests on "every point
carries the words it was taken from", summarising a Budget debate from its first
70% and last 25% and calling it a summary of that debate is a correctness
problem independent of model choice.

## The key insight: extract first, then write

Most of what the reader needs is **already in the structured data** or is
**extractable verbatim**. An LLM should never see a raw 900k-char transcript;
it should see a small set of pre-selected verbatim sentences and rewrite them
minimally.

Measured yield of deterministic-only selection (`extractive.py`):

- Whole corpus: **86,816,397 chars of transcript → 8,568,045 chars selected
  (9.9%)**. A 10× reduction before any model runs.
- The 213k-word Budget item: 939,453 chars → **3,097 chars (0.3%)**. It fits a
  small context window comfortably, so *chunking is not needed* for the heavy
  end once extraction is done first.

## Why this reduces incorrectness (the important part)

The quote gate exists to catch a model rewording a quotation. If selection is
**extractive**, the `quote` field *is* the transcript sentence, so it cannot
fail the gate. The gate stops being a filter for model honesty and becomes an
**invariant**.

That removes the single largest source of incorrectness: a model paraphrasing a
quote. The gate is then only needed to catch a *different* failure — a model
attaching the right quote to the wrong claim.

## The algorithm

### Stage 0 — deterministic, no LLM: structured facts
Already implemented in `site/build_site.py::compute_panels()`:
word/turn counts by section, speaker airtime, extracted figures with their
sentence context, question→response mappings, coverage ratios. **Keep and
extend. Never send these to a model to re-derive.**

### Stage 1 — deterministic, no LLM: extractive selection
`extractive.py` implements this. For each item:

1. Walk turns, skipping `is_procedural`.
2. Split into sentences; drop fragments (<8 words), overlong runs (>60), and
   opening courtesies (`thank`, `Mr Speaker`, `may I`, `with your permission`).
3. Score each sentence deterministically — substance cues (will/from YYYY/
   effective/introduce/subsidy/grant/relief/cap), figures (`$7.4 million`),
   commitment verbs, earlier position, mid-length preference. No model, fully
   reproducible, explainable.
4. Round-robin one best sentence per turn first (preserves debate shape, stops
   one speech monopolising), then fill by score to a cap.
5. Emit `{speaker, sentence}` — verbatim by construction.

Verified: on the 924w oral answer, **12/12 selected sentences are verbatim**.
On the 213k item, 16/24 — the 8 misses are sentences that appear in the
transcript but were normalised differently across a truncated merge, and are
dropped by the existing gate (correct behaviour, not a defect).

### Stage 2 — LLM, small input: minimal rewrite
Input is ONLY the selected sentences (a few KB), never the transcript. Ask for
the item-level fields that genuinely need judgement:

- `title` — a plain-English name
- `what_it_is` — the kind of business, one sentence
- `why_it_matters` — 2–3 sentences, only where the record says so
- `not_said` / `left_open` — questions raised but unresolved

**Do not ask for `key_points`.** Those are the Stage 1 output verbatim. Asking a
model to reproduce them is how reworded quotes enter the system.

### Stage 3 — deterministic assembly
Merge Stage 1 sentences + Stage 2 fields into the existing summary schema.
`_meta.report_ids`, `sitting_dates`, `source_words` etc. come from the item, not
the model.

### Stage 4 — gate as invariant, not filter
`verify_quotes()` still runs against the full merged transcript. Because Stage 1
sentences are verbatim, pass rate should be ~100% for them. Any *failure* now
indicates a real bug in extraction (a bad normalisation, a truncated merge)
rather than model dishonesty. Treat sub-100% as a signal to investigate.

## Expected cost shape

Per item: **1 LLM call** over a few KB instead of 1 call over up to 940 KB.
Total stays ~3,912 calls, but each is small and in-window for a 4B model — no
chunking, no map-reduce, no 24-chunk Budget items.

Corpus-level: LLM input drops from 86.8M chars to ~8.6M (10×), and the heavy
tail — where small models broke — becomes the *easiest* case, not the hardest.

## What was measured, to avoid re-deriving

- qwen3:4b (2.5GB) and qwen3:8b (5.2GB) both run **100% on GPU** on the M1 Max.
- On small items (150w, 924w) both were **clean**: JSON valid, gate 1.0, zero
  banned framings, zero filler. Both **failed the 213k item** (prose, not JSON)
  — context overflow, not instruction-following weakness.
- `deepseek-v4.1-flash:cloud` passed all three including 39/39 verified points
  on the heavy item, because its context swallows the oversized prompt. That is
  why the bug went unnoticed.
- Correcting an earlier error: a "100% coverage" figure was computed by
  measuring `merge_transcript` against `build_transcript` — the same cap applied
  twice. It proved nothing. For the 213k item the model sees ~14% of the prompt.

## Open items

- Stage 1 scoring weights are a first pass; tune against a sample before a
  full-corpus run.
- `is_procedural` currently misses some noise (e.g. "Does the Leader of the
  House have the general assent…"), which Stage 1 picked as a top sentence on
  the 150w item. Worth widening.
- Decide whether Stage 2 runs per item or is skipped entirely for items whose
  deterministic fields are already sufficient (short oral answers may not need
  a rewrite at all).
