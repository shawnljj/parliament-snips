#!/usr/bin/env python3
"""Load the summary corpus into SQLite, with the anchor resolved as a real FK to turn.key.

Design decided from measurement, not assumption:

  * The dataset's per-turn `t` and per-sentence `sid` are ITEM-LOCAL (a sid like `s00002` occurs
    2,205 times across the corpus), so neither can be a key on its own. `sid` is kept as text for
    reference, keyed by (section_id, ord).
  * Measured: a summary sentence's own text resolves to EXACTLY ONE SQLite turn 99.9% of the time
    (2,835/2,838 sentences; 3 ambiguous; 0 unlocated) and the whole corpus anchors in ~1 minute.
    So the anchor is resolved by content and STORED, rather than derived by arithmetic at read
    time -- arithmetic on an item-local index would land on a neighbouring speech, silently.

The anchor column is NULLABLE on purpose: a sentence that cannot be anchored uniquely is recorded
as unanchored with the reason, never guessed at. The FK to turn(key) then makes any dangling
anchor impossible to load at all, because FK enforcement is on.

Schema
  summary_item      one row per brief  (FK -> nothing; its own PK is item_id)
  summary_item_report  item -> report  (FK -> report.report_id)      many-to-many
  summary_item_sitting item -> sitting (FK -> sitting.date)          many-to-many
  summary_section   one row per section (FK -> summary_item)
  summary_sentence  one row per verbatim sentence, ANCHORED to turn(key) when unambiguous

Offsets char_start/char_end are into turn(text) in SQLite, so a reader can highlight the exact
span in place. They are asserted to slice back to the sentence (allowing the known punctuation
differences) before a row is written.

Usage:
    python3 build_summaries.py --dry-run     # measure, write nothing
    python3 build_summaries.py --rebuild     # drop + recreate + load
    python3 build_summaries.py --verify      # assertions only
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# The significance rules live beside the summariser that selects sentences, so the loader and the
# selector cannot answer "what is worth surfacing" differently.
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'summariser'))
import significance as SIG  # noqa: E402

DB = '/Users/shawnlin/parsnips/pipeline/hansard.db'
SUM_DIR = '/Users/shawnlin/parsnips/summaries'

# Characters that differ between the summary layer and the stored transcript.
# Normalisation must preserve an index map so offsets can be handed back in ORIGINAL coordinates.
SUBS = {'\u2019': "'", '\u2018': "'", '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-', '\u00a0': ' '}

DDL = """
CREATE TABLE summary_item (
  item_id        TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  group_name     TEXT,
  tier           TEXT,
  year           INTEGER,
  n_sections     INTEGER NOT NULL DEFAULT 0,
  n_sentences    INTEGER NOT NULL DEFAULT 0,
  n_anchored     INTEGER NOT NULL DEFAULT 0,
  n_ambiguous    INTEGER NOT NULL DEFAULT 0,
  n_unanchored   INTEGER NOT NULL DEFAULT 0,
  n_context      INTEGER NOT NULL DEFAULT 0,
  sentences_total    INTEGER,
  sentences_selected INTEGER,
  selection_share    REAL
);

CREATE TABLE summary_item_report (
  item_id   TEXT NOT NULL REFERENCES summary_item(item_id) ON DELETE CASCADE,
  report_id TEXT NOT NULL REFERENCES report(report_id),
  ordinal   INTEGER NOT NULL,
  PRIMARY KEY (item_id, report_id)
);

CREATE TABLE summary_item_sitting (
  item_id TEXT NOT NULL REFERENCES summary_item(item_id) ON DELETE CASCADE,
  date    TEXT NOT NULL REFERENCES sitting(date),
  PRIMARY KEY (item_id, date)
);

CREATE TABLE summary_section (
  section_id INTEGER PRIMARY KEY,
  item_id    TEXT NOT NULL REFERENCES summary_item(item_id) ON DELETE CASCADE,
  ordinal    INTEGER NOT NULL,
  label      TEXT,
  summary    TEXT NOT NULL,
  UNIQUE (item_id, ordinal)
);

