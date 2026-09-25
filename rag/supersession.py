"""Detect turns that SUPERSEDE an earlier statement, and link them.

WHY THIS EXISTS
---------------
Hansard corrects itself. A Minister who misstates a figure at Question Time files a
clarification saying so, and the corrected text is delivered as a separate turn. Measured on
the A1 question:

  oral-answer-2271#t1        the ORIGINAL reply: "... $15.5 billion of the JSS payouts went
                             to SMEs ..."
  written-statement-1481#t1  "I wish to make the following factual correction to the reply
                             given during Question Time ... My reply should read as
                             follows:"          <- quotes the retracted figure
  written-statement-1481#t2  "... $10.5 billion of the JSS payouts went to SMEs."   <- CORRECTED

The relation is encoded as ADJACENCY and nothing records it. So the retracted figure and its
correction compete as independent passages, and the retracted one can be retrieved and quoted
-- which is what happened: the correction sat at rank 1, the model quoted the superseded
figure from rank 9, and BOTH citations passed the verbatim gate because both are real text.
A system that answers a factual question with a number the record has withdrawn is wrong in
the way that matters most.

PRECISION BEFORE RECALL
-----------------------
A wrong supersession link SILENTLY DISCARDS a correct passage -- the same failure class as a
prefilter that excludes the gold. So the detector uses only Hansard's own terms of art and
requires a first-person self-correction signal, and the loose words are deliberately excluded:

    "factual correction"            100 turns   HIGH -- the term of art
    "should read as follows"         92 turns   HIGH -- the correction's lead-in
    "correction to the ..."          21 turns   HIGH
    ---------------------------------------------------------------
    union of the above              106 turns
    "i wish to (make|correct)"      184 turns   LOW -- "I wish to make the following points"
    "erroneous"                     134 turns   LOW
    "inaccurate"                    172 turns   LOW

The loose patterns match 5x more turns, most of them unrelated, and using them would have
produced a claimed 508 "corrections" against a real 106.

A third class must also be excluded: POLICY MECHANISM. Members debate the POFMA power to
issue "factual corrections" to falsehoods, which supersedes nothing:

  "the proposal for the Minister to take the step of issuing a factual correction on any
   falsehood is the best method"                       <- no corrected text exists

Measured separation: 101 self-corrections, 3 policy-mechanism, 2 ambiguous (of 106).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.abspath(os.path.join(HERE, "..", "pipeline"))
DB = os.path.join(PIPELINE, "hansard.db")

# Hansard's own terms of art for a correction
HIGH = re.compile(r"factual correction|should read as follows|"
                  r"correction to the (reply|answer|statement)", re.I)
# first person + a pointer to an earlier statement of the speaker's own
SELF = re.compile(
    r"to the reply given|to what i said|my reply should read|my (statement|answer) should read"
    r"|i wish to (make|correct)|i would like to correct"
    r"|to (my|the) (earlier|previous) (reply|statement)"
    r"|at the sitting on|at the sitting of", re.I)
# POFMA / falsehood debate: a correction as a POLICY INSTRUMENT, nothing is superseded
MECHANISM = re.compile(
    r"pofma|protection from online falsehoods|falsehood|false claim|issue a correction"
    r"|correction direction|publisher", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS supersession (
    announce_turn   TEXT NOT NULL,          -- the turn that announces a correction
    supersede_turn  TEXT,                   -- the corrected text (NULL if none found)
    report_id       TEXT NOT NULL,
    evidence        TEXT,                   -- the phrase that identified it
    same_speaker    INTEGER,                -- 1 if the corrected turn has the same speaker
    announcer       TEXT,                   -- speaker of the announcing turn
    PRIMARY KEY (announce_turn)
);
CREATE INDEX IF NOT EXISTS idx_supersede ON supersession(supersede_turn);
"""


