"""
Parsnips — citation verifier agent.

A SEPARATE agent whose only job is to decide whether each citation actually supports
the claim it is attached to. It exists because the deterministic gates in
build_briefs.py cannot do this, and the gap is not hypothetical:

  oral-answer-4089 published the claim "The Government IS ASSESSING how rising fuel,
  utilities and logistics costs are passed through to businesses and consumers",
  citing the sentence "asked the Deputy Prime Minister ... whether ...".

That point passed every gate. The quote was verbatim (substituted from the dataset,
never retyped), the citation resolved, the schema was complete, coverage was fine. The
gates check STRUCTURE -- that a quote exists, matches and resolves -- and no amount of
structural checking can tell that a QUESTION was reported as a FINDING, because the
quote really is faithful to the source. Meaning is a different question, and it needs a
reader.

WHY A DIFFERENT MODEL. By default this runs on a model from a different family than the
one that wrote the briefs. Asking a model to check its own output is close to asking it
to agree with itself; the failure mode this exists to catch is the one a model is least
likely to notice in its own prose. `--verifier` overrides it, and the model that ran is
recorded in every verdict so a report can never be mistaken for independent when it
was not.

ADVERSARIAL BY CONSTRUCTION. The instruction is to assume the claim may overstate and
to look for specific, named defects rather than to give a general impression:

  question_as_fact  the source ASKS; the claim states it as fact or as a decision
  overstates        the claim asserts more than the source supports
  reversed          the claim negates or inverts the source
  dropped_qualifier the claim drops a "while/although/if" that changes the meaning
  wrong_subject     the claim is about something the source does not address
  wrong_speaker     the claim attributes to someone the source does not
  unsupported       the source is related but does not carry the claim

Verdicts are cached by content, so re-running after an unrelated change costs nothing.

    python3 tools/verify_citations.py --briefs /tmp/briefs_2026/2026 --limit 40
    python3 tools/verify_citations.py --briefs summaries/2026
    python3 tools/verify_citations.py --briefs summaries/2026 --min-supported 0.98
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scraper"))
sys.path.insert(0, os.path.join(ROOT, "summariser"))

ENDPOINT = os.environ.get("PARSNIPS_LLM_URL",
                          "http://127.0.0.1:11434/v1/chat/completions")
# A DIFFERENT FAMILY from the writer (deepseek writes the briefs). Independent checking
# is the whole point of this agent, so the default is not the briefing model.
DEFAULT_VERIFIER = os.environ.get("PARSNIPS_VERIFIER_MODEL", "gpt-oss:20b-cloud")
CACHE = os.path.join(ROOT, "pipeline", "verifier_cache.jsonl")
PROMPT_VERSION = "v1"

VERIFIER_SYSTEM = """You are a strict citation verifier for a parliamentary record. \
You are given CLAIMS, each with the SOURCE sentence(s) it cites.

Your only job: decide whether the source actually supports the claim. You are not \
judging importance, style or completeness. You are judging one thing -- if a reader \
followed the citation, would they find the claim supported?

Be ADVERSARIAL. Assume each claim may overstate, and look for these named defects:

  question_as_fact   The source ASKS something ("asked the Minister whether...",
                     "will the Government...?"). The claim states it as a fact, a
                     decision, or something the Government IS DOING. A question is not
                     a finding: asking whether X is happening does NOT mean X happens.
  overstates         The claim asserts more than the source supports.
  reversed           The claim negates or inverts what the source says.
  dropped_qualifier  The source hedges ("while A, how will B...") and the claim drops
                     that hedge so it reads as a plain assertion of A.
  wrong_subject      The claim is about a topic the source does not address.
  wrong_speaker      The claim attributes something to a speaker the source does not.
  unsupported        Related, but the source does not carry the claim.

Return STRICT JSON only, no prose outside it:

{"verdicts": [{"i": 0, "verdict": "supported", "defect": "none",
               "why": "one short sentence"}]}

