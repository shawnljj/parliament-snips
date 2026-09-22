#!/usr/bin/env python3
"""The deterministic test suite. Mandatory, repeated, and it checks the SOURCE.

WHAT THIS RUNS, IN ORDER
------------------------
  SELFTEST     tools/verify_sitting.py --selftest
               The negative control, and it runs FIRST. It plants known defects (a
               dropped report, a merged turn, an altered word, a one-sid shifted
               dataset) into a real sitting and asserts the checker catches every one,
               plus that an unmodified sitting passes. A gate that has never been
               observed failing has not been shown to work (REQUIREMENTS.md D-1), and a
               checker whose own selftest has gone green-by-accident is the exact
               failure this project keeps producing: a PASS that means nothing.

  TEST 1       tools/verify_sitting.py <sittings...>
               Every sitting's stored data against a FRESH pull from Hansard. One
               sitting is re-pulled live at test time; no cache is reused. Levels:

                 1a REPORTS    what we hold vs what the source declares and serves
                 1b SENTENCES  every stored turn present in the fresh record, same
                               speaker, same text
                 2  EXTRACTION our derived sentences vs pipeline/dataset (skipped, and
                               reported as skipped, when that gitignored artifact is
                               absent from this working tree)

  TEST 2       the SECONDARY VERIFIER — a model that is NOT the implementor model
               It is given the deterministic evidence, not the corpus: the verdicts,
               the counters, and the concrete findings. Its job is to say whether that
               evidence is self-consistent and whether each failure is specific enough
               to act on, and to name checks it would add. Its verdict is recorded
               beside the deterministic result.

               Read the honesty note below before trusting it.

WHAT THE SECONDARY VERIFIER CAN AND CANNOT DO — stated because it is easy to overclaim
--------------------------------------------------------------------------------------
The deterministic levels are mechanical: ids, set membership, text equality. A model
adds nothing to them, and this suite does NOT let one override them — `--verifier` can
never turn a FAIL into a PASS, because a probabilistic layer with discretionary control
over deterministic evidence is how a `VERDICT: PASS` was once fabricated here
(`docs/3-lessons-learned.md` §6).

What the model is therefore asked is the thing determinism cannot supply: *is this
evidence complete and specific, or does it read as clean because it looked at nothing?*
That is a real risk — the same risk the selftest covers mechanically — and an
independent reader is a second, differently-shaped control on it.

It is NOT asked whether the transcripts match, because it is not shown the transcripts.
It is explicitly forbidden from claiming to have verified anything it was not given. If
it returns a verdict that does, that is recorded as a defect in the verifier, not as
evidence about the corpus.

FAILURE AND ABSENCE ARE DIFFERENT, AND ABSENCE IS NOT A PASS
------------------------------------------------------------
If the verifier cannot be reached, the secondary check is recorded as `not_run` and this
suite exits non-zero with that reason: an unverified state is not a verified one
(R-2.8, fail closed). `--no-verifier` opts out explicitly, which is recorded as
`skipped_by_flag` — a decision, not an unknown.

USAGE
-----
    python3 tools/test_deterministic.py                      # whole archive, both checks
    python3 tools/test_deterministic.py --sittings 3         # fast lane: the 3 newest
    python3 tools/test_deterministic.py --year 2026
    python3 tools/test_deterministic.py --sitting 2026-01-12
    python3 tools/test_deterministic.py --no-verifier
    python3 tools/test_deterministic.py --verifier gpt-oss:20b-cloud --verifier-url ...

Exit code 0 only when the selftest caught every planted defect AND every sitting in
scope passed AND the secondary check ran (or was explicitly disabled).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import verify_sitting as V      # noqa: E402
import storage                  # noqa: E402

EVIDENCE = os.path.join(ROOT, "pipeline", "verification.jsonl")

# The verifier is deliberately NOT the model that wrote the corpus. The pipeline is
# deepseek-v4.1-flash:cloud (MODELS.md); this is a different family. Independence is a
# control against self-agreement, not a quality ranking (docs/3-lessons-learned.md §1).
DEFAULT_VERIFIER = os.environ.get("PARSNIPS_VERIFIER_MODEL", "gpt-oss:20b-cloud")
DEFAULT_URL = os.environ.get("PARSNIPS_VERIFIER_URL",
                            "http://127.0.0.1:11434/v1/chat/completions")
PROMPT_VERSION = "t1-verifier-v1"

SYSTEM = """You are a test-evidence reviewer for an archival project. You are NOT shown \
the transcripts and you must not pretend to have seen them.

