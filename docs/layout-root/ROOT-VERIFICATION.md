# Root card t_a1329966 — integration and independent verification

The four decomposed children of this card are all merged here, and the merged artifact is
re-verified from the root card against the audit baseline. This report is what the root card
adds on top of the children's own handoffs: a single frozen integration, and a second
independent measurement of it.

## 1. The integrated artifact

Branch `parsnips/t_a1329966-ui-improvements-for-columns-on-desktop`.

| child | branch | what it contributes | in HEAD |
|---|---|---|---|
| `t_2264d837` | `wt/t_2264d837` | the audit: `docs/layout-audit/AUDIT.md`, D1–D17, `tools/measure_layout.py` | yes (via `wt/t_b764176e`) |
| `t_b764176e` | `wt/t_b764176e` | the four column hairlines + `--col-*` token set + the guide checkers | yes |
| `t_140a0b87` | `wt/t_140a0b87` | the rail anchored to the content grid; dot column, fill, box, preview | yes |
| `t_28b23d7d` | `wt/t_28b23d7d-qa` | the cross-token merge and the QA report | yes (merge `8bc6300`) |

`HEAD = 0eff07b`. All four child branches are ancestors of it, confirmed individually with
`git merge-base --is-ancestor`. No commit was needed to integrate them: `t_28b23d7d`'s merge
commit `8bc6300` is the integration point, and this card's branch is that tree.

**The merge was not a formality.** Both implementation branches named the same four column
values differently in the same `:root` (`--col-wrap/--col-pad/--col-sum/--col-gap` vs
`--content-max/--content-pad/--summary-col/--gutter`). Merging either side naively leaves the
other scheme's references resolving to nothing, and `grid-template-columns` then falls back to
`none` — the two-column grid silently disappears and the guides have nothing to sit on.
`--col-*` survives; `--content-max`/`--content-pad` remain as the rail's coordinate aliases.

Re-verified here, independently of that claim:

```
python3 site/build_site.py /tmp/rootbuild      # from the merged generator
diff /tmp/rootbuild/theme.css site/dist/theme.css   # identical
```

The committed `site/dist/theme.css` is a byte-faithful build of the merged generator. It is
also 1150 lines against 894 on `main`, i.e. the guide block and the token set are both present
and emitted (not merely present in the generator source).

## 2. The deliverable, measured two ways

The owner's complaint was that the columns have no boundaries. The guide is four 1px vertical
hairlines. It is asserted here by geometry *and* by pixels read back out of the rendered frame,
because a rule painted outside the viewport or under an opaque box passes a geometry check.

Read the frame directly: `docs/layout-root/after-1440.png` is the scrolled-free desktop view with
all four rules visible, `after-1440-scrolled.png` is the same page at 25% scroll, and
`docs/layout-audit/before-1440.png` is the audit's baseline frame for comparison.

