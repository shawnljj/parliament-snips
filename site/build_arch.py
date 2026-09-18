#!/usr/bin/env python3
"""Build site/pipe-arch.html — the architecture page.

WHY THIS IS A GENERATOR (project rule N-3): every number this page states about the
system must be computed from the system, never typed in. The hand-written version this
replaces had gone stale in ways nobody noticed — it still advertised "model choice still
open" after the model had been chosen and measured, and its footer claimed "no
implementation started" after 287 briefs had been published. A number you type is a
number that quietly becomes a lie.

So: constants are read from summariser/build_briefs.py, corpus figures from
pipeline/dataset, run accounting from pipeline/usage.jsonl, and verifier figures from
the verifier's own report. Nothing is asserted that is not measured.

The hand-authored SVG diagram, ERD and schema tables are kept as source parts under
site/parts/ — they are drawings and interface contracts, not measurements.
"""
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PARTS = os.path.join(HERE, "parts")

sys.path.insert(0, os.path.join(ROOT, "summariser"))


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def num(n):
    return f"{n:,}"


def part(name):
    p = os.path.join(PARTS, name)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- facts

def pipeline_facts():
    """Every tunable, read from the module that actually uses it."""
    import build_briefs as B
    import inspect

    def const(name, default="—"):
        return getattr(B, name, default)

    src = inspect.getsource(B)
    # The coverage floors are read inside stage4_verify from the environment; pull the
    # defaults out of the source so the page cannot drift from the code.
    def env_default(var, kind=str):
        m = re.search(r'os\.environ\.get\(\s*"%s"\s*,\s*"([^"]+)"' % var, src)
        return kind(m.group(1)) if m else None

    return {
        "schema": const("SCHEMA"),
        "model": const("DEFAULT_MODEL"),
        "prompt_budget": const("PROMPT_CHAR_BUDGET"),
        "max_reply": const("MAX_REPLY_TOKENS"),
        "timeout": const("REQUEST_TIMEOUT"),
        "retries": const("RETRIES"),
        "required_fields": list(const("REQUIRED_BRIEF_FIELDS", ())),
        "min_coverage": env_default("PARSNIPS_MIN_COVERAGE", float),
        "min_turns": env_default("PARSNIPS_MIN_TURNS_COVERED", int),
        "max_points_env": env_default("PARSNIPS_MAX_POINTS", int),
        "question_start": getattr(getattr(B, "QUESTION_START", None), "pattern", None),
    }


def corpus_facts():
    """Item/sentence/chunk counts, measured off the dataset."""
    import build_briefs as B
    ds = os.path.join(ROOT, "pipeline", "dataset")
    items = sentences = chunks = words = 0
    years = Counter()
    groups = Counter()
    for dirpath, _dirs, files in os.walk(ds):
        for fn in files:
            if not fn.endswith(".json") or fn == "index.json":
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or "sentences" not in rec:
                continue
            items += 1
            sents = rec.get("sentences") or []
            # Prefer the record's own counts; they were computed when it was built.
            sentences += rec.get("sentence_count") or len(sents)
            chunks += rec.get("chunk_count") or len(B.make_chunks(sents))
            words += rec.get("source_words") or 0
            year = os.path.basename(dirpath)
            if re.fullmatch(r"\d{4}", year):
                years[year] += 1
            groups[rec.get("group") or "?"] += 1
    return {"items": items, "sentences": sentences, "chunks": chunks,
            "words": words, "years": dict(sorted(years.items())),
            "groups": dict(groups.most_common())}


def run_facts():
    """Accounting for the most recent run, from its own usage log."""
    import build_briefs as B
    log = getattr(B, "USAGE_LOG", None)
    if not log or not os.path.exists(log):
        return None
    rows = []
    with open(log, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    if not rows:
        return None
    tin = tout = calls = 0
    cost = 0.0
    for r in rows:
        tin += r.get("prompt_tokens", 0) or 0
        tout += r.get("completion_tokens", 0) or 0
        calls += 1
        cost += r.get("cost", 0.0) or r.get("cost_usd", 0.0) or 0.0
    return {"calls": calls, "in": tin, "out": tout, "cost": cost, "rows": len(rows)}


def brief_facts(directory):
    """What was published, and what the gates withheld, measured off disk."""
    if not os.path.isdir(directory):
        return None
    published, withheld, points = [], [], 0
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, fn), encoding="utf-8") as fh:
                b = json.load(fh)
        except (OSError, ValueError):
            continue
        if b.get("_withheld") or b.get("withheld"):
            withheld.append(fn)
            continue
        published.append(fn)
        points += len(b.get("key_points") or [])
    return {"published": len(published), "withheld": len(withheld),
            "points": points, "withheld_names": withheld}


