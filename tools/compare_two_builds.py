#!/usr/bin/env python3
"""Compare EVERY element's computed styles and rects between TWO builds.

This is the stronger form of a pixel diff: a pixel comparison cannot see a z-index, a colour
that is not on screen, or a box that is outside the viewport. This snapshots every element in
the DOM and compares all of them, property by property, plus every rect.

Written for card t_2a44f62f (audit D13), where "the phone layout at <=760px is unchanged" is an
acceptance criterion and "unchanged" needed to mean more than "looks the same". It compares the
fixed build against a pristine checkout of main.

It also handles the one class of difference a change like that necessarily introduces: a NEW
INHERITED CUSTOM PROPERTY. Adding `--chrome-h` to :root puts it in the computed style of every
element on the fixed build and on none of the pristine one. That is the defining mark of the
change, not a regression in it, so elements whose ONLY difference is that token are counted
separately -- and every other property difference is still a hard failure. Without this split,
the tool would report ~12,000 "differences" and hide a real one among them.

Usage:

    python3 -m http.server 8436 --directory site/dist &
    python3 -m http.server 8437 --directory ../baseline-main/site/dist &
    python3 tools/compare_two_builds.py http://127.0.0.1:8436 http://127.0.0.1:8437 390,759,760

Pass `--toast` (anywhere after the two bases) to compare with the resume toast ON SCREEN and the
page scrolled to where the button appears. Most changes do not involve the toast and it is
hidden by default so that a toast which renders or not on timing does not make every run differ;
a change that IS about the toast has to ask for it, or the comparison cannot see the change.

Exit code 0 only when no element differs in anything but the new token, and no box moved.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_bottom_chrome import CDP, CHROME                              # noqa: E402

PAGE = "/sittings/2026-08-04.html"

# The tokens this change introduces. Only these may differ; anything else is a failure.
#
# --pbar-h looks like it predates this work, and the audit's card text says "the token already
# exists, --pbar-h:26px". On `main` it DOES NOT: it arrived with the layout branch (commit
# 2141b07, "Anchor the scroll-spy rail to the content grid"). So a fix based on main introduces
# both, and the token that was supposed to pre-exist had to be defined here or the clearance
# could not be derived from anything. Verified: `git show main:site/dist/theme.css | grep
# --pbar-h` finds nothing.
# --totop-h and --chrome-gap come from card t_91ef2ced, which keeps the toast clear of the
# button by the button's own width plus a gap rather than by a literal inset.
NEW_TOKENS = {"--chrome-h", "--pbar-h", "--totop-h", "--chrome-gap"}

SNAP = r"""
(() => {
  const out = [];
  const all = document.querySelectorAll('*');
  for (let i = 0; i < all.length; i++) {
    const e = all[i];
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    const props = [];
    for (let j = 0; j < cs.length; j++) {
      const p = cs[j];
      props.push(p + '=' + cs.getPropertyValue(p));
    }
    out.push({
      tag: e.tagName, cls: String(e.className || '').slice(0, 60),
      box: [r.left, r.top, r.width, r.height].map(v => Math.round(v * 100) / 100).join(','),
      css: props.join(';'),
    });
  }
  return JSON.stringify({ n: out.length, els: out });
})()
"""

# Freeze the transient chrome so two runs of the SAME build would be identical too: the rail's
# bubble/pill/go and the resume toast are state-dependent, and the toast in particular is built
# by offerResume() from localStorage.
#
# The toast is hidden by default because most changes have nothing to do with it and a toast that
# appears or not depending on timing would make every run differ. But card t_91ef2ced IS a change
# to the toast, so hiding it would hide the change: pass --toast to keep it on screen, with a
# seeded scroll position (same seeding as tools/check_bottom_chrome.py) so it actually renders.
SETTLE_BASE = ("(() => {const s=document.createElement('style');"
               "s.textContent='*{transition:none !important;animation:none !important}"
               "%s"
               ".section-rail-pill,.section-rail-bubble,.section-rail-go{opacity:0 !important}';"
               "document.head.appendChild(s); window.scrollTo(0,0); return 1;})()")
SETTLE = SETTLE_BASE % ".resume{display:none !important}"

TOAST = "/sittings/2026-08-04.html"
PAGE_ID = "2026-08-04"


def settle(show_toast):
    return SETTLE_BASE % "" if show_toast else SETTLE


def snap(base, w, port, show_toast=False):
    url = base + PAGE
    c = CDP(CHROME, url, w, 900, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
               deviceScaleFactor=1, mobile=False)
        if show_toast:
            # The toast needs a stored scroll position to exist at all, and it cannot be seeded
            # with a plain eval + reload: the page writes scrollY over the seed on pagehide.
            c.call("Page.addScriptToEvaluateOnNewDocument",
                   source="try{localStorage.setItem(%s, JSON.stringify({y: 4000, t: Date.now()}))}"
                          "catch(e){}" % json.dumps("parsnips:scroll:" + PAGE_ID))
        c.navigate(url)
        if show_toast:
            # ...and it must be on screen, so the snapshot is of the two boxes where they land.
            c.eval("window.scrollTo(0, 5000)")
            time.sleep(0.6)
        c.eval(settle(show_toast))
        time.sleep(1.0)
        return json.loads(c.eval(SNAP))
    finally:
        c.close()


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    a_base, b_base = sys.argv[1], sys.argv[2]
    argv = [x for x in sys.argv[3:] if x != "--toast"]
    show_toast = "--toast" in sys.argv
    widths = [int(x) for x in (argv[0] if argv else "390,759,760").split(",")]
    # Elements that this change is EXPECTED to move, as "TAG.cls" matches. On the phone that
    # is empty -- 0 differences is the acceptance criterion there. On desktop it is exactly
    # BUTTON.totop: the whole point is that the bar is moved out of the way of that one
    # element, so if ANYTHING ELSE moved, the fix reached further than it should have.
    expected = set(x for x in (argv[1] if len(argv) > 1 else "").split(",") if x)
    bad_total = 0
    for i, w in enumerate(widths):
        a = snap(a_base, w, 9000 + i * 2, show_toast)
        b = snap(b_base, w, 9100 + i * 2, show_toast)
        if a["n"] != b["n"]:
            print(f"{w}px: ELEMENT COUNT DIFFERS {a['n']} vs {b['n']}")
            bad_total += 1
            continue
        diffs, token_only, moved = [], 0, []
        for i2, (ea, eb) in enumerate(zip(a["els"], b["els"])):
            if ea["tag"] != eb["tag"] or ea["cls"] != eb["cls"]:
                diffs.append((i2, "element order shifted", ea["tag"], eb["tag"]))
                continue
            if ea["box"] != eb["box"]:
                who = ea["tag"] + "." + ea["cls"]
                if who in expected:
                    moved.append((who, ea["box"], eb["box"]))
                    continue
                diffs.append((i2, "box", who, ea["box"], eb["box"]))
                continue
            if ea["css"] != eb["css"]:
                pa = dict(p.split("=", 1) for p in ea["css"].split(";") if "=" in p)
                pb = dict(p.split("=", 1) for p in eb["css"].split(";") if "=" in p)
                delta = {k: (pa.get(k), pb.get(k)) for k in set(pa) | set(pb)
                         if pa.get(k) != pb.get(k)}
                if set(delta) <= NEW_TOKENS:
                    token_only += 1
                    continue
                diffs.append((i2, "css " + json.dumps(delta)[:300],
                              ea["tag"] + "." + ea["cls"]))
        print(f"{w}px: {a['n']} elements; {token_only} differ ONLY by "
              f"{'/'.join(sorted(NEW_TOKENS))}; {len(moved)} moved as expected; "
              f"{len(diffs)} differ in anything else")
        for m in moved:
            print(f"     expected move: {m[0]}  {m[1]} -> {m[2]}")
        for d in diffs[:12]:
            print("    ", d)
        bad_total += len(diffs)
    print()
    if bad_total:
        print(f"RESULT: {bad_total} difference(s) beyond the new token(s) and the expected move")
        return 1
    print("RESULT: 0 differences beyond the new token(s) and the expected move(s) -- every")
    print("        other element's computed styles and every other rect are identical.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
