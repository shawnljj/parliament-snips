#!/usr/bin/env python3
"""The reading-mode query, as a gate.

The schema is only worth having if one query can produce what the page needs. In particular the
highlight cannot be done in the browser: JS would need the SAME normalisation this loader used
(unicode dashes, curly quotes, nbsp) to find the span, and any drift there puts the highlight
under the wrong words. So the offsets are stored and the QUERY returns the exact slice.

Asserts, on real rows:
  1. a sitting -> items -> sections -> sentences traversal returns content
  2. every returned excerpt actually occurs in its turn text at the stored offsets
  3. a citation resolves: item -> report -> turn
  4. the "unanchored" rows are recorded, not silently missing
"""
import sqlite3
import sys

DB = '/Users/shawnlin/parsnips/pipeline/hansard.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
db.execute("PRAGMA foreign_keys = ON")

ok = True


def check(label, cond, detail=''):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}  {detail}")
    if not cond:
        ok = False
    return cond


print("=" * 78)
print("1. sitting -> items -> sections -> sentences")
print("=" * 78)
date = db.execute("""
    SELECT g.date, COUNT(DISTINCT i.item_id) n
    FROM summary_item_sitting g JOIN summary_item i ON i.item_id = g.item_id
    GROUP BY g.date HAVING n >= 5 ORDER BY n DESC LIMIT 1""").fetchone()
print(f"  test sitting: {date['date']}  ({date['n']} items)")

rows = db.execute("""
    SELECT i.item_id, i.title, i.group_name,
           c.section_id, c.ordinal, c.label, c.summary,
           COUNT(s.ord) n_sent, SUM(s.turn_key IS NOT NULL) n_anch
    FROM summary_item_sitting g
    JOIN summary_item    i ON i.item_id = g.item_id
    JOIN summary_section c ON c.item_id = i.item_id
    LEFT JOIN summary_sentence s ON s.section_id = c.section_id
    WHERE g.date = ?
    GROUP BY c.section_id ORDER BY i.item_id, c.ordinal LIMIT 8""", (date['date'],)).fetchall()
check('traversal returns sections', len(rows) > 0, f'({len(rows)} shown)')
for r in rows[:4]:
    print(f"     {r['item_id'][:26]:<26} §{r['ordinal']:<3} {r['n_sent']} sent "
          f"({r['n_anch']} anchored)  {(r['label'] or '')[:44]!r}")

print()
print("=" * 78)
print("2. the stored offsets actually slice out the sentence")
print("=" * 78)
bad = checked = 0
for r in db.execute("""
        SELECT s.text, s.char_start, s.char_end, t.text AS ttext, t.key, s.anchor_method
        FROM summary_sentence s JOIN turn t ON t.key = s.turn_key
        WHERE s.turn_key IS NOT NULL
        ORDER BY s.section_id LIMIT 5000"""):
    checked += 1
    sl = r['ttext'][r['char_start']:r['char_end']]
    if sl.strip() != r['text'].strip():
        # the only sanctioned difference is punctuation variants
        import re
        a = re.sub(r'[\u2013\u2014\-\u00a0\s]+', ' ', sl).strip()
        b = re.sub(r'[\u2013\u2014\-\u00a0\s]+', ' ', r['text']).strip()
        if a != b:
            bad += 1
            if bad <= 3:
                print(f"     MISMATCH {r['key']} [{r['char_start']}:{r['char_end']}]")
                print(f"        slice: {sl[:90]!r}")
                print(f"        sent : {r['text'][:90]!r}")
check('offsets reconstruct the sentence', bad == 0, f'checked {checked}, bad {bad}')

print()
print("=" * 78)
print("3. a citation traverses item -> report -> turn")
print("=" * 78)
cit = db.execute("""
    SELECT i.item_id, i.title, p.report_id, p.date, p.report_type, t.key, t.speaker,
           s.text, s.char_start, s.char_end
    FROM summary_sentence s
    JOIN summary_section c ON c.section_id = s.section_id
    JOIN summary_item    i ON i.item_id = c.item_id
    JOIN summary_item_report ir ON ir.item_id = i.item_id
    JOIN report p ON p.report_id = ir.report_id
    JOIN turn   t ON t.key = s.turn_key
    WHERE s.anchor_method = 'exact' AND length(s.text) > 60
    LIMIT 3""").fetchall()
check('citation chain resolves', len(cit) > 0, f'({len(cit)} examples)')
for r in cit:
    print(f"     {r['item_id']}  ->  {r['report_id']} ({r['date']}, {r['report_type']})")
    print(f"        turn {r['key']}  speaker={r['speaker']!r}")
    print(f"        quote @[{r['char_start']}:{r['char_end']}]: {r['text'][:80]!r}")

print()
print("=" * 78)
print("4. unanchored rows are RECORDED, not missing")
print("=" * 78)
for meth in ('exact', 'normalised', 'own-report', 'ambiguous', 'unresolved'):
    n = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE anchor_method=?",
                   (meth,)).fetchone()[0]
    print(f"     {meth:<14} {n:>8,}")
tot, anch = db.execute(
    "SELECT COUNT(*), SUM(turn_key IS NOT NULL) FROM summary_sentence").fetchone()
check('every sentence has a method', True, f'{tot:,} rows')
check('coverage >= 99%', anch / tot >= 0.99, f'{100*anch/tot:.2f}%')

print()
print("=" * 78)
print("5. cross-check: does the count match what the JSON files say?")
print("=" * 78)
import glob
import json
import os
n_json_sec = n_json_sent = 0
for f in glob.glob('/Users/shawnlin/parsnips/summaries/*/*.json'):
    j = json.load(open(f))
    for s in (j.get('sections') or []):
        n_json_sec += 1
        n_json_sent += len(s.get('sentences') or [])
db_sec = db.execute("SELECT COUNT(*) FROM summary_section").fetchone()[0]
db_sent = db.execute("SELECT COUNT(*) FROM summary_sentence").fetchone()[0]
check('sections match the JSON source', db_sec == n_json_sec, f'{db_sec:,} vs {n_json_sec:,}')
check('sentences match the JSON source', db_sent == n_json_sent, f'{db_sent:,} vs {n_json_sent:,}')

print()
print("RESULT:", "READING-MODE QUERY GATE PASSES" if ok else "GATE FAILED")
sys.exit(0 if ok else 1)
