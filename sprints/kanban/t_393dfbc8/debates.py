#!/usr/bin/env python3
"""Debate ingestion: the Hansard corpus -> normalized JSONL.

WHAT THIS JOB IS FOR
--------------------
Everything downstream (chunking, the vector store, the citation contract) reads
`data/normalized/*.jsonl`. This job produces the debate partition of it: every Hansard
record that is not a bill or a motion -- the bills and motions are `ingest/legislation.py`'s
records, and the two partitions are disjoint and exhaustive over the 20,884 reports the
corpus holds (spec 8).

WHERE THE RECORDS COME FROM, AND WHY THERE ARE TWO WAYS IN
---------------------------------------------------------
`scraper/parsnips_fetch.py` is the project's API client and `scraper/hansard_parse.py` its
structural parser; spec 1.1 says to reuse them rather than write a second implementation,
and this does (`F.enumerate_sitting_reports`, `F.fetch_report`, `F.post`). It does not fork
them.

Two input paths exist because both are genuinely needed and they disagree about nothing:

  * `--source live` (default) -- enumerate and fetch from `sprs.parl.gov.sg`, writing the
    raw responses to `data/raw/sittings/sitting_<date>.json` first. A rerun reads that
    cache instead of the network, which is the whole point of the cache. This is how a
    fresh backfill runs and how new sittings get ingested.
  * `--source sittings` -- map the sitting files already committed under `data/<year>/`.
    No network at all. `data/` is the archive (331 sittings, 20,884 reports) and is what
    the spec's measurements are taken against, so this path is what makes the output
    reproducible and reproducible *offline*: `python3 ingest/debates.py --source sittings`
    regenerates the entire normalized corpus in seconds, and that is the command P1's
    100%-validates check is worth running against.

Both paths converge on the same `map_report()`, which is a pure function of
(report dict, sitting context, roster). That is deliberate: it is the function the unit
test exercises against a saved fixture, with no network and no `data/`.

WHAT IS PRESERVED, AND WHY IT MATTERS FOR RETRIEVAL
---------------------------------------------------
Speaker attribution is the asset this project has over the news (README). So:

  * `turns` keeps source order, each turn's own `speaker` verbatim, its `lang`, its `time`
    stamp and its `is_procedural` flag. 2.6% of turns carry no speaker and that is valid --
    the turn is kept unattributed rather than inheriting the previous name.
  * `text` is the turns joined `"<speaker>: <text>"` per line, so a question like "what did
    Shanmugam say about X" is findable in a body that rarely repeats his name.
  * `speaker_raw` on the document is the principal speaker exactly as served -- the audit
    trail for the resolution below.
  * `member_id` is resolved with spec 3.3's rule and is `null` whenever the name does not
    resolve. It is never guessed, and chamber officers never get a member id because they
    are not members.

WHAT THE SOURCE CANNOT GIVE US (measured, spec 1.4)
---------------------------------------------------
  * No member ids anywhere -- every roster entry returns `id: 0` -- so `member_id` is a
    slug of the roster's own spelling (`sg-mp:k-shanmugam`).
  * No per-member votes anywhere. `metadata.division.per_member` is `null` by schema and
    populating it is a schema error. Only the Chair's declared totals are extractable.
  * A record with no text (2.5% of the corpus: `attendance-*` and friends) still gets a
    document row with `text: ""` so it stays citable by title and link (spec 3.5). It is
    NOT synthesised into a row with invented text.
  * A report whose fetch fails is counted as a failure and NOT emitted with an empty text,
    because an empty text already means something specific here ("the source had none").
    The id is printed and listed in the run report so the gap is visible.

USAGE
    python3 ingest/debates.py --limit 500              # smoke run, cached under data/raw/
    python3 ingest/debates.py                          # full backfill from the source
    python3 ingest/debates.py --source sittings        # offline, from the committed archive
    python3 ingest/debates.py --since 2024-01-01       # only sittings on/after a date
    python3 ingest/debates.py --limit 500 --source sittings --out /tmp/x.jsonl

`--limit N` takes the first N documents in the deterministic output order (sitting date
ascending, then the order the reports appear in the sitting). It exists for smoke runs;
it is not a sample.
"""
import argparse
import collections
import datetime
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
sys.path.insert(0, HERE)                                   # ingest/rag_schema
sys.path.insert(0, os.path.join(ROOT, "scraper"))          # the project's fetcher/parser

import rag_schema as S            # noqa: E402  (schema, enums, converters, validator)
import parsnips_fetch as F        # noqa: E402  (API client: the one implementation)
import storage                    # noqa: E402  (the project's atomic writer)

DATA = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA, "raw")
RAW_SITTINGS = os.path.join(RAW_DIR, "sittings")
RAW_ROSTER = os.path.join(RAW_DIR, "roster.json")
NORMALIZED = os.path.join(DATA, "normalized")
OUT_DEFAULT = os.path.join(NORMALIZED, "debates.jsonl")
MEMBERS_DEFAULT = os.path.join(NORMALIZED, "members.jsonl")
REPORT_DEFAULT = os.path.join(NORMALIZED, "debates.report.json")

# The two partitions of the corpus (spec 8): bills and motions belong to the
# legislation ingester. Measured sizes: bill 1,002, motion 793, everything else 19,089.
NON_DEBATE_SECTIONS = ("bill", "motion")

