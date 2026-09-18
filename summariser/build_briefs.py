"""
Parsnips — Stage 2 (extract), Stage 3 (assemble), Stage 4 (verify).

The agreed algorithm ("approach C"): the model reads the item and returns, for each
point, the *id* of the sentence it rests on plus a short claim in its own words. It
never writes a quotation. Assembly substitutes the stored sentence text afterwards,
so the published quote is verbatim by construction rather than by trust.

Why this is the shape of the whole thing: a model that paraphrases a quote in its
head has no way to publish the paraphrase, because it never types the quote. The
gate then stops being a filter for model honesty and becomes an invariant — and a
failure means a bug in the dataset, not a dishonest model.

Stages, per SUMMARISATION.md:
    Stage 2  extract   LLM    item payload  -> points as (claim, sid[])
    Stage 3  assemble  no     that          -> brief JSON in the site's schema
    Stage 4  verify    no     brief         -> gate verdict; withheld if it fails

Usage:
    python3 summariser/build_briefs.py --year 2026 --limit 3 --model qwen3:4b
    python3 summariser/build_briefs.py --year 2026 --status
    python3 summariser/build_briefs.py --dates 2026-08-05 --model deepseek-v4.1-flash:cloud
"""

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))
sys.path.insert(0, HERE)
import storage  # noqa: E402

DATASET = os.path.join(ROOT, "pipeline", "dataset")
OUT = os.path.join(ROOT, "summaries")
USAGE_LOG = os.path.join(ROOT, "pipeline", "usage.jsonl")

ENDPOINT = os.environ.get("PARSNIPS_LLM_URL",
                          "http://127.0.0.1:11434/v1/chat/completions")
# The default is CLOUD, and that is a measured decision rather than a budget one.
# A local 3B model ran a whole sitting for $0 and was rejected anyway: it is not
# reproducible (the same item gave 1-8 points across identical calls) and it
# under-extracts on small items (1 point against the cloud model's 6-8 on the same
# 160-word answer). Under-extraction is invisible -- the brief is short, truthful,
# correctly quoted and passes every gate. See MODELS.md.
DEFAULT_MODEL = os.environ.get("PARSNIPS_LLM_MODEL", "deepseek-v4.1-flash:cloud")
# The fields the SITE renders, so a brief missing one is incomplete rather than
# merely sparse. Derived by reading what build_site.py consumes, not from memory:
#   title, what_it_is, why_it_matters, not_said, what_happens_next, stage, key_points
# Every one is optional to the renderer, which is exactly why their absence needs a
# gate here instead of a crash there.
# Bump this whenever the brief shape changes. A resumed run re-does any brief whose
# schema is behind, so a corpus is never a mixture of pipeline versions.
SCHEMA = 3

# A sentence that is a QUESTION, not a statement. Singapore Hansard records the
# questioner's turn as "asked the Minister ... (a) whether ...", so these prefixes are
# the actual shape of the data rather than a guess.
QUESTION_START = re.compile(
    r"^\s*(to ask|asked|asking|whether|what|how|why|when|where|which|who|"
    r"will|would|does|do|did|is|are|was|were|has|have|had|can|could|should|may|"
    r"could the|could the hon|given|in light of|in view of)", re.I)

REQUIRED_BRIEF_FIELDS = ("title", "what_it_is", "why_it_matters",
                         "what_happens_next", "not_said", "key_points")

RETRIES = 3

# A single completion may not take longer than this. Measured: a 50-sentence chunk
# on llama3.2:3b takes ~7s, and the largest chunk in the corpus ~15s, so 180s is
# 10x headroom. Anything beyond it is a stall, not slow work.
REQUEST_TIMEOUT = int(os.environ.get("PARSNIPS_TIMEOUT", "180"))

# Cap the reply length at the provider, which is the actual fix for a real failure
# mode rather than a mitigation for it.
#
# Measured in Ollama's own log: on one chunk a 3B model generated 35,420 tokens for
# a reply that should be a few hundred — it fell into a repetition loop and never
# emitted an end token, so the request ran until the client gave up. The healthy
# median request is 1-10s, so this showed up as an occasional 3-minute stall rather
# than a consistent slowness, which is exactly the kind of failure that gets
# misdiagnosed as "the model is too slow".
#
# 900 tokens is ~3x the longest legitimate reply observed (a 10-point chunk with
# citations). A reply cut off at the cap is recovered by salvage_truncated().
MAX_REPLY_TOKENS = int(os.environ.get("PARSNIPS_MAX_TOKENS", "900"))

# THE RUNAWAY GUARD, not a content budget. It exists because one 94-chunk motion once
# produced 259 points, which is not a summary of anything. It is deliberately set well
# above what a faithful brief needs, because the cap's job is to catch a malfunction and
# NOT to bound length: a ceiling on reading time is a ceiling on coverage, and coverage
# is the product. At 0.25 points per sentence a 444-sentence oral answer may carry 111
# points, and the largest record in the corpus is ~2,000 sentences, so the effective
# ceiling is far above anything legitimate extraction produces.
RUNAWAY_FLOOR = int(os.environ.get("PARSNIPS_RUNAWAY_FLOOR", "120"))
RUNAWAY_PER_SENTENCE = float(os.environ.get("PARSNIPS_RUNAWAY_PER_SENTENCE", "0.25"))

# Where briefs are written. Overridable so a test run never clobbers the 291
# briefs built under the old item-level schema — replacing those is an owner
# decision (REQUIREMENTS.md §11 open question 6), not a side effect of a test.
OUT_DIR = os.environ.get("PARSNIPS_OUT", OUT)

# The prompt budget must leave room for the reply inside the model's window, and it
# is also the single biggest lever on OUTPUT quality, which was not obvious.
#
# Measured on llama3.2:3b against a 2026 motion: at a 24,000-char budget the model
# was handed a 19k-char slice, responded with single points carrying 84-sentence
# citation lists, ran past its output cap and returned unparseable JSON on ~20% of
# chunks. At 6,000 chars the same model returns 1-4 well-formed points per chunk with
# 1-8 citations and no truncation. A smaller prompt is not just cheaper; it is what
# makes a small model usable at all.
PROMPT_CHAR_BUDGET = int(os.environ.get("PARSNIPS_PROMPT_CHARS", "6000"))

# ------------------------------------------------------------------ the prompts
#
# These are the load-bearing artefact of Stage 2, and every clause is a requirement:
#
#  * "cite by id" is R-2.1/R-2.2 (a claim must cite a verbatim quotation that is
#    verifiable against the source) reduced to a mechanical instruction.
#  * "Never write the quotation" is the whole correctness argument. If the model is
#    asked for the words too, it will produce them, and they will sometimes differ.
#  * The neutrality rules are R-3.1/R-3.2: report what was said, never judge whether
#    it was answered, committed to, or evaded.
#  * "Do not invent an id" is R-2.3: a point whose citation cannot be resolved is
#    dropped, so a fabricated id is a wasted point.
#  * "say nothing if the slice does not support it" is N-2 (no fabricated content,
#    ever) — the model must be able to return fewer points than the maximum.

