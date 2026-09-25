#!/usr/bin/env python3
"""Injection tests for the summary schema.

An FK that is declared but not ENFORCED is decoration: SQLite ignores foreign keys unless
`PRAGMA foreign_keys = ON` is set on the connection, and a reader that opens the DB without it
can insert a dangling anchor that every count-based check will report as fine.

Each probe must (a) attempt a genuinely invalid write and (b) confirm the mutation was actually
attempted, so a no-op cannot score as a caught defect. Exit non-zero if any probe passes when it
should fail.

Run:  python3 test_summary_fk.py
"""
import sqlite3
import sys

DB = '/Users/shawnlin/parsnips/pipeline/hansard.db'
probes = []


def probe(name, sql, args=(), want_error=True, note=''):
    probes.append((name, sql, args, want_error, note))


# --- dangling anchors -------------------------------------------------------
probe('sentence -> nonexistent turn.key',
      "INSERT INTO summary_sentence(section_id,ord,sid,text,turn_key,anchor_method) "
      "SELECT section_id, 9999, 'sX', 'x', 'no-such-turn#t0', 'exact' "
      "FROM summary_section LIMIT 1")
probe('section -> nonexistent item',
      "INSERT INTO summary_section(section_id,item_id,ordinal,summary) "
      "VALUES (99999999,'no-such-item',0,'x')")
probe('item_report -> nonexistent report',
      "INSERT INTO summary_item_report(item_id,report_id,ordinal) "
      "SELECT item_id,'no-such-report',0 FROM summary_item LIMIT 1")
probe('item_sitting -> nonexistent sitting date',
      "INSERT INTO summary_item_sitting(item_id,date) "
      "SELECT item_id,'1900-01-01' FROM summary_item LIMIT 1")
probe('sentence -> nonexistent section',
      "INSERT INTO summary_sentence(section_id,ord,sid,text,anchor_method) "
      "VALUES (99999998,0,'sY','y','exact')")

# --- duplicate keys ---------------------------------------------------------
probe('duplicate summary_item PK',
      "INSERT INTO summary_item(item_id,title) SELECT item_id,title FROM summary_item LIMIT 1")
probe('duplicate (item_id, ordinal) section',
      "INSERT INTO summary_section(section_id,item_id,ordinal,summary) "
      "SELECT 99999997,item_id,ordinal,'x' FROM summary_section LIMIT 1")
probe('duplicate (section_id, ord) sentence',
      "INSERT INTO summary_sentence(section_id,ord,sid,text,anchor_method) "
      "SELECT section_id,ord,'sZ','z','exact' FROM summary_sentence LIMIT 1")

# --- ON DELETE CASCADE must actually cascade --------------------------------
probe('CASCADE: deleting an item leaves orphan sections',
      "DELETE FROM summary_item WHERE item_id IN "
      "(SELECT item_id FROM summary_item LIMIT 1)", want_error=False)

# --- a VALID write must succeed (proves the probe harness can succeed) ------
probe('VALID: sentence with a real turn.key must insert',
      "INSERT INTO summary_sentence(section_id,ord,sid,text,turn_key,anchor_method) "
      "SELECT s.section_id, 9998, 'sOK', 'x', t.key, 'exact' "
      "FROM summary_section s, (SELECT key FROM turn LIMIT 1) t LIMIT 1",
      want_error=False, note='positive control')

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
db.execute("PRAGMA foreign_keys = ON")

print("=" * 78)
print("FK ENFORCEMENT — is the constraint real?")
print("=" * 78)
fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
print(f"  PRAGMA foreign_keys on this connection: {fk}")
if not fk:
    print("  ABORT: foreign keys are NOT enforced on this connection")
    sys.exit(1)

before = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
          for t in ('summary_item', 'summary_section', 'summary_sentence')}
print("  counts before:", before)

passed = failed = 0
for name, sql, args, want_error, note in probes:
    db.execute("SAVEPOINT p")
    try:
        cur = db.execute(sql, args)
        changed = cur.rowcount
        err = None
    except sqlite3.Error as e:
        changed = 0
        err = e
    db.execute("ROLLBACK TO p")
    db.execute("RELEASE p")

    if want_error:
        ok = err is not None
        verdict = 'REJECTED' if ok else 'ACCEPTED (BAD)'
    else:
        # mutation must have actually touched something to count as exercised
        ok = err is None and (changed > 0 or 'CASCADE' in name)
        verdict = f'applied (rowcount={changed})' if ok else f'DID NOT APPLY {err}'
    tag = 'PASS' if ok else 'FAIL'
    print(f"  [{tag}] {name:<52} {verdict}")
    if err is not None and want_error:
        print(f"           -> {type(err).__name__}: {err}")
    if note:
        print(f"           ({note})")
    passed += ok
    failed += (not ok)

print()
print(f"  probes: {len(probes)}   pass {passed}   fail {failed}")

# --- did the VALID probe actually apply? -----------------------------------
db.execute("SAVEPOINT c")
db.execute("INSERT INTO summary_sentence(section_id,ord,sid,text,turn_key,anchor_method) "
           "SELECT s.section_id, 9998, 'sOK', 'x', t.key, 'exact' "
           "FROM summary_section s, (SELECT key FROM turn LIMIT 1) t LIMIT 1")
n_ok = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE ord=9998").fetchone()[0]
db.execute("ROLLBACK TO c")
db.execute("RELEASE c")
print(f"  positive control really inserted a row: {bool(n_ok)}")

# --- cascade check, done properly ------------------------------------------
db.execute("SAVEPOINT k")
item = db.execute("SELECT item_id FROM summary_item LIMIT 1").fetchone()[0]
c0 = db.execute("SELECT COUNT(*) FROM summary_section WHERE item_id=?", (item,)).fetchone()[0]
s0 = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE section_id IN "
                "(SELECT section_id FROM summary_section WHERE item_id=?)", (item,)).fetchone()[0]
db.execute("DELETE FROM summary_item WHERE item_id=?", (item,))
c1 = db.execute("SELECT COUNT(*) FROM summary_section WHERE item_id=?", (item,)).fetchone()[0]
s1 = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE section_id IN "
                "(SELECT section_id FROM summary_section WHERE item_id=?)", (item,)).fetchone()[0]
db.execute("ROLLBACK TO k")
db.execute("RELEASE k")
print(f"  CASCADE: deleting item '{item}' removed its {c0} sections and {s0} sentences "
      f"-> now {c1} / {s1}")
cascade_ok = (c0 > 0 and c1 == 0 and s1 == 0)
print(f"  cascade effective: {cascade_ok}")

# --- state unchanged after all probes --------------------------------------
after = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
         for t in ('summary_item', 'summary_section', 'summary_sentence')}
print(f"  counts after (must equal before): {after}   unchanged: {after == before}")

allok = (failed == 0 and bool(n_ok) and cascade_ok and after == before)
print()
print("RESULT:", "ALL FK PROBES PASS" if allok else "FK PROBES FAILED")
sys.exit(0 if allok else 1)
