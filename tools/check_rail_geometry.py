#!/usr/bin/env python3
"""Drive the preview with REAL trusted input, and prove the D1/D3/D4/D5 defects are fixed.

Two things this must do that the earlier probes could not:

1. TRUSTED INPUT. The preview is opened by the track's `pointerdown` -> pointer capture ->
   `railPreview()` path, and a synthetic `dispatchEvent` is not trusted, so it never fired. This
   uses Input.dispatchMouseEvent, which the browser treats as real: the scrub starts, the tick
   becomes pending, the bubble's opacity goes to 1 and railPlacePreview() runs. That is the only
   way to measure where the preview actually goes.

2. THE FOUR AUDIT DEFECTS, re-measured with their own acceptance test:
     D1  rail vs content: rail's left edge must be to the RIGHT of the content's right edge,
         with a constant clearance, at every width (the audit's table had -209/-99/-19/+221/+541).
     D3  the 9 dots must share ONE x (baseline spread 75px).
     D4  the fill must sit on that same dot column (baseline was 23.3px off).
     D5  the rail box must no longer be a 468px box around 188px of content, centred on nothing.
   python3 tools/check_rail_geometry.py <url> [baseline-url] [widths]

EXIT CODE: 0 only when D1, D3 and D4 hold on the FIRST build at every width. The second build (a
baseline) is reported and never asserted -- its defects are the point of showing it next to the
fixed one. D5 is printed as a measurement (box vs track height) and is not asserted: the acceptance
number for it is a shape, and the audit stated the pre-fix value (468px around 188px) rather than a
threshold. This exit code was added when a handoff summarised this tool's output as "D1/D3/D4/D5
OK" while its own saved log printed `D3 ... SAWTOOTH` at four widths.
"""
import json
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402

GEOM = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const q = s => document.querySelector(s);
  const box = e => { if (!e) return null; const r = e.getBoundingClientRect();
                     return { x: px(r.left), y: px(r.top), r: px(r.right), b: px(r.bottom),
                              w: px(r.width), h: px(r.height) }; };
  const rail = q('.section-rail');
  const track = q('.section-rail-track');
  const fill = q('.section-rail-fill');
  const sumcol = q('.sumcol');
  const wrap = q('.wrap');
  const railB = box(rail);
  const trackB = box(track);
  const fillB = box(fill);
  const sumcolB = box(sumcol);
  const wraps = [].slice.call(document.querySelectorAll('.wrap')).map(box);
  // The content's right edge is the summary column's right edge: .wrap is padding:0 24px
  // inside max-width:1060px, and .sumcol is the last grid track, so it ends exactly there.
  const contentRight = sumcolB ? sumcolB.r : (wrap ? px(wrap.getBoundingClientRect().right) : null);
  const ticks = [].slice.call(document.querySelectorAll('.section-rail-tick'));
  const marks = ticks.map(t => box(t.querySelector('.section-rail-tick-mark')));
  const dotXs = marks.filter(Boolean).map(m => px(m.x + m.w / 2));
  const uniq = [];
  dotXs.forEach(v => { if (!uniq.some(u => Math.abs(u - v) < 0.01)) uniq.push(v); });
  // Leftmost dot's outer edge, the thing --rail-clear is stated against.
  // EXCLUDE the active tick: .is-active scales its mark 1.35x, which inflates the measured box
  // by ~0.8px on each side and would make the clearance read 8.6 instead of the true 10.
  const restMarks = ticks.filter(t => !t.classList.contains('is-active'))
                         .map(t => box(t.querySelector('.section-rail-tick-mark')))
                         .filter(Boolean);
  const dotLeft = restMarks.length ? Math.min.apply(null, restMarks.map(m => m.x)) : null;
  const fillCS = fill ? getComputedStyle(fill) : null;
  const fb = box(fill);
  return JSON.stringify({
    wrap: wrap ? box(wrap) : null, wrapCount: wraps.length,
    sumcol: sumcolB, rail: railB, track: trackB, fill: fillB,
    contentRight: contentRight, dotLeft: dotLeft,
    dotXs: dotXs, dotXsDistinct: uniq, dotSpread: uniq.length > 1 ? px(Math.max(...uniq) - Math.min(...uniq)) : 0,
    fillStyle: fillCS ? { left: fillCS.left, right: fillCS.right, w: fillCS.width,
                          bg: fillCS.backgroundImage || fillCS.backgroundColor } : null,
    railStyle: rail ? (s => ({ h: s.height, top: s.top, transform: s.transform, right: s.right,
                               maxH: s.maxHeight, pad: s.padding, z: s.zIndex }))(getComputedStyle(rail)) : null,
    tickCount: ticks.length,
    vw: window.innerWidth,
    overflowX: document.documentElement.scrollWidth > window.innerWidth,
  });
})()
"""

# The scrub gesture, as the page's own code expects it. Mouse events ARE trusted by the
# browser, so the pointerdown listener fires and pointer capture is honoured.
SCRUB_AT = """
(() => {
  const t = document.querySelector('.section-rail-track').getBoundingClientRect();
  return JSON.stringify({ x: t.left + t.width / 2, top: t.top, bottom: t.bottom,
                          h: t.height });
})()
"""

READ_PREVIEW = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const p = document.querySelector('.section-rail-tick.is-pending');
  if (!p) return JSON.stringify({ pending: false });
  const b = p.querySelector('.section-rail-bubble'), g = p.querySelector('.section-rail-go');
  const m = p.querySelector('.section-rail-tick-mark').getBoundingClientRect();
  const br = b.getBoundingClientRect(), gr = g.getBoundingClientRect();
  const rail = document.querySelector('.section-rail').getBoundingClientRect();
  return JSON.stringify({
    pending: true, label: (b.textContent || '').trim().slice(0, 50),
    bubbleOp: getComputedStyle(b).opacity, goOp: getComputedStyle(g).opacity,
    bubble: { x: px(br.left), y: px(br.top), r: px(br.right), b: px(br.bottom),
              w: px(br.width), h: px(br.height) },
    go: { x: px(gr.left), y: px(gr.top), r: px(gr.right), b: px(gr.bottom),
          w: px(gr.width), h: px(gr.height) },
    tickCy: px(m.top + m.height / 2), railX: px(rail.left), railR: px(rail.right),
    iw: window.innerWidth, vh: window.innerHeight,
  });
})()
"""