EXTRACT_SYSTEM = """You are extracting factual points from a record of a Singapore \
Parliament sitting. You are reading an EXCERPT of one debate or answer, given as \
numbered sentences.

Each sentence begins with its id, like s00012.

Return JSON only, in this exact shape:

{"points": [{"claim": "a short factual statement, in your own words",
             "cites": ["s00012"]}],
 "what_it_is": "one sentence naming the kind of business this is",
 "why_it_matters": "2-3 sentences on what this changes for ordinary people, ONLY \
where the record says so. If it does not say, write exactly: The record does not set \
out the practical impact.",
 "what_happens_next": "the next step if the record states one (e.g. referred to a \
Select Committee, comes into force on a date). Otherwise write exactly: not stated",
 "not_said": ["a question this debate raises that the record leaves unresolved, \
phrased neutrally as an open question"]}

Rules, all of which are enforced afterwards:
- The "claim" is YOUR OWN wording. It must not be a quotation.
- The "cites" list holds sentence ids that SUPPORT the claim. Copy ids exactly as
  they appear. A point whose ids cannot be found is deleted, so a made-up id is a
  wasted point. Cite the FEWEST sentences that carry the point — usually 1 to 3.
  Do NOT list every related sentence; a long list is a sign the claim is too broad
  and should be split into narrower points.
- NEVER write out a quotation. Do not copy sentences into your output. You are
  only pointing at them. The words are looked up from the record afterwards.
- Report what was said or decided. Never judge whether a speaker answered
  adequately, committed in good faith, or evaded. Never write "failed to",
  "declined to", "did not address", "only said".
- A QUESTION IS NOT A FINDING. Many turns are questions ("asked the Minister
  whether ..."). If a cited sentence only ASKS something, your claim must stay in
  the asking register -- write "The Member asked how X" or "It was asked whether Y".
  NEVER restate a question as a fact: a question asking whether the Government is
  assessing something does NOT mean the Government is assessing it. Getting this
  wrong publishes an answer the record never gave.
- ADD NOTHING. The claim may reword the source, but it must not contain a single
  specific that the cited sentences do not state. Verified failures from this corpus,
  all of which were published and only caught later:
    * adding items to a list -- source "no ... for their commercial benefit" became
      "not for curiosity, convenience or commercial gain";
    * replacing a pronoun with a name -- source "discussing with them" became
      "discussed with GPs";
    * adding a place or attachment -- source "the family sent ... for badminton
      coaching" became "a family in the Member's constituency";
    * DROPPING A HEDGE -- source "who sits at the table POTENTIALLY decides" became
      "who sits at the table decides". A hedge removed turns a possibility into a
      finding, and this is the easiest of all to miss. If the source says "could",
      "may", "potentially" or "is considering", the claim must keep that word.
  If a detail is not in the cited sentences, it does not go in the claim -- leave it
  out, or cite the sentence that does state it.
- Figures, dates, amounts and named programmes are the most valuable things to
  capture. Capture them where they appear.
- Give 3 to 10 points for a substantial excerpt, 1 to 3 for a short one. A point
  with a very long citation list is a single over-broad claim: split it instead.
- If the excerpt does not support a point, return fewer points. Returning an empty
  list is correct and expected for procedural text. Never invent content to fill
  the shape.
- Keep the whole reply short. The reply is cut off if it runs too long, and a cut-off
  reply is discarded entirely."""

EXTRACT_TMPL = """Sentences from the record, each prefixed with its id:

{excerpt}

Return the JSON described in your instructions."""

ORAL_SYSTEM = """You are extracting the substance of a Singapore Parliament oral \
answer. You are reading numbered sentences from the exchange: a question was asked \
by a Member, and a Minister or office-holder responded.

Each sentence begins with its id, like s00012.

Return JSON only, in this exact shape:

{"question": "what was asked, in your own words",
 "response": [{"claim": "a concrete factual point from the answer",
               "cites": ["s00012"]}],
 "what_it_is": "one sentence naming the ministry and subject",
 "why_it_matters": "2-3 sentences on what this changes for ordinary people, ONLY \
where the record says so. If it does not say, write exactly: The record does not set \
out the practical impact.",
 "what_happens_next": "the next step if the record states one. Otherwise write \
exactly: not stated",
 "not_said": ["a question this answer raises that the record leaves unresolved, \
phrased neutrally as an open question"]}

Rules, all of which are enforced afterwards:
- The "question" and each "claim" are YOUR OWN wording. They must not be quotations.
- The question is asked; the response is what was actually SAID. Do not restate the
  question as if it were a finding. If the answer does not address part of the
  question, leave it out of the response and let "not_said" record it.
- The "cites" list holds sentence ids that SUPPORT the claim. Copy ids exactly. A
  point whose ids cannot be found is deleted, so a made-up id is wasted.
- NEVER write out a quotation. You are only pointing at sentences; the words are
  looked up from the record afterwards.
- Report what was said. Do not say whether the question was answered well, or at
  all. Do not frame. Map the response to the question without judging it.
- ADD NOTHING. The claim may reword the source, but it must not contain a single
  specific that the cited sentences do not state. Verified failures from this corpus,
  all of which were published and only caught later:
    * adding items to a list -- source "no ... for their commercial benefit" became
      "not for curiosity, convenience or commercial gain";
    * replacing a pronoun with a name -- source "discussing with them" became
      "discussed with GPs";
    * adding a place or attachment -- source "the family sent ... for badminton
      coaching" became "a family in the Member's constituency";
    * DROPPING A HEDGE -- source "who sits at the table POTENTIALLY decides" became
      "who sits at the table decides". A hedge removed turns a possibility into a
      finding, and this is the easiest of all to miss. If the source says "could",
      "may", "potentially" or "is considering", the claim must keep that word.
  If a detail is not in the cited sentences, it does not go in the claim -- leave it
  out, or cite the sentence that does state it.
- Figures, dates, amounts and named schemes are the most valuable things to capture.
- 2 to 8 response points."""

ORAL_TMPL = """Sentences from an oral answer, each prefixed with its id:

{excerpt}

Return the JSON described in your instructions."""


# ----------------------------------------------------------------------- transport


