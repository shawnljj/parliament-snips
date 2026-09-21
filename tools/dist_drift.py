#!/usr/bin/env python3
"""Establish, by building both trees, exactly which site/dist files are (a) pre-existing drift
between the committed dist and the generator, and (b) touched by the change on this branch.

Why this is needed before committing anything: `site/dist` is tracked and deployed, and the
generator is the source of truth. A rebuild therefore rewrites two very different kinds of file
-- ones this branch changes, and ones that were ALREADY out of sync with the generator (briefs
published after the last rebuild). Committing the second kind smuggles an unreviewed content
change into a CSS/markup task. This tool separates them mechanically instead of by eyeballing a
274-file diff.

    python3 tools/dist_drift.py <pristine-build-dir> <branch-build-dir> <committed-dist-dir>
"""
import hashlib
import os
import sys


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def walk(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            out[os.path.relpath(p, root)] = p
    return out


def main():
    pristine, branch, committed = sys.argv[1], sys.argv[2], sys.argv[3]
    P, B, C = walk(pristine), walk(branch), walk(committed)
    names = sorted(set(P) | set(B) | set(C))
    mine, drift, same = [], [], []
    only = []
    for n in names:
        hp = sha(P[n]) if n in P else None
        hb = sha(B[n]) if n in B else None
        hc = sha(C[n]) if n in C else None
        if hp is None or hb is None or hc is None:
            only.append((n, hp is not None, hb is not None, hc is not None))
            continue
        if hb != hp:
            mine.append(n)                     # the branch changes this file
        elif hc != hp:
            drift.append(n)                    # pre-existing: committed != generator
        else:
            same.append(n)
    print(f"files compared            : {len(names)}")
    print(f"this branch changes       : {len(mine)}")
    print(f"pre-existing drift        : {len(drift)}")
    print(f"unchanged (committed==gen): {len(same)}")
    if only:
        print(f"present in only some trees: {len(only)}")
        for n, a, b, c in only[:20]:
            print(f"    {n}  pristine={a} branch={b} committed={c}")
    print()
    print("DRIFT (committed dist differs from the generator at BOTH builds -- not ours to commit):")
    for n in drift[:60]:
        print(f"    {n}")
    if len(drift) > 60:
        print(f"    ... and {len(drift) - 60} more")
    print()
    print("TOUCHED BY THIS BRANCH:")
    for n in mine[:60]:
        print(f"    {n}")
    if len(mine) > 60:
        print(f"    ... and {len(mine) - 60} more")


if __name__ == "__main__":
    main()
