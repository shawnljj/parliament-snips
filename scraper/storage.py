"""Single source of truth for where Parsnips stores things, and under what key.

The original layout was one flat `data/` of `sitting_<date>.json` and one flat
`summaries/` whose filenames were derived from brief TITLES. That was fine for 23
sittings and does not survive a backfill to 2016 (~400 sittings, tens of thousands
of reports). Three things went wrong with it:

  1. Titles are not unique and they change. Two Hansard records can carry the same
     title (we already had to merge motion-3008 + motion-3010 for exactly that
     reason), an edited title orphans the old file, and a long title produces a
     filename that can blow past filesystem limits -- Vercel's git unpack fails
     outright with GIT_REPO_FILENAME_TOO_LONG.
  2. One flat directory of 56,000 files is not listable, not diffable, and not
     reviewable in a commit.
  3. Nothing recorded which era a report came from, so a source link or a
     validation pass had to guess.

This module fixes all three and is imported by every tool so they cannot drift.

    data/<year>/sitting_<date>.json      the fetched sitting view
    summaries/<year>/<stable-key>.json   one brief, keyed by report identity
    data/manifest.json                   what exists, what is summarised, what is stale

Keys are derived from `_meta.report_ids` -- the stable identity -- never from a
title. A merged brief sorts its ids so the key is deterministic:

    ["motion-3008", "motion-3010"]  ->  motion-3008+3010.json
    six budget-* ids               ->  budget-2857+2859+2861+2867+2871+2873.json

Both are fixed-length-ish, collision-free, and a title edit becomes a one-line
diff inside the JSON instead of a renamed file.

Sharding is by YEAR, not by parliament. A batch operates on a year, so year is the
grain that makes "which files belong to this batch" a directory listing. The
parliament number is recorded on every report and in the manifest, so it is
available for validation and grouping without being spent on directory nesting.
"""

import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DATA = os.path.join(ROOT, "data")
SUMMARIES = os.path.join(ROOT, "summaries")
MANIFEST = os.path.join(DATA, "manifest.json")

# Recorded on every report. The modern Hansard API has served this format since
# Parliament 12/13; the pre-2016 `sprs2` shape is deliberately out of scope, so a
# constant is honest here rather than a per-report field we would have to guess.
DEFAULT_REPORT_VERSION = "sprs3"

# The report groups that get summarised into briefs. Mirrors SUMMARISABLE in the
# summariser, kept here so the manifest can report an honest denominator instead of
# counting the ~135 written answers per sitting that are deliberately not briefed.
SUMMARISABLE_GROUPS = ("bill", "statement", "motion", "budget", "adjournment", "oral")

# Below this many words there is nothing to condense. Mirrors the summariser's
# --min-words default so "not summarised" and "not summarisable" stay distinct.
MIN_SUMMARISABLE_WORDS = 150

# The Hansard reader route that takes a report id. Correct for every era we store
# (2016+). Kept beside the version constant so the two cannot disagree.
TOPIC_URL = "https://sprs.parl.gov.sg/search/#/sprs3topic?reportid={rid}"


# ---------------------------------------------------------------- atomic writes

