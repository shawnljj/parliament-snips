#!/usr/bin/env python3
"""
Parsnips RAG — legislation ingestion: bills, motions, divisions and the member roster.

BINDING INPUTS (read them before changing anything here)
-------------------------------------------------------
  docs/rag_spec.md  §1.1 source, §1.2 roster, §1.4 what the source cannot give us,
                    §2 corpus + the division rule, §3 document schema + member rule,
                    §8 the paragraph for this task
  docs/schema.json  the normalized document / member schema. Field names, enums and
                    required sets are binding; this module invents none of them.

SCOPE, AND THE PARTITION WITH ingest/debates.py
----------------------------------------------
Every report in `data/<year>/sitting_*.json` whose section is `bill` or `motion` — 1,795
records (bill 1,002 + motion 793). `ingest/debates.py` refuses exactly those two sections
and owns the other 19,089, so the two halves sum to all 20,884 reports with nothing in
neither and nothing in both. `docs/probes/ingest_partition_check.py` (in that task) and the
`selection` line in this module's report both re-derive that boundary from the archive.

**The 18 measured division declarations split 17 / 1 across that boundary.** The one that
is not mine is `budget-855` (89/8/0) — a `budget` record, in the debates half, already
written by `ingest/debates.py` with its `division` intact. So `--outside-divisions` is OFF
by default: emitting it here too would put the same `doc_id` in two JSONL files. All 17
legislation-half declarations are in this module's output; if a count here ever reads fewer,
declarations are being lost rather than deduplicated.

NOT in scope, and why:
  * `debate` prose (written/oral answers, statements, budget debate) is `ingest/debates.py`.
  * **Per-member votes do not exist** (spec §1.4, measured four ways). `division.per_member`
    is typed `null` in the schema and populating it is a schema error, so it is always
    written as `null`. The card that asked for a per-member breakdown was superseded by
    spec §8; what is extracted instead is the Chair's declaration (totals).
  * Member *biographies*: the roster serves a name and nothing else — no birth date, no
    prose, no constituency, no id (`id: 0` on all 574). A `member_bio` *document* would
    therefore need an invented `doc_id`, `date`, `section`, `url` and `text`, and the
    schema forbids minting ids ("Never mint a synthetic id"). So members are written as
    the record type the schema declares for exactly this data — `#/$defs.member` — into
    `data/normalized/members.jsonl`. See `doc_type: member_bio` note in --help.

USAGE
-----
    python3 ingest/legislation.py --limit 500      # smoke run, 500 report rows
    python3 ingest/legislation.py                  # full run (1,796 report rows)
    python3 ingest/legislation.py --since 2020-01-01
    python3 ingest/legislation.py --check           # validate + referential integrity
    python3 ingest/legislation.py --report          # counts only, write nothing

Raw responses are cached under `data/raw/legislation/` so a rerun needs no network
(`--offline`). Reuses `scraper/parsnips_fetch.py` for transport constants and
`scraper/storage.py` for the atomic write — this module deliberately does not grow a
second HTTP client or a second writer.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import storage  # noqa: E402  (the project's single atomic writer)
import parsnips_fetch as PF  # noqa: E402  (transport constants + group_for)

DATA = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA, "raw", "legislation")
SCHEMA_PATH = os.path.join(ROOT, "docs", "schema.json")
NORMALIZED_DIR = os.path.join(DATA, "normalized")
LEGISLATION_OUT = os.path.join(NORMALIZED_DIR, "legislation.jsonl")
MEMBERS_OUT = os.path.join(NORMALIZED_DIR, "members.jsonl")

# The schema's enum, restated so a bad `group` cannot leak into a `section`.
VALID_SECTIONS = ("bill", "motion", "oral", "written", "budget", "adjournment",
                  "statement", "correction", "tribute", "petition", "other")

# spec §2: doc_type is the lossy closed filter vocabulary over `section`.
GROUP_TO_DOC_TYPE = {"bill": "bill", "motion": "motion"}

DATE_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")

# ---------------------------------------------------------------------------------
# Member resolution — spec §3.3, rule and ORDER taken verbatim.
#
# The order is the whole trick: honorifics are NOT stripped before the chamber-officer
# test, because "Mr Speaker" is only recognisable *with* the honorific. The earlier
# probe that stripped first reported a 14.6% unresolved rate; the real figure is 0.1%.
# ---------------------------------------------------------------------------------
HONORIFICS = re.compile(r"\b(Mr|Mrs|Ms|Miss|Mdm|Madam|Dr|Professor|Prof|Asst Prof|"
                        r"Assoc Prof|Er Dr|Er)\b\.?", re.I)
TRAILING_PAREN = re.compile(r"\((?:[^()]*)\)\s*$")
BRACKETED = re.compile(r"^[\[(].*[\])]$")
OFFICER_RE = re.compile(r"^(the\s+)?(mr|mdm|madam|miss|ms)?\s*(deputy\s+)?"
                        r"(speaker|chairman|deputy\s+chairman|chair)\b", re.I)
# "Hon Members" / "Some hon Members" / "An hon Member" are the House collectively, not a
# person. The rule is deliberately narrow -- "hon" ONLY when followed by "member(s)".
# A bare `^(hon|honourable)` prefix also swallows the roster's own **Hon Sui Sen**, and
# because this test runs BEFORE the roster match he could then never resolve to himself:
# measured, `sg-mp:hon-sui-sen`'s own full name resolved to `non_person`, and the audit
# caught the alias not re-resolving. Narrowing loses nothing -- both rules catch the same
# 3 strings and the same 64 turns -- and un-shadows a real MP.
NON_PERSON_RE = re.compile(r"^(hon|honourable|some hon|an hon)\s+members?\b", re.I)

# Resolution kinds. "member:raw" / "member:paren" / ... record WHICH candidate matched,
# because a paren match means the prefix was the portfolio, not the seat.
K_OFFICER, K_NON_PERSON, K_UNRESOLVED, K_NONE = "officer", "non_person", "unresolved", "none"


def slug_member_id(full_name):
    """spec §3.3: 'sg-mp:' + the roster spelling, lowercased, non-alphanumerics
    collapsed to single hyphens."""
    s = unicodedata.normalize("NFKD", full_name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return f"sg-mp:{s}" if s else None


def norm_name(s):
    """The normalized form used for roster matching: lowercase, honorifics and a
    *trailing* parenthetical dropped, non-alphanumerics stripped."""
    s = (s or "").lower()
    s = TRAILING_PAREN.sub("", s)
    s = HONORIFICS.sub(" ", s)
    s = re.sub(r"[^a-z\s'-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def iso_date(src):
    """'15-1-2016' -> '2016-01-15'. The source is NOT zero-padded, and a lexicographic
    range filter over a raw date silently mis-sorts (spec §3.2). Convert at ingest."""
    m = DATE_RE.match((src or "").strip())
    if not m:
        return src
    d, mo, y = m.groups()
    return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"


def split_mp_names(raw):
    """Split the source's `mp_names` on commas that are NOT inside parentheses.

    Measured need: a plain `split(",")` smashes "The Second Minister for Law
    (Mr Edwin Tong Chun Fai)" into two bogus fragments, and 108 of the corpus's name
    fragments are mangled that way. Depth-aware splitting on `(` `)` leaves exactly 1 bad
    fragment corpus-wide ('Ms Joan Pereira Tanjong Pagar)', a source-side unclosed paren).

    SQUARE BRACKETS ARE STRIPPED, NOT TRACKED. They are not reliable delimiters in this
    field: the source writes an opening `[` with no closing one (measured: '[Ms Anthea Ong',
    '[Mr Louis Ng Kok Kwang', '[The Chairman'), so treating `[` as a depth increase makes the
    whole list one fragment. Parentheses are the reliable pair — they carry portfolios and
    constituencies and are always balanced (1 exception in 60,370 fragments) — so only they
    affect splitting, and `[`/`]` are removed as noise.
    """
    s = (raw or "").replace("[", "").replace("]", "")
    out, buf, depth = [], [], 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


class MemberSet:
    """The roster, indexed for resolution, plus everything derived from observed aliases.

    Built from the `/fetchData` response only — spec §1.2. There is no member id in the
    source, so the roster's spelling of the name IS the key (§3.3).
    """

    def __init__(self, roster_payload):
        self.current, self.former = [], []
        self.duplicate_names = 0
        seen = {}
        for key, status, bucket in (("mpVOList", "current", self.current),
                                    ("formerMPVOList", "former", self.former)):
            for m in roster_payload.get(key) or []:
                name = (m.get("fullName") or "").strip()
                if not name:
                    continue
                mid = slug_member_id(name)
                # The roster serves 574 name *entries* but only 569 distinct member_ids:
                # 4 names are listed twice (once current, once former) and "R Ravindran" /
                # "R. Ravindran" collide on the slug rule. The entry is dropped BEFORE it
                # reaches the bucket, because a second row for the same person would make
                # `records()` emit a duplicate member_id -- which is a real defect, not a
                # cosmetic one: nothing downstream could tell which row is authoritative.
                if mid in seen:
                    self.duplicate_names += 1
                    continue
                seen[mid] = name
                bucket.append({"member_id": mid, "full_name": name, "status": status})
        self.by_id = seen
        # normalized -> member_id. A normalized collision is a WARNING, not a failure: the
        # spec measures exactly one ("R Ravindran" vs "R. Ravindran") and it is the same
        # person, so first-wins is correct and the count is reported rather than raised.
        self.by_norm = {}
        self.collisions = collections.defaultdict(set)
        for mid, name in seen.items():
            n = norm_name(name)
            self.by_norm.setdefault(n, mid)
            # collect the ROSTER NAME that owns each normalized form, not the member_id --
            # keying on the id makes the collision set a self-reference and the count
            # always zero, which is how this was wrong the first time.
            self.collisions[n].add(name)
        self.collisions = {k: v for k, v in self.collisions.items() if len(v) > 1}
        # filled by observe()
        self.aliases = collections.defaultdict(set)
        self.portfolio = collections.defaultdict(collections.Counter)
        self.constituency = collections.defaultdict(collections.Counter)
        self.observed = collections.Counter()

    # -- the rule, in the mandated order ------------------------------------------
    def resolve(self, raw):
        """-> (member_id|None, kind). Never guesses: unresolved keeps `speaker_raw`."""
        if not raw:
            return (None, K_NONE)
        s = raw.strip()
        if BRACKETED.match(s):
            return (None, K_NON_PERSON)
        if OFFICER_RE.match(s):                    # 1. officers FIRST, honorific intact
            return (None, K_OFFICER)
        if NON_PERSON_RE.match(norm_name(s)):      # 2. bracketed / "Hon Members"
            return (None, K_NON_PERSON)
        # 3. roster match on the full form, the normalized form, the parenthetical
        #    contents, and the text before the first "(" — the last is how
        #    "The Minister for Home Affairs (Mr K Shanmugam)" resolves.
        parts = re.findall(r"\(([^()]*)\)", s)
        cands = [(s, "raw"), (norm_name(s), "bare")]
        cands += [(c, "paren") for c in parts]
        if "(" in s:
            cands.append((s[:s.index("(")], "prefix"))
        for cand, where in cands:
            mid = self.by_norm.get(norm_name(cand))
            if mid:
                return (mid, f"member:{where}")
        return (None, K_UNRESOLVED)

    def member_id_of(self, raw):
        return self.resolve(raw)[0]

    # -- alias / portfolio / constituency harvest ---------------------------------
    def observe(self, raw):
        """Record one observed speaker string. Aliases are the audit trail that makes a
        speaker filter work across a decade of portfolio reshuffles (§3.3)."""
        mid, kind = self.resolve(raw)
        if not mid or not raw:
            return mid
        s = raw.strip()
        if s not in self.aliases[mid]:
            self.aliases[mid].add(s)
            self.observed[mid] += 1
            parts = re.findall(r"\(([^()]*)\)", s)
            pre = s[:s.index("(")].strip() if "(" in s else None
            matched_where = None
            for cand, where in ([(s, "raw"), (norm_name(s), "bare")]
                                + [(c, "paren") for c in parts]
                                + ([(pre, "prefix")] if pre else [])):
                if self.by_norm.get(norm_name(cand)) == mid:
                    matched_where = where
                    break
            if matched_where == "paren" and pre:
                # "The Minister for Home Affairs (Mr K Shanmugam)" — parenthetical is the
                # NAME, so the prefix is the office held at that time.
                self.portfolio[mid][pre] += 1
            elif parts and matched_where in ("raw", "bare") and pre is not None:
                # "Mr Pritam Singh (Aljunied)" — trailing parenthetical is the seat.
                seat = parts[-1].strip()
                if seat and norm_name(seat) != norm_name(self.by_id[mid]):
                    self.constituency[mid][seat] += 1
            elif parts and matched_where in ("raw", "bare") and pre is None:
                self.constituency[mid][parts[-1].strip()] += 1
        return mid

    def _common(self, table, mid):
        c = table.get(mid)
        if not c:
            return None
        top = max(c.values())
        return sorted(k for k, v in c.items() if v == top)[0] or None

    def records(self):
        """One schema-valid `#/$defs.member` per roster entry, current then former."""
        out = []
        for m in self.current + self.former:
            mid = m["member_id"]
            out.append({
                "member_id": mid,
                "full_name": m["full_name"],
                "aliases": sorted(self.aliases.get(mid, set())) or [m["full_name"]],
                "status": m["status"],
                "constituency": self._common(self.constituency, mid),
                "portfolio": self._common(self.portfolio, mid),
                "source": "sg-mp-roster",
                "url": "https://www.parliament.gov.sg/mps/list-of-current-mps",
            })
        return out


# ---------------------------------------------------------------------------------
# Divisions — the measured rule from docs/probes/probe_division.py (spec §2).
# Copied rather than re-derived: the probe is the named source of the measurement
# "18 declarations, 12 distinct outcomes, 0 unparsed", and a second rule would make
# that number unreproducible.
# ---------------------------------------------------------------------------------
WORD_NUM = {"no": 0, "none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
            "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}

DECLARATION = re.compile(
    r'There\s+(?:are|is)\s+([A-Za-z0-9,]+)\s*"?Ayes?"?\s*,\s*'
    r'([A-Za-z0-9,]+)\s*"?Noes?"?\s*(?:,?\s*(?:and\s+)?([A-Za-z0-9,]+)\s*"?Abstentions?"?)?',
    re.I)


def _decl_count(token):
    """A count as stated: digits, a number word, or 'no'/'none'/'zero'. Else None."""
    if token is None:
        return None
    t = token.strip().strip(",").lower()
    if not t:
        return None
    if t in WORD_NUM:
        return WORD_NUM[t]
    if re.fullmatch(r"\d+", t):
        return int(t)
    return None


def parse_declaration(match):
    """-> dict(ayes, noes, abstentions, raw) or None when a count does not parse.

    `abstentions` is None when the Chair did not state one — that is a different fact
    from a stated zero, which is why the schema allows null there.
    """
    ayes = _decl_count(match.group(1))
    noes = _decl_count(match.group(2))
    if ayes is None or noes is None:
        return None
    return {"ayes": ayes, "noes": noes,
            "abstentions": _decl_count(match.group(3)),
            "raw": match.group(0).strip()}


def extract_declarations(text):
    """Every parseable Chair's declaration in one record's text, in document order."""
    out = []
    for m in DECLARATION.finditer(text or ""):
        d = parse_declaration(m)
        if d:
            out.append(d)
    return out


def dedupe_declarations(decls):
    """spec §2: deduplicate on (ayes, noes, abstentions) per document.

    A record covering two readings declares twice — bill-213 reads the same result at
    second and third reading, bill-367 declares 74/9/1 then 72/9/3. The same numbers
    twice is one outcome; different numbers are two. Keeps first-seen order.
    """
    seen, out = set(), []
    for d in decls:
        key = (d["ayes"], d["noes"], d["abstentions"])
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def division_metadata(decls):
    """`metadata.division` (the named single-declaration field the schema requires) plus
    `metadata.divisions` (every distinct declaration, in order).

    Why both. `#/$defs/metadata.division` is a single object, so a two-reading record
    cannot put both of its declarations in it — and `metadata` is declared as the open
    bag ("the ONLY place a source-specific field may live", `additionalProperties: true`),
    so the full ordered list is legal there and is what makes the card's "deduplicate on
    (doc_id, ayes, noes, abstentions)" observable. `division` carries the first
    declaration; nothing downstream should assume it is the only one.
    """
    if not decls:
        return None, None
    shaped = [{"ayes": d["ayes"], "noes": d["noes"], "abstentions": d["abstentions"],
               "raw": d["raw"], "tellers": None,
               # ALWAYS null — see the module docstring and spec §1.4. Populating this is
               # a schema error, and the schema's own must-reject test asserts it.
               "per_member": None} for d in decls]
    return shaped[0], shaped


# ---------------------------------------------------------------------------------
# Document mapping
# ---------------------------------------------------------------------------------
MOVE_RE = re.compile(r"\bbeg to move\b", re.I)


def _sponsor_of(rep, members):
    """The bill's sponsor / motion's mover, as a cross-link to the member set.

    `Bills Introduced` records carry the sponsor in `mp_names[0]` as an already-bare name
    (spec §8) — the record's own turns are procedural only. Every other bill/motion
    record names its mover in the turn that "beg[s] to move" the question.
    Measured: 430/439 Bills Introduced carry `mp_names`; 654/655 movers resolve.
    """
    rtype = rep.get("report_type")
    if rtype == "Bills Introduced":
        names = split_mp_names(rep.get("mp_names"))
        if names:
            return names[0], "mp_names[0]"
        return None, None
    for t in rep.get("turns") or []:
        if t.get("speaker") and MOVE_RE.search(t.get("text") or ""):
            return t["speaker"], "beg-to-move turn"
    return None, None


def document_from(rep, members, coverage):
    """One source report -> one `#/$defs.document`. Adds no field the schema lacks."""
    turns = []
    for t in rep.get("turns") or []:
        raw = t.get("speaker")
        turns.append({
            "speaker": raw,
            "member_id": members.member_id_of(raw) if raw else None,
            "text": t.get("text") or "",
            "lang": t.get("lang") or "English",
            "time": t.get("time") or None,
            "is_procedural": bool(t.get("is_procedural")),
            "words": t.get("words", len((t.get("text") or "").split())),
        })
    doc_id = (rep.get("report_id") or "").rstrip("#")
    group = rep.get("group")
    section = group if group in VALID_SECTIONS else "other"
    doc_type = GROUP_TO_DOC_TYPE.get(section, "debate")

    body = "\n".join(f'{t["speaker"]}: {t["text"]}' if t["speaker"] else t["text"]
                     for t in turns if (t["text"] or "").strip())
    principal = next((t["speaker"] for t in turns if t["speaker"]), None)

    text_for_divisions = "\n".join((t.get("text") or "") for t in turns)
    decls = dedupe_declarations(extract_declarations(text_for_divisions))
    division, divisions = division_metadata(decls)

    sponsor_raw, sponsor_src = _sponsor_of(rep, members)
    sponsor_id = members.member_id_of(sponsor_raw) if sponsor_raw else None
    if not principal and sponsor_raw:
        # Bills Introduced: every turn is bracketed chair/clerical text, so the only
        # attributable human in the record is the sponsor named in `mp_names`.
        principal = sponsor_raw

    return {
        "doc_id": doc_id,
        "source": "sg-hansard",
        "doc_type": doc_type,
        "section": section,
        "date": iso_date(rep.get("sitting_date")),
        "title": (rep.get("title") or "").strip() or doc_id,
        "speaker_raw": principal,
        "member_id": members.member_id_of(principal) if principal else None,
        "text": body,
        "turn_count": len(turns),
        "word_count": int(rep.get("words") or 0),
        "url": PF.BASE + "/#/sprs3topic?reportid=" + doc_id,
        "turns": turns,
        "metadata": {
            "parliament_no": rep.get("parliament_no"),
            "sitting_no": rep.get("sitting_no"),
            "volume_no": rep.get("volume_no"),
            "report_version": rep.get("report_version"),
            "report_type": rep.get("report_type"),
            "mp_names_raw": rep.get("mp_names"),
            "division": division,
            "divisions": divisions,
            "sponsor": ({"raw": sponsor_raw, "member_id": sponsor_id,
                         "identified_by": sponsor_src} if sponsor_raw else None),
            "question": None,
            # Propagated, never discarded: below 0.95 the listing API dropped rows for
            # that day and the document set is known-incomplete (schema metadata note).
            "coverage_ratio": (coverage or {}).get("ratio"),
            "speaker_attribution": (coverage or {}).get("speaker_attribution"),
            "ingest": {"pipeline": "ingest/legislation.py", "rule": "spec-3.3"},
        },
    }


# ---------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------
def fetch_roster(refresh=False, offline=False):
    """The roster, from cache when possible. `data/raw/legislation/roster.json` is the
    raw response, kept verbatim so a rerun is network-free and the resolution can be
    re-checked later."""
    cache = os.path.join(RAW_DIR, "roster.json")
    if not refresh and os.path.exists(cache):
        payload = storage.read_json(cache)
        if payload:
            return payload, "cache"
    if offline:
        raise SystemExit(f"--offline and no cached roster at {cache}. "
                         f"Run once without --offline.")
    payload = PF.post("fetchData", {})
    storage.write_json_atomic(cache, payload)
    return payload, "live"


def iter_sittings(since=None):
    """Every sitting file dated >= `since`, in date order. Reads the cached archive only —
    this ingester never re-fetches Hansard (spec §1.1: a full backfill is already done)."""
    files = sorted(glob.glob(os.path.join(DATA, "*", "sitting_*.json")))
    for fp in files:
        blob = storage.read_json(fp, {}) or {}
        date = iso_date(blob.get("date"))
        if since and date < since:
            continue
        yield fp, blob


def select_records(files, since=None, outside_divisions=False):
    """-> (records, stats). Records are (rep, coverage) pairs, date-sorted.

    The partition boundary with `ingest/debates.py` is `section in ("bill", "motion")` —
    the same two the other ingester refuses, so nothing lands in neither half. Every
    section is counted either as picked or as skipped, which is what makes the `selection`
    line of the report a completeness check rather than a sample.
    """
    stats = collections.Counter()
    picked = []
    for fp, blob in files:
        coverage = blob.get("coverage") or {}
        for rep in blob.get("reports") or []:
            date = iso_date(rep.get("sitting_date"))
            if since and date < since:
                continue
            group = rep.get("group")
            if group in ("bill", "motion"):
                stats["section_" + group] += 1
                picked.append((rep, coverage))
                continue
            if not outside_divisions:
                stats["skipped_" + str(group)] += 1
                continue
            text = "\n".join((t.get("text") or "") for t in rep.get("turns") or [])
            if dedupe_declarations(extract_declarations(text)):
                # Off by default, because the one record this matches (budget-855) is
                # written by ingest/debates.py with its division intact — emitting it here
                # would be the same doc_id in two files, not extra coverage.
                stats["outside_" + str(group)] += 1
                picked.append((rep, coverage))
            else:
                stats["skipped_" + str(group)] += 1
    picked.sort(key=lambda p: (iso_date(p[0].get("sitting_date")), p[0].get("report_id") or ""))
    return picked, stats


# ---------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------
def write_jsonl(path, rows):
    """One JSON object per line, written atomically through the project's single writer."""
    body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n" for r in rows)
    storage.write_text_atomic(path, body)
    return len(rows)


