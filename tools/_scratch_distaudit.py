#!/usr/bin/env python3
"""Separate MY change from the pre-existing dist staleness.

The working tree's site/dist was rebuilt by this task, so git sees every page change. Two very
different causes are mixed in there:

  A. the CSS/JS this task actually edited -- the rail block and railPlacePreview();
  B. pages whose CONTENT is stale in the committed dist, i.e. the deployed file predates briefs
     that have since shipped. Rebuilding rewrites them regardless of any CSS work.

If (B) is real, rebuilding the PRISTINE baseline worktree must rewrite the same pages, with the
same magnitude. That is the test: same file set => pre-existing, not mine.
"""
import os
import re
import subprocess

WT = "/Users/shawnlin/parsnips/.worktrees/t_140a0b87"
BASE = "/Users/shawnlin/.hermes/profiles/dev_parsnips/cache/scratch/baseline-140a0b87"

MARKERS = ["section-rail-fill", "railPlacePreview", "rail-tick-w", "--rail-clear",
           "z-rail-bubble"]


def changed(root):
    out = subprocess.run(["git", "diff", "--numstat", "--", "site/dist/sittings/"],
                         cwd=root, capture_output=True, text=True).stdout
    rows = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            try:
                rows[parts[2]] = (int(parts[0]), int(parts[1]))
            except ValueError:
                pass
    return rows


a = changed(WT)
b = changed(BASE)

# (B): pages the pristine rebuild ALONE rewrites -- proof of content staleness.
pre = {k: v for k, v in b.items() if v[0] + v[1] > 40}
mine_only = {k: v for k, v in a.items() if k not in pre and v[0] + v[1] > 40}

print(f"worktree: {len(a)} pages differ from committed dist")
print(f"baseline: {len(b)} pages differ from committed dist (rebuild of PRISTINE HEAD)")
print()
print(f"(B) PRE-EXISTING content staleness: {len(pre)} pages rewritten by the pristine rebuild too")
for k, v in sorted(pre.items())[:8]:
    print(f"      {k}  +{v[0]}/-{v[1]}")
print()
print(f"(A) pages differing ONLY in the worktree, by >40 lines: {len(mine_only)}")
for k, v in sorted(mine_only.items())[:12]:
    print(f"      {k}  +{v[0]}/-{v[1]}")

print()
print("=== is the worktree's diff, beyond those pages, confined to the rail CSS/JS? ===")
# For a page that is in BOTH sets, check that the worktree diff touches the rail markers.
sample = sorted(set(pre) & set(a))[:3]
for k in sample:
    d = subprocess.run(["git", "diff", "-U0", "--", f"site/dist/{k}"],
                       cwd=WT, capture_output=True, text=True).stdout
    added = [ln for ln in d.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    hits = {m: sum(1 for ln in added if m in ln) for m in MARKERS}
    print(f"  {k}: +{len(added)} lines; rail-marker hits {hits}")

print()
print("=== and for a page in the worktree but NOT the baseline (pure CSS/JS change) ===")
pure = [k for k in a if k not in b]
print(f"  {len(pure)} such pages, e.g. {sorted(pure)[:5]}")
if pure:
    k = sorted(pure)[0]
    d = subprocess.run(["git", "diff", "-U0", "--", f"site/dist/{k}"],
                       cwd=WT, capture_output=True, text=True).stdout
    added = [ln for ln in d.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    print(f"  {k}: +{len(added)} lines; first 6:")
    for ln in added[:6]:
        print("     ", ln[:120])
