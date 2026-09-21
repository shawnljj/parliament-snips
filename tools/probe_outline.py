#!/usr/bin/env python3
"""Probe the heading outline and the bottom-chrome coupling: D9, D10, D11, D12, D14, D16, D17.

Standalone measuring instrument for kanban t_a15acd97. Drives headless Chrome over CDP with
the minimal client already in this repo (tools/measure_layout.py), so no third-party package.

It answers, per width, the questions the card asks -- and it MEASURES the two places where the
audit's own phrasing needs checking rather than accepting:

  * D12: is `.vs-text{max-width:74ch}` ever the binding constraint? A width sweep, printing
    the element's width against its max-width at every step, so "dead everywhere" and "a live
    tablet guard" are distinguishable. (The audit says "live between roughly 740 and 1010px".)
  * D14: the toast only exists when a reader has a saved scroll position, so the audit and QA
    could only reason about the CSS. This CONSTRUCTS the real element with the real class and
    measures where the stylesheet actually puts it -- which is the only honest way to test a
    latent defect.

Usage:
    python3 tools/probe_outline.py http://127.0.0.1:8460 1024,1280,1440,1920
    python3 tools/probe_outline.py <base> --sweep            # the D12 width sweep
"""
import json
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402

PAGE = "/sittings/2026-08-04.html"