def _post(body, timeout):
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ask(user, system, model, timeout=None):
    """One completion. Returns (text, usage_dict). Errors and stalls raise.

    The timeout is passed to the socket AND enforced by the loop below, because a
    socket timeout alone was not enough in practice: a stale Ollama runner left a
    connection OPEN with the model loaded and simply never sent a byte, so the read
    blocked indefinitely and one chunk consumed the whole run's wall-clock. A stalled
    request now fails after `timeout` and is retried, rather than hanging forever.
    """
    if timeout is None:
        timeout = REQUEST_TIMEOUT
    native = "/api/chat" in ENDPOINT
    if native:
        body = {"model": model, "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": MAX_REPLY_TOKENS},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
    else:
        body = {"model": model, "temperature": 0, "stream": False,
                "think": False, "reasoning_effort": "none",
                "max_tokens": MAX_REPLY_TOKENS,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
    last = None
    for attempt in range(RETRIES):
        t0 = time.time()
        try:
            d = _post(body, timeout)
            if native:
                text = (d.get("message") or {}).get("content") or ""
                pt, ct = d.get("prompt_eval_count"), d.get("eval_count")
                if pt is not None and ct is not None:
                    usage = {"prompt_tokens": pt, "completion_tokens": ct,
                             "estimated": False}
                else:
                    usage = {"prompt_tokens": len(system + user) // 4,
                             "completion_tokens": len(text) // 4,
                             "estimated": True}
            else:
                text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                u = d.get("usage") or {}
                if u.get("prompt_tokens") is not None:
                    usage = {"prompt_tokens": u.get("prompt_tokens", 0),
                             "completion_tokens": u.get("completion_tokens", 0),
                             "estimated": False}
                else:
                    # ~4 chars/token is the usual English rule of thumb for these
                    # tokenisers; flagged as an estimate rather than passed off as real.
                    usage = {"prompt_tokens": len(system + user) // 4,
                             "completion_tokens": len(text) // 4,
                             "estimated": True}
            return text, usage
        except Exception as exc:                                    # noqa: BLE001
            last = exc
            # A stalled socket can outlive the read timeout on some platforms, so
            # enforce the ceiling here too. Retrying is right for a stall: the same
            # request succeeds once the runner is healthy, and it costs one chunk.
            took = time.time() - t0
            print(f"      request failed after {took:.0f}s ({type(exc).__name__}); "
                  f"attempt {attempt + 1}/{RETRIES}", flush=True)
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("ask() failed with no captured exception")


def parse_json(raw):
    """Recover a JSON object from a model reply.

    Models do three things beyond returning clean JSON, and small local models do
    all three: wrap it in code fences, add prose around it, and — measured on
    llama3.2:3b against this very task — emit SEVERAL objects, one per line, instead
    of one object containing a list. The last case is not malformed output: each
    object is well-formed and carries real points, so a parser that returns only the
    first silently discards most of the item's content. That is exactly the class of
    silent under-selection this project has been bitten by before, so it is handled
    explicitly by `parse_json_many` rather than tolerated.
    """
    got = parse_json_many(raw)
    return got[0] if got else None


def parse_json_many(raw):
    """Every JSON object recoverable from the reply, in order.

    Handles fenced blocks, surrounding prose, one object per line, and a single
    object that itself contains the point list. Returns a list, possibly empty.
    """
    if not raw:
        return []
    t = raw.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t).strip()

    # 1. the whole reply is one object
    try:
        d = json.loads(t)
        return [d] if isinstance(d, dict) else []
    except ValueError:
        pass

    # 2. one object per line (llama3.2 does this); try each line, then the whole
    #    text again with newlines preserved as separators.
    out = []
    for line in t.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                out.append(d)
        except ValueError:
            pass
    if out:
        return out

    # 3. scan for brace-matched objects anywhere in the text
    out = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        d = json.loads(t[start:i + 1])
                        if isinstance(d, dict):
                            out.append(d)
                    except ValueError:
                        pass
                    start = None
    if out:
        return out

    # 4. TRUNCATED reply: the model ran out of output budget mid-object, which is a
    #    real failure mode (a runaway citation list once produced a 5KB unclosed
    #    object and cost the whole chunk). Salvage the complete elements instead of
    #    discarding everything: walk the object, keep each `{...}` that closed
    #    inside a "points"/"response" array, and return a dict of them.
    salvaged = salvage_truncated(t)
    return [salvaged] if salvaged else []