The project compares each parliamentary sitting's stored data against a freshly pulled \
copy of the official record. A deterministic checker does that comparison and produces \
the evidence you are given: per sitting, whether each level passed, the counters, the \
concrete findings, and — for level 1a — an explicit `reconciliation` string stating the \
arithmetic its own counts must satisfy.

Your job is to review THE EVIDENCE, on three questions only:

1. SELF-CONSISTENCY. Check the stated `reconciliation` arithmetic holds, and that no level
is marked PASS while carrying findings — with ONE stated exception, `missing_empty_placeholder`.
A level 1a marked PASS whose only findings are `missing_empty_placeholder` is CORRECT, not a
contradiction: those are rows we hold as 0-word entries that the source never serves content
for (`attendance-*`, `ptba-*`, `atbp-*` procedural rows). They set `sanity_ok: false` as a
bookkeeping flag and are deliberately not a completeness failure. Do not report them as an
inconsistency.

A level marked PASS with every counter at zero is either a clean sitting or a checker that
looked at nothing — `turns_checked`, `stored` and `derived` say which. Report ONLY
inconsistencies you can point at in the numbers given. Do NOT report an inconsistency you
have derived from counts that the evidence does not actually contain: the counts are
partitioned, so `stored - gone + never_collected` is expected to equal `fresh`, `gone` is
split into `missing_with_content + missing_empty_placeholder + not_obtained` and is NOT
expected to equal the placeholder count alone, and `declared` is the source's own claim which
need not equal `fresh`.

2. SPECIFICITY. For each failure, would a maintainer know which sitting, which report \
and which text is at fault from the evidence alone? Name any failure that is too vague \
to act on.

3. GAPS. Name up to three checks you would add that the evidence does not cover. \
Concrete and mechanical only — a check a script could run.

If the evidence shows no failures at all, say so plainly and do NOT invent work. \
Reporting a defect that is not in the evidence is a worse failure than reporting none.

Reply with ONE JSON object and nothing else:

