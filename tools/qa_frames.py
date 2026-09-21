#!/usr/bin/env python3
"""Capture the phone/tablet/breakpoint frames the QA report cites, from both builds.

The audit's before set is desktop-only (1024/1280/1440/1920). The card also asks for the tablet
and mobile breakpoints to be shown unchanged, and for 759/760/761 explicitly (audit D6 is the one
width where "unchanged below the desktop breakpoint" is not a safe assumption). tools/qa_pixels.py
already proves those frames with pixel counts; this writes the frames themselves so the numbers can
be looked at.

    python3 tools/qa_frames.py <after-base-url> <before-base-url> <out-dir>
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402
from qa_pixels import SETTLE                                           # noqa: E402

PAGE = "/sittings/2026-08-04.html"
WIDTHS = [(390, 844), (759, 844), (760, 844), (761, 900)]


def shot(base, w, h, port, out):
    url = base + PAGE
    c = CDP(CHROME, url, w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=False)
        c.eval(f"location.replace({json.dumps(url)})")
        time.sleep(1.6)
        c.eval(SETTLE)
        time.sleep(1.2)
        c.screenshot(out)
    finally:
        c.close()


def main():
    after, before, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)
    for i, (w, h) in enumerate(WIDTHS):
        for label, base, port in (("after", after, 9400 + i * 2), ("before", before, 9401 + i * 2)):
            out = os.path.join(out_dir, f"{label}-{w}.png")
            shot(base, w, h, port, out)
            print(f"wrote {out}  ({os.path.getsize(out)} bytes)")
        # The scrolled frame at the breakpoint, where the sticky bar and rail are live.
        for label, base, port in (("after", after, 9420 + i * 2), ("before", before, 9421 + i * 2)):
            out = os.path.join(out_dir, f"{label}-{w}-scrolled.png")
            c = CDP(CHROME, base + PAGE, w, h, port)
            try:
                c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
                       deviceScaleFactor=1, mobile=False)
                c.eval(f"location.replace({json.dumps(base + PAGE)})")
                time.sleep(1.8)
                c.eval(SETTLE)
                c.eval("window.scrollTo(0, Math.round((document.documentElement.scrollHeight"
                       " - window.innerHeight) * 0.25))")
                time.sleep(1.2)
                c.screenshot(out)
            finally:
                c.close()
            print(f"wrote {out}  ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    sys.exit(main())