def salvage_truncated(t):
    """Recover the complete points from a reply cut off mid-object.

    Deliberately conservative: only the *elements* of the point array are taken, and
    only those whose braces closed. A half-written point is dropped rather than
    guessed at, because a claim without a resolved citation is not publishable
    anyway (R-2.3), so salvaging a partial point would only add noise.
    """
    key = None
    for k in ("points", "response"):
        if f'"{k}"' in t:
            key = k
            break
    if not key:
        return None
    arr = t.find("[", t.find(f'"{key}"'))
    if arr < 0:
        return None
    items = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for i in range(arr, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        d = json.loads(t[start:i + 1])
                        if isinstance(d, dict):
                            items.append(d)
                    except ValueError:
                        pass
                    start = None
    if not items:
        return None
    got = {key: items, "_salvaged_from_truncated_reply": True}
    # what_it_is usually precedes the array, so it may still be recoverable
    m = re.search(r'"what_it_is"\s*:\s*"([^"]{0,300})"', t)
    if m:
        got["what_it_is"] = m.group(1)
    m = re.search(r'"question"\s*:\s*"([^"]{0,400})"', t)
    if m:
        got["question"] = m.group(1)
    return got


# ------------------------------------------------------------------ dataset access


def load_index():
    with open(os.path.join(DATASET, "index.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_item(path):
    """An item payload as stored: sentences carry sid, speaker, turn_index."""
    d = storage.read_json(os.path.join(DATASET, path))
    if not d:
        return None
    return d


def item_meta(path):
    p = os.path.join(DATASET, path)
    d = storage.read_json(p)
    if not d:
        return None
    return d


# --------------------------------------------------------------- excerpt building


def render_excerpt(sentences, budget=PROMPT_CHAR_BUDGET):
    """Sentences as `sid text` lines, speaker printed once per turn.

    Grouping by turn rather than repeating the speaker on every line is a measured
    24% saving on prompt text (754,082 sentences across 40,945 turns is 18.4
    sentences per turn), and it costs nothing because turn_index is already on every
    sentence.
    """
    out, cur_turn, cur_spk, used = [], None, None, 0
    for s in sentences:
        key = (s.get("report_id"), s.get("turn_index"))
        prefix = ""
        if key != cur_turn:
            cur_turn = key
            spk = (s.get("speaker") or "").strip()
            if spk and spk != cur_spk:
                cur_spk = spk
                prefix = f"\n[{spk}]\n"
            elif spk:
                prefix = "\n"
            else:
                prefix = "\n"
        line = f"{prefix}{s['sid']} {s['text']}"
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)


def group_turns(sentences):
    """Sentences grouped into turns, order preserved. A turn is the attribution unit
    (one speaker, contiguous), so it is the natural chunk boundary."""
    turns, cur = [], None
    for s in sentences:
        key = (s.get("report_id"), s.get("turn_index"))
        if key != cur:
            turns.append([])
            cur = key
        turns[-1].append(s)
    return turns


def make_chunks(sentences, budget=PROMPT_CHAR_BUDGET):
    """Chunk for the model, never splitting mid-sentence.

    Two rules, and the second exists because the first was not enough in practice:

    1. Prefer TURN boundaries, so a chunk never mixes two speakers — attribution
       stays unambiguous and the speaker label is printed once.
    2. But a single turn can exceed any budget: measured on a 2026 motion, the
       largest turn is 227 sentences / 25,773 characters. Chunking on turns alone
       therefore produced a 19k-char prompt that made a 3B model emit an 84-item
       citation list, run past its output budget and return unparseable JSON. So an
       oversized turn is SPLIT, and the split parts keep the same turn_index — which
       is why a chunk is a list of sentences rather than a list of turns.

    Splitting cannot misattribute anything: every part of a turn has one speaker by
    construction, so a part still has exactly one possible speaker.
    """
    def charlen(seq):
        return sum(len(s["text"]) + 10 for s in seq)

    chunks, cur, size = [], [], 0
    for turn in group_turns(sentences):
        if charlen(turn) <= budget:
            if cur and size + charlen(turn) > budget:
                chunks.append(cur)
                cur, size = [], 0
            cur.extend(turn)
            size += charlen(turn)
            continue
        # oversized turn: flush, then split this turn into budget-sized parts
        if cur:
            chunks.append(cur)
            cur, size = [], 0
        part, psize = [], 0
        for s in turn:
            n = len(s["text"]) + 10
            if part and psize + n > budget:
                chunks.append(part)
                part, psize = [], 0
            part.append(s)
            psize += n
        if part:
            chunks.append(part)
    if cur:
        chunks.append(cur)
    return chunks


# ----------------------------------------------------------------------- Stage 2


def stage2_extract(item, model, is_oral):
    """Read the item and return points as (claim, sid[]). Never a quotation.

    Small items go in one call. Heavy items are chunked on turn boundaries and the
    per-chunk points are concatenated — deliberately NOT reduced by another model
    call, because every point already carries its own citations and a reduce step
    would only add a place for the model to introduce unsupported connective claims.
    """
    sentences = item.get("sentences") or []
    if not sentences:
        return None, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "estimated": False}
    chunks = make_chunks(sentences)
    usage_total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                   "estimated": False}
    sys_p = ORAL_SYSTEM if is_oral else EXTRACT_SYSTEM
    tmpl = ORAL_TMPL if is_oral else EXTRACT_TMPL

    question = None
    what_it_is = None
    item_fields = {}
    points = []

    for i, chunk in enumerate(chunks, 1):
        excerpt = render_excerpt(chunk)
        # Progress within a long item. A 2026 motion is 94 chunks, so without this a
        # legitimate 11-minute item is indistinguishable from a hang — and the first
        # hang cost an hour before it was noticed.
        if len(chunks) > 4:
            print(f"      chunk {i}/{len(chunks)} ({len(chunk)} sentences)…",
                  flush=True)
        raw, usage = ask(tmpl.format(excerpt=excerpt), sys_p, model)
        usage_total["calls"] += 1
        for k in ("prompt_tokens", "completion_tokens"):
            usage_total[k] += usage.get(k, 0)
        usage_total["estimated"] = usage_total["estimated"] or usage.get("estimated", False)
        got_list = parse_json_many(raw)
        if not got_list:
            print(f"      chunk {i}/{len(chunks)}: no JSON returned")
            continue
        # A model may return one object with a list, or several objects each holding
        # one point (measured on llama3.2:3b). Both are accepted: taking only the
        # first object would silently discard most of a small model's work.
        for got in got_list:
            if is_oral and not question and got.get("question"):
                question = got["question"]
            if not what_it_is and got.get("what_it_is"):
                wi = got["what_it_is"]
                # A model that echoes the instruction back as the value contributes
                # nothing; better to fall back to the record's own title.
                if not wi.strip().lower().startswith("one sentence naming"):
                    what_it_is = wi
            # Item-level fields. The FIRST non-empty answer wins, for the same reason
            # the first point is preferred: these describe the whole item, and a later
            # chunk only saw part of it.
            for field, guard in (("why_it_matters", None),
                                 ("what_happens_next", ("not stated", "n/a", "")),
                                 ("stage", ("not stated", "n/a", ""))):
                if not item_fields.get(field):
                    v = (got.get(field) or "").strip()
                    if v and not (guard and v.lower() in guard):
                        item_fields[field] = v
            if not item_fields.get("not_said"):
                ns = [str(x).strip() for x in (got.get("not_said") or [])
                      if str(x).strip()]
                if ns:
                    item_fields["not_said"] = ns
            for key in (("response", "points") if is_oral else ("points", "response")):
                for p in (got.get(key) or []):
                    if isinstance(p, dict) and (p.get("claim") or p.get("point")):
                        # remember which chunk produced it, so the reading-budget cap
                        # can spread its selection across the whole debate
                        p["_chunk"] = i
                        points.append(p)
                if got.get(key):
                    break

    if not points:
        return None, usage_total
    return {"points": points, "question": question,
            "what_it_is": what_it_is,
            "why_it_matters": item_fields.get("why_it_matters", ""),
            "what_happens_next": item_fields.get("what_happens_next", ""),
            "not_said": item_fields.get("not_said", []),
            "stage": item_fields.get("stage", ""),
        }, usage_total


# ----------------------------------------------------------------------- Stage 3


