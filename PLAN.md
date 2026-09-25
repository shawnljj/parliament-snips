# 🌱 Parsnips — Singapore Parliament, snipped

**Repo:** `parliament-snips` · **Site:** Parsnips · **Status:** data + substance layers working, 2026 backfilling

> *"Less verbose than a Hansard transcript, more insightful than an Instagram post."*

---

## 0. Purpose (the north star)

**Help young adults understand how Parliament moves policy decisions** — so they can see
how those decisions shape their own choices about work, business and family, and what they
might contribute.

That is a specific job, and it rules things in and out:

| In | Out |
|---|---|
| What the Government decided or announced | Who "won" the exchange |
| What changes, for whom, from when | Whether an answer was adequate |
| Bills and the stage they reached | Word counts and speaking time (demoted — see §7) |
| Action being taken on issues raised | Party-political framing |

Concretely, a reader should be able to answer: *"What did Parliament just do, what does it
change, and what happens next?"* — in about two minutes, without reading 118,000 words.

**Length target:** between a Hansard transcript and an Instagram post. A brief per policy
item, ~30–50 short attributed points across a sitting, each one verifiable.

---

## 1. The gap we're filling

| | Reading load | Wait | Tells you | Misses |
|---|---|---|---|---|
| **Hansard transcript** | ~118k words / sitting | ≤7 working days | Everything, verbatim | You will never read it |
| **ST / CNA Instagram** | 40 words | ~30 min | The headline | What changes, for whom, the stage, what happens next |
| **Parsnips** | ~2 min per sitting | ~24h | What was done, what it changes, what happens next, what is unresolved | Nothing material — every point carries its source words |

---

## 2. What we verified about the data (this is the foundation)

**Everything below was tested against the live API on 16 Sep 2026.** Notes are in `scraper/parsnips_fetch.py` so nobody re-discovers them.

### ⚠️ The thing that will waste your weekend
Every tutorial, blog post and GitHub scraper calls:
```
GET https://sprs.parl.gov.sg/search/getHansardReport/?sittingdate=2024-05-07
```
**This endpoint is dead.** It was retired when Parliament rebuilt the portal as an Angular SPA. It returns `HTTP 500` for *every* date — including dates that definitely had a sitting. Code written against it reports "no sitting found" for all input and looks like a bug in your scraping, not a dead API.

### ✅ The live endpoints
Three POSTs to `https://sprs.parl.gov.sg/search` (needs browser UA + same-site `Referer` or it 500s):

| Endpoint | Purpose | Notes |
|---|---|---|
| `POST /searchResult` | Enumerate reports for a date | **Hard cap 20 rows/page** — `endIndex` is ignored. Page by 20. |
| `POST /getHansardTopic` | One report's full HTML + metadata | Content lives *only* here |
| `POST /fetchData` | Static filter lists | 107 current MPs, 467 former, 20 sections, 25 ministries |

### 🔴 The trap: `maxResult` is load-balanced and lies
Two backend nodes disagree on the total, and **deep pages silently drop rows**. Measured on 3 Mar 2026:

- Claimed `maxResult`: **73**
- Clean linear sweep returned: **62**
- Union with per-section sweeps: **70**

On the 5 Aug 2026 sitting the effect was worse: a linear sweep returned **127**, and
per-section sweeps recovered **32 more real reports** (159/167 = 95%). Those were not
phantom totals — they were reports we would have shipped *missing*. The shortfall
included 2 oral answers, 10 written answers and 16 written-answer-not-answered.

**Rule: always union a linear sweep with per-section sweeps (`rsSelected`), then publish
the coverage ratio. Never silently ship a partial day.**

### ⚠️ Hansard splits one debate across several records
A single debate can appear as multiple `reportId`s sharing a title. The 5 Aug 2026
motion "An Economy of the Future that Works for All" is **`motion-3008` (54,276 words)
AND `motion-3010` (18,347 words)**. Taking only the largest record understates the
debate by 18k words. Group by normalised title before picking a "debate of the day".

### ✅ Speaker attribution — the asset that makes this worth building
Report HTML is a flat run of `<p>` tags. A paragraph opening with `<strong>` is a new speaker turn:

```html
<p><strong>The Acting Minister-in-charge of Muslim Affairs (Mr Zaqy Mohamad)</strong>: Mr Speaker, ...
```

