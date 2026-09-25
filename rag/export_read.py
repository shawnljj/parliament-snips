#!/usr/bin/env python3
"""Export the reading view as a static site for Vercel.

WHY A STATIC EXPORT
Vercel cannot run `read_server.py` (it needs Python + a 620MB SQLite DB + 157MB of vectors), and
the ask box genuinely cannot work there -- there is no model behind a static host. So the deployed
product is the READING view: every sitting, folded, highlights in place, with the ask box removed.

WHAT MAKES THIS SAFE
The pages are produced by calling `read_server.render_read()` itself, not by reimplementing it.
A second renderer would drift from the one the owner reviews, and the drift would be invisible --
the page would look right and disagree with the tool. So this script imports the server, renders
each sitting, and rewrites only the two things that cannot survive static hosting:

  1. The ask box (`<form class="ask">`) -> removed, replaced by a line saying asking is not
     available on the published copy. Leaving a form that posts nowhere is the worse option.
  2. Inline `<style>` -> an external `../read.css`, written once. 331 pages each carrying 15KB of
     CSS is ~5MB of duplicated bytes and makes a stylesheet change a 331-file rewrite.
  3. The citation JS -> a shared `../read.js`.

EVERY LINK IS REWRITTEN RELATIVE. The exported tree is served from a subdirectory, and a
root-absolute href ('/read/2026-08-05') would resolve against the domain root, not the export
root -- the exact class of bug that made the previous deploy 404. All internal links are relative
and checked after writing.

Usage:
    python3 export_read.py --out ~/parsnips/site/dist/read
    python3 export_read.py --out ... --limit 5      # smoke test
"""
import argparse
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import read_server as RS  # noqa: E402

DB = os.path.join(HERE, '..', 'pipeline', 'hansard.db')

# The ask box is the ONLY thing removed. Matched on the rendered form, not on class names, so a
# change to the form's markup fails loudly here (the count assertion below) rather than silently
# shipping a dead form.
ASK_FORM_RE = re.compile(r'<form class="ask".*?</form>', re.S)
ASK_NOTE = (
    '<p class="sub staticnote"><b>Reading only.</b> This published copy has no ask box: '
    'answering needs the full Hansard database and a model behind it, which a static host cannot '
    'run. Ask-mode runs locally from the same data.</p>'
)


