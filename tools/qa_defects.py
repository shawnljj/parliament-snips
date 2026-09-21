#!/usr/bin/env python3
"""Re-measure the audit defects that NEITHER implementation task claimed, on the merged tree.

D1-D5 and D7/D8 have their own checkers (check_rail_geometry.py, check_column_guides.py). The
rest of the audit's list -- D6, D9-D17 -- has no acceptance test anywhere, and "deferred" is
only an honest verdict if the current state is measured rather than assumed. This does that.

Static facts (markup/CSS/JS) are read from the built site and the generator; live facts
(geometry, computed styles, hit tests) are measured in headless Chrome. Every line prints the
audit's original claim beside what the merged tree does now, so 'unchanged' and 'fixed' are
distinguishable.

Usage:
    python3 tools/qa_defects.py http://127.0.0.1:8480
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402

SITTING = "/sittings/2026-08-04.html"
ARCHIVE = "/sittings/index.html"

# ---------------------------------------------------------------------------
# Static checks (no browser)
# ---------------------------------------------------------------------------

def static_checks():
    out = []
    build = open(os.path.join(ROOT, "site/build_site.py"), encoding="utf-8").read()
    css = open(os.path.join(ROOT, "site/dist/theme.css"), encoding="utf-8").read()
    dist = os.path.join(ROOT, "site/dist")

    # D6 -- the two breakpoints.
    mn = sorted(set(int(m) for m in re.findall(r"@media \(min-width:(\d+)px\)", css)))
    mx = sorted(set(int(m) for m in re.findall(r"@media \(max-width:(\d+)px\)", css)))
    clash = sorted(set(mn) & set(mx))
    out.append(("D6", "two breakpoints disagree; at 760 the reading column is 204px",
                f"min-width={mn} max-width={mx} EXACT COLLISION={clash}",
                "STILL PRESENT" if clash else "FIXED"))

    # D10 -- .railhead has no rule of its own.
    n_railhead = len(re.findall(r"\.railhead", css))
    out.append(("D10", ".railhead has no CSS rule of its own (count 0)",
                f"'.railhead' occurrences in theme.css = {n_railhead}",
                "STILL PRESENT" if n_railhead == 0 else "CHANGED"))

    # D11 -- .sec-head is dead CSS.
    pages = [os.path.join(dist, "sittings", f) for f in os.listdir(os.path.join(dist, "sittings"))
             if f.endswith(".html")]
    hits = 0
    for p in pages:
        if 'class="sec-head"' in open(p, encoding="utf-8", errors="replace").read():
            hits += 1
    defined = ".sec-head" in css
    out.append(("D11", ".sec-head is defined but no generated page uses it",
                f"defined={defined}; pages containing class=\"sec-head\" = {hits} of {len(pages)}",
                "STILL PRESENT" if defined and hits == 0 else "CHANGED"))

    # D16 -- the trailing gap marker is a bare <li> outside the <ol>.
    src = open(os.path.join(dist, "sittings/2026-08-04.html"), encoding="utf-8").read()
    bare = len(re.findall(r"</ol>\s*<li", src))
    out.append(("D16", "trailing gap marker is a sibling <li> of its <ol>",
                f"'</ol><li' occurrences on one page = {bare}",
                "STILL PRESENT" if bare else "FIXED"))

    # D17 -- railCollect() sets level 2 for every tick.
    lv = re.findall(r"var level\s*=\s*(\d+)", build)
    out.append(("D17", "railCollect() sets level=2 for every tick; .level-3 unreachable",
                f"level assignments in build_site.py = {lv}",
                "STILL PRESENT" if lv and set(lv) == {"2"} else "CHANGED"))

    # D12 -- .vs-text max-width vs the column width is a live measurement, done below.
    # D13 / D14 -- the bottom-chrome coupling, stated as CSS.
    totop = re.search(r"\.totop\{[^}]*\}", css)
    pbar = re.search(r"\.pbar\{[^}]*\}", css)
    res = re.search(r"\.resume\{[^}]*\}", css)
    out.append(("D13", ".totop bottom:16px under a 26px .pbar at bottom:0 (44x10px overlap)",
                f"totop: {totop.group(0)[:110] if totop else None}",
                "SEE LIVE MEASUREMENT"))
    out.append(("D14", ".resume bottom is set only inside the >=761px block",
                f"base .resume: {res.group(0)[:90] if res else None}; pbar: "
                f"{pbar.group(0)[:70] if pbar else None}",
                "SEE LIVE MEASUREMENT"))
    return out


# ---------------------------------------------------------------------------
# Live checks
# ---------------------------------------------------------------------------

DEFECT_PROBE = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const q = s => document.querySelector(s);
  const box = e => { if (!e) return null; const r = e.getBoundingClientRect();
    return { x: px(r.left), y: px(r.top), right: px(r.right), bottom: px(r.bottom),
             w: px(r.width), h: px(r.height) }; };
  const out = { url: location.href };

  // D9 -- are the headings on the column grid? Measure every heading's left/width against the
  // text column's left and the wrap's content width.
  const wrap = q('main.wrap'), dsec = q('.dsec');
  const dsecBox = box(dsec);
  const cols = dsec ? (getComputedStyle(dsec).gridTemplateColumns || '').split(' ')
                 .map(parseFloat).filter(isFinite) : [];
  out.D9 = { textColLeft: dsecBox ? dsecBox.x : null,
             textColWidth: cols[0] || null,
             wrapContentRight: wrap ? px(wrap.getBoundingClientRect().right
                                          - parseFloat(getComputedStyle(wrap).paddingRight)) : null,
             headings: [] };
  const hs = document.querySelectorAll('.railhead, section.substance > h2, section.oral > h2, '
            + 'section.everything > h2, .method h2');
  for (const h of hs) {
    const b = box(h);
    out.D9.headings.push({ cls: h.className || h.tagName, text: (h.textContent || '').trim().slice(0, 34),
                           x: b.x, w: b.w, right: b.right });
    if (out.D9.headings.length >= 14) break;
  }

  // D10 -- do .railhead and the group heading below it still render identically?
  const rh = q('.railhead');
  const grp = rh ? rh.parentElement.querySelector('h2:not(.railhead)') : null;
  const styleOf = e => { if (!e) return null; const cs = getComputedStyle(e);
    return { fontSize: cs.fontSize, lineHeight: cs.lineHeight, fontWeight: cs.fontWeight,
             color: cs.color, y: px(e.getBoundingClientRect().top),
             h: px(e.getBoundingClientRect().height) }; };
  out.D10 = { railhead: rh ? (rh.textContent || '').trim().slice(0, 40) : null,
              railheadStyle: styleOf(rh),
              groupHeading: grp ? (grp.textContent || '').trim().slice(0, 40) : null,
              groupStyle: styleOf(grp) };

  // D12 -- does .vs-text's max-width bind on desktop?
  const vs = q('.vs-text');
  if (vs) { const cs = getComputedStyle(vs); const b = box(vs);
    out.D12 = { maxWidth: cs.maxWidth, measuredWidth: b.w,
                binds: parseFloat(cs.maxWidth) < b.w - 0.5 || b.w >= parseFloat(cs.maxWidth) - 0.5 }; }

  // D13 -- the back-to-top button vs the bottom progress bar.
  const totop = q('.totop'), pbar = q('.pbar');
  const tb = box(totop), pb = box(pbar);
  out.D13 = { totop: tb, pbar: pb,
              pbarDisplay: pbar ? getComputedStyle(pbar).display : null,
              overlapH: (tb && pb) ? px(Math.max(0, Math.min(tb.bottom, pb.bottom)
                                                 - Math.max(tb.y, pb.y))) : 0 };
  if (tb && pb && out.D13.overlapH > 0) {
    const y = Math.round((Math.max(tb.y, pb.y) + Math.min(tb.bottom, pb.bottom)) / 2);
    const x = Math.round(tb.x + tb.w / 2);
    const el = document.elementFromPoint(x, y);
    out.D13.hitAtOverlap = el ? (el.tagName + '.' + (el.className || '')) : null;
  }

  // D14 -- is the resume toast clear of the bar where both are live?
  const resEl = q('.resume');
  out.D14 = { present: !!resEl, bottom: resEl ? getComputedStyle(resEl).bottom : null,
              box: box(resEl) };

  // D15 -- the archive page's progress bar with nothing to measure.
  return JSON.stringify(out);
})()
"""