def validate(rows, subdef, schema=None):
    """Validate against docs/schema.json. Returns a list of failure strings."""
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema not installed (pip install jsonschema)"]
    schema = schema or json.load(open(SCHEMA_PATH, encoding="utf-8"))
    wrapper = dict(schema["$defs"][subdef])
    wrapper["$defs"] = schema["$defs"]
    failures = []
    for r in rows:
        try:
            jsonschema.validate(r, wrapper)
        except jsonschema.ValidationError as e:
            failures.append(f"{r.get('doc_id') or r.get('member_id')}: {e.message} "
                            f"@ {'/'.join(map(str, e.path))}")
    return failures


def referential_integrity(docs, member_rows):
    """spec §8 / the card's revised criterion: every NON-NULL member_id anywhere in the
    output exists in the roster.

    The weaker "every sponsor has a member_id" is impossible — the roster serves no ids
    and unresolved names must keep `speaker_raw` with `member_id: null`. Measured here:
    574 roster entries; 33 distinct unresolved speaker strings corpus-wide, the largest
    being a roster-name alias. Reported, never silenced.
    """
    roster_ids = {m["member_id"] for m in member_rows}
    problems = []
    for d in docs:
        if d["member_id"] and d["member_id"] not in roster_ids:
            problems.append(f"{d['doc_id']}: document member_id {d['member_id']} not in roster")
        sp = (d["metadata"] or {}).get("sponsor") or {}
        if sp.get("member_id") and sp["member_id"] not in roster_ids:
            problems.append(f"{d['doc_id']}: sponsor member_id {sp['member_id']} not in roster")
        for i, t in enumerate(d["turns"]):
            if t["member_id"] and t["member_id"] not in roster_ids:
                problems.append(f"{d['doc_id']}: turn {i} member_id {t['member_id']} not in roster")
    return problems


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


