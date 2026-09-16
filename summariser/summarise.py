"""
Parsnips — summariser.

Turns a sitting's worth of Hansard into a neutral brief of WHAT WAS SAID and
WHAT WILL HAPPEN, for a reader who follows the news but has never read Hansard.

WHY THIS IS A SEPARATE PASS FROM THE SCRAPER
--------------------------------------------
Everything in `parsnips_fetch.py` is mechanical: fetch, parse speaker turns,
count words. Nothing there understands a sentence. "The messages delivered" only
exist in the prose, so this layer reads the text. That means it can be wrong in
ways the scraper cannot be, so it is built defensively:

  * EVERY claim carries the verbatim source sentence it came from. The model
    returns a `quote`; we verify the quote actually appears in the source before
    accepting the claim. A claim whose quote cannot be found is DROPPED, not
    shipped. This is the anti-hallucination gate and it is the whole point.
  * Nothing is scored, ranked or labelled. The model is not asked whether an
    answer was adequate, and must not editorialise.
  * `not_said` records questions the transcript leaves open -- phrased as open
    questions, not as accusations. This is factual: it is simply "the record does
    not state X".

BILLS CAN SPAN SITTINGS
-----------------------
A debate recorded in one sitting often continues in the next: the Land Transport
and Related Matters Bill is bill-780 (30,077 words, 3 Feb 2026) AND bill-781
(13,685 words, 4 Feb 2026). Both carry the same title and the SAME `sittingNo`
is absent, but `bill-780`'s own text says it continues the next day. So we group
summarisable business across dates by normalised title, not per sitting.

Usage:
    python3 summariser/summarise.py --sitting 2026-02-03
    python3 summariser/summarise.py --all
    python3 summariser/summarise.py --all --groups bill,statement,motion
"""
import argparse
import datetime
import glob
import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))
import storage  # noqa: E402  (shared storage layout: shards, keys, atomic writes)

DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "summaries")

ENDPOINT = os.environ.get("PARSNIPS_LLM_URL",
                          "http://127.0.0.1:11434/v1/chat/completions")
MODEL = os.environ.get("PARSNIPS_LLM_MODEL", "deepseek-v4.1-flash:cloud")
RETRIES = 3

# Which sections carry "messages delivered". These are the deliberate acts of
# government; written answers are also actionable but are handled separately
# because there are ~135 of them per sitting and each is tiny.
#
# `oral` was missing here and that was a real gap, not a decision: 223 oral
# answers / 236,318 words across 2026 -- 12% of the corpus, and the section
# readers most want. Oral answers are neither "written" nor tiny, so they fell
# between the two cases this comment describes. They now have their own prompt
# below, sized for a 267-3,363 word exchange rather than a 70,000 word debate.
SUMMARISABLE = ("bill", "statement", "motion", "budget", "adjournment", "oral")

# How far apart two reports sharing a title may sit and still be one policy item.
#
# Measured on the real corpus (52 sittings, 2016 + 2026): genuine same-item gaps
# cluster at 0-3 days, with two real outliers -- a Bill introduced 22 days before
# its Second Reading, and a "rearrangement of business" recurring 84 days apart.
# True cross-year collisions sit at ~3,611-3,720 days. So anything between 84 and
# 3,600 days separates cleanly; 120 sits comfortably in that gap and is generous
# enough to keep a bill's introduction and Second Reading in one brief.
#
# Without this bound, "Debate on Annual Budget Statement" from 2016 and from 2026
# merged into a single 271,095-word item covering two different Budgets.
CLUSTER_GAP_DAYS = 120

