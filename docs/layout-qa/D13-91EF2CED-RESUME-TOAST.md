# t_91ef2ced: the resume toast covered the back-to-top button

Card `t_91ef2ced`. Branch `wt/t_91ef2ced`, based on `wt/t_2a44f62f` (`cb340897`, the D13 fix),
because D13's fix is what turned the button's position and the toast's clearance into tokens and
this fix reuses them. One commit.

---

## The defect, reproduced before it was fixed

Re-measured from scratch on this branch before any edit. `document.elementFromPoint()` sampled in
a 5x5 grid over the button's box, with the toast actually rendered (which needs a seeded
`localStorage` scroll position -- see `tools/check_bottom_chrome.py` on the why):

```
 390px  toast {'x': 16, 'y': 816, 'right': 374, 'bottom': 884, 'w': 358, 'h': 68}  z=45
        button {'x': 332, 'y': 840, 'right': 376, 'bottom': 884, 'w': 44, 'h': 44}  z=40
        OVERLAP {'w': 42, 'h': 44, 'area': 1848}
        button hit owners: {'RESUME(resume)': 14, 'RESUME(rno)': 6, 'MAIN.wrap': 4, 'TOTOP': 1}

1024px  toast {'x': 657.41, 'y': 794, 'right': 1008, 'bottom': 862, 'w': 350.59, 'h': 68}
        button {'x': 964, 'y': 814, 'right': 1008, 'bottom': 858, 'w': 44, 'h': 44}
        OVERLAP {'w': 44, 'h': 44, 'area': 1936}
        button hit owners: {'RESUME(resume)': 16, 'RESUME(rno)': 8, 'MAIN.wrap': 1}
```

Identical at 360/759/760/761/1280/1440/1920. The card's numbers are exact: 42x44 on a phone,
44x44 on desktop. The one sample that still resolved to the button on the phone is at the box's
top-left corner, which is **outside the 44px circle's `border-radius:50%`** -- so even that
"hit" is the page behind the button, not the button.

Two things the card does not state, both of which mattered for the decision:

1. **It is worse than "the button is not clickable".** 6 of the 27 phone samples resolve to
   `BUTTON.rno` -- the toast's own dismiss control. The toast's dismiss at `x 337.48..360` sits
   *inside* the button's box (`x 332..376`), so the two controls are physically on top of each
   other, not merely adjacent.
2. **Nothing is partially covered.** The smallest overlap in the card's table is the phone's
   42x44px, and the button is 44x44px. There is no width at which the button is partly usable.

## The decision: the toast yields, horizontally

The card lists three options. I measured the first one rather than reasoning about it, because
the card flags it as needing care ("it needs care" -- it puts the button on top of the toast's
own Resume control at 1024 and below). It is worse than "needs care":

```
OPTION (a) .totop{z-index:50}, measured at 3 widths
 390px  button hit owners: {'TOTOP': 21, 'MAIN.wrap': 2, 'resume-body': 2}
        dismiss control hits: {'y0.25': 'rno', 'y0.5': 'TOTOP', 'y0.75': 'TOTOP'}
 761px  dismiss control hits: {'y0.25': 'TOTOP', 'y0.5': 'TOTOP', 'y0.75': 'TOTOP'}
1024px  dismiss control hits: {'y0.25': 'TOTOP', 'y0.5': 'TOTOP', 'y0.75': 'TOTOP'}
```

Raising the button **destroys the toast's dismiss control**: 2 of 3 samples on the phone and
3 of 3 on desktop resolve to the button. The toast's timer would be the reader's only way out.
So (a) is not the smallest change -- it trades a 12-second obstruction of one control for a
12-second obstruction of another, and this one has no alternative route (the button also
becomes reachable by scrolling up, which is what it is for).

Option (b), done **horizontally**: the toast keeps its height, its message, its two buttons and
its vertical position, and stops short of the button's column.

```css
:root{--chrome-h:0px;--totop-h:44px;--chrome-gap:8px}

.resume{... right:calc(14px + var(--totop-h) + var(--chrome-gap)) ...}   /* base = the phone */
@media (min-width:761px){.resume{left:auto;
  right:calc(16px + var(--totop-h) + var(--chrome-gap));max-width:380px}}
```

