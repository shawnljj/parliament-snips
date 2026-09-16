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
│   ├── parsnips_fetch.py     ← verified, works today
│   └── digest.py             ← reading digest for editorial
├── data/
│   ├── sittings.json         ← index: dates + coverage
│   └── sitting_YYYY-MM-DD.json
├── summaries/
│   └── YYYY-MM-DD.json       ← LLM output, cited
├── site/                     ← generator + templates
└── PLAN.md
```

---

## 5. The summary pipeline (the part that needs real care)

A 57k-word sitting does **not** fit one context window with good results, and naive chunk-and-summarise produces mush. Design:

1. **Chunk by debate, not by token count.** A "debate" is one report. Never split an oral answer across chunks — the Q and the A must be reasoned over together.
2. **Two-pass:** per-report extraction → cross-report synthesis. Pass 1 can run in parallel; only pass 2 sees the whole sitting.
3. **Extract structured, not prose.** Per report, force the model to emit JSON: topic, headline, who-spoke, key numbers, commitments, points of friction, and a paragraph-level anchor for each. **Citations are produced at extraction time, not bolted on** — that's what makes them trustworthy.
4. **Quotes verbatim.** Pull the quote string from source text, never let the model retype it. A reworded "quote" is the fastest way to lose credibility.
5. **Editorial voice.** Less "the Minister highlighted the importance of", more "the Minister committed to X; Kenneth Tiong pressed on Y and got no commitment." The `humanizer` skill applies here. Ban the AI-isms.
6. **Guardrails:** never infer a policy position from a written answer's silence; flag when a question went unanswered; label everything procedural (`[Mr Speaker in the Chair]`, PTBA) as noise and exclude from summaries.

### Cost sanity
~57k words ≈ 76k tokens in, plus synthesis. At current prices that's cents per sitting, ~2×/week. Even with a generous two-pass and retries, this is a **few dollars a month**. Run it on the heaviest Budget days to check before committing.

---

## 6. Build plan — phased, each phase ends with something real

| Phase | Deliverable | Status |
|---|---|---|
| **0. De-risk** | Live API verified, dead endpoint documented, coverage trap solved, attribution measured | ✅ **Done** |
| **1. Ingest** | `parsnips_fetch.py` + sitting-date discovery + section-union enumeration | ✅ **Done** |
| **1b. First page** | 5 Aug 2026 fully parsed at 159/167 (95%) and rendered to a working sitting page | ✅ **Done** |
| **2. Backfill** | All 23 sittings of 2026 in `data/`. Gives us a corpus and history on day one | Next |
| **3. Summarise** | Structured extraction + synthesis, one sitting end-to-end, quoted & cited | Next |
| **4. Site v1** | Harden the generated page: multi-sitting index, nav, OG images | Then |
| **5. Automate** | GH Actions cron → auto-detect, fetch, summarise, deploy | Then |
| **6. Depth** | Topic threads across sittings, MP pages, commitments tracker, search | Later |
| **7. Polish** | OG image per sitting (screenshot-able = shareable), RSS, dark mode | Later |

### What already runs
```bash
# discover which days Parliament sat
python3 scraper/parsnips_fetch.py --discover 2026-01-01 2026-09-16

# fetch + parse a whole sitting
python3 scraper/parsnips_fetch.py 2026-08-05 data/sitting_2026-08-05.json

# read it as a digest (group, chars-per-turn, max reports)
python3 scraper/digest.py data/sitting_2026-08-05.json oral 700 4

# render the sitting page
python3 site/build_site.py data/sitting_2026-08-05.json site/index.html
```
All stdlib Python 3.9+. No `pip install` required — the system Python here has a broken
`requests`/OpenSSL pairing, which is why the scraper uses `urllib`.

**Known rough edge:** `fetch_sitting` currently does 21 sweeping passes and takes several
minutes per sitting. Fine for a nightly cron, too slow for interactive use. Optimise by
caching the enumeration and running section sweeps only when coverage < 100%.

### Phase 2 first, deliberately
Backfilling 2026 before building any UI means: we have a corpus to tune summarisation on, we can compare a sitting against its predecessor, and the site has real depth on launch day rather than one lonely page.

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
- Domain/branding: `parsnips.sg`, `.com`, or a Cloudflare subdomain to start?
- How prominently to disclose AI generation (recommendation: prominently, on every page
  — it is a trust asset, not a liability).
- Whether to backfill history before 2026 for trend context (the architecture supports it;
  `--discover` already works for any date range).

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
