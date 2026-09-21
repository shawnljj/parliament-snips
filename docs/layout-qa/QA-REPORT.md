# Cross-breakpoint visual QA — column guides and scroll-spy rail

Task `t_28b23d7d`. Independent verification of the two implementation tasks
(`t_b764176e`, column guides; `t_140a0b87`, scroll-spy rail and heading preview) against
`docs/layout-audit/AUDIT.md` §4, defects **D1–D17**.

Everything below is measured on the **merged** tree, not on either parent branch. The two
branches conflict in three files; the resolution is described in §1 and is itself a finding.

Reproduce with:

```
python3 site/build_site.py /tmp/qa-build          # regenerate dist from the generator
python3 -m http.server 8480 --bind 127.0.0.1 --directory site/dist   # merged
python3 -m http.server 8481 --bind 127.0.0.1 --directory <pristine>/site/dist

python3 tools/measure_layout.py  http://127.0.0.1:8480/sittings/2026-08-04.html \
        --out docs/layout-qa/after --prefix after --widths 1024,1280,1440,1920
python3 tools/check_column_guides.py http://127.0.0.1:8480/sittings/2026-08-04.html \
        docs/layout-qa/after
python3 tools/check_rail_geometry.py http://127.0.0.1:8480
python3 tools/check_rail_preview.py  http://127.0.0.1:8480 http://127.0.0.1:8481
python3 tools/compare_mobile.py      http://127.0.0.1:8480 http://127.0.0.1:8481 \
        390,740,759,760,761
python3 tools/qa_pixels.py           http://127.0.0.1:8480 http://127.0.0.1:8481
python3 tools/qa_console.py          http://127.0.0.1:8480
python3 tools/qa_defects.py          http://127.0.0.1:8480
python3 tools/qa_gates.py            http://127.0.0.1:8480 http://127.0.0.1:8481
python3 tools/qa_frames.py           http://127.0.0.1:8480 http://127.0.0.1:8481 \
        docs/layout-qa/before-after
```

---

## 1. Merge state — this had to be resolved before anything could be verified

`git merge-tree wt/t_140a0b87 wt/t_b764176e` conflicts in three files. Merged as
`8bc6300` on branch `wt/t_28b23d7d-qa`:

| file | conflict | resolution |
|---|---|---|
| `site/build_site.py` | `:root` token block, `.dsec` grid rule, `.wrap` | `--col-*` survives; `--content-max`/`--content-pad` kept as aliases of `--col-wrap`/`--col-pad`, so each of the four column numbers is stated exactly once |
| `site/dist/theme.css` | same three sites | regenerated from the generator, not hand-merged |
| `tools/measure_layout.py` | add/add | `--prefix` version, a strict superset (only the flag and its two uses differ) |

**The naming decision is not cosmetic.** The two branches named the same four values
differently (`--col-wrap`/`--col-pad`/`--col-sum`/`--col-gap` vs
`--content-max`/`--content-pad`/`--summary-col`/`--gutter`). Naively merging either side
would have left the surviving scheme defined once and referenced under a name that no longer
exists — every use of the dead name resolves to nothing and the property silently falls back
to the initial value, which for `grid-template-columns` is `none` and for `max-width` is
`none`. The aliases make that unrepresentable.

Verified after the merge: `python3 site/build_site.py <tmp>` regenerates a `theme.css`
byte-identical to the committed `site/dist/theme.css`, and the merge commit changes **no**
`site/dist` content file other than `theme.css` and `index.html` (the two files the parents
already had staged).

---

## 2. Defects D1–D17

Verdicts: **PASS** = measured fixed and re-measured here. **DEFERRED** = not fixed by either
task, with the merged tree re-measured so the deferral is a statement about the current
build, not a claim carried over from a parent's summary.

### Fixed, and re-verified on the merged tree

