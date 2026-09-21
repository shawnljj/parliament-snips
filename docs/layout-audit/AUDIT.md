# Desktop column layout audit — Parsnips reading view

**Prepared:** 2026-09-21 · **Task:** kanban `t_2264d837` (child of `t_a1329966`)
**Page measured:** `/sittings/2026-08-04.html` (**172 `.dsec` sections, 9 rail ticks** — a
mid-size page: the corpus runs from 0 to 823 sections, and 760px-plus pages are common).
Cross-checked against `/index.html` (latest) and `/sittings/index.html` (archive).
**Method:** headless Chrome over CDP at 1024/1280/1440/1920 (+ a 760–2560 sweep). Every
figure below is a measured `getBoundingClientRect()` value, reproducible with
`python3 tools/measure_layout.py <url> --out docs/layout-audit`. Raw JSON:
`docs/layout-audit/measurements.json`; screenshots `docs/layout-audit/before-*.png`.

No dark theme exists in this codebase. There is exactly one theme (`:root` tokens,
`theme.css:178-188`), so any "works in both themes" requirement in the child tasks is moot —
verify against the light theme only.

---

## 1. Where the layout lives

Everything is generated. There is **no separate CSS file to edit** — the stylesheet is a
Python string that the generator writes out verbatim:

| What | Source of truth | Note |
|---|---|---|
| **The whole stylesheet** | `site/build_site.py` → `STYLE = """…"""` (starts line 2356) | verified byte-identical to the emitted file |
| Emitted stylesheet | `site/dist/theme.css` (894 lines) | written by `build_site.py:3371`; a build artifact |
| Page markup | `site/build_site.py` → `render_sitting()` (line 1926) | the reading view |
| Client JS (scroll-spy, rail, progress bar) | `site/build_site.py` → `SCRIPT` (starts line 456) | |
| Design intent | `design/DESIGN.md` | |

**Edit `site/build_site.py`, then rebuild.** Editing `site/dist/theme.css` directly is lost on
the next build. `site/dist/` **is** tracked (it is what Vercel deploys — see `.gitignore`), so
a rebuild must be committed.

The `.dsec` two-panel grid and the section rail CSS also exist duplicated in
`site/spike-panels.html`, `site/case-study.html` and the other spike pages. Those are
standalone experiments with inlined CSS — **not** part of the shipped reading view. Do not
edit them; do not be misled by them.

---

## 2. The column model

### Container

```
.wrap { max-width:1060px; margin:0 auto; padding:0 24px }    theme.css:193
```

`<main class="wrap">` and `<header class="top"> .wrap` share it. The content box is therefore
`min(1060, 100vw) − 48px`, centred — i.e. **1012px wide at every viewport ≥ 1108px**.

### The reading grid — `.dsec`

```
@media (min-width:760px){
  .dsec{display:grid;
        grid-template-columns:minmax(0,1fr) minmax(300px,26rem);
        gap:34px; align-items:start;
        scroll-margin-top:calc(var(--topbar-h) + var(--sticky-gap) + 8px)}
  .sumcol{display:block;grid-column:2;grid-row:1;align-self:start;
          position:sticky;top:calc(var(--topbar-h) + var(--sticky-gap));
          height:fit-content;
          max-height:calc(100vh - var(--topbar-h) - var(--sticky-gap) - 20px);
          overflow-y:auto;overscroll-behavior:contain}
  .sumwrap{position:static;top:auto}
  .dsec > .vslist,.dsec > .gapd,.dsec > .gapbody{grid-column:1;grid-row:1}
  .vs-text{max-width:74ch}
  .sumtext{font-size:14px}
}
```
(`build_site.py:2511-2527`; identical at `theme.css:160-176`)

### Resolved geometry (measured)

| Viewport | `.wrap` box | text column | gutter | summary column | summary width |
|---|---|---|---|---|---|
| 1024 | 0 … 1024 | 24 … 550 (526px) | 550 … 584 (**34px**) | 584 … 1000 (416px) | 416px |
| 1280 | 110 … 1170 | 134 … 696 (562px) | 696 … 730 (**34px**) | 730 … 1146 (416px) | 416px |
| 1440 | 190 … 1250 | 214 … 776 (562px) | 776 … 810 (**34px**) | 810 … 1226 (416px) | 416px |
| 1920 | 430 … 1490 | 454 … 1016 (562px) | 1016 … 1050 (**34px**) | 1050 … 1466 (416px) | 416px |