SYSTEM = """You are a neutral parliamentary reporter for a civic-education site read by
young adults who follow the news but have never read Hansard.

Your job: report WHAT WAS SAID and WHAT WILL HAPPEN, so the reader understands how a
policy decision was moved and what it means.

Hard rules:
- Only state things explicitly present in the transcript. If it is not there, omit it.
- Every key point MUST include a "quote": a SHORT verbatim sentence (or clause) copied
  exactly, character for character, from the transcript. This is checked automatically and
  any point whose quote cannot be found will be discarded. Do not paraphrase in "quote".
- Attribute each point to the person who said it, using the name as given in the transcript.
- Prefer concrete outcomes over adjectives: what changes, for whom, from when.
- NEVER judge whether a question was answered, whether a response was adequate, or what
  anyone's motive was. Do not use words like "evaded", "refused", "failed to", "only said".
- Do not use filler like "highlighted the importance of", "reiterated", "underscored".
- If the business is procedural with no substance, say so plainly in "what_it_is" and
  return an empty key_points list.

Output STRICT JSON only. No markdown fences, no commentary."""

USER_TMPL = """Read this Singapore Parliament business and return JSON:

{{
  "title": "plain-English title, one line, no trailing full stop",
  "what_it_is": "one sentence: the kind of business this is (e.g. a Bill at Second Reading, a ministerial statement) and what it does",
  "stage": "the formal stage if stated (e.g. \\"Second Reading\\", \\"Introduced\\", \\"Third Reading\\"), else \\"not stated\\"",
  "why_it_matters": "2-3 sentences on what this changes for ordinary people, ONLY where the transcript says so. If it does not say, write \\"The record does not set out the practical impact.\\"",
  "key_points": [
    {{"point": "what was said or will happen", "speaker": "who said it", "quote": "verbatim words from the transcript"}}
  ],
  "what_happens_next": "the next step if stated (e.g. referred to a Select Committee, will come into force on a date), else \\"not stated\\"",
  "not_said": ["questions the debate raises that the transcript does not resolve, phrased as neutral open questions with no implication of fault"]
}}

Transcript follows. Speakers are shown as [Name]: text.

{transcript}"""


# Oral answers are a different shape from a bill debate: one MP asks, a Minister
# answers, then a few supplementary exchanges. The reader wants to scan 16 of
# these without reading 13,000 words, so the output is deliberately short.
#
# The neutrality rule is load-bearing here and was an explicit product decision:
# this is a MAPPING of question to response, never a verdict on whether the
# response was adequate. No "the Minister did not answer", no "declined to
# commit" -- state what was asked and what was said, and let the reader judge.
ORAL_SYSTEM = """You are a neutral parliamentary reporter for a civic-education site read by
young adults who follow the news but have never read Hansard.

Your job, for ONE parliamentary question: state plainly WHAT WAS ASKED and WHAT WAS SAID
BACK, so the reader can understand the exchange without reading the transcript.

Hard rules:
- Only state things explicitly present in the transcript. If it is not there, omit it.
- Every point MUST include a "quote": a SHORT verbatim sentence (or clause) copied exactly,
  character for character, from the transcript. This is checked automatically and any point
  whose quote cannot be found will be discarded. Do not paraphrase inside "quote".
- Attribute each point to the person who said it, using the name as given.
- This is a MAPPING, not a scorecard. NEVER judge whether the question was answered, whether
  the response was adequate, or anyone's motive. Banned framings: "did not answer",
  "evaded", "declined to commit", "failed to address", "only said", "no commitment was made".
  If the response does not cover something the question raised, put it in "left_open" as a
  neutral open point, not as a criticism.
- Prefer concrete substance: figures, schemes, dates, eligibility, what the ministry will do.
- Do not use filler like "highlighted the importance of", "reiterated", "underscored".
- Be brief. This must be scannable in a few seconds.

Output STRICT JSON only. No markdown fences, no commentary."""

ORAL_TMPL = """Read this Singapore Parliament oral answer and return JSON:

{{
  "title": "plain-English title, one line, no trailing full stop",
  "asked": "1-2 sentences: what the MP asked for, in plain English",
  "asked_by": "the MP's name as given in the transcript",
  "answered_by": "the Minister's name as given in the transcript",
  "response": "2-4 sentences: what was actually said back. The substance, not a description of the speech.",
  "key_points": [
    {{"point": "a concrete factual point from the answer", "speaker": "who said it", "quote": "verbatim words from the transcript"}}
  ],
  "left_open": ["something the question raised that the response does not address, phrased neutrally, with no implication of fault"],
  "supplementary": "ONE short sentence (max ~25 words) naming the theme of the supplementary questions, or \"not stated\" if there were none. Do NOT list each questioner or each question."
}}

Transcript follows. Speakers are shown as [Name]: text.

{transcript}"""


