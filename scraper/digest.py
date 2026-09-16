"""Dump a scannable reading digest of a parsed sitting, for editorial work."""
import json, sys

path = sys.argv[1]
sit = json.load(open(path))
cov = sit.get("coverage", {})
print(f"# SITTING {sit['date']}  reports={cov.get('collected')}/{cov.get('max_result')} "
      f"words={cov.get('words')} turns={cov.get('turns')}")

mode = sys.argv[2] if len(sys.argv) > 2 else "all"
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 200
maxreports = int(sys.argv[4]) if len(sys.argv) > 4 else 8

groups = None if mode == "all" else set(mode.split(","))

shown = 0
for r in sit["reports"]:
    if groups and r["group"] not in groups:
        continue
    if shown >= maxreports:
        break
    shown += 1
    print(f"\n{'='*100}")
    print(f"[{r['group']}] {r['title']}")
    print(f"  id={r['report_id']} type={r['report_type']} words={r['words']} turns={len(r['turns'])}")
    for t in r["turns"]:
        sp = t["speaker"] or "(unattributed)"
        print(f"  <{sp}> {t['text'][:limit]}")