So the column model is:

- **Two columns**, `1fr` (text, the reading column) + `416px` (summary rail) — the summary's
  `minmax(300px,26rem)` resolves to its 26rem maximum (416px) at every desktop width.
- **Gutter: 34px**, the grid `gap`, invariant across the desktop range.
- **Mechanism: CSS Grid**, `grid-template-columns`, on `.dsec`. Not flex.
- The text `1fr` track is **562px at ≥1160px** and shrinks below that; it is `526px` at 1024px.
- Text measure is additionally capped by `.vs-text{max-width:74ch}` → resolves to
  **745.78px**, which is **wider than the 562px column**, so it never binds on desktop. It is
  dead CSS on desktop (it binds only in the 740–1010px range) — harmless, but it is not
  holding the measure, the grid is.
- **No `--col-*` / `--gutter` custom property exists.** 1060, 24, 26rem, 34px and 74ch are all
  hard-coded at their use sites. This is the single biggest obstacle to the child tasks: there
  is no shared token to snap a rule or a rail onto.

### Breakpoints

The layout changes at **760px** and **761px**, and they disagree with each other:

| Breakpoint | Query | Effect |
|---|---|---|
| `min-width:760px` | `theme.css:160` | two-panel grid arrives; `.sumwrap` sticky→static; summary card `14px` |
| `min-width:700px` | `theme.css:149` | type bumps: brief title 22px, `.vs-text` 16px |
| `max-width:760px` | `theme.css:564, 805` | the **phone** block: rail gutter, type scale down, padding down |
| `min-width:761px` | `theme.css:754, 767` | desktop rail (`right:10px`, names shown) and the bottom progress bar |

**At exactly 760px both `min-width:760px` and `max-width:760px` match** (measured:
`matchMedia` returns true for both). The result is the two-panel grid *and* the phone rail
gutter at the same time:

```
760px → .wrap padding   = 0px 62px 0px 16px   (phone gutter: right 62px)
        .dsec columns   = 204px 416px         (two-panel grid)
        text column     = 204px               ← narrower than at 759px (653px)
```

At 759px it is one column of **653px**. At 760px it becomes two columns and the reading
column **drops to 204px**, then *widens* again at 761px (263px) as the wrap padding relaxes.
So the reading column is at its narrowest — 204px, about 25 characters — at exactly one
viewport width. Also at 760px the h1 is still 34px (phone scale) while at 761px it jumps to
57px, because the type scale is keyed to 761 and the grid to 760. This is a real, if narrow,
defect and it is a direct consequence of two breakpoints that should be one.

---

## 3. Sticky / fixed element inventory (desktop)

Measured at 1440px. All values are resolved CSS, not the authored shorthand.

| Element | Selector | Position | `top` | `right` | z-index | Box (w×h) | Containing block / context |
|---|---|---|---|---|---|---|---|
| Sticky top bar | `header.top` | `sticky` | `0` | auto | **20** | viewport × 61 | `body`; height `--topbar-h:60px`, actual **61px** |
| Per-section summary card | `.sumcol` | `sticky` | **74px** (`= 60 + 14`) | auto | `auto` | 416 × fit-content | `.dsec` (the grid parent) — **172 instances**, one per section |
| ↳ its inner wrapper | `.sumwrap` | `static` (desktop) | auto | auto | 5 (mobile only) | — | `position:static;top:auto` overrides the mobile `sticky` |
| ↳ the visible card | `.sumcard` | static | — | — | auto | 416 × 124–166 | padding 11px 15px 12px; tallest measured **165.52px** |
| Scroll-spy rail | `nav.section-rail` | `fixed` | `50%` (`450px`) | **10px** | **70** | 223 × 468 (52vh) | viewport |
| ↳ rail track | `.section-rail-track` | `relative` | 0 | 0 | auto | 223 × 188 | inside the rail |
| ↳ progress fill | `.section-rail-fill` | `absolute` | 12px | — | auto | **2 × 444** | `left:50%` of the track |
| ↳ tick names | `.section-rail-name` | static | — | — | auto | up to 190 × 16 | `display:inline-block` on desktop only |
| ↳ heading preview bubble | `.section-rail-bubble` | `fixed` | 0 (placed by JS) | `10px` | **90** | ≤300 × 30 | viewport; `opacity:0` at rest |
| ↳ "Go" chip | `.section-rail-go` | `fixed` | 0 (placed by JS) | `10px` | **91** | 32 × 22 | viewport; `opacity:0` at rest |
| Back-to-top | `button.totop` | `fixed` | `bottom:16px` | `16px` | **40** | 44 × 44 | viewport; `visibility:hidden` until `.on` |
| Bottom progress bar | `.pbar` | `fixed` | `bottom:0` | `0` | **44** | viewport × 26 | desktop only (`min-width:761px`) |
| Resume toast | `.resume` | `fixed` | `bottom:38px` | `16px` | **45** | ≤380 wide | created by JS only when a saved scroll exists |