{"agree_with_deterministic": true|false,
 "evidence_looks_complete": true|false,
 "reason": "one or two sentences",
 "self_consistency": [{"sitting": "<date>", "level": "<name>", "problem": "..."}],
 "too_vague_to_act_on": [{"sitting": "<date>", "level": "<name>", "why": "..."}],
 "checks_to_add": ["...", "...", "..."],
 "claimed_verification_of_content": false}"""


# --------------------------------------------------------------------------- helpers

def run(cmd, **kw):
    print(f"  $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, **kw)


def fresh_fingerprint(res):
    """A hash of the freshly pulled reference for a sitting.

    The doc's rule: a verification result stays valid until the SOURCE changes, not
    until a rebuild happens. This is the anchor for that — key the recorded verdict on
    it and a changed source flips the sitting back to untested (card-verification-chain
    NOT-DONE-WHEN 4). The stored file's own hash is recorded too, so a reader can tell
    which copy of our data the verdict was about.
    """
    h = hashlib.sha256()
    for name, lv in sorted((res.get("levels") or {}).items()):
        h.update(name.encode())
        h.update(json.dumps({k: v for k, v in lv.items() if k != "missing_detail"},
                            sort_keys=True, default=str).encode())
    h.update(json.dumps(res.get("fresh") or {}, sort_keys=True).encode())
    h.update(str(res.get("derived_sentences")).encode())
    return h.hexdigest()[:16]


def digest_sittings(results, max_findings=4):
    """The evidence a reviewer can actually read. Bounded on purpose: the whole
    result set for a year is megabytes, and an unbounded dump makes the model skim."""
    out = []
    for r in results:
        levels = {}
        for name, lv in (r.get("levels") or {}).items():
            d = {k: v for k, v in lv.items()
                 if k in ("passed", "skipped", "reason", "stored", "fresh", "declared",
                          "turns_checked", "reports_compared", "derived", "dataset",
                          "items_used", "reconciliation", "reconciliation_ok")}
            for k in ("missing_with_content", "missing_empty_placeholder", "not_obtained",
                      "never_collected", "missing_turns", "turn_count_mismatches",
                      "speaker_mismatches", "missing_sids", "extra_sids",
                      "text_mismatches", "missing_detail", "aside_differences",
                      "unparsed_source_content"):
                v = lv.get(k)
                if v:
                    d[k] = {"n": len(v), "sample": v[:max_findings]}
            levels[name] = d
        out.append({"sitting": r["date"], "verdict": "PASS" if r.get("ok") else "FAIL",
                    "error": r.get("error"), "levels": levels,
                    "notes": (r.get("notes") or [])[:3]})
    return out


def ask_verifier(model, url, payload, timeout=300):
    # `max_tokens` is a hard stop, and a stop mid-JSON is unparseable. 1600 was hit
    # exactly on the first live run, which made the reviewer's verdict unreadable — so the
    # cap is raised AND `finish_reason` is checked, because a silent truncation is
    # indistinguishable from a model that chose to answer badly.
    body = {"model": model, "temperature": 0, "stream": False, "max_tokens": 4000,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": payload}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    choice = (d.get("choices") or [{}])[0]
    txt = (choice.get("message") or {}).get("content") or ""
    usage = d.get("usage") or {}
    if choice.get("finish_reason") == "length":
        raise ValueError(f"verifier output truncated at max_tokens "
                         f"({usage.get('completion_tokens')} completion tokens) — "
                         f"the verdict is incomplete and cannot be read")
    return txt, usage, time.time() - t0


def parse_json(raw):
    """Tolerant of fences and prose, same rule as the rest of this repo."""
    import re
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    depth, start = 0, None
    for i, ch in enumerate(t):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(t[start:i + 1])
                except ValueError:
                    return None
    return None


def verify_deterministic_evidence(results, model, url, batch=6):
    """Run the secondary check over the evidence, in batches.

    Batched because a whole-archive digest does not fit a prompt and does not help: the
    reviewer is asked whether THIS evidence is sound, and it can answer that per group.
    Batches are contiguous by sitting so a reviewer sees each group whole.
    """
    digests = digest_sittings(results)
    groups = [digests[i:i + batch] for i in range(0, len(digests), batch)]
    verdicts, calls, tin, tout = [], 0, 0, 0
    for gi, group in enumerate(groups, 1):
        payload = (f"{len(group)} sitting(s) of deterministic evidence.\n"
                   "A level's `passed` is the deterministic checker's verdict. `sample` "
                   "entries are the first few concrete findings; `n` is the true count.\n\n"
                   + json.dumps(group, indent=1, default=str)
                   + "\n\nReturn the JSON described in your instructions.")
        try:
            raw, usage, secs = ask_verifier(model, url, payload)
        except Exception as exc:                        # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}",
                    "verdicts": verdicts, "calls": calls}
        calls += 1
        tin += usage.get("prompt_tokens") or 0
        tout += usage.get("completion_tokens") or 0
        v = parse_json(raw)
        rec = {"batch": gi, "sittings": [s["sitting"] for s in group],
               "raw": (raw or "")[:2000], "parsed": v, "secs": round(secs, 1)}
        verdicts.append(rec)
        print(f"    verifier batch {gi}/{len(groups)} "
              f"({'parsed' if v else 'UNPARSEABLE'}, {secs:.0f}s)", flush=True)
    parsed_all = all(v.get("parsed") for v in verdicts)
    unparsed = [v["batch"] for v in verdicts if not v.get("parsed")]
    # A verdict nobody could read is NOT a verdict. Reporting `status: ran` for an
    # unparseable reply would let the suite print PASS while the reviewer said nothing
    # intelligible — the same fail-open shape as a checker that looks at nothing. The run
    # still records the raw text so a maintainer can see what it actually emitted.
    return {"status": "ran" if parsed_all else "unparseable",
            "verdicts": verdicts, "calls": calls,
            "unparsed_batches": unparsed,
            "tokens": {"in": tin, "out": tout}}


# ------------------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--sitting", action="append", default=[],
                    help="restrict to these sitting dates (repeatable)")
    ap.add_argument("--year", help="restrict to one year")
    ap.add_argument("--sittings", type=int, default=0,
                    help="restrict to the N most recent sittings (fast lane)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--verifier", default=DEFAULT_VERIFIER)
    ap.add_argument("--verifier-url", default=DEFAULT_URL)
    ap.add_argument("--no-verifier", action="store_true",
                    help="run the deterministic levels only (recorded as skipped)")
    ap.add_argument("--evidence", default=EVIDENCE)
    ap.add_argument("--no-evidence", action="store_true")
    ap.add_argument("--selftest-only", action="store_true")
    a = ap.parse_args(argv)

    t0 = time.time()
    print("=" * 78)
    print("DETERMINISTIC TEST SUITE — completeness against the live source")
    print("=" * 78)
    print(f"  implementor model : {os.environ.get('PARSNIPS_MODEL', 'deepseek-v4.1-flash:cloud')}"
          f"  (writes the corpus; NOT the verifier)")
    print(f"  verifier model    : {a.verifier}"
          + ("  [DISABLED by --no-verifier]" if a.no_verifier else ""))
    print(f"  archive           : {V.DATA}")
    print(f"  dataset           : {V.DATASET}")
    print(f"  evidence          : {'(not written)' if a.no_evidence else a.evidence}")
    print()

    # ------------------------------------------------------------------ 0. selftest
    print("[0] SELFTEST — does the checker catch planted defects?")
    st = run([sys.executable, os.path.join(HERE, "verify_sitting.py"), "--selftest"],
             cwd=ROOT, capture_output=True, text=True)
    print("\n".join("    " + ln for ln in (st.stdout or "").strip().splitlines()))
    if st.returncode != 0:
        print(f"    SELFTEST FAILED (exit {st.returncode})", file=sys.stderr)
        if st.stderr:
            print("    " + st.stderr.strip().splitlines()[-1], file=sys.stderr)
    selftest_ok = st.returncode == 0
    if a.selftest_only:
        print(f"\n  selftest only: {'PASS' if selftest_ok else 'FAIL'}")
        return 0 if selftest_ok else 1
    print()

    # ------------------------------------------------------------- 1. the sittings
    days = list(a.sitting)
    if a.year:
        days += [d for d in V.all_sitting_dates({a.year}) if d not in days]
    if not days and not a.sittings:
        days = V.all_sitting_dates()
    if a.sittings:
        all_days = V.all_sitting_dates()
        scope = [d for d in all_days if (not days or d in days)]
        days = scope[-a.sittings:]
    if not days:
        print("  no sittings in scope — nothing to check", file=sys.stderr)
        return 1

    archive_days = V.all_sitting_dates()
    partial_scope = set(days) != set(archive_days)
    print(f"[1] COMPLETENESS — {len(days)} sitting(s)"
          + (f" of {len(archive_days)} in the archive (PARTIAL SCOPE)" if partial_scope
             else " (whole archive)"))
    results, t1 = [], time.time()
    for i, day in enumerate(days, 1):
        print(f"  [{i}/{len(days)}] {day}", flush=True)
        try:
            res = V.check_sitting(day, workers=a.workers, verbose=True)
        except Exception as exc:                        # noqa: BLE001
            res = {"date": day, "ok": False, "levels": {},
                   "error": f"{type(exc).__name__}: {exc}"}
        res["fresh_fingerprint"] = fresh_fingerprint(res)
        stored, _ = V.load_stored(day)
        res["stored_source_hash"] = storage.source_hash(stored) if stored else None
        results.append(res)
    passed = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    sanity_flags = sum(1 for r in results
                       if not ((r.get("levels") or {}).get("1a_reports")
                               or {}).get("sanity_ok", True))
    source_text_rows = sum(len(((r.get("levels") or {}).get("1a_reports") or {})
                               .get("unparsed_source_content") or []) for r in results)
    print(f"\n  deterministic: {len(passed)}/{len(results)} sitting(s) PASS"
          + (f", {len(failed)} FAIL" if failed else "")
          + f"  ({time.time() - t1:.0f}s)")
    for r in failed:
        why = r.get("error") or ", ".join(k for k, v in r["levels"].items()
                                          if v.get("passed") is False)
        print(f"    FAIL {r['date']}  {why}")
    print()

    # ---------------------------------------------------- 2. the secondary verifier
    print("[2] SECONDARY VERIFIER — independent review of the evidence")
    if a.no_verifier:
        secondary = {"status": "skipped_by_flag",
                     "reason": "--no-verifier was passed explicitly",
                     "model": None}
        print("    skipped explicitly by --no-verifier")
    else:
        secondary = verify_deterministic_evidence(results, a.verifier, a.verifier_url)
        secondary["model"] = a.verifier
        secondary["prompt_version"] = PROMPT_VERSION
        if secondary["status"] == "error":
            print(f"    verifier DID NOT RUN: {secondary['error']}")
            print("    -> the secondary check is unverified, which is not a pass "
                  "(R-2.8, fail closed)")
        elif secondary["status"] == "unparseable":
            print(f"    VERIFIER DEFECT: {len(secondary['unparsed_batches'])} of "
                  f"{secondary['calls']} batch(es) returned nothing readable "
                  f"(batch {secondary['unparsed_batches']}) — treated as NOT RUN")
            for v in secondary["verdicts"]:
                if not v.get("parsed"):
                    print(f"      batch {v['batch']} raw: {(v.get('raw') or '')[:300]!r}")
        else:
            agree = [v["parsed"].get("agree_with_deterministic")
                     for v in secondary["verdicts"] if v.get("parsed")]
            complete = [v["parsed"].get("evidence_looks_complete")
                        for v in secondary["verdicts"] if v.get("parsed")]
            claimed = [v["parsed"].get("claimed_verification_of_content")
                       for v in secondary["verdicts"] if v.get("parsed")]
            print(f"    batches            : {secondary['calls']}"
                  f"   tokens {secondary['tokens']['in']:,} in / "
                  f"{secondary['tokens']['out']:,} out")
            print(f"    agrees             : {sum(1 for x in agree if x)}/{len(agree)}")
            print(f"    evidence complete  : {sum(1 for x in complete if x)}/{len(complete)}")
            if any(claimed):
                print("    VERIFIER DEFECT    : it claimed to have verified content it "
                      "was not shown — that verdict is discarded")
            for v in secondary["verdicts"]:
                p = v.get("parsed") or {}
                print(f"\n    batch {v['batch']} ({', '.join(v['sittings'][:3])}"
                      f"{'...' if len(v['sittings']) > 3 else ''})")
                print(f"      agree={p.get('agree_with_deterministic')} "
                      f"complete={p.get('evidence_looks_complete')}")
                print(f"      reason: {(p.get('reason') or '')[:220]}")
                for s in (p.get("self_consistency") or [])[:3]:
                    print(f"      contradiction {s.get('sitting')} {s.get('level')}: "
                          f"{(s.get('problem') or '')[:160]}")
                for s in (p.get("too_vague_to_act_on") or [])[:3]:
                    print(f"      too vague {s.get('sitting')} {s.get('level')}: "
                          f"{(s.get('why') or '')[:160]}")
                for c in (p.get("checks_to_add") or [])[:3]:
                    print(f"      check to add: {str(c)[:160]}")
    print()

    # ------------------------------------------------------------------ 3. evidence
    # The verifier must be a DIFFERENT model from the one that writes the corpus, or
    # "secondary check" is a figure of speech. Checked, not assumed: the default is a
    # distinct model, but `--verifier` can be pointed anywhere.
    implementor = os.environ.get("PARSNIPS_MODEL", "deepseek-v4.1-flash:cloud")
    same_model = (not a.no_verifier) and a.verifier == implementor
    run_row = {
        "schema": 1,
        "kind": "test_deterministic",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model_implementor": implementor,
        "model_verifier": None if a.no_verifier else a.verifier,
        "verifier_independent": (not a.no_verifier) and not same_model,
        "secondary": {k: v for k, v in secondary.items() if k != "verdicts"},
        "selftest_passed": selftest_ok,
        "scope": {
            "sittings_checked": len(days),
            "sittings_in_archive": len(archive_days),
            "partial_scope": partial_scope,
            "years": sorted({d[:4] for d in days}),
        },
        "totals": {"passed": len(passed), "failed": len(failed),
                   "bookkeeping_flags": sanity_flags,
                   "seconds": round(time.time() - t0, 1)},
        "sittings": [
            {"date": r["date"], "ok": bool(r.get("ok")),
             "fresh_fingerprint": r.get("fresh_fingerprint"),
             "stored_source_hash": r.get("stored_source_hash"),
             "error": r.get("error"),
             "levels": {name: {k: v for k, v in lv.items()
                               if k not in ("missing_detail", "per_item")}
                        for name, lv in (r.get("levels") or {}).items()}}
            for r in results
        ],
    }
    if not a.no_evidence:
        os.makedirs(os.path.dirname(a.evidence) or ".", exist_ok=True)
        with open(a.evidence, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(run_row, ensure_ascii=False, default=str) + "\n")
        print(f"  evidence appended to {a.evidence}")

    # --------------------------------------------------------------------- verdict
    secondary_ok = (secondary["status"] == "ran" or
                    secondary["status"] == "skipped_by_flag")
    # A verifier that ran but disagrees with the evidence is surfaced, not smoothed over
    # — and NOT treated as a veto either. Both directions of the mistake are real here:
    # a probabilistic layer with discretionary control over deterministic evidence once
    # produced a fabricated `VERDICT: PASS` (docs/3-lessons-learned.md §6), and letting a
    # model's arithmetic objection fail a mechanical check would be the same error with
    # the sign flipped. So the deterministic result stands, and reviewer disagreement
    # becomes its own verdict state with its own exit code (2 = needs review).
    contradictions, verifier_defects = [], []
    if secondary.get("status") == "ran":
        for v in secondary["verdicts"]:
            p = v.get("parsed") or {}
            for s in (p.get("self_consistency") or []):
                contradictions.append({**s, "batch": v["batch"]})
            if p.get("claimed_verification_of_content"):
                verifier_defects.append(v["batch"])

    ok = selftest_ok and not failed and secondary_ok and not same_model
    needs_review = bool(contradictions or verifier_defects)

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"  [{'PASS' if selftest_ok else 'FAIL'}] selftest      "
          f"(the checker catches planted defects)")
    print(f"  [{'PASS' if not failed else 'FAIL'}] deterministic "
          f"({len(passed)}/{len(results)} sittings)")
    if sanity_flags:
        print(f"  [FLAG] bookkeeping  {sanity_flags} sitting(s) carry empty placeholder "
              f"rows — the archive claims reports the source has no content for. "
              f"Not lost text; see docs/completeness-check.md")
    if source_text_rows:
        print(f"  [NOTE] source text  {source_text_rows} row(s) are held empty while the "
              f"source serves text for them — counted as FAILURES above, not bookkeeping")
    print(f"  [{'PASS' if secondary_ok else 'FAIL'}] secondary     "
          f"({secondary['status']}: {(secondary.get('model') or 'none')})")
    if same_model:
        print(f"  [FAIL] independence — the verifier IS the implementor model "
              f"({implementor}); a model cannot be its own secondary check. Re-run with a "
              f"different --verifier, or --no-verifier to record it as skipped.")
    if partial_scope:
        print(f"  [PARTIAL] scope       ({len(days)} of {len(archive_days)} sittings — "
              f"NOT a whole-archive result)")
    if verifier_defects:
        print(f"  [VERIFIER DEFECT] batches {verifier_defects} claimed to verify content "
              f"they were not shown — those verdicts are discarded")
    if contradictions:
        print(f"  [REVIEW] reviewer disagreement: {len(contradictions)} "
              f"(does not override the deterministic result; read them)")
        for c in contradictions[:6]:
            print(f"        {c.get('sitting')} {c.get('level')}: "
                  f"{str(c.get('problem') or c.get('why'))[:170]}")
    print(f"\n  {'ALL CHECKS PASS' if ok and not needs_review else 'NEEDS REVIEW' if ok else 'NOT PASSING'}"
          f"   ({time.time() - t0:.0f}s total)")
    print("=" * 78)
    if not ok:
        return 1
    return 2 if needs_review else 0


if __name__ == "__main__":
    sys.exit(main())