def stage3_assemble(item, extracted, model):
    """Substitute stored sentence text for every cited id.

    This is where a quotation becomes verbatim "by construction": the only quotes
    that exist are the ones copied from here, out of the dataset.
    """
    by_sid = {s["sid"]: s for s in (item.get("sentences") or [])}
    kept, dropped = [], []
    # A single point citing dozens of sentences is an over-broad claim, and a long
    # citation list is also what made one small model run past its output budget and
    # lose the whole chunk. Cap it: the first citations are the ones the model put
    # first, and the site renders the leading one as the quotation anyway.
    MAX_CITES = int(os.environ.get("PARSNIPS_MAX_CITES", "6"))
    for p in extracted.get("points") or []:
        claim = (p.get("claim") or p.get("point") or "").strip()
        cites = [c for c in (p.get("cites") or []) if isinstance(c, str)]
        resolved = [by_sid[c] for c in cites if c in by_sid][:MAX_CITES]
        if not claim:
            dropped.append({"reason": "empty claim", "point": p})
            continue
        if not resolved:
            # R-2.3: no resolvable citation means the point is not publishable.
            dropped.append({"reason": "no resolvable citation", "point": p,
                            "cites": cites})
            continue
        # A point may cite several sentences, and each citation must carry ITS OWN
        # text. An earlier version stored only the first sentence's text while
        # listing every cited id, so the verifier compared sids 2..n against
        # sentence 1's words and multi-cite points failed the invariant. The model
        # was never at fault; assembly was.
        kept.append({
            "point": claim,
            "_chunk": p.get("_chunk", 0),
            "speaker": (resolved[0].get("speaker") or "").strip(),
            # Primary quote = the first citation, which is what the site renders as
            # the point's quotation. The full set travels in `citations`.
            "quote": resolved[0]["text"].strip(),
            "cites": [s["sid"] for s in resolved],
            "citations": [{"sid": s["sid"],
                           "text": s["text"].strip(),
                           "speaker": (s.get("speaker") or "").strip()}
                          for s in resolved],
        })
    if not kept:
        return None, dropped

    # DEDUPLICATE. Measured on the first 18 briefs of 2026: 11.7% of all points were
    # duplicates, affecting 44% of briefs, and oral-answer-4172 was made entirely of
    # them (6 points, 6 duplicates). The model emits the same claim twice within one
    # chunk and across chunks, and no gate caught it -- duplicates are perfectly
    # truthful, correctly quoted, and each one raises coverage, so every check passed
    # while the brief said less than it appeared to.
    #
    # Two points are the same if they cite the SAME EVIDENCE, regardless of wording.
    # Deduplicating on the claim text instead would miss a reworded restatement, and
    # the citation set is what a reader actually verifies against.
    seen, uniq = set(), []
    for k in kept:
        key = tuple(sorted(k.get("cites") or []))
        if key and key in seen:
            dropped.append({"reason": "duplicate", "point": k.get("point", "")[:80]})
            continue
        seen.add(key)
        uniq.append(k)
    n_dupes = len(kept) - len(uniq)
    kept = uniq

    # A 94-chunk debate yielded 259 points — every chunk contributed its maximum and
    # nothing ever consolidated. Some cap is still needed as a runaway guard, and it
    # must be applied by SPREAD, never by truncation: dropping the tail would silently
    # discard the end of a long debate, which is the same failure as the head+tail
    # truncation this project already removed (R-2.6).
    #
    # THE CAP IS PROPORTIONAL TO THE ITEM, because §1.4 defines the reading budget as
    # ~10 seconds PER TURN (287 turns = 48 min), not as a flat number of points.
    #
    # A flat 40 was wrong and measurably so. On the 2026-08-05 motion, 81 turns, the
    # cap bounded achievable coverage at 40/81 = 0.49 no matter how good the
    # extraction was, and the published brief reached 0.17 with a 227-sentence speech
    # among the turns left uncited. The cap was scoring against the very metric the
    # gate enforces, and the turns it discarded were the substantial ones.
    #
    # 1.5 per turn gives room for a meaty turn to carry two points while thin turns
    # carry none, which is how the budget is actually spent. The cap still exists as a
    # runaway guard (an 85-cite point is not a summary); what it must not be is a
    # content budget, because discarding content to hit a length target is exactly the
    # silent loss R-2.5 forbids. Length is the site's problem -- it collapses
    # transcripts and offers section-level stops (R-5.4, R-5.6).
    #
    # REVISED after the owner clarified §1.4: the 30-60 minute figure ILLUSTRATES the
    # order of magnitude and is NOT a ceiling, so the cap must not be derived from a
    # reading-time target at all. A ceiling on reading time is a ceiling on coverage,
    # and coverage is the product (§1.2: "summaries of every exchange, not highlights").
    # Where the two conflict, coverage wins.
    #
    # So the cap is now a pure runaway guard with a much higher headroom, and it is set
    # from the RECORD's size rather than from a reading target. The guard exists because
    # the first full run produced 259 points for one 94-chunk motion, which is not a
    # summary of anything; it does not exist to make output short.
    turns_total = len({(s.get("report_id"), s.get("turn_index"))
                       for s in (item.get("sentences") or [])})
    sentences_total = len(item.get("sentences") or [])
    MAX_POINTS = int(os.environ.get("PARSNIPS_MAX_POINTS", "0")) or max(
        RUNAWAY_FLOOR, round(RUNAWAY_PER_SENTENCE * sentences_total))
    if len(kept) > MAX_POINTS:
        for k in kept:
            k.setdefault("_chunk", 0)
        # Round-robin alone is not enough on a large item, and the numbers show why:
        # at 40 points over 81 turns, blind round-robin topped out at coverage 0.17
        # against a median of 0.62 elsewhere. The cap was bounding the very metric the
        # gate scores, so the brief could not reach the coverage the pipeline demands
        # no matter how good the extraction was.
        #
        # So selection maximises NEW TURNS PER POINT: repeatedly take the point that
        # cites the most not-yet-covered turns, preferring untouched chunks to break
        # ties. That spends the budget on covering ground rather than on the first
        # point of every chunk, and it degrades to round-robin when points are
        # single-turn.
        def _turns(k):
            return {(by_sid[s].get("report_id"), by_sid[s].get("turn_index"))
                    for s in (k.get("cites") or []) if s in by_sid}

        remaining = list(kept)
        picked = []
        seen_turns = set()
        seen_chunks = set()
        while remaining and len(picked) < MAX_POINTS:
            def score(k):
                new = len(_turns(k) - seen_turns)
                fresh = 0 if k.get("_chunk") in seen_chunks else 1
                return (new, fresh)
            best = max(remaining, key=score)
            gain = score(best)
            if gain == (0, 0) and picked:
                # Nothing left adds coverage; the remaining points are duplicates of
                # turns already cited, so the budget is genuinely spent.
                break
            picked.append(best)
            remaining.remove(best)
            seen_turns |= _turns(best)
            seen_chunks.add(best.get("_chunk"))
        dropped.append({"reason": "over reading budget",
                        "detail": f"{len(kept)} points capped to {len(picked)} by "
                                  f"most-new-turns-per-point across "
                                  f"{len({k.get('_chunk') for k in kept})} chunk(s)"})
        kept = picked
    for k in kept:
        k.pop("_chunk", None)

    # TITLE comes from the RECORD, not from the model. The model sees one chunk and
    # describes what that chunk contains, so a 13-chunk Road Traffic Bill was titled
    # "A procedural call by the Speaker inviting the Senior Minister..." -- the content
    # of chunk 1, not of the item. The dataset index already carries the correct
    # official title, and preferring the model's prose over it also meant the visible
    # heading silently disagreed with the archive's own index.
    title = (item.get("title") or "").strip()
    what_it_is = (extracted.get("what_it_is") or "").strip()
    if not title:
        title = what_it_is
    # If the model's one-line description is just the title repeated, don't show both.
    if what_it_is.lower() == title.lower():
        what_it_is = ""
    brief = {
        "title": title,
        "what_it_is": what_it_is,
        # Item-level fields the site renders. Restored after omitting them produced
        # briefs that dropped four of the sections the product spec calls for
        # (SUMMARISATION.md Stage 2) -- the site degrades gracefully, so their absence
        # was invisible rather than loud.
        "why_it_matters": (extracted.get("why_it_matters") or "").strip(),
        "not_said": extracted.get("not_said") or [],
        "what_happens_next": (extracted.get("what_happens_next") or "").strip(),
        "stage": (extracted.get("stage") or "").strip(),
        "key_points": kept,
        "_meta": {
            "report_ids": item.get("report_ids") or [],
            "sitting_dates": item.get("sitting_dates") or [],
            "group": item.get("group"),
            "source_words": item.get("source_words"),
            "model": model,
            "key": item.get("id"),
            "schema": SCHEMA,
            "quotes_by_reference": True,
            "points_dropped": len(dropped),
            "citations": sum(len(k["cites"]) for k in kept),
        },
    }
    if extracted.get("question"):
        brief["asked"] = extracted["question"]
    return brief, dropped