# Rate discipline, inherited from scraper/parsnips_fetch.py rather than re-chosen
# (spec 1.1: no published rate limits, so the project's self-imposed ones stand).
PAUSE = F.PAUSE                 # 0.2s between sequential calls
FETCH_WORKERS = 5               # <=5 concurrent report fetches
ENUM_WORKERS = 4                # <=6 concurrent discovery workers (F uses 4 here)

# ------------------------------------------------------------------ member rule
# spec 3.3, and the ORDER of these four patterns is the whole trick: the honorific must
# still be present when the officer test runs, because "Mr Speaker" is only recognisable
# *with* it. An earlier probe stripped honorifics first, so "Mr Speaker" could never match
# and the apparent unresolved rate was 14.6% instead of 0.1%.
TITLES = re.compile(r"\b(Mr|Mrs|Ms|Miss|Mdm|Madam|Dr|Professor|Prof|Asst Prof|"
                    r"Assoc Prof|Er Dr|Er)\b\.?", re.I)
DECOR = re.compile(r"\((?:[^()]*)\)\s*$")                 # a TRAILING parenthetical
BRACKET = re.compile(r"^[\[(].*[\])]$")                   # "[Mr Speaker in the Chair]"
OFFICER = re.compile(r"^(the\s+)?(mr|mdm|madam|miss|ms)?\s*(deputy\s+)?"
                     r"(speaker|chairman|deputy\s+chairman|chair)\b", re.I)
NON_PERSON = re.compile(r"^(hon|honourable|some hon|an hon)\b", re.I)

# metadata.question: the record OPENS with "asked the Minister ...". The separator after
# "asked" is a TAB on thousands of records (`asked\tthe Minister for Health`), so it has to
# be \s+ and not a literal space -- a space-anchored pattern misses 43% of the questions
# that are there. `^\W*` also absorbs the BOM and the stray "\xa0" the source emits, and
# the handful of records that literally open "asked\xa0asked the Minister".
QUESTION_OPEN = re.compile(r"^\W*asked\s+the\s+\S", re.I)

