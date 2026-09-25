"""Build the paragraph store for every report that needs splitting.

Owner decision: do the FULL 1,036-report re-fetch before embedding, not a partial one
that would need re-embedding later.

Why a re-fetch at all: the stored corpus is flattened (scraper/hansard_parse.py:280
joins continuation <p> elements with a space), so 0 of 91,964 turns contain a paragraph
break. The source HTML still has the structure, so it is recoverable -- but only by
asking the source again.

Scope, measured: only turns that EXCEED the embed budget need splitting (3,050 turns,
3.34%). A turn that fits is already the unit, so its report needs no structure.

The run is CACHE-BACKED and RESUMABLE: fetch_paragraphs() writes one JSON per report, so
an interrupted run continues rather than restarting. Delete a cache file to force a
re-fetch of that report.

Alignment is measured, not assumed. For every report it compares the fetched turn list
against the stored one by POSITION and by CONTENT, and reports the difference. Measured
so far: 2 of 40 reports disagree by position (bill-203 9 vs 15, bill-204 14 vs 24), which
is why the chunker matches by content -- this run measures how widespread that is.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chunk as C
from tokens import count_tokens, TOKEN_LIMIT

SUMMARY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "pipeline", "para_store_summary.json")


def head(s):
    return C.norm(C.CLEAN_FOR_MATCH(s or ""))[:100]


def main():
    t0 = time.time()
    turns = C.load_turns()
    over = [t for t in turns if not t["empty"]
            and count_tokens(t["clean"]) > TOKEN_LIMIT]
    reports = sorted({t["report_id"] for t in over})

    print(f"turns over the {TOKEN_LIMIT}-token budget : {len(over):,}")
    print(f"reports needing paragraph structure  : {len(reports):,}")
    print()

    by_report = {}
    for t in turns:
        by_report.setdefault(t["report_id"], []).append(t)

    stats = {
        "reports": len(reports),
        "ok": 0, "error": 0,
        "index_aligned": 0, "index_mismatch": 0,
        "content_fully_aligned": 0, "content_partial": 0, "content_bad": 0,
        "paragraphs": 0, "over_budget_turns": len(over),
        "failures": [], "mismatches": [],
    }

    n = 0
    for rid in reports:
        n += 1
        stored = by_report[rid]
        got = C.fetch_paragraphs(rid)
        if got.get("error"):
            stats["error"] += 1
            stats["failures"].append({"report_id": rid, "error": got["error"]})
            if len(stats["failures"]) <= 10:
                print(f"  [{n}/{len(reports)}] {rid}: ERROR {got['error'][:60]}")
            continue
        stats["ok"] += 1
        stats["paragraphs"] += got.get("n_paragraphs", 0)

        fturns = got.get("turns") or []

        # position alignment
        by_idx = sum(1 for i, st in enumerate(stored)
                     if i < len(fturns) and head(fturns[i].get("text", "")) == head(st["clean"]))
        if by_idx == len(stored):
            stats["index_aligned"] += 1
        else:
            stats["index_mismatch"] += 1
            stats["mismatches"].append(
                {"report_id": rid, "stored": len(stored), "fetched": len(fturns),
                 "index_aligned": by_idx})

        # content alignment
        idx = {}
        for ft in fturns:
            idx.setdefault(head(ft.get("text", "")), ft)
        matched = sum(1 for st in stored if head(st["clean"]) in idx)
        if matched == len(stored):
            stats["content_fully_aligned"] += 1
        elif matched:
            stats["content_partial"] += 1
        else:
            stats["content_bad"] += 1

        if n % 100 == 0 or n == len(reports):
            el = time.time() - t0
            print(f"  [{n}/{len(reports)}] {el:.0f}s  ok={stats['ok']} "
                  f"err={stats['error']} paras={stats['paragraphs']:,} "
                  f"idx_mismatch={stats['index_mismatch']}")

    print()
    print("=== summary ===")
    print(f"  reports fetched          : {stats['ok']:,} / {stats['reports']:,}")
    print(f"  fetch errors             : {stats['error']:,}")
    print(f"  paragraphs recovered     : {stats['paragraphs']:,}")
    print(f"  reports index-aligned    : {stats['index_aligned']:,}")
    print(f"  reports INDEX MISMATCH   : {stats['index_mismatch']:,}  <-- why content match")
    print(f"  reports content-aligned  : {stats['content_fully_aligned']:,}")
    print(f"  reports content PARTIAL  : {stats['content_partial']:,}")
    print(f"  reports content unusable : {stats['content_bad']:,}")
    print(f"  elapsed                  : {time.time()-t0:.0f}s")

    os.makedirs(os.path.dirname(SUMMARY), exist_ok=True)
    with open(SUMMARY, "w") as fh:
        json.dump(stats, fh, indent=1)
    print(f"  written: {os.path.abspath(SUMMARY)}")

    usable = stats["content_fully_aligned"] + stats["content_partial"]
    print()
    print(f"  -> {usable:,} of {stats['reports']:,} reports can use paragraph structure")
    print(f"  -> {stats['content_bad']:,} fall back to sentence splitting "
          f"(still verbatim, just no paragraph boundary)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
