#!/usr/bin/env python3
"""Chunked full-DOM computed-style comparison between two builds.

Why not tools/compare_mobile.py: that tool returns every element's ~50 properties in ONE CDP
response. On this page that is ~29,000 element-snapshots in a single JSON string, and the response
comes back CORRUPTED rather than refused -- measured on this branch, it reported the document as
86,469px tall on one port and 493,936px on the other, while a direct probe of both says 99,168px,
and it reported font-size 20px / box-sizing content-box / white text on the html element of one
build, which is the "stylesheet not applied yet" state. Its own docstring says "layout is
deterministic at offset 0", which is only true if the rail and the sticky columns are excluded --
they are not, and `osw-in` (a scroll-driven element) shows up in the diff with `top` values that
differ by exactly the ratio of the two bogus heights.

This keeps the comparison and fixes the transport: the document is walked in chunks, each
element's computed style is HASHED inside the page, and only the differing keys are fetched for
their values. Response size is therefore proportional to the DIFF, not to the page. Both builds
are parked at the same mid-document scroll offset first, so scroll-driven state is matched.

  python3 tools/compare_mobile_chunked.py <base-a> <base-b> [widths]

`a` is the BASELINE and `b` the FIXED build; the diff reads baseline -> fixed (A -> B). Exit 0 iff
the two builds are identical at every width.
"""
import json
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402

PAGE = "/sittings/2026-08-04.html"
CHUNK = 500

PROPS = ["display", "position", "visibility", "opacity", "width", "height", "maxWidth",
         "minWidth", "margin", "padding", "gap", "rowGap", "columnGap", "top", "right", "bottom",
         "left", "transform", "fontSize", "lineHeight", "fontWeight", "color", "backgroundColor",
         "borderBottom", "borderRadius", "boxShadow", "textAlign", "overflow", "textOverflow",
         "whiteSpace", "letterSpacing", "textTransform", "cursor", "boxSizing", "justifyContent",
         "alignItems", "order", "flex", "flexBasis", "gridTemplateColumns", "gridColumn",
         "aspectRatio", "zIndex", "flexShrink"]

HASHES = """
(() => {
  const PROPS = %s;
  const px = v => Math.round(v * 100) / 100;
  const all = [].slice.call(document.querySelectorAll('*'))
                .filter(e => e.tagName !== 'SCRIPT' && e.tagName !== 'STYLE');
  const start = %d, end = Math.min(%d, all.length);
  const out = [];
  const hash = s => { let h = 2166136261; for (let i = 0; i < s.length; i++) {
                        h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0); };
  for (let i = start; i < end; i++) {
    const e = all[i];
    const cs = getComputedStyle(e), r = e.getBoundingClientRect();
    const vals = PROPS.map(function (p) { return cs[p]; });
    const rect = [px(r.left), px(r.top), px(r.width), px(r.height)].join(',');
    const cls = typeof e.className === 'string' ? e.className : '';
    /* KEYED BY (tag, class, nth-of-that-kind), NOT BY TREE PATH. A path key cannot survive this
       branch's own D16 change: the trailing gap marker moved from a sibling of the <ol> to its
       last item, so every `.gapd`/`.gapi` marker in 172 sections changes path and a path-keyed
       diff reports ~7,900 "elements only in A / only in B" for a fix that is correct. An ordinal
       key inside a tag+class group is stable under re-parenting and still catches a duplicated,
       missing or reordered element of any kind. */
    out.push([i, e.tagName + '.' + cls + '#' + window.__ordn[i], rect,
              hash(vals.join('\\u0001'))]);
  }
  return JSON.stringify({ total: all.length, els: out,
                          scrollH: document.documentElement.scrollHeight,
                          scrollW: document.documentElement.scrollWidth,
                          vw: window.innerWidth, sw: document.documentElement.scrollWidth });
})()
""" % (json.dumps(PROPS), 0, CHUNK)

# The ordinal counter is shared across chunks, so it is computed in one pass over the whole
# document on the page side and then consulted by index.
ORDN = """
(() => {
  const all = [].slice.call(document.querySelectorAll('*'))
                .filter(e => e.tagName !== 'SCRIPT' && e.tagName !== 'STYLE');
  const seen = {};
  window.__ordn = {};
  for (let i = 0; i < all.length; i++) {
    const e = all[i];
    const cls = typeof e.className === 'string' ? e.className : '';
    const k = e.tagName + '.' + cls;
    seen[k] = (seen[k] || 0) + 1;
    window.__ordn[i] = seen[k];
  }
  return 'ok';
})()
"""

VALUES = """
(() => {
  const PROPS = %s;
  const px = v => Math.round(v * 100) / 100;
  const all = [].slice.call(document.querySelectorAll('*'))
                .filter(e => e.tagName !== 'SCRIPT' && e.tagName !== 'STYLE');
  const idx = %s;
  const out = {};
  idx.forEach(function (i) {
    const e = all[i];
    if (!e) { out[i] = null; return; }
    const cs = getComputedStyle(e), r = e.getBoundingClientRect();
    out[i] = {};
    PROPS.forEach(function (p) { out[i][p] = cs[p]; });
    out[i]["__rect"] = [px(r.left), px(r.top), px(r.width), px(r.height)].join(',');
  });
  return JSON.stringify(out);
})()
""" % (json.dumps(PROPS), "%s")