# ---------------------------------------------------------------------------------
def build(args):
    members_payload, roster_src = fetch_roster(refresh=args.refresh_roster,
                                               offline=args.offline)
    members = MemberSet(members_payload)

    files = list(iter_sittings(since=args.since))
    if not files:
        raise SystemExit(f"no sitting files under {DATA} (since={args.since})")

    # Pass 1: alias/portfolio/constituency harvest over every speaker string in scope.
    # Full-corpus rather than per-record, because a member's alias list is a property of
    # the corpus, not of the records this module happens to emit.
    for _fp, blob in files:
        for rep in blob.get("reports") or []:
            for t in rep.get("turns") or []:
                if t.get("speaker"):
                    members.observe(t["speaker"])
            for n in split_mp_names(rep.get("mp_names")):
                members.observe(n)

    picked, sel = select_records(files, since=args.since,
                                 outside_divisions=args.outside_divisions)
    if args.limit:
        picked = picked[:args.limit]

    docs = [document_from(rep, members, cov) for rep, cov in picked]
    member_rows = members.records()

    # The declaration ledger: one row per DISTINCT (doc_id, ayes, noes, abstentions), which
    # is exactly the dedup key the spec mandates. This is the audit trail for the spec's
    # "18 declarations, 12 distinct outcomes" claim.
    ledger = []
    for d in docs:
        for div in (d["metadata"].get("divisions") or []):
            ledger.append({"doc_id": d["doc_id"], "date": d["date"],
                           "section": d["section"], "raw": div["raw"],
                           "ayes": div["ayes"], "noes": div["noes"],
                           "abstentions": div["abstentions"]})

    # Raw MATCH count, before dedup, over the whole corpus and split by half. Reported so the
    # ledger can be reconciled: "18 raw matches, 1 of them in ingest/debates.py's half, 17
    # mine, 12 of those distinct -> 12 ledger rows" is a chain a reader can check, and a
    # ledger that silently showed 12 where the spec says 18 is how declarations get lost.
    raw_matches = collections.Counter()
    unparsed = []
    for _fp, blob in files:
        for rep in blob.get("reports") or []:
            half = ("legislation" if rep.get("group") in ("bill", "motion") else "debates")
            text = "\n".join((t.get("text") or "") for t in rep.get("turns") or [])
            for m in DECLARATION.finditer(text):
                raw_matches[half] += 1
                if parse_declaration(m) is None:
                    unparsed.append((rep.get("report_id"), m.group(0)[:80]))

    stats = {
        "roster_source": roster_src,
        "roster_current": len(members.current),
        "roster_former": len(members.former),
        "roster_duplicate_names": members.duplicate_names,
        "roster_normalized_collisions": len(members.collisions),
        "sittings_scanned": len(files),
        "documents": len(docs),
        "members": len(member_rows),
        "doc_types": dict(collections.Counter(d["doc_type"] for d in docs)),
        "sections": dict(collections.Counter(d["section"] for d in docs)),
        "division_raw_matches": dict(raw_matches),
        "divisions_total": len(ledger),
        "division_documents": sum(1 for d in docs if d["metadata"].get("division")),
        "division_outcomes_distinct": len({(r["doc_id"], r["ayes"], r["noes"], r["abstentions"])
                                           for r in ledger}),
        "division_unparsed": unparsed,
        "sponsors_resolved": sum(1 for d in docs
                                 if (d["metadata"].get("sponsor") or {}).get("member_id")),
        "sponsors_unresolved": sum(1 for d in docs
                                   if (d["metadata"].get("sponsor") or {}).get("raw")
                                   and not (d["metadata"].get("sponsor") or {}).get("member_id")),
        "documents_without_sponsor": sum(1 for d in docs
                                         if not (d["metadata"].get("sponsor") or {}).get("raw")),
        "members_with_aliases": sum(1 for m in member_rows if len(m["aliases"]) > 1),
        "selection": dict(sel),
    }
    return docs, member_rows, ledger, stats