**Nothing here is broken by a transformed ancestor.** The probe walked every ancestor of
`.top`, `.sumcol`, `.section-rail` and `.totop` looking for `transform`, `filter`,
`overflow`, `contain`, `will-change` or `display:contents`. Every chain came back **empty** —
so every `position:sticky` above is genuinely sticky. (This matters because
`tools/check_rail.py` exists precisely because a sticky element silently no-op'd here once
before.)

### The right-hand margin, as one system — and where it breaks

The rail is `position:fixed`, anchored to the **viewport** (`right:10px`), while the content
is centred with `max-width:1060px`. Those are two different coordinate systems that only
coincide over a narrow band of viewport widths. Measured:

| Viewport | content right edge | rail left edge | **gap** | summary right | rail ∩ summary (x) | rail ∩ summary **text** |
|---|---|---|---|---|---|---|
| 1024 | 1000 | 791 | **−209** (overlap) | 1000 | **209px** | **194px** |
| 1280 | 1146 | 1047 | **−99** (overlap) | 1146 | **99px** | **84px** |
| 1440 | 1226 | 1207 | **−19** (overlap) | 1226 | **19px** | **4px** |
| 1600 | 1306 | 1367 | +61 | 1306 | 0 | 0 |
| 1920 | 1466 | 1687 | **+221** | 1466 | 0 | 0 |
| 2560 | 1786 | 2327 | **+541** | 1786 | 0 | 0 |

So the content and the rail **cross over** somewhere around 1500px. Below that the rail is
painted *on top of* the summary column; above it, the rail drifts hundreds of pixels away
into empty margin. There is no viewport where the rail sits in a gutter of its own.

Verified by hit-testing `document.elementFromPoint` inside the intersection: at 1024/1280/1440
the element returned is `BUTTON.section-rail-tick.level-2` — i.e. **the rail wins**, because
it is `z-index:70` and `.sumcol` is `z-index:auto`. At 1024px the intersection reaches 2px
into the card's *text* box. It is mostly the label background and the first/last tick that
land there, but it is an overlap by construction, and it is the thing that makes the
right-hand side read as floating.

### The rail's own internal misalignment

The rail box is 223px wide but its ticks are not aligned to each other:

- **Dots do not form a column.** Measured dot centres across the 9 ticks at 1280px:
  `1254, 1181, 1256, 1247, 1256, 1256, 1203, 1212, 1235` — a **75px spread**. The ticks are
  `justify-content:flex-end` flex rows, so each dot's x depends on how long that tick's label
  is. Nothing anchors them. This is the single most visible "misalignment" on the page: the
  dot column has a visible sawtooth.
- **The progress fill does not run through the dots.** `.section-rail-fill` is `left:50%` of
  the 223px track → measured x **1157.5**. The nearest dot column is at **1181** — the fill
  line sits **23.3px** to the left of the closest dot and **98px** from the right-hand dot
  column. The fill looks like an unrelated vertical line, and at 1024px it visibly passes
  through the labels mid-word (confirmed on the screenshot).
- **The rail box is half empty.** The rail is `height:52vh` = **468px** holding a track of
  **188px** — **280px** of empty box above and below. The rail is vertically centred on the
  viewport via `top:50%;translateY(-50%)`, so it is centred on nothing in particular relative
  to the content.

### Vertical-rhythm facts that a rule would expose

Sections are separated only by whitespace and a `30px` bottom margin:

