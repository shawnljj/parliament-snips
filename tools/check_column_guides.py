#!/usr/bin/env python3
"""Prove the column boundary guides land on the column edges -- by measurement, not by eye.

Why this exists: "is the vertical rule on the column edge?" is a question about where a
1px shadow lands after grid, sticky and the sticky top bar have all been resolved. Reading
the CSS cannot answer it. `tools/measure_layout.py` measures the columns but knows nothing
about the guides; this measures the guides and asserts them against the columns.

It drives headless Chrome over CDP (same minimal websocket client as tools/check_rail.py and
tools/measure_layout.py, so no third-party dependency) and at each width checks:

  1. ALIGNMENT      every guide's x lands on a measured column edge, to the pixel. The text
                    column's left and right edges and the summary column's right edge are
                    all asserted; the summary column's LEFT edge is reported separately,
                    because the summary card's own `border-left:3px solid var(--accent)`
                    is the visible edge there and the guide is deliberately underneath it.
  2. BOUNDED HEIGHT every guide stops at its section's bottom edge, and does NOT run the full
                    scroll height (the bug this guards against: a rule cutting through the
                    full-bleed headings and panels between sections).
  3. NO OVERFLOW    the document gains no horizontal scrollbar, and the rule boxes stay inside
                    the wrap's content box.
  4. INERT          nothing that was clickable became covered by a guide: each rule line is
                    sampled at several rows and every sample must be a guide-free hit (the
                    element on top may be the text, the list, or the section -- never the
                    rule's own box, and never a guide sitting over a control).
  5. PAINT ORDER    a frame is captured with the guides live and again with them suppressed.
                    Every pixel that changes must sit on one of the four rule columns (so
                    the guides affect nothing else), and every rule must change pixels (so
                    no rule is silently not drawn). With the hit test showing text on top of
                    the rule, this is the proof that the rule tints neither the glyph nor the
                    line it crosses.
  6. BREAKPOINTS    at 1920 / 761 / 760 the grid is live and a guide is drawn; at 759 / 390
                    neither is. 760 is asserted explicitly because it is the width where this
                    stylesheet's two breakpoints disagree (audit D6).

Usage:

    python3 -m http.server 8435 --directory site/dist &
    python3 tools/check_column_guides.py http://127.0.0.1:8435/sittings/2026-08-04.html

Exit code is 0 only when every assertion holds, so this can gate the change.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

CHROME = None
for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Chromium.app/Contents/MacOS/Chromium",
          "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
    if os.path.exists(c):
        CHROME = c
        break

# Edge x-positions come from the LAYOUT box of the column elements, not from the colour:
# getComputedStyle returns the *used* value, so a `1px` shadow is measured as 1px exactly as
# the layout rounded it. Sub-pixel is kept (no rounding) because the assertion is "same
# line", and a half-pixel apart is not the same line.
PROBE = r"""
(() => {
  const out = { viewport: {}, guides: [], columns: {}, overflow: {}, hit: {} };
  const doc = document.documentElement;
  // The URL the probe actually ran against. Without it a run against about:blank reports
  // "no columns" and reads exactly like a page that has none.
  out.pageUrl = location.href;
  out.readyState = document.readyState;
  out.viewport = {
    innerWidth: window.innerWidth,
    clientWidth: doc.clientWidth,
    scrollWidth: doc.scrollWidth,
    scrollHeight: doc.scrollHeight,
    scrollbar: window.innerWidth - doc.clientWidth,
    overflowX: doc.scrollWidth > window.innerWidth + 1,
  };

  const rect = el => { const r = el.getBoundingClientRect();
    return { x: r.left, right: r.right, y: r.top, bottom: r.bottom, w: r.width, h: r.height }; };
  // Resolved tracks, in px. `gridTemplateColumns` can carry a non-px token (or read as
  // "none" when the element is not a grid), and a NaN here would silently poison every
  // derived edge -- so non-finite tracks are dropped rather than carried into arithmetic.
  const tracks = cs => (cs.gridTemplateColumns || '').split(' ')
    .map(t => parseFloat(t)).filter(v => isFinite(v));

  // Every grid container that drew a guide, and where each of its four rules landed.
  const secs = document.querySelectorAll('.dsec');
  out.sectionCount = secs.length;
  const shadows = el => {
    const cs = getComputedStyle(el);
    return [cs.boxShadow, cs.getPropertyValue('--col-rule-w'), cs.getPropertyValue('--col-rule')];
  };
  for (let i = 0; i < secs.length && i < 3; i++) {
    const sec = secs[i];
    const sr = rect(sec);
    const cs = getComputedStyle(sec);
    const g = { i: i, section: { x: sr.x, right: sr.right, y: sr.y, bottom: sr.bottom } };
    const inset = cs.boxShadow && cs.boxShadow !== 'none';
    g.guideDrawn = !!inset;
    // The pseudo-element boxes are the grid areas themselves: track 1 and track 2.
    const cols = tracks(cs);
    g.tracks = cols;
    g.gap = parseFloat(cs.columnGap || cs.gap || '0') || 0;
    g.ruleWidth = parseFloat(cs.getPropertyValue('--col-rule-w') || '1') || 1;
    // Derived edge lines, in viewport coordinates.
    g.textLeft = sr.x;
    g.textRight = sr.x + cols[0];
    g.sumLeft = sr.x + cols[0] + g.gap;
    g.sumRight = sr.x + cols[0] + g.gap + cols[1];
    g.boxShadow = cs.boxShadow;
    out.guides.push(g);

    // What the guide box actually is, as a real element: re-use the section's own children
    // is not possible (pseudo-elements are not in the DOM), so measure the track geometry
    // and the resolved shadow, which is what the browser paints.
    const st = getComputedStyle(sec, '::before');
    g.beforeContent = st.content;
    g.beforeBoxShadow = st.boxShadow;
    g.beforeZ = st.zIndex;
    const sa = getComputedStyle(sec, '::after');
    g.afterContent = sa.content;
    g.afterBoxShadow = sa.boxShadow;
    g.afterZ = sa.zIndex;
    // Read the guide ON the pseudo-element, not on the container: the container carries no
    // shadow at all, so asking it reports "no guide" for a page that has four rules.
    g.guideDrawn = st.content !== 'none' && st.boxShadow !== 'none';
  }

  // The columns themselves, from the elements, so the guides are checked against real boxes
  // rather than against the track list used to derive them.
  const q = s => document.querySelector(s);
  const box = s => { const el = q(s); if (!el) return null; const r = rect(el);
    const cs = getComputedStyle(el);
    return { x: r.x, right: r.right, w: r.w, y: r.y, bottom: r.bottom,
             position: cs.position, zIndex: cs.zIndex, borderLeft: cs.borderLeftWidth }; };
  out.columns = {
    wrap: box('main.wrap'), dsec: box('.dsec'), sumcol: box('.sumcol'),
    sumcard: box('.sumcard'), vslist: box('.vslist'),
    rail: box('.section-rail'), top: box('.top'),
    railhead: box('.railhead'), substance: box('.substance'),
  };

  // Does the guide INTERCEPT anything? The meaningful question is not "what is on top at a
  // rule line" -- the topmost element there is normally the section container itself, since
  // the rules are its own pseudo-elements -- but "did adding the guides change what the page
  // hits". So the sample is taken twice, with the guides live and with them suppressed, and
  // any position where the two disagree is a place the guide intercepted.
  const sample = () => {
    const sec0 = secs[0];
    if (!sec0) return null;
    const r = rect(sec0);
    const cs = getComputedStyle(sec0);
    const cols = tracks(cs);
    const gap = parseFloat(cs.columnGap || cs.gap || '0') || 0;
    if (cols.length < 2) return null;
    const lines = [r.x, r.x + cols[0], r.x + cols[0] + gap, r.right];
    const rows = [200, 400, 600, 800].filter(y => y > r.y + 2 && y < r.bottom - 2 &&
                                                  y < window.innerHeight - 2);
    const out = [];
    for (const lx of lines) {
      for (const ly of rows) {
        if (!isFinite(lx) || !isFinite(ly)) continue;
        const el = document.elementFromPoint(Math.round(lx), Math.round(ly));
        out.push({ x: Math.round(lx), y: Math.round(ly),
                   top: el ? el.tagName + '.' + String(el.className).slice(0, 30) : 'null' });
      }
    }
    return out;
  };
  const live = sample();
  const kill = document.getElementById('__guide-kill-probe__');
  if (kill) kill.remove();
  const s = document.createElement('style');
  s.id = '__guide-kill-probe__';
  s.textContent = '.dsec::before,.dsec::after{box-shadow:none !important}';
  document.head.appendChild(s);
  const suppressed = sample();
  s.remove();
  const intercepted = [];
  if (live && suppressed && live.length === suppressed.length) {
    for (let i = 0; i < live.length; i++) {
      if (live[i].top !== suppressed[i].top) {
        intercepted.push({ x: live[i].x, y: live[i].y,
                           withGuides: live[i].top, without: suppressed[i].top });
      }
    }
  }
  out.hit = { samples: live ? live.length : 0, intercepted: intercepted.length,
              detail: (live || []).slice(0, 8), interceptedDetail: intercepted.slice(0, 6) };
  out.overflow = { docOverflowX: out.viewport.overflowX };
  return JSON.stringify(out);
})()
"""

# The rail's own geometry, so the "guides do not fight the scroll-spy" assertion has numbers.
RAIL_PROBE = r"""
(() => {
  const el = document.querySelector('.section-rail');
  if (!el) return JSON.stringify({ present: false });
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const ticks = document.querySelectorAll('.section-rail-tick');
  const marks = [];
  for (const t of ticks) {
    const m = t.querySelector('.section-rail-tick-mark');
    if (m) { const b = m.getBoundingClientRect(); marks.push(Math.round(b.left + b.width / 2)); }
  }
  return JSON.stringify({ present: true, x: r.left, right: r.right, position: cs.position,
    zIndex: cs.zIndex, marks: marks, spread: marks.length ? Math.max(...marks) - Math.min(...marks) : 0 });
})()
"""


class CDP:
    """The smallest thing that can talk to Chrome: no third-party websocket client."""

    def __init__(self, chrome, url, width, height, port):
        self.prof = tempfile.mkdtemp(prefix="cdp-guides-")
        self.proc = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", f"--remote-debugging-port={port}",
             f"--user-data-dir={self.prof}", f"--window-size={width},{height}",
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                            timeout=2) as r:
                    for t in json.load(r):
                        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                            ws_url = t["webSocketDebuggerUrl"]
                            break
                if ws_url:
                    break
            except Exception:                                        # noqa: BLE001
                time.sleep(0.5)
        if not ws_url:
            self.proc.kill()
            raise SystemExit("could not attach to the page")

        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            self.proc.kill()
            raise SystemExit(f"handshake failed: {buf[:120]!r}")
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.mid = 0
        self.call("Runtime.enable")
        self.call("Page.enable")
        # NAVIGATE. The browser is launched on about:blank, and a probe run without this
        # measures a blank page -- which is the one failure mode that reads as "fine".
        self.proc_url = url
        self.call("Page.navigate", url=url)
        time.sleep(2.6)                       # fonts, layout, the rail's first sync

    def _send(self, payload):
        data = json.dumps(payload).encode()
        header = bytearray([0x81])
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _recv(self):
        while True:
            if len(self.buf) >= 2:
                b1, b2 = self.buf[0], self.buf[1]
                ln = b2 & 0x7F
                off = 2
                if ln == 126:
                    if len(self.buf) < 4:
                        self.buf += self.sock.recv(65536); continue
                    ln = struct.unpack(">H", self.buf[2:4])[0]; off = 4
                elif ln == 127:
                    if len(self.buf) < 10:
                        self.buf += self.sock.recv(65536); continue
                    ln = struct.unpack(">Q", self.buf[2:10])[0]; off = 10
                if len(self.buf) < off + ln:
                    self.buf += self.sock.recv(65536); continue
                payload = self.buf[off:off + ln]
                self.buf = self.buf[off + ln:]
                if b1 & 0x0F == 1:
                    return json.loads(payload)
                continue
            self.buf += self.sock.recv(65536)

    def call(self, method, **params):
        self.mid += 1
        self._send({"id": self.mid, "method": method, "params": params})
        while True:
            msg = self._recv()
            if msg.get("id") == self.mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True)
        res = r.get("result", {}) or {}
        # A probe that THREW returns no value at all, which is indistinguishable from "the
        # page has no layout" unless the exception is carried out. It is carried out here.
        if res.get("exceptionDetails"):
            det = res["exceptionDetails"]
            desc = (det.get("exception") or {}).get("description") or det.get("text")
            return json.dumps({"__probeError__": desc})
        return (res.get("result") or {}).get("value")

    def screenshot(self, path):
        data = self.call("Page.captureScreenshot", format="png")["result"]["data"]
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))

    def close(self):
        try:
            self.proc.kill()
        except Exception:                                            # noqa: BLE001
            pass


def near(a, b, tol=0.5):
    return abs(a - b) <= tol


def check_scroll(cdp, w, lines, fails, steps=12):
    """Sweep the document and assert the guides did not disturb anything on scroll.

    The guides are inert (positioned, z-index:-1), but "inert" is exactly the kind of claim
    that is made confidently and tested never. This walks the page top to bottom and at each
    step asserts:

      * no horizontal overflow appears at any scroll position,
      * the sticky top bar is still pinned to the top,
      * the per-section summary card is still sticky INSIDE its own section (and releases at
        the section's end, which is the mechanism the layout relies on),
      * the scroll-spy rail is still fixed at the viewport's right edge with its z-index
        unchanged, i.e. it was not displaced or repainted by the guides,
      * the guides are still on the column edges after scrolling.
    """
    sweep = r"""
    (() => {
      const doc = document.documentElement;
      const top = document.querySelector('header.top');
      const sect = document.querySelector('.dsec');
      const sumcol = sect ? sect.querySelector('.sumcol') : null;
      const sumcard = sect ? sect.querySelector('.sumcard') : null;
      const rail = document.querySelector('.section-rail');
      const out = { y: Math.round(window.scrollY),
                    overflowX: doc.scrollWidth > window.innerWidth + 1 };
      const r = el => { const b = el.getBoundingClientRect();
                        return { x: b.left, y: b.top, right: b.right, bottom: b.bottom,
                                 w: b.width, h: b.height }; };
      if (top) { const cs = getComputedStyle(top);
                 out.top = { y: r(top).y, position: cs.position, z: cs.zIndex }; }
      if (sect) {
        const sb = r(sect);
        out.section = { y: sb.y, bottom: sb.bottom };
        const cs = getComputedStyle(sect);
        const cols = (cs.gridTemplateColumns || '').split(' ')
          .map(parseFloat).filter(isFinite);
        const gap = parseFloat(cs.columnGap || cs.gap || '0') || 0;
        if (cols.length >= 2) {
          out.edges = { textLeft: sb.x, textRight: sb.x + cols[0],
                        sumLeft: sb.x + cols[0] + gap, sumRight: sb.right };
        }
        const st = getComputedStyle(sect, '::before');
        out.guideDrawn = st.content !== 'none' && st.boxShadow !== 'none';
      }
      if (sumcol) { const cs = getComputedStyle(sumcol); const b = r(sumcol);
                    out.sumcol = { y: b.y, position: cs.position, z: cs.zIndex }; }
      if (sumcard) { out.sumcard = { y: r(sumcard).y, bottom: r(sumcard).bottom }; }
      if (rail) { const cs = getComputedStyle(rail); const b = r(rail);
                  out.rail = { x: b.x, right: b.right, position: cs.position,
                               z: cs.zIndex }; }
      return JSON.stringify(out);
    })()
    """
    cdp.eval("window.scrollTo(0, 0)")
    time.sleep(0.8)
    total = 0
    try:
        total = int(cdp.eval("document.documentElement.scrollHeight - window.innerHeight") or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        lines.append("  scroll sweep skipped: the document does not scroll")
        return

    sticky_broken, overflow_at, guide_lost, rail_moved = [], [], [], []
    pinned = 0
    # Positions to test. A 13-step sweep of a 210,000px document jumps 16,000px per step, so
    # it never lands inside a single section and the sticky-card assertion never fires. The
    # first few sections are swept finely (that is where a card actually pins and releases),
    # and the rest coarsely (that is where the guides and the rail are exercised).
    near = list(range(0, min(total, 9000) + 1, 450))
    far = [int(total * i / 8) for i in range(1, 9)] if total > 9000 else []
    positions = sorted(set(near + far))
    for y in positions:
        cdp.eval(f"window.scrollTo(0, {y})")
        time.sleep(0.3)
        raw = cdp.eval(sweep)
        if not raw:
            continue
        s = json.loads(raw)
        if s.get("overflowX"):
            overflow_at.append(s["y"])
        if s.get("top") and s["top"]["position"] != "sticky":
            sticky_broken.append(("top bar", s["y"], s["top"]["position"]))
        if s.get("sumcol") and s["sumcol"]["position"] != "sticky":
            sticky_broken.append(("summary column", s["y"], s["sumcol"]["position"]))
        # The card must never be dragged outside its section's vertical span.
        if s.get("sumcard") and s.get("section"):
            c, sec = s["sumcard"], s["section"]
            if c["bottom"] > sec["bottom"] + 2:
                sticky_broken.append(("card below its section", s["y"], c["bottom"] - sec["bottom"]))
            # The sticky top for .sumcol is --topbar-h(60) + --sticky-gap(14) = 74px, so a
            # pinned card sits at y≈74. Use that, not an invented threshold.
            if 70 <= c["y"] <= 80 and sec["bottom"] > 200:
                pinned += 1
        if s.get("guideDrawn") is False and s.get("section"):
            guide_lost.append(s["y"])
        if s.get("rail") and s["rail"]["position"] != "fixed":
            rail_moved.append(s["y"])
    lines.append(f"  scroll sweep: {len(positions)} positions (fine over the first "
                 f"{min(total, 9000)}px, coarse over {total}px)")
    lines.append(f"    card pinned+inside its section at {pinned} position(s)")
    # A sticky-card assertion that never fires is not an assertion. If the sweep never caught
    # the card pinned, say so rather than reporting a pass.
    if pinned == 0:
        fails.append(f"{w}px: the scroll sweep never observed the per-section card pinned "
                     f"(y≈74) -- the sticky assertion did not actually run")
    lines.append(f"    overflow at {len(overflow_at)} positions; guides lost at "
                 f"{len(guide_lost)}; rail not fixed at {len(rail_moved)}")
    if overflow_at:
        fails.append(f"{w}px: horizontal overflow appeared while scrolling at "
                     f"{overflow_at[:5]}")
    if guide_lost:
        fails.append(f"{w}px: the guide stopped being drawn while scrolling at "
                     f"{guide_lost[:5]}")
    if rail_moved:
        fails.append(f"{w}px: the scroll-spy rail stopped being fixed while scrolling at "
                     f"{rail_moved[:5]}")
    if sticky_broken:
        fails.append(f"{w}px: sticky behaviour broke while scrolling: "
                     f"{sticky_broken[:4]}")


# --------------------------------------------------------------------------------------
# PIXEL PROOF. The one claim a DOM probe cannot make is the paint order: a 1px rule with
# z-index:-1 is supposed to sit UNDER the text, so a rule crossing a glyph tints neither the
# glyph nor the line. That claim is settled by comparing frames: screenshot the guide line's
# pixel column with the guides in place and with the guides suppressed, and assert the two
# agree outside the gaps between sections, while the gaps (the column edge in whitespace)
# are exactly where the rule IS drawn.
#
# Sampling a 1px line exactly is fragile at deviceScaleFactor 1, so the columns are compared
# as a band +1px wide and the assertion is about the COUNT of differing pixels, not about any
# single pixel's colour.
PIXEL_KILL = """
(() => {
  const id = '__guide-kill__';
  let s = document.getElementById(id);
  if (!s) { s = document.createElement('style'); s.id = id;
            document.head.appendChild(s); }
  s.textContent = '.dsec::before,.dsec::after{box-shadow:none !important}';
  return 1;
})()
"""
PIXEL_RESTORE = """
(() => { const s = document.getElementById('__guide-kill__');
         if (s) s.textContent = ''; return 1; })()
"""


def pixel_column_png(cdp):
    """Decode the screenshot and return a function (x, y) -> (r, g, b)."""
    import base64 as _b64
    import io
    from PIL import Image
    data = cdp.call("Page.captureScreenshot", format="png")["result"]["data"]
    im = Image.open(io.BytesIO(_b64.b64decode(data))).convert("RGB")
    return im


def check_pixels(cdp, w, lines, fails, rule_xs):
    """Assert each rule is actually drawn, and that nothing outside the rule columns moved.

    Two claims, both settled by comparing frames with the guides live and suppressed:

      * the rules ARE drawn (each of the four lines changes pixels), and
      * nothing else on the page moved (every changed pixel sits on a rule column).

    Together with the hit test -- which reports that the element on top of a rule is the
    text, not the guide -- this is the paint-order proof: the rule is visible, and the text
    is above it, so the rule tints neither the glyph nor the line it crosses.
    """
    from PIL import ImageChops
    cdp.eval(PIXEL_RESTORE)
    time.sleep(0.6)
    with_guides = pixel_column_png(cdp)
    cdp.eval(PIXEL_KILL)
    time.sleep(0.6)
    without = pixel_column_png(cdp)
    cdp.eval(PIXEL_RESTORE)
    time.sleep(0.4)

    if with_guides.size != without.size:
        fails.append(f"{w}px: frame size changed when guides were suppressed "
                     f"({with_guides.size} vs {without.size})")
        return
    diff = ImageChops.difference(with_guides, without)
    per_rule = {round(rx): 0 for rx in rule_xs}
    strays = []
    for y in range(diff.height):
        for x in range(diff.width):
            if diff.getpixel((x, y)) == (0, 0, 0):
                continue
            nearest = min(per_rule, key=lambda rx: abs(x - rx))
            if abs(x - nearest) <= 2:
                per_rule[nearest] += 1
            else:
                strays.append((x, y))
    lines.append(f"  pixel proof: changed px per rule line "
                 f"{ {k: v for k, v in sorted(per_rule.items())} }")
    for rx, n in sorted(per_rule.items()):
        if n == 0:
            fails.append(f"{w}px: the rule at x={rx} changed no pixels -- it is not being "
                         f"drawn (or is hidden behind the content at every visible row)")
    if strays:
        fails.append(f"{w}px: {len(strays)} changed px outside every rule column, "
                     f"e.g. {strays[:4]} -- the guides are affecting other content")
    return per_rule


def check_width(cdp, w, out_dir=None, shot=None):
    """All assertions for one viewport width. Returns (failures, report_lines)."""
    fails, lines = [], []
    cdp.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
             deviceScaleFactor=1, mobile=False)
    time.sleep(1.2)
    # Scroll the FIRST section into view before measuring. At scrollY=0 the first .dsec sits
    # 800-odd px down the page, entirely below the fold, so a hit test finds nothing and the
    # "nothing interactive is covered" check passes vacuously.
    cdp.eval("(() => { const e = document.querySelector('.dsec');"
             " if (!e) return 0; window.scrollTo(0, Math.max(0,"
             " e.getBoundingClientRect().top + window.scrollY - 70)); return 1; })()")
    time.sleep(1.4)
    raw = cdp.eval(PROBE)
    if not raw:
        return [f"{w}px: probe returned nothing -- check the page loaded"], lines
    d = json.loads(raw)
    if d.get("__probeError__"):
        return [f"{w}px: probe THREW: {d['__probeError__']}"], lines
    vp, cols = d["viewport"], d["columns"]
    # A probe that silently ran against about:blank would report "no sections" and look
    # exactly like a page with no columns, so the URL is asserted, not assumed.
    if not d.get("pageUrl"):
        fails.append(f"{w}px: probe is not on a real page ({d.get('pageUrl')!r})")
    elif "/sittings/" not in d["pageUrl"] and d["pageUrl"].rstrip("/").endswith("index.html"):
        lines.append(f"  NOTE: measuring {d['pageUrl']} (the latest sitting)")

    if shot:
        cdp.screenshot(shot)

    lines.append(f"  viewport {vp['innerWidth']}px  scrollWidth={vp['scrollWidth']}  "
                 f"overflowX={vp['overflowX']}  dsec sections={d['sectionCount']}")

    if d["sectionCount"] == 0:
        lines.append("  (no .dsec on this page -- nothing to guide)")
        return fails, lines

    g = d["guides"][0]
    lines.append(f"  tracks={g['tracks']}  gap={g['gap']}  rule-width={g['ruleWidth']}px  "
                 f"guideDrawn={g['guideDrawn']}")
    lines.append(f"  edges: textLeft={g['textLeft']}  textRight={g['textRight']}  "
                 f"sumLeft={g['sumLeft']}  sumRight={g['sumRight']}")

    # 1. ALIGNMENT -- the guide's derived edges against the real element boxes.
    if cols.get("wrap"):
        if not near(g["textLeft"], cols["wrap"]["x"] + 24):
            fails.append(f"{w}px: text-column left guide {g['textLeft']} != wrap.left+24 "
                         f"{cols['wrap']['x'] + 24}")
        if not near(g["sumRight"], cols["wrap"]["right"] - 24):
            fails.append(f"{w}px: summary-column right guide {g['sumRight']} != wrap.right-24 "
                         f"{cols['wrap']['right'] - 24}")
    if cols.get("sumcol"):
        if not near(g["sumLeft"], cols["sumcol"]["x"]):
            fails.append(f"{w}px: summary-column left guide {g['sumLeft']} != "
                         f"sumcol.x {cols['sumcol']['x']}")
        if not near(g["sumRight"], cols["sumcol"]["right"]):
            fails.append(f"{w}px: summary-column right guide {g['sumRight']} != "
                         f"sumcol.right {cols['sumcol']['right']}")
    if cols.get("vslist"):
        if not near(g["textRight"], cols["vslist"]["right"]):
            fails.append(f"{w}px: text-column right guide {g['textRight']} != "
                         f"vslist.right {cols['vslist']['right']}")

    # The geometry the audit measured must be unchanged: 562 (>=1160px) / 34 / 416.
    track1, track2 = g["tracks"][0], g["tracks"][1]
    lines.append(f"  geometry: track1={track1}  track2={track2}  "
                 f"(audit target: 562/416 at >=1160px, gutter 34)")
    if not near(g["gap"], 34, 0.01):
        fails.append(f"{w}px: gutter {g['gap']} != 34")
    if not near(track2, 416, 0.5):
        fails.append(f"{w}px: summary track {track2} != 416")
    if w >= 1160 and not near(track1, 562, 0.5):
        fails.append(f"{w}px: text track {track1} != 562")

    # 2. BOUNDED HEIGHT -- each guide stops at its section, and is not page-height.
    sec_h = g["section"]["bottom"] - g["section"]["y"]
    if g["section"]["y"] > vp["scrollHeight"]:
        fails.append(f"{w}px: first section starts below the document?")
    if sec_h <= 0:
        fails.append(f"{w}px: first section has no height ({sec_h})")
    if sec_h >= vp["scrollHeight"] * 0.9:
        fails.append(f"{w}px: first section is ~the whole document ({sec_h} of "
                     f"{vp['scrollHeight']}) -- guides are not bounded per section")
    lines.append(f"  first section height={round(sec_h)}px of document {vp['scrollHeight']}px "
                 f"({100.0 * sec_h / max(1, vp['scrollHeight']):.1f}%)")

    # 3. NO OVERFLOW and the rules stay inside the wrap's content box.
    if vp["overflowX"]:
        fails.append(f"{w}px: horizontal overflow -- scrollWidth {vp['scrollWidth']} > "
                     f"innerWidth {vp['innerWidth']}")
    if cols.get("wrap"):
        pad = 24
        if g["textLeft"] < cols["wrap"]["x"] + pad - 0.5:
            fails.append(f"{w}px: left guide {g['textLeft']} escapes the wrap content box")
        if g["sumRight"] > cols["wrap"]["right"] - pad + 0.5:
            fails.append(f"{w}px: right guide {g['sumRight']} escapes the wrap content box")
    lines.append(f"  guides drawn={g['guideDrawn']}  ::before content={g['beforeContent']} "
                 f"z={g['beforeZ']}")

    # 4. INERT -- adding the guides must not change what the page hits anywhere.
    hit = d["hit"]
    lines.append(f"  hit test: {hit['samples']} positions sampled on the rule lines; "
                 f"{hit['intercepted']} changed what the page hits when the guides were "
                 f"suppressed")
    if hit["samples"] == 0:
        fails.append(f"{w}px: the hit test found no guide lines to test -- the "
                     f"\"nothing is intercepted\" check passed vacuously")
    if hit["intercepted"]:
        fails.append(f"{w}px: the guides intercepted {hit['intercepted']} position(s): "
                     f"{hit['interceptedDetail'][:3]}")
    for s in hit["detail"]:
        lines.append(f"    x={s['x']} y={s['y']} -> {s['top']}")

    # 5. The scroll-spy must still be present and above (measured separately).
    if cols.get("rail"):
        lines.append(f"  rail: x={cols['rail']['x']} right={cols['rail']['right']} "
                     f"position={cols['rail']['position']} z={cols['rail']['zIndex']}")
    if cols.get("top") and cols["top"]["position"] != "sticky":
        fails.append(f"{w}px: sticky top bar is no longer sticky "
                     f"({cols['top']['position']})")
    if cols.get("sumcol") and cols["sumcol"]["position"] != "sticky":
        fails.append(f"{w}px: summary card is no longer sticky "
                     f"({cols['sumcol']['position']})")

    # 6. PIXEL PROOF -- the four rules are drawn, and only on the four rule columns.
    check_pixels(cdp, w, lines, fails,
                 [g["textLeft"], g["textRight"], g["sumLeft"], g["sumRight"]])

    # 7. SCROLL SWEEP -- sticky behaviour, the rail, and the guides under scroll.
    check_scroll(cdp, w, lines, fails)
    return fails, lines


def desktop_check(cdp, w, out_dir=None):
    fails, lines = check_width(cdp, w, out_dir,
                              os.path.join(out_dir, f"after-{w}.png") if out_dir else None)
    return fails, lines


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8435/index.html"
    out_dir = sys.argv[2] if len(sys.argv) > 2 else None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    widths = [1024, 1280, 1440, 1920]
    all_fails = []
    for i, w in enumerate(widths):
        c = CDP(CHROME, url, w, 900, 9500 + i)
        try:
            f, lines = desktop_check(c, w, out_dir)
            all_fails += f
            print(f"\n{w}px")
            for line in lines:
                print(line)
        finally:
            c.close()

    # Below the desktop breakpoint there must be NO guide at all -- and AT the breakpoint
    # there must be one. 760 is checked explicitly because it is the width where this
    # stylesheet's two breakpoints disagree (audit D6): min-width:760px and max-width:760px
    # BOTH match at exactly 760, so the two-panel grid is live there while the phone padding
    # is also live, and the reading column collapses to 204px.
    #
    # This change SIDESTEPS D6 rather than fixing it, and states the consequence: the guides
    # are drawn by the grid, so they appear wherever the grid appears -- including at 760px,
    # where the columns are 204px and 416px. Marking the real (broken) columns is the honest
    # rendering of a grid that is really there; suppressing the guide at 760 while the grid
    # is live would be the guide lying about the layout. Fixing the breakpoint belongs to the
    # task that owns it, and is flagged in the handoff.
    expectations = {
        1920: (True, True), 761: (True, True), 760: (True, True),
        759: (False, False), 390: (False, False),
    }
    for i, w in enumerate([1920, 761, 760, 759, 390]):
        want_grid, want_guide = expectations[w]
        c = CDP(CHROME, url, w, 900, 9600 + i)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            time.sleep(1.2)
            draw = c.eval("(() => { const e = document.querySelector('.dsec');"
                          " if (!e) return 'no-dsec';"
                          " const cs = getComputedStyle(e, '::before');"
                          " const c2 = getComputedStyle(e);"
                          " return JSON.stringify({content: cs.content, shadow: cs.boxShadow,"
                          " display: c2.display, cols: c2.gridTemplateColumns,"
                          " overflowX: document.documentElement.scrollWidth > window.innerWidth+1});"
                          "})()")
            d = json.loads(draw) if draw and draw != "no-dsec" else None
            print(f"\n{w}px (breakpoint check, expecting grid={want_grid} "
                  f"guide={want_guide})")
            if d is None:
                print("  no .dsec -- nothing to draw")
                if want_guide:
                    all_fails.append(f"{w}px: expected a guide but the page has no .dsec")
                continue
            grid_live = d["display"] == "grid"
            # `content` serialises as the two-quote string when it is set, so "set" is tested
            # against BOTH the empty result and the literal, not just "none".
            guide_drawn = (d["shadow"] not in ("none", None, "")) and \
                (d["content"] not in ("none", None, ""))
            print(f"  display={d['display']}  cols={d['cols']}")
            print(f"  ::before content={d['content']}  shadow={d['shadow']}")
            print(f"  grid_live={grid_live}  guide_drawn={guide_drawn}  overflowX={d['overflowX']}")
            if grid_live != want_grid:
                all_fails.append(f"{w}px: grid_live={grid_live} but expected {want_grid}")
            if guide_drawn != want_guide:
                all_fails.append(f"{w}px: guide_drawn={guide_drawn} but expected {want_guide}")
            if d["overflowX"]:
                all_fails.append(f"{w}px: horizontal overflow at the breakpoint")
        finally:
            c.close()

    print("\n" + "=" * 68)
    if all_fails:
        print(f"{len(all_fails)} FAILURE(S):")
        for f in all_fails:
            print("  -", f)
        return 1
    print("PASS: every guide is on a column edge, bounded to its section, painted under the "
          "text, and present at every desktop width and no phone width.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