def report(stats, docs, member_rows):
    print(f"roster         : {stats['roster_source']} "
          f"({stats['roster_current']} current + {stats['roster_former']} former; "
          f"{stats['roster_duplicate_names']} duplicate name entries dropped, "
          f"{stats['roster_normalized_collisions']} normalized collision)")
    print(f"sittings       : {stats['sittings_scanned']}")
    print(f"documents      : {stats['documents']}  {stats['doc_types']}")
    print(f"                 sections {stats['sections']}")
    print(f"members        : {stats['members']} "
          f"({stats['members_with_aliases']} carry >1 observed alias)")
    raw = stats["division_raw_matches"]
    print(f"divisions raw  : {sum(raw.values())} declaration matches "
          f"({raw.get('legislation', 0)} in this half, {raw.get('debates', 0)} in "
          f"ingest/debates.py's half), {len(stats['division_unparsed'])} unparsed")
    print(f"divisions kept : {stats['divisions_total']} distinct "
          f"(doc_id, ayes, noes, abstentions) rows in "
          f"{stats['division_documents']} documents "
          f"({stats['division_outcomes_distinct']} distinct outcomes)")
    for rid, raw_s in stats["division_unparsed"][:5]:
        print(f"                 UNPARSED {rid}: {raw_s!r}")
    print(f"sponsors       : {stats['sponsors_resolved']} linked to a member_id, "
          f"{stats['sponsors_unresolved']} unresolved (speaker_raw kept), "
          f"{stats['documents_without_sponsor']} records name none")
    print(f"selection      : {stats['selection']}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Ingest bills, motions, divisions and the member roster "
                    "into data/normalized/*.jsonl (spec docs/rag_spec.md §8).")
    ap.add_argument("--limit", type=int, default=0,
                    help="write at most N report rows (smoke runs). 0 = no cap.")
    ap.add_argument("--since", default=None,
                    help="ISO date; skip records before it (e.g. 2020-01-01).")
    ap.add_argument("--out", default=LEGISLATION_OUT, help="report rows output path")
    ap.add_argument("--members-out", default=MEMBERS_OUT, help="member rows output path")
    ap.add_argument("--refresh-roster", action="store_true",
                    help="re-fetch /fetchData instead of using data/raw/legislation/roster.json")
    ap.add_argument("--offline", action="store_true",
                    help="never touch the network; fail if the roster cache is missing")
    ap.add_argument("--outside-divisions", action="store_true",
                    help="ALSO emit records outside section bill/motion that carry a "
                         "division declaration. Off by default: there is exactly 1 such "
                         "record (budget-855) and it belongs to ingest/debates.py's "
                         "partition, which writes it. Turning this on produces a "
                         "cross-partition duplicate on purpose.")
    ap.add_argument("--report", action="store_true",
                    help="print counts and exit without writing")
    ap.add_argument("--report-out", default=None,
                    help="path for the run report JSON (default: <out>.report.json, which "
                         ".gitignore keeps tracked while the JSONL is ignored)")
    ap.add_argument("--check", action="store_true",
                    help="after writing, validate every row against docs/schema.json and "
                         "run the sponsor referential-integrity check; non-zero on failure")
    ap.add_argument("--schema-check-only", action="store_true",
                    help="validate the existing output files and exit (writes nothing)")
    args = ap.parse_args(argv)

    if args.schema_check_only:
        docs = load_jsonl(args.out)
        member_rows = load_jsonl(args.members_out)
        return 0 if _run_checks(docs, member_rows) else 1

    docs, member_rows, ledger, stats = build(args)
    report(stats, docs, member_rows)

    if args.report:
        return 0

    if not os.path.isdir(RAW_DIR):
        os.makedirs(RAW_DIR, exist_ok=True)
    storage.write_text_atomic(
        os.path.join(RAW_DIR, "divisions.jsonl"),
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger))
    write_jsonl(args.out, docs)
    write_jsonl(args.members_out, member_rows)
    print(f"\nwrote {len(docs)} report rows -> {os.path.relpath(args.out, ROOT)}")
    print(f"wrote {len(member_rows)} member rows -> {os.path.relpath(args.members_out, ROOT)}")
    print(f"raw cache      -> {os.path.relpath(RAW_DIR, ROOT)}/")

    # The run report is the durable artifact: the JSONL outputs are derived, ~94 MB and
    # git-ignored, so the counts are what a reviewer or the index builder actually reads.
    # Written next to the output under the name `.gitignore` keeps tracked.
    stats_path = args.report_out or (os.path.splitext(args.out)[0] + ".report.json")
    run_report = dict(stats)
    run_report["outputs"] = {
        "reports": {"path": os.path.relpath(args.out, ROOT), "rows": len(docs)},
        "members": {"path": os.path.relpath(args.members_out, ROOT), "rows": len(member_rows)},
        "raw_cache": os.path.relpath(RAW_DIR, ROOT),
    }
    run_report["args"] = {k: v for k, v in vars(args).items() if k != "report_out"}
    storage.write_json_atomic(stats_path, run_report)
    print(f"run report     -> {os.path.relpath(stats_path, ROOT)}")

    if args.check:
        print()
        return 0 if _run_checks(docs, member_rows) else 1
    return 0


