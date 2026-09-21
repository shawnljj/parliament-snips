#!/usr/bin/env python3
"""Measure the dot column straight out of the pixels of a probe_rail_shots.png strip.

The D3 claim is "every mark's centre sits on one x" -- so measure x, in the image, for every mark.
Uses only stdlib: PNG decode via zlib, then a per-row connected-component pass over "pixels that
are not the page background".

  python3 read_dots.py <strip.png> [label]
"""
import struct
import sys
import zlib


def load_png(path):
    raw = open(path, "rb").read()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a png"
    pos, idat, w, h, bd, ct = 8, b"", 0, 0, 0, 0
    while pos < len(raw):
        ln = struct.unpack(">I", raw[pos:pos + 4])[0]
        typ = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", data[:10])
        elif typ == b"IDAT":
            idat += data
        pos += 12 + ln
    assert bd == 8 and ct in (2, 6), f"unhandled png: bitdepth={bd} colortype={ct}"
    nch = 4 if ct == 6 else 3
    buf = zlib.decompress(idat)
    stride = w * nch
    out, prev = [], bytearray(stride)
    p = 0
    for _ in range(h):
        ft = buf[p]
        p += 1
        line = bytearray(buf[p:p + stride])
        p += stride
        for i in range(stride):
            a = line[i - nch] if i >= nch else 0
            b = prev[i]
            c = prev[i - nch] if i >= nch else 0
            if ft == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ft == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif ft == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out.append(bytes(line))
        prev = line
    return w, h, nch, out


def main():
    path = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else path
    # Optional x-band: the rail sits at the left edge of the clip, and everything to its right is
    # TEXT -- which is also made of non-background pixels, so an unbanded scan reports glyphs as
    # "dots" (measured: 110 "marks" with a 176px centre-x spread, which is a paragraph).
    band = None
    if len(sys.argv) > 3:
        band = tuple(int(v) for v in sys.argv[3].split("-"))
    w, h, nch, rows = load_png(path)
    x_hi = band[1] if band else w
    x_lo = band[0] if band else 0

    # The page background is the most common colour in the top-left corner region.
    from collections import Counter
    cnt = Counter()
    for y in range(0, min(h, 12)):
        for x in range(w):
            cnt[rows[y][x * nch:x * nch + 3]] += 1
    bg = cnt.most_common(1)[0][0]

    def is_bg(x, y):
        px = rows[y][x * nch:x * nch + 3]
        return all(abs(px[i] - bg[i]) <= 6 for i in range(3))

    # Per row, the runs of non-background pixels. A dot is a run whose width is ~the mark size,
    # so record every run and let the grouping below decide.
    runs = []
    for y in range(h):
        x = x_lo
        while x < x_hi:
            if not is_bg(x, y):
                x0 = x
                while x < x_hi and not is_bg(x, y):
                    x += 1
                runs.append((y, x0, x - 1))
            x += 1

    # Group runs into marks: consecutive rows whose runs OVERLAP in x.
    marks = []
    for y, x0, x1 in runs:
        for m in marks:
            if y <= m["y1"] + 1 and not (x1 < m["x0"] - 1 or x0 > m["x1"] + 1):
                m["y1"] = y
                m["x0"] = min(m["x0"], x0)
                m["x1"] = max(m["x1"], x1)
                m["n"] += 1
                break
        else:
            marks.append({"y0": y, "y1": y, "x0": x0, "x1": x1, "n": 1})

    # A dot is a mark that is roughly as wide as it is tall -- NOT a thin vertical dash (the tick
    # connector) and not the progress FILL, which is a 10x52 bar running down the column and would
    # otherwise be reported as the first "dot" and drag the centre-x spread to a false 1.0px.
    dots = []
    for m in marks:
        if m["n"] < 6:
            continue
        mw, mh = m["x1"] - m["x0"] + 1, m["y1"] - m["y0"] + 1
        if mw >= 4 and 0.6 <= mw / float(mh) <= 1.7:
            dots.append(m)
    dots.sort(key=lambda m: m["y0"])

    print(f"== {label}  ({path})  {w}x{h} bg=#{bg.hex()}")
    print("   dot    y0..y1     w  h   centre-x   centre-y")
    for m in dots:
        mw, mh = m["x1"] - m["x0"] + 1, m["y1"] - m["y0"] + 1
        cx = (m["x0"] + m["x1"]) / 2.0
        print(f"   #{dots.index(m)+1:<3} {m['y0']:>4}..{m['y1']:<4} {mw:>4} {mh:>3} "
              f"{cx:>10.1f} {(m['y0']+m['y1'])/2.0:>10.1f}")
    if dots:
        cxs = [(m["x0"] + m["x1"]) / 2.0 for m in dots]
        cys = [(m["y0"] + m["y1"]) / 2.0 for m in dots]
        y0s = [m["y0"] for m in dots]
        print(f"   centre-x: min={min(cxs):.1f} max={max(cxs):.1f} "
              f"spread={max(cxs) - min(cxs):.1f}px")
        # y0 pitch is NOT the row pitch when the marks are painted at different sizes inside the
        # same box (a 6px dot on an 8px row starts 1px lower). CENTRE pitch is the honest metric.
        print(f"   centre-y: {['%.1f' % y for y in cys]}")
        print(f"   centre pitch: {[round(cys[i+1]-cys[i], 2) for i in range(len(cys)-1)]}")
        print(f"   y0 pitch (size-sensitive, for reference): "
              f"{[y0s[i+1]-y0s[i] for i in range(len(y0s)-1)]}")


if __name__ == "__main__":
    main()
