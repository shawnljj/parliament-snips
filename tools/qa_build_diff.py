#!/usr/bin/env python3
"""Diff a new build against a reference build and prove WHICH pages changed and how.

The D15 fix edits render_archive(), which is generator-wide code, so "only the archive changed"
is a claim that has to be measured. And within the archive the claim is sharper still: the ONLY
change should be the removal of the .pbar block -- no other byte of a 332-page build may move.

    python3 tools/qa_build_diff.py <reference_dist> [new_dist]
"""
import difflib
import glob
import os
import sys


def files(dist):
    return {os.path.relpath(p, dist)
            for p in glob.glob(os.path.join(dist, "**", "*"), recursive=True)
            if os.path.isfile(p)}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: qa_build_diff.py <reference_dist> [new_dist]")
    ref = sys.argv[1]
    new = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site", "dist")

    rf, nf = files(ref), files(new)
    only_ref = sorted(rf - nf)
    only_new = sorted(nf - rf)
    print(f"reference : {ref}  ({len(rf)} files)")
    print(f"new       : {new}  ({len(nf)} files)")
    print(f"only in reference: {len(only_ref)}")
    for f in only_ref[:20]:
        print(f"  - {f}")
    print(f"only in new      : {len(only_new)}")
    for f in only_new[:20]:
        print(f"  + {f}")

    changed, identical = [], 0
    for rel in sorted(rf & nf):
        a = open(os.path.join(ref, rel), "rb").read()
        b = open(os.path.join(new, rel), "rb").read()
        if a == b:
            identical += 1
        else:
            changed.append((rel, a, b))
    print(f"\nidentical files (byte-for-byte): {identical} of {len(rf & nf)}")
    print(f"CHANGED files: {len(changed)}")
    for rel, a, b in changed:
        at = a.decode("utf-8", "replace").splitlines()
        bt = b.decode("utf-8", "replace").splitlines()
        d = [l for l in difflib.unified_diff(at, bt, "before", "after", n=0)
             if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        print(f"\n  {rel}: -{sum(1 for l in d if l[0] == '-')} "
              f"+{sum(1 for l in d if l[0] == '+')} lines")
        for l in d[:14]:
            print(f"    {l[:120]}")

    # The finding this tool exists to state. The archive's change must be EXACTLY the removal of
    # the .pbar block: the new file must equal the reference with that one block excised. String
    # equality, not a diff heuristic -- a heuristic that only inspects removed line prefixes
    # cannot tell the pbar's own closing </div> from a different one.
    print()
    print("VERDICT:")
    non_archive = [c[0] for c in changed if c[0] != "sittings/index.html"]
    print(f"  archive changed       : {'sittings/index.html' in [c[0] for c in changed]}")
    print(f"  anything else changed : {len(non_archive)} other file(s)")
    bad = []
    if non_archive:
        bad.append(f"{len(non_archive)} file(s) other than the archive changed")
    for rel, a, b in changed:
        if rel != "sittings/index.html":
            continue
        at = a.decode("utf-8", "replace")
        bt = b.decode("utf-8", "replace")
        marker = '  <div class="pbar"'
        start = at.find(marker)
        if start < 0:
            bad.append("the reference archive has no .pbar block to compare against")
            continue
        end = at.find("</div>", start) + len("</div>")
        block = at[start:end]
        after = end
        while after < len(at) and at[after] == "\n":       # the blank line the block left behind
            after += 1
        excised = at[:start] + at[after:]
        print(f"  archive: removed {len(block.splitlines())} lines, added 0")
        for l in block.splitlines():
            print(f"    - {l[:110]}")
        print(f"  reference-minus-pbar == new archive : {excised == bt}")
        if excised != bt:
            bad.append("the archive diff is not exactly the removal of the .pbar block "
                       "(a change was made elsewhere on the page)")
    if bad:
        print(f"\n{len(bad)} FAILURE(S):")
        for x in bad:
            print("  -", x)
        return 1
    print("\nOK: the archive lost exactly its .pbar block, and no other page or byte moved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
