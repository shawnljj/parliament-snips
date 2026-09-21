## WHAT
Prove that every sentence in a sitting's Hansard record is present in our dataset.
Test 1 of the verification chain. Scoped to 2026 first.

## WHERE
- `tools/verify_sitting.py` (create) — one sitting, ordered levels
- `tools/verify_corpus.py` (create) — year sweep + tracker read/update
- `pipeline/verification.jsonl` (create) — the tracker
- reuses `scraper/parsnips_fetch.py::fetch_sitting`, `summariser/build_dataset.py::load_item`
- reads the per-sitting `source_hash` in `data/manifest.json`

## WHY
No check today compares our stored transcript against the source. Every existing gate
(`check_selection`, `check_year`, `check_artifacts`) verifies briefs against
`pipeline/dataset/` — our own derived artifact. If the dataset is incomplete or
misparsed, **every gate still passes.**

This is not hypothetical. A `&nbsp;`-prefix and `<strong>`-split parser bug silently
merged one speaker's words into the previous speaker's turn, and no gate caught it.

Objectives served: O-1 (re-fetch reproducibility), O-3 (coverage reported per sitting),
O-9 (no content dropped without a recorded reason), O-10 (complete derivation).

## DONE WHEN

One sitting:

    python3 tools/verify_sitting.py 2026-01-12
Expected shape — one line per level, then the verdict:
    TEST 1a REPORTS    PASS  (<stored> stored, <declared> declared by the source)
    TEST 1b SENTENCES  PASS  (<n> missing, <n> extra, <n> text mismatches)
    TEST 1 PASS

This sitting stores 136 reports; the declared figure is whatever the fresh fetch
returns and is not known until the checker runs. Do not treat 136 as the expected
source count — the point of the check is to find out.

The year:

    python3 tools/verify_corpus.py --year 2026
Expected final line:
    `2026: 23/23 sittings PASS — 0 sentences missing, 0 extra`

The tracker:

    python3 tools/verify_corpus.py --status
Expected: 23 rows for 2026, each `passed`, `failed` or `untested` — 0 in an unknown state.

The corpus figure this is measured against, recomputed not typed:

    python3 -c "import glob,sys;sys.path.insert(0,'summariser');import build_dataset as BD;print(sum(len(BD.load_item(p) or []) for p in glob.glob('pipeline/dataset/2026/*.json')))"
Expected: `65317` (2026 sentences, measured 2026-09-21)

## NOT DONE WHEN

**1. One missing sentence passes.** The threshold is the owner's and it is absolute:
**any single sentence present in Hansard but absent from our dataset is a FAIL.** Not a
rate, not a percentage, not a tolerable loss. The check must list the missing sids by
id and exit non-zero.

**2. Only counts are compared, not ids.** Two records can hold the same number of
sentences and different text. Compare the **sid sets in both directions** (missing and
extra) and compare the text of every sid present in both.

**3. Re-parsing the stored HTML is accepted instead of re-fetching.** That tests our
parser against our own cache and cannot detect a truncated fetch. The fetch must hit
the live portal.

**4. "Passed" is recorded without the source version.** A result must be invalidated
when the source changes. The 2016 one-sid shift is the precedent: 24 briefs were built
before a dataset rebuild and silently became wrong, and nothing noticed for hours. Key
the tracker on `source_hash` so a changed source flips that sitting back to `untested`.

**5. A sample is reported as a pass.** Every sitting in the year, or the result is
stated as partial.

**6. The check writes to `summaries/`.** Test 1 reads and compares only. Repair is a
separate decision — see the generate-once rule below.

## VERIFIED BY
`tools/verify_sitting.py` and `tools/verify_corpus.py` — **deterministic. No model.**
Test 1 is mechanical by design, so it can run on every generation at zero model cost.

## The generate-once rule this card must respect

Owner's decision, stated as standing policy: **a sitting's summaries are generated once,
on generation.** Not on refresh, not on rebuild, never re-generated once the initial
summary exists. Generate once, store, then retrieve for display.

Two consequences for this card:

- **Test 1 is idempotent and side-effect-free.** It never writes to `summaries/`. If it
  finds a missing sentence it reports a defect; it does not repair the brief, because
  repair would mean regeneration.
- **A verification result stays valid until the SOURCE changes** (`source_hash`) — not
  until a rebuild happens. A rebuild that does not change the source neither invalidates
  the result nor triggers re-summarisation.

## Scope

**Test 1 only. 2026 only.** Tests 2 and 3 — model judges for selection
representativeness and summary fidelity — are separate cards that depend on this passing,
because a selection cannot be judged against a record not yet proven complete.

2026 first because it is the clean case: its manifest `summarisation` field reads
correctly (`complete: true`, 23 of 23), so it validates the checker itself before the
checker is pointed at years with known uncertainty.

## OPEN
- [ ] Does the manifest's `source_hash` hash the **fetched sitting** or our **stored
      file**? The invalidation rule depends on it. If it hashes our own output it cannot
      detect a source change and must be replaced.
- [ ] Tracker shape: append-only `pipeline/verification.jsonl` (rows per run, history
      kept, needs a folding reader) or rewritten `pipeline/verification.json` (current
      state only)?
- [ ] 2026 has 291 dataset item files but `load_item` returns content for only 290, and
      the total is 65,317 sentences. Identify that one item and why, before treating the
      sentence total as authoritative.