verdict is one of: supported | weak | unsupported | reversed
  supported   the source plainly carries the claim
  weak        partly supported: some of the claim is there, some is inferred or added
  unsupported the source does not carry the claim
  reversed    the claim says the opposite of the source, or turns a question into a fact
defect is the named defect above, or "none" when verdict is supported.
Judge the CLAIM against the SOURCE only. Do not use outside knowledge."""

VERIFY_TMPL = """{n} claims to verify.

{blocks}

Return the JSON described in your instructions, with one entry per claim, in order."""


class RateLimit(Exception):
    pass


def _post(body, timeout):
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise RateLimit(str(e))
        raise


def ask(prompt, system, model, timeout=300, retries=3):
    """One verifier call. Retries with backoff; raises rather than hanging forever."""
    native = "/api/chat" in ENDPOINT
    body = ({"model": model, "stream": False, "think": False,
             "options": {"temperature": 0, "num_predict": 2000},
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": prompt}]}
            if native else
            {"model": model, "temperature": 0, "stream": False,
             "max_tokens": 2000,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": prompt}]})
    last = None
    for attempt in range(retries):
        try:
            d = _post(body, timeout)
            if native:
                txt = (d.get("message") or {}).get("content") or ""
            else:
                txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            u = d.get("usage") or {}
            return txt, {"in": u.get("prompt_tokens") or d.get("prompt_eval_count") or 0,
                         "out": u.get("completion_tokens") or d.get("eval_count") or 0}
        except RateLimit:
            time.sleep(5 * (attempt + 1))
            last = RateLimit("rate limited")
        except Exception as exc:                                    # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last if last is not None else RuntimeError("verifier call failed")


def parse_json(raw):
    """Recover the verdict object from a model reply, tolerant of fences and prose."""
    if not raw:
        return None
    t = raw.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    # brace-matched scan: take the object that contains "verdicts"
    depth, start = 0, None
    for i, ch in enumerate(t):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunk = t[start:i + 1]
                try:
                    got = json.loads(chunk)
                except ValueError:
                    continue
                if isinstance(got, dict) and "verdicts" in got:
                    return got
                start = None
    return None


def key_of(claim, sources, model):
    h = hashlib.sha256()
    for part in (PROMPT_VERSION, model, claim, *sources):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


def load_cache():
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("k"):
                    cache[rec["k"]] = rec
    return cache


def save_cache(new_records):
    if not new_records:
        return
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as fh:
        for rec in new_records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def collect_points(brief_paths):
    """Every published point, paired with the sentence text it cites."""
    import build_briefs as B
    out = []
    for p in brief_paths:
        name = os.path.basename(p)[:-5]
        try:
            brief = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (brief.get("_meta") or {}).get("gate", {}).get("passed"):
            continue
        item = B.load_item(f"2026/{name}.json")
        if not item:
            continue
        by_sid = {s["sid"]: s for s in item.get("sentences") or []}
        for kp in brief.get("key_points") or []:
            sources = []
            for sid in kp.get("cites") or []:
                s = by_sid.get(sid)
                if s:
                    sp = (s.get("speaker") or "").strip()
                    sources.append(f"[{sid}]{(f' {sp}:' if sp else '')} {s['text'].strip()}")
            if not sources:
                continue
            out.append({"brief": name, "claim": (kp.get("point") or "").strip(),
                        "sources": sources})
    return out


def build_blocks(batch):
    lines = []
    for i, it in enumerate(batch):
        lines.append(f"CLAIM {i}: {it['claim']}")
        lines.append("SOURCE:")
        for s in it["sources"]:
            lines.append(f"  {s}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--briefs", required=True, help="directory of published briefs")
    ap.add_argument("--verifier", default=DEFAULT_VERIFIER)
    ap.add_argument("--batch", type=int, default=8, help="claims per verifier call")
    ap.add_argument("--limit", type=int, default=0, help="cap claims (0 = all)")
    ap.add_argument("--min-supported", type=float, default=0.0,
                    help="fail if the supported fraction is below this")
    ap.add_argument("--report", default=None, help="write the verdict JSONL here")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    paths = sorted(p for p in glob.glob(os.path.join(args.briefs, "*.json"))
                   if os.path.basename(p) != "index.json")
    points = collect_points(paths)
    if not points:
        print("no publishable points found -- nothing to verify", file=sys.stderr)
        return 2
    if args.limit:
        points = points[:args.limit]

    cache = {} if args.no_cache else load_cache()
    print(f"briefs        : {len(paths)}")
    print(f"claims        : {len(points)}")
    print(f"verifier model: {args.verifier}")
    print(f"cached verdicts: {sum(1 for p in points if key_of(p['claim'], p['sources'], args.verifier) in cache)}")
    print()

    todo, verdicts, new_cache = [], [], []
    for it in points:
        k = key_of(it["claim"], it["sources"], args.verifier)
        if k in cache:
            rec = dict(cache[k])
            rec["brief"] = it["brief"]
            verdicts.append(rec)
        else:
            it["_k"] = k
            todo.append(it)

    tin = tout = 0
    for start in range(0, len(todo), args.batch):
        batch = todo[start:start + args.batch]
        prompt = VERIFY_TMPL.format(n=len(batch), blocks=build_blocks(batch))
        try:
            raw, usage = ask(prompt, VERIFIER_SYSTEM, args.verifier)
        except Exception as exc:                                    # noqa: BLE001
            print(f"  batch {start // args.batch + 1}: FAILED {exc}")
            continue
        tin += usage["in"]
        tout += usage["out"]
        got = parse_json(raw)
        by_i = {v.get("i"): v for v in (got or {}).get("verdicts") or []
                if isinstance(v, dict)}
        for i, it in enumerate(batch):
            v = by_i.get(i) or {}
            rec = {"k": it["_k"], "brief": it["brief"], "claim": it["claim"][:160],
                   "verdict": (v.get("verdict") or "unjudged").lower(),
                   "defect": (v.get("defect") or "unknown").lower(),
                   "why": (v.get("why") or "")[:200],
                   "verifier": args.verifier}
            verdicts.append(rec)
            new_cache.append(rec)
        done = min(start + args.batch, len(todo))
        print(f"  verified {done}/{len(todo)}", flush=True)

    save_cache(new_cache)

    # ------------------------------------------------------------------ results
    from collections import Counter
    counts = Counter(v["verdict"] for v in verdicts)
    defects = Counter(v["defect"] for v in verdicts if v["verdict"] != "supported")
    total = len(verdicts)
    supported = counts.get("supported", 0)
    print("\n" + "=" * 78)
    print("CITATION VERIFICATION")
    print("=" * 78)
    print(f"\n  verifier      : {args.verifier}")
    print(f"  claims judged : {total}")
    print(f"  tokens        : {tin:,} in / {tout:,} out")
    print()
    for v in ("supported", "weak", "unsupported", "reversed", "unjudged"):
        n = counts.get(v, 0)
        if n:
            print(f"  {v:12s} {n:6d}  {100 * n / total:5.1f}%")
    if defects:
        print("\n  defects found:")
        for d, n in defects.most_common():
            print(f"    {d:20s} {n}")

    bad = [v for v in verdicts if v["verdict"] in ("unsupported", "reversed")]
    if bad:
        print(f"\n  WORST ({len(bad)} unsupported/reversed) -- first 12:")
        for v in bad[:12]:
            print(f"\n    {v['brief']}  [{v['defect']}]")
            print(f"      claim : {v['claim'][:110]}")
            print(f"      why   : {v['why'][:110]}")

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            for v in verdicts:
                fh.write(json.dumps(v, ensure_ascii=False) + "\n")
        print(f"\n  verdicts written to {args.report}")

    frac = supported / total if total else 0.0
    print(f"\n  supported fraction: {frac:.3f}")
    if args.min_supported and frac < args.min_supported:
        print(f"  FAIL below --min-supported {args.min_supported}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
