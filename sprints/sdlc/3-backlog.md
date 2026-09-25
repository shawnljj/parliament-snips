# 3 · Backlog

Stage 2 input. Ranked work, derived from what is **actually failing or unknown** — not
from what would be nice to have.

Every item names an objective from `sdlc/1-objectives.md` so the link from work to
requirement is explicit. An item with no objective is a wish and does not belong here.

**Last audited:** 2026-09-21 by `python3 tools/check_artifacts.py`.

---

## How this was derived

Three sources, in order of authority:

1. **`tools/check_artifacts.py`** — what is stale, ungated or unaccounted. Ran with
   **7 problems** at time of writing.
2. **`sdlc/1-objectives.md`** — 36 objectives, of which **11 have no check anywhere**
   and **4 are currently failing**.
3. **`REQUIREMENTS.md` §7** acceptance criteria — A-1…A-7, none of which has an
   automated check.

---

## P0 — Blocking. The corpus is not presently shippable.

| # | Item | Objective | Evidence | Card |
|---|---|---|---|---|
| P0-1 | **Fix the 24 stale 2016 briefs** — text shifted exactly one sid | O-6 | `check_selection summaries/2016 2016` → `FAIL — 672 defect(s)` | `t_608872bf` |
| P0-2 | **Recompute `status.json`** — it reports 2017 as empty and skips its gate | O-21, O-34 | `check_artifacts.py`: `2017: reports 0 briefs, 343 on disk` | new |
| P0-3 | **Gate 2017** — 343 briefs shipped with no recorded verdict | O-6 | `check_artifacts.py`: `2017 has 343 briefs and NO recorded gate verdict` | new |
| P0-4 | **Record the 9 silently dropped items** — content with no brief and no reason | O-9, O-27 | 2018 `president-address-14` (37 sentences!), 2017 `oral-answer-1819`, 2026 `motion-2839`, 2019 `motion-1178+1202`, 2020 `motion-1511`, 2020 `motion-1366+1407`, 2023 `motion-2162`, 2025 `motion-2759`, 2026 `motion-2799` | new |
| P0-5 | **De-duplicate withheld vs published** — 7 items are recorded as both | O-9 | 2026: 6 items both published and withheld; 2023: `oral-answer-3339` | new |

**P0-4 and P0-5 are the same defect seen twice.** The accounting for 2026 only
reconciles if you count the overlap: `287 briefs + 7 withheld − 6 double-counted + 3
unaccounted = 291 items`. So `status.json`'s 294 is wrong twice over — once by
double-counting 6, once by omitting 3.

---

## P1 — The gates that are prose, not checks.

`REQUIREMENTS.md` D-1: *"a gate that has never been observed failing has not been
shown to work."* Every item here is a requirement with **no executable check**.

| # | Item | Objective | A-criterion | Card |
|---|---|---|---|---|
| P1-1 | **Build A-1/A-2/A-4 checks with negative cases** | O-6, O-9, O-8 | A-1, A-2, A-4 | `t_1703cf6c` |
| P1-2 | **Add a coverage check** — an item with no brief must be visible | O-9, O-14 | A-2 | part of P0-4 |
| P1-3 | **Wire the artifact freshness check into the sprint ritual** | O-34 | A-5 | new |
| P1-4 | **Regression corpus from real defects** | — | A-5 | `t_04d7134c` |

---

## P2 — Unmeasured objectives.

11 of 36 objectives cannot currently be scored. These are not features; they are
requirements nobody has ever verified. Listed most-risky first.

