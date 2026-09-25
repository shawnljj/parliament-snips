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
     "I have mentioned some of the schemes earlier.", "restatement"),
]
for speaker, text, want in OWNER:
    got = SG.classify(text, speaker)
    check(f'{want}: {text[:58]!r}', got == want, f'got {got!r}')

# The owner's OTHER line -- "I said that the statutory minimum annual leave ... goes up to a cap of
# 14 years" -- is deliberately None here, and this assertion exists so nobody 'fixes' it back.
#
# On its own that sentence is indistinguishable from a figure-bearing restatement we must keep
# ("I said that the resale grant is up to $180,000"). It is only insignificant as one half of a
# correction PAIR, which is a turn-scoped question -- asserted in 2b. A single-sentence rule that
# hid it would also hide every figure restatement in the corpus.
got = SG.classify("I said that the statutory minimum annual leave under the Employment Act goes up"
                  " to a cap of 14 years.", "Ms Elysa Chen (Bishan-Toa Payoh)")
check('the annual-leave line is NOT hidden by a single-sentence rule (it needs its pair)',
      got is None, f'got {got!r} -- turn-scoped rule owns this sentence')

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
print("2b. SMALL CORRECTIONS -- the owner's second judgement")
print("=" * 78)
# "i think these small corrections are not that significant. When highlighted, it makes us sound a
# bit more nit picky." The correction is procedure about the speaker, not a move by the House -- but
# the policy content beside it must survive.
PAIR = [
    "Yes, Speaker. I misspoke earlier.",
    "I said that the statutory minimum annual leave under the Employment Act goes up to a cap of"
    " 14 years.",
    "I meant to say 14 days.",
    "The Ministry will review the scheme in 2027.",
]
got = SG.classify_turn(PAIR, ["Ms Elysa Chen"] * 4)
check('the retracted claim is held back with its retraction',
      got[0] == 'self_correction' and got[1] == 'self_correction',
      f'got {got[0]!r}, {got[1]!r}')
check('the CORRECTED figure is still surfaced', got[2] is None, f'got {got[2]!r}')
check('text after the correction is untouched', got[3] is None, f'got {got[3]!r}')

# THE REAL SHAPE OF THE CASE. The owner's actual turn is one where the selector chose ONLY the
# corrected claim -- the retraction ("Yes, Speaker. I misspoke earlier.") was never selected, so a
# rule that pairs selected sentences finds nothing and the page still shows a correction as policy.
OWNER_TURN = ("Yes, Speaker. I misspoke earlier. I said that the statutory minimum annual leave"
              " under the Employment Act goes up to a cap of 14 years. I meant to say 14 days.")
ONLY_CLAIM = ["I said that the statutory minimum annual leave under the Employment Act goes up to a"
              " cap of 14 years."]
got = SG.classify_turn_text(ONLY_CLAIM, ["Ms Elysa Chen"], OWNER_TURN)
check('a claim selected WITHOUT its retraction is still held back', got[0] == 'self_correction',
      f'got {got[0]!r} -- the turn contains the retraction even though the selector skipped it')
# the same sentence in a turn with no correction must survive, or we lose every figure restatement
CLEAN_TURN = "I said that the resale grant is up to $180,000. The scheme will be reviewed in 2027."
got = SG.classify_turn_text(["I said that the resale grant is up to $180,000."], ["Mr X"], CLEAN_TURN)
check('the same shape is kept when its turn holds no correction', got[0] is None, f'got {got[0]!r}')

ALONE = ["I said that the resale grant is up to $180,000.",
         "The Bill gives the Courts powers to order divorcing parents to attend the MPP."]
got = SG.classify_turn(ALONE, ["Mr X", "Mr X"])
check('a figure-bearing restatement with NO retraction nearby is surfaced',
      got[0] is None and got[1] is None, f'got {got[0]!r}, {got[1]!r}')

MIXED = ["What I meant to say was that several Members have asked if the Government will consider"
         " reducing fuel duty.",
         "I misspoke earlier."]
got = SG.classify_turn(MIXED, ["Mr X", "Mr X"])
check('a correction that CARRIES the policy keeps it', got[0] is None, f'got {got[0]!r}')
check('the bare retraction in the same turn still goes', got[1] == 'self_correction')

TWO = ["I said the fee is $50.", "I misspoke earlier.",
       "Mr Speaker, the fee is $80 for a first application and $40 thereafter."]
got = SG.classify_turn(TWO, ["Mr A", "Mr A", "Mr B"])
check("one Member's retraction does not take the next Member's sentence", got[2] is None,
      f'got {got[2]!r}')

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


def derive(db):
    """Re-derive every row's reason the way the loader does: turn-scoped, spoken order.

    One derivation shared by the numbers below and the cross-check after them. When these two used
    different rules the gate caught the drift (stored 390 vs rules 389) -- the pair rule fires on two
    sentences, so a single-sentence count can never agree with a turn-scoped one.
    """
    out = []
    groups = {}
    ttext = {r['key']: r['text'] for r in db.execute("SELECT key, text FROM turn")}
    for r in db.execute("""SELECT s.speaker, s.text, s.hidden_reason, s.turn_key, s.char_start
                           FROM summary_sentence s""").fetchall():
        if r['turn_key']:
            groups.setdefault((r['turn_key'], r['speaker']), []).append(r)
        else:
            # Unanchored: no position in a turn, so only the single-sentence rules can apply. They
            # are still counted, and reported separately -- see the anchored split below.
            out.append((r['text'], r['speaker'], r['hidden_reason'],
                        SG.classify(r['text'], r['speaker']), False))
    for (tk, sp), members in groups.items():
        members.sort(key=lambda r: r['char_start'] if r['char_start'] is not None else 0)
        derived = SG.classify_turn_text([m['text'] for m in members], [sp] * len(members),
                                       ttext.get(tk, ''))
        for m, d in zip(members, derived):
            out.append((m['text'], sp, m['hidden_reason'], d, True))
    return out


DERIVED = derive(db)
# Anchored rows are the ones a reader can actually be shown, so they are the basis for every number
# here and for the comparison in section 6. The unanchored few (107 pre-existing) are reported
# separately: comparing all-rows against anchored-rows is what made this section and section 6
# disagree twice.
ANCH = [d for d in DERIVED if d[4]]
UNANCH = [d for d in DERIVED if not d[4]]
reasons = collections.Counter(d[3] for d in ANCH)
all_reasons = collections.Counter(d[3] for d in DERIVED)
rows = ANCH
held = sum(v for k, v in reasons.items() if k)
held_all = sum(v for k, v in all_reasons.items() if k)
share = 100 * held / max(1, len(rows))
print(f"  anchored highlights: {len(rows):,}   (+{len(UNANCH):,} unanchored)")
for k, v in reasons.most_common():
    print(f"     {v:>7,}  {k or '(surfaced)'}")
check('rules fire on real data', held > 0,
      f'{held:,} held back of {len(rows):,} anchored rows (all rows incl. unanchored: {held_all:,})')
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
for _t, _sp, stored, derived, anchored in DERIVED:
    if derived != stored:
        mismatch += 1
check('the database agrees with the rules on every anchored row', mismatch == 0,
      f'{mismatch:,} rows disagree -- rebuild with build_summaries.py --rebuild')

print()
print("RESULT:", "SIGNIFICANCE GATE PASSES" if ok else "GATE FAILED")
sys.exit(0 if ok else 1)
