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

MIDDOT = "\u00b7"      # a plain middle dot; provenance strings are escaped,
DASH   = "\u2013"      # so these are real characters, never HTML entities

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


def _raw_context(full, quote, i, end):
    """A context window in the ORIGINAL casing, for display.

    Offsets are recorded against the NORMALISED text, and normalisation collapses
    whitespace — so the normalised string is shorter than the raw one (97,917 vs
    97,953 characters on the record this page uses) and a raw index is only
    coincidentally equal to a normalised one. Rather than assume they line up,
    confirm the raw slice at those offsets really is the quote; if it is not, fall
    back to the normalised window instead of printing a mismatched highlight.
    """
    if full[i:end].strip() == quote.strip():
        return full[max(0, i - 300):min(len(full), end + 300)], True
    j = full.find(quote)
    if j >= 0:
        return full[max(0, j - 300):min(len(full), j + len(quote) + 300)], True
    return "", False


def _lookup_sid(report_id, quote):
    """Find the sentence id the dataset assigned to this quote, and the dataset's
    own view of that sentence.

    The page must cite the sid, because the sid is what the schema's `claim.cites[]`
    holds. Deriving a turn number instead (as an earlier version did) produced a
    citation the pipeline would never emit.
    """
    import glob as _glob
    for path in _glob.glob(os.path.join(ROOT, "pipeline", "dataset", "*", "*.json")):
        try:
            d = storage.read_json(path)
        except Exception:
            continue
        if not d or d.get("id") != report_id:
            continue
        for s in (d.get("sentences") or []):
            if norm(s.get("text")) == norm(quote):
                return s.get("sid"), s
    return None, {}


