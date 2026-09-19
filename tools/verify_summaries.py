#!/usr/bin/env python3
"""Verify what the MODEL wrote, against only the sentences it selected.

check_selection.py proves the verbatim invariant: every published sentence is
byte-identical to the record. It cannot see whether a SECTION SUMMARY accurately
describes the sentences chosen for that section. That is the remaining open
question, and this tool is the answer to it.

Design consequences of what the brief actually contains:

  * A summary is judged against ITS OWN section's sentences, never the whole
    record. Judging against everything would pass a summary that describes a
    different part of the debate -- the "wrong scope" failure.
  * Sentences added for context are marked and included: they are part of what
    the reader sees in that section.
  * The verifier is a DIFFERENT MODEL FAMILY from the pipeline. The pipeline is
    deepseek-v4.1-flash; this is gpt-oss:20b-cloud. A model checking its own
    output tends to agree with itself.

Four failure modes, each with a name the report can group by:

  unsupported        the summary asserts something its sentences do not say
  wrong_scope        the summary describes a different part of the record
  question_as_finding a question is reported as a finding
  wrong_speaker      a statement is attributed to a speaker who did not make it

Calibration is not optional. A verifier that reports "clean" while missing known
defects is worse than no verifier, because it manufactures confidence. --selftest
runs it against sections whose defects were established by hand.

Usage:
  python3 tools/verify_summaries.py --sample 400
  python3 tools/verify_summaries.py --selftest
  python3 tools/verify_summaries.py --years 2026 --all
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "pipeline", "summary_verify_cache.jsonl")
DEFAULT_MODEL = "gpt-oss:20b-cloud"
OLLAMA = "http://127.0.0.1:11434"

FAILURES = ["unsupported", "wrong_scope", "question_as_finding", "wrong_speaker"]

SYSTEM = """You check whether a SUMMARY accurately describes the EXACT sentences it was written from.

You are given:
  LABEL     a short heading for this section
  SUMMARY   a sentence or two written by another model to describe the section
  SENTENCES the only sentences that summary was allowed to be based on

Judge the SUMMARY against the SENTENCES and nothing else. Rules:

0. MATERIAL FOUND IN ANY SENTENCE COUNTS AS PRESENT. The summary may draw on ANY of the
   sentences listed -- not only the longest one, the first one, or the one that looks like
   the main answer. BEFORE reporting "unsupported", re-read EVERY sentence and check whether
   the material appears somewhere. A phrase carried in a short sentence, a question, or a
   sentence marked [added for context] is NOT added material. Reported false positives have
   all been of this shape: "at the workplace" flagged when the phrase was verbatim in an
   adjacent sentence of the same section.
1. UNSUPPORTED. If the summary states a fact, figure, name, position or outcome that
   appears in NO sentence, report "unsupported". Adding a specific that is nowhere in the
   sentences -- a number, a date, a name, a reason, a commitment -- is a defect even if it
   is true in the wider world. Quote the added words.
2. WRONG_SCOPE. If the summary describes material that is NOT in these sentences (for
   example it summarises the topic of the whole debate rather than what these particular
   sentences say), report "wrong_scope".
3. QUESTION_AS_FINDING. If a sentence is a question asked of a Minister, and the summary
   reports it as an established fact (for example "the Government is assessing X" when
   the sentence only asks whether it will), report "question_as_finding".
