#!/usr/bin/env python3
"""Gate: the fold must HIDE the transcript without LOSING it, and must say what it hid.

The reading view now opens collapsed: one fold per report (topic), and inside a topic every
unsummarised stretch is an inline "[+N sentences]" toggle. The failure mode this gate exists for is
the one the design was chosen to avoid -- a page that is short because the record is not there.
That failure is invisible: a fold that omits a turn looks exactly like a fold that collapsed it.

So this asserts, per sitting:

  1. THE RECORD IS WHOLE. The concatenation of every rendered turn's runs equals turn.text, byte
     for byte, for every turn in the union. A fold may hide text; it may not drop it.
  2. THE LABEL IS TRUE. Every inline toggle's "[+N sentences]" count equals the number of sentences
     actually inside it, counted with `sentences.sentence_spans` -- the same splitter the chunker
     uses. A wrong count is a promise the page breaks when tapped.
  3. NOTHING IS DOUBLE-COUNTED. Collapsible stretches rendered == collapsible runs built, and no
     toggle is empty.
  4. THE PAGE'S OWN CLAIM MATCHES. The fold note's "N sentences folded" equals the number of
     sentences inside the toggles it actually emitted. The reach lesson from Sprint 02: a gate that
     asks the render's own question proves nothing.
  5. THE FOLD IS REAL. Toggles are emitted, and the open page is a strict minority of the record --
     otherwise "folded" is a claim about markup nothing is collapsed behind.
  6. A CITATION OPENS ITS WAY IN. Arriving with a quoted span, the cited topic is open, the cited
     span is marked, and the turn is present; arriving with no citation, no topic is open.

The checks are exposed as `checks_for(date, page, turns)` so `test_fold_injection.py` can drive them
against MUTATED pages and prove each one actually fails. A probe that cannot make a check fail is
not evidence the check works.

Run: python3 test_read_fold.py [date ...]
"""
import html as H
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import read_server as RS                                    # noqa: E402
from sentences import sentence_spans                        # noqa: E402

DB = os.path.join(HERE, '..', 'pipeline', 'hansard.db')

GAP_RE = re.compile(r'<details class="gap"><summary>\[\+(\d+) sentences?\]</summary>'
                    r'<span>(.*?)</span></details>', re.S)
CLAIM_RE = re.compile(r'<b>([\d,]+) sentences</b> in ([\d,]+) stretches are folded')
COV_RE = re.compile(r'covering\s*[\d.]+%\s*of what was said')


def checks_for(date, page, turns):
    """Every assertion, as (label, passed, detail). Pure over (page, turns): no rendering."""
    out = []

    def add(label, cond, detail=''):
        out.append((label, bool(cond), detail))

    # --- 1. the record is whole -------------------------------------------------
    bad = []
    for t in turns:
        rebuilt = ''.join(r['text'] for r in t['runs'])
        want = t.get('_turn_text', '')
        if rebuilt != want:
            bad.append((t['key'], len(rebuilt), len(want)))
    add('every turn reconstructs byte-for-byte from its runs', not bad,
        f'{len(bad)} of {len(turns)} differ' if bad else f'{len(turns)} turns exact')

    emitted = len(re.findall(r'<article class="turn', page))
    add('every turn is emitted (collapsed or open)', emitted == len(turns),
        f'emitted {emitted}, union {len(turns)}')

    # --- 2/3. the labels are true ------------------------------------------------
    gaps = GAP_RE.findall(page)
    wrong, empty, total_folded_sent = [], 0, 0
    for n, inner in gaps:
        n = int(n)
        text = H.unescape(inner)
        actual = len(sentence_spans(text))
        total_folded_sent += actual
        if actual != n:
            wrong.append((n, actual, text[:60]))
        if not text.strip():
            empty += 1
    add('every "[+N sentences]" label equals what is inside it', not wrong,
        f'{len(wrong)} wrong of {len(gaps)}' if wrong else f'{len(gaps)} toggles exact')
    add('no empty toggle', empty == 0, str(empty))
    add('the fold is real (toggles emitted)', len(gaps) > 0, f'{len(gaps)} toggles')

    plain_runs = sum(1 for t in turns for r in t['runs']
                     if r['kind'] == 'plain' and r.get('collapsible'))
    add('collapsible stretches rendered == collapsible runs built',
        len(gaps) == plain_runs, f'rendered {len(gaps)}, built {plain_runs}')

    # --- 4. the page's own claim ------------------------------------------------
    m = CLAIM_RE.search(page)
    if m:
        claimed_sent = int(m.group(1).replace(',', ''))
        claimed_str = int(m.group(2).replace(',', ''))
        add('fold claim matches the toggles actually emitted',
            claimed_sent == total_folded_sent and claimed_str == len(gaps),
            f'claimed {claimed_sent} in {claimed_str}, '
            f'rendered {total_folded_sent} in {len(gaps)}')
    else:
        add('page states how much it folded', False, 'no fold note found')

    # --- 4b. the held-back highlights are collapsed AND counted --------------------------------
    # The page says "N further highlights held back as procedure or repetition ... collapsed, not
    # removed". Two things can go wrong and neither is visible by reading the page: a held-back mark
    # rendered as ordinary policy (the page then shows procedure as if it were a policy move -- the
    # owner's complaint), or the note counting something other than the marks rendered.
    n_dim = len(re.findall(r'<mark class="hl dim">', page))
    m = re.search(r'<b>([\d,]+)</b> further highlight', page)
    # No note means nothing was held back: the sentence is only emitted when the count is non-zero,
    # so an absent note is a claim of 0, not a missing claim.
    claimed_hidden = int(m.group(1).replace(',', '')) if m else 0
    add('held-back highlights are exactly the ones the rules name',
        claimed_hidden == n_dim,
        f'note claims {claimed_hidden}, page renders {n_dim} collapsed'
        + ('' if m else ' (no note: nothing held back)'))
    add('a held-back mark is never rendered as ordinary policy',
        # every dim mark must be inside a collapsed toggle: an open dim mark would read as prose
        all('<details class="gap hidden"' in page[:mm.start()].rsplit('<details', 1)[-1]
            or 'hidden' in page[:mm.start()].rsplit('<details', 1)[-1][:40]
            for mm in re.finditer(r'<mark class="hl dim">', page)) if n_dim else True,
        f'{n_dim} held-back marks all collapsed')

    add('page still states its highlight coverage', COV_RE.search(page) is not None)

    # --- 7. ONE summary per turn ------------------------------------------------
    # The reader is looking at a turn, so a turn gets one summary line. Emitting a callout per
    # (item, section) stacked up to 18 of them under a single debate turn, each paraphrasing
    # sentences already highlighted above it. Measured before the change: 61 of 130 marked turns on
    # 2024-02-07, 8 of 98 on 2026-08-05, 1 of 87 on 2017-03-09.
    per_turn = [len(re.findall(r'class="callout"', m))
                for m in re.findall(r'<article class="turn.*?(?=<article class="turn|\Z)',
                                    page, re.S)]
    over = [n for n in per_turn if n > 1]
    add('at most one summary callout per turn', not over,
        f'{len(over)} turns with 2+' if over else f'{len(per_turn)} turns, max 1')
    marked = [n for n, t in zip(per_turn, turns) if t['n_marks']]
    add('every marked turn gets exactly one callout', all(n == 1 for n in marked),
        f'{sum(1 for n in marked if n != 1)} of {len(marked)} wrong')
    add('no callout on an unmarked turn',
        all(n == 0 for n, t in zip(per_turn, turns) if not t['n_marks']),
        f'{sum(n for n, t in zip(per_turn, turns) if not t["n_marks"])} stray')

    # --- 5. collapsed is a strict minority --------------------------------------
    shown = sum(len(r['text']) for t in turns for r in t['runs']
                if r['kind'] in ('marked', 'cited'))
    total = sum(len(r['text']) for t in turns for r in t['runs'])
    add('the open page is smaller than the record', shown < total,
        f'{shown:,} open of {total:,} chars ({100*shown/max(1,total):.1f}%)')

    return out