def snapshot(base, w, port, scroll=True):
    c = CDP(CHROME, base + PAGE, w, 900, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
               deviceScaleFactor=1, mobile=(w < 761))
        c.eval("location.replace(%s)" % json.dumps(base + PAGE))
        time.sleep(3.0)
        if scroll and w >= 761:
            # Park both builds in the same scroll state: the rail and the sticky summary column
            # are scroll-dependent, so comparing them at offset 0 compares two different states.
            #
            # BELOW 761 THIS STAYS OFF. The page fills the faded skipped runs on scroll, and
            # `isWide()` (min-width:760px) decides how much of that happens -- it is an async
            # fetch resolved against the page's own data, so a scrolled phone capture is only as
            # deterministic as that fill: measured on this card, the SAME build at 390 came back
            # as 5,461 and 12,505 elements in two runs. Offset 0 has no fill and is stable.
            c.eval("window.scrollTo(0, Math.round((document.documentElement.scrollHeight"
                   " - window.innerHeight) * 0.5))")
            time.sleep(1.0)
        c.eval(ORDN)
        raw = c.eval(HASHES)
        first = json.loads(raw if raw is not None else "{}")
        els = {e[1]: (e[0], e[2], e[3]) for e in first["els"]}
        start = CHUNK
        while start < first["total"]:
            q = HASHES.replace("const start = %d," % 0, "const start = %d," % start)
            q = q.replace("end = Math.min(%d, all.length)" % CHUNK,
                          "end = Math.min(%d, all.length)" % (start + CHUNK))
            d = json.loads(c.eval(q))
            for e in d["els"]:
                els[e[1]] = (e[0], e[2], e[3])
            start += CHUNK
        meta = {"scrollH": first["scrollH"], "scrollW": first["scrollW"], "vw": first["vw"],
                "total": first["total"], "captured": len(els)}
        return meta, els, c
    except BaseException:
        c.close()
        raise


def values_for(c, indices):
    out = {}
    idx = sorted(indices)
    for i in range(0, len(idx), 60):
        d = json.loads(c.eval(VALUES % json.dumps(idx[i:i + 60])))
        for k, v in d.items():
            out[int(k)] = v
    return out


def main():
    a, b = sys.argv[1], sys.argv[2]
    widths = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "390,760").split(",")]
    rc = 0
    for i, w in enumerate(widths):
        ma, ea, ca = snapshot(a, w, 9560 + i * 2)
        mb, eb, cb = snapshot(b, w, 9561 + i * 2)
        try:
            print("===== %dpx" % w)
            print("  A(fixed   %s) %d/%d elements  scrollH=%s scrollW=%s vw=%s"
                  % (a, ma["captured"], ma["total"], ma["scrollH"], ma["scrollW"], ma["vw"]))
            print("  B(baseline %s) %d/%d elements  scrollH=%s scrollW=%s vw=%s"
                  % (b, mb["captured"], mb["total"], mb["scrollH"], mb["scrollW"], mb["vw"]))
            onlya = sorted(set(ea) - set(eb))
            onlyb = sorted(set(eb) - set(ea))
            key_of = {ea[k][0]: k for k in ea}
            key_of.update({eb[k][0]: k for k in eb})
            if onlya:
                print("  elements only in A: %d e.g. %s" % (len(onlya), onlya[:3]))
            if onlyb:
                print("  elements only in B: %d e.g. %s" % (len(onlyb), onlyb[:3]))
            diffidx = [ea[k][0] for k in set(ea) & set(eb) if ea[k][2] != eb[k][2]]
            print("  elements whose computed style or rect differ: %d of %d"
                  % (len(diffidx), len(set(ea) & set(eb))))
            propdiff = {}
            va, vb = {}, {}
            if diffidx:
                va = values_for(ca, diffidx)
                vb = values_for(cb, diffidx)
                for i2 in diffidx:
                    A, B = va.get(i2), vb.get(i2)
                    if not A or not B:
                        continue
                    for p in PROPS:
                        if A[p] != B[p]:
                            propdiff.setdefault(p, []).append((A[p], B[p]))
                    if A["__rect"] != B["__rect"]:
                        propdiff.setdefault("__rect", []).append((B["__rect"], A["__rect"]))
            for p in sorted(propdiff, key=lambda x: -len(propdiff[x])):
                v = propdiff[p]
                print("    %-16s %5d  baseline=%r -> fixed=%r"
                      % (p, len(v), v[0][0][:38], v[0][1][:38]))
            # WHICH elements, so "rail-only" is a claim the reader can check rather than trust.
            wheres = {}
            for i2 in diffidx:
                A, B = va.get(i2), vb.get(i2)
                if not A or not B:
                    continue
                for p in PROPS:
                    if A[p] != B[p]:
                        wheres.setdefault(p, []).append(key_of.get(i2, i2))
            for p in sorted(wheres, key=lambda x: -len(wheres[x])):
                ks = wheres[p]
                print("      %-14s %s%s" % (p, ", ".join(sorted(set(ks))[:4]),
                                            "" if len(set(ks)) <= 4 else
                                            " (+%d more)" % (len(set(ks)) - 4)))
            offrail = sorted({key_of.get(i2, str(i2)) for i2 in diffidx
                              if not str(key_of.get(i2, "")).startswith(
                                  ("BUTTON.section-rail", "SPAN.section-rail",
                                   "NAV.section-rail", "DIV.section-rail"))})
            print("  elements that differ and are NOT inside .section-rail: %d %s"
                  % (len(offrail), offrail[:6]))
            if not onlya and not onlyb and not diffidx:
                print("  IDENTICAL: every element's computed style and rect match")
            else:
                rc = 1
        finally:
            ca.close()
            cb.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
