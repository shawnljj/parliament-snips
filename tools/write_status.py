#!/usr/bin/env python3
"""Write parsnips' status artifact for COS and any other reader.

WHY GENERATED, NOT WRITTEN BY HAND. Project rule N-3: every number the system reports
about itself is computed from data. A hand-written status drifts the moment someone
forgets to update it, and a stale status is worse than none -- COS would report "all fine"
from a file nobody refreshed.

WHAT IT MEASURES, and where each number comes from:
  * corpus counts      -- the actual files in summaries/ and pipeline/dataset/
  * gate results       -- tools/check_selection.py is RUN, and its verdict recorded
  * data freshness     -- mtime of the newest artifact per year
  * git state          -- unpushed commits, dirty files

A gate that could not be run is recorded as "not_run", never omitted and never assumed.
That distinction -- passed vs we did not look -- is the whole point of the artifact.

Usage:
    python3 tools/write_status.py
    python3 tools/write_status.py --check      # print, do not write
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "status.json")
YEARS = [str(y) for y in range(2016, 2027)]


def run(cmd, cwd=None, timeout=900):
    try:
        r = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                    # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def corpus():
    """Briefs and dataset items per year, from the files that exist."""
    out = {}
    for y in YEARS:
        b = len(glob.glob(os.path.join(ROOT, "summaries", y, "*.json")))
        d = len(glob.glob(os.path.join(ROOT, "pipeline", "dataset", y, "*.json")))
        if b or d:
            out[y] = {"briefs": b, "dataset_items": d}
    return out


def gate(year, timeout=1800):
    """Run check_selection for one year and record its ACTUAL verdict."""
    script = os.path.join(ROOT, "tools", "check_selection.py")
    target = os.path.join(ROOT, "summaries", year)
    if not os.path.isdir(target) or not glob.glob(os.path.join(target, "*.json")):
        return {"name": "check_selection", "scope": f"summaries/{year}",
                "result": "not_run", "reason": "no briefs for this year"}
    code, out = run([sys.executable, script, target, year], timeout=timeout)
    m = re.search(r"(PASS|FAIL)\s+—\s+(\d+)\s+defect", out)
    if not m:
        return {"name": "check_selection", "scope": f"summaries/{year}",
                "result": "not_run", "reason": "no verdict parsed from output"}
    defects = int(m.group(2))
    return {"name": "check_selection", "scope": f"summaries/{year}",
            "result": "PASS" if m.group(1) == "PASS" else "FAIL",
            "defects": defects}


def git_state():
    _, ahead = run(["git", "rev-list", "--count", "origin/main..HEAD"])
    _, dirty = run(["git", "status", "--porcelain"])
    _, last = run(["git", "log", "-1", "--format=%h %s"])
    try:
        n_ahead = int((ahead or "0").strip().splitlines()[0])
    except (ValueError, IndexError):
        n_ahead = None
    return {"unpushed_commits": n_ahead,
            "dirty_files": len([l for l in (dirty or "").splitlines() if l.strip()]),
            "last_commit": (last or "").strip()[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="print, do not write")
    ap.add_argument("--skip-gates", action="store_true",
                    help="skip the (slow) check_selection runs")
    a = ap.parse_args()

    cps = corpus()
    years_with_briefs = [y for y, v in cps.items() if v["briefs"]]

    gates = []
    if not a.skip_gates:
        for y in years_with_briefs:
            gates.append(gate(y))
    else:
        for y in years_with_briefs:
            gates.append({"name": "check_selection", "scope": f"summaries/{y}",
                          "result": "not_run", "reason": "--skip-gates"})

    failing = [g for g in gates if g.get("result") == "FAIL"]
    pending = [y for y, v in cps.items() if v["dataset_items"] and not v["briefs"]]

    changed = []
    total_briefs = sum(v["briefs"] for v in cps.values())
    changed.append(f"{total_briefs} briefs live across {len(years_with_briefs)} year(s)")
    if pending:
        changed.append(f"{len(pending)} year(s) have datasets but no briefs yet: "
                       f"{', '.join(sorted(pending))}")
    if failing:
        changed.append(f"{len(failing)} year(s) FAIL their gates: "
                       f"{', '.join(g['scope'].split('/')[-1] for g in failing)}")

    g = git_state()
    if g.get("unpushed_commits"):
        changed.append(f"{g['unpushed_commits']} unpushed commit(s)")

    needs = None
    severity = "low"
    if failing:
        needs = (f"{len(failing)} year(s) fail check_selection "
                 f"({', '.join(g2['scope'].split('/')[-1] for g2 in failing)}). "
                 f"Fix before shipping, or accept the defects.")
        severity = "high"
    elif g.get("unpushed_commits", 0) and g["unpushed_commits"] > 20:
        needs = f"{g['unpushed_commits']} commits are unpushed."
        severity = "normal"

    doc = {
        "project": "parsnips",
        "written_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "by": "tools/write_status.py",
        "status": "failed" if failing else ("working" if pending else "idle"),
        "changed_since_last": changed,
        "corpus": cps,
        "gates": gates,
        "blocked": bool(failing),
        "blocked_on": (f"{len(failing)} failing gate(s)" if failing else None),
        "needs_decision": needs,
        "severity": severity,
        "git": g,
    }

    text = json.dumps(doc, indent=1, ensure_ascii=False)
    if a.check:
        print(text)
        return 0
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    os.replace(tmp, OUT)
    print(f"wrote {OUT}")
    print(f"  status          : {doc['status']}")
    print(f"  briefs          : {total_briefs}")
    print(f"  gates           : " + ", ".join(
        f"{g2['scope'].split('/')[-1]}={g2['result']}" for g2 in gates))
    print(f"  needs_decision  : {needs or '(nothing)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
