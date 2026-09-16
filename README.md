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
| Backfill | ✅ `scraper/backfill.py`, resumable, ~90s per sitting |
| Site | ✅ `site/build_site.py` → index + archive + one page per sitting |
| 2026 backfill | 🔄 running — see `backfill.log` for progress |
| Summarisation (LLM) | ⬜ next — current panels are computed, not written by a model |
| Automation | ⬜ next — GitHub Actions cron |

See **[PLAN.md](PLAN.md)** for the design, phases, product decisions, and the hard-won
notes about the Hansard API.

---

## Quick start

Python 3.9+, standard library only.

```bash
# 1. Which days did Parliament sit?
python3 scraper/parsnips_fetch.py --discover 2026-01-01 2026-09-16

# 2. Fetch every sitting of 2026 (~90s each, resumable — safe to Ctrl-C and rerun)
python3 scraper/backfill.py

# 3. Build the whole site
python3 site/build_site.py            # -> site/dist/
```

Then open `site/dist/index.html`.

Handy while iterating:

```bash
python3 scraper/parsnips_fetch.py 2026-08-05 data/x.json            # one sitting
python3 scraper/digest.py data/sitting_2026-08-05.json oral 700 4   # read it as text
python3 site/build_site.py /tmp/out                                 # build elsewhere
```

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
│   ├── parsnips_fetch.py          API client + HTML -> speaker-turns parser
│   ├── backfill.py                resumable multi-sitting fetch
│   ├── normalise.py               raw parse -> canonical schema
│   └── digest.py                  print a sitting for editorial review
├── site/
│   ├── build_site.py              sitting JSON -> whole static site
│   └── dist/                      generated output (index, archive, per-sitting)
├── data/
│   ├── sittings.json              index of every sitting with its stats
│   └── sitting_YYYY-MM-DD.json    the archive (this is the database)
└── backfill.log / .json           progress + resumable status
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
