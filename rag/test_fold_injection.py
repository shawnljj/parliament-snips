#!/usr/bin/env python3
"""Injection test: every fold-gate check must FAIL on a page that has the defect it claims to catch.

A gate that has never failed is not evidence. `test_read_fold.py` asserts six things about the fold;
this mutates a real page six ways and proves each mutation is caught -- and, just as importantly,
that each mutation is caught by the check that claims it. A no-op mutation scores identically to a
missed defect, so every probe asserts its own mutation applied BEFORE the gate is run.

The mutations, and the defect each stands for:

  M1  delete a turn's article from the page          -> the record is not whole (turn lost)
  M2  change one "[+42 sentences]" label to [+900]   -> a label that lies about what is inside
  M3  leave a toggle open with no text in it         -> an empty toggle (a dead tap)
  M4  change the fold note's sentence count          -> the page's claim about itself is false
  M5  strip every toggle, leaving the text loose     -> the fold is gone (nothing collapsed)
  M6  drop the coverage sentence                     -> the page stops stating its coverage
  M7  clone a turn's callout twice                   -> two summaries over one turn's highlights
  M8  unhide a held-back highlight (mark dim -> mark) -> procedure reads as policy on the page

The gate's own labels are asserted by string, so each expect_fail must match a check() label in
`test_read_fold.py` exactly -- a typo here would silently probe nothing.

Run: python3 test_fold_injection.py
"""
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import read_server as RS                                    # noqa: E402
import test_read_fold as TG                                 # noqa: E402

DATE = '2026-08-05'
db = sqlite3.connect(os.path.join(HERE, '..', 'pipeline', 'hansard.db'))
db.row_factory = sqlite3.Row

base = RS.render_read(db, DATE)
turns = RS.full_transcript(db, DATE)[0]
for t in turns:
    row = db.execute("SELECT text FROM turn WHERE key=?", (t['key'],)).fetchone()
    t['_turn_text'] = (row['text'] if row else '') or ''

def run(page):
    return {label: passed for label, passed, _d in TG.checks_for(DATE, page, turns)}

clean = run(base)
print("baseline:")
for k, v in clean.items():
    print(f"  {'ok ' if v else 'BAD'} {k}")
assert all(clean.values()), "the gate does not pass on the unmutated page -- fix that first"

probes = []

# ---- M1: drop a whole turn (one with highlights, so it is not merely a stray procedural line)
target = next(t for t in reversed(turns) if t['n_marks'] > 0)
turn_pat = (r'<article class="turn[^>]*id="turn-' + re.escape(target['key'])
            + r'">.*?</article>')
m1 = re.sub(turn_pat, '', base, count=1, flags=re.S)
probes.append(("M1 a turn is dropped from the page", m1, 'every turn is emitted (collapsed or open)',
               '<article class="turn' in base and m1.count('<article class="turn')
               == base.count('<article class="turn') - 1))

# ---- M2: a label that lies
m = re.search(r'<summary>\[\+(\d+) sentences?\]</summary>', base)
assert m is not None, "no [+N sentences] toggle found -- nothing to mutate"
want_n = int(m.group(1))
m2 = base.replace(m.group(0), f'<summary>[+{want_n + 900} sentences]</summary>', 1)
probes.append(("M2 a [+N sentences] label overstates by 900", m2,
               'every "[+N sentences]" label equals what is inside it',
               m2 != base))

# ---- M3: an empty toggle
m3 = base.replace('<details class="gap"><summary>[+1 sentence]</summary><span></span></details>',
                  'x', 1)
if m3 == base:
    m3 = re.sub(r'(<details class="gap"><summary>\[\+\d+ sentence\]</summary><span>)(.*?)(</span></details>)',
                r'\1\3', base, count=1, flags=re.S)
probes.append(("M3 a toggle holds no text", m3, 'no empty toggle', m3 != base))

# ---- M4: the page's own claim is wrong
m4 = re.sub(r'(<b>)([\d,]+)(</b> sentences</b>)', r'\g<1>1\g<3>', base, count=1)
if m4 == base:
    m4 = re.sub(r'(<b>[\d,]+ sentences</b> in )([\d,]+)', r'\g<1>99999', base, count=1)
probes.append(("M4 the fold note's counts are false", m4,
               'fold claim matches the toggles actually emitted', m4 != base))

# ---- M5: the fold is gone -- text loose instead of collapsed
m5 = re.sub(r'<details class="gap"><summary>\[\+\d+ sentences?\]</summary><span>(.*?)</span></details>',
            r'\1', base, flags=re.S)
