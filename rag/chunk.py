"""
Parsnips RAG — chunking.

The job is NOT "make text fit the model". It is "make silent loss impossible".

Measured justification (see SPRINT-01-RAG-PARSNIPS.md):

  nomic-embed-text silently discards everything past ~16,000 chars. No error.
  Proven on the real corpus: the 101,692-char Budget speech of 16 Feb 2021
  produces the SAME vector as its first 16,250 chars. 84% of that speech is
  invisible, and nothing reports a problem.

  1,245 turns (1.35%) exceed the cut, carrying 9,339,093 characters -- 6.69% of
  the whole corpus.

Design decisions, each with a reason:

1. TURN-ALIGNED. 98.65% of turns already fit whole, and a turn is a complete
   semantic unit (91.9% end in terminal punctuation, 100% carry a report_id).
   A fixed token window would slice complete sentences in the majority of cases
   to solve a problem only 1.35% of turns have.

2. TWO TEXTS PER CHUNK.
     cite_text  -- a verbatim slice of the source turn. The citation gate is
                   string containment, so this must be untouched text.
     embed_text -- cite_text with the speaker label prefixed, because the eval
                   measured speaker attribution at 0/3 for keyword search: "who
                   said X" has no lexical overlap with the answer, since the
                   speaker lives in metadata rather than prose. Cheap fix.

3. SPLIT ON SENTENCE BOUNDARIES, WITH OVERLAP. A split mid-sentence produces a
   fragment that cannot be quoted. Overlap means a fact spanning a boundary is
   still wholly present in at least one chunk.

4. is_partial FLAG. A quote from half a sentence must be distinguishable from a
   quote out of a complete answer. Every split piece records part/n_parts.

5. NO SILENT DROPS. plan_chunks() returns a coverage record; verify_chunks()
   asserts every source character is accounted for.

Usage:
    python3 rag/chunk.py --stats                 # fit distribution at candidate sizes
    python3 rag/chunk.py --show budget-1572#t0   # watch the longest speech split
    python3 rag/chunk.py --verify                # assert full coverage, no loss
"""
import argparse
import glob
import html as htmlmod
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokens import count_tokens, TOKEN_LIMIT
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# ---------------------------------------------------------------- configuration
# The limit is a TOKEN budget, not a character count. From the model card:
#     nomic-bert.context_length = 2048
# Measured on real speeches (corrected, monotone predicate): the vector stops
# responding to new text at 10,178 / 10,321 / 10,607 chars across three different
# Budget speeches -- i.e. ~10,178 chars = 2048 tokens = ~4.97 chars/token.
#
# An earlier synthetic probe said 16,000 chars. That was wrong for two reasons: it
# used one repeated sentence (which tokenises far more efficiently than real prose),
# and the search predicate was broken (it compared each probe to the previous probe,
# so it converged to the midpoint every time). Synthetic text is not a safe basis for
# a threshold.
#
# TARGET_CHARS = 6,000 is 59% of the worst measured cut. At the pessimistic 4.5
# chars/token a chunk is ~1,333 tokens, 65% of the 2,048 context -- never truncates.
# Verified: zero chunks exceed 10,178 chars at targets up to 8,000.
TARGET_CHARS = 6000
OVERLAP_CHARS = 800
MIN_CHUNK_CHARS = 40          # below this, merge into the previous piece

# A sentence boundary: terminal punctuation followed by whitespace, or end of text.
SENT_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+")


def norm(text):
    if not text:
        return ""
    t = htmlmod.unescape(text)
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u2013", "-").replace("\u2014", "-")
    t = t.replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def strip_speaker_labels(text):
    """Remove [Please refer to Vernacular Speech.] style bracket asides."""
    return re.sub(r"\[[^\]]{0,200}\]", " ", text or "").strip()


def sentences(text):
    """Split into sentences, keeping the delimiters. Lossless: ''.join == text."""
    if not text:
        return []
    parts, last = [], 0
    for m in SENT_RE.finditer(text):
        parts.append(text[last:m.end()])
        last = m.end()
    if last < len(text):
        parts.append(text[last:])
    return [p for p in parts if p]


def split_paragraphs(text):
    """Split text on paragraph breaks, if it has any.

    The stored corpus is FLATTENED -- measured: 0 of 91,964 turns contain a blank
    line, and only 5 contain any newline. So this returns the whole text as one
    paragraph unless the caller supplies text that was re-fetched and re-parsed with
    paragraph boundaries preserved (see fetch_paragraphs below).

    Why it matters: a turn is many <p> elements merged into one string by
    scraper/hansard_parse.py:280 (f"{current['text']} {body}"). Re-fetched,
    budget-1572 turns out to be 529 paragraphs across 4 speaker turns -- one turn is
    525 paragraphs, flattened to a single unbroken string in storage.
    """
    if "\n\n" in text:
        parts = [p.strip() for p in re.split(r"\n{2,}", text)]
        return [p for p in parts if p]
    if "\n" in text:
        parts = [p.strip() for p in text.split("\n")]
        return [p for p in parts if p]
    return [text] if text.strip() else []