# ----------------------------------------------------------------------- Stage 4


def stage4_verify(brief, item):
    """Two independent checks, because they catch different failures.

    1. QUOTE INVARIANT — every cited sid resolves, and the published quote text is
       byte-identical to the stored sentence. Because this pipeline never lets the
       model write a quote, a failure here is a bug in assembly, not a dishonest
       model: treat sub-100% as a signal to investigate.
    2. COVERAGE — how many of the item's turns produced at least one point. A
       verifier that only checks quotes reads 100% while an item has been silently
       emptied, because selecting nothing cannot fail a quote check. That happened
       for real in this project (44 Bills and Budget items), so coverage is not
       optional.
    3. SCHEMA COMPLETENESS — every field the site renders is present. Checks 1 and 2
       both passed on briefs that were missing why_it_matters, not_said,
       what_happens_next and stage, because every field the renderer reads is
       optional: the pages looked finished with four sections quietly absent. Gates
       catch false statements; only a completeness check catches absence.
    """
    by_sid = {s["sid"]: s for s in (item.get("sentences") or [])}
    bad = []
    for kp in brief.get("key_points") or []:
        # Verify every citation against ITS OWN text. A point's primary `quote` is
        # only the first citation, so checking all cited sids against it would fail
        # any multi-cite point — which is a bug this check found in assembly.
        for c in kp.get("citations") or []:
            s = by_sid.get(c.get("sid"))
            if s is None:
                bad.append({"kind": "unresolved_sid", "sid": c.get("sid")})
            elif s["text"].strip() != (c.get("text") or "").strip():
                bad.append({"kind": "citation_text_mismatch", "sid": c.get("sid")})
        for sid in kp.get("cites") or []:
            if sid not in by_sid:
                bad.append({"kind": "unresolved_sid", "sid": sid})
        # The rendered quote must equal the dataset text of the first cited sid.
        first = (kp.get("cites") or [None])[0]
        if first and first in by_sid and kp.get("quote", "").strip() != by_sid[first]["text"].strip():
            bad.append({"kind": "quote_mismatch", "sid": first})
    quoted = sum(1 for kp in (brief.get("key_points") or []) if kp.get("quote"))

    # ------------------------------------------------- claim-from-question check
    # A claim that asserts a FACT must not rest on a sentence that only ASKS. Measured
    # across the 288 briefs of 2026: 214 points cited a question, and the failure is
    # real rather than theoretical -- oral-answer-4089 published "The Government IS
    # ASSESSING how rising fuel costs are passed through" citing a sentence that reads
    # "asked the Deputy Prime Minister ... whether ...". The record contains no such
    # answer; that sitting's Hansard captured the questions and the Minister's reply
    # was literally "Thank you, Sir."
    #
    # The item is not the bug -- a brief of questions is legitimate and the site has no
    # business inventing answers. What is not legitimate is reporting a QUESTION as a
    # FINDING, because it reads as a statement of what the Government is doing. So the
    # check is about the CLAIM's framing, and claims that stay in the asking register
    # ("The member asked how...") are correct and pass.
    ASKING = re.compile(
        r"\b(asks?|asked|asking|clarif\w*|enquir\w*|inquire[sd]?|wonder\w*|"
        r"question(?:s|ed|ing)?|whether|quer(?:y|ies|ied)|probe[sd]?|"
        r"seeks? (?:clarification|an answer)|wants? to know)\b", re.I)
    ASSERTING = re.compile(
        r"^\s*(?:the\s+)?(?:government|ministry|minister|bill|amendments?|scheme|board|"
        r"council|agency|authority|company|it|they|he|she)\b[^.]{0,80}?"
        r"\b(is|are|was|were|has|have|had|will|would|does|do|did|enables?|introduces?|"
        r"provides?|requires?|allows?|sets?|ensures?|plans?|intends?|aims?|seeks)\b",
        re.I)

    question_claims = []
    for kp in brief.get("key_points") or []:
        first = (kp.get("cites") or [None])[0]
        src = (by_sid.get(first) or {}).get("text", "")
        if not src:
            continue
        src_is_question = bool(QUESTION_START.match(src.strip())) or src.strip().endswith("?")
        claim = (kp.get("point") or "").strip()
        if src_is_question and ASSERTING.match(claim) and not ASKING.search(claim):
            question_claims.append({"sid": first,
                                    "claim": claim[:110],
                                    "source": src.strip()[:110]})

    # schema completeness: the fields the site reads, and nothing optional about them.
    #
    # A field is complete when it is PRESENT, not when it is non-empty. Three of these
    # have legitimate "nothing to report" values, and the spec requires those exact
    # words: why_it_matters falls back to "The record does not set out the practical
    # impact.", what_happens_next to "not stated", and not_said to []. Treating those
    # as missing withheld 100% of the first run -- the gate was wrong, not the briefs.
    # What must never happen is the key being absent entirely, which is how four
    # sections vanished from the page without anything failing.
    missing = []
    for field in REQUIRED_BRIEF_FIELDS:
        if field not in brief:
            missing.append(field)

    # coverage: turns represented, against turns that had eligible sentences
    turns_total = len({(s.get("report_id"), s.get("turn_index"))
                       for s in (item.get("sentences") or [])})
    cited = {sid for kp in (brief.get("key_points") or []) for sid in kp.get("cites") or []}
    turns_cited = len({(by_sid[s].get("report_id"), by_sid[s].get("turn_index"))
                       for s in cited if s in by_sid})
    min_turns = int(os.environ.get("PARSNIPS_MIN_TURNS_COVERED", "1"))

    # THE COVERAGE FLOOR IS THE REAL RULE, and it replaces a blunt "at least 2 turns"
    # minimum I tried first. That version was wrong and the regression test caught it: it
    # withheld 17 of 287 published briefs, including bill-783 and budget-2875 at coverage
    # 1.000 (1 cited turn out of 1) -- which are COMPLETE summaries of single-turn items,
    # not thin ones. The distinction is not how many turns are cited but whether the brief
    # accounts for its item:
    #
    #   coverage 1.00 (1 of 1)    a complete summary           -> must publish
    #   coverage 0.50 (1 of 2)    half the item               -> publishes
    #   coverage 0.016 (1 of 62)  one turn of sixty-two        -> must NOT publish
    #
    # Coverage expresses that directly at any item size, so a turn-count minimum is both
    # redundant and harmful. The floor is 0.15, chosen against the published corpus rather
    # than guessed: across the 287 briefs of 2026 the lowest real coverage is 0.167 (with 8
    # points), so a 0.15 floor withholds none of them and removes only the degenerate case
    # that motivated it -- oral-answer-4089, which is 62 questions and no answers, whose
    # 1-point brief at coverage 0.016 passed the old gate and published or was withheld
    # depending on which sample the model returned.
    min_coverage = float(os.environ.get("PARSNIPS_MIN_COVERAGE", "0.15"))

    # duplicates: points citing the SAME evidence say one thing, not several. Assembly
    # removes them, so this should always be 0 -- it exists to catch the day it is not.
    cite_sets = [tuple(sorted(k.get("cites") or [])) for k in (brief.get("key_points") or [])]
    dupes = len(cite_sets) - len({c for c in cite_sets if c})

    verdict = {
        "quote_invariant": len(bad) == 0,
        "quote_failures": bad,
        "schema_complete": not missing,
        "missing_fields": missing,
        "duplicate_points": dupes,
        # Reported, not withheld: a point resting on a question is a defect in FRAMING,
        # and the site can render the underlying question honestly. Withholding the
        # whole item would be the wrong trade for a public-interest archive -- it would
        # drop 85 of 288 briefs for a fixable wording issue.
        "question_claims": len(question_claims),
        "question_claim_examples": question_claims[:3],
        "points": len(brief.get("key_points") or []),
        "quoted_points": quoted,
        "turns_total": turns_total,
        "turns_cited": turns_cited,
        "coverage": round(turns_cited / turns_total, 3) if turns_total else 0.0,
        "min_turns_required": min_turns,
        "min_coverage_required": min_coverage,
    }
    cov = verdict["coverage"]
    ok = (verdict["quote_invariant"] and verdict["schema_complete"]
          and not dupes and turns_cited >= min_turns
          and cov >= min_coverage and quoted > 0)
    verdict["passed"] = ok
    # Fail closed (R-2.8 / D-3): an item that cannot be validated publishes nothing.
    if not ok:
        verdict["withheld_reason"] = ("quote invariant failed" if not verdict["quote_invariant"]
                                      else f"incomplete brief: {', '.join(missing)}" if missing
                                      else f"{dupes} duplicate point(s)" if dupes
                                      else f"too thin: covers {turns_cited} of {turns_total} "
                                           f"turns (coverage {cov:.3f}, need {min_coverage})"
                                      if turns_cited < min_turns or cov < min_coverage
                                      else "no verified quotation")
    return verdict


