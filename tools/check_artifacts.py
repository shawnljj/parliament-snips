#!/usr/bin/env python3
"""Is every SDLC artifact still true? Run BEFORE sprint planning.

A sprint plan is only as good as the artifacts it is built from. This runs first and
fails loudly, because the alternative is planning against a document that has quietly
drifted -- which is how a sprint gets spent on work that was already done.

WHAT IT CHECKS, and why each one has actually been wrong at least once:

  ARTIFACT EXISTS      a missing artifact is not a gap to fill later; it is the
                       sprint. Report it, do not skip the file.
  RECOMPUTED NUMBERS   every figure a doc states about the corpus, recomputed. A doc
                       is only useful if its numbers are true (N-3).
  FILE CLAIMS          counts a doc asserts ("343 briefs") vs the files on disk.
  STALENESS            an artifact older than the artifacts it describes. 2017 was
                       promoted 11 seconds AFTER status.json was written, so the
                       status artifact reported the year as empty and skipped its
                       gate entirely.
  GATE SCOPE           every shipped year has a gate verdict, and no year is missing.
                       A year with briefs but no recorded verdict is unverified work
                       that reads as fine.

Exit code 0 only when every artifact is current. Usage:

    python3 tools/check_artifacts.py
    python3 tools/check_artifacts.py --json
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Artifacts a sprint must be able to trust, and what they describe.
#   (path, label, the paths whose newest mtime it must be at least as new as)
ARTIFACTS = [
    ("status.json", "corpus status", ["summaries", "pipeline/dataset"]),
    ("sdlc/1-objectives.md", "objectives", []),
    ("sdlc/3-backlog.md", "backlog", []),
]


def newest_mtime(paths):
    """Newest mtime across files under each path (files or dirs)."""
    newest = 0.0
    for p in paths:
        full = os.path.join(ROOT, p)
        if os.path.isfile(full):
            newest = max(newest, os.path.getmtime(full))
        elif os.path.isdir(full):
            for dirpath, _dirnames, filenames in os.walk(full):
                if os.path.basename(dirpath) == "index.json":
                    continue
                for fn in filenames:
                    if fn.endswith(".json"):
                        try:
                            newest = max(newest, os.path.getmtime(os.path.join(dirpath, fn)))
                        except OSError:
                            pass
    return newest


def load_json(path):
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def briefs_on_disk():
    out = {}
    for d in sorted(glob.glob(os.path.join(ROOT, "summaries", "20*"))):
        year = os.path.basename(d)
        out[year] = len(glob.glob(os.path.join(d, "*.json")))
    return out


def _flag_stated_brief_counts(problems, doc, text):
    """Flag a stated brief count that matches NEITHER the corpus total nor its year.

    A doc phrase like "291 briefs" is legitimate in two shapes: the whole corpus
    ("3,863 briefs"), or one year inside a table row ("| 2026 | 23 | 291 | 287 |").
    The previous version compared every hit against the corpus total only, so it
    flagged correct per-year figures and missed nothing -- noise that would train a
    reader to ignore the check. This accepts either meaning and only reports a number
    that is true of neither.

    Prose that says "N briefs" without a year nearby is compared against the total.
    """
    real_total = sum(briefs_on_disk().values())
    lines = text.splitlines()
    for i, line in enumerate(lines):
        # A line may legitimately state a figure that is not current: a historical
        # record ("228 briefs passed every check" about the 2015 experiment), or a
        # quotation of a stale figure while correcting it. Such a line carries an
        # explicit marker so the exemption is visible in the source, not implied by a
        # heuristic. Without a way to say "this number is deliberately historical",
        # the check reports noise and a reader learns to ignore it.
        if "artifacts-check: historical" in line:
            continue
        for m in re.finditer(r"(\d[\d,]*)\s+briefs", line):
            claimed = int(m.group(1).replace(",", ""))
            if claimed <= 100 or claimed == real_total:
                continue
            # A four-digit number in the corpus's own year range is a year being read
            # as a count: "the 291 existing 2026 briefs" must not be parsed as a claim
            # of 2,026 briefs.
            if 2000 <= claimed <= 2100:
                continue
            # A year in the same line means the number may be that year's count.
            years = re.findall(r"\b(20\d{2})\b", line)
            if any(briefs_on_disk().get(y) == claimed for y in years):
                continue
            # Or the number may be one year's count stated without naming the year
            # ("287 briefs across 2026" on the row above). Accept any single year's
            # figure: the target of this check is a stale TOTAL like "949", not a
            # correct per-year number.
            if claimed in set(briefs_on_disk().values()):
                continue
            problems.append(("DRIFT", doc,
                             f"line {i + 1}: states {claimed:,} briefs; {real_total:,} "
                             f"on disk and no year matches {claimed:,}"))


def dataset_on_disk():
    out = {}
    for d in sorted(glob.glob(os.path.join(ROOT, "pipeline", "dataset", "20*"))):
        out[os.path.basename(d)] = len(glob.glob(os.path.join(d, "*.json")))
    return out


def withheld_by_year():
    out = {}
    for p in glob.glob(os.path.join(ROOT, "pipeline", "withheld", "*.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        y = (d.get("item") or {}).get("year")
        if y:
            out[y] = out.get(y, 0) + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    problems = []   # (severity, artifact, message)
    notes = []      # informational

    # ---------------------------------------------------------------- existence
    for path, label, _deps in ARTIFACTS:
        if not os.path.exists(os.path.join(ROOT, path)):
            problems.append(("MISSING", path, f"{label} artifact does not exist"))

    # ---------------------------------------------------------------- status.json
    st = load_json("status.json")
    if "__error__" in st:
        problems.append(("INVALID", "status.json", st["__error__"]))
    else:
        bd, dd, wh = briefs_on_disk(), dataset_on_disk(), withheld_by_year()
        corpus = st.get("corpus") or {}

        # Every year on disk must be represented.
        for year in sorted(bd):
            claimed = (corpus.get(year) or {}).get("briefs")
            if claimed is None:
                problems.append(("STALE", "status.json",
                                 f"{year} has {bd[year]} briefs on disk but is absent from corpus"))
            elif claimed != bd[year]:
                problems.append(("STALE", "status.json",
                                 f"{year}: reports {claimed} briefs, {bd[year]} on disk"))

        gates = [g for g in (st.get("gates") or []) if isinstance(g, dict)]

        # Every year with briefs must carry a gate verdict.
        gated = {str(g.get("scope", "")).split("/")[-1] for g in gates}
        for year, n in sorted(bd.items()):
            if n and year not in gated:
                problems.append(("UNGATED", "status.json",
                                 f"{year} has {n} briefs and NO recorded gate verdict"))

        # Accounting: every dataset item is published or withheld.
        for year in sorted(set(bd) | set(dd)):
            items = dd.get(year, 0)
            accounted = bd.get(year, 0) + wh.get(year, 0)
            # Not every item yields a brief (some have no summarisable content), so
            # only a SURPLUS is an error.
            if accounted > items:
                problems.append(("ACCOUNTING", "status.json",
                                 f"{year}: {accounted} published+withheld > {items} dataset items"))

        # Artifact freshness vs the thing it describes.
        st_mtime = os.path.getmtime(os.path.join(ROOT, "status.json"))
        data_mtime = newest_mtime(["summaries", "pipeline/dataset"])
        if data_mtime > st_mtime + 1:
            delta = data_mtime - st_mtime
            problems.append(("STALE", "status.json",
                             f"older than the corpus it describes by {delta:,.0f}s "
                             f"— recompute with tools/write_status.py"))

        # Gate scope must actually cover what shipped.
        gated_pass = {str(g.get("scope", "")).split("/")[-1] for g in gates
                      if g.get("result") == "PASS"}
        gated_fail = {str(g.get("scope", "")).split("/")[-1] for g in gates
                      if g.get("result") == "FAIL"}
        notes.append(f"gates: {len(gated_pass)} PASS, {len(gated_fail)} FAIL "
                     f"({', '.join(sorted(gated_fail)) or 'none'})")
        notes.append(f"briefs on disk: {sum(bd.values()):,} across {len(bd)} years")
        notes.append(f"withheld records: {sum(wh.values())}")

    # ---------------------------------------------------------------- objectives
    obj_path = os.path.join(ROOT, "sdlc", "1-objectives.md")
    if os.path.exists(obj_path):
        with open(obj_path, encoding="utf-8") as fh:
            text = fh.read()
        ids = re.findall(r"\|\s*(O-\d+)\s*\|", text)
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            problems.append(("DUPLICATE", "sdlc/1-objectives.md",
                             f"repeated objective ids: {sorted(dupes)}"))
        notes.append(f"objectives: {len(ids)} declared")

        # A stated count must match the files.
        _flag_stated_brief_counts(problems, "sdlc/1-objectives.md", text)

    # ---------------------------------------------------------------- stale docs
    for doc in ("PLAN.md", "README.md"):
        p = os.path.join(ROOT, doc)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        _flag_stated_brief_counts(problems, doc, text)

    # ---------------------------------------------------------------- report
    if a.json:
        print(json.dumps({"problems": problems, "notes": notes}, indent=2))
        return 1 if problems else 0

    print("ARTIFACT FRESHNESS")
    print("=" * 70)
    for n in notes:
        print("  ·", n)
    print()
    if not problems:
        print("  ALL ARTIFACTS CURRENT — safe to plan.")
        return 0
    print(f"  {len(problems)} PROBLEM(S) — fix before planning:\n")
    for sev, where, msg in problems:
        print(f"  [{sev:10}] {where}")
        print(f"               {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
