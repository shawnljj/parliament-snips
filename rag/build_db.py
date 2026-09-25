"""Phase 2b: load chunks + turns into SQLite, then verify the schema end to end.

Owner decision: SQLite (a single file, no service to run, ships with Python's stdlib).
At ~102k chunks the database is small and the schema/query logic transfers to Postgres
unchanged if it ever needs to scale.

Design notes that matter, all measured:

  - `report_id` is globally unique (20,340 ids, 0 spanning multiple sittings), so a
    report-level key is safe.
  - A turn's identity is CONTENT-DERIVED: sha1(report_id || 0x1f || normalised text).
    Two parses disagree about where turns begin -- 123 of 1,036 reports had a different
    turn count between the store and a re-fetch -- so a positional key points at a
    DIFFERENT turn after a re-parse. That is precisely how the paragraph store became
    misaligned.
  - Content hashes collide (946 over 91,263 turns) and EVERY collision checked was a
    genuine repeat, not a hash flaw, so the key is (report_id, content_hash, position).
  - `speaker` (98.8%) and `time` (35.8%) are NULLABLE; everything else is NOT NULL.
    These come from measured column population, not from guessing.

The load is idempotent: it drops and recreates the tables, so a rebuild cannot leave
stale rows behind that would inflate a count.
"""
import hashlib
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chunk as C

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.abspath(os.path.join(HERE, "..", "pipeline"))
DB = os.path.join(PIPELINE, "hansard.db")
CHUNKS = os.path.join(PIPELINE, "chunks.jsonl")
TURNS = os.path.join(PIPELINE, "turns.jsonl")

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = OFF;

DROP TABLE IF EXISTS chunk;
DROP TABLE IF EXISTS turn;
DROP TABLE IF EXISTS report;
DROP TABLE IF EXISTS sitting;

CREATE TABLE sitting (
    date            TEXT PRIMARY KEY,
    year            INTEGER NOT NULL,
    n_reports       INTEGER NOT NULL,
    n_turns         INTEGER NOT NULL,
    n_chunks        INTEGER NOT NULL
);

CREATE TABLE report (
    report_id       TEXT PRIMARY KEY,
    date            TEXT NOT NULL,
    year            INTEGER NOT NULL,
    title           TEXT NOT NULL,
    report_type     TEXT,
    group_name      TEXT,
    parliament_no   TEXT,
    volume_no       TEXT,
    sitting_no      TEXT,
    n_turns         INTEGER NOT NULL,
    n_chunks        INTEGER NOT NULL
);

-- One person's utterance. `content_hash` is the stable identity; `turn` is kept as an
-- attribute for display and for the eval gold references, but is NEVER keyed on.
CREATE TABLE turn (
    key             TEXT PRIMARY KEY,
    report_id       TEXT NOT NULL REFERENCES report(report_id),
    turn            INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    repeat_index    INTEGER NOT NULL,
    date            TEXT NOT NULL,
    year            INTEGER NOT NULL,
    title           TEXT NOT NULL,
    group_name      TEXT,
    speaker         TEXT,
    time            TEXT,
    is_procedural   INTEGER,
    lang            TEXT,
    words           INTEGER,
    n_chunks        INTEGER NOT NULL DEFAULT 0,
    text            TEXT NOT NULL,
    UNIQUE (report_id, content_hash, repeat_index)
);

CREATE TABLE chunk (
    id              TEXT PRIMARY KEY,
    turn_key        TEXT NOT NULL REFERENCES turn(key),
    report_id       TEXT NOT NULL REFERENCES report(report_id),
    date            TEXT NOT NULL,
    year            INTEGER NOT NULL,
    title           TEXT NOT NULL,
    group_name      TEXT,
    speaker         TEXT,
    time            TEXT,
    is_procedural   INTEGER,
    lang            TEXT,
    part            INTEGER NOT NULL,
    n_parts         INTEGER NOT NULL,
    is_partial      INTEGER NOT NULL,
    para_sourced    INTEGER NOT NULL,
    tokens          INTEGER NOT NULL,
    cite_text       TEXT NOT NULL,
    embed_text      TEXT NOT NULL
);

