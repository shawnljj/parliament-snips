#!/usr/bin/env python3
"""Diff before/after frames at IDENTICAL scroll positions, ignoring the bar's own last 26px.

qa_build_diff.py proves the HTML changed only by the pbar block, but HTML equality is not visual
equality, and a screenshot taken "scrolled to the bottom" lands at a different y once the page is
26px shorter -- which would show a diff everywhere for the wrong reason. This pins both frames to
the same scroll offset and compares them, then reports WHERE they differ.

    python3 tools/qa_d15_pixels.py <after-base> <before-base> <out-dir>
"""
import json
import os
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_column_guides import CHROME, CDP                            # noqa: E402

ARCHIVE = "/sittings/index.html"
SITTING = "/sittings/2026-08-04.html"
PBAR_H = 26


def decode_png(path):
    """Minimal PNG reader: 8-bit RGB/RGBA, no interlace. Returns (w, h, bytes, channels)."""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    pos, idat, w, h, chans, depth, ctype = 8, b"", 0, 0, 0, 0, 0
    while pos < len(data):
        ln = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w = int.from_bytes(body[0:4], "big")
            h = int.from_bytes(body[4:8], "big")
            depth, ctype = body[8], body[9]
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    assert depth == 8, f"{path}: bit depth {depth} unsupported"
    chans = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * chans
    out = bytearray(stride * h)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(chans, stride):
                line[i] = (line[i] + line[i - chans]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - chans] if i >= chans else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - chans] if i >= chans else 0
                b = prev[i]
                c = prev[i - chans] if i >= chans else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, bytes(out), chans


def shoot(c, base, path, width, scroll_y, out):
    c.call("Emulation.setDeviceMetricsOverride", width=width, height=900,
           deviceScaleFactor=1, mobile=False)
    c.eval(f'location.replace("{base + path}")')
    time.sleep(2.6)
    c.eval(f"window.scrollTo(0, {scroll_y})")
    time.sleep(0.8)
    # The bar is position:fixed, so suppress it in BOTH frames and compare the PAGE, not the
    # chrome. Its own absence is asserted by qa_archive_pbar.py / qa_defects.py, not here.
    c.eval("document.querySelectorAll('.pbar,.section-rail,.totop,.resume')"
           ".forEach(e => e.style.visibility = 'hidden')")
    time.sleep(0.5)
    c.screenshot(out)
    return decode_png(out)


def settled_max_scroll(c, base, path, width):
    """Navigate first, then read the maximum scroll offset -- and wait for it to STOP changing.

    This started out as a bare eval on a fresh tab, which read about:blank and returned 2857 for
    both builds: a number that is not this page's geometry at all. It also has to settle, because
    a page that is still growing reports a max that is too small. Both mistakes produce a scroll
    target the comparison silently clamps, which would have made the pixel test prove less than
    it appeared to.
    """
    c.call("Emulation.setDeviceMetricsOverride", width=width, height=900,
           deviceScaleFactor=1, mobile=False)
    c.eval(f'location.replace("{base + path}")')
    time.sleep(2.6)
    last, stable = None, 0
    for _ in range(10):
        v = json.loads(c.eval("JSON.stringify(document.documentElement.scrollHeight"
                              " - window.innerHeight)"))
        if v == last:
            stable += 1
            if stable >= 3:
                return v
        else:
            last, stable = v, 0
        time.sleep(0.4)
    return last


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    if len(sys.argv) < 4:
        sys.exit("usage: qa_d15_pixels.py <after-base> <before-base> <out-dir>")
    after_base, before_base, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)
    fails = []
    for label, path in (("archive", ARCHIVE), ("sitting", SITTING)):
        for w in (1280, 1440):
            # Pin to a scroll offset BOTH pages can reach (the shorter of the two maxima), so
            # neither frame is clamped and the comparison is of the same rows in both.
            c = CDP(CHROME, after_base + path, w, 900, 12000 + w)
            try:
                h_after = settled_max_scroll(c, after_base, path, w)
            finally:
                c.close()
            c = CDP(CHROME, before_base + path, w, 900, 12100 + w)
            try:
                h_before = settled_max_scroll(c, before_base, path, w)
            finally:
                c.close()
            y = min(h_after, h_before)
            fa = os.path.join(out_dir, f"{label}-{w}-after.png")
            fb = os.path.join(out_dir, f"{label}-{w}-before.png")
            c = CDP(CHROME, after_base + path, w, 900, 12200 + w)
            try:
                wa, ha, pa, ca = shoot(c, after_base, path, w, y, fa)
                landed_after = json.loads(c.eval("JSON.stringify(Math.round(window.scrollY))"))
            finally:
                c.close()
            c = CDP(CHROME, before_base + path, w, 900, 12300 + w)
            try:
                wb, hb, pb, cb = shoot(c, before_base, path, w, y, fb)
                landed_before = json.loads(c.eval("JSON.stringify(Math.round(window.scrollY))"))
            finally:
                c.close()
            print(f"--- {label} {w}px at scrollY={y} "
                  f"(after max {h_after} landed {landed_after} | "
                  f"before max {h_before} landed {landed_before})")
            if landed_after != landed_before:
                fails.append(f"{label} {w}px: the two frames landed at different offsets "
                             f"({landed_after} vs {landed_before}) -- frames are not comparable")
                continue
            if (wa, ha, ca) != (wb, hb, cb):
                fails.append(f"{label} {w}px: frame geometry differs "
                             f"{wa}x{ha}x{ca} vs {wb}x{hb}x{cb}")
                continue
            diff_rows, diff_px = [], 0
            stride = wa * ca
            for row in range(ha):
                base = row * stride
                if pa[base:base + stride] != pb[base:base + stride]:
                    n = sum(1 for i in range(base, base + stride, ca)
                            if pa[i:i + ca] != pb[i:i + ca])
                    diff_px += n
                    diff_rows.append((row, n))
            print(f"    differing pixels: {diff_px} across {len(diff_rows)} row(s) of {ha}")
            if diff_rows:
                for row, n in diff_rows[:8]:
                    print(f"      row {row}: {n}px")
            if diff_px:
                fails.append(f"{label} {w}px: {diff_px} pixels differ with the chrome suppressed "
                             f"-- the fix changed more than the page's bottom chrome")
            else:
                print("    IDENTICAL: the page body is pixel-identical with the chrome suppressed")
    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("OK: with the bottom chrome suppressed, the page body is pixel-identical before and "
          "after at the same scroll offset.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
