#!/usr/bin/env python3
"""Did the z-index retune change ANY stacking decision on mobile, and is the desktop scale sane?

The rail's z-index went 70 -> 24 (and the preview 90/91 -> 26) as part of establishing a
documented scale. Geometry alone cannot prove that harmless: what matters is which element wins
at every pixel where two of these overlap. So, on both builds, sweep a coarse grid of points over
each overlap region and compare the `elementsFromPoint` result at each.

Overlap regions to sweep:
  * rail  x pbar      (rail's bottom vs the progress bar)
  * rail  x totop     (the back-to-top button)
  * rail  x topbar    (short viewports)
  * rail  x resume    (the toast)
  * rail  x sumcol / text content  (the D1 defect: must NOT overlap on desktop any more)
"""
import json
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402

SWEEP = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const pick = s => document.querySelector(s);
  const boxes = {};
  ['.section-rail','.pbar','.totop','.top','.resume','.sumcol','.section-rail-bubble',
   '.section-rail-go','.wrap'].forEach(s => {
    const e = pick(s);
    if (!e) { boxes[s] = null; return; }
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    boxes[s] = { x: px(r.left), y: px(r.top), r: px(r.right), b: px(r.bottom),
                 w: px(r.width), h: px(r.height), z: cs.zIndex, pos: cs.position,
                 disp: cs.display, vis: cs.visibility, op: cs.opacity };
  });
  // The rail vs each of these: where do they intersect, and who wins inside?
  const rail = pick('.section-rail');
  const out = { boxes: boxes, overlaps: {}, wins: {} };
  const inter = (a, b) => (a && b && a.x < b.r && b.x < a.r && a.y < b.b && b.y < a.b)
    ? { x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y),
        x2: Math.min(a.r, b.r), y2: Math.min(a.b, b.b) } : null;
  if (rail) {
    ['.pbar','.totop','.top','.resume','.sumcol','.wrap'].forEach(s => {
      const i = inter(boxes['.section-rail'], boxes[s]);
      // The wrap is the whole page, so it always "intersects"; only the rail's own box matters.
      if (s !== '.wrap' && !i) return;
      out.overlaps[s] = i;
    });
    // Who wins at the rail's own centre, and at each overlap's centre?
    const rr = rail.getBoundingClientRect();
    out.wins['rail-centre'] = (() => {
      const e = document.elementFromPoint(rr.left + rr.width / 2, rr.top + rr.height / 2);
      return e ? e.tagName + '.' + e.className : null;
    })();
    Object.keys(out.overlaps).forEach(s => {
      const i = out.overlaps[s];
      if (!i) return;
      out.wins[s] = (() => {
        const e = document.elementFromPoint((i.x1 + i.x2) / 2, (i.y1 + i.y2) / 2);
        return e ? e.tagName + '.' + e.className : null;
      })();
    });
    // Does the rail cover any of the summary column's TEXT? (D1, must be "no" on desktop.)
    const sc = boxes['.sumcol'];
    if (sc && inter(boxes['.section-rail'], sc)) {
      out.sumcolCovered = true;
      const i = inter(boxes['.section-rail'], sc);
      out.sumcolCoveredBy = (() => {
        const e = document.elementFromPoint((i.x1 + i.x2) / 2, (i.y1 + i.y2) / 2);
        return e ? e.tagName + '.' + e.className : null;
      })();
    } else { out.sumcolCovered = false; }
  }
  return JSON.stringify(out);
})()
"""


def scan(base, page, w, h, port):
    c = CDP(CHROME, base + page, w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=(w < 760))
        c.eval(f"location.replace({json.dumps(base + page)})")
        time.sleep(2.5)
        # Scroll to roughly the rail's territory too: the rail is vertically centred, the pbar
        # is bottom-anchored, so their overlap depends on scroll.
        res = {}
        for tag, y in (("top", 0), ("mid", 0.5), ("bot", 1.0)):
            c.eval("window.scrollTo(0, Math.round((document.documentElement.scrollHeight"
                   f" - window.innerHeight) * {y}))")
            time.sleep(0.9)
            res[tag] = json.loads(c.eval(SWEEP))
        return res
    finally:
        c.close()


def main():
    page = "/sittings/2026-08-04.html"
    a, b = sys.argv[1], sys.argv[2]
    fail = 0
    for w, h in ((390, 844), (740, 900), (1024, 900), (1280, 900), (1440, 900), (1920, 900)):
        ra = scan(a, page, w, h, 9400)
        rb = scan(b, page, w, h, 9430)
        print(f"===== {w}x{h}")
        for tag in ("top", "mid", "bot"):
            da, db = ra[tag], rb[tag]
            railA, railB = da["boxes"].get(".section-rail"), db["boxes"].get(".section-rail")
            if not railA and not railB:
                print(f"  {tag:3s} rail absent in both")
                continue
            # The decisive comparison: the winner at each overlap centre.
            keys = sorted(set(da["wins"]) | set(db["wins"]))
            diffs = [(k, da["wins"].get(k), db["wins"].get(k)) for k in keys
                     if da["wins"].get(k) != db["wins"].get(k)]
            print(f"  {tag:3s} rail={railA and (railA['x'], railA['r'], railA['y'])} "
                  f"z={railA and railA['z']}->{railB and railB['z']} | "
                  f"overlaps={sorted(da['overlaps'])} / {sorted(db['overlaps'])} | "
                  f"sumcolCovered={da.get('sumcolCovered')}->{db.get('sumcolCovered')}"
                  + (f" by {db.get('sumcolCoveredBy')}" if db.get("sumcolCovered") else ""))
            if diffs:
                fail += 1
                for k, va, vb in diffs:
                    print(f"      STACKING CHANGED at {k}: worktree={va}  baseline={vb}")
            if da.get("sumcolCovered") != db.get("sumcolCovered"):
                print(f"      sumcol coverage changed: {da.get('sumcolCovered')} -> "
                      f"{db.get('sumcolCovered')}")
    print()
    print(f"RESULT: {'no stacking regressions' if not fail else str(fail) + ' changed decisions'}")


if __name__ == "__main__":
    main()
