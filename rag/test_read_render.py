#!/usr/bin/env python3
"""Gate: the reading view must emit EVERY anchored highlight, and must not invent any.

The defect this catches (measured, 2024-02-07): items span sittings, so deriving the turn set from
the sitting date dropped 275 of 882 highlights -- 31% of the page's marks -- while the page looked
complete. A render that is internally consistent can still be silently narrower than the data.

Asserts, per sitting:
  1. marks emitted == distinct (turn_key, char_start, char_end) among the sitting's items
  2. no mark is emitted for text outside the turn it claims
  3. every rendered turn belongs to one of the items' reports
  4. the callout count equals the number of distinct (item, section) whose sentences are in view
  5. the page reports the same coverage it actually renders

Run: python3 test_read_render.py [date ...]
"""
import re
import sqlite3
import sys

sys.path.insert(0, '/Users/shawnlin/parsnips/rag')
sys.path.insert(0, '/Users/shawnlin/parsnips/summariser')
import read_server as RS
import significance as SG

db = RS.connect()
db.execute("PRAGMA foreign_keys = ON")

DATES = sys.argv[1:] or ['2024-02-07', '2016-01-15', '2026-08-05', '2017-03-09']
ok = True


def _derive_hidden(db, date):
    """Per-sentence reasons for one sitting, using the loader's turn-scoped pass.

    Mirrors build_summaries.stamp_reasons: group each (turn, speaker) in spoken order by char_start,
    then ask the rules with the turn's own words (a retraction is usually NOT itself selected, so the
    turn text is what reveals a correction happened).
    """
    sys.path.insert(0, '/Users/shawnlin/parsnips/summariser')
    import significance as SG
    ttext = {r[0]: r[1] for r in db.execute("SELECT key, text FROM turn")}
    rows = db.execute("""SELECT s.speaker, s.text, s.turn_key, s.char_start FROM summary_sentence s
        JOIN summary_section c ON c.section_id = s.section_id
        JOIN summary_item    i ON i.item_id = c.item_id
        JOIN summary_item_sitting g ON g.item_id = i.item_id
        WHERE g.date = ? AND s.turn_key IS NOT NULL""", (date,)).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault(r[2], []).append(r)
    out = []
    for tk, mem in groups.items():
        mem.sort(key=lambda r: r[3] if r[3] is not None else 0)
        sp = [m[0] for m in mem]
        out += SG.classify_turn_text([m[1] for m in mem], sp, ttext.get(tk, ''))
    return out


def check(label, cond, detail=''):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}  {detail}")
    if not cond:
        ok = False