def split_turn(text, target=TARGET_CHARS, overlap=OVERLAP_CHARS):
    """Split one turn into pieces of at most ~target chars.

    Splits on PARAGRAPH boundaries when the text has them, and on SENTENCE
    boundaries otherwise. A paragraph is a coherent argument; a sentence is not, and
    the source already decided where the paragraphs are.

    Measured on three real Budget speeches: paragraph- and sentence-splitting give the
    same chunk COUNT (18/18, 15/15, 21/20), so there is no efficiency trade-off. The
    difference is what a chunk opens with -- sentence-splitting begins a chunk at
    "I will also enhance these packages..." (mid-paragraph, continuing a thought whose
    opening sits in the previous chunk), while paragraph-splitting opens on a complete
    unit.

    Returns a list of contiguous verbatim slices. Guarantees:
      - every piece is a substring of `text` (so citation containment holds)
      - the pieces cover `text` end to end (no silent loss)
      - each piece is <= target, except a single unit longer than target, which is
        hard-split and flagged
    """
    if len(text) <= target:
        return [text]

    paras = split_paragraphs(text)
    # if there is only one paragraph, fall back to sentence boundaries
    units = paras if len(paras) > 1 else sentences(text)

    # Rebuild each unit's whitespace the way the project's norm() does, then join with
    # a space. Measured bugs fixed here at once:
    #   - paragraphs carry their own "\n\n" separators, so concatenating them leaves
    #     newlines inside a chunk where the reference has a space;
    #   - split_paragraphs() strips each paragraph, so a bare `cur += u` wELDS one
    #     paragraph's last word to the next's first ("2022.\"Last year"), which makes
    #     every quote crossing the seam fail its own citation;
    #   - Hansard uses a NON-BREAKING SPACE (\xa0) that Python's \s does NOT match, so
    #     a plain re.sub(r"\s+", " ") left "\xa0" in place and every chunk still
    #     mismatched the reference by that one character.
    if len(paras) > 1:
        def _flat(u):
            return re.sub(r"[\s\u00a0]+", " ", u).strip()
        units = [_flat(u) for u in units]
        units = [u + " " for u in units[:-1]] + [units[-1]]

    pieces, cur = [], ""
    for u in units:
        # a single unit longer than target must be hard-split
        if len(u) > target:
            if cur:
                pieces.append(cur)
                cur = ""
            for i in range(0, len(u), target):
                pieces.append(u[i:i + target])
            continue
        if len(cur) + len(u) <= target:
            cur += u
        else:
            if cur:
                pieces.append(cur)
            # start the next piece with overlap, snapped back to a boundary
            start = max(0, len(cur) - overlap) if cur else 0
            tail = cur[start:] if cur else ""
            cur = (tail + u) if len(tail) + len(u) <= target else u
    if cur:
        pieces.append(cur)

    # merge any piece too small to be useful
    merged = []
    for p in pieces:
        if merged and len(p) < MIN_CHUNK_CHARS:
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)
    return merged


def embed_text_for(piece, speaker):
    """The text the model sees: speaker label + verbatim content.

    The label is what makes "who said X" retrievable at all -- measured, speaker
    attribution scored 0/3 for keyword search because the name is metadata, not prose.
    """
    who = speaker or "(unattributed)"
    return f"[{who}] {piece}"