Geometry — `tools/check_column_guides.py` against a **fresh build of the merged generator**
(port 8477, not the QA branch's dist): **exit 0**, every rule on its column edge at
1024/1280/1440/1920, bounded per `.dsec`, painted under the text, drawn at 760/761 and absent at
759/390.

Pixels — scanning `docs/layout-root/after-<w>.png` for the one unbroken colour column:

| frame | vertical hairlines found | x |
|---|---|---|
| `docs/layout-audit/before-1440.png` (baseline) | **0** | — |
| `docs/layout-root/after-1440.png` | **4** | 214, 775, 810, 1225 |
| `docs/layout-root/after-1440-scrolled.png` | 2 | 214, 775 |

and the geometry the checker derives at 1440 is `textLeft=214 textRight=776 sumLeft=810
sumRight=1226`. Reading the two together gives the exact mechanism: each hairline occupies the
**last pixel inside** its track (214/810 on the left edges, 775/1225 being the last pixel before
776/1226). That is what an `inset` box-shadow does, and it is why the guide is drawn that way —
a border would shrink the `minmax(0,1fr)` content box and pull the text column off the 562px
the audit measured. So the 1px is the mechanism, not an off-by-one.

## 3. Nothing else moved

`tools/compare_layout.py` against the audit's own `baseline-measurements.json`:

* fresh merged build: **516 differences, all 516 under `sticky.*`, 0 under `columns.*`**
* the QA branch's run, same command: **byte-identical output** bar the URL line

Every one of the 516 is the declared rail work of `t_140a0b87` (`rail`, `railTicks`,
`railMarks`, `railTrack`, `railFill`, `railName`, `railBubble`, `railGo`, `railPill`). The
column geometry the owner looks at is bit-for-bit what it was.

At the other end of the range:

| check | result |
|---|---|
| `tools/compare_mobile.py` 390/740/759/760 | **IDENTICAL** — every computed style and rect, 41 368 element-snapshots, 0 differing properties |
| `tools/qa_pixels.py` | 390 → **0**/329 160 px, 759 → **0**/640 596 px; 760/761/1024/1280/1440/1920 differ (the guides) |
| `tools/qa_console.py` at 8 widths | 0 errors, 0 rejections, 0 warnings; `overflowAt=never`; `topBarShift=none` |
| `tools/check_rail_geometry.py` | exit 0 — dot clearance 10px, 1 dot column (spread 0.00px), fill offset 0.0px |
| `tools/check_rail_preview.py` merged vs pristine | **0 failing of 32** on the merged build, **32 of 32** on the pristine one (the tool's combined line reads "32 failing of 64 sampled preview states" — every failure is in the baseline arm) |
| `tools/qa_gates.py` | `check_artifacts`/`check_selection`/`check_year` exit 1 on **both** builds; `check_rail`/`qa_console`/`qa_defects`/`qa_pixels` exit 0 |

The preview claim is worth restating because it is the one defect the original audit could not
see: the rail's `transform:translateY(-50%)` makes the rail the containing block for its own
`position:fixed` descendants, so `right` resolved inside the rail and the JS's viewport-`top`
was applied against the rail's box. On the pristine build the bubble sits ~216px below the tick
it names and its two parts are inverted; on the merged build 0 of 64 sampled states fail.

## 4. What the root card found that the children did not

**The deployed site is a mixed-generation build, and the boundary is not the stylesheet.**
`site/dist` is tracked (it is what Vercel serves), so 331 pages were committed by the
implementation tasks and 25 of the 332 did not change:

```
deployed site/dist, per year:
  2016 29/29 2018 32/32 2019 28/28 2020 34/34 2021 30/30
  2022 35/35 2023 39/39 2024 30/30 2025 26/26 2026 23/23   pages changed
  2017  0/25    <- not rebuilt
```

The consequence is subtler than "25 stale pages". A deployed 2017 page **links the new
`theme.css`** (so it gets the rail anchor and the guide rules) but **carries markup the grid
cannot apply to**:

```
site/dist/sittings/2017-01-09.html               .dsec sections:   0
                                                 (substance 1, qa-pair 10)
fresh build of the same page                     .dsec sections: 142
                                                 (vslist 142, sumcol 142, sumcard 142)
fresh build of a 2026 page                       .dsec sections: 172
```

The deployed 2017 page has **no `.dsec` sections at all** — no grid, no summary column, nothing
for a column guide to bound — because it predates both the `dsec` markup and the rebuilt summary
briefs. Its inline rail JS is older than the content it renders.

Measured on that page, on this build, with the same instrument used everywhere above:

* preview: **3 of 3** sampled states fail, with the bubble clipped vertically — a fresh 2026
  page on the same build fails **0 of 8**;
* guides: the only vertical hairlines painted are the left/right borders of the "Jump to a
  question" card (x=214/1225, 198px tall, i.e. the card's own box) — not column guides. The
  frame is correctly **not** covered by hairlines, because there are no columns on it to bound.

So the deliverable reaches 306 of 331 deployed sitting pages, and it does not degrade the 25 it
does not reach: they fail identically on the pristine build, before this change. This is the
same root cause already on the board as `t_79a0e9e2` (site/dist 27 files out of sync); what the
root card adds is that **the failure mode is a page that half-adopts the new build** — new
stylesheet, old markup, old JS — which is why "the 2017 pages look wrong" cannot be diagnosed
from the stylesheet alone, and why that card should be read as a release blocker for the 2017
archive rather than as cosmetic staleness.

## 5. Caveats and hazards for whoever picks this up

1. **Frame byte-identity is not an instrument.** `tools/measure_layout.py` screenshots are not
   byte-stable across sessions at 1440/1920 (1024/1280 happened to reproduce; a re-run of 1024
   did not). Geometry and the counted pixel differences are stable and are what the checks
   assert; do not diff frames across sessions and call the difference a regression.
   `tools/qa_pixels.py`'s same-session self-check is the valid form of that test and passes.
2. **`tools/compare_renders.py:28` hardcodes a sibling worktree path**
   (`/Users/shawnlin/parsnips/.worktrees/t_b764176e`). It resolves today; it breaks the moment
   that worktree is pruned. `tools/qa_frames.py` imports from it, so both break together. Three
   lines to fix with `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`, as every
   other tool in `tools/` already does.
3. **Two acceptance criteria remain unmeetable as written** and are reported N/A with evidence
   rather than silently dropped: there is no dark theme in this codebase, and there is no lint
   config, type-check or test suite. The three gates that exit 1 do so identically on a pristine
   checkout.
4. **D6 (the 760px breakpoint collision) is still live, and the guides make it visible.** At
   exactly 760px `min-width:760px` and `max-width:760px` both match, the reading column collapses
   to 204px, and a guide is drawn over it. Suppressing the guide there would make it lie about
   the layout, so it is drawn. `t_bb9c77d0` owns the decision.
5. **The `--content-max`/`--content-pad` aliases are load-bearing.** Remove them and
   `--rail-inset` stops resolving and the rail falls back to the viewport edge — the original
   overlap defect, D1.

## 6. How to reproduce

```
git -C <worktree> checkout parsnips/t_a1329966-ui-improvements-for-columns-on-desktop
python3 site/build_site.py /tmp/merged                      # fresh build of this generator
python3 -m http.server 8477 --bind 127.0.0.1 --directory site/dist          # the deployed artifact
python3 -m http.server 8478 --bind 127.0.0.1 --directory <pristine>/site/dist
# <pristine> = a worktree at 994a1df (main) with `python3 site/build_site.py <pristine>/site/dist`

python3 tools/measure_layout.py     http://127.0.0.1:8477/sittings/2026-08-04.html \
        --out docs/layout-root --prefix after --widths 1024,1280,1440,1920
python3 tools/compare_layout.py     docs/layout-audit/baseline-measurements.json \
        docs/layout-root/measurements.json
python3 tools/check_column_guides.py http://127.0.0.1:8477/sittings/2026-08-04.html \
        docs/layout-root
python3 tools/check_rail_geometry.py http://127.0.0.1:8477
python3 tools/check_rail_preview.py  http://127.0.0.1:8477 http://127.0.0.1:8478
python3 tools/compare_mobile.py      http://127.0.0.1:8477 http://127.0.0.1:8478 390,740,759,760,761
python3 tools/qa_pixels.py           http://127.0.0.1:8477 http://127.0.0.1:8478
python3 tools/qa_console.py          http://127.0.0.1:8477
python3 tools/qa_gates.py            http://127.0.0.1:8477 http://127.0.0.1:8478
```

Raw output of every one of those runs is in `docs/layout-root/raw-*.log`. Ports 8477–8479 were
chosen because 8435 is held by two stale servers from other worktrees and cannot be trusted;
`lsof -nP -iTCP:8435 -sTCP:LISTEN` shows them.
