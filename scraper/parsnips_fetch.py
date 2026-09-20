"""
Parsnips — Hansard fetcher (stdlib only, no third-party deps).

VERIFIED 2026-09-16 against the live Parliament of Singapore portal.

WHY THIS FILE EXISTS
--------------------
Every blog post, tutorial and GitHub scraper for Singapore Hansard calls
``GET https://sprs.parl.gov.sg/search/getHansardReport/?sittingdate=YYYY-MM-DD``.
That endpoint was RETIRED when Parliament rebuilt the portal as an Angular SPA.
It now returns HTTP 500 for *every* date, including known sitting dates. Code
written against it silently returns "no sitting" for all input.

The live portal talks to three POST endpoints on https://sprs.parl.gov.sg/search:

  POST /searchResult      enumerate reports for a date (+ filter by section)
  POST /getHansardTopic   fetch one report's full HTML content
  POST /fetchData         static filter lists: MPs, former MPs, sections, ministries

All three need a browser-ish User-Agent and a same-site Referer or they 500.

GOTCHAS DISCOVERED THE HARD WAY
-------------------------------
1. Paging is capped at 20 rows per call. ``endIndex`` is ignored: asking for
   startIndex=0, endIndex=199 still returns 20 rows. You must page by 20.
2. ``maxResult`` is LOAD-BALANCED and unreliable. Two backend nodes disagree,
   and deep pages silently drop rows. A clean linear sweep of the 3 Mar 2026
   sitting returned 62 of a claimed 73 reports. Unioning a linear sweep with
   per-section sweeps recovered 70/73. Always compute a coverage ratio and
   treat a shortfall as a data-quality flag rather than shipping a partial day.
3. ``searchResult`` rows carry ``content: null``. Content ONLY comes from
   ``getHansardTopic``. Do not try to read text out of the listing.
4. The API uses HTTP 500 for both real errors and transient hiccups, so retry
   with backoff before believing a failure.
5. Sitting dates are NOT served by an API. The parliament.gov.sg "sitting dates"
   page 404s. Day-probing weekdays against /searchResult works and is what we use.

SPEAKER ATTRIBUTION
-------------------
This is the asset that makes the project more insightful than a news post.
Report HTML is a flat run of ``<p>`` tags. A paragraph that opens with a
``<strong>`` tag is a new speaker turn:

    <p><strong>The Acting Minister-in-charge of Muslim Affairs (Mr Zaqy Mohamad)</strong>: ...

Measured on the 5 Aug 2026 sitting: 405/418 turns (96.9%) carry an explicit
speaker tag. Reports also carry an ``mpNames`` field listing everyone who spoke.
Inline language switches appear as ``(<em>In English</em>)`` / Chinese / Malay.
"""
import datetime
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
# The repo's data directory, used only for the discovery calendar cache.
DATA_DIR = os.path.join(os.path.dirname(HERE), "data")

sys.path.insert(0, HERE)
import storage  # noqa: E402  (the project's single atomic-write implementation)
import hansard_parse as H  # noqa: E402  (structural parser; see that module for why)

BASE = "https://sprs.parl.gov.sg/search"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HEADERS = {
    "User-Agent": _UA,
    "Content-Type": "application/json",
    "Referer": "https://sprs.parl.gov.sg/search/",
    "Accept": "application/json, text/plain, */*",
}
PAGE = 20          # server-side hard cap; endIndex is ignored
PAUSE = 0.2        # polite delay between sequential calls

# Discovery probes one request per weekday, so a year is ~260 requests. Sequential
# at network latency that is ~13 minutes of dead time before a batch even starts.
# Parallel with staggered submission brings it down to roughly a minute, while
# still going easy on a public government portal.
DISCOVER_WORKERS = 6
RETRIES = 4

# Sections (rsSelected values) confirmed from /fetchData orSectionList.
SECTIONS = [
    "ministerial-statement", "motion", "bill", "clarification",
    "written-answer", "written-answer-na", "oral-answer", "bill-intro",
    "president-address", "ptba", "speaker", "petition", "admin-oaths",
    "point-of-order", "written-statement", "attendance", "tribute",
    "personal-explanation", "matter-adj", "budget",
]

