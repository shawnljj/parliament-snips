#!/usr/bin/env python3
"""Reading-mode server: serves the Fold reading view from SQLite, and the RAG answer.

Everything the reading page shows comes from the database -- transcript, summaries, and the
highlight offsets -- so there is one source of truth and no JSON re-read at request time.

  GET /                     index of sittings, with the ask box
  GET /read/<date>          the reading view for a sitting (Fold: full record, highlights in place)
  GET /ask?q=...            the RAG answer, with citations linked into reading mode
  GET /api/ask?q=...        the same answer as JSON
  GET /api/read/<date>      JSON for the reading view
  GET /health

Two things are load-bearing and non-obvious:

  * The highlight is rendered SERVER-side by slicing turn.text at the stored char offsets. Doing it
    in the browser would require re-implementing the loader's normalisation (unicode dashes, curly
    quotes, nbsp) in JS, and any drift puts the highlight under the wrong words.
  * The answer runs IN-PROCESS (the system interpreter can import answer.py and load the vector
    store; measured 2.9s per answer warm). One process serves both halves, so ask-mode and
    read-mode cannot drift apart, and the bridge between them is a plain fragment link.
"""
import argparse
import html
import json
import os
import re
import sqlite3
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote as urlquote, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# The project's one sentence splitter, shared with the chunker, the gate and the eval.
from sentences import sentence_spans  # noqa: E402

DB = '/Users/shawnlin/parsnips/pipeline/hansard.db'
PORT = 8444

# ---------------------------------------------------------------- data layer


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def plain_run(text):
    """A stretch of unsummarised text, as a collapsed run carrying its sentence count.

    The count is what the reader taps to open it ("[+9 sentences]"), so it has to be the number of
    SENTENCES in this stretch -- not lines, not paragraphs. Sentences are counted with the same
    splitter the chunker, the gate and the eval use (`sentences.sentence_spans`), so the number on
    the button and the number of sentences revealed are the same number by construction.

    A stretch with no whitespace at all (a stray mark, punctuation between two adjacent highlights)
    is not offered as a fold: there is nothing to read behind it, and an empty toggle is a dead tap.
    """
    n = len(sentence_spans(text)) if text.strip() else 0
    return {'kind': 'plain', 'text': text, 'sentences': n, 'collapsible': n > 0}


def sitting_index(db):
    """Index of sittings with per-sitting counts.

    Pre-aggregated in CTEs rather than correlating report/turn (91,263 rows) once per sitting.
    """
    return db.execute("""
        WITH sit_rep AS (
            SELECT date, COUNT(*) n_reports, SUM(n_turns) n_turns
            FROM report GROUP BY date
        ),
        agg AS (
            SELECT c.item_id,
                   COUNT(s.ord) n_sent,
                   SUM(s.turn_key IS NOT NULL) n_anch
            FROM summary_section c
            LEFT JOIN summary_sentence s ON s.section_id = c.section_id
            GROUP BY c.item_id
        )
        SELECT g.date,
               COUNT(DISTINCT i.item_id) n_items,
               SUM(a.n_sent) n_sent,
               SUM(a.n_anch) n_anch,
               MAX(sr.n_reports) n_reports,
               MAX(sr.n_turns) n_turns
        FROM summary_item_sitting g
        JOIN summary_item i ON i.item_id = g.item_id
        LEFT JOIN agg     a ON a.item_id = i.item_id
        LEFT JOIN sit_rep sr ON sr.date = g.date
        GROUP BY g.date
        ORDER BY g.date DESC""").fetchall()


def read_sitting(db, date):
    """Items for a date, each with its sections and anchored sentences.

    A section's sentences are returned with the EXACT slice of turn.text at the stored offsets,
    plus the context needed to render: speaker and the surrounding turn.
    """
    items = db.execute("""
        SELECT i.item_id, i.title, i.group_name, i.year,
               i.n_sections, i.n_sentences, i.n_anchored
        FROM summary_item_sitting g JOIN summary_item i ON i.item_id = g.item_id
        WHERE g.date = ? ORDER BY i.n_sentences DESC""", (date,)).fetchall()
    out = []
    for it in items:
        secs = db.execute("""
            SELECT section_id, ordinal, label, summary
            FROM summary_section WHERE item_id=? ORDER BY ordinal""", (it['item_id'],)).fetchall()
        sections = []
        for s in secs:
            sents = db.execute("""
                SELECT s.ord, s.sid, s.speaker, s.attributed, s.added_for_context, s.text,
                       s.turn_key, s.char_start, s.char_end, s.anchor_method,
                       t.text AS turn_text, t.speaker AS turn_speaker, t.key AS tkey,
                       r.report_id, r.report_type, r.date AS rdate
                FROM summary_sentence s
                LEFT JOIN turn   t ON t.key = s.turn_key
                LEFT JOIN report r ON r.report_id = t.report_id
                WHERE s.section_id=? ORDER BY s.ord""", (s['section_id'],)).fetchall()
            sections.append({
                'label': s['label'], 'summary': s['summary'],
                'sentences': [{
                    'sid': x['sid'], 'speaker': x['speaker'],
                    'attributed': bool(x['attributed']),
                    'context': bool(x['added_for_context']),
                    'text': x['text'],
                    'turn_key': x['turn_key'], 'report_id': x['report_id'],
                    'report_type': x['report_type'],
                    # what the page highlights: the slice at the stored offsets
                    'span': (x['turn_text'][x['char_start']:x['char_end']]
                             if x['turn_key'] and x['char_start'] is not None else None),
                    'turn_excerpt': (x['turn_text'][:400] if x['turn_text'] else None),
                    'anchor_method': x['anchor_method'],
                } for x in sents],
            })
        reports = db.execute("""
            SELECT p.report_id, p.report_type, p.n_turns
            FROM summary_item_report ir JOIN report p ON p.report_id = ir.report_id
            WHERE ir.item_id=? ORDER BY ir.ordinal""", (it['item_id'],)).fetchall()
        out.append({
            'item_id': it['item_id'], 'title': it['title'], 'group': it['group_name'],
            'year': it['year'], 'sections': sections,
            'n_sections': it['n_sections'], 'n_sentences': it['n_sentences'],
            'n_anchored': it['n_anchored'],
            'reports': [dict(r) for r in reports],
        })
    return out


