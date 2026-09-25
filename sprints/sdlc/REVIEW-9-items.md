# Review — the 9 unaccounted items

For the owner's decision, item by item. Prepared 2026-09-21.

**What makes these 9 special:** they are the dataset items that produced neither a
brief nor a withheld record. Nothing reports them. Every gate reads PASS because every
gate is scoped to briefs that exist — an item that produced no file is invisible to all
of them. This is an A-2 (no silent loss) violation that no check can currently see.

The other 33 unaccounted items have **0 sentences** — nothing to summarise, genuinely
fine, and outside this decision.

---

## The pattern: 7 of 9 are procedural motions

They share a shape. `excluded_counts` shows almost every sentence was filtered as
**procedural** before summarising:

| Item | Sentences | Excluded as procedural |
|---|---|---|
| `motion-1178+1202` (2019) | 1 | 10 |
| `motion-1366+1407` (2020) | 1 | 12 |
| `motion-2759` (2025) | 1 | 11 |
| `motion-2799` (2026) | 1 | 9 |
| `motion-1511` (2020) | 1 | 5 |
| `motion-2162` (2023) | 1 | 4 |
| `motion-2839` (2026) | 2 | 1 |

Read the titles and this is obviously correct behaviour, not a bug:

- *"Time Limit for Senior Minister of State's Speech"*
- *"Suspension of Standing Orders to Allow Minister of State and Minister for Law to
  Speak More Than Once and To Remove Time Limit"*
- *"Proceedings on Supply Business and Rearrangement of Business"*
- *"Proceedings on 15 October 2020"*

These are the House managing its own business. Your `PLAN.md §0` rules this out
explicitly — the "In" column is *what the Government decided*, and the "Out" column
already excludes procedural business, word counts and speaking order.

**So the pipeline is right and the accounting is wrong.** These should not be briefs.
They must carry a **withheld record naming "procedural"** — because absence is supposed
to be auditable, and right now it is not.

## The 2 that look like real content

**`president-address-14` (2018) — the one that matters.**

- Title: **"Prime Minister's Office (Smart Nation and Digital Government Group)"**
- **833 words, 37 sentences**, single turn — a Minister's speech
- Only 4 sentences excluded as `too_short`; essentially the whole speech survived filtering
- Group is `budget`, sitting 2018-05-07

**This is the genuine miss.** It is a Minister's address on Smart Nation policy, fully
parsed, nothing filtered, and it produced no brief. 37 sentences is not small — most
published oral answers in the corpus are 8–12. I have no explanation for why this one
was skipped, and it is the item I would investigate first.

**`oral-answer-1819` (2017)**

- Title: **"Use of Steel Supplied by Company that Falsified Data"**
- 248 words, **9 sentences**, 2 turns, 1 courtesy sentence excluded
- A real policy Q&A: a question was asked, a Minister answered

9 sentences is above the `PARSNIPS_SELECT_MIN` floor of 8, so the floor does not
explain the skip — and items with 5–9 sentences are published elsewhere in the same
corpus (`oral-answer-1244` has 9; `oral-answer-1335` has 5). **This one is a real
candidate for publication**, and its absence is unexplained.

---

## Recommendation

| Item | Recommend | Why |
|---|---|---|
| `president-address-14` (2018) | **Investigate, then publish** | 37 sentences of Ministerial policy content. A genuine miss. |
| `oral-answer-1819` (2017) | **Publish** | Real Q&A, 9 sentences, above the select floor, comparable items ship |
| the 7 procedural motions | **Withhold, reason "procedural"** | Correctly excluded by PLAN.md §0; needs a record, not a brief |

**The distinction that matters:** publishing is a product decision; *recording the
reason* is a correctness requirement. For the 7, withholding with a reason fully
satisfies R-2.5. For the two content items, the reason is missing because the behaviour
was wrong.

## What I would not do

**Force-publish the 7 to make the count zero.** That is the "measure the wrong thing"
failure this project has already made twice — `check_selection` reporting PASS while 72
sections were blank, and `verify_citations` returning success while reading fields the
schema no longer had. A clean count achieved by shipping procedural noise is worse than
an honest count with recorded exclusions.

---

# Also for review: the sprint artifacts

Three files, committed as `7d4e097`.

**`sdlc/1-objectives.md`** — 36 objectives from every MUST in REQUIREMENTS.md §5–6,
each with a threshold, an artifact and a grader (det / model / human). Records **11 with
no check anywhere** and **4 currently failing**. Where a threshold cannot be stated it
says `OPEN` rather than inventing one.

**`sdlc/3-backlog.md`** — ranked P0–P4. P0 is "the corpus is not shippable"; P4 is doc
drift. Exclusions stated explicitly so they read as decisions.

**`tools/check_artifacts.py`** — the new first step of sprint planning. Recomputes every
figure a doc states, checks every year with briefs carries a gate verdict, and flags any
artifact older than the data it describes.

## Open questions the artifacts surface

1. **O-15** — what number defines "legible to a reader who has never read Hansard"? No
   measure exists, so the objective is unscoreable as written.
2. **`oral-answer-3339`** (2023) is recorded as **both published and withheld**. Which is
   correct?
3. **Should `promote_briefs.py` refresh `status.json`?** `build_briefs.py` already does,
   which is precisely why promotion is the one step that can desync it.
4. **`PLAN.md` reports 949 briefs; the real figure is 3,863.** It also lists 2023 as "in
   progress" and 2016–2022 as pending, all of which are done. Ranked P4, but it is a
   4× understatement that would mislead the next sprint's planning.
