#!/usr/bin/env python3
"""Prove the D15 archive probe reads the bar AFTER the page has re-synced it.

The trap this guards against (it bit this task twice): pbarSync() runs inside the page's own
requestAnimationFrame, so reading the bar in the same tick as scrollTo() returns the value from
BEFORE the scroll. On a perfectly working bar that reads a stale "0" at the foot -- which is
indistinguishable from the D15 defect itself, and which made the option-2 branch of
tools/qa_defects.py's own acceptance unpassable.

So this runs BOTH readings against a page whose bar demonstrably works (a sitting page), using
the real ARCHIVE_PROBE from tools/qa_defects.py:

  * the SAME-TICK read must show the stale pre-scroll value (that is what the trap looks like);
  * the probe AS IT NOW STANDS must show the settled value, and it must be 100 at the foot.

If the fixed probe ever loses its awaits, this fails: the two readings would agree, and the
"OK option 2" branch would be asserting a stale number.

    python3 tools/check_probe_tick.py http://127.0.0.1:8482
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402
from qa_defects import ARCHIVE_PROBE                                   # noqa: E402

SITTING = "/sittings/2026-08-04.html"

# The same read the archive probe used to do: no await between scrollTo() and the read.
SAME_TICK = r"""
(async () => {
  const pbar = document.querySelector('.pbar');
  const at = () => pbar ? { text: pbar.textContent.trim(),
                            aria: pbar.getAttribute('aria-valuenow') } : null;
  const before = at();
  window.scrollTo(0, document.documentElement.scrollHeight);
  const sameTick = at();
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  await new Promise(r => setTimeout(r, 150));
  return JSON.stringify({ present: !!pbar, before: before, sameTick: sameTick,
                          settled: at(),
                          scrollY: Math.round(window.scrollY),
                          maxScroll: Math.round(document.documentElement.scrollHeight
                                                - window.innerHeight) });
})()
"""


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8482"
    fails = []
    for w in (1280, 1440):
        c = CDP(CHROME, base + SITTING, w, 900, 9950 + w)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(base + SITTING)})")
            time.sleep(2.6)
            d = json.loads(c.eval(SAME_TICK))
            # Reset to the top and let the bar re-settle BEFORE the probe runs: otherwise the
            # tab is still at the foot from the read above, the probe's scrollTo() is a no-op,
            # and "before" would already read 100 -- which would prove nothing about the await.
            c.eval("window.scrollTo(0, 0)")
            time.sleep(1.0)
            probe = json.loads(c.eval(ARCHIVE_PROBE))
        finally:
            c.close()
        if "__probeError__" in probe:
            fails.append(f"{w}px: ARCHIVE_PROBE threw: {probe['__probeError__']}")
            continue
        at_foot = probe["scrollY"] >= probe["maxScroll"] - 2
        settled_aria = (probe["pbarAfterScrollToBottom"] or {}).get("aria")
        print(f"----- {w}px  sitting page (bar works)  scrollY={d['scrollY']} of "
              f"{d['maxScroll']}")
        print(f"  same-tick read (the old pattern) : before={d['before']} "
              f"sameTick={d['sameTick']}")
        print(f"  settled read                     : settled={d['settled']}")
        print(f"  ARCHIVE_PROBE as it now stands   : hasDataPage="
              f"{probe['hasDataPage']} present={probe['pbarPresent']} "
              f"before={probe['pbarBefore']['aria']} "
              f"after={settled_aria}")
        if d["sameTick"]["aria"] != "0":
            fails.append(f"{w}px: the same-tick read did not show the stale value "
                         f"({d['sameTick']['aria']!r}) -- the page may not be re-syncing the "
                         f"bar, so this guard proves nothing")
        if probe["pbarBefore"] is None or probe["pbarBefore"]["aria"] != "0":
            fails.append(f"{w}px: the probe's own 'before' read is "
                         f"{(probe['pbarBefore'] or {}).get('aria')!r}, not 0 -- the bar was not "
                         f"back at the top, so the probe's scroll was a no-op and its settled "
                         f"reading proves nothing about the await")
        if not at_foot:
            fails.append(f"{w}px: did not reach the foot (scrollY={probe['scrollY']} of "
                         f"{probe['maxScroll']})")
        if settled_aria != "100":
            fails.append(f"{w}px: ARCHIVE_PROBE read aria-valuenow={settled_aria!r} at the foot "
                         f"of a bar that works -- the probe is still reading too early")
        else:
            print(f"  {w}px OK   the probe sees the settled 100; the option-2 branch of "
                  f"qa_defects.py can pass on a real bar")
    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: the probe awaits the frame, so it reads the settled value; the same-tick read "
          "still shows the stale one, which is the trap it exists to catch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