def full_transcript(db, date):
    """The WHOLE record for a sitting, with the summarised sentences highlighted inside it.

    This is the other half of the reading mode: the summary layer covers only ~16% of the
    transcript, so a reader who wants the record itself must see every turn -- including the
    the ones no summary selected -- with the summarised spans marked where they occur.

    The reader opens a sitting and gets it COLLAPSED: one fold per report (a report == a topic --
    one debate, or one question and its answers), and inside a report only the highlighted sentences
    are visible, with the unhighlighted stretches replaced by an inline "[+N sentences]" toggle that
    reveals them in place. Measured on the sitting pages: 835,491 chars -> 142,684 (17.1%) on
    2024-02-07, 14.7% on 2026-08-05, 23.0% on 2016-01-15. Every unsummarised stretch is a toggle,
    never an omission -- the record is still whole, it is just not all open at once.

    IMPORTANT: the turn set is the UNION of two sets, and using only the first is wrong in both
    directions (both measured):
      A. turns of reports dated this sitting  -- the literal transcript of the day
      B. turns of reports the day's items reference -- where the highlights actually live
    An item can span sittings (2024-02-07: 275 of 882 marks belong to turns dated 2024-02-06), so
    A alone drops highlights. But items do not cover every report (2026-08-05: 295 turns on the day
    belong to reports no item references -- mostly written answers), so B alone is not the
    transcript either. Only A UNION B is both complete and fully highlighted.

    Builds, per turn: the text, split into (plain, marked) runs from the stored offsets, plus
    any section summaries whose sentences land in this turn.
    """
    turns = db.execute("""
        SELECT t.key, t.turn, t.speaker, t.text, t.words, t.is_procedural,
               p.report_id, p.title AS report_title, p.report_type, p.date AS rdate
        FROM turn t JOIN report p ON p.report_id = t.report_id
        WHERE p.date = ?
           OR p.report_id IN (
                SELECT DISTINCT ir.report_id
                FROM summary_item_sitting g JOIN summary_item_report ir ON ir.item_id = g.item_id
                WHERE g.date = ?)
        ORDER BY p.date, p.sitting_no, p.report_id, t.turn""", (date, date)).fetchall()
    if not turns:
        return [], []

    # every anchored sentence for this sitting, keyed by turn
    marks = {}
    for r in db.execute("""
            SELECT s.turn_key, s.char_start, s.char_end, s.text, s.speaker, s.sid,
                   c.section_id, c.label, c.summary, c.ordinal, i.item_id, i.title
            FROM summary_sentence s
            JOIN summary_section c ON c.section_id = s.section_id
            JOIN summary_item    i ON i.item_id = c.item_id
            JOIN summary_item_sitting g ON g.item_id = i.item_id
            WHERE g.date = ? AND s.turn_key IS NOT NULL
            ORDER BY s.turn_key, s.char_start""", (date,)):
        marks.setdefault(r['turn_key'], []).append(dict(r))

    out = []
    for t in turns:
        text = t['text'] or ''
        spans = sorted(marks.get(t['key'], []), key=lambda x: x['char_start'])
        # non-overlapping runs; an overlap is dropped rather than double-wrapped
        runs = []
        pos = 0
        for s in spans:
            cs, ce = s['char_start'], s['char_end']
            if cs is None or ce is None or cs < pos or ce > len(text):
                continue
            if cs > pos:
                runs.append(plain_run(text[pos:cs]))
            runs.append({'kind': 'marked', 'text': text[cs:ce],
                         'summary': s['summary'], 'label': s['label'],
                         'item': s['item_id'], 'title': s['title'],
                         'sid': s['sid'], 'spk': s['speaker']})
            pos = ce
        if pos < len(text):
            runs.append(plain_run(text[pos:]))
        out.append({
            'key': t['key'], 'n': t['turn'], 'speaker': t['speaker'],
            'report_id': t['report_id'], 'report_title': t['report_title'],
            'report_type': t['report_type'], 'words': t['words'],
            'rdate': t['rdate'],
            'procedural': bool(t['is_procedural']),
            'runs': runs, 'n_marks': sum(1 for r in runs if r['kind'] == 'marked'),
        })

    # A report == a topic. One fold per report, not per brief: 57 reports on 2024-02-07 against
    # 23 briefs, and the 34 reports no brief summarises (mostly written answers) would otherwise
    # fall outside every fold -- a topic the reader cannot open at all.
    group, order = {}, []
    for t in out:
        if t['report_id'] not in group:
            group[t['report_id']] = {
                'report_id': t['report_id'], 'title': t['report_title'],
                'type': t['report_type'], 'rdate': t['rdate'],
                'n_turns': 0, 'words': 0, 'n_marks': 0, 'turns': [],
            }
            order.append(t['report_id'])
        g = group[t['report_id']]
        g['turns'].append(t)
        g['n_turns'] += 1
        g['words'] += t['words'] or 0
        g['n_marks'] += t['n_marks']
    reports = [group[r] for r in order]

    # summaries for the sitting, in item order, for the inline callouts / top index
    items = db.execute("""
        SELECT i.item_id, i.title, i.n_sections, i.n_sentences, i.n_anchored
        FROM summary_item_sitting g JOIN summary_item i ON i.item_id = g.item_id
        WHERE g.date = ? ORDER BY i.n_sentences DESC""", (date,)).fetchall()
    return out, [dict(i) for i in items], reports


