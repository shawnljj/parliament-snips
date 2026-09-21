# 1 · Objectives

Stage 1 of the SDLC. Every MUST in `REQUIREMENTS.md` §5 and §6 restated as an
objective **with a threshold a grader could score**.

**The rule for this file: no objective without a number.** "Summaries must be
faithful" is a wish. "0 briefs whose published sentence does not appear verbatim in
the record" is an objective, because a check can decide it.

Each entry names:
- **ID** — O-n, mapping to its requirement (R-x.y / N-x)
- **Threshold** — the number that decides pass
- **Measured on** — the artifact the number comes from
- **Grader** — `det` deterministic · `model` judge · `human` judgement
- **Now** — today's actual value, where known

Where a threshold cannot be stated, that is recorded as `OPEN`, not invented. An
invented threshold is worse than a missing one: it makes a real question look settled.

---

## 5.1 Archival integrity

| ID | Req | Objective | Threshold | Measured on | Grader | Now |
|---|---|---|---|---|---|---|
| O-1 | R-1.1 | A re-fetch of a sitting yields byte-identical content, or the difference is listed | 0 unexplained diffs across all 354 sittings | `data/<year>/sitting_*.json` | det | not measured |
| O-2 | R-1.2 | An interrupted fetch loses only in-flight sittings | 0 corrupted completed sittings after SIGTERM mid-run | `data/` + manifest | det | **observed OK** (19:34 SIGTERM, no corruption) |
| O-3 | R-1.3 | Coverage is reported per sitting from a declared enumeration | 100% of sittings carry a coverage ratio | `data/` `_meta` | det | not measured |
| O-4 | R-1.4 | No reader observes a partial artifact | 0 `.tmp` files present at rest | `data/`, `summaries/`, `site/dist/` | det | not measured |
| O-5 | R-1.5 | The archive is diffable: one sitting, one change | 1 file changed per single-sitting re-fetch | git diff | det | not measured |

## 5.2 Provenance and correctness

| ID | Req | Objective | Threshold | Measured on | Grader | Now |
|---|---|---|---|---|---|---|
| O-6 | R-2.1/2.2 | Every published sentence exists verbatim in the record at a recorded position | **0 mismatches, every year** | `summaries/<y>/` vs `pipeline/dataset/<y>/` | det | **2016 FAILS: 672** |
| O-7 | R-2.3 | A claim whose quotation cannot be verified is not published | 0 published briefs with an unresolvable sid | as above | det | 0 (all years except 2016) |
| O-8 | R-2.4 | Every claim carries an attribution, or is flagged inferred | ≥99% attributed, inferred count reported | `_meta` | det | 99.4–99.78% measured |
| O-9 | R-2.5 | No content is dropped without a recorded reason | **0 dataset items with content and no brief or withheld record** | `pipeline/dataset/` vs `summaries/` + `pipeline/withheld/` | det | **9 items** ✗ |
| O-10 | R-2.6 | A brief is derived from its complete item, not a truncated part | 0 briefs where `sentences_total` < the item's sentence count, unexplained | `_meta` | det | not measured |
| O-11 | R-2.7 | A reader can trace any published claim to the record | every section links to its source | `site/dist/` | det | not measured |

## 5.3 Summary quality

| ID | Req | Objective | Threshold | Measured on | Grader | Now |
|---|---|---|---|---|---|---|
| O-12 | R-3.1 | Tone is neutral and factual | 0 sections carrying a judgement adjective from the banned list | section summaries | model | not measured |
| O-13 | R-3.2 | The system never asserts whether a speaker answered adequately | 0 sections asserting adequacy, evasion or good faith | section summaries | model | not measured |
| O-14 | R-3.3 | All business types are covered | ≥1 brief for every group present in the dataset, per year | `_meta.group` | det | appears complete |
| O-15 | R-3.4 | Legible to a reader who has never read Hansard | OPEN — needs a stated measure (vocabulary? reader test?) | — | human | **OPEN** |
| O-16 | R-3.5 | A brief is shorter than its transcript by a wide margin | `sentences_selected` − `sentences_added_for_context` ≤ `max(1, round(sentences_total × 0.60))` | `_meta` | det | **0 violations / 3,863** ✅ |

> **O-16 was wrong in the first draft of this file, and the way it was wrong is
> instructive.** It read "`selection_share` ≤ 0.60" and was measured against the raw
> `selection_share` field, which reported **379 briefs over 0.6** and looked like a
> real defect. It was not: `selection_share` counts *selected + context-repaired*
> sentences, and the cap governs **selection only** — `sentences_added_for_context`
> is added afterwards, by design, so a section's argument is not stranded mid-sentence.
>
> Measured correctly (`selected − added ≤ cap`) there are **0 violations in 3,863
> briefs**. The threshold was right; the quantity was wrong. That is the exact failure
> mode this file exists to prevent, and it happened while writing the file.

