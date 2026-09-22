# The completeness check — what it does, what it found, and what it does not prove

Card: `t_13589edf`. Implements Test 1 of `docs/card-verification-chain.md`, which until
now was a spec with no code: `tools/verify_sitting.py` did not exist.

    python3 tools/verify_sitting.py --selftest        # the checker vs planted defects
    python3 tools/verify_sitting.py 2026-01-12        # one sitting
    python3 tools/verify_sitting.py --sanity          # bookkeeping only, no network
    python3 tools/test_deterministic.py --year 2026   # the suite, as a run
    python3 tools/test_deterministic.py               # whole archive

## What is compared

Every sitting's stored data, against the sitting **re-fetched from
`sprs.parl.gov.sg` during the test run.** No cache, no reuse of our copy. Three levels:

| level | what it answers | catches |
|---|---|---|
| 1a REPORTS | did we collect every report the source has for this day? | a truncated or under-swept fetch |
| 1b SENTENCES | is every stored turn present in the fresh record, same speaker, same text? | the parser defect that merged one speaker's words into another's |
| 2 EXTRACTION | does the dataset carry every sentence the sitting yields, same ids? | a stale dataset — the failure that corrupted 2016 |

Level 2 needs `pipeline/dataset/`, which is **gitignored** (270 MB, regenerated in ~70s).
In a fresh worktree it is absent, so level 2 is reported **SKIP with a reason** and the
run is reported PARTIAL. It is never a silent pass. Point `PARSNIPS_DATASET` at the
primary tree's copy to run it.

## Why a fresh pull is not `fetch_sitting(day)`

Measured 2026-09-21, three live pulls of 2026-08-05: **169, then 159, then 165 reports**
against a declared `maxResult` of 167 — the load-balancing documented in
`scraper/parsnips_fetch.py` (gotcha 2). The four `written-answer-na-*` ids in the first
pull were absent from our stored file; the ten `written-answer-2411x` ids in our stored
file were absent from the second.

So a checker that compared `fetch_sitting(day)` against the stored file would **fail on a
correct archive**, and its failures would be indistinguishable from real ones.

The reference set is therefore:

    the fresh enumeration             (what the source says exists today)
  ∪ every report id we already know  (each fetched by id, which IS stable)

`fetch_report(rid)` returns an empty payload for an unknown id rather than erroring, so
"the source cannot produce this" is detectable and is reported separately from "the
source has this and we do not".

## Sentence ids are per ITEM, not global

`build_dataset.sid(n)` numbers sentences within one `build_item` call, so **`s00001`
exists in every item**. Keying a sitting's sentences by sid in one flat dict silently
collapses items onto each other — the first version of this check did exactly that and
reported **2,667 sentences where the dataset holds 1,311** for the same reports, plus
1,180 "text mismatches" that were really `s00001` of one item against `s00001` of another.

Level 2 is therefore item-by-item, keyed by `storage.stable_key(report_ids)` — the same key
the dataset files use. And the **grouping comes from `pipeline/dataset/index.json`**, not
from a per-report assumption: the dataset builder merges reports into items by title and
day-cluster, so `bill-773`, `bill-774` and `bill-775` are one item and grouping them
separately invents items the dataset never had. With the correct grouping the same sitting
reports **9 items, 2,667 sentences, 0 findings**.