Measured on 5 Aug 2026 at full coverage: **97.1% of 560 turns carry an explicit speaker
tag.** Reports also carry an `mpNames` roster. Inline language switches appear as
`(<em>In English</em>)` — Hansard flags Chinese/Malay too.

**This is what a news post cannot give you: `[Minister] said X → [MP] challenged with Y → [Minister] conceded Z.`**

#### Parsing speaker names is harder than it looks
Two traps, both of which produced wrong output before being fixed:

1. **The first parenthetical is often not the name.**
   `The Minister for Trade and Industry (Energy and Industry) (Dr Tan See Leng)`
   → taking the first paren gives you *"Energy and Industry"*. Take the **last**
   parenthetical that begins with a personal title (Mr/Ms/Dr/…); if none does, strip
   all parentheses (they are constituency markers, e.g. `Ms Hany Soh (Marsiling-Yew Tee)`).

2. **A Minister is introduced with a portfolio once, then by bare name.** So
   `Dr Syed Harun Alhabsyi` and `The Senior Parliamentary Secretary to the Minister for
   Education (Dr Syed Harun Alhabsyi)` are the same person. Any question-vs-answer metric
   that buckets by "does the speaker string have a portfolio" will put the same person on
   both sides. Resolve to a name first, then classify every turn by that name.

### 📅 Sitting dates need discovery
The `parliament.gov.sg/.../sitting-dates` page **404s**. Probing each weekday against `/searchResult` works. Verified result for 2026 YTD:

```
Jan 12,13,14 · Feb 3,4,12,24,25,26,27 · Mar 2,3,4,5,6 · Apr 7,8
May 5,6,7 · Jul 7 · Aug 4,5        →  23 sittings
```

Cadence: Parliament sits in **clusters** (Budget season Feb–Mar is brutal), then breaks for weeks. This shapes the whole scheduling design.

### Other confirmed facts
- **Publication lag:** Hansard lands within **7 working days** — Budget season often runs to the wire. A sitting date is *not* a publish date.
- **Volume:** ordinary sitting ≈ 118k words / 560 turns (5 Aug 2026, measured at 95%
  coverage). Budget day (3 Mar 2026) ≈ **98.6k words**, of which `Budget` (Committee of
  Supply) is **78.4k**. Budget weeks are the stress test, not the ordinary case.

  Note the trap: our *first* measurement of 5 Aug said 57,290 words, because 32 reports
  were missing from enumeration. Under-counting coverage silently understates the volume
  too. **Fix enumeration before you size anything.**
- **Report types:** oral answers (~14/report-day), written answers (~54), written-answer-not-answered, motions, bills, ministerial statements, adjournment matters, tributes, corrections.
- **The hidden gems are in the written answers.** 135 written answers on 5 Aug, most only
  ~200 words, covering crow-shooting operations, bicycle parking standards, heritage
  business digitisation, cancer drug list reviews. Too small for news, perfect for a
  digest. **These are a differentiator — nobody reads them.**
- **Committee of Supply is the meatiest content**: `budget-2934` alone was **13,512 words** on Social & Family Development.

### Environment
- System python is 3.9 with broken `requests`/OpenSSL pairing → **scraper uses stdlib `urllib` only** (zero deps, deploys anywhere).
- Node v26.8.2, npm 11.19.1 available.

---

## 3. Product shape

### The site: `parsnips.sg` (or a Cloudflare subdomain)

```
┌─────────────────────────────────────────────────────────┐
│  🌱 Parsnips            [sittings ▾]   [topics] [MPs]   │
├─────────────────────────────────────────────────────────┤
│  5 AUG 2026 · Sitting No. 34 · 57,290 words · 2h 41m    │
│  ← the reading nobody has time for, done for you        │
├─────────────────────────────────────────────────────────┤
│  [ INFOGRAPHIC — full-bleed, the hero ]                 │
│                                                         │
│   THE ONE THING          WHERE THE TIME WENT            │
│   ┌──────────────┐       motions      ███████████ 18k   │
│   │ "An Economy  │       written      ████████     22k   │
│   │ of the       │       oral         ████████     12k   │
│   │ Future"     │                                        │
│   └──────────────┘                                        │
│                             WHO SPOKE                   │
│   AI · jobs · cost of living   Tan See Leng  ██████ 4.6k│
│                                 Kenneth Tiong █████ 4.4k│
│   THE NUMBERS                WHAT WAS COMMITTED         │
│   $800M · 10,000 firms ·     [...3 cards, each cited]   │
│        100,000 workers                                  │
├─────────────────────────────────────────────────────────┤
│  THE DEBATE OF THE DAY                                  │
│  18,347 words → 400, with who-said-what preserved       │
│  ○ Motion moved        ○ Minister's reply               │
│  ○ Kenneth Tiong cut in ○ Government response           │
├─────────────────────────────────────────────────────────┤
│  EVERYTHING ELSE — 71 more items, grouped & linked      │
│  ▸ Oral answers (14)   ▸ Written answers (109)          │
│  ▸ Bills (2)           ▸ Adjournment (1)                │
├─────────────────────────────────────────────────────────┤
│  Every claim links to the paragraph in Hansard.         │
│  Coverage 70/73 reports. Generated 16 Sep 2026 09:14.   │
└─────────────────────────────────────────────────────────┘
```