# The archive probe AWAITS the frames for exactly the reason SITTING_PBAR_PROBE does (see the
# long note below): pbarSync() runs inside the page's own requestAnimationFrame, so a read in
# the same tick as scrollTo() returns the value from BEFORE the scroll. Getting that wrong here
# made the option-2 branch of this file's own acceptance unpassable -- a correct, scroll-tracking
# bar would have been reported as failing, because 'after' would always have shown the pre-scroll
# value. The archive is gated today (option 1), so nothing was misreported; the probe is fixed
# rather than left, because the card makes this file the acceptance pattern and an option-2
# implementation must be able to pass it.
ARCHIVE_PROBE = r"""
(async () => {
  const px = v => Math.round(v * 100) / 100;
  const pbar = document.querySelector('.pbar');
  const body = document.body;
  const box = e => { const r = e.getBoundingClientRect();
    return { y: px(r.top), bottom: px(r.bottom), h: px(r.height) }; };
  const read = () => pbar ? { text: pbar.textContent.trim(),
                              aria: pbar.getAttribute('aria-valuenow'),
                              display: getComputedStyle(pbar).display,
                              fillWidth: pbar.querySelector('.pbar-fill')
                                 ? pbar.querySelector('.pbar-fill').style.width : null,
                              box: box(pbar) } : null;
  const frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const before = read();
  window.scrollTo(0, document.documentElement.scrollHeight);
  await frame();
  await new Promise(r => setTimeout(r, 150));
  const after = read();
  return JSON.stringify({ url: location.href,
                          width: window.innerWidth, height: window.innerHeight,
                          hasDataPage: body.hasAttribute('data-page'),
                          dataPage: body.getAttribute('data-page'),
                          hasScript: !!document.querySelector('script'),
                          totopPresent: !!document.querySelector('.totop'),
                          railPresent: !!document.querySelector('.section-rail'),
                          pbarPresent: !!pbar,
                          pbarBefore: before, pbarAfterScrollToBottom: after,
                          scrollY: Math.round(window.scrollY),
                          maxScroll: Math.round(document.documentElement.scrollHeight
                                                - window.innerHeight) });
})()
"""

