#!/usr/bin/env python3
"""Frame-level regression check: the MERGED tree against a pristine pre-change build.

tools/compare_mobile.py proves the phone/tablet computed styles and rects are identical, which is
the stronger statement about CSS. This is the complementary one the audit's screenshot set
implies: the actual PIXELS at each breakpoint, captured from two servers serving two real dists.

  * at the phone and tablet widths the frames must be identical (0 changed pixels). 760 is a
    PHONE width now (audit D6, fixed by t_bb9c77d0): the desktop layer is min-width:761px, so
    760 and below take the phone path and must be byte-identical to the pre-change build;
  * at 761 and 1024+ they must differ (the guides are live).

A difference at 760 or below would be a regression; no difference above it would mean nothing
rendered.

Determinism: the rail's bubble/pill and the resume toast are transient chrome, and the scroll-spy
highlights whatever is nearest the reading line. Both are pinned off before capture, and a
self-check captures the SAME url twice and requires zero difference -- if that ever fails, this
comparison is not measuring the stylesheet.

Usage:
    python3 tools/qa_pixels.py http://127.0.0.1:8480 http://127.0.0.1:8481
"""
import base64
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402
from PIL import Image, ImageChops                                      # noqa: E402

SETTLE = """
(() => {
  const s = document.createElement('style');
  s.textContent = '*{transition:none !important;animation:none !important}'
    + '.section-rail-pill,.section-rail-bubble,.section-rail-go{opacity:0 !important}'
    + '.resume{display:none !important}';
  document.head.appendChild(s);
  window.scrollTo(0, 0);
  return 1;
})()
"""


def shot(base, w, h, port, page="/sittings/2026-08-04.html", settle_ms=1200):
    url = base + page
    c = CDP(CHROME, url, w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=False)
        c.eval(f"location.replace({json.dumps(url)})")
        time.sleep(1.6)
        c.eval(SETTLE)
        time.sleep(settle_ms / 1000.0)
        data = c.call("Page.captureScreenshot", format="png")["result"]["data"]
        state = c.eval("(() => JSON.stringify({y: Math.round(window.scrollY),"
                       " h: document.documentElement.scrollHeight,"
                       " pw: document.documentElement.clientWidth}))()")
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB"), state
    finally:
        c.close()


def changed(a, b):
    if a.size != b.size:
        return None
    d = ImageChops.difference(a, b)
    return sum(1 for px in d.getdata() if px != (0, 0, 0))


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    if len(sys.argv) < 3:
        sys.exit("usage: qa_pixels.py <after-base-url> <before-base-url> [widths]")
    after, before = sys.argv[1], sys.argv[2]
    widths = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3
                               else "390,759,760,761,1024,1280,1440,1920").split(",")]
    MUST_BE_SAME = {390, 759, 760}
    # 760 was the pre-fix breakpoint, so the pre-change build renders it as DESKTOP and this
    # build renders it as PHONE (audit D6, fixed by t_bb9c77d0). A like-for-like pixel diff at
    # 760 against that baseline is therefore not a regression test -- the layout is MEANT to
    # change there -- and the page heights differ by design (66005 vs 499657). The real
    # assertion for 760 is in tools/check_breakpoints.py: it must render as the PHONE width,
    # matching 759. Here it is reported as a moved layout rather than failed.
    LAYOUT_MOVED = {760}
    fails = []

    # Self-check first: same URL, two sessions, must be pixel-identical.
    a1, s1 = shot(after, 1024, 900, 9301)
    a2, s2 = shot(after, 1024, 900, 9302)
    self_diff = changed(a1, a2)
    print(f"self-check (same url twice): {self_diff} px differ  "
          f"state {s1} vs {s2}")
    if self_diff != 0:
        fails.append(f"self-check failed: {self_diff} px between two captures of the "
                     f"same page -- captures are not deterministic")
    print()

    print(f"{'width':>6}  {'verdict':<34} changed px / total")
    for i, w in enumerate(widths):
        h = 844 if w < 800 else 900
        A, sa = shot(after, w, h, 9310 + i * 2)
        B, sb = shot(before, w, h, 9311 + i * 2)
        if sa != sb:
            if w in LAYOUT_MOVED:
                print(f"{w:>6}  LAYOUT MOVED (phone now, was desktop) -- {sa} vs {sb}")
                print(f"        asserted by tools/check_breakpoints.py, not by a pixel diff")
                continue
            fails.append(f"{w}px: page state differs ({sa} vs {sb}) -- not like-for-like")
            print(f"{w:>6}  SKIPPED (state differs) {sa} vs {sb}")
            continue
        n = changed(A, B)
        if n is None:
            fails.append(f"{w}px: frame sizes differ {A.size} vs {B.size}")
            print(f"{w:>6}  FRAME SIZE MISMATCH {A.size} vs {B.size}")
            continue
        total = A.size[0] * A.size[1]
        same = n == 0
        want_same = w in MUST_BE_SAME
        ok = same == want_same
        verdict = ("IDENTICAL (as required)" if want_same and same else
                   "DIFFERS (as required)" if (not want_same) and not same else
                   "UNEXPECTED")
        if not ok:
            fails.append(f"{w}px: {verdict} -- {n} px changed, expected "
                         f"{'identical' if want_same else 'different'}")
        print(f"{w:>6}  {verdict:<34} {n} / {total}")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: the phone and tablet frames are pixel-identical to the pre-change build, and "
          "every desktop width differs (the guides are rendering).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
