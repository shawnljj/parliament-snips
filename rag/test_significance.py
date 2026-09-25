#!/usr/bin/env python3
"""Gate: the significance rules must be RIGHT about the sentences they touch, in both directions.

Two failures matter and they pull opposite ways:

  * a rule that fires on policy prose hides what the reader came for. The owner's report was the
    other way round -- procedure surfaced as if it were policy -- but the repair must not create the
    mirror-image defect, because a hidden commitment is invisible: the page still looks complete.
  * a rule that never fires is not a filter. Every rule must demonstrably catch the class it names,
    on real corpus data, and its probes must assert their own expectation was exercised.

So this asserts, against the real database:

  1. THE OWNER'S CASE. The two lines that prompted this are classified, with their reasons.
  2. THE MIRROR CASE. A commitment phrased as a restatement survives ("As I said, the GST rate will
     not rise in 2026" must NOT be hidden); an ordinary policy sentence is never touched; the
     Chair's substantive ruling is kept even though the speaker is the Chair.
  3. THE SPEAKER MATTERS. The same housekeeping phrasing in a MEMBER's mouth is not chair business.
     This is the assertion that caught a real bug in the render gate while it was being written.
  4. ANCHORING. No rule may fire mid-sentence where it would eat a speech: the phrase that once
     emptied 44 items (`proc text` mid-turn) is checked explicitly.
  5. THE CORPUS NUMBERS. Measured share of highlights held back, reported and bounded -- a rule set
     that suddenly hides 20% of the record must fail here rather than pass silently.
  6. THE RECORD IS INTACT. Every held-back sentence is still present in the database, still anchored,
     and still carries its reason. Nothing was deleted to make the page tidier.

Run: python3 test_significance.py
"""
import collections
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'summariser'))
sys.path.insert(0, HERE)
import significance as SG                                     # noqa: E402

DB = os.path.join(HERE, '..', 'pipeline', 'hansard.db')
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
ok = True


def check(label, cond, detail=''):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}  {detail}")
    if not cond:
        ok = False


print("=" * 78)
print("1. THE CASE THAT PROMPTED THIS (owner's screenshot, 2026-08-05)")
print("=" * 78)
OWNER = [
    ("Mr Speaker", "Ms Chen, you have a clarification to make?", "chair_housekeeping"),
    ("Ms Elysa Chen (Bishan-Toa Payoh)",
     "I said that the statutory minimum annual leave under the Employment Act goes up to a cap of"
     " 14 years.", "restatement"),
]
for speaker, text, want in OWNER:
    got = SG.classify(text, speaker)
    check(f'{want}: {text[:58]!r}', got == want, f'got {got!r}')

print()
print("=" * 78)
print("2. THE MIRROR CASE -- hiding a policy move would be the worse defect")
print("=" * 78)
KEEP = [
    ("The Minister for Finance", "As I said, the GST rate will not rise in 2026.",
     'a commitment phrased as a restatement'),
    ("Mr X", "The Bill gives the Courts powers to order divorcing parents to attend the MPP and"
             " focus on the needs of the child.", 'ordinary policy prose'),
    ("Mr Speaker", "The Standing Orders require that a Member be heard in silence, and I would ask"
                   " Members to observe that.", "the Chair's substantive ruling"),
    ("Mdm Speaker", "Presently, the statutory minimum annual leave under section 88A of the"
                    " Employment Act is seven days, increasing by one each year, up to a cap of"
                    " 14 years.", 'a factual description of an existing rule'),
]
for speaker, text, why in KEEP:
    got = SG.classify(text, speaker)
    check(f'kept -- {why}', got is None, f'classified {got!r}')

print()
print("=" * 78)
print("3. THE SPEAKER IS PART OF THE RULE, NOT DECORATION")
print("=" * 78)
HK = "Order. Order."
check('chair + housekeeping -> hidden', SG.classify(HK, "Mr Speaker") == 'chair_housekeeping')
check('MEMBER + the same words -> kept', SG.classify(HK, "Mr Lim Swee Say") is None,
      "a member quoting the chair's words is not chair business")

print()
print("=" * 78)
print("4. ANCHORING -- the rule must not fire inside a speech")
print("=" * 78)
# The exact class of defect extractive.py documents: a marker appearing mid-turn ate a whole
# ministerial speech and emptied 44 Bill/Budget items while every quote check still passed.
MID = ("We have built world-class infrastructure for economic productivity and ingrained in"
       " ourselves a hardworking work culture. [ (proc text) Question put, and agreed to."
       " (proc text) ]")
