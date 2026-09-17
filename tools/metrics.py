"""Recompute every metric the docs quote, from data. Requirement N-3.

Why this exists: PLAN.md asserted "~1,100,000 words per sitting" for weeks. The real
figure is 68,861 — wrong by 16x. Nobody noticed because nothing recomputed it. Any
number written by hand into a document drifts, and a wrong number in a correctness-
focused project is worse than no number: it silently justifies wrong decisions.

So documentation cites THIS SCRIPT's output, not a typed figure. If a number changes,
the script changes and the diff is visible.

    python3 tools/metrics.py            # human-readable
    python3 tools/metrics.py --json     # machine-readable
    python3 tools/metrics.py --check    # verify figures quoted in docs still match
"""

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def corpus():
    m = json.load(open(os.path.join(ROOT, "data", "manifest.json")))
    s = m["sittings"]
    words = sum(e["words"] for e in s.values())
    reports = sum(e["reports"] for e in s.values())
    turns = sum(e["turns"] or 0 for e in s.values())
    summ = sum(e["summarisation"]["summarisable"] for e in s.values())
    done = sum(e["summarisation"]["summarised"] for e in s.values())
    return {
        "sittings": len(s),
        "reports": reports,
        "words": words,
        "turns": turns,
        "words_per_sitting": words // max(1, len(s)),
        "reports_per_sitting": round(reports / max(1, len(s)), 1),
        "years": f"{min(e['year'] for e in s.values())}-{max(e['year'] for e in s.values())}",
        "parliaments": sorted({e["parliament_no"] for e in s.values() if e["parliament_no"]}),
        "summarisable_reports": summ,
        "summarised_reports": done,
    }


def dataset():
    p = os.path.join(ROOT, "pipeline", "dataset", "index.json")
    if not os.path.exists(p):
        return {"built": False}
    idx = json.load(open(p))
    items = idx["items"]
    by_tier = {}
    for i in items:
        t = by_tier.setdefault(i["tier"], {"items": 0, "chunks": 0, "chars": 0})
        t["items"] += 1
        t["chunks"] += i["chunk_count"]
        t["chars"] += i["prompt_chars"]
    sentences = sum(i["sentence_count"] for i in items)
    return {
        "built": True,
        "items": len(items),
        "sentences": sentences,
        "chunks": sum(i["chunk_count"] for i in items),
        "prompt_chars": sum(i["prompt_chars"] for i in items),
        "tiers": by_tier,
    }


def briefs():
    n = len([f for f in glob.glob(os.path.join(ROOT, "summaries", "20*", "*.json"))])
    pts = 0
    for f in glob.glob(os.path.join(ROOT, "summaries", "20*", "*.json")):
        try:
            pts += len(json.load(open(f)).get("key_points") or [])
        except (OSError, ValueError):
            pass
    return {"briefs": n, "verified_points": pts}


def all_metrics():
    c, d, b = corpus(), dataset(), briefs()
    return {"corpus": c, "dataset": d, "briefs": b,
            # the two numbers that are easy to confuse, kept side by side on purpose
            "units": {
                "note": "reports are merged into items; never interchange them",
                "summarisable_reports": c["summarisable_reports"],
                "policy_items": d.get("items"),
                "difference": (c["summarisable_reports"] - d.get("items", 0)
                               if d.get("built") else None),
            }}


def show(m):
    c, d, b, u = m["corpus"], m["dataset"], m["briefs"], m["units"]
    print("CORPUS")
    print(f"  sittings ................ {c['sittings']:,}  ({c['years']})")
    print(f"  reports ................. {c['reports']:,}")
    print(f"  words ................... {c['words']:,}")
    print(f"  turns ................... {c['turns']:,}")
    print(f"  words per sitting ....... {c['words_per_sitting']:,}")
    print(f"  reports per sitting ..... {c['reports_per_sitting']:,}")
    print(f"  parliaments ............. {', '.join(c['parliaments'])}")
    print(f"  summarisable reports .... {c['summarisable_reports']:,}")
    print(f"  summarised reports ...... {c['summarised_reports']:,}")
    print()
    print("BRIEFS")
    print(f"  briefs on disk .......... {b['briefs']:,}")
    print(f"  verified points ......... {b['verified_points']:,}")
    print()
    if d["built"]:
        print("DATASET (stage 1)")
        print(f"  policy items ............ {d['items']:,}")
        print(f"  sentences ............... {d['sentences']:,}")
        print(f"  chunks .................. {d['chunks']:,}")
        print(f"  prompt chars ............ {d['prompt_chars']:,}")
        for t in ("small", "heavy"):
            v = d["tiers"].get(t)
            if v:
                print(f"    {t:<6} {v['items']:>5,} items  {v['chunks']:>6,} chunks  "
                      f"{v['chars']:>13,} chars")
        print()
    print("UNITS  (the two numbers never to interchange)")
    print(f"  summarisable reports .... {u['summarisable_reports']:,}")
    print(f"  policy items ............ {u['policy_items']:,}")
    print(f"  difference (merged) ..... {u['difference']:,}")


def check(m):
    """Verify figures quoted in docs still match the data."""
    docs = {}
    for name in ("PLAN.md", "README.md", "REQUIREMENTS.md", "SUMMARISATION.md"):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            docs[name] = open(p, encoding="utf-8").read()
    c = m["corpus"]
    # each entry: doc figure -> what the data says
    checks = [
        ("68,861 words per sitting", f"{c['words_per_sitting']:,}"),
        ("22,793,049 words", f"{c['words']:,}"),
        ("331 sittings", f"{c['sittings']:,}"),
    ]
    bad = 0
    for label, actual in checks:
        found = any(label in t for t in docs.values())
        if found and label.split()[0] != actual:
            # the label is what the docs say; confirm the data agrees
            if actual not in label:
                print(f"  MISMATCH: docs say '{label}', data says {actual}")
                bad += 1
    print(f"  {bad} mismatch(es) between docs and data")
    return bad


if __name__ == "__main__":
    m = all_metrics()
    if "--json" in sys.argv:
        print(json.dumps(m, indent=2))
    elif "--check" in sys.argv:
        sys.exit(1 if check(m) else 0)
    else:
        show(m)