# ---------------------------------------------------------------- the answer

# The ranker holds a 157 MB array and the model call is serialized, so one question at a time.
_ask_lock = threading.Lock()
_answer_mod = None
_vr_ready = False


def get_answer_mod():
    global _answer_mod
    if _answer_mod is None:
        import answer as A
        _answer_mod = A
    return _answer_mod


def warm_ranker(db):
    """Load the vector store up front. The first query costs ~20s otherwise."""
    global _vr_ready
    if not _vr_ready:
        get_answer_mod().get_vr(db)
        _vr_ready = True


def _anchor_ref(db, chunk_id, quote=None):
    """Turn a cited chunk id into a link into reading mode.

    A chunk id looks like `written-statement-1481#t2#c0` (report#turn#chunk). The reader needs the
    SITTING (for the page) and the TURN (to scroll to the passage), both derived from the report --
    never asked of the model, per the project's rule that an id we already hold is looked up.

    Also resolves the character span to mark, so the read page can highlight the exact words the
    answer used. `quote` is the answer's own quoted text: when it is present and found inside the
    cited chunk, the span narrows to that sentence. Measured: a chunk's cite_text is a verbatim
    span of its turn in 400 of 400 sampled first-chunks (319 identical to the whole turn, 81 a
    substring, 0 absent), so the span is computed by content and never guessed.
    """
    if not chunk_id:
        return None
    report_id = str(chunk_id).split('#', 1)[0]
    turn_key = str(chunk_id).rsplit('#c', 1)[0]
    row = db.execute("""
        SELECT p.date, p.report_id, t.key AS turn_key, t.text AS turn_text
        FROM report p LEFT JOIN turn t ON t.key = ?
        WHERE p.report_id = ?""", (turn_key, report_id)).fetchone()
    if not row or not row['date']:
        return None
    # the exact span of the cited chunk inside its turn, found by content
    span = None
    ck = db.execute("SELECT cite_text FROM chunk WHERE id=?", (chunk_id,)).fetchone()
    ct = (ck['cite_text'] if ck else None) or ''
    if ct and row['turn_text']:
        # Prefer the QUOTE the answer actually made, narrowed inside the chunk. The answer stage
        # guarantees every quote is verbatim (its citation gate rejects anything else), so this is
        # a substring search, not a guess -- and it marks the sentence that was quoted rather than
        # the whole 2,000-character chunk, which is the difference between "here is the passage"
        # and "here are the words I used".
        want = (quote or '').strip()
        if want:
            off = ct.find(want)
            if off < 0:
                # the model may have joined sentences across the chunk with its own spacing;
                # fall back to the chunk span rather than marking nothing
                off = None
            if off is not None and off >= 0:
                base = row['turn_text'].find(ct)
                if base >= 0:
                    span = [base + off, base + off + len(want)]
        if span is None:
            off = row['turn_text'].find(ct)
            if off >= 0:
                span = [off, off + len(ct)]
    # a summary sentence anchored in this turn, if any, so the page can mark it
    sent = db.execute("""
        SELECT s.text FROM summary_sentence s
        JOIN summary_section c ON c.section_id = s.section_id
        JOIN summary_item    i ON i.item_id = c.item_id
        WHERE s.turn_key = ? LIMIT 1""", (row['turn_key'],)).fetchone() if row['turn_key'] else None
    url = f"/read/{row['date']}"
    if row['turn_key']:
        url += f"?turn={urlquote(str(row['turn_key']))}"
        if span:
            url += f"&span={span[0]}-{span[1]}"
    return {
        'date': row['date'],
        'report_id': row['report_id'],
        'turn_key': row['turn_key'],
        'url': url,
        'chunk_span': span,
        'span_scope': 'quote' if (quote and span and span[1] - span[0] == len((quote or '').strip())
                                  and (quote or '').strip()) else 'chunk',
        'summary_sentence': sent['text'] if sent else None,
    }


def answer_question(db, q, k=10):
    """Run the RAG answer stage and shape it for the reading UI.

    Returns the real answer (quotes, citations, refusal, related topics) PLUS a reading-mode link
    for every cited passage. The link is the point: an answer that ends in a citation is only
    useful if the reader can get from the quote to the record it came from.
    """
    A = get_answer_mod()
    res = A.answer(q, db, k=k, ranker='vector')
    out = {
        'question': q,
        'refused': res.get('refused'),
        'reason': res.get('reason'),
        'source': res.get('source'),
        'quotes': [],
        'related': [],
        'n_chunks': res.get('n_chunks'),
        'attempts': res.get('attempts'),
    }
    for c in (res.get('quotes') or []):
        out['quotes'].append({
            'quote': c.get('quote'),
            'citation': c.get('citation'),
            'chunk_id': c.get('chunk_id'),
            'speaker': c.get('speaker'),
            'cited_ok': c.get('cited_ok'),
            'read_in': _anchor_ref(db, c.get('chunk_id'), quote=c.get('quote')),
        })
    for s in (res.get('related') or []):
        out['related'].append({
            'title': s.get('title'),
            'date': s.get('date'),
            'report_id': s.get('report_id'),
            'grounded': s.get('grounded'),
            'read_in': _anchor_ref(db, s.get('chunk_id')),
        })
    return out


