#!/usr/bin/env python3
"""Report scroll geometry on a page, twice, after settling -- to tell a real height from a race.

Two probes of the same unchanged page reported different maximum scroll offsets (2770 vs 2857),
which is either a genuine layout difference or a measurement made before the page stopped
growing. This reads scrollHeight repeatedly until it stops changing, and prints the trace, so the
number quoted downstream is the settled one.

    python3 tools/qa_scroll_geometry.py <base-url> [path]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402

PROBE = r"""
(() => {
  const d = document.documentElement;
  return JSON.stringify({ scrollHeight: d.scrollHeight, innerHeight: window.innerHeight,
                          maxScroll: d.scrollHeight - window.innerHeight,
                          openDetails: document.querySelectorAll('details[open]').length,
                          details: document.querySelectorAll('details').length,
                          imgs: document.querySelectorAll('img').length,
                          fontsReady: document.fonts ? document.fonts.status : null,
                          bodyH: document.body.getBoundingClientRect().height });
})()
"""


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8482"
    path = sys.argv[2] if len(sys.argv) > 2 else "/sittings/index.html"
    for w in (1280, 1440):
        c = CDP(CHROME, base + path, w, 900, 13000 + w)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f'location.replace("{base + path}")')
            print(f"--- {w}px  {base + path}")
            for i in range(8):
                d = json.loads(c.eval(PROBE))
                print(f"    t+{i * 0.6:.1f}s maxScroll={d['maxScroll']:<7} "
                      f"scrollHeight={d['scrollHeight']:<7} details {d['openDetails']}/"
                      f"{d['details']} open  imgs={d['imgs']} fonts={d['fontsReady']}")
                time.sleep(0.6)
        finally:
            c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