| # | Defect | Merged-tree measurement |
|---|---|---|
| **D1** | rail anchored to viewport, overlapping the summary column by 209/99/19px and drifting 221px clear at 1920 | **PASS.** Dot clearance from the content's right edge is exactly **10.0px at all four widths** (1024/1280/1440/1920); rail box clearance 8px (the 2px is `--rail-pad`, so the dot is at 10). No overlap at any width |
| **D2** | z-index literals (rail 70, bubble 90, rail-go 91) all above the progress bar (44) | **PASS.** One ascending token set in `:root`; on desktop the rail measures **z-index 24**, bubble/go **26**, both now *below* `.totop` (40) / `.pbar` (44) / `.resume` (45) |
| **D3** | the 9 tick dots do not form a column (75.2px spread) | **PASS.** `dotXs=[1014]` at 1024, `[1160]` at 1280, `[1240]` at 1440, `[1480]` at 1920 — **1 distinct x, spread 0.00px** at every width |
| **D4** | progress fill 95px off the dot column | **PASS.** fill centre − dot column = **0.0px** at all four widths |
| **D5** | 468px rail box around 188px of content, centred on the viewport | **PASS.** box **208px** against a 188px track (188 + 2×10 of chrome), no longer viewport-relative |
| **D7** | `.dsec` has no vertical boundary | **PASS.** Four 1px rules drawn on the grid's own tracks: at 1440 the rule columns land at x = **214, 776, 810, 1226**, against measured column edges textLeft 214 / textRight 776 / sumLeft 810 / sumRight 1226 — exact at all four widths. Frame diff shows changed pixels *only* on those four columns (804/804/680/696 px per rule line) |
| **D8** | summary column has no boundary above/below its card | **PASS.** Same rule pair; bounded per `.dsec` (first section 2311px of a 211 323px document), so it stops where the columns stop |
| **P1** *(not in the audit's numbering)* | heading preview wrong in 32/32 sampled states (bubble ~216px below the tick it named, drawn over the rail, parts inverted), caused by the rail's `transform` making it the containing block for its own `position:fixed` children | **PASS.** `check_rail_preview.py` with real CDP mouse input: **worktree 0 failing of 32 sampled states** at 1024/1280/1440/1920; the pristine build fails **32 of 32** on the same instrument | — |

### Deferred, with the deferral re-measured

| # | Defect | Merged-tree state | Why deferred |
|---|---|---|---|
| **D6** | `min-width:760px` (grid) and `max-width:760px` (phone) both match at exactly 760px; reading column collapses to **204px** | **STILL PRESENT.** `check_column_guides.py` at 760px: `display=grid cols=204px 416px`, guide drawn. At 759: `display=block`, no guide. Both parents state they sidestep rather than fix it | Neither implementation task owns the breakpoint decision. It is a design choice (which side of 760 the layout switches on), not a defect with one correct fix — it needs an owner's call. **Consequence stated explicitly:** the guides are drawn by the grid, so at exactly 760px a guide appears over a genuinely broken 204px column. Suppressing it there would make the guide lie about the layout. This is the honest rendering |
| **D9** | headings are full-bleed across 1012px, not on the column grid | **STILL PRESENT.** 5 of 6 sampled headings have `right == wrap content right` (x=214 w=1012 right=1226 at 1440); the `.method h2` outlier is still indented (x=245 w=950) | Out of scope for both tasks; `t_b764176e` states D9/D10/D11/D17 were deliberately not touched. Not a regression — byte-identical to the audit's numbers |
| **D10** | `.railhead` has no CSS rule of its own; a section heading and a nested group heading render identically | **STILL PRESENT.** `.railhead` occurs **0 times as a selector** (the single grep hit is inside a comment); measured `fontSize 24px / lineHeight 28.8px / fontWeight 700 / color rgb(20,24,29)` for both "What the Government is doing" and "Bills" | Same as D9 |
| **D11** | `.sec-head` is dead CSS | **STILL PRESENT.** Defined in `theme.css`; **0 of 332** generated pages contain `class="sec-head"` | Same as D9 |
| **D12** | `.vs-text{max-width:74ch}` never binds on desktop | **STILL PRESENT.** resolves to 745.78px against a 562px column — dead on desktop | Same as D9 |
| **D13** | `.totop` overlaps `.pbar` by 44×10px, and the bar wins the hit test | **STILL PRESENT, and now measurable precisely.** At 1024/1280/1440/1920: totop bottom **884**, pbar top **874**, overlap **10px**, `elementFromPoint` at the overlap returns **`SPAN.pbar-txt`** — the bar still eats the bottom 10px of a 44px touch target | Same as D9. This one is a real, reproducible interaction bug (a click on the lower 10px of the button hits the progress bar) and is the strongest candidate for a follow-up card |
| **D14** | `.resume` bottom is set only inside `min-width:761px` | **STILL PRESENT, latent.** Base `.resume{bottom:16px}` (`theme.css:911`) sits under the 26px bar; only `@media (min-width:761px){.resume{left:auto;right:16px;max-width:380px}}` (`:920`) repositions it — and it does **not** set `bottom`, so the desktop toast is also at 16px, i.e. behind the bar. Not observable at any width tested: the toast requires a persisted scroll position and did not render in any probe | Same as D9. Note the audit's own wording ("base rule `bottom:16px` would put it behind the bar") is accurate but the **desktop** rule does not fix it either |
| **D15** | the archive page renders a progress bar with nothing to measure, reporting a false 0% | **STILL PRESENT.** `/sittings/index.html` at 1280: `data-page` **absent**, `.pbar` **display:block** reading **"0%"** / `aria-valuenow="0"` both before and after scrolling to the bottom. `.totop` and `.section-rail` are correctly absent there | Same as D9 |
| **D16** | 171 of 172 sections contain a bare `<li>` outside any `<ol>` | **STILL PRESENT.** `</ol><li` occurs **171 times** on one sitting page | Same as D9 |
| **D17** | 9 headings, 9 ticks, all `level-2`; `.level-3` unreachable | **STILL PRESENT.** the only level assignment in `railCollect()` is `var level = 2` | Same as D9 |

**Summary: 8 of the audit's 17 defects fixed (D1–D5, D7, D8), 9 explicitly deferred (D6,
D9–D17), plus 1 defect the audit could not see (the heading preview) found and fixed by
`t_140a0b87`. No defect is silently absorbed, and every deferral was re-measured on the
merged tree rather than quoted from a parent's handoff.**