# ---------------------------------------------------------------- rendering

CSS = """
:root{
  --bg:#fbfbf9; --panel:#ffffff; --ink:#12161b; --dim:#5b6672; --line:#e4e7ea;
  --accent:#1c6b4a; --accent-soft:#e8f1ec; --mark:#fdf3d4; --mark-line:#e8d48b;
  /* the passage an answer quoted: a warmer, stronger tint than a summary highlight, kept
     light enough that body text on it stays far above the 4.5:1 minimum */
  --cited:#fbe0c4; --cited-line:#d98b3f;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.5;
  -webkit-text-size-adjust:100%}
a{color:var(--accent)}
.wrap{max-width:none;padding:0 18px}
header.top{position:sticky;top:0;z-index:20;background:rgba(251,251,249,.94);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.top .wrap{padding:10px 18px}
.brand{font-weight:700;font-size:15px;letter-spacing:-.01em;text-decoration:none;color:var(--ink);
  display:inline-flex;align-items:center;min-height:44px}
.brand span{color:var(--accent)}
.crumb{font-size:12.5px;color:var(--dim);margin-top:2px}
h1{font-size:22px;line-height:1.25;margin:18px 0 4px;letter-spacing:-.02em}
h2{font-size:17px;margin:0;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13.5px;margin:0 0 14px}
.ask{display:flex;gap:8px;margin:16px 0 6px}
.ask input{flex:1;font:inherit;font-size:15px;padding:13px 14px;border:1px solid var(--line);
  border-radius:11px;background:#fff;min-height:46px}
.ask button{font:inherit;font-weight:650;font-size:15px;padding:0 18px;border:0;border-radius:11px;
  background:var(--accent);color:#fff;min-height:46px}
.ask button:active{opacity:.85}
.item{background:var(--panel);border:1px solid var(--line);border-radius:13px;margin:12px 0;
  overflow:hidden}
.item>summary{list-style:none;cursor:pointer;padding:14px;display:block;min-height:44px}
.item>summary::-webkit-details-marker{display:none}
.ih{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.meta{font-size:12px;color:var(--dim);margin-top:5px;display:flex;gap:10px;flex-wrap:wrap}
.pill{display:inline-block;font-size:11.5px;font-weight:650;padding:2px 8px;border-radius:999px;
  background:var(--accent-soft);color:var(--accent)}
.pill.heavy{background:#fdeee2;color:#9a4a12}
.bar{height:4px;background:var(--line);border-radius:2px;margin-top:9px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent)}
/* --- full-transcript reading view --- */
/* --- the topic fold (a report == a topic) --- */
.topic{background:#fff;border:1px solid var(--line);border-radius:12px;margin:0 0 10px;overflow:hidden}
.topic>summary{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px;padding:14px 13px;
  cursor:pointer;min-height:44px;list-style:none}
.topic>summary::-webkit-details-marker{display:none}
.topic>summary:active{background:#f6f7f5}
.topic>summary .rtype{margin-bottom:0;font-size:10.5px}
.topic>summary .ttl{font-size:15.5px;font-weight:650;line-height:1.32;flex:1 1 100%;
  letter-spacing:-.01em}
.topic>summary .tmeta2{font-size:11.5px;color:var(--dim);flex:1 1 auto}
.topic>summary .chev{color:var(--accent);font-size:18px;line-height:1;transition:transform .15s}
.topic[open]>summary .chev{transform:rotate(90deg)}
.topicbody{padding:0 13px 14px}
.topic .rhead{margin:0 0 10px;padding-top:0;border-top:0}
/* --- the inline fold for unsummarised text --- */
.gap{display:inline}
.gap>summary{display:inline;font-family:var(--sans);font-size:12px;font-weight:650;
  color:var(--accent);cursor:pointer;list-style:none;white-space:nowrap;
  background:var(--accent-soft);border:1px solid #cfe0d6;border-radius:6px;
  padding:3px 6px;margin:0 2px}
.gap>summary::-webkit-details-marker{display:none}
.gap>summary:active{background:#dcebe2}
.gap[open]>summary{color:var(--dim);background:#f3f4f2;border-color:var(--line);font-weight:400}
.gapdemo{font-size:11.5px;font-weight:650;color:var(--accent);background:var(--accent-soft);
  border:1px solid #cfe0d6;border-radius:6px;padding:2px 6px;white-space:nowrap}
.foldnote{font-size:13px;color:var(--ink)}
.foldnote b{font-weight:650}
.rhead{margin:26px 0 10px;padding-top:14px;border-top:2px solid var(--ink)}
.rhead:first-of-type{border-top:0;padding-top:0}
.rtype{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;color:var(--accent);margin-bottom:5px}
.rhead h3{font-size:17px;margin:0 0 3px;letter-spacing:-.01em;line-height:1.3}
.rid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;color:var(--dim)}
.otherdate{display:inline-block;margin-left:8px;font-size:11px;font-weight:650;padding:2px 8px;
  border-radius:999px;background:#fdeee2;color:#9a4a12}
.turn{margin:0 0 20px;padding-left:12px;border-left:2px solid var(--line)}
.turn.proc{opacity:.62}
.turn.proc .tbody{font-size:14.5px;color:var(--dim)}
.tmeta{font-size:11.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  color:var(--dim);margin-bottom:5px;display:flex;justify-content:space-between;gap:10px}
.tmeta .tw{font-weight:400;text-transform:none;letter-spacing:0}
.tbody{font-family:var(--serif);font-size:16px;line-height:1.72;color:#232a32;overflow-wrap:anywhere}
.tbody mark.hl{background:var(--mark);box-shadow:inset 0 -1px 0 var(--mark-line);
  padding:1px 0;border-radius:2px;color:var(--ink)}
/* the passage an ANSWER quoted -- distinct from a summary highlight, because they are
   different claims: this one is what the model cited, that one is what the Government wrote */
.tbody mark.hl.cited{background:var(--cited);box-shadow:inset 0 -2px 0 var(--cited-line);
  font-weight:600}
.callout{background:var(--accent-soft);border-left:3px solid var(--accent);border-radius:0 10px 10px 0;
  padding:12px 13px;margin:12px 0 4px}
.callout .slabel{margin-bottom:5px}
.callout .summary{font-family:var(--serif);font-size:15.5px;line-height:1.6;margin:0}
.callout .cite{font-size:11.5px;color:var(--dim);margin-top:7px}
.sec{border-top:1px solid var(--line);padding:14px}
.sec:first-child{border-top:0}
.slabel{font-size:11.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--accent);margin-bottom:7px}
.summary{font-family:var(--serif);font-size:16.5px;line-height:1.62;margin:0 0 12px}
.transcript{border-left:3px solid var(--mark-line);padding-left:12px;margin-top:4px}
.sent{font-family:var(--serif);font-size:15.5px;line-height:1.68;margin:0 0 9px;color:#2c343d}
.sent mark{background:var(--mark);padding:1px 0;border-radius:2px;color:var(--ink)}
.sent.ctx{color:var(--dim);font-size:14.5px}
.who{font-family:var(--sans);font-size:11.5px;font-weight:700;color:var(--dim);
  text-transform:uppercase;letter-spacing:.05em;margin-right:6px}
.note{font-size:12.5px;color:var(--dim);margin-top:10px}
.note.warn{color:#9a4a12}
.cite{font-family:var(--sans);font-size:11.5px;color:var(--dim);margin-top:3px}
.openfull{font:inherit;font-weight:650;font-size:14px;color:var(--accent);background:none;border:0;
  padding:12px 0;cursor:pointer;min-height:44px}
.moretext{font-family:var(--serif);font-size:15px;line-height:1.7;color:#3a424c;
  background:#fafaf7;border:1px solid var(--line);border-radius:10px;padding:12px;margin-top:8px;
  max-height:340px;overflow:auto}
.stat{display:flex;gap:14px;font-size:12.5px;color:var(--dim);margin:4px 0 16px;flex-wrap:wrap}
.grid{display:grid;gap:8px}
.row{display:flex;justify-content:space-between;gap:10px;padding:12px 13px;background:#fff;
  border:1px solid var(--line);border-radius:11px;text-decoration:none;color:var(--ink);
  min-height:44px;align-items:center}
.row:active{background:#f6f7f5}
.row .n{font-size:12px;color:var(--dim);text-align:right;white-space:nowrap}
.row b{font-weight:650;font-size:15px}
.empty{color:var(--dim);font-size:14px;padding:14px 0}
/* --- ask / answer view --- */
form.ask{display:flex;gap:8px;margin:16px 0 6px}
.qecho{font-size:12.5px;color:var(--dim);margin:0 0 14px;font-style:italic}
.quote{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:0 12px 12px 0;padding:14px;margin:12px 0}
.quote blockquote{margin:0;font-family:var(--serif);font-size:16.5px;line-height:1.65;color:#1e252d}
.quote .cite{font-size:12px;color:var(--dim);margin-top:9px;line-height:1.5}
.quote .meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:9px}
.pill.ok{background:var(--accent-soft);color:var(--accent)}
.pill.bad{background:#fdecec;color:#9a1c1c}
.cid{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10.5px;color:var(--dim)}
.gotoread{display:inline-flex;align-items:center;min-height:44px;font-size:13.5px;
  font-weight:650;color:var(--accent);text-decoration:none;margin-top:4px}
.refusal{background:#fff8f2;border:1px solid #f0d9c4;border-left:3px solid #b4622a;
  border-radius:0 12px 12px 0;padding:14px;margin:12px 0;font-size:15px;line-height:1.6}
.refusal .why{font-size:13px;color:var(--dim);margin-top:8px}
.fail{background:#fdecec;border:1px solid #e8bcbc;border-radius:11px;padding:14px;margin:12px 0;
  font-size:14px;color:#7a1c1c}
.row.rel{text-decoration:none}
.turn.flash{border-left-color:var(--accent);background:var(--accent-soft);
  border-radius:0 10px 10px 0}
html{scroll-behavior:smooth}
footer{color:var(--dim);font-size:12px;padding:22px 0 34px;border-top:1px solid var(--line);
  margin-top:22px}
@media (min-width:760px){ .wrap{padding:0 26px} h1{font-size:26px}
  .item>summary{padding:16px} .sec{padding:18px} }
"""