| # | Objective | Why it matters | Effort |
|---|---|---|---|
| P2-1 | O-4 — no partial artifacts observable | Atomicity is claimed throughout; never tested with a concurrent reader | M |
| P2-2 | O-10 — briefs derived from the complete item | A brief built from a truncated part looks perfect | M |
| P2-3 | O-1 — re-fetch reproducibility | The foundation of the archive claim | M |
| P2-4 | O-3 — coverage reported per sitting | R-1.3 says the API under-reports; nobody measures our own ratio | M |
| P2-5 | O-18 — deterministic stages are deterministic | Two runs should be byte-identical; never compared | S |
| P2-6 | O-23 — cost predictable before a run | `cost_report.py` exists but has never been run against a projection | M |
| P2-7 | O-12, O-13 — tone neutral; no adequacy claims | Needs a model judge; the only objectives requiring one | L |
| P2-8 | O-11 — reader can trace a claim to the record | Believed done; never checked on built HTML | S |
| P2-9 | O-24 — no overflow at 360/390px | Standing requirement; `check_rail.py` exists but only covers the spike page | S |
| P2-10 | O-25 — every page states coverage and method | Believed true; never verified across 332 pages | S |
| P2-11 | O-2 — interrupted fetch loses nothing | **Observed OK** (SIGTERM at 19:34, no corruption) — needs a repeatable test, not an anecdote | M |

---

## P3 — Open questions. These block work rather than describe it.

| # | Question | Blocks | Owner |
|---|---|---|---|
| Q-1 | **O-15** — what number defines "legible to a reader who has never read Hansard"? | any legibility objective | **owner** |
| Q-2 | **The 9 dropped items** — publish or withhold, and by what threshold? Items with 1–9 sentences are published elsewhere in the same corpus, so the skip is not a clean floor. | P0-4 | **owner** |
| Q-3 | **`oral-answer-3339`** (2023) is both published and withheld. Which is correct? | P0-5 | owner |
| Q-4 | **Should `status.json` be auto-recomputed by `promote_briefs.py`?** It already is by `build_briefs.py`, which is why promotion is the one step that can desync it. | P0-2 | owner |

---

## P4 — Documentation drift.

| # | Item | Objective | Evidence |
|---|---|---|---|
| P4-1 | Fix `PLAN.md` — says 949 briefs, "2023 in progress", 2016–2022 pending | O-34 (N-3) | `check_artifacts.py` DRIFT |
| P4-2 | Fix `README.md` — status table says 2017–2025 pending | O-34 | `check_artifacts.py` DRIFT |
| P4-3 | Fix `sdlc/1-objectives.md` — states 379 briefs | O-34 | self-inflicted; will clear when O-16's note is reworded |

Note: **P4 is not cosmetic.** `PLAN.md` understates the corpus by 4× and reports a
finished phase as in progress. Anyone reading it — including me, next sprint — plans
against a false picture. It is ranked below the gates because it cannot corrupt
artifacts, only decisions.

---

## Deliberately excluded

Stated so the omissions are decisions, not oversights:

- **Bot fleet / dispatcher** — settled: not needed at this workload.
- **GitHub Pages docs site** — agreed, but it is a *delivery* task, not correctness.
  Belongs in a sprint once the corpus is shippable.
- **`site/dist` untracking / history rewrite** — real, but a repo-hygiene job with no
  bearing on correctness. Do it once, deliberately, outside a sprint.
- **2015** — out of scope by decision (D-10).
- **New features** (topic threads, MP pages, RSS, OG images — PLAN.md phase 9) — the
  corpus is not shippable. Shipping more surface on top of a failing gate is exactly
  how the wrong thing gets built correctly.

---

## Proposed Sprint 0001

Smallest coherent unit that traverses all six stages and leaves the corpus shippable.

**Objective:** O-6 (0 defects, every year) + O-9 (0 items with content and no record).

**Why these two:** they are the only failures that make the *product* wrong rather
than its *reporting*. O-34 fixes the report; P0-4/P0-5 fix the accounting. But a brief
whose text is shifted by one sid is a wrong citation, and R-2.3 says a wrong citation
is worse than a missing point.

**In scope:** P0-1, P0-2, P0-3, P0-4, P0-5.
**Out of scope:** everything in P1–P4.

**Definition of Done:**
- `check_selection.py summaries/<year> <year>` → `PASS — 0 defect(s)` for all 11 years
- `check_artifacts.py` → exit 0, 0 problems
- every dataset item with content is either published or carries a withheld reason
- a regression case exists for the one-sid shift (`t_680e3bde`)

**Estimated:** 2016 regeneration is 24 items (~40 min of model time by prior runs);
P0-5 is a data-hygiene pass, minutes. P0-2/P0-3 are free once promotion refreshes status.