# ------------------------------------------------------------------ transport
def ask(user, system=SYSTEM, timeout=420, model=MODEL):
    body = {"model": model, "temperature": 0.2,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    last = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            return d["choices"][0]["message"]["content"]
        except Exception as exc:                       # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last


def parse_json(raw):
    """Models sometimes wrap JSON in fences or add prose. Recover the object."""
    if not raw:
        return None
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt).strip()
    try:
        return json.loads(txt)
    except ValueError:
        pass
    # fall back to the outermost {...}
    start, end = txt.find("{"), txt.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(txt[start:end + 1])
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ transcript
def norm(text):
    """Normalise for verbatim quote checking.

    Whitespace and unicode punctuation differ between what the model echoes and
    the stored text (Hansard uses curly quotes and non-breaking spaces), so
    compare on a flattened form. This is deliberately forgiving about formatting
    and strict about wording.
    """
    if not text:
        return ""
    t = htmlmod.unescape(text)
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u2013", "-").replace("\u2014", "-")
    t = t.replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def strip_speaker_labels(text):
    """Remove "[Please refer to Vernacular Speech.]" style bracket asides."""
    return re.sub(r"\[[^\]]{0,200}\]", " ", text or "").strip()


def build_transcript(report, char_budget=120000):
    """Speaker-labelled transcript, trimmed in the middle if enormous."""
    parts = []
    for t in report["turns"]:
        body = strip_speaker_labels(t["text"])
        if not body:
            continue
        who = t["speaker"] or "(unattributed)"
        parts.append(f"[{who}]: {body}")
    full = "\n\n".join(parts)
    if len(full) <= char_budget:
        return full, False
    # keep the head and tail: the mover's speech and the response usually frame it
    head = full[:int(char_budget * 0.7)]
    tail = full[-int(char_budget * 0.25):]
    return head + "\n\n[...middle of this debate omitted for length...]\n\n" + tail, True


def verify_quotes(summary, transcript_norm):
    """Drop any key point whose quote cannot be found verbatim in the source.

    This is the anti-hallucination gate. A summary that survives it can be
    trusted to that extent; a claim that fails it is discarded rather than shown
    with a caveat, because a wrong citation is worse than a missing point.
    """
    kept, dropped = [], []
    for p in summary.get("key_points", []) or []:
        quote = norm(p.get("quote"))
        if not quote or len(quote) < 12:
            dropped.append({"reason": "no usable quote", "point": p.get("point", "")[:90]})
            continue
        if quote in transcript_norm:
            p["verified"] = True
            kept.append(p)
        else:
            dropped.append({"reason": "quote not found in source",
                            "point": p.get("point", "")[:90], "quote": p.get("quote", "")[:90]})
    summary["key_points"] = kept
    summary["_dropped"] = dropped
    return summary


