# t_140a0b87 — sticky scroll-spy rail and heading-preview alignment

Scope: the desktop right-hand rail (the tick column that indexes a page's sections), its
progress fill, its preview bubble, and the shared column/gutter tokens they and the content
columns both resolve from. Stylesheet source is `site/build_site.py` (`STYLE`,
`site/dist/theme.css` is a build artifact). This worktree also carries the audit's
`tools/measure_layout.py` and `docs/layout-audit/AUDIT.md` as its reference.

This file is the machine-readable evidence behind the card's acceptance criteria. Every number
below came from headless Chrome over CDP; nothing here is read off the CSS.

## How to reproduce

    python3 site/build_site.py
    python3 -m http.server 8442 --bind 127.0.0.1 --directory site/dist     # THIS tree
    # the pristine baseline for comparison, built from HEAD in a separate worktree:
    python3 -m http.server 8436 --bind 127.0.0.1 --directory site/dist

Then, in `tools/`: `check_rail_geometry.py` (audit defects D1/D3/D4/D5 + the live preview
gesture), `compare_mobile.py` (whole-DOM mobile diff), `check_rail_preview.py` (preview, per
build), `_scratch_zstack.py` (stacking decisions), `_scratch_shots.py` (the screenshot set),
`_scratch_behavior.py` (scroll stability).

**Do not use port 8435.** A stale `python3 -m http.server 8435` from the audit worktree
(`.worktrees/t_2264d837`) is bound to `0.0.0.0:8435` and shadows it; a probe against 8435 can
silently measure the audit stylesheet instead of this tree's.

## The audit's defects, before and after

Measured with the same instrument on both builds, at 900px viewport height, scrolled to the
middle of the page. "before" is a clean build of HEAD (`994a1df`).

| | 1024 | 1280 | 1440 | 1920 |
|---|---|---|---|---|
| **D1** baseline rail.x − content right (box) | **−209** | **−99** | **−19** | **+221** |
| **D1** after, rail box | +8 | +8 | +8 | +8 |
| **D1** after, dot outer edge | **10** | **10** | **10** | **10** |
| **D3** baseline dot-x spread | **75.2** | **75.2** | **75.2** | **75.2** |
| **D3** after | **0** | **0** | **0** | **0** |
| **D4** baseline fill − dot column | **−95.0** | **−95.0** | **−95.0** | **−95.0** |
| **D4** after | **0.0** | **0.0** | **0.0** | **0.0** |
| **D5** baseline rail box / track | 468 / 188 | 468 / 188 | 468 / 188 | 468 / 188 |
| **D5** after | 208 / 188 | 208 / 188 | 208 / 188 | 208 / 188 |

The audit's own headline numbers (D1: −209/−99/−19/+221 and crossing at ~1500px) reproduce
exactly on the baseline build, which is what validates the instrument.

- **D1** — the rail is now derived from the container, not the viewport. `--rail-right` is
  solved from `--rail-inset` (the wrap's margin + padding), `--rail-clear`, and the box width, so
  the dot's outer edge lands **exactly `--rail-clear` (10px) past the content's right edge at
  every width** instead of crossing zero at ~1500px. (The rail *box* reads +8 because `--rail-pad`
  is inside it; the token is measured to the dot, which is the thing a reader sees.) The previous
  "no overlap" at 1920 was a 221px drift into empty margin; both failure modes are gone. The 1920
  baseline's dot clearance of +350.8 is the clearest statement of the old bug: the dots floated
  350px away from the content they indexed.
- **D3** — the dot leads the row and the name is a **fixed width**, so no tick's dot depends on
  its own label. 9 dots, one x.
- **D4** — the fill rides the dot column; its centre is 0.0px from the dot centres. Note the base
  rule's `margin-left:-1px` already centres the 2px bar, so the desktop `left` must not subtract
  a further 1px.
- **D5** — the box hugs its content (`height:auto`) and is centred on the band it indexes
  (`--topbar-h` → `--pbar-h`) rather than on the raw viewport with a 52vh box holding 188px.

## The heading preview, measured while it is OPEN

The audit could only measure the bubble and Go chip at rest (`opacity:0`), so this is new
evidence. The preview is opened by the track's `pointerdown` → pointer capture → `railPreview()`
path; a synthetic `dispatchEvent` is untrusted and never fires it, so this drives real input via
`Input.dispatchMouseEvent` and drags down the track.

Baseline: **32 of 32** sampled preview states fail. After: **0 of 32** fail (64 sampled states
across 1024/1280/1440/1920). Baseline failures, per tick: `bubble centre 470.0 vs tick centre
254`, `bubble covers the rail`, `go chip above the bubble` — i.e. the preview was drawn ~216px
below the tick it named, on top of the rail, with its two parts inverted.

Root cause, and it is worth naming for anyone touching this next: **the rail's
`transform:translateY(-50%)` makes the rail the containing block for its `position:fixed`
descendants.** So
- `right:` on the bubble resolved *inside the rail*, not from the viewport — the bubble landed on
  the wrong side entirely;
- the JS wrote `top` in viewport coordinates, which were then applied against the rail's own box.

Fix: the pair is anchored `right:100%` (they grow leftwards into the gutter — the only direction
with room, now that the rail sits hard against the content edge) and `railPlacePreview()`
converts to the rail's coordinate space, clamping in viewport terms first.

## Mobile unchanged — proof, not assertion

`_scratch_mobile_full.py` walks **every element** in the document on both builds and compares the
full computed style and the rect. Not a selector sample: all 5,456 elements.

| width | elements | result |
|---|---|---|
| 390px | 5,456 | **identical** — 0 rect diffs, 0 computed-style diffs |
| 740px | 5,456 | **identical** |
| 759px | 5,456 | **identical** |
| 760px | 12,500 | **identical** |
| 761px | 12,500 | differs — desktop block activates here (see below) |

This took three passes to reach, and the first two are the interesting part: the initial run
showed 5,456 elements with **zero rect differences** but **37 computed-style differences** —
z-index on the rail and its preview, and `flex-shrink` on the 9 dots. Nothing moved on screen, so
a rect-only check would have called it clean. It was not: those properties really did reach the
phone's computed styles.

They were then scoped to desktop, which is the honest fix rather than accepting "visually
identical":
- **z-index** — the retune exists to stop the rail painting over the bottom progress bar (44) and
  the resume toast (45). Both are `display:none` on a phone, so retuning the rail's layer there
  bought nothing. Base `:root` now keeps the original 70/90/91; the desktop block sets 24/26/26.
  The scale is documented in one place, ascending, replacing four literals.
- **`flex:none` on the dot** — moved from the base rule into the desktop tick rule, where the
  fixed-width label is what makes it necessary.

761px is where the desktop block starts by design (`min-width:761px`), so a diff there is the
feature, not a regression. 760px is *inside* the phone block and is identical — see the note on
D6 below for why 760 is nonetheless a separate pre-existing problem.

## Stacking, checked as decisions rather than numbers

`_scratch_zstack.py` sweeps the overlap regions and compares which element **wins** at each, on
both builds. On mobile: identical winners in all 18 sampled regions. On desktop the only changed
decisions are the intended ones — at 1024/1280/1440 the baseline had the rail painting over the
summary column (`sumcolCovered=True`, winner `DIV.sumwho`/`DIV.sumcard`, z-index 70 over auto);
after, the rail no longer intersects it at all. The `rail-centre` winner changes from a page
element to the rail's own tick, which is the rail correctly becoming the top thing at its own
coordinates instead of being buried.

## Behaviour on scroll

`_scratch_behavior.py`, 12-step scroll sweep at each width: the rail's x and y are **constant**
(min = max at every width), so there is no drift or jump; `overflowX=False` everywhere; no
console errors. Hit-testing every dot centre returns the tick itself, and the summary column's
text is never intercepted by the rail.

## Z-index scale

Before: literals at four use sites — top bar 20, totop 40, pbar 44, resume 45, rail **70**,
bubble **90**, rail-go **91**. The last three sat *above* the progress bar and the toast.
After: one ascending token set in `:root`, with the rail's three retuned inside the desktop
block (phone values preserved). No literal z-index remains in the rail's rules.

## Column/gutter tokens introduced

`--content-max:1060px`, `--content-pad:24px`, `--summary-col:26rem`, `--gutter:34px` — the
audit's §5 column model, which was hard-coded at all four use sites. `.wrap` and the `.dsec`
grid now resolve from them, so the rail and the columns share one source. Rail geometry:
`--rail-clear`, `--rail-dot`, `--rail-dot-gap`, `--rail-pad`, `--rail-outer`,
`--rail-track-pad`, `--rail-inset`, `--rail-label`/`-cap`/`-derive`, `--rail-tick-min/-w`,
`--rail-box-w`, `--rail-right`.

**`--rail-label` is derived, not breakpointed.** `clamp(0px, <margin formula>, 180px)` — it is 0
where the margin cannot hold a label, so the rail is the bare dot strip on a phone and at 1024,
and grows continuously. The chip is `display:none` below 1200px rather than given a zero width,
which would leave its 4px of padding as an 8px sliver beside every dot. There is no width
constant for the label to disagree with the box width; both resolve from the same token.

## Pre-existing issues found, reported rather than absorbed

1. **D6 is not fixed and is out of this card's scope.** `min-width:760px` (grid) and
   `max-width:760px` (phone) both match at exactly 760px, collapsing the reading column to
   204px. Measured here: 760px renders 12,500 elements and is byte-identical between the two
   builds, so this work neither causes nor worsens it. The sibling card `t_b764176e` also
   sidestepped it and said so; unifying the breakpoint needs a decision (which block wins at
   760) that no card in this batch owns.
2. **`tools/check_artifacts.py` exits 1 on both builds**, with 11 `[ACCOUNTING]` problems
   (`<year>: N published+withheld > 0 dataset items`). Identical on the pristine baseline and on
   the audit worktree, caused by gitignored `pipeline/dataset/*/` payloads counting as 0 in a
   fresh checkout. Not a layout regression; `tools/check_rail.py` exits 0.
3. **There is no lint config, type-check, or test suite.** The card's "lint and existing tests
   pass" has no counterpart in the repo. What was run instead is listed at the end of this file.
4. **`--z-rail`'s original 70 put the rail above the bottom chrome** (D2); fixed here. But the
   same ordering question applies to `.totop` (40) vs `.pbar` (44) — D13, a separate audit
   defect, untouched by this card.

## Token collision with the sibling card (for the QA card)

`t_b764176e` independently introduced `--col-wrap/--col-pad/--col-sum/--col-gap` for the *same*
four values this card names `--content-max/--content-pad/--summary-col/--gutter`. Both branches
change the same `:root`, so whichever merges second will either duplicate the values or silently
win. They should be collapsed to one naming scheme at merge; the values agree exactly
(1060px / 24px / 26rem / 34px), so it is a rename, not a reconciliation.

## Checks actually run

    python3 -m py_compile site/build_site.py                       -> OK
    python3 site/build_site.py                                     -> 331 pages + archive
    python3 tools/check_rail.py                                    -> exit 0, PASS (13 scroll positions)
    python3 tools/check_artifacts.py                               -> exit 1, 11 pre-existing accounting problems
    python3 tools/check_selection.py / check_year.py               -> exit 1 on both builds, pre-existing
    python3 tools/check_rail_geometry.py <after> <before> 1024,1280,1440,1920
    python3 tools/compare_mobile.py <after> <before> 390,740        -> 0 differences
    python3 tools/compare_mobile.py <after> <before> 759,760,761
    python3 tools/check_rail_preview.py <after> <before> 1024,1280,1440,1920 -> 0/32 vs 32/32
    python3 tools/_scratch_zstack.py <after> <before>
    python3 tools/_scratch_behavior.py <after> 1024,1280,1440,1920
    python3 tools/_scratch_shots.py <after> <before> 1024,1280,1440,1920

## Screenshot set

`docs/layout-after/` — `after-<w>.png` (top), `after-<w>-scrolled.png` (mid-page, rail live),
`after-<w>-preview.png` (a tick held open, showing the bubble and Go chip). Matching
`before-<w>*.png` captured from the pristine build by the same script, so the pairs are
comparable frame-for-frame. At 1024px the label is off by design (the margin cannot hold one) and
the rail is the dot strip; at 1280/1440/1920 labels read 93px / 173px / 180px (the cap).
