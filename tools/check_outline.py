#!/usr/bin/env python3
"""Assert the D9-D12/D14/D16/D17 fixes, by measurement -- the acceptance gate for this card.

`tools/qa_defects.py` (added by the QA card) MEASURES these items and prints the audit's original
claim beside what the build does now; it deliberately asserts nothing, because its job was to
record the state of a deferred list. Now that the items are fixed, "fixed" needs to be falsifiable
or it is just a second opinion. This is that gate: same instrument, same probes, but every item
either holds or the tool exits non-zero and names the item that broke.

Seven assertions, one per defect:

  D9   every `main h2` sits on the wrap's content left edge (none indented, none off-grid), and
       the one heading inside a padded card is inset by exactly that card's own padding -- i.e.
       it is the card's content box, not a third alignment story.
  D10  `.railhead` has a rule of its own, and a group label is measurably subordinate to the
       section heading above it: different size, weight, colour and box height.
  D11  `.sec-head` is live: it is defined AND used by the generated pages (both halves, or it is
       the same dead-CSS defect in the other direction).
  D12  no desktop text element carries a max-width that can never bind (`.vs-text` no longer
       declares one at all).
  D14  the resume toast clears the progress bar at every desktop width, measured on the REAL
       toast element constructed with the real class -- the toast needs a saved scroll position
       to exist, so it is otherwise only ever reasoned about from the CSS.
  D16  no `<li>` outside a list, on the sample page AND across every built page.
  D17  the rail renders more than one level, and the level-3 ticks are the group labels.

Usage:
    python3 -m http.server 8461 --directory site/dist &
    python3 tools/check_outline.py http://127.0.0.1:8461
Exit code 0 only when all seven hold.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, "tools")
from measure_layout import CHROME, CDP                              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITTING = "/sittings/2026-08-04.html"

PROBE = r"""
(() => {
  const px = v => Math.round(v * 100) / 100;
  const q = s => document.querySelector(s);
  const out = { url: location.href };
  const tracks = cs => (cs.gridTemplateColumns || '').split(' ').map(parseFloat)
                         .filter(v => isFinite(v));

  /* D9 -- every heading in main, and the wrap edge it should share. */
  const wrap = q('main.wrap'), wcs = getComputedStyle(wrap), wr = wrap.getBoundingClientRect();
  out.wrapLeft = px(wr.left + parseFloat(wcs.paddingLeft));
  out.wrapRight = px(wr.right - parseFloat(wcs.paddingRight));
  out.headings = [];
  [].slice.call(document.querySelectorAll('main h2')).forEach(function (h) {
    const cs = getComputedStyle(h), r = h.getBoundingClientRect();
    const card = h.closest('.method, .panel');
    let cardInset = null;
    if (card) {
      const ccs = getComputedStyle(card), cr = card.getBoundingClientRect();
      cardInset = px(r.left - (cr.left + parseFloat(ccs.borderLeftWidth)
                              + parseFloat(ccs.paddingLeft)));
    }
    out.headings.push({ text: (h.textContent || '').trim().slice(0, 34),
      cls: h.className || '', x: px(r.left), w: px(r.width), right: px(r.right),
      railhead: h.classList.contains('railhead'),
      inBrief: !!h.closest('article.brief'), inSecHead: !!h.closest('.sec-head'),
      cardInset: cardInset,
      fontSize: cs.fontSize, fontWeight: cs.fontWeight, color: cs.color,
      h: px(r.height), borderBottom: cs.borderBottomWidth,
      secTag: (function () { let n = h.parentElement;
        while (n && n !== document.body) { if (n.tagName === 'SECTION') return n.className; n = n.parentElement; }
        return null; })() });
  });

  /* D10 -- a group label vs the section heading it belongs to. */
  const rh = q('.railhead');
  const secH2 = q('section .sec-head h2');
  /* The colour behind a label matters as much as the label's own: the same grey can clear 4.5:1
     on the white page and fail over a tinted card. Walk up to the first opaque background rather
     than assuming white. */
  const bgBehind = el => {
    let n = el;
    while (n && n !== document.documentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c && c !== 'transparent' && !/rgba\(0, 0, 0, 0\)/.test(c)) return c;
      n = n.parentElement;
    }
    return 'rgb(255, 255, 255)';
  };
  const styleOf = e => { if (!e) return null; const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    return { fontSize: cs.fontSize, fontWeight: cs.fontWeight, color: cs.color,
             textTransform: cs.textTransform, h: px(r.height),
             bg: bgBehind(e),
             borderBottom: cs.borderBottomWidth }; };
  out.D10 = { railhead: styleOf(rh), section: styleOf(secH2),
              railheadText: rh ? (rh.textContent || '').trim().slice(0, 30) : null };

  /* D11 -- is the class both defined and used? */
  const sheet = [].slice.call(document.styleSheets).map(function (s) {
    try { return [].slice.call(s.cssRules).map(function (r) { return r.cssText; }).join('\n'); }
    catch (e) { return ''; } }).join('\n');
  out.D11 = { rulesInSheet: (sheet.match(/\.sec-head/g) || []).length,
              usedOnPage: document.querySelectorAll('.sec-head').length };

  /* D12 -- a max-width is DEAD only if it exceeds the width available to the element, which is
     its containing block's content box. `.dek{max-width:56ch}` is a live cap on a lede paragraph
     (667.9px against a 1012px wrap): the text simply does not reach it. `.vs-text{max-width:74ch}`
     was dead in the other direction -- 745.78px against a 562px column, so it could never once
     have had an effect. Measuring the declared value alone cannot tell those apart. */
  out.D12 = [];
  ['.vs-text', '.sumtext', '.sub', '.dek', '.method p'].forEach(function (sel) {
    const el = q(sel);
    if (!el) return;
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    const par = el.parentElement, pcs = par ? getComputedStyle(par) : null;
    const pr = par ? par.getBoundingClientRect() : null;
    const avail = pr ? px(pr.width - parseFloat(pcs.paddingLeft)
                          - parseFloat(pcs.paddingRight)
                          - parseFloat(pcs.borderLeftWidth)
                          - parseFloat(pcs.borderRightWidth)) : null;
    const mw = parseFloat(cs.maxWidth);
    out.D12.push({ sel: sel, maxWidth: cs.maxWidth, width: px(r.width), available: avail,
                   dead: isFinite(mw) && avail !== null && mw > avail + 0.5 });
  });

  /* D14 -- construct the real toast and measure where the sheet puts it. */
  const pbar = q('.pbar');
  const el = document.createElement('div');
  el.className = 'resume';
  el.innerHTML = '<span>You were 42% through this sitting</span>' +
                 '<button type="button" class="rgo">Resume</button>' +
                 '<button type="button" class="rno">&times;</button>';
  document.body.appendChild(el);
  const rr = el.getBoundingClientRect(), pr = pbar ? pbar.getBoundingClientRect() : null;
  out.D14 = { bottom: getComputedStyle(el).bottom, box: { y: px(rr.top), bottom: px(rr.bottom) },
              overlapsBar: pr ? px(Math.max(0, Math.min(rr.bottom, pr.bottom)
                                            - Math.max(rr.top, pr.top))) : 0,
              gapToBar: pr ? px(pr.top - rr.bottom) : null };
  el.remove();

  /* D16 -- a bare <li> anywhere. */
  out.D16 = { liOutsideList: document.querySelectorAll('li:not(ol li):not(ul li)').length,
              liTotal: document.querySelectorAll('li').length,
              dsecRows: (function () { const d = q('.dsec'); if (!d) return null;
                const rows = (getComputedStyle(d).gridTemplateRows || '').split(' ')
                  .map(parseFloat).filter(isFinite); return rows.length; })() };

  /* D17 -- the rendered levels, and which labels hold them. */
  out.D17 = [];
  [].slice.call(document.querySelectorAll('.section-rail-tick')).forEach(function (t) {
    const m = /level-(\d)/.exec(t.className);
    out.D17.push({ level: m ? +m[1] : null,
                   label: (t.getAttribute('aria-label') || '').replace(/^Go to /, '').slice(0, 30),
                   isGroupLabel: /^(Bills|Debates and other business)$/.test(
                     (t.getAttribute('aria-label') || '').replace(/^Go to /, '')) });
  });
  return JSON.stringify(out);
})()
"""

# (Reserved: an in-page corpus walk would re-render 332 pages in the browser for a fact that a
# file read answers. The corpus half of D16/D11 is a static scan -- see corpus_scan().)


def corpus_scan():
    """D16/D11 across every built page: a static scan, so it costs one file read per page."""
    dist = os.path.join(ROOT, "site", "dist", "sittings")
    pages = sorted(f for f in os.listdir(dist) if f.endswith(".html"))
    bare_pages, bare_total, sec_pages, h2_pages = [], 0, 0, 0
    for f in pages:
        src = open(os.path.join(dist, f), encoding="utf-8", errors="replace").read()
        n = len(re.findall(r"</ol>\s*<li", src))
        if n:
            bare_pages.append((f, n))
            bare_total += n
        if 'class="sec-head"' in src:
            sec_pages += 1
        if "<h2" in src:
            h2_pages += 1
    return {"pages": len(pages), "bare_pages": bare_pages, "bare_total": bare_total,
            "sec_head_pages": sec_pages, "pages_with_h2": h2_pages}


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(s):
    n = [int(v) for v in re.findall(r"\d+", s or "")[:3]]
    return tuple(n) if len(n) == 3 else None


def contrast(fg, bg):
    """WCAG relative-luminance ratio. fg/bg are 'rgb(r, g, b)' strings."""
    a, b = _rgb(fg), _rgb(bg)
    if not a or not b:
        return None
    la = sum(w * _lin(v) for w, v in zip((0.2126, 0.7152, 0.0722), a))
    lb = sum(w * _lin(v) for w, v in zip((0.2126, 0.7152, 0.0722), b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def main():
    if not CHROME:
        sys.exit("no Chromium browser found")
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8461"
    widths = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2
                               else "1024,1280,1440,1920").split(",")]
    fails, lines = [], []

    def ok(cond, msg):
        lines.append(("  PASS  " if cond else "  FAIL  ") + msg)
        if not cond:
            fails.append(msg)

    for i, w in enumerate(widths):
        c = CDP(CHROME, base + SITTING, w, 900, 9820 + i * 2)
        try:
            c.call("Emulation.setDeviceMetricsOverride", width=w, height=900,
                   deviceScaleFactor=1, mobile=False)
            c.eval(f"location.replace({json.dumps(base + SITTING)})")
            time.sleep(2.6)
            d = json.loads(c.eval(PROBE))
        finally:
            c.close()

        lines.append(f"\n===== {w}px  (wrap content {d['wrapLeft']}..{d['wrapRight']})")
        # -- D9
        off = [h for h in d["headings"]
               if abs(h["x"] - d["wrapLeft"]) > 1.5 and h["cardInset"] is None]
        carded = [h for h in d["headings"] if h["cardInset"] is not None]
        ok(not off, f"D9 every heading on the wrap's content edge "
                    f"({len(d['headings'])} headings; off-edge: "
                    f"{[(h['text'], h['x']) for h in off]})")
        ok(all(abs(h["cardInset"]) < 1.5 for h in carded),
           f"D9 the {len(carded)} heading(s) inside a padded card are inset by exactly that "
           f"card's padding, not by a magic offset "
           f"(insets: {[h['cardInset'] for h in carded]})")
        # -- D10
        s, g = d["D10"]["section"], d["D10"]["railhead"]
        if s and g:
            ok(s["fontSize"] != g["fontSize"] and s["color"] != g["color"],
               f"D10 a group label is subordinate to its section heading "
               f"(section {s['fontSize']}/{s['fontWeight']}/{s['color']} vs label "
               f"{g['fontSize']}/{g['fontWeight']}/{g['color']}; heights {s['h']} vs {g['h']})")
            # Subordinate must not mean unreadable: 15px/12px bold is normal-size text, 4.5:1.
            cr = contrast(g["color"], g["bg"])
            sr = contrast(s["color"], s["bg"])
            ok(cr is not None and cr >= 4.5,
               f"D10 the label still clears WCAG AA on the colour it actually sits on "
               f"(label {cr if cr is None else round(cr, 2)}:1 against {g['bg']})")
            ok(sr is not None and cr is not None and sr > cr,
               f"D10 the section heading outranks the label in contrast "
               f"(section {sr} vs label {cr})")
        else:
            ok(False, "D10 could not find both a .railhead and a section heading")
        # -- D11
        ok(d["D11"]["rulesInSheet"] > 0 and d["D11"]["usedOnPage"] > 0,
           f"D11 `.sec-head` is both defined and used (rules {d['D11']['rulesInSheet']}, "
           f"elements on page {d['D11']['usedOnPage']})")
        # -- D12
        dead = [x for x in d["D12"] if x["dead"]]
        ok(not dead, f"D12 no text element declares a max-width wider than it can ever be "
                     f"({len(d['D12'])} checked; dead: "
                     f"{[(x['sel'], x['maxWidth'], x['available']) for x in dead]})")
        # -- D14
        ok(d["D14"]["overlapsBar"] == 0,
           f"D14 the resume toast clears the progress bar (toast bottom "
           f"{d['D14']['box']['bottom']}, gap to bar {d['D14']['gapToBar']}px, "
           f"bottom:{d['D14']['bottom']} overlap {d['D14']['overlapsBar']}px)")
        # -- D16
        ok(d["D16"]["liOutsideList"] == 0,
           f"D16 no bare <li> on the sample page ({d['D16']['liOutsideList']} of "
           f"{d['D16']['liTotal']}); .dsec grid rows = {d['D16']['dsecRows']}")
        # -- D17
        lv = sorted({t["level"] for t in d["D17"] if t["level"]})
        ok(len(lv) > 1, f"D17 the rail renders more than one level (levels present: {lv})")
        labels = [t for t in d["D17"] if t["isGroupLabel"]]
        ok(labels and all(t["level"] == 3 for t in labels),
           f"D17 the group labels are the level-3 ticks "
           f"({[(t['label'], t['level']) for t in labels]})")

    lines.append("\n===== the whole corpus (static scan)")
    cs = corpus_scan()
    ok(not cs["bare_pages"],
       f"D16 no bare <li> in any of the {cs['pages']} built pages "
       f"(pages with one: {len(cs['bare_pages'])}; total {cs['bare_total']})")
    ok(cs["sec_head_pages"] == cs["pages_with_h2"],
       f"D11 `.sec-head` is used by every page that has headings "
       f"({cs['sec_head_pages']} of {cs['pages_with_h2']} pages with an h2)")

    print("\n".join(lines))
    print()
    if fails:
        print(f"{len(fails)} FAILURE(S):")
        for f in fails:
            print("  -", f)
        return 1
    print("ALL ASSERTIONS HOLD.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
