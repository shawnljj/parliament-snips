# Efficient, correctness-first summarisation

**Status: DESIGN AGREED (17 Sep 2026). Algorithm = tiered hybrid, "approach C".**
Supersedes both the single-call-per-item pipeline and the extract-first-only design.
Author's constraint: deterministic where possible, LLM only where necessary, minimise
the possibility of incorrectness.

**DECIDED**
- **Algorithm:** approach C — full-text LLM for small items, map-reduce for heavy
  items, quotes **by reference so the model never retypes them**.
- **Model for benchmarking:** `deepseek-v4.1-flash:cloud` (already proven on the
  heavy 213k item at 39/39 verified points).
- **Stop rule:** the pipeline is built one stage at a time, and work STOPS for review
  after Stage 1 (the dataset) before Stage 2 begins. Do not run the whole pipeline
  through.

## The pipeline stages

This is the authoritative stage list. Each stage is separately runnable and
separately reviewable.

| # | Stage | LLM? | Input | Output | Where |
|---|---|---|---|---|---|
| 0 | Structured facts | no | sitting JSON | counts, speakers, figures, Q→A maps, coverage | `site/build_site.py::compute_panels()` — exists |
| 1 | **Dataset** | no | sitting JSON | per-item prompt payloads + verbatim sentence index | `summariser/build_dataset.py` — **CURRENT STAGE** |
| 2 | Extract | LLM | Stage 1 payload | points + sentence ids, item fields | not built |
| 3 | Assemble | no | Stage 2 output | summary JSON in the existing schema | not built |
| 4 | Verify | no | Stage 3 + full transcript | gate verdict; invariant assertions | `verify_quotes()` exists, needs id-awareness |

### Stage 1 — the dataset (current stage, no LLM)

For every item, emit a self-contained payload containing everything a later stage
needs, so Stages 2–4 never re-read the corpus:

- `id`, `title`, `group`, `report_ids`, `sitting_dates`, `source_words`
- `tier`: `small` (fits the prompt budget whole) or `heavy` (needs chunking)
- `sentences[]`: every eligible sentence with a **stable `sid`**, its speaker, a
  `turn_index`, an `attributed` flag, and its deterministic `score`
- `chunks[]`: for heavy items, chunk definitions as **lists of sentence ids** — so
  chunking is expressed in references, never re-sliced text
- the deterministic pre-filter's view of procedural/noise sentences (excluded, but
  recorded so the choice is auditable)

**Why sentence ids matter.** They are what makes quotes verbatim by construction: the
model returns `sid`s, the assembler substitutes the stored sentence text. A model that
mangles a quote in its head cannot put a mangled quote on the page. This is the single
most important property in the design and it is established here, in Stage 1.

**Sizing rule for the tier split.** A prompt must leave room for output inside the
window. The prompt budget is therefore a *measured constant*, not a guess — record the
character budget actually used for each tier in the dataset manifest.

### Stage 2 — extract (LLM)

Small items: one call over the full text. Heavy items: one call per chunk, then one
reduce call over the chunk outputs. In both cases the model returns, per point, an
`sid` plus a short claim — **never a retyped quotation**, and never `key_points` text
taken on trust. Item-level fields (title, what_it_is, why_it_matters, not_said) come
from the reduce call for heavy items and the single call for small ones.

Not yet built. Stage 1 must be reviewed first.

### Stage 3 — assemble (no LLM)

Substitute sentence text by `sid`, merge item fields, attach `_meta` from the item
(never from the model). Emit the existing summary schema so the site needs no change.

### Stage 4 — verify (no LLM)

Two independent checks, because they catch different failures:

1. **Quote invariant** — every referenced `sid` resolves, and its text appears in the
   full transcript. A failure is a bug in extraction or the dataset, never "model
   dishonesty".
2. **Coverage check** — selection size, zero-selection count, thin-selection count,
   and carried-speaker count. **This is not optional.** A previous bug emptied 44
   Bills and Budget items while the quote invariant read 100%, because selecting
   nothing passes a quote check trivially.

