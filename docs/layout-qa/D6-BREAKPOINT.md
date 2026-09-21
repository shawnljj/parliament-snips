# D6 — one desktop breakpoint, measured

Task `t_bb9c77d0`. Audit defect **D6**: `min-width:760px` (the two-panel grid) and
`max-width:760px` (the phone block) both matched at exactly 760px, so the grid was live while
the phone padding was also live and the reading column collapsed to **204px**.

## The decision

**The desktop layer is `min-width:761px`; the phone block stays `max-width:760px`.**

760 and 761 are adjacent integers, so the two queries are exact complements: every viewport
width matches exactly one of them, and 760 is a phone width.

Why this side and not the other (moving the phone block to `max-width:759px`):

* **The rail and the progress bar were already on `min-width:761px`.** They arrived there in
  `6221dde` ("Desktop progress bar") and the phone block was already `max-width:760px` from
  `dbcfa1f`. The grid's two-panel rule was the *only* one on 760 (`71959fa`, `e45b250`).
  Moving the grid up matches the two existing systems; moving the phone block down would
  leave the grid still outvoted by nothing and change a number two systems already agree on.
* **A 760px viewport is a small tablet in portrait, not a desktop.** The two-panel layout
  needs 562px of reading column plus a 416px summary column. At 760px even with the desktop
  padding the reading column resolves to **263px**, which is not prose. So 760 belongs on the
  phone side, which is where the rail, the pbar, the type scale and the jump list already put
  it.
* It is **D6's own recommendation**: "unify on one desktop breakpoint", with the rail/pbar
  (761) and the phone block (760) already agreeing.

## What changed

| file | change |
|---|---|
| `site/build_site.py` | 4 grid-layer blocks `min-width:760px` → `min-width:761px`; the page's own `matchMedia('(min-width: 760px)')` (the `isWide()` gate that decides whether the full record is fetched) → `761px` |
| `site/dist/theme.css` | regenerated from the generator, not hand-edited |
| `site/dist/**/*.html` | 332 pages, each changed by exactly the one-character JS gate swap |
| `tools/check_column_guides.py` | the 760 expectation is now `grid=False, guide=False` |
| `tools/check_breakpoints.py` | NEW — the gate that asserts this card's acceptance criteria |
| `tools/qa_pixels.py`, `tools/compare_renders.py`, `tools/qa_frames.py` | 760 moved from the "must differ" list to the "phone width" list |

The three queries that must agree — the grid, the rail/pbar, and the phone block — are now
one number (`761`/`760`) at every layer: CSS, the generated pages' inline JS, and the
generator.

## Acceptance, measured

`tools/check_breakpoints.py` (new, exit 0) asserts all three criteria directly:

```
  min-width  = [700, 761, 1200]        max-width  = [760]        intersection = []
  desktop break = min-width:761px  phone break = max-width:760px  (complements: True)
  JS matchMedia gates on the page     = ['max-width:760px', 'min-width:761px']
  JS matchMedia gates in the generator= ['max-width:760px', 'min-width:761px']

  761px  grid=True  tracks=[263, 416] readingCol=263   rail_layer(pad/z)=0px/24 pbar=True  jumpOpen=True
  760px  grid=False tracks=[]         readingCol=None  rail_layer(pad/z)=8px/70 pbar=False jumpOpen=False
  759px  grid=False tracks=[]         readingCol=None  rail_layer(pad/z)=8px/70 pbar=False jumpOpen=False
```

* **At 760px exactly one layout is live.** `display=block`, `gridTemplateColumns=none`, no
  column guide. 760 and 759 now measure identically.
* **The reading column is never the collapsed 204px.** At 760 the phone column is **682px**
  (was 204px as a grid track). The gate also fails on any reading column under 240px.
* **The rail/pbar switch and the grid switch are the same number.** The rail is *present* at
  every width by design (one system, thumb-sized on a phone) — what switches is its desktop
  layer, measured as track padding `0px` + `--z-rail: 24` at 761 vs `8px` + `70` at 760. The
  pbar goes `display:none` → `block` on the same boundary, and so does the page's own JS jump
  list.

Re-runs as the card asks:

* `tools/check_column_guides.py` breakpoint section — **exit 0**, `760 grid=False
  guide=False`, `759 grid=False`, `390 grid=False`, `761`/`1920` `grid=True guide=True`.
  The guide no longer marks a broken column at any width.
* `tools/compare_mobile.py` 390/740/759/760/761 — 390, 740 and 759 **IDENTICAL** (every
  element's computed style and rect). 760 legitimately changed: `elements: worktree=5456`
  vs `baseline=12500`, i.e. the page now renders the *phone* DOM (5456 elements) where it
  rendered the desktop DOM (12500) before.
* `tools/qa_pixels.py` — 390/759 **0 px changed**; 760 reported as LAYOUT MOVED (page height
  66005 vs 499657, by design); 761/1024/1280/1440/1920 differ (guides live).
* `tools/check_rail_geometry.py` at 761/1024/1440/1920 — dot clearance exactly **10px**,
  dot spread **0**, fill offset **0.0px** at every width, including the new lowest desktop
  width.
* `tools/qa_console.py` at 390/759/760/761 and 1024/1280/1440/1920 — silent console,
  **no horizontal overflow**, no top-bar shift.
* `tools/qa_defects.py` static section — `D6 FIXED: min-width=[700, 761, 1200]
  max-width=[760] EXACT COLLISION=[]`.
* Project gates vs the pristine baseline: `check_rail.py` 0/0; `check_artifacts.py` and
  `check_year.py` fail **identically on both** (pre-existing, unrelated to layout).

Frames at the boundary: `docs/layout-qa/before-after-d6/{before,after}-{390,759,760,761}.png`.
At 760 the before frame shows the 204px reading column beside the summary card; the after
frame is a single full-width phone column.

## Negative tests

A gate that cannot fail proves nothing. `tools/check_breakpoints.py` was run against five
mutations, each of which it caught (exit 1):

| mutation | caught by |
|---|---|
| grid back on `min-width:760px` (the original defect) | static: exact collision at 760 |
| the page's own JS gate left at 760 | JS-vs-CSS boundary disagreement |
| the generator's JS gate left at 760 | generator boundary check |
| a new stray layout `min-width` (900) | static: unexpected min-width |
| the phone block moved off 760 | static: max-width set is not `[760]` |

It was also run against the real pre-fix build (`wt/t_28b23d7d-qa` on port 8481): **7
failures**, including `760px: grid_live=True, expected False` and `760px: grid track is
204px, the collapsed width D6 produces`.

## Note on `site/dist`

332 generated pages carry the JS gate inline, so the fix has to reach them; each is changed by
exactly `min-width: 760px` → `min-width: 761px` and nothing else (verified by diffing every
changed file). The 25 stale 2017 archive pages that card `t_79a0e9e2` owns are **not**
regenerated here — only their gate character was swapped, the same as the other 307.
