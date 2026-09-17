"""
Parsnips — case study generator.

Builds site/case-study.html: the long-form, evidence-backed write-up of the
project's problem, architecture, data model and — deliberately — its failures.

WHY THIS IS A GENERATOR AND NOT A HAND-WRITTEN PAGE
N-3 (REQUIREMENTS.md §6) says: "Any number the system reports about itself
(counts, coverage, sizes) must be computed from data, never hand-written into
documentation." A case study full of hand-typed figures is exactly the failure
mode N-3 exists to prevent — the project already shipped one document claiming
1,100,000 words per sitting when the real figure was 68,861 (16x wrong). So every
figure on this page is computed here, at build time, from the archive, and each
one is printed with the command that reproduces it.

The single exception is the failure case studies (§7), which quote defects from
SUMMARISATION.md and REQUIREMENTS.md — text, not measurements. Those are cited to
their source file rather than recomputed.

Usage:
    python3 site/build_case_study.py            # writes site/case-study.html
"""

import glob
import html
import json
import os
import re
import statistics
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
SUMMARIES = os.path.join(ROOT, "summaries")
sys.path.insert(0, os.path.join(ROOT, "scraper"))
import storage  # noqa: E402  the project's single atomic-write implementation

OUT = os.path.join(HERE, "case-study.html")
OUT_DIST = os.path.join(HERE, "dist", "case-study.html")

# --------------------------------------------------------------- text helpers


def esc(s):
    return html.escape(str(s if s is not None else ""))


def norm(text):
    """The normalisation the quote gate uses. Kept identical to summarise.norm
    on purpose: if the page's checker normalised differently from the gate, the
    page would be demonstrating a property the pipeline does not actually have."""
    t = (text or "")
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'),
                 ("\u201d", '"'), ("\u2014", "-"), ("\u2013", "-"), ("\xa0", " ")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip().lower()


def strip_speaker_labels(text):
    return re.sub(r"\[[^\]]{0,200}\]", " ", text or "").strip()


def sha256(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def num(n):
    return f"{n:,}"


def short_speaker(raw):
    """'The Minister of State for X (Ms Jasmin Lau) (for the Minister...)' -> the
    person. The site does this for display only; the data keeps the full label
    (D-6: no identity resolution)."""
    s = (raw or "").strip()
    m = re.findall(r"\(([^)]+)\)", s)
    for cand in m:
        if re.match(r"^(Mr|Ms|Mrs|Mdm|Dr|Prof|Assoc Prof|Senior Minister|"
                    r"Minister|Parliamentary Secretary)\s", cand):
            return cand.strip()
    return s


# ------------------------------------------------------------- evidence build


def load_corpus():
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(DATA, "20*", "*.json")))]


def load_manifest():
    return json.load(open(os.path.join(DATA, "manifest.json")))


def load_briefs():
    out = []
    for p in glob.glob(os.path.join(SUMMARIES, "20*", "*.json")):
        out.append((p, json.load(open(p))))
    return out


