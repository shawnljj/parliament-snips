"""Normalise a raw parsed sitting into the canonical Parsnips schema."""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parsnips_fetch import group_for


def normalise(raw):
    reports = []
    for r in raw["reports"]:
        turns = []
        for t in r.get("turns", []):
            turns.append({
                "speaker": t.get("speaker_raw"),
                "lang": t.get("lang") or "English",
                "text": t.get("text") or "",
                "words": t.get("words", len((t.get("text") or "").split())),
            })
        rtype = r.get("report_type")
        reports.append({
            "report_id": r["report_id"],
            "report_type": rtype,
            "group": group_for(rtype, r["report_id"]),
            "title": (r.get("title") or "").strip(),
            "sitting_date": r.get("sitting_date"),
            "parliament_no": r.get("parl_no"),
            "sitting_no": r.get("sitting_no"),
            "volume_no": r.get("volume_no"),
            "mp_names": r.get("mp_names"),
            "words": sum(t["words"] for t in turns),
            "turns": turns,
        })
    reports.sort(key=lambda x: -x["words"])

    words = sum(r["words"] for r in reports)
    turns = sum(len(r["turns"]) for r in reports)
    tagged = sum(1 for r in reports for t in r["turns"] if t["speaker"])
    mx = raw.get("max_result") or len(reports)
    coverage = {
        "date": raw["date"],
        "collected": len(reports),
        "max_result": mx,
        "ratio": round(len(reports) / mx, 3) if mx else None,
        "words": words,
        "turns": turns,
        "speaker_attribution": round(tagged / turns, 3) if turns else None,
    }
    return {"date": raw["date"], "coverage": coverage, "reports": reports}


if __name__ == "__main__":
    raw = json.load(open(sys.argv[1]))
    out = normalise(raw)
    dest = sys.argv[2]
    # Atomic: a truncated sitting JSON would be consumed by the site builder.
    tmp = f"{dest}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, dest)
    c = out["coverage"]
    print(f"{dest}: {c['collected']}/{c['max_result']} reports, {c['words']:,} words, "
          f"{c['turns']} turns, attribution {c['speaker_attribution']}")