def load_turns(db):
    """turn_key -> joined text, plus the first row's metadata."""
    text = defaultdict(str)
    meta = {}
    for r in db.execute("SELECT turn_key, report_id, title, date, speaker, cite_text "
                        "FROM chunk ORDER BY turn_key, id"):
        text[r["turn_key"]] += " " + (r["cite_text"] or "")
        meta.setdefault(r["turn_key"], dict(r))
    return text, meta


def turn_no(tk):
    return int(tk.rsplit("#t", 1)[1]) if "#t" in tk else -1


def detect(text, meta):
    """Return the links this module would write, WITHOUT writing them."""
    links = []
    for tk in sorted(text):
        t = text[tk]
        m = HIGH.search(t)
        if not m:
            continue
        if not SELF.search(t):
            continue                      # needs a first-person self-correction signal
        if MECHANISM.search(t):
            continue                      # POFMA/falsehood debate: supersedes nothing
        rid = meta[tk]["report_id"]
        nxt = f"{rid}#t{turn_no(tk) + 1}"
        sup = nxt if nxt in text else None
        links.append({
            "announce_turn": tk,
            "supersede_turn": sup,
            "report_id": rid,
            "evidence": m.group(0),
            "same_speaker": (int(bool(sup) and
                                 (meta[sup]["speaker"] or "") == (meta[tk]["speaker"] or ""))
                             if sup else None),
            "announcer": meta[tk]["speaker"],
            "title": meta[tk]["title"],
            "date": meta[tk]["date"],
        })
    return links


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--json", help="write the links to this path")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    text, meta = load_turns(db)
    print(f"turns indexed: {len(text):,}")

    links = detect(text, meta)
    with_sup = [l for l in links if l["supersede_turn"]]
    print(f"self-corrections detected : {len(links)}")
    print(f"  with a corrected turn   : {len(with_sup)}")
    print(f"  announcing only (no next): {len(links) - len(with_sup)}")
    if with_sup:
        same = sum(1 for l in with_sup if l["same_speaker"])
        print(f"  corrected turn has the SAME speaker: {same}/{len(with_sup)} "
              f"({same/len(with_sup)*100:.0f}%)")

    print()
    print("=== GATE: every link must resolve to a real turn in the same report ===")
    problems = []
    for l in with_sup:
        s = l["supersede_turn"]
        if s not in text:
            problems.append(f"{l['announce_turn']} -> {s}: target does not exist")
            continue
        if meta[s]["report_id"] != l["report_id"]:
            problems.append(f"{l['announce_turn']} -> {s}: different report")
        if turn_no(s) != turn_no(l["announce_turn"]) + 1:
            problems.append(f"{l['announce_turn']} -> {s}: not the adjacent turn")
        if not (text[s] or "").strip():
            problems.append(f"{l['announce_turn']} -> {s}: target is empty")
    print(f"  problems: {len(problems)}")
    for p in problems[:10]:
        print(f"    {p}")

    print()
    print("=== sample of what would be linked ===")
    for l in with_sup[:10]:
        print(f"  {l['announce_turn']:36s} -> {l['supersede_turn']:36s} "
              f"{'same speaker' if l['same_speaker'] else 'DIFFERENT speaker'}  "
              f"{(l['title'] or '')[:34]}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(links, fh, indent=1)
        print(f"\nwritten: {args.json}")

    if args.dry_run:
        print("\nDRY RUN -- nothing written to the database")
        return 0

    db.executescript(SCHEMA)
    db.executemany(
        "INSERT OR REPLACE INTO supersession "
        "(announce_turn, supersede_turn, report_id, evidence, same_speaker, announcer) "
        "VALUES (:announce_turn, :supersede_turn, :report_id, :evidence, :same_speaker, "
        ":announcer)",
        links)
    db.commit()

    n = db.execute("SELECT COUNT(*) FROM supersession").fetchone()[0]
    linked = db.execute("SELECT COUNT(*) FROM supersession "
                        "WHERE supersede_turn IS NOT NULL").fetchone()[0]
    print(f"\nwrote {n} rows to supersession ({linked} with a corrected turn)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
