#!/usr/bin/env python3
"""Drive the spike page over CDP and PROVE the rail tracks the reader.

Why this exists: the rail's scroll behaviour was silently broken once already -- it was
position:sticky with no overflow, so the sync() call did nothing and the active card just
drifted out of the viewport. That bug is invisible on a 4-section item and total on a
35-section one, and reading the CSS would not have caught it: the code LOOKED right and
the property was simply a no-op.

So this measures positions in a real browser instead of trusting the source. It scrolls
the document in steps and, at each step, asks: which card is active, and is it inside the
rail's visible box?

Usage: python3 tools/check_rail.py <file-or-url> [steps]
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

CHROME = None
for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Chromium.app/Contents/MacOS/Chromium",
          "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
    if os.path.exists(c):
        CHROME = c
        break

PROBE = r"""
(() => {
  const rail = document.querySelector('.rail');
  const on = document.querySelector('.card.on');
  const doc = document.documentElement;
  const out = {
    scrollY: Math.round(window.scrollY),
    docScrollable: doc.scrollHeight > window.innerHeight + 2,
    railScrollable: rail ? rail.scrollHeight > rail.clientHeight + 2 : null,
    railTop: rail ? Math.round(rail.getBoundingClientRect().top) : null,
    railBottom: rail ? Math.round(rail.getBoundingClientRect().bottom) : null,
    railScrollTop: rail ? Math.round(rail.scrollTop) : null,
    cards: document.querySelectorAll('.card').length,
    active: on ? +(on.dataset.sec) : null,
  };
  if (on) {
    const r = on.getBoundingClientRect();
    out.activeTop = Math.round(r.top);
    out.activeBottom = Math.round(r.bottom);
    // THE ASSERTION THAT MATTERS: is the active card inside the rail's visible box?
    out.activeVisible = rail
      ? (r.top >= rail.getBoundingClientRect().top - 2 &&
         r.bottom <= rail.getBoundingClientRect().bottom + 2)
      : null;
  }
  return JSON.stringify(out);
})()
"""


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    target = sys.argv[1] if len(sys.argv) > 1 else \
        "/Users/shawnlin/parsnips/site/dist/spike-panels.html"
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    if not target.startswith("http"):
        target = "file://" + os.path.abspath(target)

    port = 9333
    prof = tempfile.mkdtemp(prefix="cdp-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={port}",
         f"--user-data-dir={prof}", "--window-size=1500,900",
         "--no-first-run", "--no-default-browser-check", target],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws_url = None
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                        timeout=2) as r:
                tabs = json.load(r)
            for t in tabs:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    ws_url = t["webSocketDebuggerUrl"]
                    break
            if ws_url:
                break
        except Exception:                                           # noqa: BLE001
            time.sleep(0.5)
    if not ws_url:
        proc.kill()
        sys.exit("could not attach to the page")

    # minimal websocket client, so this needs no third-party package
    import base64
    import socket
    import struct
    from urllib.parse import urlparse

    u = urlparse(ws_url)
    sock = socket.create_connection((u.hostname, u.port), timeout=20)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((
        f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += sock.recv(4096)
    if b"101" not in buf.split(b"\r\n")[0]:
        proc.kill()
        sys.exit(f"handshake failed: {buf[:120]!r}")
    buf = buf.split(b"\r\n\r\n", 1)[1]

    def send(payload):
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
        sock.sendall(bytes(header) + masked)

    def recv():
        nonlocal buf
        while True:
            if len(buf) >= 2:
                b1, b2 = buf[0], buf[1]
                ln = b2 & 0x7F
                off = 2
                if ln == 126:
                    if len(buf) < 4:
                        buf += sock.recv(65536); continue
                    ln = struct.unpack(">H", buf[2:4])[0]; off = 4
                elif ln == 127:
                    if len(buf) < 10:
                        buf += sock.recv(65536); continue
                    ln = struct.unpack(">Q", buf[2:10])[0]; off = 10
                if len(buf) < off + ln:
                    buf += sock.recv(65536); continue
                payload = buf[off:off + ln]
                buf = buf[off + ln:]
                if b1 & 0x0F == 1:
                    return json.loads(payload)
                continue
            buf += sock.recv(65536)

    mid = [0]

    def call(method, **params):
        mid[0] += 1
        send({"id": mid[0], "method": method, "params": params})
        while True:
            msg = recv()
            if msg.get("id") == mid[0]:
                return msg

    def evaluate(expr):
        r = call("Runtime.evaluate", expression=expr, returnByValue=True,
                 awaitPromise=True)
        return (r.get("result", {}).get("result", {}) or {}).get("value")

    call("Runtime.enable")
    time.sleep(2.0)          # let fonts, layout and the first sync() settle

    first = json.loads(evaluate(PROBE))
    print(f"cards: {first['cards']}   rail scrollable: {first['railScrollable']}   "
          f"doc scrollable: {first['docScrollable']}")
    if not first["railScrollable"] and first["cards"] > 6:
        print("  NOTE: rail is not a scroll container -- it cannot track anything.")

    total = evaluate("document.documentElement.scrollHeight - window.innerHeight")
    total = int(total or 0)
    print(f"document scrollable height: {total}px; stepping in {steps} jumps\n")

    bad = []
    print(f"{'scrollY':>8} {'active':>7} {'cardTop':>8} {'cardBot':>8} "
          f"{'railTop':>8} {'railBot':>8} {'inview':>7}")
    for i in range(steps + 1):
        y = int(total * i / steps) if steps else 0
        evaluate(f"window.scrollTo(0, {y})")
        time.sleep(0.45)                       # let rAF sync() run
        s = json.loads(evaluate(PROBE))
        ok = s.get("activeVisible")
        print(f"{s['scrollY']:>8} {str(s.get('active')):>7} {str(s.get('activeTop')):>8} "
              f"{str(s.get('activeBottom')):>8} {str(s['railTop']):>8} "
              f"{str(s['railBottom']):>8} {str(ok):>7}")
        if ok is False:
            bad.append(s["scrollY"])

    proc.kill()
    print()
    if bad:
        print(f"FAIL: the active card was NOT visible at {len(bad)} of {steps + 1} "
              f"positions (scrollY {bad}).")
        return 1
    print(f"PASS: at every one of {steps + 1} scroll positions the active card was "
          f"inside the rail's viewport.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
