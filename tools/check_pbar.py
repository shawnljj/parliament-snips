#!/usr/bin/env python3
"""Measure the progress bar on BOTH page kinds, with the rAF settled.

D15 is about the archive's bar reading a false 0%. The other half of the acceptance is that the
sitting page's bar is not collateral damage, so this scrolls each page to its foot and reads the
bar only AFTER the scroll handler's requestAnimationFrame has run -- reading in the same tick as
scrollTo() would report 0% even on a bar that works, which is a measurement artefact, not a bug.

    python3 tools/check_pbar.py http://127.0.0.1:8482
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402

SITTING = "/sittings/2026-08-04.html"
ARCHIVE = "/sittings/index.html"

# Scroll in steps and read after the frame settles, so the bar is given the same chance a human
# scroller gives it. `read()` is a function so the last read happens after the settle.
PROBE = r"""
(() => {
  const pbar = document.querySelector('.pbar');
  const read = () => pbar ? { text: (pbar.querySelector('.pbar-txt') || {}).textContent,
                              aria: pbar.getAttribute('aria-valuenow'),
                              fill: (pbar.querySelector('.pbar-fill') || {style:{}}).style.width,
                              display: getComputedStyle(pbar).display } : null;
  const max = document.documentElement.scrollHeight - window.innerHeight;
  const samples = [];
  const steps = 5;
  for (let i = 1; i <= steps; i++) {
    window.scrollTo(0, Math.round(max * i / steps));
    samples.push({ at: Math.round(max * i / steps), ...(read() || {}) });
  }
  return JSON.stringify({ url: location.href, present: !!pbar,
                          dataPage: document.body.getAttribute('data-page'),
                          hasScript: !!document.querySelector('script'),
                          maxScroll: max, samples });
})()
"""

# Same probe, but each scroll is followed by two animation frames and a timeout so the page's own
# rAF-debounced pbarSync really runs. Returned as an awaitable so CDP's awaitPromise waits for it.
SETTLED_PROBE = r"""
(async () => {
  const pbar = document.querySelector('.pbar');
  const frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const read = () => pbar ? { text: (pbar.querySelector('.pbar-txt') || {}).textContent,
                              aria: pbar.getAttribute('aria-valuenow'),
                              fill: (pbar.querySelector('.pbar-fill') || {style:{}}).style.width,
                              display: getComputedStyle(pbar).display } : null;
  const max = document.documentElement.scrollHeight - window.innerHeight;
  const samples = [{ at: 0, ...(read() || {}) }];
  for (const f of [0.25, 0.5, 0.75, 1]) {
    const y = Math.round(max * f);
    window.scrollTo(0, y);
    await frame();
    await new Promise(r => setTimeout(r, 120));
    samples.push({ at: y, ...(read() || {}) });
  }
  return JSON.stringify({ url: location.href, present: !!pbar,
                          dataPage: document.body.getAttribute('data-page'),
                          hasScript: !!document.querySelector('script'),
                          maxScroll: max, samples });
})()
"""


def run(c, width, path, port):
    c.call("Emulation.setDeviceMetricsOverride", width=width, height=900,
           deviceScaleFactor=1, mobile=False)
    c.eval(f"location.replace({json.dumps(path)})")
    time.sleep(2.6)
    return json.loads(c.eval(SETTLED_PROBE))


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8482"
    fails = []
    for label, path in (("archive", ARCHIVE), ("sitting", SITTING)):
        for w in (1280, 1440):
            c = CDP(CHROME, base + path, w, 900, 10020 + (0 if label == "archive" else 20) + w)
            try:
                d = run(c, w, base + path, w)
            finally:
                c.close()
            print(f"--- {label} {w}px  {d['url']}")
            print(f"    .pbar present={d['present']} data-page={d['dataPage']!r} "
                  f"script={d['hasScript']} maxScroll={d['maxScroll']}")
            for s in d["samples"]:
                print(f"    y={s['at']:<6} text={s.get('text')!r:<8} "
                      f"aria={s.get('aria')!r:<8} fill={s.get('fill')!r:<8} "
                      f"display={s.get('display')}")
            if not d["present"]:
                print(f"    VERDICT: absent -> claims nothing. OK (option 1)")
                continue
            foot = d["samples"][-1]
            at_foot = foot["at"] >= d["maxScroll"] - 2
            if at_foot and foot["aria"] == "100":
                print(f"    VERDICT: reaches 100% at the foot. OK")
            else:
                print(f"    VERDICT: FAIL -- renders and reads {foot['aria']!r} at the foot")
                fails.append(f"{label} {w}px: bar reads {foot['aria']!r} at the foot "
                             f"(scrollY {foot['at']} of {d['maxScroll']})")
    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("OK: no page renders a progress bar that misreports position.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