for date in DATES:
    print("=" * 78)
    print(f"SITTING {date}")
    print("=" * 78)

    # expected marks: distinct spans across ALL of the sitting's items
    exp = db.execute("""
        SELECT COUNT(DISTINCT s.turn_key || ':' || s.char_start || ':' || s.char_end)
        FROM summary_sentence s
        JOIN summary_section c ON c.section_id = s.section_id
        JOIN summary_item    i ON i.item_id = c.item_id
        JOIN summary_item_sitting g ON g.item_id = i.item_id
        WHERE g.date = ? AND s.turn_key IS NOT NULL""", (date,)).fetchone()[0]

    html = RS.render_read(db, date)
    if html is None:
        print(f"  no page for {date}, skipping")
        continue

    # A highlight is rendered either live (`hl`) or held back (`hl dim`). Both are MARKS: the
    # question this gate asks is "is every anchored span rendered", so counting only live ones would
    # report a drop every time the significance rules hold something back -- and counting both
    # without checking which is which would let a rule change silently hide half the record.
    got = html.count('<mark class="hl">') + html.count('<mark class="hl dim">')
    turns = RS.full_transcript(db, date)[0]   # (turns, items, reports)
    check('every anchored span is rendered', got == exp, f"emitted {got}, expected {exp}")

    # no mark text may be empty, and no mark may contain a raw '<'
    marks = re.findall(r'<mark class="hl">(.*?)</mark>', html, re.S)
    empties = sum(1 for m in marks if not m.strip())
    check('no empty marks', empties == 0, f'{empties} empty of {len(marks)}')

    # every rendered turn belongs to an item's report OR the sitting's own date, and the set is
    # the UNION -- so no turn on the day may go missing either
    valid_reports = {r[0] for r in db.execute("""
        SELECT DISTINCT ir.report_id FROM summary_item_sitting g
        JOIN summary_item_report ir ON ir.item_id = g.item_id WHERE g.date = ?""", (date,))}
    bad = [t['report_id'] for t in turns
           if t['report_id'] not in valid_reports and t['rdate'] != date]
    check('no stray turns rendered', not bad,
          f'{len(bad)} stray' if bad else f'{len(turns)} turns')

    exp_keys = {r[0] for r in db.execute("""
        SELECT DISTINCT t.key FROM turn t JOIN report p ON p.report_id = t.report_id
        WHERE p.date = ?
           OR p.report_id IN (SELECT DISTINCT ir.report_id FROM summary_item_sitting g
                              JOIN summary_item_report ir ON ir.item_id = g.item_id
                              WHERE g.date = ?)""", (date, date))}
    got_keys = {t['key'] for t in turns}
    check('turn set is the full union (day + items)', got_keys == exp_keys,
          f'rendered {len(got_keys)}, union {len(exp_keys)}, '
          f'missing {len(exp_keys - got_keys)}, extra {len(got_keys - exp_keys)}')

    day_only = {r[0] for r in db.execute("""
        SELECT DISTINCT t.key FROM turn t JOIN report p ON p.report_id = t.report_id
        WHERE p.date = ?""", (date,))}
    check('no turn on the sitting\'s own day is dropped',
          day_only <= got_keys,
          f'{len(day_only - got_keys)} of {len(day_only)} day-turns missing')

    # callouts: ONE per turn that carries a highlight (owner's rule, 2026-09-25). The old rule was
    # one per (item, section) in view, which stacked up to 18 callouts under a single debate turn --
    # each paraphrasing sentences already highlighted above it. The reach this check exists for is
    # unchanged: a callout must exist for every marked turn, so a render that silently drops them
    # still fails.
    exp_calls = db.execute("""
        SELECT COUNT(DISTINCT s.turn_key)
        FROM summary_sentence s
        JOIN summary_section c ON c.section_id = s.section_id
        JOIN summary_item    i ON i.item_id = c.item_id
        JOIN summary_item_sitting g ON g.item_id = i.item_id
        WHERE g.date = ? AND s.turn_key IS NOT NULL""", (date,)).fetchone()[0]
    got_calls = html.count('class="callout"')
    check('one callout per turn carrying highlights', got_calls == exp_calls,
          f'emitted {got_calls}, expected {exp_calls}')

    # and no turn may carry more than one -- the rule the change was made for
    per_turn_calls = [len(re.findall(r'class="callout"', m))
                      for m in re.findall(r'<article class="turn.*?(?=<article class="turn|\\Z)',
                                          html, re.S)]
    check('no turn carries two summaries', max(per_turn_calls, default=0) <= 1,
          f'max {max(per_turn_calls, default=0)} per turn')

    # Exactly the sentences the significance rules name are the ones held back -- no more, no fewer.
    #
    # Derived through the SAME turn-scoped pass the loader uses, not per sentence. Two earlier
    # versions of this check disagreed with the render for instructive reasons: `classify` without
    # the speaker judged phrasing alone (32 named vs 6 rendered), and per-sentence derivation cannot
    # see a correction PAIR (3 named vs 4 rendered). The rules are turn-scoped, so this must be too.
    exp_hidden = [d for d in _derive_hidden(db, date) if d]
    got_hidden = len(re.findall(r'<mark class="hl dim">', html))
    check('held-back highlights are exactly the ones the rules name',
          got_hidden == len(exp_hidden), f'rendered {got_hidden}, rules name {len(exp_hidden)}')

    # the page's own coverage claim must match what it rendered
    m = re.search(r'covering\s*([\d.]+)%\s*of what was said', html)
    if m:
        claimed = float(m.group(1))
        chars = sum(len(r['text']) for t in turns for r in t['runs'])
        mchars = sum(len(r['text']) for t in turns for r in t['runs'] if r['kind'] == 'marked')
        actual = 100 * mchars / max(1, chars)
        check('coverage claim matches the render', abs(claimed - actual) < 0.15,
              f'claimed {claimed}%, actual {actual:.2f}%')
    else:
        check('page states its coverage', False, 'no coverage figure found')

    # the spans really are inside their turn text
    bad_span = 0
    for t in turns:
        for r in t['runs']:
            if r['kind'] == 'marked' and not r['text'].strip():
                bad_span += 1
    check('no blank spans', bad_span == 0, str(bad_span))

print()
print("=" * 78)
print("MULTI-SITTING ITEMS: the class of bug this gate exists for")
print("=" * 78)
n = db.execute("""
    SELECT COUNT(*) FROM (
      SELECT i.item_id FROM summary_item i
      JOIN summary_item_sitting g ON g.item_id = i.item_id
      GROUP BY i.item_id HAVING COUNT(DISTINCT g.date) > 1)""").fetchone()[0]
print(f"  items spanning more than one sitting: {n:,} of "
      f"{db.execute('SELECT COUNT(*) FROM summary_item').fetchone()[0]:,}")

# the probe must ask whether ANY checked date's items actually span >1 report date, which is the
# property that made the original defect possible.
exercised = []
for d in DATES:
    spans = db.execute("""
        SELECT COUNT(*) FROM (
          SELECT g.item_id FROM summary_item_sitting g
          JOIN summary_item_report ir ON ir.item_id = g.item_id
          JOIN report p ON p.report_id = ir.report_id
          WHERE g.item_id IN (SELECT item_id FROM summary_item_sitting WHERE date = ?)
          GROUP BY g.item_id HAVING COUNT(DISTINCT p.date) > 1)""", (d,)).fetchone()[0]
    exercised.append((d, spans))
    print(f"    {d}: {spans} of its items span more than one report date")
hit = [d for d, s in exercised if s > 0]
check('gate exercised a multi-sitting item', len(hit) > 0,
      f"exercised on {hit} ({sum(s for _d, s in exercised)} items) — "
      f"the rest have no such item, which is a property of those sittings, not a gate gap")

print()
print("RESULT:", "READ RENDER GATE PASSES" if ok else "GATE FAILED")
sys.exit(0 if ok else 1)
