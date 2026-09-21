# D15 — the archive page's progress bar: gated off

**Decision: option 1 — the archive does not get a progress bar.** The audit offered
"gate it" or "make it real"; gating is correct here, and the reasoning is recorded in
`render_archive()` beside the removal, where the next reader will look for it.

## The defect as measured, not as quoted

Reproduced on the pristine pre-change build at 1280 (`tools/check_pbar.py`, raw log
`raw-check_pbar-pristine.log`), sampling the bar at five scroll positions:

```
archive 1280px  .pbar present=True data-page=None script=False maxScroll=2770
  y=0      text='0%'  aria='0'  fill=''  display=block
  y=693    text='0%'  aria='0'  fill=''  display=block
  y=1385   text='0%'  aria='0'  fill=''  display=block
  y=2078   text='0%'  aria='0'  fill=''  display=block
  y=2770   text='0%'  aria='0'  fill=''  display=block
  VERDICT: FAIL -- renders and reads '0' at the foot
```

The mechanism the audit described is confirmed: the archive page has `data-page` absent
and **no `<script>` at all** (`hasScript: false`), so `pbarSync()` is never reached and
the bar keeps its server-rendered 0% for the whole scroll. `fill=''` — the fill element
was never given a width either.

## Why gate, not make it real

* The archive is not a piece of reading to make progress through. It is **2 770px of
  scroll at 1280 and 1440** (settled, `raw-qa_scroll_geometry.log`), and it is already
  grouped into year disclosures a reader opens one at a time.
* The page has no scroll behaviour to report on at all: no `data-page`, no `SCRIPT`, no
  scroll memory, no `.totop`, no `.section-rail`. Option 2 would mean writing scroll
  state for a page that has deliberately never had any.
* `.section-rail` and `.totop` are already correctly absent here. The archive had gated
  its chrome in one place and not another; this makes it consistent.

The element's own `aria-label` read **"Progress through this sitting"** on a page that is
not a sitting — a second wrong claim, removed with the element.

## Change

`site/build_site.py`, `render_archive()`: the 5-line `.pbar` block is gone, with a comment
stating the decision and why. That is the whole product change — nothing else in the
generator was touched.

## Verification

Every claim below is a logged run under `docs/layout-qa/d15/`, not an assertion.

| Claim | Tool | Result |
|---|---|---|
| The archive lost exactly that block and nothing else in the build moved | `qa_build_diff.py` vs the pre-change build in `../t_2bd5daf9-ref` | **3 847 of 3 848 files byte-identical**; the one change is `sittings/index.html`, and the new file equals the old with exactly the 5-line block excised (string equality, not a diff heuristic) |
| The page body did not move a pixel | `qa_d15_pixels.py` | **0 differing pixels** across 900 rows × 4 runs (archive and a sitting page, 1280 and 1440), chrome suppressed, both frames landing at the same scroll offset |
| The sitting page keeps its bar | `qa_defects.py`, `check_pbar.py` | present, `display:block`, **reads 0% at top and 100% at the foot** at 1280 and 1440 |
| No page anywhere renders a bar nothing drives | `qa_archive_pbar.py` | 339 pages scanned, **0** with the D15 shape; archive has none; **all 331** sitting pages keep theirs |
| The D15 check is not vacuous | `qa_defects.py` on the pre-change build | **FAILS** there with `reads '0%' / aria-valuenow='0' at the foot (scrollY=2770 of 2770)` at both widths; passes here |
| No console, overflow or layout-shift regression | `qa_console.py` | **exit 0** — silent console, no horizontal overflow, no top-bar shift at all 8 widths |
| The column guides still hold | `check_column_guides.py` | **exit 0** — every guide on a column edge, bounded, painted under the text |
| The phone/tablet frames are unchanged | `qa_pixels.py` vs the same build with the guide rules off, and `qa_pixels.py` at 390/759 against the pre-change build | **exit 0** both ways: 390 → 0 / 329 160 px, 759 → 0 / 640 596 px |

### The `qa_pixels.py` row, corrected

The first version of this report claimed `check_column_guides.py, qa_pixels.py — both pass` and
named a run of `qa_pixels.py` that is **not** a passing pairing. Reviewing it found that
`qa_pixels.py <merged> <pre-change build>` exits 1, and that it must: the pre-change build
**also carries the guides** (its `theme.css` is byte-identical to the merged one), so nothing
differs above 760 and every desktop width reports `UNEXPECTED 0 px`.

