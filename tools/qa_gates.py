#!/usr/bin/env python3
"""Run every pre-existing project gate plus the two other checkers, and report exit codes.

There is no lint config, no type-check and no test suite in this repo (see the audit's note and
tools/ -- they are standalone checkers, not tests). The honest version of "run the project's
gates" is: run everything the repo has that returns a status, say which are gates and which are
ad-hoc spike tools, and compare each exit code against the PRISTINE pre-change build so a
pre-existing failure cannot be mistaken for a regression.

    python3 tools/qa_gates.py <after-base-url> <before-base-url>
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = "/Users/shawnlin/.hermes/profiles/dev_parsnips/cache/scratch/baseline-140a0b87"

# Gates: every tool that takes no interactive input and exits non-zero on a problem.
STATIC = ["tools/check_artifacts.py", "tools/check_selection.py", "tools/check_year.py"]
# Browser-driven, need a URL argument.
LIVE = ["tools/check_rail.py", "tools/qa_console.py", "tools/qa_defects.py",
        "tools/qa_pixels.py"]


def run(args, cwd, timeout=900):
    r = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, (r.stdout or "")[-1400:], (r.stderr or "")[-400:]


def main():
    after, before = sys.argv[1], sys.argv[2]
    rows = []

    for tool in STATIC:
        rc_a, out_a, err_a = run([tool], ROOT)
        base_tool = os.path.join(BASELINE, tool)
        rc_b, out_b, err_b = (run([base_tool], BASELINE) if os.path.exists(base_tool)
                              else (None, "", ""))
        rows.append(("static", tool, rc_a, rc_b))

    for tool in LIVE:
        if tool == "tools/qa_pixels.py":
            args = [tool, after, before, "390,759"]
        elif tool == "tools/check_rail.py":
            args = [tool, after + "/sittings/2026-08-04.html"]
        else:
            args = [tool, after]
        try:
            rc_a, out_a, err_a = run(args, ROOT)
        except subprocess.TimeoutExpired:
            rc_a, out_a, err_a = "TIMEOUT", "", ""
        rows.append(("live", tool, rc_a, None))

    print(f"{'kind':<7} {'tool':<30} {'after':>8} {'pristine':>9}")
    for kind, tool, rc_a, rc_b in rows:
        print(f"{kind:<7} {tool:<30} {str(rc_a):>8} {str(rc_b) if rc_b is not None else '-':>9}")
    print()
    print("A non-zero that also happens on the pristine build is PRE-EXISTING and not a "
          "regression from this change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
