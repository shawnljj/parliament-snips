"""
Parsnips — model benchmark for the Stage 2 extraction task.

Answers one question with measurements rather than opinion: which model can do
extraction reliably, and what does each cost in time and tokens?

WHAT IS MEASURED, and why each matters
  1. VALID JSON      — a model that returns prose instead of JSON fails outright.
  2. CITATIONS RESOLVE — every cited sid must exist in the dataset. An unresolvable
     id is a point that will be silently dropped, so a model that invents ids
     produces a thinner brief for no visible reason.
  3. QUOTE INVARIANT — with by-reference extraction this should be 100% by
     construction. If it is not, the bug is in assembly, not the model. Measuring it
     per model proves the property rather than assuming it.
  4. POINTS and COVERAGE — how much of the item actually got summarised.
  5. TIME and TOKENS — the binding constraint at corpus scale (3,912 items).

The corpus is large enough that a slow model is a real problem rather than an
inconvenience: at 140s/item, one year of 2026 (291 items) is 11 hours.

Usage:
    python3 summariser/bench_models.py --models llama3.2:3b qwen3:4b \\
        --dates 2026-08-05 --group oral --limit 3
    python3 summariser/bench_models.py --models ... --probe     # one tiny JSON test
"""

import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))
sys.path.insert(0, HERE)
import build_briefs as B  # noqa: E402

# A deliberately trivial probe. It isolates "can this model emit JSON at all"
# from "can it do the extraction task", and it is cheap, so a model that cannot
# pass it never gets a full run.
PROBE_SYSTEM = """Return JSON only, exactly this shape:
{"ok": true, "names": ["alpha", "beta"]}
No prose, no code fences, no explanation."""
PROBE_USER = "Return the JSON object described in your instructions."


def probe(model):
    """Can the model return JSON at all, and how expensive is a trivial answer?"""
    t0 = time.time()
    try:
        txt, usage = B.ask(PROBE_USER, PROBE_SYSTEM, model, timeout=300)
    except Exception as exc:                                        # noqa: BLE001
        return {"ok": False, "error": str(exc)[:120], "seconds": round(time.time() - t0, 1)}
    got = B.parse_json(txt)
    return {
        "ok": bool(got) and got.get("ok") is True and got.get("names") == ["alpha", "beta"],
        "objects_returned": len(B.parse_json_many(txt)),
        "got": (txt or "")[:90],
        "seconds": round(time.time() - t0, 1),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "estimated": usage.get("estimated"),
    }