## 5.4 Pipeline operation

| ID | Req | Objective | Threshold | Measured on | Grader | Now |
|---|---|---|---|---|---|---|
| O-17 | R-4.1 | Stages are separated and independently runnable | each stage runs alone and exits 0 on valid input | `summariser/` | det | believed true |
| O-18 | R-4.2 | Deterministic stages are deterministic | same input → byte-identical output, 2 runs | stage 1 output | det | not measured |
| O-19 | R-4.3 | Model use is confined to stages needing judgement, and visible | the model-using stages are enumerable and named | `summariser/` | human | documented |
| O-20 | R-4.4 | A stage is resumable | re-running a completed stage performs 0 model calls | `pipeline/usage.jsonl` | det | **observed OK** (skip-existing) |
| O-21 | R-4.5 | Processing state is inspectable without reading logs | 1 command reports queued/done/failed | `status.json` | det | partial |
| O-22 | R-4.6 | One bad item does not halt a corpus run | 0 runs aborted by a single item failure | run logs | det | held in practice |
| O-23 | R-4.7 | Cost is predictable before a run | a dry-run reports projected cost within 20% of actual | `tools/cost_report.py` | det | not measured |

## 5.5 Presentation

| ID | Req | Objective | Threshold | Measured on | Grader | Now |
|---|---|---|---|---|---|---|
| O-24 | R-5.1 | Works at a mobile viewport | 0 horizontal overflow at 360 and 390px | `site/dist/` | det | not measured |
| O-25 | R-5.2 | Every page states its own coverage and method | 100% of sitting pages carry both | `site/dist/sittings/` | det | believed true |
| O-26 | R-5.3 | The site is a static build | deploys with no server process | `site/dist/` | det | true |
| O-27 | R-5.5 | Missing briefs appear as missing, not as silent gaps | 0 items with content and no visible record | `site/dist/` | det | **9 items silent** ✗ |

## 5.6 Data model

| ID | Req | Objective | Threshold | Measured on | Grader | Now |
|---|---|---|---|---|---|---|
| O-28 | R-6.1 | Entities are normalized with stable ids | 0 entities duplicated as text where an id exists | schema | det | believed true |
| O-29 | R-6.2 | Derived artifacts reference source by id, never copied text | 0 briefs citing by quoted text instead of sid | `summaries/` | det | **true — select-by-id** |
| O-30 | R-6.4 | The schema is versioned and a change is detectable | 100% of briefs carry `_meta.schema` | `summaries/` | det | 100% (schema 4) |
| O-31 | R-6.5 | Invalid data fails loudly at the boundary | 0 invalid artifacts reaching `summaries/` | `summaries/` | det | **4 schema-3 briefs got through** ✗ |

## 6. Non-functional

| ID | Req | Objective | Threshold | Measured on | Grader | Now |
|---|---|---|---|---|---|---|
| O-32 | N-1 | Correctness over completeness — publish less | 0 wrong briefs published to satisfy a count | corpus | human | held |
| O-33 | N-2 | No fabricated content, ever | 0 non-verbatim published sentences | corpus | det | same as O-6 |
| O-34 | N-3 | Numbers the system reports are computed from data | 0 hand-written figures in docs | `docs/`, `*.md` | det | **FAILS: PLAN.md says 949 briefs; real is 3,863** ✗ |
| O-35 | N-4 | Polite to the source: rate-limited, cached, fetched once | 0 re-fetches of an already-fetched sitting | `data/`, logs | det | held |
| O-36 | N-5 | Runs need no owner attention | 0 runs blocked awaiting input | run logs | det | held |

---

## What this file exposes

**11 of 37 objectives cannot currently be scored.** They have no check anywhere in
`tools/`. That is the real backlog: not missing features, but unmeasured requirements.

**Four objectives are currently FAILING:**

- **O-6** — 2016: 672 defects (text shifted one sid)
- **O-9** — 9 dataset items with content, no brief and no withheld record
- **O-27** — the same 9 are invisible on the site
- **O-34** — `PLAN.md` reports 949 briefs; the corpus is 3,863

**Three are open questions, not objectives:**

- **O-15** — no stated measure for legibility. Needs the owner's definition.
- **O-16** — `selection_share` reaches 1.000 (a whole item selected). The ≤0.60 cap is
  `PARSNIPS_SELECT_MAX_SHARE`, so a value of 1.0 means the cap was not applied on some
  path. Either the threshold is wrong or the cap is not being enforced.
- **O-23** — no projected-cost comparison has ever been run.
