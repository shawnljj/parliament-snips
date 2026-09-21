#!/usr/bin/env python3
"""Capture the before/after screenshot set the acceptance criteria ask for.

Baseline = the pristine HEAD build served on 8436; after = this worktree's build on 8435.
Each width gets a resting shot and a scrolled shot (the audit's own convention), plus a preview
shot with a tick held down, since the preview is the thing this card is about.
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402
from _scratch_accept import SCRUB_AT                                # noqa: E402

PAGE = "/sittings/2026-08-04.html"
OUT = "docs/layout-after"


def shoot(c, path, w):
    msg = c.call("Page.captureScreenshot", format="png")
    # CDP.call returns the raw envelope {"id":..,"result":{...}}, so the payload is nested.
    r = msg.get("result", msg).get("data")
    data = base64.b64decode(r)
    with open(path, "wb") as fh:
        fh.write(data)
    print(f"    wrote {path} ({len(data)} bytes, {w}px)")


def capture(base, w, h, port, prefix):
    c = CDP(CHROME, base + PAGE, w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=(w < 760))
        c.eval(f"location.replace({json.dumps(base + PAGE)})")
        time.sleep(2.5)
        shoot(c, f"{OUT}/{prefix}-{w}.png", w)
        c.eval("window.scrollTo(0, Math.round((document.documentElement.scrollHeight"
               " - window.innerHeight) * 0.5))")
        time.sleep(1.0)
        shoot(c, f"{OUT}/{prefix}-{w}-scrolled.png", w)
        # The preview, held open on a mid rail tick.
        if w >= 760:
            sc = json.loads(c.eval(SCRUB_AT))
            y = sc["top"] + sc["h"] * 0.5
            c.call("Input.dispatchMouseEvent", type="mousePressed", x=sc["x"], y=y,
                   button="left", clickCount=1)
            time.sleep(0.5)
            c.call("Input.dispatchMouseEvent", type="mouseMoved", x=sc["x"], y=y + 20,
                   button="left")
            time.sleep(0.6)
            shoot(c, f"{OUT}/{prefix}-{w}-preview.png", w)
            c.call("Input.dispatchMouseEvent", type="mouseReleased", x=sc["x"], y=y + 20,
                   button="left", clickCount=1)
            time.sleep(0.5)
    finally:
        c.close()


def main():
    a = sys.argv[1]
    b = sys.argv[2] if len(sys.argv) > 2 else None
    widths = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "1024,1280,1440,1920")
              .split(",")]
    os.makedirs(OUT, exist_ok=True)
    for i, w in enumerate(widths):
        print(f"===== {w}px")
        if b:
            print("  BASELINE:")
            capture(b, w, 900, 9800 + i * 2, "before")
        print("  WORKTREE:")
        capture(a, w, 900, 9801 + i * 2, "after")


if __name__ == "__main__":
    main()
