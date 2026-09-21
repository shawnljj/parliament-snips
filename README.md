# 🌱 Parsnips

**Singapore Parliament, snipped.** Less verbose than the Hansard transcript, more
insightful than an Instagram post from the news outlets.

> Repo name: `parliament-snips` · Site name: **Parsnips** (PARL + snips)

---

## What this is

Every sitting, Singapore's Parliament publishes the full Hansard record — around
**118,000 words** for an ordinary sitting, more during Budget. It lands up to 7 working
days later, as a wall of HTML. Almost nobody reads it.

Parsnips turns each sitting into:

- **an infographic** of where Parliament actually spent its words,
- **who held the floor** — airtime measured in words, excluding the Chair,
- **a question-by-question mapping** of what was asked and what was said back,
- **the figures that came up**, each with the sentence around it and the speaker,
- **the debate of the day**, with speaker order preserved,
- **everything else** — including the ~135 written answers per sitting that never make
  the news.

Coverage, speaker-attribution rate and method are printed on every page.

---

## Status

| | |
|---|---|
| Live API | ✅ verified 2026-09-16 against the Parliament portal |
| Scraper | ✅ `scraper/parsnips_fetch.py`, stdlib only, no `pip install` |
| Backfill | ✅ `scraper/backfill.py`, year-batched, resumable, ~90s per sitting |
| Storage | ✅ year-sharded + manifest — see **Storage** below |
| Site | ✅ `site/build_site.py` → index + archive + one page per sitting |
| 2026 backfill | ✅ first year, 23 sittings, 287 briefs |
| Corpus | ✅ **2016–2026 complete: 3,863 briefs across 11 years, 331 sittings** |
| Gates | ✅ 10 of 11 years PASS — 0 defects. **2016 FAILs 672** (24 stale briefs) |
| Backfill floor | **2016** (2015 deliberately out of scope) |
| Automation | ⬜ next — GitHub Actions cron |
| Verification chain | ⬜ next — sentence completeness vs a fresh Hansard pull, see `docs/card-verification-chain.md` |

See **[PLAN.md](PLAN.md)** for the design, phases, product decisions, and the hard-won
notes about the Hansard API.

---

## Quick start

Python 3.9+, standard library only.

```bash
# 1. Which days did Parliament sit?
python3 scraper/parsnips_fetch.py --discover 2026-01-01 2026-09-16

# 2. Fetch one year — the batch unit. Resumable: safe to Ctrl-C and rerun.
python3 scraper/backfill.py --year 2016 --discover
python3 scraper/backfill.py --years 2017 2018 2019 --discover

# 3. What do I have, and what is summarised?
python3 scraper/backfill.py --status

# 4. Summarise (separate pass, resumable)
python3 summariser/summarise.py --all --workers 3

# 5. Build the whole site
python3 site/build_site.py            # -> site/dist/
```

Then open `site/dist/index.html`.

Handy while iterating:

```bash
python3 scraper/parsnips_fetch.py 2026-08-05 data/x.json             # one sitting
python3 scraper/digest.py data/2026/sitting_2026-08-05.json oral 700 4  # read it as text
python3 site/build_site.py /tmp/out                                  # build elsewhere
```

---

## Storage

Sharded by **year**, keyed by **report identity**, with a **manifest**. The original
layout was one flat directory of `sitting_*.json` plus one flat `summaries/` whose
filenames came from brief *titles*. That was fine for 23 sittings and would not
survive a backfill to 2016 (~400 sittings, tens of thousands of reports).

```
data/2016/sitting_2016-03-01.json          one sitting
data/manifest.json                         what exists, what is summarised, what is stale
summaries/2016/motion-3008+3010.json       one brief, keyed by report ids
summaries/index.json                       flat index for the site
```

`scraper/storage.py` is the single source of truth for paths and keys, imported by the
fetcher, the summariser and the site builder so they cannot drift.

**Why keys are not filenames from titles.** Titles are not unique — two Hansard records
can share one (that is why `motion-3008` and `motion-3010` are merged into a single
brief) — and they change, which orphans the old file. Long titles also produce filenames
that break Vercel's git unpack with `GIT_REPO_FILENAME_TOO_LONG`. The report ids are the
real identity:

| | |
|---|---|
| one report | `oral-answer-4213.json` |
| a merged debate | `motion-3008+3010.json` |
| a six-record Budget debate | `budget-2857+2859+2861+2867+2871+2873.json` |

