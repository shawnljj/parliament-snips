#!/usr/bin/env python3
"""Gate: arriving from a citation must mark the EXACT quoted passage, in the right place.

`/read/<date>?turn=<key>&span=<start>-<end>` is the bridge from an answer into the record. The
failure mode is silent: the page renders, a passage is highlighted, and it is the WRONG passage --
which would put the site's own words in the mouth of a Minister. Off-by-one drift is the specific
risk, because the span is applied by tiling the turn's text run by run.

So this asserts, for real citations drawn from the database:
  * the cited run's text equals the chunk's cite_text EXACTLY (not approximately)
  * the reconstruction of the turn from all its runs is byte-identical to the turn text
    (i.e. nothing was dropped, duplicated, or reordered while splitting)
  * the mark is placed inside the focused turn only
and fails loudly on any mismatch.

Run: python3 test_read_span.py
"""
import os
import re
import sqlite3
import sys
import html as H

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import read_server as RS

DB = os.path.join(HERE, '..', 'pipeline', 'hansard.db')
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# real chunks whose turn text they sit inside -- the bridge's own input
rows = db.execute("""
    SELECT c.id AS chunk_id, c.cite_text, c.turn_key, t.text AS turn_text,
           p.date, t.key AS tk
    FROM chunk c JOIN turn t ON t.key = c.turn_key
    JOIN report p ON p.report_id = t.report_id
    WHERE c.id LIKE '%#c1' AND LENGTH(c.cite_text) BETWEEN 800 AND 3000
    ORDER BY c.id
    LIMIT 60""").fetchall()

print(f"probes: {len(rows)}")
# A gate that runs on no data is not a passing gate -- it is a gate that verified nothing.
if not rows:
    print("  [FAIL] no probes selected -- the query does not match the corpus, so this would")
    print("         have 'passed' while checking nothing.")
    sys.exit(1)

ok = True
checked = spans = 0

for r in rows:
    anchor = RS._anchor_ref(db, r['chunk_id'])
    if not anchor or not anchor.get('chunk_span'):
        continue
    spans += 1
    page = RS.render_read(db, anchor['date'], focus_turn=anchor['turn_key'],
                          focus_span=f"{anchor['chunk_span'][0]}-{anchor['chunk_span'][1]}")
    if page is None:
        print(f"  [FAIL] {r['chunk_id']}: no page rendered for {anchor['date']}")
        ok = False
        continue

    html = page if isinstance(page, str) else page[0]
    cited = re.findall(r'<mark class="hl cited">(.*?)</mark>', html, re.S)

    # every run of the focused turn, in order, must reconstruct the turn exactly
    turns, _, _reports = RS.full_transcript(db, anchor['date'])
    ft = next((t for t in turns if t['key'] == anchor['turn_key']), None)
    if ft is None:
        print(f"  [FAIL] {r['chunk_id']}: focused turn not in the rendered set")
        ok = False
        continue
    rebuilt = ''.join(x['text'] for x in ft['runs'])
    trow = db.execute("SELECT text FROM turn WHERE key=?", (anchor['turn_key'],)).fetchone()
    turn_text = (trow['text'] if trow else '') or ''

    import html as _H
    # concatenate, do NOT join with a space: the marks tile the span contiguously, so any
    # separator here would invent characters that are not in the record
    cited_text = ''.join(_H.unescape(c) for c in cited)
    want = r['cite_text']
    exact = (cited_text == want)
    rebuild_ok = (rebuilt == turn_text)

    if not exact or not rebuild_ok:
        ok = False
        print(f"  [FAIL] {r['chunk_id']}")
        if not exact:
            print(f"         marked : {cited_text[:90]!r}")
            print(f"         quoted : {want[:90]!r}")
        if not rebuild_ok:
            print(f"         reconstruct differs: {len(rebuilt)} vs {len(ft['text'] or '')} chars")
    else:
        checked += 1
        print(f"  [PASS] {r['chunk_id'][:44]:<44} {len(cited_text):>4} chars marked exactly")

print()
print(f"  spans resolved     {spans}/{len(rows)}")
print(f"  exact marks        {checked}")
# Same principle one level down: probes were selected, but if not ONE produced a resolvable span,
# the bridge is broken everywhere and the run must not report success.
if spans == 0:
    print("  [FAIL] probes selected but NO span resolved -- the bridge is broken.")
    ok = False

# ---- part 2: the QUOTE-narrowed span ------------------------------------------
# The answer stage's quote is what the reader should SEE marked, so when it is present the link
# must carry the quote's own span, not the whole chunk's. Verified against real answers: the
# marked text must equal the quote exactly.
print()
print("--- quote-narrowed spans ---")
qrows = db.execute("""
    SELECT c.id AS chunk_id, c.cite_text, t.text AS turn_text, p.date
    FROM chunk c JOIN turn t ON t.key = c.turn_key
    JOIN report p ON p.report_id = t.report_id
    WHERE c.id LIKE '%#c1' AND LENGTH(c.cite_text) BETWEEN 800 AND 3000
    ORDER BY c.id LIMIT 40""").fetchall()

qn = qok = 0
for r in qrows:
    ct = r['cite_text']
    # take a real sentence from the middle of the chunk, as the model would
    sents = [x.strip() for x in re.split(r'(?<=\.)\s+', ct) if len(x.strip()) > 60]
    if not sents:
        continue
    quote = sents[len(sents) // 2]
    a = RS._anchor_ref(db, r['chunk_id'], quote=quote)
    if not a or not a.get('chunk_span'):
        qn += 1
        ok = False
        print(f"  [FAIL] {r['chunk_id']}: no span for a quote that is inside the chunk")
        continue
    qn += 1
    page = RS.render_read(db, a['date'], focus_turn=a['turn_key'],
                          focus_span=f"{a['chunk_span'][0]}-{a['chunk_span'][1]}")
    html = page if isinstance(page, str) else page[0]
    marked = ''.join(H.unescape(x) for x in
                     re.findall(r'<mark class="hl cited">(.*?)</mark>', html, re.S))
    if marked == quote and a.get('span_scope') == 'quote':
        qok += 1
    else:
        ok = False
        print(f"  [FAIL] {r['chunk_id']}: scope={a.get('span_scope')}")
        print(f"         marked : {marked[:80]!r}")
        print(f"         quote  : {quote[:80]!r}")
print(f"  quote spans exact  {qok}/{qn}")

print()
print("RESULT:", "CITED SPANS MARK THE QUOTED WORDS EXACTLY" if ok else "MISMATCH — DO NOT SHIP")
sys.exit(0 if ok else 1)