## Why approach C rather than extract-first-only

Extract-first-only makes the deterministic selector a **hard ceiling**: the model sees
only pre-selected sentences, so a heuristic mistake cannot be recovered, and the model
will write a confident brief about whatever it was handed. Four defects were found in
that selector in a single session — most damagingly an unanchored `(proc text)` match
that discarded whole ministerial speeches — which is evidence that hand-tuned heuristics
over 22M words do not converge quickly.

Approach C keeps the determinism where it is reliable (procedural filtering, figures,
Q→A mapping, and the substitution that guarantees verbatim quotes) and lets the model
do the salience judgement it is actually good at, over text it can actually see.

Evidence the LLM path was never the weak link: on the heavy 213k item the cloud model
scored **39/39 verified points** once given the text. The earlier failure was a
truncated prompt, not a model limitation.

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
- The three models on the local endpoint are ALL `-cloud` proxied, not actually
  local. An actual local model must be pulled before any local benchmark is
  meaningful.
- Extractive selection, for reference: 86,816,397 chars of transcript →
  9,318,893 selected (10.7%). Selection is verbatim against the complete transcript
  on all 3,912 items. Useful as a *cheap* path; not the chosen algorithm.

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

- Whole corpus: **86,816,397 chars of transcript → 9,318,893 chars selected
  (10.7%)**. A ~10× reduction before any model runs.
- The 213k-word Budget item: 939,453 chars → **3,542 chars (0.4%)**. It fits a
  small context window comfortably, so *chunking is not needed* for the heavy
  end once extraction is done first.
- Verified across **all 3,912 items: every selected sentence is verbatim against
  the COMPLETE transcript** (the harness asserts this and prints any failure).

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

1. Walk turns, skipping procedural business. **Read turn text through
   `turn_text()`**, which applies the same `strip_speaker_labels()` normalisation
   `build_chunks()` uses — see the mismatch note below.
2. Split into sentences; drop fragments (<8 words), opening courtesies (`thank`,
   `Mr Speaker`, `may I`, `with your permission`) and procedural/chair business.
3. Score each sentence deterministically — substance cues (will/from YYYY/
   effective/introduce/subsidy/grant/relief/cap), figures (`$7.4 million`),
   commitment verbs, earlier position, mid-length preference. No model, fully
   reproducible, explainable.
4. Round-robin one best sentence per turn first (preserves debate shape, stops
   one speech monopolising), then fill by score to a cap.
5. Emit `{speaker, sentence}` — verbatim by construction.

#### Four defects found and fixed during implementation review

**1. The selector and the gate disagreed about the source text.** `extractive.py`
originally read `sentences(t["text"])` — RAW turn text — while `build_chunks()` and
therefore `verify_quotes()` normalise with `strip_speaker_labels()` first. Hansard
turns carry bracket markers such as `[(proc text) Debate resumed. (proc text)]`
which survive raw selection but are stripped from the gate's reference. **Any
sentence built around such a marker could never verify, however faithful the model
was.** This is the actual cause of the 8 misses reported on the 213k item; the
earlier diagnosis blamed truncation alone and called the drops "correct behaviour".

Measured on the 213k item, against the complete (untruncated) transcript:

| | |
|---|---|
| raw turn text, capped transcript | 16/24 |
| raw turn text, complete transcript | 23/24 |
| `turn_text()`, complete transcript | **24/24** |

So truncation was part of it and the normalisation mismatch was the rest. Both are
fixed. `verbatim_check(item)` now exposes the invariant so the pipeline can assert
it, and the harness reports any item where it fails.