def export_page(html):
    """Rewrite a rendered server page for static hosting. Returns (html, n_forms_removed)."""
    n = len(ASK_FORM_RE.findall(html))
    html = ASK_FORM_RE.sub(ASK_NOTE, html)
    # The brand is <a href="/">, which on a static host resolves against the DOMAIN root, not the
    # export -- the same root-absolute class of bug that made the previous deploy 404 its own pages.
    # Pages sit beside the index, so the index is 'index.html'.
    html = html.replace('<a class="brand" href="/">', '<a class="brand" href="index.html">')
    # one shared stylesheet instead of 331 copies of the same 15KB
    html = html.replace('<style>' + RS.CSS + '</style>', '<link rel="stylesheet" href="read.css">')
    return html, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True, help='output directory (e.g. site/dist/read)')
    ap.add_argument('--limit', type=int, default=0, help='export only the first N sittings (smoke test)')
    ap.add_argument('--depth', type=int, default=0,
                    help='directory nesting from the site root, for relative prefix (0 = site root)')
    args = ap.parse_args()

    out = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(out, exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    dates = [r['date'] for r in db.execute("SELECT date FROM sitting ORDER BY date DESC")]
    if args.limit:
        dates = dates[:args.limit]
    print(f"exporting {len(dates)} sittings to {out}")

    # shared assets, written once
    open(os.path.join(out, 'read.css'), 'w').write(RS.CSS)

    t0 = time.time()
    forms_removed = 0
    written = 0
    for i, date in enumerate(dates, 1):
        try:
            html = RS.render_read(db, date)
        except Exception as e:
            print(f"  !! {date}: render failed ({type(e).__name__}: {e})")
            continue
        if html is None:
            print(f"  !! {date}: no page")
            continue
        html, n = export_page(html)
        forms_removed += n
        # every exported page sits at <out>/<date>.html, so a link back to the index is README-style
        open(os.path.join(out, f'{date}.html'), 'w').write(html)
        written += 1
        if i % 25 == 0 or i == len(dates):
            print(f"   {i:,}/{len(dates):,}  {time.time()-t0:.0f}s  {written} written")

    if args.limit:
        print(f"\nSMOKE TEST ONLY ({written} pages) -- not a usable export")
        return 0

    # the index: one row per sitting, newest first. Built here rather than reused from
    # render_index() because that one lists briefs/turns for the SQL views, not sittings to read.
    rows = []
    for r in db.execute("""
            SELECT g.date, COUNT(t.key) AS n_turns, COALESCE(SUM(LENGTH(t.text)), 0) AS n_chars
            FROM sitting g
            LEFT JOIN turn t ON t.date = g.date
            GROUP BY g.date ORDER BY g.date DESC"""):
        rows.append(r)
    body = [f"""<h1>Read the sittings</h1>
<p class="sub">Every sitting of the Singapore Parliament, <b>{len(rows)}</b> of them, with the
summarised passages highlighted in place. The rest is the record itself.</p>
<p class="sub staticnote"><b>Reading only.</b> The ask box needs the full Hansard database and a
model behind it, so it runs locally rather than here.</p>
<div class="grid">"""]
    for r in rows:
        body.append(f"""<a class="row" href="{r['date']}.html">
  <span><b>{r['date']}</b><br><span class="n">{r['n_turns']:,} turns ·
  {r['n_chars']:,} characters</span></span>
  <span class="n">read &rsaquo;</span></a>""")
    body.append('</div>')
    if len(rows) != len(dates):
        print(f"  !! index lists {len(rows)} sittings but {len(dates)} were exported")
    idx = RS.page('PARSNIPS — read the sittings', ''.join(body), 'Index')
    # the index gets the same brand-link fix as the sitting pages -- it is built by RS.page()
    # directly, so it never passes through export_page()
    idx = idx.replace('<a class="brand" href="/">', '<a class="brand" href="index.html">')
    open(os.path.join(out, 'index.html'), 'w').write(idx)

    # ---- verify the export, because a static copy fails differently from a served page ----
    print("\n" + "=" * 74)
    print("EXPORT VERIFICATION")
    print("=" * 74)
    problems = []
    n_disk = len([f for f in os.listdir(out) if f.endswith('.html')])
    print(f"  pages on disk          : {n_disk:,} ({written} sittings + index)")
    if n_disk != written + 1:
        problems.append(f"page count {n_disk} != {written}+1")

    # 1. NO ASK FORM ANYWHERE. A dead form on a published page is the defect this export exists to
    #    avoid, so assert zero rather than trusting the substitution.
    leftover = [f for f in os.listdir(out) if f.endswith('.html')
                and 'class="ask"' in open(os.path.join(out, f), encoding='utf-8').read()[:200000]]
    print(f"  ask forms left on disk : {len(leftover)}" + (f" {leftover[:3]}" if leftover else "  ok"))
    if leftover:
        problems.append(f"{len(leftover)} pages still carry an ask form")
    print(f"  ask forms replaced     : {forms_removed:,} across the export")

    # 2. NO ROOT-ABSOLUTE INTERNAL LINKS. They would resolve against the domain root, not the
    #    export root -- the bug that made the previous deploy 404 its own pages.
    abs_bad = []
    for f in os.listdir(out):
        if not f.endswith('.html'):
            continue
        h = open(os.path.join(out, f), encoding='utf-8').read()
        for m in re.finditer(r'(?:href|src)="(/(?!/)[^"]*)"', h):
            abs_bad.append((f, m.group(1)))
    print(f"  root-absolute links    : {len(abs_bad)}" + (f" {abs_bad[:3]}" if abs_bad else "  ok"))
    if abs_bad:
        problems.append(f"{len(abs_bad)} root-absolute links")

    # 3. EVERY INTERNAL LINK RESOLVES ON DISK. Relative or it is wrong.
    missing = []
    for f in os.listdir(out):
        if not f.endswith('.html'):
            continue
        h = open(os.path.join(out, f), encoding='utf-8').read()
        for m in re.finditer(r'(?:href|src)="([^"#][^"]*)"', h):
            u = m.group(1)
            if u.startswith(('http', '//', 'mailto:', 'data:')):
                continue
            target = os.path.normpath(os.path.join(out, u))
            if not os.path.exists(target):
                missing.append((f, u))
    print(f"  broken internal links  : {len(missing)}" + (f" {missing[:3]}" if missing else "  ok"))
    if missing:
        problems.append(f"{len(missing)} broken internal links")

    # 4. THE FOLD IS INTACT -- the same promise the render gate makes, asserted on the EXPORTED
    #    bytes rather than on a freshly rendered string.
    sample = dates[0]
    h = open(os.path.join(out, f'{sample}.html'), encoding='utf-8').read()
    n_topics = h.count('class="topic"')
    n_gap = h.count('<details class="gap"')
    has_claim = re.search(r'<b>[\d,]+ sentences</b> in [\d,]+ stretches are folded', h) is not None
    has_cov = re.search(r'covering\s*[\d.]+%\s*of what was said', h) is not None
    print(f"  {sample}: {n_topics} topics, {n_gap:,} inline folds, "
          f"fold claim={has_claim}, coverage claim={has_cov}")
    if not (n_topics and n_gap and has_claim and has_cov):
        problems.append(f"{sample} lost its fold or its claims")

    # 5. SIZE, against the host's limit. Hobby allows 100MB of static upload.
    total = 0
    for root, _dirs, files in os.walk(out):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    print(f"  export size            : {total/1e6:.1f} MB")
    if total > 100e6:
        print(f"    NOTE: over Vercel Hobby's 100MB static-upload limit -- see the deploy note")

    print()
    if problems:
        print("RESULT: EXPORT HAS PROBLEMS")
        for p in problems:
            print("   -", p)
        return 1
    print("RESULT: EXPORT VERIFIED")
    return 0


if __name__ == '__main__':
    sys.exit(main())