def esc(s):
    return html.escape(s or '')


def page(title, body, crumb=''):
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{esc(title)}</title><style>{CSS}</style></head><body>
<header class="top"><div class="wrap">
  <a class="brand" href="/">PARSNIPS <span>reading mode</span></a>
  <div class="crumb">{esc(crumb)}</div>
</div></header>
<div class="wrap">{body}</div>
<footer>Served from <code>hansard.db</code> — transcript, summaries and highlight offsets all in
SQLite. {esc('')}</footer>
</body></html>"""


def render_ask_page(db, q, k=10):
    """The answer page: the question, the quoted answer, and a way into the record.

    Rendered server-side so the page works with JavaScript off and is linkable, shareable and
    readable on a phone. Each quote carries a "read this in the sitting" link built from the
    chunk's own report id -- the bridge from ask-mode to read-mode.
    """
    if not q:
        return page('Ask — PARSNIPS', f"""<h1>Ask the Hansard</h1>
<p class="sub">Questions are answered only from the record, with citations. If the record does not
answer it, the system says so and lists what it does have on the subject.</p>
{ask_form()}""", 'Ask')

    try:
        res = answer_question(db, q, k=k)
    except Exception as e:
        traceback.print_exc()
        return page('Ask — PARSNIPS', f"""<h1>Ask the Hansard</h1>
{ask_form(q)}<div class="fail"><b>Could not answer</b><br>{esc(type(e).__name__)}:
{esc(str(e))}</div>""", 'Ask')

    body = [f"<h1>Ask the Hansard</h1>{ask_form(q)}",
            f'<div class="qecho">{esc(q)}</div>']

    if res['refused']:
        body.append("""<div class="refusal"><b>Not in the record.</b>
