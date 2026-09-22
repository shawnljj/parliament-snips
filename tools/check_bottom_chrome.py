#!/usr/bin/env python3
"""Prove the bottom-pinned chrome does not overlap -- by measurement, not by reading the CSS.

Why this exists: audit D13 was a *real interaction* bug that no stylesheet-reading review had
caught: `.totop{bottom:16px}` and `.pbar{bottom:0;height:26px}` overlapped by 44x10px, and
because the bar carries z-index 44 against the button's 40, a click on the lower 10px of the
44px button hit the progress bar instead. The fix is a clearance that is arithmetically
obvious and geometrically easy to get wrong, so what matters is where the boxes actually land
once `position:fixed`, `calc()` and the breakpoints have been resolved.

This measures, at each width:

  1. NO OVERLAP      .totop's bottom edge is at or above .pbar's top edge, and the reported
                     clearance is the distance between them. (The bar is display:none below
                     761px, so on a phone the assertion is "the bar takes no space at all"
                     rather than a comparison against a hidden box -- a hidden box that still
                     carries a 26px height would make an overlap of 0 mean nothing.)
  2. HIT TEST        document.elementFromPoint() at the button's bottom edge, and at 1/3/6/10px
                     above it, resolves to .totop -- never .pbar / .pbar-txt. The 10px offset
                     is the exact band the defect was measured in. A synthetic click is also
                     dispatched at the bottom edge to prove it reaches a handler.
  3. THE TOAST (D14) the toast CLEARS the bar on desktop and wins the hit test at its own
                     bottom edge, and on the phone it is at its original 16px. The toast is
                     NOT in the server HTML -- offerResume() builds it from localStorage --
                     so a probe that only queries .resume measures nothing, which is why the
                     audit and the QA pass both recorded D14 as "latent, from CSS rather than
                     measurement". This tool seeds localStorage before any page script runs
                     (a plain seed + reload loses the race with the page's own pagehide
                     handler, which writes scrollY=0 over it) and reloads, so the toast is
                     measured live.
                     NOTE ON THE AUDIT'S D14 CLAIM: the desktop half of it -- "the desktop
                     toast is also behind the bar" -- does NOT reproduce. Measured on the
                     pre-change build, the toast sits at 38px, i.e. 12px ABOVE the 26px
                     bar. The assertion here is therefore the MEASURED clearance (bar top
                     minus toast bottom >= 0), not "the toast moved above the bar", and this
                     tool fails loudly if the toast ever stops rendering rather than passing
                     a vacuous test.
  4. PHONE UNCHANGED at <=760px the button is at its original 16px from the floor and 14px
                     from the right, the bar is display:none and the toast is at 16px -- the
                     toast's RIGHT inset is the one phone number t_91ef2ced moves, so it is
                     asserted as the clearance it now is (button inset + button width + gap)
                     rather than as the 16px it used to be, and the toast's HEIGHT is asserted
                     unchanged because the message wrapping must not change the box.
  5. TOAST vs BUTTON (card t_91ef2ced) the toast must NOT cover the button. The toast is
                     z 45, the button z 40, and they used to be pinned to the same corner, so
                     wherever they met the toast won and the button was unreachable for the
                     12s the toast was up -- measured, the toast covered the button by 42x44px
                     on the phone and 44x44px on desktop, and every elementFromPoint() sample
                     inside the button's circle resolved to the toast (or to the toast's own
                     dismiss control). The fix keeps the toast short of the button's column,
                     so this asserts: zero overlap between the two boxes, a gap of at least
                     8px, the button winning elementFromPoint() inside its own circle, and the
                     toast's own two controls still reachable (the rejected alternative, a
                     higher z-index on the button, failed exactly that last one).
                     This tool REPORTED this defect as "PRE-EXISTING (not D13)" while it was
                     unfixed, deliberately, so it could not be confused with the bar-over-
                     button defect; now that it is fixed it is asserted, and the same tool
                     fails on the build before the fix (30 failures).
  6. NO NEW CHROME   the bar itself is unmoved (display:block, 26px tall, css bottom 0px) and
                     no horizontal overflow appears at any width. The fix must move the things
                     above the bar, not resize the bar.

Usage:

    python3 -m http.server 8436 --directory site/dist &
    python3 tools/check_bottom_chrome.py http://127.0.0.1:8436

Exit code 0 only when every assertion holds, so this can gate the change.
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
for _c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium",
           "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
           "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
    if os.path.exists(_c):
        CHROME = _c
        break

SITTING = "/sittings/2026-08-04.html"
PAGE_ID = "2026-08-04"

# The widths the audit reproduced D13 at, plus both sides of the breakpoint the fix turns on:
# 760 (bar hidden, the max-width:760px rule applies) and 761 (bar live, the desktop clearance).
DESKTOP = [1024, 1280, 1440, 1920]
PHONE = [390, 759, 760]
EDGE = [761]

# Pre-fix measurements for the phone, from docs/layout-qa/raw-qa_defects-pristine.log (same
# instrument, the build before the column work). This is what "unchanged on the phone" is
# asserted against: the fix must not have moved the phone layout at all.
PHONE_TOTOP_BOTTOM = 16.0
PHONE_TOTOP_RIGHT = 14.0
PHONE_RESUME_BOTTOM = 16.0
# ...except this ONE number, which card t_91ef2ced changes on purpose: the toast's right inset.
# It was 16px, which put its right edge on the button (right:14px on a phone, width 44px).
# It is now the button's inset + the button's width + the gap, i.e. 66px, and the assertion
# below checks it as that arithmetic rather than as a literal, so the clearance cannot be
# satisfied by moving the button -- a wider button would have to push the toast further aside.
PHONE_RESUME_RIGHT_OLD = 16.0
TOAST_GAP_MIN = 8.0
# The toast's height on the phone, before and after. It does NOT change: the message wraps to two
# lines once the box is inset (measured at 360/375/390/414/430), but the toast is already 68px
# tall because its two 44px controls plus 24px of padding set the height, and two lines of 13.5px
# text need only 36.4px. So the inset costs no vertical space and moves nothing. 320px is the one
# width where three lines finally exceed the controls (measured 79px) and it is below the base
# 390px layout, so it is not asserted.
PHONE_RESUME_HEIGHT = 68.0

# The toast's bottom inset on desktop, measured on the pre-change build: 38px, i.e. 12px
# clear of the 26px bar. Asserted so that "the toast still clears the bar" cannot be satisfied
# by moving the toast somewhere else -- and so the audit's D14 desktop claim, which this
# measurement refutes, is recorded as a number rather than as an opinion. Card t_91ef2ced moved
# the toast HORIZONTALLY and deliberately left this vertical number alone.
DESKTOP_RESUME_BOTTOM = 38.0
DESKTOP_RESUME_CLEARANCE = 12.0

# Where the hit test is sampled on the button, measured upward from its bottom edge. 10px is
# the exact overlap height the defect was measured at.
HIT_OFFSETS = [1, 3, 6, 10]

# The button is a 44px CIRCLE (border-radius:50%), so its box corners are not hit-testable as
# the button -- sampling the corners of a circular control reports the page behind it at every
# z-index and is a property of the control's shape, not of any overlap. These samples sit inside
# the circle, where the button must win, and they are also taken on the TOAST's two controls.
CIRCLE_UNIT = [(0, 0), (0, -0.375), (0, 0.375), (0.375, 0), (-0.375, 0),
               (0.25, 0.25), (-0.25, 0.25), (0.25, -0.25), (-0.25, -0.25)]

PROBE = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const q = s => document.querySelector(s);
  const box = e => { if (!e) return null; const r = e.getBoundingClientRect();
    return { x: px(r.left), y: px(r.top), right: px(r.right), bottom: px(r.bottom),
             w: px(r.width), h: px(r.height) }; };
  const cs = e => e ? getComputedStyle(e) : null;
  const num = v => { const n = parseFloat(v); return isFinite(n) ? n : null; };
  const doc = document.documentElement;

  const totop = q('.totop'), pbar = q('.pbar'), res = q('.resume');
  const tb = box(totop), pb = box(pbar), rb = box(res);
  const pc = cs(pbar), tc = cs(totop), rc = cs(res);

  const pbarLive = !!pbar && pc && pc.display !== 'none' && pc.visibility !== 'hidden'
                   && pb && pb.h > 0;

  const out = {
    url: location.href,
    scrollY: Math.round(window.scrollY),
    viewport: { innerWidth: window.innerWidth, innerHeight: window.innerHeight,
                clientWidth: doc.clientWidth, scrollWidth: doc.scrollWidth,
                overflowX: doc.scrollWidth > window.innerWidth + 1 },
    totopPresent: !!totop, totopVisible: !!totop && tc.display !== 'none'
                  && tc.visibility !== 'hidden' && num(tc.opacity) !== 0,
    totop: tb,
    totopCss: tc ? { bottom: tc.bottom, bottomPx: num(tc.bottom), right: tc.right,
                     rightPx: num(tc.right), width: tc.width, widthPx: num(tc.width),
                     zIndex: tc.zIndex, display: tc.display,
                     visibility: tc.visibility, opacity: tc.opacity } : null,
    pbarPresent: !!pbar, pbarLive: pbarLive,
    pbar: pbarLive ? pb : null,
    pbarCss: pc ? { display: pc.display, height: pc.height, heightPx: num(pc.height),
                    bottom: pc.bottom, bottomPx: num(pc.bottom), zIndex: pc.zIndex } : null,
    resumePresent: !!res, resume: rb,
    resumeCss: rc ? { bottom: rc.bottom, bottomPx: num(rc.bottom), right: rc.right,
                      rightPx: num(rc.right), width: rc.width, widthPx: num(rc.width),
                      left: rc.left, maxWidth: rc.maxWidth, zIndex: rc.zIndex } : null,
    chromeVars: { chromeH: tc ? tc.getPropertyValue('--chrome-h').trim() : null,
                  pbarH: tc ? tc.getPropertyValue('--pbar-h').trim() : null },
    overlapH: 0, clearance: null, resumeOverlapH: null, resumeClearance: null,
    hits: {}, totopClick: null,
  };

  // 1. OVERLAP, only meaningful when the bar is actually laid out.
  if (tb && pbarLive) {
    out.overlapH = px(Math.max(0, Math.min(tb.bottom, pb.bottom) - Math.max(tb.y, pb.y)));
    out.clearance = px(pb.y - tb.bottom);
  }

  // 2. HIT TESTS down the button's centre column. Every sample must be the button -- but the
  //    button is not the only thing at the foot of the page, and the audit measured D13 in
  //    the state where the resume toast is NOT on screen. So the samples are taken twice:
  //    once with the toast dismissed (the D13 assertion: every sample is .totop, and the
  //    pre-fix build fails it at every offset with SPAN.pbar-txt), and once with the toast
  //    present (where the assertion is only "the bar is not the thing on top", because the
  //    toast covering the button is a separate, pre-existing defect -- see the report).
  if (tb && out.totopVisible) {
    const x = Math.round(tb.x + tb.w / 2);
    const sample = (y, key) => {
      const el = document.elementFromPoint(x, y);
      out.hits[key] = el ? { tag: el.tagName, cls: String(el.className || ''),
                             isTotop: !!el.closest('.totop'), isPbar: !!el.closest('.pbar'),
                             isResume: !!el.closest('.resume') } : null;
    };
    const sweep = prefix => {
      for (const off of [1, 3, 6, 10]) sample(Math.round(tb.bottom - off),
                                              prefix + off + 'px');
      if (out.overlapH > 0) {
        sample(Math.round((Math.max(tb.y, pb.y) + Math.min(tb.bottom, pb.bottom)) / 2),
               prefix + 'overlap-middle');
      }
    };
    sweep('totop-');
    // The button's own click handler, dispatched at its bottom edge.
    let reached = false;
    const ev = new MouseEvent('click', { bubbles: true, cancelable: true,
                                         clientX: x, clientY: Math.round(tb.bottom - 1) });
    totop.addEventListener('click', function once() { reached = true;
      totop.removeEventListener('click', once); });
    totop.dispatchEvent(ev);
    out.totopClick = { reachedHandler: reached, defaultPrevented: ev.defaultPrevented };

    // Is the toast covering the button at all, and by how much? With the toast up this is the
    // card t_91ef2ced assertion: it must be 0x0. Recorded by area, because a box that merely
    // touches (overlap 0 wide, 44 tall) is not a cover and would read as one if only the
    // heights were compared.
    if (res && rb) {
      const ow = px(Math.max(0, Math.min(tb.right, rb.right) - Math.max(tb.x, rb.x)));
      const oh = px(Math.max(0, Math.min(tb.bottom, rb.bottom) - Math.max(tb.y, rb.y)));
      out.totopToastOverlapH = oh;
      out.totopToastOverlapW = ow;
      out.totopToastOverlapArea = px(ow * oh);
      out.totopToastGapX = px(Math.max(0, Math.max(tb.x - rb.right, rb.x - tb.right)));
    }

    // WHO OWNS THE BUTTON'S OWN PIXELS, with the toast up. Sampled INSIDE the 44px circle:
    // its border-radius is 50%, so the corners of the box belong to the page behind it at any
    // z-index and asserting on them would fail forever for a reason that is not an overlap.
    // Before t_91ef2ced was fixed every one of these resolved to the toast or to the toast's
    // dismiss control.
    if (tb) {
      const unit = [[0,0],[0,-0.375],[0,0.375],[0.375,0],[-0.375,0],
                    [0.25,0.25],[-0.25,0.25],[0.25,-0.25],[-0.25,-0.25]];
      const owners = {};
      for (const [ux, uy] of unit) {
        const el = document.elementFromPoint(Math.round(tb.x + tb.w / 2 + ux * tb.w),
                                             Math.round(tb.y + tb.h / 2 + uy * tb.h));
        const key = el ? (el.closest('.totop') ? 'totop'
              : el.closest('.rgo') ? 'toast-resume'
              : el.closest('.rno') ? 'toast-dismiss'
              : el.closest('.resume') ? 'toast' : 'other') : 'none';
        owners[key] = (owners[key] || 0) + 1;
      }
      out.totopOwners = owners;
      out.totopOwnedByTotop = owners['totop'] || 0;
      out.totopOwnedByToast = (owners['toast'] || 0) + (owners['toast-resume'] || 0)
                            + (owners['toast-dismiss'] || 0);
      out.totopSamples = unit.length;
    }

    // AND THE TOAST MUST STILL BE USABLE. This is the assertion that rejected the alternative
    // fix (raise the button's z-index instead): measured on that variant, all three samples on
    // the dismiss control resolved to the button, so the toast could not be dismissed except by
    // waiting out its 12s timer.
    if (res) {
      const ctl = (e, key) => {
        if (!e) return;
        const b = e.getBoundingClientRect();
        const hits = [];
        for (const uy of [-0.3, 0, 0.3]) {
          const el = document.elementFromPoint(Math.round(b.left + b.width / 2),
                                              Math.round(b.top + b.height / 2 + uy * b.height));
          hits.push(!!el && !!el.closest('.resume'));
        }
        out[key] = { samples: hits.length, reachable: hits.filter(Boolean).length };
      };
      ctl(res.querySelector('.rgo'), 'resumeCtlResume');
      ctl(res.querySelector('.rno'), 'resumeCtlDismiss');
    }
  }

  // 3. THE TOAST, hit-tested while it is still in the document.
  if (res && rb && rc) {
    if (pbarLive && pb) {
      out.resumeOverlapH = px(Math.max(0, Math.min(rb.bottom, pb.bottom) - Math.max(rb.y, pb.y)));
      out.resumeClearance = px(pb.y - rb.bottom);
    }
    const el = document.elementFromPoint(Math.round(rb.x + rb.w / 2), Math.round(rb.bottom - 2));
    out.hits['resume-bottom'] = el
      ? { tag: el.tagName, cls: String(el.className || ''), isResume: !!el.closest('.resume'),
          isPbar: !!el.closest('.pbar') } : null;
    // Dismiss it, and NOW take the bare sweep. This is the state the audit measured D13 in:
    // its probe ran on the pristine build where the toast never rendered, so its
    // elementFromPoint() at the button's bottom edge resolved against the BAR. The toast
    // covering the button is a separate, pre-existing defect (the toast is z45, the button
    // z40, and they overlap because both are pinned to the same corner) -- reporting it as a
    // D13 failure would be blaming the bar for the toast.
    if (res.parentNode) {
      res.parentNode.removeChild(res);
      out.resumeRemoved = true;
      if (tb) {
        const x2 = Math.round(tb.x + tb.w / 2);
        const sample2 = key => {
          const e2 = document.elementFromPoint(x2, Math.round(tb.bottom - key));
          out.hits['bare-' + key + 'px'] = e2
            ? { tag: e2.tagName, cls: String(e2.className || ''),
                isTotop: !!e2.closest('.totop'), isPbar: !!e2.closest('.pbar') } : null;
        };
        [1, 3, 6, 10].forEach(sample2);
        if (out.overlapH > 0 && pb) {
          const oy2 = Math.round((Math.max(tb.y, pb.y) + Math.min(tb.bottom, pb.bottom)) / 2);
          const e2 = document.elementFromPoint(x2, oy2);
          out.hits['bare-overlap-middle'] = e2
            ? { tag: e2.tagName, cls: String(e2.className || ''),
                isTotop: !!e2.closest('.totop'), isPbar: !!e2.closest('.pbar') } : null;
        }
      }
    }
  }
  return JSON.stringify(out);
})()
"""


