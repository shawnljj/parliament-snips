#!/usr/bin/env python3
"""Make the "before" side that tools/qa_pixels.py needs: a build with the guides OFF.

qa_pixels.py compares the merged tree (guides ON) against a build with the guides OFF, and
requires the guides to be the only difference. A pre-change build is not that side: measured, its
`theme.css` is byte-identical to the merged one, so it carries the guides too and every desktop
width reports 0 changed px -- which reads as a regression and is not one. (A dist can also be
unusable for an unrelated reason: one whose pages cannot expand their runtime-fetched
`skipped/*.json` payloads renders 56 041 px at 1024 against 217 634, and every width is then
skipped as "not like-for-like".)

This removes the guide declarations from a COPY of the merged build, and makes the two
pseudo-elements stop being GRID ITEMS, so the page geometry really is unchanged -- which is
exactly the documented pairing. It prints every declaration it removes.

WHY THE SECOND PART IS NEEDED, and it is a defect this tool shipped with. Removing only the
two box-shadow rules left the base rule

    .dsec::before,.dsec::after{content:"";pointer-events:none;position:relative;
      z-index:-1;align-self:stretch;justify-self:stretch}

so both pseudo-elements were still generated -- `content:""` makes a box -- and still grid
items. On the tree this tool was validated on that cost nothing, because `.dsec` already
resolved to a second grid row: the orphan trailing `<li>` that `t_a15acd97`'s D16 fix
removed. With D16 landed the section is a single row, so the two auto-placed pseudo-elements
create a new implicit row, and `.dsec{gap:var(--col-gap)}` is a ROW gap as well as a column
gap. Measured on the landed tree, page 2026-08-04.html at 1024:

    guides ON : grid-template-rows "741.375px"      scrollHeight 209960
    guides OFF: grid-template-rows "741.375px 0px"  scrollHeight 215808
    215808 - 209960 = 5848 = 172 sections x 34px (--col-gap), exactly

`tools/qa_pixels.py`'s preflight catches that and REFUSES the pairing (exit 2, nothing
captured), which is correct behaviour -- this tool was the wrong half. `content:none` means
the pseudo-element is not generated at all: no box, no grid item, geometry identical.

    cp -R site/dist /tmp/dist-noguides          # or: ditto site/dist /tmp/dist-noguides
    python3 tools/make_noguides_dist.py /tmp/dist-noguides
    python3 -m http.server 8488 --bind 127.0.0.1 --directory /tmp/dist-noguides
    python3 tools/qa_pixels.py http://127.0.0.1:8480 http://127.0.0.1:8488
"""
import os
import re
import sys

# The base rule, and the one token in it that decides whether a box exists at all. Anchored on
# the SELECTOR: `.osw i::after{content:""...}` appears earlier in the stylesheet, so a bare
# `content:""` substitution silently disables the wrong rule.
BASE_SELECTOR = ".dsec::before,.dsec::after"
NOT_GENERATED = BASE_SELECTOR + '{content:""'
GENERATED = BASE_SELECTOR + "{content:none"


def strip(path):
    s = open(path, encoding="utf-8").read()
    drawn = []
    base = []

    # 1. The two pseudo-element rules that DRAW the rules -- the only box-shadow users of
    #    --col-rule-w, and so the only things that paint a guide.
    def drop_rule(m):
        body = m.group(0)
        if "col-rule-w" in body and "box-shadow" in body:
            drawn.append(body.strip().split("{")[0].strip()
                        .replace("\n", " ")[:90])
            return ""
        return body

    out = re.sub(r"[^{}]*\{[^{}]*\}", drop_rule, s)

    # 2. Stop the pseudo-elements being generated, so the rules above cannot leave two
    #    auto-placed grid items behind and change the section's row count. Done as a single
    #    token substitution on the base rule's own selector, NOT by deleting the rule: a
    #    regex that scans for `selector{...}` splits comments in half, because theme.css
    #    contains braces inside comments (the D12 note quotes `.vs-text{max-width:74ch}`).
    if NOT_GENERATED in out:
        out = out.replace(NOT_GENERATED, GENERATED, 1)
        base.append(f"{BASE_SELECTOR}  content:\"\" -> content:none")

    # 3. The two custom properties on :root, removed individually so the rest of the token block
    #    (--col-wrap, --col-pad, --col-gap, --col-section-gap, ...) is untouched.
    props = []
    for name in ("--col-rule-w", "--col-rule"):
        new = re.sub(rf"\n?[ \t]*{re.escape(name)}[ \t]*:[^;]*;", "", out)
        if new != out:
            props.append(name)
        out = new

    open(path, "w", encoding="utf-8").write(out)
    return drawn, props, base, len(s) - len(out)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: make_noguides_dist.py <dist-dir>   (a COPY; it is edited in place)")
    theme = os.path.join(sys.argv[1], "theme.css")
    if not os.path.exists(theme):
        sys.exit(f"no theme.css at {theme}")
    if "--col-rule" not in open(theme, encoding="utf-8").read():
        print("these guides are already absent; nothing to strip")
        return 0
    drawn, props, base, n = strip(theme)
    print(f"rules removed ({len(drawn)}):")
    for sel in drawn:
        print(f"  - {sel}")
    print(f"custom properties removed ({len(props)}): {props}")
    for b in base:
        print(f"pseudo-elements no longer generated: {b}")
    if not base:
        print("WARNING: could not find the base .dsec::before,.dsec::after rule -- the two")
        print("         pseudo-elements may still be generated and add an implicit grid row.")
    print(f"chars removed: {n}")
    print("This dist now renders WITHOUT the guides; the page geometry is unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
