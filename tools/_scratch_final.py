#!/usr/bin/env python3
"""Final check run: compile, build, and the repo's own checkers, with exit codes stated."""
import subprocess
import sys

ROOT = "/Users/shawnlin/parsnips/.worktrees/t_140a0b87"


def run(label, cmd, cwd=ROOT):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=isinstance(cmd, str))
    tail = (p.stdout + p.stderr).strip().splitlines()
    print(f"=== {label}: exit={p.returncode}")
    for line in tail[-3:]:
        print(f"    {line[:160]}")
    return p.returncode


rc = {}
rc["py_compile"] = run("py_compile build_site.py", ["python3", "-m", "py_compile",
                                                    "site/build_site.py"])
rc["build"] = run("site/build_site.py", ["python3", "site/build_site.py"])
rc["check_rail"] = run("tools/check_rail.py", ["python3", "tools/check_rail.py"])
rc["check_artifacts"] = run("tools/check_artifacts.py", ["python3", "tools/check_artifacts.py"])
rc["check_selection"] = run("tools/check_selection.py", ["python3", "tools/check_selection.py"])
rc["check_year"] = run("tools/check_year.py", ["python3", "tools/check_year.py"])

print()
print("=== SAME CHECKS ON THE PRISTINE BASELINE (to separate pre-existing from new) ===")
BASE = "/Users/shawnlin/.hermes/profiles/dev_parsnips/cache/scratch/baseline-140a0b87"
for name in ("check_artifacts", "check_selection", "check_year", "check_rail"):
    run(f"BASELINE {name}", ["python3", f"tools/{name}.py"], cwd=BASE)

print()
print("NEW vs PRE-EXISTING:", {k: v for k, v in rc.items()})
sys.exit(0)
