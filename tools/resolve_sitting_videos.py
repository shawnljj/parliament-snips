#!/usr/bin/env python3
"""Resolve each sitting to its official MDDI Singapore YouTube video.

MDDI live-streams every sitting onto @SingaporeMDDI and keeps the archive, so the
recording is the citable primary source behind the brief. The link is a convenience
for a reader who wants to check the brief against what was actually said.

COVERAGE IS NOT COMPLETE AND MUST NOT BE FAKED. The channel's own listing endpoint
refuses to be paged by the available tooling, so this resolves by SEARCH, which
reaches recent sittings (2026 reliably, 2025 partially) and does not reach 2024 and
earlier. Where no video resolves, NOTHING is written -- the page simply omits the
link. A wrong link is worse than no link.

MATCHING RULES, all of them required:
  * the title must parse as a sitting on the date we hold, under the title format MDDI
    uses ("Parliament Sitting 5 Aug 2026")
  * the channel must be MDDI Singapore -- an unofficial re-upload of a sitting is not
    the official record
  * "[English interpretation]" variants are skipped in favour of the original audio

Cached and resumable in pipeline/sitting_videos.json; a re-run only checks dates it
does not already hold a verdict for.

Usage:
  python3 tools/resolve_sitting_videos.py --years 2026
  python3 tools/resolve_sitting_videos.py              # every sitting
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "pipeline", "sitting_videos.json")
CHANNEL = "MDDI Singapore"

MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}
for _k, _v in list(MONTHS.items()):
    MONTHS[_k[:3]] = _v


def yt_search(query, timeout=180):
    """Return [(title, duration_s, video_id, channel)] from a YouTube search."""
    try:
        r = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--no-warnings",
             "--print", "%(title)s\t%(duration)s\t%(id)s\t%(channel)s", query],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return []
    rows = []
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            rows.append(parts[:4])
    return rows


def day_label(date):
    """2026-08-05 -> '5 Aug 2026', the shape MDDI titles use."""
    y, m, d = date.split("-")
    mon = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"][int(m) - 1]
    return f"{int(d)} {mon[:3]} {y}", f"{int(d)} {mon} {y}"


def resolve(date):
    short, long_ = day_label(date)
    seen = {}
    for q in (f"ytsearch5:Parliament Sitting {short}",
              f"ytsearch5:Parliament Sitting {long_}"):
        for title, dur, vid, chan in yt_search(q):
            if chan.strip() != CHANNEL:
                continue
            if "interpretation" in title.lower():
                continue
            m = re.match(r"Parliament Sitting (\d{1,2}) ([A-Za-z]+) (\d{4})", title.strip())
            if not m:
                continue
            mon = MONTHS.get(m.group(2).lower())
            if not mon:
                continue
            got = f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d}"
            if got != date:
                continue
            seen[vid] = {"video_id": vid, "title": title.strip(),
                         "duration_s": float(dur) if dur not in ("NA", "None") else None}
    if not seen:
        return None
    # prefer the longest: a full sitting beats a clip
    best = max(seen.values(), key=lambda v: v.get("duration_s") or 0)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=1.0)
    a = ap.parse_args()

    have = sorted(glob.glob(os.path.join(ROOT, "data", "20*", "sitting_*.json")))
    days = [re.search(r"sitting_(\d{4}-\d{2}-\d{2})", p).group(1) for p in have]
    if a.years:
        ys = {y.strip() for y in a.years.split(",")}
        days = [d for d in days if d[:4] in ys]
    if a.limit:
        days = days[:a.limit]

    cache = {}
    if os.path.exists(OUT):
        try:
            cache = json.load(open(OUT, encoding="utf-8"))
        except ValueError:
            cache = {}

    todo = [d for d in days if d not in cache]
    print(f"sittings: {len(days)}  cached: {len(days) - len(todo)}  to check: {len(todo)}",
          flush=True)
    if not todo:
        found = sum(1 for d in days if cache.get(d))
        print(f"nothing to do; {found}/{len(days)} have a video")
        return 0

    t0 = time.time()
    for i, date in enumerate(todo, 1):
        try:
            r = resolve(date)
        except Exception as e:                                # noqa: BLE001
            r = None
            print(f"      {date}: {type(e).__name__}: {str(e)[:70]}", flush=True)
        cache[date] = r                                  # None is a real verdict: not found
        if i % 5 == 0 or i == len(todo):
            tmp = OUT + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, OUT)
        el = time.time() - t0
        rate = el / i
        print(f"  [{i}/{len(todo)}] {date}  "
              f"{('found ' + (cache[date] or {}).get('video_id', '')) if cache[date] else 'not found':24s} "
              f"({rate:.0f}s each, ~{rate * (len(todo) - i) / 60:.0f} min left)", flush=True)
        time.sleep(a.sleep)

    found = [d for d in days if cache.get(d)]
    print(f"\nwrote {OUT}")
    print(f"  video found: {len(found)}/{len(days)}")
    by_year = {}
    for d in days:
        y = d[:4]
        by_year.setdefault(y, [0, 0])
        by_year[y][1] += 1
        if cache.get(d):
            by_year[y][0] += 1
    for y in sorted(by_year):
        f, t = by_year[y]
        print(f"    {y}: {f}/{t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
