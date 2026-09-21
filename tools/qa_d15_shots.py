#!/usr/bin/env python3
"""Capture the archive (and a sitting page) at each width, top and scrolled, for visual proof.

Removing the bar should leave no gap and no layout shift anywhere else on the page. The
screenshots are the human-checkable half of that claim; qa_build_diff.py is the machine half.

    python3 tools/qa_d15_shots.py <base-url> <out-dir> [reference-base-url]
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402

PAGES = [("archive", "/sittings/index.html"), ("sitting", "/sittings/2026-08-04.html")]


def shots(base, out_dir, tag):
    os.makedirs(out_dir, exist_ok=True)
    for label, path in PAGES:
        for w in (1280, 1440):
            c = CDP(CHROME, base + path, w, 900, 11000 + (w % 100) + (5 if label == "sitting" else 0))
            try:
                c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                       deviceScaleFactor=1, mobile=False)
                c.eval(f"location.replace({base + path!r})".replace("'", '"'))
                time.sleep(2.6)
                p = os.path.join(out_dir, f"{tag}-{label}-{w}.png")
                c.screenshot(p)
                print(f"  {p}")
                c.eval("window.scrollTo(0, document.documentElement.scrollHeight)")
                time.sleep(0.6)
                p = os.path.join(out_dir, f"{tag}-{label}-{w}-bottom.png")
                c.screenshot(p)
                print(f"  {p}")
            finally:
                c.close()


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    if len(sys.argv) < 3:
        sys.exit("usage: qa_d15_shots.py <base-url> <out-dir> [reference-base-url]")
    out = sys.argv[2]
    shots(sys.argv[1], out, "after")
    if len(sys.argv) > 3:
        shots(sys.argv[3], out, "before")
    return 0


if __name__ == "__main__":
    sys.exit(main())