# metadata.division: the Chair's declaration, verbatim, and the only place a result is
# stated as numbers. Same rule as docs/probes/probe_division.py (spec 2 names it).
WORD_NUM = {"no": 0, "none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
            "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
DECL = re.compile(
    r'There\s+(?:are|is)\s+([A-Za-z0-9,]+)\s*"?Ayes?"?\s*,\s*'
    r'([A-Za-z0-9,]+)\s*"?Noes?"?\s*(?:,?\s*(?:and\s+)?([A-Za-z0-9,]+)\s*"?Abstentions?"?)?',
    re.I)
DECL_SENTENCE = re.compile(r"[^.!?]*There\s+(?:are|is)\s+[A-Za-z0-9,]+\s*\"?Ayes?\"?[^.!?]*[.!?]",
                           re.I)

RESOLUTIONS = ("MEMBER", "OFFICER", "NON_PERSON", "UNRESOLVED")

# Zero-width and bidirectional marks the source occasionally glues to a speaker string
# (`'\ufeffMdm Speaker'`, `'Mr Deputy Speaker\ufeff'`). Measured: 10 turns in the corpus, on 10
# distinct strings (docs/probes/ingest_doc_numbers.py). The officer pattern is `^`-anchored,
# so a LEADING mark makes `Mdm Speaker` -- a chamber officer -- unmatchable and the turn loses
# its attribution entirely.
INVISIBLE = re.compile("[\ufeff\u200b\u200c\u200d\u200e\u200f\u2060]")


def clean_speaker(s):
    """The string as served, minus invisible marks -- for RESOLUTION only.

    `turns[].speaker` keeps what the source served, verbatim, because that is the audit
    trail; this is the form the rule matches on. Stripping an invisible mark is not guessing
    a name: it removes a character that renders as nothing, and it can only ever move a string
    INTO a resolution, never invent one -- an unresolvable name stays unresolvable.
    """
    return INVISIBLE.sub("", s or "").strip()


# --------------------------------------------------------------------- utilities
def norm_name(s):
    """The normalized form spec 3.3 matches on.

    lowercase -> drop a TRAILING parenthetical -> drop honorifics -> strip non-alphanumerics.
    The trailing-parenthetical rule is what makes "Ms Tin Pei Ling (MacPherson)" and
    "Tin Pei Ling" the same key while leaving "The Minister for Home Affairs (Mr K
    Shanmugam)" intact, because there the parenthetical is not trailing-only state -- it
    is handled by the candidate list in `resolve_speaker` instead.
    """
    s = (s or "").lower()
    s = DECOR.sub("", s)
    s = TITLES.sub(" ", s)
    s = re.sub(r"[^a-z0-9\s'-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def member_slug(name):
    """spec 3.3 / schema $defs.member_id: 'sg-mp:' + slug of the roster's spelling."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return f"sg-mp:{s}" if s else None


def num_token(tok):
    """'72' -> 72, 'six' -> 6, 'no'/'none'/'zero' -> 0, anything else -> None."""
    if tok is None:
        return None
    t = tok.strip().strip(",").lower()
    if not t:
        return None
    if t in WORD_NUM:
        return WORD_NUM[t]
    return int(t) if re.fullmatch(r"\d+", t) else None


# ------------------------------------------------------------------------ roster
class Roster:
    """The `/fetchData` roster, plus the aliases observed while resolving.

    `status` is the roster of record: a name on `mpVOList` is `current`, a name only on
    `formerMPVOList` is `former`. This is NOT inferred from the date of a sitting -- the
    roster endpoint serves one snapshot and the spec does not claim otherwise, so a
    historical turn by someone who has since left resolves to `former`, which is the
    honest answer for the roster we actually have.
    """

    def __init__(self, blob):
        self.current = [m.get("fullName") for m in (blob.get("mpVOList") or [])
                        if m.get("fullName")]
        self.former = [m.get("fullName") for m in (blob.get("formerMPVOList") or [])
                       if m.get("fullName")]
        self.by_norm = collections.defaultdict(list)
        for name in self.current + self.former:
            self.by_norm[norm_name(name)].append(name)
        self.member_id = {}
        self.status = {}
        self.full_name = {}
        for name in sorted(set(self.current + self.former)):
            mid = member_slug(name)
            if not mid:
                continue
            self.member_id[name] = mid
            self.full_name[mid] = name
            self.status[mid] = "current" if name in self.current else "former"
        self.aliases = collections.defaultdict(set)
        self.collisions = {k: sorted(set(v)) for k, v in self.by_norm.items() if len(set(v)) > 1}

    def __len__(self):
        return len(self.member_id)

    @property
    def member_count(self):
        """distinct member identities, which is what `members_jsonl()` writes.

        `__len__` counts name entries: the roster serves one MORE name string than it has
        slugs, because `R Ravindran` and `R. Ravindran` are two entries that collapse to
        one `member_id` (docs/probes/ingest_roster_counts.py measures this). Printing
        `len(roster)` next to a written row count made it read as a lost member; both
        numbers are real and they answer different questions, so both are printed.
        """
        return len(self.full_name)

    def resolve(self, raw):
        """spec 3.3 -> (kind, member_id). `kind` is one of RESOLUTIONS.

        The test string is `clean_speaker(raw)` (invisible marks removed); the caller keeps
        `raw` itself. Nothing else about the rule changes.
        """
        s = clean_speaker(raw)
        if not s:
            return "UNRESOLVED", None
        # 1. officers FIRST -- before any honorific is removed.
        # 2. bracketed / non-person strings.
        # 3. the roster match, on the marks-stripped form as well as the raw one.
        if BRACKET.match(s):
            return "NON_PERSON", None
        if OFFICER.match(s):
            return "OFFICER", None
        if NON_PERSON.match(norm_name(s)):
            return "NON_PERSON", None
        # 3. roster match, trying the marks-stripped string, its normalized form, the string
        #    AS SERVED, each parenthetical's contents (portfolio or constituency) and the
        #    text before the first "(". The last two are how "The Minister for Home Affairs
        #    (Mr K Shanmugam)" resolves to K Shanmugam; the raw form is there because a mark
        #    left INSIDE a name is still not part of the name.
        cands = [s, norm_name(s)]
        if (raw or "").strip() != s:
            cands += [(raw or "").strip(), norm_name(raw)]
        cands += re.findall(r"\(([^()]*)\)", s)
        if "(" in s:
            cands.append(s[:s.index("(")])
        for c in cands:
            names = self.by_norm.get(norm_name(c))
            if not names:
                continue
            key = norm_name(c)
            if len(set(names)) > 1:
                # spec 3.3 step 4: the whole roster has exactly one normalized collision
                # (R Ravindran / R. Ravindran, the same person). Recorded, not fatal.
                self.collisions.setdefault(key, sorted(set(names)))
            name = self._best(names)
            return "MEMBER", self.member_id.get(name)
        # 4. the measured name-variant step. spec 3.3's prose is steps 1-3, but the
        #    numbers quoted with it (77.6% MEMBER / 31 unresolved strings / 52 turns)
        #    come from docs/probes/probe_alias2.py, which carries this fallback; without
        #    it the same corpus gives 33 strings / 58 turns. The delta is one person
        #    spelled two ways -- "Mr Mohd Fahmi Bin Aliman" against the roster's "Mohd
        #    Fahmi Aliman" -- so the fallback is a name-variant rule, not a guess, and
        #    it refuses when it is ambiguous.
        parts = norm_name(s).split()
        if len(parts) >= 2:
            hits = [k for k in self.by_norm
                    if len(k.split()) >= 2 and k.split()[0] == parts[0]
                    and k.split()[-1] == parts[-1]]
            if len(hits) == 1:
                name = self._best(self.by_norm[hits[0]])
                return "MEMBER", self.member_id.get(name)
        # 5. unresolved -> null, never guessed.
        return "UNRESOLVED", None

    def _prefer_current(self, names):
        """Prefer a current MP over a former one when both forms normalize alike."""
        uniq = sorted(set(names))
        cur = [n for n in uniq if n in self.current]
        return cur or uniq

    def _best(self, names):
        uniq = self._prefer_current(names)
        return uniq[0] if uniq else None

    def note_alias(self, member_id, raw):
        if member_id and raw:
            self.aliases[member_id].add(raw)

    def members_jsonl(self):
        """One `member` record per roster member, with the aliases this run observed.

        This is the referential-integrity surface for spec P4 ("every `member_id` written
        appears in the roster") and the thing a speaker filter needs: a query for one
        member has to match every way the corpus spells them, which is what `aliases` is.
        """
        out = []
        for mid in sorted(self.full_name):
            out.append({
                "member_id": mid,
                "full_name": self.full_name[mid],
                "aliases": sorted(self.aliases.get(mid, ())),
                "status": self.status[mid],
                "constituency": None,
                "portfolio": None,
                "source": "sg-mp-roster",
                "url": "https://www.parliament.gov.sg/mps/list-of-current-mps",
            })
        return out


def load_roster(offline=False, log=print):
    """The roster, from the raw cache when there is one, else from the source."""
    if os.path.exists(RAW_ROSTER):
        blob = storage.read_json(RAW_ROSTER)
        if blob:
            log(f"  roster: cache {os.path.relpath(RAW_ROSTER, ROOT)} "
                f"({len(blob.get('mpVOList') or [])} current, "
                f"{len(blob.get('formerMPVOList') or [])} former)")
            return Roster(blob)
    if offline:
        raise SystemExit(f"--offline and no roster cache at {RAW_ROSTER}; run once online")
    log("  roster: fetching POST /fetchData")
    blob = F.post("fetchData", {}) or {}
    os.makedirs(RAW_DIR, exist_ok=True)
    storage.write_json_atomic(RAW_ROSTER, blob)
    return Roster(blob)


def load_roster_file(path):
    """A roster from an explicit file -- what the unit test uses, so it needs no network."""
    blob = storage.read_json(path)
    if not blob:
        raise SystemExit(f"no roster file at {path}")
    return Roster(blob)


# ------------------------------------------------------------------- field rules
def extract_division(turn_texts):
    """The Chair's declaration -> the schema's division object, or None.

    Totals only, and `per_member` is ALWAYS null because the source publishes no
    per-member votes (spec 1.4, verified three ways there). A record can declare twice --
    bill-213 and bill-230 state the same result at two readings, and bill-367 states two
    different results -- so the caller deduplicates on (ayes, noes, abstentions) per spec 2.
    """
    seen, found = set(), []
    for i, text in enumerate(turn_texts):
        for m in DECL.finditer(text or ""):
            ayes, noes = num_token(m.group(1)), num_token(m.group(2))
            if ayes is None or noes is None:
                continue
            abst = num_token(m.group(3))
            key = (ayes, noes, abst)
            if key in seen:
                continue
            seen.add(key)
            sentence = DECL_SENTENCE.search(text[max(0, m.start() - 200):m.end() + 200])
            found.append({
                "ayes": ayes,
                "noes": noes,
                "abstentions": abst,
                "raw": (sentence.group(0).strip() if sentence else m.group(0).strip()),
                "tellers": None,
                "per_member": None,
                "_at": i,
            })
    found.sort(key=lambda d: d["_at"])
    return found


def map_report(rep, ctx, roster, stats=None):
    """One sitting report -> one normalized document.

    Pure: depends only on its arguments. `ctx` is the sitting it came from and carries the
    date and the enumeration coverage the schema's metadata expects to be propagated:
    {"date": ISO, "coverage": {...}}.

    Returns (document, skipped_reason). `skipped_reason` is a string when the record must
    NOT be emitted (it is in the other ingester's partition), and a (kind, detail) tuple
    when it could not be mapped, so a caller can count the categories instead of guessing
    from a message.
    """
    rid = (rep.get("report_id") or "").rstrip("#")
    if not rid:
        return None, ("no_doc_id", None)

    section = rep.get("group") or "other"
    if section not in S.SECTIONS:
        section = "other"
    if section in NON_DEBATE_SECTIONS:
        return None, "legislation_partition"

    doc_id = rid

    # Date: the source serves D-M-YYYY; ISO is mandatory (spec 3.2). The sitting file's own
    # date is a safe fallback -- measured, it agrees with every record's sitting_date on all
    # 20,884 reports -- but a record with no parseable date either way is an error, not
    # something to paper over: a raw source date reaching a consumer breaks range filters
    # silently, which is exactly the failure the schema's date pattern exists to prevent.
    date = S.iso_date(rep.get("sitting_date"), fallback=ctx.get("date"))
    if not date:
        return None, ("bad_date", rep.get("sitting_date"))

    # Title: schema minLength 1, and the spec calls an untitled record an ingest error.
    # Measured: 0 of 20,884 records are untitled. If one ever is, the record still ships --
    # spec 3.5's rule is that a record stays citable rather than disappearing -- but the
    # substitution is counted and printed, never silent.
    title = (rep.get("title") or "").strip()
    title_fallback = False
    if not title:
        title, title_fallback = doc_id, True
        if stats is not None:
            stats["title_fallback"] += 1

    raw_turns = rep.get("turns") or []

    def keep(t):
        return bool((t.get("text") or "").strip())

    turns, dropped_blank = [], 0
    for t in raw_turns:
        if not keep(t):
            dropped_blank += 1
            continue
        speaker = (t.get("speaker") or "").strip() or None
        kind, mid = roster.resolve(speaker) if speaker else ("UNRESOLVED", None)
        if mid:
            roster.note_alias(mid, speaker)
        if stats is not None and speaker:
            stats["turns_by_kind"][kind] += 1
        text = t.get("text") or ""
        turns.append({
            "speaker": speaker,
            "member_id": mid,
            "text": text,
            "lang": t.get("lang") or "English",
            "time": (t.get("time") or None),
            "is_procedural": bool(t.get("is_procedural")),
            "words": len(text.split()),
        })

    # The document text: turns joined "<speaker>: <text>" per line. Empty only when the
    # record genuinely has no turns -- 512 records (2.5%) are like that and they still get a
    # row, because dropping them loses records from an "everything about X" query (spec 3.5).
    text = "\n".join(f'{t["speaker"]}: {t["text"]}' if t["speaker"] else t["text"]
                     for t in turns)

    principal = next((t["speaker"] for t in turns if t["speaker"]), None)
    if principal:
        kind, mid = roster.resolve(principal)
    else:
        kind, mid = "UNRESOLVED", None

    div = extract_division([t["text"] for t in turns])
    question = None
    if section in ("oral", "written") and turns:
        opening = next((t["text"] for t in turns if t["speaker"]), None)
        if opening and QUESTION_OPEN.match(opening):
            question = opening

    # metadata.division is a single object (`$defs.metadata.division`), so a record that
    # declared two DIFFERENT results can only carry one. Measured: exactly one document in
    # the corpus does that (bill-367, in the other partition). The first declaration is
    # kept -- the earlier reading, in source order -- and the overflow is counted and
    # printed rather than dropped quietly.
    if stats is not None and len(div) > 1:
        stats["division_overflow"].append({"doc_id": doc_id, "declarations": len(div)})

    cov = ctx.get("coverage") or {}
    doc = {
        "doc_id": doc_id,
        "source": "sg-hansard",
        "doc_type": S.doc_type_for(section),
        "section": section,
        "date": date,
        "title": title,
        "speaker_raw": principal,
        "member_id": mid,
        "text": text,
        "turn_count": len(turns),
        "word_count": sum(t["words"] for t in turns),
        "url": S.topic_url(doc_id),
        "turns": turns,
        "metadata": {
            "parliament_no": rep.get("parliament_no"),
            "sitting_no": rep.get("sitting_no"),
            "volume_no": rep.get("volume_no"),
            "report_version": rep.get("report_version"),
            "report_type": rep.get("report_type"),
            "mp_names_raw": rep.get("mp_names"),
            "division": ({k: v for k, v in div[0].items() if k != "_at"} if div else None),
            "question": question,
            # Propagated from the sitting's enumeration pass, never discarded: below 0.95
            # the listing API dropped rows that day and the document set is known-incomplete.
            "coverage_ratio": cov.get("ratio"),
            "speaker_attribution": cov.get("speaker_attribution"),
        },
    }

    # Cheap invariants, checked on every row because a silent mismatch is what makes a
    # downstream count wrong: the sitting file precomputed `words`, and it must agree.
    if stats is not None:
        if rep.get("words") is not None and rep["words"] != doc["word_count"]:
            stats["word_count_mismatch"].append(
                {"doc_id": doc_id, "report": rep["words"], "recomputed": doc["word_count"]})
        if title_fallback:
            stats["title_fallback_ids"].append(doc_id)
        stats["turns_dropped_blank"] += dropped_blank
        stats["principal_kind"][kind] += 1
        stats["by_section"][section] += 1
        stats["by_doc_type"][doc["doc_type"]] += 1
        if question:
            stats["questions"] += 1
        if div:
            stats["division_rows"].append({"doc_id": doc_id, "section": section,
                                           "ayes": div[0]["ayes"], "noes": div[0]["noes"],
                                           "abstentions": div[0]["abstentions"]})
    return doc, None


# ------------------------------------------------------------------ input paths
def sitting_files():
    return sorted(glob.glob(os.path.join(DATA, "20*", "sitting_*.json")))


def context_from_sitting(blob, date=None):
    """The `ctx` map_report needs, from a sitting file."""
    cov = blob.get("coverage") or {}
    return {
        "date": S.iso_date(blob.get("date") or date),
        "coverage": {
            "ratio": cov.get("ratio") or cov.get("coverage_ratio"),
            "speaker_attribution": cov.get("speaker_attribution"),
        },
    }


def iter_source_sittings(since=None):
    """--source sittings: map the committed archive. No network."""
    for path in sitting_files():
        blob = storage.read_json(path)
        if not blob:
            continue
        ctx = context_from_sitting(blob)
        if since and ctx["date"] and ctx["date"] < since:
            continue
        yield ctx, (blob.get("reports") or []), {"path": path, "source": "sittings"}


def cache_path(date):
    return os.path.join(RAW_SITTINGS, f"sitting_{date}.json")


def fetch_sitting_raw(date, log=print, force=False):
    """Enumerate + fetch one sitting, caching the raw responses under data/raw/.

    The cache is the point: `data/raw/sittings/sitting_<date>.json` holds the listing rows
    and each report's `resultHTML`, which is everything `map_report` needs. A rerun reads
    it and does not touch the network -- that is what makes iterating on the mapper cheap
    and what the card asks for.
    """
    path = cache_path(date)
    if not force and os.path.exists(path):
        blob = storage.read_json(path)
        if blob and blob.get("reports"):
            return blob, True

    reports, coverage = F.enumerate_sitting_reports(date, workers=ENUM_WORKERS)
    if not reports:
        return None, False
    ids = sorted(reports)
    failures = []

    def one(rid):
        # `F.post` retries 4x with backoff, and RAISES on total failure. Deliberately not
        # `F.fetch_report`, which swallows the failure and returns {} -- an empty dict is
        # indistinguishable from a record that legitimately has no content, and only one of
        # those should produce a row.
        try:
            data = F.post("getHansardTopic", {"id": rid.rstrip("#")})
        except Exception as exc:                                    # noqa: BLE001
            return rid, {"_error": f"{type(exc).__name__}: {exc}"}
        return rid, ((data or {}).get("resultHTML") or {})

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        fetched = dict(pool.map(one, ids))

    out = {}
    for rid in ids:
        topic = fetched.get(rid) or {}
        if topic.get("_error"):
            failures.append({"report_id": rid, "error": topic["_error"]})
            continue
        out[rid] = {"listing": reports[rid], "topic": topic}

    blob = {
        "date": date,
        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "coverage": coverage,
        "reports": out,
        "failures": failures,
    }
    storage.write_json_atomic(path, blob)
    if failures:
        log(f"    {date}: {len(failures)} report fetch(es) failed after retries: "
            + ", ".join(f["report_id"] for f in failures[:5]))
    return blob, False


def report_from_raw(rid, entry):
    """A raw cache entry -> the same report shape a sitting file carries.

    Both input paths land here, so `map_report` never learns which one it is looking at.
    The parse is `parsnips_fetch.parse_report_turns`, i.e. the project's structural parser
    (scraper/hansard_parse.py) -- spec 1.1 says one parser, and this is it.
    """
    listing = entry.get("listing") or {}
    topic = entry.get("topic") or {}
    turns = F.parse_report_turns(topic.get("content") or "")
    rtype = topic.get("reportType") or listing.get("reportType")
    return {
        "report_id": rid,
        "report_type": rtype,
        "group": F.group_for(rtype, rid),
        "title": (topic.get("title") or listing.get("title") or "").strip(),
        "sitting_date": topic.get("sittingDate") or listing.get("sittingDate"),
        "parliament_no": topic.get("parlNo"),
        "sitting_no": topic.get("sittingNo"),
        "volume_no": topic.get("volumeNo"),
        "report_version": "sprs3",
        "mp_names": topic.get("mpNames"),
        "words": sum(t["words"] for t in turns),
        "turns": turns,
    }


def iter_live_sittings(since=None, force=False, log=print):
    """--source live: fetch (or reuse the cache for) every sitting, oldest first."""
    dates = []
    for path in sitting_files():
        blob = storage.read_json(path)
        if blob and blob.get("date"):
            dates.append(S.iso_date(blob["date"]) or blob["date"])
    dates = sorted(set(d for d in dates if d))
    if not dates:
        raise SystemExit("no sitting files under data/ -- nothing to know which days to "
                         "fetch. --source sittings would work against whatever is there.")
    if since:
        dates = [d for d in dates if d >= since]
    for i, date in enumerate(dates, 1):
        t0 = time.time()
        blob, cached = fetch_sitting_raw(date, log=log, force=force)
        if not blob:
            log(f"[{i}/{len(dates)}] {date}  no reports (not a sitting?)")
            continue
        log(f"[{i}/{len(dates)}] {date}  {len(blob['reports'])} reports "
            f"{'(cached)' if cached else f'({time.time() - t0:.0f}s)'}"
            + (f"  failures={len(blob.get('failures') or [])}" if blob.get("failures") else ""))
        ctx = context_from_sitting({"date": date, "coverage": blob.get("coverage")})
        rows = [report_from_raw(rid, e) for rid, e in sorted(blob["reports"].items())]
        yield ctx, rows, {"date": date, "source": "live", "cached": cached,
                          "failures": blob.get("failures") or []}
        if not cached:
            time.sleep(PAUSE)


# ------------------------------------------------------------------------ writing
def jsonl_row(obj):
    """One JSON object per line. Compact separators: this is machine-read, and the corpus
    is ~25 minutes of JSON either way."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def write_jsonl(path, rows):
    """Atomic: temp file + os.replace, via the project's single writer.

    A half-written output is worse than no output -- a consumer that reads it sees a
    truncated corpus and has no way to tell. Same reasoning as scraper/storage.py's.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = "".join(jsonl_row(r) + "\n" for r in rows)
    storage.write_text_atomic(path, text)
    return len(text)


def map_sitting(reports, ctx, roster, stats):
    """Map one sitting's reports, updating `stats` for every refusal and failure.

    Extracted from `run()` so the accounting has exactly one implementation -- and so a test
    can reach it. The previous version kept this loop inline, which meant a test could not
    assert that a refused record was *counted* as well as refused, and that is the half that
    matters: an ingester that silently drops records reports success.
    """
    for rep in reports:
        stats["reports_read"] += 1
        doc, skipped = map_report(rep, ctx, roster, stats=stats)
        if doc is not None:
            yield doc
            continue
        if isinstance(skipped, tuple):
            stats["errors"][skipped[0]] += 1
            if len(stats["error_examples"]) < 10:
                stats["error_examples"].append(
                    {"doc_id": rep.get("report_id"), "why": skipped[0],
                     "detail": str(skipped[1])[:80]})
        else:
            stats["skipped"][skipped] += 1


def archive_report_ids(dates):
    """The report ids the committed archive holds for these dates.

    Used by a live run to say how far its output diverges from `--source sittings`' output.
    The two are snapshots taken at different times and they DO differ: measured on the 21
    cached sittings, the live enumeration returned 16 records absent from the archive
    (`attendance-*`, `ptba-*`, 11 `written-answer-*` on one day) while the archive held 14 the
    live cache lacked (docs/probes/ingest_attendance_gap.py). That is a fact about the source,
    not a bug in the mapper -- but it means the normalized corpus is not reproducible across
    the two paths, which is worth printing rather than discovering later.
    """
    ids = set()
    for date in dates:
        path = os.path.join(DATA, date[:4], f"sitting_{date}.json")
        blob = storage.read_json(path)
        if not blob:
            continue
        ids |= {r["report_id"].rstrip("#") for r in blob.get("reports") or []}
    return ids


def blank_stats():
    return {
        "sittings": 0, "reports_read": 0, "rows": 0,
        "skipped": collections.Counter(), "errors": collections.Counter(),
        "error_examples": [],
        "turns_by_kind": collections.Counter(), "principal_kind": collections.Counter(),
        "by_section": collections.Counter(), "by_doc_type": collections.Counter(),
        "turns_dropped_blank": 0, "questions": 0,
        "title_fallback": 0, "title_fallback_ids": [],
        "word_count_mismatch": [], "division_rows": [], "division_overflow": [],
        "unresolved_strings": collections.Counter(),
        "low_coverage_sittings": [],
        "fetch_failures": [],
        "invalid_rows": [],
    }


def run(args, log=print):
    t_start = time.time()
    since = S.iso_date(args.since) if args.since else None
    if args.since and not since:
        raise SystemExit(f"--since not parseable as a date: {args.since!r} "
                         f"(use YYYY-MM-DD or D-M-YYYY)")

    log(f"ingest/debates.py  source={args.source}  limit={args.limit}  since={since}")
    roster = load_roster(offline=(args.source == "sittings"), log=log)
    log(f"  roster: {roster.member_count} members "
        f"({len(roster.current)} current, {len(roster.former)} former), "
        f"{len(roster.collisions)} normalized collision(s), "
        f"{len(roster)} name entries")

    stats = blank_stats()
    rows = []
    seen_dates = []

    source = args.source
    if source == "sittings":
        sittings = iter_source_sittings(since=since)
    else:
        sittings = iter_live_sittings(since=since, force=args.force, log=log)

    for ctx, reports, meta in sittings:
        stats["sittings"] += 1
        if meta.get("date"):
            seen_dates.append(meta["date"])
        if meta.get("failures"):
            stats["fetch_failures"].extend(
                [dict(f, date=meta.get("date")) for f in meta["failures"]])
        cov = ctx.get("coverage") or {}
        try:
            ratio = float(cov.get("ratio")) if cov.get("ratio") is not None else None
        except (TypeError, ValueError):
            ratio = None
        if ratio is not None and ratio < 0.95:
            stats["low_coverage_sittings"].append(
                {"date": ctx.get("date"), "ratio": ratio})
        for doc in map_sitting(reports, ctx, roster, stats):
            rows.append(doc)
            stats["rows"] += 1
            if args.limit and len(rows) >= args.limit:
                break
        if args.limit and len(rows) >= args.limit:
            break

    # Every emitted row is validated. This is the acceptance criterion P1 and it is
    # checked here rather than promised, because "it should be valid" and "it is valid" are
    # different claims and only one of them is worth reporting.
    log(f"  validating {len(rows)} rows against docs/schema.json ...")
    validator = S.document_validator()
    for doc in rows:
        errs = [f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}"
                for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path))]
        if errs:
            stats["invalid_rows"].append({"doc_id": doc["doc_id"], "errors": errs[:3]})

    # Unresolved speakers, by string, so a wrong attribution is visible instead of silent.
    for doc in rows:
        for t in doc["turns"]:
            if t["speaker"] and t["member_id"] is None:
                kind, _ = roster.resolve(t["speaker"])
                if kind == "UNRESOLVED":
                    stats["unresolved_strings"][t["speaker"]] += 1

    out_text = write_jsonl(args.out, rows)
    members = roster.members_jsonl()
    write_jsonl(args.members, members)

    elapsed = round(time.time() - t_start, 1)
    report = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "since": since,
        "limit": args.limit,
        "elapsed_secs": elapsed,
        "out": os.path.relpath(args.out, ROOT),
        "out_bytes": out_text,
        "members_out": os.path.relpath(args.members, ROOT),
        "sittings_read": stats["sittings"],
        "reports_read": stats["reports_read"],
        "rows": len(rows),
        "invalid_rows": len(stats["invalid_rows"]),
        "skipped_legislation_partition": stats["skipped"].get("legislation_partition", 0),
        "map_errors": dict(stats["errors"]),
        "error_examples": stats["error_examples"],
        "turns": dict(stats["turns_by_kind"]),
        "principal_speaker": dict(stats["principal_kind"]),
        "by_section": dict(stats["by_section"]),
        "by_doc_type": dict(stats["by_doc_type"]),
        "turns_dropped_blank": stats["turns_dropped_blank"],
        "questions_extracted": stats["questions"],
        "division_rows": stats["division_rows"],
        "division_overflow": stats["division_overflow"],
        "title_fallbacks": stats["title_fallback"],
        "title_fallback_ids": stats["title_fallback_ids"],
        "word_count_mismatch": stats["word_count_mismatch"],
        "unresolved_speaker_strings": dict(
            sorted(stats["unresolved_strings"].items(), key=lambda kv: -kv[1])),
        "low_coverage_sittings": stats["low_coverage_sittings"],
        "fetch_failures": stats["fetch_failures"],
        "members": len(members),
        "members_with_aliases": sum(1 for m in members if m["aliases"]),
        "roster_name_entries": len(roster),
        "roster_members": roster.member_count,

        # Counts this half of the corpus cannot stand behind. `turns_by_kind` above is the
        # partition's count; spec 3.3 quotes corpus-wide ones, and the honest thing is to say
        # so in the file a reviewer reads rather than let the two be confused.
        "resolution_counts_scope": (
            "partition: sections other than bill/motion. Corpus-wide totals (both partitions) "
            "are reproduced by docs/probes/ingest_resolution_ledger.py. NOTE that spec 3.3's "
            "quoted table is now 4 turns stale: it reads MEMBER 1,148/69,829, OFFICER "
            "17/19,946, NON_PERSON 11/209, UNRESOLVED 31/52, and the measured figures are "
            "OFFICER 19/19,948, NON_PERSON 13/211, UNRESOLVED 27/48, MEMBER unchanged. The "
            "spec's numbers predate the invisible-mark fix (a BOM glued to a speaker string "
            "made a chamber officer uncountable, see clean_speaker); every share is "
            "unchanged at 77.6/22.2/0.2/0.1%"),
    }

    # A live run is a SNAPSHOT, and it disagrees with the committed archive in both
    # directions. Printing the divergence is the difference between "the corpus" and "the
    # corpus as of whenever this ran" -- and a downstream index build that cannot reproduce
    # its input needs to know which one it is holding (docs/probes/ingest_attendance_gap.py).
    if source == "live" and seen_dates:
        arch = archive_report_ids(seen_dates)
        if arch:
            wrote = {r["doc_id"] for r in rows}
            report["live_vs_archive"] = {
                "sittings_compared": len(set(seen_dates)),
                "dates": sorted(set(seen_dates)),
                "live_only": len(wrote - arch),
                "archive_only": len(arch - wrote),
                "live_only_examples": sorted(wrote - arch)[:6],
                "archive_only_examples": sorted(arch - wrote)[:6],
                "note": ("the two paths are snapshots taken at different times and do not "
                         "agree on every report id; `--source sittings` is the reproducible "
                         "one and is what the corpus counts in docs/rag_spec.md rest on"),
            }
    if not args.no_report:
        os.makedirs(os.path.dirname(args.report), exist_ok=True)
        storage.write_json_atomic(args.report, report)

    log("")
    log(f"rows written        : {len(rows)}  -> {os.path.relpath(args.out, ROOT)} "
        f"({out_text / 1e6:.1f} MB)")
    log(f"members written     : {len(members)}  -> {os.path.relpath(args.members, ROOT)}"
        f"  ({report['members_with_aliases']} with observed aliases)")
    log(f"sittings / reports  : {stats['sittings']} sittings, "
        f"{stats['reports_read']} reports read")
    log(f"schema validation   : {len(rows) - len(stats['invalid_rows'])}/{len(rows)} pass")
    log(f"legislation skipped : {report['skipped_legislation_partition']} "
        f"(bill/motion -> ingest/legislation.py)")
    log(f"sections            : {dict(stats['by_section'].most_common())}")
    log(f"doc_types           : {dict(stats['by_doc_type'])}")
    log(f"turns by resolution : {dict(stats['turns_by_kind'])}")
    log(f"principal speakers  : {dict(stats['principal_kind'])}")
    log(f"questions extracted : {stats['questions']}")
    log(f"division rows       : {len(stats['division_rows'])} "
        f"{[d['doc_id'] for d in stats['division_rows']][:6]}")
    log(f"turns dropped blank : {stats['turns_dropped_blank']}")
    log(f"title fallbacks     : {stats['title_fallback']} {stats['title_fallback_ids'][:5]}")
    log(f"word-count mismatch : {len(stats['word_count_mismatch'])} "
        f"{stats['word_count_mismatch'][:3]}")
    log(f"unresolved speakers : {len(stats['unresolved_strings'])} distinct string(s), "
        f"{sum(stats['unresolved_strings'].values())} turn(s)")
    for s, n in sorted(stats["unresolved_strings"].items(), key=lambda kv: -kv[1])[:8]:
        log(f"    {n:4d}  {s!r}")
    log(f"low-coverage sittings: {len(stats['low_coverage_sittings'])} "
        f"(ratio < 0.95; the listing API dropped rows those days)")
    log(f"fetch failures      : {len(stats['fetch_failures'])}")
    if "live_vs_archive" in report:
        v = report["live_vs_archive"]
        log(f"live vs archive     : {v['live_only']} row(s) not in the committed archive, "
            f"{v['archive_only']} archive report(s) not in this run "
            f"(snapshot drift, not a mapping error)")
    log(f"elapsed             : {elapsed}s")

    if stats["invalid_rows"] or stats["errors"]:
        for r in stats["invalid_rows"][:5]:
            log(f"  INVALID {r['doc_id']}: {r['errors']}")
        for e in stats["error_examples"][:5]:
            log(f"  ERROR   {e['doc_id']}: {e['why']} {e['detail']}")
    if stats["invalid_rows"]:
        log("RESULT: FAIL -- rows written that do not validate")
        return 1
    log("RESULT: PASS -- every written row validates against docs/schema.json")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Normalize the Singapore Hansard corpus into RAG documents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="python3 ingest/debates.py --limit 500\n"
               "python3 ingest/debates.py --source sittings   # offline, no network\n")
    ap.add_argument("--limit", type=int, default=None,
                    help="write at most N documents (a smoke run, not a sample)")
    ap.add_argument("--since", default=None,
                    help="only documents dated on/after this date (YYYY-MM-DD or D-M-YYYY)")
    ap.add_argument("--source", choices=("live", "sittings"), default="live",
                    help="live = fetch from sprs.parl.gov.sg, caching raw responses under "
                         "data/raw/ and reusing them on a rerun (default); "
                         "sittings = map the committed archive under data/<year>/ offline")
    ap.add_argument("--force", action="store_true",
                    help="with --source live, re-fetch even when a raw cache exists")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--members", default=MEMBERS_DEFAULT,
                    help="the roster resource: one `member` record per MP, with the "
                         "speaker strings this run observed resolving to them")
    ap.add_argument("--report", default=REPORT_DEFAULT,
                    help="run report: counts, coverage flags, unresolved speakers")
    ap.add_argument("--no-report", action="store_true", help="skip writing the run report")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
