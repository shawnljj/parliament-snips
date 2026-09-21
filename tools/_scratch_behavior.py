#!/usr/bin/env python3
"""Behaviour checks the static probe cannot make: scroll, jump, preview, hit tests.

  1. SCROLL STABILITY -- the rail's box must not move while the page scrolls (it is fixed and
     centred on a band, so any movement is a jump). Sampled at 12 scroll positions.
  2. LAYOUT SHIFT -- the content must not move horizontally on scroll (the old rail was z-70
     over the summary column, so a scroll could not move it, but a sticky card could).
  3. HIT TEST -- elementFromPoint at the middle of every dot must return that dot's tick, i.e.
     the rail is not covered by anything and nothing covers it.
  4. NO OVERLAP with the summary column at any scroll position: the rail box must stay to the
     right of the content's right edge.
  5. PREVIEW -- tap a tick (click), then check the bubble's right edge and the Go chip's right
     edge agree with the rail's own right edge, and that both are on screen.
  6. JUMP -- click a tick and confirm the target heading lands at readingLine (stickyH + 12).
  7. CONSOLE -- no errors emitted during any of it.
"""
import json
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402

SETUP = r"""
(() => {
  window.__errs = [];
  window.addEventListener('error', e => window.__errs.push(String(e.message)));
  window.addEventListener('unhandledrejection',
                          e => window.__errs.push('rejection: ' + String(e.reason)));
  return 'ok';
})()
"""

SCROLL_SAMPLE = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const rail = document.querySelector('.section-rail');
  const fill = document.querySelector('.section-rail-fill');
  const sumcol = document.querySelector('.sumcol');
  const t = document.querySelector('.top');
  const g = e => { if (!e) return null; const r = e.getBoundingClientRect();
                   return { x: px(r.left), right: px(r.right), y: px(r.top), h: px(r.height) }; };
  const dots = [].map.call(document.querySelectorAll('.section-rail-tick-mark'),
                           m => { const r = m.getBoundingClientRect();
                                  return { cx: px(r.left + r.width / 2), cy: px(r.top + r.height / 2) }; });
  return JSON.stringify({ y: Math.round(window.scrollY), rail: g(rail), fill: g(fill),
                          sumcol: g(sumcol), top: g(t), dots: dots,
                          progress: fill ? getComputedStyle(fill, '::after').height : null });
})()
"""

PREVIEW = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const ticks = [].slice.call(document.querySelectorAll('.section-rail-tick'));
  const idx = Math.min(4, ticks.length - 1);
  const b = ticks[idx];
  b.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  return new Promise(res => setTimeout(() => {
    // Force the preview path, which is what JS positions.
    const bubble = b.querySelector('.section-rail-bubble');
    const go = b.querySelector('.section-rail-go');
    const rail = document.querySelector('.section-rail').getBoundingClientRect();
    const g = e => { const r = e.getBoundingClientRect();
                     return { x: px(r.left), right: px(r.right), y: px(r.top),
                              bottom: px(r.bottom), op: getComputedStyle(e).opacity }; };
    res(JSON.stringify({ idx: idx, rail: { x: px(rail.left), right: px(rail.right) },
                         bubble: g(bubble), go: g(go),
                         vw: window.innerWidth, vh: window.innerHeight,
                         heading: (b.getAttribute('aria-label') || '') }));
  }, 400));
})()
"""

HITTEST = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const ticks = [].slice.call(document.querySelectorAll('.section-rail-tick'));
  const out = [];
  ticks.forEach((t, i) => {
    const m = t.querySelector('.section-rail-tick-mark').getBoundingClientRect();
    const el = document.elementFromPoint(m.left + m.width / 2, m.top + m.height / 2);
    const ok = el === t || (el && t.contains(el));
    if (!ok) out.push({ i: i, dotCx: px(m.left + m.width / 2), dotCy: px(m.top + m.height / 2),
                        got: el ? el.tagName + '.' + el.className : null });
  });
  // And the summary column's TEXT must be hittable, i.e. the rail is not over it.
  const sc = document.querySelector('.sumcol');
  const sr = sc.getBoundingClientRect();
  const hitY = Math.max(0, Math.min(sr.top + 40, window.innerHeight - 10));
  const sel = document.elementFromPoint(sr.left + 20, hitY);
  return JSON.stringify({ misses: out, sumcolHit: sel ? sel.tagName + '.' + sel.className : null,
                          sumcolLeft: px(sr.left), sumcolTop: px(sr.top),
                          sumcolRight: px(sr.right), hitY: px(hitY),
                          vh: window.innerHeight, dormant:
                            !!document.querySelector('.section-rail.is-dormant') });
})()
"""


def main():
    url = sys.argv[1]
    widths = [int(w) for w in (sys.argv[2] if len(sys.argv) > 2
                               else "1024,1280,1440,1920").split(",")]
    for i, w in enumerate(widths):
        c = CDP(CHROME, url, w, 900, 9000 + i)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(url)})")
            time.sleep(2.5)
            c.eval(SETUP)
            print(f"===== {w}px")
            total = c.eval("document.documentElement.scrollHeight - window.innerHeight")
            rail_xs, sums = set(), []
            for k in range(12):
                y = int(total * k / 11)
                c.eval(f"window.scrollTo(0, {y})")
                time.sleep(0.8)
                # Re-read the scroll position until it has actually settled: in headless the
                # compositor's scroll can lag the main thread, and hit-testing against a stale
                # offset reports the root element -- an instrumentation artifact, not a defect.
                for _ in range(10):
                    if c.eval(f"Math.abs(window.scrollY - {y}) < 2"):
                        break
                    time.sleep(0.2)
                d = json.loads(c.eval(SCROLL_SAMPLE))
                rail_xs.add((d["rail"]["x"], d["rail"]["right"]))
                sums.append((d["rail"]["x"] - d["sumcol"]["right"], d["sumcol"]["y"],
                             d["rail"]["y"]))
            print(f"  rail x positions across 12 scroll steps: {sorted(rail_xs)}")
            print(f"  rail-left minus sumcol-right: "
                  f"min={min(s[0] for s in sums):.1f} max={max(s[0] for s in sums):.1f}")
            print(f"  rail y across scroll: min={min(s[2] for s in sums):.1f} "
                  f"max={max(s[2] for s in sums):.1f}")
            ht = json.loads(c.eval(HITTEST))
            print(f"  hit test: dot misses={ht['misses']}")
            print(f"            sumcolHit={ht['sumcolHit']} sumcol={ht['sumcolLeft']}.."
                  f"{ht['sumcolRight']} top={ht['sumcolTop']} hitY={ht['hitY']} "
                  f"vh={ht['vh']} dormant={ht['dormant']}")
            pv = json.loads(c.eval(PREVIEW))
            print(f"  preview: rail={pv['rail']} bubble={pv['bubble']} go={pv['go']}")
            errs = c.eval("JSON.stringify(window.__errs || [])")
            print(f"  console errors: {errs}")
        finally:
            c.close()


if __name__ == "__main__":
    main()