# Human-facing groupings. Keys cover BOTH forms the API uses: the slug from the
# searchResult listing ("oral-answer") and the prose label from getHansardTopic
# ("Oral Answers to Questions"). Never rely on just one -- verify by dumping
# Counter(r["report_type"]) before trusting a mapping.
SECTION_GROUPS = {
    # prose labels (getHansardTopic)
    "Oral Answers to Questions": "oral",
    "Written Answers to Questions": "written",
    "Written Answers to Questions for Oral Answer Not Answered by End of Question Time": "written",
    "Bills": "bill", "Bills Introduced": "bill", "Bill": "bill",
    "Budget": "budget", "Committee of Supply": "budget",
    "Motions": "motion", "Motion": "motion",
    "Ministerial Statements": "statement", "Ministerial Statement": "statement",
    "Matter Raised On Adjournment Motion": "adjournment",
    "Matter raised on Adjournment Motion": "adjournment",
    "Correction by Written Statement": "correction",
    "Corrections by Written Statements": "correction",
    "Tributes": "tribute", "Petitions": "petition",
    # slugs (searchResult listing)
    "oral-answer": "oral",
    "written-answer": "written",
    "written-answer-na": "written",
    "bill": "bill", "bill-intro": "bill",
    "budget": "budget", "president-address": "budget",
    "motion": "motion",
    "ministerial-statement": "statement",
    "matter-adj": "adjournment",
    "written-statement": "correction",
    "tribute": "tribute", "petition": "petition",
}


def group_for(report_type, report_id=""):
    """Best-effort section group from the type label, falling back to the id prefix.

    report_id prefixes observed live: oral-answer-, written-answer-,
    written-answer-na-, motion-, bill-, bill-intro-, budget-, matter-adj-,
    ministerial-statement-, petition-, tribute-, attendance-, ptba-.
    """
    if report_type in SECTION_GROUPS:
        return SECTION_GROUPS[report_type]
    prefix = re.sub(r"-\d+$", "", (report_id or "").rstrip("#"))
    return SECTION_GROUPS.get(prefix, "other")

SPEAKER_LEAD = re.compile(r"^\s*(\d+\s+)?<strong>(.*?)</strong>", re.S)
LANG_MARK = re.compile(r"^\(\s*<em>\s*(?:In\s+)?(English|Chinese|Malay|Tamil)\s*</em>\s*\)")
PROCEDURAL = re.compile(r"^\[.*\]$")


# ---------------------------------------------------------------- transport
def post(endpoint, payload, timeout=90, retries=RETRIES):
    """POST to the Hansard API. Returns parsed JSON or raises on total failure."""
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            f"{BASE}/{endpoint}", data=json.dumps(payload).encode(),
            headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as exc:                      # noqa: BLE001
            last = exc
            time.sleep(1.2 * (attempt + 1))
    raise last


def post_soft(endpoint, payload, timeout=90):
    """Like post() but returns None instead of raising (for flaky pagination)."""
    try:
        return post(endpoint, payload, timeout=timeout)
    except Exception:                                 # noqa: BLE001
        return None


