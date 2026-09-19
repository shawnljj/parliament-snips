#!/usr/bin/env python3
"""Extract each sitting's start and end time from the Hansard's own clock stamps.

WHY THIS EXISTS. The dataset has no time-of-day field, and the per-turn clock
stamps that ARE in the source HTML were dropped by the parser. The stamps live in
`<h6>2.03 pm</h6>` elements. Taking the earliest and latest across ALL reports of a
sitting brackets it end to end.

WHY IT FETCHES EVERY REPORT. A shortcut (fetch the first report by document order)
was measured and rejected: `sno` is shared by many reports and is not a reliable
order, so 2026-08-04 came out at 19:25 instead of 12:00. The earliest CHAMBER item
is not necessarily the first listed. All reports are fetched.

WHAT IS SKIPPED. written-answer, ptba and attendance reports are tabled papers with
no chamber timestamps; they are skipped when reading stamps, though they are still
counted for the record.

CACHED, RESUMABLE. Results land in pipeline/sitting_times.json keyed by date, so a
re-run skips what it already has. The job is free -- no model calls.

Usage:
  python3 tools/extract_sitting_times.py            # every sitting
  python3 tools/extract_sitting_times.py --years 2024,2025,2026
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper"))
import parsnips_fetch as PF                                     # noqa: E402

OUT = os.path.join(ROOT, "pipeline", "sitting_times.json")
STAMP = re.compile(r"<h6>\s*(\d{1,2})[.:](\d{2})\s*(am|pm)\s*</h6>", re.I)
ADJ = re.compile(r"Adjourned\s+accordingly\s+at\s+(\d{1,2})[.:](\d{2})\s*(am|pm)", re.I)
SUS = re.compile(r"Sitting\s+accordingly\s+suspended\s+at\s+(\d{1,2})[.:](\d{2})\s*(am|pm)", re.I)
RES = re.compile(r"Sitting\s+resumed\s+at\s+(\d{1,2})[.:](\d{2})\s*(am|pm)", re.I)
NOTIME = ("written-answer", "ptba", "attendance")


def mins(h, m, ap):
    h, ap = int(h), ap.lower()
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h * 60 + int(m)


def load():
    if os.path.exists(OUT):
        try:
            return json.load(open(OUT, encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def save(d):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, OUT)


def one_sitting(day):
    """Earliest and latest chamber stamp across the sitting's reports."""
    rows, _ = PF.enumerate_sitting_reports(day)
    if not rows:
        return {"date": day, "status": "no reports listed"}

    def get(rid_meta):
        rid, meta = rid_meta
        rtype = str((meta or {}).get("reportType") or "").lower()
        try:
            h = PF.fetch_report(rid.rstrip("#"))
        except Exception as e:                                  # noqa: BLE001
            return rtype, [], None
        c = (h or {}).get("content") or ""
        if any(k in rtype for k in NOTIME):
            return rtype, [], None
        return rtype, [mins(*m.groups()) for m in STAMP.finditer(c)], c

    with ThreadPoolExecutor(max_workers=4) as pool:
        res = list(pool.map(get, sorted(rows.items())))

    stamps = sorted(t for _, sub, _ in res for t in sub)
    if not stamps:
        return {"date": day, "status": "no chamber clock stamps",
                "reports": len(rows)}

    # a sitting does not exceed 16h; if a stray stamp looks out of range, drop it
    first, last = stamps[0], stamps[-1]
    if last - first > 16 * 60:
        last = first

    # adjournment / break markers, from the same text, for cross-checking
    adj = sus = res_m = None
    for _, _, c in res:
        if not c:
            continue
        for name, pat in (("adj", ADJ), ("sus", SUS), ("res", RES)):
            m = pat.search(c)
            if not m:
                continue
            v = mins(*m.groups())
            if name == "adj" and adj is None:
                adj = v
            elif name == "sus" and sus is None:
                sus = v
            elif name == "res" and res_m is None:
                res_m = v
    return {"date": day, "status": "ok", "reports": len(rows),
            "stamps": len(stamps),
            "start_min": first, "end_min": last,
            "minutes": last - first,
            "adjourned_min": adj, "suspended_min": sus, "resumed_min": res_m}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4,
                    help="sittings to process at once (default 4)")
    a = ap.parse_args()

    have = sorted(glob.glob(os.path.join(ROOT, "data", "20*", "sitting_*.json")))
    days = [re.search(r"sitting_(\d{4}-\d{2}-\d{2})", p).group(1) for p in have]
    if a.years:
        ys = {y.strip() for y in a.years.split(",")}
        days = [d for d in days if d[:4] in ys]
    if a.limit:
        days = days[:a.limit]

    done = load()
    todo = [d for d in days if d not in done]
    print(f"sittings: {len(days)}  already done: {len(days) - len(todo)}  "
          f"to do: {len(todo)}", flush=True)
    if not todo:
        done_ok = sum(1 for d in days if done.get(d, {}).get("status") == "ok")
        print(f"nothing to do; {done_ok}/{len(days)} have a usable duration")
        return 0

    t0 = time.time()
    done_n = 0
    # SITTING-LEVEL PARALLELISM. Each sitting needs ~20-180 report fetches at ~86s
    # sequentially; 331 sittings that way is ~8 hours. Sittings are independent, so run
    # several at once. Kept modest (4) so the Hansard API is not hammered into rate-limiting
    # -- a 429 storm would cost more than the parallelism saves.
    lock = __import__("threading").Lock()

    def work(day):
        try:
            return day, one_sitting(day)
        except Exception as e:                                  # noqa: BLE001
            return day, {"date": day,
                         "status": f"error: {type(e).__name__}: {str(e)[:90]}"}

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for day, r in pool.map(work, todo):
            with lock:
                done[day] = r
                done_n += 1
                if done_n % 5 == 0 or done_n == len(todo):
                    save(done)
                el = time.time() - t0
                rate = el / done_n
                mins_left = rate * (len(todo) - done_n) / 60
                st = r.get("status")
                dur = (f"{r['minutes'] // 60}h{r['minutes'] % 60:02d}"
                       if r.get("minutes") else "-")
                print(f"  [{done_n}/{len(todo)}] {day}  {st:28s} {dur:>7s}  "
                      f"({rate:.0f}s each, ~{mins_left:.0f} min left)", flush=True)
    save(done)

    got = [d for d in days if done.get(d, {}).get("status") == "ok"]
    print(f"\nwrote {OUT}")
    print(f"  usable duration: {len(got)}/{len(days)}")
    bad = {}
    for d in days:
        s = done.get(d, {}).get("status", "missing")
        if s != "ok":
            bad[s] = bad.get(s, 0) + 1
    for k, v in sorted(bad.items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
