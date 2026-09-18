#!/usr/bin/env python3
"""SPIKE (throwaway): the 3-panel funnel — transcript -> selection -> summary.

Desktop-only by explicit instruction. This is the one exception to the mobile-first
rule, and it applies ONLY to this spike artifact: every shipped surface stays
mobile-first.

   panel 1  the full transcript, with unselected sentences faded to light grey
   panel 2  the selected sentences, verbatim, shaded by importance
   panel 3  a model summary per GROUP of selected sentences

The honest position on panel 3: it is model-written text, which is exactly the thing
selection was introduced to eliminate. It is acceptable HERE only because it is
adjacent to the verbatim sentences it claims to summarise, so a reader can check it.
It is a convenience layer, not the evidence. Panels 1 and 2 carry no model words at all.

The funnel is the argument: many sentences -> fewer selected -> fewest summaries, with
connectors showing which transcript lines feed which selected sentence, and a brace
showing which selected sentences each summary covers.
"""
import html as H
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "summariser"))
import build_briefs as B  # noqa: E402

HERE = ROOT
OUT = os.path.join(HERE, "site", "dist", "spike-funnel.html")

SELECT_SYSTEM = """You are selecting the sentences that matter from a record of a \
Singapore Parliament sitting.

Return JSON only:

{"keep": ["s00002", "s00003"], "what_it_is": "one sentence naming the kind of business"}

Rules:
- "keep" holds sentence ids ONLY, copied exactly. Never write, quote or reword a
  sentence -- anything other than ids is discarded.
- Choose sentences a reader MUST see: what was decided, committed, answered or
  quantified. Figures, dates, amounts and named schemes are the most valuable.
- SKIP procedural text: thanks, welcomes, greetings, "I beg to move", points of order,
  housekeeping such as hotline numbers, URLs and form links, and sentences that only
  announce what comes next.
- Keep 3 to 12 sentences for a substantial record, 1 to 4 for a short one."""

SUMMARY_SYSTEM = """You summarise a set of consecutive sentences from a Singapore \
Parliament record.

Write ONE sentence, under 30 words, stating what this set establishes. Use ONLY what
these sentences state. Do not add a fact, name, number or qualification they do not
contain. Do not evaluate or editorialise.

Return JSON only: {"summary": "..."}"""

SELECT_TMPL = """Sentences from the record, each prefixed with its id:

{excerpt}

Return the JSON described in your instructions."""

SUMMARY_TMPL = """These consecutive sentences from the record:

{block}

Return the JSON described in your instructions."""

DANGLING = re.compile(
    r"^\s*(This|That|These|Those|It|They|He|She|We|I|There|Such|"
    r"However|Therefore|Thus|Hence|So|And|But|Also|Then|Now|Certainly)\b", re.I)


def words(s):
    return len(re.findall(r"[A-Za-z0-9']+", s or ""))


def esc(s):
    return H.escape(str(s or ""))