def _run_checks(docs, member_rows):
    ok = True
    dfail = validate(docs, "document")
    mfail = validate(member_rows, "member")
    print(f"schema         : {len(docs)} docs, {len(member_rows)} members")
    if dfail:
        ok = False
        print(f"  FAIL {len(dfail)} document rows invalid:")
        for f in dfail[:10]:
            print("   -", f)
    if mfail:
        ok = False
        print(f"  FAIL {len(mfail)} member rows invalid:")
        for f in mfail[:10]:
            print("   -", f)
    if not (dfail or mfail):
        print("  PASS every row validates against docs/schema.json")

    problems = referential_integrity(docs, member_rows)
    if problems:
        ok = False
        print(f"referential integrity: FAIL {len(problems)}")
        for p in problems[:10]:
            print("   -", p)
    else:
        print("referential integrity: PASS (every non-null member_id resolves to the roster)")

    bad = [d["doc_id"] for d in docs
           if d["metadata"].get("division")
           and d["metadata"]["division"]["per_member"] is not None]
    if bad:
        ok = False
        print(f"per_member           : FAIL {len(bad)} division(s) carry per-member votes "
              f"(the schema types it null): {bad[:5]}")
    else:
        print("per_member           : PASS (null on every division, as the schema requires)")
    return ok


if __name__ == "__main__":
    sys.exit(main())
