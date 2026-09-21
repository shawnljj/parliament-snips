#!/usr/bin/env python3
"""Measure the desktop column geometry of a built Parsnips page, in a real browser.

Why this exists: the desktop layout has no vertical rule anywhere, so "does this element
sit on the column grid?" cannot be answered by reading the CSS -- it is a question about
where boxes actually land after grid, sticky and the scroll-spy rail have all been
resolved. The audit that this tool was written for had to answer exactly that, at four
viewport widths, with numbers rather than impressions.

It drives headless Chrome over CDP (same minimal websocket client as tools/check_rail.py,
so no third-party package) at 1024/1280/1440/1920, and at each width records:

  * the content container and the two-column section grid, as resolved pixel tracks,
  * every sticky/fixed element (sticky top bar, per-section summary card, scroll-spy rail,
    its pill/bubble preview, back-to-top, progress bar),
  * the positioning context of each of those, because a transformed or scroll-clipped
    ancestor silently turns position:sticky into a no-op,
  * horizontal overflow, which an element that escapes its column produces.

Usage:

    python3 tools/measure_layout.py http://127.0.0.1:8435/sittings/2026-08-04.html
    python3 tools/measure_layout.py <url> --out docs/layout-audit --widths 1024,1280

Writes <out>/measure-<width>.json, <out>/before-<width>.png and prints a table.
Exit code is 0 -- this is a measuring instrument, not a gate.
"""
import argparse
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

# One expression, evaluated in the page. Everything is rounded to whole pixels: the
# question the audit asks is "are these two edges on the same line", and sub-pixel noise
# does not change that answer.
PROBE = r"""
(() => {
  const out = {};
  const px = v => Math.round(v * 100) / 100;

  // The rect AND the resolved box model of one element, or null if absent/hidden.
  function box(sel, root) {
    const el = (root || document).querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return { present: false };
    const r = el.getBoundingClientRect();
    return {
      present: true,
      x: px(r.left), y: px(r.top), w: px(r.width), h: px(r.height),
      right: px(r.right), bottom: px(r.bottom),
      position: cs.position, top: cs.top, right_css: cs.right, zIndex: cs.zIndex,
      display: cs.display, opacity: cs.opacity,
      margin: cs.margin, padding: cs.padding,
      gridTemplateColumns: cs.gridTemplateColumns, gap: cs.gap,
      maxWidth: cs.maxWidth, width: cs.width,
      borderLeft: cs.borderLeftWidth, borderRight: cs.borderRightWidth,
    };
  }

  // Sticky silently no-ops under a transformed / filtered / scroll-clipped ancestor.
  function context(sel) {
    const el = document.querySelector(sel);
    if (!el) return null;
    const chain = [];
    for (let n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      const notable = [];
      if (cs.transform !== 'none') notable.push('transform:' + cs.transform);
      if (cs.filter !== 'none') notable.push('filter:' + cs.filter);
      if (cs.overflow !== 'visible') notable.push('overflow:' + cs.overflow);
      if (cs.overflowY !== 'visible') notable.push('overflow-y:' + cs.overflowY);
      if (cs.contain !== 'none') notable.push('contain:' + cs.contain);
      if (cs.willChange !== 'auto') notable.push('will-change:' + cs.willChange);
      if (cs.display === 'contents') notable.push('display:contents');
      if (notable.length) {
        chain.push((n.tagName.toLowerCase() + '.' + (n.className || '')).slice(0, 48)
                   + ' -> ' + notable.join(' '));
      }
    }
    return chain;
  }

  out.viewport = {
    innerWidth: window.innerWidth, innerHeight: window.innerHeight,
    clientWidth: document.documentElement.clientWidth,
    scrollbar: window.innerWidth - document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
    overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
    scrollY: Math.round(window.scrollY),
  };

  out.columns = {
    body: box('body'),
    wrap: box('main.wrap'),
    topWrap: box('.top .wrap'),
    lede: box('.lede'),
    h1: box('.lede h1'),
    slogan: box('.slogan'),
    substance: box('.substance'),
    dsec: box('.dsec'),
    sumcol: box('.sumcol'),
    sumwrap: box('.sumwrap'),
    sumcard: box('.sumcard'),
    vslist: box('.vslist'),
    vsText: box('.vs-text'),
    brief: box('details.brief'),
    footer: box('footer'),
    railhead: box('.railhead'),
  };

  out.sticky = {
    top: box('.top'),
    sumcol: box('.sumcol'),
    sumcard: box('.sumcard'),
    rail: box('.section-rail'),
    railTrack: box('.section-rail-track'),
    railFill: box('.section-rail-fill'),
    railName: box('.section-rail-name'),
    railPill: box('.section-rail-pill'),
    railBubble: box('.section-rail-bubble'),
    railGo: box('.section-rail-go'),
    totop: box('.totop'),
    pbar: box('.pbar'),
    resume: box('.resume'),
  };

  // The rail's ticks are what the reader sees, and they are laid out inside a box that is
  // taller than they are -- so measure the ticks themselves, not just their container.
  const ticks = document.querySelectorAll('.section-rail-tick');
  if (ticks.length) {
    const f = ticks[0].getBoundingClientRect(), l = ticks[ticks.length - 1].getBoundingClientRect();
    out.sticky.railTicks = {
      present: true, count: ticks.length,
      x: px(f.left), right: px(l.right), w: px(Math.max(f.width, l.width)),
      y: px(f.top), bottom: px(l.bottom), h: px(l.bottom - f.top),
    };
    // The marks (dots) are right-aligned inside each tick, so their column is what a
    // vertical guide on the right would have to line up with.
    const marks = [];
    for (let i = 0; i < ticks.length; i++) {
      const m = ticks[i].querySelector('.section-rail-tick-mark');
      if (m) { const r = m.getBoundingClientRect();
               marks.push({ x: px(r.left), right: px(r.right), cy: px(r.top + r.height / 2) }); }
    }
    out.sticky.railMarks = { present: true, first: marks[0],
                             last: marks[marks.length - 1] || null };
  }

  // Summary-card heights drive whether the sticky card can reach the rail's band.
  const cards = [];
  const secs = document.querySelectorAll('.dsec');
  for (let i = 0; i < secs.length; i++) {
    const c = secs[i].querySelector('.sumcard');
    if (!c) continue;
    const r = c.getBoundingClientRect();
    cards.push({ i: i, h: px(r.height), w: px(r.width), x: px(r.left),
                 text: (c.textContent || '').trim().length });
  }
  cards.sort((a, b) => b.h - a.h);
  out.sumcards = { count: cards.length, tallest: cards.slice(0, 5) };

  out.context = {
    sumcol: context('.sumcol'),
    top: context('.top'),
    rail: context('.section-rail'),
    totop: context('.totop'),
  };

  // How many things live in the right-hand margin, and do any of them share an edge?
  out.rail = {
    ticks: document.querySelectorAll('.section-rail-tick').length,
    names: document.querySelectorAll('.section-rail-name').length,
    dormant: !!document.querySelector('.section-rail.is-dormant'),
    dsecCount: document.querySelectorAll('.dsec').length,
    // Every level-1 section heading, in document order, with its left edge.
    headings: [].map.call(document.querySelectorAll('main.wrap h2'), h => {
      const r = h.getBoundingClientRect();
      return { text: (h.textContent || '').trim().slice(0, 44),
               left: px(r.left), width: px(r.width), cls: h.className };
    }).slice(0, 14),
  };
  return JSON.stringify(out);
})()
"""