def rows_of(payload):
    """searchResult returns EITHER a list OR an object keyed \"0\",\"1\",..."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = [v for v in payload.values() if isinstance(v, dict)]
    else:
        return []
    return [r for r in rows if r.get("reportId")]


# ---------------------------------------------------------------- querying
def search_body(day, start=0, rs_selected=""):
    """Build a /searchResult body for one calendar day.

    The server ignores the fromday/frommonth/... fields but honours a Solr
    range on ``date_dt``, so the day filter lives in ``dateRange``.
    """
    return {
        "keyword": "", "reportContent": "with all the words", "parliamentNo": "",
        "selectedSort": "date_dt desc", "portfolio": [], "mpName": "",
        "rsSelected": rs_selected, "lang": "",
        "startIndex": str(start), "endIndex": str(start + PAGE - 1),
        "titleChecked": "false", "footNoteChecked": "false", "ministrySelected": [],
        "fromday": "", "frommonth": "", "fromyear": "",
        "today": "", "tomonth": "", "toyear": "",
        "dateRange": f"{day}T00:00:00Z TO {day}T23:59:59Z",
    }


def get_filters():
    """Static filter lists (MPs sitting now, former MPs, sections, ministries)."""
    return post("fetchData", {}) or {}


def reports_for_date(day, rs_selected="", max_pages=60):
    """Enumerate reports for one day. Returns (dict id->row, maxResult)."""
    first = rows_of(post_soft("searchResult", search_body(day, 0, rs_selected)))
    if not first:
        return {}, 0
    max_result = int(first[0].get("maxResult") or 0)
    seen = {r["reportId"].rstrip("#"): r for r in first}
    for start in range(PAGE, PAGE * max_pages, PAGE):
        page = rows_of(post_soft("searchResult", search_body(day, start, rs_selected)))
        if not page:
            break
        max_result = max(max_result, int(page[0].get("maxResult") or 0))
        before = len(seen)
        for r in page:
            seen.setdefault(r["reportId"].rstrip("#"), r)
        time.sleep(PAUSE)
        if len(seen) == before and start > max_result + PAGE:
            break
    return seen, max_result


def enumerate_sitting_reports(day, workers=4):
    """Union of a linear sweep and per-section sweeps.

    Necessary because maxResult is load-balanced and deep pages drop rows: on
    5 Aug 2026 a clean linear sweep returned 127 reports and section sweeps
    recovered 32 more real ones (159/167).

    The section sweeps are independent, so they run concurrently -- sequentially
    they dominated runtime (~50 of ~290 seconds per sitting) for identical
    output. Worker count is deliberately modest: this is a public government
    portal and we are guests.

    Returns (reports dict, coverage dict) so callers can gate on completeness.
    """
    reports, max_result = reports_for_date(day)

    # Nothing missing? Don't issue 21 extra requests to confirm it.
    if max_result and len(reports) >= max_result:
        return reports, {
            "date": day, "collected": len(reports), "max_result": max_result,
            "ratio": 1.0, "enumeration": "linear",
        }

    def sweep(section):
        rows, _ = reports_for_date(day, rs_selected=section)
        return rows

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sec_rows in pool.map(sweep, SECTIONS):
            for rid, row in sec_rows.items():
                reports.setdefault(rid, row)

    coverage = {
        "date": day,
        "collected": len(reports),
        "max_result": max_result,
        "ratio": round(len(reports) / max_result, 3) if max_result else None,
        "enumeration": "linear+sections",
    }
    return reports, coverage


def discover_sittings_cached(year, *, refresh=False, workers=None):
    """Sitting dates for a year, cached to data/calendar/<year>.json.

    Probing costs one request per weekday at ~13s latency each, so a year is ~8
    minutes of waiting. Caching makes that a one-time cost per year rather than a
    per-run cost, which is what makes a ten-year backfill practical: a rerun after
    an interruption resumes against the cached calendar instead of re-probing.

    The cache records the probe date so a future refresh can tell how stale it is.
    """
    cache_dir = os.path.join(DATA_DIR, "calendar")
    cache = os.path.join(cache_dir, f"{year}.json")
    if not refresh and os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                blob = json.load(fh)
            dates = blob.get("dates") or []
            if dates:
                return [(d, n) for d, n in dates]
        except (OSError, ValueError):
            pass

    found = discover_sittings(f"{year}-01-01", f"{year}-12-31", workers=workers)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = cache + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({
                "year": year,
                "probed": datetime.date.today().isoformat(),
                "probe_workers": workers or DISCOVER_WORKERS,
                "dates": found,
            }, fh, indent=1)
        os.replace(tmp, cache)
    except OSError:
        pass
    return found


def discover_sittings(start, end, weekdays_only=True, workers=None):
    """Find sitting dates by probing each weekday. Returns [(date, report_count)].

    Parallel on purpose: a year is ~260 weekdays, and probing them one at a time at
    network latency (~3s each) makes discovery alone cost ~13 minutes per year. That
    is pure dead time in a 10-year backfill, where discovery happens before any real
    fetching. Workers are kept modest -- this is a public government portal and we
    are guests -- and requests are staggered on submission so we do not open N
    connections in the same instant.
    """
    if isinstance(start, str):
        start = datetime.date.fromisoformat(start)
    if isinstance(end, str):
        end = datetime.date.fromisoformat(end)

    days = []
    day = start
    while day <= end:
        if not weekdays_only or day.weekday() < 5:
            days.append(day)
        day += datetime.timedelta(days=1)

    if workers is None:
        workers = DISCOVER_WORKERS

    def probe(d):
        try:
            rows = rows_of(post_soft("searchResult", search_body(d.isoformat())))
        except Exception:                                       # noqa: BLE001
            # A transient failure must not silently turn a sitting into a
            # non-sitting: report the date as unknown so a rerun retries it.
            return (d.isoformat(), None)
        if not rows:
            return (d.isoformat(), 0)
        return (d.isoformat(), int(rows[0].get("maxResult") or 0))

    out, unsure = [], []
    if workers <= 1:
        for d in days:
            date_s, n = probe(d)
            if n:
                out.append((date_s, n))
            elif n is None:
                unsure.append(date_s)
            time.sleep(PAUSE)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for d in days:
                futures.append(pool.submit(probe, d))
                time.sleep(PAUSE / workers)   # stagger submissions
            for fut in futures:
                date_s, n = fut.result()
                if n:
                    out.append((date_s, n))
                elif n is None:
                    unsure.append(date_s)

    if unsure:
        print(f"  warning: {len(unsure)} day(s) could not be probed and were skipped: "
              f"{', '.join(unsure[:6])}{' ...' if len(unsure) > 6 else ''}",
              file=sys.stderr)
    out.sort()
    return out


# ----------------------------------------------------------------- parsing
def clean(fragment):
    """HTML fragment -> normalised plain text."""
    text = re.sub(r"<[^>]+>", "", fragment or "")
    text = htmlmod.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_turns(content_html):
    """Split report HTML into speaker-attributed turns.

    A ``<p>`` opening with <strong> starts a new turn; other paragraphs append
    to the current turn (they are continuation or quoted speech). Paragraphs
    before any speaker tag become an unattributed lead turn.

    TWO SOURCE-QUIRK HANDLINGS THAT MATTER, both found by chasing wrong speakers
    published on the site:

    1. ``&nbsp;`` is NOT matched by ``\\s``, so a paragraph opening
       "&nbsp;&nbsp;<strong>Minister ..." failed the ^\\s* anchor and its text was
       APPENDED to the previous speaker's turn. Measured: oral-answer-3621 s00008
       published the Minister for Home Affairs' reply under the questioner's name.

    2. The speaker can be split across CONSECUTIVE <strong> tags with styling
       between them ("<strong>The Senior Parliamentary Secretary ... (for the&nbsp;
       </strong><strong style=...>Minister for National Development)</strong>"). A
       non-greedy single-tag match stopped at the first tag and TRUNCATED the name
       mid-title, which then disagreed with the sentence text.
    """
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", content_html or "", re.S | re.I)
    turns, current = [], None

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # (1) normalise HTML space entities BEFORE matching -- \s does not match &nbsp;
        para = (para.replace("&nbsp;", " ").replace("&#160;", " ")
                    .replace("&ensp;", " ").replace("&emsp;", " ").replace("&#8203;", " "))

        lang = None
        m_lang = LANG_MARK.match(para)
        if m_lang:
            lang = m_lang.group(1)
            para = para[m_lang.end():].strip()

        m = SPEAKER_LEAD.match(para)
        if m:
            speaker = clean(m.group(2))
            body = clean(para[m.end():]).lstrip(":").strip()

            # (2) a speaker name split across consecutive <strong> tags: keep pulling the
            # next tag while the accumulated name still looks like an unfinished title
            # (ends with a conjunction, bracket or preposition) and the tag has no prose.
            rest = para[m.end():]
            for _ in range(3):
                mm = re.match(r"^\s*<strong[^>]*>(.*?)</strong>", rest, re.S | re.I)
                if not mm:
                    break
                nxt = clean(mm.group(1)).strip()
                if not nxt or len(nxt) > 120 or nxt.endswith(".") and " " in nxt[: -1]:
                    break
                joined = f"{speaker} {nxt}".strip()
                if not re.search(r"(?:\bfor the|\band\b|\bof\b|\bthe|\(|,)\s*$",
                                 speaker.strip(), re.I):
                    break
                speaker = joined
                rest = rest[mm.end():]
            body = clean(rest).lstrip(":").strip()
            if not body:
                body = clean(para[m.end():]).lstrip(":").strip()
            current = {
                "speaker": speaker,
                "lang": lang or "English",
                "text": body,
                "is_procedural": bool(PROCEDURAL.match(speaker)),
            }
            turns.append(current)
        else:
            body = clean(para)
            if not body:
                continue
            if current is None:
                current = {"speaker": None, "lang": lang or "English",
                           "text": body, "is_procedural": False}
                turns.append(current)
            else:
                current["text"] = f"{current['text']} {body}".strip()
                if lang:
                    current["lang"] = lang

    for t in turns:
        t["words"] = len(t["text"].split())
    return turns


def fetch_report(report_id):
    """Full content + metadata for one report."""
    data = post_soft("getHansardTopic", {"id": report_id.rstrip("#")})
    return ((data or {}).get("resultHTML") or {})


def fetch_sitting(day, workers=5):
    """Fetch and parse a complete sitting. Returns a JSON-ready dict."""
    reports, coverage = enumerate_sitting_reports(day)
    if not reports:
        return None

    ids = sorted(reports)

    def one(rid):
        return rid, fetch_report(rid)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = dict(pool.map(one, ids))

    items = []
    for rid in ids:
        meta = fetched.get(rid) or {}
        listing = reports[rid]
        # THE NEW STRUCTURAL PARSER. Replaces the regex parse_turns, which failed on
        # &nbsp;-prefixed paragraphs, styled <strong> tags and names split across tags --
        # each failure merging one speaker's words into the previous speaker's turn.
        # See scraper/hansard_parse.py for the rule and the evidence behind it.
        turns = H.parse_report(meta.get("content") or "")
        # normalise to the shape the rest of the pipeline expects: speaker/text/lang/
        # is_procedural/words. The new parser also yields `time` (the clock stamp) which the
        # old one discarded; it is kept because it is what sitting durations were scraped for.
        turns = [{"speaker": t["speaker"], "text": t["text"],
                  "lang": t.get("lang") or "English",
                  "is_procedural": bool(t.get("is_procedural")),
                  "time": t.get("time"),
                  "words": len((t["text"] or "").split())}
                 for t in turns]
        group = group_for(meta.get("reportType") or listing.get("reportType"), rid)
        items.append({
            "report_id": rid,
            "report_type": meta.get("reportType") or listing.get("reportType"),
            "group": group,
            "title": (meta.get("title") or listing.get("title") or "").strip(),
            "sitting_date": meta.get("sittingDate") or listing.get("sittingDate"),
            "parliament_no": meta.get("parlNo"),
            "sitting_no": meta.get("sittingNo"),
            "volume_no": meta.get("volumeNo"),
            "mp_names": meta.get("mpNames"),
            "words": sum(t["words"] for t in turns),
            "turns": turns,
        })

    items.sort(key=lambda r: -r["words"])
    coverage["words"] = sum(i["words"] for i in items)
    coverage["turns"] = sum(len(i["turns"]) for i in items)
    tagged = sum(1 for i in items for t in i["turns"] if t["speaker"])
    coverage["speaker_attribution"] = (
        round(tagged / coverage["turns"], 3) if coverage["turns"] else None)

    return {"date": day, "coverage": coverage, "reports": items}


# -------------------------------------------------------------------- CLI
def main(argv):
    if len(argv) < 2:
        print("usage: parsnips_fetch.py <YYYY-MM-DD> [out.json]")
        print("       parsnips_fetch.py --discover <start> <end>")
        return 1

    if argv[1] == "--discover":
        sittings = discover_sittings(argv[2], argv[3])
        for day, n in sittings:
            print(f"  {day}  reports={n}")
        print(f"{len(sittings)} sitting(s)")
        return 0

    day = argv[1]
    out = argv[2] if len(argv) > 2 else f"sitting_{day}.json"
    sitting = fetch_sitting(day)
    if not sitting:
        print(f"no sitting on {day}")
        return 1

    cov = sitting["coverage"]
    print(f"{day}: {cov['collected']}/{cov['max_result']} reports "
          f"(coverage {cov['ratio']}), {cov['words']:,} words, "
          f"{cov['turns']} turns, attribution {cov['speaker_attribution']}")
    for r in sitting["reports"][:10]:
        print(f"   {r['words']:7,d}w  {r['group']:11s} {r['title'][:64]}")

    # Atomic: the sitting JSON is read by the site builder, so a truncated write
    # could be consumed mid-build. Delegates to storage so there is one atomic
    # writer in the project and the parent directory is always created -- a local
    # copy is what allowed summaries/2016/ to be missing and kill a run.
    storage.write_json_atomic(out, sitting)
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
