# Model choice for Stage 2 extraction

**Decision: `llama3.2:3b` locally, `deepseek-v4.1-flash:cloud` for the cloud path.**

Measured, not assumed. The comparison below is the reason this file exists: three
candidates were eliminated by numbers, and two of them looked reasonable on paper.

## The task being measured

Stage 2 extraction is mechanical. The model receives numbered sentences and returns,
per point, a short claim in its own words plus the ids of the sentences that support
it. It never writes a quotation. This is not a reasoning task — there is nothing to
deduce — which turned out to be the single most important fact in this comparison.

## Measured on the same items (2026-08-05, four oral answers)

| model | probe: out tokens | sec/item | tok/item | points (4165/4167) | coverage | bad cites |
|---|---|---|---|---|---|---|
| **llama3.2:3b** (2.0 GB) | **15** | **5.5–29** | ~1,100 | 15 / 13 | **0.62 / 0.83** | 0 |
| qwen2.5:7b-instruct (4.7 GB) | 15 | 17–57 | ~520 | 13 / 6 | 0.38 / 0.50 | 0 |
| qwen3:4b (2.5 GB) | **547** | **143** | ~6,800 | 4 | 0.25 | 0 |

All three passed the JSON probe and returned zero unresolvable citations, so the
elimination is on cost and yield rather than on validity.

## Why each rejected model was rejected

**qwen3:4b / qwen3:8b — reasoning models, wrong tool.** The probe is where this
showed: a one-line JSON reply cost **547 output tokens** against 15 for llama3.2, and
a real item cost **6,802 tokens for five short points**. Every token above the answer
is deliberation, and on a task with nothing to reason about it is pure overhead. It
also made the wall-clock untenable: **143s/item** against 5.5s, which across 3,912
items is 155 hours versus 6.

Disabling `think` did not help — measured 7,932 tokens with it off against 7,100 with
it on, i.e. slightly worse. The deliberation is not switchable on this model.

**qwen2.5:7b-instruct — right kind of model, worse on both axes.** It is an instruct
model, so the probe is clean (15 tokens, same as llama3.2). But on the real task it
was **~2× slower** (57s vs 29s on the 3,363-word answer) and produced **fewer points
at lower coverage** (13 vs 15 points; coverage 0.38 vs 0.62 on the same item, and 6
vs 13 points on the next one). It is also 2.4× the disk.

Its output is genuinely good — arguably tighter prose than llama3.2's — so this is
not a quality rejection in the absolute sense. It is that at corpus scale the extra
size buys a *worse* yield per second, and coverage is the product (§1.2: summaries of
every exchange, not highlights).

## What was learned about model selection here

1. **Probe with a trivial task.** "Return this JSON" separates models that cannot
   follow the format from models that can, and costs seconds. It also immediately
   exposes a reasoning model: the token count is absurd relative to the answer.
2. **Measure output tokens per item, not just quality.** The reasoning model was not
   worse in *content* — its points were fine. It was worse in cost, by 6×.
3. **Coverage is a better quality signal than prose quality** for this product,
   because reading a fluent summary of the wrong subset is the failure mode the whole
   architecture exists to prevent.
4. **A smaller prompt makes a small model usable.** See
   `build_briefs.py:PROMPT_CHAR_BUDGET` — at 24k chars llama3.2 emitted 84-citation
   points and unparseable JSON on ~20% of chunks; at 6k chars it is clean. Model
   choice and prompt size are not independent decisions.

## Cost

Local inference is $0 at the margin — the machine is already on. Per item it consumes
roughly **10,950 in / 1,475 out tokens**, which at `deepseek-v4.1-flash:cloud` rates
($0.14 / $0.28 per M) is about **$0.002 per item**. So the whole 3,912-item archive is
roughly **$8 on cloud** against **~20 hours of local wall-clock**.

That is the real trade: cloud for a few dollars, or local for free and a night. The
local path is the one that has been verified end to end.