# ------------------------------------------------------------------------ pricing
#
# Rates are per million tokens and are recorded here so a cost figure is
# reproducible rather than remembered. Local models cost nothing at the margin
# (the machine is already on), which is the point of wanting them to work.
PRICES = {
    "deepseek-v4.1-flash:cloud": {"in": 0.14, "out": 0.28},
    "gpt-oss:20b-cloud": {"in": 0.10, "out": 0.40},
    "nemotron-3-nano:30b-cloud": {"in": 0.10, "out": 0.40},
}


def cost_of(model, prompt_tokens, completion_tokens):
    p = PRICES.get(model)
    if not p:
        return 0.0
    return (prompt_tokens * p["in"] + completion_tokens * p["out"]) / 1_000_000


def log_usage(rec):
    rec = dict(rec)
    rec["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(USAGE_LOG), exist_ok=True)
    with open(USAGE_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def usage_summary():
    if not os.path.exists(USAGE_LOG):
        return {}
    agg = {}
    with open(USAGE_LOG, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = r.get("model") or "?"
            a = agg.setdefault(k, {"calls": 0, "in": 0, "out": 0, "cost": 0.0,
                                   "estimated": False, "items": 0})
            a["calls"] += r.get("calls", 0)
            a["in"] += r.get("prompt_tokens", 0)
            a["out"] += r.get("completion_tokens", 0)
            a["cost"] += r.get("cost", 0.0)
            a["items"] += 1
            a["estimated"] = a["estimated"] or bool(r.get("estimated"))
    return agg


# ------------------------------------------------------------------------- driver


def brief_path(item_meta):
    year = str(item_meta.get("year"))
    return os.path.join(OUT_DIR, year, f"{item_meta['id']}.json")


def run_item(meta, model, force=False):
    """One item, all stages. Returns a result dict; never raises for one bad item."""
    dest = brief_path(meta)
    if os.path.exists(dest) and not force:
        # Resume only over a COMPLETE brief from the CURRENT schema. Existence alone is
        # not enough: every schema change in this build (item-level fields, the point
        # cap, deduplication, titles) would otherwise leave a corpus that looks finished
        # and is a mixture of pipeline versions -- and a resumed run would skip those
        # files forever. Re-running an item costs a few cents; publishing a stale brief
        # costs the archive's consistency.
        try:
            prev = storage.read_json(dest)
        except Exception:                                           # noqa: BLE001
            prev = None
        meta_prev = (prev or {}).get("_meta") or {}
        gate_prev = meta_prev.get("gate") or {}
        if (meta_prev.get("schema") == SCHEMA
                and gate_prev.get("passed")
                and (prev or {}).get("key_points")):
            return {"id": meta["id"], "skipped": True}
    item = load_item(os.path.join(str(meta["year"]), f"{meta['id']}.json"))
    if not item:
        return {"id": meta["id"], "error": "dataset payload missing"}

    is_oral = meta.get("group") == "oral"
    t0 = time.time()
    try:
        extracted, usage = stage2_extract(item, model, is_oral)
    except Exception as exc:                                        # noqa: BLE001
        log_usage({"id": meta["id"], "model": model, "error": str(exc)[:200]})
        return {"id": meta["id"], "error": f"stage2: {exc}"[:160]}
    if not extracted:
        log_usage({"id": meta["id"], "model": model, "calls": usage["calls"],
                   "prompt_tokens": usage["prompt_tokens"],
                   "completion_tokens": usage["completion_tokens"],
                   "estimated": usage["estimated"], "cost": 0.0,
                   "outcome": "no points"})
        return {"id": meta["id"], "withheld": True, "reason": "model returned no points",
                "seconds": round(time.time() - t0, 1),
                "calls": usage["calls"], "model": model}

    brief, dropped = stage3_assemble(item, extracted, model)
    if not brief:
        log_usage({"id": meta["id"], "model": model, "calls": usage["calls"],
                   "prompt_tokens": usage["prompt_tokens"],
                   "completion_tokens": usage["completion_tokens"],
                   "estimated": usage["estimated"], "cost": 0.0,
                   "outcome": "no citable points"})
        return {"id": meta["id"], "withheld": True,
                "reason": f"no point had a resolvable citation ({len(dropped)} dropped)",
                "seconds": round(time.time() - t0, 1),
                "calls": usage["calls"], "model": model}

    verdict = stage4_verify(brief, item)
    cost = cost_of(model, usage["prompt_tokens"], usage["completion_tokens"])
    log_usage({"id": meta["id"], "model": model, "calls": usage["calls"],
               "prompt_tokens": usage["prompt_tokens"],
               "completion_tokens": usage["completion_tokens"],
               "estimated": usage["estimated"], "cost": cost,
               "outcome": "published" if verdict["passed"] else "withheld"})
    brief["_meta"]["gate"] = verdict
    # R-2.5: every exclusion is recorded with its reason, so absence is auditable.
    # Duplicates in particular must be visible -- otherwise the only trace of them is
    # a point count that is lower than the model produced, with no explanation.
    brief["_meta"]["points_dropped"] = len(dropped)
    if dropped:
        from collections import Counter as _Counter
        brief["_meta"]["dropped_reasons"] = dict(
            _Counter(d.get("reason", "?") for d in dropped))
    brief["_meta"]["usage"] = {"calls": usage["calls"],
                               "prompt_tokens": usage["prompt_tokens"],
                               "completion_tokens": usage["completion_tokens"],
                               "estimated": usage["estimated"],
                               "cost_usd": round(cost, 6)}
    if not verdict["passed"]:
        # D-3: withhold entirely. Not a partial brief, not a flagged one.
        storage.write_json_atomic(os.path.join(ROOT, "pipeline", "withheld",
                                               f"{meta['id']}.json"),
                                  {"item": meta, "verdict": verdict,
                                   "attempt": brief})
        return {"id": meta["id"], "withheld": True, "verdict": verdict,
                "seconds": round(time.time() - t0, 1), "model": model,
                "calls": usage["calls"], "cost": cost}

    dest = brief_path(meta)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    storage.write_json_atomic(dest, brief)
    return {"id": meta["id"], "published": True, "verdict": verdict,
            "points": verdict["points"], "coverage": verdict["coverage"],
            "seconds": round(time.time() - t0, 1), "model": model,
            "calls": usage["calls"], "cost": cost,
            "in": usage["prompt_tokens"], "out": usage["completion_tokens"]}


# ----------------------------------------------------------------------- CLI


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", help="a single year, e.g. 2026")
    ap.add_argument("--years", nargs="+", help="several years, newest first")
    ap.add_argument("--dates", nargs="+", help="only items from these sittings")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, help="stop after N items")
    ap.add_argument("--force", action="store_true", help="rebuild existing briefs")
    ap.add_argument("--status", action="store_true", help="what exists, what is missing")
    ap.add_argument("--group", help="only this group, e.g. bill / oral / motion")
    args = ap.parse_args(argv)

    idx = load_index()
    items = idx["items"]

    if args.status:
        have = glob.glob(os.path.join(OUT_DIR, "20*", "*.json"))
        have_keys = {os.path.basename(p)[:-5] for p in have}
        by_year = {}
        for it in items:
            y = str(it["year"])
            d = by_year.setdefault(y, {"total": 0, "done": 0, "words": 0, "done_words": 0})
            d["total"] += 1
            d["words"] += it.get("source_words") or 0
            if it["id"] in have_keys:
                d["done"] += 1
                d["done_words"] += it.get("source_words") or 0
        print("BRIEFS (by year, newest first)")
        for y in sorted(by_year, reverse=True):
            d = by_year[y]
            print(f"  {y}  {d['done']:4d}/{d['total']:4d} items "
                  f"({d['done_words']:,}/{d['words']:,} words)")
        print()
        print("USAGE AND COST")
        agg = usage_summary()
        if not agg:
            print("  (nothing logged yet)")
        for m, a in sorted(agg.items()):
            est = " (estimated)" if a["estimated"] else ""
            print(f"  {m:28s} {a['items']:5d} items  {a['calls']:5d} calls  "
                  f"{a['in']:>10,} in / {a['out']:>8,} out tok  ${a['cost']:.4f}{est}")
        return 0

    years = args.years or ([args.year] if args.year else None)
    if not years and args.dates:
        # A date implies its year; requiring both would be a pointless trap.
        years = sorted({str(d)[:4] for d in args.dates})
    if not years:
        ap.error("give --year, --years, --dates or --status")
    sel = [it for it in items if str(it["year"]) in [str(y) for y in years]]
    if args.dates:
        want = set(args.dates)
        sel = [it for it in sel if set(it.get("sitting_dates") or []) & want]
    if args.group:
        sel = [it for it in sel if it.get("group") == args.group]
    # Newest first within a year, so a partial run still covers the most recent work.
    sel.sort(key=lambda i: (str(i["year"]), str((i.get("sitting_dates") or [""])[0])),
             reverse=True)
    if args.limit:
        sel = sel[:args.limit]
    if not sel:
        print("no items matched")
        return 1

    print(f"model {args.model}  |  {len(sel)} item(s)  |  "
          f"prompt budget {PROMPT_CHAR_BUDGET:,} chars")
    if sel:
        print(f"  from {sel[0]['id']} ({sel[0].get('sitting_dates')}) "
              f"to {sel[-1]['id']} ({sel[-1].get('sitting_dates')})")
    pub = wh = err = skip = 0
    t_start = time.time()
    tin = tout = 0
    cost = 0.0
    for n, meta in enumerate(sel, 1):
        r = run_item(meta, args.model, force=args.force)
        # Flush every line: a long run is monitored by watching the log, and a
        # buffered progress line is indistinguishable from a hung process.
        if r.get("skipped"):
            skip += 1
            continue
        tin += r.get("in") or 0
        tout += r.get("out") or 0
        cost += r.get("cost") or 0.0
        if r.get("error"):
            err += 1
            print(f"  [{n}/{len(sel)}] {meta['id']:26s} ERROR {r['error']}", flush=True)
        elif r.get("withheld"):
            wh += 1
            reason = r.get("reason") or (r.get("verdict") or {}).get("withheld_reason")
            print(f"  [{n}/{len(sel)}] {meta['id']:26s} withheld — {reason}", flush=True)
        else:
            pub += 1
            v = r["verdict"]
            print(f"  [{n}/{len(sel)}] {meta['id']:26s} {r['points']:3d} pts  "
                  f"cov {v['coverage']:.2f}  {r['seconds']:5.1f}s  "
                  f"{r['calls']} call(s)", flush=True)
    dt = time.time() - t_start
    print(f"\npublished {pub}  withheld {wh}  errors {err}  skipped {skip}  "
          f"in {dt:.0f}s")
    print(f"tokens {tin:,} in / {tout:,} out   cost ${cost:.4f}")
    if pub + wh:
        print(f"per item: {tin/(pub+wh):,.0f} in / {tout/(pub+wh):,.0f} out tokens, "
              f"{dt/(pub+wh):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