The tool's premise is that its two sides differ **only** by the guide rules, and a pre-change
tree cannot satisfy it — it differs in the rail too, and its `skipped/*.json` payloads are a
separate question (a dist whose pages cannot expand their payloads renders 56 041 px tall at
1024 against 217 634, and every width is then skipped as `not like-for-like`). So the run that
produced the phone/tablet result is the pairing the tool documents: the merged build against the
same build with the guide declarations removed — `raw-qa_pixels.log`, **exit 0**, 390 →
0 / 329 160 px and 759 → 0 / 640 596 px. It is corroborated against the real pre-change build at
those two widths in `raw-qa_pixels-vs-prechange-phone.log`, also **exit 0**.

`qa_pixels.py` now **checks the premise before it compares anything** and refuses with a named
reason (`exit 2`) instead of reporting 0 px as a regression — the wrong pairing is kept in
`raw-qa_pixels-vs-prechange.log` and `raw-qa_pixels-preflight.log` section B to show it failing
that way rather than reading as a regression. `tools/make_noguides_dist.py` makes the correct
"before" side reproducibly.

`qa_defects.py`'s D15 section was a `print` of a JSON blob and is now an **assertion**, at
1280 and 1440, checking both halves of the card's acceptance: the archive may not render a
bar that misreports, and the sitting page may not lose its bar.

## Three measurement traps found and fixed while doing this

All were caught because the numbers disagreed with each other. The first two are recorded in the
tools so they cannot recur; the third is in the archive probe, where it could.

1. **Reading the bar in the same tick as `scrollTo()`** returns the *previous* value —
   `pbarSync()` runs inside the page's own `requestAnimationFrame`. This produced a false
   "the sitting page's bar reads 0 at the foot" verdict on a perfectly working bar, which
   nearly became a reported regression. `SITTING_PBAR_PROBE` awaits two frames plus a settle.
2. **Reading geometry on a fresh tab before navigating** measures `about:blank`. This
   returned `maxScroll=2857` for both builds — a number that is not this page's geometry,
   and which would have silently clamped the pixel comparison's scroll target, making that
   test prove less than it appeared to. Geometry is now read after navigation and only
   once it has stopped changing; the settled value is **2770 on both builds**.
3. **`ARCHIVE_PROBE` itself still read in the same tick as `scrollTo()`**, so the option-2
   branch of this file's own acceptance could not pass on a correct implementation: a
   scroll-tracking bar would have been reported as failing, because `after` would always carry
   the pre-scroll value. It was invisible while option 1 was chosen (there is no bar to read),
   and *trap 1's own claim that the probes now await the frames was not true of the probe the
   card names.* Reproduced by running that read pattern on the sitting page, whose bar works:
   same-tick `aria='0'` at the foot, settled `aria='100'`. `ARCHIVE_PROBE` is now an async IIFE
   with the same awaits, the option-2 branch prints the settled value it measured, and
   `check_probe_tick.py` asserts both readings so a lost await fails loudly.

The audit's `2770` and `scrollY: 2770` are therefore correct; the 2857 was my tool's error.

## Files

* changed: `site/build_site.py` (the removal + the decision comment), `tools/qa_defects.py`
  (D15 print → assertion covering both page kinds; archive probe now awaits the frame),
  `tools/qa_pixels.py` (pairing preflight)
* new: `tools/make_noguides_dist.py`, `tools/check_probe_tick.py`, `tools/qa_build_diff.py`,
  `tools/qa_archive_pbar.py`, `tools/check_pbar.py`, `tools/qa_d15_pixels.py`,
  `tools/qa_d15_shots.py`, `tools/qa_scroll_geometry.py`, `tools/_d15_logs.sh`
* artifacts: before/after frames in this directory

## Not done / adjacent, deliberately

* `site/dist/` is not committed with this change. **27 tracked dist files were already
  stale** against a fresh build of this commit before the change. Pre-existing drift,
  unrelated to D15, and not mine to fold in silently. (The fresh build is byte-identical to
  this worktree's dist for `index.html`, `theme.css` and every sitting page tested; only the
  archive differs, by exactly the `.pbar` block — that is the D15 change itself.)
* `check_rail.py` exits 0 on this repo but is **vacuous** — it reports `cards: 0` on both
  the merged and the pre-change build, so it asserts nothing. Pre-existing, unchanged, worth
  its own card. Its sibling `check_rail_geometry.py` does assert, and passes.
* `.totop`/`.pbar` overlap (D13) is untouched; it is a separate card.