class CDP:
    """The smallest thing that can talk to Chrome: no third-party websocket client.

    Same minimal client as tools/check_rail.py and tools/check_column_guides.py, so this file
    has no dependency beyond the standard library and the browser.
    """

    def __init__(self, chrome, url, width, height, port):
        self.prof = tempfile.mkdtemp(prefix="cdp-chrome-")
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
        # measures a blank page -- the one failure mode that reads as "fine".
        self.navigate(url)

    def navigate(self, url, settle=2.6):
        self.call("Page.navigate", url=url)
        time.sleep(settle)

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

    def close(self):
        try:
            self.proc.kill()
        except Exception:                                            # noqa: BLE001
            pass


def near(a, b, tol=0.5):
    return a is not None and b is not None and abs(a - b) <= tol


def measure(base, url, w, port):
    """Load the page at width w WITH a seeded scroll position, and probe it."""
    cdp = CDP(CHROME, url, w, 900, port)
    try:
        # The device metrics override has to be in place BEFORE the navigation the probe
        # reads, so the layout is the one being asserted. `mobile` is deliberately false at
        # every width: this stylesheet's breakpoints are pure width queries, and the audit
        # and the QA pass both measured with mobile=False, so the numbers stay comparable.
        cdp.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                 deviceScaleFactor=1, mobile=False)
        # THE SEED. offerResume() builds the toast from localStorage and bails out below
        # s.y = 1200, so without a stored position the toast never renders and the D14 half
        # of this tool measures nothing -- which is exactly why both the audit and the QA pass
        # recorded D14 as "latent, from CSS rather than measurement".
        #
        # It cannot be seeded with a plain eval + reload: the page registers
        # window.addEventListener('pagehide', writeScroll), so navigating away writes the
        # CURRENT scrollY -- 0 -- straight over the seeded value. Verified: set {y:4000},
        # reload, read back {y:0}. addScriptToEvaluateOnNewDocument runs before any page
        # script on the new document, so it wins the race by construction.
        cdp.call("Page.addScriptToEvaluateOnNewDocument",
                 source="try{localStorage.setItem(%s, JSON.stringify({y: 4000, t: Date.now()}))}"
                        "catch(e){}" % json.dumps("parsnips:scroll:" + PAGE_ID))
        cdp.navigate(url)
        # .totop is only given .on past scrollY 900, and a visibility:hidden element is not a
        # hit-test target -- without this scroll every hit test below would return null.
        cdp.eval("window.scrollTo(0, 5000)")
        time.sleep(0.9)
        return json.loads(cdp.eval(PROBE))
    finally:
        cdp.close()


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8436"
    url = base + SITTING
    widths = PHONE + EDGE + DESKTOP
    fails, rows = [], []

    print("=" * 78)
    print("BOTTOM CHROME: back-to-top button + resume toast vs the desktop progress bar")
    print(f"page: {SITTING}   widths: {widths}   chrome: {os.path.basename(CHROME)}")
    print("=" * 78)

    for i, w in enumerate(widths):
        d = measure(base, url, w, 9840 + i * 2)
        print(f"\n----- {w}px  ({'phone' if w <= 760 else 'desktop'})")
        if "__probeError__" in d:
            print("  probe threw:", str(d["__probeError__"])[:300])
            fails.append(f"{w}px: probe threw")
            continue
        rows.append((w, d))
        print(f"  .totop   box={d['totop']}")
        print(f"           css={d['totopCss']}")
        print(f"  .pbar    live={d['pbarLive']} box={d['pbar']}  css={d['pbarCss']}")
        print(f"  .resume  present={d['resumePresent']} css={d['resumeCss']}")
        print(f"  --chrome-h={d['chromeVars']['chromeH']!r}  --pbar-h={d['chromeVars']['pbarH']!r}"
              f"  scrollY={d['scrollY']}  overflowX={d['viewport']['overflowX']}")

        if not d["totopPresent"]:
            fails.append(f"{w}px: no .totop in the document")
            continue
        if not d["totopVisible"]:
            fails.append(f"{w}px: the button is not visible at scrollY=5000 "
                         f"(css={d['totopCss']}) -- every hit test below would be vacuous")
        if d["viewport"]["overflowX"]:
            fails.append(f"{w}px: horizontal overflow appeared")

        # 5. THE BAR MUST NOT MOVE. Only the things above it.
        if d["pbarLive"]:
            if not near(d["pbarCss"]["heightPx"], 26.0):
                fails.append(f"{w}px: the bar's height is {d['pbarCss']['height']}, expected 26px")
            if not near(d["pbarCss"]["bottomPx"], 0.0):
                fails.append(f"{w}px: the bar's css bottom is {d['pbarCss']['bottom']}, "
                             f"expected 0px")
        elif w >= 761:
            fails.append(f"{w}px: the bar is not live at a desktop width")
        elif d["pbarPresent"] and d["pbarCss"]["display"] != "none":
            fails.append(f"{w}px: the bar is not display:none below 761px "
                         f"({d['pbarCss']['display']}) -- the phone has no bar to clear")

        if w <= 760:
            # 4. PHONE UNCHANGED, against the pre-fix numbers rather than against the CSS --
            #    except the toast's RIGHT inset, which card t_91ef2ced moves on purpose.
            if not near(d["totopCss"]["bottomPx"], PHONE_TOTOP_BOTTOM):
                fails.append(f"{w}px: phone button bottom is {d['totopCss']['bottom']}, "
                             f"expected {PHONE_TOTOP_BOTTOM}px")
            if not near(d["totopCss"]["rightPx"], PHONE_TOTOP_RIGHT):
                fails.append(f"{w}px: phone button right is {d['totopCss']['right']}, "
                             f"expected {PHONE_TOTOP_RIGHT}px")
            if d["resumePresent"] and not near(d["resumeCss"]["bottomPx"], PHONE_RESUME_BOTTOM):
                fails.append(f"{w}px: phone toast bottom is {d['resumeCss']['bottom']}, "
                             f"expected {PHONE_RESUME_BOTTOM}px")
            # The toast's right inset is now a CLEARANCE, asserted as the arithmetic rather
            # than as the literal 66px: button inset + button width + gap. A button that grew
            # would have to push the toast further aside, not simply pass this.
            want_right = (PHONE_TOTOP_RIGHT + d["totopCss"]["widthPx"] + TOAST_GAP_MIN
                          if d["totopCss"].get("widthPx") else None)
            if d["resumePresent"] and want_right is not None:
                if not near(d["resumeCss"]["rightPx"], want_right):
                    fails.append(f"{w}px: phone toast right is {d['resumeCss']['right']}, "
                                 f"expected {want_right}px (button inset "
                                 f"{PHONE_TOTOP_RIGHT} + width {d['totopCss']['widthPx']} + "
                                 f"gap {TOAST_GAP_MIN})")
                if near(d["resumeCss"]["rightPx"], PHONE_RESUME_RIGHT_OLD):
                    fails.append(f"{w}px: the phone toast is back at its old "
                                 f"{PHONE_RESUME_RIGHT_OLD}px inset, which is where the button "
                                 f"is -- this is the regression card t_91ef2ced fixed")
            # The inset must not have cost the toast any height. The message wraps to two lines
            # at these widths, but the two 44px controls already set the box's height, so the
            # measured height must be exactly what it was: this is the check that the fix moved
            # the toast sideways and nothing else.
            if d["resumePresent"] and d.get("resume") and not near(
                    d["resume"]["h"], PHONE_RESUME_HEIGHT):
                fails.append(f"{w}px: the toast's height changed with the inset: "
                             f"{d['resume']['h']}px, was {PHONE_RESUME_HEIGHT}px (the message "
                             f"wrapping must not change the toast's box)")
            print(f"  phone: bar hidden; button bottom {d['totopCss']['bottom']}, toast bottom "
                  f"{(d['resumeCss'] or {}).get('bottom')}, toast right "
                  f"{(d['resumeCss'] or {}).get('right')}")
        else:
            # 1. NO OVERLAP.
            print(f"  overlap={d['overlapH']}px  clearance(bar top - button bottom)="
                  f"{d['clearance']}px")
            if d["overlapH"] > 0:
                fails.append(f"{w}px: button/bar overlap is {d['overlapH']}px, expected 0")
            if d["clearance"] is not None and d["clearance"] < 0:
                fails.append(f"{w}px: the button's bottom edge is {-d['clearance']}px BELOW "
                             f"the bar's top edge")
            # 3. THE TOAST. It must still clear the bar, at the inset it already had.
            if not d["resumePresent"]:
                fails.append(f"{w}px: the resume toast did not render, so the D14 half of this "
                             f"tool measured nothing (localStorage seeding failed)")
            else:
                print(f"  toast bottom={d['resumeCss']['bottom']} "
                      f"overlap={d['resumeOverlapH']}px  clearance={d['resumeClearance']}px")
                if d["resumeOverlapH"] > 0:
                    fails.append(f"{w}px: toast/bar overlap is {d['resumeOverlapH']}px, "
                                 f"expected 0")
                elif d["resumeClearance"] is not None and d["resumeClearance"] < 0:
                    fails.append(f"{w}px: the toast's bottom edge is {-d['resumeClearance']}px "
                                 f"below the bar's top edge")
                # The toast must not have MOVED. Its desktop clearance was already correct;
                # the fix couples it to the bar arithmetically, it does not relocate it.
                if not near(d["resumeCss"]["bottomPx"], DESKTOP_RESUME_BOTTOM):
                    fails.append(f"{w}px: the toast moved: bottom is "
                                 f"{d['resumeCss']['bottom']}, was {DESKTOP_RESUME_BOTTOM}px on "
                                 f"the pre-change build")
                if not near(d["resumeClearance"], DESKTOP_RESUME_CLEARANCE):
                    fails.append(f"{w}px: the toast's clearance changed: "
                                 f"{d['resumeClearance']}px, was {DESKTOP_RESUME_CLEARANCE}px")

        # 2. HIT TESTS on the button, WITH THE TOAST DISMISSED. That is the state the audit
        #    measured D13 in, and the state in which "the bar swallows the button" is a pure
        #    statement about the bar.
        for off in HIT_OFFSETS:
            h = d["hits"].get(f"bare-{off}px")
            if not h:
                fails.append(f"{w}px: nothing at the button's bottom edge -{off}px "
                             f"(toast dismissed)")
            elif not h["isTotop"]:
                fails.append(f"{w}px: at the button's bottom edge -{off}px the hit is "
                             f"{h['tag']}.{h['cls']} (bar={h['isPbar']}), not .totop")
        h = d["hits"].get("bare-overlap-middle")
        if h and h["isPbar"]:
            fails.append(f"{w}px: the middle of the button/bar overlap still hits "
                         f"{h['tag']}.{h['cls']}")
        if d["totopClick"] and not d["totopClick"]["reachedHandler"]:
            fails.append(f"{w}px: a click at the button's bottom edge never reached its handler")

        # 2b. WITH THE TOAST PRESENT. The bar must still not be the thing on top of the button,
        #     and -- card t_91ef2ced -- the toast must not be either.
        for off in HIT_OFFSETS:
            h = d["hits"].get(f"totop-{off}px")
            if h and h["isPbar"]:
                fails.append(f"{w}px: with the toast up, the bar covers the button at "
                             f"-{off}px")
        # THE TOAST MUST NOT COVER THE BUTTON: no overlap in either axis, and a real gap.
        if d.get("totopToastOverlapArea", 0) > 0:
            fails.append(f"{w}px: the resume toast covers the button by "
                         f"{d['totopToastOverlapW']}x{d['totopToastOverlapH']}px "
                         f"({d['totopToastOverlapArea']}px^2) -- the toast is z45, the button "
                         f"z40, so the button is unreachable while the toast is up")
        elif d.get("totopToastOverlapW", 0) > 0:
            fails.append(f"{w}px: the toast's box and the button's box still intersect "
                         f"horizontally ({d['totopToastOverlapW']}px wide)")
        if d.get("totopToastGapX") is not None and d["totopToastGapX"] < TOAST_GAP_MIN:
            fails.append(f"{w}px: the toast is only {d['totopToastGapX']}px from the button, "
                         f"expected at least {TOAST_GAP_MIN}px")
        # The button must own its own pixels while the toast is up. Sampled inside the circle.
        if d.get("totopSamples"):
            if d.get("totopOwnedByToast", 0) > 0:
                fails.append(f"{w}px: with the toast up, {d['totopOwnedByToast']} of "
                             f"{d['totopSamples']} samples INSIDE the button resolve to the "
                             f"toast, not the button ({d.get('totopOwners')})")
            elif d.get("totopOwnedByTotop", 0) != d["totopSamples"]:
                fails.append(f"{w}px: with the toast up, only {d['totopOwnedByTotop']} of "
                             f"{d['totopSamples']} samples inside the button resolve to it "
                             f"({d.get('totopOwners')})")
        # ...and the toast must still be usable itself. This is what rules out "just give the
        # button a higher z-index": measured on that variant the button lands on the dismiss
        # control instead, and all three of its samples resolve to the button.
        if d["resumePresent"]:
            for key, label in (("resumeCtlResume", "Resume"), ("resumeCtlDismiss", "dismiss")):
                c = d.get(key)
                if not c:
                    fails.append(f"{w}px: the toast's {label} control is missing")
                elif c["reachable"] != c["samples"]:
                    fails.append(f"{w}px: the toast's {label} control is covered -- only "
                                 f"{c['reachable']} of {c['samples']} samples reach the toast")
        print(f"  toast vs button: overlap {d.get('totopToastOverlapW')}x"
              f"{d.get('totopToastOverlapH')}px (area {d.get('totopToastOverlapArea')}), "
              f"gap {d.get('totopToastGapX')}px; button owns "
              f"{d.get('totopOwnedByTotop')}/{d.get('totopSamples')} of its own samples; "
              f"toast controls "
              f"resume {(d.get('resumeCtlResume') or {}).get('reachable')}/"
              f"{(d.get('resumeCtlResume') or {}).get('samples')}, dismiss "
              f"{(d.get('resumeCtlDismiss') or {}).get('reachable')}/"
              f"{(d.get('resumeCtlDismiss') or {}).get('samples')}")

        # 3b. THE TOAST'S OWN BOTTOM EDGE.
        if d["resumePresent"]:
            h = d["hits"].get("resume-bottom")
            if not h:
                fails.append(f"{w}px: nothing at the toast's bottom edge")
            elif not h["isResume"]:
                fails.append(f"{w}px: at the toast's bottom edge the hit is {h['tag']}.{h['cls']}"
                             f" (bar={h['isPbar']}), not .resume")

        print("  hits, toast dismissed: " + "  ".join(
            f"{k}->" + (f"{v['tag']}.{v['cls']}" + ("(TOTOP)" if v.get("isTotop") else
                                                    ("(PBAR)" if v.get("isPbar") else ""))
                        if v else "None")
            for k, v in sorted(d["hits"].items()) if k.startswith("bare-")))
        print("  hits, toast present:   " + "  ".join(
            f"{k}->" + (f"{v['tag']}.{v['cls']}" + ("(TOTOP)" if v.get("isTotop") else
                                                    ("(RESUME)" if v.get("isResume") else
                                                     ("(PBAR)" if v.get("isPbar") else "")))
                        if v else "None")
            for k, v in sorted(d["hits"].items())
            if k.startswith("totop-") or k == "resume-bottom"))

    print()
    print("=" * 78)
    if fails:
        print(f"D13/D14 CHECK: {len(rows)} width(s) measured, {len(fails)} FAILURE(S)")
        print("=" * 78)
        for f in fails:
            print("  FAIL", f)
        return 1
    print(f"D13/D14 CHECK: {len(rows)} width(s) measured, 0 failures")
    print("=" * 78)
    for w, d in rows:
        if w <= 760:
            print(f"  {w:>5}px  phone: no bar; button bottom {d['totopCss']['bottom']}, "
                  f"right {d['totopCss']['right']}, toast bottom "
                  f"{(d['resumeCss'] or {}).get('bottom')} -- unchanged")
        else:
            print(f"  {w:>5}px  button/bar overlap {d['overlapH']}px (clearance "
                  f"{d['clearance']}px); toast/bar overlap {d['resumeOverlapH']}px "
                  f"(clearance {d['resumeClearance']}px); hits down the button's bottom "
                  f"{HIT_OFFSETS[-1]}px all .totop")
    print("\nPASS: the bar swallows no part of the button and no part of the toast, at every")
    print("      width, and the phone layout is byte-for-byte where it was.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
