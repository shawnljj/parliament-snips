"""
Parsnips — what the pipeline has cost, in tokens and money.

Reads pipeline/usage.jsonl (one record per item, written by build_briefs.py) and
reports the totals. Every figure here is derived from logged runs, never typed, so
a cost claim on the case study and a cost report here cannot drift apart.

Reports three numbers, and keeps them separate because they mean different things:
  * measured  — the provider returned its own token counts
  * estimated — counts inferred from character length (flagged, never blended in)
  * wall-clock — the constraint that matters at corpus scale, not the token price

Usage:
    python3 tools/cost_report.py
    python3 tools/cost_report.py --json
    python3 tools/cost_report.py --project-year 2026   # what one year would cost
"""

import argparse
import glob
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
USAGE = os.path.join(ROOT, "pipeline", "usage.jsonl")
DATASET = os.path.join(ROOT, "pipeline", "dataset", "index.json")
sys.path.insert(0, os.path.join(ROOT, "summariser"))
import build_briefs as B  # noqa: E402  (one source of truth for prices)


def load_usage(path=USAGE):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def summarise(rows):
    per_model = {}
    for r in rows:
        m = r.get("model") or "?"
        a = per_model.setdefault(m, {
            "items": 0, "calls": 0, "in": 0, "out": 0, "cost": 0.0,
            "estimated": False, "published": 0, "withheld": 0, "errors": 0,
            "seconds": [],
        })
        a["items"] += 1
        a["calls"] += r.get("calls") or 0
        a["in"] += r.get("prompt_tokens") or 0
        a["out"] += r.get("completion_tokens") or 0
        a["cost"] += r.get("cost") or 0.0
        a["estimated"] = a["estimated"] or bool(r.get("estimated"))
        o = r.get("outcome")
        if o == "published":
            a["published"] += 1
        elif o == "withheld":
            a["withheld"] += 1
        if r.get("error"):
            a["errors"] += 1
    return per_model


def project(model, years, per_item=None):
    """What a given model would cost over a set of years, from measured per-item use.

    Uses the MEDIAN observed cost per published item where available, because the
    mean is skewed by the few very large items, and extrapolating a mean would
    overstate the total for the many small ones.
    """
    idx = json.load(open(DATASET, encoding="utf-8"))
    sel = [i for i in idx["items"] if str(i["year"]) in [str(y) for y in years]]
    n = len(sel)
    words = sum(i.get("source_words") or 0 for i in sel)
    if per_item is None:
        return {"items": n, "words": words, "cost": None, "hours": None}
    cin, hours_s = per_item
    return {"items": n, "words": words,
            "cost": cin * n,
            "hours": hours_s * n / 3600}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--project-year", action="append", default=[],
                    help="estimate the cost of a whole year from measured per-item use")
    args = ap.parse_args(argv)

    rows = load_usage()
    per_model = summarise(rows)

    if args.json:
        print(json.dumps({"per_model": per_model, "records": len(rows)}, indent=1))
        return 0

    print("=" * 74)
    print("USAGE AND COST — measured from pipeline/usage.jsonl")
    print("=" * 74)
    if not rows:
        print("  nothing logged yet. Run: python3 summariser/build_briefs.py --year 2026")
        return 0

    tot_cost = 0.0
    tot_in = tot_out = tot_items = 0
    for m, a in sorted(per_model.items()):
        src = "estimated" if a["estimated"] else "measured"
        print(f"\n  {m}")
        print(f"    items ............ {a['items']:>6,}   "
              f"(published {a['published']}, withheld {a['withheld']}, errors {a['errors']})")
        print(f"    model calls ...... {a['calls']:>6,}")
        print(f"    tokens ........... {a['in']:>12,} in / {a['out']:>9,} out   ({src})")
        if a["items"]:
            print(f"    per item ......... {a['in']/a['items']:>12,.0f} in / "
                  f"{a['out']/a['items']:>9,.0f} out")
        print(f"    cost ............. ${a['cost']:.4f}")
        if a["published"]:
            print(f"    cost/published ... ${a['cost']/a['published']:.6f}")
        tot_cost += a["cost"]
        tot_in += a["in"]
        tot_out += a["out"]
        tot_items += a["items"]

    print()
    print(f"  TOTAL {tot_items:,} items, {tot_in:,} in / {tot_out:,} out tokens, "
          f"${tot_cost:.4f}")

    if args.project_year:
        print()
        print("=" * 74)
        print("PROJECTION — what a whole year would cost at the measured rate")
        print("=" * 74)
        for m, a in sorted(per_model.items()):
            if not a["published"]:
                continue
            per = a["cost"] / a["published"] if a["cost"] else 0.0
            for y in args.project_year:
                p = project(m, [y], per_item=(per, 60.0))
                est = f"${p['cost']:.2f}" if p["cost"] is not None else "n/a"
                print(f"  {m:26s} {y}  {p['items']:>4,} items  "
                      f"{p['words']:>10,} words  ~{est}")
    print()
    print("  Note: local models are priced at $0 because the machine is already on.")
    print("  Their real cost is wall-clock, which is why the benchmark reports it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
