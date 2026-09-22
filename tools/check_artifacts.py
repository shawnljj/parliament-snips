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
    """Dataset items per year, from the CENSUS rather than the payload directories.

    The payload directories are gitignored (~270 MB, deterministically regenerated in
    ~70s by `python3 summariser/build_dataset.py`), so on a fresh clone they are simply
    absent. Reading them here made every year report "349 published+withheld > 0 dataset
    items" -- 11 problems on a clean checkout, caused by the check, not by the corpus.

    pipeline/dataset/index.json IS committed and holds one entry per item with its year,
    so it is the census that survives a clone. It is what this check should have used:
    the dirs are absent on a clone by construction, and where they ARE present they are
    a *rebuild*, so reading them made the check's answer depend on which artifacts
    happened to be on the box. The index is the committed statement of the same set.
    Where a payload dir IS present it wins, so a partial run -- an --limit build, or a
    year mid-rebuild -- cannot make the index disagree silently.

    NOT "verified equal to the payload dirs". Measured three ways
    (`r4/probe_r4a.py`): a fresh `build_dataset.py --out` produces exactly the index's
    set (11 years, 3,910 items, per-year id sets identical), and so does this worktree.
    But a developer checkout that has carried the dirs across runs holds 3,912, because
    `build_dataset.py` never prunes: 2017 keeps `oral-answer-1819` and 2018 keeps
    `president-address-14`, two derived payloads with no summary, no withheld record and
    no index entry. The count is therefore identical for a CLONE (which is the case this
    check runs in, and the case that was broken) and 2 higher on a long-lived checkout.
    `oral-answer-1819` is separately filed as a defect by 442d49e's own message, so a
    stale file here is a known symptom, not a surprise.
    """
    out = {}
    idx = os.path.join(ROOT, "pipeline", "dataset", "index.json")
    if os.path.exists(idx):
        try:
            with open(idx, encoding="utf-8") as fh:
                for e in (json.load(fh).get("items") or []):
                    y = e.get("year") or (str((e.get("sitting_dates") or [""])[0])[:4])
                    if y:
                        out[y] = out.get(y, 0) + 1
        except (OSError, ValueError):
            pass
    for d in sorted(glob.glob(os.path.join(ROOT, "pipeline", "dataset", "20*"))):
        if os.path.isdir(d):
            out[os.path.basename(d)] = len(glob.glob(os.path.join(d, "*.json")))
    return out