# ------------------------------------------------------------------ selection
def summarisable_items(sittings, groups=SUMMARISABLE, min_words=150):
    """Group summarisable business into policy items.

    Bills are debated over consecutive sitting days under the same title, so the
    same bill appears as several reports; merging them gives one brief per policy
    item instead of one per day.

    The merge is bounded by TIME, not just by title. Titles repeat across years --
    "Debate on Annual Budget Statement" and "Committee of Supply - Head K (Ministry
    of Education)" appear in both 2016 and 2026 -- so a bare title key welds a
    decade-apart pair into one item. That produced a 271,095-word "item" spanning
    2016-04-04 to 2026-02-26 (Budget 2016 + Budget 2026 in a single brief).

    So: group by title, then split each group into clusters whose consecutive
    sitting days are within CLUSTER_GAP_DAYS. A debate running across consecutive
    or near-consecutive days stays one item; the same title a year or more later
    becomes its own. Measured on the real corpus this splits exactly the 22
    cross-year collisions and leaves every same-year merge intact.
    """
    def key(t):
        return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()

    def days_between(a, b):
        d1 = datetime.date.fromisoformat(a)
        d2 = datetime.date.fromisoformat(b)
        return abs((d2 - d1).days)

    # Collect every qualifying report under its normalised title.
    by_title = {}
    for s in sittings:
        for r in s["reports"]:
            if r["group"] not in groups or r["words"] < min_words:
                continue
            k = key(r["title"])
            if not k:
                continue
            by_title.setdefault(k, []).append((s["date"], r))

    items = []
    for k, pairs in by_title.items():
        # Sort by sitting date so clustering only has to look at neighbours.
        pairs.sort(key=lambda p: (p[0], p[1]["report_id"]))
        cluster, prev = [], None
        for date, r in pairs:
            if prev is not None and days_between(prev, date) > CLUSTER_GAP_DAYS:
                items.append(_to_item(cluster))
                cluster = []
            cluster.append((date, r))
            prev = date
        if cluster:
            items.append(_to_item(cluster))

    return sorted(items, key=lambda g: -g["words"])


def _to_item(cluster):
    """One policy item from a cluster of (date, report) pairs."""
    reports = [r for _, r in cluster]
    item = {
        "title": reports[0]["title"],
        "group": reports[0]["group"],
        "reports": reports,
        "sitting_dates": sorted({d for d, _ in cluster}),
        "words": sum(r["words"] for r in reports),
        "report_ids": [r["report_id"] for r in reports],
    }
    reports.sort(key=lambda r: (r.get("sitting_date") or "", r["report_id"]))
    return item


def merge_transcript(item):
    """One transcript across every sitting-day this item was debated."""
    chunks, truncated = [], False
    for r in item["reports"]:
        txt, cut = build_transcript(r, char_budget=90000)
        truncated = truncated or cut
        if len(item["reports"]) > 1:
            chunks.append(f"--- Sitting day: {r.get('sitting_date')} ({r['report_id']}) ---\n{txt}")
        else:
            chunks.append(txt)
    return "\n\n".join(chunks), truncated


def summarise_item(item):
    transcript, truncated = merge_transcript(item)
    t_norm = norm(strip_speaker_labels(transcript))
    is_oral = item["group"] == "oral"
    # Oral answers get their own prompt: shorter, question-to-response shaped, and
    # it reports what was left open instead of a "not_said" list. Same pipeline,
    # same quote gate.
    prompt = (ORAL_TMPL if is_oral else USER_TMPL).format(transcript=transcript)
    raw = ask(prompt, system=ORAL_SYSTEM if is_oral else SYSTEM)
    data = parse_json(raw)
    if not data:
        return None
    data = verify_quotes(data, t_norm)
    data["_meta"] = {
        "report_ids": item["report_ids"],
        "sitting_dates": item["sitting_dates"],
        "group": item["group"],
        "source_words": item["words"],
        "transcript_truncated": truncated,
        "model": MODEL,
        "points_dropped": len(data.get("_dropped", [])),
    }
    return data


# ------------------------------------------------------------------------ CLI
def load_sittings(dates=None):
    """Every sitting on disk, sharded by year. Tolerates a flat legacy layout."""
    paths = sorted(glob.glob(os.path.join(DATA, "20*", "sitting_*.json")))
    if not paths:
        paths = sorted(glob.glob(os.path.join(DATA, "sitting_*.json")))
    out = []
    for path in paths:
        date = os.path.basename(path)[len("sitting_"):-len(".json")]
        if dates and date not in dates:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"  warning: skipping {path}: {exc}", file=sys.stderr)
    out.sort(key=lambda s: s["date"])
    return out