def write_text_atomic(path, text):
    """Write text atomically: temp file in the same directory, then os.replace.

    `open(path, "w")` truncates the target to zero bytes immediately and writes
    afterwards, so a reader can observe an empty or half-written file. On the site
    that produced a blank white page. os.replace is atomic on the same filesystem,
    so a reader sees either the complete old file or the complete new one.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_json_atomic(path, obj, indent=1):
    write_text_atomic(path, json.dumps(obj, indent=indent, ensure_ascii=False))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------- shard paths

def year_of(date):
    """'2026-08-05' -> '2026'. Accepts a date or an ISO-ish string."""
    return str(date)[:4]


def sitting_path(date, root=None):
    """data/<year>/sitting_<date>.json"""
    return os.path.join(root or DATA, year_of(date), f"sitting_{date}.json")


def summary_path(key, year, root=None):
    """summaries/<year>/<key>.json"""
    return os.path.join(root or SUMMARIES, str(year), f"{key}.json")


# ------------------------------------------------------------------- stable keys

_ID_RE = re.compile(r"^([a-z][a-z0-9-]*?)-(\d+)$")


def _split_id(rid):
    """'motion-3008' -> ('motion', 3008). Falls back to (rid, 0)."""
    m = _ID_RE.match(rid or "")
    if not m:
        return (rid or "unknown", 0)
    return (m.group(1), int(m.group(2)))


def stable_key(report_ids):
    """A deterministic, title-free filename stem for a set of report ids.

    All ids sharing one prefix collapse to `prefix-n1+n2+...` with the numbers
    sorted, so the key does not depend on the order the reports happened to be
    discovered in. Mixed prefixes (a brief covering unlike records) join the full
    ids with '+' instead, which is rare and stays unambiguous.
    """
    ids = sorted({r for r in (report_ids or []) if r})
    if not ids:
        return "unknown"
    parts = [_split_id(r) for r in ids]
    prefixes = {p for p, _ in parts}
    if len(prefixes) == 1:
        prefix = parts[0][0]
        nums = "+".join(str(n) for _, n in sorted(parts))
        return f"{prefix}-{nums}"
    return "+".join(ids)


def summary_key_from(brief):
    """Stable key for a brief dict, from its _meta.report_ids."""
    return stable_key((brief.get("_meta") or {}).get("report_ids") or [])


def summary_year_from(brief):
    """Which year directory a brief belongs in: the EARLIEST sitting it draws on.

    A brief can span two sitting days (the Budget debate ran 24-25 Feb 2026). The
    earliest date keeps a brief filed under the year it STARTED, which is stable
    even if a later sitting is added to it.
    """
    dates = (brief.get("_meta") or {}).get("sitting_dates") or []
    if dates:
        return year_of(min(dates))
    ids = (brief.get("_meta") or {}).get("report_ids") or []
    # Fall back to the highest-numbered ids, which are the most recent records.
    return "unknown" if not ids else year_of("2026")


# ---------------------------------------------------------------------- manifest

def source_hash(sitting):
    """A cheap fingerprint of a sitting's CONTENT, for staleness detection.

    Hashes the sorted (report_id, words) pairs rather than the full payload: that
    catches a report being added, removed or re-fetched with a different length,
    which is what "is my local copy stale" actually means, and it stays fast on a
    118,000-word sitting.
    """
    pairs = sorted((r.get("report_id", ""), int(r.get("words") or 0))
                   for r in sitting.get("reports", []))
    blob = "\n".join(f"{rid}\t{w}" for rid, w in pairs)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def load_manifest(root=None):
    return read_json(os.path.join(root or DATA, "manifest.json"),
                     {"version": 1, "sittings": {}}) or {"version": 1, "sittings": {}}


def save_manifest(manifest, root=None):
    manifest["sittings"] = dict(sorted(manifest.get("sittings", {}).items()))
    write_json_atomic(os.path.join(root or DATA, "manifest.json"), manifest)


def describe_sitting(sitting, summarised_groups=None):
    """The manifest entry for one sitting.

    `summarised_groups` is an optional set of report ids that have a brief, so the
    entry can record coverage of summarisation as well as of fetching. Both live in
    one record so "what is missing" is a query, not a directory glob.
    """
    cov = sitting.get("coverage", {}) or {}
    reports = sitting.get("reports", []) or []
    groups = {}
    for r in reports:
        g = groups.setdefault(r.get("group") or "other", {"n": 0, "words": 0})
        g["n"] += 1
        g["words"] += int(r.get("words") or 0)

    parl = next((r.get("parliament_no") for r in reports if r.get("parliament_no")), None)
    dates = {r.get("sitting_date") for r in reports if r.get("sitting_date")}
    entry = {
        "date": sitting.get("date"),
        "year": year_of(sitting.get("date")),
        "parliament_no": parl,
        "volume_no": next((r.get("volume_no") for r in reports if r.get("volume_no")), None),
        "sitting_no": next((r.get("sitting_no") for r in reports if r.get("sitting_no")), None),
        "report_version": next((r.get("report_version") for r in reports
                                if r.get("report_version")), DEFAULT_REPORT_VERSION),
        "reports": len(reports),
        "words": cov.get("words") or sum(int(r.get("words") or 0) for r in reports),
        "turns": cov.get("turns"),
        "collected": cov.get("collected"),
        "max_result": cov.get("max_result"),
        "ratio": cov.get("ratio"),
        "speaker_attribution": cov.get("speaker_attribution"),
        "enumeration": cov.get("enumeration"),
        "groups": groups,
        "source_hash": source_hash(sitting),
    }
    if summarised_groups is not None:
        # Denominator is only what SHOULD have a brief: the summarisable groups,
        # above the word floor. Counting every report would report a permanent
        # 12% forever because ~135 written answers per sitting are never briefed.
        want = sorted(r.get("report_id") for r in reports
                      if (r.get("group") in SUMMARISABLE_GROUPS
                          and int(r.get("words") or 0) >= MIN_SUMMARISABLE_WORDS))
        done = len(set(want) & set(summarised_groups))
        entry["summarisation"] = {
            "summarisable": len(want),
            "summarised": done,
            "complete": done == len(want),
        }
        # The ids themselves, so progress can be recomputed WITHOUT re-reading the
        # sitting. The summariser writes briefs continuously over days; without
        # this, updating its progress means re-reading every sitting file each time.
        entry["summarisable_ids"] = want
    return entry


def refresh_summarisation(manifest, summarised_ids):
    """Recompute each sitting's summarisation progress from stored ids.

    Cheap by design: needs only the manifest, not the sitting files. This exists
    because the summariser writes briefs but the manifest was only ever refreshed
    by a backfill run -- so during a long summarisation the manifest claimed
    "0 of 382" while 22 briefs sat on disk. Progress tracking has to be honest
    across a multi-day run.

    Sittings whose entry predates `summarisable_ids` are left alone and counted in
    the return value, so a caller can tell it needs a full rebuild_index().
    """
    done = set(summarised_ids)
    updated = stale = 0
    for entry in manifest.get("sittings", {}).values():
        want = entry.get("summarisable_ids")
        if want is None:
            stale += 1
            continue
        n = len(set(want) & done)
        entry["summarisation"] = {
            "summarisable": len(want),
            "summarised": n,
            "complete": n == len(want),
        }
        updated += 1
    return updated, stale