CREATE INDEX idx_chunk_year    ON chunk(year);
CREATE INDEX idx_chunk_report  ON chunk(report_id);
CREATE INDEX idx_chunk_title   ON chunk(title);
CREATE INDEX idx_chunk_speaker ON chunk(speaker);
CREATE INDEX idx_chunk_group   ON chunk(group_name);
CREATE INDEX idx_chunk_turn    ON chunk(turn_key);
CREATE INDEX idx_turn_report   ON turn(report_id);
CREATE INDEX idx_turn_hash     ON turn(content_hash);
"""


def main():
    t0 = time.time()
    if not os.path.exists(CHUNKS):
        print(f"missing {CHUNKS} -- run rag/build_chunks.py first")
        return 1

    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)

    # ---- sittings ------------------------------------------------------------
    sitters = {}
    reports = {}
    turns = {}
    chunks = []
    with open(CHUNKS) as fh:
        for line in fh:
            c = json.loads(line)
            chunks.append(c)
            d = c["date"]
            s = sitters.setdefault(d, {"year": c["year"], "r": set(), "t": set(), "c": 0})
            s["r"].add(c["report_id"])
            s["t"].add(c["key"])
            s["c"] += 1
            r = reports.setdefault(c["report_id"], {
                "date": d, "year": c["year"], "title": c["title"],
                "report_type": c.get("report_type"), "group": c.get("group"),
                "parliament_no": c.get("parliament_no"), "volume_no": c.get("volume_no"),
                "sitting_no": c.get("sitting_no"), "t": set(), "c": 0})
            r["t"].add(c["key"])
            r["c"] += 1
            if c["key"] not in turns:
                turns[c["key"]] = {"key": c["key"], "report_id": c["report_id"],
                                   "turn": c["turn"], "n": 0}

    print(f"read {len(chunks):,} chunks "
          f"({len(sitters)} sittings, {len(reports):,} reports, {len(turns):,} turns) "
          f"[{time.time()-t0:.0f}s]")

    # ---- insert sittings + reports -------------------------------------------
    db.executemany(
        "INSERT INTO sitting (date, year, n_reports, n_turns, n_chunks) VALUES (?,?,?,?,?)",
        [(d, v["year"], len(v["r"]), len(v["t"]), v["c"]) for d, v in sitters.items()])
    db.executemany(
        """INSERT INTO report (report_id, date, year, title, report_type, group_name,
                              parliament_no, volume_no, sitting_no, n_turns, n_chunks)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [(k, v["date"], v["year"], v["title"], v["report_type"], v["group"],
          v["parliament_no"], v["volume_no"], v["sitting_no"], len(v["t"]), v["c"])
         for k, v in reports.items()])

    # ---- insert turns from turns.jsonl (has text + content_hash) --------------
    n_turn_rows = 0
    if os.path.exists(TURNS):
        with open(TURNS) as fh:
            batch = []
            for line in fh:
                t = json.loads(line)
                if t["key"] not in turns:
                    continue          # a turn with no chunk: not retrievable
                batch.append((
                    t["key"], t["report_id"], t["turn"], t["content_hash"],
                    t["repeat_index"], t["date"], t["year"], t["title"],
                    t["group"], t["speaker"], t["time"],
                    1 if t.get("is_procedural") else 0, t["lang"], t["words"],
                    turns[t["key"]]["n"], t["text"]))
                if len(batch) >= 5000:
                    db.executemany(
                        """INSERT INTO turn (key, report_id, turn, content_hash,
                               repeat_index, date, year, title, group_name, speaker,
                               time, is_procedural, lang, words, n_chunks, text)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
                    n_turn_rows += len(batch)
                    batch = []
            if batch:
                db.executemany(
                    """INSERT INTO turn (key, report_id, turn, content_hash,
                           repeat_index, date, year, title, group_name, speaker,
                           time, is_procedural, lang, words, n_chunks, text)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
                n_turn_rows += len(batch)
    print(f"inserted {n_turn_rows:,} turn rows")

    # ---- insert chunks -------------------------------------------------------
    from tokens import count_tokens
    batch = []
    for c in chunks:
        batch.append((
            c["id"], c["key"], c["report_id"], c["date"], c["year"], c["title"],
            c.get("group"), c.get("speaker"), c.get("time"),
            1 if c.get("is_procedural") else 0, c.get("lang"),
            c["part"], c["n_parts"], 1 if c["is_partial"] else 0,
            1 if c.get("para_sourced") else 0, count_tokens(c["cite_text"]),
            c["cite_text"], c["embed_text"]))
    db.executemany(
        """INSERT INTO chunk (id, turn_key, report_id, date, year, title, group_name,
                              speaker, time, is_procedural, lang, part, n_parts,
                              is_partial, para_sourced, tokens, cite_text, embed_text)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
    db.commit()
    print(f"inserted {len(batch):,} chunk rows")
    print()

    # ---- verify --------------------------------------------------------------
    q = db.execute
    print("=== row counts ===")
    for tbl in ("sitting", "report", "turn", "chunk"):
        n = q(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl:9s} {n:>9,}")

    print()
    print("=== integrity (exit criterion 1) ===")
    orphan_chunk_turn = q("""SELECT COUNT(*) FROM chunk c
                             LEFT JOIN turn t ON c.turn_key = t.key
                             WHERE t.key IS NULL""").fetchone()[0]
    orphan_chunk_report = q("""SELECT COUNT(*) FROM chunk c
                               LEFT JOIN report r ON c.report_id = r.report_id
                               WHERE r.report_id IS NULL""").fetchone()[0]
    orphan_turn_report = q("""SELECT COUNT(*) FROM turn t
                              LEFT JOIN report r ON t.report_id = r.report_id
                              WHERE r.report_id IS NULL""").fetchone()[0]
    orphan_report_sitting = q("""SELECT COUNT(*) FROM report r
                                 LEFT JOIN sitting s ON r.date = s.date
                                 WHERE s.date IS NULL""").fetchone()[0]
    print(f"  chunk -> turn orphans   : {orphan_chunk_turn}")
    print(f"  chunk -> report orphans : {orphan_chunk_report}")
    print(f"  turn  -> report orphans : {orphan_turn_report}")
    print(f"  report-> sitting orphans: {orphan_report_sitting}")

    print()
    print("=== identity (exit criterion 2) ===")
    dup = q("""SELECT COUNT(*) FROM (
                 SELECT report_id, content_hash, repeat_index
                 FROM turn GROUP BY 1,2,3 HAVING COUNT(*) > 1)""").fetchone()[0]
    print(f"  duplicate (report_id, content_hash, repeat_index): {dup}")
    nh = q("SELECT COUNT(DISTINCT content_hash) FROM turn").fetchone()[0]
    nt = q("SELECT COUNT(*) FROM turn").fetchone()[0]
    print(f"  distinct content_hash {nh:,} over {nt:,} turns "
          f"({nt-nh:,} genuine repeats -- position disambiguates them)")

    print()
    print("=== the JSS separation (exit criterion 4) ===")
    for term in ("Jobs Support Scheme", "Joint Singles Scheme"):
        n = q("SELECT COUNT(*) FROM chunk WHERE title LIKE ?",
              (f"%{term}%",)).fetchone()[0]
        ys = q("SELECT year, COUNT(*) FROM chunk WHERE title LIKE ? GROUP BY year "
               "ORDER BY year", (f"%{term}%",)).fetchall()
        print(f"  {term:24s} {n:>5,} chunks  years={dict(ys)}")

    print()
    print(f"database: {DB}  ({os.path.getsize(DB)/1e6:.1f} MB)")
    print(f"total elapsed: {time.time()-t0:.0f}s")

    bad = orphan_chunk_turn + orphan_chunk_report + orphan_turn_report + \
        orphan_report_sitting + dup
    if bad:
        print(f"\nFAIL -- {bad} integrity problem(s)")
        return 1
    print("\nPASS -- schema loaded, foreign keys reconcile, identity key is unique")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