The Hansard does not answer this question, so nothing is quoted rather than risking an
invented answer.""")
        if res.get('reason'):
            body.append(f'<div class="why">{esc(res["reason"])}</div>')
        body.append("</div>")
        rel = res.get('related') or []
        if rel:
            body.append('<div class="slabel">What the record does have on this</div>'
                        '<p class="note">Not an answer to your question — related passages, '
                        'oldest first.</p><div class="grid">')
            for s in rel:
                link = (s.get('read_in') or {}).get('url')
                title = s.get('title') or s.get('report_id') or 'passage'
                date = s.get('date') or ''
                if link:
                    body.append(f'<a class="row rel" href="{esc(link)}">'
                                f'<span><b>{esc(title)}</b><br>'
                                f'<span class="n">{esc(date)}'
                                f'{" · summary available" if (s.get("read_in") or {}).get("summary_sentence") else ""}'
                                f'</span></span><span class="n">read &rsaquo;</span></a>')
                else:
                    body.append(f'<div class="row rel"><span><b>{esc(title)}</b><br>'
                                f'<span class="n">{esc(date)}</span></span></div>')
            body.append('</div>')
    else:
        for c in res['quotes']:
            ref = c.get('read_in') or {}
            ok = c.get('cited_ok')
            badge = ('<span class="pill ok">citation verified</span>' if ok
                     else '<span class="pill bad">citation NOT verified</span>')
            body.append(f"""<div class="quote">
  <blockquote>{esc(c.get('quote'))}</blockquote>
  <div class="cite">{esc(c.get('citation'))}</div>
  <div class="meta">{badge}
    <span class="cid">{esc(c.get('chunk_id'))}</span></div>""")
            if ref.get('url'):
                body.append(f'<a class="gotoread" href="{esc(ref["url"])}">'
                            f'Read this in the sitting &rsaquo;</a>')
            body.append('</div>')
        nq = len(res["quotes"])
        body.append(f'<div class="stat"><span>{nq} quoted passage{"s" if nq != 1 else ""}</span>'
                    f'<span>{res.get("n_chunks") or 0} considered</span></div>')

    body.append(ASK_JS)
    return page(f'Ask — {q[:60]}', ''.join(body), 'Ask')


def ask_form(q=''):
    return f"""<form class="ask" method="get" action="/ask">
<input name="q" value="{esc(q)}" placeholder="Ask the Hansard a question…"
  aria-label="Ask a question" autocomplete="off">
<button type="submit">Ask</button></form>"""


ASK_JS = """<script>
// one Enter press submits; the form already works without JS
document.querySelectorAll('form.ask input').forEach(i => i.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); e.target.form.submit(); }
}));
</script>"""


def render_index(db):
    rows = sitting_index(db)
    n_sit = len(rows)
    tot_items = db.execute("SELECT COUNT(*) FROM summary_item").fetchone()[0]
    tot_sent = db.execute("SELECT COUNT(*) FROM summary_sentence").fetchone()[0]
    anch = db.execute("SELECT COUNT(*) FROM summary_sentence WHERE turn_key IS NOT NULL")\
        .fetchone()[0]
    body = [f"""<h1>Read the sittings</h1>