An item can span sitting days, and its sids are numbered over the whole item, so the item
is always rebuilt **in full** (its other day's reports are loaded from the archive) and
only then filtered to this sitting's reports. Filtering first would renumber the ids and
manufacture findings.

## The derived-sentence equivalence, proven corpus-wide

Level 1 needs no dataset on disk, because the sitting's sentences can be re-derived with
the dataset builder's own code. That claim is checked, not asserted:

    python3 tools/verify_sitting.py --sentences-total
    -> derived sentences, whole archive: 764,302

    2026 only:  derived 65,317   dataset index 65,317
    all years:  derived 764,302  dataset index 764,302

Per-year differences are fully explained by multi-day items, where the whole item is
attributed to its earlier year: 2016 −4 / 2017 +4, and 2024 −19 / 2025 +19. The totals
agree exactly.

## The second check is a different model, and it cannot overrule the first

The deterministic levels are mechanical: ids, set membership, text equality.
`tools/test_deterministic.py` also asks an **independent verifier model**
(`gpt-oss:20b-cloud` by default — not `deepseek-v4.1-flash:cloud`, which writes the
corpus) to review the evidence.

What it is asked is the one thing determinism cannot supply: **does this evidence read as
clean because it looked at nothing?** It is not shown the transcripts, is forbidden from
claiming to have verified content, and a verdict that does so is recorded as a defect in
the *verifier*.

It is **not** allowed to fail the run on a disagreement. `docs/3-lessons-learned.md` §6
records a fabricated `VERDICT: PASS` from giving a model discretionary control over
deterministic evidence; letting a model's arithmetic objection fail a mechanical check is
the same error with the sign flipped. So reviewer disagreement is its own verdict state
and its own exit code:

    0  selftest caught everything, every sitting passed, the secondary check ran
    1  a deterministic failure, a failed selftest, or a verifier that did not run
    2  deterministic PASS, but the reviewer disagreed or a defect was found in its verdict

An unreachable verifier is `not_run` and exits 1 — an unverified state is not a verified
one (R-2.8, fail closed). `--no-verifier` opts out explicitly and records
`skipped_by_flag`.

## The selftest is the point

A checker that reports PASS while doing nothing is indistinguishable from one that works.
`--selftest` plants seventeen defects into a real sitting held in memory and asserts every one
is caught, **including that an unmodified copy passes**. It runs first in the suite and a
failed selftest fails the run.

Two of those cases were wrong on the first attempt and are kept as a record of how:

- The "one word altered" case reported `MISSED` because **the mutation never landed** —
  Hansard text is normalised on the way in and the token being replaced was not present.
  A mutation that silently does nothing looks exactly like a checker that catches
  nothing. The case now asserts the mutation landed before asserting the checker noticed.
- The report-level cases planted the defect on the **wrong side of the comparison**: an
  id removed from `stored` is `never_collected`, not `gone`, so a case passed while
  reporting a category it did not create.

Two more came out of the first full 2026 sweep, both the same shape — a rule that was too
strict and a rule that was not strict enough:

- The **placeholder** case asserted the level *fails* on a 0-word row. On real data that
  rule failed 22 of 23 correct sittings. The case now asserts the row is flagged
  (`sanity_ok=False`) **and** that the level still passes, which is the distinction the
  check exists to draw.
- The **aside** case is new, taken from `written-answer-22754`. It asserts a bracket aside is
  recorded and not failed, and — because a rule that ignores differences eventually ignores
  lost text — a second case trims 25 characters off that same turn and asserts the checker
  still **fails** on it.

## What it found

**A completeness gap in 2026: 62 reports the source publishes and the archive does not hold
at all**, spread over 14 sittings:

    sitting      missing      sitting      missing
    2026-01-12       4        2026-03-02       1
    2026-01-13       5        2026-03-04      10
    2026-01-14       1        2026-05-05       8
    2026-02-04      12        2026-05-06       1
    2026-02-26       2        2026-05-07      11
    2026-02-27       1        2026-07-07       1
    2026-08-04       1        2026-08-05       4

Every one is a written answer — 20 `written-answer-*` and 42 `written-answer-na-*` — so the
gap is not random: a whole class of report has been under-collected. Each is checked against
the alternative explanations before being reported:

- **Not a date-boundary artifact.** `search_body` filters a UTC range, so a leak would pull
  the neighbouring day's reports; but each id reports the *same* `sittingDate` as the sitting
  it is missing from, and appears in that day's fresh enumeration only.
- **Not held under another id or date.** No missing id appears anywhere in the archive under
  any sitting date, and none of their titles is already held under a different report id.
- **Not an enumeration fluke.** All 62 appear in **more than one independent run** (three
  full sweeps, hours apart), so none is a one-off.

### 62 is a lower bound, and a single run will understate it

An id is only *detectable* as never-collected if that run's fresh enumeration happens to
return it — and the enumeration is load-balanced, so it does not always. Measured directly:
three full 2026 sweeps gave 61, then 55, then 62 as the union, and every one of the 62 turned
up in at least two of them. The count moves in that direction **only** (an id cannot
disappear from the archive between runs), so:

- a single run's total is a **lower bound**, not the gap;
- the number to quote is the **union across runs** — 62 here;
- a sitting that PASSES on 1a has passed *for the ids that run happened to see*, and is
  therefore re-tested on the next run rather than permanently cleared.

This is the one place where "the sitting passed" is weaker than it sounds, and it is why the
check is *repeated* rather than run once.

A second, smaller finding: **47 rows the archive holds with no content and the source will
not serve** — every one an `attendance`, `ptba` or `atbp` procedural entry (22/21/4), present
in 22 of the 23 sittings. `backfill.fetch_one` appends a row for every id the enumeration
returns even when the content fetch comes back empty (counted in `empty_reports`), so the
archive's own report counts include rows the source has no text for. That is bookkeeping, not
lost text, and it is **flagged, not failed** (`sanity_ok`) — see the rule below.
`verify_sitting.py --sanity` lists them without touching the network.

Neither is repaired here. Repair is a separate decision (card scope: no backfill, no
ingestion change).

### A third finding: rows we hold empty that the source has text for

`motion-1901` (2022-03-10) is stored with **0 words and 0 turns** — so the first version of
this check classified it as an empty placeholder like any `attendance-*` row. It is not: the
source serves a 36-word procedural entry,

    <h6>[(proc text) Resolved, "That the proceedings on the business set down on the Order
    Paper for today be exempted at today's Sitting from the provisions of Standing Order
    No 2." – [Ms Indranee Rajah] (proc text)]</h6>

and `parse_report_turns` yields **0 turns** from it, because the text is inside an `<h6>`
that the turn parser does not treat as a turn. Both halves matter:

- **The archive holds an empty row for text the source publishes** — real lost content, and
  no level reported it: 1a saw the id as *obtained* (the fetch succeeded, it just returned
  nothing usable), and 1b had no stored turn to compare.
- **The discriminator had been our own word count**, which is the wrong side of the
  comparison. Keyed on what we hold, a row is "empty" whether the source has nothing or has
  something we dropped.

The rule now asks the **source**, for every 0-turn row we hold, and classifies it as
`unparsed_source_content` → **FAIL**. Checked live: 2022-03-10 fails on exactly this id, and
`--selftest` carries both directions — the same row must fail when the source has text and
must pass when it genuinely has none (otherwise every `attendance`/`ptba` row would fail).

`motion` is the only id class affected: sampled across the archive, the other 511 empty rows
(`attendance` 221, `ptba` 214, `atbp` 76) all return **no content at all** from the source.
This is one row in the whole archive, and it is the only gap the check has found that was
invisible to every other gate.

### The whole-2026 result

    23 sittings:  10 PASS, 13 FAIL   (every failure is 1a never_collected)
    1b SENTENCES  PASS on 23/23      (turns checked, no missing/altered/wrong-speaker turn)
    reconciliation balances          23/23
    'gone' categories partition      23/23
    missing_with_content             0    (nothing we hold has been withdrawn by the source)
    not_obtained                     0
    unparsed_source_content          0 in 2026 (the one instance is motion-1901, 2022)

13 sittings failing is the finding, not a failure of the check: the 1a level is doing exactly
what the card asked it to do, and the gap it reports is real and unrepaired (out of scope).

### A check that fails 22 of 23 correct sittings is a broken check

The first full 2026 sweep with the 0-word placeholders counted as failures reported **22 of
23 sittings FAIL** — not because 22 sittings were broken, but because the rule could not
tell *"we lost text"* from *"the source has no text here"*. `attendance-*`/`ptba-*`/`atbp-*`
rows are returned by the enumeration and never carry content, so every sitting with a
procedural row failed. The rule now separates them:

| category | meaning | verdict |
|---|---|---|
| `never_collected` | source has content, we hold nothing | **FAIL** |
| `missing_with_content` | we hold words, source serves nothing | **FAIL** |
| `not_obtained` | we hold it, fresh pull never obtained it | **FAIL** |
| `unparsed_source_content` | we hold the row empty, but the source serves text for it | **FAIL** |
| `missing_empty_placeholder` | we hold a 0-word row, and the source really has nothing | `sanity_ok = False` |
| `aside_differences` | differs only by a bracket aside the pipeline strips | recorded, not failed |

The `unparsed_source_content` row was added last, and it is the only category that required
asking the source a *second* question per row (`motion-1901`, below). Both of the last two
were found by running the check on real data, not by reasoning about it.

### Three more defects the check found in itself

Each was a rule that looked right and was not, and each is now a selftest case so it cannot
come back:

- **The level-2 comparison was keyed by sid across items.** `build_dataset.sid(n)` numbers
  sentences *within an item*, so `s00001` exists in all eleven items of a sitting. Comparing
  one flat dict of sids compared `s00001` of one item against `s00001` of another: it
  reported 2,667 derived sentences against a dataset holding 1,311, plus 1,180 "text
  mismatches" that were pure key collision. Level 2 now rebuilds and compares **item by
  item**, using the index's own report grouping.
- **The partition check asserted nothing.** Its first version summed three category lengths
  and iterated an empty sequence, so it returned `True` unconditionally while looking like a
  check. It now tests real sets for pairwise disjointness and union, and the selftest plants
  an overlapping id and an uncovered id to prove it can fail.
- **An unreadable verifier verdict counted as a pass.** The reviewer hit `max_tokens` exactly
  and its JSON was truncated, and the suite reported `secondary PASS`. An unparseable verdict
  is now its own state (`unparseable` → not run → exit 1), the token cap is raised, and
  `finish_reason=length` raises instead of returning half an answer.
- **The verifier's disagreement was reported as a contradiction, and it was right.** Asked
  whether any level was marked PASS while carrying findings, it flagged 2026-04-07 — where
  `1a_reports` is PASS and does carry `missing_empty_placeholder` entries. That is the
  intended design, but the prompt had not said so, so the reviewer was inferring a rule that
  contradicted it. Fixed on both sides: the prompt states the exception, and the
  reconciliation string now names the three-way split of `gone`.

## What this does NOT prove

- **Level 1a is sensitive to the source's own enumeration.** A report that neither the
  fresh enumeration nor our stored copy knows about is invisible to this check. It compares
  what we hold against what the source will serve *now* — it cannot prove the source
  itself is complete.
- **Level 1b is turn granularity, not sentence granularity.** A sentence lost *inside* a
  turn is invisible; a turn lost or altered is not. Level 2 covers sentence-level loss for
  the dataset, where the sentence ids exist.
- **Level 1b compares on `strip_speaker_labels`'d text.** A change the source makes that the
  pipeline's own stripper also removes reads as an `aside_difference` and does not fail. That
  is deliberate — the dataset is unaffected by it — but it means level 1b is a check on the
  text *the pipeline would publish*, not on byte equality with the source.
- **Level 2 trusts `pipeline/dataset/index.json` for the item grouping.** Where the index
  and the dataset builder's own algorithm disagree, this check inherits the index's answer
  and does not adjudicate it. Without an index it falls back to
  `summarise.summarisable_items` and records which source it used
  (`grouping_source` in the result).
- **Level 2 is not run in a bare worktree.** `pipeline/dataset/` is gitignored; a PARTIAL
  result is not a whole-archive result and the suite says so in its verdict block. Point
  `PARSNIPS_DATASET` at another tree's copy to run it.
- **`extra_sids` is reported, not failed.** An item spanning two sitting days legitimately
  holds sids this day does not own, so extras alone are not a defect; `spanning_items`
  names the items where that can happen.
- **A PASS on 1a is a lower bound, not a clearance.** See above: the enumeration is
  load-balanced, so an id that the source serves but never listed in this run is invisible.
  A sitting that passes is re-tested next run; it is never "cleared forever".
- **A green run is not a proof of the source.** It is a proof that our copy and a fresh
  pull agree at the level checked.

## Verdict states and exit codes

    0  selftest 15/15, every sitting passed, the secondary check ran (or was explicitly skipped)
    1  a deterministic failure, a failed selftest, a secondary check that did not run or
       could not be read, or a verifier that is the same model as the implementor
    2  deterministic PASS, but the reviewer contradicted the evidence or was found to have
       claimed verification of content it was never shown

Disagreement is **not** a veto. A probabilistic layer with discretionary control over
deterministic evidence once produced a fabricated `VERDICT: PASS`
(`docs/3-lessons-learned.md` §6); letting a model's objection fail a mechanical check is the
same error with the sign flipped. So the reviewer's disagreement is recorded as its own state
and its own exit code, and the deterministic result stands.

Independence is **checked, not assumed**: `--verifier` is compared against the implementor
model (`PARSNIPS_MODEL`, default `deepseek-v4.1-flash:cloud`) and a match fails the run —
a model cannot be its own secondary check.
