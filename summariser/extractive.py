"""
Deterministic-first summarisation: test the core claim.

CLAIM: most of what the reader needs is already in the structured data or is
extractable verbatim. An LLM should only be used where rewriting genuinely
adds value, and when it does, it should be given pre-selected verbatim
sentences rather than a 900k-char transcript.

WHY THIS FIXES CORRECTNESS
Extractive selection picks sentences FROM the transcript. The "quote" field is
therefore the sentence itself -- it cannot fail the verbatim gate, because it
already IS the transcript text. The gate stops being a filter for model honesty
and becomes an invariant. That removes the single largest source of
incorrectness: a model rewording a quotation.

WHAT IS MEASURED HERE (no LLM involved)
  1. How much can be derived with zero LLM calls?
  2. Sentence selection quality: do the selected sentences carry the substance?
  3. How big does the LLM's input become? (the chunking problem disappears if
     we only ever send pre-selected sentences)

Usage: python3 extractive.py
"""
import collections
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "summariser"))
import summarise as S  # noqa: E402

DATA = os.path.join(ROOT, "data")

# ---------------------------------------------------------------- text signals
# IMPORTANT: selection must read turn text through the SAME function the gate
# uses, or the gate cannot be an invariant.
#
# extractive.py originally called sentences(t["text"]) on the RAW turn text while
# build_chunks() -- and therefore verify_quotes() -- normalises with
# strip_speaker_labels() first. Hansard turns carry bracket markers such as
# "[(proc text) Debate resumed. (proc text)]" which survive raw selection but are
# stripped from the gate's reference text. Any sentence built around one of those
# markers could NEVER verify, no matter how faithful the model was.
#
# Measured consequence on the 213k item: 16/24 verbatim. Against the complete
# (untruncated) transcript with the mismatch fixed it is 24/24. The design doc
# attributed those misses to truncation alone and called the drops "correct
# behaviour"; truncation was part of it, but this mismatch was a real defect that
# would have looked like a model problem forever.
def turn_text(turn):
    """The ONLY way selection may read a turn.

    Mirrors summarise.build_chunks(): strip bracket asides, then use the text as-is.
    """
    return S.strip_speaker_labels(turn.get("text") or "")


# Cue phrases that mark substance rather than rhetoric. Kept deliberately
# conservative: a false positive here puts an empty sentence in the summary.
SUBSTANCE_CUES = [
    r"\bwill\b", r"\bfrom \d{4}\b", r"\bby \d{4}\b", r"\beffective\b",
    r"\bintroduce[ds]?\b", r"\bimplement", r"\ballocat", r"\bfund",
    r"\bcommitt", r"\bentitle", r"\beligib", r"\bsubsid", r"\bgrant",
    r"\brelief\b", r"\bcap(?:ped)?\b", r"\bceiling\b", r"\bthreshold\b",
    r"\bper cent\b", r"\bpercent\b", r"[$£]\d", r"\b\d[\d,.]*\s*(?:million|billion|m|k)\b",
    r"\bwe (?:are|will|have|intend|plan)\b", r"\bthe (?:Ministry|Government|Board)\b",
]
SUBSTANCE_RE = re.compile("|".join(SUBSTANCE_CUES), re.I)

# Rhetorical / procedural noise that should never reach a summary.
# NB "mdm" as well as "madam": Hansard uses both, and the miss let the chair's
# consent-seeking sentence through as the only pick on a 150-word item.
NOISE_RE = re.compile(
    r"^(?:thank|i thank|mr speaker|mdm speaker|madam speaker|mr deputy speaker|"
    r"mdm|madam|sir|may i|i beg|with your permission|"
    r"i am happy|i would like to|let me|firstly|secondly|finally|in conclusion)\b", re.I)