PROBE = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const q = s => document.querySelector(s);
  const rect = e => { const r = e.getBoundingClientRect();
    return { x: px(r.left), y: px(r.top), right: px(r.right), bottom: px(r.bottom),
             w: px(r.width), h: px(r.height) }; };
  const out = { url: location.href, vw: window.innerWidth };
  const tracks = cs => (cs.gridTemplateColumns || '').split(' ').map(parseFloat)
                         .filter(v => isFinite(v));

  /* ---- the two columns, and the wrap's own content box (the "full-bleed" reference) ---- */
  const dsec = q('.dsec'), wrap = q('main.wrap');
  const cols = dsec ? tracks(getComputedStyle(dsec)) : [];
  const wcs = wrap ? getComputedStyle(wrap) : null;
  const wr = wrap ? wrap.getBoundingClientRect() : null;
  out.columns = { tracks: cols,
    textLeft: dsec ? px(dsec.getBoundingClientRect().left) : null,
    textWidth: cols[0] || null,
    wrapContentLeft: wr ? px(wr.left + parseFloat(wcs.paddingLeft)) : null,
    wrapContentRight: wr ? px(wr.right - parseFloat(wcs.paddingRight)) : null };

  /* ---- D9 / D10 / D17: every heading in main, with its ancestry and its edge ---- */
  out.headings = [];
  [].slice.call(document.querySelectorAll('main h2')).forEach(function (h) {
    let sec = null, n = h.parentElement;
    while (n && n !== document.body) { if (n.tagName === 'SECTION') { sec = n; break; }
                                       n = n.parentElement; }
    const cs = getComputedStyle(h), r = h.getBoundingClientRect();
    out.headings.push({
      text: (h.textContent || '').trim().slice(0, 42),
      cls: h.className || h.tagName,
      railhead: h.classList.contains('railhead'),
      inBrief: !!h.closest('article.brief'),
      inMethod: !!h.closest('.method'),
      secCls: sec ? sec.className : null,
      secParent: sec && sec.parentElement ? sec.parentElement.tagName : null,
      x: px(r.left), w: px(r.width), right: px(r.right),
      fontSize: cs.fontSize, fontWeight: cs.fontWeight, color: cs.color,
      lineHeight: cs.lineHeight, marginTop: cs.marginTop, borderTop: cs.borderTopWidth });
  });

  /* ---- D9's outlier: the panel heading, against the panel's own content box ---- */
  const m = q('.method');
  if (m) {
    const cs = getComputedStyle(m), r = m.getBoundingClientRect();
    const mh = m.querySelector('h2');
    out.method = { x: px(r.left), borderLeft: cs.borderLeftWidth, paddingLeft: cs.paddingLeft,
      contentLeft: px(r.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft)),
      h2x: mh ? px(mh.getBoundingClientRect().left) : null,
      h2w: mh ? px(mh.getBoundingClientRect().width) : null };
  }

  /* ---- D12 and the same question for the group heading ---- */
  const vst = q('.vs-text');
  if (vst) {
    const cs = getComputedStyle(vst), w = px(vst.getBoundingClientRect().width);
    const mw = parseFloat(cs.maxWidth);
    out.vsText = { maxWidth: cs.maxWidth, width: w, binds: isFinite(mw) && w >= mw - 0.5,
                   fontSize: cs.fontSize };
  }

  /* ---- D16: list validity, by DOM, not by grepping for "</ol><li" ---- */
  out.lists = { liOutsideOl: document.querySelectorAll('li:not(ol li)').length,
                liInsideOl: document.querySelectorAll('ol li').length,
                bareLiAfterOl: (document.documentElement.outerHTML.match(/<\/ol>\s*<li/g) || []).length };
  const tail = q('.dsec > .gapi, .dsec > .gapd, .dsec > .gapbody');
  if (tail) {
    const cs = getComputedStyle(tail);
    out.tail = { tag: tail.tagName, cls: tail.className, gridColumn: cs.gridColumn,
                 gridRow: cs.gridRow, ...rect(tail) };
    // Where the marker sits relative to its own list: the reader's question ("is the gap
    // marker under the sentences it belongs to, or drifting below a tall summary card?").
    const ol = tail.closest('.dsec').querySelector('ol.vslist');
    if (ol) { const o = ol.getBoundingClientRect();
              out.tail.listBottom = px(o.bottom);
              out.tail.gapBelowList = px(rect(tail).y - o.bottom); }
  }

  /* ---- D14: build the REAL toast and measure it. It has no other way to exist here. ---- */
  const pbar = q('.pbar');
  let el = document.createElement('div');
  el.className = 'resume';
  el.innerHTML = '<span>You were 42% through this sitting</span>' +
                 '<button type="button" class="rgo">Resume</button>' +
                 '<button type="button" class="rno">&times;</button>';
  document.body.appendChild(el);
  const rcs = getComputedStyle(el);
  out.resume = { bottom: rcs.bottom, left: rcs.left, right: rcs.right, maxWidth: rcs.maxWidth,
                 box: rect(el), pbar: pbar ? rect(pbar) : null,
                 pbarDisplay: pbar ? getComputedStyle(pbar).display : null,
                 overlapsBar: (pbar && getComputedStyle(pbar).display !== 'none')
                   ? px(Math.max(0, Math.min(rect(el).bottom, rect(pbar).bottom)
                                 - Math.max(rect(el).y, rect(pbar).y))) : 0 };
  el.remove();

  /* ---- D13, measured alongside because it shares the tokens ---- */
  const tt = q('.totop'), tb = tt ? rect(tt) : null;
  out.totop = { box: tb, pbar: pbar ? rect(pbar) : null,
    overlap: (tb && pbar) ? px(Math.max(0, Math.min(tb.bottom, rect(pbar).bottom)
                                        - Math.max(tb.y, rect(pbar).y))) : 0 };

  /* ---- D17: the rail's levels, as rendered ---- */
  out.rail = [];
  [].slice.call(document.querySelectorAll('.section-rail-tick')).forEach(function (t) {
    const mk = t.querySelector('.section-rail-tick-mark');
    const nm = t.querySelector('.section-rail-name');
    out.rail.push({ cls: t.className, label: (t.getAttribute('aria-label') || '')
                     .replace(/^Go to /, '').slice(0, 34),
                    h: px(t.getBoundingClientRect().height),
                    dotW: mk ? px(mk.getBoundingClientRect().width) : null,
                    dotX: mk ? px(mk.getBoundingClientRect().left) : null,
                    nameColor: nm ? getComputedStyle(nm).color : null,
                    nameDisplay: nm ? getComputedStyle(nm).display : null });
  });
  out.railLevels = out.rail.reduce(function (a, t) {
    const m = /level-(\d)/.exec(t.cls); const k = m ? 'level-' + m[1] : 'none';
    a[k] = (a[k] || 0) + 1; return a; }, {});

  /* ---- the CSS facts the static half of the card asserts ---- */
  const sheet = [].slice.call(document.styleSheets)
    .map(function (s) { try { return [].slice.call(s.cssRules).map(function (r) { return r.cssText; })
                                    .join('\n'); } catch (e) { return ''; } }).join('\n');
  out.css = { railheadRules: (sheet.match(/\.railhead\s*\{|\.railhead[^,{]*\{/g) || []).length,
              secHeadRules: (sheet.match(/\.sec-head/g) || []).length,
              vsTextMaxCh: /\.vs-text\{[^}]*ch/.test(sheet) };
  return JSON.stringify(out);
})()
"""

SWEEP = r"""
(() => {
  const vst = document.querySelector('.vs-text');
  if (!vst) return JSON.stringify({ none: true });
  const cs = getComputedStyle(vst), w = Math.round(vst.getBoundingClientRect().width * 100) / 100;
  const mw = parseFloat(cs.maxWidth);
  return JSON.stringify({ vw: window.innerWidth, maxWidth: cs.maxWidth, width: w,
    binds: isFinite(mw) && w >= mw - 0.5, fontSize: cs.fontSize,
    ds: getComputedStyle(document.querySelector('.dsec')).display });
})()
"""


def run(base, widths, sweep=False, port0=9700):
    for i, w in enumerate(widths):
        c = CDP(CHROME, base + PAGE, w, 900, port0 + i)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(base + PAGE)})")
            time.sleep(2.6)
            c.eval("window.scrollTo(0, Math.round((document.documentElement.scrollHeight"
                   " - window.innerHeight) * 0.35))")
            time.sleep(1.0)
            raw = c.eval(SWEEP if sweep else PROBE)
        finally:
            c.close()
        d = json.loads(raw)
        if sweep:
            print(f"  {w:>5}px  ds={d.get('ds'):<6} fs={d.get('fontSize'):<6} "
                  f"maxWidth={d.get('maxWidth'):<10} width={d.get('width'):<8} "
                  f"BINDS={d.get('binds')}")
            continue
        print(f"\n===== {w}px  vw={d['vw']}  tracks={d['columns']['tracks']}")
        col = d["columns"]
        print(f"  columns: textLeft={col['textLeft']} textWidth={col['textWidth']} "
              f"wrapContent={col['wrapContentLeft']}..{col['wrapContentRight']}")
        print(f"  headings ({len(d['headings'])}):")
        for h in d["headings"]:
            full = (abs(h["right"] - col["wrapContentRight"]) < 1.5)
            left_ok = (abs(h["x"] - col["wrapContentLeft"]) < 1.5)
            print(f"    x={h['x']:<7} w={h['w']:<7} right={h['right']:<7} "
                  f"full_bleed={full!s:<5} left_on_wrap={left_ok!s:<5} "
                  f"{h['fontSize']:>6}/{h['fontWeight']:<4} {h['cls'][:34]!r} {h['text'][:30]!r}")
        if d.get("method"):
            m = d["method"]
            print(f"  .method x={m['x']} borderLeft={m['borderLeft']} padLeft={m['paddingLeft']} "
                  f"contentLeft={m['contentLeft']} h2x={m['h2x']} "
                  f"h2_inset={None if m['h2x'] is None else round(m['h2x'] - m['contentLeft'], 2)}")
        if d.get("vsText"):
            v = d["vsText"]
            print(f"  D12 .vs-text maxWidth={v['maxWidth']} width={v['width']} BINDS={v['binds']}")
        print(f"  D16 liOutsideOl={d['lists']['liOutsideOl']} liInsideOl={d['lists']['liInsideOl']} "
              f"bareLiAfterOl={d['lists']['bareLiAfterOl']}")
        if d.get("tail"):
            t = d["tail"]
            print(f"      tail {t['tag']}.{t['cls']} gridColumn={t['gridColumn']} "
                  f"gridRow={t['gridRow']} y={t['y']} listBottom={t.get('listBottom')} "
                  f"gapBelowList={t.get('gapBelowList')}")
        r = d["resume"]
        print(f"  D14 .resume(constructed) bottom={r['bottom']} box={r['box']}")
        print(f"      .pbar display={r['pbarDisplay']} box={r['pbar']} "
              f"toast/bar overlap={r['overlapsBar']}px")
        print(f"  D13 .totop overlap with .pbar = {d['totop']['overlap']}px")
        print(f"  D17 rail levels={d['railLevels']}")
        for t in d["rail"]:
            print(f"      {t['label'][:30]:<32} h={t['h']:<5} dotW={t['dotW']:<5} "
                  f"dotX={t['dotX']:<7} nameDisplay={t['nameDisplay']:<6} {t['nameColor']}")
        print(f"  CSS: .railhead rules={d['css']['railheadRules']} "
              f".sec-head rules={d['css']['secHeadRules']} "
              f"vs-text ch-guard={d['css']['vsTextMaxCh']}")


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8460"
    if len(sys.argv) > 2 and sys.argv[2] == "--sweep":
        widths = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3
                                   else "700,740,759,760,761,800,900,1010,1100,1160,1200,1280,1440,1920"
                                   ).split(",")]
        print(f"D12 sweep against {base}{PAGE}")
        run(base, widths, sweep=True)
        return 0
    widths = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2
                               else "1024,1280,1440,1920").split(",")]
    run(base, widths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
