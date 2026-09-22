#!/usr/bin/env python3
"""Test 1 — is every sitting's data complete against the SOURCE, not against itself?

WHY THIS FILE EXISTS
--------------------
Every other gate in this repo checks our artifacts against our other artifacts:

    check_selection.py   summaries/<year>/  against pipeline/dataset/<year>/
    check_year.py        briefs            against pipeline/dataset/
    check_artifacts.py   docs              against files on disk

If the dataset is incomplete or misparsed, **all of them still pass**, because they
never look at Hansard. That is not hypothetical: a `&nbsp;`-prefix and a `<strong>`-split
parser bug silently merged one speaker's words into the previous speaker's turn, and no
gate caught it (see `docs/card-verification-chain.md`, `scraper/hansard_parse.py`).

So this tool fetches the sitting from the live portal AT TEST TIME — no cache, no reuse
of our stored copy — and compares.

TWO LEVELS, because they fail for different reasons and one is not a proxy for the other

    TEST 1a REPORTS    did we collect every report the source declares for this day?
                       Catches a truncated or under-swept fetch.
    TEST 1b SENTENCES  is every turn we stored present in the freshly pulled record,
                       with the same speaker and the same text?
                       Catches a parser that dropped or merged content.

    TEST 2 EXTRACTION  (only when the dataset is present) does the dataset carry every
                       sentence the sitting yields, with the same ids?
                       Catches a stale dataset, which is the failure that actually
                       corrupted 2016: 24 briefs were built before a dataset rebuild
                       and their ids shifted by one.

WHAT "A FRESH PULL" MEANS HERE, AND WHY IT IS NOT JUST `fetch_sitting(day)`
--------------------------------------------------------------------------
A re-fetch is NOT reproducible at the report level, and that is measured, not assumed.
Three live pulls of 2026-08-05 on 2026-09-21 returned **169, 159 and 165** reports
against a declared `maxResult` of 167, with different ids missing each time — the
documented `maxResult` load-balancing (`parsnips_fetch.py`, module docstring gotcha 2).
The four `written-answer-na-*` ids in the first pull were absent from our stored file;
the ten `written-answer-2411x` ids in our stored file were absent from the second.

A checker that compared `fetch_sitting(day)` against the stored file would therefore
FAIL on a correct archive, and its failures would be indistinguishable from real ones.
So the reference set is:

    the fresh enumeration           (what the source says exists TODAY)
  ∪ every report id we already know (each fetched by id, which IS stable)

and a report is only reported MISSING when the source itself cannot produce it.
`fetch_report("no-such-report-99999")` returns an empty payload rather than erroring, so
"the source does not have it" is detectable and is reported as extra-content-we-hold
rather than as a source gap.

WHY THIS NEEDS NO MODEL, AND WHY THE CARD ASKED FOR ONE ANYWAY
-------------------------------------------------------------
Levels 1a/1b/2 are mechanical: ids, set membership and text equality. That is the point
— `docs/3-lessons-learned.md` §5: minimise the surface that needs an oracle.

The card's secondary check exists for a different failure: a deterministic checker that
reports PASS while doing nothing looks exactly like one that works. So the negative
control is a first-class feature here:

    python3 tools/verify_sitting.py --selftest
      plants four known defects (drop a report, drop a turn, merge two turns, alter one
      word) into an in-memory copy of a real sitting and asserts this tool FAILS each
      one. If the checker ever stops catching them, --selftest fails and says so.

`tools/test_deterministic.py` runs that selftest, this checker, and an independent
verifier model whose output is recorded beside the deterministic verdict.

USAGE
-----
    python3 tools/verify_sitting.py 2026-01-12
    python3 tools/verify_sitting.py --year 2026
    python3 tools/verify_sitting.py --all --workers 8
    python3 tools/verify_sitting.py --selftest
    python3 tools/verify_sitting.py --year 2026 --json --out /tmp/t1.json

Exit code is 0 only when every level passes for every sitting checked. Partial runs are
reported as PARTIAL, never as PASS (card NOT-DONE-WHEN 5).
"""

import argparse
import glob
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))
sys.path.insert(0, os.path.join(ROOT, "summariser"))

import parsnips_fetch as P          # noqa: E402
import storage                      # noqa: E402

DATA = os.environ.get("PARSNIPS_DATA", os.path.join(ROOT, "data"))
# The dataset is gitignored (270 MB, deterministically regenerated), so a worktree does
# not have one. It is only needed for level 2, and level 2 is skipped — never silently
# passed — when this directory has no shard for the year.
DATASET = os.environ.get("PARSNIPS_DATASET", os.path.join(ROOT, "pipeline", "dataset"))

# How a sitting's sentences are derived, so the check can work without the dataset.
# Imported rather than retyped: if the summariser's floor or the summarisable groups
# change, this check must change with it or it starts comparing the wrong things.
MIN_WORDS = storage.MIN_SUMMARISABLE_WORDS
GROUPS = storage.SUMMARISABLE_GROUPS

# A turn's text, as it is compared. NFKC folds the ligatures and full-width forms the
# source uses; whitespace is collapsed because the source mixes tabs and non-breaking
# spaces into the SAME sentence ("In the case \t\tPage: 10 of PCP, we do track after
# that") and the archive normalises some of them on the way in. Whitespace-only
# differences are reported separately from text differences: the first is a
# normalisation decision, the second is a lost or altered sentence.
_WS = re.compile(r"\s+")


def norm_text(t):
    """Comparison form of a piece of text. See the note above on why not byte-exact."""
    t = unicodedata.normalize("NFKC", t or "")
    t = t.replace("\u00a0", " ")
    return _WS.sub(" ", t).strip()


# --------------------------------------------------------------------------- loading

def sitting_path(day):
    return os.path.join(DATA, str(day)[:4], f"sitting_{day}.json")


def load_stored(day):
    p = sitting_path(day)
    if not os.path.exists(p):
        return None, p
    return storage.read_json(p), p


def all_sitting_dates(years=None):
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, "20*", "sitting_*.json"))):
        day = os.path.basename(p)[len("sitting_"):-len(".json")]
        if years and day[:4] not in years:
            continue
        out.append(day)
    return out


# ----------------------------------------------------------------------- fresh pull