def one_item(model, meta):
    """Run one item through stages 2-4 and report the measurements that matter."""
    item = B.load_item(os.path.join(str(meta["year"]), f"{meta['id']}.json"))
    if not item:
        return {"id": meta["id"], "error": "payload missing"}
    is_oral = meta.get("group") == "oral"
    t0 = time.time()
    try:
        extracted, usage = B.stage2_extract(item, model, is_oral)
    except Exception as exc:                                        # noqa: BLE001
        return {"id": meta["id"], "error": str(exc)[:140], "model": model}
    secs = time.time() - t0

    rec = {"id": meta["id"], "model": model, "seconds": round(secs, 1),
           "calls": usage["calls"], "in": usage["prompt_tokens"],
           "out": usage["completion_tokens"], "estimated": usage["estimated"],
           "json": bool(extracted)}
    if not extracted:
        rec["withheld"] = "no JSON / no points"
        return rec

    # citation resolvability, measured before assembly drops anything
    valid = {s["sid"] for s in item["sentences"]}
    raw_pts = extracted["points"]
    all_cites = [c for p in raw_pts for c in (p.get("cites") or []) if isinstance(c, str)]
    rec["raw_points"] = len(raw_pts)
    rec["citations"] = len(all_cites)
    rec["unresolvable"] = len([c for c in all_cites if c not in valid])
    rec["points_without_cites"] = len([p for p in raw_pts if not (p.get("cites") or [])])

    brief, dropped = B.stage3_assemble(item, extracted, model)
    if not brief:
        rec["withheld"] = f"nothing citable ({len(dropped)} dropped)"
        rec["dropped"] = len(dropped)
        return rec
    verdict = B.stage4_verify(brief, item)
    rec.update({"published_points": verdict["points"],
                "coverage": verdict["coverage"],
                "turns_cited": verdict["turns_cited"],
                "turns_total": verdict["turns_total"],
                "quote_invariant": verdict["quote_invariant"],
                "passed": verdict["passed"]})
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--dates", nargs="+")
    ap.add_argument("--year")
    ap.add_argument("--group")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--probe", action="store_true",
                    help="only run the trivial JSON probe, then exit")
    ap.add_argument("--out", help="write raw results as JSON here")
    args = ap.parse_args(argv)

    print("=" * 78)
    print("PROBE — can the model return JSON at all?")
    print("=" * 78)
    probes = {}
    for m in args.models:
        p = probe(m)
        probes[m] = p
        flag = "PASS" if p["ok"] else "FAIL"
        print(f"  {m:26s} {flag:4s}  {p.get('seconds'):>6}s  "
              f"{p.get('completion_tokens')} out tok"
              + (f"  {p.get('error')}" if p.get("error") else "")
              + (f"  got={p['got']!r}" if not p["ok"] else ""))
    if args.probe:
        return 0

    models = [m for m in args.models if probes[m]["ok"]] or args.models
    if len(models) != len(args.models):
        print(f"\n  skipping models that failed the probe: "
              f"{[m for m in args.models if m not in models]}")

    idx = B.load_index()
    years = [args.year] if args.year else (sorted({d[:4] for d in args.dates}) if args.dates else None)
    sel = [it for it in idx["items"] if not years or str(it["year"]) in years]
    if args.dates:
        want = set(args.dates)
        sel = [it for it in sel if set(it.get("sitting_dates") or []) & want]
    if args.group:
        sel = [it for it in sel if it.get("group") == args.group]
    sel.sort(key=lambda i: (str(i["year"]), str((i.get("sitting_dates") or [""])[0])),
             reverse=True)
    sel = sel[:args.limit]
    if not sel:
        print("no items matched")
        return 1

    print()
    print("=" * 78)
    print(f"EXTRACTION — {len(sel)} item(s): " +
          ", ".join(f"{s['id']}({s.get('source_words')}w)" for s in sel))
    print("=" * 78)

    results = {}
    for m in models:
        print(f"\n--- {m} ---")
        rows = []
        for meta in sel:
            r = one_item(m, meta)
            rows.append(r)
            if r.get("error"):
                print(f"  {r['id']:26s} ERROR {r['error']}")
            elif r.get("withheld"):
                print(f"  {r['id']:26s} withheld — {r['withheld']}")
            else:
                print(f"  {r['id']:26s} {r['seconds']:6.1f}s  "
                      f"{r['in']:>7,}in/{r['out']:>6,}out  "
                      f"pts {r['raw_points']:2d}->{r['published_points']:2d}  "
                      f"badcites {r['unresolvable']}  cov {r['coverage']:.2f}  "
                      f"quote_inv {'OK' if r['quote_invariant'] else 'FAIL'}")
        results[m] = rows

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  {'model':26s} {'json':>5s} {'sec/item':>9s} {'tok out':>9s} "
          f"{'pts':>5s} {'badcite':>8s} {'cov':>5s} {'quote':>6s} {'$/item':>8s}")
    for m, rows in results.items():
        ok = [r for r in rows if not r.get("error") and not r.get("withheld")]
        if not ok:
            print(f"  {m:26s} {'no usable item':>5s}  "
                  f"({len([r for r in rows if r.get('error')])} error, "
                  f"{len([r for r in rows if r.get('withheld')])} withheld)")
            continue
        sec = statistics.median(r["seconds"] for r in ok)
        out = statistics.median(r["out"] for r in ok)
        pts = statistics.median(r["raw_points"] for r in ok)
        bad = sum(r["unresolvable"] for r in ok)
        cov = statistics.median(r["coverage"] for r in ok)
        qi = all(r["quote_invariant"] for r in ok)
        cost = B.cost_of(m, 0, int(out)) or 0.0
        print(f"  {m:26s} {len(ok)}/{len(rows):>3} {sec:>9.1f} {out:>9,.0f} "
              f"{pts:>5.0f} {bad:>8d} {cov:>5.2f} {'OK' if qi else 'FAIL':>6s} "
              f"{'$%.4f' % cost:>8s}")
    print()
    print("  json    = items that returned usable JSON (of those attempted)")
    print("  badcite = cited sentence ids that do not exist (each is a dropped point)")
    print("  quote   = the quote invariant held (should always be OK: quotes are")
    print("            substituted from the dataset, never written by the model)")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"probe": probes, "results": results}, fh, indent=1)
        print(f"\n  raw results -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