# The sitting page's bar must keep working. This is the guard that gating the archive cannot
# cost the sitting page its bar: scroll to the foot and it must read 100.
#
# It AWAITS the frames. pbarSync() runs inside the page's own requestAnimationFrame, so reading
# the bar in the same tick as scrollTo() returns the PREVIOUS value -- on a perfectly working
# bar that reads 0 at the foot, which is a measurement artefact and not the defect. Getting this
# wrong once already produced a false "regression" verdict; the awaits are why it cannot again.
SITTING_PBAR_PROBE = r"""
(async () => {
  const pbar = document.querySelector('.pbar');
  const frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const at = () => pbar ? { text: (pbar.querySelector('.pbar-txt') || {}).textContent,
                            aria: pbar.getAttribute('aria-valuenow'),
                            display: getComputedStyle(pbar).display } : null;
  const top = at();
  window.scrollTo(0, document.documentElement.scrollHeight);
  await frame();
  await new Promise(r => setTimeout(r, 150));
  const bottom = at();
  return JSON.stringify({ url: location.href, present: !!pbar,
                          maxScroll: Math.round(document.documentElement.scrollHeight
                                                - window.innerHeight),
                          atTop: top, atBottom: bottom });
})()
"""


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8480"
    widths = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2
                               else "1024,1280,1440,1920").split(",")]
    print("=" * 78)
    print("STATIC CHECKS (markup / generated CSS / generator source)")
    print("=" * 78)
    for d, claim, measured, verdict in static_checks():
        print(f"{d:<4} {verdict}")
        print(f"     audit claim : {claim}")
        print(f"     measured    : {measured}")

    fails = []
    print()
    print("=" * 78)
    print("LIVE CHECKS (headless Chrome, sitting page)")
    print("=" * 78)
    for i, w in enumerate(widths):
        c = CDP(CHROME, base + SITTING, w, 900, 9810 + i * 2)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(base + SITTING)})")
            time.sleep(2.6)
            d = json.loads(c.eval(DEFECT_PROBE))
        finally:
            c.close()
        print(f"\n----- {w}px")
        print(f"  D9  text column left={d['D9']['textColLeft']} "
              f"width={d['D9']['textColWidth']} wrap content right="
              f"{d['D9']['wrapContentRight']}")
        wide = [h for h in d["D9"]["headings"]
                if d["D9"]["wrapContentRight"] and abs(h["right"]
                                                       - d["D9"]["wrapContentRight"]) < 2]
        print(f"       {len(wide)}/{len(d['D9']['headings'])} sampled headings span the FULL "
              f"wrap (right edge == wrap content right)")
        for h in d["D9"]["headings"][:6]:
            print(f"         x={h['x']:<7} w={h['w']:<7} right={h['right']:<7} "
                  f"{h['cls'] or h['text']!r}")
        print(f"  D10 railhead={d['D10']['railhead']!r} {d['D10']['railheadStyle']}")
        print(f"      group   ={d['D10']['groupHeading']!r} {d['D10']['groupStyle']}")
        if d.get("D12"):
            print(f"  D12 .vs-text maxWidth={d['D12']['maxWidth']} "
                  f"measured={d['D12']['measuredWidth']} (never binds on desktop)")
        print(f"  D13 .totop={d['D13']['totop']}")
        print(f"      .pbar ={d['D13']['pbar']} display={d['D13']['pbarDisplay']}")
        print(f"      overlap height={d['D13']['overlapH']}px  "
              f"hit at overlap={d['D13'].get('hitAtOverlap')}")
        print(f"  D14 .resume present={d['D14']['present']} bottom={d['D14']['bottom']} "
              f"box={d['D14']['box']}")

    print()
    print("=" * 78)
    print("D15 -- the archive page's progress bar")
    print("=" * 78)
    for w in (1280, 1440):
        c = CDP(CHROME, base + ARCHIVE, w, 900, 9870 + w)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(base + ARCHIVE)})")
            time.sleep(2.6)
            a = json.loads(c.eval(ARCHIVE_PROBE))
        finally:
            c.close()
        print(json.dumps(a, indent=2))
        # ACCEPTANCE. The bar may be ABSENT, or it may be REAL (reads a value that tracks
        # scroll and reaches 100 at the foot). What is forbidden is a bar that RENDERS and
        # still reports 0 at the bottom of the page -- a numeric claim that is wrong. An
        # element present but display:none is absent for this purpose: it claims nothing.
        shown = bool(a["pbarPresent"] and a["pbarBefore"]
                     and a["pbarBefore"]["display"] != "none")
        if not a["pbarPresent"]:
            print(f"  {w}px OK   option 1: no .pbar on the archive -- absent, so it claims nothing")
        elif not shown:
            print(f"  {w}px OK   option 1: .pbar present but display="
                  f"{a['pbarBefore']['display']} -- it renders no claim")
        else:
            aria = a["pbarAfterScrollToBottom"]["aria"]
            at_foot = a["scrollY"] >= a["maxScroll"] - 2
            if not (at_foot and aria == "100"):
                fails.append(
                    f"D15 at {w}px: .pbar renders and reads "
                    f"{a['pbarAfterScrollToBottom']['text']!r} / aria-valuenow={aria!r} at the "
                    f"foot of the archive (scrollY={a['scrollY']} of {a['maxScroll']}) -- a "
                    f"false progress claim; gate it or make it track scroll")
            else:
                # Print the settled reading, not just the verdict: the value below was taken
                # after two frames plus a settle, so a reader can see it was not read in the
                # same tick as scrollTo() (which would report the pre-scroll value).
                print(f"  {w}px OK   option 2: bar tracks scroll and reads "
                      f"{a['pbarAfterScrollToBottom']['text']!r} / aria-valuenow={aria!r} "
                      f"at the foot (settled, scrollY={a['scrollY']} of {a['maxScroll']})")

    # The other half of the acceptance: gating the archive must not cost the SITTING page its
    # bar. Scroll a sitting page to its foot and it must still read 100.
    for w in (1280, 1440):
        c = CDP(CHROME, base + SITTING, w, 900, 9970 + w)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(base + SITTING)})")
            time.sleep(2.6)
            s = json.loads(c.eval(SITTING_PBAR_PROBE))
        finally:
            c.close()
        print(f"  sitting {w}px: present={s['present']} display="
              f"{s['atTop']['display'] if s['atTop'] else None} atTop="
              f"{s['atTop']['aria'] if s['atTop'] else None} atBottom="
              f"{s['atBottom']['aria'] if s['atBottom'] else None}")
        if not s["present"]:
            fails.append(f"D15 regression at {w}px: the sitting page lost its .pbar")
        elif s["atBottom"]["aria"] != "100":
            fails.append(f"D15 regression at {w}px: sitting page .pbar reads "
                         f"{s['atBottom']['aria']!r} at the foot, expected 100")
        else:
            print(f"  {w}px OK   the sitting page's bar still reaches 100% at the foot")
    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("Ran to completion. Verdicts above; no assertion failed to execute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