### Design principles
1. **The infographic is the product, not decoration.** Above the fold, no scrolling to reach it. It should be *screenshot-able* — that's the distribution mechanism.
2. **Every claim is a link.** Click a stat, land on the Hansard paragraph. That's the credibility line that separates this from an AI slop account.
3. **Show the argument.** Named speakers, question → answer → pushback. Not "the Minister announced…".
4. **Say what it cost.** Word count vs summary length, in public. Earns trust.
5. **Show data quality.** If coverage is 70/73, say so. If attribution dropped, say so.
6. **Restraint on the chrome.** One accent colour, generous whitespace, big type. Civic, not tabloid. No red/blue party tribalism on the homepage — party labels live in the MP view where they're context, not framing.

### The infographic — what actually goes in it
All panels are **computed from the parsed JSON**, not written by a model. They describe,
they don't score:

| Panel | Source | Why it's insightful |
|---|---|---|
| **Word budget** | `words` per section | Shows what Parliament actually spent time on vs what news covered |
| **Who held the floor** | words per speaker | Backbenchers and NMPs who got real airtime |
| **Questions and responses** | formal question → Minister's reply, then supplementary turns in order | The exchange as it happened, with participants named. Deliberately **not** scored — see §7. |
| **The numbers that came up** | figures + surrounding sentence + speaker | Lets a reader judge a promise from a statistic themselves |

### Panels that were built and then removed
Recorded so they don't get rebuilt:

- **Question-vs-answer ratio.** Computed answer/question length per oral answer and ranked
  by it. Accurate, interesting, and it implicitly judges whether an answer was adequate.
  Prohibited by the neutrality decision in §7.
- **"Commitment" tag on figures.** A regex to mark money the Government promised to spend.
  It was wrong on the facts — it tagged "$39,000" (a GST Assessable Income threshold being
  *described*) as a spending promise. Telling a promise from a cited statistic is reading
  comprehension, not pattern-matching. It belongs in the Phase 3 LLM pass where the model
  can be held to a citation.

**Rule: if a panel ranks, scores or labels the participants, it does not ship.**

---

## 4. Architecture

```
┌─ GitHub Actions cron ────────────────────────────────────┐
│  nightly 22:00 SGT                                       │
│    ① probe yesterday+today for a new sitting             │
│    ② if found: fetch (~70 reports, ~25s at 5 workers)    │
│    ③ parse → speaker-attributed turns                    │
│    ④ summarise (LLM, chunked, cited)                     │
│    ⑤ build infographic data + site                       │
│    ⑥ commit data/ + deploy                               │
└──────────────────────────────────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        ▼                               ▼
  data/*.json (git, versioned)     Cloudflare Pages
  = the permanent archive          = the site
```

**Why static + cron, not a server:** the content changes ~2×/week, the archive is small, and a git repo of JSON *is* the database. Free hosting, no cold starts, and every sitting is a diffable, citable commit. Reproducible by anyone.

**Stack:** Python stdlib scraper → JSON → static site generator → Cloudflare Pages. No framework needed; hand-written HTML/CSS with a p5.js or SVG infographic. Node is available if we want a build step, but the data layer should stay stdlib Python so it runs anywhere.