def write_atomic(path, obj):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def rebuild_site():
    """Regenerate the static site from the current data + summaries.

    Called after each brief so the served site is never stale while the
    summariser is still working through a backfill. Cheap: one page per sitting.
    Imported lazily so the summariser still runs if the site builder is absent.
    """
    try:
        sys.path.insert(0, os.path.join(ROOT, "site"))
        import importlib
        import build_site
        importlib.reload(build_site)
        build_site.build_all(os.path.join(ROOT, "site", "dist"))
        return True
    except Exception as exc:                           # noqa: BLE001
        print(f"  (site rebuild skipped: {exc})")
        return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sitting", action="append", default=None,
                    help="limit to these sitting dates (repeatable)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--groups", default=",".join(SUMMARISABLE))
    ap.add_argument("--min-words", type=int, default=150)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    groups = tuple(g.strip() for g in args.groups.split(",") if g.strip())
    sittings = load_sittings(args.sitting)
    if not sittings:
        print("no sittings found in data/", file=sys.stderr)
        return 1

    items = summarisable_items(sittings, groups=groups, min_words=args.min_words)
    if args.limit:
        items = items[:args.limit]
    print(f"{len(items)} policy item(s) to summarise across "
          f"{len({d for i in items for d in i['sitting_dates']})} sitting day(s)")

    os.makedirs(OUT, exist_ok=True)
    index_path = os.path.join(OUT, "index.json")
    index = {}
    if os.path.exists(index_path):
        try:
            index = json.load(open(index_path, encoding="utf-8"))
        except (OSError, ValueError):
            index = {}

    def key_for(item):
        """Stable, title-free key from report identity.

        Titles are not unique (two Hansard records can share one) and they change,
        so a title-slugged filename collides, orphans itself on an edit, and can
        exceed filesystem limits. The report ids are the real identity.
        """
        return storage.stable_key(item["report_ids"])

    # Existing keys: accept the new stable keys and the old title slugs, so a run
    # over a partially migrated corpus does not re-summarise everything.
    done_ids = set()
    for k, entry in index.items():
        meta = entry if entry.get("report_ids") is not None else entry.get("_meta", {})
        for rid in (meta.get("report_ids") or []):
            done_ids.add((rid, entry.get("year") or storage.year_of(
                min(meta.get("sitting_dates") or ["9999"]))))

    todo = []
    for it in items:
        k = key_for(it)
        year = storage.year_of(min(it["sitting_dates"]))
        already = all((r, year) in done_ids for r in it["report_ids"])
        if not args.force and already and os.path.exists(
                storage.summary_path(k, year, OUT)):
            continue
        if not args.force and k in index and \
                (index[k].get("report_ids") == it["report_ids"]):
            continue
        todo.append((k, year, it))

    print(f"{len(todo)} to do, {len(items) - len(todo)} already done")
    done = failed = 0
    t0 = time.time()

    def work(pair):
        return pair[0], pair[1], pair[2], summarise_item(pair[2])

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for k, year, item, data in pool.map(work, todo):
            if not data:
                print(f"  FAILED  {item['title'][:66]}")
                failed += 1
                continue
            data["_meta"]["key"] = k
            data["_meta"]["schema"] = 2
            data["_meta"]["year"] = year
            write_atomic(storage.summary_path(k, year, OUT), data)
            index[k] = {
                "title": data.get("title") or item["title"],
                "key": k,
                "year": year,
                "sitting_dates": data["_meta"].get("sitting_dates") or [],
                "group": data["_meta"].get("group"),
                "report_ids": data["_meta"].get("report_ids") or [],
                "what_it_is": data.get("what_it_is", ""),
                "stage": data.get("stage", ""),
                "why_it_matters": data.get("why_it_matters", ""),
                "key_points": len(data.get("key_points", [])),
                "not_said": len(data.get("not_said", [])),
            }
            write_atomic(index_path, index)
            done += 1
            print(f"  ok  [{data['_meta']['group']:11s}] "
                  f"{len(data.get('key_points', [])):>2} pts "
                  f"(dropped {data['_meta']['points_dropped']:>2})  "
                  f"{str(data.get('stage'))[:18]:18s} {item['title'][:52]}")

    if done:
        rebuild_site()

    print(f"\ndone in {time.time() - t0:.0f}s: {done} summarised, {failed} failed")
    print(f"index: {index_path} ({len(index)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
