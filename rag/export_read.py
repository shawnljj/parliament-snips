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
each sitting, and rewrites only the things that cannot survive static hosting:

  1. The ask box stays on the page but DISABLED, with the reason printed on it. It used to be
     removed entirely; Shawn's call is that removing it hid a whole capability of the product
     from the published copy. Nothing about the box is patched here -- export calls the server's
     own `ask_form(disabled=True)`, so "off" has ONE definition.
  2. Inline `<style>` -> an external `read.css`, written once. 331 pages each carrying 15KB of
     CSS is ~5MB of duplicated bytes and makes a stylesheet change a 331-file rewrite.
  3. The citation JS -> a shared `read.js`.

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

# The ask box stays on the page, DISABLED. It used to be stripped and replaced with a line of
# prose, on the reasoning that a form that posts nowhere is worse than no form. Shawn's call is
# the other way: stripping it hid an entire capability of the product from the published copy, so
# a reader could not tell there was an ask mode at all. The box now stays, visibly switched off,
# with the same disclaimer it always carried.
#
# Matched on the rendered form rather than on class names, so a change to the form's markup fails
# loudly here (the count assertion below) rather than silently shipping a live box.
ASK_FORM_RE = re.compile(r'<form class="ask".*?</form>', re.S)


def export_page(html):
    """Rewrite a rendered server page for static hosting. Returns (html, n_forms_disabled)."""
    # The form is switched off rather than removed: the served page's own ask_form(disabled=True)
    # is the single definition of what "off" looks like, so the export calls it rather than
    # patching attributes into rendered markup -- a hand-patched copy would drift from the server's
    # and the drift would be invisible, since the box looks the same either way.
    n = len(ASK_FORM_RE.findall(html))
    html = ASK_FORM_RE.sub(lambda _m: RS.ask_form(disabled=True), html)
    # Nothing here rewrites the brand or the crumb any more: page() now builds them with
    # relative targets ('index.html'), so the export has no absolute link to fix. Kept as a
    # comment rather than a no-op line, because the absence is the point -- the export only
    # touches what genuinely cannot survive static hosting.
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

    # The year menu is in EVERY page's header, the index included, so the cache has to be filled
    # before the first sitting is rendered -- an empty menu on 331 pages would be a navigation
    # control that goes nowhere, which is the defect this change exists to remove.
    RS._SITTING_YEARS_CACHE['years'] = [
        (r['y'], r['n']) for r in db.execute(
            "SELECT substr(date,1,4) y, COUNT(*) n FROM sitting GROUP BY 1 ORDER BY 1 DESC")]
    print(f"  years menu: {len(RS._SITTING_YEARS_CACHE['years'])} years "
          f"{RS._SITTING_YEARS_CACHE['years'][0][0]}..{RS._SITTING_YEARS_CACHE['years'][-1][0]}")

    # shared assets, written once
    open(os.path.join(out, 'read.css'), 'w').write(RS.CSS)

    t0 = time.time()
    forms_disabled = 0
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
        forms_disabled += n
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
    # It does use render's own sitting_names(), so the name on the index and the name in the
    # page header can never disagree -- a second naming rule here would drift exactly the way a
    # second renderer would.
    # The header now carries a working crumb and a years menu, and the menu anchors to year
    # headings on the index. Both are relative ('index.html#2016'), so export_page() already
    # leaves them alone -- unlike the old root-absolute brand link, which was rewritten here.
    names = RS.sitting_names(db)
    years = [(r['y'], r['n']) for r in db.execute(
        "SELECT substr(date,1,4) y, COUNT(*) n FROM sitting GROUP BY 1 ORDER BY 1 DESC")]
    RS._SITTING_YEARS_CACHE['years'] = years
    year_counts = dict(years)
    cur_year = None
    rows = []
    for r in db.execute("""
            SELECT g.date, COUNT(t.key) AS n_turns, COALESCE(SUM(LENGTH(t.text)), 0) AS n_chars
            FROM sitting g
            LEFT JOIN turn t ON t.date = g.date
            GROUP BY g.date ORDER BY g.date DESC"""):
        rows.append(r)
    unnamed = [r['date'] for r in rows if not names.get(r['date'])]
    if unnamed:
        print(f"  !! {len(unnamed)} sitting(s) have no name: {unnamed[:3]}")
    body = [f"""<h1>Read the sittings</h1>
<p class="sub">Every sitting of the Singapore Parliament, <b>{len(rows)}</b> of them, each named
by the topic it spent the most words on, with the summarised passages highlighted in place. The
rest is the record itself.</p>
<details class="about about--index">
<summary><span>About this site</span><span class="chev">&rsaquo;</span></summary>
<div class="aboutbody">
{RS.ask_form(disabled=True)}
</div></details>
<div class="grid">"""]
    for r in rows:
        y = r['date'][:4]
        if y != cur_year:
            cur_year = y
            body.append(f"""<h2 class="yearhead" id="{y}"><span class="y">{y}</span>
<span class="yc">{year_counts.get(y, 0)} sittings</span>
<a class="gotop" href="#top">All years &uarr;</a></h2>""")
        body.append(f"""<a class="row" href="{r['date']}.html">
  <span><b class="t">{RS.esc(names.get(r['date']) or r['date'])}</b><br><span class="n">{r['date']} ·
  {r['n_turns']:,} turns · {r['n_chars']:,} characters</span></span>
  <span class="n">read &rsaquo;</span></a>""")
    body.append('</div>')
    # The instant year jump, from the same function the server renderer uses. A separate <style>
    # tag is safe: export_page() replaces only the main stylesheet, matched exactly.
    body.append(RS.year_jump_style())
    if len(rows) != len(dates):
        print(f"  !! index lists {len(rows)} sittings but {len(dates)} were exported")
    idx = RS.page('PARSNIPS — read the sittings', ''.join(body), path='')
    # the index gets the same brand-link fix as the sitting pages -- it is built by RS.page()
    # directly, so it never passes through export_page()
    idx = idx.replace('<a class="brand" href="/">', '<a class="brand" href="index.html">')
    open(os.path.join(out, 'index.html'), 'w').write(idx)

    # ---- robots.txt and sitemap.xml, generated so they can never go stale -------------------
    # The watcher publishes sittings unattended; a hand-maintained sitemap would silently stop
    # listing new ones, which is the one failure mode worth designing out. Both are derived from
    # the same `dates` list the pages were written from.
    with open(os.path.join(out, 'robots.txt'), 'w') as f:
        f.write(f"User-agent: *\nAllow: /\n\nSitemap: {RS.canonical('sitemap.xml')}\n")
    urls = [RS.SITE_ORIGIN + '/'] + [RS.canonical(f'{d}.html') for d in dates]
    with open(os.path.join(out, 'sitemap.xml'), 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for u in urls:
            f.write(f'  <url><loc>{u}</loc></url>\n')
        f.write('</urlset>\n')
    print(f"  sitemap.xml            : {len(urls):,} urls")
    print(f"  robots.txt             : sitemap declared")

    # ---- verify the export, because a static copy fails differently from a served page ----
    print("\n" + "=" * 74)
    print("EXPORT VERIFICATION")
    print("=" * 74)
    problems = []
    n_disk = len([f for f in os.listdir(out) if f.endswith('.html')])
    print(f"  pages on disk          : {n_disk:,} ({written} sittings + index)")
    if n_disk != written + 1:
        problems.append(f"page count {n_disk} != {written}+1")

    # 1. EVERY ASK BOX ON DISK IS DISABLED. The export ships the box switched off rather than
    #    absent, so the thing worth asserting is inertness, not absence: a live form on a static
    #    host posts nowhere, which is the defect this export exists to avoid. Asserted on the
    #    exported bytes -- the disabled attribute on BOTH controls, and no input left live.
    pages = [f for f in os.listdir(out) if f.endswith('.html')]
    with_form = live_ctrl = 0
    for f in pages:
        h = open(os.path.join(out, f), encoding='utf-8').read()
        for form in re.findall(r'<form class="ask.*?</form>', h, re.S):
            with_form += 1
            if '<input' in form and 'disabled' not in form.split('<input')[1].split('>')[0]:
                live_ctrl += 1
            if '<button' in form and 'disabled' not in form.split('<button')[1].split('>')[0]:
                live_ctrl += 1
    print(f"  ask forms on disk      : {with_form:,} (want {written + 1:,}: one per page)")
    print(f"  live ask controls      : {live_ctrl}  " + ("ok" if not live_ctrl else "!! post nowhere"))
    if with_form != written + 1:
        problems.append(f"{with_form} ask forms on disk, expected {written + 1}")
    if live_ctrl:
        problems.append(f"{live_ctrl} ask control(s) left enabled on a static host")
    print(f"  ask forms disabled     : {forms_disabled:,} across the export")
    if forms_disabled != written:
        problems.append(f"{forms_disabled} forms disabled by export_page, expected {written}")
    # the disclaimer has to be ON the box: the point of shipping it is that the reason travels
    # with it, rather than the box looking broken.
    no_note = [f for f in pages
               if 'class="askoff"' not in open(os.path.join(out, f), encoding='utf-8').read()]
    print(f"  forms missing the note : {len(no_note)}" + (f" {no_note[:3]}" if no_note else "  ok"))
    if no_note:
        problems.append(f"{len(no_note)} page(s) carry a disabled box with no reason given")

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
    #    The fragment is stripped before resolving, and that is not a detail: 'index.html#2026'
    #    is a file that exists plus an anchor, and testing the whole string against the
    #    filesystem reports a working link as broken. Measured when the years menu landed: 3,652
    #    "broken" links, every one of them a year anchor, and the menu was fine.
    missing = []
    for f in os.listdir(out):
        if not f.endswith('.html'):
            continue
        h = open(os.path.join(out, f), encoding='utf-8').read()
        for m in re.finditer(r'(?:href|src)="([^"#][^"]*)"', h):
            u = m.group(1)
            if u.startswith(('http', '//', 'mailto:', 'data:')):
                continue
            target = os.path.normpath(os.path.join(out, u.split('#', 1)[0]))
            if not os.path.exists(target):
                missing.append((f, u))
    print(f"  broken internal links  : {len(missing)}" + (f" {missing[:3]}" if missing else "  ok"))
    if missing:
        problems.append(f"{len(missing)} broken internal links")

    # 3b. THE NAVIGATION WORKS. A control in the header that goes nowhere is the defect this
    #     change was made for, and it was invisible from the source: the header used to carry a
    #     pill that read like a button and was a <div>. So assert the properties that make a
    #     navigation control rather than asserting the markup is present: every page carries the
    #     menu, and every year anchor in the menu has a matching target on the index.
    #     The old crumb pill is asserted ABSENT -- 'Index' as a nav button restated the site the
    #     reader was already on, so it was removed, and a gate still requiring one would fail.
    idx = open(os.path.join(out, 'index.html'), encoding='utf-8').read()
    targets = set(re.findall(r'<h2 class="yearhead" id="(\d{4})"', idx))
    menu_years = set(re.findall(r'data-year="(\d{4})"', idx))
    no_menu = [f for f in pages
               if '<details class="ymenu">' not in open(os.path.join(out, f), encoding='utf-8').read()]
    dead = sorted(menu_years - targets)
    has_crumb = [f for f in pages
                 if 'class="crumb"' in open(os.path.join(out, f), encoding='utf-8').read()]
    print(f"  year headings on index : {len(targets)} {sorted(targets)}")
    print(f"  year menu entries      : {len(menu_years)}, dead anchors: {len(dead)}"
          + (f" {dead}" if dead else "  ok"))
    print(f"  pages with the menu    : {len(pages) - len(no_menu):,}/{len(pages):,}"
          + (f"  MISSING {no_menu[:3]}" if no_menu else "  ok"))
    print(f"  crumb pill gone        : {'ok' if not has_crumb else 'STILL PRESENT ' + str(has_crumb[:3])}")
    # the wordmark must still be the way home, since the pill is no longer one
    no_home = [f for f in pages
               if '<a class="brand" href="index.html">' not in open(os.path.join(out, f), encoding='utf-8').read()]
    print(f"  wordmark links home    : {len(pages) - len(no_home):,}/{len(pages):,}"
          + (f"  MISSING {no_home[:3]}" if no_home else "  ok"))
    # 3c. the index must turn off smooth scrolling for its year jumps. Without it every jump
    #     animates the full height of the archive (~4.7s measured) and the menu feels broken even
    #     though every anchor resolves.
    has_jump = 'scroll-behavior:auto' in idx
    print(f"  instant year jump      : {'ok' if has_jump else 'MISSING'}")
    if not has_jump:
        problems.append("index is missing the instant year-jump rule")
    if dead:
        problems.append(f"{len(dead)} year menu anchor(s) with no target: {dead}")
    if no_menu:
        problems.append(f"{len(no_menu)} page(s) missing the years menu")
    if has_crumb:
        problems.append(f"{len(has_crumb)} page(s) still carry the removed crumb pill")
    if no_home:
        problems.append(f"{len(no_home)} page(s) whose wordmark does not link home")
    if menu_years != set(y for y, _n in years):
        problems.append("year menu entries do not match the years in the corpus")

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

    # 6. CANONICAL COVERAGE. Every page must declare its own canonical on the real domain, or the
    #    vercel.app hostname and the custom domain become competing copies of the same content.
    missing = [f for f in pages
               if f'<link rel="canonical" href="{RS.SITE_ORIGIN}/' not in
               open(os.path.join(out, f), encoding='utf-8').read()]
    n_canon = len(pages) - len(missing)
    print(f"  canonical tags         : {n_canon:,}/{len(pages):,} pages")
    if missing:
        problems.append(f"{len(missing)} page(s) have no canonical: {missing[:3]}")

    # 7. SITEMAP AGREES WITH DISK. A sitemap listing URLs that were never written is worse than
    #    none: it asks a crawler to fetch 404s. Every <loc> must exist as a file, and vice versa.
    #    The origin root and index.html are the SAME page, which is why the index canonicalises to
    #    '/' -- so both are normalised to index.html before comparing, not treated as two URLs.
    sm = open(os.path.join(out, 'sitemap.xml'), encoding='utf-8').read()
    locs = re.findall(r'<loc>(.*?)</loc>', sm)
    norm = lambda u: 'index.html' if u in (RS.SITE_ORIGIN, RS.SITE_ORIGIN + '/') else u[len(RS.SITE_ORIGIN) + 1:]
    on_disk = {f for f in pages}
    listed = {norm(u) for u in locs if u.startswith(RS.SITE_ORIGIN)}
    print(f"  sitemap urls           : {len(locs):,}")
    ghost = sorted(listed - on_disk)
    unlisted = sorted(on_disk - listed)
    if ghost:
        problems.append(f"sitemap lists {len(ghost)} page(s) not on disk: {ghost[:3]}")
    if unlisted:
        problems.append(f"{len(unlisted)} page(s) missing from sitemap: {unlisted[:3]}")
    if 'Sitemap:' not in open(os.path.join(out, 'robots.txt'), encoding='utf-8').read():
        problems.append("robots.txt does not declare the sitemap")

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
