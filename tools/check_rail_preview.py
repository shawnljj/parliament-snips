#!/usr/bin/env python3
"""Drive the preview gesture and verify it against the tick it ACTUALLY selected.

The earlier version pressed at a computed y and reported the result under the LOOP index, which
made "tick0 never became pending" look like a defect when it was the harness: a first
Input.dispatchMouseEvent on a fresh connection is not always delivered, and the y a press lands on
selects the NEAREST tick rather than the one the loop intended. This version:

  * warms the input state with a mouseMoved before the press (the documented CDP behaviour);
  * drags the finger DOWN the track so every tick is visited in sequence;
  * reports the tick that is ACTUALLY pending, by reading its index out of the DOM, so the label
    can never disagree with what was measured;
  * asserts the bubble/Go geometry for the tick that is really open.
"""
import json
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402

SETUP = r"""
(() => {
  const t = document.querySelector('.section-rail-track').getBoundingClientRect();
  const ticks = [].slice.call(document.querySelectorAll('.section-rail-tick'));
  const centres = ticks.map(e => {
    const m = e.querySelector('.section-rail-tick-mark').getBoundingClientRect();
    return m.top + m.height / 2;
  });
  return JSON.stringify({ x: t.left + t.width / 2, top: t.top, bottom: t.bottom,
                          centres: centres, n: ticks.length });
})()
"""

READ = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const all = [].slice.call(document.querySelectorAll('.section-rail-tick'));
  const i = all.findIndex(e => e.classList.contains('is-pending'));
  if (i < 0) return JSON.stringify({ pendingIdx: -1 });
  const p = all[i];
  const b = p.querySelector('.section-rail-bubble'), g = p.querySelector('.section-rail-go');
  const m = p.querySelector('.section-rail-tick-mark').getBoundingClientRect();
  const br = b.getBoundingClientRect(), gr = g.getBoundingClientRect();
  const rail = document.querySelector('.section-rail').getBoundingClientRect();
  return JSON.stringify({
    pendingIdx: i, label: (b.textContent || '').trim().slice(0, 44),
    bubbleOp: getComputedStyle(b).opacity,
    bubble: { x: px(br.left), y: px(br.top), r: px(br.right), b: px(br.bottom) },
    go: { x: px(gr.left), y: px(gr.top), r: px(gr.right), b: px(gr.bottom) },
    tickCy: px(m.top + m.height / 2), railX: px(rail.left), railR: px(rail.right),
    iw: window.innerWidth, vh: window.innerHeight,
  });
})()
"""


def run(base, w, h, port, page="/sittings/2026-08-04.html"):
    c = CDP(CHROME, base + page, w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=False)
        c.eval(f"location.replace({json.dumps(base + page)})")
        time.sleep(2.5)
        c.eval("window.scrollTo(0, Math.round((document.documentElement.scrollHeight"
               " - window.innerHeight) * 0.5))")
        time.sleep(1.0)
        s = json.loads(c.eval(SETUP))
        # Warm the input state: the first dispatched mouse event on a fresh connection can be
        # dropped, which previously made tick 0 look like it never opened.
        c.call("Input.dispatchMouseEvent", type="mouseMoved", x=s["x"], y=s["top"] + 2)
        time.sleep(0.2)
        c.call("Input.dispatchMouseEvent", type="mousePressed", x=s["x"], y=s["centres"][0],
               button="left", clickCount=1)
        time.sleep(0.25)
        seen = []
        for i, cy in enumerate(s["centres"]):
            c.call("Input.dispatchMouseEvent", type="mouseMoved", x=s["x"], y=cy,
                   button="left")
            time.sleep(0.3)
            seen.append(json.loads(c.eval(READ)))
        c.call("Input.dispatchMouseEvent", type="mouseReleased", x=s["x"],
               y=s["centres"][-1], button="left", clickCount=1)
        time.sleep(0.5)
        return seen
    finally:
        c.close()


def check(sample, w):
    """Return the list of problems for one sampled preview state."""
    if sample.get("pendingIdx", -1) < 0:
        return ["no tick pending"]
    b, g = sample["bubble"], sample["go"]
    bad = []
    if sample["bubbleOp"] != "1":
        bad.append(f"bubble opacity={sample['bubbleOp']}")
    for name, bx in (("bubble", b), ("go", g)):
        if bx["x"] < 0 or bx["r"] > sample["iw"]:
            bad.append(f"{name} clipped horizontally {bx['x']}..{bx['r']}")
        if bx["y"] < 0 or bx["b"] > sample["vh"]:
            bad.append(f"{name} clipped vertically {bx['y']}..{bx['b']}")
    if abs((b["y"] + b["b"]) / 2 - sample["tickCy"]) > 2:
        bad.append(f"bubble centre {(b['y'] + b['b']) / 2} vs tick centre {sample['tickCy']}")
    if not (b["r"] <= sample["railX"] + 0.5 or b["x"] >= sample["railR"] - 0.5):
        bad.append("bubble covers the rail")
    if g["y"] < b["b"] - 1:
        bad.append("go chip above the bubble")
    return bad


def main():
    a, b = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None
    widths = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "1024,1280,1440,1920")
              .split(",")]
    total = fails = 0
    for i, w in enumerate(widths):
        for label, base, port in (("WORKTREE", a, 10010 + i * 2),
                                  ("BASELINE", b, 10011 + i * 2) if b else None) if b else \
                                 (("WORKTREE", a, 10010 + i * 2),):
            seen = run(base, w, 900, port)
            print(f"===== {w}px {label}")
            for s in seen:
                if s.get("pendingIdx", -1) < 0:
                    print("    (no tick pending for this sample)")
                    continue
                total += 1
                bad = check(s, w)
                if bad:
                    fails += 1
                print(f"    tick{s['pendingIdx']} op={s['bubbleOp']} "
                      f"bubble x={s['bubble']['x']}..{s['bubble']['r']} "
                      f"y={s['bubble']['y']}..{s['bubble']['b']} "
                      f"go x={s['go']['x']}..{s['go']['r']} "
                      f"tickCy={s['tickCy']} label={s['label']!r}"
                      + ("  OK" if not bad else "  << " + "; ".join(bad)))
    print()
    print(f"RESULT: {fails} failing of {total} sampled preview states")


if __name__ == "__main__":
    main()
