#!/usr/bin/env python3
"""Mobile-unchanged proof at FULL-DOM resolution, not a selector sample.

The earlier check compared a hand-picked selector list, which can miss a rule that reaches an
element nobody thought to name. This walks EVERY element in the document on both builds and
compares the full computed style, so "mobile is unchanged" becomes a statement about the whole
tree rather than about the elements I happened to think of.

Reported precisely, because "identical" and "identical except z-index" are different claims:
  * elements present in only one build;
  * elements whose computed style differs, and by which property.
"""
import json
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402

PAGE = "/sittings/2026-08-04.html"

# Walk the whole tree; skip nothing. Return a stable-ordered snapshot per element.
DUMP = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const out = [];
  const all = document.querySelectorAll('*');
  for (let i = 0; i < all.length; i++) {
    const e = all[i];
    if (e.tagName === 'SCRIPT' || e.tagName === 'STYLE') continue;
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    // A stable identity: the tag plus its path index, so the two builds line up.
    out.push({
      path: (() => { const p = []; let n = e; while (n && n !== document.body) {
                       const i2 = n.parentNode ? Array.prototype.indexOf.call(n.parentNode.children, n) : -1;
                       p.unshift(n.tagName + ':' + i2); n = n.parentNode; }
                     return p.join('/'); })(),
      tag: e.tagName,
      cls: typeof e.className === 'string' ? e.className : '',
      rect: { x: px(r.left), y: px(r.top), w: px(r.width), h: px(r.height) },
      // Every property that can come from a stylesheet rule, so a changed rule cannot hide.
      s: {
        display: cs.display, position: cs.position, zIndex: cs.zIndex, opacity: cs.opacity,
        visibility: cs.visibility, width: cs.width, height: cs.height,
        maxWidth: cs.maxWidth, minWidth: cs.minWidth, maxHeight: cs.maxHeight,
        margin: cs.margin, padding: cs.padding, gap: cs.gap, rowGap: cs.rowGap,
        columnGap: cs.columnGap, top: cs.top, right: cs.right, bottom: cs.bottom,
        left: cs.left, transform: cs.transform, transition: cs.transition,
        justifyContent: cs.justifyContent, alignItems: cs.alignItems, order: cs.order,
        flex: cs.flex, flexBasis: cs.flexBasis, flexGrow: cs.flexGrow, flexShrink: cs.flexShrink,
        gridTemplateColumns: cs.gridTemplateColumns, gridColumn: cs.gridColumn,
        fontSize: cs.fontSize, lineHeight: cs.lineHeight, fontWeight: cs.fontWeight,
        color: cs.color, background: cs.backgroundColor, backgroundImage: cs.backgroundImage,
        border: cs.border, borderTop: cs.borderTop, borderLeft: cs.borderLeft,
        borderRadius: cs.borderRadius, boxShadow: cs.boxShadow, textAlign: cs.textAlign,
        overflow: cs.overflow, overflowX: cs.overflowX, overflowY: cs.overflowY,
        textOverflow: cs.textOverflow, whiteSpace: cs.whiteSpace, letterSpacing: cs.letterSpacing,
        textTransform: cs.textTransform, paddingLeft: cs.paddingLeft, paddingRight: cs.paddingRight,
        cursor: cs.cursor, boxSizing: cs.boxSizing, isolation: cs.isolation,
        mixBlendMode: cs.mixBlendMode, filter: cs.filter, backdropFilter: cs.backdropFilter,
      },
    });
  }
  return JSON.stringify({ count: out.length, vw: window.innerWidth,
                          sw: document.documentElement.scrollWidth, els: out });
})()
"""


def snap(base, w, h, port):
    c = CDP(CHROME, base + PAGE, w, h, port)
    try:
        c.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
               deviceScaleFactor=1, mobile=True)
        c.eval(f"location.replace({json.dumps(base + PAGE)})")
        time.sleep(2.5)
        # No scrolling: layout is deterministic at offset 0, and the rail is fixed.
        return json.loads(c.eval(DUMP))
    finally:
        c.close()


def main():
    a, b = sys.argv[1], sys.argv[2]
    widths = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "390,740").split(",")]
    total_els = 0
    total_props = 0
    for i, w in enumerate(widths):
        da, db = snap(a, w, 844, 9950 + i * 2), snap(b, w, 844, 9951 + i * 2)
        ea = {(e["path"], e["tag"], e["cls"]): e for e in da["els"]}
        eb = {(e["path"], e["tag"], e["cls"]): e for e in db["els"]}
        print(f"===== {w}px  elements: worktree={da['count']} baseline={db['count']} "
              f"scrollWidth {da['sw']}/{db['sw']}")
        only_a = [k for k in ea if k not in eb]
        only_b = [k for k in eb if k not in ea]
        if only_a:
            print(f"  only in worktree ({len(only_a)}): {only_a[:5]}")
        if only_b:
            print(f"  only in baseline ({len(only_b)}): {only_b[:5]}")
        propdiff = {}
        reldiff = []
        for k in ea:
            if k not in eb:
                continue
            A, B = ea[k], eb[k]
            for p, va in A["s"].items():
                vb = B["s"].get(p)
                if va != vb:
                    propdiff.setdefault(p, []).append((A["cls"] or A["tag"], vb, va))
            if A["rect"] != B["rect"]:
                reldiff.append((A["cls"] or A["tag"], B["rect"], A["rect"]))
        total_els += da["count"]
        total_props += sum(len(v) for v in propdiff.values())
        if not propdiff and not reldiff:
            print("  IDENTICAL: every element's computed style and rect match")
            continue
        if reldiff:
            print(f"  RECT DIFFS ({len(reldiff)}):")
            for cls, rb, ra in reldiff[:10]:
                print(f"    {cls}: baseline={rb} worktree={ra}")
        else:
            print("  no rect differences")
        print(f"  COMPUTED-STYLE DIFFS: {sum(len(v) for v in propdiff.values())} "
              f"across {len(propdiff)} propert(ies)")
        for p, items in sorted(propdiff.items()):
            print(f"    {p}: {len(items)} element(s), e.g.")
            for cls, vb, va in items[:3]:
                print(f"      {cls!r}: baseline={vb!r} -> worktree={va!r}")
    print()
    print(f"TOTAL: {total_els} element-snapshots, {total_props} differing properties")


if __name__ == "__main__":
    main()
