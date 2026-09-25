"""Recount the archived runs using `pass` ONLY, and flag verdict strings the source no longer emits.

Two bugs this exists to prevent:

1. The previous report OR'd `gate_pass` into the pass test (`x.get('pass') or x.get('gate_pass')`),
   which counts a run as passing when its EXPECTATION failed but its citation gate held. That masked
   D1: pass=False, gate_pass=True, reported as PASS. Count the strict field.

2. The archived runs carry the verdict STRINGS of the code that produced them. When a verdict is
   relabelled, every older archive keeps the retired wording, and printing one as "the item that is
   failing, and why" re-publishes a label the project has already corrected. Measured: D1's archived
   text read "FALSE REFUSAL (answer existed)" -- asserting a defect where the system behaved
   correctly -- long after the source was fixed to say "refused (correct for class D ...)".

The real signal is simpler than matching strings: an archive records the CODE THAT PRODUCED IT,
so an archive older than the source was produced by different code and its verdicts are history.
Each run is therefore stamped against `run_answer_eval.py`'s mtime, and an older one is marked
STALE -- mechanically, rather than by anyone remembering when a label changed.

Usage:
    python3 spread_report.py [run.json ...]      # default: the archived runs in /tmp
"""
import ast
import json
import os
import sys
import time

DEFAULT_RUNS = ['/tmp/now_r1.json', '/tmp/stability_0.json', '/tmp/stability_1.json',
                '/tmp/stability_2.json', '/tmp/ans_v2.json']

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, 'run_answer_eval.py')


def current_labels():
    """The verdict strings the current source can ACTUALLY emit.

    Parsed from the AST, not by regex over the file text. A regex finds string literals in
    COMMENTS and docstrings too -- and the comment on the relabel line quotes the retired wording
    to explain what was fixed, so a text scan reports the dead label as live. That is the same
    failure the project already hit once (a substring test matching the comment that documents a
    key). The AST only contains strings the code can produce.
    """
    try:
        tree = ast.parse(open(SOURCE).read())
    except (OSError, SyntaxError):
        return None
    return {n.value.strip() for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.strip()}


def label_is_current(label, labels):
    """A label is current if the source still contains it (or a distinctive prefix of it)."""
    if not label or labels is None:
        return None          # cannot tell -- say so rather than guess
    if label in labels:
        return True
    # the source may build the label from a template; accept a long shared prefix
    for s in labels:
        if len(label) > 20 and (s.startswith(label[:20]) or label.startswith(s[:20])):
            return True
    return False


RUNS = sys.argv[1:] or DEFAULT_RUNS
labels = current_labels()

runs = {}
stamps = {}
for f in RUNS:
    if not os.path.exists(f):
        continue
    r = json.load(open(f))
    if not isinstance(r, list):
        continue
    n = os.path.basename(f)
    runs[n] = {x['id']: x for x in r}
    stamps[n] = os.path.getmtime(f)

if not runs:
    print("no readable runs -- pass paths, or re-run eval/run_answer_eval.py --json <path>")
    sys.exit(1)

ids = sorted({i for d in runs.values() for i in d})
print(f"{'item':<5}" + "".join(f"{n[:10]:>13}" for n in runs))
flippers = []
for i in ids:
    row = [runs[n].get(i, {}).get('pass') for n in runs]
    cells = "".join(f"{'PASS' if v else 'FAIL' if v is False else '-':>13}" for v in row)
    flip = len({v for v in row if v is not None}) > 1
    if flip:
        flippers.append(i)
    print(f"{i:<5}{cells}" + ("  <- FLIPS" if flip else ""))

print()
for n, d in runs.items():
    p = sum(1 for x in d.values() if x.get('pass'))
    gp = sum(1 for x in d.values() if x.get('gate_pass'))
    print(f"  {n:<22} pass {p}/{len(d)}   gate_pass {gp}/{len(d)}")

totals = [sum(1 for x in d.values() if x.get('pass')) for d in runs.values()]
print(f"\n  STRICT score range across {len(runs)} runs: {min(totals)}-{max(totals)} of {len(ids)}")
print(f"  items that flip: {flippers or 'none'}")

# The failing item, from EVERY run -- not just the first, whose wording may be retired.
print("\n  the item that is failing, and why   (oldest archive first)")
try:
    src_mtime = os.path.getmtime(SOURCE)
    print(f"    current source: {time.strftime('%m-%d %H:%M', time.localtime(src_mtime))}"
          f"  ({os.path.basename(SOURCE)})")
except OSError:
    src_mtime = None
    print("    current source: (could not stat run_answer_eval.py)")
for n, d in sorted(runs.items(), key=lambda kv: stamps[kv[0]]):
    x = d.get('D1')
    if not x:
        continue
    v = x.get('verdict')
    when = time.strftime('%m-%d %H:%M', time.localtime(stamps[n]))
    # STALE by TIMESTAMP: an archive predating the source was produced by different code, whatever
    # its strings say. Matching label text would miss this, because the source still contains the
    # old literal on the branch a non-D refusal takes.
    if src_mtime is None:
        tag = 'unchecked'
    else:
        tag = 'STALE' if stamps[n] < src_mtime else 'current'
    print(f"    {when}  {n:<22} pass={x.get('pass')} gate_pass={x.get('gate_pass')}")
    print(f"              [{tag}] {v}")

print()
print("  note: an archive records the CODE THAT PRODUCED IT. Only a run newer than the source")
print("        reflects it; the rest are history. Re-run for a current verdict.")
print("        Refs: pass + gate_pass are DIFFERENT questions -- never OR them.")
print(f"              {os.path.relpath(SOURCE, os.path.dirname(HERE) or '.')}")
