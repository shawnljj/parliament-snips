#!/usr/bin/env python3
"""Make the "before" side that tools/qa_pixels.py needs: a build with the guides OFF.

qa_pixels.py compares the merged tree (guides ON) against a build with the guides OFF, and
requires the guides to be the only difference. A pre-change build is not that side: measured, its
`theme.css` is byte-identical to the merged one, so it carries the guides too and every desktop
width reports 0 changed px -- which reads as a regression and is not one. (A dist can also be
unusable for an unrelated reason: one whose pages cannot expand their runtime-fetched
`skipped/*.json` payloads renders 56 041 px at 1024 against 217 634, and every width is then
skipped as "not like-for-like".)

This removes the guide declarations from a COPY of the merged build and leaves everything else
alone, which is exactly the documented pairing. It prints every declaration it removes.

    cp -R site/dist /tmp/dist-noguides          # or: ditto site/dist /tmp/dist-noguides
    python3 tools/make_noguides_dist.py /tmp/dist-noguides
    python3 -m http.server 8488 --bind 127.0.0.1 --directory /tmp/dist-noguides
    python3 tools/qa_pixels.py http://127.0.0.1:8480 http://127.0.0.1:8488
"""
import os
import re
import sys


def strip(path):
    s = open(path, encoding="utf-8").read()
    drawn = []

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

    # 2. The two custom properties on :root, removed individually so the rest of the token block
    #    (--col-wrap, --col-pad, --col-gap, --col-section-gap, ...) is untouched.
    props = []
    for name in ("--col-rule-w", "--col-rule"):
        new = re.sub(rf"\n?\s*{re.escape(name)}\s*:[^;]*;", "", out)
        if new != out:
            props.append(name)
        out = new

    open(path, "w", encoding="utf-8").write(out)
    return drawn, props, len(s) - len(out)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: make_noguides_dist.py <dist-dir>   (a COPY; it is edited in place)")
    theme = os.path.join(sys.argv[1], "theme.css")
    if not os.path.exists(theme):
        sys.exit(f"no theme.css at {theme}")
    if "--col-rule" not in open(theme, encoding="utf-8").read():
        print("these guides are already absent; nothing to strip")
        return 0
    drawn, props, n = strip(theme)
    print(f"rules removed ({len(drawn)}):")
    for sel in drawn:
        print(f"  - {sel}")
    print(f"custom properties removed ({len(props)}): {props}")
    print(f"chars removed: {n}")
    print("This dist now renders WITHOUT the guides; the page geometry is unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
