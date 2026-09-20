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
    discover_sittings, discover_sittings_cached, enumerate_sitting_reports,
    fetch_report, group_for, parse_report_turns,
)
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
import storage  # noqa: E402  (shared layout: year shards, keys, manifest)

DATA = os.path.join(ROOT, "data")
INDEX = os.path.join(DATA, "sittings.json")
STATUS = os.path.join(ROOT, "backfill_status.json")
LOG = os.path.join(ROOT, "backfill.log")

# Backfill floor.
#
# SET BACK TO 2016 BY THE OWNER (2026-09-20), after 2015 was fetched and summarised.
# The decision: keep the corpus to 2016 onward. 2015 data was measured to be the SAME
# sprs3 era and parsed cleanly (23 sittings, 96.8% attribution, 0 pollution), so this is
# a SCOPE choice rather than a technical limit -- do not re-open it on the grounds that
# 2015 "works". It does. It is out of scope.
#
# 2015 artifacts produced during that experiment were removed: data/2015,
# pipeline/dataset/2015, summaries/2015.
FLOOR_YEAR = 2016

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
    storage.write_json_atomic(path, obj)


def sitting_path(date):
    """Year-sharded: data/<year>/sitting_<date>.json.

    Sharding by year (not by parliament) because a batch operates on a year, so
    year is the grain that makes "which files belong to this batch" a directory
    listing. Parliament number is recorded per report and in the manifest.
    """
    return storage.sitting_path(date)


def fetched_dates():
    """Every sitting already on disk, from the sharded layout."""
    out = []
    for p in sorted(__import__("glob").glob(os.path.join(DATA, "20*", "sitting_*.json"))):
        out.append(os.path.basename(p)[len("sitting_"):-len(".json")])
    return out


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
        turns = parse_report_turns(meta.get("content") or "")
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
            # Which Hansard format this record came from. Recorded in the data
            # rather than inferred from the id, so a source link or a validation
            # pass does not have to guess the era. Always sprs3 for 2016+.
            "report_version": "sprs3",
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


def summarised_ids():
    """Report ids that already have a brief, read from the summary index."""
    idx = storage.read_json(os.path.join(ROOT, "summaries", "index.json")) or {}
    ids = set()
    for entry in idx.values():
        if not isinstance(entry, dict):
            continue
        for rid in (entry.get("report_ids")
                    or (entry.get("_meta") or {}).get("report_ids") or []):
            ids.add(rid)
    return ids


def rebuild_index():
    """Rebuild data/manifest.json and data/sittings.json from what is on disk.

    The manifest is what makes a batched backfill resumable and makes "what is
    missing" a query instead of a directory glob. It records, per sitting: date,
    year, parliament, coverage ratio, report count, words, the per-group split, a
    content hash of the source payload, and how much of it is summarised.
    """
    done = summarised_ids()
    manifest = storage.load_manifest()
    entries = []
    for p in sorted(__import__("glob").glob(os.path.join(DATA, "20*", "sitting_*.json"))):
        sit = storage.read_json(p)
        if not sit:
            log(f"  index: skipping {p}")
            continue
        entry = storage.describe_sitting(sit, summarised_groups=done)
        manifest["sittings"][sit["date"]] = entry
        entries.append(entry)

    manifest["generated"] = datetime.datetime.now().isoformat(timespec="seconds")
    manifest["floor_year"] = FLOOR_YEAR
    manifest["schema"] = 2
    storage.save_manifest(manifest)

    # data/sittings.json stays as the flat, site-facing view it always was.
    entries.sort(key=lambda e: e["date"])
    write_json_atomic(INDEX, {"generated": manifest["generated"],
                              "floor_year": FLOOR_YEAR,
                              "sittings": entries})
    return entries


