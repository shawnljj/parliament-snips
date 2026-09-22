# D13: the progress bar swallowed the lower 10px of the back-to-top button

Card `t_2a44f62f`. Branch `wt/t_2a44f62f`, based on `main` (`994a1df`) -- **not** on the QA
branch's merge commit `8bc6300`, because the layout change and this fix are independent.

---

## The defect, reproduced from scratch before it was fixed

The audit measured this at 1024/1280/1440/1920. It was re-measured on a pristine checkout of
`main` in this worktree (`baseline-main`, the build before any of the column work) so the
before/after pair is two real dists served by two real servers, not a quote:

```
----- 1024px  (desktop)                       PRISTINE main @ 994a1df
  .totop   box={'x': 964, 'y': 840, 'right': 1008, 'bottom': 884, 'w': 44, 'h': 44}
  .pbar    live=True box={'x': 0, 'y': 874, 'right': 1024, 'bottom': 900, 'w': 1024, 'h': 26}
  overlap=10px  clearance(bar top - button bottom)=-10px
  hits, toast dismissed: bare-1px->SPAN.pbar-txt(PBAR)
                         bare-3px->SPAN.pbar-txt(PBAR)
                         bare-6px->SPAN.pbar-txt(PBAR)
                         bare-10px->SPAN.pbar-txt(PBAR)
```

Identical at 761/1280/1440/1920. `elementFromPoint()` at the button's bottom edge returns
`SPAN.pbar-txt` at **every one of the four offsets**, so the whole lower 10px of a 44px button
was inert. Confirmed: real, reproducible, and not a regression.

## The fix

**One token, one number, one place.** `site/dist/theme.css` + the same text in the generator's
`STYLE` (they are byte-identical; see "generator parity" below).

```css
:root{--chrome-h:0px}                       /* base layer = the phone; the bar is display:none */

.totop{... bottom:calc(var(--chrome-h) + 16px) ...}
@media (max-width:760px){.totop{bottom:calc(var(--chrome-h) + 16px);right:14px}}

@media (min-width:761px){
  :root{--pbar-h:26px;--chrome-h:var(--pbar-h)}
  .pbar{... height:var(--pbar-h); line-height:var(--pbar-h) ...}
  .resume{bottom:calc(var(--pbar-h) + 12px)}
}
```

Why this shape and not a bare `bottom:42px`:

* **The phone keeps its exact previous geometry by construction.** `--chrome-h` is `0px` on the
  phone, so every `calc()` above collapses to the literal `16px` the phone always had. There is
  no phone-specific magic number that can drift.
* **`--chrome-h` is defined on the phone, not only on desktop.** An *undefined* custom property
  inside `calc()` invalidates the whole declaration, which would silently drop `bottom` and pin
  the button to its static position. Defining it as `0px` in the base layer makes the token
  always resolvable.