# Procedural / chair business. is_procedural on the turn is nearly useless: measured
# on a 560-turn sitting only 1 turn carried it, so the flag cannot carry this load.
# These patterns catch the chair's housekeeping instead.
#
# ANCHORING IS LOAD-BEARING -- this is the subtle one.
# Hansard appends parser markers to the END of a turn, e.g. a 10,046-char ministerial
# speech whose text finishes "... (proc text) Question put, and agreed to. (proc
# text)]". An UNANCHORED `proc text` pattern therefore matched inside that speech and
# discarded the entire turn, leaving whole Bills and Budget items selecting ZERO
# sentences (measured: 44 bill/budget items, up to 3,918 words each). So:
#   * `(proc text)` is only procedural when the turn STARTS with it -- a turn that is
#     nothing BUT a marker. Mid-turn markers are trailing artefacts, not content.
#   * Consent/assent seeking must match mid-sentence ("Mdm Speaker, may I seek your
#     consent and the general assent of Members present to now move a Business
#     Motion…"), so those stay unanchored -- but they are specific enough not to fire
#     on ordinary debate.
#   * `motion (?:made|put)` was dropped entirely: "Question put" already covers the
#     chair's action, and "motion … put" risks matching substantive motion debate.
PROCEDURAL_RE = re.compile(
    # a turn that is ONLY a parser marker
    r"^\W*\[?\(?\s*proc text\b"
    # chair housekeeping that begins the sentence
    r"|^(?:order (?:read|for)|question (?:put|proposed)|debate (?:resumed|adjourned)|"
    r"sitting (?:suspended|resumed)|the house (?:adjourned|resumed)|"
    r"clerk (?:will|read)|leave of absence|"
    r"papers? (?:laid|presented)|bill (?:read|introduced)|"
    r"the house will now|i shall now put the question)\b"
    # consent / assent seeking, wherever it sits in the sentence
    r"|\bseek(?:s|ing)? (?:your|the)\b[^.]{0,40}\b(?:consent|assent)\b"
    r"|\b(?:general )?assent of (?:hon )?members\b"
    r"|\bhave the general assent\b"
    r"|\bi give my consent\b"
    r"|\bpermission of the house\b"
    r"|^so,? just to confirm\b"
    r"|^leader of the opposition,? you wanted\b"
    r"|^(?:yes|no|sir|madam|correct|agreed|noted)\.?$",
    re.I)

SPEAKER_ROLE_RE = re.compile(r"\b(?:Minister|Senior Minister|Parliamentary Secretary|"
                             r"Speaker|Deputy Speaker|Mr|Ms|Mrs|Dr|Prof|Assoc Prof)\b")


def is_procedural(text):
    """True for housekeeping that should never reach a summary.

    Exposed as a function (not a regex constant) so the pipeline and the tests can
    ask the same question.
    """
    return bool(PROCEDURAL_RE.search(text or ""))


MIN_WORDS = 8
MAX_WORDS = 60


def re_split_sentences(text):
    """Sentence split, no filtering. The caller decides what to drop.

    Kept separate from sentences() so Stage 1 can RECORD every exclusion
    (procedural, courtesy, too short) instead of having sentences() silently
    swallow them. An exclusion that is not recorded is indistinguishable from a
    bug -- exactly how an unanchored pattern once emptied whole items unnoticed.
    """
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(])", text or "") if p.strip()]


def sentences(text, min_words=MIN_WORDS, max_words=MAX_WORDS, allow_long=False):
    """Split into sentences, dropping fragments and overlong runs.

    max_words guards against clause-heavy formalities, but a long sentence can still
    carry real substance (a Minister listing measures in one breath). Rather than
    discard it, callers that need coverage pass allow_long=True and keep it.
    """
    out = []
    for p in re_split_sentences(text):
        n = len(p.split())
        if n < min_words or (n > max_words and not allow_long):
            continue
        if NOISE_RE.match(p) or is_procedural(p):
            continue
        out.append(p)
    return out


def score(sent, position_frac, total_turns):
    """Deterministic sentence salience. No model, reproducible.

    Deliberately simple and explainable: substance cues dominate, then figures,
    then early position (the mover's framing), with a small penalty for very
    long sentences that are usually clause-heavy formalities.
    """
    s = 0.0
    hits = len(SUBSTANCE_RE.findall(sent))
    s += min(hits, 4) * 2.0
    if re.search(r"[$£]\d|\b\d[\d,.]*\s*(?:million|billion)\b", sent):
        s += 3.0
    if re.search(r"\b(?:will|shall|commit|intend)\b", sent, re.I):
        s += 2.5
    # earlier content is usually the substantive framing
    s += (1.0 - position_frac) * 2.0
    # prefer mid-length: very short is often a courtesy, very long is often procedural
    n = len(sent.split())
    if 12 <= n <= 40:
        s += 1.0
    if n > 50:
        s -= 1.0
    # a named speaker role adds authority but not substance; keep it neutral
    return s