`14 + 44 + 8 = 66px` on the phone, `16 + 44 + 8 = 68px` on desktop. The two insets are
per-breakpoint because the **button's** inset is (`right:14px` at base, `16px` from 761px); the
width and the gap come from the tokens, so a larger button has to push the toast further aside
rather than quietly start covering it. `--totop-h` is now also what sets the button's own
`width`/`height`, so there is exactly one number for the button's size.

### Why not option (b) done vertically

Moving the toast up instead is what the card's option (b) most naturally suggests, and it is
what I tried first. It fails at short viewports, because the section rail (`z-index:70`, higher
than both) lives in the same right-hand band:

```
VARIANT V: .resume{bottom:calc(var(--chrome-h) + 68px)}   viewport 600px tall
 390px  toast/rail intersection {'w': 30, 'h': 68, 'area': 2040}
        dismiss control hits: {'y0.2': 'RAIL', 'y0.5': 'RAIL', 'y0.8': 'RAIL'}
```

Lifting the toast by 52px pushes its dismiss control into the rail's band, and the rail is
`z70`, so the control becomes unreachable -- the same failure mode as option (a), by a
different route. Staying low is what keeps the toast clear of the rail at all: measured at a
600px-tall viewport, the toast/rail intersection goes from `30x18.5px` (area 555) un-lifted to
`30x68px` (area 2040) lifted at 390px.

The horizontal form does the opposite of harm: it **narrows** the toast, so the toast/rail
intersection shrinks rather than grows -- `30x18.5px` (area 555) becomes 0 at 390px, and the
same at every width from 320 to 760. The vertical relationships -- toast/bar clearance,
toast/rail, the bar itself -- are deliberately untouched, and at the heights the tool measures
(900px) there is no toast/rail intersection on either build at any width.

## The one cost of insetting, measured rather than assumed

Insetting the toast reserves the button's column, so its content box is narrower on a phone, and
the message wraps earlier. Measured on both builds at 13 widths:

```
         message lines       toast box
 width   pre-fix  fixed      pre-fix      fixed
 320       2        3        288x68      238x79
 360       2        2        328x68      278x68
 375       2        2        343x68      293x68
 390       1        2        358x68      308x68
 414       1        2        382x68      332x68
 430       1        2        398x68      348x68
 480       1        1        448x68      398x68
 540       1        1        508x68      458x68
 700       1        1        668x68      618x68
 759/760   1        1        727x68      677/678x68
 761+      1        1        351x68      351x68   (desktop unchanged: max-width:380px)
```

So the message does wrap from 390px down -- but **the toast's height does not change at all**
(68px at every width from 360 to 760, on both builds). Its two 44px controls plus 24px of padding
already set the height, and two lines of 13.5px text need only 36.4px, so the wrap is absorbed
with no vertical cost and the toast moves sideways and *only* sideways. The checker asserts the
68px height at every phone width for exactly that reason: if a future message was long enough to
exceed the controls, the fix would have changed the toast's footprint and this fails.

320px is the one width where three lines finally exceed the controls (79px tall). That is below
the base 390px layout this stylesheet is written for, the overlap is still 0 there, and it is
recorded rather than asserted.

## Acceptance, measured

`tools/check_bottom_chrome.py` was extended: what it used to **report** as
`PRE-EXISTING (not D13)` it now **asserts**, which is the change the card asks for. Same
instrument, same 8 widths, same real toast.

| width | toast/button overlap | gap | button owns its own pixels | toast Resume / dismiss reachable |
|---|---|---|---|---|
| 390 | 0x44px (area 0) | 8px | 9/9 | 3/3, 3/3 |
| 759 | 0x44px (area 0) | 8px | 9/9 | 3/3, 3/3 |
| 760 | 0x44px (area 0) | 8px | 9/9 | 3/3, 3/3 |
| 761 | 0x44px (area 0) | 8px | 9/9 | 3/3, 3/3 |
| 1024 | 0x44px (area 0) | 8px | 9/9 | 3/3, 3/3 |
| 1280 | 0x44px (area 0) | 8px | 9/9 | 3/3, 3/3 |
| 1440 | 0x44px (area 0) | 8px | 9/9 | 3/3, 3/3 |
| 1920 | 0x44px (area 0) | 8px | 9/9 | 3/3, 3/3 |

