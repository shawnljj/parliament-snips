#!/usr/bin/env python3
"""Measure the rail's tick-list row pitch and each mark's offset INSIDE its tick, per build.

The round-1 review reported two symptoms: the level-3 dot 1px off the column, and "vertical tick
pitch 21/22px uneven". These are two different measurements and a clipped screenshot cannot tell
them apart (it mixes the progress fill in with the dots). This reads the DOM directly:

  pitch      = y of tick[n+1] - y of tick[n]              (the LIST rhythm)
  markOffset = the mark's offsetTop/offsetLeft in its tick (where the dot sits in its CELL)

  python3 tools/probe_rail_pitch.py <base> <width> [label]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_layout import CHROME, CDP                              # noqa: E402

PAGE = "/sittings/2026-08-04.html"
PROBE = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const rail = document.querySelector('.section-rail');
  const ticks = [...document.querySelectorAll('.section-rail-tick')];
  if (!ticks.length) return JSON.stringify({dormant: true});
  // Force the rail awake: it is dormant until the reader has scrolled into the page.
  window.dispatchEvent(new Event('scroll'));
  const rows = ticks.map(t => {
    const r = t.getBoundingClientRect();
    const m = t.querySelector('.section-rail-tick-mark');
    const dot = m ? m.querySelector('*') : null;
    return {
      cls: t.className.replace('section-rail-tick', '').trim() || '(level-1)',
      top: px(r.top + window.scrollY),
      h: px(r.height),
      // TWO BOXES, and confusing them mislabels the fix. getBoundingClientRect is the RENDERED
      // box (it includes the transient active-scale transform, so it reads 10.8 for the active
      // tick and 6 for a level-3 dot); offsetWidth/Height is the LAYOUT box, which is what the
      // round-1 regression actually changed and what check_outline.py's D3 asserts on.
      markBox: m ? px(m.offsetWidth) + 'x' + px(m.offsetHeight) : null,
      markRendered: m ? px(m.getBoundingClientRect().width) + 'x' + px(m.getBoundingClientRect().height) : null,
      markOffsetTop: m ? px(m.offsetTop) : null,
      markOffsetLeft: m ? px(m.offsetLeft) : null,
      dotW: dot ? px(dot.getBoundingClientRect().width) : null,
      dotCx: dot ? px(dot.getBoundingClientRect().left + dot.getBoundingClientRect().width / 2) : null,
      markCx: m ? px(m.getBoundingClientRect().left + m.getBoundingClientRect().width / 2) : null,
    };
  });
  return JSON.stringify({rail: !!rail, rows: rows});
})()
"""


def main():
    base, w = sys.argv[1], int(sys.argv[2])
    label = sys.argv[3] if len(sys.argv) > 3 else base
    c = CDP(CHROME, base + PAGE, w, 1400, 9670)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=1400,
               deviceScaleFactor=1, mobile=(w < 761))
        c.eval("location.replace(%s)" % json.dumps(base + PAGE))
        time.sleep(3.0)
        c.eval("window.scrollTo(0, 2000)")
        time.sleep(1.2)
        d = json.loads(c.eval(PROBE))
        if d.get("dormant"):
            print("%s @%s: rail dormant, no ticks" % (label, w))
            return
        rows = d["rows"]
        print("== %s @%spx  (%d ticks)" % (label, w, len(rows)))
        print("   #  level       rowTop    rowH  pitch  layoutBox  rendered  offTop offLeft  markCx")
        prev = None
        pitches = []
        for i, r in enumerate(rows):
            p = "" if prev is None else "%.1f" % (r["top"] - prev)
            if prev is not None:
                pitches.append(round(r["top"] - prev, 1))
            print("   %-2d %-11s %8.1f %5.1f %6s  %-9s %-9s %6s %7s %7s"
                  % (i, r["cls"], r["top"], r["h"], p, r["markBox"], r["markRendered"],
                     r["markOffsetTop"], r["markOffsetLeft"], r["markCx"]))
            prev = r["top"]
        print("   pitch: %s   distinct=%s" % (pitches, sorted(set(pitches))))
        boxes = sorted({r["markBox"] for r in rows})
        print("   mark layout boxes: %s" % boxes)
        cxs = sorted({r["markCx"] for r in rows})
        print("   mark centres: %s  (spread %.2f)" % (cxs, cxs[-1] - cxs[0]))
        ot = sorted({r["markOffsetTop"] for r in rows})
        ol = sorted({r["markOffsetLeft"] for r in rows})
        print("   mark offsetTop: %s   offsetLeft: %s" % (ot, ol))
    finally:
        c.close()


if __name__ == "__main__":
    main()
