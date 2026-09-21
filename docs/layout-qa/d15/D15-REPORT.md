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
| The archive lost exactly that block and nothing else in the build moved | `qa_build_diff.py` vs a fresh build of the same commit in a detached worktree | **3 847 of 3 848 files byte-identical**; the one change is `sittings/index.html`, and the new file equals the old with exactly the 5-line block excised (string equality, not a diff heuristic) |
| The page body did not move a pixel | `qa_d15_pixels.py` | **0 differing pixels** across 900 rows × 4 runs (archive and a sitting page, 1280 and 1440), chrome suppressed, both frames landing at the same scroll offset |
| The sitting page keeps its bar | `qa_defects.py`, `check_pbar.py` | present, `display:block`, **reads 0% at top and 100% at the foot** at 1280 and 1440 |
| No page anywhere renders a bar nothing drives | `qa_archive_pbar.py` | 339 pages scanned, **0** with the D15 shape; archive has none; **all 331** sitting pages keep theirs |
| The D15 check is not vacuous | `qa_defects.py` on the pristine build | **FAILS** there with `reads '0%' / aria-valuenow='0' at the foot (scrollY=2770 of 2770)` at both widths; passes here |
| No console, overflow or layout-shift regression | `qa_console.py` | silent console, no horizontal overflow, no top-bar shift at all 8 widths |
| The column work still holds | `check_column_guides.py`, `qa_pixels.py` | both pass; phone/tablet frames still pixel-identical to the pre-change build |

`qa_defects.py`'s D15 section was a `print` of a JSON blob and is now an **assertion**, at
1280 and 1440, checking both halves of the card's acceptance: the archive may not render a
bar that misreports, and the sitting page may not lose its bar.

## Two measurement traps found and fixed while doing this

Both were caught because the numbers disagreed with each other, and both are recorded in
the tools so they cannot recur:

1. **Reading the bar in the same tick as `scrollTo()`** returns the *previous* value —
   `pbarSync()` runs inside the page's own `requestAnimationFrame`. This produced a false
   "the sitting page's bar reads 0 at the foot" verdict on a perfectly working bar, which
   nearly became a reported regression. The probes now await two frames plus a settle.
2. **Reading geometry on a fresh tab before navigating** measures `about:blank`. This
   returned `maxScroll=2857` for both builds — a number that is not this page's geometry,
   and which would have silently clamped the pixel comparison's scroll target, making that
   test prove less than it appeared to. Geometry is now read after navigation and only
   once it has stopped changing; the settled value is **2770 on both builds**.

The audit's `2770` and `scrollY: 2770` are therefore correct; the 2857 was my tool's error.

## Files

* changed: `site/build_site.py` (the removal + the decision comment), `tools/qa_defects.py` (D15 print → assertion, both page kinds)
* new: `tools/qa_build_diff.py`, `tools/qa_archive_pbar.py`, `tools/check_pbar.py`, `tools/qa_d15_pixels.py`, `tools/qa_d15_shots.py`, `tools/qa_scroll_geometry.py`, `tools/_d15_logs.sh`
* artifacts: before/after frames in this directory

## Not done / adjacent, deliberately

* `site/dist/` is not committed with this change. **27 tracked dist files were already
  stale** against a fresh build of this commit before the change — proved by rebuilding
  the unmodified commit in a detached worktree and getting the same 27. Pre-existing drift,
  unrelated to D15, and not mine to fold in silently.
* `check_rail.py` exits 0 on this repo but is **vacuous** — it reports `cards: 0` on both
  the merged and the pristine build, so it asserts nothing. Pre-existing, unchanged, worth
  its own card.
* `.totop`/`.pbar` overlap (D13) is untouched; it is a separate card.