- `.dsec{margin:0 0 30px}` — 30px between sections, no border, no rule.
- `.runfull .ctx` has `border-top:1px solid rgba(227,231,236,.7)`; `.vs` has
  `border-top:1px solid var(--line)`, `.spk` and `.supp` likewise. So the *page already uses
  horizontal hairlines extensively* — a matching vertical hairline is stylistically native,
  not a new idea.
- `.vs-text` is `15.5px/1.6` mobile and `16px/1.6` desktop (`theme.css:151`), but
  `.sumtext` is `14.5px/1.5` mobile and `14px/1.5` desktop (`theme.css:175`) — **the summary
  text is larger on mobile than on desktop.** Deliberate or not, it means the two columns have
  different type scales, which is part of why they read as two unrelated columns.
- `.sumcard` uses `padding:9px 12px 10px` (mobile) → `11px 15px 12px` (desktop), while
  `.sumcol` is a bare grid cell with no padding. The card's left edge and the summary
  column's left edge coincide (both 584 at 1024), so the accent `border-left:3px` on the card
  *is* the column's left edge — that is the only existing vertical boundary on the page, and
  it is 3px of accent green, per-card, restarting every section.

---

## 4. Numbered alignment defects

Each with the file/selector to change. "Target geometry" for the child tasks is stated at the
end.

**D1 — The rail is anchored to the viewport, not the content grid.**
`theme.css:754-755` `.section-rail{right:10px;top:50%}`. The rail overlaps the summary column
by 209px at 1024, 99px at 1280, 19px at 1440, and floats 221px away from it at 1920 and 541px
at 2560. *Change:* `.section-rail` in `build_site.py`. The rail needs to become part of the
content coordinate system (e.g. positioned relative to a `.wrap`-derived offset, or moved
into a third grid track) rather than `right:10px` of the viewport.

**D2 — The z-index stack makes the overlap a *cover*, not a *collision*.**
`.section-rail` z-index **70** vs `.sumcol` z-index **auto**. Confirmed by hit test: the rail
button is the top element inside the intersection at 1024/1280/1440. *Change:* establish a
documented z-index scale. Current values in use: top bar 20, `.sumwrap` 5 (mobile), rail-pill
2, totop 40, pbar 44, resume 45, rail 70, bubble 90, rail-go 91. 70/90/91 sit above the
progress bar and the toast, which is probably not intended.

**D3 — The rail's tick dots do not form a vertical column (75px spread).**
`.section-rail-tick{justify-content:flex-end}` + `.section-rail-name` growing to fit the
label, so the dot's x is a function of label length. Measured centres
`1254,1181,1256,1247,1256,1256,1203,1212,1235`. *Change:* make the tick a fixed-width row
with the dot at a fixed offset (e.g. the name in a fixed-width cell, the dot
absolutely positioned) so all 9 dots share one x.

**D4 — The rail's progress fill is not on the dot column.**
`.section-rail-fill{left:50%;width:2px}` → x 1157.5, i.e. 23.3px left of the nearest dot and
98px left of the right-hand dot column. It reads as a stray line, and at 1024px it crosses the
labels. *Change:* the fill must be positioned from the dot column, which requires D3 first.

**D5 — The rail's 468px box contains 188px of content and is centred on the viewport, not the
content.** `.section-rail{height:52vh;top:50%;translateY(-50%)}`. *Change:* size and position
the rail from the content it indexes; there is no relationship between 52vh and the section
positions it maps.

**D6 — Two breakpoints disagree: `min-width:760px` (grid) vs `min-width:761px` (rail/pbar) vs
`max-width:760px` (phone block).** At exactly 760px both the two-panel grid and the phone
gutter are live, and the reading column collapses to **204px**. *Change:* unify on one
desktop breakpoint. Note `theme.css:564` and `theme.css:805` are `max-width:760px` phone
blocks that also fire at 760.

**D7 — `.dsec` has no vertical boundary; sections are separated by 30px of whitespace only**
(`.dsec{margin:0 0 30px}`). This is the "words float in space" complaint, stated mechanically.
The page is otherwise full of horizontal hairlines (`.vs`, `.spk`, `.gapd`, `.runfull .ctx`,
`.supp`), so a hairline is the native idiom. *Change:* `.dsec` / `.wrap` in `build_site.py`.