def fresh_pull(day, known_ids=(), workers=8, verbose=True):
    """Pull this sitting from the live portal. No cache is consulted.

    Returns (reports_by_id, coverage, notes) where each report carries its freshly
    parsed turns, plus the ids the source could not produce.
    """
    notes = []
    t0 = time.time()
    enum, coverage = P.enumerate_sitting_reports(day)
    if verbose:
        print(f"    enumerated {len(enum)} report(s) for {day} in {time.time() - t0:.0f}s"
              f"  (source declares {coverage.get('max_result')})", flush=True)

    # Union with what we already know about. Without this, the load-balanced
    # enumeration flakiness described in the module docstring shows up as dozens of
    # false "missing" reports on a perfectly good archive.
    wanted = sorted(set(enum) | set(known_ids))
    by_enum_only = sorted(set(wanted) - set(enum))

    def one(rid):
        try:
            return rid, P.fetch_report(rid) or {}
        except Exception as exc:                        # noqa: BLE001
            return rid, {"__error__": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = dict(pool.map(one, wanted))

    reports, unobtainable, errored = {}, [], []
    for rid in wanted:
        meta = fetched.get(rid) or {}
        if meta.get("__error__"):
            errored.append((rid, meta["__error__"]))
        content = meta.get("content") or ""
        if not content:
            unobtainable.append(rid)
            continue
        turns = P.parse_report_turns(content)
        reports[rid] = {
            "report_id": rid,
            "title": (meta.get("title") or (enum.get(rid) or {}).get("title") or "").strip(),
            "report_type": meta.get("reportType"),
            "group": P.group_for(meta.get("reportType"), rid),
            "turns": turns,
            "words": sum(t["words"] for t in turns),
            "id_from_enumeration": rid in enum,
        }

    if errored:
        notes.append(f"{len(errored)} report fetch(es) errored, e.g. {errored[0][0]}: "
                     f"{errored[0][1][:90]}")
    if by_enum_only:
        notes.append(f"{len(by_enum_only)} id(s) we hold were not returned by the fresh "
                     f"enumeration and were fetched by id instead "
                     f"(e.g. {', '.join(by_enum_only[:3])})")
    if coverage.get("max_result") and len(enum) < coverage["max_result"]:
        notes.append(f"the fresh enumeration itself is short: {len(enum)} of "
                     f"{coverage['max_result']} declared (maxResult is load-balanced; "
                     f"this is a source-side condition, not proof of a local gap)")
    return reports, coverage, notes, unobtainable


def _report_lookup(stored, day):
    """Find a report dict by id, from this sitting or any other sitting on disk.

    Needed because a dataset item can span sitting days, and its sids are numbered over
    the WHOLE item. Deriving an item from only this day's reports would renumber the
    later day's sids and manufacture findings; so the item is always rebuilt in full and
    filtered afterwards.
    """
    cache = {}

    def get(rid):
        if rid in cache:
            return cache[rid]
        cache[rid] = None
        for r in stored.get("reports") or []:
            if r.get("report_id") == rid:
                cache[rid] = r
                return r
        for p in sorted(glob.glob(os.path.join(DATA, "20*", "sitting_*.json"))):
            if os.path.basename(p) == f"sitting_{day}.json":
                continue
            other = storage.read_json(p) or {}
            for r in other.get("reports") or []:
                if r.get("report_id") == rid:
                    cache[rid] = r
                    return r
        return None

    return get


def derived_sentences(day, stored, report_groups=None):
    """The sids THIS SITTING yields, computed with the dataset builder's own code.

    **Sentence ids are per ITEM, not global.** `build_dataset.sid(n)` numbers sentences
    within one `build_item` call, so `s00001` exists in every item of the corpus. Keying a
    whole sitting's sentences by sid in one flat dict silently collapses items onto each
    other — that mistake was made in the first version of this function, and produced 2,667
    sentences where the dataset holds 1,311 for the same reports, plus 1,180 "text
    mismatches" that were really `s00001` of one item against `s00001` of another.

    `report_groups` is the item grouping to use, as a list of report-id lists. The
    authority for that grouping is `pipeline/dataset/index.json`, because the dataset
    builder merges reports into items by title and day-cluster
    (`summarise.summarisable_items`) — `bill-773`, `bill-774` and `bill-775` are ONE item,
    so grouping per report invents items the dataset never had. When `report_groups` is
    None (the selftest, which runs with no dataset present) each summarisable report is
    its own group, which is enough to exercise the comparison.

    Returns {item_key: {sid: {text, speaker, attributed, report_id, turn_index}}}, keyed
    by `storage.stable_key(report_ids)` — the same key the dataset files use.
    """
    import build_dataset as BD

    want = [r for r in stored.get("reports") or []
            if r.get("group") in GROUPS and int(r.get("words") or 0) >= MIN_WORDS]
    if report_groups is None:
        report_groups = [[r["report_id"]] for r in want]
    here = {r["report_id"] for r in stored.get("reports") or []}
    lookup = _report_lookup(stored, day)

    items = {}
    for group in report_groups:
        reports = [lookup(rid) for rid in group]
        reports = [r for r in reports if r]
        if not reports:
            continue
        key = storage.stable_key([r["report_id"] for r in reports])
        item = {
            "key": key, "title": reports[0].get("title") or "",
            "group": reports[0].get("group") or "other",
            "report_ids": [r["report_id"] for r in reports],
            "sitting_dates": sorted({r.get("sitting_date") or day for r in reports}),
            "words": sum(int(r.get("words") or 0) for r in reports),
            "reports": reports,
        }
        payload = BD.build_item(item, 1)
        speakers = payload["speakers"]
        sents = {}
        for turn in payload["turns"]:
            if turn["r"] not in here:
                continue          # the item's other sitting day owns this sentence
            spk = speakers[turn["spk"]] if turn["spk"] < len(speakers) else ""
            for i, sid in enumerate(turn["sid"]):
                sents[sid] = {"text": turn["text"][i], "speaker": spk,
                              "attributed": turn["attr"], "report_id": turn["r"],
                              "turn_index": turn["t"]}
        items[key] = sents
    return items


def dataset_items(stored, item_index):
    """What `pipeline/dataset/<year>/` holds, keyed the way `derived_sentences` keys it.

    An item can span sitting days (a Budget debate running across two days), and its
    sentences for the OTHER day belong to that day's check — so only the sentences whose
    `report_id` is one of this sitting's are returned. The counts are reported separately
    so a reader can see the item was not read as a whole.
    """
    import build_dataset as BD

    day = stored["date"]
    year = str(day)[:4]
    here = {r["report_id"] for r in stored.get("reports") or []}
    out, items_used, missing_items, spanning = {}, [], [], []
    for it in item_index:
        if it.get("year") != year:
            continue
        ids = set(it.get("report_ids") or [])
        if not (ids & here):
            continue
        path = os.path.join(DATASET, year, f"{it['id']}.json")
        if not os.path.exists(path):
            missing_items.append(it["id"])
            continue
        sents = BD.load_item(path) or []
        items_used.append(it["id"])
        if ids - here:
            spanning.append({"item": it["id"],
                             "reports_elsewhere": sorted(ids - here)})
        out[it["id"]] = {
            s["sid"]: {"text": s["text"], "speaker": s["speaker"],
                       "attributed": s["attributed"], "report_id": s["report_id"],
                       "turn_index": s["turn_index"]}
            for s in sents if s["report_id"] in here
        }
    return out, items_used, missing_items, spanning


# ------------------------------------------------------------------------ comparison

def _partition_ok(content_gone, empty_gone, not_obtained, gone):
    """Do the three `gone` categories partition `gone` exactly?

    Pairwise disjoint, and their union is `gone` — so every row we held and did not get is
    in exactly one category, and no row is double-counted. Returns True on an empty `gone`.
    """
    a = {r["report_id"] for r in content_gone}
    b = set(empty_gone)
    c = set(not_obtained)
    if a & b or a & c or b & c:
        return False
    return (a | b | c) == set(gone)


def compare_reports(stored, fresh, unobtainable, coverage, source_lookup=None):
    """Level 1a — reports we hold but the source cannot produce, and vice versa.

    Two different questions, reported separately because conflating them made 22 of 23
    correct 2026 sittings FAIL on the first full run:

      COMPLETENESS   do we hold every report that carries CONTENT, and did the fresh pull
                     obtain every report we hold? This is the question the level exists to
                     answer, and it is what decides `passed`.
      SANITY         is the archive's own report bookkeeping self-consistent? An id we hold
                     as a 0-word placeholder and the source will not serve is a row the
                     ingestion path invented (`backfill.fetch_one` appends a row for every
                     id the enumeration returns, counting the content failures in
                     `empty_reports`). That is a real defect — the archive claims a report
                     it never got — but it is NOT lost content, and it has its own flag
                     (`sanity_ok`) rather than failing the completeness verdict.

    Measured 2026-09-21 across 2026: 47 placeholder rows and 0 rows with content missing,
    every placeholder being an `attendance`, `ptba` or `atbp` procedural entry. The source
    returns no content for those at all, so failing on them would have reported 22/23
    correct sittings as broken — a check that cannot distinguish "we lost text" from
    "the source has no text here" is not usable as a gate.

    THE DISCRIMINATOR IS WHAT THE **SOURCE** HAS, NOT WHAT WE HOLD. Keying the categories on
    our own word count made a real gap disappear: `motion-1901` (2022-03-10) is stored with
    0 words, but the source serves a 36-word procedural entry whose markup
    (`<h6>[(proc text) Resolved, ...]</h6>`) yields 0 turns — so it looked exactly like an
    `attendance` row. It is in fact lost content: the source has text for it and we have
    none. Every `empty_` row is therefore checked against the source, and one where the
    source has words is `unparsed_source_content` and FAILS the level.
    """
    held = {r["report_id"]: r for r in stored.get("reports") or []}
    got = set(fresh)
    gone = sorted(set(held) - got)
    unobtainable = set(unobtainable)
    empty_gone, content_gone = [], []
    for rid in gone:
        if rid not in unobtainable:
            continue
        r = held[rid]
        if int(r.get("words") or 0) or (r.get("turns") or []):
            content_gone.append({"report_id": rid, "words": r.get("words"),
                                 "turns": len(r.get("turns") or [])})
        else:
            empty_gone.append(rid)
    not_obtained = sorted(rid for rid in gone if rid not in unobtainable)
    never_collected = sorted(got - set(held))

    # ---------------------------------------------------------------- source-side check
    # One extra request per empty-turns row we hold (0-turn rows are rare: 512 archive-wide,
    # and 47 in 2026). Without this, a row the source DOES have text for is misreported as
    # bookkeeping and the gap it represents is never surfaced by any level.
    unparsed, still_empty = [], []
    lookup = source_lookup or P.fetch_report
    for rid in sorted(held):
        r = held[rid]
        if int(r.get("words") or 0) or (r.get("turns") or []):
            continue
        try:
            payload = lookup(rid)
        except Exception:                               # noqa: BLE001
            unparsed.append({"report_id": rid, "checked": False,
                             "why": "the source could not be reached for this id"})
            continue
        raw = (payload or {}).get("content") or ""
        words = len(raw.split())
        if not raw.strip():
            still_empty.append(rid)
        else:
            # The source serves text. Either we lost it, or the parser cannot read this
            # markup — the stored row is empty either way, so it is a FAILURE, but the two
            # need different repairs and are named differently.
            turns = 0
            try:
                turns = len(P.parse_report_turns(raw))
            except Exception:                           # noqa: BLE001
                pass
            unparsed.append({"report_id": rid, "source_words": words,
                             "source_turns": turns,
                             "cause": ("source has text, we hold none — LOST"
                                       if turns else
                                       "source has text the parser yields no turns from "
                                       "(e.g. a `<h6>` procedural entry) — the archive "
                                       "holds an empty row for readable text")})

    # The arithmetic that must balance, stated so it can be CHECKED rather than inferred
    # by a reader:
    #     fresh = stored − gone + never_collected
    #     gone  = with_content + empty_placeholder + not_obtained
    # A level whose own categories do not partition is itself defective. Both sides are
    # recomputed here from the raw sets rather than trusted from the lists above.
    gone_check = len(content_gone) + len(empty_gone) + len(not_obtained)
    expected_fresh = len(held) - gone_check + len(never_collected)
    reconciliation_ok = (gone_check == len(gone) and expected_fresh == len(got))

    complete = not content_gone and not not_obtained and not never_collected
    sanity_ok = not empty_gone
    # Rows the source has text for but we hold empty are a FAILURE of the level, because the
    # source demonstrably has content this archive does not. This is the `motion-1901` hole.
    return {
        "stored": len(held),
        "fresh": len(got),
        "fresh_enumeration": coverage.get("collected"),
        "declared": coverage.get("max_result"),
        "missing_with_content": content_gone,
        "missing_empty_placeholder": empty_gone,
        "placeholder_source_checked": len(still_empty),
        "unparsed_source_content": unparsed,
        "not_obtained": not_obtained,
        "never_collected": never_collected,
        "reconciliation_ok": reconciliation_ok,
        # The partition is named in full, because a reviewer reading "stored 136 − gone 2
        # + never_collected 4" cannot tell what the 2 gone rows WERE, and a live reviewer
        # duly asked whether `gone` should equal the placeholder count. It should not —
        # `gone` is partitioned three ways — so the string says so rather than leaving the
        # reader to guess at an identity that does not hold.
        "reconciliation": (
            f"fresh {len(got)} = stored {len(held)} − gone {gone_check} "
            f"+ never_collected {len(never_collected)}"
            f"   [gone = {len(content_gone)} with content + {len(empty_gone)} empty "
            f"placeholder + {len(not_obtained)} not obtained]"
            + ("" if reconciliation_ok else "  <-- DOES NOT BALANCE")),
        # The partition must actually partition: the three `gone` categories have to be
        # pairwise disjoint and together account for every id in `gone`. Written as a real
        # check over real sets — a first attempt at this line iterated an empty sequence
        # and so asserted nothing while looking authoritative.
        "partition_ok": _partition_ok(content_gone, empty_gone, not_obtained, gone),
        "sanity_ok": sanity_ok,
        "passed": (complete and reconciliation_ok
                   and _partition_ok(content_gone, empty_gone, not_obtained, gone)
                   and not any(u.get("source_words") for u in unparsed)),
    }


def compare_turns(stored, fresh):
    """Level 1b — per report, is every stored turn in the fresh record and identical?

    THREE COMPARISONS, because they fail for different reasons:

      CONTAINMENT  is each stored turn's text present as a turn of the fresh record at
                   all? A turn the parser merged into its neighbour is here — this is the
                   `&nbsp;`/`<strong>` defect that published the Minister's reply under the
                   questioner's name (parsnips_fetch.parse_turns, note 1).
      SEQUENCE     are the two turn lists identical, in order, one for one? A report whose
                   text shifted by one turn, or whose count differs, is here.
      SPEAKERS     do the two lists name the same speaker for each turn? A wrong speaker is
                   a defect this project has actually published.

    **Bracket asides are compared on the text the PIPELINE uses, not the raw source.** The
    source revises published answers in place: `written-answer-22754` gained
    `[Please refer to "Clarification by Minister for Social and Family Development", Official
    Report, 8 September 2026 ...]` mid-turn after our copy was taken. `summarise.
    strip_speaker_labels` removes exactly that construction by design, so the dataset is
    unaffected — but a raw-text comparison reported a correct sitting as FAIL. Every
    comparison here therefore also runs on stripped text, and a raw difference that vanishes
    under stripping is reported as `aside_differences` (informational), not as a defect. The
    stripper is imported, not reimplemented, so this cannot drift from the pipeline's rule.
    """
    import summarise as S

    def strip(t):
        return norm_text(S.strip_speaker_labels(t or ""))

    missing_turns, mismatched, speaker_diff, asides = [], [], [], []
    n_turns = 0
    for rid in sorted({r["report_id"] for r in stored.get("reports") or []} & set(fresh)):
        a = next(r for r in stored["reports"] if r["report_id"] == rid)
        b = fresh[rid]
        ta, tb = a.get("turns") or [], b.get("turns") or []
        n_turns += len(ta)
        pa = [norm_text(t.get("text")) for t in ta]
        pb = [norm_text(t.get("text")) for t in tb]
        qa = [strip(t.get("text")) for t in ta]
        qb = [strip(t.get("text")) for t in tb]
        joined_qb = "\n".join(qb)
        for t, q in zip(ta, qa):
            if q and q not in joined_qb:
                missing_turns.append({"report_id": rid, "speaker": t.get("speaker"),
                                      "text": q[:220]})
        if pa != pb:
            if qa == qb:
                # The only difference is a bracket aside the pipeline strips. Record it,
                # do not fail on it.
                i = next(i for i in range(min(len(pa), len(pb))) if pa[i] != pb[i])
                asides.append({"report_id": rid, "turn": i,
                               "stored_len": len(pa[i]), "fresh_len": len(pb[i]),
                               "note": "differs only by a bracket aside the pipeline "
                                       "strips; the dataset is unaffected"})
            else:
                mismatched.append({"report_id": rid, "stored_turns": len(ta),
                                   "fresh_turns": len(tb),
                                   "first_diff": _first_diff(qa, qb)})
        sa = [norm_text(t.get("speaker")) for t in ta]
        sb = [norm_text(t.get("speaker")) for t in tb]
        if len(sa) == len(sb) and sa != sb:
            i = next(i for i in range(len(sa)) if sa[i] != sb[i])
            speaker_diff.append({"report_id": rid, "index": i,
                                 "stored": sa[i][:90], "fresh": sb[i][:90]})
    return {
        "turns_checked": n_turns,
        "reports_compared": len({r["report_id"] for r in stored.get("reports") or []}
                                & set(fresh)),
        "missing_turns": missing_turns,
        "turn_count_mismatches": mismatched,
        "speaker_mismatches": speaker_diff,
        "aside_differences": asides,
        "passed": not missing_turns and not mismatched and not speaker_diff,
    }


def _first_diff(a, b):
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return {"index": i, "stored": a[i][:160], "fresh": b[i][:160]}
    if len(a) != len(b):
        i = min(len(a), len(b))
        return {"index": i,
                "stored": (a[i][:160] if i < len(a) else "<absent>"),
                "fresh": (b[i][:160] if i < len(b) else "<absent>")}
    return None


def compare_extraction(stored, derived_items, dataset_by_item):
    """Level 2 — for every item, does the dataset carry every sentence it yields?

    ITEM BY ITEM, because sids are per item (`build_dataset.sid`). A comparison across
    the whole sitting at once compares `s00001` of one item against `s00001` of another
    and manufactures findings; that mistake was made here once already.

    Three findings, each separately actionable:

      missing_items       the item is in the committed index but its file is not on disk
      missing_sids        the dataset has fewer sentences than the sitting yields
      text_mismatches     same sid, different text — the shape of a stale dataset

    `extra_sids` is reported but is NOT a failure on its own: an item can span two sitting
    days, so a sid belonging to the other day is legitimately present in a file that this
    day only partly owns. `spanning` names those items.
    """
    missing_files = []
    missing_sids, extra_sids, text_diff = [], [], []
    per_item = []
    for key in sorted(set(derived_items) | set(dataset_by_item)):
        d = derived_items.get(key)
        s = dataset_by_item.get(key)
        if d is None:
            missing_files.append(key)
            continue
        if s is None:
            # The sitting yields sentences for an item the dataset has no file for.
            missing_files.append(key)
            continue
        miss = sorted(set(d) - set(s))
        extra = sorted(set(s) - set(d))
        diffs = [sid for sid in sorted(set(d) & set(s))
                 if norm_text(d[sid]["text"]) != norm_text(s[sid]["text"])]
        per_item.append({"item": key, "derived": len(d), "dataset": len(s),
                         "missing": len(miss), "extra": len(extra),
                         "text_diffs": len(diffs)})
        for sid in miss[:4]:
            missing_sids.append({"item": key, "sid": sid,
                                 "report_id": d[sid]["report_id"],
                                 "speaker": d[sid]["speaker"],
                                 "text": norm_text(d[sid]["text"])[:200]})
        if len(miss) > 4:
            missing_sids.append({"item": key, "sid": f"... {len(miss) - 4} more"})
        for sid in extra[:2]:
            extra_sids.append({"item": key, "sid": sid})
        for sid in diffs[:4]:
            text_diff.append({"item": key, "sid": sid, "report_id": d[sid]["report_id"],
                              "derived": norm_text(d[sid]["text"])[:200],
                              "dataset": norm_text(s[sid]["text"])[:200]})
        if len(diffs) > 4:
            text_diff.append({"item": key, "sid": f"... {len(diffs) - 4} more"})
    return {
        "items_compared": len(per_item),
        "derived": sum(len(v) for v in derived_items.values()),
        "dataset": sum(len(v) for v in dataset_by_item.values()),
        "missing_files": missing_files,
        "missing_sids": missing_sids,
        "extra_sids": extra_sids,
        "text_mismatches": text_diff,
        "per_item": sorted(per_item, key=lambda r: -(r["missing"] + r["text_diffs"]))[:12],
        "passed": not missing_files and not missing_sids and not text_diff,
    }


def detail_missing(derived, sids, limit=8):
    """Give a missing sid enough context to locate the gap, not just its id.

    `derived` may be either shape: a flat {sid: rec} or the item-keyed
    {item: {sid: rec}}. Both are accepted so this stays usable from either side.
    """
    out = []
    flat = derived
    if derived and all(isinstance(v, dict) and "text" not in v for v in derived.values()):
        flat = {}
        for item, sents in derived.items():
            for sid, rec in sents.items():
                flat[f"{item}/{sid}"] = {**rec, "item": item}
    for sid in sids[:limit]:
        d = flat.get(sid) or {}
        out.append({"sid": sid, "item": d.get("item"), "report_id": d.get("report_id"),
                    "speaker": d.get("speaker"), "text": norm_text(d.get("text"))[:220]})
    return out


# ---------------------------------------------------------------------------- report

def item_grouping(stored, day, index):
    """The item grouping for this sitting's reports, taken from the dataset index.

    Without an index, fall back to the dataset builder's own algorithm
    (`summarise.summarisable_items`) restricted to this sitting, so the grouping is
    derived rather than assumed. The fallback is a second implementation of a rule the
    index already states, and the two disagreeing is itself a finding — so the returned
    grouping is tagged with which source produced it.
    """
    here = {r["report_id"] for r in stored.get("reports") or []}
    if index:
        groups = [list(it.get("report_ids") or []) for it in index
                  if it.get("year") == str(day)[:4]
                  and (set(it.get("report_ids") or []) & here)]
        return groups, "dataset index"

    import summarise as S
    # A single-sitting view, because an item spanning days needs its other day's reports
    # to be built in full — `_report_lookup` supplies those for the sentences, but the
    # grouping itself only needs to know which reports belong together here.
    one = [{"date": day, "reports": stored.get("reports") or []}]
    items = S.summarisable_items(one)
    return [list(it["report_ids"]) for it in items], "summariser grouping (no index)"


def check_sitting(day, workers=8, verbose=True):
    """Run every level for one sitting. Returns a result dict; never raises for a data
    problem, only for a tool problem (network, missing archive file)."""
    stored, path = load_stored(day)
    if stored is None:
        return {"date": day, "ok": False, "levels": {},
                "error": f"no stored sitting at {path}"}

    res = {"date": day, "path": path, "ok": True, "levels": {}, "notes": []}
    if verbose:
        print(f"  {day}  stored {len(stored.get('reports') or [])} reports, "
              f"{sum(int(r.get('words') or 0) for r in stored.get('reports') or [])} words",
              flush=True)

    known = [r["report_id"] for r in stored.get("reports") or []]
    fresh, coverage, notes, unobtainable = fresh_pull(day, known, workers=workers,
                                                     verbose=verbose)
    res["notes"].extend(notes)
    res["fresh"] = {"reports": len(fresh), "declared": coverage.get("max_result"),
                    "enumeration": coverage.get("collected"),
                    "enumeration_mode": coverage.get("enumeration")}

    l1a = compare_reports(stored, fresh, unobtainable, coverage)
    res["levels"]["1a_reports"] = l1a

    l1b = compare_turns(stored, fresh)
    res["levels"]["1b_sentences"] = l1b

    year = str(day)[:4]
    year_dir = os.path.join(DATASET, year)
    has_dataset = bool(os.path.isdir(year_dir)
                       and glob.glob(os.path.join(year_dir, "*.json")))
    index = []
    if has_dataset:
        index = ((storage.read_json(os.path.join(DATASET, "index.json")) or {})
                 .get("items") or [])
    grouping, grouping_source = item_grouping(stored, day, index)
    res["grouping_source"] = grouping_source
    derived = derived_sentences(day, stored, report_groups=grouping)
    res["derived_sentences"] = sum(len(v) for v in derived.values())
    res["derived_items"] = len(derived)
    if has_dataset:
        from_dataset, items_used, missing_items, spanning = dataset_items(stored, index)
        l2 = compare_extraction(stored, derived, from_dataset)
        l2["items_used"] = len(items_used)
        l2["items_missing_from_disk"] = missing_items
        l2["spanning_items"] = spanning
        if missing_items:
            l2["passed"] = False
        res["levels"]["2_extraction"] = l2
    else:
        # Never a silent pass. The dataset is a gitignored build artifact; when it is
        # absent (a fresh worktree) that is a SKIP with a reason, and the run is
        # reported as PARTIAL.
        res["levels"]["2_extraction"] = {
            "skipped": True, "passed": None,
            "reason": f"no dataset shard at {year_dir} — level 2 not run "
                      f"(set PARSNIPS_DATASET if it lives elsewhere)",
        }

    for name, lv in res["levels"].items():
        if lv.get("passed") is False:
            res["ok"] = False
    res["partial"] = any(lv.get("passed") is None for lv in res["levels"].values())
    return res


def print_result(res):
    day = res["date"]
    if res.get("error"):
        print(f"TEST 1 {day}  ERROR  {res['error']}")
        return
    print(f"  --- {day} ---")
    l1a = res["levels"]["1a_reports"]
    state = "PASS" if l1a["passed"] else "FAIL"
    print(f"    TEST 1a REPORTS    {state}  (stored {l1a['stored']}, freshly "
          f"obtained {l1a['fresh']}, source declares {l1a['declared']})")
    print(f"        reconciled: {l1a['reconciliation']}")
    if not l1a.get("partition_ok", True):
        print(f"        CATEGORY LEAK: the 'gone' categories overlap or leave a report "
              f"uncounted — the counts below double-count or omit a report")
    if not l1a["sanity_ok"]:
        print(f"        SANITY: {len(l1a['missing_empty_placeholder'])} row(s) we hold "
              f"with no content and the source will not serve — a report count the source "
              f"does not support (not lost text; see docs/completeness-check.md)")
    for label, key in (("report we hold with content, that the source returns nothing for",
                        "missing_with_content"),
                       ("report we hold that the fresh pull did not obtain",
                        "not_obtained"),
                       ("report the source has that we never collected",
                        "never_collected"),
                       ("row we hold as an empty placeholder, that the source returns "
                        "nothing for", "missing_empty_placeholder"),
                       ("row we hold EMPTY, but the source serves text for — the source "
                        "has content we do not", "unparsed_source_content")):
        ids = l1a[key]
        if ids:
            print(f"        {label}: {len(ids)}")
            for rid in ids[:8]:
                if isinstance(rid, dict):
                    extra = (f"  source has ~{rid['source_words']} words "
                             f"({rid['source_turns']} turns)") if rid.get("source_words") \
                        else ""
                    print(f"          - {rid['report_id']}  (we hold "
                          f"{rid.get('words', 0)} words in "
                          f"{rid.get('turns', 0)} turn(s)){extra}")
                    if rid.get("cause"):
                        print(f"            {rid['cause']}")
                else:
                    print(f"          - {rid}")
            if len(ids) > 8:
                print(f"          ... {len(ids) - 8} more")

    l1b = res["levels"]["1b_sentences"]
    state = "PASS" if l1b["passed"] else "FAIL"
    print(f"    TEST 1b SENTENCES  {state}  ({l1b['turns_checked']} turns over "
          f"{l1b['reports_compared']} reports: {len(l1b['missing_turns'])} missing, "
          f"{len(l1b['turn_count_mismatches'])} mismatched, "
          f"{len(l1b['speaker_mismatches'])} wrong speaker)")
    if l1b.get("aside_differences"):
        print(f"        note: {len(l1b['aside_differences'])} report(s) differ only by a "
              f"bracket aside the pipeline strips — the dataset is unaffected "
              f"(e.g. {l1b['aside_differences'][0]['report_id']})")
    for t in l1b["missing_turns"][:5]:
        print(f"        - MISSING TURN {t['report_id']} [{t['speaker']}]: {t['text'][:120]}")
    for m in l1b["turn_count_mismatches"][:5]:
        fd = m["first_diff"] or {}
        print(f"        - TEXT DIFF {m['report_id']} (stored {m['stored_turns']} turns, "
              f"fresh {m['fresh_turns']}) at turn {fd.get('index')}")
        print(f"            stored: {(fd.get('stored') or '')[:150]}")
        print(f"            fresh : {(fd.get('fresh') or '')[:150]}")
    for s in l1b["speaker_mismatches"][:5]:
        print(f"        - SPEAKER DIFF {s['report_id']} turn {s['index']}: "
              f"stored {s['stored']!r} vs fresh {s['fresh']!r}")

    l2 = res["levels"]["2_extraction"]
    if l2.get("skipped"):
        print(f"    TEST 2  EXTRACTION SKIP  ({l2['reason']})")
    else:
        state = "PASS" if l2["passed"] else "FAIL"
        print(f"    TEST 2  EXTRACTION {state}  ({l2['items_compared']} item(s): derived "
              f"{l2['derived']} sentences, dataset {l2['dataset']}; "
              f"{len(l2['missing_sids'])} missing, {len(l2['extra_sids'])} extra, "
              f"{len(l2['text_mismatches'])} text diffs, "
              f"{len(l2['missing_files'])} item file(s) absent)")
        for it in l2["per_item"][:6]:
            if it["missing"] or it["text_diffs"]:
                print(f"        - {it['item']}: derived {it['derived']} vs dataset "
                      f"{it['dataset']}  ({it['missing']} missing, {it['text_diffs']} "
                      f"text diffs)")
        for d in l2["missing_sids"][:5]:
            if "report_id" in d:
                print(f"        - MISSING {d['item']} {d['sid']} {d['report_id']} "
                      f"[{d['speaker']}]: {d['text'][:110]}")
            else:
                print(f"        -        {d['item']} {d['sid']}")
        for s in l2["extra_sids"][:3]:
            print(f"        - EXTRA   {s['item']} {s['sid']} (spans sitting days)")
        for s in l2["text_mismatches"][:3]:
            print(f"        - TEXT    {s['item']} {s['sid']} {s.get('report_id')}")
            print(f"            derived: {s['derived'][:130]}")
            print(f"            dataset: {s['dataset'][:130]}")
        for k in l2["missing_files"][:5]:
            print(f"        - NO FILE {k}")
    verdict = "PASS" if res["ok"] else "FAIL"
    if res["ok"] and res.get("partial"):
        verdict = "PASS (PARTIAL — level 2 not run)"
    print(f"    TEST 1 {day} {verdict}")
    for n in res.get("notes") or []:
        print(f"      note: {n}")


# ------------------------------------------------------------------------- selftest

def selftest(day="2026-01-12"):
    """Plant known defects and assert this tool catches every one.

    A gate that has never been observed failing has not been shown to work
    (REQUIREMENTS.md D-1, R-2.9). These are the four plausible ways this corpus has
    actually lost content, each planted into a real sitting held in memory — no
    network is used for the planted side.
    """
    import copy

    stored, path = load_stored(day)
    if stored is None:
        print(f"  selftest needs a stored sitting at {path}", file=sys.stderr)
        return 1

    derived = derived_sentences(day, stored)
    fresh = {r["report_id"]: r for r in stored["reports"]}

    # The source-side placeholder check is stubbed here: the selftest must not touch the
    # network (it plants defects into a sitting held in memory), and the real behaviour is
    # covered by the live runs. `no_source` is the honest default for planted cases: "the
    # source was asked and has nothing".
    def no_source(rid):
        return {"report_id": rid, "content": ""}

    cases = []

    # DIRECTION MATTERS, and the first version of these cases got it backwards. `stored`
    # is what we hold; `fresh` is what the source serves now. An id removed from `stored`
    # is content the source has that we never collected (never_collected); an id removed
    # from `fresh` is something we hold that the source no longer serves (gone). Planting
    # the wrong side makes a case pass for the wrong reason and report the wrong
    # category — which is what happened here, and is exactly the "plausible but wrong"
    # outcome a selftest exists to expose.

    # 1. WE HOLD AN EMPTY PLACEHOLDER the source returns nothing for. This is the live
    #    shape: attendance-12012026 and ptba-12012026 on 2026-01-12 are 0-word rows in
    #    our archive that the source will not produce.
    placeholder = next((r for r in stored["reports"]
                        if not int(r.get("words") or 0) and not (r.get("turns") or [])),
                       None)
    if placeholder:
        pid = placeholder["report_id"]
        l1a = compare_reports(stored, {k: v for k, v in fresh.items() if k != pid}, [pid],
                              {"collected": len(fresh) - 1, "max_result": len(fresh)},
                              source_lookup=no_source)
        cases.append((f"an empty placeholder row the source does not serve ({pid})",
                      (not l1a["sanity_ok"]) and l1a["passed"],
                      f"{len(l1a['missing_empty_placeholder'])} empty placeholder — "
                      f"sanity_ok={l1a['sanity_ok']}, level passed={l1a['passed']} "
                      f"(flagged, not failed)"))
        # 1b. THE SAME ROW, BUT THE SOURCE DOES HAVE TEXT FOR IT. This is the motion-1901
        #     hole: keyed on our own 0-word copy it looked like bookkeeping, and no level
        #     reported it, while the source served a 36-word procedural entry. The
        #     discriminator is the SOURCE's answer, so the same stored row must now FAIL.
        def has_text(rid, _pid=pid):
            return {"report_id": rid,
                    "content": "<h6>[(proc text) Resolved, that the motion be agreed "
                               "to.]</h6>" if rid == _pid else ""}
        l1a2 = compare_reports(stored, {k: v for k, v in fresh.items() if k != pid}, [pid],
                               {"collected": len(fresh) - 1, "max_result": len(fresh)},
                               source_lookup=has_text)
        cases.append((f"a row we hold empty, but the source HAS text for ({pid}) — the "
                      f"motion-1901 shape",
                      not l1a2["passed"],
                      f"{len(l1a2['unparsed_source_content'])} row(s) with source text: "
                      f"{(l1a2['unparsed_source_content'] or [{}])[0].get('source_words')} "
                      f"source words"))
        # 1c. and the check must not fire when the source genuinely has nothing — otherwise
        #     it would fail every attendance/ptba row in the archive.
        cases.append((f"the same row when the source really has nothing ({pid})",
                      l1a["passed"],
                      f"level passed={l1a['passed']} with the source returning no content"))

    # 2. WE HOLD A REPORT WITH CONTENT the source no longer serves. The 0-word variant
    #    above and this one must be distinguishable, or a bookkeeping defect reads as
    #    lost text.
    rich = next((r for r in stored["reports"] if int(r.get("words") or 0) > 0), None)
    if rich:
        l1a = compare_reports(stored, {k: v for k, v in fresh.items()
                                       if k != rich["report_id"]},
                              [rich["report_id"]],
                              {"collected": len(fresh) - 1, "max_result": len(fresh)},
                              source_lookup=no_source)
        cases.append((f"a report with content the source no longer serves "
                      f"({rich['report_id']}, {rich['words']}w)",
                      not l1a["passed"],
                      f"{len(l1a['missing_with_content'])} with content, "
                      f"{len(l1a['missing_empty_placeholder'])} empty placeholder"))

    # 3. A REPORT THE SOURCE HAS THAT WE NEVER COLLECTED — the content gap this whole
    #    check exists to find. Dropped from `stored`, so the source side is unchanged.
    bad = copy.deepcopy(stored)
    dropped = bad["reports"][0]
    bad["reports"] = bad["reports"][1:]
    l1a = compare_reports(bad, fresh, [], {"collected": len(fresh),
                                           "max_result": len(fresh)},
                          source_lookup=no_source)
    cases.append((f"a report the source has that our copy lacks ({dropped['report_id']}, "
                  f"{dropped.get('words')}w)",
                  not l1a["passed"],
                  f"{len(l1a['never_collected'])} never collected: "
                  f"{', '.join(l1a['never_collected'][:2])}"))

    # 4. a report we hold that nothing was fetched for and the source does not list
    skip_id = "written-answer-21219"
    if skip_id not in set(fresh):
        skip_id = sorted(fresh)[-1]
    l1a = compare_reports(stored, {k: v for k, v in fresh.items() if k != skip_id}, [],
                          {"collected": len(fresh) - 1, "max_result": len(fresh)},
                          source_lookup=no_source)
    cases.append((f"a report the fresh pull skipped entirely ({skip_id})",
                  not l1a["passed"], f"{len(l1a['not_obtained'])} not obtained"))

    # 5. the reconciliation itself: a level whose categories do not partition the set of
    #    gone ids must fail even when every finding list is empty. A checker that reports
    #    "no findings" while its own counters do not add up is a checker to distrust.
    l1a = compare_reports(stored, fresh, [], {"collected": len(fresh),
                                              "max_result": len(fresh)},
                          source_lookup=no_source)
    cases.append(("a clean comparison reconciles",
                  l1a["passed"] and l1a["reconciliation_ok"],
                  l1a["reconciliation"]))

    # 6. a turn merged into its neighbour (the &nbsp;/<strong> parser defect)
    rid = next(r["report_id"] for r in stored["reports"] if len(r.get("turns") or []) > 2)
    bad = copy.deepcopy(stored)
    rep = next(r for r in bad["reports"] if r["report_id"] == rid)
    victim = rep["turns"].pop(1)
    rep["turns"][0]["text"] = f"{rep['turns'][0]['text']} {victim['text']}".strip()
    l1b = compare_turns(bad, fresh)
    cases.append((f"a turn merged into its neighbour ({rid})", not l1b["passed"],
                  f"{len(l1b['missing_turns'])} missing turn(s), "
                  f"{len(l1b['turn_count_mismatches'])} text diff(s)"))
    # 4. one word altered inside a turn.
    #
    #    The FIRST version of this case replaced " the " with " teh " and was reported
    #    MISSED. The mutation had not landed: Hansard turn text is normalised on the way
    #    in, and the chosen turn had already been concatenated with its neighbour by case
    #    3, so the token was not there to replace. A mutation that silently does nothing
    #    is indistinguishable from a checker that catches nothing, so this asserts the
    #    text actually changed before it asserts the checker notices.
    bad = copy.deepcopy(stored)
    rep = next(r for r in bad["reports"] if r["report_id"] == rid)
    original = rep["turns"][0]["text"]
    mutated = original.replace(" the ", " teh ", 1)
    if mutated == original:
        mutated = original + " Zzq"          # a suffix cannot fail to land
    rep["turns"][0]["text"] = mutated
    l1b = compare_turns(bad, fresh)
    landed = mutated != original
    cases.append((f"one word altered inside a turn ({rid})",
                  landed and not l1b["passed"],
                  f"mutation landed: {landed}; "
                  f"{len(l1b['turn_count_mismatches'])} text diff(s)"))

    # 5. stale dataset: one sid shifted. This is the 2016 shape — ids renumbered after the
    #    briefs were built — and it is planted PER ITEM, because that is the granularity
    #    at which a sid means anything.
    if derived:
        key = sorted(derived, key=lambda k: -len(derived[k]))[0]
        sids = sorted(derived[key])
        stale = {k: dict(v) for k, v in derived.items()}
        stale[key] = {s: derived[key][s] for s in sids[1:]}
        l2 = compare_extraction(stored, derived, stale)
        cases.append((f"a dataset item one sid short ({key}, the 2016 shift)",
                      not l2["passed"], f"{len(l2['missing_sids'])} missing sid(s)"))

    # 6. dataset text altered for one sid
    if derived:
        key = sorted(derived, key=lambda k: -len(derived[k]))[0]
        sid0 = sorted(derived[key])[0]
        edited = {k: dict(v) for k, v in derived.items()}
        edited[key] = dict(derived[key])
        edited[key][sid0] = dict(derived[key][sid0],
                                 text=derived[key][sid0]["text"] + " and so it was")
        l2 = compare_extraction(stored, derived, edited)
        cases.append((f"a dataset sentence with altered text ({key} {sid0})",
                      not l2["passed"],
                      f"{len(l2['text_mismatches'])} text mismatch(es)"))

    # 6b. an item file missing from disk entirely — the index claims it, the shard has no
    #     file. Silence here would let a 270 MB gap read as a pass.
    if derived:
        key = sorted(derived)[0]
        short = {k: v for k, v in derived.items() if k != key}
        l2 = compare_extraction(stored, derived, short)
        cases.append((f"an item file absent from disk ({key})", not l2["passed"],
                      f"{len(l2['missing_files'])} item file(s) absent"))

    # 7. THE ASIDE CASE, from a real one: `written-answer-22754` gained a bracket aside
    #    after our copy was taken. A checker that fails on that reports a correct sitting
    #    as broken; this asserts the aside is recorded, not failed — and that real content
    #    loss inside the same turn still fails.
    if fresh:
        rid = next((k for k, v in fresh.items()
                    if v.get("turns") and len(v["turns"][0].get("text") or "") > 40), None)
        if rid:
            bad = copy.deepcopy(stored)
            rep = next((r for r in bad["reports"] if r["report_id"] == rid), None)
            if rep and rep.get("turns"):
                aside = ('[Please refer to "Clarification by Minister", Official Report, '
                         '8 September 2026, Vol 96, Issue 35, Clarification section.] ')
                rep["turns"][0]["text"] = aside + rep["turns"][0]["text"]
                l1b = compare_turns(bad, fresh)
                cases.append((f"a bracket aside added at the source ({rid})",
                              bool(l1b["passed"] and l1b["aside_differences"]),
                              f"aside(s) {len(l1b['aside_differences'])}, "
                              f"mismatch(es) {len(l1b['turn_count_mismatches'])} "
                              f"— recorded, not failed"))
                # ...and the same turn with real text DELETED must still FAIL, or the
                # aside handling has been widened into "ignore differences".
                worse = copy.deepcopy(bad)
                wrep = next(r for r in worse["reports"] if r["report_id"] == rid)
                wrep["turns"][0]["text"] = wrep["turns"][0]["text"][:-25]
                l1b2 = compare_turns(worse, fresh)
                cases.append((f"content trimmed off the end of that same turn ({rid})",
                              not l1b2["passed"],
                              f"{len(l1b2['missing_turns'])} missing turn(s), "
                              f"{len(l1b2['turn_count_mismatches'])} mismatch(es)"))

    # 9. THE PARTITION MUST PARTITION — and the check of it must not be vacuous. A
    #    category that overlaps another would double-count a single lost report, inflating
    #    the gap and breaking the reconciliation while each individual count still looked
    #    plausible. Asserting only that the numbers SUM to `gone` cannot see that.
    if fresh:
        rid_any = None
        for r in stored["reports"]:
            if r["report_id"] in fresh and int(r.get("words") or 0):
                rid_any = r["report_id"]
                break
        if rid_any:
            overlap = [{"report_id": rid_any, "words": 1, "turns": 1}]
            cases.append(("a report counted in two categories at once "
                          "(content + not_obtained)",
                          not _partition_ok(overlap, [], [rid_any], [rid_any]),
                          "overlapping categories detected — the partition check is not "
                          "vacuous"))
            cases.append(("categories that do not cover every gone report",
                          not _partition_ok([], [], [], ["some-other-id"]),
                          "uncovered report detected"))

    # 10. a clean copy must PASS — the control for the controls. A checker that fails
    #    everything is as useless as one that passes everything.
    l1a = compare_reports(stored, fresh, [], {"collected": len(fresh),
                                              "max_result": len(fresh)},
                          source_lookup=no_source)
    l1b = compare_turns(stored, fresh)
    l2 = compare_extraction(stored, derived, {k: dict(v) for k, v in derived.items()})
    cases.append(("an unmodified copy passes every level",
                  l1a["passed"] and l1b["passed"] and l2["passed"],
                  "all levels PASS on the unmodified sitting"))

    print(f"selftest: planted defects against {day} "
          f"({len(stored['reports'])} reports, "
          f"{sum(len(v) for v in derived.values())} derived sentences "
          f"across {len(derived)} items)")
    ok = 0
    for name, caught, detail in cases:
        caught = bool(caught)
        ok += caught
        print(f"  [{'CAUGHT' if caught else 'MISSED'}] {name}  — {detail}")
    print(f"\n  {ok}/{len(cases)} correct")
    return 0 if ok == len(cases) else 1


# ----------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("sittings", nargs="*", help="sitting dates (YYYY-MM-DD)")
    ap.add_argument("--year", help="check every sitting of this year")
    ap.add_argument("--all", action="store_true", help="check the whole archive")
    ap.add_argument("--workers", type=int, default=8, help="parallel report fetches")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    ap.add_argument("--out", help="write the JSON result here")
    ap.add_argument("--selftest", action="store_true",
                    help="plant known defects and assert they are caught")
    ap.add_argument("--sanity", action="store_true",
                    help="only report the archive's own bookkeeping defects "
                         "(empty placeholder rows), no network")
    ap.add_argument("--sentences-total", action="store_true",
                    help="print the derived sentence total per year (equivalence check)")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    if a.sanity:
        bad = 0
        print("BOOKKEEPING SANITY — rows we hold that the source has no content for")
        for day in all_sitting_dates():
            stored, _ = load_stored(day)
            if not stored:
                continue
            rows = [(r["report_id"], r.get("words"), len(r.get("turns") or []))
                    for r in stored.get("reports") or []
                    if not int(r.get("words") or 0) and not (r.get("turns") or [])]
            if rows:
                bad += len(rows)
                print(f"  {day}  {len(rows)} empty row(s): "
                      f"{', '.join(r[0] for r in rows[:6])}"
                      f"{' ...' if len(rows) > 6 else ''}")
        print(f"\n  {bad} empty placeholder row(s) across the archive")
        return 1 if bad else 0

    if a.sentences_total:
        total = 0
        for day in all_sitting_dates():
            stored, _ = load_stored(day)
            if not stored:
                continue
            n = sum(len(v) for v in derived_sentences(day, stored).values())
            total += n
            print(f"  {day}  {n:6d}")
        print(f"  derived sentences, whole archive: {total:,}")
        print("  (compare against pipeline/dataset/index.json: the two should agree)")
        return 0

    days = list(a.sittings)
    if a.year:
        days += [d for d in all_sitting_dates({a.year}) if d not in days]
    if a.all:
        for d in all_sitting_dates():
            if d not in days:
                days.append(d)
    if not days:
        ap.error("give a sitting date, --year, or --all (or --selftest)")

    print("=" * 78)
    print("TEST 1 — COMPLETENESS AGAINST A FRESH HANSARD PULL")
    print("=" * 78)
    print(f"  sittings   : {len(days)}")
    print(f"  archive    : {DATA}")
    print(f"  dataset    : {DATASET}")
    print(f"  live source: {P.BASE}   (every sitting re-fetched now, no cache)")
    print()

    results, t0 = [], time.time()
    for day in days:
        try:
            res = check_sitting(day, workers=a.workers)
        except Exception as exc:                        # noqa: BLE001
            res = {"date": day, "ok": False, "levels": {},
                   "error": f"{type(exc).__name__}: {exc}"}
        results.append(res)
        print_result(res)
        print(flush=True)

    passed = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    partial = [r for r in passed if r.get("partial")]
    print("=" * 78)
    l2_run = sum(1 for r in results if not (r["levels"].get("2_extraction") or {}).get("skipped"))
    print(f"  {len(passed)}/{len(results)} sitting(s) PASS"
          + (f"  ({len(failed)} FAIL)" if failed else "")
          + (f"  [{len(partial)} PARTIAL — level 2 not run]" if partial else "")
          + f"   {time.time() - t0:.0f}s")
    print(f"  level 2 (dataset) ran for {l2_run}/{len(results)} sitting(s)")
    if failed:
        print("\n  FAILING SITTINGS")
        for r in failed:
            why = r.get("error") or ", ".join(
                k for k, v in r["levels"].items() if v.get("passed") is False)
            print(f"    {r['date']}  {why}")
    print("=" * 78)

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "data": DATA, "dataset": DATASET,
                       "sittings": results}, fh, indent=1)
        print(f"  result written to {a.out}")
    if a.json:
        print(json.dumps({"passed": len(passed), "failed": len(failed),
                          "partial": len(partial),
                          "sittings": [r["date"] for r in results]}, indent=1))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