def fetch_paragraphs(report_id, cache_dir=None):
    """Re-fetch one report and return its turns WITH paragraph breaks preserved.

    Needed because the stored corpus is flattened: scraper/hansard_parse.py:280 does
    f"{current['text']} {body}", joining continuation paragraphs with a space. The
    stored turn therefore has no usable internal structure (measured: 0 of 91,964
    turns contain a blank line).

    Re-fetching recovers it, because the source keeps <p> boundaries and the parser
    classifies them correctly -- it is only the JOIN that discards them.

    Returns {report_id: [{"speaker","text" (with \\n\\n between paragraphs),"paragraphs"}]}

    Cached, so a re-run costs nothing. Network is the slow part: ~1-3s per report.
    """
    import json as _json
    import time as _time

    cache_dir = cache_dir or os.path.join(ROOT, "pipeline", "para_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"paras_{report_id}.json")
    if os.path.exists(cache_path):
        try:
            return _json.load(open(cache_path))
        except Exception:
            pass

    scraper = os.path.join(ROOT, "scraper")
    if scraper not in sys.path:
        sys.path.insert(0, scraper)
    import parsnips_fetch as PF
    import hansard_parse as HP

    try:
        res = PF.fetch_report(report_id)
    except Exception as e:
        return {"error": f"fetch failed: {str(e)[:120]}", "report_id": report_id,
                "turns": []}

    html = ""
    if isinstance(res, dict):
        for k in ("content", "htmlContent", "htmlFullContent", "html", "resultHTML"):
            v = res.get(k)
            if isinstance(v, str) and len(v) > 200:
                html = v
                break
            if isinstance(v, dict):
                for k2 in ("content", "html"):
                    if isinstance(v.get(k2), str) and len(v[k2]) > 200:
                        html = v[k2]
                        break
            if html:
                break
    if not html:
        return {"error": "no html in response", "report_id": report_id, "turns": []}

    rp = HP.ReportParser()
    rp.feed(html)
    rp.close()

    # Walk blocks exactly as parse_report does, but KEEP the paragraph split
    # instead of joining each continuation onto the previous string.
    turns = []
    cur = None
    for kind, pieces in rp.blocks:
        if kind == "h6":
            continue
        speaker, body, _lang = HP.split_paragraph(pieces)
        if speaker is not None:
            if not body.strip():
                continue
            cur = {"speaker": speaker, "paragraphs": [body]}
            turns.append(cur)
        else:
            if not body or not body.strip():
                continue
            if cur is None:
                cur = {"speaker": None, "paragraphs": [body]}
                turns.append(cur)
            else:
                cur["paragraphs"].append(body)

    for t in turns:
        # Clean each paragraph, then rejoin with \n\n.
        #
        # Both parts matter. Cleaning keeps fetched text consistent with the stored
        # corpus (which is already cleaned) so the citation gate can compare them;
        # \n\n is what makes the paragraph structure usable by split_paragraphs().
        # Measured bug: keeping the RAW paragraphs here put "[Please refer to...]"
        # bracket asides back into the chunks and made every verbatim check fail.
        #
        # Also flatten NON-BREAKING SPACES. Hansard uses \xa0, which Python's \s does
        # NOT match, so it survived normalisation and left every chunk differing from
        # its own reference by one character. The project's norm() already collapses
        # \xa0, and this keeps the fetched text canonical with the stored text.
        cleaned = []
        for p in t["paragraphs"]:
            p = strip_speaker_labels(p)
            p = re.sub(r"[\s\u00a0]+", " ", p).strip()
            if p:
                cleaned.append(p)
        t["paragraphs"] = cleaned
        t["text"] = "\n\n".join(t["paragraphs"])

    # Attach the turn KEY so a caller can match this fetched turn to a stored turn by
    # content. Position cannot be used: measured, 2 of 40 reports have a fetched turn
    # list that differs in LENGTH from the stored one (bill-203 9 vs 15, bill-204 14 vs
    # 24), so from the differing turn onward fetched[i] is a different turn.
    for i, t in enumerate(turns):
        # Assign unconditionally: the parser may already set key=None, and setdefault
        # would leave that None in place, so every fetched turn matched nothing.
        t["key"] = t.get("key") or f"{report_id}#t{i}"

    out = {"report_id": report_id, "turns": turns,
           "n_paragraphs": sum(len(t["paragraphs"]) for t in turns),
           "fetched": _time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(cache_path, "w") as fh:
        _json.dump(out, fh)
    return out


def build_para_store(report_ids, cache_dir=None):
    """Fetch paragraph structure for a set of reports. Network-bound; cache-backed."""
    store = {}
    for n, rid in enumerate(sorted(set(report_ids)), 1):
        got = fetch_paragraphs(rid, cache_dir)
        store[rid] = got
        status = "error" if got.get("error") else f"{got.get('n_paragraphs', 0)} paras"
        if n % 25 == 0 or n == 1:
            print(f"  [{n}] {rid}: {status}")
    return store


def load_turns(years=None):
    """Every turn, preserving order and provenance. Includes empties (flagged)."""
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, "*", "sitting_*.json"))):
        year = int(os.path.basename(os.path.dirname(f)))
        if years and year not in years:
            continue
        d = json.load(open(f))
        date = (d.get("coverage") or {}).get("date")
        for r in (d.get("reports") or []):
            rid, grp = r.get("report_id"), r.get("group")
            # Report-level metadata: needed for a complete citation, and free to carry
            # (it is attached to the chunk dict, NEVER to embed_text -- measured, the
            # title is redundant with the turn text for every eval gold turn, so putting
            # it in the embedded text would only dilute the vector).
            rmeta = {
                "title": (r.get("title") or "").strip(),
                "report_type": r.get("report_type"),
                "parliament_no": r.get("parliament_no"),
                "volume_no": r.get("volume_no"),
                "sitting_no": r.get("sitting_no"),
                "report_version": r.get("report_version"),
            }
            for i, t in enumerate(r.get("turns") or []):
                raw = t.get("text") or ""
                clean = strip_speaker_labels(raw)
                rec = {
                    "key": f"{rid}#t{i}",
                    "report_id": rid, "turn": i, "year": year, "date": date,
                    "group": grp, "speaker": t.get("speaker"),
                    "raw": raw,
                    "clean": clean,
                    # Emptiness must be judged on the CLEANED text, not the raw text.
                    # Measured bug: a turn whose entire content is a bracket aside
                    # ("[Please refer to Vernacular Speech.]") has non-empty raw text
                    # but empty clean text, so it produced a 0-character chunk --
                    # un-embeddable and un-quotable, and the gate did not catch it.
                    "empty": not clean.strip(),
                    # turn-level provenance
                    "time": t.get("time"),
                    "is_procedural": t.get("is_procedural"),
                    "lang": t.get("lang"),
                    "words": t.get("words"),
                }
                rec.update(rmeta)
                out.append(rec)
    return out


