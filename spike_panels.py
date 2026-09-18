#!/usr/bin/env python3
"""SPIKE (throwaway): two panels — transcript with selection emphasised, and a sticky
summary rail.

Desktop-only by explicit instruction; this is the one exception to the mobile-first
rule and it applies ONLY to this spike artifact.

   LEFT   the full transcript. Sentences selected as important are emphasised; the
          rest recede. A switch hides the unselected ones entirely, so the reader can
          collapse the record to just the selection without losing the ability to see
          what was skipped.
   RIGHT  the summaries, in a rail that stays put while you scroll. Each summary takes
          over as its section comes into view, so no connector or brace is needed --
          the alignment problem disappears because the summary comes to the reader
          instead of having to line up with anything.

Every word in the left panel is copied from the archived Hansard by id: the model
returns ids only. The right panel is model-written, which is why it is presented as a
companion to the verbatim text rather than a replacement for it.
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

OUT_DIR = os.path.join(ROOT, "site", "dist")

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

TITLE_SYSTEM = """Given consecutive sentences from a Singapore Parliament record, give a \
3-6 word plain-English label for what this passage is about.

Use only words for subjects the passage actually names. No evaluation.

Return JSON only: {"label": "..."}"""

LABEL_TMPL = """These consecutive sentences from the record:

{block}

Return the JSON described in your instructions."""

# What share of an item's sentences are worth keeping. Measured: the small-item runs
# kept 8-12 of 40 (20-30%), and 1.0 would mean "keep everything", which is not a
# summary. 0.14 of a 444-sentence answer is ~62 sentences, still far more than anyone
# reads -- the reading budget has to be reconsidered for verbatim text, and this spike
# is how we find out what it should be.
SELECT_SHARE = 0.14

SELECT_TMPL = """Sentences from the record, each prefixed with its id:

{excerpt}

Return the JSON described in your instructions."""

SUMMARY_TMPL = """These consecutive sentences from the record:

{block}