### Repo layout
```
parliament-snips/
├── scraper/
│   ├── storage.py            ← paths, stable keys, manifest (single source of truth)
│   ├── parsnips_fetch.py     ← verified, works today
│   ├── backfill.py           ← year-batched, resumable (--year / --status)
│   ├── migrate_storage.py    ← flat layout -> year shards (re-runnable)
│   └── digest.py             ← reading digest for editorial
├── summariser/
│   └── summarise.py          ← sitting JSON -> verified briefs (resumable)
├── data/
│   ├── manifest.json         ← what exists, what is summarised, what is stale
│   ├── sittings.json         ← flat index for the site
│   └── <year>/sitting_<date>.json
├── summaries/
│   ├── index.json            ← flat index for the site
│   └── <year>/<stable-key>.json
├── site/                     ← generator + templates
└── PLAN.md
```

### Storage design (settled 16 Sep 2026)

Shard by **year**, key by **report identity**, keep a **manifest**.

**Keys are report ids, never titles.** Titles are not unique (Hansard splits one
debate across records sharing a title — that is why `motion-3008` and `motion-3010`
are a single brief) and they change, so a title slug collides, orphans itself on an
edit, and can exceed filesystem limits (Vercel git unpack fails with
`GIT_REPO_FILENAME_TOO_LONG`). Ids sorted, so the key is deterministic:

| | |
|---|---|
| one report | `oral-answer-4213.json` |
| a merged debate | `motion-3008+3010.json` |
| six-record Budget debate | `budget-2857+2859+2861+2867+2871+2873.json` |

Longest key 41 chars including year and extension; the longest title-slug filename
was 105. Verified: 287 briefs → 287 distinct keys, 0 collisions.

**Shard by year, not parliament.** A batch operates on a year, so year is the grain
that makes "which files belong to this batch" a directory listing. Parliament number
is recorded per report and in the manifest. `data/<year>/`, `summaries/<year>/`.

**Every report carries `report_version` ("sprs3")** plus `parliament_no`,
`sitting_no`, `volume_no`. A source link or validation pass never infers the era
from the id.

**The manifest** records per sitting: date, year, parliament, volume, sitting no,
format, report count, words, turns, coverage ratio, speaker-attribution rate,
per-group split, a content hash of the source payload, and summarisation coverage.
"What is missing" is a query (`--status`), not a glob. The summarisation denominator
counts only summarisable groups above the 150-word floor — counting every report
would report a permanent 12% because ~135 written answers per sitting are never
briefed.

**JSON stays the source of truth.** ~240 MB at 400 sittings, sharded so each
directory stays small. A database queries faster but turns a reviewable, citable
archive into a binary blob. Add a derived index only if cross-year page queries need
it.

### Batched backfill

One batch = one year, modern-first, independently resumable. Each sitting is written
atomically and the manifest is rebuilt as we go, so an interrupted batch costs only
the sittings in flight.

```bash
python3 scraper/backfill.py --year 2016 --discover
python3 scraper/backfill.py --years 2017 2018 --discover
python3 scraper/backfill.py --status
```

**Floor is 2016 and is enforced.** 2016 is the first cleanly modern year: all
sampled 2016-03-01 rows are `sprs3` with null `reportContent`, and
`getHansardTopic` returns real content. So from 2016 onward ONE code path works and
no era branching is needed. The legacy `sprs2` era is deliberately out of scope —
those reports answer HTTP 400 from `getHansardTopic` and carry their text in
`reportContent` on the search listing instead. Verified by probing the boundary:
`2012-03-01` is sprs2; the `2013-02-04` and `2016-03-01` samples are sprs3.

> **SCOPE DECISION, RE-AFFIRMED 20 Sep 2026.** 2015 was fetched, parsed and summarised
> during a floor experiment — 23 sittings, 96.8% attribution, zero pollution classes, and
> 228 briefs that passed every check. The measurement showed 2015 is the SAME `sprs3` era  <!-- artifacts-check: historical -->
> as 2016, so the original "legacy sprs2, HTTP 400" reasoning did not hold for it.
> **The owner's decision is to keep the corpus at 2016 onward anyway.** This is a scope
> choice, not a technical limit: do not re-open it on the grounds that 2015 works. It does.
> It is out of scope. The 2015 artifacts were removed (`data/2015`, `pipeline/dataset/2015`,
> `summaries/2015`) and the manifest is back to 331 sittings.

---

## 5. The summary pipeline (the part that needs real care)

A sitting averages **68,861 words** across **63 reports**, and the heaviest single
*item* is 213,325 words, so a heavy item does not fit one context window and small
local models cannot read it whole. Design:

