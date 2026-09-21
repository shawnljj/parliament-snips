#!/usr/bin/env python3
"""Assert the unified desktop breakpoint, and that the whole page agrees on it.

Why this exists: audit D6 was two breakpoints disagreeing by one pixel -- the two-panel
grid on `min-width:760px`, the rail and progress bar on `min-width:761px`, and the phone
block on `max-width:760px`. At exactly 760px the grid was live *while* the phone padding
was also live, and the reading column collapsed to 204px. Fixing that is not one number;
it is the claim that EVERY width-dependent switch in this stylesheet is on the same
boundary, and that the two sides are complements so no width matches both.

`tools/check_column_guides.py` covers the grid and its guides. It cannot see the rail or
the pbar, and it cannot tell you whether the CSS boundary and the JS `matchMedia` boundary
are the same number. This is that check, and it asserts the card's acceptance criteria
directly:

  1. ONE BOUNDARY, STATICALLY   every `min-width` in the generated stylesheet is either
                               the desktop break, 700 (a type bump), or 1200 (the rail
                               label). No `min-width` may equal a `max-width`.
  2. ONE BOUNDARY, IN THE JS    the page's own `matchMedia` gates switch at the same
                               width as the CSS. A JS gate that disagreed with the CSS
                               would be the same defect one layer down: the two-panel
                               DOM would be filled for a phone, or not filled for a
                               desktop.
  3. EXACTLY ONE LAYOUT AT 760  measured, at 760 and at 759: the grid is off and the
                               reading column is the full phone column, never 204px.
  4. THE SAME SWITCH FOR RAIL   measured, at 760 and at 761: the rail and the pbar are
                               off at 760 and on at 761 -- the same boundary as the grid,
                               which is the "rail/pbar switch and the grid switch are the
                               same number" acceptance criterion.

Usage:

    python3 -m http.server 8480 --directory site/dist &
    python3 tools/check_breakpoints.py http://127.0.0.1:8480/sittings/2026-08-04.html

Exit code is 0 on pass, 1 on any failure.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from measure_layout import CHROME, CDP                              # noqa: E402

ROOT = os.path.dirname(HERE)
CSS = os.path.join(ROOT, "site", "dist", "theme.css")
SRC = os.path.join(ROOT, "site", "build_site.py")

# THE DECISION (t_bb9c77d0). The desktop layer is min-width:761px and the phone block is
# max-width:760px. These are adjacent integers, so they are exact complements: every
# viewport width matches exactly one of them, and 760 is a PHONE width.
#
# Why this side and not the other: the rail and the progress bar were ALREADY on
# min-width:761px (commit 6221dde, "Desktop progress bar"), and the phone block was already
# max-width:760px (commit dbcfa1f). Moving the grid's 760 up to 761 changes ONE number and
# both of the others stay put; moving the phone block down to 759 instead would also change
# one number, so the two are not distinguished by diff size. They are distinguished by what
# 760px IS: a 760px viewport is a small tablet in portrait, and the two-panel grid needs
# 562px of reading column plus a 416px summary column to be a two-panel layout at all. At
# 760px with the desktop padding the reading column resolves to 263px -- too narrow to read
# as prose. So 760 belongs to the phone side, which is where every other part of the page
# (rail, pbar, type scale, jump list) already put it.
DESKTOP_BREAK = 761
PHONE_BREAK = 760
# min-widths that are not the desktop switch: 700 is a two-line type bump on the brief
# title, 1200 is where the rail's margin can hold a text label. Neither is a layout switch
# and neither may collide with a max-width.
OTHER_ALLOWED_MIN = {700, 1200}
# The two values that make the rail's DESKTOP LAYER distinguishable from its phone form.
# Measured on the built page, not read from the CSS: the mobile layer keeps the original
# 70/90/91 z-scale and the track's 8px of horizontal padding, and the desktop block retunes
# both (see the comment above `@media (min-width:761px)` at the rail).
PHONE_RAIL_TRACK_PAD = "8px"
DESKTOP_RAIL_TRACK_PAD = "0px"
PHONE_Z_RAIL = "70"
DESKTOP_Z_RAIL = "24"

PROBE = r"""
(() => {
  const out = {};
  const px = v => Math.round(v * 100) / 100;
  const q = s => document.querySelector(s);
  const vis = e => { if (!e) return null;
    const cs = getComputedStyle(e);
    return cs.display !== 'none' && cs.visibility !== 'hidden'; };

  const dsec = q('.dsec');
  const wrap = q('main.wrap');
  const cols = dsec ? (getComputedStyle(dsec).gridTemplateColumns || '')
                      .split(' ').map(parseFloat).filter(isFinite) : [];
  out.w = window.innerWidth;
  out.gridLive = !!dsec && getComputedStyle(dsec).display === 'grid';
  out.tracks = cols;
  out.readingCol = cols.length ? px(cols[0]) : null;

  // The reading column when there is no grid: the wrap's content box, which is the phone
  // column. This is the number that must never be 204.
  if (wrap) {
    const cs = getComputedStyle(wrap);
    const r = wrap.getBoundingClientRect();
    out.wrapContentW = px(r.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight));
    out.wrapPadding = cs.padding;
  }

  // The rail is deliberately PRESENT at every width (one system, thumb-sized on a phone);
  // what switches at the boundary is its DESKTOP LAYER -- the track's horizontal padding
  // and the z-index retune that puts it below the fixed bottom chrome. Asserting presence
  // would fail on a correct build, so assert the layer.
  const track = q('.section-rail-track');
  out.railTrackPad = track ? getComputedStyle(track).paddingLeft : null;
  out.railZ = getComputedStyle(document.documentElement)
    .getPropertyValue('--z-rail').trim();
  out.railVisible = vis(q('.section-rail')) === true;
  out.pbarVisible = vis(q('.pbar')) === true;
  // The jump list is JS-driven (`min-width:761px` in the page's own script): on desktop it
  // is opened on load, on a phone it stays closed.
  const qj = q('details.qajump');
  out.jumpOpen = qj ? qj.open : null;

  out.overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
  return JSON.stringify(out);
})()
"""


def static_checks():
    """The boundary as stated in the generated stylesheet and in the page's own JS."""
    fails = []
    css = open(CSS, encoding="utf-8").read()
    mn = sorted(set(int(m) for m in re.findall(r"@media \(min-width:(\d+)px\)", css)))
    mx = sorted(set(int(m) for m in re.findall(r"@media \(max-width:(\d+)px\)", css)))
    lines = [f"  min-width  = {mn}",
             f"  max-width  = {mx}",
             f"  intersection = {sorted(set(mn) & set(mx))}"]

    collision = sorted(set(mn) & set(mx))
    if collision:
        fails.append(f"a min-width and a max-width both match at {collision} -- that is D6")

    # Every min-width must be the desktop break or a named exception. A stray layout
    # min-width is how this defect comes back.
    unexpected = [w for w in mn
                  if w != DESKTOP_BREAK and w not in OTHER_ALLOWED_MIN]
    if unexpected:
        fails.append(f"unexpected min-width breakpoint(s) {unexpected}: every layout "
                     f"switch must be {DESKTOP_BREAK}px (or one of {sorted(OTHER_ALLOWED_MIN)})")
    if mx != [PHONE_BREAK]:
        fails.append(f"max-width breakpoints are {mx}, expected exactly [{PHONE_BREAK}]")
    if DESKTOP_BREAK != PHONE_BREAK + 1:
        fails.append(f"{DESKTOP_BREAK} and {PHONE_BREAK} are not adjacent, so they are not "
                     f"complements and some width matches neither")
    lines.append(f"  desktop break = min-width:{DESKTOP_BREAK}px  "
                 f"phone break = max-width:{PHONE_BREAK}px  "
                 f"(complements: {DESKTOP_BREAK == PHONE_BREAK + 1})")

    # THE JS SIDE. The generated pages carry the gate inline, so read one.
    page = os.path.join(ROOT, "site", "dist", "sittings", "2026-08-04.html")
    js_gates = set()
    if os.path.exists(page):
        html = open(page, encoding="utf-8").read()
        js_gates = set(re.findall(r"matchMedia\('\(([a-z-]+)-width: (\d+)px\)'\)", html))
    else:
        fails.append(f"{page} is missing -- cannot check the JS gates")
    lines.append(f"  JS matchMedia gates on the page = "
                 f"{sorted(f'{k}-width:{v}px' for k, v in js_gates)}")
    for kind, w in sorted(js_gates):
        if kind == "min" and int(w) != DESKTOP_BREAK:
            fails.append(f"the page's JS switches at min-width:{w}px but the CSS switches "
                         f"at min-width:{DESKTOP_BREAK}px -- the JS and CSS boundaries disagree")
        if kind == "max" and int(w) != PHONE_BREAK:
            fails.append(f"the page's JS switches at max-width:{w}px but the phone block is "
                         f"max-width:{PHONE_BREAK}px -- the JS and CSS boundaries disagree")

    # And the generator, which is what will be regenerated next time.
    src = open(SRC, encoding="utf-8").read()
    src_gates = set(re.findall(r"matchMedia\('\(([a-z-]+)-width: (\d+)px\)'\)", src))
    bad_src = [(k, w) for k, w in src_gates
               if (k == "min" and int(w) != DESKTOP_BREAK)
               or (k == "max" and int(w) != PHONE_BREAK)]
    lines.append(f"  JS matchMedia gates in the generator = "
                 f"{sorted(f'{k}-width:{v}px' for k, v in src_gates)}")
    if bad_src:
        fails.append(f"the generator has matchMedia gate(s) off the boundary: {bad_src}")
    return lines, fails