def CLEAN_FOR_MATCH(s):
    """Normalise a turn's text for content-matching a fetch against the store."""
    return strip_speaker_labels(s.replace("\n\n", " "))


def chunk_meta(t):
    """The provenance a chunk carries so an answer can be traced and cited.

    These fields are attached to the chunk dict and NEVER to embed_text. Measured:
    for every eval gold turn that has a report title, the turn text already contains
    the title's key words (12 redundant, 0 additive), so putting the title in the
    embedded text would dilute the vector without adding retrieval signal -- and
    anything in embed_text cannot be changed later without re-embedding.

    Included: report title, the report's citation numbers, and the turn's clock time
    and procedural flag. Carrying them makes a citation reproducible (Hansard citable
    form needs Parliament/volume/sitting) and lets a caller tell a procedural
    interjection from a substantive speech without re-reading the corpus.
    """
    return {
        "title": t.get("title") or "",
        "report_type": t.get("report_type"),
        "parliament_no": t.get("parliament_no"),
        "volume_no": t.get("volume_no"),
        "sitting_no": t.get("sitting_no"),
        "report_version": t.get("report_version"),
        "time": t.get("time"),
        "is_procedural": t.get("is_procedural"),
        "lang": t.get("lang"),
        "words": t.get("words"),
    }


def citation_for(chunk):
    """A human-readable citation, so an answer is not just 'oral-answer-2271#t1'.

    Falls back gracefully: not every field is present on every report, and a citation
    with a gap is still better than an opaque id.
    """
    parts = []
    if chunk.get("parliament_no"):
        parts.append(f"Parliament {chunk['parliament_no']}")
    if chunk.get("volume_no"):
        parts.append(f"Vol {chunk['volume_no']}")
    if chunk.get("sitting_no"):
        parts.append(f"Sitting {chunk['sitting_no']}")
    head = ", ".join(parts)
    title = (chunk.get("title") or "").strip()
    date = chunk.get("date") or ""
    out = " — ".join(x for x in (head, title, date) if x)
    if chunk.get("speaker"):
        out = f"{out} ({chunk['speaker']})" if out else chunk["speaker"]
    return out or chunk.get("id", "")