> **Corrected 17 Sep 2026.** This section previously claimed "~1,100,000 words per
> sitting". That figure was wrong by 16× — it appears to have been the corpus total
> divided by nothing. The measured mean is 68,861 words/sitting (22,793,049 words
> across 331 sittings). Requirement N-3 now forbids hand-written metrics; recompute
> rather than transcribe.

1. **Chunk on turn boundaries, never mid-sentence** — and cap the PROMPT, not each
   report. See "Map-reduce" below: this was got wrong once and it silently truncated
   the middle out of every large debate.
2. **Map-reduce:** per-chunk extraction → one item-level reduce. Chunks run in
   sequence over one item; items can run in parallel (the worker pool does this).
3. **Extract structured, not prose.** Chunks emit JSON: key points, speaker, quote.
   **Citations are produced at extraction time, not bolted on** — that's what makes
   them trustworthy.
4. **Quotes verbatim, gated.** Every quote is checked against the full transcript and
   dropped if it does not appear character-for-character. A reworded "quote" is the
   fastest way to lose credibility.
5. **Editorial voice.** Less "the Minister highlighted the importance of", more "the
   Minister committed to X; Kenneth Tiong pressed on Y and got no commitment." The
   `humanizer` skill applies here. Ban the AI-isms.
6. **Guardrails:** never infer a policy position from a written answer's silence; report
   open ground neutrally rather than flagging a question as unanswered; exclude
   procedural noise (`[Mr Speaker in the Chair]`, PTBA) from summaries.

### Map-reduce, and the truncation bug it fixes

**The bug (found 17 Sep 2026, during local-model benchmarking).**
`build_transcript(report, char_budget=90000)` capped **each report**. Then
`merge_transcript(item)` **concatenated every report in the item**. The cap was
written as if one report = one prompt, which was true before items began merging
across sitting days. After the merge it bounded nothing:

| item | reports | merged prompt |
|---|---|---|
| President's Speech | 14 | 939,453 chars |
| Debate on President's Address | 9 | 709,901 |
| Constitution of the Republic of Singapore (Amendment) Bill | 8 | 483,248 |
| Debate on Annual Budget Statement | 6 | 468,205 |

939,453 chars is roughly 235,000 tokens. The cloud model's context absorbed it, which
is why nobody noticed for months. On a smaller model the prompt exceeded the window
and the response was **prose instead of JSON** — a silent, confusing failure that
looked like the model ignoring the format.

**Why truncation was the wrong fix.** Head+tail truncation silently drops the
**middle of a debate**. For a site whose credibility rests on "every point carries
the words it was taken from", summarising a debate from its first 70% and last 25% and
calling it a summary of the debate is a correctness problem in its own right —
independent of which model runs it.

**The fix, as implemented.** Chunk at 40,000 chars, map, reduce, and gate at item
level:

1. **Chunk on turn boundaries.** The transcript is speaker-labelled, so a turn is the
   natural unit. Splitting mid-turn would cut a sentence in half, which then fails the
   quote gate for reasons that look like model error.
