# site/dist: what is committed, why, and what the gate enforces

> **ARCHIVED 2026-09-25 — this describes old site v1, not the deployed site.**
>
> It was written when `site/build_site.py` generated `site/dist` (index + archive +
> `sittings/<date>.html`). That generator is retired: the deployed site is now built by
> `rag/export_read.py` (331 sitting pages at the root) and the old routes 404 on the live
> host. This doc is still the best account of *why* `dist` being tracked is dangerous, which
> carried over unchanged — but every page count, path and command below is a measurement of
> the old site. See `site/README.md` for what deploys today.
>
> **Also stale below:** `tools/check_dist.py` and `tools/check_fresh_clone.sh` no longer
> exist in the tree (removed in `fe6ffcb6`; `check_dist.py` was already absent from `main`,
> as the text itself notes). The surviving gates are `tools/check_artifacts.py`,
> `tools/check_selection.py`, `tools/check_year.py` (whose RENDERABLE step now runs
> `rag/export_read.py`), and the export's own verification block.

`site/dist` is tracked **on purpose** — it is the built site Vercel deploys (see
`.gitignore`). That makes it the one directory where *"I built it"* and *"I committed
it"* can silently disagree, and for weeks they did.

## The failure this card existed for

Two generators, one artifact. `site/build_site.py` renders each sitting page; the CSS
evolves separately. Between them, 24 of the 25 deployed 2017 pages ended up with pre-grid
markup:

| across the 25 deployed 2017 pages | deployed | fresh build |
|---|---|---|
| `.dsec` sections total | **1** (all on `2017-02-28`) | 5,881 |
| `.vslist` lists total | 1 | 5,881 |
| substance briefs carried, total | 1 | 100 |
| pages carrying `.dsec` | 1 | 25 |

`2017-01-09`, as the card cites it: deployed 0 `.dsec`, 0 briefs, 125,727 bytes (125,674
characters). The figures this card quotes are character counts; the byte counts are 53
and 314 higher than the character counts on those two pages, because both are UTF-8 with
non-ASCII in the Hansard text. The deployed page reads `73 items of business, read so you
don't have to.` with no brief bodies; the build reads `3 policies and 73 items of business,
read so you don't have to.`, carries the `Parliament sitting ~6 hours, Parsnips ~50 min.`
slogan, and holds the three briefs.

**The built page at each ref** (committed bytes; `rev_r3/probe_d1c.py` →
`rev_r3/d1c_counts.txt`):

