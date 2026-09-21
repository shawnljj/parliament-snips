#!/usr/bin/env python3
"""Confirm the worktree's dist diff is confined to the rail CSS/JS (fixed paths).

The previous run double-prefixed "site/dist/" onto paths that were already repo-relative, so the
per-file diffs came back empty and the marker check proved nothing. This uses the paths as git
reports them.
"""
import subprocess

WT = "/Users/shawnlin/parsnips/.worktrees/t_140a0b87"
BASE = "/Users/shawnlin/.hermes/profiles/dev_parsnips/cache/scratch/baseline-140a0b87"

MARKERS = ["section-rail-fill", "railPlacePreview", "rail-tick-w", "--rail-clear",
           "z-rail-bubble", "--content-max", "--rail-label"]


def numstat(root):
    out = subprocess.run(["git", "diff", "--numstat", "--", "site/dist/sittings/"],
                         cwd=root, capture_output=True, text=True).stdout
    rows = {}
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) == 3:
            try:
                rows[p[2]] = (int(p[0]), int(p[1]))
            except ValueError:
                pass
    return rows


a, b = numstat(WT), numstat(BASE)
pre = set(b)
pure = sorted(k for k in a if k not in b)
print(f"worktree changed: {len(a)}   baseline changed (pre-existing): {len(b)}")
print(f"pages changed ONLY by this worktree: {len(pure)}")
print()

for k in (pure[:2] + sorted(pre)[:1]):
    d = subprocess.run(["git", "diff", "-U0", "--", k], cwd=WT,
                       capture_output=True, text=True).stdout
    added = [ln[1:] for ln in d.splitlines()
             if ln.startswith("+") and not ln.startswith("+++")]
    removed = [ln[1:] for ln in d.splitlines()
               if ln.startswith("-") and not ln.startswith("---")]
    hits = {m: sum(1 for ln in added if m in ln) for m in MARKERS}
    # Lines added that are NOT rail-related: the real question for a CSS task.
    nonrail = [ln for ln in added
               if not any(m in ln for m in MARKERS) and ln.strip()]
    print(f"--- {k}")
    print(f"    +{len(added)}/-{len(removed)} lines; rail markers: {hits}")
    print(f"    added lines NOT mentioning a rail marker: {len(nonrail)}")
    for ln in nonrail[:6]:
        print(f"      {ln[:130]}")
    print()
