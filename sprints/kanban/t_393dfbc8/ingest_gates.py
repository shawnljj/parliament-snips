#!/usr/bin/env python3
"""Run every gate for this card in one pass and print a single verdict.

Exists so "it passes" is one reproducible command rather than a list of commands someone has
to remember to run in the right order, and so a FAIL anywhere is visible (the shell `&&`
version stops at the first failure and hides the rest).
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GATES = [
    ("unit tests (74, offline fixture)", ["python3", "-m", "pytest", "ingest/test_debates.py", "-q"]),
    ("fixture still matches the corpus", ["python3", "docs/probes/ingest_fixture_check.py"]),
    ("partition is complete + disjoint", ["python3", "docs/probes/ingest_partition_check.py"]),
    ("every claim re-derived from the files", ["python3", "docs/probes/ingest_verify.py"]),
    ("spec 3.3's table, corpus-wide", ["python3", "docs/probes/ingest_resolution_ledger.py"]),
    ("the two input paths agree", ["python3", "docs/probes/ingest_path_agreement.py"]),
    ("fixtures namespace survives the merge", ["python3", "docs/probes/ingest_namespace_check.py"]),
    ("spec 3.4/3.2 numbers + the BOM", ["python3", "docs/probes/ingest_doc_numbers.py"]),
    ("the officer-test order, measured", ["python3", "docs/probes/ingest_officer_order.py"]),
    ("live vs archive divergence", ["python3", "docs/probes/ingest_attendance_gap.py"]),
]


def main():
    fails = []
    for name, cmd in GATES:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        verdict = "PASS" if p.returncode == 0 else "FAIL"
        # surface the probe's own RESULT line when it has one
        last = next((l for l in reversed(out.splitlines())
                     if l.startswith("RESULT")), None)
        print(f"  [{verdict}] {name}")
        tail = (last or (out.strip().splitlines() or [""])[-1])
        if tail:
            print(f"          {tail.strip()[:150]}")
        if p.returncode != 0:
            fails.append(name)

    print()
    if fails:
        print(f"OVERALL: FAIL -- {len(fails)}/{len(GATES)} gate(s) failed: {fails}")
        return 1
    print(f"OVERALL: PASS -- {len(GATES)}/{len(GATES)} gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