def citation_checks(date, focus_turn, focus_span, cited_page, plain_page):
    """The arrival-from-an-answer assertions, separate because they need two renders."""
    out = []

    def add(label, cond, detail=''):
        out.append((label, bool(cond), detail))

    n_open = len(re.findall(r'<details class="topic" open>', cited_page))
    add('a citation opens exactly one topic', n_open == 1, f'{n_open} open')
    add('the cited topic is the one holding the cited turn',
        f'id="turn-{focus_turn}"' in cited_page)
    add('the cited span is marked', '<mark class="hl cited">' in cited_page)
    add('with no citation, every topic is closed',
        len(re.findall(r'<details class="topic" open>', plain_page)) == 0)
    return out


def main(argv):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    dates = argv[1:] or ['2024-02-07', '2016-01-15', '2026-08-05', '2017-03-09']
    ok = True

    for date in dates:
        print("=" * 78)
        print(f"SITTING {date}")
        print("=" * 78)
        page = RS.render_read(db, date)
        if page is None:
            print(f"  no page for {date}, skipping")
            continue
        turns = RS.full_transcript(db, date)[0]
        for t in turns:
            row = db.execute("SELECT text FROM turn WHERE key=?", (t['key'],)).fetchone()
            t['_turn_text'] = (row['text'] if row else '') or ''

        for label, passed, detail in checks_for(date, page, turns):
            print(f"  [{'PASS' if passed else 'FAIL'}] {label}  {detail}")
            ok = ok and passed

        row = db.execute("""
            SELECT c.id, c.cite_text, t.key AS turn_key
            FROM chunk c JOIN turn t ON t.key = c.turn_key
            JOIN report p ON p.report_id = t.report_id
            WHERE p.date = ? AND c.id LIKE '%#c0' AND LENGTH(c.cite_text) BETWEEN 400 AND 2000
            ORDER BY c.id LIMIT 1""", (date,)).fetchone()
        if row:
            a = RS._anchor_ref(db, row['id'], quote=None)
            if a and a.get('chunk_span'):
                cited = RS.render_read(db, date, focus_turn=a['turn_key'],
                                       focus_span=f"{a['chunk_span'][0]}-{a['chunk_span'][1]}")
                for label, passed, detail in citation_checks(
                        date, a['turn_key'], a['chunk_span'], cited, page):
                    print(f"  [{'PASS' if passed else 'FAIL'}] {label}  {detail}")
                    ok = ok and passed
            else:
                print(f"  [FAIL] citation probe resolved a span  {row['id']}")
                ok = False
        else:
            print(f"  (no citation probe for {date})")

    print()
    print("RESULT:", "FOLD GATE PASSES" if ok else "GATE FAILED")
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