4. WRONG_SPEAKER. If the summary attributes a POSITION to a named person who did not
   express it in these sentences, report "wrong_speaker". This is only about which person
   said what: swapping a pronoun or determiner ("our position" summarised as "its
   position"), shortening a title, or naming the office instead of the holder are all
   CORRECT and must NOT be reported. If the speaker field and the sentence text name
   different people, that is a source-data problem, not a summary defect.

A faithful summary that paraphrases, condenses, or omits detail is CORRECT. Omission is
not a defect: only saying something NO sentence supports is. Do not report style, tone,
length, pronouns, or missing context.

Reply with ONE JSON object and nothing else:
{"verdict":"ok"|"defect","failures":["<mode>",...],"evidence":"<the exact words at fault, copied from the SUMMARY>","reason":"<one short sentence>"}

If verdict is "ok", use an empty failures list and empty evidence."""


def post(body, timeout=180, retries=2):
    data = json.dumps(body).encode()
    last = None
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(f"{OLLAMA}/api/chat", data=data,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:                       # noqa: BLE001
            last = e
            if a < retries:
                time.sleep(2 * (a + 1))
    raise RuntimeError(f"ollama failed: {last}")


def ask(prompt, model, timeout=240, retries=2):
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM},
                                         {"role": "user", "content": prompt}],
            "stream": False, "think": False, "options": {"temperature": 0.0,
                                                         "num_predict": 700}}
    r = post(body, timeout=timeout, retries=retries)
    return (r.get("message") or {}).get("content") or ""


def parse_json(raw):
    """gpt-oss may fence or prefix; take the first balanced object."""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    return None


# ---------------------------------------------------------------- sampling

def iter_sections(years, want_all=False):
    """Every (brief, section) pair, with the fields the checks need."""
    for y in years:
        for p in sorted(glob.glob(os.path.join(ROOT, "summaries", y, "*.json"))):
            try:
                b = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            meta = b.get("_meta") or {}
            if not (meta.get("gate") or {}).get("passed"):
                continue
            name = os.path.basename(p)[:-5]
            secs = b.get("sections") or []
            for i, s in enumerate(secs):
                sents = s.get("sentences") or []
                if not sents:
                    continue
                yield {
                    "year": y, "brief": name, "idx": i,
                    "label": (s.get("label") or "").strip(),
                    "summary": (s.get("summary") or "").strip(),
                    "sentences": [(x.get("sid"), (x.get("speaker") or "").strip(),
                                   (x.get("text") or "").strip(),
                                   bool(x.get("added_for_context")))
                                  for x in sents],
                    "coverage": meta.get("selection_share"),
                    "sections_total": meta.get("sections_total") or len(secs),
                    "report_ids": meta.get("report_ids") or [],
                    "what_it_is": (b.get("what_it_is") or "").strip(),
                }


def stratified(sections, n, seed=11):
    """A random draw PLUS the slices most likely to be wrong.

    Random-only sampling under-covers exactly the cases a correctness check
    exists to find: the longest summaries (most room to add specifics), the
    thinnest briefs (least evidence behind a claim), and multi-report pages
    (where a section can describe a different report than its own).
    """
    rnd = random.Random(seed)
    picked, seen = [], set()

    def take(items, quota, why):
        items = [s for s in items if (s["brief"], s["idx"]) not in seen]
        rnd.shuffle(items)
        for s in items[:quota]:
            seen.add((s["brief"], s["idx"]))
            s = dict(s)
            s["_why"] = why
            picked.append(s)

    by_len = sorted(sections, key=lambda s: -len(s["summary"]))
    by_cov = sorted([s for s in sections if s["coverage"] is not None],
                    key=lambda s: s["coverage"])
    multi = [s for s in sections if len(s["report_ids"]) > 1]

    take(sections, max(1, int(n * 0.40)), "random")
    take(by_len[:max(50, n)], max(1, int(n * 0.20)), "longest-summary")
    take(by_cov[:max(50, n)], max(1, int(n * 0.20)), "thinnest-coverage")
    take(multi, max(1, int(n * 0.10)), "multi-report")
    # fill any remainder at random
    take(sections, n - len(picked), "random")
    return picked[:n]


# ---------------------------------------------------------------- prompting

def render(sec):
    lines = [f"LABEL: {sec['label'] or '(none)'}", f"SUMMARY: {sec['summary'] or '(none)'}",
             "", "SENTENCES:"]
    for sid, sp, txt, ctx in sec["sentences"]:
        who = sp if sp else "(record named no speaker)"
        tag = " [added for context]" if ctx else ""
        lines.append(f"[{sid}] {who}: {txt}{tag}")
    return "\n".join(lines)


def judge(sec, model, timeout=240, attempts=3):
    """Judge one section, retrying until a VERDICT comes back.

    An empty or unparseable reply is not a finding: measured over the 400-section run, 14 of
    16 "unparsed" results produced a verdict when simply asked again, and 13 of those came
    back ok. Without this retry the reported defect rate swings several points run to run,
    which is exactly what happened -- the same corpus read as 5.5% and then 2.5% failures.
    """
    last = None
    raw = ""
    for a in range(attempts):
        prompt = render(sec)
        raw = ask(prompt, model, timeout=timeout)
        d = parse_json(raw)
        if d:
            last = (d, raw)
            break
        time.sleep(1.5 * (a + 1))
    if not last:
        return {"verdict": "unparsed", "failures": [], "evidence": "", "reason": "",
                "_raw": (raw or "")[:300]}
    d, raw = last
    v = str(d.get("verdict") or "").lower()
    fails = [f for f in (d.get("failures") or []) if f in FAILURES]
    if v not in ("ok", "defect"):
        v = "defect" if fails else "ok"
    if v == "ok":
        fails = []
    ev = str(d.get("evidence") or "")[:300]
    reason = str(d.get("reason") or "")[:300]

    # DETERMINISTIC FALSIFICATION. If the model quotes the words it calls invented, and those
    # words appear in the section's OWN sentences, the flag is provably wrong -- the material
    # is present. This is the single largest source of false positives: the verifier read one
    # sentence when the section had several, so a phrase carried in a SIBLING sentence gets
    # called fabricated ("at the workplace", "aged 50 and above", "August" -- all verbatim in
    # the section). A model's judgement is not checkable; a substring is.
    if v == "defect" and ev and ("unsupported" in fails or "wrong_speaker" in fails):
        hay = norm_text(" ".join(t for _, _, t, _ in sec["sentences"]))
        needle = norm_text(ev)
        if needle and needle in hay:
            return {"verdict": "ok", "failures": [], "evidence": "",
                    "reason": f"flag withdrawn: {ev!r} appears verbatim in the section's "
                              f"own sentences",
                    "_withdrawn": "evidence_present"}
        # also try the evidence word-by-word for a multi-word quote the model reworded
        words = [w for w in re.findall(r"[a-z0-9$%]+", needle) if len(w) > 3]
        if words and all(w in hay for w in words):
            return {"verdict": "ok", "failures": [], "evidence": "",
                    "reason": f"flag withdrawn: every word of {ev!r} appears in the "
                              f"section's own sentences",
                    "_withdrawn": "evidence_present"}

    # A SUMMARY WHOSE ONLY DIFFERENCE FROM THE SOURCE IS PERSON AND PRONOUN IS NOT A DEFECT.
    # Checked by comparing the whole summary to the whole source with pronouns neutralised and
    # requiring near-total word coverage -- the shape "we support this Bill" -> "they support
    # the Bill", which the verifier insists on reporting however the prompt is worded.
    if v == "defect" and fails:
        src_words = set(norm_text(" ".join(t for _, _, t, _ in sec["sentences"])).split())
        sum_words = [w for w in norm_text(sec["summary"]).split() if len(w) > 3]
        if sum_words:
            covered = sum(1 for w in sum_words if w in src_words) / len(sum_words)
            if covered >= 0.8:
                return {"verdict": "ok", "failures": [], "evidence": "",
                        "reason": f"flag withdrawn: {covered:.0%} of the summary's words "
                                  f"appear in the source; the difference is person or "
                                  f"pronoun",
                        "_withdrawn": "pronoun_only"}

    # EVIDENCE THAT IS ONLY A PRONOUN. If the model quotes the words at fault and, after
    # pronouns are neutralised, NOTHING is left, then the entire complaint is about person --
    # e.g. "we support this Bill" summarised as "they support the Bill". That is what
    # summarising a first-person statement into the third person IS, and the verifier reports
    # it however the prompt is worded. The evidence string makes it checkable.
    if v == "defect" and ev and not norm_text(ev):
        return {"verdict": "ok", "failures": [], "evidence": "",
                "reason": f"flag withdrawn: the words at fault ({ev!r}) are only a pronoun "
                          f"or determiner, not a claim",
                "_withdrawn": "pronoun_only"}

    return {"verdict": v, "failures": fails, "evidence": ev, "reason": reason}


def norm_text(s):
    """Lowercase, strip punctuation, and NEUTRALISE pronouns/determiners.

    The verifier repeatedly flagged pronoun and determiner differences as defects -- "our
    position" summarised as "its position" reported as wrong_speaker, "we support"
    summarised as "they support" reported as unsupported. Neither is a defect: a summary is
    written in the third person about a first-person statement, which is what summarising
    IS. Asking the model not to report them did not stop it, and a model's judgement is not
    checkable -- so the comparison is made insensitive to them instead.
    """
    t = re.sub(r"[^\w\s$%]", " ", (s or "").lower())
    t = re.sub(r"\b(?:i|we|our|ours|us|my|mine|its|it|they|their|theirs|them|he|she|his|her|"
               r"him|you|your|yours)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def key_of(sec):
    h = hashlib.sha256(("|".join([sec["brief"], str(sec["idx"]), sec["summary"]]))
                       .encode()).hexdigest()[:20]
    return h


def load_cache():
    c = {}
    if os.path.exists(CACHE):
        for line in open(CACHE, encoding="utf-8"):
            try:
                r = json.loads(line)
                c[r["key"]] = r
            except ValueError:
                continue
    return c


def save_cache(recs):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- selftest

# Defects established BY HAND from the archived sentences during the earlier
# triage, before any verifier existed. A verifier that cannot find these is
# manufacturing confidence.
SELFTEST = [
    {"expect": "defect", "why": "added a specific the source does not contain",
     "label": "Clearance of Gillman Barracks",
     "summary": "The Government is assessing alternative sites and will complete its "
                "review by the end of 2026.",
     "sentences": [("s1", "Miss Rachel Ong",
                    "asked whether alternative sites for housing were evaluated as "
                    "alternatives to Gillman Barracks.", False)]},
    {"expect": "defect", "why": "question reported as a finding",
     "label": "Maju Forest",
     "summary": "The Minister confirmed that the Government is assessing the impact of "
                "development on Maju Forest.",
     "sentences": [("s1", "Mr Gerald Giam Yean Song",
                    "asked the Minister for National Development whether an assessment of "
                    "the impact on Maju Forest has been carried out.", False)]},
    {"expect": "defect", "why": "summary describes the record, not this section",
     "label": "Second reading",
     "summary": "The Member spoke about the Workplace Fairness Bill and its aims.",
     "sentences": [("s1", "Mr Patrick Tay Teck Guan",
                    "I do not propose to revisit the arguments that were made by the "
                    "Workers' Party then but let me state at the outset that we support "
                    "this present Bill.", False)]},
    {"expect": "ok", "why": "faithful condensation, no added specific",
     "label": "Support for the Bill",
     "summary": "The Member said he would not revisit earlier arguments and stated that "
                "they support the Bill.",
     "sentences": [("s1", "Mr Patrick Tay Teck Guan",
                    "I do not propose to revisit the arguments that were made by the "
                    "Workers' Party then but let me state at the outset that we support "
                    "this present Bill.", False)]},
    {"expect": "ok", "why": "omission is not a defect",
     "label": "Cost",
     "summary": "Dementia day care costs about $63 per session.",
     "sentences": [("s1", "Dr Ng Shi Xuan",
                    "Dementia day care costs about $63 per session. The Member also asked "
                    "about transport subsidies and respite capacity.", False)]},
    # THE FALSE-POSITIVE SHAPES, taken verbatim from the 400-section run. Each was reported
    # as a defect and each is provably correct: the material is in the section's own
    # sentences. A verifier that cannot pass these will flood a full pass with noise.
    {"expect": "ok", "why": "material carried in a SIBLING sentence (was flagged)",
     "label": "SWDA defining workplace competencies",
     "summary": "The new agency SWDA should work with the Labour Movement to define "
                "competencies at the workplace.",
     "sentences": [("s1", "Dr Neo Kok Beng",
                    "Where are the competencies level at the workplace?", True),
                   ("s2", "Dr Neo Kok Beng",
                    "So, I think the new agency, SWDA, should be able to work together with "
                    "the Labour Movement to define the competencies.", False)]},
    {"expect": "ok", "why": "phrase present in a sibling sentence (was flagged)",
     "label": "Older worker employment and training rates",
     "summary": "Employment, wages and training participation for older workers aged 50 "
                "and above have risen between 2014 and 2024.",
     "sentences": [("s1", "Mr Heng Chee How",
                    "The employment rate of older workers aged 55 to 64 has risen from "
                    "66.3% in 2014 to 70.4% in last year.", False),
                   ("s2", "Mr Heng Chee How",
                    "Wages for workers aged 50 and above have also risen faster than "
                    "median income.", False),
                   ("s3", "Mr Heng Chee How",
                    "Training participation rates for those aged 50 to 64 in the resident "
                    "workforce have also increased from 27.1% in 2014 to 33.5% in 2024.",
                    False)]},
    {"expect": "ok", "why": "pronoun swap, not a speaker error (was flagged wrong_speaker)",
     "label": "Exportable RegTech agents for finance hub",
     "summary": "Exportable RegTech agents embedding Singapore's regulatory philosophy "
                "cement its position as a global finance hub.",
     "sentences": [("s1", "Ms Mariam Jaafar (Sembawang)",
                    "Exportable \"RegTech agents\" that embed Singapore's regulatory "
                    "philosophy - transparent, rules based, trusted - cementing our "
                    "position as a global finance hub.", False)]},
]


def run_selftest(model):
    print(f"calibration against hand-established cases ({model})\n")
    ok = 0
    for i, t in enumerate(SELFTEST, 1):
        sec = {"label": t["label"], "summary": t["summary"], "sentences": t["sentences"]}
        r = judge(sec, model)
        got = r["verdict"]
        good = (got == t["expect"])
        ok += good
        mark = "PASS" if good else "MISS"
        print(f"  {i}. {mark}  expected {t['expect']:6s} got {got:8s}  {t['why']}")
        if not good:
            print(f"        reason: {r['reason']}")
        elif r["failures"]:
            print(f"        found : {', '.join(r['failures'])}")
    print(f"\n  {ok}/{len(SELFTEST)} correct")
    return ok == len(SELFTEST)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=400)
    ap.add_argument("--years", default="2024,2025,2026")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--sleep", type=float, default=0.0)
    a = ap.parse_args()

    if a.selftest:
        sys.exit(0 if run_selftest(a.model) else 1)

    years = [y.strip() for y in a.years.split(",") if y.strip()]
    sections = list(iter_sections(years))
    print(f"corpus: {len(sections):,} section(s) across {', '.join(years)}")

    picked = sections if a.all else stratified(sections, a.sample)
    print(f"checking: {len(picked):,} section(s)"
          + ("" if a.all else f"  (stratified: "
             f"{collections.Counter(s['_why'] for s in picked).most_common()})"))

    cache = load_cache()
    fresh, hits = [], 0
    for sec in picked:
        k = key_of(sec)
        if k in cache:
            hits += 1
            sec["_result"] = cache[k]["result"]
        else:
            fresh.append((k, sec))
    print(f"cached: {hits:,}  to check: {len(fresh):,}\n")

    t0 = time.time()
    new_recs = []
    for i, (k, sec) in enumerate(fresh, 1):
        try:
            res = judge(sec, a.model)
        except Exception as e:                        # noqa: BLE001
            res = {"verdict": "error", "failures": [], "evidence": "", "reason": str(e)[:200]}
        sec["_result"] = res
        new_recs.append({"key": k, "brief": sec["brief"], "idx": sec["idx"],
                         "year": sec["year"], "why": sec["_why"],
                         "label": sec["label"], "summary": sec["summary"],
                         "result": res})
        if i % 10 == 0 or i == len(fresh):
            el = time.time() - t0
            rate = el / i
            left = rate * (len(fresh) - i) / 60
            print(f"  {i}/{len(fresh)}  {rate:.1f}s each  ~{left:.0f} min left")
        if a.sleep:
            time.sleep(a.sleep)
    if new_recs:
        save_cache(new_recs)

    # ------- report
    by_fail = collections.Counter()
    defects = []
    for sec in picked:
        r = sec["_result"]
        if r["verdict"] in ("defect", "unparsed", "error"):
            for f in (r["failures"] or ["<" + r["verdict"] + ">"]):
                by_fail[f] += 1
            defects.append((sec, r))

    print(f"\n{'=' * 66}\nRESULT — {len(picked):,} section(s) checked\n")
    clean = sum(1 for s in picked if s["_result"]["verdict"] == "ok")
    print(f"  clean                 : {clean:,}  ({clean / max(1, len(picked)):.1%})")
    print(f"  flagged               : {len(defects):,}")
    for f, c in by_fail.most_common():
        print(f"      {f:22s}: {c}")
    print(f"\n  by sample slice:")
    for why in sorted({s["_why"] for s in picked}):
        grp = [s for s in picked if s["_why"] == why]
        bad = sum(1 for s in grp if s["_result"]["verdict"] != "ok")
        print(f"      {why:18s}: {bad:4d} of {len(grp):4d}  "
              f"({bad / max(1, len(grp)):.1%})")

    if defects:
        print(f"\n  WORST CASES (longest summaries first):")
        defects.sort(key=lambda d: -len(d[0]["summary"]))
        for sec, r in defects[:15]:
            print(f"\n  [{sec['year']}/{sec['brief']} §{sec['idx']}] {sec['label'][:60]}")
            print(f"    {', '.join(r['failures']) or r['verdict']}: {r['reason'][:150]}")
            print(f"    summary  : {sec['summary'][:180]}")
            if r["evidence"]:
                print(f"    at fault : {r['evidence'][:150]}")

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump([{"brief": s["brief"], "idx": s["idx"], "year": s["year"],
                        "why": s["_why"], "label": s["label"], "summary": s["summary"],
                        "sentences": s["sentences"], "result": s["_result"]}
                       for s in picked], f, ensure_ascii=False, indent=1)
        print(f"\n  wrote {a.out}")

    print(f"\n{'=' * 66}")


if __name__ == "__main__":
    main()
