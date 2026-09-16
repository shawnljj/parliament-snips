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
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "summaries")

ENDPOINT = os.environ.get("PARSNIPS_LLM_URL",
                          "http://127.0.0.1:11434/v1/chat/completions")
MODEL = os.environ.get("PARSNIPS_LLM_MODEL", "deepseek-v4.1-flash:cloud")
RETRIES = 3

# Which sections carry "messages delivered". These are the deliberate acts of
# government; written answers are also actionable but are handled separately
# because there are ~135 of them per sitting and each is tiny.
SUMMARISABLE = ("bill", "statement", "motion", "budget", "adjournment")

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
    """Group summarisable business across sittings by title.

    Bills are debated over consecutive sitting days under the same title, so the
    same bill appears as several reports. Merging by normalised title gives one
    brief per policy item instead of one per day.
    """
    def key(t):
        return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()

    grouped = {}
    for s in sittings:
        for r in s["reports"]:
            if r["group"] not in groups or r["words"] < min_words:
                continue
            k = key(r["title"])
            if not k:
                continue
            g = grouped.setdefault(k, {
                "title": r["title"], "group": r["group"], "reports": [],
                "sitting_dates": [], "words": 0, "report_ids": [],
            })
            g["reports"].append(r)
            g["report_ids"].append(r["report_id"])
            g["sitting_dates"].append(s["date"])
            g["words"] += r["words"]
    for g in grouped.values():
        g["sitting_dates"] = sorted(set(g["sitting_dates"]))
        g["reports"].sort(key=lambda r: (r.get("sitting_date") or "", r["report_id"]))
    return sorted(grouped.values(), key=lambda g: -g["words"])


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
    prompt = USER_TMPL.format(transcript=transcript)
    raw = ask(prompt)
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
    out = []
    for fn in sorted(os.listdir(DATA)):
        if not (fn.startswith("sitting_") and fn.endswith(".json")):
            continue
        if dates and fn[len("sitting_"):-len(".json")] not in dates:
            continue
        try:
            with open(os.path.join(DATA, fn), encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"  warning: skipping {fn}: {exc}", file=sys.stderr)
    out.sort(key=lambda s: s["date"])
    return out


def write_atomic(path, obj):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


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
        return re.sub(r"[^a-z0-9]+", "-", item["title"].lower()).strip("-")[:90]

    todo = []
    for it in items:
        k = key_for(it)
        if not args.force and k in index and index[k].get("_meta", {}).get("report_ids") == it["report_ids"]:
            continue
        todo.append((k, it))

    print(f"{len(todo)} to do, {len(items) - len(todo)} already done")
    done = failed = 0
    t0 = time.time()

    def work(pair):
        return pair[0], pair[1], summarise_item(pair[1])

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for k, item, data in pool.map(work, todo):
            if not data:
                print(f"  FAILED  {item['title'][:66]}")
                failed += 1
                continue
            write_atomic(os.path.join(OUT, f"{k}.json"), data)
            index[k] = {
                "title": data.get("title") or item["title"],
                "_meta": data["_meta"],
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

    print(f"\ndone in {time.time() - t0:.0f}s: {done} summarised, {failed} failed")
    print(f"index: {index_path} ({len(index)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