2. **A turn that is itself oversized splits at SENTENCE boundaries.** This is not
   optional: **57 single turns exceed 40,000 chars**, the longest being 100,940 (a
   Finance Minister's Budget speech). Turn-boundary chunking alone would still
   overflow. That speech has 862 sentences, longest 387 chars, so sentence splitting is
   safe. A single sentence longer than the budget is emitted whole — measured as 30
   chunks overshooting by at most 0.6% (worst: +241 chars).
3. **Map:** each chunk emits `key_points` only. A chunk cannot know the item's title or
   significance, so it is not asked for them — asking would invite invention.
4. **Reduce:** one small call over the chunk notes produces the item-level fields
   (title, what_it_is, stage, why_it_matters, what_happens_next, not_said). That is
   where item-level judgement belongs.
5. **Gate at item level, once.** `verify_quotes` runs against the FULL merged
   transcript, not per chunk. Per-chunk quotes were copied from the transcript so they
   verify at item level, and if a chunk invented anything the item-level gate still
   catches it. One gate, one source of truth.

**Cost, measured on the real corpus (3,912 items):** 6,256 chunks vs 3,912 items —
**+59.9% calls**, with 569 items needing more than one chunk. The heavy end carries
the extra calls (the 213,325-word President's Speech becomes 41 chunks). A single-chunk
item takes the direct path, so the ~3,300 items that never needed chunking cost exactly
what they did before. That is a fair price for not discarding most of a Budget debate.

**Note the interaction with §6b:** this bug surfaced only because small local models
have small windows. It was always present in production; the cloud model's large
context masked it.

### Cost sanity
A sitting averages 68,861 words (23M words across the 331-sitting archive), chunked
into ~40k-char prompts. At current prices that's cents per sitting, ~2×/week, so a few
dollars a month for the ongoing feed. The full 2016-onward backfill is the expensive
one — which is why §6b exists.

---

## 6. Build plan — phased, each phase ends with something real

| Phase | Deliverable | Status |
|---|---|---|
| **0. De-risk** | Live API verified, dead endpoint documented, coverage trap solved, attribution measured | ✅ **Done** |
| **1. Ingest** | `parsnips_fetch.py` + sitting-date discovery + section-union enumeration | ✅ **Done** |
| **1b. First page** | 5 Aug 2026 fully parsed at 159/167 (95%) and rendered to a working sitting page | ✅ **Done** |
| **2. Backfill** | All 23 sittings of 2026: 1,959,951 words, 97% attribution | ✅ **Done** |
| **2b. Storage** | Year shards + stable keys + manifest, ready for ~400 sittings | ✅ **Done** |
| **3. Summarise** | 287 briefs across 2026, every published sentence quote-checked | ✅ **Done** |
| **4. Site v2** | Mobile-first sitting pages, section rail, scroll memory, collapsible cards | ✅ **Done** |
| **5. Depth** | 2016–2026 backfill **complete: 331 sittings in the manifest** | ✅ **Done** |
| **5b. Model choice** | Settled by measurement: `deepseek-v4.1-flash:cloud`. Local models disqualified — see §6b | ✅ **Done** |
| **6. Verbatim-verified pipeline** | Selection-by-sentence-id (schema 4). Every published sentence byte-identical to the record; a gate proves it. See §6c | ✅ **Done** |
| **7. Corpus generation** | All 11 years 2016–2026 briefed: **3,863 briefs**. Regenerate-and-promote loop per year | ✅ **Done** |
| **7b. Verification chain** | Test 1 (sentence completeness vs a fresh Hansard pull) not yet built — see `docs/card-verification-chain.md` | ⬜ **Next** |
| **8. Automate** | GH Actions cron → auto-detect, fetch, summarise, deploy | Then |
| **9. Polish** | Topic threads across sittings, MP pages, RSS, OG images | Later |

### Where the corpus actually stands (measured 2026-09-21)

| Year | Sittings | Dataset items | Briefs published | Verification |
|---|---|---|---|---|
| 2016 | 29 | 354 | **349** | **FAIL — 672 defects** (24 stale briefs) |
| 2017 | 25 | 345 | **343** | PASS — 0 defects |
| 2018 | 32 | 366 | **362** | PASS — 0 defects |
| 2019 | 28 | 301 | **299** | PASS — 0 defects |
| 2020 | 34 | 333 | **326** | PASS — 0 defects |
| 2021 | 30 | 352 | **346** | PASS — 0 defects |
| 2022 | 35 | 415 | **412** | PASS — 0 defects |
| 2023 | 39 | 485 | **481** | PASS — 0 defects |
| 2024 | 30 | 369 | **364** | PASS — 0 defects |
| 2025 | 26 | 301 | **294** | PASS — 0 defects |
| 2026 | 23 | 291 | **287** | PASS — 0 defects |

**Total live: 3,863 briefs, 134,500 published sentences.** Every year must pass
`python3 tools/check_selection.py summaries/<year> <year>` with **0 defects** before it ships.

> **This table was previously wrong by 4×.** It read "949 briefs, 34,746 sentences" with  <!-- artifacts-check: historical -->
> 2016–2022 marked *pending* and 2023 *running*, while all eleven years had in fact
> shipped. The figures above are recomputed from `summaries/` — do not hand-edit them;
> they come from `tools/write_status.py` and are checked by `tools/check_artifacts.py`.


> **Phase 5b is settled, not forgotten.** The model question was answered by measurement
> rather than deferred: `deepseek-v4.1-flash:cloud` is the pipeline model. The local-model
> idea was tested and rejected — `llama3.2:3b` gave **1–8 points on identical input**, and
> `qwen3:4b` is a reasoning model (547 tokens for a 12-char reply, 143s/item). Full figures
> in **§6b** and `MODELS.md`.

### What already runs
```bash
# discover which days Parliament sat
python3 scraper/parsnips_fetch.py --discover 2026-01-01 2026-09-16

# fetch + parse one sitting
python3 scraper/parsnips_fetch.py 2026-08-05 data/x.json

# read it as a digest (group, chars-per-turn, max reports)
python3 scraper/digest.py data/2026/sitting_2026-08-05.json oral 700 4

# fetch a whole year, then see what you have
python3 scraper/backfill.py --year 2016 --discover
python3 scraper/backfill.py --status

# summarise, then build the site (the 331 pages Vercel serves)
python3 summariser/summarise.py --all --workers 3
python3 rag/export_read.py --out site/dist
```
All stdlib Python 3.9+. No `pip install` required — the system Python here has a broken
`requests`/OpenSSL pairing, which is why the scraper uses `urllib`.

**Fixed since:** `fetch_sitting` used to do 21 sweeping passes (several minutes per
sitting). Enumeration is now parallel (`ENUM_WORKERS = 4`) and unioned over one linear
plus per-section sweep: **293s → ~90s**, measured. Kept modest on purpose — this is a
public government portal and we are guests.

### Phase 5 first, deliberately
Backfilling 2016–2025 before automation means the archive has decade-scale depth when
the cron goes live, and summarisation has a varied corpus to be tuned against rather
than one year's habits. Batches run modern-first from 2016 upward so the most-read
years land first.

---

## 6b. Choosing a model for the full corpus — DEFERRED until the fetch finishes

**Status: not started, on purpose. Do not act on this until the 2016–2025 download is
complete.** Recorded here because the next person to touch this repo may be a session
with no memory of the night this was decided.

### The problem

The full 2016-onward corpus is a very large amount of summarisation. Running it all
through cloud inference is the expensive path, so the target is a **smaller model
running locally**. That decision is not made yet — this section records what has been
measured so the choice can be made deliberately later.

### Hardware (measured on this machine)

| | |
|---|---|
| Chip | Apple M1 Max |
| Unified memory | 32 GB |
| GPU cores | 32 |
| Ollama | 0.34.1 |

A 4B–8B model at 4–5 bit quantisation is roughly 3–6 GB and runs entirely in GPU
memory here — the configuration where small models actually perform well. A usefully
quantised 27B wants ~16–20 GB and competes with everything else on the machine, which
is the likely reason **~27B models were tried previously and "didn't work that well"**.
So 8B is a reasonable target and 4B is worth benchmarking.

### The real constraint is input size, not parameter count

Measured across the 2026 briefs at the time (`_meta.source_words`, a schema-3 field that no longer exists):

| | |
|---|---|
| median | **845 words** |
| p90 | 17,404 words |
| max | 151,476 words |
| over 30k words | 13 of 291 |
| over 60k words | 2 of 291 |

Most items are tiny. The summariser builds a **120,000-character** transcript
(**90,000** for merged multi-day items, where several reports are concatenated) and
truncates beyond that with a head+tail policy — roughly 30k input tokens. So a small
model must survive a long context for the heavy items, while the bulk of the work is
small inputs.

### Option that fits the data (to evaluate, not yet decided)

- a **small model (4B–8B)** for the long tail — the ~845-word median briefs, which is
  most items
- keep a **more capable model** for the ~13 heavy debates (Bills, Budget, Committee of
  Supply)
- **benchmark on a fixed sample of ~10 briefs** spanning median and heavy before
  committing to anything

### Two correctness constraints that must not be traded for speed

1. **The verbatim-quote gate is what makes the site trustworthy.** Every key point
   must carry a quote that actually appears in the transcript; unverifiable points are
   dropped. A smaller model is *more* likely to lightly reword a quotation. The gate
   will catch that, and dropping is the safe direction — but **benchmark the drop
   rate**. It was 4 dropped points on the 2016 run. A much higher rate means the model
   is unsuitable no matter how fast it is.
2. **The quality bar is the neutral, cited, outcome-focused brief.** A cheaper model
   that produces mush is worse than no summaries, because the page asserts that every
   point is backed by the transcript.

Measure three things on the sample: **points surviving the gate**, **wall-clock per
brief**, and **whether the prose still reads as neutral reporting**.

### Switching model is a variable, not a code change

The summariser already reads its configuration from the environment:

```bash
PARSNIPS_LLM_URL    # default http://127.0.0.1:11434/v1/chat/completions
PARSNIPS_LLM_MODEL  # default deepseek-v4.1-flash:cloud
```

### ⚠️ Nothing genuinely local is installed yet

All three models currently reachable on the local endpoint are **cloud-proxied**,
despite living on localhost:

| name | params | quant |
|---|---|---|
| `nemotron-3-nano:30b-cloud` | 32B | FP8 |
| `gpt-oss:20b-cloud` | 20.9B | MXFP4 |
| `deepseek-v4.1-flash:cloud` | 763B | FP8 |

The `-cloud` suffix means they proxy out to a provider. **An actual local model must be
pulled before any of this can be benchmarked** — until then "local" is a misnomer and
the cost argument does not hold.

---

## 7. Decisions (settled 16 Sep 2026)

| Question | Decision |
|---|---|
| **Audience** | The general public who read the news. Not Hansard nerds, not journalists. Plain language, no jargon, no assumed institutional knowledge. |
| **Tone** | Bite-sized, neutral, factual. No side-taking, no framing. State what was said and by whom. |
| **Opinion** | None. We do **not** flag "this question went unanswered". Instead we **map the question to the response** — summarised, never taken out of context, presented in order with participants named. Whether a response was adequate is the reader's call, not ours. |
| **Questions panel** | A **mapping**, not a scorecard. No answer/question ratio. Supplementary exchanges are kept in order and collapsible so context is preserved. |
| **Vernacular turns** | Flagged as "not transcribed here" (the words live in a separate document). Never invent text; never silently drop them. |

### What this ruled out
An earlier build computed a question-vs-answer word ratio and ranked oral answers by it
("Probe into 15 July GCE O Level" = 6.7x). It was accurate and it was the most
interesting thing on the page — but any ranking of that kind implies a judgement about
whether an answer was adequate. That is exactly the framing the decision above
prohibits. It was removed. Same reasoning killed the "commitment vs statistic" tag, which
was also wrong on the facts (it tagged a GST income threshold as a spending promise).

**Rule for future features: if a panel ranks, scores or labels the participants, it does
not ship.**

### Still open
- **Model choice for bulk summarisation — DEFERRED, see §6b.** No benchmarking until the
  2016–2025 fetch finishes. A genuinely local 4B–8B model must be pulled first; nothing
  on the local endpoint is actually local today.
- Domain/branding: `parsnips.sg`, `.com`, or a Cloudflare subdomain to start?
- How prominently to disclose AI generation (recommendation: prominently, on every page
  — it is a trust asset, not a liability).
- **2014–2015 were never actually empty — RESOLVED.** An earlier boundary probe found
  no sitting on any of six sampled days in each year and they were recorded as
  unexplained. That conclusion was wrong: the probe sampled calendar slots, and
  Parliament does not sit on most weeks, so it could not tell "no sittings" from
  "sampled the wrong weeks". Full-year sweeps found **33 sitting days in 2014 and 23 in
  2015** — ordinary years, consistent with 2016-2017. Nothing is wrong with those
  years and no special handling is needed; they sit below the 2016 floor by choice,
  not because they are unreadable.
  **Rule worth keeping: test an absence during Budget season (Feb–Mar) before believing
  it.** A sparse probe of arbitrary dates cannot establish that a year has no sittings.

*(Backfilling before 2026 is no longer open: settled as a 2016 floor, in progress.)*

---

## 8. The honest risks

| Risk | Severity | Mitigation |
|---|---|---|
| `maxResult` drops reports silently | **High** | Union sweeps + published coverage ratio. **Already solved.** |
| Portal rebuild breaks the API again | **High** | All quirks documented in one module; the fetcher is the only thing that touches the API. Keep the raw JSON archive so the site survives an API outage. |
| Budget-season volume (98k words) | Medium | Per-report chunking, parallel pass 1, test on 3 Mar 2026 first |
| LLM hallucinates a commitment | **High** | Structured extraction, verbatim quotes pulled from source, per-claim links, coverage disclosure |
| 7-day publication lag confuses the schedule | Low | Cron polls; it doesn't assume |
| Political sensitivity | Medium | Neutral tone, cite everything, disclose method, no party framing on the homepage |
| Legal / copyright | Medium | Hansard is public record; summarise and link, never republish full text. Check the portal's terms. |

---

## 9. Why Parsnips is a good name

A parsnip is a root vegetable: **unglamorous, and the good stuff is underground.** That's the written answers, the adjournment motions, the question a Minister didn't quite answer. Also: `PARL` + `snips`. It's memorable, and the tagline writes itself.
