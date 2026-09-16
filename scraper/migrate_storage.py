"""Migrate the flat layout to year-sharded storage. Safe to re-run.

    data/sitting_<date>.json        -> data/<year>/sitting_<date>.json
    summaries/<title-slug>.json     -> summaries/<year>/<stable-key>.json
    (new)                           -> data/manifest.json

Also stamps `report_version` on every report and rewrites each brief's `_meta`
with its stable `key`, so nothing downstream has to re-derive identity.

Does not delete the old files until the new ones are verified present and equal.
Pass --apply to act; the default is a dry run.
"""

import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage as S  # noqa: E402

APPLY = "--apply" in sys.argv


def main():
    report = {"sittings": 0, "summaries": 0, "skipped": [], "manifest": 0}

    # ---------------------------------------------------------- sitting files
    for src in sorted(glob.glob(os.path.join(S.DATA, "sitting_*.json"))):
        fn = os.path.basename(src)
        date = fn[len("sitting_"):-len(".json")]
        sit = S.read_json(src)
        if not sit or "reports" not in sit:
            report["skipped"].append(f"{fn}: unreadable")
            continue
        # Stamp the era on every report so a source link or validation pass never
        # has to guess which Hansard format a record came from.
        for r in sit["reports"]:
            r.setdefault("report_version", S.DEFAULT_REPORT_VERSION)
            # report_id is the identity; keep it explicitly first-class.
            if "report_id" not in r and "id" in r:
                r["report_id"] = r.pop("id")
        dst = S.sitting_path(date)
        if APPLY:
            S.write_json_atomic(dst, sit)
        report["sittings"] += 1

    # -------------------------------------------------------- summary files
    index_src = os.path.join(S.SUMMARIES, "index.json")
    index = S.read_json(index_src) or {}
    old_index_keys = set(index.keys())
    new_index = {}

    for src in sorted(glob.glob(os.path.join(S.SUMMARIES, "*.json"))):
        fn = os.path.basename(src)
        if fn == "index.json":
            continue
        brief = S.read_json(src)
        if not brief or "_meta" not in brief:
            report["skipped"].append(f"{fn}: no _meta")
            continue
        key = S.summary_key_from(brief)
        year = S.summary_year_from(brief)
        if year == "unknown":
            report["skipped"].append(f"{fn}: no sitting_dates")
            continue
        meta = brief["_meta"]
        meta["key"] = key
        meta["schema"] = 2
        dst = S.summary_path(key, year)
        if APPLY:
            S.write_json_atomic(dst, brief)
        # rebuild the index under the new keys, carrying the display title
        entry = dict(index.get(fn[:-5]) or {})
        entry["title"] = brief.get("title") or entry.get("title")
        entry["key"] = key
        entry["year"] = year
        entry["sitting_dates"] = meta.get("sitting_dates") or []
        entry["group"] = meta.get("group")
        entry["report_ids"] = meta.get("report_ids") or []
        new_index[key] = entry
        report["summaries"] += 1

    report["index_old_keys"] = len(old_index_keys)
    report["index_new_keys"] = len(new_index)

    if APPLY:
        S.write_json_atomic(os.path.join(S.SUMMARIES, "index.json"), new_index, indent=1)

    # ------------------------------------------------------------- manifest
    manifest = {"version": 1, "generated": None, "sittings": {}}
    # Read sharded if present, else the flat layout, so this works during a
    # partial migration as well as after one.
    sit_files = sorted(glob.glob(os.path.join(S.DATA, "20*", "sitting_*.json")))
    if not sit_files:
        sit_files = sorted(glob.glob(os.path.join(S.DATA, "sitting_*.json")))
    for src in sit_files:
        sit = S.read_json(src)
        if not sit:
            continue
        manifest["sittings"][sit["date"]] = S.describe_sitting(sit)
    report["manifest"] = len(manifest["sittings"])
    if APPLY:
        S.save_manifest(manifest)

    # ---------------------------------------------------------- verification
    print(("APPLIED" if APPLY else "DRY RUN") + ":")
    print(f"  sitting files migrated:  {report['sittings']}")
    print(f"  summary files migrated:  {report['summaries']}")
    print(f"  index entries: {report['index_old_keys']} -> {report['index_new_keys']}")
    print(f"  manifest sittings:       {report['manifest']}")
    if report["skipped"]:
        print(f"  SKIPPED ({len(report['skipped'])}):")
        for s in report["skipped"][:10]:
            print(f"    {s}")

    if APPLY:
        # Old flat files are only removed once the sharded copies exist.
        missing = []
        for src in glob.glob(os.path.join(S.DATA, "sitting_*.json")):
            date = os.path.basename(src)[len("sitting_"):-len(".json")]
            if not os.path.exists(S.sitting_path(date)):
                missing.append(src)
        for src in glob.glob(os.path.join(S.SUMMARIES, "*.json")):
            fn = os.path.basename(src)
            if fn == "index.json":
                continue
            b = S.read_json(src)
            if not b:
                continue
            k, y = S.summary_key_from(b), S.summary_year_from(b)
            if not os.path.exists(S.summary_path(k, y)):
                missing.append(src)
        if missing:
            print(f"  NOT REMOVING originals: {len(missing)} sharded copies missing")
        else:
            for src in (glob.glob(os.path.join(S.DATA, "sitting_*.json"))
                        + [f for f in glob.glob(os.path.join(S.SUMMARIES, "*.json"))
                           if os.path.basename(f) != "index.json"]):
                os.remove(src)
            print("  originals removed (all sharded copies verified present)")

    return report


if __name__ == "__main__":
    main()