CREATE TABLE summary_sentence (
  section_id        INTEGER NOT NULL REFERENCES summary_section(section_id) ON DELETE CASCADE,
  ord               INTEGER NOT NULL,
  sid               TEXT NOT NULL,
  speaker           TEXT,
  attributed        INTEGER NOT NULL DEFAULT 0,
  added_for_context INTEGER NOT NULL DEFAULT 0,
  text              TEXT NOT NULL,
  turn_key          TEXT REFERENCES turn(key),
  char_start        INTEGER,
  char_end          INTEGER,
  anchor_method     TEXT,
  -- NULL when this sentence is SURFACED. A reason -- 'chair_housekeeping', 'restatement',
  -- 'near_duplicate' -- when significance.classify() says it is procedure or a repeat, so the page
  -- renders it collapsed and says how much it hid. The row is never deleted: an exclusion that is
  -- not recorded on the row is indistinguishable from a data-loss bug, which is how an unanchored
  -- pattern once emptied 44 items while every check passed.
  hidden_reason     TEXT,
  PRIMARY KEY (section_id, ord)
);

CREATE INDEX idx_si_year        ON summary_item(year);
CREATE INDEX idx_si_group       ON summary_item(group_name);
CREATE INDEX idx_sir_report     ON summary_item_report(report_id);
CREATE INDEX idx_sis_date       ON summary_item_sitting(date);
CREATE INDEX idx_ssent_turn     ON summary_sentence(turn_key);
CREATE INDEX idx_ssent_sid      ON summary_sentence(sid);
CREATE INDEX idx_ssec_item      ON summary_section(item_id);
"""

TABLES = ('summary_item', 'summary_item_report', 'summary_item_sitting',
          'summary_section', 'summary_sentence')


def norm_map(s):
    """Normalise text, returning (normalised, index_map).

    index_map[i] is the offset in the ORIGINAL string of normalised character i, so a match found
    in normalised space converts back to original coordinates exactly.
    """
    out, idx = [], []
    prev_space = True
    for i, ch in enumerate(s or ''):
        ch = SUBS.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            out.append(' ')
            idx.append(i)
            prev_space = True
        else:
            out.append(ch)
            idx.append(i)
            prev_space = False
    while out and out[-1] == ' ':
        out.pop()
        idx.pop()
    return ''.join(out), idx


def anchor(probe, turns, own_report_ids):
    """Resolve a sentence's verbatim text to (turn_key, char_start, char_end, method).

    turns: list of (key, report_id, norm_text, index_map, raw_text)
    Returns None when it cannot be resolved to exactly one turn -- never guesses.

    Preference when several turns contain the text: the item's OWN report wins (a sentence can
    legitimately recur in another report, e.g. a repeated procedural line). If that still leaves
    more than one, the sentence is recorded ambiguous.
    """
    hits = [t for t in turns if probe in t[2]]
    if not hits:
        return None
    method = 'exact'
    if len(hits) > 1:
        own = [t for t in hits if t[1] in own_report_ids]
        if len(own) == 1:
            hits = own
            method = 'own-report'
        else:
            return ('__ambiguous__', None, None, None)
    key, _rid, ntext, imap, raw = hits[0]
    start = ntext.index(probe)
    end = start + len(probe)
    # map normalised offsets back to original coordinates
    o_start = imap[start]
    o_end = imap[end - 1] + 1
    if method == 'exact' and (probe != ntext or len(imap) != len(raw)):
        pass
    # verify the slice reconstructs the sentence (allowing the known punctuation differences)
    sliced = raw[o_start:o_end]
    if norm_map(sliced)[0] != probe:
        # fall back to a looser check: same normalised content ignoring dash/quote variants
        a = re.sub(r'[\u2013\u2014-]', '-', norm_map(sliced)[0])
        b = re.sub(r'[\u2013\u2014-]', '-', probe)
        if a != b:
            return None
        method = 'normalised'
    return (key, o_start, o_end, method)


def turn_texts(db, keys):
    """The full words of each turn, keyed by turn.key.

    The selected sentences are not enough to see a correction: the retraction is usually NOT selected
    (the owner's case is a turn where only the corrected claim was chosen), so the turn's own text is
    what tells us a correction happened here at all.
    """
    keys = [k for k in keys if k]
    out = {}
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        qs = ','.join('?' * len(chunk))
        for r in db.execute(f"SELECT key, text FROM turn WHERE key IN ({qs})", chunk):
            out[r['key']] = r['text']
    return out


def stamp_reasons(rows_sent, db=None):
    """Fill hidden_reason on anchored rows, using the turn-scoped significance pass.

    `rows_sent` tuples are (sec_id, ord, sid, speaker, attributed, context, text, turn_key,
    char_start, char_end, method, hidden_reason). Returns a new list; never drops a row.

    Why this cannot be folded into the per-sentence classify(): the correction rule fires on a PAIR
    of adjacent sentences, and adjacency means the order the sentences were SPOKEN in. Sections
    regroup them by topic, so the order has to be rebuilt from char_start within each
    (turn, speaker) group -- exactly the reader's view of the transcript.
    """
    groups = {}
    for i, r in enumerate(rows_sent):
        if r[7] and r[8] is not None:          # anchored rows only: an unanchored row has no position
            groups.setdefault((r[7], r[3]), []).append((r[8], i, r[6]))
    reasons = [None] * len(rows_sent)
    ttext = turn_texts(db, {tk for (tk, _sp) in groups}) if db is not None else {}
    for (tk, _sp), members in groups.items():
        members.sort()
        idxs = [m[1] for m in members]
        texts = [m[2] for m in members]
        for k, why in zip(idxs, SIG.classify_turn_text(texts, [_sp] * len(texts),
                                                       ttext.get(tk, ''))):
            reasons[k] = why
    # Unanchored rows still get a reason from the single-sentence rules: their reason is recorded on
    # the row like any other, so an exclusion can always be explained -- never silently applied.
    for i, r in enumerate(rows_sent):
        if reasons[i] is None and not r[7]:
            reasons[i] = SIG.classify(r[6], r[3])
    return [r[:11] + (reasons[i],) for i, r in enumerate(rows_sent)]


def build(db, dry_run=False, verbose=True):
    files = sorted(glob.glob(os.path.join(SUM_DIR, '*', '*.json')))
    if verbose:
        print(f"summary files: {len(files):,}")
    stats = dict(items=0, sections=0, sentences=0, anchored=0, ambiguous=0,
                 unanchored=0, context=0, dangling=0, items_no_reports=0)
    t0 = time.time()
    sec_id = 0
    problems = []

    for n, sf in enumerate(files, 1):
        item = os.path.splitext(os.path.basename(sf))[0]
        try:
            sj = json.load(open(sf))
        except (ValueError, OSError) as e:
            problems.append(f"{item}: unreadable ({e})")
            continue
        meta = sj.get('_meta') or {}
        rids = meta.get('report_ids') or []
        dates = meta.get('sitting_dates') or []
        if not rids:
            stats['items_no_reports'] += 1
            problems.append(f"{item}: no report_ids")
            continue

        qs = ','.join('?' * len(rids))
        turns = []
        for r in db.execute(f"SELECT key, report_id, text FROM turn WHERE report_id IN ({qs})", rids):
            nt, im = norm_map(r['text'])
            if nt:
                turns.append((r['key'], r['report_id'], nt, im, r['text']))

        sections = sj.get('sections') or []
        sentences = [x for s in sections for x in (s.get('sentences') or [])]

        # Resolve every anchor BEFORE writing anything: the FK from summary_section to
        # summary_item means the parent row must exist first, so all three levels are computed
        # here and inserted in FK order (item -> section -> sentence) below.
        rows_sent = []
        rows_sec = []
        for si, s in enumerate(sections):
            sec_id += 1
            for oi, x in enumerate(s.get('sentences') or []):
                probe, _ = norm_map(x.get('text') or '')
                resolved = anchor(probe, turns, set(rids)) if probe else None
                if resolved and resolved[0] == '__ambiguous__':
                    tk, cs, ce, meth = None, None, None, 'ambiguous'
                    stats['ambiguous'] += 1
                elif resolved:
                    tk, cs, ce, meth = resolved
                    stats['anchored'] += 1
                else:
                    tk, cs, ce, meth = None, None, None, 'unresolved'
                    stats['unanchored'] += 1
                if not tk and probe and meth == 'unresolved':
                    stats['dangling'] += 1
                if x.get('added_for_context'):
                    stats['context'] += 1
                rows_sent.append((sec_id, oi, x.get('sid'), x.get('speaker'),
                                  1 if x.get('attributed') else 0,
                                  1 if x.get('added_for_context') else 0,
                                  x.get('text') or '', tk, cs, ce, meth,
                                  None))   # reasons stamped in the pass below
            rows_sec.append((sec_id, item, si, s.get('label'), s.get('summary') or ''))
            if not dry_run:
                stats['sections'] += 1

        # Stamp the significance reason AFTER anchoring, from a turn-scoped pass.
        #
        # The correction rule needs the sentences around a retraction, and their order IN THE TURN --
        # which the section listing does not give (sections regroup sentences by topic). char_start is
        # the in-turn position, so sorting by it inside each (turn, speaker) group reproduces the turn.
        # Grouping by speaker too, so one Member retracting cannot sweep away the next Member's
        # sentences in a written answer that carries several speakers.
        rows_sent = stamp_reasons(rows_sent, db)

        if not dry_run:
            n_anch = sum(1 for r in rows_sent if r[7])
            n_amb = sum(1 for r in rows_sent if r[10] == 'ambiguous')
            n_un = sum(1 for r in rows_sent if r[10] == 'unresolved')
            n_ctx = sum(1 for r in rows_sent if r[5])
            db.execute(
                "INSERT INTO summary_item(item_id,title,group_name,tier,year,n_sections,"
                "n_sentences,n_anchored,n_ambiguous,n_unanchored,n_context,"
                "sentences_total,sentences_selected,selection_share) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item, sj.get('title') or item, meta.get('group'), None,
                 int(meta['year']) if str(meta.get('year') or '').isdigit() else None,
                 len(sections), len(rows_sent), n_anch, n_amb, n_un, n_ctx,
                 meta.get('sentences_total'), meta.get('sentences_selected'),
                 meta.get('selection_share')))
            for oi, rid in enumerate(rids):
                db.execute("INSERT OR IGNORE INTO summary_item_report VALUES (?,?,?)",
                           (item, rid, oi))
            for d in dates:
                db.execute("INSERT OR IGNORE INTO summary_item_sitting VALUES (?,?)", (item, d))
            db.executemany(
                "INSERT INTO summary_section(section_id,item_id,ordinal,label,summary) "
                "VALUES (?,?,?,?,?)", rows_sec)
            db.executemany(
                "INSERT INTO summary_sentence(section_id,ord,sid,speaker,attributed,"
                "added_for_context,text,turn_key,char_start,char_end,anchor_method,"
                "hidden_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows_sent)

        stats['items'] += 1
        stats['sentences'] += len(rows_sent)
        if verbose and n % 500 == 0:
            print(f"   {n:,}/{len(files):,}  sentences {stats['sentences']:,}  "
                  f"anchored {stats['anchored']:,}  ({time.time()-t0:.0f}s)")

    if not dry_run:
        db.commit()
    return stats, problems


def verify(db):
    """Every assertion this schema is worth having."""
    print("\n" + "=" * 78)
    print("VERIFICATION")
    print("=" * 78)
    ok = True

    for t in TABLES:
        n = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<22} {n:>9,}")

    # 1. FK integrity
    viol = list(db.execute("PRAGMA foreign_key_check"))
    print(f"\n  foreign_key_check violations: {len(viol)}")
    if viol:
        ok = False
        for v in viol[:5]:
            print(f"     {v}")

    # 2. every anchor resolves to a real turn, and its offsets slice correctly
    print("\n  anchor offsets re-checked against turn.text:")
    checked = bad = 0
    for r in db.execute("""
            SELECT s.text, t.text, s.char_start, s.char_end, t.key
            FROM summary_sentence s JOIN turn t ON t.key = s.turn_key
            WHERE s.turn_key IS NOT NULL LIMIT 2000"""):
        checked += 1
        stext, ttext, cs, ce, key = r
        if cs is None or ce is None or not (0 <= cs < ce <= len(ttext)):
            bad += 1
            continue
        if norm_map(ttext[cs:ce])[0] != norm_map(stext)[0]:
            a = re.sub(r'[\u2013\u2014-]', '-', norm_map(ttext[cs:ce])[0])
            b = re.sub(r'[\u2013\u2014-]', '-', norm_map(stext)[0])
            if a != b:
                bad += 1
                if bad <= 3:
                    print(f"     MISMATCH {key} [{cs}:{ce}] "
                          f"db={ttext[cs:ce][:50]!r} sent={stext[:50]!r}")
    print(f"     checked {checked:,}   offsets that do NOT reconstruct the sentence: {bad}")
    if bad:
        ok = False

    # 3. no sentence both anchored and ambiguous
    n = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE turn_key IS NOT NULL "
                   "AND anchor_method IN ('ambiguous','unresolved')").fetchone()[0]
    print(f"\n  rows anchored but marked unresolved/ambiguous: {n}")
    if n:
        ok = False

    # 4. every sentence carries a method
    n = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE anchor_method IS NULL").fetchone()[0]
    print(f"  rows with no anchor_method: {n}")
    if n:
        ok = False

    # 5. item counters agree with the rows they summarise
    bad_items = list(db.execute("""
        SELECT i.item_id, i.n_anchored, COUNT(s.turn_key)
        FROM summary_item i
        LEFT JOIN summary_section c ON c.item_id = i.item_id
        LEFT JOIN summary_sentence s ON s.section_id = c.section_id
        GROUP BY i.item_id HAVING i.n_anchored <> COUNT(s.turn_key) LIMIT 5"""))
    print(f"  items whose n_anchored disagrees with their rows: {len(bad_items)}")
    if bad_items:
        ok = False
        for b in bad_items:
            print(f"     {b}")

    # 6. no orphan sections / sentences
    for name, sql in (
        ('sections with no item',
         "SELECT COUNT(*) FROM summary_section c LEFT JOIN summary_item i "
         "ON i.item_id=c.item_id WHERE i.item_id IS NULL"),
        ('sentences with no section',
         "SELECT COUNT(*) FROM summary_sentence s LEFT JOIN summary_section c "
         "ON c.section_id=s.section_id WHERE c.section_id IS NULL"),
        ('item_report rows with no report',
         "SELECT COUNT(*) FROM summary_item_report r LEFT JOIN report p "
         "ON p.report_id=r.report_id WHERE p.report_id IS NULL"),
        ('item_sitting rows with no sitting',
         "SELECT COUNT(*) FROM summary_item_sitting s LEFT JOIN sitting g "
         "ON g.date=s.date WHERE g.date IS NULL"),
    ):
        n = db.execute(sql).fetchone()[0]
        print(f"  {name}: {n}")
        if n:
            ok = False

    # 7. anchor coverage, the number that matters
    tot, anch = db.execute(
        "SELECT COUNT(*), SUM(turn_key IS NOT NULL) FROM summary_sentence").fetchone()
    amb, unr = db.execute(
        "SELECT SUM(anchor_method='ambiguous'), SUM(anchor_method='unresolved') "
        "FROM summary_sentence").fetchone()
    print(f"\n  ANCHOR COVERAGE: {anch:,}/{tot:,} = {100*anch/max(1,tot):.2f}%"
          f"   ambiguous {amb}   unresolved {unr}")
    print(f"\n  {'ALL CHECKS PASS' if ok else 'CHECKS FAILED'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--rebuild', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")   # the FK to turn(key) is the point of this schema

    if args.verify:
        sys.exit(0 if verify(db) else 1)

    existing = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    have = [t for t in TABLES if t in existing]

    if have and not args.rebuild and not args.dry_run:
        print(f"tables already present: {have}\nrefusing to load without --rebuild")
        sys.exit(2)

    if args.rebuild and not args.dry_run:
        print("dropping existing summary tables...")
        for t in TABLES:
            db.execute(f"DROP TABLE IF EXISTS {t}")
        db.commit()
        for stmt in DDL.strip().split(';'):
            if stmt.strip():
                db.execute(stmt)
        db.commit()
        print("schema created.\n")

    # FK targets must be unique or the schema is a lie
    for tbl, col in (('turn', 'key'), ('report', 'report_id'), ('sitting', 'date')):
        n, d = db.execute(f"SELECT COUNT(*), COUNT(DISTINCT {col}) FROM {tbl}").fetchone()
        if n != d:
            print(f"ABORT: {tbl}.{col} is not unique ({n} rows, {d} distinct)")
            sys.exit(3)
    print("FK targets verified unique (turn.key, report.report_id, sitting.date)")

    stats, problems = build(db, dry_run=args.dry_run)
    print("\n" + "=" * 78)
    print("LOAD RESULT" + (" (dry run — nothing written)" if args.dry_run else ""))
    print("=" * 78)
    for k, v in stats.items():
        print(f"  {k:<20} {v:>10,}")
    if problems:
        print(f"\n  problems ({len(problems)}):")
        for p in problems[:10]:
            print(f"     {p}")

    if not args.dry_run:
        verify(db)


if __name__ == '__main__':
    main()