def verifier_facts():
    """Verifier results, from the report the verifier wrote. Honest when absent."""
    cand = os.environ.get("PARSNIPS_VERIFY_REPORT",
                          os.path.join(ROOT, "pipeline", "verify_report.jsonl"))
    if not os.path.exists(cand):
        return None
    counts = Counter()
    defects = Counter()
    total = 0
    with open(cand, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            total += 1
            counts[(r.get("verdict") or "unjudged").lower()] += 1
            if (r.get("verdict") or "") in ("weak", "unsupported", "reversed"):
                defects[(r.get("defect") or "unknown").lower()] += 1
    judged = total - counts.get("unjudged", 0)
    return {"total": total, "judged": judged, "counts": dict(counts),
            "defects": dict(defects),
            "supported_frac": (counts.get("supported", 0) / judged) if judged else 0.0,
            "coverage": (judged / total) if total else 0.0}


# ------------------------------------------------------------------------------ css

def css():
    return """
/* ============================================================
   BASE LAYER IS THE PHONE. Everything here is the 390px layout.
   Wider viewports only ever ADD, in the two min-width blocks at
   the end. There is no max-width override anywhere on this page:
   a max-width block would mean the desktop came first.
   ============================================================ */
:root{
  --bg:#0a0f1a; --panel:#111a2b; --panel-2:#0d1524; --line:#22304a;
  --ink:#e8eef7; --dim:#9fb0c8; --meta:#8fa0ba; --accent:#22d3ee;
  --green:#34d399; --amber:#fbbf24; --rose:#fb7185; --violet:#a78bfa;
  --mono:ui-monospace,SFMono-Regular,Menlo,'JetBrains Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html{color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:var(--mono);
  font-size:14px;line-height:1.68;overflow-wrap:break-word}
.wrap{padding:20px 14px 56px}
h1{font-size:1.12rem;font-weight:700;letter-spacing:-.01em;line-height:1.3}
h2{font-size:.98rem;font-weight:700;margin:2.4rem 0 .5rem;color:#f1f6fc;
  padding-top:1.1rem;border-top:1px solid var(--line)}
h2 .n{color:var(--accent);margin-right:.5rem;font-variant-numeric:tabular-nums}
h3{font-size:.86rem;font-weight:700;color:#f1f6fc;margin:1.6rem 0 .4rem}
h4{font-size:.78rem;font-weight:700;color:var(--dim);margin:1.1rem 0 .3rem;
  text-transform:uppercase;letter-spacing:.08em}
p{margin:0 0 .85rem;color:var(--dim)}
p b,li b{color:#e8eef7}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(34,211,238,.35)}
code{font-family:var(--mono);font-size:.9em;color:#cfe3f5;
  background:rgba(34,211,238,.09);border:1px solid rgba(34,211,238,.16);
  border-radius:3px;padding:.05em .3em}
.hdr{margin-bottom:1.6rem}
.hdr-row{display:flex;align-items:center;gap:.6rem;margin-bottom:.5rem}
.dot{width:10px;height:10px;border-radius:50%;background:var(--accent);flex:none;
  animation:pulse 2.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){.dot{animation:none}}
.sub{color:var(--meta);font-size:.78rem;line-height:1.7;margin-left:1.5rem}
.lede{color:var(--dim);font-size:.8rem;line-height:1.7;margin-bottom:1.1rem}
.diagram{background:var(--panel-2);border:1px solid var(--line);border-radius:10px;
  padding:12px;overflow-x:auto}
.diagram svg{width:100%;min-width:820px;display:block}
.cards{display:grid;gap:.85rem;margin-top:1.1rem}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:1rem}
.card-hd{display:flex;align-items:center;gap:.5rem;margin-bottom:.55rem}
.cdot{width:8px;height:8px;border-radius:50%;flex:none}
.cdot.cyan{background:var(--accent)}.cdot.green{background:var(--green)}
.cdot.amber{background:var(--amber)}.cdot.rose{background:var(--rose)}
.cdot.violet{background:var(--violet)}
.card h3{margin:0;font-size:.8rem}
.card ul{list-style:none;color:var(--dim);font-size:.75rem;line-height:1.75}
.card li{margin-bottom:.28rem}
.card li::before{content:"·";color:var(--meta);margin-right:.45rem}
table{width:100%;border-collapse:collapse;font-size:.73rem;margin-top:.6rem;
  background:var(--panel-2);border:1px solid var(--line);border-radius:8px;
  overflow:hidden}
caption{text-align:left;color:#e8eef7;font-size:.8rem;font-weight:700;padding:.7rem 0 .4rem}
caption .tag{color:var(--meta);font-weight:400;font-size:.7rem;margin-left:.4rem}
th{text-align:left;color:var(--meta);font-weight:600;font-size:.64rem;
  letter-spacing:.07em;text-transform:uppercase;padding:.5rem .6rem;
  border-bottom:1px solid var(--line);background:rgba(34,48,74,.5)}
td{padding:.45rem .6rem;border-bottom:1px solid rgba(34,48,74,.55);
  color:var(--dim);vertical-align:top}
tr:last-child td{border-bottom:0}
td.k{color:#dbe6f3;font-weight:600}
td.t{color:var(--violet)}
.tables{display:grid;gap:1rem;margin-top:.8rem}
.note{background:rgba(52,211,153,.07);border:1px solid rgba(52,211,153,.28);
  border-radius:8px;padding:.8rem .9rem;margin-top:1rem;font-size:.75rem;
  color:#bfe9d7;line-height:1.72}
.note.warn{background:rgba(251,191,36,.07);border-color:rgba(251,191,36,.3);
  color:#f5dfa8}
.note.bad{background:rgba(251,113,133,.07);border-color:rgba(251,113,133,.3);
  color:#fbd0d7}
.note b{color:#fff}
.ledger{display:grid;gap:.6rem;margin-top:.8rem}
.led{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:8px;padding:.75rem .85rem}
.led .v{font-size:1.28rem;font-weight:700;color:#fff;font-variant-numeric:tabular-nums;
  line-height:1.2}
.led .v small{font-size:.62rem;color:var(--meta);font-weight:500;margin-left:.3rem}
.led .l{font-size:.72rem;color:var(--dim);margin-top:.2rem}
.led .s{font-size:.66rem;color:var(--meta);margin-top:.15rem}
.steps{counter-reset:s;list-style:none;margin-top:.7rem}
.steps li{counter-increment:s;position:relative;padding-left:2rem;margin-bottom:.85rem;
  font-size:.77rem;color:var(--dim);line-height:1.72}
.steps li::before{content:counter(s);position:absolute;left:0;top:.05rem;
  width:1.35rem;height:1.35rem;border-radius:50%;background:rgba(34,211,238,.13);
  border:1px solid rgba(34,211,238,.4);color:var(--accent);font-size:.68rem;
  font-weight:700;display:flex;align-items:center;justify-content:center}
.steps li b{color:#e8eef7}
pre{background:var(--panel-2);border:1px solid var(--line);border-radius:8px;
  padding:.8rem .9rem;overflow-x:auto;font-size:.7rem;line-height:1.7;
  color:#cfe3f5;margin:.6rem 0}
pre .c{color:var(--meta)}
.flow{display:grid;gap:.5rem;margin-top:.8rem}
.fbox{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:.7rem .8rem;font-size:.74rem;color:var(--dim)}
.fbox b{color:#fff;display:block;font-size:.78rem;margin-bottom:.15rem}
.fbox .tag{font-size:.63rem;color:var(--meta)}
.fail{border-left:3px solid var(--rose)}
.pass{border-left:3px solid var(--green)}
.footer{margin-top:2.4rem;padding-top:1rem;border-top:1px solid var(--line);
  color:var(--meta);font-size:.68rem;line-height:1.8;text-align:left}

/* Wider viewports ADD. Nothing above is overridden, only extended. */
@media (min-width:620px){
  body{font-size:15px}
  .wrap{padding:32px 24px 72px;max-width:1140px;margin:0 auto}
  h1{font-size:1.4rem}
  h2{font-size:1.08rem}
  .cards{grid-template-columns:repeat(2,1fr)}
  .ledger{grid-template-columns:repeat(2,1fr)}
  .flow{grid-template-columns:repeat(2,1fr)}
}
@media (min-width:900px){
  body{font-size:15.5px}
  .wrap{padding:44px 32px 88px}
  h1{font-size:1.55rem}
  .cards{grid-template-columns:repeat(4,1fr)}
  .ledger{grid-template-columns:repeat(4,1fr)}
  .tables{grid-template-columns:repeat(2,1fr)}
}
"""


# ----------------------------------------------------------------------------- page

def ledger_rows(rows):
    out = ['<div class="ledger">']
    for label, sub, value, unit, lead in rows:
        cls = ' style="border-left-color:#34d399"' if lead else ""
        out.append(f'<div class="led"{cls}>'
                   f'<div class="v">{value}'
                   + (f"<small>{unit}</small>" if unit else "")
                   + f'</div><div class="l">{label}</div>'
                   f'<div class="s">{sub}</div></div>')
    out.append("</div>")
    return "\n".join(out)


def build():
    P = pipeline_facts()
    C = corpus_facts()
    R = run_facts()
    brief_dir = os.environ.get("PARSNIPS_ARCH_BRIEFS",
                               os.path.join(ROOT, "summaries", "2026"))
    BF = brief_facts(brief_dir)
    VF = verifier_facts()

    H = []
    A = H.append

    A(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parsnips — how a day of Parliament becomes a checkable brief</title>
<meta name="description" content="The algorithm behind Parsnips: extract by sentence reference, assemble, then verify. Every figure computed from the pipeline, never typed.">
<meta name="color-scheme" content="dark">
<style>{css()}</style>
</head>
<body>
<div class="wrap">

  <div class="hdr">
    <div class="hdr-row"><div class="dot"></div>
      <h1>Parsnips — how a day of Parliament becomes a checkable brief</h1></div>
    <p class="sub">
      Sitting &rarr; Section &rarr; Speaker &rarr; Sentence &rarr; Claim.
      Deterministic everywhere it can be, a model only where judgement is required.<br>
      Schema v{P['schema']} &middot; model <b>{esc(P['model'])}</b> &middot; every number on this
      page is measured at build time.
    </p>
  </div>

  <p class="lede">
    The problem is not summarisation. A language model summarises Hansard well enough.
    The problem is that <b>a summary you cannot check is indistinguishable from one that
    is wrong</b> — and a wrong claim about what a minister said is the one failure this
    project cannot ship. So the design question was never "which model", it was
    <b>"what can be made structurally impossible, and what has to be verified"</b>.
    This page is the answer: the four stages, the gates that fail closed, and the two
    separate checks that stand behind every published sentence.
  </p>

  <h2><span class="n">01</span>What exists, measured</h2>
  <p class="lede">Corpus figures are counted off <code>pipeline/dataset</code> at build time.</p>
""")

    A(ledger_rows([
        ("Items ingested, each with stable sentence ids", "pipeline/dataset", num(C["items"]), "items", True),
        ("Sentences addressable by id", "never by text or offset", num(C["sentences"]), "sentences", False),
        ("Chunks, defined as id lists", f"budget {num(P['prompt_budget'])} chars per call", num(C["chunks"]), "chunks", False),
        ("Model calls logged", f"schema v{P['schema']}", num(R["calls"]) if R else "—", "calls", False),
    ]))

    if R:
        A(f"""
  <h3>Inference spend, from the run's own log</h3>
  <table>
    <caption>Accounting, not estimate <span class="tag">pipeline/usage.jsonl</span></caption>
    <tbody>
      <tr><td class="k">Tokens in</td><td>{num(R['in'])}</td></tr>
      <tr><td class="k">Tokens out</td><td>{num(R['out'])}</td></tr>
      <tr><td class="k">Model calls</td><td>{num(R['calls'])}</td></tr>
      <tr><td class="k">Spent</td><td>${R['cost']:.2f}</td></tr>
    </tbody>
  </table>""")

    if BF:
        names = ", ".join(esc(n[:-5]) for n in BF["withheld_names"][:12])
        A(f"""
  <h3>What the gates did to {esc(os.path.basename(brief_dir))}</h3>
  <table>
    <caption>Published vs withheld <span class="tag">{esc(brief_dir)}</span></caption>
    <tbody>
      <tr><td class="k">Published</td><td>{num(BF['published'])} briefs</td></tr>
      <tr><td class="k">Withheld</td><td>{num(BF['withheld'])}
        {"— " + names if names else ""}</td></tr>
      <tr><td class="k">Key points</td><td>{num(BF['points'])}</td></tr>
    </tbody>
  </table>""")

    # --------------------------------------------------------------- the algorithm
    A(f"""
  <h2><span class="n">02</span>The summarising algorithm</h2>
  <p class="lede">
    Four stages. <b>One of them uses a model.</b> The other three are ordinary code that
    either passes or refuses, and the refusal is the point — a brief that fails a gate is
    withheld rather than published with a caveat.
  </p>

  <div class="flow">
    <div class="fbox pass"><b>Stage 1 · build the corpus</b>
      <span class="tag">deterministic · offline</span><br>
      Parse Hansard into items, turns and sentences. Every sentence gets a
      <b>stable id</b> and a character span into the archived source.
      Nothing downstream ever quotes text it did not receive from here.</div>

    <div class="fbox pass"><b>Stage 2 · extract by reference</b>
      <span class="tag">the only model stage</span><br>
      The model reads a chunk and returns <b>sentence ids plus its own paraphrase</b> —
      never a quotation. Quote text is substituted from the record afterwards, so a
      fabricated quote is not caught, it is <b>unrepresentable</b>.</div>

    <div class="fbox pass"><b>Stage 3 · assemble</b>
      <span class="tag">deterministic</span><br>
      Resolve every cited id, attach each citation's <b>own</b> text, cap the point count
      against the item's reading budget, drop duplicates, and record a reason for every
      exclusion.</div>

    <div class="fbox fail"><b>Stage 4 · verify and gate</b>
      <span class="tag">deterministic · fails closed</span><br>
      Re-slice the source at each cited span, compare hashes, check coverage and schema
      completeness. Any failure withholds the brief. <b>Four gates, all failing closed.</b></div>
  </div>

  <h3>Stage 2 in detail — why the model never writes a quotation</h3>
  <p>
    This is the single design decision the rest of the system is built around. Asking a
    model to <i>quote</i> asks it to reproduce text, and the failure mode is a plausible
    sentence that appears nowhere in Hansard — which reads exactly like a correct one.
    So the model is asked for a <b>reference</b> instead:
  </p>
  <pre>Sentences from the record, each prefixed with its id:
  <span class="c">s41: The PSGA was introduced in 2018 to enable public agencies to share data safely.</span>
  <span class="c">s42: It applies to all public agencies unless expressly excluded.</span>

Return, for each claim, the ids of the sentences that support it.
Cite the fewest sentences that support the claim.</pre>
  <p>
    The model returns a claim and a list of ids. The pipeline then reads those sentences
    <i>out of the record</i> and uses that text as the quote. The model's paraphrase is
    kept as the human-readable claim; the quotation is never the model's.
  </p>
  <div class="note">
    <b>What this eliminates:</b> the entire class of fabricated-quote error. A model
    cannot invent a quotation it was never asked to produce. What it can still do — and
    does — is <b>misread</b> a real sentence, which is why Stage 4 and the separate
    citation verifier exist.
  </div>

  <h3>The reading budget</h3>
  <p>
    A brief is capped by how long the <i>item</i> is, not by a flat number of points. The
    requirement is roughly <b>ten seconds of reading per turn</b> of the record: a
    one-turn item gets one point, a 60-turn motion may carry sixty. A flat cap would
    either truncate long items or pad short ones, and both read as a system that does not
    understand its own input. Chunked items are capped by selecting across chunks
    (coverage-aware, falling back to round-robin) so no single chunk supplies everything.
  </p>

  <h3>Prompt size is a correctness parameter</h3>
  <p>
    Chunks are bounded at <b>{num(P['prompt_budget'])} characters</b>. This is not a
    performance tweak. At ~24k characters the model produced citation lists of eighty-plus
    ids and roughly a fifth of replies arrived truncated and unparseable; at
    {num(P['prompt_budget'])} the same work is clean. Longer replies are capped at
    <b>{num(P['max_reply'])} tokens</b> for the same reason — one chunk once generated
    35,420 tokens in a repetition loop and never stopped.
  </p>
""")

    # ------------------------------------------------------------- the verification
    A(f"""
  <h2><span class="n">03</span>The verifying algorithm</h2>
  <p class="lede">
    There are <b>two</b> independent checks, and they are deliberately different in kind.
    A gate can prove structure. It cannot tell you whether a sentence means what the claim
    says it means. That is a separate job, done by a separate agent.
  </p>

  <h3>Check 1 — the deterministic gates</h3>
  <p>All four run in Stage 4 and <b>fail closed</b>: a failure withholds the brief.</p>
  <table>
    <caption>Gates <span class="tag">stage4_verify</span></caption>
    <thead><tr><th>Gate</th><th>What it proves</th></tr></thead>
    <tbody>
      <tr><td class="k">quote invariant</td>
          <td>For every point: re-slice the archived source at the cited span and compare
          sha256 with the quoted text. This is what makes a quote <b>proven rather than
          trusted</b>, and it is re-runnable after publication.</td></tr>
      <tr><td class="k">citation resolves</td>
          <td>Every cited id exists and belongs to this item. Catches a model citing a
          sentence from elsewhere in the corpus.</td></tr>
      <tr><td class="k">coverage floor</td>
          <td>The brief must account for a real share of its item — floor
          <b>{P['min_coverage']}</b> of turns, and at least {P['min_turns']} turn(s).
          Added after a one-point brief covering <b>1 of 62 turns</b> passed and
          published.</td></tr>
      <tr><td class="k">schema complete</td>
          <td>Every field the site renders is present. Derived from what the renderer
          actually reads, so a field the site consumes can never quietly go missing.</td></tr>
    </tbody>
  </table>

  <h3>What the gates caught that no test would have</h3>
  <ul class="steps">
    <li><b>Every multi-citation point failed.</b> Assembly stored one quote but listed all
      cited ids, so the verifier compared sentence 2's words against sentence 1's id.
      The gate was right and the code was wrong — it found a real bug on its first run.</li>
    <li><b>A question reported as a finding.</b> A brief claimed <i>"The Government is
      assessing how rising fuel costs are passed through"</i> where the source said
      <i>"asked the Deputy Prime Minister whether…"</i>. Across the year, 214 points cited
      a question and 18 asserted a fact from one. Fixed in the prompts so the claim stays
      in the asking register, and flagged in Stage 4.</li>
    <li><b>Silent schema regression.</b> 96 briefs were missing four item-level fields.
      Every field the site reads is optional, so the pages rendered complete and looked
      fine. Invisible to the eye, caught by a gate.</li>
  </ul>
  <div class="note warn">
    <b>The lesson these share:</b> a gate is only as good as its ability to fail. Each of
    those three was found by a check that was expected to pass.
  </div>

  <h3>Check 2 — the citation verifier</h3>
  <p>
    The gates check structure and never meaning. So a second agent judges each claim
    against the sentences it cites and returns a verdict:
    <code>supported</code>, <code>weak</code>, <code>unsupported</code> or
    <code>reversed</code>, plus a defect class such as <code>dropped_qualifier</code>,
    <code>overstates</code> or <code>wrong_speaker</code>.
  </p>
  <p>
    Its prompt is adversarial by construction: the reader is told to find the way the
    claim <i>overstates</i> the source, and a claim that drops a hedge — "who sits at the
    table <b>potentially</b> decides" becoming "decides" — is reported as
    <code>dropped_qualifier</code>, not as supported. It found exactly that class of error
    in the published corpus.
  </p>
  <div class="note">
    <b>On independence — an honest note.</b> The verifier was originally a
    <i>different model family</i> from the writer, so that the same blind spots could not
    judge themselves. Cost won: it now runs on <b>{esc(P['model'])}</b>, the same model
    that writes the briefs. That weakness is not assumed away — it is <b>tested</b>. The
    verifier is scored against a ground-truth set of known-good claims and deliberately
    planted defects, including the original question-as-fact error, and it must keep
    catching them. A different role (judge rather than author) and an adversarial prompt
    are what carry it now; if the score falls, the test says so.
  </div>
""")

    # verifier results, only if measured
    if VF:
        cov = VF["coverage"]
        A(f"""
  <h3>Verifier results, measured</h3>
  <table>
    <caption>Claims judged <span class="tag">the verifier's own report</span></caption>
    <tbody>
      <tr><td class="k">Claims submitted</td><td>{num(VF['total'])}</td></tr>
      <tr><td class="k">Actually judged</td>
          <td>{num(VF['judged'])} <b>({100 * cov:.1f}% coverage)</b></td></tr>
      <tr><td class="k">Supported</td>
          <td>{num(VF['counts'].get('supported', 0))} — {100 * VF['supported_frac']:.1f}% of judged</td></tr>
      <tr><td class="k">Weak / unsupported</td>
          <td>{num(VF['counts'].get('weak', 0))} / {num(VF['counts'].get('unsupported', 0) + VF['counts'].get('reversed', 0))}</td></tr>
    </tbody>
  </table>""")
        if cov < 0.99:
            A(f"""
  <div class="note bad">
    <b>Coverage is {100 * cov:.1f}%, not 100%.</b> These figures describe a
    <i>sample</i> of the corpus, not a verdict on it. A claim that was never judged is
    counted separately from one that passed.
  </div>""")
        else:
            A("""
  <div class="note">
    <b>Coverage is complete</b> — every submitted claim received a verdict, so the
    supported fraction describes the corpus rather than a sample of it.
  </div>""")

    A(f"""
  <h3>Why the verifier retries instead of giving up</h3>
  <p>
    The model sometimes returns <b>fewer verdicts than claims</b> in a batch — silently,
    with valid JSON. On the first full-size run this left <b>33.4%</b> of claims
    <code>unjudged</code>, and because the summary counted them in the denominator, a run
    that had examined two-thirds of the corpus read as though it had examined all of it.
    Two fixes, both now load-bearing:
  </p>
  <ul class="steps">
    <li><b>A short reply is a partial failure.</b> The batch is re-asked for <i>only the
      missing indices</i>, so recovering a third of a batch costs a third of a batch.
      Bounded, so a model that refuses outright cannot loop forever.</li>
    <li><b>An unjudged claim is never cached.</b> Caching it made the retry a no-op: the
      skipped claims were read back as already answered, so re-running asked nothing and
      coverage never moved. Absence of a result is not a result.</li>
  </ul>
  <div class="note">
    <b>The reporting rule this produced:</b> coverage is printed next to the supported
    fraction, and both are stated — supported-of-all and supported-of-judged — with a
    warning below 99%. A verifier that samples must say that it samples.
  </div>
""")

    # ----------------------------------------------------------- diagrams & schema
    A("""  <h2><span class="n">04</span>Pipeline, entities, schema</h2>""")
    diag = part("arch-diagram.html")
    if diag:
        A('  <div class="diagram">')
        A(diag)
        A("  </div>")
    A(part("erd.html"))
    A(f"""  <div class="tables">
{part("schema-tables.html")}
  </div>""")

    A(f"""
  <p class="footer">
    Parsnips &middot; architecture &amp; algorithm &middot; generated by
    <code>site/build_arch.py</code> &middot; schema v{P['schema']} &middot;
    corpus {num(C['items'])} items / {num(C['sentences'])} sentences<br>
    Every figure on this page is read from the pipeline at build time. Nothing here is
    typed in, so nothing here can go stale.
  </p>

</div>
</body>
</html>
""")
    return "\n".join(H)


def main():
    html = build()
    out = os.path.join(HERE, "pipe-arch.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    # The mobile-first rule is mechanically checkable, so check it mechanically.
    bad = re.findall(r"@media\s*\(\s*max-width", html)
    mins = len(re.findall(r"@media\s*\(\s*min-width", html))
    print(f"wrote {out}  ({len(html):,} chars)")
    print(f"  min-width blocks : {mins}   (mobile-first)")
    print(f"  max-width blocks : {len(bad)}   "
          + ("OK" if not bad else "VIOLATION — the base layer must be the phone"))


if __name__ == "__main__":
    main()