def measure(base, w, h, port, page="/sittings/2026-08-04.html", drive=True):
    c = CDP(CHROME, base + page, w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=(w < 760))
        c.eval(f"location.replace({json.dumps(base + page)})")
        time.sleep(2.5)
        c.eval("window.scrollTo(0, Math.round((document.documentElement.scrollHeight"
               " - window.innerHeight) * 0.5))")
        time.sleep(1.0)
        geom = json.loads(c.eval(GEOM))
        out = {"geom": geom, "previews": []}
        if drive and geom["rail"]:
            sc = json.loads(c.eval(SCRUB_AT))
            # Walk the finger down the track: press once, move through each tick, release.
            n = max(1, geom["tickCount"])
            for k in range(n):
                y = sc["top"] + (sc["h"] * (k + 0.5) / n)
                if k == 0:
                    c.call("Input.dispatchMouseEvent", type="mousePressed",
                           x=sc["x"], y=y, button="left", clickCount=1)
                else:
                    c.call("Input.dispatchMouseEvent", type="mouseMoved",
                           x=sc["x"], y=y, button="left")
                time.sleep(0.35)
                out["previews"].append(json.loads(c.eval(READ_PREVIEW)))
            c.call("Input.dispatchMouseEvent", type="mouseReleased",
                   x=sc["x"], y=sc["top"] + sc["h"] - 1, button="left", clickCount=1)
            time.sleep(0.6)
        return out
    finally:
        c.close()