---

## 2b. Follow-up cards filed

Five cards were created from this QA run, each assigned to `dev2-parsnips` and parented to this
task (so they stay in `todo` until this card closes). None of them is part of the column-UI
change; all are pre-existing defects or merge hygiene that this run measured and deliberately
did not absorb.

| card | what | why it is its own card |
|---|---|---|
| **t_2a44f62f** | D13: the back-to-top button's lower 10px is swallowed by the progress bar | A one-line fix in `.totop`/`.pbar` with no design decision attached — the cheapest real bug on the list |
| **t_bb9c77d0** | D6: unify the 760px breakpoint | Needs an owner's decision on which side the layout switches; no card in this batch owned it, which is why both parents deferred instead of guessing |
| **t_79a0e9e2** | `site/dist` is 27 files out of sync with its own generator (25 stale 2017 archive pages) | A content-pipeline problem, not a layout one — the deployed archive genuinely lacks published briefs, but folding that into a CSS commit would be unreviewable |
| **t_2bd5daf9** | D15: the archive page renders a progress bar reading a false 0% | Needs a product decision (gate the component at its emission site, or give the archive a real metric) |
| **t_a15acd97** | D9–D12/D14/D16/D17: the remaining audit items (headings off the column grid, dead `.railhead`/`.sec-head`, the latent `.resume`/`.pbar` coupling, invalid list markup, flat tick hierarchy) | A coherent batch of "the page's own outline and chrome do not know about the columns"; each is small, none is a regression |

---

## 3. Acceptance criteria, including the ones this repo cannot meet as written

### "Desktop widths 1024/1280/1440/1920: column rules aligned"

`tools/check_column_guides.py` → **exit 0**, `PASS`. Geometry at every width: tracks
`562px 416px` (526px at 1024), gap 34px, rule width 1px. Alignment exact:

| width | textLeft | textRight | sumLeft | sumRight | rule columns seen in the frame diff |
|---|---|---|---|---|---|
| 1024 | 24 | 550 | 584 | 1000 | 24, 550, 584, 1000 |
| 1280 | 134 | 696 | 730 | 1146 | 134, 696, 730, 1146 |
| 1440 | 214 | 776 | 810 | 1226 | 214, 776, 810, 1226 |
| 1920 | 454 | 1016 | 1050 | 1466 | 454, 1016, 1050, 1466 |

### "No horizontal overflow"

**PASS at every width.** `measure_layout.py`: `overflowX=False` at 1024/1280/1440/1920.
`qa_console.py` sweeps the whole document in 5% steps at 390/759/760/761/1024/1280/1440/1920:
`overflowAt=never` at all eight.

### "Sticky elements behave on scroll"

**PASS.** `check_column_guides.py` scroll sweep, 29 positions over 216 734px: *overflow at 0
positions; guides lost at 0; rail not fixed at 0;* the summary card pinned inside its own
section at 5 positions. `qa_console.py` separately checks the sticky top bar at every sweep
step: `topBarShift=none` at all eight widths. `check_rail_geometry.py` preview sweep with real
mouse input: 8/8 sampled preview states OK at each of 1024/1280/1440/1920.