<p class="sub">{tot_items:,} briefs across {n_sit} sittings, {tot_sent:,} summarised sentences —
<b>{100*anch/tot_sent:.1f}%</b> anchored to their exact place in the transcript.</p>
{ask_form()}
<div class="stat"><span>Newest first</span><span>{n_sit} sittings</span>
<span>answers cite the record</span></div>
<div class="grid">"""]
    for r in rows:
        pct = 100 * (r['n_anch'] or 0) / max(1, r['n_sent'] or 1)
        body.append(f"""<a class="row" href="/read/{esc(r['date'])}">
  <span><b>{esc(r['date'])}</b><br><span class="n">{r['n_items']} briefs ·
  {r['n_reports']} reports · {r['n_turns']} turns</span></span>
  <span class="n">{r['n_sent'] or 0} sent<br>{pct:.0f}% anchored</span></a>""")
    body.append("</div>")
    return page('PARSNIPS — read the sittings', ''.join(body), 'Index')


def render_read(db, date, focus_turn=None, focus_span=None):
    """The Fold reading view: topics folded, highlights in place, the rest one tap away.

    Structure: a fold per report (topic) -> its turns -> only the highlighted sentences open, each
    unsummarised stretch collapsed behind an inline "[+N sentences]" toggle. A summary callout is
    attached to the first marked span of each section, so the reader gets the Government's summary
    AND the verbatim record it was drawn from, in place.

    Every turn no summary selected is still IN the page -- it is inside a collapsed stretch, not
    omitted. That distinction is the whole design: the record is complete and the page is short.

    focus_turn / focus_span: when arriving from an answer's citation, the turn to highlight and
    scroll to, and the character span within it that the answer actually quoted. The citation also
    FORCES its topic open and its stretch visible -- landing on a closed fold would hide the very
    passage the reader came for.
    """
    turns, items, reports = full_transcript(db, date)
    if not turns:
        return None
    focus_key = None
    if focus_turn:
        # the link carries a turn key (report#tN); accept it, and also accept a bare chunk id
        cand = str(focus_turn).rsplit('#c', 1)[0]
        for t in turns:
            if t['key'] == cand or t['key'] == focus_turn:
                focus_key = t['key']
                break
    focus_report = next((t['report_id'] for t in turns if t['key'] == focus_key), None)
    # A cited span is applied per CHARACTER over the focused turn, then regrouped into runs.
    # The earlier run-splitting version could not mark a span that overlapped a summary
    # highlight, and emitted the cited text as several marks whose concatenation no longer
    # equalled the quoted chunk -- measured 5 of 60 exact. Working per character makes the
    # result provable: the cited runs concatenate to exactly text[cs:ce].
    # Cost is O(turn length) and it touches ONE turn, the one the citation points at.
    cs = ce = None
    if focus_key and focus_span:
        try:
            cs, ce = (int(x) for x in str(focus_span).split('-', 1))
        except (ValueError, TypeError):
            cs = ce = None
        if cs is not None and ce is not None and ce > cs:
            for t in turns:
                if t['key'] != focus_key:
                    continue
                old = t['runs']
                n = sum(len(r['text']) for r in old)
                kinds = ['plain'] * n
                meta = [None] * n
                i = 0
                for r in old:
                    if r['kind'] != 'plain':
                        for j in range(i, i + len(r['text'])):
                            kinds[j] = 'marked'
                            meta[j] = r
                    i += len(r['text'])
                for j in range(max(0, cs), min(n, ce)):
                    kinds[j] = 'cited'        # the answer's own citation wins over a summary mark
                # regroup consecutive same-kind characters into runs
                new, i = [], 0
                while i < n:
                    k = kinds[i]
                    j = i
                    while j < n and kinds[j] == k:
                        j += 1
                    seg = ''.join(r['text'] for r in old)[i:j]
                    if k == 'marked':
                        m = meta[i] or {}
                        new.append({**m, 'kind': 'marked', 'text': seg})
                    elif k == 'cited':
                        new.append({'kind': 'cited', 'text': seg})
                    else:
                        # rebuilt from the split characters, so it is re-measured, not inherited
                        new.append(plain_run(seg))
                    i = j
                t['runs'] = new
                t['n_cited'] = sum(1 for r in new if r['kind'] == 'cited')
                t['n_marks'] = sum(1 for r in new if r['kind'] == 'marked')
                break
    n_marks = sum(t['n_marks'] for t in turns)
    n_marked_turns = sum(1 for t in turns if t['n_marks'])
    n_chars = sum(sum(len(r['text']) for r in t['runs']) for t in turns)
    n_marked_chars = sum(len(r['text']) for t in turns for r in t['runs']
                         if r['kind'] == 'marked')
    n_shown_chars = n_marked_chars + sum(len(r['text']) for t in turns for r in t['runs']
                                         if r['kind'] == 'cited')
    # Collapsed stretches the reader is NOT being shown: the fold's own measure of itself.
    foldable = [r for t in turns for r in t['runs']
                if r['kind'] == 'plain' and r.get('collapsible')]
    n_folded_sent = sum(r['sentences'] for r in foldable)
    n_open_sent = sum(len(sentence_spans(r['text'])) for t in turns for r in t['runs']
                      if r['kind'] in ('marked', 'cited'))
    body = [f"""<h1>{esc(date)}</h1>
<p class="sub">Full transcript, {n_chars:,} characters — every turn. Summarised passages are
<mark>highlighted</mark>: {n_marks:,} passages across {n_marked_turns} turns, covering
{100*n_marked_chars/max(1,n_chars):.1f}% of what was said. The rest is the record itself.</p>
<p class="sub foldnote">Open on <b>{n_open_sent:,} sentences</b> ({100*n_shown_chars/max(1,n_chars):.1f}%
of the characters). <b>{n_folded_sent:,} sentences</b> in {len(foldable):,} stretches are folded
inline — tap a <span class="gapdemo">[+N sentences]</span> to read them where they sit. Every topic
below is closed until you open it.</p>
{ask_form()}
<div class="stat"><span>{len(reports)} topics</span><span>{len(turns):,} turns</span>
<span>{len(items)} briefs summarised</span><span>{n_marks:,} highlighted passages</span></div>"""]
    seen = set()          # a section's callout belongs to its FIRST sentence, once for the page
    for g in reports:
        o = ' open' if (focus_report and g['report_id'] == focus_report) else ''
        other = (f'<span class="otherdate">recorded {esc(g["rdate"])}</span>'
                 if g['rdate'] != date else '')
        body.append(f"""<details class="topic"{o}>