Return the JSON described in your instructions."""

DANGLING = re.compile(
    r"^\s*(This|That|These|Those|It|They|He|She|We|I|There|Such|"
    r"However|Therefore|Thus|Hence|So|And|But|Also|Then|Now|Certainly)\b", re.I)

# HOW FAR TO WALK BACK for an antecedent. Measured over all 8,176 selections in the 2026
# briefs: depth 0 resolves 59.2%; step 1 resolves a further 22.4% and takes the
# "still ambiguous" share from 40.9% to 18.6%; step 2 buys another 9.8% (to 8.8%), and a
# recursive walk runs to a maximum depth of 15 with 0.15% of walks reaching the item's
# first sentence. So it DOES terminate -- the question is only whether the extra text is
# worth it, and the answer is no: the walk stops at ONE step.
#
# The reason one step is enough is the layout, not the language. Unselected sentences are
# not deleted, they are faded -- so a reader who needs the antecedent of an emphasised
# sentence can read the faint line directly above it. The transcript IS the fallback,
# which means "unresolved" here does not mean "unavailable". One step promotes the common
# case (a pronoun needing the noun before it) to full ink; everything rarer stays legible
# and merely unemphasised.
WALK_BACK = 1


def words(s):
    return len(re.findall(r"[A-Za-z0-9']+", s or ""))


def esc(s):
    return H.escape(str(s or ""))


def load_item(item_id, year=2026):
    with open(os.path.join(ROOT, "pipeline", "dataset", str(year),
                           f"{item_id}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def select(item, model):
    """Select across the whole item, CHUNKING when it does not fit.

    A 444-sentence oral answer is 68k characters against a 6k budget, so it needs 15
    calls. The difficulty is that no chunk knows what the others kept, so the merge is
    where this can go wrong in both directions: keep every chunk's output and a long
    item is flooded (the original pipeline hit 259 points this way); keep too few and a
    long item is starved.

    So the per-chunk ask is PROPORTIONAL to the chunk's share of the item, and the merge
    is a global cap applied round-robin across chunks. That way no single chunk can
    dominate and no chunk is silently dropped -- the same shape the point cap already
    uses, for the same reason.
    """
    sents = item["sentences"]
    by_sid = {s["sid"]: s for s in sents}
    chunks = B.make_chunks(sents)
    total = len(sents)
    target = max(3, round(total * SELECT_SHARE))
    per_chunk = max(1, round(target / max(1, len(chunks))))

    picked, usage_total, what = [], {"completion_tokens": 0, "prompt_tokens": 0}, ""
    for n, chunk in enumerate(chunks, 1):
        excerpt = "\n".join(f"{s['sid']}: {s['text']}" for s in chunk)
        sys_p = SELECT_SYSTEM + (
            f"\n- This excerpt is part {n} of {len(chunks)} from a longer record. Keep "
            f"AT MOST {per_chunk} sentence ids -- the most substantive in this excerpt.")
        raw, usage = B.ask(SELECT_TMPL.format(excerpt=excerpt), sys_p, model)
        usage_total["completion_tokens"] += usage.get("completion_tokens", 0) or 0
        usage_total["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
        got = B.parse_json(raw) or {}
        if not what:
            what = got.get("what_it_is") or ""
        for x in (got.get("keep") or []):
            x = str(x)
            if x in by_sid and x not in picked:
                picked.append(x)

    # global cap, round-robin across chunks so coverage is spread not concentrated
    if len(picked) > target:
        rank = {s["sid"]: i for i, s in enumerate(sents)}
        buckets = [[] for _ in chunks]
        owner = {}
        for ci, chunk in enumerate(chunks):
            for s in chunk:
                owner[s["sid"]] = ci
        for sid in sorted(picked, key=lambda x: rank[x]):
            buckets[owner[sid]].append(sid)
        merged, i = [], 0
        while len(merged) < target and any(buckets):
            for b in buckets:
                if b and len(merged) < target:
                    merged.append(b.pop(0))
            i += 1
        picked = merged
    return picked, what, usage_total


def groups_of(sents, keep, gap=2):
    """Consecutive selections form one section. A small gap of unselected sentences does
    not split a section, because a passage that belongs together is usually interrupted
    by a sentence that merely connects it."""
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


def ask_one(tmpl, system, model, **kw):
    raw, _ = B.ask(tmpl.format(**kw), system, model)
    return (B.parse_json(raw) or {})


def build(item_id):
    item = load_item(item_id)
    sents = item["sentences"]
    model = B.DEFAULT_MODEL

    keep, what_it_is, _u = select(item, model)
    keep_set = set(keep)
    idx = {s["sid"]: i for i, s in enumerate(sents)}

    # Deterministic dangling repair: an emphasised sentence that opens with a pronoun or
    # connective needs its predecessor emphasised too, or it reads as if it started the
    # thought. Bounded at WALK_BACK so this can never drag in a whole item.
    repaired = set()
    for sid in keep:
        if not DANGLING.match(sents[idx[sid]]["text"]):
            continue
        j = idx[sid]
        for _ in range(WALK_BACK):
            if j == 0:
                break
            j -= 1
            prev = sents[j]["sid"]
            if prev not in keep_set:
                keep_set.add(prev)
                repaired.add(prev)

    gs = groups_of(sents, keep_set)
    sections = []
    for n, g in enumerate(gs):
        block = [sents[i] for i in g]
        sm = ask_one(SUMMARY_TMPL, SUMMARY_SYSTEM, model,
                     block="\n".join(f"{s['sid']}: {s['text']}" for s in block))
        lb = ask_one(LABEL_TMPL, TITLE_SYSTEM, model,
                     block="\n".join(f"{s['sid']}: {s['text']}" for s in block))
        sections.append({
            "n": n, "idx": g, "sids": [s["sid"] for s in block],
            "summary": sm.get("summary") or "", "label": lb.get("label") or "",
            "words": sum(words(s["text"]) for s in block)})
    return {"item": item, "sents": sents, "keep": keep_set, "repaired": repaired,
            "sections": sections, "what_it_is": what_it_is}


def render(d):
    item, sents = d["item"], d["sents"]
    gsec = {}
    for sec in d["sections"]:
        for i in sec["idx"]:
            gsec[i] = sec["n"]

    rows = []
    seen_sec = set()
    for i, s in enumerate(sents):
        sid = s["sid"]
        # ORDER MATTERS: the walk-back repair adds predecessors into keep_set, so
        # testing keep first would classify every grammar addition as selected and the
        # distinct shade would never appear. The repair set is checked first.
        if sid in d["repaired"]:
            cls = "r-con"                     # present for grammar, not for content
        elif sid in d["keep"]:
            cls = "r-sel"
        else:
            cls = "r-dim"
        sec = gsec.get(i)
        marker = ""
        if sec is not None and sec not in seen_sec:
            seen_sec.add(sec)
            sec_obj = d["sections"][sec]
            marker = (f'<div class="sec" data-sec="{sec}" id="sec-{sec}">'
                      f'<span class="sec-n">Section {sec + 1}</span>'
                      f'<span class="sec-l">{esc(sec_obj["label"])}</span></div>')
        rows.append(
            f'{marker}<div class="r {cls}" data-sid="{sid}"'
            + (f' data-sec="{sec}"' if sec is not None else "") + ">"
            f'<span class="sid">{sid}</span>'
            f'<span class="who">{esc((s.get("speaker") or "")[:52])}</span>'
            f'<span class="tx">{esc(s["text"])}</span></div>')

    rail = []
    for sec in d["sections"]:
        rail.append(
            f'<a class="card" data-sec="{sec["n"]}" href="#sec-{sec["n"]}">'
            f'<div class="card-n">Section {sec["n"] + 1}'
            f'<span class="card-m">{len(sec["sids"])} sentences &middot; '
            f'{sec["words"]} words</span></div>'
            f'<div class="card-l">{esc(sec["label"])}</div>'
            f'<div class="card-s">{esc(sec["summary"])}</div></a>')

    kept = len(d["keep"])
    kept_w = sum(words(sents[i]["text"]) for i, s in enumerate(sents)
                 if s["sid"] in d["keep"] and s["sid"] not in d["repaired"])
    total_w = sum(words(s["text"]) for s in sents)
    sum_w = sum(words(sec["summary"]) for sec in d["sections"])
    n_sec = len(d["sections"])

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Spike — transcript and summaries</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --ink:#14181d; --mid:#4a545e; --dim:#8b95a1; --faint:#c8cfd6; --ghost:#dde3e9;
  --line:#e3e7ec; --bg:#fbfbfa; --card:#fff;
  --sel:#1c6b4a; --sel-soft:#e8f2ec; --warm:#a4551f;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}}
body{{background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;
  font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
  padding:30px 34px 120px}}

header{{max-width:1080px}}
h1{{font-size:1.34rem;letter-spacing:-.012em;line-height:1.28}}
.sub{{color:var(--dim);font-size:.82rem;margin-top:.4rem;line-height:1.68;max-width:80ch}}
.sub b{{color:var(--mid)}}
.stats{{display:flex;gap:1.5rem;margin-top:1.1rem;flex-wrap:wrap}}
.stat{{border-left:2px solid var(--line);padding-left:.72rem}}
.stat .v{{font:700 1.04rem/1.2 var(--mono);font-variant-numeric:tabular-nums}}
.stat .l{{font-size:.66rem;color:var(--dim);text-transform:uppercase;letter-spacing:.07em}}

.bar{{display:flex;align-items:center;gap:.85rem;margin:1.5rem 0 0;
  padding:.85rem 1rem;background:var(--card);border:1px solid var(--line);
  border-radius:11px;max-width:1080px}}
.sw{{position:relative;width:40px;height:23px;flex:none}}
.sw input{{position:absolute;opacity:0;width:100%;height:100%;margin:0;cursor:pointer;z-index:2}}
.sw i{{position:absolute;inset:0;background:var(--ghost);border-radius:999px;
  transition:background .18s ease}}
.sw i::after{{content:"";position:absolute;top:2.5px;left:2.5px;width:18px;height:18px;
  background:#fff;border-radius:50%;transition:transform .18s cubic-bezier(.3,1.4,.5,1);
  box-shadow:0 1px 2px rgba(20,24,29,.22)}}
.sw input:checked + i{{background:var(--sel)}}
.sw input:checked + i::after{{transform:translateX(17px)}}
.sw input:focus-visible + i{{outline:2.5px solid var(--sel);outline-offset:2px}}
.bar label{{font-size:.82rem;cursor:pointer;user-select:none}}
.bar label b{{color:var(--sel)}}
.bar .hint{{margin-left:auto;font-size:.72rem;color:var(--faint)}}

.panels{{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(300px,1fr);
  gap:64px;margin-top:1.7rem;align-items:start}}

/* ---------------- LEFT: the transcript ---------------- */
.doc{{min-width:0}}
.r{{display:grid;grid-template-columns:3.3rem minmax(0,1fr);gap:.55rem;
  padding:.4rem .6rem .4rem .7rem;border-left:3px solid transparent;
  border-radius:0 6px 6px 0}}
.r .sid{{font:600 .58rem/1.5 var(--mono);color:var(--faint);padding-top:.2rem}}
.r .who{{grid-column:2;font-size:.62rem;color:var(--faint);text-transform:uppercase;
  letter-spacing:.055em;margin-bottom:.12rem}}
.r .tx{{grid-column:2;font-size:.84rem;line-height:1.6;color:var(--faint)}}

/* selected: full ink, a green edge and a wash -- this is the emphasis */
.r-sel{{border-left-color:var(--sel);
  background:linear-gradient(90deg,var(--sel-soft),rgba(232,242,236,0) 82%)}}
.r-sel .sid{{color:var(--sel)}}
.r-sel .who{{color:var(--dim)}}
.r-sel .tx{{color:var(--ink);font-weight:500;font-size:.875rem}}

/* predecessor pulled in only so a sentence has its antecedent */
.r-con{{border-left-color:var(--ghost)}}
.r-con .tx{{color:#7c8794}}
.r-con .who{{color:var(--faint)}}

/* A turn the record does not attribute keeps its line but prints nothing, so an
   unattributed sentence does not shift the ones below it. */
.r .who:empty{display:block;min-height:.62rem}

/* hidden when the switch is on */
body.only-sel .r-dim{{display:none}}

.sec{{display:flex;align-items:baseline;gap:.6rem;margin:1.5rem 0 .35rem;
  padding-top:.85rem;border-top:1px solid var(--line)}}
.r:first-child + .sec, .doc > .sec:first-child{{margin-top:0;padding-top:0;border-top:0}}
.sec-n{{font:700 .62rem/1 var(--mono);color:var(--sel);text-transform:uppercase;
  letter-spacing:.09em;background:var(--sel-soft);border-radius:999px;padding:.25rem .55rem}}
.sec-l{{font-size:.76rem;color:var(--dim)}}

/* ---------------- RIGHT: the sticky rail ---------------- */
.rail{{position:sticky;top:26px;display:grid;gap:.6rem;
  max-height:calc(100vh - 52px);overflow-y:auto;overscroll-behavior:contain;
  scrollbar-width:thin;padding-right:6px}}
.rail::-webkit-scrollbar{{width:7px}}
.rail::-webkit-scrollbar-thumb{{background:#dfe4e9;border-radius:4px}}
.rail::-webkit-scrollbar-track{{background:transparent}}
/* a long rail is expected -- 35 cards is a legitimate brief for a 12,000-word record.
   What it must do is carry the reader to the section they are on, so the active card
   is always held inside the scroll viewport. */
.rail-hd{{position:sticky;top:0;background:var(--bg);z-index:2;
  font-size:.66rem;text-transform:uppercase;letter-spacing:.1em;color:var(--dim);
  padding:.25rem 0 .35rem}}
.card{{display:block;text-decoration:none;color:inherit;background:var(--card);
  border:1px solid var(--line);border-left:3px solid var(--ghost);border-radius:10px;
  padding:.7rem .85rem;transition:border-color .16s ease,opacity .16s ease,
  box-shadow .16s ease,transform .16s ease;opacity:.55}}
.card:hover{{opacity:.85}}
.card.on{{border-left-color:var(--sel);opacity:1;
  box-shadow:0 2px 10px rgba(20,24,29,.07);transform:translateX(-2px)}}
.card-n{{font:600 .58rem/1.4 var(--mono);color:var(--dim);text-transform:uppercase;
  letter-spacing:.08em}}
.card-m{{float:right;font-weight:400;color:var(--faint);text-transform:none;letter-spacing:0}}
.card-l{{font-size:.76rem;color:var(--mid);margin:.2rem 0 .3rem;font-weight:600}}
.card-s{{font-size:.83rem;line-height:1.58;color:var(--ink)}}
.card.on .card-s{{color:var(--ink)}}
.card:not(.on) .card-s{{color:var(--mid)}}

.note{{margin-top:3rem;border-top:1px solid var(--line);padding-top:1.2rem;
  color:var(--mid);font-size:.8rem;line-height:1.72;max-width:74ch}}
.note b{{color:var(--ink)}}
.warn{{background:#fdf1e8;border:1px solid #f0d9c6;border-radius:10px;
  padding:.9rem 1.1rem;margin-top:1.3rem;max-width:74ch;color:#7a4a1e;
  font-size:.79rem;line-height:1.7}}
.warn b{{color:#5e360f}}
</style></head><body>

<header>
  <h1>Transcript &amp; summaries</h1>
  <p class="sub">
    <b>{esc(item.get('id'))}</b> &middot; {esc(item.get('title') or '')}<br>
    The model returned only sentence ids. <b>Every word on the left is copied from the
    archived Hansard record by id</b> &mdash; the model never wrote any of it. The right
    panel is model-written and sits alongside the record as a convenience; it does not
    replace it.
  </p>
  <div class="stats">
    <div class="stat"><div class="v">{len(sents)}</div><div class="l">sentences</div></div>
    <div class="stat"><div class="v">{kept}</div><div class="l">selected</div></div>
    <div class="stat"><div class="v">{len(d['repaired'])}</div><div class="l">added for grammar</div></div>
    <div class="stat"><div class="v">{n_sec}</div><div class="l">sections</div></div>
    <div class="stat"><div class="v">{total_w:,}</div><div class="l">words in record</div></div>
    <div class="stat"><div class="v">{kept_w:,}</div><div class="l">words selected</div></div>
    <div class="stat"><div class="v">{sum_w:,}</div><div class="l">words of summary</div></div>
  </div>
</header>

<div class="bar">
  <span class="sw"><input type="checkbox" id="onlysel"><i></i></span>
  <label for="onlysel">Hide the sentences <b>not</b> selected &mdash; show only what the
    model kept</label>
  <span class="hint">{len(sents) - kept - len(d['repaired'])} sentences hidden</span>
</div>

<div class="panels">
  <div class="doc">
    {''.join(rows)}
  </div>

  <div class="rail">
    <div class="rail-hd">Summaries &middot; follow the section you are reading</div>
    {''.join(rail)}
  </div>
</div>

<p class="note">
  <b>How to read it.</b> Left is the record itself: sentences the model kept are in full
  ink with a green edge, sentences pulled in only so a kept sentence has its antecedent
  are mid-grey, and everything the model passed over is faint. Flip the switch to drop
  the faint ones and the record collapses to the selection &mdash; nothing is deleted,
  it is just out of the way.
</p>
<p class="note">
  <b>Why the summaries move.</b> An earlier version put the summaries in their own column
  aligned to the passages they covered, with braces drawn across. It failed: with 40
  sentences of record collapsing to 8 kept ones, no two things could be lined up, and the
  braces crossed each other. Keeping the summaries in a rail that follows the reader
  removes the alignment problem instead of solving it &mdash; the summary arrives when
  its section does.
</p>
<div class="warn">
  <b>The right panel is model-written, and that is the one place this page can be wrong.</b>
  Three rounds of prompt rules did not reduce the rate at which the model adds a specific
  the source does not state, so the summary is treated as a derived convenience rather
  than evidence. Everything checkable is on the left, in the record's own words. If a
  summary disagrees with the passage beside it, the passage is right.
</div>

<script>
const doc = document.body;
document.getElementById('onlysel').addEventListener('change', e => {{
  doc.classList.toggle('only-sel', e.target.checked);
  // headings for hidden sections would otherwise float alone; hide them too
  document.querySelectorAll('.sec').forEach(sec => {{
    const n = sec.dataset.sec;
    const any = document.querySelector(`.r[data-sec="${{n}}"]:not(.r-dim)`);
    sec.style.display = (e.target.checked && !any) ? 'none' : '';
  }});
  sync();
}});

const cards = [...document.querySelectorAll('.card')];
function sync() {{
  // the active section is the last one whose heading has passed the reading line
  const line = window.innerHeight * 0.28;
  let active = 0;
  document.querySelectorAll('.sec').forEach(sec => {{
    if (sec.style.display === 'none') return;
    const r = sec.getBoundingClientRect();
    if (r.top <= line) active = Math.max(active, +sec.dataset.sec);
  }});
  cards.forEach(c => c.classList.toggle('on', +c.dataset.sec === active));
  const on = cards.find(c => +c.dataset.sec === active);
  if (on) {{
    const rail = document.querySelector('.rail');
    if (!rail) return;
    const rr = rail.getBoundingClientRect(), or_ = on.getBoundingClientRect();
    const pad = 12;                       // keep a little air above the active card
    if (or_.top < rr.top + pad) {{
      rail.scrollTop += (or_.top - rr.top) - pad;          // card is above the viewport
    }} else if (or_.bottom > rr.bottom - pad) {{
      rail.scrollTop += (or_.bottom - rr.bottom) + pad;    // card is below it
    }}
  }}
}}
let raf = null;
addEventListener('scroll', () => {{
  if (raf) return;
  raf = requestAnimationFrame(() => {{ raf = null; sync(); }});
}}, {{passive: true}});
addEventListener('resize', sync);
const railEl = document.querySelector('.rail');
if (railEl) railEl.addEventListener('scroll', () => {{
  // a manual scroll of the rail must not be fought by the tracker; release the next
  // automatic nudge until the reader stops
  clearTimeout(window.__railHold);
  window.__railHold = setTimeout(sync, 900);
}}, {{passive: true}});
sync();
</script>
</body></html>"""


def main():
    ids = sys.argv[1:] or ["oral-answer-3964"]
    for item_id in ids:
        t0 = time.time()
        d = build(item_id)
        html = render(d)
        out = os.path.join(OUT_DIR, f"spike-panels.html" if len(ids) == 1
                           else f"spike-{item_id}.html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        _report(d, item_id, time.time() - t0, out)


def _report(d, item_id, secs, out):
    print(f"{item_id}: {len(d['sents'])} sentences -> {len(d['keep'])} selected "
          f"-> {len(d['sections'])} sections   ({secs:.0f}s)")
    print(f"  grammar-only additions : {sorted(d['repaired']) or 'none'}")
    for sec in d["sections"]:
        print(f"  [{sec['n'] + 1}] {sec['label']:34s} | {sec['summary'][:78]}")
    print(f"  wrote {out}\n")
    return


def _unused(item_id, t0):
    print("no-op")


if __name__ == "__main__":
    main()