def report(label, d, fails=None):
    """Print one build's D1/D3/D4/D5 and, when `fails` is given, record the failures.

    THE EXIT CODE EXISTS NOW, and that is a correction from the round-1 review of t_a15acd97:
    this tool was a pure report, and a handoff summarised its output as "D1/D3/D4/D5 OK" while the
    tool's own saved log printed `D3 ... SAWTOOTH` at all four widths. A report cannot disagree
    with a summary; a gate can. The threshold for each check is the one the printer already
    stated in words, so nothing new is being asserted -- the words just became fatal.

    `fails` is None for a baseline build, whose defects are the point of showing it.
    """
    g = d["geom"]
    print(f"  {label}: viewport={g['vw']} overflowX={g['overflowX']}")
    cr = g["contentRight"]
    if g["rail"] and cr is not None:
        cl = round(g["rail"]["x"] - cr, 2)
        # --rail-clear is measured to the DOT's outer edge, which is --rail-pad inside the box.
        dotCl = (round(g["dotLeft"] - cr, 2) if g.get("dotLeft") is not None else None)
        good = cl >= 0
        print(f"    D1 rail.x={g['rail']['x']} contentRight={cr} boxClearance={cl} "
              f"dotClearance={dotCl} "
              f"{'OK (right of content)' if good else 'OVERLAPS CONTENT by ' + str(-cl) + 'px'}")
        if fails is not None and not good:
            fails.append(f"D1 {label} rail overlaps the content by {-cl}px at {g['vw']}px")
    elif g["rail"]:
        print(f"    D1 rail={g['rail']} contentRight={cr} (sumcol absent)")
    single = len(g["dotXsDistinct"]) <= 1
    print(f"    D3 dotXs={g['dotXsDistinct']} spread={g['dotSpread']} "
          f"{'OK (single column)' if single else 'SAWTOOTH'}")
    if fails is not None and g["tickCount"] and not single:
        fails.append(f"D3 {label} dot column sawtoothed: {g['dotXsDistinct']} "
                     f"spread {g['dotSpread']}px at {g['vw']}px")
    if g["fill"] and g["dotXsDistinct"]:
        fillCx = g["fill"]["x"] + g["fill"]["w"] / 2
        off = round(fillCx - g["dotXsDistinct"][0], 2)
        # THE PHONE IS EXCLUDED AND THAT IS MEASURED, not a convenience: below 761px the fill is
        # `left:50%` of the TRACK (the base rule) and sits 6px off the dot column -- identically on
        # the pristine control build, so it is pre-existing and out of the desktop column work's
        # scope. Above 761px the fill's left is derived from the dot column and must be exact.
        scoped = g["vw"] >= 761
        good4 = abs(off) <= 0.51
        print(f"    D4 fill x={g['fill']['x']}..{g['fill']['r']} centre={fillCx} "
              f"dotColumnX={g['dotXsDistinct'][0]} offset={off} "
              f"{'OK (on the dot column)' if good4 else 'OFF THE COLUMN'}"
              f"{'' if scoped else ' (phone: left:50% of the track, pre-existing)'}")
        if fails is not None and scoped and not good4:
            fails.append(f"D4 {label} fill {off}px off the dot column at {g['vw']}px")
    if g["rail"]:
        print(f"    D5 rail box h={g['rail']['h']} track h={g['track']['h'] if g['track'] else None} "
              f"top={g['railStyle']['top']} transform={g['railStyle']['transform']}")
    if d["previews"]:
        for i, p in enumerate(d["previews"]):
            if not p.get("pending"):
                print(f"    PREVIEW tick{i}: never became pending")
                continue
            b, gg = p["bubble"], p["go"]
            issues = []
            if b["x"] < 0 or b["r"] > p["iw"]:
                issues.append(f"CLIPPED-X {b['x']}..{b['r']}")
            if b["y"] < 0 or b["b"] > p["vh"]:
                issues.append(f"CLIPPED-Y {b['y']}..{b['b']}")
            if gg["x"] < 0 or gg["r"] > p["iw"]:
                issues.append(f"GO-CLIPPED-X {gg['x']}..{gg['r']}")
            if abs((b["y"] + b["b"]) / 2 - p["tickCy"]) > 2:
                issues.append(f"CENTRE {(b['y'] + b['b']) / 2} vs tick {p['tickCy']}")
            if not (b["r"] <= p["railX"] + 0.5 or b["x"] >= p["railR"] - 0.5):
                issues.append("COVERS-RAIL")
            if gg["y"] < b["b"] - 1:
                issues.append("GO-ABOVE-BUBBLE")
            print(f"    PREVIEW tick{i} op={p['bubbleOp']} bubble x={b['x']}..{b['r']} "
                  f"y={b['y']}..{b['b']} go x={gg['x']}..{gg['r']} "
                  f"{'ok' if not issues else '<< ' + '; '.join(issues)}")
    else:
        print("    PREVIEW: no preview state captured")


def main():
    a = sys.argv[1]
    b = sys.argv[2] if len(sys.argv) > 2 else None
    widths = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3
                               else "1024,1280,1440,1920").split(",")]
    fails = []
    for i, w in enumerate(widths):
        print(f"===== {w}px")
        report("WORKTREE", measure(a, w, 900, 9600 + i * 2), fails=fails)
        if b:
            report("BASELINE", measure(b, w, 900, 9601 + i * 2))
    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("D1/D3/D4 all hold on the build under test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