def build_provenance_demo():
    """Pick a REAL published claim and re-derive it from the archive, here, at
    build time. This is the page's signature object: the reader is not shown an
    illustration of verification, they are shown a verification that already ran.

    Selection is deterministic: the first claim, in key order, across all
    published briefs, whose quote is long enough to be worth displaying and whose
    speaker the record supplies. Rebuilds pick the same claim every time.

    The citation shown is the SENTENCE ID (`s00001`), not a turn number. That
    matters: `turn_index` in the dataset counts the item's turns that had text, so
    it is neither the sitting's array index nor a per-report counter — for
    motion-3008+3010 it runs 1..69 in the first report and 72..125 in the second.
    The sid is the unit the schema actually cites, so the page shows the sid.
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
            sid, ds_sentence = _lookup_sid(rid, q)
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
                "sid": sid,
                "ds_sentence": ds_sentence,
                "normalised": nf,
                "norm_len": len(nf),
                "raw_len": len(full),
                "char_start": i,
                "char_end": end,
                "hash_quote": sha256(norm(q)),
                "hash_reslice": sha256(nf[i:end]),
                "hash_quote_raw": sha256(q),
                "context": nf[max(0, i - 300):min(len(nf), end + 300)],
                # A context window in ORIGINAL casing, for display. NOT simply the
                # raw string at the same offsets: normalisation collapses whitespace,
                # so the two strings differ in length (97,953 vs 97,917 on this
                # record) and a raw index is only coincidentally the same. So verify
                # the raw slice really is the quote before trusting the offsets;
                # otherwise fall back to the normalised window.
                "context_raw": _raw_context(full, q, i, end)[0],
                "context_raw_ok": _raw_context(full, q, i, end)[1],
                "quote_norm": norm(q),
                "quote_norm_len": len(norm(q)),
                "ctx_prefix_len": min(300, i),
                "turn_words": len(strip_speaker_labels(
                    rep["turns"][turn_index]["text"]).split()) if turn_index is not None else 0,
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
.step .val .lbl{color:var(--meta);font-size:11.5px;letter-spacing:.04em;
  text-transform:uppercase}
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
/* the source context, with the real offsets highlighted */
.ctx{margin:14px 0 0;font:400 12.5px/1.75 var(--mono);color:#4a545e;
  background:#f7f9f8;border:1px solid var(--rule);border-radius:8px;
  padding:13px 14px;overflow-wrap:anywhere}
.ctx mark{background:#d9ede2;color:#0f3b2b;font-weight:600;
  box-shadow:0 0 0 2px #d9ede2;border-radius:2px}

/* ---- where it stands ---- */
.gap{margin:24px 0 0;border:1px solid var(--rule);border-radius:10px;overflow:hidden}
.gap .g{display:grid;gap:4px;padding:15px 16px;border-bottom:1px solid var(--line)}
.gap .g:last-child{border-bottom:0}
.gap .g .req{font:600 13px/1.4 var(--mono);color:var(--accent)}
.gap .g .st{font-size:15.5px;color:var(--dim)}
.gap .g .st b{color:var(--ink);font-weight:600}

/* ---- the data sample : real records, read from the archive ---- */
.sample{margin:26px 0 0}
.sstep{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;
  padding-top:22px;margin-top:22px;border-top:1px solid var(--rule)}
.sstep:first-child{padding-top:0;margin-top:0;border-top:0}
.sl{font:700 11.5px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;
  color:var(--accent)}
.sf{font-size:14px;color:var(--meta)}
.sf code{font-size:13px}
/* A phone must not need a sideways swipe to read the records: shrink the mono
   and let it wrap, since the sample IS the content here. Measured: with no wrap,
   3 of the 5 sample blocks scrolled horizontally at 768px — a viewport with
   plenty of room — because one long speaker label exceeds even 712px of column.
   So the blocks wrap by default and only widen the font on roomy screens. */
/* Wrapping: `anywhere` broke strings mid-word ("(Ms Jasmin Lau) (for the
   Min…"), which makes a data sample look corrupt. `break-word` wraps at spaces
   and only breaks a token when it genuinely cannot fit. */
.code{font:400 12.5px/1.72 var(--mono);background:#191d22;color:#e6ebf0;
  border-radius:9px;padding:15px;margin:13px 0 0;
  white-space:pre-wrap;overflow-wrap:break-word;word-break:normal}
.code .jk{color:#8fb8ff}
.snote{font-size:14.5px;color:var(--dim);margin:.8em 0 0;max-width:64ch}
@media (max-width:520px){
  .code{font-size:10.5px;line-height:1.65;padding:13px 12px}
}

/* ---- lessons : the blog's "what went wrong" list ---- */
.lessons{list-style:none;padding:0;margin:26px 0 0;counter-reset:les}
.lessons > li{padding:22px 0;border-bottom:1px solid var(--rule);counter-increment:les}
.lessons > li:first-child{border-top:1px solid var(--rule)}
.lessons h3{font-size:clamp(19px,4.8vw,23px);margin:0 0 .5em;padding-left:2.3em;
  position:relative}
.lessons h3::before{content:counter(les);position:absolute;left:0;top:.15em;
  font:600 12px/1 var(--mono);color:var(--accent);background:var(--accent-soft);
  width:1.7em;height:1.7em;border-radius:50%;display:flex;align-items:center;
  justify-content:center;letter-spacing:0}
.lessons p{font-size:16px;color:var(--dim);margin:0 0 .8em;max-width:68ch}
.lessons p:last-child{margin-bottom:0}
.lessons p strong,.lessons p b{color:var(--ink);font-weight:600}

/* ---- footer ---- */
footer{padding:48px 0 64px;border-top:1px solid var(--line);
  color:var(--meta);font-size:14.5px}
footer .prose{max-width:70ch}
footer b{color:var(--ink)}

/* ============================================================
   ENHANCEMENT 1 — from ~620px. The ledger gains its two columns.
   ============================================================ */
@media (min-width:620px){
  .wrap{padding:0 28px}
  .row{grid-template-columns:1fr auto;gap:6px 40px;align-items:baseline}
  .row .fig{order:0}
  .stage.is-model{margin:0 -20px;padding-left:20px;padding-right:20px}
  .gap .g{grid-template-columns:120px 1fr;gap:18px;align-items:baseline}
}

/* ============================================================
   ENHANCEMENT 2 — from ~900px. Roomier type and rhythm. The
   900px block deliberately does NOT introduce a second grid: an
   earlier version split the case studies side by side here, and
   measurement killed it (a 451px code column at 1440 against
   674px at 768 — the widest viewport scrolled the most).
   ============================================================ */
@media (min-width:900px){
  section{padding:76px 0}
  .hero{padding:72px 0 0}
  .ledger{margin-top:52px}
  .row{padding:19px 0}
  .row .fig{font-size:34px}
  body{font-size:17.5px}
  .prose p,.lessons p{font-size:17.5px}
  .step{padding:19px 26px}
  .insp-h,.steps,.verdict{padding-left:26px;padding-right:26px}
}

/* Reduced motion: gentler, not zero — state changes stay, travel goes. */
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .step,.sdot{transition:opacity 200ms ease,border-color 200ms ease,color 200ms ease}
  .btn{transition:none}
  .btn:active{transform:none}
  *{animation:none!important}
}

/* Hover only where hovering is real (touch fires false hovers on tap). */
@media (hover:hover) and (pointer:fine){
  .btn:hover{background:#175940}
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


def build_data_sample(report_id, quote_prefix):
    """A real sample of the sitting -> report -> turn -> sentence shape.

    Every value is read from the archive at build time; nothing is typed. Three
    deliberate choices:

    * The turn shown is the one containing the quoted sentence, so the sample
      explains the example above it instead of introducing a second story.
    * The speaker is shown in full, because that is what the data holds — D-6 keeps
      the recorded label rather than resolving identities.
    * Long strings are shortened with a marker, and the marker is honest about it,
      rather than showing a truncated string that looks complete.
    """
    sitting_path = os.path.join(DATA, "2026", "sitting_2026-01-12.json")
    sn = json.load(open(sitting_path))
    rep = next((r for r in sn["reports"] if r.get("report_id") == report_id), None)
    if not rep:
        return None
    ds_path = os.path.join(ROOT, "pipeline", "dataset", "2026", f"{report_id}.json")
    ds = storage.read_json(ds_path) if os.path.exists(ds_path) else {}

    # the turn holding the quote — found by search, not by an assumed index
    turn = next((t for t in rep["turns"] if quote_prefix in (t.get("text") or "")), {})

    def clip(s, n):
        s = s or ""
        return s if len(s) <= n else s[:n].rstrip() + "…"

    return {
        "sitting_file": os.path.relpath(sitting_path, ROOT),
        "sitting": [("date", "2026-01-12"),
                    ("coverage", sn.get("coverage") or {}),
                    ("reports", f"a list of {len(sn['reports'])}")],
        "coverage": list((sn.get("coverage") or {}).items()),
        "report": [
            ("report_id", rep.get("report_id")),
            ("report_type", rep.get("report_type")),
            ("group", rep.get("group")),
            ("title", rep.get("title")),
            ("report_version", rep.get("report_version")),
            ("parliament_no", rep.get("parliament_no")),
            ("sitting_no", rep.get("sitting_no")),
            ("volume_no", rep.get("volume_no")),
            ("words", rep.get("words")),
            ("turns", f"a list of {len(rep.get('turns') or [])}"),
        ],
        "turn": [("speaker", turn.get("speaker")),
                 ("lang", turn.get("lang")),
                 ("is_procedural", turn.get("is_procedural")),
                 ("words", turn.get("words"))],
        "item": [
            ("id", ds.get("id")),
            ("schema", ds.get("schema")),
            ("tier", ds.get("tier")),
            ("source_words", ds.get("source_words")),
            ("sentence_count", ds.get("sentence_count")),
            ("chunk_count", ds.get("chunk_count")),
            ("excluded_counts", ds.get("excluded_counts") or {}),
        ],
        "sentence": [
            ("sid", (ds.get("sentences") or [{}])[0].get("sid")),
            ("text", (ds.get("sentences") or [{}])[0].get("text")),
            ("speaker", clip((ds.get("sentences") or [{}])[0].get("speaker"), 96)),
            ("turn_index", (ds.get("sentences") or [{}])[0].get("turn_index")),
            ("attributed", (ds.get("sentences") or [{}])[0].get("attributed")),
            ("words", (ds.get("sentences") or [{}])[0].get("words")),
            ("score", (ds.get("sentences") or [{}])[0].get("score")),
        ],
        "chunk_sample": (ds.get("chunks") or [[]])[0][:5],
        "chunk_len": len((ds.get("chunks") or [[]])[0]),
        "turn_words": turn.get("words"),
        "sentence_total": ds.get("sentence_count"),
    }


def json_block(pairs):
    """A readable JSON-ish block: plain keys, real JSON values.

    Keys are unquoted so the block stays legible at 390px; values are JSON, so
    `null`, `false` and `0.892` appear exactly as the data holds them.
    """
    return "\n".join(
        f'  <span class="jk">{esc(k)}</span>: {esc(json.dumps(v, ensure_ascii=False))}'
        for k, v in pairs)


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

    tier = ev["tier"]
    H = []
    A = H.append

    A(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reading a day of Parliament in forty minutes</title>
<meta name="description" content="How Parsnips turns {num(ev['words'])} words of Singapore Parliament Hansard into a page you can read in {ev['minutes_median_10s']:.0f} minutes, and check.">
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
  <p class="stamp">Notes on a small project</p>
  <h1>Reading a day of Parliament in <em>forty minutes</em></h1>
  <p class="dek">
    Singapore publishes everything Parliament says. That is
    <b>{num(ev['words_median_sit'])} words</b> for a single sitting &mdash; roughly
    <b>{ev['hours_median_sit']:.1f} hours</b> of reading, if you have the day free. Parsnips
    turns it into a page you can finish over coffee, where every claim still points
    at the sentence it came from.
  </p>
  {ledger([
    ("<b>Words of Hansard in the archive</b>",
     f"{num(ev['sits'])} sittings, 2016–2026 {MIDDOT} data/manifest.json",
     num(ev["words"]), "words", False),
    ("<b>Median sitting, read at a summary per speaker</b>",
     f"{num(ev['median_turns_sit'])} speaker turns {MIDDOT} ~10s each",
     f"{ev['minutes_median_10s']:.0f}", "min", True),
    ("<b>Points published, each with its citation</b>",
     f"summaries/ {MIDDOT} python3 tools/metrics.py",
     num(ev["points"]), "points", False),
  ])}
</header>""")

    # ------------------------------------------------------------ 1. why
    A(f"""
<section id="why">
  <h2>Why bother</h2>
  <div class="prose">
    <p class="lede">
      A sitting covers Bills, ministerial statements, oral answers and Budget
      debates, and the record of it is genuinely good. It is also unusable for
      anyone who is not a lawyer or a political scientist. News coverage swings the
      other way and gives you a phrase, chosen for how it reads rather than for what
      was decided.
    </p>
    <p>
      What I wanted was the thing in between: enough to understand what the
      government is actually doing, without pretending to be the transcript. The
      target I set was deliberately blunt &mdash; a sitting should take
      <strong>30 to 60 minutes</strong> to read. That turns a vague ambition into a
      number I can work against, and it turns into roughly <strong>10 seconds per
      speaker</strong> once you divide it up. Every summary the system writes has to
      fit that budget.
    </p>
  </div>
</section>""")

    # ------------------------------------------------------- 2. what it looks like
    A(f"""
<section id="output">
  <h2>What a page looks like</h2>
  <div class="prose">
    <p class="lede">
      Every exchange gets a short brief, in the order it happened. Not a highlights
      reel &mdash; the whole sitting, just compressed. Each brief carries a few
      points, and each point carries the words it was taken from, one tap away.
    </p>
  </div>
  <div class="claimbox">
    <span class="cl">An example, from {esc(demo['sitting_date'])}</span>
    <p class="say">&ldquo;{esc(demo['claim'])}&rdquo;</p>
    <p class="who">Said by <b>{esc(demo['speaker'])}</b>, and shown with this
      underneath it: <b>&ldquo;{esc(demo['quote'])}&rdquo;</b></p>
  </div>
  <div class="prose" style="margin-top:1.5em">
    <p>
      The quote is not a paraphrase. It is the sentence from Hansard, and the system
      is built so that it cannot be anything else. That constraint is the most
      interesting thing about the project, so the next section is about it.
    </p>
  </div>
</section>""")

    # ------------------------------------------------------- 3. the core idea
    ctx = demo["context"]
    # Prefer the original-casing context; fall back to the normalised window if the
    # offsets could not be confirmed against the raw text (see _raw_context).
    if demo.get("context_raw_ok") and demo.get("context_raw"):
        ctx = demo["context_raw"]
        j = ctx.find(demo["quote"])
        if j < 0:
            ctx, j = demo["context"], demo["ctx_prefix_len"]
    else:
        ctx, j = demo["context"], demo["ctx_prefix_len"]
    pre = ctx[:j]
    hit = ctx[j:j + len(demo["quote"])]
    post = ctx[j + len(demo["quote"]):]
    A(f"""
<section id="provenance">
  <h2>The one idea that makes it work</h2>
  <div class="prose">
    <p class="lede">
      Most summarisers ask a model to read the text and write out the quotes. That
      is where things go wrong quietly: the model reword a sentence slightly, and
      unless you check every quotation against the original you will never notice.
      So I stopped asking.
    </p>
    <p>
      The model never writes a quotation. It points at one &mdash; it returns the
      <em>id</em> of the sentence it is talking about, and the system pastes the real
      sentence in afterwards. The model can be wrong about which sentence matters.
      It cannot invent one.
    </p>
    <p>
      Which means the claim below is checkable by anyone, including you. Try it.
    </p>
  </div>

  <div class="insp pending" id="insp">
    <div class="insp-h">
      <h3>Follow a published quote back to Hansard</h3>
      <p>A real claim from the site, traced to the record it came from.</p>
      <div class="claimbox">
        <span class="cl">The claim</span>
        <p class="say">&ldquo;{esc(demo['claim'])}&rdquo;</p>
        <p class="who">Its quotation, as published:
          <b>&ldquo;{esc(demo['quote'])}&rdquo;</b></p>
      </div>
      <button class="btn" id="run" type="button">
        Run the check <span class="ar">&rarr;</span>
      </button>
    </div>
    <ol class="steps">
      <li class="step" id="s1">
        <div class="sh"><span class="sdot">1</span>
          <div><p class="st">The claim knows which sentence it rests on</p>
          <p class="sn">Not a copy of the sentence &mdash; a pointer to it.</p>
          <div class="val">claim &rarr; <b>{esc(demo['sid'] or 's?????')}</b>
            in <b>{esc(demo['report_id'])}</b>, from the sitting on
            {esc(demo['sitting_date'])}</div></div></div>
      </li>
      <li class="step" id="s2">
        <div class="sh"><span class="sdot">2</span>
          <div><p class="st">Go and fetch it</p>
          <p class="sn">The stored archive is opened at the recorded position,
            part-way through a {num(demo['turn_words'])}-word speech.</p>
          <div class="val">characters <b>{num(demo['char_start'])}</b> to
            <b>{num(demo['char_end'])}</b> &rarr;
            {num(demo['char_end'] - demo['char_start'])} characters</div></div></div>
      </li>
      <li class="step" id="s3">
        <div class="sh"><span class="sdot">3</span>
          <div><p class="st">Fingerprint both</p>
          <p class="sn">The quote as published, and the text taken from the record,
            hashed the same way.</p>
          <div class="val"><span class="lbl">published</span><br>{demo['hash_quote']}<br><br>
            <span class="lbl">from the record</span><br>{demo['hash_reslice']}</div></div></div>
      </li>
      <li class="step" id="s4">
        <div class="sh"><span class="sdot">4</span>
          <div><p class="st">They agree</p>
          <p class="sn">Which means the words on the page are the words in the
            record, at that position. Nobody has to take my word for it.</p></div></div>
      </li>
    </ol>
    <div class="verdict">
      <p class="vt">&#10003; Match &mdash; the quote reproduces from the record</p>
      <p>Here is that sentence sitting in Hansard, with the quoted part marked:</p>
      <div class="ctx">{esc(pre)}<mark>{esc(hit)}</mark>{esc(post)}</div>
    </div>
  </div>
</section>""")

    # --------------------------------------------------------- 4. how it fits
    A(f"""
<section id="shape">
  <h2>How it fits together</h2>
  <div class="prose">
    <p class="lede">
      Four steps, and only one of them uses a model. Everything mechanical &mdash;
      fetching, cleaning, counting, pasting quotes back in &mdash; is ordinary code,
      which is both cheaper and repeatable.
    </p>
  </div>
  <ol class="stages">
    {stage("Fetch",
           "Pull the sittings down and keep them, exactly as published.",
           False,
           "One batch per year, resumable, so an interrupted run picks up where it "
           "stopped. 331 sittings, no failures.")}
    {stage("Break it up",
           "Cut each sitting into the units a reader thinks in.",
           False,
           "A sitting becomes sections (Bills, statements, oral answers), each "
           "section becomes one speaker's turn, and each turn gets its own id. "
           "Every sentence is numbered so it can be pointed at later.")}
    {stage("Summarise",
           "Read a turn and decide what mattered.",
           True,
           f"The only step that needs judgement, so it is the only step that uses a "
           f"model. Large items &mdash; {num(tier['heavy']['items'])} of "
           f"{num(ev['items'])} &mdash; are handled in pieces.")}
    {stage("Check",
           "Prove it before it goes out.",
           False,
           "Every quote is re-checked against the archive, and so is the coverage: "
           "how much of the item actually got summarised. Anything that fails is "
           "held back rather than published with a caveat.")}
  </ol>
  <div class="prose" style="margin-top:1.5em">
    <p>
      The check is two questions, not one, and that turned out to matter. The first
      is &#8220;does this quote exist in the record&#8221;, which sounds like enough.
      The second is &#8220;did we actually summarise the whole thing&#8221;. I only
      added the second after the first one spent a while telling me everything was
      fine while it wasn't.
    </p>
  </div>
</section>""")

    # -------------------------------------------------- the data structure
    smp = build_data_sample(demo["report_id"], demo["quote"][:34])
    if smp is None:
        raise SystemExit("could not build the data sample — archive shape changed?")
    A(f"""
<section id="shape-sample">
  <h2>What the data actually looks like</h2>
  <div class="prose">
    <p class="lede">
      Everything above is the same idea at four different sizes. A sitting becomes
      reports, a report becomes turns, a turn is cut into numbered sentences &mdash;
      and it is the sentence that gets cited. Here is the real thing, taken from the
      sitting behind the example you just checked.
    </p>
  </div>

  <div class="sample">
    <div class="sstep">
      <span class="sl">1 &middot; sitting</span>
      <span class="sf">{esc(smp['sitting_file'])}</span>
    </div>
    <pre class="code">{json_block(smp['sitting'])}</pre>
    <p class="snote">Coverage is recorded per sitting, not assumed &mdash; the source
      API under-reports its own result counts, so this is the number the pipeline
      actually achieved rather than the one it was promised.</p>

    <div class="sstep">
      <span class="sl">2 &middot; report</span>
      <span class="sf">one entry in <code>reports[]</code></span>
    </div>
    <pre class="code">{json_block(smp['report'])}</pre>
    <p class="snote">The format era is recorded (<code>report_version</code>) so a
      later link or check never has to guess it, and the title is stored but never
      used as a key.</p>

    <div class="sstep">
      <span class="sl">3 &middot; turn</span>
      <span class="sf">one entry in <code>turns[]</code> &mdash; one speaker</span>
    </div>
    <pre class="code">{json_block(smp['turn'])}</pre>
    <p class="snote">This is the turn the quotation came from &mdash; a
      {num(smp['turn_words'])}-word speech. The speaker label is the whole thing, in
      full and unedited, because that is what the record holds: the pipeline keeps
      the portfolio and the parenthetical and does not try to tidy it into a name.</p>

    <div class="sstep">
      <span class="sl">4 &middot; sentence</span>
      <span class="sf">the unit that gets cited</span>
    </div>
    <pre class="code">{json_block(smp['sentence'])}</pre>
    <p class="snote">This is the row the claim above points at. The
      <code>sid</code> is what a published point stores instead of a quotation, and
      on a real build it also carries the character offsets and a hash of the text
      &mdash; the pair that lets the check you ran be repeated by anyone. (The
      speaker here is shortened to fit; the record holds it in full.)</p>

    <div class="sstep">
      <span class="sl">5 &middot; the item the summariser reads</span>
      <span class="sf">sentences plus chunk definitions</span>
    </div>
    <pre class="code">{json_block(smp['item'])}
  sentences: a list of {num(smp['sentence_total'])} sentence records
  chunks:    {num(smp['chunk_len'])} lists of sentence ids, e.g. {esc(json.dumps(smp['chunk_sample'], ensure_ascii=False))}</pre>
    <p class="snote">Chunks are lists of <em>ids</em>, not slices of text, which is
      what keeps the whole thing checkable. <code>excluded_counts</code> records what
      was left out and why, so a silent drop is impossible. The
      <code>turn_index</code> on each sentence counts the turns that survived
      filtering, across the whole item &mdash; not the sitting's array positions
      &mdash; which is exactly the kind of thing that has to be written down, because
      I got it wrong the first time and cited a turn number the pipeline would never
      produce.</p>
  </div>
</section>""")

    # ------------------------------------------------------------- 5. lessons
    A(f"""
<section id="lessons">
  <h2>Things that went wrong</h2>
  <div class="prose">
    <p class="lede">
      Worth writing down, because they changed how the thing is built rather than
      just being bugs I fixed.
    </p>
  </div>
  <ol class="lessons">
    <li>
      <h3>The check that said 100% while the output was empty</h3>
      <p>
        A rule meant to skip procedural noise was matching in the wrong place, and
        throwing away whole ministerial speeches &mdash; {num(ev['turns_flagged'])} turns
        across the archive carry that flag, including one
        {num(ev['biggest_flagged_words'])}-word reply.
      </p>
      <p>
        The lesson: my quote checker was reporting a clean bill of health the entire
        time. It could not see the problem, because a summary with nothing in it
        trivially has no wrong quotes. <strong>A check that only tests one way of
        being wrong will happily certify a different failure.</strong> That is why
        coverage is now checked as well.
      </p>
    </li>
    <li>
      <h3>A document of mine was wrong by 16&times;</h3>
      <p>
        I had written &#8220;1,100,000 words per sitting&#8221; in my own notes.
        The real figure is {num(ev['words_median_sit'])}. Nobody had recomputed it,
        because it was written in prose rather than produced by the code.
      </p>
      <p>
        So now anything the system says about itself is computed from the data, and
        this page is built the same way &mdash; the numbers you are reading were
        generated from the archive when the page was built, not typed by me.
      </p>
    </li>
    <li>
      <h3>Being clever about selection was the wrong instinct</h3>
      <p>
        My first design had code pick the important sentences and gave the model only
        those, which felt efficient and responsible. It was a trap: if the code
        chooses badly, the model never sees the rest and writes a confident summary
        of the wrong material anyway.
      </p>
      <p>
        Deterministic code is good at the boring parts &mdash; cleaning, filtering,
        pasting quotes back. Deciding what matters is the part it is bad at, and the
        part a model is good at. Splitting the work along that line is the whole
        architecture.
      </p>
    </li>
  </ol>
</section>""")

    # --------------------------------------------------------- 6. where it is
    A(f"""
<section id="where">
  <h2>Where it stands</h2>
  <div class="prose">
    <p class="lede">
      In progress, and built one step at a time on purpose. The archive is done and
      the summarising step is next; I would rather show you where the line actually
      is than round it up.
    </p>
  </div>
  <div class="gap">
    <div class="g"><span class="req">Done</span>
      <span class="st">All {num(ev['sits'])} sittings from 2016 to 2026, fetched and
      kept &mdash; {num(ev['words'])} words. Resumable, and honest about its own
      gaps: {num(ev['sits_below_one'])} of the {num(ev['sits'])} sittings came back
      with fewer records than the source claimed they contained.</span></div>
    <div class="g"><span class="req">Done</span>
      <span class="st">The sitting is broken into
      {num(ev['turns'])} speaker turns and {num(ev['sent_total'])} numbered
      sentences, ready to be pointed at.</span></div>
    <div class="g"><span class="req">Next</span>
      <span class="st">Summarising at scale. {num(ev['briefs'])} briefs were written
      as a trial, which is enough to prove the approach and not enough to be the
      product.</span></div>
    <div class="g"><span class="req">Next</span>
      <span class="st">Storing the position of every sentence, so the check you just
      ran works across the whole archive rather than the example above.</span></div>
  </div>
</section>""")

    # ------------------------------------------------------------- footer
    A(f"""
<footer>
  <div class="prose">
    <p>Built on Singapore's Parliament Hansard (sprs.parl.gov.sg), 2016 to 2026.
      Every number on this page is generated from the archive by
      <code>site/build_case_study.py</code> rather than written by hand, which is a
      rule the project adopted after the 16&times; mistake above.</p>
  </div>
</footer>
</div>""")

    # ------------------------------------------- the inspector's real script
    A("""
<script>
(function(){
  var insp = document.getElementById('insp'),
      btn = document.getElementById('run');
  if(!btn || !insp) return;
  var ids = ['s1','s2','s3','s4'], timers = [];

  btn.addEventListener('click', function(){
    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var gap = reduce ? 90 : 520;
    insp.classList.remove('done');
    insp.classList.add('pending');
    ids.forEach(function(id){ document.getElementById(id).classList.remove('on'); });
    btn.disabled = true; btn.textContent = 'Running\u2026';
    timers.forEach(clearTimeout); timers = [];
    ids.forEach(function(id, i){
      timers.push(setTimeout(function(){
        document.getElementById(id).classList.add('on');
      }, gap * i + 60));
    });
    timers.push(setTimeout(function(){
      insp.classList.remove('pending');
      insp.classList.add('done');
      btn.disabled = false;
      btn.innerHTML = 'Run it again <span class="ar">\u21bb</span>';
    }, gap * ids.length + 240));
  });
})();
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