def load_item(item_id, year=2026):
    with open(os.path.join(ROOT, "pipeline", "dataset", str(year),
                           f"{item_id}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def select(item, model):
    sents = item["sentences"]
    excerpt = "\n".join(f"{s['sid']}: {s['text']}" for s in sents)
    raw, usage = B.ask(SELECT_TMPL.format(excerpt=excerpt), SELECT_SYSTEM, model)
    got = B.parse_json(raw) or {}
    by_sid = {s["sid"]: s for s in sents}
    return ([str(x) for x in (got.get("keep") or []) if str(x) in by_sid],
            got.get("what_it_is") or "", usage)


def groups_of(sents, keep, gap=2):
    """Consecutive selected sentences form a group; a gap of up to `gap` unselected
    sentences does not split it, because a set of sentences that belong together is
    usually interrupted by a sentence that merely connects them."""
    idx = {s["sid"]: i for i, s in enumerate(sents)}
    order = sorted(keep, key=lambda sid: idx[sid])
    out = []
    for sid in order:
        i = idx[sid]
        if out and i - out[-1][-1] <= gap:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def summarise(sents, model):
    block = "\n".join(f"{sents[i]['sid']}: {sents[i]['text']}" for i in range(len(sents)))
    raw, usage = B.ask(SUMMARY_TMPL.format(block=block), SUMMARY_SYSTEM, model)
    got = B.parse_json(raw) or {}
    return got.get("summary") or "", usage


def build(item_id):
    item = load_item(item_id)
    sents = item["sentences"]
    model = B.DEFAULT_MODEL

    keep, what_it_is, u1 = select(item, model)
    keep_set = set(keep)

    # deterministic dangling repair: take the predecessor of a dangling selection
    repaired = set()
    idx = {s["sid"]: i for i, s in enumerate(sents)}
    for sid in keep:
        if DANGLING.match(sents[idx[sid]]["text"]):
            j = idx[sid]
            if j > 0 and sents[j - 1]["sid"] not in keep_set:
                keep_set.add(sents[j - 1]["sid"])
                repaired.add(sents[j - 1]["sid"])

    gs = groups_of(sents, keep_set)
    summaries = []
    for g in gs:
        block = [sents[i] for i in g]
        text, u = summarise(block, model)
        summaries.append({"sids": [s["sid"] for s in block], "text": text,
                          "words": sum(words(s["text"]) for s in block)})

    return {"item": item, "sents": sents, "keep": keep_set, "repaired": repaired,
            "groups": gs, "summaries": summaries, "what_it_is": what_it_is,
            "select_out": u1.get("completion_tokens"),
            "summary_out": sum(1 for _ in summaries)}


def render(d):
    item, sents = d["item"], d["sents"]
    idx = {s["sid"]: i for i, s in enumerate(sents)}
    gi = {}                       # sid -> group number
    for n, g in enumerate(d["groups"]):
        for i in g:
            gi[sents[i]["sid"]] = n

    # ---- panel 1: the whole transcript, unselected faded
    p1 = []
    for s in sents:
        sel = s["sid"] in d["keep"]
        if sel:
            cls, gid = "t-sel", gi.get(s["sid"])
        elif s["sid"] in d["repaired"]:
            cls, gid = "t-con", gi.get(s["sid"])
        else:
            cls, gid = "t-dim", None
        gattr = f' data-g="{gid}"' if gid is not None else ""
        p1.append(
            f'<div class="ts {cls}" data-sid="{s["sid"]}"{gattr}>'
            f'<span class="sid">{s["sid"]}</span>'
            f'<span class="spk">{esc((s.get("speaker") or "")[:46])}</span>'
            f'<span class="tx">{esc(s["text"])}</span></div>')

    # ---- panel 2: selected only, grouped, shaded by whether it carries a summary
    p2 = []
    for n, g in enumerate(d["groups"]):
        rows = []
        for i in g:
            s = sents[i]
            flag = ('<span class="flag">predecessor added</span>'
                    if s["sid"] in d["repaired"] else "")
            rows.append(
                f'<div class="ss" data-sid="{s["sid"]}" data-g="{n}">'
                f'<span class="sid">{s["sid"]}{flag}</span>'
                f'<span class="spk">{esc((s.get("speaker") or "")[:46])}</span>'
                f'<span class="tx">{esc(s["text"])}</span></div>')
        p2.append(f'<div class="grp" data-g="{n}">{"".join(rows)}</div>')

    # ---- panel 3: one summary bubble per group, with a brace pointing left
    p3 = []
    for n, sm in enumerate(d["summaries"]):
        cov = f"{len(sm['sids'])} sentences &middot; {sm['words']} words"
        p3.append(
            f'<div class="bub" data-g="{n}">'
            f'<div class="brc"><svg viewBox="0 0 16 100" preserveAspectRatio="none">'
            f'<path d="M15 1 C4 1 3 50 1 50 C3 50 4 99 15 99" fill="none" '
            f'stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke"/>'
            f'</svg></div>'
            f'<div class="bub-in"><div class="bub-h">summary of group {n + 1}'
            f'<span class="cov">{cov}</span></div>'
            f'<div class="bub-t">{esc(sm["text"]) or "<i>no summary returned</i>"}</div></div>'
            f'</div>')

    kept = len(d["keep"])
    kept_w = sum(words(sents[i]["text"]) for i in
                 [idx[x] for x in d["keep"] if x in idx])
    total_w = sum(words(s["text"]) for s in sents)
    sum_w = sum(words(sm["text"]) for sm in d["summaries"])

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Spike — transcript &rarr; selection &rarr; summary</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --ink:#14181d; --mid:#4a545e; --dim:#8b95a1; --faint:#c3cad2; --line:#e3e7ec;
  --bg:#fbfbfa; --sel:#1c6b4a; --sel-soft:#e8f2ec; --warm:#a4551f;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}}
body{{background:var(--bg);color:var(--ink);
  font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
  padding:28px 32px 80px;-webkit-font-smoothing:antialiased}}
header{{margin-bottom:1.4rem}}
h1{{font-size:1.3rem;letter-spacing:-.01em;line-height:1.3}}
.sub{{color:var(--dim);font-size:.82rem;margin-top:.35rem;line-height:1.65}}
.sub b{{color:var(--mid)}}
.stats{{display:flex;gap:1.4rem;margin-top:1rem;flex-wrap:wrap}}
.stat{{border-left:2px solid var(--line);padding-left:.7rem}}
.stat .v{{font:700 1.05rem/1.2 var(--mono);font-variant-numeric:tabular-nums}}
.stat .l{{font-size:.68rem;color:var(--dim);text-transform:uppercase;letter-spacing:.07em}}

/* ---------- the three panels ---------- */
.panels{{display:grid;grid-template-columns:1.15fr 1fr .95fr;gap:44px;
  margin-top:2.2rem;align-items:start}}
.ph{{position:sticky;top:0;background:var(--bg);z-index:3;padding:.5rem 0 .6rem;
  border-bottom:1px solid var(--line);margin-bottom:1rem}}
.ph h2{{font-size:.78rem;text-transform:uppercase;letter-spacing:.1em;color:var(--mid)}}
.ph .c{{font-size:.68rem;color:var(--dim);margin-top:.15rem}}

/* panel 1 — transcript. Unselected sentences fade; selected ones are ink. */
.ts{{display:grid;grid-template-columns:3.4rem 1fr;gap:.5rem;padding:.3rem 0;
  border-left:2px solid transparent;padding-left:.5rem}}
.ts .sid{{font:600 .6rem/1.5 var(--mono);color:var(--faint);padding-top:.18rem}}
.ts .spk{{grid-column:2;font-size:.66rem;color:var(--faint);
  text-transform:uppercase;letter-spacing:.05em}}
.ts .tx{{grid-column:2;font-size:.82rem}}
.t-dim .tx{{color:var(--faint)}}
.t-sel{{border-left-color:var(--sel);background:linear-gradient(90deg,var(--sel-soft),transparent 70%)}}
.t-sel .tx{{color:var(--ink);font-weight:500}}
.t-sel .sid{{color:var(--sel)}}
.t-sel .spk{{color:var(--dim)}}

/* panel 2 — selection. Shaded by group so the eye can follow the funnel. */
.grp{{margin-bottom:1.15rem;padding-left:.6rem;border-left:2px solid var(--sel-soft)}}
.ss{{display:grid;grid-template-columns:3.4rem 1fr;gap:.5rem;margin-bottom:.85rem}}
.ss .sid{{font:600 .6rem/1.6 var(--mono);color:var(--sel);padding-top:.16rem}}
.ss .flag{{display:block;color:var(--warm);font-size:.55rem;font-weight:500;
  text-transform:none;letter-spacing:0;margin-top:.1rem;line-height:1.3}}
.ss .spk{{grid-column:2;font-size:.64rem;color:var(--dim);
  text-transform:uppercase;letter-spacing:.05em}}
.ss .tx{{grid-column:2;font-size:.85rem;line-height:1.58}}

/* panel 3 — summaries, each braced to its group */
.bub{{display:grid;grid-template-columns:14px 1fr;gap:.5rem;margin-bottom:1.15rem;
  color:var(--sel)}}
.bub .brc svg{{width:16px;height:100%;display:block}}
.bub-in{{border:1px solid var(--line);border-left:3px solid var(--sel);
  border-radius:9px;padding:.7rem .85rem;background:#fff;
  box-shadow:0 1px 2px rgba(20,24,29,.04)}}
.bub-h{{font-size:.6rem;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
  margin-bottom:.35rem}}
.bub-h .cov{{text-transform:none;letter-spacing:0;color:var(--faint);margin-left:.5rem}}
.bub-t{{font-size:.86rem;color:var(--ink);line-height:1.6}}

.note{{margin-top:2.6rem;border-top:1px solid var(--line);padding-top:1.1rem;
  color:var(--mid);font-size:.8rem;line-height:1.7;max-width:74ch}}
.note b{{color:var(--ink)}}
.note.warn{{background:#fdf1e8;border:1px solid #f0d9c6;border-radius:9px;
  padding:.9rem 1.1rem;margin-top:1.2rem}}
#wires{{position:fixed;inset:0;pointer-events:none;z-index:1}}
</style></head><body>

<svg id="wires"></svg>

<header>
  <h1>Transcript &rarr; selection &rarr; summary</h1>
  <p class="sub">
    <b>{esc(item.get('id'))}</b> &middot; {esc(item.get('title') or '')}<br>
    The model returned only sentence ids. <b>Every word in panels 1 and 2 is copied from
    the archived Hansard record by id</b> — the model never wrote any of it. Panel 3 is
    model-written and exists only as a convenience; it sits beside the sentences it
    claims to summarise so it can be checked.
  </p>
  <div class="stats">
    <div class="stat"><div class="v">{len(sents)}</div><div class="l">sentences in record</div></div>
    <div class="stat"><div class="v">{kept}</div><div class="l">selected</div></div>
    <div class="stat"><div class="v">{len(d['summaries'])}</div><div class="l">summaries</div></div>
    <div class="stat"><div class="v">{total_w:,}</div><div class="l">words in record</div></div>
    <div class="stat"><div class="v">{kept_w:,}</div><div class="l">words selected</div></div>
    <div class="stat"><div class="v">{sum_w:,}</div><div class="l">words of summary</div></div>
    <div class="stat"><div class="v">{kept/max(1,len(sents)):.0%}</div><div class="l">kept</div></div>
  </div>
</header>

<div class="panels">
  <div>
    <div class="ph"><h2>1 &middot; Full transcript</h2>
      <div class="c">{len(sents)} sentences &middot; faded = not selected</div></div>
    {''.join(p1)}
  </div>
  <div>
    <div class="ph"><h2>2 &middot; Selected as important</h2>
      <div class="c">{kept} sentences &middot; verbatim, grouped into {len(d['summaries'])} sets</div></div>
    {''.join(p2)}
  </div>
  <div class="p3">
    <div class="ph"><h2>3 &middot; Summary of each set</h2>
      <div class="c">{len(d['summaries'])} summaries &middot; model-written</div></div>
    {''.join(p3)}
  </div>
</div>

<p class="note">
  <b>How to read it.</b> Grey lines run from each selected sentence in panel 1 to the
  same sentence in panel 2 &mdash; that is the first cut of the funnel. The brace beside
  each summary in panel 3 spans exactly the set of sentences it covers, so a claim in
  panel 3 can always be traced back to the words that justify it, and no further than
  the record.
</p>
<div class="note warn">
  <b>Panel 3 is the weak link, and it is labelled as such.</b> It is the only place on
  this page where a machine writes prose, which is precisely the failure mode that
  sentence selection was introduced to remove: three rounds of prompt rules did not
  reduce the rate at which the model adds specifics the source does not state. Here that
  risk is contained rather than eliminated &mdash; the summary is adjacent to its
  evidence, marked as derived, and nothing in panels 1 or 2 depends on it. If it proves
  unreliable, it can be dropped without touching the other two panels.
</div>

<script>
// Draw the funnel connectors after layout, measuring real positions. Redrawn on resize.
const wires = document.getElementById('wires');
function draw() {{
  const NS = 'http://www.w3.org/2000/svg';
  while (wires.firstChild) wires.removeChild(wires.firstChild);
  const box = document.body.getBoundingClientRect();
  wires.setAttribute('viewBox', `0 0 ${{window.innerWidth}} ${{window.innerHeight}}`);
  wires.setAttribute('width', window.innerWidth);
  wires.setAttribute('height', window.innerHeight);

  // 1 -> 2 : ONE FUNNEL PER GROUP. A per-sentence line was tried first and read as
  // spaghetti, because panel 1 keeps all 40 sentences while panel 2 keeps 10, so the
  // same sentence sits at wildly different heights in each. Connecting the RANGE of
  // transcript a group draws from, to the group itself, is both legible and truer:
  // a set of selected sentences comes from a stretch of debate, not from nothing.
  const g1 = {{}};
  document.querySelectorAll('.ts[data-g]').forEach(el => {{
    const g = el.dataset.g, r = el.getBoundingClientRect();
    const t = r.top - box.top, b = r.bottom - box.top;
    if (!g1[g]) g1[g] = {{top: t, bottom: b, right: r.right - box.left}};
    else {{ g1[g].top = Math.min(g1[g].top, t); g1[g].bottom = Math.max(g1[g].bottom, b);
            g1[g].right = Math.max(g1[g].right, r.right - box.left); }}
  }});
  document.querySelectorAll('.grp').forEach(el => {{
    const a = g1[el.dataset.g]; if (!a) return;
    const r = el.getBoundingClientRect();
    const x1 = a.right - 2, x2 = r.left - box.left + 2;
    const mid = (x1 + x2) / 2;
    const y1 = r.top - box.top, y2 = r.bottom - box.top;
    const p = document.createElementNS(NS, 'path');
    p.setAttribute('d', `M${{x1}} ${{a.top}} C${{mid}} ${{a.top}} ${{mid}} ${{y1}} ${{x2}} ${{y1}}`
                      + ` M${{x1}} ${{a.bottom}} C${{mid}} ${{a.bottom}} ${{mid}} ${{y2}} ${{x2}} ${{y2}}`);
    p.setAttribute('fill', 'none');
    p.setAttribute('stroke', '#cbd6dd');
    p.setAttribute('stroke-width', '1.1');
    wires.appendChild(p);
  }});

  // 2 -> 3 : each summary bubble is placed LEVEL with the group it summarises, and
  // its brace is stretched to span that group's full height. Placement happens after
  // measurement, and bubbles are nudged down on collision so a long summary next to a
  // short group cannot overlap the one below it.
  const p3 = document.querySelector('.p3');
  const p2col = document.querySelector('.panels > div:nth-child(2)');
  const gs = {{}};
  document.querySelectorAll('.grp').forEach(el => {{
    const r = el.getBoundingClientRect();
    gs[el.dataset.g] = {{top: r.top - box.top, bottom: r.bottom - box.top,
                         right: r.right - box.left}};
  }});
  if (p3 && p2col) {{
    const pr = p2col.getBoundingClientRect();
    const ph = document.querySelector('.p3 .ph').getBoundingClientRect();
    p3.style.minHeight = (pr.height + 2) + 'px';
    let floor = ph.bottom - box.top + 6;
    document.querySelectorAll('.bub').forEach(el => {{
      const g = gs[el.dataset.g]; if (!g) return;
      // measure natural height, then pin the top to the group's top
      el.style.top = '0px'; el.style.height = 'auto';
      const nat = el.getBoundingClientRect().height;
      let top = Math.max(g.top, floor);
      el.style.top = top + 'px';
      el.style.height = Math.max(nat, (g.bottom - g.top)) + 'px';
      floor = top + Math.max(nat, (g.bottom - g.top)) + 14;
      // the brace spans exactly this group
      const svg = el.querySelector('.brc svg');
      if (svg) svg.setAttribute('viewBox', '0 0 16 ' + Math.max(nat, g.bottom - g.top));
      const b = el.getBoundingClientRect();
      const x1 = g.right + 6, x2 = b.left - box.left;
      if (x2 <= x1) return;
      const mid = (x1 + x2) / 2;
      const y1 = b.top - box.top + b.height / 2;
      const p = document.createElementNS(NS, 'path');
      p.setAttribute('d', `M${{x1}} ${{g.top}} C${{mid}} ${{g.top}} ${{mid}} ${{y1}} ${{x2}} ${{y1}}`
                        + ` M${{x1}} ${{g.bottom}} C${{mid}} ${{g.bottom}} ${{mid}} ${{y1}} ${{x2}} ${{y1}}`);
      p.setAttribute('fill', 'none');
      p.setAttribute('stroke', '#9dc4ae');
      p.setAttribute('stroke-width', '1.3');
      wires.appendChild(p);
    }});
  }}
}}
draw();
window.addEventListener('resize', draw);
window.addEventListener('load', draw);
setTimeout(draw, 350);
</script>
</body></html>"""


def main():
    item_id = sys.argv[1] if len(sys.argv) > 1 else "oral-answer-3964"
    t0 = time.time()
    d = build(item_id)
    html = render(d)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    sents = d["sents"]
    print(f"{item_id}: {len(sents)} sentences -> {len(d['keep'])} selected "
          f"-> {len(d['summaries'])} summaries   ({time.time() - t0:.0f}s)")
    print(f"  dangling repaired : {sorted(d['repaired']) or 'none'}")
    for n, sm in enumerate(d["summaries"]):
        print(f"  [{n + 1}] {len(sm['sids'])} sents: {sm['text'][:96]}")
    print(f"\nwrote {OUT}  ({len(html):,} chars)")


if __name__ == "__main__":
    main()