### "Light and dark themes both correct" — **N/A, criterion cannot be met**

There is **no dark theme in this codebase.** One `:root` token set (`theme.css`); grep for
`prefers-color-scheme` and for `data-theme` across `site/` returns nothing for the reading
view. The only `color-scheme` hits are the standalone `pipe-arch.html` and `case-study.html`,
which are not the reading view. The audit reached the same conclusion (§7). Reported as N/A
with that evidence rather than passed silently.

### "Run the project's lint, type-check and test suite" — **N/A as written**

There is **no lint config, no type-check and no test suite** in this repo. `tools/` holds
standalone checkers. What was actually run, and what each returned, including on the pristine
pre-change build so a pre-existing failure cannot read as a regression:

| tool | merged | pristine | note |
|---|---|---|---|
| `tools/check_artifacts.py` | **1** | **1** | 11 `[ACCOUNTING]` problems, identical set on both — caused by gitignored `pipeline/dataset/*/` payloads counting as 0 in a checkout. Pre-existing |
| `tools/check_selection.py` | **1** | **1** | pre-existing, identical: `IndexError` — it requires a directory argument (`sys.argv[1]`), i.e. it is a per-batch inspection tool, not a repo-wide gate |
| `tools/check_year.py` | **1** | **1** | pre-existing, identical: `NOT CORRECT YET — no current-pipeline briefs to check; 291 of 291 items still to summarise` |
| `tools/check_rail.py` | **0** | — | pre-existing rail gate, green |
| `tools/qa_console.py` | **0** | (below) | new in this task |
| `tools/qa_defects.py` | **0** | — | new in this task |
| `tools/qa_pixels.py` | **0** | — | new in this task |

`python3 -m py_compile` on `site/build_site.py` and every touched tool: OK.

The task's acceptance wording "test suite green" is therefore **unmeetable as written** on this
repo, and the three project gates that exit 1 do so identically before and after the change.

### "No new console errors"

**PASS, and the baseline is clean too.** `qa_console.py` installs its collector before the
document loads, scrolls the whole page, drives all 9 rail ticks with real CDP mouse input, and
clicks `.totop`: **0 errors, 0 unhandled rejections, 0 console.error, 0 console.warn at every
width** (390/759/760/761/1024/1280/1440/1920). The pristine build was run on the same widths
and is equally silent on all of them, so this is a genuine "no new errors" rather than an
insensitive probe.

### "Capture an after screenshot set matching the audit's before set"

`docs/layout-qa/after/after-<w>.png` and `after-<w>-scrolled.png` at 1024/1280/1440/1920,
captured by the audit's own `tools/measure_layout.py` at 900px viewport height — same shape,
same harness, same page (`/sittings/2026-08-04.html`), same scroll fraction (25%) as
`docs/layout-audit/before-*`. Raw measurements beside them in `measure-<w>.json` and
`measurements.json`.

The audit's before set is desktop-only. The card also asks about tablet and mobile, so
`docs/layout-qa/before-after/` adds the missing frames **from both builds** at 390/759/760/761
(top and scrolled), which is what makes "unchanged" inspectable rather than asserted.

---

## 4. Regression check

### "Nothing else moved" — mechanical, not visual

`tools/compare_layout.py` on the audit's own `measurements.json` vs the merged run: **516
differences**, and classifying them by element shows **all 516 are in the rail system**
(`rail`, `railTicks`, `railMarks`, `railTrack`, `railFill`, `railName`, `railBubble`,
`railGo`, `railPill`) — the declared work of `t_140a0b87`. **`columns.*` differences: 0.
Non-rail differences: 0.** The guides are pseudo-elements and are not in the audit's field
set at all, so they cannot hide in this comparison; their presence is asserted by
`check_column_guides.py` instead.

### Tablet and mobile unchanged — at two levels of evidence

1. **Computed style and rect, whole DOM** (`tools/compare_mobile.py`, 5 456 elements at phone
   widths, 12 500 at grid widths): **390 / 740 / 759 / 760 px IDENTICAL — every element's
   computed style and every rect match.** At 761px 49 rects and 245 computed properties differ
   across 22 properties, all inside `section-rail*` — the desktop rail arriving by design.
2. **Pixels, both real dists** (`tools/qa_pixels.py`, self-check 0px between two captures of
   the same page): **390 → 0 / 329 160 px. 759 → 0 / 640 596 px.** Then 760 → 866 px,
   761 → 8 940 px, 1024 → 9 205 px, 1280 → 12 061 px, 1440 → 14 505 px, 1920 → 14 778 px — the
   guides rendering.