**2. `is_procedural` was load-bearing but nearly always unset.** Measured: 1 of 560
turns on a 2026 sitting carried the flag, so filtering on it removed almost nothing
and chair business reached the top picks — the design doc saw this and flagged two
examples by hand ("Does the Leader of the House have the general assent…", "Leader of
the Opposition, you wanted a clarification…"). `PROCEDURAL_RE` now screens those plus
`order read`, `debate resumed`, `question put`, bare `yes`/`sir` replies, and the
`(proc text)` markers. It is applied per sentence *and* per turn.

**3. Anchoring `(proc text)` unanchored destroyed whole speeches — caught by a
zero-selection audit, not by the verbatim check.** After fixing (2), 84 items selected
*zero* sentences, including 44 Bills and Budget items. Cause: Hansard appends parser
markers to the END of a turn, so a 10,046-char ministerial speech ends
`"... (proc text) Question put, and agreed to. (proc text)]"`. An unanchored pattern
matched inside that speech and discarded the **entire turn**.

This is the instructive one: the verbatim check stayed at 100% while this was broken,
because selecting *nothing* cannot fail a quote check. **A correctness invariant alone
does not catch under-selection — it needs a companion coverage check.** Both are now
in the harness.

Fix: `(proc text)` is procedural only when the turn STARTS with it (a turn that is
nothing but a marker). Consent/assent seeking stays unanchored because it genuinely
appears mid-sentence ("Mdm Speaker, may I seek your consent and the general assent of
Members present to now move a Business Motion…"), and it is specific enough not to
fire on ordinary debate. `motion (?:made|put)` was dropped — `question put` already
covers the chair's action and the looser form risks matching substantive debate.

Result: zero-selection items **84 → 38**, and the wrongly-emptied
`Customs (Amendment) Bill` now selects 24/24. The remaining 38 are genuinely
procedural (30 suspension motions, 7 ceremonial President's Addresses, 1 other).

**4. `max_words=60` silently discarded long substantive sentences.** A Minister
listing measures in one breath can exceed 60 words and would be dropped entirely.
Selection now passes `allow_long=True`; the length penalty in `score()` still
de-prioritises them without making them invisible.

Verified: on the 924w oral answer, **12/12 selected sentences are verbatim**.

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
- ~~`is_procedural` misses some noise~~ — **fixed**: `PROCEDURAL_RE` now screens the
  two hand-flagged cases and general chair business, applied per sentence and per
  turn. Worth re-reading a fresh sample to see whether new noise reaches the top
  picks.
- Decide whether Stage 2 runs per item or is skipped entirely for items whose
  deterministic fields are already sufficient (short oral answers may not need
  a rewrite at all).
- **The 60-char cap bug is not in the current code.** The handoff mentioned "the
  60-char cap bug", to be fixed through Stage 1 rather than a separate patch. No
  60-char truncation exists in `site/build_site.py` or `summariser/summarise.py`
  (grep for `[:60]`, `[0:60]`, `, 60)` finds nothing). The likely referent is
  `sentences(max_words=60)`, which **is** real and is now fixed by `allow_long=True`.
  If a different 60-char cap was meant, it needs to be identified before it can be
  fixed — do not assume this note covers it.

## Status of the superseded designs

- **Map-reduce chunking** (implemented in `summariser/summarise.py::build_chunks`,
  then superseded): keep the code but it is no longer needed for the heavy end once
  extraction runs first. The 213k item's SELECTED text is 3,097 chars, which fits a
  small window, so chunking a 939,453-char transcript is unnecessary work. Left in
  place because it is correct and harmless; remove it only after Stage 1–3 are live.
- **Truncation** (`build_transcript`): still used by `merge_transcript()` and the
  digest tooling. It must NOT be used to build an LLM prompt. See `build_chunks`'s
  docstring for the bug it caused.

## What a fresh session should do first

1. Read this file and `extractive.py`.
2. Run `python3 summariser/extractive.py` — it self-checks the Stage 4 invariant
   (selection must be verbatim against the COMPLETE transcript) and prints any item
   where it fails. A failure there is a bug in extraction, never a model problem.
3. Tune Stage 1 weights on a sample, then implement Stage 2 + Stage 3.
4. Stage 2 must NOT ask for `key_points`. Those are Stage 1's verbatim output.
