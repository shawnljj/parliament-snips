# UAT — PARSNIPS RAG answer stage

**Open on your phone:** http://100.93.66.68:8441/

## Before you start

The server must be running. If the page does not load:

```bash
cd ~/parsnips && /Users/shawnlin/.hermes/hermes-agent/venv/bin/python \
  rag/uat_server.py --port 8441 --warm
```

`--warm` preloads the 157 MB vector index — without it your first question takes ~20s, with it
1.8–4.2s. Ollama must be running (`nomic-embed-text` for ranking, the cloud model for answering).

## How to judge

Ask the questions **you** would actually ask. The eval set is mine; yours is the point of a UAT.

Every answer has buttons. Tap one:

- **Correct** — the answer is right and quoted from the right place
- **Partly right** — some of it is right, some missing
- **Wrong** — the answer is incorrect
- **Should have refused** — it answered when the record doesn't support it (the most dangerous failure)
- **Should have answered** — it refused when the answer exists
- **Not in the record** — you asked something Hansard legitimately doesn't cover

Verdicts save to `pipeline/uat_verdicts.jsonl` as you tap. None of them can be wrong — every tap
is data.

## When something looks off — check this BEFORE judging

Open **"What the system actually did"**. It lists every passage the model was shown.

| what you see | what it means | who fixes it |
|---|---|---|
| the fact isn't in **any** passage | **RETRIEVAL** — never reached the model | ranking / filters |
| the fact **is** in a passage, answer didn't use it | **GENERATION** — model had it and missed | prompt / context format |
| it refused, but the topic list contains the answer | **FALSE REFUSAL** — a real bug | threshold logic |

That distinction is the difference between two completely different fixes, and it's the single
most useful thing you can tell me.

## What is deliberately the way it is

Push back on any of these if you disagree — they were my calls, not measurements.

1. **Answers are verbatim quotes only.** No paraphrase, no summary. If an answer reads like a wall
   of quotation, that's the contract working. The alternative ("answer in your own words") makes
   hallucination undetectable.
2. **Refusal is a real answer**, not an empty result. It means the record doesn't contain it.
3. **Related topics after a refusal are chosen from the corpus, never written by the model**, and
   labelled as not-an-answer. 0 invented across every run.
4. **Attribution is derived, not generated.** Ask "who said it" and the speaker is read from the
   passage, not asked of the model. This fixed a class of failures — but it means the speaker
   shown is the *cited* passage's speaker, which is the right one for a quote and the wrong one if
   your question is really about a different passage.
5. **The correction pair.** Hansard corrects itself. When a passage is later corrected, both are
   shown and the correction is flagged. A1 is the test: it now quotes `$10.5 billion` (the
   corrected figure), not the retracted `$15.5 billion`.

## Things I already know are weak — you don't need to report these

- **Two-part questions** ("what was X in 2020 and Y in 2021?") mostly fail. Genuine multi-hop is
  the open gap; it needs a retrieval change, already scoped.
- **Bare abbreviations** like "JSS" are the known trap — it means both Jobs Support Scheme and
  Joint Singles Scheme. The system should separate them by context; verify it does.
- **Thin coverage**: the corpus is 2016–2026. A question about 2011 or 2027 is *correctly* refused,
  because no sitting happened then — but a question about what someone said *about* 2027 is
  answerable, because the text mentions it. That distinction is deliberate.
- Numbers are only as good as the record's own corrections. I found and fixed one (A1); if you hit
  another case where the corpus contradicts itself, that's genuinely valuable.

## What I want back

1. **The verdicts** (already saved — just tell me when you're done).
2. **The one or two answers that annoyed you most**, with the question text. Those are worth more
   than the whole eval set.
3. **Whether the refusal behaviour is right.** Over-refusing is safe but useless; under-refusing is
   dangerous. Where's your tolerance?

## Known-good answers to try first (if you want a baseline)

These pass the eval every time. If any of them fails, something regressed and I want to know:

- "How much of the Jobs Support Scheme payouts went to SMEs, and what share of local workers did that cover?" → `$10.5 billion`, 73%
- "Who spoke about the Progressive Wage Model for cleaners?" → a real speaker, with citation
- "What did Dr Koh Poh Koon say about the Joint Singles Scheme in 2016?" → 2,184 / 398
- "How many people were supported under the JSS?" → two million
- "What did Parliament decide about the 2027 GST rate?" → **refusal**, with related topics
- "What is the Jobs Support Scheme payout figure for 2026?" → **refusal** (scheme ended)
- "How many people attended the National Day Parade in 2020?" → **refusal** (not a parliamentary matter)