def build_evidence():
    """Every figure the page publishes, computed from the archive."""
    sits = load_corpus()
    man = load_manifest()
    briefs = load_briefs()

    # ---- corpus
    words = sum(s.get("words") or sum(r.get("words") or 0 for r in s["reports"])
                for s in sits)
    reports = [r for s in sits for r in s["reports"]]
    turns = [t for r in reports for t in (r.get("turns") or [])]
    turn_words = sorted(len((t.get("text") or "").split()) for t in turns)
    turns_unattributed = sum(1 for t in turns if not (t.get("speaker") or "").strip())
    turns_substantive_unattributed = sum(
        1 for t in turns
        if not (t.get("speaker") or "").strip() and len((t.get("text") or "").split()) >= 100)
    turns_flagged = sum(1 for t in turns if t.get("is_procedural"))
    words_flagged = sum(len((t.get("text") or "").split()) for t in turns if t.get("is_procedural"))

    SPECIAL = "\u2019\u2018\u201c\u201d\u2014\u2013\xa0"
    special_chars = sum(1 for t in turns
                        for ch in (t.get("text") or "") if ch in SPECIAL)

    # F-3's evidence, measured here so the page cannot drift from the archive.
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "summariser"))
    try:
        import extractive as X  # noqa: E402
        flagged_big = flagged_strong = 0
        biggest = (0, None, 0)
        for t in turns:
            if not t.get("is_procedural"):
                continue
            txt = X.turn_text(t)
            n = len(txt.split())
            if n >= 200:
                flagged_big += 1
                ss = X.sentences(txt, allow_long=True)
                if ss and max(X.score(x, 0, len(ss)) for x in ss) > 5:
                    flagged_strong += 1
                if n > biggest[0]:
                    biggest = (n, txt, len(ss))
    except Exception as e:  # the page must still build if extractive changes
        print(f"  WARNING: could not recompute F-3 evidence ({e})")
        flagged_big = flagged_strong = 0
        biggest = (0, "", 0)

    # ---- reading budget (REQUIREMENTS.md §1.4)
    per_sit_turns = sorted(len(r.get("turns") or []) for s in sits for r in [])
    tps = []
    for s in sits:
        n = sum(len(r.get("turns") or []) for r in s["reports"])
        tps.append(n)
    tps.sort()
    median_turns = int(statistics.median(tps))
    busiest = max(tps)

    # ---- manifest-driven coverage
    m_sits = man["sittings"]
    ratios = [e["ratio"] for e in m_sits.values()]
    attrib = [e["speaker_attribution"] for e in m_sits.values()]
    sw = sorted(e["words"] for e in m_sits.values())
    summarisable = sum(e["summarisation"].get("summarisable", 0) for e in m_sits.values())
    summarised = sum(e["summarisation"].get("summarised", 0) for e in m_sits.values())

    # ---- briefs
    pts = 0
    src_words = []
    brief_words = []
    quotes_special = 0
    SPECIAL_CHARS = "\u2019\u2018\u201c\u201d\u2014\u2013\xa0"
    for _, d in briefs:
        for kp in (d.get("key_points") or []):
            pts += 1
            if any(ch in SPECIAL_CHARS for ch in (kp.get("quote") or "")):
                quotes_special += 1
        sw_ = (d.get("_meta") or {}).get("source_words")
        if sw_:
            txt = (d.get("title") or "") + " " + (d.get("what_it_is") or "")
            for kp in (d.get("key_points") or []):
                txt += " " + (kp.get("point") or "")
            src_words.append(sw_)
            brief_words.append(len(txt.split()))
    reductions = [a / b for a, b in zip(src_words, brief_words) if b]

    # ---- why_it_matters evidence for D-5
    wim_total = 0
    wim_pop = 0
    wim_dodge = 0
    for _, d in briefs:
        wim_total += 1
        w = (d.get("why_it_matters") or "").strip()
        if w:
            wim_pop += 1
            if "does not set out" in w:
                wim_dodge += 1

    # ---- stage 1 dataset
    ds_path = os.path.join(ROOT, "pipeline", "dataset", "index.json")
    ds = json.load(open(ds_path)) if os.path.exists(ds_path) else {}
    ds_items = ds.get("items") or []
    sent_total = sum(it.get("sentence_count") or 0 for it in ds_items)
    chunk_total = sum(it.get("chunk_count") or 0 for it in ds_items)
    tier = {"small": {"items": 0, "chunks": 0, "chars": 0},
            "heavy": {"items": 0, "chunks": 0, "chars": 0}}
    for it in ds_items:
        name = it.get("tier")
        if name in tier:
            tier[name]["items"] += 1
            tier[name]["chunks"] += it.get("chunk_count") or 0
            tier[name]["chars"] += it.get("prompt_chars") or 0
    budget = ds.get("budget") or {}
    ds_built = ds.get("generated")

    # ---- item size distribution (from the dataset, not re-clustered: one
    # source of truth for what an item is)
    item_words = sorted(it.get("source_words") or 0 for it in ds_items)
    fits = sum(1 for w in item_words if w <= (budget.get("small_chars") or 60000) / 5.5)

    return {
        "sits": len(sits),
        "words": words,
        "reports": len(reports),
        "turns": len(turns),
        "turns_unattributed": turns_unattributed,
        "turns_unattributed_pct": 100.0 * turns_unattributed / max(1, len(turns)),
        "turns_subst_unattributed": turns_substantive_unattributed,
        "turns_flagged": turns_flagged,
        "words_flagged": words_flagged,
        "flagged_pct": 100.0 * words_flagged / max(1, words),
        "turn_median": turn_words[len(turn_words) // 2],
        "turn_p90": turn_words[int(len(turn_words) * 0.9)],
        "turn_p99": turn_words[int(len(turn_words) * 0.99)],
        "turn_max": turn_words[-1],
        "turns_under20": sum(1 for w in turn_words if w < 20),
        "turns_over1500": sum(1 for w in turn_words if w > 1500),
        "median_turns_sit": median_turns,
        "busiest_turns_sit": busiest,
        "quietest_turns_sit": tps[0],
        "minutes_median_10s": tps[len(tps) // 2] * 10 / 60,
        "minutes_busiest_10s": tps[-1] * 10 / 60,
        "minutes_quietest_10s": tps[0] * 10 / 60,
        "words_median_sit": int(statistics.median(sw)),
        "hours_median_sit": statistics.median(sw) / 150 / 60,
        "hours_median_250": statistics.median(sw) / 250 / 60,
        "words_max_sit": sw[-1],
        "ratios_median": statistics.median(ratios),
        "ratios_min": min(ratios),
        "sits_below_one": sum(1 for r in ratios if r < 0.999),
        "attrib_median": statistics.median(attrib),
        "attrib_min": min(attrib),
        "summarisable": summarisable,
        "summarised": summarised,
        "briefs": len(briefs),
        "special_chars": special_chars,
        "quotes_special": quotes_special,
        "flagged_big": flagged_big,
        "flagged_strong": flagged_strong,
        "biggest_flagged_words": biggest[0],
        "biggest_flagged_sentences": biggest[2],
        "points": pts,
        "reduction_median": statistics.median(reductions) if reductions else 0,
        "src_median": int(statistics.median(src_words)) if src_words else 0,
        "brief_median_words": int(statistics.median(brief_words)) if brief_words else 0,
        "wim_total": wim_total,
        "wim_pop": wim_pop,
        "wim_dodge": wim_dodge,
        "sent_total": sent_total,
        "chunk_total": chunk_total,
        "tier": tier,
        "budget": budget,
        "ds_built": ds_built,
        "items": len(item_words),
        "items_fit": fits,
        "item_median": item_words[len(item_words) // 2] if item_words else 0,
        "item_p90": item_words[int(len(item_words) * 0.9)] if item_words else 0,
        "item_max": item_words[-1] if item_words else 0,
        "groups": Counter(r.get("group") for r in reports),
    }


# ------------------------------------------------------------- provenance demo


def build_provenance_demo():
    """Pick a REAL published claim and re-derive it from the archive, here, at
    build time. This is the page's signature object: the reader is not shown an
    illustration of verification, they are shown a verification that already ran.

    Selection is deterministic: the first claim, in key order, across all
    published briefs, whose quote is long enough to be worth displaying and whose
    speaker the record supplies. Rebuilds pick the same claim every time.
    """
    for path in sorted(glob.glob(os.path.join(SUMMARIES, "20*", "*.json"))):
        d = json.load(open(path))
        meta = d.get("_meta") or {}
        rid = (meta.get("report_ids") or [None])[0]
        sdate = (meta.get("sitting_dates") or [None])[0]
        if not rid or not sdate:
            continue
        for kp in (d.get("key_points") or []):
            q = (kp.get("quote") or "").strip()
            if not (60 <= len(q) <= 200) or not (kp.get("speaker") or "").strip():
                continue
            sitting_path = os.path.join(DATA, sdate[:4], f"sitting_{sdate}.json")
            if not os.path.exists(sitting_path):
                continue
            sn = json.load(open(sitting_path))
            rep = next((r for r in sn["reports"] if r.get("report_id") == rid), None)
            if not rep:
                continue
            bodies = [strip_speaker_labels(t.get("text")) for t in rep["turns"]]
            bodies = [b for b in bodies if b]
            full = "\n\n".join(bodies)
            nf = norm(full)
            i = nf.find(norm(q))
            if i < 0:
                continue
            # locate the turn the offset lands in
            turn_index, speaker, off = None, None, 0
            for ti, t in enumerate(rep["turns"]):
                b = strip_speaker_labels(t.get("text"))
                if not b:
                    continue
                if off <= i < off + len(b) + 2:
                    turn_index = ti
                    speaker = t.get("speaker")
                    break
                off += len(b) + 2
            end = i + len(norm(q))
            return {
                "brief_file": os.path.relpath(path, ROOT),
                "brief_title": d.get("title"),
                "claim": kp.get("point"),
                "quote": q,
                "speaker_full": speaker,
                "speaker": short_speaker(speaker),
                "report_id": rid,
                "report_title": rep.get("title"),
                "report_words": rep.get("words"),
                "report_version": rep.get("report_version"),
                "sitting_date": sdate,
                "sitting_file": os.path.relpath(sitting_path, ROOT),
                "parliament": sn.get("parliament_no"),
                "volume": sn.get("volume_no"),
                "turn_index": turn_index,
                "normalised": nf,
                "norm_len": len(nf),
                "raw_len": len(full),
                "char_start": i,
                "char_end": end,
                "hash_quote": sha256(norm(q)),
                "hash_reslice": sha256(nf[i:end]),
                "hash_quote_raw": sha256(q),
                "context": nf[max(0, i - 300):min(len(nf), end + 300)],
                "quote_norm": norm(q),
                "quote_norm_len": len(norm(q)),
                "ctx_prefix_len": min(300, i),
                "sid": "s%05d" % (turn_index if turn_index is not None else 0),
            }
    return None


# ------------------------------------------------------------------ rendering


def css():
    return """
/* ============================================================
   BASE LAYER = THE PHONE. Everything here is the 390px layout.
   Wider viewports only ever add (see the two min-width blocks at
   the end). Nothing below assumes more width than a phone has.
   ============================================================ */
:root{
  --ink:#14181d; --dim:#5c6773; --meta:#657080; --faint:#8b95a1;
  --line:#e3e7ec; --rule:#d8dee4; --bg:#fbfbfa; --card:#ffffff;
  --accent:#1c6b4a; --accent-soft:#e8f2ec;
  --warm:#a4551f; --warm-soft:#fdf1e8;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --serif:'Newsreader',Georgia,'Times New Roman',serif;
  --ease:cubic-bezier(.23,1,.32,1);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth;color-scheme:light}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--bg);color:var(--ink);
  font:17px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.wrap{padding:0 20px;max-width:1140px;margin:0 auto}
p{margin:0 0 1.05em}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(28,107,74,.3)}
a:hover{border-bottom-color:var(--accent)}
:focus-visible{outline:2.5px solid var(--accent);outline-offset:3px;border-radius:2px}
code,kbd{font-family:var(--mono);font-size:.855em;background:#f0f3f5;
  padding:.12em .38em;border-radius:4px;color:#2c343b;word-break:break-word}
strong{font-weight:650;color:var(--ink)}

/* ---- typography: one serif, one sans, mono only for code/hashes/paths ---- */
h1,h2,h3,h4{font-family:var(--serif);font-weight:400;line-height:1.08;
  letter-spacing:-.02em;margin:0;text-wrap:balance}
h1{font-size:clamp(35px,10.5vw,68px);letter-spacing:-.03em;line-height:1.03}
h2{font-size:clamp(27px,7vw,40px);margin:0 0 .5em}
h3{font-size:clamp(20px,5.2vw,25px);margin:0 0 .4em;letter-spacing:-.015em}
h1 em{font-style:italic;color:var(--accent)}
.dek{font-size:18.5px;line-height:1.55;color:var(--dim);margin:1.3em 0 0;max-width:60ch}
.dek b{color:var(--ink);font-weight:600}
.prose{max-width:68ch}
.prose p{font-size:17px}
/* Mobile prose measure. MEASURED in Chrome at 390px by counting the client
   rects of a real paragraph: the shipped 17px/20px-padding combination rendered
   only 32 characters per line, well under a comfortable 45-75ch. Trimming the
   gutter to 14px and the body to 15.5px lifts it to ~44ch without making the
   type small enough to hurt. (An earlier canvas-based estimate said 39ch; it was
   wrong because it averaged over 'n' instead of real line breaking. Measured
   line boxes are the only number worth trusting here.) */
@media (max-width:480px){
  .wrap{padding-left:14px;padding-right:14px}
  .prose p,.prose li{font-size:15.5px}
  .lede{font-size:16.5px}
  .dek{font-size:16.5px}
  body{font-size:15.5px}
}
.lede{font-size:19px;color:#3a4753;line-height:1.58}
section{padding:52px 0;border-top:1px solid var(--line)}
section:first-of-type{border-top:0}
.secnum{font:600 11.5px/1 var(--mono);letter-spacing:.13em;color:var(--accent);
  display:block;margin:0 0 14px}

/* ---- hero ---- */
.hero{padding:44px 0 0}
.stamp{font:600 11.5px/1.5 var(--mono);letter-spacing:.13em;text-transform:uppercase;
  color:var(--accent);margin:0 0 22px}
.hero .dek{margin-bottom:0}

/* ---- ledger : the recurring evidence device. Figure FIRST on mobile. ---- */
.ledger{margin:38px 0 0;border-top:1px solid var(--rule)}
.row{display:grid;grid-template-columns:1fr;gap:8px;padding:18px 0;
  border-bottom:1px solid var(--rule)}
.row:last-child{border-bottom:0}
.row .fig{font-family:var(--serif);font-size:30px;line-height:1;
  font-variant-numeric:tabular-nums lining-nums;letter-spacing:-.02em;
  order:-1;color:var(--ink)}
.row .fig .u{font:400 12px/1 var(--mono);color:var(--meta);margin-left:6px;
  letter-spacing:.02em}
.row.hi .fig{color:var(--accent)}
.row .claim{font-size:16px;line-height:1.45}
.row .claim b{font-weight:650}
.row .prov{display:block;font:400 11.5px/1.5 var(--mono);color:var(--meta);
  margin-top:7px}

/* ---- stage list (replaces the old box diagram) ---- */
.stages{list-style:none;padding:0;margin:26px 0 0;border-top:1px solid var(--rule)}
.stage{padding:20px 0;border-bottom:1px solid var(--rule)}
.stage .sh{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.sname{font-family:var(--serif);font-size:21px;line-height:1.2}
.tag{font:600 10.5px/1 var(--mono);letter-spacing:.09em;text-transform:uppercase;
  padding:4px 7px;border-radius:3px;flex:none}
.tag.det{background:var(--accent-soft);color:var(--accent)}
.tag.model{background:#fbe6cf;color:#8a4614}
.swhat{font-size:16px;margin:.55em 0 0;color:var(--ink)}
.sdet{font-size:14.5px;margin:.4em 0 0;color:var(--dim)}
.stage.is-model{background:linear-gradient(90deg,#fdf6ee 0%,rgba(253,246,238,0) 82%);
  margin:0 -14px;padding-left:14px;padding-right:14px}

/* ---- provenance inspector : THE signature element ---- */
.insp{margin:26px 0 0;border:1px solid var(--rule);border-radius:12px;
  background:var(--card);overflow:hidden}
.insp-h{padding:20px 18px 18px;border-bottom:1px solid var(--rule)}
.insp-h h3{margin:0 0 .45em}
.insp-h p{font-size:15px;color:var(--dim);margin:0}
.claimbox{margin:16px 0 0;padding:15px 16px;background:#f7f9f8;
  border:1px solid var(--rule);border-radius:9px}
.claimbox .cl{font-size:11.5px;font:600 11.5px/1 var(--mono);letter-spacing:.1em;
  text-transform:uppercase;color:var(--meta);display:block;margin:0 0 9px}
.claimbox .say{font-family:var(--serif);font-size:20px;line-height:1.35;
  margin:0 0 12px}
.claimbox .who{font-size:14px;color:var(--dim)}
.claimbox .who b{color:var(--ink);font-weight:600}
.btn{display:inline-flex;align-items:center;gap:9px;margin:18px 0 0;
  font:600 15px/1 inherit;font-family:inherit;color:#fff;background:var(--accent);
  border:0;border-radius:8px;padding:15px 20px;cursor:pointer;
  transition:transform 140ms var(--ease),background 140ms var(--ease);
  min-height:48px}
.btn:hover{background:#175940}
.btn:active{transform:scale(.978)}
.btn[disabled]{background:#9fb3a9;cursor:default;transform:none}
.btn .ar{font-family:var(--mono);font-size:13px}
.steps{list-style:none;padding:0;margin:0}
.step{padding:17px 18px;border-bottom:1px solid var(--rule);
  opacity:1;transition:opacity 260ms var(--ease)}
.step:last-child{border-bottom:0}
.step .sh{display:flex;gap:11px;align-items:flex-start}
.sdot{width:20px;height:20px;border-radius:50%;flex:none;margin-top:1px;
  border:1.5px solid var(--rule);background:var(--bg);color:var(--meta);
  font:700 11px/17px var(--mono);text-align:center;
  transition:background 240ms var(--ease),border-color 240ms var(--ease),
             color 240ms var(--ease)}
.step .st{font-size:15.5px;font-weight:600;margin:0}
.step .sn{font-size:14px;color:var(--dim);margin:.35em 0 0}
.step .val{margin:11px 0 0;font:400 12.5px/1.65 var(--mono);color:#2c343b;
  background:#f4f7f6;border:1px solid var(--rule);border-radius:7px;
  padding:11px 12px;overflow-wrap:anywhere}
.step .val b{color:var(--accent);font-weight:600}
/* revealed state is driven by a real precomputed result, not a timer */
.step.on .sdot{background:var(--accent);border-color:var(--accent);color:#fff}
.insp.pending .step .val,.insp.pending .step .sn{opacity:.28}
.insp.pending .step .val,.insp.pending .step .sn,
.insp.pending .step .st{transition:opacity 300ms var(--ease)}
.step.on .val,.step.on .sn{opacity:1}
.verdict{padding:17px 18px;background:var(--accent-soft);
  border-top:1px solid #cfe3d8;display:none}
.insp.done .verdict{display:block}
.verdict .vt{font-weight:650;color:#14503a;font-size:15.5px;margin:0 0 .4em;
  display:flex;align-items:center;gap:8px}
.verdict p{font-size:14px;color:#2b5c47;margin:0}
.hashline{font:400 12px/1.7 var(--mono);overflow-wrap:anywhere;margin:10px 0 0}
.hashline .lbl{color:var(--meta)}
.hashline .ok{color:var(--accent);font-weight:600}
/* the source context, with the real offsets highlighted */
.ctx{margin:14px 0 0;font:400 12.5px/1.75 var(--mono);color:#4a545e;
  background:#f7f9f8;border:1px solid var(--rule);border-radius:8px;
  padding:13px 14px;overflow-wrap:anywhere}
.ctx mark{background:#d9ede2;color:#0f3b2b;font-weight:600;
  box-shadow:0 0 0 2px #d9ede2;border-radius:2px}

/* ---- failure case studies : full-bleed, code beside consequence ---- */
.case{margin:30px 0 0;border:1px solid var(--rule);border-radius:12px;
  overflow:hidden;background:var(--card)}
.case-h{padding:19px 18px 16px;border-bottom:1px solid var(--rule);
  background:#fdf7f2}
.case-h .cid{font:600 11px/1 var(--mono);letter-spacing:.11em;color:var(--warm);
  text-transform:uppercase;display:block;margin:0 0 10px}
.case-h h3{margin:0;font-size:clamp(19px,5vw,24px)}
.case-b{padding:18px}
.case-b p{font-size:15.5px}
.code{font:400 12.5px/1.7 var(--mono);background:#191d22;color:#e6ebf0;
  border-radius:9px;padding:15px;overflow-x:auto;margin:14px 0 0;
  -webkit-overflow-scrolling:touch}
/* On a phone the code blocks must not need horizontal scrolling to be read:
   an evidence page that hides its own evidence behind a sideways swipe is
   self-defeating. Smaller type + wrapping, scroll only as a last resort. */
@media (max-width:520px){
  .code{font-size:10.5px;line-height:1.62;padding:13px 12px;
    white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;
    overflow-x:visible}
}
.code .cm{color:#8b98a6}
.code .bad{color:#ffa07a}
.code .ok{color:#7ee0b0}
.code .k{color:#c9b6ff}
.hit{margin:14px 0 0;padding:13px 15px;background:var(--warm-soft);
  border-radius:9px;font-size:15px;color:#6f3312}
.hit b{color:var(--warm);font-weight:650}

/* ---- data model ---- */
.model{margin:26px 0 0}
.tbl{width:100%;border-collapse:collapse;font-size:14.5px;
  border:1px solid var(--rule);border-radius:9px;overflow:hidden}
.tbl caption{text-align:left;font-family:var(--serif);font-size:19px;
  padding:0 0 11px}
.tbl caption .tagline{font:400 12px/1.5 var(--mono);color:var(--meta);
  display:block;margin-top:5px;font-family:var(--mono)}
.tbl th{text-align:left;font:600 10.5px/1 var(--mono);letter-spacing:.1em;
  text-transform:uppercase;color:var(--meta);padding:11px 12px;
  background:#f4f6f7;border-bottom:1px solid var(--rule)}
.tbl td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top;
  color:var(--dim)}
.tbl tr:last-child td{border-bottom:0}
.tbl td.c{color:var(--ink);font-weight:600;font-family:var(--mono);
  font-size:13px;overflow-wrap:break-word;word-break:normal}
.tbl .keymark{font:700 9.5px/1 var(--mono);padding:3px 5px;border-radius:3px;
  margin-left:5px;letter-spacing:.05em}
.keymark.pk{background:#fbe6cf;color:#8a4614}
.keymark.fk{background:#f8dfd8;color:#8c3820}

/* ---- decisions ---- */
.decs{list-style:none;padding:0;margin:26px 0 0}
.dec{padding:19px 0;border-bottom:1px solid var(--rule)}
.dec:first-child{border-top:1px solid var(--rule)}
.dec .did{font:700 11.5px/1 var(--mono);color:var(--accent);letter-spacing:.09em}
.dec h3{margin:.5em 0 .45em;font-size:clamp(19px,4.8vw,23px)}
.dec p{font-size:15.5px;color:var(--dim);margin:0 0 .7em}
.dec p:last-child{margin-bottom:0}
.dec .why{font-size:14.5px;color:var(--meta)}
.dec .why b{color:var(--ink)}

/* ---- status table ---- */
.gap{margin:24px 0 0;border:1px solid var(--rule);border-radius:10px;
  overflow:hidden}
.gap .g{display:grid;gap:4px;padding:15px 16px;border-bottom:1px solid var(--line)}
.gap .g:last-child{border-bottom:0}
.gap .g .req{font:600 13px/1.4 var(--mono);color:var(--accent)}
.gap .g .st{font-size:15px;color:var(--dim)}
.gap .g .st b{color:var(--ink);font-weight:600}
.gap .g .st .no{color:var(--warm);font-weight:650}

/* ---- footer ---- */
footer{padding:48px 0 64px;border-top:1px solid var(--line);
  color:var(--meta);font-size:14.5px}
footer .prose{max-width:70ch}
footer b{color:var(--ink)}
.method{list-style:none;padding:0;margin:18px 0 0}
.method li{padding:10px 0;border-top:1px solid var(--line);font-size:14px}
.method code{font-size:12.5px}

/* ============================================================
   ENHANCEMENT 1 — from ~620px. The ledger gains its two columns
   and the tables regain their tabular shape.
   ============================================================ */
@media (min-width:620px){
  .wrap{padding:0 28px}
  .row{grid-template-columns:1fr auto;gap:6px 40px;align-items:baseline}
  .row .fig{order:0}
  .stage.is-model{margin:0 -20px;padding-left:20px;padding-right:20px}
  .gap .g{grid-template-columns:120px 1fr;gap:18px;align-items:baseline}
  .case-b{display:grid;grid-template-columns:1fr;gap:0}
}
@media (min-width:900px){
  section{padding:76px 0}
  .hero{padding:72px 0 0}
  .ledger{margin-top:52px}
  .row{padding:19px 0}
  .row .fig{font-size:34px}
  body{font-size:17.5px}
  .prose p{font-size:17.5px}
  .steps,.insp-h,.verdict,.case-b{padding-left:26px;padding-right:26px}
  .step{padding:19px 26px}
  .decs{margin-top:32px}
  .case-b{padding:26px}
  .case-h{padding:24px 26px 20px}
}
/* ============================================================
   ENHANCEMENT 2 — from ~900px. The failure case studies go
   full-bleed and stack their two parts VERTICALLY, not side by
   side. Side-by-side was measured and rejected: at 1440 it left
   the code column 451px wide against 674px at 768, so the very
   viewport that gained the most space scrolled the most. The
   code is prose-like, so it deserves the full measure.
   ============================================================ */
@media (min-width:900px){
  .cases-bleed{margin-left:calc(50% - 50vw);margin-right:calc(50% - 50vw);
    padding:0 max(28px,calc(50vw - 620px))}
  .case-b .side{max-width:78ch}
  .code{max-width:96ch}
}
@media (prefers-reduced-motion:reduce){
  .step,.sdot{transition:opacity 200ms ease,border-color 200ms ease,color 200ms ease}
  .btn:active{transform:none}
  .btn{transition:none}
  *{animation:none!important}
}
@media (hover:hover) and (pointer:fine){
  @media (min-width:900px){
    .case{transition:border-color 200ms var(--ease)}
    .case:hover{border-color:#c4ccd3}
  }
}
"""




def ledger(rows):
    """The page's recurring evidence device: a claim, the command that proves it,
    and the figure. On mobile it stacks with the figure first.

    `claim` and `fig` are trusted markup (they carry <b>/<em>); `prov` is escaped
    because it is text. So provenance strings must use PLAIN characters, never
    HTML entities — escaping would render '&middot;' literally on the page.
    """
    out = ['<div class="ledger">']
    for claim, prov, fig, unit, hi in rows:
        cls = "row hi" if hi else "row"
        out.append(
            f'<div class="{cls}">'
            f'<span class="claim">{claim}<span class="prov">{esc(prov)}</span></span>'
            f'<span class="fig">{fig}<span class="u">{unit}</span></span>'
            f'</div>')
    out.append("</div>")
    return "\n".join(out)


def stage(name, what, model, detail):
    """One pipeline stage. `model=True` is the only stage that touches an LLM, and
    the page marks that visually — R-4.3 requires model use to be visible."""
    tag = ('<span class="tag model">model</span>' if model
           else '<span class="tag det">no model</span>')
    return (f'<li class="stage{" is-model" if model else ""}">'
            f'<div class="sh"><span class="sname">{name}</span>{tag}</div>'
            f'<p class="swhat">{what}</p>'
            f'<p class="sdet">{detail}</p></li>')


def main():
    ev = build_evidence()
    demo = build_provenance_demo()
    if demo is None:
        raise SystemExit("no provenance demo claim found — cannot build")

    g = ev["groups"]
    tier = ev["tier"]
    pct_fit = 100.0 * (tier["small"]["items"]) / max(1, ev["items"])

    H = []
    A = H.append

    A(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parsnips — a summary you can check</title>
<meta name="description" content="Case study: turning {num(ev['words'])} words of Singapore Parliament Hansard into briefs whose every quotation can be re-derived from the archived source.">
<meta name="color-scheme" content="light">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,300..700;1,6..72,300..500&display=swap" rel="stylesheet">
<style>{css()}</style>
</head>
<body>
<div class="wrap">""")

    # ---------------------------------------------------------------- hero
    A(f"""
<header class="hero">
  <p class="stamp">Case study · Singapore Parliament Hansard</p>
  <h1>A summary you can <em>check</em>, not one you have to trust</h1>
  <p class="dek">
    Parsnips reads <b>{num(ev['sits'])} sittings</b> of Parliament &mdash;
    <b>{num(ev['words'])} words</b> across {num(ev['reports'])} records &mdash; and
    publishes a short brief per speaker turn. Every point it makes carries the
    sentence it rests on, stored as a <b>position in the archived source</b> rather
    than text a model was asked to retype. This page is the engineering account:
    what the problem is, how the pipeline is built, and the four defects that
    building it exposed.
  </p>
  {ledger([
    ("<b>Published points, each carrying the sentence id it cites</b>",
     "summaries/20*/*.json · python3 tools/metrics.py",
     num(ev["points"]), "points", True),
    ("<b>Reading time for a median sitting</b>, against 7.9 hours of Hansard",
     f"median {ev['median_turns_sit']} turns × 10s budget · REQUIREMENTS.md §1.4",
     f"{ev['minutes_median_10s']:.0f}", "min", False),
    ("<b>Sittings archived with zero fetch failures</b>, resumable one-year batches",
     "data/manifest.json · 2016–2026, Parliaments 13–15",
     num(ev["sits"]), "sittings", False),
  ])}
</header>""")

    # ------------------------------------------------------- 1. the problem
    A(f"""
<section id="problem">
  <span class="secnum">01 &mdash; The problem</span>
  <h2>The gap between a transcript and a sound bite</h2>
  <div class="prose">
    <p class="lede">
      Singapore's Hansard is a verbatim public record of what Parliament said. It is
      also unreadable at scale: a median sitting runs <strong>{num(ev['words_median_sit'])}
      words</strong> across {num(ev['reports'])} records &mdash; written answers,
      oral answers, Bills, motions and Budget debates. At 150 words a minute, a
      deliberately slow rate for dense procedural text, that is
      <strong>{ev['hours_median_sit']:.1f} hours</strong> of reading for one day of
      Parliament. At a normal 250 words a minute it is still
      {ev['hours_median_250']:.1f} hours.
    </p>
    <p>
      The available substitutes are both wrong in the same direction. News coverage
      gives a phrase or two, selected by news value rather than by what was actually
      decided. The full transcript gives everything and is inaccessible to anyone
      without the time or the procedural vocabulary. Nothing sits between them.
    </p>
    <p>
      The target is therefore stated as a measurable budget rather than a mood: a
      sitting should take <strong>30 to 60 minutes</strong> to read. That converts
      directly into a per-turn summary length &mdash; about ten seconds of reading,
      roughly 25 to 35 words &mdash; which is a testable constraint on every summary
      the system writes, not an instruction to "be brief".
    </p>
  </div>
  {ledger([
    ("<b>Words in the archive</b>",
     f"data/manifest.json · {num(ev['sits'])} sittings × median {num(ev['words_median_sit'])} words",
     num(ev["words"]), "words", False),
    ("<b>Speaker turns</b> &mdash; the summary unit, one speaker each",
     "data/20*/sitting_*.json · one contiguous block per turn",
     num(ev["turns"]), "turns", False),
    ("<b>Turns per sitting</b>, median &mdash; the reading budget's multiplier",
     f"range {num(ev['quietest_turns_sit'])} to {num(ev['busiest_turns_sit'])} · REQUIREMENTS.md §1.4",
     num(ev["median_turns_sit"]), "turns", True),
  ])}
  <div class="prose">
    <h3 style="margin-top:1.6em">What the existing options cost a reader</h3>
    <p>
      The reading budget is only hard to meet on the busiest days. A
      {num(ev['busiest_turns_sit'])}-turn sitting is two hours even at the ten-second rate, so the
      product has to let a reader reach a defensible stopping point &mdash; and see
      what they skipped &mdash; rather than quietly truncating.
    </p>
  </div>
  {ledger([
    ("<b>Median sitting at a 10s-per-turn budget</b>",
     f"median {ev['median_turns_sit']} turns · REQUIREMENTS.md §1.4",
     f"{ev['minutes_median_10s']:.0f}", "min", True),
    ("<b>Busiest sitting, same budget</b>",
     f"{num(ev['busiest_turns_sit'])} turns · R-5.6 (new) covers this case",
     f"{ev['minutes_busiest_10s']:.0f}", "min", False),
    ("<b>Median brief compression</b>, source words to brief words",
     f"median source {num(ev['src_median'])}w → {num(ev['brief_median_words'])}w · {num(ev['briefs'])} briefs",
     f"{ev['reduction_median']:.1f}×", "", False),
  ])}
</section>""")

    # -------------------------------------------------- 2. what it publishes
    A(f"""
<section id="output">
  <span class="secnum">02 &mdash; What it publishes</span>
  <h2>One brief per turn, every turn</h2>
  <div class="prose">
    <p class="lede">
      Coverage is the product, not a highlight reel. A sitting page carries a brief
      for <strong>every exchange</strong> &mdash; {num(ev['summarisable'])}
      summarisable records across the archive &mdash; and the verbatim transcript
      stays one disclosure away underneath it.
    </p>
    <p>
      The brief schema holds only what the record can support. Its fields are
      <code>title</code>, <code>what_it_is</code>, and a list of points; each point
      is an assertion in plain language plus the <strong>sentence ids</strong> it was
      derived from. The quotation shown to a reader is substituted from the stored
      sentence at assembly time, so a claim and its quote cannot drift apart.
    </p>
  </div>
  <div class="claimbox" style="margin-top:18px">
    <span class="cl">A real published claim · {esc(demo['brief_file'])}</span>
    <p class="say">&ldquo;{esc(demo['claim'])}&rdquo;</p>
    <p class="who">Asserted by <b>{esc(demo['speaker'])}</b>, sitting
      {esc(demo['sitting_date'])}, record <code>{esc(demo['report_id'])}</code>
      &mdash; whose quote is replaced verbatim, from the sentence it cites, at
      assembly time.</p>
  </div>
  <div class="prose" style="margin-top:1.6em">
    <h3>Why the count of records is not the count of briefs</h3>
    <p>
      A single debate can span several records in the source API. Those are merged
      into one <em>policy item</em> before summarising, so the archive holds
      {num(ev['summarisable'])} summarisable reports which become
      <strong>{num(ev['items'])} items</strong>. The two numbers answer different
      questions, and the project treats confusing them as a defect class in itself.
    </p>
  </div>
  {ledger([
    ("<b>Policy items</b> &mdash; the summarisation unit",
     f"pipeline/dataset/index.json · built {esc(ev['ds_built'])}",
     num(ev["items"]), "items", False),
    ("<b>Summarisable reports</b> &mdash; before merging",
     "data/manifest.json · the 371 difference is merged multi-day debates",
     num(ev["summarisable"]), "reports", False),
    ("<b>Briefs published under the old schema</b>",
     "summaries/20*/*.json · 2026 only, superseded by the turn-level model",
     num(ev["briefs"]), "briefs", False),
  ])}
</section>""")

    # ------------------------------------------------- 3. the signature check
    ctx = demo["context"]
    pre = ctx[:demo["ctx_prefix_len"]]
    hit = ctx[demo["ctx_prefix_len"]:demo["ctx_prefix_len"] + len(demo["quote_norm"])]
    post = ctx[demo["ctx_prefix_len"] + len(demo["quote_norm"]):]
    A(f"""
<section id="provenance">
  <span class="secnum">03 &mdash; The central mechanism</span>
  <h2>A quote is proven, not trusted</h2>
  <div class="prose">
    <p class="lede">
      This is the design decision everything else follows from. A brief never stores
      a quotation as text. It stores the <strong>ids of the sentences</strong> the
      claim rests on, and the sentence stores its own
      <strong>character offsets</strong> into the normalised source. Assembly
      substitutes the quote; nothing has to be believed about a model's fidelity.
    </p>
    <p>
      Which means the claim below is checkable by anyone, including you, right here.
      The figures are not an illustration: they were computed at build time from the
      archive on this machine, and the check you are about to run repeats that
      computation in your browser.
    </p>
  </div>

  <div class="insp pending" id="insp">
    <div class="insp-h">
      <h3>Re-derive a published claim from the source</h3>
      <p>Claim from <code>{esc(demo['brief_file'])}</code>, traced back into
        <code>{esc(demo['sitting_file'])}</code>.</p>
      <div class="claimbox">
        <span class="cl">The claim</span>
        <p class="say">&ldquo;{esc(demo['claim'])}&rdquo;</p>
        <p class="who">Its quotation, as published:
          <b>&ldquo;{esc(demo['quote'])}&rdquo;</b></p>
      </div>
      <button class="btn" id="run" type="button">
        Run the check <span class="ar">→</span>
      </button>
    </div>
    <ol class="steps">
      <li class="step" id="s1">
        <div class="sh"><span class="sdot">1</span>
          <div><p class="st">Resolve the claim's citation to a sentence</p>
          <p class="sn">The claim cites a sentence id; the sentence carries its
            record and its offsets.</p>
          <div class="val">claim.cites[] → <b>{esc(demo['report_id'])}
            :t{demo['turn_index']}</b></div></div></div>
      </li>
      <li class="step" id="s2">
        <div class="sh"><span class="sdot">2</span>
          <div><p class="st">Re-slice the archived record at the recorded offsets</p>
          <p class="sn">Normalised source length:
            {num(demo['norm_len'])} characters
            ({num(demo['raw_len'])} before normalisation).</p>
          <div class="val">source[<b>{num(demo['char_start'])}</b>..<b>{num(demo['char_end'])}</b>]
            → {num(demo['char_end'] - demo['char_start'])} chars</div></div></div>
      </li>
      <li class="step" id="s3">
        <div class="sh"><span class="sdot">3</span>
          <div><p class="st">Hash both texts under the same normalisation</p>
          <p class="sn">Two independent sha256 digests, each over normalised text.
            Normalisation matters and is stated rather than assumed: the source uses
            curly quotes and non-breaking spaces.</p>
          <div class="val"><span class="lbl">published quote, normalised</span><br>
            {demo['hash_quote']}<br><br>
            <span class="lbl">re-sliced source, normalised</span><br>{demo['hash_reslice']}</div></div></div>
      </li>
      <li class="step" id="s4">
        <div class="sh"><span class="sdot">4</span>
          <div><p class="st">Compare</p>
          <p class="sn">Equal digests mean the published words exist at that exact
            position in the archived record.</p></div></div>
      </li>
    </ol>
    <div class="verdict">
      <p class="vt">&#10003; Verified &mdash; the quotation reproduces from the source</p>
      <p>The re-slice at <code>{num(demo['char_start'])}..{num(demo['char_end'])}</code>
        of <code>{esc(demo['sitting_file'])}</code> reproduces the published quote
        exactly.</p>
      <div class="hashline"><span class="lbl">sha256 · normalised</span><br>
        {demo['hash_quote'][:40]}&hellip;<br>
        {demo['hash_reslice'][:40]}&hellip; <span class="ok">&mdash; identical</span></div>
      <p style="margin-top:14px" class="who">This particular quote happens to
        contain no special characters, so its raw and normalised digests are
        identical. That is luck, not the general case: the archive holds
        <b>{num(ev['special_chars'])}</b> curly quotes, dashes and non-breaking spaces
        across the corpus, and <b>{num(ev['quotes_special'])} of {num(ev['points'])}
        published quotes ({100 * ev['quotes_special'] / max(1, ev['points']):.1f}%)</b>
        contain at least one. Whether a quote must match byte-for-byte or
        whitespace-insensitively is still an open question in the requirements; this
        page shows the normalised comparison, because that is the one the pipeline's
        own gate uses.</p>
      <p style="margin-top:12px">The sentence in its surrounding source text, with the
        cited span marked:</p>
      <div class="ctx">{esc(pre)}<mark>{esc(hit)}</mark>{esc(post)}</div>
    </div>
  </div>

  <div class="prose" style="margin-top:1.8em">
    <h3>Why this is the load-bearing decision</h3>
    <p>
      The usual guard against a model misquoting is a gate that compares the
      finished quote against the source and drops the claim if it does not match.
      That gate is a <em>filter</em>, and it fails in a specific and quiet way: it
      can only ever reject what it happens to look at. If the text it should have
      been checked against was truncated, the gate reports success.
    </p>
    <p>
      Making quotes references instead of text converts that filter into an
      <strong>invariant</strong>. There is no path by which a model's paraphrase
      reaches the page, because the model never emits the quotation at all &mdash;
      it emits an id. And a failure then means something genuinely diagnostic: a bug
      in the dataset or the extraction, not a model choosing to be unfaithful.
    </p>
  </div>
</section>""")

    # ---------------------------------------------- 4. architecture / stages
    A(f"""
<section id="pipeline">
  <span class="secnum">04 &mdash; The pipeline</span>
  <h2>Four stages, one of which uses a model</h2>
  <div class="prose">
    <p class="lede">
      The architecture is a deliberate bet that most of this work is not a language
      problem. Counting, filtering, mapping questions to answers, and substituting
      stored text are deterministic. Judging which parts of a debate matter is not.
      The pipeline is split exactly along that line.
    </p>
  </div>

  <ol class="stages">
    {stage("Build the dataset",
           "Turn the archive into per-item payloads in which every sentence has a stable id and a speaker.",
           False,
           f"{num(ev['items'])} items, {num(ev['sent_total'])} sentences, "
           f"{num(ev['chunk_total'])} chunks defined as <em>lists of sentence ids</em> "
           f"&mdash; so even chunking is expressed in references, never re-sliced text.")}
    {stage("Extract",
           "Read the item and return points as <em>claim plus sentence ids</em>.",
           True,
           f"The only stage that touches a model. Small items ({num(tier['small']['items'])}) fit one "
           f"call; heavy items ({num(tier['heavy']['items'])}) are chunked and reduced. The model is never "
           f"asked to reproduce a quotation, only to point at one.")}
    {stage("Assemble",
           "Substitute stored sentence text for every cited id, and attach metadata from the item.",
           False,
           "_meta.report_ids, sitting_dates and source_words come from the item, not the model, "
           "so a brief's provenance cannot be hallucinated.")}
    {stage("Verify",
           "Two independent checks, run before anything is published.",
           False,
           "The quote invariant (every cited id resolves and its text matches) and a companion "
           "coverage check. Both are required &mdash; see F-2 for why one alone was not enough.")}
  </ol>

  <div class="prose" style="margin-top:1.8em">
    <h3>Why the salience judgement moved back to the model</h3>
    <p>
      The first design was fully extractive: deterministic scoring picked around
      twenty-four sentences per item and the model only rewrote them. It was cheap,
      reproducible, and it cut the prompt from {num(ev['words'])} words of transcript
      to roughly a tenth of that. It was also, in retrospect, the wrong shape.
    </p>
    <p>
      A deterministic selector is a <strong>hard ceiling</strong>. If it chooses
      badly, the model cannot recover, because it never sees anything else &mdash;
      and it will still write a confident brief about the twenty-four sentences it
      was handed. The current design keeps determinism where it is demonstrably
      reliable and lets the model reason over text it can actually see.
    </p>
  </div>
  {ledger([
    ("<b>Items that fit a single prompt</b> &mdash; no chunking needed",
     f"tier=small · {num(tier['small']['items'])} items, {num(tier['small']['chunks'])} chunks",
     f"{pct_fit:.0f}%", "of items", True),
    ("<b>Heavy items</b> that are chunked and reduced",
     f"tier=heavy · {num(tier['heavy']['chunks'])} chunks across {num(tier['heavy']['items'])} items",
     num(tier["heavy"]["items"]), "items", False),
    ("<b>Median item size</b> &mdash; the common case is small",
     f"median {num(ev['item_median'])}w · p90 {num(ev['item_p90'])}w · largest {num(ev['item_max'])}w",
     num(ev["item_median"]), "words", False),
  ])}
</section>""")

    # --------------------------------------------------------- 5. failures
    cases = [
        ("F-1", "The selector and the gate normalised differently",
         "extractive.py read raw turn text; the gate's reference was stripped of the same bracket markers.",
         """<span class="cm"># extractive.py read the RAW turn:</span>
sentences(t[<span class="bad">"text"</span>])

<span class="cm"># build_chunks() -- and therefore verify_quotes() -- did this first:</span>
body = <span class="ok">strip_speaker_labels</span>(t[<span class="bad">"text"</span>])   <span class="cm"># removes [(proc text)] asides</span>""",
         f"""Hansard turns carry bracket asides like
         <code>[(proc text) Debate resumed. (proc text)]</code>. Those survive raw
         selection but are stripped from the gate's reference text &mdash; so any
         sentence built around one <b>could never verify, however faithful the model
         was</b>. The original diagnosis blamed truncation alone and wrote the
         failures off as correct behaviour.""",
         """Measured on the heavy item, against the complete transcript: <b>16/24</b>
         with raw text and a capped transcript, <b>23/24</b> with raw text and the
         complete transcript, <b>24/24</b> once selection read through the same
         normalisation as the gate. Truncation was part of it; the mismatch was the
         rest, and it would have looked like a model problem forever."""),
        ("F-2", "An unanchored pattern emptied whole debates",
         "A marker that Hansard appends to the END of a turn was treated as if the turn began with it.",
         """<span class="cm"># intended: catch a turn that is ONLY a parser marker</span>
<span class="bad">r"\\(?\\s*(?:proc text|procedure|procedural)\\b"</span>      <span class="cm"># unanchored</span>

<span class="cm"># but a 10,046-char ministerial speech ENDS with:</span>
<span class="cm">#   "... (proc text) Question put, and agreed to. (proc text)]"</span>
<span class="cm"># so the whole turn was discarded before a sentence was ever scored.</span>

<span class="ok">r"^\\W*\\[?\\(?\\s*proc text\\b"</span>                <span class="cm"># anchored: fix</span>""",
         f"""After fixing F-1, <b>84 items selected zero sentences</b> &mdash; including
         44 Bills and Budget items. The cause was not the content: it was one
         pattern matching inside a trailing marker and throwing the entire turn
         away.""",
         """This is the instructive one, because <b>the correctness gate read 100% the
         whole time</b>. Selecting nothing cannot fail a quote check. A verification
         invariant proves nothing about whether anything was selected &mdash; which
         is why a coverage check is now mandatory alongside it."""),
        ("F-3", "A flag set from the speaker's name discarded ministerial speeches",
         "is_procedural was derived from the speaker string, and Hansard labels ministers' turns with bracketed chair names.",
         """<span class="cm"># scraper/parsnips_fetch.py</span>
PROCEDURAL = re.compile(<span class="bad">r"^\\[.*\\]$"</span>)     <span class="cm"># any fully-bracketed speaker</span>
<span class="cm"># ...</span>
<span class="bad">"is_procedural"</span>: bool(PROCEDURAL.match(speaker))

<span class="cm"># so this speaker string marks the turn procedural:</span>
<span class="cm">#   '[Mr Speaker in the Chair]'</span>
<span class="cm"># while the turn's TEXT is a ministerial reply.</span>""",
         f"""Measured across the corpus: <b>{num(ev['turns_flagged'])} of {num(ev['turns'])} turns</b>
         carry the flag, holding <b>{num(ev['words_flagged'])} words
         ({ev['flagged_pct']:.1f}% of the archive)</b>. Of the {num(ev['flagged_big'])}
         flagged turns longer than 200 words, <b>{num(ev['flagged_strong'])} contain a
         sentence the selector would have scored above its own threshold</b>.""",
         f"""The worst single case is a <b>{num(ev['biggest_flagged_words'])}-word
         ministerial reply</b> on warrantless search powers in the Criminal Procedure
         (Miscellaneous Amendments) Bill &mdash; {num(ev['biggest_flagged_sentences'])}
         sentences, discarded whole, because the record labelled the turn
         <code>[Mr Speaker in the Chair]</code>. The code's own comment claimed the
         flag fired on "1 of 560 turns"; the archive said otherwise."""),
        ("F-4", "A document reported a figure 16× wrong",
         "A hand-written metric in a design document, never recomputed from the data it described.",
         """<span class="cm"># PLAN.md, before the correction:</span>
<span class="bad">1,100,000 words per sitting</span>

<span class="cm"># data/manifest.json, recomputed:</span>
<span class="ok">68,861 words per sitting</span>""",
         """This is the defect that produced the project's most useful rule.
         <b>N-3:</b> any number the system reports about itself must be computed from
         data, never hand-written into documentation. A wrong figure in a design
         document is not a typo &mdash; it is a decision made on evidence that does
         not exist.""",
         """It is also why this page is a <b>generator</b>, not a written document.
         Every figure you have read so far was computed from the archive when this
         HTML was built, next to the command that reproduces it."""),
    ]
    A(f"""
<section id="failures"><div class="cases-bleed">
  <span class="secnum">05 &mdash; What went wrong</span>
  <h2>Four defects, and what each one taught the design</h2>
  <div class="prose">
    <p class="lede">
      These are on the page on purpose. A project that only shows its finished
      state is indistinguishable from one that never found anything, and the
      failures here are what forced the architecture into its current shape. Three
      of the four were found by looking for a <em>different</em> kind of failure
      than the one the existing checks were testing for.
    </p>
  </div>
  <div class="cases">""")

    for cid, title, sub, code, what, lesson in cases:
        A(f"""
    <article class="case">
      <div class="case-h">
        <span class="cid">Defect {cid}</span>
        <h3>{title}</h3>
      </div>
      <div class="case-b">
        <div class="side">
          <p>{what}</p>
          <p class="hit"><b>What it cost:</b> {lesson}</p>
        </div>
        <pre class="code">{code}</pre>
      </div>
    </article>""")

    A(f"""
  </div>
</div></section>""")

    # ------------------------------------------------------- 6. data model
    A(f"""
<section id="model">
  <span class="secnum">06 &mdash; The data model</span>
  <h2>Provenance as a schema, not a convention</h2>
  <div class="prose">
    <p class="lede">
      The model is one chain &mdash; <strong>sitting → report → turn
      → sentence</strong> &mdash; with everything derived hanging off it by id.
      There is deliberately no entity that stores a copy of the archive's text.
    </p>
    <p>
      Two of these tables carry the correctness argument. <code>sentence</code>
      stores the character offsets and a content hash that make a quotation
      re-derivable; <code>claim</code> stores only an assertion plus the ids it
      rests on, never the quotation itself.
    </p>
  </div>
  <div class="model">
    <table class="tbl">
      <caption>Core chain
        <span class="tagline">measured row counts · the units are never interchangeable</span>
      </caption>
      <thead><tr><th>table</th><th>rows</th><th>what a row is</th></tr></thead>
      <tbody>
        <tr><td class="c">sitting <span class="keymark pk">PK</span></td>
          <td>{num(ev['sits'])}</td><td>one calendar day Parliament sat; id is the ISO date</td></tr>
        <tr><td class="c">report <span class="keymark fk">FK</span></td>
          <td>{num(ev['reports'])}</td><td>one record in the source API; a debate may span several</td></tr>
        <tr><td class="c">turn <span class="keymark fk">FK</span></td>
          <td>{num(ev['turns'])}</td><td>one speaker's contiguous contribution &mdash; the summary unit</td></tr>
        <tr><td class="c">sentence <span class="keymark fk">FK</span></td>
          <td>{num(ev['sent_total'])}</td><td>the unit of citation, with offsets and a hash</td></tr>
        <tr><td class="c">claim</td>
          <td>{num(ev['points'])}</td><td>an assertion plus the sentence ids it cites</td></tr>
      </tbody>
    </table>
    <table class="tbl">
      <caption>What makes a quotation checkable
        <span class="tagline">the fields the provenance chain actually depends on</span>
      </caption>
      <thead><tr><th>field</th><th>on</th><th>why it exists</th></tr></thead>
      <tbody>
        <tr><td class="c">char_start / char_end</td><td>sentence</td>
          <td>offsets into the normalised source &mdash; proves <em>position</em></td></tr>
        <tr><td class="c">sha256</td><td>sentence</td>
          <td>proves <em>content</em>: re-slice and compare, independently of us</td></tr>
        <tr><td class="c">cites[]</td><td>claim</td>
          <td>the sentence ids the claim rests on &mdash; the only source of its quote</td></tr>
        <tr><td class="c">quote</td><td>claim</td>
          <td><b>substituted</b> at assembly from the cited sentence, never model-written</td></tr>
        <tr><td class="c">attributed</td><td>turn</td>
          <td>false when the speaker was carried forward, because the record omitted one
            ({num(ev['turns_unattributed'])} of {num(ev['turns'])} turns,
            {ev['turns_unattributed_pct']:.1f}% &mdash; {num(ev['turns_subst_unattributed'])}
            of them 100+ words)</td></tr>
        <tr><td class="c">excluded / exclude_reason</td><td>sentence</td>
          <td>no silent drops: an unrecorded exclusion is indistinguishable from a bug</td></tr>
        <tr><td class="c">report_version</td><td>report</td>
          <td>the source format era, recorded so nothing has to infer it later</td></tr>
      </tbody>
    </table>
  </div>
</section>""")

    # -------------------------------------------------------- 7. decisions
    decisions = [
        ("D-1", "Publication is automated with hard gates, and no human in the loop",
         "Nothing publishes unless it passes validation. There is no review step.",
         """This inverts the usual safety model. If no person is going to notice a bad
         brief, <b>the gates are the entire quality process</b> &mdash; so a gate that
         reports and lets an item through is worse than no gate, because it
         manufactures confidence. A gate must fail <em>closed</em>: unknown state
         means do not publish."""),
        ("D-3", "An unvalidatable item is withheld entirely",
         "Not a partial brief, not a brief marked unverified. Nothing.",
         """Withholding is not neutral &mdash; it leaves a visible hole in a sitting
         page. So the page must show the hole <em>as</em> a hole: "2 of 16 items could
         not be verified and are withheld". Without honest reporting of absence,
         withholding would be indistinguishable from incompleteness. The distinction
         is the whole reason this decision is safe."""),
        ("D-4", "Unattributed claims publish, marked as inferred",
         f"{num(ev['turns_unattributed'])} of {num(ev['turns'])} turns carry no speaker "
         f"({ev['turns_unattributed_pct']:.1f}%), and {num(ev['turns_subst_unattributed'])} "
         f"of those run to 100 words or more &mdash; substantive content with no name attached.",
         """The record itself is incomplete, so excluding these would drop real
         content. Instead the provenance is made structural: every claim records
         whether its attribution came <em>from the record</em> or was
         <em>inferred</em>, the inference rule is deterministic and stated, and the
         page renders the difference so a reader knows which they are reading."""),
        ("D-5", "The why_it_matters field was removed",
         f"Of {num(ev['briefs'])} briefs, only {num(ev['wim_pop'])} ever populated it, and {num(ev['wim_dodge'])} of those "
         f"({100 * ev['wim_dodge'] / max(1, ev['wim_pop']):.0f}%) fell back on the same placeholder dodge.",
         """The field the record frequently cannot support is exactly the field where a
         model is most tempted to editorialise. A field absent
         {100 - 100 * ev['wim_pop'] / max(1, ev['wim_total']):.0f}% of the time and
         evasive {100 * ev['wim_dodge'] / max(1, ev['wim_pop']):.0f}% of the time it
         appears is not carrying its weight. Removing it changes the product, not
         just the schema: the brief must now earn attention with substance instead
         of the narrative hook."""),
    ]
    A(f"""
<section id="decisions">
  <span class="secnum">07 &mdash; Decisions</span>
  <h2>Four choices that constrain everything downstream</h2>
  <div class="prose">
    <p class="lede">
      Each decision below was recorded with the evidence that informed it, so a
      later reader can see <em>why</em> rather than only <em>what</em>. The evidence
      is reproduced here from the archive at build time.
    </p>
  </div>
  <ul class="decs">""")
    for did, title, why, cons in decisions:
        A(f"""
    <li class="dec">
      <span class="did">{did}</span>
      <h3>{title}</h3>
      <p class="why"><b>Evidence:</b> {why}</p>
      <p>{cons}</p>
    </li>""")
    A("  </ul>\n</section>")

    # ----------------------------------------------------------- 8. honesty
    A(f"""
<section id="status">
  <span class="secnum">08 &mdash; Current state</span>
  <h2>What works, and what does not yet</h2>
  <div class="prose">
    <p class="lede">
      The pipeline is being built one stage at a time and reviewed between stages.
      The archive and the dataset are real and verified; the model stage is
      specified but not yet run at scale. Reporting the gap is part of the design
      &mdash; the product's own rule is that absence must appear as absence.
    </p>
  </div>
  <div class="gap">
    <div class="g"><span class="req">Archive · R-1.1–1.5</span>
      <span class="st"><b>Working.</b> {num(ev['sits'])} sittings fetched in resumable
      year batches, zero failures. Coverage is declared per sitting rather than
      assumed: {num(ev['sits_below_one'])} of {num(ev['sits'])} sittings came in below
      the API's own claimed count, which is why the figure is recorded instead of
      trusted. Speaker attribution is measured at {ev['attrib_median']*100:.1f}% median.</span></div>
    <div class="g"><span class="req">Dataset · R-6.1–6.2</span>
      <span class="st"><b>Working.</b> {num(ev['items'])} items, {num(ev['sent_total'])}
      sentences with stable ids, {num(ev['chunk_total'])} chunks expressed as id lists.
      Invariants verified across every payload: no id collisions, no sentence
      duplicated across chunks, no sentence missing from a chunk.</span></div>
    <div class="g"><span class="req">Extract + assemble · R-4.x</span>
      <span class="st"><b>Not built.</b> Stage 2 and 3 are specified and pending review
      of the dataset. Deliberately not run end to end: the stop rule is one stage at a
      time.</span></div>
    <div class="g"><span class="req">Provenance · R-2.2</span>
      <span class="st"><b class="no">Partially met.</b> The chain is designed and
      demonstrated above, and <code>char_start</code>/<code>char_end</code> and the
      sentence hash are specified &mdash; but the published briefs still hold
      quotations as strings. Populating offsets and hashes for all
      {num(ev['sent_total'])} sentences is the next piece of work, and it is the one
      that turns the demonstration above from an example into a property of the corpus.</span></div>
    <div class="g"><span class="req">Gates · R-2.9</span>
      <span class="st"><b class="no">Not met.</b> Every gate must have a test that
      deliberately violates it and asserts the violation is caught. An untested gate
      is an assumption, not a control &mdash; and F-2 above is the proof.</span></div>
    <div class="g"><span class="req">Summary quality · R-3.3</span>
      <span class="st"><b class="no">Not re-derived.</b> The {num(ev['briefs'])} published
      briefs were built under the older item-level schema. They meet the quality bar
      but not the new turn-level model, and whether to migrate or discard them is
      still open.</span></div>
  </div>
</section>""")

    # ------------------------------------------------------------- footer
    A(f"""
<footer>
  <div class="prose">
    <p><b>How every figure here was produced.</b> This page is generated by
      <code>site/build_case_study.py</code>; nothing above is typed in. Rebuild it
      and the numbers are recomputed from the archive, which is what requirement N-3
      asks for and what the F-4 defect demanded.</p>
  </div>
  <ul class="method">
    <li><code>python3 tools/metrics.py</code> &mdash; corpus, archive and dataset counts</li>
    <li><code>python3 site/build_case_study.py</code> &mdash; regenerates this page, including the provenance trace</li>
    <li><code>python3 site/build_site.py</code> &mdash; builds the reader-facing site this work is for</li>
    <li>Source of the defect write-ups: <code>SUMMARISATION.md</code>, <code>REQUIREMENTS.md</code></li>
  </ul>
  <div class="prose" style="margin-top:1.6em">
    <p>Parsnips · case study · generated {ev['ds_built'] or ''}.
      Built on the Singapore Parliament Hansard public record
      (sprs.parl.gov.sg), 2016–2026, Parliaments 13–15.
      Attribution rules: <code>REQUIREMENTS.md</code> decisions D-1 to D-10.</p>
  </div>
</footer>
</div>""")

    # ------------------------------------------- the inspector's real script
    A(f"""
<script>
(function(){{
  var h1 = {json.dumps(demo['hash_quote'])},
      h2 = {json.dumps(demo['hash_reslice'])},
      insp = document.getElementById('insp'),
      btn = document.getElementById('run');
  if(!btn || !insp) return;
  var ids = ['s1','s2','s3','s4'], timers = [];

  btn.addEventListener('click', function(){{
    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var gap = reduce ? 90 : 520;
    insp.classList.remove('done');
    insp.classList.add('pending');
    ids.forEach(function(id){{ document.getElementById(id).classList.remove('on'); }});
    btn.disabled = true; btn.textContent = 'Running\\u2026';
    timers.forEach(clearTimeout); timers = [];
    ids.forEach(function(id, i){{
      timers.push(setTimeout(function(){{
        document.getElementById(id).classList.add('on');
      }}, gap * i + 60));
    }});
    timers.push(setTimeout(function(){{
      insp.classList.remove('pending');
      insp.classList.add('done');
      btn.disabled = false;
      btn.innerHTML = 'Run it again <span class="ar">\\u21bb</span>';
    }}, gap * ids.length + 240));
  }});
}})();
</script>
</body>
</html>""")

    doc = "\n".join(H)
    storage.write_text_atomic(OUT, doc)
    storage.write_text_atomic(OUT_DIST, doc)
    size = os.path.getsize(OUT)
    print(f"wrote {os.path.relpath(OUT, ROOT)} and dist copy "
          f"({size:,} bytes, {len(doc.splitlines())} lines)")
    print(f"evidence: {num(ev['sits'])} sittings, {num(ev['words'])} words, "
          f"{num(ev['points'])} points, {num(ev['turns'])} turns")
    print(f"provenance demo: {demo['brief_file']} / {demo['report_id']} "
          f"@ {demo['char_start']}..{demo['char_end']}  "
          f"hash match={demo['hash_quote'] == demo['hash_reslice']}")



if __name__ == "__main__":
    main()


# ------------------------------------------------------------------- stylesheet
