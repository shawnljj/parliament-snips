#!/usr/bin/env python3
"""Tally the preview-gesture results per build from the captured log."""
import sys

cur = None
counts = {}
for line in open(sys.argv[1]):
    if line.startswith("====="):
        cur = line.strip().replace("=====", "").strip()
        counts[cur] = [0, 0, 0]
    elif cur and ("tick" in line or "no tick pending" in line):
        counts[cur][0] += 1
        if "<<" in line:
            counts[cur][1] += 1
        elif "OK" in line:
            counts[cur][2] += 1

tw = tf = bw = bf = 0
for k, v in counts.items():
    print(f"{k:24s} samples={v[0]:2d} FAIL={v[1]:2d} OK={v[2]:2d}")
    if "WORKTREE" in k:
        tw += v[0] - 1  # skip the 'no tick pending' line
        tf += v[1]
    else:
        bw += v[0] - 1
        bf += v[1]
print()
print(f"WORKTREE: {tf} failures out of {tw} preview states")
print(f"BASELINE: {bf} failures out of {bw} preview states")
