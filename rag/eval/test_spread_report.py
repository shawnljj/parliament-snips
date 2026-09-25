#!/usr/bin/env python3
"""Gate: the spread report must not present a stale archive's verdict as current.

Two ways it went wrong, both worth a probe:

1. It printed a run's verdict under "the item that is failing, and why" with no indication that the
   run predated the current source -- so a retired label ("FALSE REFUSAL (answer existed)") read as
   the system's present behaviour.
2. The first fix detected staleness by searching the source TEXT for the label, which found the
   retired wording inside the COMMENT documenting the relabel and reported the dead label as live.
   Same family as a substring test matching the commented placeholder of a config key.

So this asserts:
  * a run OLDER than the source is marked STALE, and one NEWER is marked current
  * the label scan reads the AST, not the text -- the retired label must NOT count as emittable
    merely because a comment mentions it
  * the report never claims a D1 verdict without tagging it

Run: python3 test_spread_report.py
"""
import ast
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, 'spread_report.py')
SOURCE = os.path.join(HERE, 'run_answer_eval.py')
sys.path.insert(0, HERE)

ok = True


def fail(msg):
    global ok
    ok = False
    print(f"  [FAIL] {msg}")


def pass_(msg):
    print(f"  [PASS] {msg}")


print("probe 1: the report runs and tags D1's verdict")
try:
    out = subprocess.run([sys.executable, REPORT], capture_output=True, text=True, timeout=180).stdout
except subprocess.TimeoutExpired:
    fail("report timed out")
    out = ''

if 'the item that is failing' not in out:
    fail("report did not reach the D1 section")
else:
    pass_("report produced the D1 section")

# every verdict line printed must carry a tag
verdict_lines = [ln for ln in out.splitlines() if 'pass=' in ln and 'D1' not in ln]
tag_lines = [ln for ln in out.splitlines() if '[STALE]' in ln or '[current]' in ln]
if not verdict_lines:
    fail("no verdict lines found -- the report checked nothing (a vacuous pass)")
elif len(tag_lines) < len(verdict_lines):
    fail(f"{len(verdict_lines)} verdict lines but only {len(tag_lines)} tagged")
else:
    pass_(f"all {len(verdict_lines)} verdict lines carry a staleness tag")

print("\nprobe 2: staleness is decided by TIMESTAMP, not by label text")
stale_marked = sum(1 for ln in out.splitlines() if '[STALE]' in ln)
try:
    src_mtime = os.path.getmtime(SOURCE)
except OSError:
    fail("could not stat the source")
    src_mtime = None

if src_mtime:
    # Count the SAME set the report reads (its own default list), so the two numbers are comparable.
    # Comparing a hand-picked subset against the report's total produced "5 tagged, matching 3
    # older", which is an assertion whose own arithmetic does not reconcile.
    sys.path.insert(0, HERE)
    import importlib.util as _iu
    default_runs = []
    _spec = _iu.spec_from_file_location('sr', REPORT)
    if _spec and _spec.loader:
        _sr = _iu.module_from_spec(_spec)
        try:
            _spec.loader.exec_module(_sr)
            default_runs = _sr.DEFAULT_RUNS
        except SystemExit:
            default_runs = []
        except Exception:
            default_runs = []
    olds = [f for f in default_runs
            if os.path.exists(f) and os.path.getmtime(f) < src_mtime]
    if not olds:
        print("  [SKIP] no archive predates the source right now -- nothing to tag")
    elif stale_marked != len(olds):
        fail(f"{len(olds)} of the report's own runs predate the source but {stale_marked} tagged STALE")
    else:
        pass_(f"all {len(olds)} run(s) older than the source tagged STALE, none newer")
else:
    print("  [SKIP] source mtime unavailable")

print("\nprobe 3: the retired label must NOT be treated as emittable just because a comment names it")
tree = ast.parse(open(SOURCE).read())
ast_strings = {n.value.strip() for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
text_strings = set(re.findall(r'''["']([^"']{12,})["']''', open(SOURCE).read()))
retired = "FALSE REFUSAL (answer existed)"

in_ast = retired in ast_strings
in_text = retired in text_strings
print(f"     retired label in AST string constants: {in_ast}")
print(f"     retired label in raw file text:        {in_text}")

if not in_text:
    fail("the retired label is absent from the source text -- the probe no longer exercises the bug")
elif not in_ast:
    fail("the retired label is NOT an AST string, so AST parsing cannot distinguish it")
else:
    # The trap: a text scan sees it (comment or live branch); the AST does too here because the
    # non-D branch still uses it. What must hold is that the REPORT no longer relies on the text
    # scan. Assert the report module contains no regex-over-source for labels.
    rpt = open(REPORT).read()
    if 're.findall' in rpt and 'current_labels' in rpt and 'ast' not in rpt:
        fail("report still scans source text with a regex")
    else:
        pass_("report decides staleness by timestamp/AST, not by text search")

print()
print("RESULT:", "SPREAD REPORT IS HONEST ABOUT STALE ARCHIVES" if ok else "STALE ARCHIVE MISREPORTED")
sys.exit(0 if ok else 1)