def withheld_ids_by_year():
    """Withheld records as {year: {id, ...}}, so a double-count can be detected.

    The accounting rule below sums published + withheld against the item count, which is
    only correct while the two sets are DISJOINT. They are not: 7 items are recorded as
    both (2026: matter-adj-3012, motion-3008+3010, oral-answer-4055/4066/4067/4115;
    2023: oral-answer-3339). Summing then over-states the total -- that is the real
    surplus behind the 2026 report, and it is a data-hygiene defect, not a build one.
    """
    out = {}
    for p in glob.glob(os.path.join(ROOT, "pipeline", "withheld", "*.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        y = (d.get("item") or {}).get("year")
        if y:
            out.setdefault(y, set()).add(os.path.basename(p)[:-5])
    return out


def published_ids(year):
    return {os.path.basename(p)[:-5]
            for p in glob.glob(os.path.join(ROOT, "summaries", year, "*.json"))}


def _last_commit_ts(paths):
    """Unix time of the newest commit that touched any of `paths`, or 0.

    Used for freshness instead of mtime: a checkout rewrites mtimes in path order, so
    mtime cannot tell "written before the corpus changed" from "checked out after it".
    Commit order can.
    """
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--"] + list(paths),
                             cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return int(out) if out.isdigit() else 0
    except (OSError, ValueError):
        return 0


def corpus_dirty_paths(paths):
    try:
        return bool(subprocess.run(
            ["git", "status", "--porcelain", "--"] + list(paths),
            cwd=ROOT, capture_output=True, text=True).stdout.strip())
    except OSError:
        return False


def withheld_by_year():
    return {y: len(ids) for y, ids in withheld_ids_by_year().items()}


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
        #
        # The two sets must be DISJOINT for a sum to mean anything. 7 items are recorded
        # as both published and withheld, so summing counted them twice and invented a
        # surplus that is not there. Use the union, and report the overlap separately as
        # the data-hygiene defect it is.
        wh_ids = withheld_ids_by_year()
        for year in sorted(set(bd) | set(dd) | set(wh_ids)):
            items = dd.get(year, 0)
            pub = published_ids(year)
            wh = wh_ids.get(year, set())
            doubled = pub & wh
            accounted = len(pub | wh)
            # Not every item yields a brief (some have no summarisable content), so
            # only a SURPLUS is an error.
            if accounted > items:
                # A withheld record for a year that is not in the corpus at all is not
                # an accounting surplus, it is a leftover from a reverted run -- the
                # 2015 experiment was reverted to 2016+ (438c810). Report it as what it
                # is so the accounting rule stays trustworthy.
                stray = sorted(wh - set(dd))
                if stray and accounted - len(stray) <= items:
                    problems.append(("ORPHAN", "pipeline/withheld",
                                     f"{year}: {len(stray)} withheld record(s) for a year "
                                     f"with no dataset items and no briefs: {stray}"))
                else:
                    problems.append(("ACCOUNTING", "status.json",
                                     f"{year}: {accounted} accounted (published|withheld) "
                                     f"> {items} dataset items"))
            if doubled:
                problems.append(("DOUBLE-COUNTED", "status.json",
                                 f"{year}: {len(doubled)} item(s) recorded as both "
                                 f"published and withheld: {sorted(doubled)}"))

        # Artifact freshness vs the thing it describes.
        #
        # This was an mtime comparison, and mtime is the wrong instrument: `git clone` and
        # `git checkout` write every file in path order within one second, so a pristine
        # checkout can leave status.json 1-2s "older" than summaries/2026/ with nothing
        # drifted, while a worktree checkout can leave it 42 minutes "older". Both are
        # false positives, and a check that cries wolf on a fresh clone is a check people
        # learn to ignore.
        #
        # The real question is "was status.json written before the corpus last changed",
        # which git answers directly: the commit that last touched one side versus the
        # other. That is what the rule was reaching for -- the observed case was 2017
        # being promoted 11 seconds AFTER status.json was written, i.e. two commits in
        # the wrong order.
        st_commit = _last_commit_ts(["status.json"])
        corpus_commit = _last_commit_ts(["summaries", "pipeline/dataset"])
        if st_commit and corpus_commit and corpus_commit > st_commit:
            delta = corpus_commit - st_commit
            problems.append(("STALE", "status.json",
                             f"last updated {delta:,.0f}s BEFORE the commit that last "
                             f"changed the corpus it describes — recompute with "
                             f"tools/write_status.py"))
        # A dirty corpus is the other way this drifts: files changed but nothing
        # committed or recomputed yet.
        elif corpus_dirty_paths(["summaries", "pipeline/dataset"]):
            data_mtime = newest_mtime(["summaries", "pipeline/dataset"])
            st_mtime = os.path.getmtime(os.path.join(ROOT, "status.json"))
            if data_mtime > st_mtime + 60:
                problems.append(("STALE", "status.json",
                                 f"corpus is dirty and {data_mtime - st_mtime:,.0f}s newer "
                                 f"than status.json — recompute with "
                                 f"tools/write_status.py"))

        # ARTIFACT VERSUS MUTABLE INPUT. The freshness rule above compares status.json to
        # the corpus. This compares it to the INPUT the corpus is derived from, which is
        # the drift that matters here and that no mtime can see: pipeline/dataset/
        # index.json is generated from data/, it is COMMITTED, and status.json is checked
        # against it -- so if data/ changes after both, the chain is silently broken.
        # Measured on the real repo: index.json is 8s NEWER than the newest data/ file
        # while status.json is 10,332s OLDER, i.e. the corpus was refreshed without
        # recomputing either the index or the status. Reported as a note, not a problem,
        # because the fix is a two-command rebuild rather than a defect in the corpus.
        data_mtime = newest_mtime(["data"])
        idx_mtime = os.path.getmtime(os.path.join(ROOT, "pipeline", "dataset", "index.json")) \
            if os.path.exists(os.path.join(ROOT, "pipeline", "dataset", "index.json")) else 0
        if data_mtime and idx_mtime and abs(data_mtime - idx_mtime) > 3600:
            notes.append(
                f"pipeline/dataset/index.json and data/ are {abs(data_mtime - idx_mtime) / 3600:.1f}h "
                f"apart; regenerate the index (`python3 summariser/build_dataset.py`) and "
                f"status.json (`python3 tools/write_status.py`) together after a fetch")
        gated_pass = {str(g.get("scope", "")).split("/")[-1] for g in gates
                      if g.get("result") == "PASS"}
        gated_fail = {str(g.get("scope", "")).split("/")[-1] for g in gates
                      if g.get("result") == "FAIL"}
        notes.append(f"gates: {len(gated_pass)} PASS, {len(gated_fail)} FAIL "
                     f"({', '.join(sorted(gated_fail)) or 'none'})")
        notes.append(f"briefs on disk: {sum(bd.values()):,} across {len(bd)} years")
        n_withheld = sum(len(v) for v in wh_ids.values())
        notes.append(f"withheld records: {n_withheld} across "
                     f"{len(wh_ids)} year(s)")

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
