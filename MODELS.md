# Model choice for Stage 2 extraction

**Decision: `deepseek-v4.1-flash:cloud`. The local model was tried, measured, and
rejected.**

Measured, not assumed. The comparison below is the reason this file exists: four
candidates were eliminated by numbers, and the local one was eliminated by the last
measurement rather than the first.

## Why local was abandoned, having worked

`llama3.2:3b` produced a whole sitting end to end -- 18/18 items, zero errors, $0.00 --
and was the default for a day. Two measurements then disqualified it, and neither was
about money:

**1. It is not reproducible.** The same item, same prompt, repeated gave point counts
of **[3, 1, 3, 8, 5]** on `oral-answer-4178` and 1 point on five consecutive runs of
`oral-answer-4179` while the cloud model found 6-7 every time. So a published "1 point"
said nothing about the item and everything about which sample came out. A corpus cannot
be reproducible when its summariser is not, and re-running to fix an item would produce
a different brief rather than a confirmed one -- which makes the whole verification
story weaker, since the gate validates an output that will not recur.

Setting temperature to 0.0 fixed the *variance* (3,3,3,3) but not the *under-selection*
(1,1,1,1 on 4179 against cloud's 6-7). Determinism alone did not make it right; it made
it consistently wrong.

**2. It under-extracts badly on small items.** On identical text:

| item | llama3.2:3b | deepseek-v4.1-flash:cloud |
|---|---|---|
| oral-answer-4178 | 3 | **8** |
| oral-answer-4179 | 1 | **7** |
| oral-answer-4168 | 5 | **8** |
| (and repeat runs) | 1-8 | 6-8, spread 0-1 |

That is the failure this project keeps meeting in new clothes: a brief with too few
points is short, truthful, correctly quoted, schema-complete and passes every gate.
Nothing flags it. The user's brief on noticing it was to stop optimising for cost --
"if the cloud model is consistent then forget about using local and just use cloud" --
and the measurements agree.

## Cloud, measured

Consistent across repeat runs: 8/7/7/7, 7/6/7/6, 8/8/8/8 -- spread 0-1 against local's
1-8. Cost per item and the projection for the whole archive are in the table below.

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

## Cost, re-measured with the decision

Cloud is the chosen path, so its cost is the one that matters. The local comparison is
retained only as the reason it was rejected.

Rates: `deepseek-v4.1-flash:cloud` at $0.14 / $0.28 per million tokens (in/out).
Measured cost per item, and the projection for 2026 and for the full archive, are
produced by `tools/cost_report.py` from `pipeline/usage.jsonl` rather than estimated
here.

The decisive point is not the dollar figure -- it is a few dollars either way, which is
why cost was the right thing to stop optimising on. It is that a free summariser which
under-reports a 160-word ministerial answer as one point is not a cheaper version of
the product. It is a different, worse product that costs nothing.