Longest key in the corpus is 41 characters including the year and extension; under the
old scheme the longest filename was 105.

**The manifest** (`data/manifest.json`) records per sitting: date, year, parliament,
volume, sitting number, report format, report count, words, turns, coverage ratio,
speaker-attribution rate, the per-group split, a content hash of the source payload, and
how much of it is summarised. That is what makes "what is missing" a query rather than a
directory glob, and what makes a batch resumable.

**Reports carry `report_version`.** Every report records the Hansard format it came from
(`sprs3`), plus `parliament_no`, `sitting_no` and `volume_no`, so a source link or a
validation pass never has to infer the era from the id.

**JSON stays the source of truth.** 14 MB now; ~400 sittings at this density is roughly
240 MB, and year-sharding keeps each directory small. Git handles that, and it preserves
the property that every sitting is a reviewable, citable commit. A database would query
faster but turns the archive into a binary blob. Add a derived index only if page-level
queries across years ever need it.

---

## Product decisions

Settled 16 Sep 2026. These constrain what may be built — see PLAN.md §7.

| | |
|---|---|
| **Audience** | The general public who read the news. Plain language, no jargon. |
| **Tone** | Bite-sized, neutral, factual. No side-taking, no framing. |
| **Opinion** | None. We never flag "this question went unanswered". |
| **Questions** | **Mapped** to responses, in order, participants named. Not scored. |

**Rule: if a panel ranks, scores or labels the participants, it does not ship.**

Two features were built and then removed under this rule — a question-vs-answer ratio and
a "commitment" tag on figures. Both are documented in PLAN.md §7 so they don't get
rebuilt.

---

## Read this before touching the scraper

**The documented endpoint is dead.** Every blog post and GitHub scraper calls
`GET /search/getHansardReport/?sittingdate=...`. It returns HTTP 500 for *every* date,
including dates that definitely had a sitting — the portal is now an Angular SPA.

The live API is three POSTs to `https://sprs.parl.gov.sg/search` (`searchResult`,
`getHansardTopic`, `fetchData`) and needs a browser User-Agent plus a same-site Referer.

Four traps, all of which produce silently wrong output rather than an error, are
documented in the module docstring of `scraper/parsnips_fetch.py`:

1. `maxResult` is load-balanced and unreliable — a clean sweep loses ~15% of a sitting.
   It also *under*-reports sometimes (13 Jan 2026: 126 collected against a claimed 124),
   so never treat a ratio above 1 as a bug. Always union a linear sweep with per-section
   sweeps.
2. Hansard splits one debate across several records sharing a title.
3. Speaker names: the first parenthetical is often a portfolio, not a name.
4. A Minister is introduced with a portfolio once, then referred to bare — so any
   per-speaker bucketing must resolve names first.

---

## Layout

```
parsnips/
├── PLAN.md                        design, phases, decisions, API gotchas
├── README.md
├── scraper/
│   ├── storage.py                 paths, stable keys, manifest (single source of truth)
│   ├── parsnips_fetch.py          API client + HTML -> speaker-turns parser
│   ├── backfill.py                year-batched resumable fetch (--year / --status)
│   ├── normalise.py               raw parse -> canonical schema
│   ├── migrate_storage.py         flat layout -> year shards (re-runnable)
│   └── digest.py                  print a sitting for editorial review
├── summariser/
│   └── summarise.py               sitting JSON -> verified briefs (LLM, resumable)
├── site/
│   ├── build_site.py              sitting JSON -> whole static site
│   └── dist/                      generated output (index, archive, per-sitting)
├── data/
│   ├── manifest.json              what exists, what is summarised, what is stale
│   ├── sittings.json              flat site-facing index
│   └── <year>/sitting_<date>.json the archive (this is the database)
└── summaries/
    ├── index.json                 flat index for the site
    └── <year>/<stable-key>.json   one brief per policy item
```

---

## Data source & etiquette

Source: Parliament of Singapore, Official Reports (Hansard) — `sprs.parl.gov.sg`.
Hansard is a public record. Parsnips **summarises and links**; it does not republish
full transcripts. Scraping is polite and rate-limited, each sitting is fetched once, and
everything is cached to `data/` so the site survives an API outage.

This is an unofficial project, not affiliated with the Parliament of Singapore.

---

## Still open

Domain/branding, how prominently to disclose AI generation, and whether to backfill
history before 2026 — listed in [PLAN.md §7](PLAN.md).