<summary><span class="rtype">{esc(g['type'])}</span><span class="ttl">{esc(g['title'])}</span>
<span class="tmeta2">{g['n_turns']} turns · {g['words']:,}w
· {g['n_marks']} highlighted</span><span class="chev">›</span></summary>
<div class="topicbody">
<div class="rhead"><span class="rid">{esc(g['report_id'])}</span>{other}</div>""")
        for t in g['turns']:
            cls = ' class="turn proc"' if t['procedural'] else ' class="turn"'
            if focus_key and t['key'] == focus_key:
                cls = (' class="turn proc flash"' if t['procedural'] else ' class="turn flash"')
            who = esc(t['speaker']) if t['speaker'] else 'Speaker not recorded'
            anchor = f' id="turn-{esc(t["key"])}"'
            body.append(f"<article{cls}{anchor}><div class=\"tmeta\">{who}"
                        f"<span class=\"tw\">{t['words'] or 0} words</span></div><div class=\"tbody\">")
            for r in t['runs']:
                if r['kind'] == 'marked':
                    body.append(f"<mark class=\"hl\">{esc(r['text'])}</mark>")
                elif r['kind'] == 'cited':
                    body.append(f"<mark class=\"hl cited\">{esc(r['text'])}</mark>")
                elif r.get('collapsible'):
                    # An inline fold. The marker sits at the highlight's baseline and its text
                    # rejoins the paragraph flow when opened, so the reveal stays IN the sentence
                    # rather than escaping into a block of its own.
                    # The count in the label is the number of SENTENCES inside, so opening it shows
                    # exactly as many sentences as the button promised.
                    body.append(f"<details class=\"gap\"><summary>[+{r['sentences']} "
                                f"sentence{'' if r['sentences'] == 1 else 's'}]</summary>"
                                f"<span>{esc(r['text'])}</span></details>")
                else:
                    body.append(esc(r['text']))
            body.append("</div>")
            # attach each section summary once, under the turn holding its first sentence
            for r in t['runs']:
                if r['kind'] != 'marked' or r['summary'] is None:
                    continue
                key = (r['item'], r['label'])
                if key in seen:
                    continue
                seen.add(key)
                body.append(f"""<aside class="callout">
  <div class="slabel">{esc(r['label'] or 'Section')}</div>
  <p class="summary">{esc(r['summary'])}</p>
  <div class="cite">summary of <b>{esc(r['title'])}</b>
    · sid {esc(r['sid'] or '')}</div></aside>""")
            body.append("</article>")
        body.append("</div></details>")
    body.append("""<script>
// Arriving from a citation: the server opened the cited topic, but a citation can point at a
// passage inside a COLLAPSED stretch. Open only that stretch -- not the whole page, which would
// undo the fold -- then scroll to the passage.
document.querySelectorAll('article.turn.flash .gap').forEach(d => {
  if (d.querySelector('mark.hl.cited')) d.open = true;
});
document.querySelectorAll('article.turn.flash').forEach(a => {
  a.scrollIntoView({block: 'center'});
});
</script>""")
    return page(f'{date} — PARSNIPS reading', ''.join(body), f'Reading · {date}')


# ---------------------------------------------------------------- server


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, body, ctype='text/html; charset=utf-8'):
        raw = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        db = connect()
        try:
            if path == '/health':
                warm = _vr_ready
                return self._send(200, json.dumps({'ok': True, 'ranker_warm': warm}),
                                  'application/json')
            if path == '/':
                return self._send(200, render_index(db))
            if path == '/ask':
                q = (qs.get('q') or [''])[0].strip()
                k = int((qs.get('k') or ['10'])[0])
                return self._send(200, render_ask_page(db, q, k=k))
            if path == '/api/ask':
                q = (qs.get('q') or [''])[0].strip()
                if not q:
                    return self._send(400, json.dumps({'error': 'empty question'}),
                                      'application/json')
                k = int((qs.get('k') or ['10'])[0])
                with _ask_lock:
                    res = answer_question(db, q, k=k)
                return self._send(200, json.dumps(res), 'application/json')
            if path.startswith('/read/'):
                date = unquote(path[len('/read/'):])
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
                    return self._send(400, page('Bad date', '<p class="empty">Bad date.</p>'))
                focus = (qs.get('turn') or [None])[0]
                fspan = (qs.get('span') or [None])[0]
                h = render_read(db, date, focus_turn=focus, focus_span=fspan)
                if h is None:
                    return self._send(404, page('Not found',
                                                f'<p class="empty">No sitting on {esc(date)}.</p>'))
                return self._send(200, h)
            if path.startswith('/api/read/'):
                date = unquote(path[len('/api/read/'):])
                data = read_sitting(db, date)
                return self._send(200, json.dumps({'date': date, 'items': data}),
                                  'application/json')
            return self._send(404, page('Not found', '<p class="empty">Not found.</p>'))
        except Exception as e:
            # An exception in a handler must still produce a RESPONSE. Without this, the
            # traceback goes to the log and the client receives an empty reply (curl exit 52,
            # browser shows a network error), so a code bug is indistinguishable from the
            # server being down -- which is exactly how a stale server masked a fixed bug here.
            import traceback
            traceback.print_exc()
            try:
                self._send(500, page('Server error',
                                     f'<p class="empty">Internal error: {esc(type(e).__name__)}: '
                                     f'{esc(str(e))}</p>'))
            except Exception:
                pass
        finally:
            db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--bind', default='0.0.0.0')
    ap.add_argument('--warm', action='store_true',
                    help='load the vector ranker before serving (first question ~20s otherwise)')
    args = ap.parse_args()

    db = connect()
    n = db.execute("SELECT COUNT(*) FROM summary_item").fetchone()[0]
    if n == 0:
        print("summary tables are empty — run build_summaries.py --rebuild first")
        sys.exit(2)

    if args.warm:
        print("warming the vector ranker…", flush=True)
        try:
            warm_ranker(db)
            print("ranker ready", flush=True)
        except Exception as e:
            # reading mode must still serve if the ranker cannot load; only /ask degrades
            print(f"WARNING: could not warm the ranker ({type(e).__name__}: {e}). "
                  f"Reading mode works; /ask will fail.", flush=True)
    db.close()

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"reading mode + ask on http://{args.bind}:{args.port}/  ({n:,} briefs loaded)",
          flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
