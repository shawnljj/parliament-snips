# Integrating `wt/t_a15acd97` with `t_2a44f62f` (D13)

**Keep BOTH bottom-chrome token layers.** Either side alone is silently wrong, and neither
branch's own gate can see it, because each gate passes on its own tree.

This file exists because the round-1 review of `t_a15acd97` measured the hazard and the
information had nowhere durable to live: the two cards edit the same token block, and a naive
"take mine" or "take theirs" resolution produces a page that looks fine and fails a gate that
neither branch runs.

## The conflict

`site/dist/theme.css` is generated from the STYLE block in `site/build_site.py`, so the conflict
appears twice — **3 hunks in each file, the same 3**, all in the bottom-chrome token layer:

| # | token / rule | this card (`t_a15acd97`) | D13 (`t_2a44f62f`) |
|---|---|---|---|
| 1 | `.totop` `bottom` | `calc(var(--chrome-h) + 16px)` | — (kept the literal) |
| 2 | `.pbar` block | the column work's `:root` tokens | `--pbar-h:26px`, `z-index`, `--chrome-h` |
| 3 | `.resume` comment | records that D14's desktop claim does not reproduce | records the 26+12 arithmetic |

## Why each single-sided resolution is wrong

- **Keep only D13's side.** `--z-pbar`, `--z-totop` and `--z-resume` are referenced by rules this
  card keeps, but the declarations disappear with D13's hunk. The bar, the button and the toast
  all fall back to `z-index: auto` — they stop being ordered against the page content at all.
- **Keep only this card's side.** `--chrome-h` is never set to the bar's height on desktop.
  `.totop{bottom:calc(var(--chrome-h) + 16px)}` therefore collapses to `16px`, and **D13's
  10px of button/bar overlap comes straight back at every desktop width** — the exact defect
  `t_2a44f62f` was opened to fix, reintroduced by a merge that each branch's gate calls green.

## The resolution, and how it was verified

Resolution is applied to **both** files from one script, `tools/resolve_merge_a15_d13.py` (kept in
the tree so the instruction is runnable rather than advice), so the generated stylesheet cannot
drift from its generator:

    python3 tools/resolve_merge_a15_d13.py <worktree>/site/build_site.py <worktree>/site/dist/theme.css

It refuses to run unless it finds exactly 3 conflicts in each file, and it checks that the HEAD
side of each hunk is the one it knows how to resolve — so if either branch moves, it fails loudly
instead of resolving the wrong thing.

1. `.totop` → `bottom:calc(var(--chrome-h) + 16px)` **and** `z-index:var(--z-totop)`.
2. the `.pbar` block → `:root{--chrome-h:var(--pbar-h)}` **plus** `z-index:var(--z-pbar)`. The
   `--pbar-h:26px` re-statement is **dropped**, because on this branch `--pbar-h` already has a
   single home in the main `:root`; keeping a second copy is the second-place-to-keep-in-step that
   D13's own comment argues against.
3. the `.resume` comment → this card's text (it records the D14 measurement) merged with D13's
   point that the coupling is now arithmetic. The rule itself was byte-identical on both sides:
   `.resume{bottom:calc(var(--pbar-h) + 12px)}`.

Measured on a build of the merge:

```
check_bottom_chrome.py (D13's own gate)  exit 0, PASS   0px button/bar overlap at
                                                        761/1024/1280/1440/1920
check_bottom_chrome.py on the PRISTINE control
                                         exit 1, FAIL   10px overlap, SPAN.pbar-txt at the
                                                        button's bottom edge
check_outline.py (this card's gate)      exit 0         ALL ASSERTIONS HOLD
check_rail_geometry.py                   exit 0         D1/D3/D4 hold at 1024/1440/1920
generator reproducibility                byte-identical  a rebuild of site/build_site.py
                                                        reproduces the committed theme.css
                                                        (sha 5cb3df455940)
```

The D13 gate discriminating (0 on the merge, 10px on the pristine base) is the part that matters:
it means the merged tree really does carry D13's fix, not merely the absence of a failure.