class CDP:
    """The smallest thing that can talk to Chrome: no third-party websocket client."""

    def __init__(self, chrome, url, width, height, port):
        self.prof = tempfile.mkdtemp(prefix="cdp-measure-")
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
        return (r.get("result", {}).get("result", {}) or {}).get("value")

    def close(self):
        try:
            self.proc.kill()
        except Exception:                                            # noqa: BLE001
            pass


def _probe(cdp):
    """Evaluate the probe and parse it, failing loudly on a null result.

    ``Runtime.evaluate`` returns None when the expression throws, and a silent None here
    would look exactly like "the page has no layout" -- so it is an error, not a value.
    """
    raw = cdp.eval(PROBE)
    if not raw:
        raise SystemExit("probe returned nothing -- check the page loaded")
    return json.loads(raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default="docs/layout-audit")
    ap.add_argument("--widths", default="1024,1280,1440,1920")
    ap.add_argument("--prefix", default="before",
                    help="screenshot name prefix; use a second --out with 'after' so an "
                         "after-state run does not overwrite the baseline capture")
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--port", type=int, default=9344)
    ap.add_argument("--scroll-frac", type=float, default=0.25,
                    help="fraction down the page for the second, sticky-state capture")
    args = ap.parse_args()

    if not CHROME:
        sys.exit("no Chromium browser found")
    widths = [int(w) for w in args.widths.split(",")]
    os.makedirs(args.out, exist_ok=True)

    results = {}
    for i, w in enumerate(widths):
        c = CDP(CHROME, args.url, w, args.height, args.port + i)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=args.height,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(args.url)})")
            time.sleep(2.5)                       # fonts, layout, the rail's first sync
            top = _probe(c)

            with open(os.path.join(args.out, f"{args.prefix}-{w}.png"), "wb") as fh:
                fh.write(base64.b64decode(
                    c.call("Page.captureScreenshot", format="png")["result"]["data"]))

            # One scroll, then re-measure: sticky elements only reveal their offsets
            # once they are actually pinned.
            c.eval(f"window.scrollTo(0, Math.round((document.documentElement.scrollHeight"
                   f" - window.innerHeight) * {args.scroll_frac}))")
            time.sleep(1.6)
            scrolled = _probe(c)

            with open(os.path.join(args.out, f"{args.prefix}-{w}-scrolled.png"), "wb") as fh:
                fh.write(base64.b64decode(
                    c.call("Page.captureScreenshot", format="png")["result"]["data"]))

            results[str(w)] = {"at_top": top, "scrolled": scrolled}
            with open(os.path.join(args.out, f"measure-{w}.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"url": args.url, "width": w, "at_top": top,
                           "scrolled": scrolled}, fh, indent=2, sort_keys=True)
            print(f"{w}px  overflowX={top['viewport']['overflowX']}  "
                  f"scrollbar={top['viewport']['scrollbar']}  "
                  f"wrap={top['columns']['wrap']['x']}..{top['columns']['wrap']['right']}"
                  f"  dsec-cols={top['columns']['dsec']['gridTemplateColumns']}"
                  f"  sumcol={top['columns']['sumcol']['x']}.."
                  f"{top['columns']['sumcol']['right']}  rail="
                  f"{top['sticky']['rail']['x']}..{top['sticky']['rail']['right']}")
        finally:
            c.close()

    with open(os.path.join(args.out, "measurements.json"), "w", encoding="utf-8") as fh:
        json.dump({"url": args.url, "widths": results}, fh, indent=2, sort_keys=True)
    print(f"\nwrote {args.out}/measurements.json and the screenshot set")


if __name__ == "__main__":
    sys.exit(main())