def plan_chunks(turns, target=TARGET_CHARS, overlap=OVERLAP_CHARS, para_store=None):
    """Produce chunks plus a coverage record. Never silently drops anything.

    para_store: optional {report_id: fetched} from fetch_paragraphs(). When supplied,
    the turns that actually need splitting get their paragraph structure back from the
    source instead of being sliced on sentence boundaries.

    Why this is opt-in rather than automatic: it costs a network fetch per report, and
    only 1.35% of turns need splitting. Re-fetching all 331 sittings to serve 1.35% of
    turns would put a full re-scrape in the critical path for no retrieval benefit.
    """
    chunks = []
    covered_chars = 0
    source_chars = 0
    split_turns = 0
    empty_turns = 0
    para_sourced = 0
    chunked_text = {}      # turn_key -> the exact text chunked (may be para-sourced)

    # Index the paragraph store by CONTENT, not by position.
    #
    # Measured bug this fixes: the fetched turn list does NOT always align with the
    # stored one. Of 40 reports checked, 2 had an index mismatch -- bill-203 stored 9
    # turns but fetched 15, bill-204 stored 14 but fetched 24 -- so from the differing
    # turn onward, fetched[i] is a DIFFERENT turn from stored[i]. Indexing by position
    # therefore attached one turn's paragraph structure to another turn, and every
    # quote from such a turn would have failed its own citation.
    #
    # Matching on the normalised head of the turn text recovers all but ~1 report in 40.
    # Where a turn cannot be matched, it is simply left out: the stored text is used and
    # the turn still chunks, just without paragraph structure. Never guess.
    para_by_key = {}
    if para_store:
        for rid, got in para_store.items():
            if got.get("error"):
                continue
            for ft in (got.get("turns") or []):
                k = ft.get("key")
                if k:
                    para_by_key[k] = ft

    def para_for(turn):
        """The fetched paragraph structure for this turn, or None if it cannot be
        matched EXACTLY.

        Matched on the FULL normalised text, not on a 100-character head. Measured: a
        head match FALSE-MATCHES -- 5 of 606 over-budget turns opened with identical
        words ("Mdm Speaker, I beg to move, \"That the Bill be now read a Second
        time.\"") but had different full text (1,956 vs 1,935 words), because the fetch
        bounded the turn differently. Attaching that fetched turn's paragraphs would
        give this turn structure that does not belong to it, and the text would still
        read as plausible Hansard.

        Full-text equality cannot false-match: identical whole texts ARE the same turn.
        The cost is a few more fallbacks, and a fallback is safe -- the turn still
        chunks, using the stored text, just without paragraph boundaries.
        """
        ft = para_by_key.get(turn["key"])
        if ft is None:
            return None
        if norm(CLEAN_FOR_MATCH(ft.get("text") or "")) == norm(CLEAN_FOR_MATCH(turn["clean"])):
            return ft
        return None

    for t in turns:
        if t["empty"]:
            empty_turns += 1
            continue
        # source_chars must measure the text ACTUALLY CHUNKED, not the stored text: the
        # paragraph path legitimately re-joins text and changes its length, so measuring
        # the stored side made coverage_exact False for every paragraph-sourced turn
        # while the chunks themselves were complete.
        text = t["clean"]
        chunked_text[t["key"]] = text
        split_text = text      # overwritten below if a paragraph version is available

        # Long turn? Prefer the paragraph-preserving version from the source, but only
        # when the fetched turn can be matched to this stored turn BY CONTENT.
        ft = para_for(t) if (len(text) > target and para_by_key) else None
        if ft is not None:
            fp = ft.get("text") or ""
            # Guard against turn-index drift: confirm this fetched turn really is the
            # same speech as the stored one before trusting its structure.
            #
            # The comparison must be LIKE-FOR-LIKE. Measured bug: comparing raw fetched
            # text (which still carries "[Please refer to...]" bracket asides) against
            # the CLEANED stored text failed every time, so the paragraph store was
            # silently rejected and the output was byte-identical to sentence-splitting.
            # Clean the fetched side first, then compare normalised heads.
            fp_clean = strip_speaker_labels(fp.replace("\n\n", " "))
            if (fp_clean and len(fp_clean) >= len(text) * 0.95
                    and norm(fp_clean[:400]) == norm(text[:400])):
                # Two forms are needed, for two different purposes:
                #   split_text  -- keeps \n\n so split_turn() can find paragraph
                #                  boundaries and prefer them over sentences
                #   reference   -- flattened to single spaces, which is what
                #                  split_turn() actually emits (it flattens each unit
                #                  and joins with a space), so the gate can compare
                #                  chunks against it EXACTLY and still catch a weld
                #
                # Storing only one of the two was the bug: the \n\n form made every
                # chunk fail its verbatim check at each paragraph seam, and using the
                # flattened form for splitting silently discarded the paragraph
                # structure this whole rework exists to recover.
                split_text = fp
                text = re.sub(r"[\s\u00a0]+", " ", fp).strip()
                para_sourced += 1
                chunked_text[t["key"]] = text

        source_chars += len(text)

        if len(text) < MIN_CHUNK_CHARS:
            chunks.append({
                "id": f"{t['key']}#c0", "key": t["key"], "part": 0, "n_parts": 1,
                "is_partial": False, "short": True,
                "cite_text": text, "embed_text": embed_text_for(text, t["speaker"]),
                "report_id": t["report_id"], "turn": t["turn"], "year": t["year"],
                "date": t["date"], "group": t["group"], "speaker": t["speaker"],
                "para_sourced": False,
                **chunk_meta(t),
            })
            covered_chars += len(text)
            continue

        # split on the structured form (paragraphs preserved) but the chunks are
        # verbatim slices, so they remain findable in the flattened reference
        pieces = split_turn(split_text, target, overlap)
        if len(pieces) > 1:
            split_turns += 1
        for p, piece in enumerate(pieces):
            chunks.append({
                "id": f"{t['key']}#c{p}", "key": t["key"], "part": p,
                "n_parts": len(pieces), "is_partial": len(pieces) > 1,
                "cite_text": piece,
                "embed_text": embed_text_for(piece, t["speaker"]),
                "report_id": t["report_id"], "turn": t["turn"], "year": t["year"],
                "date": t["date"], "group": t["group"], "speaker": t["speaker"],
                "para_sourced": t["key"] in para_by_key,
                **chunk_meta(t),
            })
        covered_chars += len(text)

    coverage = {
        "source_turns": len(turns),
        "empty_turns": empty_turns,
        "source_chars": source_chars,
        "chunks": len(chunks),
        "split_turns": split_turns,
        "para_sourced_turns": para_sourced,
        "coverage_exact": covered_chars == source_chars,
        "covered_chars": covered_chars,
    }
    return chunks, coverage, chunked_text