def live_checks(url):
    fails = []
    lines = []
    # 760 and 759 are the SAME layout (phone) and 761 is the other one. If the boundary
    # were still 760/760 the 760 row would show a grid with a ~204px reading column.
    for i, w in enumerate([761, 760, 759]):
        c = CDP(CHROME, url, w, 900, 9920 + i * 2)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(url)})")
            time.sleep(1.4)
            d = json.loads(c.eval(PROBE))
        finally:
            c.close()
        want_grid = (w >= DESKTOP_BREAK)
        want_desktop_chrome = (w >= DESKTOP_BREAK)
        lines.append(f"  {w}px  grid={d['gridLive']} tracks={d['tracks']} "
                     f"readingCol={d['readingCol']} wrapContent={d['wrapContentW']}")
        lines.append(f"        rail_layer(pad/z)={d['railTrackPad']}/{d['railZ']} "
                     f"pbar={d['pbarVisible']} jumpOpen={d['jumpOpen']} "
                     f"overflowX={d['overflowX']}")

        if d["w"] != w:
            fails.append(f"{w}px: page reports innerWidth {d['w']} -- probe did not resize")
        if d["gridLive"] != want_grid:
            fails.append(f"{w}px: grid_live={d['gridLive']}, expected {want_grid}")
        # Criterion: the reading column is NEVER the collapsed 204px.
        for label, val in (("grid track", d["readingCol"]), ("phone column", d["wrapContentW"])):
            if val is not None and 200 <= val <= 210:
                fails.append(f"{w}px: {label} is {val}px, the collapsed width D6 produces")
        if d["readingCol"] is not None and d["readingCol"] < 240:
            fails.append(f"{w}px: reading column {d['readingCol']}px is too narrow to read "
                         f"prose -- the grid should not be live this narrow")
        # Criterion: the rail/pbar switch and the grid switch are the same number. The rail
        # is present either way, so this is its desktop layer (padding + z-index) that must
        # be on the same boundary the grid is.
        want_pad = DESKTOP_RAIL_TRACK_PAD if want_desktop_chrome else PHONE_RAIL_TRACK_PAD
        if d["railTrackPad"] != want_pad:
            fails.append(f"{w}px: rail track padding-left={d['railTrackPad']}, expected "
                         f"{want_pad} -- the rail's desktop layer does not switch with the grid")
        want_z = DESKTOP_Z_RAIL if want_desktop_chrome else PHONE_Z_RAIL
        if d["railZ"] != want_z:
            fails.append(f"{w}px: --z-rail={d['railZ']}, expected {want_z} -- the rail's "
                         f"desktop z retune does not switch with the grid")
        if d["pbarVisible"] != want_desktop_chrome:
            fails.append(f"{w}px: pbar visible={d['pbarVisible']}, expected "
                         f"{want_desktop_chrome} -- the pbar does not switch with the grid")
        if d["jumpOpen"] is not None and bool(d["jumpOpen"]) != want_desktop_chrome:
            fails.append(f"{w}px: jump list open={d['jumpOpen']}, expected "
                         f"{want_desktop_chrome} -- the JS gate does not switch with the CSS")
        if d["overflowX"]:
            fails.append(f"{w}px: horizontal overflow at the breakpoint")
    return lines, fails


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "http://127.0.0.1:8480/sittings/2026-08-04.html"
    print("=" * 70)
    print(f"STATIC: the boundary in the generated stylesheet, the pages and the generator")
    print("=" * 70)
    slines, sfails = static_checks()
    for l in slines:
        print(l)
    print()
    print("=" * 70)
    print("LIVE: 761 / 760 / 759 in a real browser")
    print("=" * 70)
    llines, lfails = live_checks(url)
    for l in llines:
        print(l)
    print()
    fails = sfails + lfails
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print(f"PASS: one breakpoint at {DESKTOP_BREAK}px. At 760px exactly one layout is live "
          f"(the phone's), the reading column is never the collapsed 204px, and the grid, "
          f"the rail, the pbar and the page's own JS all switch on the same number.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