def batch(year, *, discover=False, force=False, limit=None, refresh_calendar=False):
    """Fetch every sitting of one year. Independently resumable.

    One batch = one year. Run modern-first from 2016 upward. An interrupted batch
    costs only the sittings in flight, because each sitting is written atomically
    and the manifest is rebuilt as we go.

    Discovery is cached per year (data/calendar/<year>.json). Probing a year costs
    ~8 minutes of pure waiting at ~13s per weekday request, so paying it once per
    year rather than once per run is what makes a ten-year backfill practical.
    """
    if year < FLOOR_YEAR:
        log(f"refusing {year}: the backfill floor is {FLOOR_YEAR} "
            f"(earlier years are the legacy sprs2 era, out of scope)")
        return 1

    if discover or refresh_calendar:
        cached = os.path.exists(os.path.join(DATA, "calendar", f"{year}.json"))
        log(f"discovering sittings for {year} ..."
            + ("  (re-probing; cache ignored)" if refresh_calendar and cached else "")
            + ("" if refresh_calendar else "  (cached if previously probed)"))
        found = discover_sittings_cached(year, refresh=refresh_calendar)
        dates = [d for d, _ in found]
        log(f"  {len(dates)} sitting day(s)")
    else:
        # No --discover: use the cached calendar if we have one, else say so.
        found = discover_sittings_cached(year)
        dates = [d for d, _ in found]
        if not dates:
            log(f"no sitting list for {year}; run with --discover")
            return 1
        log(f"  {len(dates)} sitting day(s) from the calendar cache")
    if limit:
        dates = dates[:limit]
    return run_dates(dates, force=force)


def run_dates(dates, *, force=False):
    os.makedirs(DATA, exist_ok=True)
    on_disk = set(fetched_dates())
    todo = [d for d in dates if force or d not in on_disk]
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
    log(f"manifest now has {len(entries)} sitting(s)")
    if status["failed"]:
        log("failed dates: " + ", ".join(f["date"] for f in status["failed"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Batch backfill of Hansard sittings.")
    ap.add_argument("--year", type=int, help="fetch one year (the batch unit)")
    ap.add_argument("--years", type=int, nargs="*",
                    help=f"fetch several years; refuses anything below {FLOOR_YEAR}")
    ap.add_argument("--dates", nargs="*", default=None, help="explicit sitting dates")
    ap.add_argument("--discover", action="store_true",
                    help="re-derive sitting dates from the API before running")
    ap.add_argument("--force", action="store_true", help="re-fetch what is on disk")
    ap.add_argument("--limit", type=int, help="cap sittings per year (for testing)")
    ap.add_argument("--refresh-calendar", action="store_true",
                    help="re-probe the sitting calendar even if one is cached")
    ap.add_argument("--status", action="store_true",
                    help="report what is fetched and what is summarised, then exit")
    args = ap.parse_args(argv)

    if args.status:
        manifest = storage.load_manifest()
        sits = manifest.get("sittings", {})
        if not sits:
            log("manifest empty; run a batch first")
            return 0
        by_year = {}
        for e in sits.values():
            y = by_year.setdefault(e["year"], {"n": 0, "words": 0, "sum": 0, "summ": 0})
            y["n"] += 1
            y["words"] += e.get("words") or 0
            s = e.get("summarisation") or {}
            y["sum"] += s.get("summarisable") or 0
            y["summ"] += s.get("summarised") or 0
        log(f"manifest: {len(sits)} sitting(s), floor {manifest.get('floor_year')}")
        for y in sorted(by_year):
            v = by_year[y]
            log(f"  {y}: {v['n']:>3} sittings  {v['words']:>10,}w  "
                f"summarised {v['summ']}/{v['sum']}")
        return 0

    if args.dates:
        return run_dates(args.dates, force=args.force)

    years = args.years or ([args.year] if args.year else [])
    if not years:
        # Default: the verified 2026 list, preserving the original behaviour.
        return run_dates(SITTINGS_2026, force=args.force)

    rc = 0
    # Modern-first: ascending from the floor, so the most-read years land first.
    for y in sorted(years):
        log(f"=== batch {y} ===")
        rc |= batch(y, discover=args.discover, force=args.force, limit=args.limit,
                    refresh_calendar=args.refresh_calendar)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