def select_turns(item, max_sentences=24):
    """Pick the highest-scoring verbatim sentences across the item's turns.

    Round-robins across turns first so one long speech cannot monopolise the
    summary, then fills by score.

    Reads turn text through turn_text() so what is selected matches what the gate
    checks. Returns [{speaker, sentence, score, turn_index}] -- `sentence` is
    verbatim by construction, which is the whole point.
    """
    picked = []
    turns = []
    # Speaker attribution is incomplete in the source: measured across the corpus,
    # 3,802 of 94,272 turns (4.0%) carry no speaker at all, yet many are substantive
    # (a 438-word ministerial response with no name). Selecting from them without a
    # speaker would produce a brief whose citation says "someone said this".
    #
    # So carry the last known speaker forward when a turn lacks one. This mirrors what
    # the site already does for oral answers, where compute_panels() resolves the
    # responder by ROLE (is_role_turn) rather than trusting the per-turn field. A
    # carried-forward name is a best guess, not an assertion -- see the "attributed"
    # flag below, which the pipeline can surface or suppress.
    last_speaker = None
    for r in item["reports"]:
        for t in r.get("turns", []):
            txt = turn_text(t)
            spk = (t.get("speaker") or "").strip() or None
            if spk:
                last_speaker = spk
            if not txt:
                continue
            # The per-turn is_procedural flag is nearly useless (1 of 560 turns on a
            # measured sitting), so also screen the whole turn with the pattern.
            if t.get("is_procedural") or is_procedural(txt):
                continue
            ss = sentences(txt, allow_long=True)
            if ss:
                turns.append((spk or last_speaker, ss, bool(spk)))

    # pass 1: best sentence from each turn, in order (preserves the debate shape)
    for ti, (spk, ss, attributed) in enumerate(turns):
        best, bs = None, -1
        for i, s in enumerate(ss):
            sc = score(s, i / max(1, len(ss)), len(turns))
            if sc > bs:
                best, bs = s, sc
        if best and bs > 2.0:
            picked.append({"speaker": spk, "sentence": best,
                           "score": round(bs, 2), "turn_index": ti,
                           "attributed": attributed})

    # pass 2: fill with remaining high scorers if we are short
    if len(picked) < max_sentences:
        seen = {p["sentence"] for p in picked}
        rest = []
        for ti, (spk, ss, attributed) in enumerate(turns):
            for i, s in enumerate(ss):
                if s in seen:
                    continue
                sc = score(s, i / max(1, len(ss)), len(turns))
                if sc > 3.0:
                    rest.append({"speaker": spk, "sentence": s,
                                 "score": round(sc, 2), "turn_index": ti,
                                 "attributed": attributed})
        rest.sort(key=lambda x: -x["score"])
        picked.extend(rest[: max_sentences - len(picked)])

    return picked[:max_sentences]


def verbatim_check(item, picked=None):
    """Self-check: every selected sentence MUST appear in the item's full transcript.

    This is the invariant Stage 4 relies on. It is exposed here so the pipeline can
    assert it rather than hope -- a failure means the selector and the gate disagree
    about what the source text is, which is exactly the bug that made 8 of 24
    selections unverifiable on the 213k item.
    """
    picked = picked if picked is not None else select_turns(item)
    full = "\n\n".join(S.build_chunks(item, budget=10 ** 9))
    t_norm = S.norm(full)
    misses = [x for x in picked if S.norm(x["sentence"]) not in t_norm]
    return len(picked) - len(misses), len(picked), misses