**D8 — The summary column has no boundary above or below the card.**
`.sumcol` is a bare grid cell (`padding:0`, no border, no background); only the inner
`.sumcard` is visible, and only where a summary exists. When a section's card is short the
416px column is entirely blank whitespace — there is no column, only an occasional box.

**D9 — Headings are not on the column grid.** `.railhead`, `section.substance > h2`,
`section.oral > h2`, `section.everything > h2` are all full-bleed across **1012px** (the whole
wrap) at left **134/214/454** — they span both the text column and the summary column. Measured
heading lefts at 1440: `214, 214, 214, 214, 214, 214, 214, 214, 245`. The "How this page was
made" heading is **+31px** because it sits inside `.method` (`padding:28px 30px`), so it is the
one heading that does not share the left edge. So: full-width headings, one indented outlier,
and then 172 sections that *are* gridded — three different alignment stories on one page.

**D10 — `.railhead` has no CSS rule of its own, so two distinct heading levels render
identically.** It is emitted as `<h2 class="railhead">` (`build_site.py:2087`) and matched only
by the generic `h2` rule — there is **no `.railhead` selector anywhere in `theme.css`**
(count: 0). Measured, "What the Government is doing" (the section heading) and "Bills" (a
group heading *underneath* it) both compute to `font-size:24px; line-height:28.8px;
font-weight:700`, and their boxes are 29px tall, 5px apart, with no rule, no spacing and no
colour difference between them. The page's own outline is therefore flat where the markup says
it is nested. If the child tasks draw column rules, this flat pair will read as a single
block of chrome — give `.railhead` a treatment (it is the natural place for the first vertical
rule's anchor).

**D11 — `.sec-head` is dead CSS.** `theme.css:499-501` defines `.sec-head{margin:0 0 22px}` and
`.sec-head h2{font-size:26px}`, but **no generated page contains `class="sec-head"`** (grep
across all 331 pages: zero hits). It looks like the intended section-heading style; the live
markup uses bare `<h2>`. If the child tasks want a section-heading treatment, this is where it
was supposed to live.

**D12 — `.vs-text{max-width:74ch}` never binds on desktop.** It resolves to 745.78px against a
562px column. Dead on desktop; it only binds between roughly 740 and 1010px.

**D13 — the back-to-top button is 10px under the bottom progress bar.**
`.totop{bottom:16px}` (h=44 → occupies 840…884) vs `.pbar{bottom:0;height:26px}` (874…900):
**44×10px of overlap**, and the hit test at that overlap returns `SPAN.pbar-txt` — the bar is
z-44, the button z-40, so the *bar wins* and eats the bottom 10px of the 44px touch target.
Measured identically at 1280 and 1440. *Change:* `.totop{bottom:…}` at `theme.css:730`, or the
`.pbar` block at `theme.css:767`.

**D14 — the `.resume` toast is also under the progress bar.** `.resume{bottom:38px}` is only
set inside the `min-width:761px` block (`theme.css:776`), and the base rule is
`bottom:16px` (`theme.css:741`), which would put it *behind* the 26px bar. At `≤760px` the bar
is `display:none` so this is harmless, but the coupling is implicit. Confirm when touching D13.

**D15 — the archive page renders the desktop progress bar with nothing to measure.** On
`/sittings/index.html` the `.pbar` is present and `display:block` at 1280, but there is no
`data-page` on `<body>` and therefore no scroll memory and no `totop`. Measured: after
scrolling to the very bottom, the bar still reads **`0%`** with `aria-valuenow="0"` — the
percentage is computed from `scrollY / (scrollHeight − innerHeight)`, and the archive's
`pbarSync()` never fires because `parsnips:scroll:` state is not written for a page with no
`data-page`. It is a sitting-page component leaking onto a non-sitting page and reporting a
false 0% the whole way down. *Change:* the `.pbar` block is emitted from `render_archive()` at
`build_site.py:2342` (vs `:2245` for the sitting page) — gate it there, the same way
`.section-rail` is correctly absent on the archive.

**D16 — 171 of 172 sections contain a bare `<li>` outside any `<ol>`.**
`.dsec` children are `[div.sumcol, ol.vslist, li.gapi.run]` — the trailing gap marker is a
sibling `<li>` of the `<ol>`, not a child. It is invalid list markup, and it is also an
auto-placed grid child (its computed `grid-column` is `auto`). It happens to land in column 1
today, but only because the explicit `grid-column:1` rules on `.vslist`/`.gapd`/`.gapbody`
leave it nothing else to do. *Change:* `render_brief_selected()` in `build_site.py` — move the
trailing gap marker inside the `<ol>`, or add it to the `grid-column:1` rule list.

**D17 — 9 headings, 9 ticks, all one level.** `railCollect()` sets `var level = 2` for every
tick (`build_site.py:2087` region, ~line 677). The `.level-3` CSS rules exist
(`theme.css:759`) but are unreachable. Cosmetic, but it means the rail cannot express
hierarchy, so a vertical rule will make the uniform tick column more conspicuous, not less.

---

## 5. Target column geometry (the numbers the child tasks should hit)

Any vertical rule must be derived from these, not hard-coded:

| Token (to be introduced) | Value | Used by |
|---|---|---|
| content max-width | `1060px` | `.wrap` |
| content padding | `24px` | `.wrap` |
| **text column** | `1fr` (562px at ≥1160px) | `.dsec` track 1 |
| **summary column** | `416px` (`26rem`) | `.dsec` track 2 |
| **gutter** | `34px` | `.dsec` gap |
| reading line | `74px` = `--topbar-h`(60) + `--sticky-gap`(14) | `.sumcol` sticky top |
| anchor line | `73px` = `--sticky-h`(61) + 12 | `scroll-margin-top` everywhere |
| rule colour | `var(--line)` = `#e3e7ec` | already the page's hairline colour |

**Three rules are needed, not two**, because the summary card's own left edge
(`border-left:3px solid var(--accent)`) is already a boundary and is *inside* the gutter's
right side:
1. left edge of the content = `wrap.left + 24` (at the text column's left)
2. the gutter's centre-ish boundary between text and summary — the audit's evidence puts the
   text column's right edge at `text.right` and the summary's left at `summary.left`, 34px
   apart
3. right edge of the content = `wrap.right − 24` (the summary column's right)

**A note on the anchor line that the child tasks will trip on.** Two different values are live
and both are correct today: `.dsec`'s own rule gives **82px** (60+14+8) while everything else
gets **73px** from the `[id],.has-section-anchor` rule via `--sticky-h`. Measured, `.dsec` and
`.has-section-anchor` both currently resolve to **73px** because the `[id]` rule wins on
specificity for the real elements. The JS's `readingLine()` is `stickyH() + 12` = **73**. If a
child task touches `scroll-margin-top` it must preserve 73px, or scroll-spy jumps will land
one section early — the exact reported symptom the comment at `build_site.py:480` documents.

---

## 6. Screenshot set

`docs/layout-audit/` (captured at 900px viewport height):

| File | Contents |
|---|---|
| `before-1024.png`, `before-1280.png`, `before-1440.png`, `before-1920.png` | top of page at each width |
| `before-<w>-scrolled.png` | 25% down the page — sticky bar, pinned card, rail all live |
| `measurements.json`, `measure-<w>.json` | every rect, computed position, z-index and grid track |

Reproduce with:

```
python3 -m http.server 8435 --directory site/dist
python3 tools/measure_layout.py http://127.0.0.1:8435/sittings/2026-08-04.html \
        --out docs/layout-audit --widths 1024,1280,1440,1920
```

`tools/measure_layout.py` is new in this task. It is the same minimal-CDP harness style as the
existing `tools/check_rail.py` (no third-party dependency) and it is the tool the QA child task
should re-run against the after-state.

---

## 7. What the audit could not establish

- **No dark theme exists**, so the requirement in the child cards to check "both themes" cannot
  be satisfied as written. Recommend striking it or scoping it to the light theme.
- **Colour contrast of a new rule could not be evaluated at 1× DPR** in the headless capture.
  If a child task needs WCAG evidence for the hairline, capture at `deviceScaleFactor:2`.
- The **resume toast** was never observed on-screen (it requires a persisted scroll position in
  `localStorage`); its geometry is from CSS, not measurement.
- The `.section-rail-bubble` and `.section-rail-go` are `opacity:0` at rest and are placed by
  JS on interaction; the audit measured their resting boxes only.