`D13/D14 CHECK: 8 width(s) measured, 0 failures` (`raw-check_bottom_chrome-fixed.log`, exit 0).

### The checker is a positive control, not a tautology

The same tool on the **pre-fix** build (this branch's parent `cb340897`, served at 8437):

```
D13/D14 CHECK: 8 width(s) measured, 30 FAILURE(S)
  FAIL 390px: the resume toast covers the button by 42x44px (1848px^2) -- the toast is z45,
              the button z40, so the button is unreachable while the toast is up
  FAIL 390px: with the toast up, 9 of 9 samples INSIDE the button resolve to the toast, not
              the button ({'toast-dismiss': 4, 'toast': 5})
  FAIL 1024px: the resume toast covers the button by 44x44px (1936px^2) ...
  ... at all 8 widths
EXIT=1
```

A test that passes on the broken build measures nothing. This one fails on it with the card's
exact numbers, at every width.

### Regression

`tools/compare_two_builds.py` compares **every element's every computed property and every
rect** against the pre-fix build, with the toast ON SCREEN (`--toast`, a flag this card added --
the tool used to hide the toast unconditionally, which would have hidden the entire change):

```
  390px: 5462 elements; 5458 differ ONLY by the new tokens; 4 moved as expected; 0 differ in
         anything else
         DIV.resume  16,816,308,68  -> 16,816,358,68    (width only; height and y unchanged)
         SPAN.       30,831.78,...  -> 30,840.89,...     (the wrap)
         BUTTON.rgo  199.84,828,..  -> 249.84,828,..
         BUTTON.rno  287.48,828,..  -> 337.48,828,..
  ...identical at 759/760/761/1024/1280/1440/1920, 4 elements each
RESULT: 0 differences beyond the new token(s) and the expected move(s)
EXIT=0
```

The button itself is **not** among the movers at any width -- this card does not touch its
position or size, and the comparison proves it.

* The bar is unmoved: `display:block`, 26px tall, `bottom:0`, asserted at every desktop width.
* Phone: the button is at its pre-fix `bottom:16px;right:14px`, the toast at `bottom:16px` with
  its height unchanged at 68px. The **one** phone number this card changes is the toast's right
  inset, and the tool asserts it as the arithmetic (inset + width + gap) *and* explicitly fails
  if the toast returns to its old 16px, so this exact regression cannot pass.
* No horizontal overflow at any width.
* The repo's pre-existing gates (`check_artifacts.py`, `check_year.py`) exit 1 **identically on
  this branch and on its parent** -- untouched by this change.

### Generator parity

`site/dist/theme.css` is generated from `STYLE` in `site/build_site.py`; both were edited.
`python3 site/build_site.py <tmp>` reproduces the committed `site/dist/theme.css` byte-for-byte,
and the sitting pages are byte-identical to the committed ones (they link `theme.css`
externally, so only the stylesheet changes). The generator rebuild shows 27 files drifting on
**both** this branch and its parent -- pre-existing, already card `t_79a0e9e2`, zero new drift.

## Files

* `site/build_site.py` -- the fix, in `STYLE` (generator source of truth).
* `site/dist/theme.css` -- the same text, generated.
* `tools/check_bottom_chrome.py` -- extended from reporting this defect to asserting it.
* `tools/compare_two_builds.py` -- the two new tokens added to the allowed-difference set.
* `docs/layout-qa/raw-check_bottom_chrome-fixed.log` -- the passing run (0 failures).
* `docs/layout-qa/raw-check_bottom_chrome-prefix.log` -- the same tool on the pre-fix build
  (30 failures; the positive control).
* `docs/layout-qa/raw-compare_two_builds-91ef2ced.log` -- the regression comparison.
