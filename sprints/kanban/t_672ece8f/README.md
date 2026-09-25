# Legislation ingestion — card t_672ece8f

`ingest/legislation.py` + `ingest/test_legislation.py` + `ingest/fixtures/legislation_fixture.json`.
Spec: `docs/rag_spec.md` §1.1, §1.2, §1.4, §2, §3, §8. Schema: `docs/schema.json` (both
vendored into this worktree from t_67d1e87a, because the card makes them binding inputs and
this worktree did not have them).

## Measured output (full corpus)

```
roster         : cache (107 current + 462 former; 5 duplicate name entries dropped,
                 0 normalized collision)
sittings       : 331
documents      : 1795  {'bill': 1002, 'motion': 793}
members        : 569 (198 carry >1 observed alias)
divisions raw  : 18 declaration matches (17 in this half, 1 in ingest/debates.py's half),
                 0 unparsed
divisions kept : 11 distinct (doc_id, ayes, noes, abstentions) rows in 10 documents
sponsors       : 1059 linked to a member_id, 21 unresolved (speaker_raw kept),
                 715 records name none
```

Runtime ~2 min for the full corpus, 42 s for `--limit 500`. No network except `/fetchData`,
which is cached at `data/raw/legislation/roster.json`.

## Acceptance criteria

| Criterion | Result |
|---|---|
| `python ingest/legislation.py --limit 500` produces >=500 schema-valid rows | **PASS** — 500 report rows + 569 member rows, 0 invalid |
| Referential integrity: every non-null sponsor `member_id` exists in the member set | **PASS** — 194 distinct ids used, 0 unresolved |
| Unit tests cover the vote-breakdown mapping | **PASS** — 47 tests, `python3 -m unittest ingest.test_legislation` |

The card's third criterion was rewritten by dev2's comment (spec §8): "every non-null sponsor
member_id exists in the roster", not "every sponsor has a member_id" — the latter is
impossible, since the roster serves no ids.

## Independent audit

A separate script re-derives every claim from the written files, deliberately not sharing code
with the ingester's own `--check` (a checker that shares code with the thing it checks cannot
catch that code's bugs). All ten checks pass:

1. schema — 0 invalid documents, 0 invalid members
2. referential integrity — 0 of 194 used ids missing from the roster; 569 rows, 569 unique ids, 0 duplicates
3. `per_member` — 0 documents with a non-null value
4. partition — 0 `doc_id` overlap with `ingest/debates.py`'s output; 0 duplicates within the file
5. divisions — 11 distinct rows, 0 missing, 0 extra, versus a re-derivation from the raw corpus
6. sponsors — 1059 resolved / 21 unresolved
7. dates — 0 non-ISO dates
8. `doc_type` — 0 inconsistent with `section`
9. URLs — 0 malformed
10. aliases — 0 aliases that fail to re-resolve to their own member

## Four defects this work found and fixed

1. **`split_mp_names` never split anything.** Treating `[` as a paren depth increase made a
   list-opening record one fragment, and the source writes an unclosed `[` on 108 fragments
   ('[Ms Anthea Ong', '[The Chairman'). Square brackets are now stripped, not tracked.
2. **Duplicate member rows.** The roster serves 574 name entries for 569 distinct ids
   (4 names listed twice, plus the `R Ravindran` / `R. Ravindran` slug collision). Rows were
   appended to the bucket before the dedup check, so `members.jsonl` had 5 duplicates.
3. **`roster_normalized_collisions` always read 0** — the collision set was keyed on
   `member_id`, making it a self-reference. The report now shows `5 duplicate name entries
   dropped, 0 normalized collision`, which is the truth.
4. **A real MP was unreachable.** `NON_PERSON_RE = ^(hon|honourable)` ran before the roster
   match, so the roster's own **Hon Sui Sen** resolved to `non_person` — his own full name
   could not find him. Caught by audit check 10 (every alias must re-resolve to its own
   member). Narrowed to `^hon\s+members?\b`; measured, both rules catch the identical 3
   strings and 64 turns, so nothing was lost.

## Scope decisions, stated rather than assumed

- **`metadata.division.per_member` is always `null`** and is asserted null by a test. The
  source publishes no per-member votes (spec §1.4).
- **`members.jsonl` collides in name with `ingest/debates.py`'s output.** Verified mine is a
  strict superset — all 569 ids, 1636 aliases vs their 268, no field populated by them left
  null by me — so the index builder should use mine. Flagged rather than silently
  overwritten; see the note in this card's completion metadata.
- **`--outside-divisions` is OFF by default.** The 18th declaration is `budget-855`, which is
  in `ingest/debates.py`'s partition and already written by it. Emitting it here too would put
  the same `doc_id` in two files. The report prints the 17/1 split so the reconciliation is
  visible.
- **Members are `#/$defs.member` records, not `member_bio` documents.** The roster serves a
  name and nothing else, so a document would need an invented `doc_id`, date, section, url and
  text — and the schema forbids minting ids.
- **Bill sponsors.** `Bills Introduced` records carry the sponsor in `mp_names[0]` (bare
  names, as spec §8 says); every other bill/motion names its mover in the turn that "beg[s] to
  move" it. Not every record names one — 715 of 1,795 name none, which is reported, not filled.

## Two errors found in the binding spec (reported, not silently worked around)

1. **§3.2's example is arithmetically false.** It says a raw date mis-sorts because
   "`15-1` > `3-3`"; as strings `'1' < '3'`, so `15-1` sorts *before* `3-3`. The claim that
   raw dates mis-sort is correct — `'9-1-2016' > '15-1-2016'` reproduces it — so the test uses
   a real counterexample and records the discrepancy in a docstring.
2. **§2's "18 declarations" counts raw matches, not distinct outcomes.** 18 matches dedupe to
   **12 distinct** `(doc_id, ayes, noes, abstentions)` rows, which is the number §2 itself
   states elsewhere. My output writes 11 (12 minus `budget-855`, which is dev1's). The report
   prints both numbers so the two readings cannot be confused.
