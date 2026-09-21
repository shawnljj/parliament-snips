#!/usr/bin/env python3
"""Capture the WHOLE rail strip (all 9 ticks) at 1:1 and 6x, for both a build and a baseline.

probe_rail_shots.py clips to `.section-rail-track`, which is only the visible track -- 5-6 ticks
in one viewport. This expands the clip to the tick list's full scroll height so every tick is in
one image, which is what a pitch measurement needs.

  python3 tools/probe_rail_strip.py <base> <out-prefix> <width>
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_layout import CHROME, CDP                              # noqa: E402

PAGE = "/sittings/2026-08-04.html"
CLIP = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const ticks = [...document.querySelectorAll('.section-rail-tick')];
  if (!ticks.length) return JSON.stringify(null);
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity, n = 0;
  for (const el of ticks) {
    const q = el.getBoundingClientRect();
    if (q.width === 0 && q.height === 0) continue;
    l = Math.min(l, q.left); t = Math.min(t, q.top + window.scrollY);
    r = Math.max(r, q.right); b = Math.max(b, q.bottom + window.scrollY);
    n++;
  }
  return JSON.stringify({ x: px(l - 10), y: px(t - 4), width: px(r - l + 20),
                          height: px(b - t + 10), ticks: n });
})()
"""


def main():
    base, prefix, w = sys.argv[1], sys.argv[2], int(sys.argv[3])
    c = CDP(CHROME, base + PAGE, w, 1400, 9670)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=1400,
               deviceScaleFactor=1, mobile=(w < 761))
        c.eval("location.replace(%s)" % json.dumps(base + PAGE))
        time.sleep(3.0)
        # Unpin the rail: at scroll 0 it is dormant and every tick sits at one y. Forcing the
        # rail's sticky/fixed state is what the real reader sees mid-page, but a full-height clip
        # of the tick list is enough to measure the pitch and the column.
        c.eval("window.scrollTo(0, 2000)")
        time.sleep(1.2)
        clip = json.loads(c.eval(CLIP))
        if clip is None:
            print("no ticks found")
            return
        print("clip %sx%s over %s ticks" % (clip["width"], clip["height"], clip["ticks"]))
        for scale, suffix in ((1, ""), (6, "-6x")):
            res = c.call("Page.captureScreenshot", format="png",
                         clip=dict(clip, scale=scale))["result"]
            path = "%s%s.png" % (prefix, suffix)
            with open(path, "wb") as f:
                f.write(base64.b64decode(res["data"]))
            print("wrote %s (%sx)" % (path, scale))
    finally:
        c.close()


if __name__ == "__main__":
    main()