def main():
    sittings = []
    for year in sorted(os.listdir(DATA)):
        d = os.path.join(DATA, year)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.startswith("sitting_") and fn.endswith(".json"):
                sittings.append(json.load(open(os.path.join(d, fn))))

    items = S.summarisable_items(sittings)
    items.sort(key=lambda i: i.get("words", 0))
    sample = [items[0], items[len(items) // 2], items[-1]]

    print("=" * 88)
    print("DETERMINISTIC-ONLY YIELD")
    print("=" * 88)
    for it in sample:
        full, _ = S.merge_transcript(it)
        sel = select_turns(it)
        sel_chars = sum(len(x["sentence"]) for x in sel)
        print(f"\n{it.get('words'):>8,d}w  {it.get('group'):10s} {str(it.get('title'))[:50]}")
        print(f"    transcript      : {len(full):>9,} chars")
        print(f"    LLM input needed: {sel_chars:>9,} chars   "
              f"({sel_chars/max(1,len(full))*100:.1f}% of transcript)")
        print(f"    sentences picked: {len(sel)}")

        # the correctness claim: every picked sentence is verbatim by construction.
        # Checked against the COMPLETE transcript, not merge_transcript's capped view
        # -- measuring against the capped text is what made this look like 16/24.
        got, tot, misses = verbatim_check(it, sel)
        flag = "" if got == tot else "   <-- SELECTOR/GATE MISMATCH"
        print(f"    verbatim vs COMPLETE transcript: {got}/{tot}{flag}")

        print("    top picks:")
        for x in sorted(sel, key=lambda y: -y["score"])[:4]:
            print(f"      [{x['score']:>5.2f}] {str(x['speaker'])[:26]:28s} {x['sentence'][:74]}")

    # whole-corpus sizing
    print("\n" + "=" * 88)
    print("WHOLE CORPUS: how much would an LLM ever need to read?")
    print("=" * 88)
    tot_full = tot_sel = 0
    over_ctx = 0
    bad = []
    for it in items:
        full, _ = S.merge_transcript(it)
        sel = select_turns(it)
        sc = sum(len(x["sentence"]) for x in sel)
        tot_full += len(full)
        tot_sel += sc
        if len(full) > 60000:
            over_ctx += 1
        got, tot, misses = verbatim_check(it, sel)
        if got != tot:
            bad.append((it.get("title", "")[:44], got, tot))
    print(f"  items                     : {len(items):,}")
    print(f"  transcript chars total    : {tot_full:>12,}")
    print(f"  LLM input if pre-selected : {tot_sel:>12,}  ({tot_sel/tot_full*100:.1f}%)")
    print(f"  items over 60k chars      : {over_ctx:,}  (these broke the small models)")
    print(f"  ...but their SELECTED text fits a small window, so chunking is not needed")
    # The invariant the design depends on: selection is verbatim against the COMPLETE
    # transcript. Any failure here is a bug in extraction, not model behaviour.
    print()
    if bad:
        print(f"  !! SELECTOR/GATE MISMATCH on {len(bad)} of {len(items)} items:")
        for t, g, n in bad[:10]:
            print(f"       {g}/{n}  {t}")
        print("     This breaks the Stage 4 invariant and must be fixed before use.")
    else:
        print(f"  INVARIANT HOLDS: every selected sentence is verbatim across all "
              f"{len(items):,} items.")

    # COMPANION COVERAGE CHECK -- the invariant above cannot catch under-selection,
    # because selecting NOTHING passes a quote check trivially. This is exactly how a
    # bug that emptied 44 Bills and Budget items stayed invisible while the verbatim
    # check read 100%. Both checks are required.
    print()
    sizes = collections.Counter()
    zero, thin, unattributed, total_sel = [], [], 0, 0
    for it in items:
        sel = select_turns(it)
        n = len(sel)
        total_sel += n
        sizes["0" if n == 0 else "1-2" if n < 3 else "3-9" if n < 10
              else "10-23" if n < 24 else "24"] += 1
        if n == 0:
            zero.append(it)
        elif n < 3:
            thin.append((n, it))
        unattributed += sum(1 for x in sel if not x.get("attributed"))
    print(f"  COVERAGE: {total_sel:,} sentences selected across {len(items):,} items")
    print(f"    selection size distribution: {dict(sizes)}")
    print(f"    items selecting ZERO       : {len(zero)}  (procedural business -- "
          f"these get no brief, by design)")
    print(f"    items selecting only 1-2   : {len(thin)}  (thin evidence; a brief "
          f"built from these would be close to a quotation)")
    print(f"    sentences with a CARRIED speaker: {unattributed:,} "
          f"({unattributed/max(1,total_sel)*100:.1f}%)")
    if zero:
        print("    zero-selection items:")
        for it in sorted(zero, key=lambda x: -x.get("words", 0))[:6]:
            print(f"      {it.get('words'):>6,d}w {it.get('group'):10s} "
                  f"{str(it.get('title'))[:48]}")
    if thin:
        print("    thinnest non-empty items:")
        for n, it in sorted(thin, key=lambda p: -p[1].get("words", 0))[:6]:
            print(f"      {n} sent  {it.get('words'):>6,d}w {it.get('group'):10s} "
                  f"{str(it.get('title'))[:44]}")


if __name__ == "__main__":
    main()