| `2017-01-09.html` | bytes | characters | `.dsec` sections | briefs held |
|---|---|---|---|---|
| `994a1dff` (deployed — what the card cites) | 125,727 | 125,674 | 0 | 0 |
| **`7cd32a6d` (the tip at review round 9)** | **379,673** | **379,359** | **142** | **3** |
| `9fb0f545` (`main`'s tip) | 379,673 | 379,359 | 142 | 3 |
| `backup/t_79a0e9e2-before-rebase` (pre-rebase build) | 376,614 | 376,300 | 142 | 3 |

*Review round 9 correction — the label on the middle row.* This row read
`` **`09dc3259` (the deliverable)** ``. `09dc3259` is not the deliverable: it is an
ancestor, **4 commits** before `7cd32a6d`, the tip that was current when round 9
raised it (`git rev-list --count 09dc3259..7cd32a6d` = 4). The row's figures are true
of the tree — the page is byte-identical (`sha256 9bba6006…`) at `09dc3259`,
`2da003aa`, `7cd32a6d` and `9fb0f545` — so only the label was stale. It now names the
ref it was measured at rather than the word "deliverable", because the delivered tip
moves with every round and the row does not: the page has been byte-identical since
`09dc3259` and `main` holds those same bytes, which is why the row below it is
redundant by construction — same bytes, two branches. (`r14/labels_r14.txt`; the row
was re-checked against `f2303b8a` when the round-13 edits were committed: same
sha256, so the label is still the only thing that had moved.)


The delivered page is byte-identical to `main`'s (`sha256 9bba6006…`), because this branch
rebuilds the archive with `main`'s own generator and does not touch pages.

*Review round 9 correction — which tree the "built" figures belonged to.* This paragraph
said built **376,614 bytes (376,300 characters)**, **557 changed lines** (401 added, 156
removed) over a **655-line unified diff**, introduced with "as the card cites it". Every
one of those figures is the **pre-rebase** build's, to the byte and to the line: they
reproduce exactly on `backup/t_79a0e9e2-before-rebase` and nowhere else. At the delivered
tree the deployed-vs-built pair is **643 changed lines** (464 added, 179 removed) over an
**800-line** unified diff at n=3 (`rev_r3/probe_d1.py` → `rev_r3/d1_page.txt`). Both are
true of their tree; this table names which.

*Review round 4 correction — the card's "619 diff lines".* That figure is a different diff
instrument from the one this document reports, and it does not reproduce under any
instrument measured here: 20 of them, from `difflib.unified_diff` at n=0/1/2/3/5/10
(573 / 601 / 629 / 655 / 703 / 736) through GNU `diff`'s normal / `-u` / `-U0` / `-U1` /
`-c` / `-y` / `-e` / `-n` / `-D` formats (585 / 647 / 565 / 593 / 784 / 1430 / 459 / 449 /
1648) to opcode and hunk-marker reconstructions (changed lines 557; 557 + 14 hunks = 571).
It sits between the n=1 (601) and n=2 (629) unified counts, so 619 is most consistent with
a unified diff at some context width between 1 and 2 — not reproducible as stated and
**not adopted**. The figures of record are the **557 changed lines** (401 added, 156
removed) over the **655-line unified diff** at the conventional n=3 — and, per the round-9
correction above, that is the **pre-rebase** build's pair, not the delivered one. At the
delivered tree the same series reads 672 / 720 / 763 / 800 / 872 / 951 unified lines with
643 changed lines at every width, so `619` is not between anything there either: it is
below the whole series (`rev_r3/probe_d1b.py` → `rev_r3/d1b_619.txt`).

`.dsec` is the two-column grid — summary column, brief bodies, sentence list. It does
not exist on those pages. So they are a **mixed-generation artifact**: today's
`theme.css` (rail anchored to the content grid, guide rules present, all of it) over
pre-grid markup and pre-grid inline script. Their behaviour is predictable from
neither generation, which is why the failure showed up as a preview defect
(`check_rail_preview.py`: 3 of 3 sampled states failing on `2017-01-09`, 0 of 8 on a
2026 page of the same build) rather than as an obviously broken page.

*This is how the card found the archive.* While the review ran, `main` rebuilt those 25
pages itself (`94737f6e`, with a rewritten `build_site.py`), so the mixed-generation half
of this is fixed on `main` — but the payloads those rebuilt pages fetch were left
uncommitted, which is what this branch still lands. See "Why the 2017 payloads are
committed" below; the table above is the state the card was filed against.

`2017-02-28` is the page that makes this sharper, and it is not simply "less stale":
it *has* `.dsec` (1 section) and carries exactly one brief — so its markup is current and
only its content is old. The brief it carries, `motion-757+781+904`, is a merged 2016
motion whose `sitting_dates` span three dates (`2016-08-15`, `2016-11-07`, `2017-02-28`),
so it is legitimately placed here. What is missing is the 4 briefs whose own year is
2017: `bill-288`, `bill-289`, `bill-290`, `budget-905+907+911+913+915+919` (the other 6
briefs naming the date are oral answers and render in the page's oral-answers section,
not as cards). The build gives it 540 `.dsec` sections and all 5 substance briefs. So it
is a **partially rebuilt page** — current in structure, missing content, and carrying one
brief it does share with the build. A structure marker alone passes it; the gate's second
assertion passes it too if it only counts hosts, because it does carry a host — what
catches it is that 4 of the 5 briefs the data places on it are absent. The gate therefore
asks for both.

Cause: `442d49e` shipped 2017's briefs and never rebuilt `site/dist`. Measured from git,
not asserted: the commit touched **344 paths** — `summaries/2017/*.json` (343) and
`pipeline/withheld/oral-answer-1645.json` (1) — with **124,206 insertions, 0 deletions**,
and **0 paths under `site/dist`**. Its message's own figures check out against the tree:
**343 briefs** (343 files), **5,352 sections** (`_meta.sections_total` sums to 5,352) and
**10,585 selected sentences** (`_meta.sentences_selected` sums to 10,585). Nothing caught
the missing rebuild because the stale pages are *valid* HTML that link the *current*
stylesheet; only a markup-level comparison can see it, so that comparison is now a gate.

*Review round 3 correction.* An earlier draft of this sentence said the commit "shipped
343 briefs and **1,067 files**". **That number has no derivation and is withdrawn**: no
file, commit, or aggregate in the repo yields 1,067 (the nearest candidates — 1,066 and
1,067 — only appear by summing four unrelated counts by hand). The reproducible figures
are the 344 paths / 124,206 insertions above, and the commit message's 343 / 5,352 /
10,585, each of which re-derives exactly (`r4/probe_commit_figs.py`, `r4/hunt1067.txt`).

## What was rebuilt, and what was held back

*Review round 5/6 — the branch no longer carries page commits.* `main` moved 30 commits
during this review (`994a1dff` → `9fb0f545`) and rebuilt the 2017 sitting pages itself
(`94737f6e`) with a rewritten generator (`site/build_site.py` +566/−79). So the 25 page
commits this branch originally carried are gone: they were a build of the *old* generator
and would have reverted main's outline/rail work (they conflict on 25 of 26 pages).
What remains is the part `main` left undone.

| artifact | decision | why |
|---|---|---|
| `sittings/2017-*.html` (25) + `sittings/index.html` | **dropped for main's own** | `main` rebuilt these pages itself in `94737f6e`; its versions are newer and this branch's page commits revert its layout work |
| `sittings/index.html` | **committed** | a build still rewrites it; the file is main's own bytes, not this branch's old build |
| `skipped/*.json` for 2017 (341 new) | **committed** | `main` rebuilt the pages that fetch them and never committed the payloads; see below |
| `pipeline/state.json` | **committed** | now byte-identical to the `pipeline/state.json` it is a copy of |
| `tools/check_dist.py`, `tools/check_fresh_clone.sh`, `tools/check_artifacts.py`, this file | **committed** | the gate and its narrative; none of them existed on `main` |
| `skipped/*.json` for 2016 (42 modified) | **held back** | see below — committing them would make the deployed site *worse* |

The 341 payloads are the generator's own output for main's inputs, verified byte for byte:
at `main`'s tip, with the payload dirs linked, `site/build_site.py` writes 341 new payloads
under `site/dist/skipped` and **all 341 are byte-identical to the copies this branch
commits** (0 different, 0 missing). Only two files outside `skipped/` differ after a build,
and neither is a page: `pipeline/state.json` and `sittings/index.html`.

### Why the 2017 payloads are committed, restated

An earlier version of this table said "their absence was a live 404". **That was false of
the tree it was measured on, and it is true of `main` today.** Both halves are measured
below; the claim that survives is the one about the committed page's own fetch logic.

`fillInline(brief)` in the page's inlined script calls `loadSkipped(brief)` **before** it
looks for any host:

```js
var secs = document.querySelectorAll('.dsec[data-brief="' + brief + '"]');
if (!secs.length) return;
if (!isWide()) {
  // mobile: only the small runs that are visible by default
  loadSkipped(brief).then(...)      // <- unconditional below the breakpoint
  return;
}
var hosts = [];
secs.forEach(... sec.querySelectorAll('.runfull') ...);
if (!hosts.length) return;          // <- desktop: needs a .runfull host
```

and `initBrief(b)` is called once per distinct `.dsec[data-brief]` on load. So a hosted
brief is fetched by **every viewport below `isWide()`** (a plain
`matchMedia('(min-width: 761px)')` — 760px before main's generator rewrite) and, on
desktop, whenever its section holds a `.runfull` host. `loadSkipped()` then requests
`../skipped/<brief>.json`. Measured over all 331 committed pages, committed bytes. **The
`main` row describes `main` WITH its payload inputs present**; a payload-free clone — which
is what `check_fresh_clone.sh` builds, and the state the acceptance runs in — measures
differently for `main` (see the clause below the table):

| ref | `.dsec` (page, brief) pairs | distinct hosted briefs | pairs whose brief has no committed payload | … of those, fetch on mobile | … on desktop |
|---|---|---|---|---|---|
| `main` at `994a1dff` (the base) | 1,150 | 1,010 | 11 over 10 pages | 11 | **0** |
| `main` at `9fb0f545` (today, payload inputs present) | 1,249 | 1,103 | **110 over 35 pages, 104 briefs** | **110** | **97** |
| this branch | 1,249 | 1,103 | 13 over 11 pages | 13 | **0** |

*Review round 9 correction — which input state the `main` row is measured in.* The
**110 over 35 pages / 104 briefs** is `main` with `pipeline/dataset/*` present, which is
the operator's checkout and the tree the claim is about: there, `build_site.py` writes the
341 payloads those pages fetch and does not commit them
(`rev_r3/probe_d3.py` → `rev_r3/d3_main_inputs.txt`: payload inputs linked → **341**
build-created files uncommitted, first three `bill-275`, `bill-276+277`, `bill-278`;
payload-free → **0** build-created files, 2 tracked files rewritten). A payload-free clone
does not contradict the row — it simply cannot produce it, because
`build_skipped_payloads()` writes nothing there. The row's *fetch* columns are properties
of the **committed pages** and so are identical in both states: measured in a payload-free
clone of `main`, `r8/probe_fetch.py` still reports **110 / 110 / 97** over 35 pages
(`rev_r3/d3_free_fetch.txt`). This branch's own row of **13** is its clone reading in
either state, because here the payloads *are* committed — 3,848 of them.

*Review round 5 correction.* The earlier table reported `main`'s would-fetch column as
**0** and said "no committed page on either ref fetches a payload that is not committed".
That was true of `994a1dff`; it is **false of `main` as it stands**, and the 0 was the
justification for committing the 341 at all. `main` rebuilt its 2017 pages without their
payloads, so today `main` produces **110** fetching pairs — 99 of them on the 2017 pages —
across 35 pages and 104 distinct briefs, and 97 of them fetch on desktop too. One concrete
case on `main`'s `2017-01-09`: `bill-275`, `bill-276+277` and `matter-adj-893` each carry
`.runfull` hosts, and none of the three payloads is committed (tree-level check:
`git cat-file -e main:site/dist/skipped/bill-275.json` fails while the page hosts the
brief; the request path is the same `fetch('../skipped/bill-275.json')` quoted above).

*Review round 6 correction.* The mobile column was **0 for every ref** in the earlier
version of this table, because that instrument treated a section as fetch-capable only when
it held a `.runfull` or `.gapd`. Reading the committed script shows the mobile branch is
unconditional, which is why the `994a1dff` row is 11 fetching pairs and not 0. The
instrument now transcribes the script (`r8/probe_fetch.py`); the desktop column is
unchanged and both are reported separately.

On this branch the same measurement is **13** pairs over 11 pages and **0** of them fetch
on desktop — because all thirteen are briefs whose section carries no expander control.
Eleven are the pattern that already exists on the base (2018–2024, one apiece plus 2021's
four); two are new and are on `2017-03-09`: `bill-292` and `budget-2739`. Neither can have
a payload: `build_skipped_payloads()` writes one file per brief from
`pipeline/dataset/<year>/<id>.json` and **skips a brief whose skipped list is empty**, and
both of these have a dataset item holding a single sentence the brief already publishes, so
their skipped list is empty (`rec=1 published=1 skipped=0` — `r8/probe_payload_cause2.py`,
which calls the generator's own `BD.load_item`; the five other 2017 briefs checked beside
them have 116–4,207 skipped sentences and all have payloads). No payload can exist for these
two under any commit. A host with no expandable run is a heading with no control,
not a broken request — but it is a page/generator mismatch worth naming, and it is what
stops this branch's count from being zero.

*Review round 8, the clause round 7 asked for.* "No expander control" has to be read as
**neither** control: measured on the branch's committed `2017-03-09`, `bill-292` and
`budget-2739` each host one `.dsec` section, and neither section holds a `.runfull` (desktop)
or a `.gapd` (mobile) node, so the table's "0 fetch on desktop" is about `.runfull` while the
pair still counts as hosted. The page does carry both controls elsewhere — 1 `.runfull` and 2
`.gapd` occurrences site-page-wide — so the page is not control-free; these two briefs are
(`r10/probe_r10_log.txt`).

For the 2017 archive, `main`'s 25 rebuilt pages now carry expander hosts on every brief, so
the 341 payloads are the condition for those pages to work at all — the base's pages had no
hosts to break (`994a1dff`: 1 `.dsec` section in the whole year, on `2017-02-28`, and that
one host's brief `motion-757+781+904` has a committed payload).

`91` of the 341 are fetched by a committed page and **250 are not** — measured, and the
250 have a single explanation: they are the **oral-answer** payloads. `build_skipped_payloads()`
iterates the summaries it is given and does not apply `render_sitting`'s rule (a
`group == "oral"` brief renders in the page's oral-answers section, not as a `.dsec` card),
so it writes a payload for every schema-4 brief with a non-empty skipped list. Measured
(`r8/probe_unhosted.py`, `r8/probe_groups.py`): of the 341, **250 are oral answers and all
250 are hosted on no page at all**; the other 91 — 48 bills, 24 budgets, 9 adjournment
motions, 6 motions, 4 ministerial statements — are hosted, and **0** of the hosted ones
lack an expander control. (*Review round 6:* the earlier wording said the 250 were
"payloads of briefs whose hosts carry no expandable run". There are 0 such briefs; the
distinction matters because it is the difference between a rendering rule and a defect.)

### Why the 2016 payloads are held back, not accepted

Measured, not assumed. A page and its payload must be **disjoint**: the page renders
the sentences the brief selects, the payload holds the ones it does not. Count the
texts that appear on *both* sides, summed over all 349 2016 briefs:

| pair | overlap (2016 only) |
|---|---|
| deployed 2016 page + **committed** payload (at base) | **2** |
| deployed 2016 page + **rebuilt** payload | **502** (487 of it one payload) |

*Round 4/11 correction — what the rows count, and under which pairing rule.* The rebuilt
row used to say **610**, which is the **site-wide** figure for the swept state. Measured
2016-only, with this check's own instrument and confirmed by an independently written one
(`r11/probe_r11l.py` → `r11/r11l.txt`), the rebuilt payloads add **502** overlapping
sentence texts over **3 payloads** — `bill-223+…+230` 487, `bill-236` 14,
`president-address-266+…+665` 1. The base row's **2** was already 2016-only and is
unchanged. Both rows are now **counted once per payload**, over every committed page that
hosts the brief; round 10 found that the previous one-page-per-payload pairing was decided
by filesystem order, so these two rows are restated against the pairing rule the gate
actually implements (below).

The committed pair is consistent. The reason a rebuild breaks it is that
`build_skipped_payloads()` derives the payload from `pipeline/dataset/<year>/<id>.json`,
and that record's `sid → text` map has moved since the pages were built:
`tools/check_selection.py summaries/2016 2016` reports **672** verbatim mismatches against
the briefs, and the mismatches are inside the very brief that carries the 487
(`bill-223+224+225+226+227+228+229+230`, e.g. `s01331`, `s01332`, `s01336`, `s01337` —
`r8/p2016.txt`). So the rebuilt payload agrees with the current record where the committed
one does not, and the expanders would reveal sentences the reader can already see in the
body.

*Review round 6.* The earlier wording gave that agreement as "100% of common sids
(3,928/3,928) vs 29% (1,100/3,814)". **Both fractions are withdrawn**: measured at the
rebased tip they do not reproduce by sid intersection at all — this instrument finds **no
common sids** between the payload's entries and the dataset record's sentences for that
brief, in either state (`r8/p2016.txt`, `r8/probe_2016_r8.py`), so the denominators cannot
be read as stated. The direction of the claim is unchanged and is what `check_selection`'s
672 independently measures: the record moved after the pages were built, so a rebuild
desynchronises payload from page.

So committing would rewrite the hidden text to the new map while the pages keep
rendering the old one, and the expanders would reveal sentences the reader can already
see in the body — 502 of them in 2016, 613 site-wide. That is **P0-1** (`t_608872bf`),
which has to fix the briefs first; then page and payload can move together, in one commit.

### The pairing rule, and the round-10/11 rewrite of every figure in this section

Round 10 found that assertion 7 — the check that guards this hold — **was not a function of
the tree**. `briefs_expected_by_date()` built its `date → {brief}` map while iterating
`glob.glob(...)`, and the pairing then took `date_of_brief[brief]`, i.e. the *first* date
the filesystem happened to return. That is a real defect in a shipped gate, not a wording
problem, so round 11 changed the rule rather than the prose.

**The rule now.** The pair is `(payload, page)` and the invariant is about the page that
**fetches** the payload, so the pairs are exactly the committed payload × committed page
combinations where the page hosts the brief —
`<section class="dsec" data-brief="…">`. *Every* such page is paired. A payload hosted on
no page has no pair to check: it is never fetched, so it cannot reveal anything.

That removes the reduction entirely — there is no tie to break and no order to depend on —
and it is what the "first page that carries it" version could not be: **112 of this
branch's 1,090 committed payloads are hosted on more than one page** (the most-hosted real
brief, `president-address-2661+…+2733`, is on **6**); picking one was arbitrary whichever
way it was picked.

**The magnitude is counted once per payload.** Summing over pairs would multiply a payload
by its host count: `bill-223+…+230` alone is hosted on 3 pages and would contribute its 487
three times. A sentence shown and hidden at once is *one* sentence whether one page or six
reveal it, so the figure is the union, per payload, of the texts it shares with any host
page. For a single-host payload it equals the pair sum — and **978 of this branch's 1,090
hosted payloads are single-host** (892 of 999 at the base).

**What the rewrite changes, per ref** — all measured on fresh payload-free clones at each
ref, gate present, summary directory order forced to natural / sorted / reversed
(`r11/probe_r11c.py`, `r11/probe_r11l.py`, `r11/verify_order.sh`):

| ref | old rule (per-payload, glob order) | new rule (union per payload, all host pages) | pairs |
|---|---|---|---|
| `994a1dff` (base) | 106 / 107 / 108 — **order-dependent** | **109** over 83 payloads | 1,139 |
| `9fb0f545` (`main`) | 106 / 107 / 108 — **order-dependent** | **109** over 83 payloads | 1,139 |
| `7c9275ad` (this branch) | 110 / 110 / 112 — **order-dependent** | **113** over 87 payloads | 1,236 |
| this branch, 42 payloads swept in | 610 / 610 / 612 — **order-dependent** | **613** over 88 payloads | 1,236 |

The three numbers in the middle column are the same tree under three directory orders. The
right column is byte-identical output under all three, on all four trees
(`r11/order_{ref}_{natural,sorted,reversed}.txt`, `cmp`-equal four times out of four,
`r11/verify_order.sh` → `r11/r11_order.txt`).

**Which single pair moved, exactly.** Only one payload in the whole corpus changes an
overlap verdict with the pairing: `budget-1123+1129`, `sitting_dates`
`['2019-03-01','2019-03-04']`, hosted on both pages. Under natural order it paired with
`2019-03-04` and contributed 0; under sorted and reversed it paired with `2019-03-01` and
contributed 1 — the one text that separated 106 from 107 and 110 from 111. Another 45
payloads (46 on the branch) paired with a *different* page under the old rule without
changing a verdict. The new rule pairs it with both, so the choice cannot be made wrongly.

**The per-year split, re-measured** (`r11/probe_r11m.py` → `r11/r11m.txt`). The round-10
review was right that the old rows were quoted from two logs that disagree with each other —
`r8/gatefx.txt` (80 pairs / 106 texts) and `r9/r_overlap.txt` (81 / 107), in the **2019**
cell — so the rows could not both be right. (The review's "4 texts and 4 pairs" is not
reproducible from those two files: the disagreement is 1 text and 1 pair, in 2019, not 2017 —
`r11/probe_r11o.py` → `r11/r11o.txt`.) These are the union figures on each ref's own
committed bytes:

| year | base `994a1dff` / `main` | this branch | this branch, swept |
|---|---|---|---|
| 2016 | 2 | 2 | **502** |
| 2017 | — | 4 | 4 |
| 2018 | 6 | 6 | 6 |
| 2019 | 12 | 12 | 12 |
| 2020 | 13 | 13 | 13 |
| 2021 | 12 | 12 | 12 |
| 2022 | 11 | 11 | 11 |
| 2023 | 15 | 15 | 15 |
| 2024 | 14 | 14 | 14 |
| 2025 | 15 | 15 | 15 |
| 2026 | 9 | 9 | 9 |
| **total** | **109** | **113** | **613** |

2016 contributes **2**, not 0, in both the base and the branch — the residue is
pre-existing and not confined to the held-back year. What the hold guards against is the
swept state's **613**, an order of magnitude larger, which is where assertion 7's threshold
sits. The branch's own four extra texts over `main` are all 2017.

*Round 9/11 correction — the "linked in, where they count as committed" mechanism.* Round 9
attributed the old 111/85 to the 42 held-back payloads being *linked into the working tree*.
It is not: the gate's pair list comes from `git ls-files` (the index) and its bytes from
HEAD, so the working tree cannot enter either — two clones of the tip differing only by
those 42 files on disk print the identical line. Under the new rule this question does not
arise at all, because the pairing no longer depends on the index, the worktree, or a
directory order: it is read out of the committed pages' own `data-brief` attributes.

The swept state is the third row above, **613 over 88 payloads of 1,236 pairs**: with those
42 actually committed, the total jumps from 113 to 613 and the payload count from 87 to 88.
That is re-verified on the tip every round by committing the 42 into a clone and running the
gate unpiped: `[PAGE-PAYLOAD-OVERLAP] 88 of 1090 committed payload(s) show 613 … exit 1`
(`r11/r11_gate_swept.txt`). The threshold (250) sits between 113 and 613 in both readings,
so the fix does not change whether the hold fires — only what it prints.

The hold lives in `tools/check_dist.py` and is re-verified on **every run**: it applies
only while `check_selection` still exits non-zero for that year, it is scoped to a single
year, and if the tool cannot be run the exemption is refused (fails closed). Fix P0-1 and
`check_dist.py` starts demanding the rebuild by itself.

**The hold was a report line once, and that was not enough.** A commit on this branch used
`git add -A` and swept all 42 rebuilt payloads in — the exact drift the hold describes,
committed by the very change that was supposed to be enforcing it. It went unnoticed
because the hold only *printed*. Assertion 7 below is what it should have been: the
invariant is now measured against the committed tree, so no sequence of `git add` flags
can commit past it.

## The gate

    python3 tools/check_dist.py        # build, verify, exit 0/1

Seven assertions, each of which has been false at least once:

1. **IDEMPOTENT** — two consecutive builds produce byte-identical output. Without this,
   a diff after a build cannot be distinguished from generator churn. (It is
   deterministic: 331 pages + archive, `44 modified / 341 new` stable across runs — 42 of
   the 44 are the held-back 2016 payloads and the other two are `pipeline/state.json` and
   `sittings/index.html`.)
2. **FAITHFUL** — no tracked file under `site/dist` differs from a build of the current
   inputs, except the drift held back above. Files the generator does not own
   (`case-study.html`, `pipe-arch.html`, `spike-*.html`) are listed in `NOT_GENERATED`
   with their real builder, so "not generated" is a decision on record.
3. **NEW FILES** — a build introduces no uncommitted file. An untracked payload is a
   broken expander on the deployed site.
4. **NOTHING LOST** — no tracked file disappears.
5. **MIXED-GENERATION** — every committed sitting page carries `.dsec` and `.vslist`, the
   structures only the current `render_sitting` emits.
6. **INCOMPLETE-PAGE** — every committed sitting page carries every brief that
   `_meta.sitting_dates` places on it (excluding `group == "oral"`, which renders in the
   page's oral-answers section, exactly as `render_sitting` decides it).
7. **PAGE-PAYLOAD-OVERLAP** — no committed payload shares a sentence with a committed page
   that shows it: the body and the "N sentences hidden" expander. Threshold 250 against a
   measured residue of ~113; the swept-in drift was 613 site-wide (502 of it 2016). The
   pair is `(payload, every committed page carrying the brief)` and the magnitude is
   counted once per payload — see "The pairing rule" above, which round 11 rewrote.

Assertions 5 and 6 are deliberately both present, because they catch different pages.
Measured against the pre-fix tree (`main`, as this card was filed): **5 flags 24 of the
25 stale 2017 pages and 6 flags the 25th** — `2017-02-28` has current markup but 1 brief
of the **5** the data places on it (4 missing: `bill-288`, `bill-289`, `bill-290`,
`budget-905+907+911+913+915+919`; `rev_r3/probe_d6.py` → `rev_r3/d6_expectation.txt`). A
marker alone is not enough, and a *count* would be the wrong instrument twice over: the
assertion's test is a **set difference** (`want - have`), and on this page a count is
satisfied anyway — it carries one host section, for one brief. What fails it is that one
of the five it should carry is missing. On this branch all 331 pages pass both.

Assertions 5 and 6 read **HEAD**, not the working tree, and so does 7. This tool rebuilds
`site/dist` in place, so a marker test on the files it just wrote would pass by
construction — measured: with the build's output on disk, `2017-01-09` has 142 `.dsec`
while HEAD has 0. What Vercel deploys is what is committed, so committed bytes are the
only ones worth asserting on.

Assertion 7 is a function of the tree, and it took three attempts to make it one. The first
capped the sample at 400 by iterating a *set*, so it was in hash order and the same tree
measured 533 in one run and 30 in the next. The second sorted the pair *list* but built the
pairing with `setdefault` while iterating `glob.glob(...)`, so the page a payload was
compared against was whichever sitting date the filesystem returned first — the same tree
measured 106/107/108 (`main`) and 110/110/112 (this branch) as the directory order changed,
and `docs` quoted both readings as "two instruments, same committed bytes" when they were
one instrument on two machines. The third, current form pairs each payload with **every**
committed page that carries its brief and counts the texts once per payload, so there is no
reduction left to depend on an order: the output is byte-identical under natural, sorted and
reversed summary-directory order, on all four refs measured (`r11/verify_order.sh` →
`r11/r11_order.txt`). A gate has to be a function of the tree — of the *bytes*, and of the
whole of them, not of a sample or a first-in-directory-order choice.

Assertion 6 names **every** missing brief on each flagged page. It used to print a sample,
`m[:3]`, which on `main` silently dropped `budget-905+907+911+913+915+919` — the largest of
`2017-02-28`'s four missing briefs. The page count is what is bounded (5); the evidence
about each page is not, because that message is the only place the partial-rebuild defect
is legible.

The reproduction is `tools/check_fresh_clone.sh`, which runs the whole acceptance
criterion on a real clone and exercises the negative case of every rule it can.

Its two `check_artifacts.py` assertions are scoped to **the tree it is run against**, because
away from this branch they are not meaningful and the script used to report a misleading
FAIL (*review round 5, item 3*): on `main`, `tools/check_dist.py` does not exist at all (the
file's absence is now stated rather than asserted against), and `check_artifacts.py` yields
**11** problems rather than 3 — the 11 identical `[ACCOUNTING]` lines are precisely what the
census substitution removes, so 11 is the right answer for a tree that does not carry it.
Measured on `main` at `9fb0f545`: exit 1, 11 `[ACCOUNTING]` problems, 0 `STALE`
(`r8/ca_main.txt`). The positive case of the staleness rule is likewise skipped, with a
note, where the mtime rule it replaces is still in place — a tree without the fix cannot
exercise the fix.

## What a fresh clone does now

    git clone … && cd …
    python3 site/build_site.py     # 0 differences against what is committed
    python3 tools/check_dist.py    # exit 0
    python3 tools/check_artifacts.py   # exit 1, with 3 problems — see below

`check_artifacts.py` was 11 problems on a fresh clone, then 3. All 11 were caused by
the check, not the corpus: it counted items by globbing the **gitignored**
`pipeline/dataset/<year>/` payload directories, which a clone does not have, so every
year reported `349 published+withheld > 0 dataset items`. It now reads the committed
`pipeline/dataset/index.json` census.

*Review round 9 correction — what in this paragraph was wrong, and what "gitignored"
actually describes.* The acceptance table's literal row carried the sentence "3,507 not
built, all gitignored payloads" in its **`main` column**, where the 3,507 is correct;
what the sentence got wrong is the noun and the tree it quietly described. Both halves
were measured:

1. **The count belongs to `main`, and the branch's is different.** 3,507 is the payload
   count on `main` at `9fb0f545` — payloads `main` committed and cannot rebuild in a
   clone. On the delivered tip the same figure is **3,848** (`r14/where_out.txt`),
   because this branch committed the 341 payloads `main`'s rebuilt pages fetch. Both
   rows now name the tree, so the two numbers stop reading as one contradiction.
   Measured in a clone of the tip **with the dataset inputs linked in** (review round
   13's instrument, `r14/lit_branch.txt`; re-run at the round-13 tip in `r15/lit_final.txt`):
   **0 missing, 4,142 of 4,184 identical, 42 differing** — and the 42 differing are, file
   for file, the 42 held-back 2016 payloads (the differing set equals `git status
   --porcelain` minus the round-13 docs edit — while that edit was uncommitted it was the
   43rd path, and at the committed tip it is 42 of 42, `r15/check_eq.txt`). Without the
   inputs: **3,848 missing**, all under `site/dist/skipped/`, and the generator writes
   **0** payloads.
2. **The reason is not that the payloads are gitignored — they are tracked, and 0 of
   the 4,189 committed `site/dist` blobs is matched by `.gitignore`.** What is
   gitignored is their **input**: `pipeline/dataset/*/` (`.gitignore:26-28`). A clone has
   `pipeline/dataset/index.json` and no year directory, so `build_skipped_payloads`
   (`site/build_site.py:3826`) finds no source for any brief, `continue`s, and writes
   nothing. Verified by construction: link the year directories into the same clone and
   the build reproduces **all 3,848** payloads; remove them and it reproduces **none**.
   An earlier round-9 note in this file's neighbourhood attributed the absence to "the
   briefs were deleted upstream" — that is false too: all 3,864 committed summaries are
   present at HEAD (`r14/confirm_out.txt`).

So the row now reads: the clone-only build is absent **3,848 payloads whose generator
input (`pipeline/dataset/<year>/`) `.gitignore` excludes** — absent by input, not by
tracking, and every one of them is reproduced the moment the inputs are present.


*Review round 3 correction.* An earlier draft justified that substitution as "verified
equal to the payload dirs wherever those exist: 3,910 items". **That is true of a clone
and false of a long-lived checkout.** Measured three ways (`r4/probe_r4a.py`): a fresh
`python3 summariser/build_dataset.py --out <dir>` reproduces the index's set exactly —
11 years, **3,910** items, per-year id sets identical — and so does this worktree; but the
operator's main checkout holds **3,912**, because the generator never prunes and two
derived payloads have outlived the items they came from: 2017 `oral-answer-1819` (2,408 B)
and 2018 `president-address-14` (6,883 B), with no summary, no withheld record and no
entry in `index.json`, `state.json` or `data/`. So the substitute is exact for the case
this check runs in — a clone, and a clone is what was broken — and differs by 2 files on a
checkout that has been rebuilt repeatedly. `oral-answer-1819` is separately filed as a
defect by `442d49e`'s own commit message, so it is a known symptom rather than a new
finding. The substitution itself is unchanged and still the right call: with the payload
dirs linked in, the same run yields the same 3 problems (`r4/gate_dirs.txt`).

**It still exits 1.** The tool's own `return 1 if problems else 0` means "3 problems" and
"exit 0" cannot both be true, and an earlier draft of this document and the run summary
claimed "exit 0". That claim is withdrawn: what this card achieved is the card's
**second** acceptance branch — the remaining problems are enumerated and attributed
below, not eliminated. `tools/check_fresh_clone.sh` now asserts both the exit code (1) and
the problem count (3) on the clone, so the claim cannot drift away from the measurement
again.

The other two fixes were a genuine double-count (7 items recorded as both published and
withheld, so summing counted them twice — 2026's real total is 288, not 294) and a
`git checkout` mtime artifact (`git` writes files in path order within the same second,
leaving `status.json` 1–2s "older" than `summaries/2026/` on a clean tree).

### The 3 remaining problems, enumerated as accepted

| severity | finding | owner |
|---|---|---|
| `DOUBLE-COUNTED` | 2026: 6 items both published and withheld (`matter-adj-3012`, `motion-3008+3010`, `oral-answer-4055/4066/4067/4115`); 2023: `oral-answer-3339` | P0-5 — an editorial decision nobody has made |
| `ORPHAN` | 2015: `pipeline/withheld/oral-answer-784.json` — a withheld record for a year with no dataset items and no briefs, left by the reverted 2015 experiment (`438c810`) | data hygiene; deleting it would lose the record of why the item was withheld |
| (gate) | 2016 fails `check_selection` with 672 verbatim mismatches | P0-1, `t_608872bf` |

None of these is a build-faithfulness problem, which is what this card owns. Each is now
attributed rather than folded into an 11-line wall of identical `[ACCOUNTING]` messages.
