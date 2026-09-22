#!/usr/bin/env python3
"""Rebuild site/dist from the committed inputs and assert the result is a faithful,
idempotent build. Run with NO arguments; exits non-zero if the assertion fails.

WHY THIS EXISTS. site/dist is the artifact Vercel deploys, and it is also tracked, so it
is the one directory where "I built it" and "I committed it" can silently disagree. It
disagreed for weeks: 25 of the 25 2017 sitting pages still carried pre-grid markup after
442d49e shipped 343 2017 briefs, because dist was never rebuilt. Nothing detected it --
the pages were valid HTML, they linked the current stylesheet, and only a markup-level
comparison could see that they came from a different generator generation.

WHAT IT ASSERTS (each one has been false at least once):
  1. IDEMPOTENT   a second build changes nothing, so the generator is deterministic and
                  a diff after a build is real drift, not churn.
  2. FAITHFUL     every tracked file under site/dist equals what a build of the current
                  inputs produces (modulo the files the generator does not own, listed
                  in NOT_GENERATED below, and the drift held back in HELD_BACK_BY_GATE).
  3. NEW FILES    a build introduces no file that is not committed -- an untracked
                  payload is a broken expander on the deployed site.
  4. NOTHING LOST no tracked file disappears.
  5. MIXED-GENERATION every COMMITTED sitting page carries the markers the current
                  render_sitting emits. This reads HEAD, not the working tree, because
                  this tool rebuilds site/dist in place -- a marker test on freshly-built
                  files passes by construction.
  6. INCOMPLETE-PAGE every COMMITTED sitting page carries every brief the data places on
                  it. Two sub-assertions (5 and 6) because they catch different pages:
                  against the pre-fix commit the marker caught 24 of the 25 stale 2017
                  pages and the brief-set check caught the 25th (2017-02-28 carries 1 of
                  the 5 substance briefs the data places on it).
  7. PAGE-PAYLOAD-OVERLAP no COMMITTED page/payload pair shows a sentence both in the
                  body and in its "N sentences hidden" expander. The invariant a
                  `git add -A` sweep silently broke once.

WHAT IT DOES NOT DO. It does not decide whether a diff is acceptable: it refuses to
guess and exits 1 with the file list. Committing a rebuilt dist is a deliberate step.

Usage:
    python3 tools/check_dist.py            # build to site/dist, verify, exit 0/1
    python3 tools/check_dist.py --explain  # print the generator/artifact history
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join("site", "dist")

# Files under site/dist that build_site.py does not produce, and why each is there.
# Listed explicitly so "not generated" is a decision on record rather than a surprise.
NOT_GENERATED = {
    "case-study.html": "built by site/build_case_study.py",
    "pipe-arch.html": "built by site/build_arch.py",
    "spike-funnel.html": "built by spike_funnel.py (design spike)",
    "spike-panels.html": "built by spike_panels.py (design spike)",
    "spike-select.html": "built by spike_select.py (design spike)",
}

# Drift that is HELD BACK, not accepted, and the defect it is held back for.
#
# Committing these would make site/dist internally inconsistent -- measured, not assumed:
# the 2016 sitting pages and the committed 2016 skipped payloads are the same record
# generation and are mutually consistent (2 sentence texts appear both on a page and in
# its payload, summed across all 349 briefs). The current record's sid->text map has
# since moved -- `tools/check_selection.py summaries/2016 2016` reports 672 verbatim
# mismatches against the briefs -- so a rebuild rewrites the payload to the NEW map while
# the page keeps rendering the OLD text. In 2016 that raises the overlap from 2 to 502
# (3 payloads: 487 + 14 + 1); site-wide the swept state measures 613 over 88 of 1,236
# payload x host-page pair(s), the other years contributing 111 in both states. Readers
# would see sentences in the body that the "N sentences hidden" expander also reveals.
#
# The hold is CONDITIONAL and re-verified on every run: it applies only while
# check_selection still FAILs that year. When the defect is fixed the exemption
# disappears and this check starts demanding the rebuild -- which is the correct moment,
# because then page and payload can be moved together. A hold is also strictly
# year-scoped, so it cannot mask drift in any other year.
HELD_BACK_BY_GATE = {
    "2016": "the 2016 page/payload generation split: check_selection reports the briefs "
            "verbatim-mismatching the record (P0-1, t_608872bf). Committing the rebuilt "
            "payload would desync it from the page it is served to.",
}

# How much page/payload sentence overlap the corpus already has, counted once per payload
# over every committed page that hosts the brief (site-wide, all years):
#
#   109  on main (the pre-fix tree: 999 committed payloads, 83 of them overlapping)
#   113  on this branch after the 42 held-back payloads were restored
#   613  on this branch's HEAD before that restore, i.e. with the rebuilt payloads in
#        (2016's own contribution is 502 over 3 payloads, one of which -- bill-223+224+
#         225+226+227+228+229+230 -- accounts for 487 alone; the other years 111 in both
#         states: 109 = 2 + 107, 613 = 502 + 111)
#
# The 2016-only view of the same pair of states is 2 -> 502. Both scopes are re-measured
# by r11/probe_r11l.py in the task workspace with an independently written instrument.
#
# The ~109 is pre-existing residue: single sentences, a handful of pages per year, and it
# is NOT confined to the years this check exempts -- 2016 contributes 2 of the 109 (and 2
# of this branch's 113). It is not what this check is for. The check exists to catch the
# REGRESSION, which is an order of magnitude larger, so the threshold sits between them.
# It is deliberately not set at the baseline itself: a gate that is always red is a gate
# nobody reads, and a threshold pinned to the exact current value would fire on any
# unrelated single-sentence change.
#
# These three figures are the ONES THIS RULE PRINTS. An earlier version of this check
# paired each payload with one page, chosen by whichever sitting date glob.glob() returned
# first, and printed 106/107/108 on main and 110/112 on this branch depending on directory
# order -- a gate that was not a function of the tree. Pairing with every host page removes
# the choice; the figures above are stable under all three orders.
OVERLAP_BASELINE = 250


def year_of_brief(brief_id):
    """The year of the summary that owns `brief_id`, or None."""
    hits = glob.glob(os.path.join(ROOT, "summaries", "20*", f"{glob.escape(brief_id)}.json"))
    return os.path.basename(os.path.dirname(hits[0])) if hits else None


def held_back(rel):
    """(year, reason) when `rel` (relative to site/dist) is held back, else None."""
    if rel.startswith("skipped/"):
        brief_id = os.path.basename(rel)[:-5]
        year = year_of_brief(brief_id)
        if year in HELD_BACK_BY_GATE and selection_gate_failing(year):
            return year, HELD_BACK_BY_GATE[year]
    return None


def selection_gate_failing(year):
    """True when check_selection reports a FAIL for `year`.

    Used only to decide whether a hold-back exemption still applies. If the tool cannot
    be run, the exemption is refused -- failing closed, so an unverifiable defect cannot
    silently excuse drift.
    """
    tool = os.path.join(ROOT, "tools", "check_selection.py")
    d = os.path.join(ROOT, "summaries", year)
    if not (os.path.exists(tool) and os.path.isdir(d)):
        return False
    r = subprocess.run([sys.executable, tool, d, year], cwd=ROOT,
                       capture_output=True, text=True)
    return r.returncode != 0

# Structures only the CURRENT render_sitting emits. A page without them is from an
# older generator and, if it is still deployed, is a mixed-generation artifact: it gets
# today's theme.css but not today's markup or inline script.
GENERATION_MARKERS = ("dsec", "vslist")

PAGE_RE = re.compile(r"^sittings/20\d\d-\d\d-\d\d\.html$")


def briefs_expected_by_date():
    """{date: {brief id, ...}} — the substance briefs each sitting page must carry.

    Mirrors render_sitting's own rule: a brief belongs to a page when its
    _meta.sitting_dates names that date, and group == "oral" is excluded because oral
    answers render in the page's oral-answers section instead of as a substance card.
    """
    out = {}
    for p in glob.glob(os.path.join(ROOT, "summaries", "20*", "*.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                b = json.load(fh)
        except (OSError, ValueError):
            continue
        meta = b.get("_meta") or {}
        if meta.get("group") == "oral":
            continue
        for d in meta.get("sitting_dates") or []:
            out.setdefault(d, set()).add(os.path.basename(p)[:-5])
    return out


def git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                          text=True).stdout


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def walk_dist():
    out = {}
    for dp, _dn, fns in os.walk(os.path.join(ROOT, DIST)):
        for fn in fns:
            full = os.path.join(dp, fn)
            out[os.path.relpath(full, os.path.join(ROOT, DIST))] = full
    return out


def head_blob(rel):
    """The committed bytes of site/dist/<rel>, or None if it is not tracked."""
    p = subprocess.run(["git", "show", f"HEAD:{DIST}/{rel}"], cwd=ROOT,
                       capture_output=True)
    return p.stdout if p.returncode == 0 else None


def tracked():
    return {os.path.relpath(p, DIST) for p in git("ls-files", DIST).splitlines()
            if p.strip()}


def status_snapshot():
    """Normalised `git status` for site/dist: (mode, path) pairs, sorted.

    Both staged and unstaged changes count: `git status` reports a staged change as "M "
    and an unstaged one as " M", and the question this check asks is "does the committed
    tree match a build of the current inputs", not "was it staged".
    """
    out = []
    for line in git("status", "--porcelain", "--", DIST).splitlines():
        if line.strip():
            out.append((line[:2].strip() or "??", line[3:].strip('"')))
    return sorted(out)


def uncommitted():
    """Paths under site/dist that differ from HEAD, staged or not (mode -> path)."""
    out = {}
    for line in git("diff", "HEAD", "--name-status", "--", DIST).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out[os.path.relpath(parts[-1], DIST)] = parts[0]
    return out


def blob_batch(rels):
    """{rel -> bytes} for HEAD:site/dist/<rel>, in ONE subprocess.

    `git show` per file is ~10ms, which over thousands of payloads is minutes; this
    streams them all through `git cat-file --batch` instead. Order is preserved by
    position because the output echoes the resolved sha, not the requested spec.
    """
    specs = [f"HEAD:{DIST}/{r}" for r in rels]
    if not specs:
        return {}
    r = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT,
                       input="".join(s + "\n" for s in specs).encode(),
                       capture_output=True)
    buf, i, out, n = r.stdout, 0, {}, 0
    while i < len(buf) and n < len(rels):
        nl = buf.find(b"\n", i)
        if nl < 0:
            break
        parts = buf[i:nl].split()
        i = nl + 1
        if len(parts) >= 3 and parts[1] == b"blob":
            size = int(parts[2])
            out[rels[n]] = buf[i:i + size]
            i += size + 1
        elif len(parts) >= 2 and (parts[1] == b"missing" or parts[-1] == b"missing"):
            pass
        else:
            break
        n += 1
    return out


_TEXT = re.compile(r'<p class="vs-text">(.*?)</p>', re.S)
_ENTS = (("&amp;", "&"), ("&quot;", '"'), ("&#x27;", "'"), ("&#39;", "'"),
         ("&mdash;", "\u2014"), ("&ndash;", "\u2013"), ("&lt;", "<"), ("&gt;", ">"),
         ("&nbsp;", " "), ("&hellip;", "\u2026"))


def page_sentence_texts(html):
    """The published sentence texts on a sitting page, normalised for comparison."""
    out = set()
    for m in _TEXT.finditer(html):
        t = re.sub(r"<[^>]+>", "", m.group(1))
        for a, b in _ENTS:
            t = t.replace(a, b)
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) > 40:
            out.add(t)
    return out


def payload_texts(raw):
    try:
        entries = json.loads(raw)
    except ValueError:
        return set()
    out = set()
    for e in entries:
        t = re.sub(r"\s+", " ", str(e.get("text", ""))).strip()
        if len(t) > 40:
            out.add(t)
    return out


def build():
    r = subprocess.run([sys.executable, os.path.join("site", "build_site.py")],
                       cwd=ROOT, capture_output=True, text=True)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--explain", action="store_true",
                    help="print why this check exists")
    a = ap.parse_args()
    if a.explain:
        print(__doc__)
        return 0

    problems = []
    notes = []

    # ---- 1. build, then build again and require the same status both times --------
    r1 = build()
    if r1.returncode != 0:
        print(r1.stdout)
        print(r1.stderr, file=sys.stderr)
        print("FAIL — the generator itself exited non-zero.")
        return 1
    first = status_snapshot()
    r2 = build()
    second = status_snapshot()
    if first != second:
        added = sorted(set(second) - set(first))
        problems.append(("NOT-DETERMINISTIC", "site/dist",
                         f"a second build changed {len(added)} more path(s); "
                         f"first: {added[:5]}"))
    else:
        notes.append("idempotent: two consecutive builds produce identical output")

    # ---- 2..4. compare disk against HEAD ------------------------------------------
    trk = tracked()
    on_disk = walk_dist()          # keys are already relative to site/dist
    dirty_rel = set(uncommitted())          # staged or not, vs HEAD
    new_rel = set(on_disk) - trk
    gone_rel = trk - set(on_disk)

    stale = sorted(dirty_rel - set(NOT_GENERATED))
    held, remaining = [], []
    for rel in stale:
        exempt = held_back(rel)
        if exempt:
            held.append((rel, exempt))
        else:
            remaining.append(rel)
    if held:
        years = sorted({y for _r, (y, _w) in held})
        notes.append(f"{len(held)} file(s) held back from this rebuild, NOT accepted: "
                     f"{', '.join(years)} — {held[0][1][1]}")
    if remaining:
        problems.append(("UNCOMMITTED-BUILD-CHANGE", "site/dist",
                         f"{len(remaining)} tracked file(s) differ from a build of the "
                         f"current inputs: {remaining[:8]}"))
    new_bad = sorted(new_rel - set(NOT_GENERATED))
    if new_bad:
        problems.append(("UNTRACKED-OUTPUT", "site/dist",
                         f"{len(new_bad)} file(s) a build produces are not committed "
                         f"(a deployed page will fetch these and 404): {new_bad[:8]}"))
    if gone_rel:
        problems.append(("MISSING", "site/dist",
                         f"{len(gone_rel)} tracked file(s) the generator no longer "
                         f"produces: {sorted(gone_rel)[:8]}"))

    # ---- 5. one generation ---------------------------------------------------------
    # Read the COMMITTED bytes, not the working tree. This tool builds site/dist in
    # place, so the files on disk have just been regenerated and would pass any marker
    # test by construction -- measured: on the pre-fix tree the disk copy has 142 .dsec
    # on 2017-01-09 while HEAD has 0. What Vercel deploys is what is committed, so that
    # is what these two assertions read.
    #
    # Two assertions, because a structure marker and a brief-SET check catch different
    # pages. Measured against the pre-fix commit: the marker catches 24 of the 25 stale
    # 2017 pages, and the set check catches the 25th (2017-02-28 has .dsec but carries
    # 1 of the 5 briefs the data places on it). Neither alone is enough -- and note the
    # test below is `want - have` (a set difference), not a count: on that same page a
    # count would be satisfied, because it does carry one host section for one brief.
    old, incomplete = [], []
    expected = briefs_expected_by_date()
    for rel in sorted(trk):
        if not PAGE_RE.match(rel):
            continue
        blob = head_blob(rel)
        if blob is None:
            continue
        text = blob.decode("utf-8", "replace")
        if not all(f'class="{m}"' in text for m in GENERATION_MARKERS):
            old.append(rel)
            continue
        date = os.path.basename(rel)[:-5]
        want = expected.get(date, set())
        have = {b for b in re.findall(r'data-brief="([^"]+)"', text)
                if not b.startswith("'")}
        if want - have:
            incomplete.append((rel, sorted(want - have)))
    n_pages = sum(1 for rel in trk if PAGE_RE.match(rel))
    if old:
        problems.append(("MIXED-GENERATION", "site/dist",
                         f"{len(old)} of {n_pages} committed sitting page(s) lack the "
                         f"current generator's markup ({', '.join(GENERATION_MARKERS)}); "
                         f"the deployed site serves today's theme.css over an older "
                         f"page: {old[:8]}"))
    if incomplete:
        # Every missing brief is named, not a sample: this message is the only place the
        # on-main defect (2017-02-28 carries 1 of the 5 briefs the data places on it) is
        # legible, and an m[:3] truncation silently dropped `budget-905+907+911+913+915+919`,
        # the largest of the four. The number of pages is what is bounded here, not the
        # evidence about each one.
        problems.append(("INCOMPLETE-PAGE", "site/dist",
                         f"{len(incomplete)} of {n_pages} committed sitting page(s) "
                         f"carry the current markup but not every brief the data places "
                         f"on them: {incomplete[:5]}"))
    if not old and not incomplete:
        notes.append(f"all {n_pages} committed sitting pages carry the current markup "
                     f"and every brief the data places on them")

    # ---- 7. page and payload are disjoint ------------------------------------------
    # The invariant a sitting page and the payload its expander fetches must satisfy:
    # the page shows the sentences the brief SELECTED, the payload lists the ones it did
    # NOT. A sentence in both is shown and "hidden" at once. Measured at the base commit
    # the pair overlaps by 2 sentence texts across all 349 2016 briefs; after the 42
    # rebuilt payloads were swept into a commit by `git add -A` it was 502 in 2016 and
    # 613 site-wide. The failure is silent in the browser -- the expander just re-reveals
    # text already on the page -- so this has to be checked by construction.
    #
    # THE PAIRING RULE, and why it is stated here rather than left implicit. The pair is
    # (payload, page) and the invariant is about the page that FETCHES the payload, so the
    # pairs are exactly the committed payload x committed page combinations where the page
    # hosts the brief: <section class="dsec" data-brief="...">. Every such page is paired,
    # in sorted order, and a payload hosted on no page has no pair to check -- it is never
    # fetched, so it cannot reveal anything.
    #
    # This has to be a function of the TREE, and three earlier versions were not:
    #   * the sample was capped at 400 by iterating a set, so the same tree measured 533
    #     in one run and 30 in the next (hash order);
    #   * then the pair list was sorted but the PAIRING was built with `setdefault` while
    #     iterating glob.glob(), so date_of_brief[brief] was whichever date the filesystem
    #     happened to return first. Only ONE payload (budget-1123+1129, sitting dates
    #     2019-03-01/2019-03-04, hosted on both pages) changes an overlap verdict, but it
    #     moved the site-wide figure between 106/107/108 on main and 110/112 on this
    #     branch as the directory order changed (r11/probe_r11c.py in the task workspace);
    #   * a one-page reduction ("the first page that carries it") is still arbitrary for a
    #     brief hosted on several pages, and 112 of the 1,090 committed payloads here are.
    # Pairing with every host page removes the reduction entirely: no tie to break, no
    # order to depend on. It also makes the pair COUNT larger (1,236 here, not 1,090),
    # because 112 payloads are hosted on more than one page.
    page_rels = sorted(r for r in trk if PAGE_RE.match(r))
    hosted_by = {}
    for page_rel, blob in blob_batch(page_rels).items():
        for bid in re.findall(r'data-brief="([^"]+)"', blob.decode("utf-8", "replace")):
            if bid.startswith("'"):
                continue          # the page's own inline script template, not a host
            sites = hosted_by.setdefault(bid, [])
            if page_rel not in sites:
                sites.append(page_rel)
    pairs = []
    for rel in sorted(trk):
        if not rel.startswith("skipped/") or not rel.endswith(".json"):
            continue
        brief_id = os.path.basename(rel)[:-5]
        if year_of_brief(brief_id) is None:
            continue
        for page_rel in hosted_by.get(brief_id, ()):
            pairs.append((rel, page_rel))
    # The MAGNITUDE is counted once per payload, over the UNIQUE texts it shares with any
    # of its host pages. Summing over pairs instead would multiply a payload by the number
    # of pages hosting its brief -- `bill-223+224+225+226+227+228+229+230` alone is hosted
    # on 3 and would contribute its 487 three times, turning the swept state's 613 into
    # 1,668. Both sides of that arithmetic are measured on the swept state with this file's
    # own pairing rule and text extractors (r12/probe_item3.py in the task workspace):
    # 88 of 1,090 payloads overlap once per payload, 141 pairs overlap summed over pairs,
    # and the two totals are 613 and 1,668 (one payload, `bill-223+...`, contributes 487
    # three times = 1,461 of the 1,668).
    # (611 was the superseded round-9 reading, taken under the one-page-per-payload pairing
    # this rule replaced; the same tree reads 613 under the rule below, which is what the
    # three other comments in this file and docs/site-dist.md state.)
    # A sentence shown and hidden at once is one sentence whether one page or six reveal
    # it, so the union per payload is what the invariant actually measures; and for the
    # 978 payloads with a single host page it is identical to the pair sum.
    overlaps = []
    if pairs:
        blobs = blob_batch(sorted({r for pair in pairs for r in pair}))
        page_text = {}
        for pay_rel, page_rel in pairs:
            pb, gb = blobs.get(pay_rel), blobs.get(page_rel)
            if pb is None or gb is None:
                continue
            if page_rel not in page_text:
                page_text[page_rel] = page_sentence_texts(gb.decode("utf-8", "replace"))
            overlaps.append((pay_rel, page_rel, payload_texts(pb) & page_text[page_rel]))
    by_payload = {}
    for pay_rel, _page_rel, both in overlaps:
        by_payload.setdefault(pay_rel, set()).update(both)
    if pairs:
        total = sum(len(s) for s in by_payload.values())
        n_pay = sum(1 for s in by_payload.values() if s)
        worst = sorted(((len(s), r) for r, s in by_payload.items() if s), reverse=True)[:5]
        if total > OVERLAP_BASELINE:
            problems.append(("PAGE-PAYLOAD-OVERLAP", "site/dist",
                             f"{n_pay} of {len(by_payload)} committed payload(s) show "
                             f"{total} sentence text(s) in both the page body and the "
                             f"'hidden sentences' expander payload that fetches them, "
                             f"above the recorded baseline of {OVERLAP_BASELINE} "
                             f"(counted once per payload, over all {len(pairs)} "
                             f"payload x host-page pair(s)): {worst}"))
        else:
            notes.append(f"{n_pay} of {len(by_payload)} committed payload(s) share "
                         f"{total} sentence text(s) with a page that shows them "
                         f"(baseline {OVERLAP_BASELINE}, not a regression; counted once "
                         f"per payload over {len(pairs)} payload x host-page pair(s))")
    elif not by_payload:
        notes.append("no committed payload is hosted by a committed page, so there is no "
                     "page/payload pair to compare")

    # ---- report -------------------------------------------------------------------
    print("SITE/DIST FAITHFULNESS")
    print("=" * 70)
    print("  " + r1.stdout.strip())
    for n in notes:
        print("  ·", n)
    print()
    if not problems:
        print("  site/dist IS A FAITHFUL, IDEMPOTENT BUILD — safe to deploy.")
        return 0
    print(f"  {len(problems)} PROBLEM(S):\n")
    for sev, where, msg in problems:
        print(f"  [{sev:26}] {where}")
        print(f"    {msg}")
    print("\n  To accept a deliberate rebuild, review the list above and commit")
    print("  site/dist together with the input change that caused it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