This matters because a rect-only check is not sufficient: the audit's successor found 37
computed-style differences reaching the phone on an earlier pass of this work with **zero**
rect differences. Computed styles are compared here, not just rects.

### The phone end of the breakpoint, explicitly

759 and 390 are pixel-identical, and 760 is where D6 still breaks. The breakpoint itself is
untouched by this change — which is the deferral's whole point — but the guides *are* drawn at
760 and not at 759, so the two builds differ there by 866 px (4 rules × 160px of visible
section, as `t_b764176e` predicted). That difference is the guides appearing over a broken
column, not a new break. Noted rather than hidden.

---

## 5. Findings and follow-ups

1. **The token collision was real and would have silently broken the layout if merged
   naively.** Both branches defined the same four column values under different names in the
   same `:root`. Resolved with `--content-max`/`--content-pad` as aliases (§1). Any future
   branch touching the column model should extend `--col-*` rather than adding a parallel set.
2. **`tools/compare_renders.py` and `tools/qa_frames.py` hardcode the worktree path.**
   `compare_renders.py` has `ROOT = "/Users/shawnlin/parsnips/.worktrees/t_b764176e"` at line
   28, and `qa_frames.py` imports `SETTLE` from it. On the merged tree the path is still valid,
   but the moment `t_b764176e`'s worktree is pruned the file breaks. Should be
   `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` like every other tool.
3. **`site/dist` on both branches is inconsistent with what the generator produces.**
   `site/build_site.py <tmp>` rewrites **27** files in `site/dist`: `pipeline/state.json`,
   `sittings/index.html`, and **25 of the 26** `sittings/2017-*.html` pages (the 26th,
   `2017-02-20.html`, differs only in `state.json`-adjacent content). The tracked copies are
   the **stale** ones — e.g. `2017-01-09.html` still reads "73 items of business" and lacks
   three published briefs (619 diff lines). Both parents flagged 26 stale pages and left them
   unstaged; the effect is that the deployed 2017 archive is out of date and, separately, that
   `site/dist` cannot be regenerated without a 27-file churn. This is a **content-pipeline
   issue, not a layout one**, and it needs its own card — do not fold it into a layout commit.
4. **D13 is the one interaction bug worth fixing next.** Measured on the merged tree: a click
   on the bottom 10px of the 44px back-to-top button hits `SPAN.pbar-txt` (the progress bar is
   z-44, the button z-40). Reproducible at all four desktop widths. One-line fix in `.totop`
   or `.pbar`; no design decision needed.
5. **D15 (archive page reads a false 0% the whole way down) needs a product decision, not a
   layout one** — gate `.pbar` at its emission site or give the archive a real metric.
6. **The rail's containing-block trap is worth a comment where it is now correct**, because
   any future `position:fixed` child of `.section-rail` inherits it: the rail's
   `transform:translateY(-50%)` makes the rail the containing block, so `right` resolves inside
   the rail and the JS's viewport-coordinate `top` must be converted. `t_140a0b87` documented
   this in the generator; it should survive the merge review.

---

## 6. Verdict

- **D1–D5, D7, D8, and the unlogged heading-preview defect: fixed and independently
  re-verified on the merged tree.** Rail dot clearance exactly 10px at all four desktop
  widths; 9 dots at 1 x, spread 0.00px; fill offset 0.0px; box 208px on a 188px track; four
  column rules landing exactly on the measured column edges; preview 0/32 failing against
  32/32 on the baseline.
- **D6, D9–D17: deferred, with the deferral re-measured** so each is a statement about the
  current build. None is worsened by this change.
- **No new console errors, no horizontal overflow, no horizontal layout shift at any of the
  eight widths tested.** Phone (390) and tablet (759) are pixel-identical to the pre-change
  build and identical in every computed style and rect across the whole DOM.
- **Two acceptance criteria are unmeetable as written** (dark theme; lint/type-check/test
  suite) and are reported as N/A with evidence. The three project gates that exit 1 do so
  identically on the pristine build.
- **One unresolved merge artifact to keep an eye on:** the `--content-max`/`--content-pad`
  aliases are a deliberate choice to keep the rail's coordinate system readable; if a later
  change removes them, `--rail-inset` stops resolving and the rail falls back to the viewport
  edge — exactly the D1 defect.