probes.append(("M5 every toggle stripped, text left loose", m5,
               'collapsible stretches rendered == collapsible runs built', m5 != base))

# ---- M6: the coverage sentence is removed
m6 = re.sub(r'covering\s*[\d.]+%\s*of what was said', 'summarised', base, count=1)
probes.append(("M6 the coverage claim is dropped", m6,
               'page still states its highlight coverage', m6 != base))

# ---- M7: two summaries over the same turn's highlights
m = re.search(r'class="callout">.*?</aside>', base, re.S)
assert m is not None, "no callout to clone"
m7 = base.replace(m.group(0), m.group(0) + m.group(0), 1)
probes.append(("M7 a turn carries two summary callouts", m7,
               'at most one summary callout per turn', m7 != base))

# ---- M8: a held-back highlight is shown as if it were policy
# This is the owner's complaint, as a mutation: the page claims to hold procedure back and then shows
# it. Catches a render that ignores hidden_reason while the note above still counts it.
m = re.search(r'<mark class="hl dim">', base)
if m is not None:
    m8 = base.replace('<mark class="hl dim">', '<mark class="hl">', 1)
else:
    m8 = base
probes.append(("M8 a held-back highlight is unhidden", m8,
               'held-back highlights are exactly the ones the rules name', m8 != base))

# ---- M9: the header's notes are dropped instead of folded --------------------------------
# The whole point of the disclosure is that nothing is lost, so the mutation REMOVES the body and
# leaves the summary: the page then still looks like it has an "About this sitting", and the
# coverage claim it exists to carry is gone. Asserting only "a disclosure exists" would pass.
m9 = re.sub(r'<details class="about">.*?</details>',
            '<details class="about"><summary>About this sitting</summary></details>',
            base, count=1, flags=re.S)
probes.append(("M9 the folded notes are deleted, disclosure left empty", m9,
               'folding the notes did not lose them', m9 != base))

# ---- M10: the disclosure is left open, so the notes are back in front of the record ------
m10 = base.replace('<details class="about">', '<details class="about" open>', 1)
probes.append(("M10 the notes disclosure is left open", m10,
               "the sitting's notes are folded, not left open", m10 != base))

# ---- M11: the ask box is pulled out of the disclosure and left loose in the header -------
m = re.search(r'<form class="ask.*?</form>', base, re.S)
assert m is not None, "no ask form to move"
m11 = base.replace(m.group(0), '', 1) + m.group(0)
probes.append(("M11 the ask box is left outside the disclosure", m11,
               'the ask box travels with the notes', m11 != base))

# ---- M12: the count chips come back out of the disclosure -------------------------------
# The chips are the header's second rendering of the notes' numbers. Moving them back out is what
# the owner asked to stop, so the mutation is the revert, not a corruption.
m = re.search(r'<div class="stat">.*?</div>', base, re.S)
assert m is not None, "no chip row to move"
m12 = base.replace(m.group(0), '', 1).replace('</details>', '</details>' + m.group(0), 1)
probes.append(("M12 the count chips are left outside the disclosure", m12,
               'the count chips are folded with the notes', m12 != base))

# ---- M13: a chip contradicts the page it summarises --------------------------------------
m13 = re.sub(r'(<span>\d+ topics</span>)', '<span>9,999 topics</span>', base, count=1)
probes.append(("M13 the chips disagree with the page", m13,
               'the chips agree with the page they summarise', m13 != base))

ok = True
print()
for name, page, expect_fail, mutation_applied in probes:
    res = run(page)
    caught = res.get(expect_fail) is False
    others = [k for k, v in res.items() if not v and k != expect_fail]
    # A real defect may violate more than one invariant at once -- that is the page being wrong in
    # several ways, not a gate problem. What must hold: the mutation applied, and the check that
    # claims this defect is among those that tripped.
    status = 'PASS' if (mutation_applied and caught) else 'FAIL'
    if status == 'FAIL':
        ok = False
    print(f"  [{status}] {name}")
    print(f"         mutation applied: {mutation_applied}")
    print(f"         caught by '{expect_fail}': {caught}")
    if others:
        print(f"         also tripped (expected for a real defect): {others}")

print()
print(f"  probes: {len(probes)}  all mutations applied and caught: {ok}")
print("RESULT:", "INJECTION TEST PASSES" if ok else "INJECTION TEST FAILS")
sys.exit(0 if ok else 1)