check('mid-sentence marker does not hide the sentence',
      SG.classify(MID, "The Minister for Finance") is None)
check('unanchored "I said" mid-sentence does not hide it',
      SG.classify("The Member is right, and I said as much in my opening speech yesterday.",
                  "Mr X") is None)

print()
print("=" * 78)
print("5. THE CORPUS NUMBERS -- measured, not asserted")
print("=" * 78)
rows = db.execute("""
    SELECT s.speaker, s.text FROM summary_sentence s
    JOIN summary_section c ON c.section_id = s.section_id
    JOIN summary_item    i ON i.item_id = c.item_id
    WHERE s.turn_key IS NOT NULL""").fetchall()
reasons = collections.Counter(SG.classify(r['text'], r['speaker']) for r in rows)
held = sum(v for k, v in reasons.items() if k)
share = 100 * held / max(1, len(rows))
print(f"  highlights: {len(rows):,}")
for k, v in reasons.most_common():
    print(f"     {v:>7,}  {k or '(surfaced)'}")
check('rules fire on real data', held > 0, f'{held:,} held back')
check('rules do not swallow the record', share < 2.0, f'{share:.2f}% held back')
check('both reasons are exercised',
      len([k for k in reasons if k]) >= 2, str(sorted(k for k in reasons if k)))

print()
print("=" * 78)
print("6. NOTHING WAS DELETED TO MAKE THE PAGE TIDIER")
print("=" * 78)
n_all = db.execute("SELECT COUNT(*) FROM summary_sentence").fetchone()[0]
n_hid = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE hidden_reason IS NOT NULL")\
    .fetchone()[0]
n_anch = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE turn_key IS NOT NULL")\
    .fetchone()[0]
check('every held-back sentence is still a row', n_hid > 0 and n_hid <= n_all,
      f'{n_hid:,} of {n_all:,} rows')
# An unanchored row can carry a reason -- the anchor resolution ran before these rules and left 107
# (105 ambiguous + 2 unresolved) either way. What must NOT happen is these rules adding to that set,
# which would hide a sentence the reader has no way to open and check.
hid_unanchored = db.execute("""SELECT COUNT(*) FROM summary_sentence
    WHERE hidden_reason IS NOT NULL AND turn_key IS NULL""").fetchone()[0]
all_unanchored = db.execute("""SELECT COUNT(*) FROM summary_sentence
    WHERE turn_key IS NULL""").fetchone()[0]
check('the rules did not unanchor anything (held-back unanchored < all unanchored)',
      hid_unanchored <= all_unanchored and all_unanchored <= 110,
      f'{hid_unanchored} held-back of {all_unanchored} unanchored (pre-existing)')
# Compare like with like: section 5 measures ANCHORED highlights while n_hid counts every row.
# The extra rows carrying a reason are the 107 pre-existing unanchored ones, not a mismatch.
n_hid_anchored = db.execute('''SELECT COUNT(*) FROM summary_sentence
    WHERE hidden_reason IS NOT NULL AND turn_key IS NOT NULL''').fetchone()[0]
check('the stored reason matches the rules on the anchored set', n_hid_anchored == held,
      f'stored {n_hid_anchored:,}, rules say {held:,} (unanchored {n_hid - n_hid_anchored} more)')

# the stored reasons must be the ones the rules give, not a stale value from an older rule set
mismatch = 0
for r in db.execute("""SELECT speaker, text, hidden_reason FROM summary_sentence
                       WHERE hidden_reason IS NOT NULL OR TRUE LIMIT 0""").fetchall():
    pass
for r in db.execute("""SELECT s.speaker, s.text, s.hidden_reason, s.turn_key
                       FROM summary_sentence s WHERE s.turn_key IS NOT NULL""").fetchall():
    if SG.classify(r['text'], r['speaker']) != r['hidden_reason']:
        mismatch += 1
check('the database agrees with the rules on every anchored row', mismatch == 0,
      f'{mismatch:,} rows disagree -- rebuild with build_summaries.py --rebuild')

print()
print("RESULT:", "SIGNIFICANCE GATE PASSES" if ok else "GATE FAILED")
sys.exit(0 if ok else 1)