* **The bar's height is now stated once.** `--pbar-h` was already the height of the bar, written
  as a literal `26px` in three places (`height`, `line-height`, and the toast's `bottom:38px`);
  it is now derived from in all of them, so a change to the bar cannot leave the clearance
  behind. The `min-width:761px` block is `display:none`-gated already, so putting the `:root`
  block inside it is safe and applies exactly where the bar is.

Verified geometry after the fix, at all four widths the audit used plus 761 (the breakpoint):

```
overlap 0px, clearance 16px (bar top 874 - button bottom 858)
hits down the lower 10px of the button: BUTTON.totop at 1/3/6/10px
phone 390/759/760: bar display:none, button bottom 16px, right 14px -- unchanged
```

## What this does NOT claim: D14

The card said "if you touch this, confirm D14 in the same pass", and the audit's D14 says the
`.resume` toast is behind the bar on desktop because the `min-width:761px` rule "never sets
`bottom`". **That desktop half does not reproduce.** The same `min-width:761px` block at
`theme.css:776` sets `.resume{bottom:38px}` -- 12px clear of the 26px bar. Measured on the
pristine build, at every desktop width:

```
resume bottom=38px  overlap=0px  clearance=12px
```

So the audit's statement is half right and half wrong:

* **Right**: the base `.resume{bottom:16px}` does not clear the bar *by itself*, and the phone
  value being correct is a coincidence of the bar being `display:none` there.
* **Wrong**: the desktop toast is not behind the bar. It was already above it.

The toast was therefore **not moved**. Its desktop `bottom` was rewritten from the literal `38px`
to `calc(var(--pbar-h) + 12px)` -- the same arithmetic it always was, now coupled to the token
instead of coincidentally matching it. `tools/check_bottom_chrome.py` asserts the toast's
measured clearance (`bar top - toast bottom == 12px`) at every desktop width, so if the bar's
height ever changes, this tool fails rather than the coupling silently breaking.

## A third finding, pre-existing and NOT fixed here

With the toast on screen it covers the back-to-top button. Measured identically on both builds:

```
390px:  the resume toast covers the button by 42x44px   (both builds)
1024px: the resume toast covers the button by 44x44px   (both builds)
```

Cause: `.resume` is `z-index:45` and `.totop` is `z-index:40`, and on a phone the toast is
`left:16px;right:16px` (full width) so its right edge lands on the button; on desktop the toast
is `right:16px;max-width:380px`, so its right edge is exactly the button's `right:16px`. The
button is unreachable for the 12s the toast is up.

This is **not D13** (different element, different z-index pair) and **not a regression** (byte-
for-byte the same on the pristine build). It is out of scope for this card, which is a one-
element clearance fix, and fixing it means a product decision (should the button move, or the
toast, or should `totop` outrank the toast?). It is filed as a follow-up card rather than
silently folded in here.

**UPDATE (card `t_91ef2ced`, branch `wt/t_91ef2ced`): fixed there, by insetting the toast.**
That branch extended this tool from *reporting* this defect to *asserting* it: the string
`PRE-EXISTING (not D13)` no longer appears in the output, and the same tool now fails on the
build this report was written against with 30 failures (42x44px and 44x44px overlap, 9 of 9
samples inside the button resolving to the toast). The measurements in this section are the
pre-fix state, correctly described here as pre-existing.


## Acceptance, measured

The repo has no test suite (`t_28b23d7d` established that), so this is asserted by measurement
the way the existing tools do. New: **`tools/check_bottom_chrome.py`** -- same minimal
CDP-over-websocket client as `tools/check_rail.py` and `tools/check_column_guides.py`, no
third-party dependency. It exits 0 only when every assertion holds.

| width | phone/desktop | button/bar overlap | button/bar clearance | toast/bar overlap | hits on the button's lower 10px |
|---|---|---|---|---|---|
| 390 | phone | n/a (bar hidden) | n/a | n/a | `BUTTON.totop` |
| 759 | phone | n/a (bar hidden) | n/a | n/a | `BUTTON.totop` |
| 760 | phone | n/a (bar hidden) | n/a | n/a | `BUTTON.totop` |
| 761 | desktop | 0px | 16px | 0px | `BUTTON.totop` |
| 1024 | desktop | 0px | 16px | 0px | `BUTTON.totop` |
| 1280 | desktop | 0px | 16px | 0px | `BUTTON.totop` |
| 1440 | desktop | 0px | 16px | 0px | `BUTTON.totop` |
| 1920 | desktop | 0px | 16px | 0px | `BUTTON.totop` |

Every acceptance criterion from the card, one line each:

* *"at 1024/1280/1440/1920, totop's bottom edge is at or above the bar's top edge (overlap 0)"*
  -- **0px at all four**, and at 761 too. `clearance = 16px`.
* *"a click at the button's bottom edge hits `.totop` and not `.pbar-txt`"* -- **`BUTTON.totop`
  at 1/3/6/10px above the bottom edge**, at all five desktop widths, with the toast dismissed;
  the button's own click handler is also dispatched and reached. The `overlap-middle` sample
  the audit took is not merely non-`pbar-txt` -- there is no overlap for it to be taken in.
* *"the phone layout at <=760px is unchanged"* -- the button is at `bottom:16px;right:14px`
  (its pre-fix values), the bar is `display:none`, the toast is at `16px`; and every element's
  computed style **and** rect at 390/759/760 is identical to the pristine build.

### The checker is a positive control, not a tautology

The same tool was run against the **pristine pre-change build** (`baseline-main`, port 8437):

```
PRISTINE: 55 FAILURE(S)
  FAIL 1024px: button/bar overlap is 10px, expected 0
  FAIL 1024px: the button's bottom edge is 10px BELOW the bar's top edge
  FAIL 1024px: at the button's bottom edge -1px the hit is SPAN.pbar-txt (bar=True), not .totop
  ... (at 761/1280/1440/1920 too)
FIXED:    0 FAILURE(S)
```

A test that passes on the broken build measures nothing. This one fails on it, at every width,
with the audit's exact signature (`SPAN.pbar-txt`).

### Regression checks

* `tools/check_rail.py` (the rail's scroll-spy): **PASS**, active card inside the rail's
  viewport at all 9 scroll positions.
* Phone/tablet: **0 differing elements** at 390/759/760. Every element in the DOM (5,458 at
  390, 12,502 at 760) was compared against the pristine build on **every** computed property
  (~340 of them) **and** every rect. The only difference on any element is the presence of the
  new inherited `--chrome-h` token itself, which is the defining mark of the change rather than
  a regression in it -- the tool counts that separately so a real property difference could not
  hide inside it. **0 differ in anything else**, and 0 boxes moved.
* No horizontal overflow at any of the 8 widths.
* The bar itself is unmoved: `display:block`, `height 26px`, `css bottom 0px` -- asserted, so
  "the button cleared the bar" cannot be satisfied by resizing the bar instead.
* The repo's pre-existing gates (`check_artifacts.py`, `check_year.py`) exit 1 **identically on
  my tree and on pristine `main`**, so they are unchanged by this work. `check_year.py` fails
  because 291 of 291 items are unsummarised; that is the corpus state, not this change.

### Generator parity

`site/dist/theme.css` is generated from `STYLE` in `site/build_site.py`, and both were edited.
Verified rather than assumed: `python3 site/build_site.py <tmp>` produces a `theme.css` that is
byte-identical to the committed one, and the sitting page is byte-identical too.

The rebuild also shows **27 files in `site/dist` drifting from the generator on both my tree and
pristine `main`** (25 stale 2017 archive pages and 2 hand-made pages that the generator does not
emit). That is pre-existing and is already its own card, `t_79a0e9e2`; this change introduces
zero new drift.

## Files

* `site/dist/theme.css` -- the fix (generated).
* `site/build_site.py` -- the same text in `STYLE` (generator source of truth).
* `tools/check_bottom_chrome.py` -- new, the acceptance instrument.
* `docs/layout-qa/raw-check_bottom_chrome.log` -- the passing run.
* `docs/layout-qa/raw-check_bottom_chrome-pristine.log` -- the same tool on pristine `main`
  (the positive control, 55 failures).
* `docs/layout-qa/raw-check_bottom_chrome-toolbug.log` -- kept deliberately: the first run of
  the tool, which failed because the seeded scroll position was overwritten by the page's own
  `pagehide` handler and so the toast never rendered. It is why the tool now seeds via
  `Page.addScriptToEvaluateOnNewDocument` and **fails loudly** if the toast is absent, instead of
  asserting a vacuous truth about an element that is not there.
