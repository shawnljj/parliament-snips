#!/usr/bin/env python3
"""Frame-level regression check: the MERGED tree (guides ON) against a build with the guides OFF.

tools/compare_mobile.py proves the phone/tablet computed styles and rects are identical, which is
the stronger statement about CSS. This is the complementary one the audit's screenshot set
implies: the actual PIXELS at each breakpoint, captured from two servers serving two real dists.

WHAT THE TWO SIDES MUST BE -- this is a precondition, not a preference, and the tool checks
it before it compares anything:

  * the "after" side is the merged tree, i.e. the guides DRAW there;
  * the "before" side is a build with the guides OFF, and otherwise the same page geometry.
    `tools/make_noguides_dist.py` produces exactly that side from a copy of the merged build.

Passing a "before" build that also carries the guides makes every width above 760 report 0
changed pixels -- not a bug in the pages, but the absence of the variable this test exists to
observe. The run then exits 1 with "UNEXPECTED", which reads like a regression and is not one.
A "before" build can also be unusable for a second, unrelated reason: a dist whose sitting pages
cannot expand their `skipped/*.json` payloads renders about 4x shorter (measured: 56 041 vs
217 634 px at 1024), so every width is skipped as "not like-for-like". The preflight below
reports which of these it is, instead of leaving it to be guessed.

WIDTHS: 760 is a PHONE width -- the desktop layer is `min-width:761px` and the phone block is
`max-width:760px` (audit D6, fixed by t_bb9c77d0), so 390/759/760 take the phone path and must
be byte-identical to a phone-path baseline, while 761 and above differ because the guides draw
there. A PRE-CHANGE build is not a valid "before" side at 760: it renders 760 as desktop, so the
two sides are not like-for-like. That case is reported as LAYOUT MOVED rather than failed, and
asserted by `tools/check_breakpoints.py`.

A difference at 760 or below would be a regression; no difference above it would mean nothing
rendered.

Determinism: the rail's bubble/pill and the resume toast are transient chrome, and the scroll-spy
highlights whatever is nearest the reading line. Both are pinned off before capture, and a
self-check captures the SAME url twice and requires zero difference -- if that ever fails, this
comparison is not measuring the stylesheet.

Usage:
    python3 tools/qa_pixels.py <after-base-url> <before-base-url> [widths]
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


GUIDE_PROBE = r"""(() => {
  // Is a guide actually DRAWN here? Two independent facts, because either alone can lie:
  // the declaration must resolve, and a rule must have a box to paint on. Measured rather than
  // inferred from the stylesheet text: a rule can be present and still not draw.
  const dsec = document.querySelector('.dsec');
  const cs = dsec ? getComputedStyle(dsec) : null;
  const before = dsec ? getComputedStyle(dsec, '::before') : null;
  const w = cs ? cs.getPropertyValue('--col-rule-w').trim() : '';
  const shadow = before ? (before.boxShadow || '') : '';
  return JSON.stringify({
    url: location.href,
    dsecs: document.querySelectorAll('.dsec').length,
    ruleWidth: w,
    pseudoBoxShadow: shadow.slice(0, 80),
    // The guides are an inset box-shadow built from --col-rule-w; with the token undefined the
    // shadow resolves to none.
    guideDrawn: !!(w && shadow && shadow !== 'none'),
    gridTracks: cs ? (cs.gridTemplateColumns || 'none') : null,
    scrollHeight: document.documentElement.scrollHeight,
  });
})()"""


def guide_state(base, page="/sittings/2026-08-04.html", width=1024):
    """Read whether the guides draw, and the page's scroll height, from a real page load."""
    c = CDP(CHROME, base + page, width, 900, 9280)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=width, height=900,
               deviceScaleFactor=1, mobile=False)
        c.eval(f"location.replace({json.dumps(base + page)})")
        time.sleep(1.8)
        return json.loads(c.eval(GUIDE_PROBE))
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

    # Preflight: the two sides must differ in the guides and in nothing else that matters --
    # but only for the widths where the guides are the thing being observed. Below 761 the
    # guides do not draw by design, so "the two sides must differ" is not a precondition there;
    # what is required is that they are the SAME page, or the frames are not comparable.
    # Without this preflight the wrong "before" side reports 0 changed px above 760 and reads as
    # a regression; with it, the run says which precondition failed. Done before the captures,
    # because a wrong pairing is not worth 8 pairs of page loads.
    desktop = [w for w in widths if w not in MUST_BE_SAME]
    ga = guide_state(after)
    gb = guide_state(before)
    print(f"preflight  after : guides={ga['guideDrawn']} rule-w={ga['ruleWidth']!r} "
          f"dsec={ga['dsecs']} tracks={ga['gridTracks']} h={ga['scrollHeight']}")
    print(f"preflight  before: guides={gb['guideDrawn']} rule-w={gb['ruleWidth']!r} "
          f"dsec={gb['dsecs']} tracks={gb['gridTracks']} h={gb['scrollHeight']}")
    if desktop:
        print(f"preflight: {len(desktop)} width(s) above the phone/tablet pair "
              f"({','.join(str(w) for w in desktop)}) are compared, so the guides must be the "
              f"only difference")
    if ga["guideDrawn"] != gb["guideDrawn"] and not desktop:
        print("preflight: the two sides differ in the guides, which is not observable at "
              "390/759 by design; only the frame equality below is being asserted")
    if desktop and not ga["guideDrawn"]:
        fails.append(f"the AFTER side ({after}) does not draw the guides "
                     f"(rule-w={ga['ruleWidth']!r}) -- this test observes the guides above 760, "
                     f"so its 'after' side must be the tree that has them")
    if desktop and gb["guideDrawn"]:
        fails.append(f"the BEFORE side ({before}) ALSO draws the guides -- so there is no "
                     f"difference above 760 for this test to find, and every desktop width will "
                     f"report 0 changed px. Pass a build with the guides off.")
    if ga["scrollHeight"] != gb["scrollHeight"]:
        fails.append(f"the two sides are not the same page: scrollHeight {ga['scrollHeight']} "
                     f"vs {gb['scrollHeight']} at 1024 -- they differ by more than the guides "
                     f"(a different tree, or a dist missing the runtime-expanded "
                     f"skipped/*.json payloads). Every width will be skipped as "
                     f"'not like-for-like'.")
    if fails:
        print()
        for f in fails:
            print(f"  - {f}")
        print()
        print("Refusing to compare: fix the pairing above. Nothing was captured.")
        return 2
    print()

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
    compared, same_count, diff_count = [], 0, 0
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
        compared.append(w)
        same_count += 1 if same else 0
        diff_count += 0 if same else 1
        print(f"{w:>6}  {verdict:<34} {n} / {total}")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    # Report only what was actually compared. The old wording asserted "every desktop width
    # differs" unconditionally, which is a false claim when the run was given just the
    # phone/tablet pair -- the same class of error as the defect this task is about.
    n_same = len(MUST_BE_SAME & set(compared))
    n_diff = len([w for w in compared if w not in MUST_BE_SAME])
    parts = []
    if n_same:
        parts.append(f"{n_same} phone/tablet width(s) identical, as required")
    if n_diff:
        parts.append(f"{n_diff} width(s) above 760 differ, so the guides are rendering")
    print("PASS: " + "; ".join(parts) + f". (compared: "
          f"{','.join(str(w) for w in compared)}; identical={same_count} "
          f"differing={diff_count})")
    if not n_diff:
        print("NOTE: no width above 760 was compared, so this run does NOT show that the "
              "guides render. Pass the full width list for that.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
