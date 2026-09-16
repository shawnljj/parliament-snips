"""
Parsnips — backfill every 2026 sitting.

Designed for a long unattended run:
  * RESUMABLE. A sitting already on disk is skipped, so a killed run can just be
    restarted. Each sitting is written atomically (temp file + rename) so a
    crash mid-write cannot leave a truncated JSON that poisons later runs.
  * CHECKPOINTED. Progress is appended to backfill.log and a machine-readable
    backfill_status.json, so a supervisor can poll without parsing stdout.
  * POLITE. Sequential enumeration with a pause; content fetch is mildly
    parallel. One pass per sitting, and the raw JSON is the cache.
  * HONEST. Coverage ratio is recorded per sitting. A shortfall is reported,
    never hidden.

Usage:
    python3 scraper/backfill.py                # all missing sittings, 2026
    python3 scraper/backfill.py --dates 2026-08-05 2026-07-07
    python3 scraper/backfill.py --force        # refetch even if present
    python3 scraper/backfill.py --discover     # re-derive the sitting list first
"""
import argparse
import datetime
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from parsnips_fetch import (  # noqa: E402
    discover_sittings, enumerate_sitting_reports, fetch_report, group_for,
    parse_turns,
)
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

DATA = os.path.join(ROOT, "data")
INDEX = os.path.join(DATA, "sittings.json")
STATUS = os.path.join(ROOT, "backfill_status.json")
LOG = os.path.join(ROOT, "backfill.log")

# Verified sitting dates for 2026 (day-probe sweep, 16 Sep 2026).
SITTINGS_2026 = [
    "2026-01-12", "2026-01-13", "2026-01-14",
    "2026-02-03", "2026-02-04", "2026-02-12",
    "2026-02-24", "2026-02-25", "2026-02-26", "2026-02-27",
    "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06",
    "2026-04-07", "2026-04-08",
    "2026-05-05", "2026-05-06", "2026-05-07",
    "2026-07-07",
    "2026-08-04", "2026-08-05",
]

WORKERS = 5
# Enumeration workers: the section sweeps are independent, so running them
# concurrently roughly halves per-sitting wall time (293s -> ~150s measured).
# Kept modest -- this is a public government portal and we are guests.
ENUM_WORKERS = 4


def log(msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def write_json_atomic(path, obj):
    """Write via temp + rename so a crash cannot leave a partial file behind."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def sitting_path(date):
    return os.path.join(DATA, f"sitting_{date}.json")


def fetch_one(date):
    """Fetch, parse and normalise a single sitting. Returns the sitting dict."""
    reports, coverage = enumerate_sitting_reports(date, workers=ENUM_WORKERS)
    if not reports:
        return None

    ids = sorted(reports)

    def one(rid):
        return rid, fetch_report(rid)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fetched = dict(pool.map(one, ids))

    items, failures = [], 0
    for rid in ids:
        meta = fetched.get(rid) or {}
        if not meta:
            failures += 1
        listing = reports[rid]
        turns = parse_turns(meta.get("content") or "")
        rtype = meta.get("reportType") or listing.get("reportType")
        items.append({
            "report_id": rid,
            "report_type": rtype,
            "group": group_for(rtype, rid),
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
    words = sum(r["words"] for r in items)
    turns = sum(len(r["turns"]) for r in items)
    tagged = sum(1 for r in items for t in r["turns"] if t["speaker"])
    mx = coverage.get("max_result") or len(items)
    coverage.update({
        "collected": len(items),
        "ratio": round(len(items) / mx, 3) if mx else None,
        "words": words,
        "turns": turns,
        "speaker_attribution": round(tagged / turns, 3) if turns else None,
        "empty_reports": failures,
    })
    return {"date": date, "coverage": coverage, "reports": items}


def rebuild_index():
    """Summarise every sitting on disk into data/sittings.json."""
    entries = []
    for fn in sorted(os.listdir(DATA)):
        if not (fn.startswith("sitting_") and fn.endswith(".json")):
            continue
        try:
            with open(os.path.join(DATA, fn), encoding="utf-8") as fh:
                sit = json.load(fh)
        except (OSError, ValueError) as exc:
            log(f"  index: skipping {fn} ({exc})")
            continue
        cov = sit.get("coverage", {})
        groups = {}
        for r in sit["reports"]:
            g = groups.setdefault(r["group"], {"n": 0, "words": 0})
            g["n"] += 1
            g["words"] += r["words"]
        entries.append({
            "date": sit["date"],
            "collected": cov.get("collected"),
            "max_result": cov.get("max_result"),
            "ratio": cov.get("ratio"),
            "words": cov.get("words"),
            "turns": cov.get("turns"),
            "speaker_attribution": cov.get("speaker_attribution"),
            "groups": groups,
        })
    entries.sort(key=lambda e: e["date"])
    write_json_atomic(INDEX, {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                             "sittings": entries})
    return entries


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--discover", action="store_true",
                    help="re-derive sitting dates from the API before running")
    args = ap.parse_args(argv)

    os.makedirs(DATA, exist_ok=True)
    dates = list(args.dates) if args.dates else list(SITTINGS_2026)

    if args.discover:
        log("discovering sittings 2026-01-01 .. today ...")
        found = discover_sittings("2026-01-01",
                                 datetime.date.today().isoformat())
        dates = [d for d, _ in found]
        log(f"  discovered {len(dates)} sittings: {', '.join(dates)}")

    todo = [d for d in dates
            if args.force or not os.path.exists(sitting_path(d))]
    log(f"backfill start: {len(todo)} to fetch, {len(dates) - len(todo)} already on disk")

    status = {"started": datetime.datetime.now().isoformat(timespec="seconds"),
              "total": len(dates), "todo": len(todo), "done": [], "failed": [],
              "current": None}
    write_json_atomic(STATUS, status)

    t_start = time.time()
    for i, date in enumerate(todo, 1):
        status["current"] = date
        write_json_atomic(STATUS, status)
        t0 = time.time()
        try:
            sit = fetch_one(date)
            if sit is None:
                log(f"[{i}/{len(todo)}] {date}  no reports (not a sitting?)")
                status["failed"].append({"date": date, "error": "no reports"})
                continue
            write_json_atomic(sitting_path(date), sit)
            cov = sit["coverage"]
            log(f"[{i}/{len(todo)}] {date}  "
                f"{cov['collected']}/{cov['max_result']} ({cov['ratio']}) "
                f"{cov['words']:,}w {cov['turns']}t "
                f"attr={cov['speaker_attribution']} "
                f"empty={cov['empty_reports']} "
                f"[{time.time() - t0:.0f}s]")
            status["done"].append({"date": date, "ratio": cov["ratio"],
                                   "words": cov["words"], "secs": round(time.time() - t0)})
        except Exception as exc:                               # noqa: BLE001
            log(f"[{i}/{len(todo)}] {date}  FAILED: {exc}")
            log(traceback.format_exc())
            status["failed"].append({"date": date, "error": str(exc)})
        write_json_atomic(STATUS, status)
        rebuild_index()

    status["current"] = None
    status["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    status["elapsed_secs"] = round(time.time() - t_start)
    write_json_atomic(STATUS, status)
    entries = rebuild_index()
    log(f"backfill done in {status['elapsed_secs']}s: "
        f"{len(status['done'])} fetched, {len(status['failed'])} failed")
    log(f"index now has {len(entries)} sitting(s)")
    if status["failed"]:
        log("failed dates: " + ", ".join(f["date"] for f in status["failed"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