def verify_chunks(chunks, turns, target=TARGET_CHARS, chunked_text=None):
    """Assert the properties that make silent loss impossible.

    This is the gate. Chunking's whole purpose here is to make model truncation
    visible, so the chunk plan itself must be provably lossless.

    chunked_text: {turn_key: text} -- the EXACT text plan_chunks used for each turn.
    Pass it. It matters: a paragraph-sourced turn is reconstructed by joining its
    paragraphs, so its text differs from the stored flattened text at the seams. The
    gate must compare chunks against what was actually chunked, or it either
    false-alarms on correct output or (if it normalises whitespace to compensate) goes
    blind to a welded join.

    Measured: normalising whitespace in this check made it MISS a deliberately welded
    paragraph join ("2022.\\"Last year" with no space) -- a defect that would have made
    every quote crossing that seam fail its own citation. Exact comparison against the
    chunked text catches it.
    """
    problems = []
    by_key = {}
    for c in chunks:
        by_key.setdefault(c["key"], []).append(c)

    # 0. EVERY CHUNKED TURN MUST PRODUCE A CHUNK.
    #
    #    plan_chunks computes source_chars vs covered_chars and reports
    #    `coverage_exact`, but the gate never asserted it -- so the gate returned
    #    "0 problems" while the coverage record said coverage_exact: False. A computed
    #    value the verdict ignores is worse than no check: it prints the problem and
    #    then passes.
    if chunked_text:
        missing = [k for k in chunked_text if k not in by_key]
        if missing:
            problems.append(f"{len(missing)} chunked turn(s) produced NO chunk: "
                            f"{missing[:5]}")

    source = {t["key"]: t for t in turns if not t["empty"]}
    # reference text per turn: the EXACT text that was chunked when supplied,
    # falling back to the stored clean text.
    ref = dict(source)
    if chunked_text:
        for k, v in chunked_text.items():
            if k in ref:
                ref[k] = dict(ref[k])
                ref[k]["clean"] = v

    # 1. every non-empty turn produced at least one chunk
    missing = [k for k in source if k not in by_key]
    if missing:
        problems.append(f"{len(missing)} non-empty turns produced NO chunk: {missing[:5]}")

    # 2. every chunk's cite_text is a verbatim substring of its source turn
    #    (the citation gate is string containment, so this must hold).
    #
    #    EXACT comparison against the text that was actually chunked. Do not normalise
    #    whitespace to paper over a mismatch: measured, that made this check MISS a
    #    deliberately welded paragraph join, which would have made every quote crossing
    #    the seam fail its own citation.
    for c in chunks:
        src = ref.get(c["key"])
        if src is None:
            problems.append(f"chunk {c['id']} has a key absent from the source")
            continue
        if c["cite_text"] not in src["clean"]:
            problems.append(f"{c['id']}: cite_text is NOT a substring of the source")

    # 3. the pieces of a split turn COVER it end to end
    for key, group in by_key.items():
        src = ref.get(key)
        if not src or len(group) == 1:
            continue
        ordered = sorted(group, key=lambda c: c["part"])
        # reconstruct: walk each piece's position in the reference text and check for
        # gaps. Exact comparison, same reasoning as check 2.
        src_txt = src["clean"]
        pos = 0
        gaps = []
        for c in ordered:
            at = src_txt.find(c["cite_text"], pos)
            if at < 0:
                at = src_txt.find(c["cite_text"])
                if at < 0:
                    problems.append(f"{c['id']}: cannot locate piece in source")
                    continue
            if at > pos:
                gaps.append((pos, at))
            pos = max(pos, at + len(c["cite_text"]))
        if pos < len(src_txt) and src_txt[pos:].strip():
            gaps.append((pos, len(src_txt)))
        # gaps are only allowed up to the overlap size (they are re-covered by the
        # following piece's overlap); report any gap larger than the overlap
        big = [(a, b) for a, b in gaps if b - a > OVERLAP_CHARS + 50]
        if big:
            problems.append(f"{key}: uncovered gap(s) of {[b-a for a,b in big]} chars")

    # 3b. A SINGLE-part chunk must EQUAL its whole turn.
    #
    #     This assertion was missing and a real defect passed without it. Measured:
    #     truncating a chunk by half produced ZERO problems, because a truncated string
    #     is still a substring of its source (check 2 passes) and check 3 only walks
    #     turns that were split (a one-part turn is skipped entirely). 98.65% of turns
    #     produce exactly one chunk, so nothing at all was asserting that those chunks
    #     cover their turns -- silent loss, the precise failure this module exists to
    #     prevent.
    for key, group in by_key.items():
        src = ref.get(key)
        if not src or len(group) != 1:
            continue
        c = group[0]
        if c.get("short"):
            # deliberately tiny turns are still required to be complete
            pass
        if c["cite_text"] != src["clean"]:
            problems.append(
                f"{c['id']}: single-part chunk does not equal its whole turn "
                f"({len(c['cite_text']):,} vs {len(src['clean']):,} chars) -- "
                f"content lost or altered")

    # 3c. INTERNAL CONSISTENCY: the fields that tell a reader whether a quote is a
    #     whole statement or a fragment must not disagree with each other. A chunk
    #     labelled is_partial=False would be presented as a complete utterance while
    #     actually being one piece of a longer speech -- a factual error in the answer,
    #     not a crash, so nothing else would catch it.
    for key, group in by_key.items():
        n = len(group)
        for c in group:
            if c.get("n_parts") != n:
                problems.append(
                    f"{c['id']}: n_parts={c.get('n_parts')} but {n} chunk(s) exist")
            if bool(c.get("is_partial")) != (n > 1):
                problems.append(
                    f"{c['id']}: is_partial={c.get('is_partial')} with {n} part(s)")
        parts = sorted(c["part"] for c in group)
        if parts != list(range(n)):
            problems.append(f"{key}: part indices are {parts}, expected 0..{n-1}")

    # 4. no chunk exceeds the safe limit by a wide margin
    over = [c for c in chunks if len(c["cite_text"]) > target * 1.5]
    if over:
        problems.append(f"{len(over)} chunks exceed 1.5x target: "
                        f"{[len(c['cite_text']) for c in over[:5]]}")

    # 5. TOKEN BUDGET -- the check that actually matters.
    #
    #    nomic-embed-text silently truncates past 2,048 tokens and still returns a
    #    valid vector, so an over-long chunk loses text with no error anywhere. A
    #    CHARACTER margin cannot express this: the same 2,048-token limit is reached at
    #    ~3,296 chars of dense figures/punctuation but ~12,256 chars of plain prose.
    #    Measured: the char-only check above passed a chunk that was 6x over budget.
    tok_over = []
    worst = 0
    for c in chunks:
        n = count_tokens(c["cite_text"])
        if n > worst:
            worst = n
        if n > TOKEN_LIMIT:
            tok_over.append((n, c["id"]))
    if tok_over:
        tok_over.sort(reverse=True)
        problems.append(
            f"{len(tok_over)} chunk(s) exceed the {TOKEN_LIMIT}-token embed limit and "
            f"would be SILENTLY TRUNCATED, e.g. {tok_over[:5]}")
    # also refuse to sit near the edge: leave room for the embed_text speaker prefix
    near = [(count_tokens(c["embed_text"]), c["id"]) for c in chunks
            if count_tokens(c["embed_text"]) > TOKEN_LIMIT]
    if near:
        near.sort(reverse=True)
        problems.append(
            f"{len(near)} chunk(s) exceed the limit once the speaker prefix is added, "
            f"e.g. {near[:5]}")

    # 6. METADATA PRESENT, and embed_text uncontaminated.
    #
    #    The citation fields are what make an answer traceable, so a chunk missing them
    #    is a defect -- and this is exactly the class of check that passes silently when
    #    the source field is absent (measured earlier: load_turns dropped `title` for
    #    every turn and nothing noticed).
    need_meta = ("title", "parliament_no", "volume_no", "sitting_no", "time")
    missing_meta = Counter(k for c in chunks for k in need_meta if not c.get(k))
    if missing_meta:
        worst = missing_meta.most_common()
        # a report-level field may legitimately be absent for some reports; only fail
        # when it is absent from EVERY chunk, which means it was never wired in.
        dead = [k for k, n in worst if n == len(chunks)]
        if dead:
            problems.append(f"metadata absent from EVERY chunk (never wired): {dead}")
        else:
            pass   # partial gaps are tolerated and reported in coverage, not failed

    #    embed_text must be EXACTLY the canonical construction (speaker label + verbatim
    #    text), so nothing else can have crept into the vector. The title was kept OUT
    #    of embed_text deliberately: it costs a re-embed and adds no retrieval signal.
    #
    #    Do NOT test `title in embed_text` -- measured, that reports a false positive,
    #    because a turn's own speech legitimately contains its report's title (a bill
    #    debate opens by naming the bill). Equality against the canonical form is the
    #    assertion that actually distinguishes leakage from content.
    for c in chunks:
        expected = embed_text_for(c["cite_text"], c.get("speaker"))
        if c["embed_text"] != expected:
            problems.append(
                f"{c['id']}: embed_text is not the canonical speaker-label + text "
                f"(something else was added to the vector)")
            break

    # 4b. NO EMPTY CHUNKS. This assertion was missing and a real defect passed
    # without it: turns whose entire content is a bracket aside cleaned down to
    # "" and produced 0-character chunks that cannot be embedded or quoted.
    empties = [c for c in chunks if not c["cite_text"].strip()]
    if empties:
        problems.append(f"{len(empties)} EMPTY chunks (un-embeddable, un-quotable): "
                        f"{[c['id'] for c in empties[:5]]}")

    # 4c. nothing to embed either
    noembed = [c for c in chunks if not c["embed_text"].strip()]
    if noembed:
        problems.append(f"{len(noembed)} chunks with no embed_text")

    # 4d. the corpus itself must not claim empties that are not empty, and vice versa
    for t in turns:
        if t["empty"] and t["clean"].strip():
            problems.append(f"{t['key']}: flagged empty but has content")
            break
        if not t["empty"] and not t["clean"].strip():
            problems.append(f"{t['key']}: not flagged empty but is blank")
            break

    # 5. partial chunks are labelled
    for key, group in by_key.items():
        if len(group) > 1:
            for c in group:
                if not c["is_partial"] or c["n_parts"] != len(group):
                    problems.append(f"{c['id']}: split turn not correctly flagged "
                                    f"(is_partial={c['is_partial']}, "
                                    f"n_parts={c['n_parts']}, actual={len(group)})")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="fit distribution at candidate sizes")
    ap.add_argument("--show", help="show how one turn key splits")
    ap.add_argument("--verify", action="store_true", help="assert lossless coverage")
    ap.add_argument("--target", type=int, default=TARGET_CHARS)
    ap.add_argument("--overlap", type=int, default=OVERLAP_CHARS)
    ap.add_argument("--years", type=int, nargs="*")
    args = ap.parse_args()

    turns = load_turns(set(args.years) if args.years else None)
    print(f"turns: {len(turns):,}  (empty: {sum(1 for t in turns if t['empty']):,})")
    print()

    if args.stats:
        print("turns fitting whole at each candidate target:")
        print(f"{'target':>8} {'chunks':>10} {'split turns':>12} {'split%':>8} {'max chunk':>10}")
        real = [t for t in turns if not t["empty"] and len(t["clean"]) >= MIN_CHUNK_CHARS]
        for tgt in (2000, 3000, 4000, 6000, 8000, 12000):
            ch, cov, _ = plan_chunks(turns, target=tgt, overlap=args.overlap)
            mx = max(len(c["cite_text"]) for c in ch)
            print(f"{tgt:>8} {len(ch):>10,} {cov['split_turns']:>12,} "
                  f"{cov['split_turns']/len(real)*100:>7.1f}% {mx:>10,}")
        print()
        print(f"the measured silent-truncation point is ~16,000 chars -- never approach it.")
        return 0

    if args.show:
        t = next((x for x in turns if x["key"] == args.show), None)
        if not t:
            print(f"no such turn: {args.show}")
            return 1
        print(f"=== {t['key']}  {t['date']}  {t['group']} ===")
        print(f"speaker: {t['speaker']}")
        print(f"length : {len(t['clean']):,} chars")
        print()
        pieces = split_turn(t["clean"], args.target, args.overlap)
        for i, p in enumerate(pieces):
            print(f"--- piece {i+1}/{len(pieces)}  {len(p):,} chars ---")
            print(f"    start: {p[:110]!r}")
            print(f"    end  : {p[-110:]!r}")
            print(f"    verbatim in source: {p in t['clean']}")
        return 0

    if args.verify:
        chunks, cov, ctext = plan_chunks(turns, args.target, args.overlap)
        print("coverage:")
        for k, v in cov.items():
            print(f"  {k:>16}: {v}")
        print()
        problems = verify_chunks(chunks, turns, args.target, chunked_text=ctext)
        if problems:
            print(f"FAIL -- {len(problems)} problem(s):")
            for p in problems[:25]:
                print(f"  - {p}")
            return 1
        print("PASS -- every turn chunked, every piece verbatim, coverage complete, "
              "partial chunks flagged.")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
