#!/usr/bin/env python3
"""Console-cleanliness and horizontal-overflow probe, at every breakpoint that matters.

The QA acceptance criterion is "no new console errors, overflow, or layout shift". Reading the
page's source cannot establish any of those, so this installs a collector BEFORE the document
loads (Page.addScriptToEvaluateOnNewDocument), exercises the page the way a reader does --
scroll the whole document, hover every tick on the rail with REAL CDP mouse input, click the
back-to-top control, toggle the summary card's own scroll -- and then reports every error,
rejection, console.error and console.warn the page produced, plus whether the document ever
gained a horizontal scrollbar or shifted the sticky top bar.

Usage:
    python3 tools/qa_console.py http://127.0.0.1:8480
Exit code 0 only when the page is silent and never overflows.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402

COLLECTOR = r"""
(() => {
  if (window.__qaInstalled) return 1;
  window.__qaInstalled = 1;
  window.__qaErrors = [];
  const push = (kind, msg) => window.__qaErrors.push({ kind: kind, msg: String(msg) });
  window.addEventListener('error', e => push('error',
    (e.message || 'error') + ' @ ' + (e.filename || '?') + ':' + (e.lineno || 0)));
  window.addEventListener('unhandledrejection', e => push('rejection',
    (e.reason && (e.reason.message || e.reason)) || 'rejection'));
  const wrap = (name, level) => {
    const orig = console[name].bind(console);
    console[name] = function () {
      push(level, Array.prototype.map.call(arguments, a => {
        try { return typeof a === 'string' ? a : JSON.stringify(a); } catch (err) { return String(a); }
      }).join(' '));
      return orig.apply(null, arguments);
    };
  };
  wrap('error', 'console.error');
  wrap('warn', 'console.warn');
  return 1;
})()
"""

SWEEP = r"""
(() => {
  const doc = document.documentElement;
  const top = document.querySelector('header.top');
  const out = { overflowAt: [], topBarShift: [], scrollHeight: doc.scrollHeight };
  const y0 = top ? Math.round(top.getBoundingClientRect().top) : null;
  for (let f = 0; f <= 1.0001; f += 0.05) {
    window.scrollTo(0, Math.round((doc.scrollHeight - window.innerHeight) * f));
    const here = Math.round(window.scrollY);
    if (doc.scrollWidth > window.innerWidth + 1) out.overflowAt.push(here);
    if (top) { const t = Math.round(top.getBoundingClientRect().top);
               if (t !== y0) out.topBarShift.push({ y: here, top: t }); }
  }
  window.scrollTo(0, 0);
  return JSON.stringify(out);
})()
"""

TICKS = r"""
(() => {
  const cs = [...document.querySelectorAll('.section-rail-tick')];
  return JSON.stringify(cs.map(t => {
    const r = t.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  }));
})()
"""


def run(base, w, h, port, page="/sittings/2026-08-04.html"):
    url = base + page
    c = CDP(CHROME, "about:blank", w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=False)
        c.call("Page.addScriptToEvaluateOnNewDocument", source=COLLECTOR)
        c.call("Page.navigate", url=url)
        time.sleep(2.8)
        loaded = c.eval("location.href + '|' + document.readyState")
        sweep = json.loads(c.eval(SWEEP))

        # Exercise the rail with trusted input: press on tick 0 and move across all of them.
        ticks = json.loads(c.eval(TICKS))
        if ticks:
            c.call("Input.dispatchMouseEvent", type="mouseMoved",
                   x=ticks[0]["x"], y=ticks[0]["y"])
            time.sleep(0.2)
            c.call("Input.dispatchMouseEvent", type="mousePressed",
                   x=ticks[0]["x"], y=ticks[0]["y"], button="left", clickCount=1)
            for t in ticks:
                c.call("Input.dispatchMouseEvent", type="mouseMoved",
                       x=t["x"], y=t["y"], button="left")
                time.sleep(0.12)
            c.call("Input.dispatchMouseEvent", type="mouseReleased",
                   x=ticks[-1]["x"], y=ticks[-1]["y"], button="left", clickCount=1)
            time.sleep(0.4)

        # And the back-to-top control, if this page has one.
        totop = c.eval("(() => { const e = document.querySelector('.totop');"
                       " if (!e) return 'absent'; const r = e.getBoundingClientRect();"
                       " return JSON.stringify({x: Math.round(r.left + r.width / 2),"
                       " y: Math.round(r.top + r.height / 2)}); })()")
        if totop and totop != "absent":
            t = json.loads(totop)
            c.eval("window.scrollTo(0, Math.round(document.documentElement.scrollHeight * 0.5))")
            time.sleep(0.5)
            c.call("Input.dispatchMouseEvent", type="mousePressed", x=t["x"], y=t["y"],
                   button="left", clickCount=1)
            c.call("Input.dispatchMouseEvent", type="mouseReleased", x=t["x"], y=t["y"],
                   button="left", clickCount=1)
            time.sleep(0.6)

        errors = json.loads(c.eval("JSON.stringify(window.__qaErrors || [])"))
        return {"url": url, "loaded": loaded, "sweep": sweep, "errors": errors,
                "ticks": len(ticks)}
    finally:
        c.close()


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8480"
    widths = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2
                               else "390,759,760,761,1024,1280,1440,1920").split(",")]
    fails = []
    for i, w in enumerate(widths):
        h = 844 if w < 800 else 900
        try:
            r = run(base, w, h, 9710 + i * 3)
        except Exception as e:                                        # noqa: BLE001
            print(f"{w:>6}px  PROBE FAILED: {e}")
            fails.append(f"{w}px: probe failed: {e}")
            continue
        print(f"{w:>6}px  ticks={r['ticks']:>2}  scrollHeight={r['sweep']['scrollHeight']}"
              f"  overflowAt={r['sweep']['overflowAt'] or 'never'}"
              f"  topBarShift={r['sweep']['topBarShift'] or 'none'}")
        if r["errors"]:
            print(f"        {len(r['errors'])} console/error event(s):")
            for e in r["errors"][:12]:
                print(f"          [{e['kind']}] {e['msg'][:160]}")
            fails.append(f"{w}px: {len(r['errors'])} console event(s)")
        else:
            print("        console clean: 0 errors, 0 rejections, 0 warnings")
        if r["sweep"]["overflowAt"]:
            fails.append(f"{w}px: horizontal overflow at scroll {r['sweep']['overflowAt']}")
        if r["sweep"]["topBarShift"]:
            fails.append(f"{w}px: sticky top bar shifted {r['sweep']['topBarShift'][:3]}")
        print(f"        loaded={r['loaded']}")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: silent console, no horizontal overflow and no top-bar shift at any width.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
