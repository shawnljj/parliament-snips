"""
Parsnips — site generator.

Builds the whole static site from data/sitting_*.json:

    site/dist/index.html              the latest sitting (what most people want)
    site/dist/sittings/index.html     the archive, newest first
    site/dist/sittings/<date>.html    one page per sitting, permanent URL
    site/dist/theme.css               shared stylesheet

Design notes:
  * All infographic panels are COMPUTED from parsed data, never hard-coded.
  * The infographic is the hero and sits above the fold.
  * Every figure links back to the report it came from.
  * Coverage, attribution and the method are printed on the page. Trust is a
    feature; we publish our own error bars.
  * Output paths are relative so the site works from file:// in development and
    from a web root in production.

Product decisions baked in (agreed with the owner):
  * Audience: the general public who read the news.
  * Tone: bite-sized, neutral, factual. No side-taking, no framing.
  * Questions are MAPPED to responses, never scored or labelled unanswered.
"""
import datetime
import glob
import html
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
SUMMARIES = os.path.join(ROOT, "summaries")
sys.path.insert(0, os.path.join(ROOT, "scraper"))
import storage  # noqa: E402  (the project's single atomic-write implementation)

# ---------------------------------------------------------------- presentation
GROUP_LABEL = {
    "oral": "Oral answers", "written": "Written answers", "budget": "Committee of Supply",
    "motion": "Motions", "bill": "Bills", "statement": "Ministerial statements",
    "adjournment": "Adjournment", "correction": "Corrections", "tribute": "Tributes",
    "petition": "Petitions", "other": "Other business",
}
GROUP_ORDER = ["motion", "budget", "bill", "statement", "oral", "written",
               "adjournment", "correction", "tribute", "petition", "other"]

# Figures worth surfacing. Deliberately conservative: a currency, a unit, or a
# percentage. A bare 4-digit year (2026) or a small integer is noise, not a figure.
NUM = re.compile(
    r"(\$[\d.,]+\s?(?:billion|million|bil|mil|bn)\b"
    r"|S\$[\d.,]+\s?(?:billion|million|bil|mil|bn)\b"
    r"|\$[\d.,]{3,}"
    r"|\b\d[\d.,]*\s?(?:billion|million)\b"
    r"|\b\d[\d.,]*\s?(?:per cent|%)\b)", re.I)

TITLE = r"(?:Mr|Ms|Mrs|Mdm|Dr|Prof|Assoc\s+Prof|Associate\s+Prof|Encik|Haji|Puan|Datuk)"
TITLE_IN_PAREN = re.compile(rf"^({TITLE})\b", re.I)
PROCEDURAL_SPEAKER = re.compile(r"^\[.*\]$")
CHAIR = re.compile(r"^(Mr\s+)?Speaker\b|^(The\s+)?(Deputy\s+)?Speaker\b|^Chairman\b", re.I)


def read_text(path):
    """Read a text file, or "" if it is absent/unreadable.

    Deliberately forgiving: a missing optional asset (the pipeline dashboard, its
    state file) must not fail a site build that is otherwise fine.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def esc(s):
    return html.escape(s or "", quote=True)


# A constituency is the parenthetical a reader uses to place a Member. It is NOT a
# portfolio ("for the Minister for Health"), NOT a membership class ("Nominated Member"),
# and NOT the Member's own name, all of which also appear in parentheses in Hansard. The
# three are told apart by a small set of exclusions plus the fact that a constituency is a
# place-like phrase, so it never opens with a title or a preposition.
NOT_CONSTITUENCY = re.compile(
    r"^(for\s+the\b|for\s+Mr\b|for\s+Ms\b|for\s+Dr\b|In\b|To\b|on\b|as\b|and\b|"
    r"the\b|"
    + TITLE + r"\b)", re.I)

# Membership classes read as a constituency to a reader even though they name no place, so
# they are kept. Checked BEFORE the exclusions, which previously swallowed them.
MEMBERSHIP = ("Nominated Member", "Non-Constituency Member")


def constituency(raw):
    """The constituency or membership class at the END of a Hansard speaker string.

    Returns "" when the speaker has none, which is common: ministers are recorded by
    portfolio and 28% of sentences carry no parenthetical at all. Measured over 2026, 4,454
    of 11,173 speaker strings (40%) end in one.
    """
    groups = re.findall(r"\(([^()]*)\)", raw or "")
    if not groups:
        return ""
    last = groups[-1].strip()
    if not last:
        return ""
    if last.startswith("Nominated") or last.startswith("Non-Constituency"):
        return last          # a membership class, kept before the exclusions run
    if NOT_CONSTITUENCY.match(last):
        return ""
    return last if re.match(r"^[A-Z][A-Za-z'\- ]{2,40}$", last) else ""


def short_speaker(raw):
    """Reduce a Hansard speaker string to the person a reader scans for.

    The trap: the FIRST parenthetical is often a portfolio or an actor, not the
    name. e.g.
      'The Minister for Trade and Industry (Energy and Industry) (Dr Tan See Leng)'
    Taking the first paren yields 'Energy and Industry' -- wrong. Hansard also
    uses trailing parens for constituency:
      'Ms Hany Soh (Marsiling-Yew Tee)'
      'Mr Low Wu Yang Andre (Non-Constituency Member)'
    where there is no name in parens at all and the parens must be dropped.

    So: prefer the LAST parenthetical that opens with a personal title; if none
    does, strip parentheticals entirely and keep the leading name.
    """
    if not raw:
        return None
    groups = re.findall(r"\(([^()]*)\)", raw)
    titled = [g.strip() for g in groups if TITLE_IN_PAREN.match(g.strip())]
    if titled:
        return titled[-1]
    stripped = re.sub(r"\s*\([^()]*\)", "", raw).strip()
    return stripped or raw.strip()


def is_procedural(speaker):
    """Chair/administrative noise, excluded from people-facing stats."""
    if not speaker:
        return False
    s = speaker.strip().rstrip(":")
    return bool(PROCEDURAL_SPEAKER.match(s)) or bool(CHAIR.match(s))


MONEY_UNIT = {"billion": 1e9, "bil": 1e9, "b": 1e9,
              "million": 1e6, "mil": 1e6, "m": 1e6}


def magnitude(value):
    """Rough numeric magnitude of a matched figure, for ordering the panel."""
    s = value.replace("$", "").replace(",", "").replace(" ", "").strip().lower()
    m = re.match(r"^([\d.]+)([a-z%]*)$", s)
    if not m:
        return 0.0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0.0
    unit = m.group(2)
    if unit == "%":
        return n * 1e3
    return n * MONEY_UNIT.get(unit, 1.0)


def word_snap(text, idx, length, pad=95):
    """Expand a [idx, idx+length) span to whole words with context padding."""
    start = max(0, idx - pad)
    end = min(len(text), idx + length + pad)
    if start > 0:
        nxt = text.find(" ", start)
        start = nxt + 1 if 0 <= nxt < idx else start
    if end < len(text):
        prv = text.rfind(" ", idx + length, end)
        end = prv if prv > idx + length else end
    snippet = text[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def pretty_date(iso):
    """'2026-08-05' -> '5 August 2026' (Singapore reads dates day-first)."""
    try:
        dt = datetime.date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso or ""
    return f"{dt.day} {dt.strftime('%B %Y')}"


def hansard_url(r):
    """Deep link to one report in the official Hansard reader.

    VERIFIED 2026-09-16 against the live portal. The Angular app has a
    ``sprs3topic`` route that takes the report id we already store, e.g.

        https://sprs.parl.gov.sg/search/#/sprs3topic?reportid=motion-3008

    which renders that single report (title, sitting metadata, MPs, full text).
    Checked working for a motion, an oral answer, a matter on adjournment and a
    written answer.

    Do NOT send readers to ``#/home``: that is the search box, with the query
    blank, so it looks like the link is broken. Do NOT invent a REST permalink
    either -- ``/search/getHansardReport`` is dead (HTTP 500) and the older
    ``sprs2topic`` route wants an ``htmlFileName`` that is null in the API
    response for sprs3-era reports.
    """
    rid = (r.get("report_id") or r.get("id") or "").rstrip("#")
    if not rid:
        return "https://sprs.parl.gov.sg/search/#/home"
    return f"https://sprs.parl.gov.sg/search/#/sprs3topic?reportid={rid}"


# ---------------------------------------------------------------- computation
def compute_panels(sitting):
    reports = sitting["reports"]

    budget, counts = defaultdict(int), defaultdict(int)
    for r in reports:
        budget[r["group"]] += r["words"]
        counts[r["group"]] += 1

    speaker_words, speaker_report = defaultdict(int), {}
    for r in reports:
        for t in r["turns"]:
            if not t["speaker"] or is_procedural(t["speaker"]):
                continue
            name = short_speaker(t["speaker"])
            if not name:
                continue
            speaker_words[name] += t["words"]
            speaker_report.setdefault(name, r["report_id"])

    # Figures cited in the debate.
    #
    # Dedupe GLOBALLY by value: the same figure restated in a second record of a
    # split debate should not fill the panel twice.
    #
    # DELIBERATELY NOT CLASSIFYING "commitment" vs "statistic".
    # A regex for it was tried and produced false authority: it tagged "$39,000"
    # (a GST Assessable Income threshold being described) as money the Government
    # promised to spend. Telling a promise from a cited statistic is reading
    # comprehension, not pattern-matching. It belongs in the summarisation pass,
    # where a model reasons over the whole turn and is held to a citation.
    seen, numbers = set(), []
    for r in reports:
        for t in r["turns"]:
            if t["lang"] != "English" or t["words"] < 8:
                continue
            for m in NUM.finditer(t["text"]):
                # strip trailing punctuation the pattern greedily swallowed
                value = re.sub(r"\s+", " ", m.group(0).strip()).rstrip(",.;:")
                key = value.lower().replace(" ", "")
                if key in seen:
                    continue
                if not re.search(r"[$%]|million|billion|per cent", value, re.I):
                    if len(re.sub(r"[^\d]", "", value)) < 3:
                        continue
                seen.add(key)
                numbers.append({
                    "value": value,
                    "mag": magnitude(value),
                    "context": word_snap(t["text"], m.start(), len(value)),
                    "report_id": r["report_id"],
                    "speaker": short_speaker(t["speaker"]) or "Unattributed",
                    "title": r["title"],
                })
    numbers.sort(key=lambda n: -n["mag"])

    # Question -> response mapping for oral answers.
    #
    # PRODUCT DECISION: this is a MAPPING, not a scorecard. We do NOT compute an
    # answer/question word ratio and we do NOT label anything "unanswered". Both
    # are judgements, and this site does not take sides or frame. We show what
    # was asked and what was said back, in order, with participants named.
    #
    # Structure of an oral answer record, verified against 2026-08-05:
    #   * one or more "asked the Minister for X ..." turns  -> the question(s)
    #   * the Minister's main reply                        -> the response
    #   * then supplementary questioners and replies, interleaved with
    #     "Mr Speaker" turns that just call the next speaker by name.
    #
    # Vernacular turns are NOT in the English Hansard: they read
    # "[Please refer to Vernacular Speech.]" with the words in a separate
    # document. We flag these rather than inventing text.
    PORTFOLIO = re.compile(
        r"^(?:The\s+)?(?:Acting\s+|Senior\s+|Second\s+|Deputy\s+|Associate\s+)*"
        r"(?:Minister|Parliamentary\s+Secretary|Prime\s+Minister|Speaker)\b", re.I)
    DELEGATION = re.compile(r"\(for\s+the\s+", re.I)
    VERNACULAR = re.compile(r"refer to Vernacular Speech|Vernacular Speech", re.I)
    FORMAL_Q = re.compile(r"^\s*asked\s+the\s+", re.I)
    PERMISSION = re.compile(
        r"may I (?:please )?(?:have|seek) (?:your )?permission|"
        r"address oral Question|answer Question Nos?|may I please address", re.I)

    def is_role_turn(speaker):
        s = (speaker or "").strip()
        return bool(PORTFOLIO.match(s)) or bool(DELEGATION.search(s))

    def not_transcribed(text):
        return bool(VERNACULAR.search(text or ""))

    def trim(text, limit=340):
        text = (text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " …"

    # Substance limits for oral answers.
    #
    # These were 420/520 characters, which truncated most Minister responses
    # mid-answer -- a typical response runs 1,000-2,000 words, so the page was
    # showing only the opening sentence or two and hiding the actual content.
    # Raised so a reader gets the whole question and the substance of the reply,
    # with any overflow cut on a word boundary and marked with an ellipsis.
    Q_LIMIT, R_LIMIT, S_LIMIT = 1400, 4000, 1200

    qa = []
    for r in reports:
        if r["group"] != "oral":
            continue

        # resolve the respondent's name from role-marked turns
        role_names = set()
        for t in r["turns"]:
            if not is_procedural(t["speaker"]) and is_role_turn(t["speaker"]):
                nm = short_speaker(t["speaker"])
                if nm:
                    role_names.add(nm)
        if len(role_names) != 1:
            continue
        answerer = next(iter(role_names))

        # the formal question (first "asked the ..." turn, else the lead turn)
        question, asker, q_lang = None, None, "English"
        for t in r["turns"]:
            if is_procedural(t["speaker"]):
                continue
            if FORMAL_Q.match(t["text"] or ""):
                question = re.sub(r"^\s*asked\s+the\s+", "", t["text"].strip(), flags=re.I)
                asker = short_speaker(t["speaker"]) or "Unknown"
                q_lang = t["lang"]
                break
        if question is None:
            for t in r["turns"]:
                if t["speaker"] and not is_role_turn(t["speaker"]) \
                        and not is_procedural(t["speaker"]) and t["words"] > 25:
                    question, asker, q_lang = t["text"], short_speaker(t["speaker"]), t["lang"]
                    break
        if not question or not asker:
            continue

        # the response: first substantive turn by the respondent, skipping a bare
        # "may I address Question Nos 1 to 3" (that is a request, not the answer)
        response, r_lang, r_nt = None, "English", False
        for t in r["turns"]:
            if is_procedural(t["speaker"]):
                continue
            nm = short_speaker(t["speaker"])
            if nm != answerer and not is_role_turn(t["speaker"]):
                continue
            if t["words"] < 20 and PERMISSION.search(t["text"] or ""):
                continue
            if t["words"] < 8:
                continue
            response, r_lang, r_nt = t["text"], t["lang"], not_transcribed(t["text"])
            break
        if not response:
            continue

        # supplementary exchange, kept in order so nothing is out of context
        supp, started = [], False
        for t in r["turns"]:
            if is_procedural(t["speaker"]) or not t["speaker"]:
                continue
            if t["text"] is response or t["text"] is question:
                started = True
                continue
            if not started:
                continue
            nm = short_speaker(t["speaker"])
            if nm == "Mr Speaker":
                continue
            who, role = (answerer, "response") if (nm == answerer or is_role_turn(t["speaker"])) \
                else (nm, "question")
            if t["words"] < 8:
                continue
            supp.append({"who": who, "role": role, "text": trim(t["text"], S_LIMIT),
                         "not_transcribed": not_transcribed(t["text"])})

        qa.append({
            "title": r["title"], "report_id": r["report_id"],
            "asker": asker, "answerer": answerer,
            "question": trim(question, Q_LIMIT),
            "question_not_transcribed": not_transcribed(question),
            "question_lang": q_lang,
            "response": trim(response, R_LIMIT),
            "response_not_transcribed": r_nt,
            "response_lang": r_lang,
            "supplementary": supp,
            "supplementary_askers": sorted({s["who"] for s in supp if s["role"] == "question"}),
            "total_turns": len(r["turns"]),
            # Carried so the page can tell an answer the summariser deliberately
            # skipped (below its 150-word floor) from one still pending.
            "total_words": r["words"],
        })
    qa.sort(key=lambda x: -x["total_turns"])

    return {
        "budget": sorted(budget.items(), key=lambda kv: -kv[1]),
        "counts": counts,
        "speakers": sorted(speaker_words.items(), key=lambda kv: -kv[1])[:12],
        "numbers": numbers[:14],
        "qa": qa,
    }


def bar_rows(pairs, total, label_map=None):
    out = []
    for key, val in pairs:
        pct = (val / total * 100) if total else 0
        label = (label_map or {}).get(key, key)
        out.append(
            f'<div class="row"><span class="rk">{esc(label)}</span>'
            f'<span class="rb"><i style="width:{pct:.1f}%"></i></span>'
            f'<span class="rv">{val:,}</span></div>')
    return "\n".join(out)


MAPPING_NOTE = ("What was asked and what was said back, in order and in full where the "
                "record allows. Nothing is labelled answered or unanswered &mdash; "
                "we map the exchange and leave the judgement to you.")

# ---------------------------------------------------------------------------
# Client-side behaviour.
#
# Kept as a plain (non-f) string so JS braces don't need escaping, and written
# defensively: every feature degrades to "content is visible" if storage or
# IntersectionObserver is unavailable. Nothing on this page may depend on JS to
# be readable.
#
# Three jobs:
#   1. Scrollspy on the sticky section bar, so a long page stays navigable.
#   2. Per-card open/closed state persisted per sitting, so a repeat visitor
#      gets back what they had.
#   3. Scroll position persisted per sitting, with an opt-in "resume" rather
#      than a forced jump -- forcing a jump fights the browser's own
#      back/forward restore, which users rely on.
# ---------------------------------------------------------------------------
SCRIPT = r"""
(function () {
  var PAGE = document.body.getAttribute('data-page') || 'unknown';
  var K_CARDS = 'parsnips:cards:' + PAGE;
  var K_SCROLL = 'parsnips:scroll:' + PAGE;

  function store(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {}
  }
  function load(key) {
    try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; }
  }
  function reduced() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  /* ---------- sticky offset (header + section bar) ---------- */
  function stickyH() {
    var t = document.querySelector('.top');
    return t ? t.offsetHeight : 0;
  }

  /* THE READING LINE, defined once. It must match the CSS scroll-margin-top, or a jump lands
     a heading just below the line and the scrollspy still calls the previous section active --
     the reported "it takes me to the section before". The CSS is sticky-h + 12, so the spy
     uses the same 12 and a 2px tolerance for sub-pixel scroll positions. */
  var LINE_PAD = 12;                  // keep in step with .has-section-anchor's scroll-margin
  var LINE_TOLERANCE = 2;             // a heading exactly on the line counts as reached
  function readingLine() {
    return stickyH() + LINE_PAD;
  }
  function syncStickyVar() {
    document.documentElement.style.setProperty('--sticky-h', stickyH() + 'px');
  }

  /* ---------- 1. per-card open/closed state ---------- */
  var cards = [].slice.call(document.querySelectorAll('details[data-card]'));
  var savedCards = load(K_CARDS) || {};

  cards.forEach(function (d) {
    var id = d.getAttribute('data-card');
    // Restore BEFORE first paint where possible to avoid a flash of open
    // content collapsing. Cards default to open on the server, so only an
    // explicit saved "closed" needs applying.
    if (savedCards[id] === false) { d.open = false; }
    d.addEventListener('toggle', function () {
      var m = load(K_CARDS) || {};
      m[id] = d.open;
      store(K_CARDS, m);
    });
  });

  /* ---------- 2. scroll memory + resume ---------- */
  function writeScroll() {
    store(K_SCROLL, { y: Math.round(window.scrollY), t: Date.now() });
  }
  var saveTimer = null;
  function saveScroll() {
    if (saveTimer) return;
    saveTimer = setTimeout(function () { saveTimer = null; writeScroll(); }, 250);
  }
  window.addEventListener('scroll', saveScroll, { passive: true });
  // Belt and braces: the debounced scroll handler is the normal path, but
  // neither 'pagehide' nor 'visibilitychange' can be missed when the reader
  // actually leaves, and those are precisely the moments we care about. Without
  // these, a reader who scrolls and immediately closes the tab saves nothing.
  window.addEventListener('pagehide', writeScroll);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') { writeScroll(); }
  });

  function navType() {
    try {
      var e = performance.getEntriesByType('navigation')[0];
      return e ? e.type : 'navigate';
    } catch (err) { return 'navigate'; }
  }

  function offerResume() {
    var s = load(K_SCROLL);
    if (!s || !s.y || s.y < 1200) return;                 // not worth offering
    var max = document.documentElement.scrollHeight - window.innerHeight;
    if (s.y > max - 400) return;                          // was already at the end
    // Do not fight the browser's own restoration on back/forward.
    if (navType() === 'back_forward') return;

    var pct = Math.min(99, Math.round((s.y / Math.max(max, 1)) * 100));
    var el = document.createElement('div');
    el.className = 'resume';
    el.innerHTML = '<span>You were ' + pct + '% through this sitting</span>' +
                   '<button type="button" class="rgo">Resume</button>' +
                   '<button type="button" class="rno" aria-label="Dismiss">&times;</button>';
    document.body.appendChild(el);
    el.querySelector('.rgo').addEventListener('click', function () {
      window.scrollTo({ top: s.y, behavior: reduced() ? 'auto' : 'smooth' });
      el.remove();
    });
    el.querySelector('.rno').addEventListener('click', function () { el.remove(); });
    setTimeout(function () { if (el.parentNode) el.remove(); }, 12000);
  }

  /* ---------- desktop progress bar ----------
     Same measurement the resume toast quotes, computed the same way, so the two cannot
     disagree. Updated on scroll through rAF, like the rest of this page's scroll work. */
  var pbar = document.querySelector('.pbar');
  var pbarFill = pbar ? pbar.querySelector('.pbar-fill') : null;
  var pbarTxt = pbar ? pbar.querySelector('.pbar-txt') : null;
  var pbarRaf = null;

  function pbarSync() {
    if (!pbar) return;
    var max = document.documentElement.scrollHeight - window.innerHeight;
    var y = window.scrollY || window.pageYOffset || 0;
    var pct = max > 0 ? Math.min(100, Math.max(0, Math.round((y / max) * 100))) : 100;
    if (pbarFill) pbarFill.style.width = pct + '%';
    if (pbarTxt) pbarTxt.textContent = pct + '%';
    pbar.setAttribute('aria-valuenow', pct);
  }

  function pbarOnScroll() {
    if (pbarRaf) return;
    pbarRaf = requestAnimationFrame(function () { pbarRaf = null; pbarSync(); });
  }
  // The page grows as the faded context fills in, so recompute after those land rather than
  // trusting the height measured at load -- otherwise the percentage is wrong early on.
  window.addEventListener('resize', pbarSync, { passive: true });

  /* ---------- 3. section rail (mobile only) ----------
     Ported from the owner's sgfamily.life rail (src/section-rail.ts), because
     the pattern is proven and he asked for that, not a fresh invention.

     Differences from the original, and why:
       * Ticks come from h2/h3 headings on the sitting page, not from tab panels
         (there are no tabs here), so the MutationObserver on [data-area-panel]
         is dropped as irrelevant.
       * STICKY_OFFSET is derived from this page's real sticky header height
         rather than hard-coded, because Parsnips' header is 61px, not 88/96.
       * The palette is the site's own green; the original's warm accent is not
         imported.
       * A "dormant" state replaces the original's view-toggle rule: the rail
         hides while the lede is on screen and there is nothing below to map. */
  // The rail is built at EVERY width now: on desktop it is the breakpoint navigator, on
  // mobile the same thing sized for a thumb. One system, so a fix to one is a fix to both.
  var RAIL_MQ = '(max-width: 760px)';
  // Numbering for the level-3 ticks, so a dot has a stable identity that can be
  // matched against the numbered jump list in the Oral answers section.
  var railOrdinal = 0;
  var rail = null, railFill = null, railPill = null, railEntries = [], railActive = -1;
  var railPillTimer = null, railResizeTimer = null, pendingIndex = -1;

  // Retained for the pill logic below, which still asks whether the rail is in its compact
  // form. The rail itself is no longer gated on width.
  function railIsMobile() {
    return window.matchMedia && window.matchMedia('(max-width: 760px)').matches;
  }
  function truncate(text, max) {
    max = max || 42;
    return text.length <= max ? text : text.slice(0, max).replace(/\s+$/, '') + '…';
  }

  function railCollect() {
    var seen = {};
    var out = [];
    railOrdinal = 0;
    // FIRST LEVEL ONLY -- the top-level sections. Questions inside the Oral answers section
    // were tried as a second level and added 16 more ticks for little value: the reader
    // arrives at "Oral answers" and reads down, rather than picking question 11 of 16. The
    // rail is a coarse map; the progress bar carries fine position.
    [].slice.call(document.querySelectorAll('main h2')).forEach(function (h, i) {
      var label = (h.textContent || '').replace(/\s+/g, ' ').trim();
      if (!label) return;
      // The section h2s already carry ids we control; heading-level content
      // (oral-answer h3s, group headings) gets a generated one.
      if (!h.id) {
        h.id = 'sec-' + i + '-' + label.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
      }
      if (seen[h.id]) return;
      seen[h.id] = 1;
      h.classList.add('has-section-anchor');
      // One level, so one size: every tick is a top-level section.
      var level = 2;
      // Ordinal numbers the level-3 ticks in reading order, matching the
      // numbered list the reader can open in the Oral answers section.
      if (level === 3) railOrdinal += 1;
      out.push({ el: h, id: h.id, label: label, level: level,
                 ordinal: level === 3 ? railOrdinal : 0 });
    });
    return out;
  }

  function railEnsure() {
    if (rail && document.body.contains(rail)) return;
    rail = document.createElement('nav');
    rail.className = 'section-rail';
    rail.setAttribute('aria-label', 'Sections on this page');

    railFill = document.createElement('span');
    railFill.className = 'section-rail-fill';
    railFill.setAttribute('aria-hidden', 'true');
    rail.appendChild(railFill);

    var track = document.createElement('div');
    track.className = 'section-rail-track';
    rail.appendChild(track);

    railPill = document.createElement('div');
    railPill.className = 'section-rail-pill';
    railPill.setAttribute('aria-live', 'polite');
    rail.appendChild(railPill);

    document.body.appendChild(rail);
  }

  function railBuild() {
    if (!rail) return;
    var track = rail.querySelector('.section-rail-track');
    if (!track) return;
    track.replaceChildren();
    railEntries.forEach(function (entry, index) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'section-rail-tick level-' + entry.level;
      // The label is announced to AT and used as the tooltip; the visible name
      // comes from the always-on label below for section-level ticks.
      b.setAttribute('aria-label', 'Go to ' + entry.label);
      b.title = entry.label;

      var mark = document.createElement('span');
      mark.className = 'section-rail-tick-mark';
      mark.setAttribute('aria-hidden', 'true');
      b.appendChild(mark);

      // EVERY tick is labelled. The name used to be created only for level-3 ticks, so when
      // the rail was reduced to first-level sections every tick became a bare dot with no
      // visible identity -- a reader could not tell what any node represented.
      //
      // The earlier objection was real and is answered by width, not by dropping the label:
      // always-on names made the rail 107px wide (27% of a 390px screen). So on mobile the
      // label is shown for the ACTIVE tick only, and on desktop -- where there is room at the
      // margin -- every tick keeps its name.
      var name = document.createElement('span');
      name.className = 'section-rail-name';
      name.textContent = truncate(entry.label, 34);
      b.appendChild(name);

      var bubble = document.createElement('span');
      bubble.className = 'section-rail-bubble';
      // NOT aria-hidden any more: once the preview becomes the primary way to
      // identify a dot, hiding it from AT would hide information rather than
      // decoration. Its text is also in the button's aria-label.
      bubble.textContent = truncate(entry.label);
      b.appendChild(bubble);

      // A confirm affordance inside the preview, so the intended gesture is
      // explicit rather than something the reader has to discover.
      var go = document.createElement('span');
      go.className = 'section-rail-go';
      go.textContent = 'Go';
      b.appendChild(go);

      b.addEventListener('click', function (ev) {
        // ONE TAP JUMPS. The two-step preview existed because a dot gave no clue what it
        // led to, so a tap had to be able to answer that without moving the reader. Now the
        // name is visible on the tick itself, so the preview answers a question nobody is
        // asking -- and in practice the first tap looked like a broken jump, which is
        // exactly how the owner reported it.
        ev.preventDefault();
        railClearPending();
        railGo(index);
      });

      entry.button = b;
      track.appendChild(b);
    });
  }

  function railGo(index) {
    var e = railEntries[index];
    if (!e) return;
    e.el.scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth', block: 'start' });
    railSetActive(index, true);
  }

  /* Preview: name the target without moving the reader. Tapping a dot must never
     navigate by surprise -- that was the reported failure, where the only way to
     learn what a dot was, was to be taken to it. */
  function railPreview(index) {
    var e = railEntries[index];
    if (!e) return;
    railClearPending();
    pendingIndex = index;
    if (e.button) {
      e.button.classList.add('is-pending');
      railPlacePreview(e.button);
    }
  }

  /* The bubble and Go chip are position:fixed so they escape the rail's narrow
     column and paint above page content; that means JS has to say where.

     THE CONTAINING BLOCK IS THE RAIL, NOT THE VIEWPORT -- and that is a trap worth naming. The
     rail carries transform:translateY(-50%), and a transformed element becomes the containing
     block for its position:fixed descendants. So:
       * `top` is measured from the RAIL's box top, not the viewport's -- the JS below converts;
       * a `right:` here would be an offset inside the rail, not from the viewport edge, so the
         CSS anchors the pair with right:100% and they grow LEFTWARDS into the gutter.
     Clamping still happens in viewport terms first, so the bubble cannot leave the screen. */
  function railPlacePreview(btn) {
    var r = btn.getBoundingClientRect();
    var railBox = rail ? rail.getBoundingClientRect() : { top: 0, left: 0 };
    var bubble = btn.querySelector('.section-rail-bubble');
    var go = btn.querySelector('.section-rail-go');
    // The rail's own offset to the viewport, i.e. the coordinate change for `top`.
    var railTop = railBox.top;
    var cy = r.top + r.height / 2;
    // Keep the pair inside the viewport: the bubble is ~31px tall, the Go chip 22px + 4 gap.
    var minTop = 10 + railTop;
    var maxTop = window.innerHeight - 10 - 31 - 22 - 4;
    var bubbleTop = Math.min(Math.max(cy, minTop), Math.max(minTop, maxTop));
    if (bubble) {
      bubble.style.top = (bubbleTop - railTop) + 'px';
      go.style.top = (bubbleTop - railTop) + 30 + 'px';
    }
  }

  function railClearPending() {
    if (pendingIndex >= 0) {
      var prev = railEntries[pendingIndex];
      if (prev && prev.button) prev.button.classList.remove('is-pending');
    }
    pendingIndex = -1;
  }

  /* ---------- drag-along-the-rail scrubbing ----------
     The primary touch gesture. A drag that starts on the track moves a highlight
     along the rail and shows each section's name live as the finger travels, so
     the reader can read the dots before committing; releasing jumps to whatever
     is under the finger.

     A drag rather than a hold, deliberately: the finger is expected to move, so
     the browser claiming vertical movement as a scroll is no longer a conflict.
     `touch-action:none` on the track plus pointer capture is what makes the rail
     own the gesture. */
  var scrubIndex = -1, scrubActive = false;

  function railIndexAtY(clientY) {
    // Nearest tick centre to the finger, so scrubbing feels continuous rather
    // than requiring the finger to land inside a 27px box.
    var best = -1, bestDist = Infinity;
    railEntries.forEach(function (e, i) {
      if (!e.button) return;
      var r = e.button.getBoundingClientRect();
      var d = Math.abs((r.top + r.height / 2) - clientY);
      if (d < bestDist) { bestDist = d; best = i; }
    });
    return best;
  }

  function railScrubTo(index) {
    if (index < 0 || index === scrubIndex) return;
    scrubIndex = index;
    railPreview(index);            // reuses the pending highlight + bubble
  }

  function railScrubStart(ev) {
    if (!rail) return;
    scrubActive = true;
    rail.classList.add('is-scrubbing');
    // Capture so the drag keeps reporting even when the finger leaves the track.
    try { rail.querySelector('.section-rail-track').setPointerCapture(ev.pointerId); } catch (e) {}
    railScrubTo(railIndexAtY(ev.clientY));
  }

  function railScrubMove(ev) {
    if (!scrubActive) return;
    ev.preventDefault();
    railScrubTo(railIndexAtY(ev.clientY));
  }

  function railScrubEnd(ev) {
    if (!scrubActive) return;
    scrubActive = false;
    if (rail) rail.classList.remove('is-scrubbing');
    try { rail.querySelector('.section-rail-track').releasePointerCapture(ev.pointerId); } catch (e) {}
    var target = scrubIndex;
    scrubIndex = -1;
    if (target >= 0) {
      railClearPending();
      railGo(target);              // release commits the jump
    }
  }

  function railSetActive(index, force) {
    if (!force && index === railActive) return;
    railActive = index;
    railEntries.forEach(function (e, i) {
      var on = i === index;
      if (e.button) {
        e.button.classList.toggle('is-active', on);
        if (on) e.button.setAttribute('aria-current', 'true');
        else e.button.removeAttribute('aria-current');
      }
    });
    if (railPill) {
      var label = (railEntries[index] || {}).label || '';
      railPill.textContent = label;
      if (label) {
        // Show while the reader is moving between sections, then fade. Gating on
        // activity rather than a timeout alone is what keeps it off content: on a
        // phone every pixel scrolls, so a resting position always covers
        // something. 700ms is long enough to read a two-word label mid-scroll.
        railPill.classList.add('is-visible');
        if (railPillTimer !== undefined) window.clearTimeout(railPillTimer);
        // Capture the ELEMENT, not the global: railRebuild() nulls the global on desktop, so
        // reading it 700ms later threw and the repeat errors broke the rest of the page.
        var pill = railPill;
        railPillTimer = window.setTimeout(function () {
          if (pill && pill.classList) pill.classList.remove('is-visible');
        }, 700);
      } else {
        railPill.classList.remove('is-visible');
      }
    }
    if (railFill && railEntries.length > 1 && index >= 0) {
      railFill.style.setProperty('--rail-progress', (index / (railEntries.length - 1)).toFixed(4));
    }
  }

  /* The section whose heading most recently crossed under the sticky header.
     Offset is 8px more than the scroll-margin so a just-jumped section reads
     active immediately instead of lagging one behind. */
  function railActiveIndex() {
    if (!railEntries.length) return -1;
    // ADD the tolerance: a jump lands the heading ON the line, and sub-pixel rounding can put
    // it a pixel below. Subtracting made the test harder and produced the off-by-one.
    var off = readingLine() + LINE_TOLERANCE;
    var cur = 0;
    railEntries.forEach(function (e, i) {
      // <= 0 means the heading has reached or passed the reading line.
      if (e.el.getBoundingClientRect().top - off <= 0) cur = i;
    });
    var nearBottom = window.innerHeight + window.scrollY >=
                     document.documentElement.scrollHeight - 4;
    return nearBottom ? railEntries.length - 1 : cur;
  }

  /* The rail maps what is BELOW the fold. While the lede fills the screen there
     is nothing to map, and a centred rail would sit on top of it. */
  function railVisibility() {
    if (!rail) return;
    var lede = document.querySelector('.lede');
    var dormant = false;
    if (lede) {
      var r = lede.getBoundingClientRect();
      dormant = r.bottom > window.innerHeight * 0.5;
    }
    rail.classList.toggle('is-dormant', dormant);
  }

  function railSync() {
    var next = railActiveIndex();
    if (next !== railActive) railSetActive(next);
    railVisibility();
    var tt = document.querySelector('.totop');
    if (tt) tt.classList.toggle('on', window.scrollY > 900);
  }

  /* Size ticks to fit the viewport. The 5 Aug page has 21 headings; at a full
     44px touch target that is 924px of ticks, taller than a phone screen, so
     the rail would overflow. Compute the largest height that fits (gap included)
     and cap at 44px, the touch minimum. The visible dot is unchanged, so the
     rail still looks like the original. */
  function railSizeTicks() {
    if (!rail) return;
    var n = railEntries.length;
    if (!n) return;
    var available = window.innerHeight - 140;              // leave breathing room
    // Gap tightens as ticks multiply, so a long page keeps a useful target size
    // rather than collapsing every tick to a sliver.
    var gap = n > 16 ? 6 : (n > 10 ? 10 : 14);
    var h = Math.floor(available / n) - gap;
    h = Math.max(12, Math.min(44, h));
    rail.style.setProperty('--rail-tick-h', h + 'px');
    rail.style.setProperty('--rail-gap', gap + 'px');
    rail.setAttribute('data-ticks', String(n));
    var track = rail.querySelector('.section-rail-track');
    if (track) {
      track.style.maxHeight = Math.max(140, window.innerHeight - 120) + 'px';
      track.style.overflowY = (n * (h + gap)) > available ? 'auto' : 'visible';
      track.style.scrollbarWidth = 'none';
    }
  }

  function railRebuild() {
    // No width gate: the rail is built at every width now. It used to be torn down above
    // 760px, which is why desktop had no breakpoints at all.
    railEnsure();
    railEntries = railCollect();
    railBuild();
    railSizeTicks();
    railWireScrub();
    railActive = -1;
    railSync();
  }

  window.addEventListener('scroll', function () {
    pbarOnScroll();
    // Scrolling dismisses a pending preview: the reader has moved on, and a
    // stale "tap again to go" would be misleading.
    if (pendingIndex >= 0) railClearPending();
    railSync();
  }, { passive: true });
  window.addEventListener('resize', function () {
    railSizeTicks();
    pbarSync();
    if (railResizeTimer !== undefined) window.clearTimeout(railResizeTimer);
    railResizeTimer = window.setTimeout(railRebuild, 150);
  });
  if (false) {
    // The media-query listener was needed only while the rail was mobile-only. Kept as a
    // no-op rather than deleted so the surrounding block's braces stay balanced.
    var railMq = null;
  }

  /* ---------- jump list: closed on mobile, open on desktop ---------- */

  /* ---------- back to top ---------- */
  var tt = document.querySelector('.totop');
  if (tt) {
    tt.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: reduced() ? 'auto' : 'smooth' });
    });
  }

  /* ---------- keep jump links clear of the sticky bar ---------- */
  document.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('a[href^="#"]');
    if (!a) return;
    var id = a.getAttribute('href').slice(1);
    if (!id) return;
    var t = document.getElementById(id);
    if (!t) return;
    ev.preventDefault();
    // Open the card being targeted, otherwise the jump lands on a closed header.
    var card = t.closest('details[data-card]') || (t.tagName === 'DETAILS' ? t : null);
    if (card && !card.open) { card.open = true; }
    var y = t.getBoundingClientRect().top + window.scrollY - stickyH() - 12;
    window.scrollTo({ top: y, behavior: reduced() ? 'auto' : 'smooth' });
    history.replaceState(null, '', '#' + id);
  }, false);

  /* ---------- jump list: closed on mobile, open on desktop ---------- */
  var qj = document.querySelector('details.qajump');
  if (qj && window.matchMedia) {
    var wide = window.matchMedia('(min-width: 761px)');
    if (wide.matches) { qj.open = true; }
    var onWide = function (e) { qj.open = e.matches; };
    if (wide.addEventListener) { wide.addEventListener('change', onWide); }
    else if (wide.addListener) { wide.addListener(onWide); }
  }

  /* Scrub wiring lives on the TRACK, so a drag anywhere in the rail's column is
     captured (including the gaps between dots). A tap still works: a pointerdown
     with no meaningful movement falls through to the tick's own click handler. */
  function railWireScrub() {
    if (!rail) return;
    var track = rail.querySelector('.section-rail-track');
    if (!track || track.__scrubWired) return;
    track.__scrubWired = true;

    var downY = null;
    track.addEventListener('pointerdown', function (ev) {
      downY = ev.clientY;
      // Don't preventDefault here: a plain tap must still reach the tick's click.
    });

    track.addEventListener('pointermove', function (ev) {
      if (downY === null) return;
      if (!scrubActive) {
        // 6px of vertical travel means this is a drag, not a tap. Below that we
        // stay out of the way so tap-to-preview keeps working.
        if (Math.abs(ev.clientY - downY) < 6) return;
        railScrubStart(ev);
      }
      railScrubMove(ev);
    });

    ['pointerup', 'pointercancel'].forEach(function (evName) {
      track.addEventListener(evName, function (ev) {
        if (scrubActive) railScrubEnd(ev);
        downY = null;
      });
    });
  }

  document.addEventListener('click', function (ev) {
    // A tap outside the rail dismisses the preview.
    if (pendingIndex >= 0 && rail && !rail.contains(ev.target)) railClearPending();
  }, true);

  syncStickyVar();
  railRebuild();

  // ---------------------------------------------------------------- skipped sentences
  // Every sentence in the record is either published in the brief or sits inside an
  // unpublished run that the page ACCOUNTS for -- leading, trailing, or internal. Small
  // runs render inline; larger ones are collapsed behind a button. The text itself is
  // never in the page: measured, the unpublished material is 3.74x the published text, so
  // embedding it would take a sitting from 465 KB to ~3 MB. It is fetched once per brief
  // and cached, so every run in that brief fills from one request.
  var SKIP_CACHE = {};

  function skEsc(t) {
    return String(t == null ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function skNum(sid) {
    var m = /^s0*(\d+)$/.exec(sid || '');
    return m ? +m[1] : -1;
  }
  function rowHtml(s) {
    var who = (s.speaker || '').trim();
    var meta = '<span class="sid">' + skEsc(s.sid) + '</span>'
      + (who ? '<span class="who">' + skEsc(who) + '</span>'
             : '<span class="who none"></span>');
    return '<div class="sk"><div class="sk-meta">' + meta + '</div>'
         + '<p class="sk-text">' + skEsc(s.text) + '</p></div>';
  }
  function loadSkipped(brief) {
    // Keyed by brief, so a host can only ever be filled from its own brief's payload.
    // Necessary because sentence ids are numbered WITHIN an item: "s00003" is a different
    // sentence in every brief on a sitting (on 2026-05-07, 92 of 320 numbers meant
    // different text). Matching ids without the brief is what put a Johor summary above
    // unrelated text and rendered sentences twice.
    if (!brief) return Promise.reject(new Error('no brief'));
    if (SKIP_CACHE[brief] && !SKIP_CACHE[brief].then) {
      return Promise.resolve(SKIP_CACHE[brief]);
    }
    if (SKIP_CACHE[brief]) return SKIP_CACHE[brief];
    var url = '../skipped/' + encodeURIComponent(brief) + '.json';
    SKIP_CACHE[brief] = fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (list) {
      SKIP_CACHE[brief] = list;
      return list;
    });
    return SKIP_CACHE[brief];
  }
  function fill(host, list, from, to) {
    var picked = list.filter(function (s) {
      var v = skNum(s.sid);
      return v >= from && v <= to;
    });
    host.innerHTML = picked.length ? picked.map(rowHtml).join('')
      : '<p class="gapmsg">No sentences to show here.</p>';
  }
  // The desktop form of a run: the real sentences, faded, in place. Same payload, a quieter
  // rendering, so a reader on a wide screen sees the whole record with the selection marked.
  function ctxHtml(s) {
    var who = (s.speaker || '').trim();
    return '<div class="ctx">'
      + '<span class="csid">' + skEsc(s.sid) + '</span>'
      + '<span class="cwho">' + (who ? skEsc(who) : '') + '</span>'
      + '<p class="ctx-text">' + skEsc(s.text) + '</p></div>';
  }
  function fillRun(host, list, from, to) {
    var picked = list.filter(function (s) {
      var v = skNum(s.sid);
      return v >= from && v <= to;
    });
    host.innerHTML = picked.map(ctxHtml).join('');
  }

  // Fill every inline run for a brief on first sight, so the page always ACCOUNTS for the
  // whole record without the reader having to ask.
  // WHICH VIEW ARE WE IN? The two presentations are separate UIs and each fetches only what
  // it shows. On desktop the faded full transcripts are filled up front, because seeing the
  // whole record with the selection marked out is the point of that view. On mobile nothing
  // is fetched until the reader taps: the payload is 5x the selected text and a phone should
  // not pay for text it has not asked to see.
  function isWide() {
    return window.matchMedia && window.matchMedia('(min-width: 760px)').matches;
  }

  function fillInline(brief) {
    var secs = document.querySelectorAll('.dsec[data-brief="' + brief + '"]');
    if (!secs.length) return;
    if (!isWide()) {
      // mobile: only the small runs that are visible by default
      loadSkipped(brief).then(function (list) {
        secs.forEach(function (sec) {
          sec.querySelectorAll('.ski').forEach(function (h) {
            if (h.dataset.done || !h.dataset.from) return;
            h.dataset.done = '1';
            fill(h, list, +h.dataset.from, +h.dataset.to);
          });
        });
      }).catch(function () {});
      return;
    }
    var hosts = [];
    secs.forEach(function (sec) {
      sec.querySelectorAll('.runfull').forEach(function (h) {
        if (!h.dataset.done) hosts.push(h);
      });
    });
    if (!hosts.length) return;
    loadSkipped(brief).then(function (list) {
      hosts.forEach(function (h) {
        if (h.dataset.done) return;
        h.dataset.done = '1';
        fillRun(h, list, +h.dataset.from, +h.dataset.to);
      });
    }).catch(function () {});
  }

  function initBrief(brief) {
    var secs = document.querySelectorAll('.dsec[data-brief="' + brief + '"]');
    if (!secs.length) return;
    secs.forEach(function (sec) {
      var gap = sec.querySelector('.gapd');
      if (gap && !gap.dataset.wired) {
        gap.dataset.wired = '1';
      }
    });
    fillInline(brief);
  }

  // The desktop collapse switch: hides the faded context for one brief, leaving the selected
  // sentences and the collapsed markers. Per brief, because a Budget sitting carries several
  // and a reader may want the record on one and not another.
  document.addEventListener('change', function (e) {
    var cb = e.target;
    if (!cb || !cb.classList || !cb.classList.contains('osw-in')) return;
    var brief = cb.getAttribute('data-brief');
    if (!brief) return;
    document.querySelectorAll('.dsec[data-brief="' + brief + '"]').forEach(function (sec) {
      sec.classList.toggle('hide-ctx', cb.checked);
    });
  });

  document.addEventListener('click', function (e) {
    var t = e.target;
    var btn = (t && t.closest) ? t.closest('.gapd') : null;
    if (!btn) return;
    var body = btn.nextElementSibling;
    if (!body) return;
    var x = btn.querySelector('.gapx');
    if (btn.getAttribute('aria-expanded') === 'true') {
      btn.setAttribute('aria-expanded', 'false');
      body.hidden = true;
      body.innerHTML = '';
      if (x) x.textContent = 'show';
      return;
    }
    var sec = btn.closest ? btn.closest('.dsec') : null;
    var brief = sec ? sec.getAttribute('data-brief') : null;
    if (!brief) return;
    btn.setAttribute('aria-expanded', 'true');
    if (x) x.textContent = 'hide';
    body.hidden = false;
    body.innerHTML = '<p class="gapmsg">Loading&hellip;</p>';
    loadSkipped(brief).then(function (list) {
      fill(body, list, +btn.getAttribute('data-from'), +btn.getAttribute('data-to'));
    }).catch(function () {
      body.innerHTML = '<p class="gapmsg">Could not load these sentences. '
        + 'The full record is at sprs.parl.gov.sg.</p>';
    });
  });

  window.addEventListener('load', function () {
    syncStickyVar(); railRebuild(); offerResume();
    // Fill every brief's inline runs, so the page accounts for the whole record without
    // the reader having to ask. Deferred to load so the fetch never delays first paint.
    var seen = {};
    document.querySelectorAll('.dsec[data-brief]').forEach(function (sec) {
      var b = sec.getAttribute('data-brief');
      if (b && !seen[b]) { seen[b] = 1; initBrief(b); }
    });
  });
})();
"""

QA_PREVIEW = 4          # unused placeholder (oral answers render in full)
SPEAKER_PREVIEW = 4     # speaker groups shown per brief before the expand control


def render_mapping(q, brief=None):
    """One oral answer: the summarised exchange, with the transcript as evidence.

    Structure, and why:
      * The SUMMARY is the message and sits on top -- what was asked, what was
        said back, the concrete points. This is what makes 16 answers scannable.
      * The VERBATIM transcript is the evidence and is collapsed behind a
        disclosure. Before it rendered open at up to 4,000 characters per answer,
        which is why the section was 23,654px of verbatim text with no summary at
        all. Both belong on the page; the transcript just should not lead.
      * If no brief exists yet for this answer, fall back to the old verbatim
        layout so the page is never worse than it was.
    """
    def lang_tag(lang):
        return "" if (lang or "English") == "English" else \
            f'<span class="lt">{esc(lang)}</span>'

    def nt_flag(flag):
        return ('<span class="nt" title="The words are in a separate vernacular '
                'document, not the English Hansard">not transcribed here</span>'
                if flag else "")

    supp = q.get("supplementary") or []
    supp_html = ""
    if supp:
        rows = []
        for s in supp:
            cls = "sresp" if s["role"] == "response" else "sq"
            rows.append(f'<div class="supp {cls}">'
                        f'<span class="sw">{esc(s["who"])}</span>'
                        f'<span class="st">{esc(s["text"])}{nt_flag(s["not_transcribed"])}</span>'
                        f'</div>')
        extra = q.get("supplementary_askers") or []
        who_txt = ("Supplementary questions from " + ", ".join(esc(x) for x in extra)
                   if extra else "Further exchange")
        supp_html = (f'<details class="supp-wrap"><summary>{who_txt} '
                     f'({len(supp)} further turns)</summary>{"".join(rows)}</details>')

    transcript_html = f"""
        <details class="transcript">
          <summary>Read the transcript ({len(supp) + 2} turns)</summary>
          <div class="qa-pair">
            <div class="qa-side ask">
              <span class="qa-role">Asked</span>
              <span class="qa-who">{esc(q['asker'])}{lang_tag(q['question_lang'])}</span>
              <p>{esc(q['question'])}{nt_flag(q['question_not_transcribed'])}</p>
            </div>
            <div class="qa-link" aria-hidden="true"></div>
            <div class="qa-side resp">
              <span class="qa-role">Response</span>
              <span class="qa-who">{esc(q['answerer'])}{lang_tag(q['response_lang'])}</span>
              <p>{esc(q['response'])}{nt_flag(q['response_not_transcribed'])}</p>
            </div>
          </div>
          {supp_html}
          <a class="srclink" href="{esc(hansard_url(q))}" target="_blank" rel="noopener">Full exchange in Hansard &rarr;</a>
        </details>"""

    head = f"""
        <summary class="mapsum">
          <h3 class="mapt">{esc(q['title'])}</h3>
          <span class="maphint">{esc(q['asker'])} &rarr; {esc(q['answerer'])}</span>
        </summary>"""

    if not brief:
        # No summary yet: keep the original verbatim-first layout.
        return f"""
      <li class="mapwrap" id="{esc(q['report_id'])}">
        <details class="map" data-card="{esc(q['report_id'])}" open>
        {head}
        <div class="qabody">
        <div class="qa-pair">
          <div class="qa-side ask">
            <span class="qa-role">Asked</span>
            <span class="qa-who">{esc(q['asker'])}{lang_tag(q['question_lang'])}</span>
            <p>{esc(q['question'])}{nt_flag(q['question_not_transcribed'])}</p>
          </div>
          <div class="qa-link" aria-hidden="true"></div>
          <div class="qa-side resp">
            <span class="qa-role">Response</span>
            <span class="qa-who">{esc(q['answerer'])}{lang_tag(q['response_lang'])}</span>
            <p>{esc(q['response'])}{nt_flag(q['response_not_transcribed'])}</p>
          </div>
        </div>
        {supp_html}
        <a class="srclink" href="{esc(hansard_url(q))}" target="_blank" rel="noopener">Full exchange in Hansard &rarr;</a>
        </div>
        </details>
      </li>"""

    asked = brief.get("asked") or ""
    resp = brief.get("response") or ""
    kp = brief.get("key_points") or []
    kp_html = ""
    if kp:
        rows = "".join(
            f'<li><span class="pt">{esc(p.get("point", ""))}</span>'
            f'<span class="pw">{esc(p.get("speaker") or "")}</span></li>' for p in kp)
        kp_html = f'<ul class="oralkp">{rows}</ul>'
    lo = brief.get("left_open") or []
    lo_html = ""
    if lo:
        rows = "".join(f"<li>{esc(x)}</li>" for x in lo)
        lo_html = (f'<details class="ns"><summary>Left open in this exchange '
                   f'({len(lo)})</summary>'
                   f'<p class="nnote">Raised by the question and not addressed in the '
                   f'response. Recorded as open points, not as findings.</p>'
                   f'<ul>{rows}</ul></details>')
    supp_note = (brief.get("supplementary") or "").strip()
    supp_note_html = ""
    if supp_note and supp_note.lower() not in ("not stated", "none", ""):
        supp_note_html = (f'<p class="oralsupp"><b>Supplementary questions</b> '
                          f'{esc(supp_note)}</p>')

    return f"""
      <li class="mapwrap" id="{esc(q['report_id'])}">
        <details class="map" data-card="{esc(q['report_id'])}" open>
        {head}
        <div class="qabody">
          <div class="oral-sum">
            <div class="orow">
              <span class="olabel">Asked</span>
              <p>{esc(asked)}</p>
            </div>
            <div class="orow resp">
              <span class="olabel">Said back</span>
              <p>{esc(resp)}</p>
            </div>
          </div>
          {kp_html}
          {supp_note_html}
          {lo_html}
          {transcript_html}
        </div>
        </details>
      </li>"""


def nav(home, archive, current=""):
    def cls(name):
        return ' class="on"' if name == current else ""
    return (f'<nav><a href="{esc(home)}">Latest</a>'
            f'<a href="{esc(archive)}"{cls("archive")}>Sittings</a></nav>')


def coverage_text(cov):
    """Human-readable coverage.

    Never print "126 of 124": maxResult is load-balanced and under-reports, so a
    bare ratio above 1 reads like a bug to a general reader. When we have
    everything, say so plainly; when we are short, give the shortfall.
    """
    n, mx = cov.get("collected"), cov.get("max_result")
    if not n:
        return "—"
    if not mx or n >= mx:
        return f"{n} (complete)"
    return f"{n} of about {mx} ({n / mx * 100:.0f}%)"


_SITTING_TIMES = None
_SITTING_VIDEOS = None


def sitting_times():
    """Sitting durations, keyed by date. Missing file is not an error -- the pages just
    report the length as not recorded."""
    global _SITTING_TIMES
    if _SITTING_TIMES is None:
        p = os.path.join(ROOT, "pipeline", "sitting_times.json")
        try:
            _SITTING_TIMES = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            _SITTING_TIMES = {}
    return _SITTING_TIMES


def sitting_videos():
    """Official MDDI YouTube recordings, keyed by date.

    A date is present with a null value when a search ran and found nothing -- that is a
    real verdict, recorded so the search is not repeated. Where there is no video the page
    OMITS the link rather than guessing: a wrong link to the primary record would be worse
    than none, and the archive does not reach every year.
    """
    global _SITTING_VIDEOS
    if _SITTING_VIDEOS is None:
        p = os.path.join(ROOT, "pipeline", "sitting_videos.json")
        try:
            _SITTING_VIDEOS = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            _SITTING_VIDEOS = {}
    return _SITTING_VIDEOS


def published_words(briefs):
    """Words a reader actually sees: the published sentences plus the summaries.

    Counts what the page puts in front of them, not the whole record -- the point is to
    tell them how long THIS page takes, not how long the Hansard does.
    """
    n = 0
    for b in briefs or []:
        for sec in b.get("sections") or []:
            n += len((sec.get("summary") or "").split())
            for s in sec.get("sentences") or []:
                n += len((s.get("text") or "").split())
    return n


def brief_substance(brief):
    """The size of a brief's substance, whatever schema it is.

    Schema 3 counted key_points; schema 4 replaced them with sections of verbatim
    sentences. Five call sites tested `key_points` directly, so every schema-4 brief looked
    EMPTY -- it was classified as procedural and listed compactly instead of rendered as a
    card, and the page showed no briefs at all while reporting "17 verified points". One
    helper, so the next schema change is a one-line edit instead of a hunt.
    """
    if brief.get("sections"):
        return sum(len(s.get("sentences") or []) for s in brief["sections"])
    return len(brief.get("key_points") or [])


def render_brief(brief, sitting_dates=None):
    """Render one summarised policy item as a neutral brief.

    Each key point shows the verbatim quote it was verified against, so a reader
    can check us. Points whose quote failed verification were dropped upstream
    and never reach here.
    """
    meta = brief.get("_meta", {})
    title = brief.get("title") or "Untitled"
    stage = (brief.get("stage") or "").strip()
    stage_html = ""
    if stage and stage.lower() not in ("not stated", "n/a", ""):
        stage_html = f'<span class="stage">{esc(stage)}</span>'

    dates = meta.get("sitting_dates") or ([sitting_dates] if sitting_dates else [])
    dates_txt = ""
    if dates:
        if len(dates) == 1:
            dates_txt = pretty_date(dates[0])
        else:
            dates_txt = f"{pretty_date(dates[0])} &ndash; {pretty_date(dates[-1])}"

    # Points are grouped by speaker turn: the name is printed once, and the
    # points that follow under the same speaker are nested beneath it. Without
    # this a debate reads "Mr X / Mr X / Mr X / Assoc Prof Y / Assoc Prof Y",
    # which is ~70% redundant attribution lines on the page.
    def build_point(p, first_under_speaker):
        quote = (p.get("quote") or "").strip()
        quote_html = ""
        if quote:
            quote_html = (
                f'<details class="qsum">'
                f'<summary title="Show the verbatim line from Hansard">'
                f'<span class="qlabel">Verbatim</span>'
                f'</summary>'
                f'<blockquote>{esc(quote)}</blockquote>'
                f'</details>')
        return (f'<li class="pt">'
                f'<p class="pttext">{esc(p.get("point", ""))}</p>'
                f'{quote_html}'
                f'</li>')

    groups, cur = [], None
    for p in brief.get("key_points", []) or []:
        who = (p.get("speaker") or "Unattributed").strip() or "Unattributed"
        if cur is None or who != cur["who"]:
            cur = {"who": who, "pts": []}
            groups.append(cur)
        cur["pts"].append(p)

    pts = []
    for g in groups:
        inner = "".join(build_point(p, i == 0) for i, p in enumerate(g["pts"]))
        pts.append(
            f'<li class="spk">'
            f'<p class="spkwho">{esc(g["who"])}</p>'
            f'<ul class="points">{inner}</ul>'
            f'</li>')

    # A busy brief can carry 60+ verified points (~25k characters of prose and
    # quotes). Showing them all by default reproduced the debate we exist to
    # summarise -- across 2026 the corpus came to ~116k words of briefs, which is
    # roughly the size of Hansard itself. So: always show the first few speakers
    # (they are usually the mover and the respondent), and put the rest behind
    # one disclosure. Nothing is dropped; the full record stays one click away.
    head, tail = pts[:SPEAKER_PREVIEW], pts[SPEAKER_PREVIEW:]
    if tail:
        hidden_pts = sum(len(g["pts"]) for g in groups[SPEAKER_PREVIEW:])
        more_pts_html = (
            f'<details class="morepts"><summary>Show the remaining '
            f'{len(tail)} speaker{"s" if len(tail) != 1 else ""} and '
            f'{hidden_pts} point{"s" if hidden_pts != 1 else ""} from this debate'
            f'</summary><ul class="points">{"".join(tail)}</ul></details>')
    else:
        more_pts_html = ""
    points_html = f'<ul class="points">{"".join(head)}</ul>{more_pts_html}'

    not_said = brief.get("not_said") or []
    ns_html = ""
    if not_said:
        items = "".join(f"<li>{esc(x)}</li>" for x in not_said)
        ns_html = (f'<details class="ns"><summary>Open questions from this debate '
                   f'({len(not_said)})</summary>'
                   f'<p class="nnote">Questions the debate raises that the record does '
                   f'not resolve. Listed as open questions, not as findings.</p>'
                   f'<ul>{items}</ul></details>')

    next_txt = (brief.get("what_happens_next") or "").strip()
    next_html = ""
    if next_txt and next_txt.lower() not in ("not stated", ""):
        next_html = f'<p class="next"><b>What happens next</b> {esc(next_txt)}</p>'

    why = (brief.get("why_it_matters") or "").strip()
    why_html = (f'<p class="why">{esc(why)}</p>' if why else "")

    return f"""
      <article class="briefwrap" id="{esc(meta.get('report_ids', [''])[0])}">
      <details class="brief" data-card="{esc(meta.get('report_ids', [''])[0])}" open>
        <summary class="briefsum">
          <span class="btitle">{esc(title)}</span>
          <span class="bhint">{stage_html}<span class="bdate">{dates_txt}</span>
            <span class="bsrc">{', '.join(esc(x) for x in (meta.get('report_ids') or [])[:4])}</span>
          </span>
          <span class="bcaret" aria-hidden="true"></span>
        </summary>
        <div class="bbody">
        <p class="whatis">{esc(brief.get('what_it_is', ''))}</p>
        {why_html}
        {points_html}
        {next_html}
        {ns_html}
        </div>
      </details>
      </article>"""


def short_brief(item_id, others=()):
    """A compact qualifier for a sentence id, long enough to disambiguate, no longer.

    The stored id is an internal key like "budget-2916+2918+2928". The reader needs only
    enough to tell two records on the same page apart, so the SHARED prefix across the
    page's ids is dropped: on a page of oral answers that leaves the number, and on a mixed
    page it leaves the group word. Falls back to the full id when nothing is shared.
    """
    i = str(item_id or "")
    others = [str(o) for o in others if o and o != i]
    if not others:
        return i
    # longest common prefix across every id on the page
    pre = i
    for o in others:
        n = 0
        while n < len(pre) and n < len(o) and pre[n] == o[n]:
            n += 1
        pre = pre[:n]
    # Trim the shared prefix back to a SEPARATOR so no half-word is left behind:
    # "motion-" vs "matter-" share only "m", and cutting there leaves "otion-...".
    cut = 0
    for sep in ("-", "+", "/"):
        k = pre.rfind(sep)
        if k > cut:
            cut = k + 1
    out = i[cut:].strip("+-") or i
    return out if len(out) >= 2 else i


# How many silent rows before a speaker's name is shown again. Carrying it across a whole
# brief hides 84% of repeats versus 46% if the name resets per section, but a reader must
# never be left without a name to attribute a passage to -- so it reappears periodically.
# Counted in ROWS, not pixels: the renderer has no layout information, and a row is roughly
# 2-4 lines, so six rows is about one screen.
RESHOW_AFTER = 6


def render_brief_selected(brief, sitting_dates=None, page_brief_ids=None):
    """Render a selection-schema (v4) brief: sections, verbatim sentences, sticky summary.

    This is the shape that replaced paraphrasing. Every published sentence is copied from
    the record by id, so the page cannot contain a sentence that Hansard does not. The
    one model-written element per section is its summary, which sits immediately above the
    sentences it claims to summarise so a reader can check it in one glance.
    """
    meta = brief.get("_meta", {}) or {}
    title = brief.get("title") or meta.get("id") or "Untitled"
    item_id = meta.get("id") or ""
    # A sentence id is unique only WITHIN an item. When the page carries several briefs --
    # a Budget sitting has four -- "s00006" appears in each with different text, so the bare
    # id is ambiguous and uncheckable. Qualify it with the brief, but keep the raw id in the
    # DOM (data attributes and the anchor) so provenance is unchanged.
    ambiguous_ids = bool(page_brief_ids) and len(page_brief_ids) > 1

    def sid_label(sid):
        if not ambiguous_ids:
            return esc(sid)
        return f"{esc(sid)} \u00b7 {esc(short_brief(item_id, page_brief_ids))}"
    sections = brief.get("sections") or []
    if not sections:
        return ""

    dates = meta.get("sitting_dates") or ([sitting_dates] if sitting_dates else [])
    if dates:
        if len(dates) == 1:
            dates_txt = pretty_date(dates[0])
        else:
            dates_txt = f"{pretty_date(dates[0])} &ndash; {pretty_date(dates[-1])}"
    else:
        dates_txt = ""

    grp = (meta.get("group") or "").strip()
    total = meta.get("sentences_total") or 0
    published = meta.get("sentences_in_sections") or 0
    skipped = max(0, total - published)

    def sid_num(x):
        """Integer position of a sentence id. Ids are global and ordered within an item
        (s00008, s00009, s00025...), so the number IS the position in the record -- which
        makes the gap between two sections arithmetic rather than a lookup. A set built
        from published sentences only would report every gap as zero, because the
        unselected sentences in between are not in the set."""
        m = re.match(r"s(\d+)$", str(x or "").strip())
        return int(m.group(1)) if m else None

    # ---------------------------------------------------------------- runs
    # A marker "between sections" was the first attempt and it LOST SENTENCES: measured
    # across 2026, 277 of 287 briefs had text the page never accounted for -- 817 leading
    # sentences with no marker, 755 trailing, and ~800 more inside sections, because a
    # section's sentences are not always contiguous either (bill-772's section jumps from
    # s00194 to s00196). That is silent loss, which R-2.5 forbids outright.
    #
    # So the record is walked as ALTERNATING RUNS of published and unpublished ids, and
    # every unpublished run is accounted for. One rule, and it covers leading, trailing and
    # internal holes without any of them being a special case.
    #
    # Small runs are shown INLINE rather than collapsed: 26% of runs are one or two
    # sentences, and a marker saying "1 sentence hidden" costs as much space as the
    # sentence and reads as noise. The collapse is for material worth hiding.
    INLINE_MAX = 2

    def sid_num(x):
        """Integer position of a sentence id. Ids are global and ordered within an item
        (s00008, s00009, s00025...), so the number IS the position in the record."""
        m = re.match(r"s0*(\d+)$", str(x or "").strip())
        return int(m.group(1)) if m else None

    total = meta.get("sentences_total") or 0
    pub_by_id = {}
    for sec in sections:
        for x in sec.get("sentences") or []:
            k = sid_num(x.get("sid"))
            if k is not None:
                pub_by_id[k] = x

    # walk 1..total, collecting the unpublished runs and where each begins
    runs, start = [], None
    for i in range(1, max(1, total) + 1):
        if i in pub_by_id:
            if start is not None:
                runs.append((start, i - 1))
                start = None
        elif start is None:
            start = i
    if start is not None:
        runs.append((start, total))
    # runs keyed by the id that FOLLOWS them, so when a published sentence is reached the
    # run immediately before it is findable. Keyed by b + 1: a run ending at k-1 is found
    # by looking up k. (An earlier version looked up k-1 here, which missed every run that
    # ended exactly one id before a published sentence -- the off-by-one that left
    # sentences unaccounted for.)
    run_before = {}
    for a, b in runs:
        run_before[b + 1] = (a, b)
    # Each run is rendered EXACTLY once, keyed by its START id. The key must be the same in
    # both places that emit (a section's tail and the head of the next section's first
    # sentence): keying one by `a` and the other by `b + 1` made the guard miss, so 64 runs
    # appeared twice and the second copy sat under the wrong summary.
    emitted = set()
    hidden_n = sum(b - a + 1 for a, b in runs)   # sentences behind the buttons

    def skipped_rows(a, b):
        """Same dual form as inline_rows: full text on desktop, a collapse control on mobile."""
        return inline_rows(a, b)

    def inline_rows(a, b):
        """A run rendered as quiet rows.

        ON DESKTOP the unselected sentences are part of the page: the spike showed the whole
        record with the selection emphasised, and that is the honest form -- a reader sees
        what was passed over, in place. ON MOBILE they are collapsed behind a count, because a
        phone cannot afford the scroll and the full transcript is 5x the selected text.

        So the markup carries BOTH: the real sentence rows (hidden on mobile by CSS) and the
        collapse control (hidden on desktop). One payload, two presentations, no second
        template.
        """
        between = b - a + 1
        return (
            f'<li class="gapi run" data-from="{a}" data-to="{b}">'
            # desktop: filled in place by the script, faded
            f'<span class="runfull" data-from="{a}" data-to="{b}"></span>'
            # mobile: a button plus a body the script fills on demand
            f'<button class="gapd" type="button" data-from="{a}" data-to="{b}" '
            f'aria-expanded="false">'
            f'<span class="gapn">{between:,} '
            f'sentence{"s" if between != 1 else ""} hidden</span>'
            f'<span class="gapx">show</span></button>'
            f'<div class="gapbody" hidden></div>'
            f'</li>')

    cards = []
    seen_speakers = set()
    
    for n, sec in enumerate(sections):
        sents = sec.get("sentences") or []
        if not sents:
            continue
        # Who speaks in this section. Named in the card when there is one speaker (95.6% of
        # sections); when there are several the card lists them AND the sentences mark where
        # the speaker changes, because otherwise a reader cannot attribute a sentence.
        sec_speakers, _seen = [], set()
        for x in sents:
            sp = (x.get("speaker") or "").strip()
            if sp and sp not in _seen:
                _seen.add(sp)
                sec_speakers.append(sp)
        multi_speaker = len(sec_speakers) > 1
        sec_last_spk = None       # only used when the section has several speakers
        rows = []
        # The last speaker SHOWN anywhere in this brief, carried across sections: consecutive
        # sections are often the same speaker, and resetting per section reprinted the name
        # three times inside one continuous speech. Re-shown after RESHOW_AFTER silent rows
        # so a long run cannot leave the reader without a name to attribute it to.
        for x in sents:
            k = sid_num(x.get("sid"))
            # The unpublished run immediately before this sentence, if this section is
            # where the reader first meets it. Skipped when a previous section's tail
            # already emitted it -- keyed on the run START, matching the tail loop.
            if k is not None and k in run_before and run_before[k][0] not in emitted:
                a, b = run_before[k]
                emitted.add(a)
                rows.append(inline_rows(a, b) if b - a + 1 <= INLINE_MAX
                            else skipped_rows(a, b))
            raw_spk = (x.get("speaker") or "").strip()
            # EVERY sentence carries its speaker. Suppressing it made the page tidier and
            # made the attribution easier to miss, and the owner's reason for wanting it
            # back is the stronger one: naming the speaker on each sentence is what lends
            # the record credibility, because a reader can see who said each thing rather
            # than inferring it from a card at the top. The card ALSO names them, so the
            # section is attributable at a glance and every line is attributable in detail.
            #
            # The first mention of a speaker in a brief carries their full string (portfolio
            # or constituency) in a second line; later mentions are just the name, so the
            # repetition stays short.
            if not raw_spk:
                spk_html = '<span class="who none"></span>'
            else:
                # Name, then the CONSTITUENCY on every sentence. The owner's reason: the
                # constituency is what places the speaker, so it carries the same credibility
                # value as the name and must not be a first-mention-only detail. Inline after
                # an em dash, so it costs a few characters rather than a line per sentence.
                # Ministers have none -- the record gives them a portfolio instead, which is
                # honest rather than a gap.
                spk = esc(short_speaker(raw_spk))
                con = constituency(raw_spk)
                if con:
                    spk = f'{spk} <span class="spkcon">&mdash; {esc(con)}</span>'
                spk_html = f'<span class="who">{spk}</span>'
            ctx = ('<span class="ctxmark">context</span>'
                   if x.get("added_for_context") else "")
            rows.append(
                f'<li class="vs" data-speaker="{esc(raw_spk)}">'
                f'<div class="vs-meta">'
                f'<span class="sid" title="{esc(item_id)}">{sid_label(x.get("sid", ""))}'
                f'</span>'
                f'{spk_html}{ctx}</div>'
                f'<p class="vs-text">{esc(x.get("text", ""))}</p>'
                f'</li>')

        label = (sec.get("label") or "").strip()
        summary = (sec.get("summary") or "").strip()
        # Attribution line for the card. One speaker is the common case and reads as a plain
        # name; several are listed so the reader knows whose exchange this is.
        who_line = ""
        if len(sec_speakers) == 1:
            who_line = esc(short_speaker(sec_speakers[0]))
        elif len(sec_speakers) > 1:
            # Separator is HTML, the names are escaped -- escaping the whole joined string
            # would print "&middot;" as literal text.
            parts = [esc(short_speaker(x)) for x in sec_speakers[:4]]
            if len(sec_speakers) > 4:
                parts.append(f"+{len(sec_speakers) - 4} more")
            who_line = ' <span class="sep">&middot;</span> '.join(parts)
        raw_title = esc(" | ".join(sec_speakers)) if sec_speakers else ""
        sum_html = ""
        if summary or label or who_line:
            # .sumcol is the grid column on desktop and display:contents on mobile, so the
            # same markup gives "card above the sentences" on a phone and "card beside them"
            # on a wide screen.
            sum_html = (
                f'<div class="sumcol"><div class="sumwrap"><div class="sumcard">'
                + (f'<div class="sumwho" title="{raw_title}">{who_line}</div>'
                   if who_line else "")
                + (f'<div class="sumlabel">{esc(label)}</div>' if label else "")
                + (f'<p class="sumtext">{esc(summary)}</p>' if summary else "")
                + '</div></div></div>')

        # A run that starts after this section's last sentence belongs HERE, at the end of
        # this section: that is where the reader meets it. Emitting only the first such run
        # left the rest unaccounted for, so every remaining run before the next section's
        # first sentence is emitted in id order.
        tail = ""
        last = sid_num(sents[-1].get("sid"))
        if last is not None:
            tail_runs = [r for r in runs if r[0] > last and r[0] not in emitted]
            # stop at the next section's start so runs stay with the text they precede
            nxt_first = None
            if n + 1 < len(sections):
                nxt_s = sections[n + 1].get("sentences") or []
                if nxt_s:
                    nxt_first = sid_num(nxt_s[0].get("sid"))
            for a, b in tail_runs:
                if nxt_first is not None and b + 1 > nxt_first:
                    break
                emitted.add(a)
                tail += (inline_rows(a, b) if b - a + 1 <= INLINE_MAX
                         else skipped_rows(a, b))

        cards.append(
            f'<section class="dsec" id="sec-{n + 1}" data-brief="{esc(item_id)}">'
            f'{sum_html}'
            f'<ol class="vslist">{"".join(rows)}</ol>'
            f'{tail}'
            f'</section>')

    # anything left is the record's tail; emit it after the last section. Without this the
    # final sentences of an item were silently absent from the page.
    leftover = "".join(
        (inline_rows(a, b) if b - a + 1 <= INLINE_MAX else skipped_rows(a, b))
        for a, b in runs if a not in emitted)
    if leftover and cards:
        cards[-1] = cards[-1].replace("</section>", leftover + "</section>")

    # The collapse switch, built as its own string: Python 3.9 cannot nest a triple-quoted
    # f-string inside another, and an f-string expression may not contain a backslash.
    switch_html = ""
    if total:
        switch_html = (
            '<div class="onlysel">'
            f'<input type="checkbox" class="osw-in" id="onlysel-{esc(item_id)}" '
            f'data-brief="{esc(item_id)}">'
            f'<label class="osw" for="onlysel-{esc(item_id)}"><i></i>'
            f'<span>Hide the sentences <b>not</b> selected</span></label>'
            f'<span class="oshint">{hidden_n:,} of {total:,} sentences in this record</span>'
            '</div>')

    return f"""
<article class="brief brief-v4">
  <header class="brief-hd">
    <h2>{esc(title)}</h2>
    <p class="brief-meta">
      {esc(grp + " · ") if grp else ""}{dates_txt}
      {f' · {total:,} sentences in the record' if total else ''}
    </p>
    {f'<p class="brief-what">{esc(brief.get("what_it_is", ""))}</p>' if brief.get("what_it_is") else ''}
    {switch_html}
  </header>
  {''.join(cards)}
  <footer class="brief-ft">
    <p class="prov">
      {published:,} of {total:,} sentences in this record are shown in full; the other
      {hidden_n:,} are hidden behind the buttons, and can be shown by tapping one. Nothing
      was deleted \u2014 the brief simply does not emphasise them.
    </p>
    <p class="prov">
      Every sentence above is copied from the Hansard record by id; the model returned
      ids only and never wrote them. The summary in each section is machine-written and
      sits beside the sentences it covers, so it can be checked.
    </p>
  </footer>
</article>"""


def render_brief_any(brief, sitting_dates=None, page_brief_ids=None):
    """Dispatch on schema, so a mixed archive still renders coherently."""
    meta = brief.get("_meta", {}) or {}
    if brief.get("sections"):
        return render_brief_selected(brief, sitting_dates, page_brief_ids)
    return render_brief(brief, sitting_dates)


def render_sitting(sitting, *, css_href, home_href, archive_href, summaries=None):
    d = sitting["date"]
    cov = sitting["coverage"]
    reports = sitting["reports"]
    panels = compute_panels(sitting)
    total_words = cov.get("words", 0)

    grouped = defaultdict(list)
    for r in reports:
        grouped[r["group"]].append(r)
    order = [g for g in GROUP_ORDER if g in grouped]

    # "Everything else" -- the long tail.
    #
    # Previously a flat list per group with a "+N more" button. Two problems:
    # the button was a no-op (the JS removed a `hidden` class that was never
    # added, so nothing was ever concealed), and a Budget sitting carries 200+
    # items, which is an unscrollable wall.
    #
    # Now each section group is a native <details> card, collapsed by default,
    # so the page opens showing only group headings and a count. Native details
    # means it works with no JS, survives the page being saved, and is
    # keyboard/AT accessible for free.
    def report_row(r):
        return (f'<li id="{esc(r["report_id"])}">'
                f'<a href="{esc(hansard_url(r))}" target="_blank" rel="noopener">'
                f'<span class="it">{esc(r["title"]) or "(untitled)"}</span>'
                f'</a></li>')

    groups_html = []
    for g in order:
        # Oral answers have their own full section above; listing them again here
        # as a collapsed card would duplicate every title on the page.
        if g == "oral":
            continue
        items = sorted(grouped[g], key=lambda r: -r["words"])
        if not items:
            continue
        label = GROUP_LABEL.get(g, g)
        # a short flavour line so a collapsed card still tells you something.
        # NOTE: build this from escaped parts, never esc() a string that already
        # contains an HTML entity -- escaping turns "&middot;" into "&amp;middot;"
        # which renders literally on the page.
        with_debate = sum(1 for r in items if r["turns"])
        n_items = len(items)
        summary_note = (f"{n_items} item{'s' if n_items != 1 else ''}"
                        f" &middot; {with_debate} with recorded debate")
        groups_html.append(
            f'<details class="grp" id="grp-{esc(g)}">'
            f'<summary><span class="gtitle">{esc(label)}</span>'
            f'<span class="gcount">{summary_note}</span></summary>'
            f'<ul class="glist">{"".join(report_row(r) for r in items)}</ul>'
            f'</details>')

    # NOTE: there used to be a "debate of the day" section here that dumped the
    # largest debate's first 14 turns as raw transcript (~10k words on 5 Aug
    # 2026). It duplicated the summarised brief above it -- same source records,
    # same speakers, same order -- while reprinting word counts the product
    # deliberately drops. Removed: the brief is the better version of the same
    # thing, and the full text now sits one click away via hansard_url().

    numbers_html = "\n".join(
        f'<li class="num"><b>{esc(n["value"])}</b><span>{esc(n["context"])}</span>'
        f'<a class="sig" href="#{esc(n["report_id"])}">{esc(n["speaker"])} '
        f'&middot; {esc(n["title"][:44])}</a></li>'
        for n in panels["numbers"])

    qa_rows = panels["qa"]
    # Index the oral briefs by report id so each mapping can render its summary.
    oral_briefs = {}
    for b in (summaries or []):
        if b.get("_meta", {}).get("group") != "oral":
            continue
        for rid in (b.get("_meta", {}).get("report_ids") or []):
            oral_briefs[rid] = b
    qa_html = "\n".join(
        render_mapping(q, oral_briefs.get(q["report_id"])) for q in qa_rows)
    n_summarised = sum(1 for q in qa_rows if q["report_id"] in oral_briefs)
    # Very short answers (under the summariser's 150-word floor) are skipped on
    # purpose -- there is nothing to condense. Count them separately so the note
    # does not imply work is still pending when it is finished.
    n_too_short = sum(1 for q in qa_rows
                      if q["report_id"] not in oral_briefs and q.get("total_words", 0) < 150)
    # Built here rather than inline: nested f-strings with conditional plurals are
    # unreadable and easy to break.
    oral_note = ""
    if n_summarised:
        oral_note = f"<b>{n_summarised} of {len(qa_rows)} summarised</b>"
        if n_too_short == 1:
            oral_note += "; 1 answer was too short to condense and shows in full"
        elif n_too_short:
            oral_note += (f"; {n_too_short} answers were too short to condense "
                          f"and show in full")
        oral_note += "."

    attr = cov.get("speaker_attribution")
    attr_txt = f"{attr*100:.0f}%" if attr else "n/a"
    # maxResult is load-balanced and has been observed UNDER-reporting (13 Jan
    # 2026: 126 reports collected against a claimed 124). So a ratio above 1 is
    # normal, not an error -- only warn when we actually came up short.
    ratio = cov.get("ratio")
    short = ratio is not None and ratio < 0.99
    coverage_warn = ("" if not short else
                     '<li class="warn">Coverage is below 100%. Some items from this '
                     'sitting may be missing, so read the totals as a floor rather '
                     'than a final count.</li>')

    # ---- substance layer: pick the briefs relevant to this sitting ----
    briefs = []
    for b in (summaries or []):
        meta = b.get("_meta", {})
        if d not in (meta.get("sitting_dates") or []):
            continue
        # Oral answers belong to the "Oral answers" section, where they render as
        # question -> response pairs. Rendering them here as well duplicated all 16
        # of them as substance cards and doubled the page height.
        if meta.get("group") == "oral":
            continue
        briefs.append(b)
    # order by what a citizen most needs to know
    ORDER = {"bill": 0, "statement": 1, "budget": 2, "motion": 3, "adjournment": 4}
    briefs.sort(key=lambda b: (ORDER.get(b.get("_meta", {}).get("group"), 9),
                               -brief_substance(b)))
    # A brief with no verified points is procedural business -- the summariser
    # correctly reports "no policy substance". Rendering it as a full card would
    # pad the page with headings that say nothing, so list those compactly and
    # keep the cards for things that actually decided or announced something.
    substantive = [b for b in briefs if brief_substance(b)]
    procedural = [b for b in briefs if not brief_substance(b)]
    bills = [b for b in substantive if b.get("_meta", {}).get("group") == "bill"]
    others = [b for b in substantive if b.get("_meta", {}).get("group") != "bill"]

    proc_html = ""
    if procedural:
        items = "".join(
            f'<li><span class="pstage">{esc((b.get("stage") or "").strip() or "procedural")}</span>'
            f'<span class="pttl">{esc(b.get("title") or "")}</span>'
            f'<span class="pnote2">{esc((b.get("what_it_is") or "")[:150])}</span></li>'
            for b in procedural)
        proc_html = (f'<details class="proc"><summary>Procedural business with no '
                     f'policy content ({len(procedural)})</summary>'
                     f'<p class="nnote">These items are recorded in Hansard as formal '
                     f'steps &mdash; openings, acknowledgements, sum approvals &mdash; '
                     f'with no policy content in the transcript. Listed for completeness.</p>'
                     f'<ul>{items}</ul></details>')

    def brief_list(items, limit=None):
        out = items if limit is None else items[:limit]
        ids = [(b.get("_meta") or {}).get("id") for b in out]
        return "".join(render_brief_any(b, page_brief_ids=ids) for b in out)

    # The section rail builds ticks from h2/h3 headings, so the briefs need real
    # headings rather than being a run of <article>s. Without these the rail
    # would only ever show the four section titles.
    def brief_block(items, heading):
        if not items:
            return ""
        # The id list is built ONCE, outside the generator. Nested inside it, the
        # comprehension became the generator's item rather than an argument, so join()
        # received ids instead of HTML and the page collapsed to a single card.
        ids = [(x.get("_meta") or {}).get("id") for x in items]
        return (f'<h2 class="railhead">{esc(heading)}</h2>'
                + "".join(render_brief_any(b, page_brief_ids=ids) for b in items))

    lead = reports[0] if reports else {}
    if substantive:
        lede_words = (f"{len(substantive)} polic{'y' if len(substantive) == 1 else 'ies'} "
                      f"and {len(reports):,} items of business")
    else:
        lede_words = f"{len(reports):,} items of business"

    # THE SLOGAN. Owner's call: a fixed, round figure for Parliament with the read estimate
    # computed per page. One line, no labels.
    #
    # THE 6 HOURS NOW AGREES WITH THE MEASUREMENT, which is why the earlier caveat is gone.
    # The corpus median from pipeline/sitting_times.json is 6.0h (p25 4.5, p75 7.8), so this
    # figure matches what the record actually shows rather than being a round number chosen
    # against it. It is still a GENERAL statement about Parliament, not this sitting's own
    # length -- the per-sitting value is available and is deliberately not used, because the
    # owner wanted one line rather than a fact table. Kept as a named constant so the choice
    # lives in one place.
    SITTING_SLOGAN_HOURS = 6

    dur_part = (f"Parliament sitting <b>~{SITTING_SLOGAN_HOURS} hours</b>")

    # The read estimate IS per page, computed from the words this page actually publishes at
    # 200 words per minute, so two sittings of very different size get very different
    # numbers (measured range across the corpus: 7 min to nearly 5 hours).
    read_part = ""
    pw = published_words(briefs)
    if pw:
        mins = max(1, pw / 200.0)
        if mins >= 55:
            rough = round(mins / 30.0) / 2.0                   # nearest half hour
            tail = f"~{rough:g} hour{'s' if rough != 1 else ''}"
        else:
            rough = max(5, int(round(mins / 10.0) * 10))       # nearest ten minutes
            tail = f"~{rough} min"
        read_part = f"Parsnips <b>{tail}</b>"
    meta_html = (f'<p class="slogan">{dur_part}, {read_part}.</p>'
                 if read_part else f'<p class="slogan">{dur_part}.</p>')

    # THE PRIMARY SOURCE. Where a video was positively resolved to MDDI's own channel on this
    # sitting's date, link it. Where it was not, SAY SO rather than omitting the line: the
    # gap is then visible and honest, and a reader is not left wondering whether the page
    # simply forgot. MDDI's archive does not reach further back than about November 2025, and
    # no other source is the official recording.
    vid = sitting_videos().get(d) or {}
    if vid.get("video_id"):
        meta_html += (
            f'<p class="srcvid"><a href="https://www.youtube.com/watch?v='
            f'{esc(vid["video_id"])}" target="_blank" rel="noopener noreferrer">'
            f'Watch the full sitting &rarr;</a>'
            f'<span class="hint">MDDI Singapore, the official recording</span></p>')
    else:
        # ONE sentence, not two fragments joined by punctuation: a middot between two wrapping
        # spans renders as an orphan bullet at the start of the second line. Plain prose cannot
        # break that way.
        meta_html += (
            '<p class="srcvid is-none">'
            '<span class="novid">No recording on the official MDDI channel '
            '&mdash; their archive does not go back this far</span></p>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parsnips — Parliament, {esc(pretty_date(d))}</title>
<meta name="description" content="{esc(pretty_date(d))}: what Singapore's Parliament decided and what it means, in a page.">
<link rel="stylesheet" href="{esc(css_href)}">
</head>
<body data-page="{esc(d)}">
<header class="top">
  <div class="wrap">
    <a class="logo" href="{esc(home_href)}"><span class="veg">🌱</span> Parsnips</a>
    {nav(home_href, archive_href)}
  </div>
</header>
<button class="totop" type="button" aria-label="Back to top">&uarr;</button>

<main class="wrap">

  <section class="lede">
    <p class="kicker">Singapore Parliament &middot; Sitting No. {esc(str(lead.get("sitting_no") or ""))}</p>
    <h1><time datetime="{esc(d)}">{esc(pretty_date(d))}</time></h1>
    <p class="dek">{lede_words}, read so you don't have to.</p>
    {meta_html}
  </section>

  {f'''
  <section class="substance" id="sec-briefs">
    <h2>What the Government is doing</h2>
    <p class="sub">What was decided or announced, who said it, and where it goes next.
    Every point carries the words it was taken from.</p>
    {brief_block(bills, "Bills")}
    {brief_block(others, "Debates and other business")}
    {proc_html}
  </section>''' if substantive else f'''
  <section class="substance" id="sec-briefs">
    <h2>What the Government is doing</h2>
    <p class="empty">This sitting's business was entirely procedural.</p>
    {proc_html}
  </section>'''}

  {f'''
  <section class="oral" id="sec-oral">
    <h2>Oral answers</h2>
    <p class="sub">{len(qa_rows)} questions put to Ministers, each paired with the
    response given. {MAPPING_NOTE} {oral_note}</p>
    <details class="qajump">
      <summary>Jump to a question ({len(qa_rows)})</summary>
      <nav class="qjbody" aria-label="Jump to an oral answer">
        <ol>{''.join(f'<li><a href="#{esc(q["report_id"])}">{esc(q["title"])}</a>'
                     f'<span class="qw">{esc(q["asker"])}</span></li>' for q in qa_rows)}</ol>
      </nav>
    </details>
    <ul class="maplist">{qa_html}</ul>
  </section>''' if qa_rows else ''}

  <section class="everything" id="sec-rest">
    <h2>Everything else</h2>
    <p class="sub">The rest of the sitting, grouped. {len(reports)} items.</p>
    {''.join(groups_html)}
  </section>

  <section class="stats-note">
    <details>
      <summary>How much was said (sitting statistics)</summary>
      <div class="statgrid">
        <div class="bars people">
          <h4>Words by section</h4>
          {bar_rows(panels["budget"], total_words, GROUP_LABEL)}
        </div>
        <div class="bars people">
          <h4>Who spoke most</h4>
          {bar_rows(panels["speakers"][:10], panels["speakers"][0][1] if panels["speakers"] else 1)}
        </div>
      </div>
      <p class="statnote">Volume, not substance. Kept for reference.</p>
    </details>
  </section>

  <section class="method" id="sec-method">
    <h2>How this page was made</h2>
    <p>Built from the official Hansard record (Parliament of Singapore Official Reports).
    No news reporting was used. Briefs are written by software from the transcript and
    every key point carries the verbatim words it was drawn from.</p>
    <ul>
      <li>Reports collected: <b>{coverage_text(cov)}</b></li>
      <li>Briefs on this page: <b>{len(substantive)}</b>
        {f'({sum(brief_substance(b) for b in substantive):,} sentences published)' if substantive else ''}</li>
      <li>Speaker attribution: <b>{attr_txt}</b> of turns carry an explicit speaker tag</li>
      <li>Any point whose quote could not be found in the transcript was discarded
        rather than shown.</li>
      {coverage_warn}
    </ul>
  </section>

  <div class="pbar" role="progressbar" aria-label="Progress through this sitting"
       aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
    <span class="pbar-fill"></span>
    <span class="pbar-txt">0%</span>
  </div>

  <footer><p>Parsnips &middot; an unofficial reader for the Official Report.
  Hansard is a public record; the full text is at sprs.parl.gov.sg.</p></footer>
</main>
<script>
{SCRIPT}
</script>
</body>
</html>
"""


def render_archive(sittings, *, css_href, home_href, archive_href, summaries=None):
    """Archive: every sitting, newest first, with the leading policy item."""
    by_date = {}
    for b in (summaries or []):
        for dt in (b.get("_meta", {}).get("sitting_dates") or []):
            by_date.setdefault(dt, []).append(b)

    rows = []
    for s in reversed(sittings):
        d = s["date"]
        cov = s.get("coverage", {})
        dt = datetime.date.fromisoformat(d)
        briefs = by_date.get(d) or []
        if briefs:
            lead = briefs[0]
            headline = esc(lead.get("title") or "")
            lead_line = headline
            detail = f"{len(briefs)} brief{'s' if len(briefs) != 1 else ''}"
        else:
            r = max(s["reports"], key=lambda x: x["words"]) if s["reports"] else None
            lead_line = esc(((r or {}).get("title") or "(no business)")[:98])
            detail = "not yet summarised"
        rows.append((d[:4], f"""
      <li class="srow">
        <a href="{esc(d)}.html">
          <span class="sdate">{esc(pretty_date(d))}
            <span class="sdow">{esc(dt.strftime('%A'))}</span></span>
          <span class="slead">{lead_line}</span>
          <span class="sstats">{esc(detail)} &middot; {len(s['reports'])} items
            &middot; {coverage_text(cov)}</span>
        </a>
      </li>"""))

    # Group by year, newest year first. A flat list of 400+ sittings is unusable,
    # and a year is the unit we backfill in, so it is the unit a reader scans by.
    years = {}
    for yr, row in rows:
        years.setdefault(yr, []).append(row)
    blocks = []
    for yr in sorted(years, reverse=True):
        yr_rows = years[yr]
        n = len(yr_rows)
        words = sum(s.get("coverage", {}).get("words") or 0
                    for s in sittings if s["date"][:4] == yr)
        blocks.append(f"""
  <details class="yr" {'open' if yr == str(datetime.date.today().year) or yr == max(years) else ''}>
    <summary><span class="yrnum">{esc(yr)}</span>
      <span class="yrmeta">{n} sitting{'' if n == 1 else 's'} &middot; {words:,} words</span></summary>
    <ul class="slist">{''.join(yr_rows)}</ul>
  </details>""")

    total_items = sum(len(s["reports"]) for s in sittings)
    dates = [s["date"] for s in sittings]
    span = f"{pretty_date(dates[0])} &ndash; {pretty_date(dates[-1])}" if dates else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parsnips — every sitting</title>
<meta name="description" content="Every Singapore Parliament sitting in the Parsnips archive, newest first.">
<link rel="stylesheet" href="{esc(css_href)}">
</head>
<body>
<header class="top"><div class="wrap">
  <a class="logo" href="{esc(home_href)}"><span class="veg">🌱</span> Parsnips</a>
  {nav(home_href, archive_href, current="archive")}
</div></header>
<main class="wrap">
  <section class="lede">
    <p class="kicker">The archive</p>
    <h1>Every sitting</h1>
    <p class="dek">{len(sittings)} sitting{'' if len(sittings) == 1 else 's'} and
    {total_items:,} items of business, read so you don't have to.</p>
    <p class="span-note">{span}</p>
  </section>
  <section class="arch">
    {''.join(blocks) or '<p class="empty">No sittings yet.</p>'}
  </section>
  <div class="pbar" role="progressbar" aria-label="Progress through this sitting"
       aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
    <span class="pbar-fill"></span>
    <span class="pbar-txt">0%</span>
  </div>

  <footer><p>Parsnips &middot; an unofficial reader for the Official Report.
  Hansard is a public record; the full text is at sprs.parl.gov.sg.</p></footer>
</main>
</body>
</html>
"""


STYLE = """
/* ============================================================
   SELECTION BRIEF (schema v4). BASE LAYER IS THE PHONE.
   Wider viewports only ever ADD, from the min-width block at
   the end. There is no max-width override for these rules.
   ============================================================ */
.brief-v4{margin:0 0 34px}
.brief-v4 .brief-hd{margin-bottom:14px}
.brief-v4 .brief-hd h2{font-size:19px;line-height:1.32;margin:0 0 4px;letter-spacing:-.01em}
.brief-v4 .brief-meta{margin:0;font-size:12.5px;color:var(--dim)}
.brief-v4 .brief-what{margin:9px 0 0;font-size:14.5px;line-height:1.55;color:#2c343b}
/* The collapse switch. DESKTOP ONLY: on a phone the record is already collapsed behind a
   count, so a second control would be redundant. It exists because the full transcript is 5x
   the selected text and is a long scroll even on a wide screen. */
.onlysel{display:none}
.osw{display:inline-flex;align-items:center;gap:10px;cursor:pointer;user-select:none;
  font-size:13.5px;color:var(--dim);margin-top:14px}
.osw b{color:var(--accent)}
.onlysel .osw-in{position:absolute;opacity:0;width:1px;height:1px;pointer-events:none}
.osw i{position:relative;flex:none;width:38px;height:22px;background:#dde3e9;
  border-radius:999px;transition:background .18s ease}
.osw i::after{content:"";position:absolute;top:2px;left:2px;width:18px;height:18px;
  background:#fff;border-radius:50%;box-shadow:0 1px 2px rgba(20,24,29,.22);
  transition:transform .18s cubic-bezier(.3,1.4,.5,1)}
.onlysel .osw-in:checked ~ .osw i{background:var(--accent)}
.onlysel .osw-in:checked ~ .osw i::after{transform:translateX(16px)}
.onlysel .osw-in:focus-visible ~ .osw i{outline:2.5px solid var(--accent);outline-offset:2px}
.oshint{margin-left:14px;font-size:12px;color:var(--faint)}
@media (min-width:760px){
  .onlysel{display:flex;align-items:baseline;gap:4px;flex-wrap:wrap}
}
/* When the switch is on the faded context goes, and the collapsed marker returns in its
   place, so the reader keeps a count and can still expand it. Scoped to the brief. */
.dsec.hide-ctx .runfull{display:none}
.dsec.hide-ctx .gapi.run > .gapd{display:flex}
.dsec.hide-ctx .ctx{display:none}

/* A section is the sticky card's containing block, so the card releases exactly when
   the section ends -- no JavaScript, and the requirement is met by layout alone.
   The section gap is a token because the column guides are drawn per section: it is what
   the vertical rules break on, so it has to agree with the grid they are drawn from. */
.dsec{margin:0 0 var(--col-section-gap);
  scroll-margin-top:calc(var(--topbar-h) + var(--sticky-gap) + 8px)}
/* Pinned BELOW the sticky top bar: at top:0 the bar covered the card's first line for
   the entire time its section was in view. */
.sumwrap{position:sticky;top:calc(var(--topbar-h) + var(--sticky-gap));z-index:5}
.sumcol{display:contents}
.sumcard{background:var(--accent-soft);border-left:3px solid var(--accent);
  border-radius:0 8px 8px 0;padding:9px 12px 10px;margin:0 0 12px}
/* Who is speaking, in the card rather than on every sentence. 95.6% of sections have one
   speaker, so this removes the per-sentence name entirely for the vast majority -- and a
   name in the card cannot be "missed" the way a suppressed name on a row can. */
.sumwho .sep{color:var(--faint)}
.sumwho{font-size:11.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;
  margin-bottom:2px;line-height:1.35}
.sumlabel{font-size:10.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  color:var(--accent);margin-bottom:3px}
.sumtext{margin:0;font-size:14.5px;line-height:1.5;color:#17352a}

.vslist{list-style:none;margin:0;padding:0}
/* A list item that holds a marker or an inline run: no bullet, no indent, so it sits flush
   with the sentence rows it belongs between. */
.vslist .gapi{list-style:none;margin:0;padding:0}
.vs{padding:11px 0 12px;border-top:1px solid var(--line)}
.vs:first-child{border-top:0;padding-top:2px}
.vs-meta{display:flex;align-items:baseline;gap:8px;margin-bottom:4px;flex-wrap:wrap}
.sid{font:600 10.5px/1.5 var(--mono);color:var(--faint);letter-spacing:.03em}
/* The speaker repeats on every sentence -- deliberately, because naming who said each thing
   is what makes the record checkable. Kept small and tight so it does not dominate the text
   it attributes. */
.vs-meta .who{font-size:11px;color:var(--dim);text-transform:uppercase;
  letter-spacing:.045em;line-height:1.3}
/* The constituency, on every sentence: it is what places the speaker, so it carries the
   same credibility value as the name. Inline after an em dash, not on its own line. */
.vs-meta .spkcon{color:var(--faint);text-transform:none;letter-spacing:.01em}
.vs-meta .who.none{display:block;min-height:.7rem}
/* Same speaker as the row above: nothing to show, and no line taken. Distinct from
   .who.none, which means the RECORD names no speaker -- that one keeps its line so an
   unattributed sentence does not shift the rows beneath it. */
.vs-meta .who.rep{display:none}
.sk-meta .who.rep{display:none}
.ctxmark{font-size:10px;color:var(--warm);background:#fdf1e8;border-radius:4px;
  padding:1px 5px;margin-left:auto}
.vs-text{margin:0;font-size:15.5px;line-height:1.6;color:var(--ink)}

.brief-ft{margin-top:20px;padding-top:12px;border-top:1px solid var(--line)}
/* The gap count sits at the gap, between one section and the next, and is a BUTTON: a
   reader who wants the sentences that were passed over can open them in place. */
.gapd{display:flex;justify-content:center;align-items:center;gap:8px;width:100%;
  margin:0;padding:9px 0 0;font:inherit;font-size:12px;color:var(--faint);
  background:none;border:0;border-top:1px dashed var(--line);cursor:pointer;
  letter-spacing:.02em;-webkit-appearance:none}
.gapd:hover{color:var(--accent)}
.gapd:focus-visible{outline:2.5px solid var(--accent);outline-offset:2px;border-radius:4px}
/* .gapx is the affordance: without it the count reads as a static label and nobody taps */
.gapd .gapx{color:var(--accent);border-bottom:1px solid rgba(28,107,74,.35)}
.gapd[aria-expanded="true"] .gapx{border-bottom:0;color:var(--faint)}
.gapbody{margin:6px 0 0;border-top:1px dashed var(--line)}
.sk{padding:9px 0 10px;border-bottom:1px solid var(--line)}
.sk:last-child{border-bottom:0}
.sk-meta{display:flex;align-items:baseline;gap:8px;margin-bottom:3px;flex-wrap:wrap}
.sk .sid{font:600 10.5px/1.5 var(--mono);color:var(--faint);letter-spacing:.03em}
.sk .who{font-size:11.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.045em}
.sk .who.none{display:block;min-height:.7rem}
/* Skipped sentences are deliberately quieter than published ones: they are context, not
   the brief. Faint-but-readable, so a reader can still check what was passed over. */
.sk-text{margin:0;font-size:14px;line-height:1.55;color:var(--dim)}
.gapmsg{margin:6px 0 0;font-size:12px;color:var(--faint);text-align:center}
/* ---- MOBILE: the record is collapsed behind a count ---- */
.runfull{display:none}
.gapd{display:flex}
/* ---- DESKTOP: the whole record is shown, faded, with the selection emphasised ----
   This is the spike's design and the honest form: a reader sees what was passed over, in
   place. It costs 5x the selected text (median 549 KB per sitting), which a desktop
   connection carries and a phone should not. */
@media (min-width:760px){
  .runfull{display:block}
  .gapi.run > .gapd{display:none}
  .runfull .ctx{display:grid;grid-template-columns:3.6rem minmax(0,1fr);gap:.5rem;
    padding:7px 0 8px;border-top:1px solid rgba(227,231,236,.7)}
  .runfull .ctx .csid{font:600 10.5px/1.6 var(--mono);color:#ccd3da}
  .runfull .ctx .cwho{font-size:10.5px;color:#ccd3da;text-transform:uppercase;
    letter-spacing:.045em}
  .runfull .ctx .ctx-text{grid-column:2;margin:0;font-size:14.5px;line-height:1.55;
    color:var(--faint)}
  /* A sentence that needed its predecessor for grammar sits between the two shades: not
     selected, but needed for the emphasis above it to make sense. */
  .runfull .ctx.is-context .ctx-text{color:#7c8794}
}
/* the inline (short-run) container follows the same rule */
.ski{display:none}
.ski-inline{display:block}
@media (min-width:760px){
  .ski{display:block}
  .ski-inline{display:none}
}

/* Small unpublished runs render INLINE: 26% of runs are one or two sentences, and a
   marker saying "1 sentence hidden" costs as much space as the sentence it hides.
   They are visually quieter than published rows so the brief still reads as a brief. */
.ski{margin:0}
.ski .sk{padding:8px 0 9px;border-bottom:1px solid var(--line)}
.ski .sk-text{color:var(--faint);font-size:14px}
.ski .sid{color:#dfe4ea}
/* Portfolio shown once, then just the name -- a 49-char title on every sentence wraps. */
.spkfull{display:block;text-transform:none;letter-spacing:0;font-size:11px;
  color:var(--faint);margin-top:1px}
.prov{margin:0;font-size:11.5px;line-height:1.6;color:var(--faint)}

/* Wider viewports ADD only. */
@media (min-width:700px){
  .brief-v4 .brief-hd h2{font-size:22px}
  .vs-text{font-size:16px}
  .sumcard{padding:11px 15px 12px}
}

/* ============================================================
   DESKTOP: TWO PANELS. Summary beside its evidence, not above it.
   Arrives at 760px and only ever adds; the base layer above is
   still the phone, so there is one source of truth.
   ============================================================ */
@media (min-width:760px){
  .dsec{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,var(--col-sum));
    gap:var(--col-gap);align-items:start;
    scroll-margin-top:calc(var(--topbar-h) + var(--sticky-gap) + 8px)}
  /* the summary is the SECOND column visually but the FIRST in the DOM, so on mobile it
     reads above the sentences and here it sits to their right. */
  /* align-self:start keeps the cell its natural height instead of stretching it to the row,
     which is what left a tall empty column beside a short summary. */
  .sumcol{display:block;grid-column:2;grid-row:1;align-self:start;
    position:sticky;top:calc(var(--topbar-h) + var(--sticky-gap));
    height:fit-content;max-height:calc(100vh - var(--topbar-h) - var(--sticky-gap) - 20px);
    overflow-y:auto;overscroll-behavior:contain}
  .sumwrap{position:static;top:auto}
  .dsec > .vslist,.dsec > .gapd,.dsec > .gapbody{grid-column:1;grid-row:1}
  .vs-text{max-width:74ch}
  .sumtext{font-size:14px}
  /* ============================================================
     COLUMN BOUNDARY GUIDES. Vertical hairlines on the column edges,
     so the reading column and the summary column read as bounded
     rather than as text floating in whitespace (audit D7, D8).

     They are ::before/::after ON THE GRID CONTAINER, which is what
     makes them land exactly on the grid: no arithmetic, no magic
     offsets. The content box of .dsec is exactly track1 + gap +
     track2, so
        left:0                        -> the text column's left edge
        right:calc(100% - track1)     -> the text column's right edge
        left:calc(100% - track2 - w)  -> the summary column's left edge
        right:0                       -> the summary column's right edge
     are all four edges, expressed in the grid's own terms.
     Two pseudo-elements give four rules: ::before is the box the
     text column's two edges are drawn on, ::after the box the
     summary column's two edges are drawn on. The two boxes are
     flush, so all four are exactly 1px (--col-rule-w) wide.

     WHY PER SECTION AND NOT ONE FULL-HEIGHT RULE. The columns exist
     only inside .dsec. Between the sections the page is full-bleed
     prose -- .railhead section headings, .vs-text intro paragraphs,
     the whole Oral answers / Everything else / How-this-page-was-made
     blocks, the footer -- which spans both columns (audit D9: 1012px
     against a 562px track). A rule that ran the full scroll height
     would cut through every one of those. The guide therefore marks
     what it is a guide TO: it appears where the two columns exist and
     stops where they stop. That is also what makes it align by
     construction instead of by calculation.

     Drawn as an inset box-shadow rather than a border: .dsec is a
     grid container whose tracks are minmax(0, 1fr), and a border
     would shrink the content box under border-box sizing and pull
     the text column off the 562px the audit measured. A shadow
     paints outside the layout box and changes nothing.

     PAINT ORDER. The guides carry z-index:-1, so they paint below
     the in-flow content of the page: text is never tinted by a rule
     passing over it, and .sumcol (sticky, i.e. a positioned
     z-index:auto box) paints over its own column's left rule exactly
     where the summary card is -- so the accent border-left the card
     already has stays the visible edge there, and the hairline
     supplies the column edge in the whitespace above and below it,
     which is the gap audit D8 describes. Below the desktop
     breakpoint the whole block is skipped, so the phone and tablet
     layouts have no guides and are untouched. */
  .dsec::before,.dsec::after{content:"";pointer-events:none;position:relative;z-index:-1;
    align-self:stretch;justify-self:stretch}
  .dsec::before{grid-column:1;grid-row:1;
    box-shadow:inset var(--col-rule-w) 0 0 0 var(--col-rule),
      inset calc(-1 * var(--col-rule-w)) 0 0 0 var(--col-rule)}
  .dsec::after{grid-column:2;grid-row:1;
    box-shadow:inset var(--col-rule-w) 0 0 0 var(--col-rule),
      inset calc(-1 * var(--col-rule-w)) 0 0 0 var(--col-rule)}
}

:root{
  --ink:#14181d; --dim:#5c6773; --faint:#8b95a1; --line:#e3e7ec;
  --bg:#fbfbfa; --card:#ffffff; --accent:#1c6b4a; --accent-soft:#e8f2ec;
  --warm:#b4622a; --radius:14px;
  /* Height of the sticky top bar. Two sticky elements depend on it: the bar itself, and
     the per-section summary card, which pins BELOW the bar rather than under it.
     --sticky-gap is the air between them: at zero the card butts against the bar and
     reads as cramped, even though nothing is actually covered. */
  --topbar-h:60px; --sticky-gap:14px;
  /* THE COLUMN MODEL, in one place (docs/layout-audit/AUDIT.md §5). Every one of these was
     hard-coded at its use site -- 1060px at the wrap, 24px at its padding, 26rem at the
     summary track, 34px at the grid gap, 30px at the section margin. A vertical rule has to
     LAND on a column edge, and a number that only appears at its use site gives a rule
     nothing to be derived from. --col-sum is the summary track's MAXIMUM: minmax(300px,26rem)
     resolves to this at every desktop width, because the wrap is never narrow enough for the
     track to shrink below it.

     These four values were independently named twice, once by each implementation task that
     needed them (--content-max/--content-pad/--summary-col/--gutter vs this set). The values
     agreed exactly, so the merge is a rename, not a reconciliation; --col-* is the survivor
     because it is the scheme that also covers --col-section-gap and --col-rule, which have no
     counterpart in the other set. --content-max/--content-pad survive ONLY as the rail's
     coordinate-system aliases below, so every value is still stated exactly once. */
  --col-wrap:1060px; --col-pad:24px; --col-sum:26rem; --col-gap:34px;
  --col-section-gap:30px;
  /* The guides themselves. --col-rule resolves to the hairline colour already used by every
     horizontal rule on the page (.vs, .spk, .gapd, .runfull .ctx, .supp), so a vertical rule
     is the same weight and the same ink as the ones beside it and no new colour is
     introduced. There is one theme in this stylesheet (see the audit's note); this is the
     only token set it would have to be redefined in. */
  --col-rule:var(--line); --col-rule-w:1px;
  --content-max:var(--col-wrap); --content-pad:var(--col-pad);
  /* BOTTOM-PINNED CHROME. The rail centres itself in the band between the top bar and the
     progress bar, so it needs both heights to place itself rather than a guess. */
  --pbar-h:26px; --rail-margin:16px;
  /* THE RAIL, expressed in the CONTENT's coordinate system rather than the viewport's.
     The rail reads [pad][dot][gap][label][pad], the DOT FIRST, and the DOT COLUMN is the anchor:
     its left edge sits --rail-clear to the right of the content's right edge, and the label grows
     rightwards into the margin. (Before, the label came first against justify-content:flex-end,
     so each dot's x was whatever that tick's label happened to leave over.)
       --rail-clear   air between the content's right edge and the dot column
       --rail-dot     the dot column
       --rail-dot-gap the space between the dot and its label
       --rail-pad     the tick's horizontal padding (per side)
       --rail-outer   the rail's own margin from the viewport edge, so the label is never clipped
     Every width and offset below derives from these five. There is no second place to keep in
     step by hand -- which is the actual defect being fixed: the previous rail had a `right` of
     10px and a tick sized to its own label, and nothing tied either to the columns. */
  --rail-clear:10px; --rail-dot:8px; --rail-dot-gap:5px;
  --rail-pad:2px; --rail-outer:16px; --rail-track-pad:8px;
  /* Distance from the viewport's right edge to the CONTENT's right edge:
     (viewport - container)/2 + the container's own padding -- how far the wrap is inset from the
     edge, which on a phone is just its padding. This one value IS the page's right margin, and
     every right-edge element measures from it rather than being given its own offset.
     NOTE: 100vw includes a classic scrollbar, so on a platform that reserves one this resolves a
     few px wide -- which pulls right-edge chrome INWARDS, never out. */
  --rail-inset:calc((100vw - min(var(--content-max), 100vw)) / 2 + var(--content-pad));
  /* THE LABEL COLUMN, DERIVED FROM THE MARGIN, NOT SET. Solve the placement below for the widest
     label whose RIGHT edge still leaves --rail-outer before the viewport edge. The label's right
     edge is content_right + clear + label + dot + gap + pad, so the constraint is
         label <= inset - outer - clear - dot - gap - pad
     which is exactly what is written here. So the rail's clearance from the content is a CONSTANT
     --rail-clear at every width, instead of depending on a chosen offset that can only be right
     at one viewport.
     On a phone this resolves negative and clamps to 0, leaving the bare dot strip with no
     separate mobile rule. --rail-label-cap is a readability ceiling, not a layout one. */
  --rail-label-cap:180px;
  --rail-label-derive:clamp(0px, calc(var(--rail-inset) - var(--rail-outer) - var(--rail-clear)
                                      - var(--rail-dot) - var(--rail-dot-gap) - var(--rail-pad)),
                            var(--rail-label-cap));
  /* The name column is OFF until the margin can hold a readable one, and the switch is the
     min-width:1200px block below. --rail-label stays 0 here so the base rail is the dot strip --
     which is also what a phone renders, so there is one shape below that breakpoint rather than
     two. Keeping the derivation in a separate token is what lets the block turn the column on
     WITHOUT re-stating any geometry: --rail-tick-w, --rail-box-w and --rail-right all reference
     --rail-label, so setting it is enough (custom properties resolve lazily). */
  --rail-label:0px;
  /* THE BOX'S WIDTH, then the offset that places it. The width is the label plus its chrome,
     and with the label off it is the dot column plus the tick's padding -- 12px, not the base
     rule's 24px touch minimum. That minimum is a PHONE constraint: on a phone the rail is the
     only navigation and a thumb has to hit it, so the tick keeps a 24px floor there. On desktop
     the tick's own ::before still widens the hit area by 8px each way (see the base rule), so a
     12px visible tick is a 28px target with a mouse -- and holding to 24px would push the rail
     off the viewport at 1024, where the entire margin beside the content is the wrap's 24px of
     padding. So: the floor is dropped on desktop and the clearance below becomes exact. */
  --rail-tick-min:calc(var(--rail-dot) + var(--rail-pad) * 2);
  /* With the label OFF the tick holds only the dot, so its width is the dot plus the tick's
     padding -- the dot-gap has nothing to separate and must NOT be added. It used to be, which
     made the box 5px wider than its own contents at 1024px; the clearance equation then had to
     give 5px back out of a margin that is only the wrap's 24px of padding, so the dot measured
     9px of clearance instead of 10. The min-width:1200px block redefines this with the gap once
     there is a second item for the gap to separate. */
  --rail-tick-w:var(--rail-tick-min);
  --rail-box-w:var(--rail-tick-w);
  /* The clearance equation solved for `right`, so the dot column lands exactly --rail-clear past
     the content by construction. Width and offset share every token, so they cannot drift. */
  --rail-right:max(0px, calc(var(--rail-inset) - var(--rail-clear) + var(--rail-pad)
                             - var(--rail-box-w)));
  /* Z-INDEX SCALE, in one place and ascending. Before this the values were literals at four use
     sites: rail 70, bubble 90, rail-go 91 -- all three ABOVE the progress bar (44) and the resume
     toast (45), so a preview could paint over the page's own bottom chrome. The rail belongs to
     the content, so on DESKTOP it sits above the page but below every piece of fixed chrome.
     THE BASE VALUES ARE THE PHONE'S. The conflict being fixed is between the rail and the pbar
     and toast, and on a phone the pbar is display:none -- so retuning the rail's layer there
     would change mobile stacking for no benefit. Keeping the original numbers at base and
     retuning in the desktop block means the phone's computed styles are bit-for-bit what they
     were, which is what "mobile unchanged" has to mean. */
  --z-topbar:20; --z-rail:70; --z-rail-bubble:90; --z-rail-go:91;
  --z-totop:40; --z-pbar:44; --z-resume:45;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:var(--col-wrap);margin:0 auto;padding:0 var(--col-pad)}
a{color:inherit;text-decoration:none}
h1,h2,h3{line-height:1.2;margin:0}

/* top bar */
.top{border-bottom:1px solid var(--line);background:rgba(251,251,250,.86);
  backdrop-filter:blur(10px);position:sticky;top:0;z-index:var(--z-topbar)}
.top .wrap{display:flex;align-items:center;justify-content:space-between;
  height:var(--topbar-h)}
.logo{font-weight:700;font-size:18px;letter-spacing:-.01em;display:flex;gap:8px;align-items:center}
.veg{font-size:19px}
.top nav{display:flex;gap:22px;font-size:14px;color:var(--dim)}
.top nav a:hover,.top nav a.on{color:var(--ink)}
.top nav a.on{font-weight:650}

/* lede */
.lede{padding:56px 0 34px;border-bottom:1px solid var(--line)}
.kicker{font:600 12px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;
  color:var(--accent);margin:0 0 16px}
.lede h1{font-size:clamp(40px,7.5vw,76px);letter-spacing:-.032em;font-weight:800}
.dek{font-size:19px;color:var(--dim);margin:18px 0 0;max-width:56ch}
/* The slogan line: how long Parliament sat, how long this page takes. One line, no
   labels -- the owner's ask. Base stylesheet IS the 390px layout. */
.slogan{font-size:15px;color:var(--dim);margin:16px 0 0;line-height:1.5;
  /* clear the rail's floating label, which is absolutely positioned over this column */
  padding-right:var(--rail-gutter,0px)}
.slogan b{color:var(--ink);font-weight:700}
/* The link to the official recording. Sits under the slogan, modest -- it is an escape
   hatch for a reader who wants the primary source, not a call to action. */
.srcvid{margin:12px 0 0;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;
  font-size:14px;padding-right:var(--rail-gutter,0px)}
.srcvid a{color:var(--accent);font-weight:600;text-decoration:none;
  border-bottom:1px solid rgba(22,120,80,.3)}
.srcvid a:hover{border-bottom-color:var(--accent)}
.srcvid .hint{color:var(--meta,#6b7280);font-size:12.5px}
/* The "no recording" case. Stated plainly rather than hidden -- the gap is real and the
   reader is told why, instead of the line silently vanishing. Muted, not alarming. The two
   halves are joined by a middot so they read as ONE statement rather than two stray lines. */
.srcvid .novid{color:var(--dim);font-weight:500}
.srcvid.is-none{padding-right:var(--rail-gutter,0px)}
/* no pseudo-element separator: the text is one sentence now, so a generated middot would
   only be able to land at a line start and read as an orphan bullet. */
.dek b{color:var(--ink)}
.span-note{font:500 12.5px var(--mono);color:var(--faint);margin:14px 0 0}

/* infographic grid */
.info{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:26px 0 8px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:26px 26px 22px;box-shadow:0 1px 2px rgba(20,24,29,.03)}
.span2{grid-column:span 2}
.panel h2{font-size:15px;font-weight:700;letter-spacing:-.005em;
  padding-bottom:14px;margin-bottom:18px;border-bottom:1px solid var(--line)}
.pnote{font-size:13.5px;color:var(--faint);margin:-8px 0 18px;max-width:70ch}

/* bars */
.bars .row{display:grid;grid-template-columns:132px 1fr 72px;align-items:center;
  gap:14px;margin-bottom:13px;font-size:14px}
/* Speaker list fills a full-width panel, so lay it out in two columns.
   Otherwise the names truncate ("Mr Kenneth Tiong Boon K...") and leave a
   large empty gap beside a half-width panel. */
.bars.people{display:grid;grid-template-columns:1fr 1fr;gap:0 30px}
.bars.people .row{grid-template-columns:1fr 92px 62px}
.rk{color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rb{background:#f0f3f6;height:9px;border-radius:5px;overflow:hidden}
.rb i{display:block;height:100%;background:var(--accent);border-radius:5px}
.bars.people .rb i{background:var(--warm)}
.rv{text-align:right;font:600 12.5px var(--mono);color:var(--faint)}

/* question -> response mapping */
/* Oral answers get their own full-width section and render in full -- no
   collapse, no expand control. Only the verbatim quote chips and the
   "Everything else" group cards are collapsible. */
.oral{padding:52px 0 10px;border-top:1px solid var(--line);margin-top:40px}
/* A jump list, not a collapse: 16 full answers is a long scroll, and this keeps
   them navigable without hiding a single word. */
.qajump{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:18px 22px 16px;margin-bottom:26px}
.qajump ol{list-style:none;margin:0;padding:0;counter-reset:q}
.qajump li{counter-increment:q;display:flex;gap:12px;align-items:baseline;
  padding:7px 0;border-bottom:1px solid var(--line);font-size:14.5px}
.qajump li:last-child{border-bottom:0}
.qajump li::before{content:counter(q);font:600 11px var(--mono);color:var(--faint);
  min-width:18px;text-align:right;flex:none}
.qajump a:hover{color:var(--accent)}
.qajump .qw{font:500 11.5px var(--mono);color:var(--faint);margin-left:auto;
  white-space:nowrap;flex:none}
.maplist{list-style:none;margin:0;padding:0}
/* Oral answer cards are disclosures too -- default open. */
.mapwrap{border-bottom:1px solid var(--line)}
.mapwrap:last-child{border-bottom:0}
details.map{padding:0}
.mapsum{list-style:none;cursor:pointer;padding:16px 0;position:relative;
  padding-right:34px}
.mapsum::-webkit-details-marker{display:none}
.mapsum:hover .mapt{color:var(--accent)}
.mapsum::after{content:"▾";position:absolute;right:6px;top:20px;color:var(--faint);
  font-size:12px}
details.map:not([open])>.mapsum::after{content:"▸"}
.mapsum .mapt{margin-bottom:0}
.maphint{display:block;font:600 11.5px var(--mono);color:var(--faint);margin-top:7px}
.qabody{padding-bottom:20px}
/* ---- oral answer: summary first, transcript as evidence ---- */
.oral-sum{margin-bottom:12px}
.orow{display:grid;grid-template-columns:74px 1fr;gap:10px;padding:9px 0;
  border-top:1px solid var(--line)}
.orow:first-child{border-top:0;padding-top:2px}
.olabel{font:700 9.5px var(--mono);letter-spacing:.09em;text-transform:uppercase;
  color:var(--faint);padding-top:2px}
.orow.resp .olabel{color:var(--accent)}
.orow p{margin:0;font-size:15px;line-height:1.55;color:#232a31}
.oralkp{list-style:none;margin:6px 0 0;padding:0}
.oralkp li{display:grid;grid-template-columns:74px 1fr;gap:10px;padding:6px 0;
  border-top:1px dashed var(--line)}
.oralkp .pt{grid-column:2;grid-row:1;font-size:14px;line-height:1.5;color:#2c343b}
.oralkp .pw{grid-column:1;grid-row:1;font:600 10.5px var(--mono);color:var(--faint);
  line-height:1.4;padding-top:1px}
.oralsupp{margin:14px 0 0;padding:11px 13px;background:#f8faf9;border:1px solid var(--line);
  border-radius:9px;font-size:13.5px;line-height:1.5;color:var(--dim)}
.oralsupp b{display:block;font:700 9.5px var(--mono);letter-spacing:.09em;
  text-transform:uppercase;color:var(--faint);margin-bottom:5px}
details.transcript{margin-top:14px;border-top:1px dashed var(--line);padding-top:11px}
details.transcript>summary{cursor:pointer;font:600 12.5px var(--mono);color:var(--accent);
  list-style:none;min-height:44px;display:flex;align-items:center}
details.transcript>summary::-webkit-details-marker{display:none}
details.transcript>summary::before{content:"▸ ";}
details.transcript[open]>summary::before{content:"▾ "}
details.transcript[open]>summary{border-bottom:1px solid var(--line);margin-bottom:14px}
.map{padding:20px 0;border-bottom:1px solid var(--line)}
.map:last-child{border-bottom:0}
.map.hidden{display:none}
.qa-more{list-style:none}
.qa-more.hidden{display:none}
.mapt{display:block;font-size:16px;font-weight:700;letter-spacing:-.012em;
  margin-bottom:16px;color:var(--ink)}
.mapt:hover{color:var(--accent)}
.qa-pair{display:grid;grid-template-columns:1fr 26px 1fr;gap:0;align-items:stretch}
.qa-side{background:#f8faf9;border:1px solid var(--line);border-radius:11px;
  padding:14px 16px}
.qa-side.resp{background:var(--accent-soft);border-color:#cfe3d8}
.qa-role{display:block;font:700 9.5px var(--mono);letter-spacing:.1em;
  text-transform:uppercase;color:var(--faint);margin-bottom:5px}
.qa-side.resp .qa-role{color:var(--accent)}
.qa-who{display:block;font:700 12.5px var(--mono);color:var(--dim);
  margin-bottom:9px;letter-spacing:.01em}
.qa-side p{margin:0;font-size:14.5px;line-height:1.55;color:#232a31}
/* the connector between the two sides */
.qa-link{position:relative}
.qa-link::before{content:"";position:absolute;left:0;right:0;top:50%;height:1px;
  background:var(--line)}
.qa-link::after{content:"";position:absolute;left:50%;top:50%;width:7px;height:7px;
  margin:-4px 0 0 -4px;border-radius:50%;background:var(--accent)}
.lt{display:inline-block;margin-left:7px;font:700 9px var(--mono);letter-spacing:.07em;
  text-transform:uppercase;background:#eef1f4;color:var(--dim);
  padding:2px 5px;border-radius:4px;vertical-align:middle}
.nt{display:inline-block;margin-left:7px;font:600 10.5px var(--mono);
  color:var(--warm);background:#fdf1e8;padding:2px 6px;border-radius:4px}
/* supplementary exchange */
.supp-wrap{margin-top:14px;border-top:1px dashed var(--line);padding-top:12px}
.supp-wrap summary{cursor:pointer;font:600 12.5px var(--mono);color:var(--accent);
  list-style:none}
.supp-wrap summary::-webkit-details-marker{display:none}
.supp-wrap summary::before{content:"▸ ";display:inline-block}
.supp-wrap[open] summary::before{content:"▾ "}
.supp{display:grid;grid-template-columns:132px 1fr;gap:14px;padding:9px 0 9px 10px;
  border-left:2px solid var(--line);margin-top:9px}
.supp.sq{border-left-color:var(--warm)}
.supp.sresp{border-left-color:var(--accent)}
.sw{font:700 11.5px var(--mono);color:var(--dim)}
.st{font-size:13.5px;color:#2c343b;line-height:1.5}
.srclink{display:inline-block;margin-top:12px;font:600 12px var(--mono);color:var(--faint)}
.srclink:hover{color:var(--accent)}
.reveal-maps{display:block;width:100%;margin-top:18px;padding:12px;
  background:none;border:1px dashed var(--line);border-radius:10px;
  color:var(--accent);font:600 13px inherit;cursor:pointer}
.reveal-maps:hover{background:var(--card)}

/* numbers */
.nums{list-style:none;margin:0;padding:0;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(268px,1fr));gap:14px}
.num{border:1px solid var(--line);border-radius:11px;padding:15px 16px;background:#fdfefd}
.num b{display:block;font-size:26px;letter-spacing:-.03em;color:var(--accent);
  margin-bottom:7px}
.num span{display:block;font-size:12.8px;color:var(--dim);line-height:1.5}
.sig{display:inline-block;margin-top:11px;font:600 11px var(--mono);
  color:var(--faint);border-top:1px solid var(--line);padding-top:8px}
.empty{color:var(--faint);font-size:14px}

/* ---- substance layer: policy briefs ---- */
.substance{padding:34px 0 10px}
/* Cards are now disclosures so a repeat visitor can collapse what they have
   already read. Default open: the page must be readable without JS. */
.briefwrap{margin-bottom:18px}
details.brief{background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);box-shadow:0 1px 2px rgba(20,24,29,.03);overflow:hidden}
.briefsum{list-style:none;cursor:pointer;padding:20px 24px;position:relative;
  padding-right:52px}
.briefsum::-webkit-details-marker{display:none}
.briefsum:hover{background:#f8faf9}
/* Caret sized to the TITLE line only. A caret positioned against the summary's
   box lands halfway down a wrapped multi-line title, which reads as broken on
   mobile where titles wrap 3-4 lines. */
.bcaret{position:absolute;right:22px;top:24px;color:var(--faint);font-size:12px;
  line-height:1}
.bcaret::before{content:"▾"}
details.brief:not([open])>.briefsum .bcaret::before{content:"▸"}
.btitle{display:block;font-size:23px;letter-spacing:-.022em;font-weight:750;
  margin-bottom:8px;padding-right:8px}
.bhint{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;
  font:600 11.5px var(--mono);color:var(--faint)}
.bbody{padding:0 28px 22px}
details.brief[open]>.briefsum{border-bottom:1px solid var(--line)}
.bhead{padding-bottom:14px;margin-bottom:16px}
.brief h3{font-size:23px;letter-spacing:-.022em;font-weight:750;margin-bottom:10px}
.bmeta{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;
  font:600 11.5px var(--mono);color:var(--faint)}
.stage{background:var(--accent);color:#fff;padding:3px 8px;border-radius:5px;
  letter-spacing:.05em;text-transform:uppercase;font-size:10px}
.bdate{color:var(--dim)}
.bsrc{color:var(--faint)}
.whatis{font-size:16.5px;line-height:1.6;margin:0 0 12px;color:#232a31}
.why{font-size:15px;line-height:1.62;margin:0 0 20px;padding:14px 16px;
  background:var(--accent-soft);border-radius:10px;color:#1e3a2c}
.points{list-style:none;margin:0;padding:0}
/* A speaker turn: the name appears once, its points hang beneath it. */
.spk{list-style:none;border-top:1px solid var(--line);padding:14px 0 2px}
.spk:first-child{border-top:0;padding-top:4px}
.spkwho{margin:0 0 9px;font:700 12px var(--mono);color:var(--accent);
  letter-spacing:.02em;line-height:1.35}
.spk .points{margin:0}
.spk .points .pt{padding:9px 0 9px 15px;border-top:1px dashed var(--line);
  border-left:2px solid var(--accent-soft);margin-left:1px}
.spk .points .pt:first-child{border-top:0;padding-top:2px}
.pttext{margin:0 0 8px;font-size:15.5px;line-height:1.58}
.pt blockquote{margin:0 0 8px;padding-left:14px;border-left:2px solid var(--line);
  font-size:14px;line-height:1.55;color:var(--dim);font-style:italic}
/* Verbatim source is collapsed by default: the point is the summary, the quote is
   the receipt. Expanding shows the exact Hansard line for that claim. */
.qsum{margin:0 0 9px}
.qsum summary{display:inline-flex;align-items:center;gap:7px;cursor:pointer;
  list-style:none;padding:4px 10px 4px 8px;border:1px solid var(--line);
  border-radius:999px;background:#fff;color:var(--faint);
  font:600 11px var(--mono);letter-spacing:.03em;transition:.12s}
.qsum summary::-webkit-details-marker{display:none}
.qsum summary::before{content:"▸";font-size:10px;line-height:1;color:var(--faint)}
.qsum[open] summary::before{content:"▾"}
.qsum summary:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.qsum summary:hover::before{color:var(--accent)}
.qsum[open] summary{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.qsum .qlabel{color:inherit}
.qsum .qw{color:var(--faint);font-weight:500}
/* the rest of a long brief, one disclosure away */
.morepts{margin-top:14px;border-top:1px dashed var(--line);padding-top:12px}
.morepts>summary{cursor:pointer;font:600 12.5px var(--mono);color:var(--accent);
  list-style:none}
.morepts>summary::-webkit-details-marker{display:none}
.morepts>summary::before{content:"▸ ";}
.morepts[open]>summary::before{content:"▾ "}
.morepts>.points{margin-top:12px}
.qsum summary:hover .qw,.qsum[open] summary .qw{color:var(--accent);opacity:.75}
.qsum blockquote{margin:9px 0 0;padding:10px 14px;border-left:2px solid var(--accent);
  background:#fafbfa;border-radius:0 8px 8px 0;
  font-size:14px;line-height:1.6;color:var(--dim);font-style:italic}
@media print{.qsum blockquote{display:block}}
@media (prefers-reduced-motion:reduce){.qsum summary{transition:none}}
.ptwho{display:block;font:600 11.5px var(--mono);color:var(--faint);
  letter-spacing:.02em}
.next{margin:18px 0 0;padding:14px 16px;background:#f8faf9;border:1px solid var(--line);
  border-radius:10px;font-size:14.5px;line-height:1.55}
.next b{display:block;font:700 10px var(--mono);letter-spacing:.1em;
  text-transform:uppercase;color:var(--accent);margin-bottom:6px}
.ns{margin-top:16px;border-top:1px dashed var(--line);padding-top:12px}
.ns summary{cursor:pointer;font:600 13px inherit;color:var(--accent);list-style:none}
.ns summary::-webkit-details-marker{display:none}
.ns summary::before{content:"▸ ";}
.ns[open] summary::before{content:"▾ "}
.nnote{font-size:12.5px;color:var(--faint);margin:10px 0 8px;max-width:70ch}
.ns ul{margin:0;padding-left:20px}
.ns li{font-size:14px;color:var(--dim);margin-bottom:7px;line-height:1.5}

/* procedural business, compactly listed rather than carded */
.proc{margin-top:20px;border-top:1px dashed var(--line);padding-top:14px}
.proc summary{cursor:pointer;font:600 13px inherit;color:var(--faint);list-style:none}
.proc summary::-webkit-details-marker{display:none}
.proc summary::before{content:"▸ ";}
.proc[open] summary::before{content:"▾ "}
.proc ul{list-style:none;margin:12px 0 0;padding:0}
.proc li{padding:9px 0;border-bottom:1px solid var(--line);display:grid;
  grid-template-columns:104px 1fr;gap:3px 14px;align-items:baseline}
.proc li:last-child{border-bottom:0}
.pstage{font:700 10px var(--mono);letter-spacing:.05em;text-transform:uppercase;
  color:var(--faint)}
.pttl{font-size:14.5px;font-weight:600}
.pnote2{grid-column:2;font-size:13px;color:var(--dim);line-height:1.5}

/* sitting statistics, demoted to an appendix */
.stats-note{margin:44px 0 0;border-top:1px solid var(--line);padding-top:22px}
.stats-note summary{cursor:pointer;font:600 13px var(--mono);color:var(--faint);
  list-style:none}
.stats-note summary::-webkit-details-marker{display:none}
.stats-note summary::before{content:"▸ ";}
.stats-note[open] summary::before{content:"▾ "}
.statgrid{display:grid;grid-template-columns:1fr 1fr;gap:26px;margin-top:20px}
.statgrid h4{font-size:13px;font-weight:700;margin-bottom:14px;color:var(--dim)}
.statnote{font:500 12px var(--mono);color:var(--faint);margin-top:18px}

/* sections */
.sec-head{margin:0 0 22px}
.sec-head h2{font-size:26px;letter-spacing:-.022em;font-weight:750}
.sub{color:var(--faint);font-size:14px;margin:9px 0 0}

/* everything else -- collapsible group cards */
.everything{padding:56px 0 10px;border-top:1px solid var(--line);margin-top:36px}
/* A Budget sitting carries 200+ items. Collapsed by default so the page opens
   as a handful of group headings instead of a wall of text. */
details.grp{background:var(--card);border:1px solid var(--line);
  border-radius:var(--radius);margin-bottom:10px;overflow:hidden}
details.grp>summary{display:flex;align-items:baseline;gap:14px;padding:16px 44px 16px 20px;
  cursor:pointer;list-style:none;position:relative}
details.grp>summary::-webkit-details-marker{display:none}
details.grp>summary:hover{background:#f8faf9}
details.grp>summary::after{content:"▸";position:absolute;right:18px;top:50%;
  transform:translateY(-50%);color:var(--faint);font-size:12px}
details.grp[open]>summary::after{content:"▾"}
details.grp[open]>summary{border-bottom:1px solid var(--line)}
.gtitle{font-size:15.5px;font-weight:700;letter-spacing:-.01em}
.gcount{font:500 11.5px var(--mono);color:var(--faint)}
.glist{list-style:none;margin:0;padding:6px 0}
.glist li{border-bottom:1px solid var(--line)}
.glist li:last-child{border-bottom:0}
.glist a{display:block;padding:11px 20px;font-size:14.5px;line-height:1.45}
.glist a:hover{background:#f8faf9;color:var(--accent)}

/* method + footer */
.method{margin:56px 0 0;padding:28px 30px;background:var(--accent-soft);
  border-radius:var(--radius);border:1px solid #cfe3d8}
.method h2{font-size:17px;margin-bottom:14px}
.method p{font-size:14px;color:var(--dim);margin:0 0 12px}
.method ul{margin:0;padding-left:20px;font-size:13.5px;color:var(--dim)}
.method li{margin-bottom:6px}
.method b{color:var(--ink)}
.method .warn{color:var(--warm)}
footer{margin-top:40px;padding:26px 0 60px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--faint)}

/* archive */
.arch{padding:34px 0 10px}
/* Year groups: a year is the unit we backfill in and the unit a reader scans by. */
.yr{border:1px solid var(--line);border-radius:var(--radius);background:var(--card);
  margin-bottom:14px;overflow:hidden}
.yr>summary{cursor:pointer;list-style:none;display:flex;align-items:baseline;
  gap:12px;padding:14px 18px;min-height:44px;background:#f7f9f8}
.yr>summary::-webkit-details-marker{display:none}
.yr>summary::before{content:"▸";color:var(--accent);font-size:11px;flex:none}
.yr[open]>summary::before{content:"▾"}
.yr[open]>summary{border-bottom:1px solid var(--line)}
.yr:hover>summary{background:var(--accent-soft)}
.yrnum{font:750 17px var(--mono);letter-spacing:-.01em;color:var(--ink)}
.yrmeta{font-size:12.5px;color:var(--faint)}
.yr .slist{padding:0 18px}
.yr .srow:last-child{border-bottom:0}
.slist{list-style:none;margin:0;padding:0}
.srow{border-bottom:1px solid var(--line)}
.srow a{display:grid;grid-template-columns:190px 1fr;gap:8px 22px;
  padding:20px 4px;align-items:baseline}
.srow a:hover{background:var(--card)}
.sdate{font-weight:700;font-size:15px;display:flex;flex-direction:column;gap:3px}
.sdow{font:500 11px var(--mono);color:var(--faint);text-transform:uppercase;
  letter-spacing:.08em}
.slead{font-size:15.5px;color:var(--ink);line-height:1.4}
.sstats{grid-column:2;font:500 11.5px var(--mono);color:var(--faint)}

@media (max-width:760px){
  .info{grid-template-columns:1fr}
  .span2{grid-column:span 1}
  .bars .row{grid-template-columns:104px 1fr 58px}
  .bars.people{grid-template-columns:1fr}
  .bars.people .row{grid-template-columns:1fr 72px 52px}
  /* the question/response connector only reads left-to-right; stack on mobile */
  .qa-pair{grid-template-columns:1fr;gap:10px}
  .qa-link{display:none}
  /* keep the speaker indent shallow on narrow screens */
  .spk .points .pt{padding-left:11px}
  .spkwho{font-size:11.5px}
  .supp{grid-template-columns:1fr;gap:4px}
  .srow a{grid-template-columns:1fr;gap:6px}
  .sstats{grid-column:1}
}

/* ===================== mobile-first navigation ===================== */

/* In-page jumps must clear the sticky header, or the target lands hidden
   underneath it. Kept 8px less than the rail's active-offset so a just-jumped
   heading reads active immediately. */
[id],.has-section-anchor{scroll-margin-top:calc(var(--sticky-h, 61px) + 12px)}

/* ---- section rail (ported from sgfamily.life, re-coloured to Parsnips) ----
 A "you are here" map for a page that is 26,000px tall on a phone. Mobile
 only: on a wide screen the page is short enough and a right-edge rail would
 simply cover content.

 The rail stays permanently visible and the right gutter below reserves room
 for it. That is deliberate: an always-present position indicator was chosen
 over a collapsible drawer precisely because the indicator is the point.

 Tick sizing is a computed trade-off. The 5 Aug page has 21 headings; at a full
 44px touch target that is 924px of ticks, taller than a phone screen, so the
 rail would overflow. JS sizes ticks and gap to the largest that fits, and
 enlarges the HIT AREA independently of the visible dot, so the target does not
 have to be sacrificed to fit the column. */
.section-rail{display:block;position:fixed;top:50%;right:6px;transform:translateY(-50%);
  z-index:var(--z-rail);padding:10px 0;pointer-events:none;max-width:calc(100vw - 12px)}

.section-rail{right:6px;transform:translateY(-50%)}
.section-rail.is-dormant{display:none}
.section-rail-track{position:relative;display:flex;flex-direction:column;
    align-items:center;gap:var(--rail-gap,14px);padding:2px var(--rail-track-pad,8px);
    pointer-events:auto}
  /* Ticks must not shrink. They are flex items in a fixed-height column, so the
     default flex-shrink:1 silently squeezed every computed height (a 40px
     request rendered at 25px). The JS sizes them to fit, so shrinking is not
     wanted. */
.section-rail-tick{flex:none}
  /* Gesture blockers. Without these a long press could not complete: the tick
     inherits touch-action:auto, so the browser claims a vertical thumb drift as
     a scroll and fires pointercancel, and iOS would raise its text-selection
     callout on the label inside the button. Both competed with the gesture.
     touch-action:none is applied to the TRACK only, so a drag that starts on the
     rail is owned by the rail (scrubbing) while the rest of the page keeps
     scrolling normally. This deliberately makes the rail strip a dead zone for
     page scrolling -- acceptable, and in fact intended, because that strip is
     already reserved as the rail gutter. */
.section-rail-track{touch-action:none}
.section-rail,.section-rail-tick,.section-rail-bubble,.section-rail-go{
    touch-action:manipulation}
.section-rail-tick{-webkit-touch-callout:none;user-select:none;
    -webkit-user-select:none;-webkit-tap-highlight-color:transparent}
.section-rail-tick *{user-select:none;-webkit-user-select:none}
  /* While scrubbing, the finger owns the rail: suppress tick hover states so the
     highlight reflects the drag position, not a stray hover. */
.section-rail.is-scrubbing{cursor:ns-resize}
.section-rail.is-scrubbing .section-rail-tick{transition:none}
  /* progress fill runs behind the ticks.
     left:50% is correct for THIS layer: on a phone the tick holds only the dot (the name is
     display:none below), so the track's centre IS the dot column. On desktop the tick grows
     a label to the LEFT of the dot, which moves the track's centre off the dot column -- the
     desktop block below re-derives this from the dot column instead. Measured before the
     fix: fill x 1157.5 against the nearest dot column at 1181, i.e. 23.3px adrift. */
.section-rail-fill{position:absolute;top:12px;bottom:12px;left:50%;width:2px;
    margin-left:-1px;border-radius:2px;background:#e2e8e3;overflow:hidden}
.section-rail-fill::after{content:'';position:absolute;inset:0 0 auto 0;
    height:calc(var(--rail-progress,0) * 100%);
    background:linear-gradient(180deg,var(--accent),#2f8f66);
    transition:height .25s ease}
  /* Ticks are laid out as a row: the dot, then its identity (a name for the
     section-level ticks, a number for the oral-answer ones). The rail answers
     "which section is this?" at rest rather than only after a gesture. */
  /* WHY THE DOT COLUMN IS JAGGED, AND THE FIX. The tick is justify-content:flex-end with
     the name in flow, so the dot's x was a function of how long that tick's label happened
     to be -- measured, the 9 dots spanned 75px (1254,1181,1256,1247,1256,1256,1203,1212,
     1235). Nothing anchored them. Giving the name a FIXED width (desktop block below) makes
     flex-end pin the dot's right edge to the tick's right edge for every tick, whatever its
     label, so all dots share one x -- and that x is the rail box's right edge, which is what
     --rail-right positions. No order/margin trickery is needed. */
.section-rail-tick{position:relative;appearance:none;border:0;background:transparent;
    padding:0 2px;width:auto;min-width:24px;height:var(--rail-tick-h,22px);
    display:flex;align-items:center;justify-content:flex-end;
    gap:var(--rail-dot-gap,5px);cursor:pointer}
.section-rail-tick-mark{display:block;width:6px;height:6px;border-radius:50%;
    background:#c4cfc7;box-shadow:0 0 0 3px rgba(251,251,250,.9);
    transition:width .2s ease,height .2s ease,background .2s ease,transform .2s ease}
  /* Expand the HIT AREA independently of the visible dot. The column can only be
     ~27px tall per tick or 21 ticks overflow the screen, but the tappable box can
     still be widened horizontally and padded vertically via a pseudo-element, so
     the target approaches 44px without changing what is drawn. */
.section-rail-tick::before{content:'';position:absolute;left:-8px;right:-8px;
    top:-8px;bottom:-8px}
.section-rail-tick.level-2 .section-rail-tick-mark{width:8px;height:8px}
  /* Always-visible name for section ticks, and the number for oral-answer ticks.
     Both sit to the LEFT of the dot so the dot column stays aligned. */
.section-rail-name{order:-1;font:600 9.5px/1.15 var(--mono);color:var(--dim);
    text-align:right;max-width:74px;white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis;background:rgba(251,251,250,.82);border-radius:5px;
    padding:2px 4px;
    /* PHONE: only the ACTIVE tick shows its name. All of them at once made the rail 107px
       wide over content with 14px of padding -- a 103px overlap. The reader still learns what
       any tick is, by reading the active one.
       Width is capped to the gutter the rail actually has, so the label cannot reach back over
       the text; it ellipsizes instead. Measured: the gutter is 46px, so 40px of label plus its
       padding fits with nothing overlapping. */
    /* HIDDEN on a phone: the rail's column is ~62px and a readable name needs ~96px, so an
       always-on label overhangs the text column and paints over it. The transient pill
       (below) names the active section on demand instead. */
    display:none}
.section-rail-num{order:-1;font:600 9px/1 var(--mono);color:var(--faint);
    min-width:11px;text-align:right}
.section-rail-tick.level-2 .section-rail-name{color:var(--ink)}
.section-rail-tick.is-active .section-rail-name{color:var(--accent)}
.section-rail-tick.is-pending .section-rail-name,
.section-rail-tick.is-pending .section-rail-num{color:var(--accent)}
.section-rail-tick.is-active .section-rail-tick-mark{background:var(--accent);
    transform:scale(1.35)}
  /* A tick waiting to confirm: first tap names the section, second tap jumps. */
.section-rail-tick.is-pending .section-rail-tick-mark{background:var(--accent);
    transform:scale(1.25)}
.section-rail-tick.is-pending .section-rail-bubble{opacity:1;
    transform:translateY(-50%) scale(1)}
  /* always-visible label for the section currently on screen.
     No fixed position can avoid ALL content on a page where every pixel between
     the header and footer scrolls: centred on the rail it landed on the heading
     it named, above the rail it landed on the header nav. So it is gated on
     active scrolling instead (see the JS) -- present while the reader is moving
     between sections, gone ~700ms after they stop, when they are reading. */
.section-rail-pill{position:absolute;top:50%;right:0;
    transform:translateY(-50%) translateX(6px);max-width:min(190px,52vw);
    padding:5px 10px;border-radius:999px;background:rgba(20,24,29,.94);color:#fff;
    font-size:10.5px;font-weight:700;line-height:1.25;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis;opacity:0;
    transition:opacity .2s ease,transform .2s ease;pointer-events:none;z-index:2}
.section-rail-pill.is-visible{opacity:1;transform:translateY(-50%) translateX(0)}
  /* Preview bubble: the full name, shown while scrubbing or pending.
     It must be WIDER than the rail column: constrained to the track it collapsed
     to 58px and wrapped into a tall sliver, and because the track is the only
     part of the rail with pointer-events, the bubble was painted behind page
     content. Fixed to the viewport's right edge instead, above everything. */
.section-rail-bubble{position:fixed;top:0;left:auto;right:10px;
    transform:translateY(-50%) scale(.96);
    max-width:min(300px,78vw);width:max-content;
    padding:7px 11px;border-radius:10px;
    background:rgba(20,24,29,.97);color:#fff;
    border:1px solid rgba(255,255,255,.14);
    box-shadow:0 12px 24px -14px rgba(20,24,29,.7);
    font-size:12px;font-weight:700;line-height:1.3;white-space:normal;
    opacity:0;pointer-events:none;z-index:var(--z-rail-bubble);
    transition:opacity .15s ease,transform .15s ease}
.section-rail-tick.is-pending .section-rail-bubble{opacity:1;
    transform:translateY(-50%) scale(1)}
  /* The explicit confirm affordance, visible while pending. */
.section-rail-go{position:fixed;top:0;left:auto;right:10px;
    font:700 10px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;
    color:#fff;background:var(--accent);border-radius:6px;padding:6px 9px;
    opacity:0;pointer-events:none;z-index:var(--z-rail-go);
    transition:opacity .15s ease}
.section-rail-tick.is-pending .section-rail-go{opacity:1}


@media (prefers-reduced-motion:reduce){
  .section-rail-fill::after,.section-rail-tick-mark,.section-rail-pill,
  .section-rail-bubble{transition:none}
}

/* Back to top: a long page needs one, and it doubles as "you are deep in". */
.totop{position:fixed;right:16px;bottom:16px;z-index:var(--z-totop);width:44px;height:44px;
  border-radius:50%;border:1px solid var(--line);background:var(--card);
  color:var(--accent);font-size:17px;line-height:1;cursor:pointer;
  box-shadow:0 2px 10px rgba(20,24,29,.12);opacity:0;visibility:hidden;
  transition:opacity .2s,visibility .2s}
.totop.on{opacity:1;visibility:visible}
@media (prefers-reduced-motion:reduce){.totop{transition:none}}
/* keep the rail and the back-to-top button from crowding each other */
@media (max-width:760px){.totop{bottom:16px;right:14px}}

/* "Resume where you left off" -- offered, never forced. Auto-dismisses. */
.resume{position:fixed;left:16px;right:16px;bottom:16px;z-index:var(--z-resume);
  display:flex;align-items:center;gap:10px;padding:12px 14px;
  background:var(--ink);color:#fff;border-radius:12px;font-size:13.5px;
  box-shadow:0 8px 26px rgba(20,24,29,.28)}
.resume span{flex:1;line-height:1.35}
.resume button{font:600 13px inherit;border-radius:8px;cursor:pointer;
  min-height:44px;padding:0 14px}
.resume .rgo{background:var(--accent);color:#fff;border:0}
.resume .rno{background:none;border:0;color:#aeb8c2;font-size:18px;padding:0 6px}
@media (min-width:761px){.resume{left:auto;right:16px;max-width:380px}}

/* The section rail, on desktop: anchored to the CONTENT's right edge, not the viewport's.
   The rail used to sit at right:10px of the viewport while the content is a centred
   1060px box, so the two coordinate systems crossed around 1500px: below that the rail
   painted over the summary column (209px of cover at 1024, 99px at 1280, 19px at 1440) and
   above it, drifted into empty margin (221px clear at 1920, 541px at 2560). right is now
   --rail-right, derived from the container, so the rail's distance from the content is
   CONSTANT -- 10px -- at every width. Level-2 ticks are small, level-3 largest. */
@media (min-width:761px){
  /* THE DESKTOP LAYER RETUNE. The conflict is between the rail and the fixed BOTTOM chrome --
     the progress bar (44) and the resume toast (45) -- both of which are display:none on a phone.
     So the retune lives here, and the phone keeps the original 70/90/91 from :root. */
  :root{--z-rail:24; --z-rail-bubble:26; --z-rail-go:26}
  .section-rail{right:var(--rail-right);transform:translateY(-50%);
    height:auto;max-height:calc(100vh - var(--topbar-h) - var(--pbar-h)
                                - var(--rail-margin) * 2);
    /* Centred on the BAND the rail indexes -- between the sticky top bar and the progress
       bar -- rather than on the raw viewport with a 52vh box. Measured before: a 468px box
       holding a 188px track, i.e. 280px of empty box centred on nothing in particular. Now
       the box hugs its content (height:auto) and the band centres it. */
    top:calc((100vh + var(--topbar-h) - var(--pbar-h)) / 2)}
  /* The track's own horizontal padding goes to zero on desktop: the tick already carries
     --rail-pad, and a second layer of padding would have to be added into --rail-box-w and the
     clearance equation. Removing it keeps the placement to the one equation in :root, which is
     what makes the clearance provably --rail-clear at every width. The phone keeps its 8px. */
  .section-rail-track{padding:2px 0}
  /* Desktop: the margin is empty, so every tick keeps its name, laid out inline.
     THE D3 FIX, and it is two changes that have to happen together:
       1. the name cell is a FIXED width (--rail-label, the same token the box's width is derived
          from), so no tick's dot depends on how long its own label is;
       2. the name is reset to `order:0`, putting the DOT FIRST in the row. The base rule puts
          the name first because on a phone the dot is the rightmost thing in a narrow gutter.
          With the dot leading and the label a fixed width, the dot column's left edge is the
          box's left edge plus --rail-pad, at EVERY level -- which is exactly what --rail-right
          places. Measured before the fix: a 75px spread across the 9 dots
          (1254,1181,1256,1247,1256,1256,1203,1212,1235). */
  .section-rail-tick{padding:0 var(--rail-pad);gap:var(--rail-dot-gap);justify-content:flex-start;
    min-width:0;width:var(--rail-tick-w)}
  /* The dot must not shrink once the label shares the row: with a fixed-width name the flex
     algorithm would otherwise steal from the dot rather than the label. */
  .section-rail-tick .section-rail-tick-mark{flex:none}
  /* The name column exists only where the margin can hold one. Below 1200px the rail is the bare
     dot strip -- so the chip is display:none here and switched on in the block below, rather
     than being given a zero width, which would leave its 4px of chip padding either side as an
     8px sliver of background beside every dot. */
  .section-rail-name{position:static;flex:none;order:0;display:none}
  .section-rail-tick.level-2{height:8px}
  .section-rail-tick.level-3{height:6px}
  /* The preview bubble and the Go chip. They are position:fixed, and the rail's own
     transform:translateY(-50%) makes THE RAIL their containing block -- so `right` here would be
     an offset inside the rail, not from the viewport edge.
     They are anchored to the rail's LEFT edge instead (right:100%), so they grow LEFTWARDS into
     the gutter and column. That is deliberate, and it is the only direction with room: the rail
     now sits hard against the content's right edge with at most ~250px of margin beyond it, so a
     bubble anchored rightwards left the viewport at 1024 and 1280 (measured x 1275..1555 against
     a 1280px viewport). Growing leftwards always fits, because the bubble is narrower than the
     page and there is a full column of room in that direction.
     Overlapping content is the point of this preview -- it is a transient, high-z-index chip that
     names the target without moving the reader, and the rail column is far too narrow to hold a
     190px label. */
  .section-rail-bubble,.section-rail-go{right:100%;left:auto}
  .section-rail-bubble{margin-right:6px}
  .section-rail-go{margin-right:6px}
  /* The fill rides the DOT COLUMN. The dot is the tick's first item, --rail-pad in from the
     tick's left edge, so the dot column's CENTRE is --rail-pad + --rail-dot/2 from the tick's
     left edge -- and the box's left edge is the rail's left edge.
     The base rule's `margin-left:-1px` is what centres the 2px bar on that point, so the `left`
     here must NOT subtract a further 1px: doing so measured the bar's centre 1px left of the dot
     column at every width. Previously the fill was left:50% of the TRACK, which is the dot
     column only while the tick has no label; once the label is there it measured x 1157.5
     against a dot column at 1181, i.e. 23.3px adrift and visibly crossing the labels mid-word. */
  .section-rail-fill{left:calc(var(--rail-pad) + var(--rail-dot) / 2);right:auto}
  .section-rail-pill{right:var(--rail-pad)}
}

/* WHERE THE NAME COLUMN IS TURNED ON. Below this the rail is the dot strip, and the only thing
   that has to change to switch the labels on is --rail-label -- every width and offset derives
   from it, so the block states no geometry of its own.
   The threshold is measured, not chosen: --rail-label-derive passes 48px at 1186px of viewport,
   and 48px is the narrowest label whose text survives the chip's 4px of padding. 1200px is the
   round width above that, where the derived label is 55px. At the four widths this task is
   verified at, the rail therefore reads: 1024 = dot strip; 1280 = 93px labels; 1440 = 173px;
   1920 = 180px (the cap) with 249px of margin to spare. */
@media (min-width:1200px){
  :root{--rail-label:var(--rail-label-derive);
    /* Now there IS a second item in the tick, so the gap between dot and label joins the width.
       Redefined here rather than in the base rule because below 1200px there is no label and the
       gap would be dead width -- see --rail-tick-w's comment in :root. */
    --rail-tick-w:calc(var(--rail-label) + var(--rail-dot) + var(--rail-dot-gap)
                       + var(--rail-pad) * 2)}
  /* The name chip: a FIXED width, and the same token the box's width is derived from. This is
     what makes every dot share one x -- see the desktop block above. */
  .section-rail-name{display:inline-block;width:var(--rail-label);
    max-width:var(--rail-label);font-size:10.5px;overflow:hidden;
    text-overflow:ellipsis}
}

/* DESKTOP PROGRESS BAR: bottom-pinned, same ink and accent as the resume toast, because it
   reports the same thing the toast does -- how far through this sitting the reader is. It is
   desktop-only: on a phone the section rail already shows position, and a bar would cost
   vertical space that a phone cannot spare. */
.pbar{display:none}
@media (min-width:761px){
  .pbar{display:block;position:fixed;left:0;right:0;bottom:0;z-index:var(--z-pbar);
    height:var(--pbar-h);background:var(--ink);color:#fff;font-size:11px;
    line-height:var(--pbar-h);letter-spacing:.06em;text-transform:uppercase}
  .pbar-fill{position:absolute;left:0;top:0;bottom:0;width:0;
    background:var(--accent);transition:width .12s linear}
  .pbar-txt{position:relative;display:block;text-align:center;color:#fff;
    mix-blend-mode:difference;font-weight:600}
  /* The toast sits above the bar, not on it. */
  .resume{bottom:38px}
}

/* Touch targets: text stays compact, the tappable box grows to >=44px. */
.qsum>summary,.supp-wrap>summary,.ns>summary,.morepts>summary,.proc>summary,
.stats-note>details>summary,.top a,.top nav a{
  min-height:44px;display:inline-flex;align-items:center;padding-left:6px;
  padding-right:6px;margin-left:-6px}
.stats-note>details>summary,.top nav a{margin-left:0;padding-left:0}
.srclink{display:inline-flex;align-items:center;min-height:44px}

/* Jump list: a disclosure on mobile, open on desktop. On mobile it was 2,905px
   of links sitting above the content they index. */
.qajump>summary{min-height:44px;display:flex;align-items:center;cursor:pointer;
  list-style:none;font-weight:700;font-size:15px}
.qajump>summary::-webkit-details-marker{display:none}
.qajump>summary::before{content:"▸ ";color:var(--faint);margin-right:6px}
.qajump[open]>summary::before{content:"▾ "}

/* Long pages on small screens.
   Two distinct problems, in order of size:
     1. TYPE SCALE. The brief title was 23px/37.26px line-height, so the 5 Aug
        motion title wrapped to FIVE lines = 186px of card height for one
        heading. That was the single largest waste on the page, and it is type,
        not padding.
     2. PADDING. Every one of 79 points, 17 speaker groups, 32 Q/A sides and 79
        quote chips carried a full-size pad; trimmed together they recover over
        a thousand pixels with no content loss.
   Horizontal: headings must stop short of the rail, see the .wrap rule below. */
@media (max-width:760px){
  /* ---- type scale ---- */
  .lede h1{font-size:clamp(28px,8.4vw,34px);letter-spacing:-.03em}
  .dek{font-size:15px;line-height:1.5}
  .kicker{font-size:11px;margin-bottom:12px}
  main h2{font-size:19px;line-height:1.22;letter-spacing:-.02em}
  .sub{font-size:13px;line-height:1.45;margin-top:6px}
  /* The brief title. 23px/1.62 wrapped to five lines; 17.5px/1.25 takes the
     same title to two or three. */
  .btitle{font-size:17.5px;line-height:1.26;letter-spacing:-.015em;margin-bottom:6px}
  .brief h3{font-size:17px;line-height:1.26}
  .bhint{font-size:10.5px;gap:5px 10px}
  .stage{font-size:9.5px;padding:2px 6px}
  .mapt{font-size:14.5px;line-height:1.3;margin-bottom:8px}
  .maphint{font-size:10.5px;margin-top:5px}
  .spkwho{font-size:10.5px;line-height:1.3;margin-bottom:6px}
  .pttext{font-size:14.5px;line-height:1.5}
  .whatis{font-size:14.5px;line-height:1.5}
  .why{font-size:13.5px;line-height:1.5}
  .qa-side p{font-size:14px;line-height:1.5}
  .gtitle{font-size:14px}
  .gcount{font-size:10.5px}

  /* ---- padding ---- */
  .lede{padding:26px 0 18px}
  .substance,.oral,.everything{padding-top:22px;margin-top:18px}
  .brief{padding:15px 14px 13px;border-radius:11px}
  .briefsum{padding:14px 17px;padding-right:40px}
  .bbody{padding:0 14px 13px}
  .bhead{padding-bottom:8px;margin-bottom:11px}
  .bcaret{right:15px;top:17px}
  .briefwrap{margin-bottom:11px}
  .pt{padding:9px 0}
  .spk{padding:9px 0 1px}
  .spk .points .pt{padding:7px 0 7px 11px}
  details.map .mapsum{padding:12px 0;padding-right:26px}
  .mapsum::after{right:2px;top:15px}
  .qabody{padding-bottom:12px}
  .mapwrap{margin:0}
  .qa-side{padding:10px 11px}
  .qa-pair{gap:8px}
  .qsum{margin-bottom:5px}
  /* The point chips are inline-flex and can run past their column into the rail
     gutter. Let them shrink and wrap instead of overhanging. */
  .qsum,.qsum>summary{max-width:100%;min-width:0}
  .qsum>summary{flex-wrap:wrap;white-space:normal}
  .qsum>summary,.supp-wrap>summary,.ns>summary,.morepts>summary,.proc>summary{
    padding-left:4px;padding-right:4px;margin-left:-4px}
  .srclink{margin-top:6px}
  .why{padding:10px 12px;margin-bottom:13px}
  .next{padding:11px 13px;margin-top:12px}
  .next b{font-size:9.5px;margin-bottom:4px}
  .sup-wrap{margin-top:10px;padding-top:9px}
  .supp{padding:7px 0 7px 9px;margin-top:6px}
  .proc{margin-top:13px;padding-top:10px}
  .proc li{padding:7px 0;grid-template-columns:88px 1fr;gap:2px 11px}
  .spk .points .pt:first-child{padding-top:0}
  .grp{margin-bottom:16px}
  details.grp>summary{padding:12px 34px 12px 13px;flex-wrap:wrap;gap:3px 10px}
  details.grp{margin-bottom:7px}
  .glist{padding:4px 0}
  .glist a{padding:11px 13px;min-height:44px;display:flex;align-items:center}
  .qajump{padding:11px 13px}
  .qajump li{font-size:13.5px;padding:6px 0;flex-wrap:wrap}
  /* The speaker name sits on its own line, indented 26px under the question
     title. width:100% PLUS margin-left:26px overflowed the row by 26px and ran
     into the rail gutter; subtract the indent so it stays inside the column. */
  .qajump .qw{margin-left:26px;width:calc(100% - 26px);white-space:normal}
  .qajump>summary{font-size:14px}
  .method{padding:18px 15px;margin-top:24px}
  .method h2{font-size:16px;margin-bottom:11px}
  .method p,.method ul,.method li{font-size:13px}
  .stats-note{margin-top:26px;padding-top:15px}
  .statgrid{grid-template-columns:1fr;gap:16px}
  footer{margin-top:24px;padding:18px 0 84px;font-size:12px}
  /* Reserve room for the rail so no heading (or body text) runs underneath it.
     Measured: the rail's box is 24px wide (tick) + 2px padding at right:6px, so
     its left edge sits 42px in from the viewport edge. 46px left a 2px sliver of
     every line still under it; 50px clears it with margin to spare. Horizontal
     room is the price of a fixed right-edge rail; the alternative is a rail that
     overlaps the text it exists to index. */
  /* The rail gets its OWN column: 46px of gutter reserved, so it never overlaps content.
     Without this the rail floated over the text and a reader could not tell where the page
     ended and the navigator began. */
  .wrap{padding-left:16px;padding-right:62px}
  /* the rail's label overhangs into the content column, so text that can collide with it
     reserves the same space. Raised on .lede only: body copy is already inside the wrap. */
  :root{--rail-gutter:34px}
  .top .wrap{padding-left:14px;padding-right:14px}
}
"""


def write_text_atomic(path, text):
    """Write text to `path` atomically.

    `open(path, "w")` truncates the target to zero bytes immediately, then writes
    the content. index.html is ~204KB, so every rebuild leaves a real window where
    the file on disk is empty or partial -- and `python -m http.server` serves
    whatever is on disk at request time, with no locking. A request landing in
    that window got a blank white page; a partial theme.css rendered as an
    unstyled wall even when the HTML arrived intact.

    It also failed unsafely: a build interrupted mid-write (Ctrl-C, timeout, an
    exception) left a permanently truncated page on disk instead of aborting with
    the previous good one intact.

    Writing to a temp file in the same directory and then os.replace() makes the
    swap atomic on the same filesystem: a reader sees either the complete old file
    or the complete new one, never a partial. Matches write_json_atomic in
    scraper/backfill.py and write_atomic in summariser/summarise.py, which already
    did this -- the site writer just did not follow the convention.
    """
    storage.write_text_atomic(path, text)
    return path


def load_summaries():
    """Every summarised policy brief, sharded by year, or [] if none exist yet."""
    out = []
    if not os.path.isdir(SUMMARIES):
        return out
    paths = sorted(glob.glob(os.path.join(SUMMARIES, "**", "*.json"), recursive=True))
    for path in paths:
        if os.path.basename(path) == "index.json":
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"  warning: skipping summary {path}: {exc}", file=sys.stderr)
    return out


def load_sittings():
    """Every sitting on disk, sharded by year, oldest first."""
    out = []
    paths = sorted(glob.glob(os.path.join(DATA, "20*", "sitting_*.json")))
    if not paths:
        paths = sorted(glob.glob(os.path.join(DATA, "sitting_*.json")))
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"  warning: skipping {path}: {exc}", file=sys.stderr)
    out.sort(key=lambda s: s["date"])
    return out



def build_skipped_payloads(out_dir, summaries):
    """Write the skipped sentences for each brief as a small JSON file, one per brief.

    WHY NOT INLINE. Measured across 2026: the skipped sentences are 52,698 sentences and
    6,691,507 characters against 11,239 published sentences and 1,789,397 characters -- the
    material behind the expanders is 3.74x the text already on the page. Embedding it would
    take a single sitting from 465 KB to roughly 3 MB, which is the wrong trade on a phone
    for something most readers never open. So the count stays on the page and the text is
    FETCHED when a reader actually asks for it.

    One file per brief rather than one index per sitting: the expanders belong to a brief,
    and a per-brief file keeps a tap cheap on a budget sitting holding six of them. A
    reader without JavaScript still sees the count; only the expansion is unavailable.
    """
    # the dataset loader lives with the summariser, not the site
    sys.path.insert(0, os.path.join(ROOT, "summariser"))
    import build_dataset as BD
    d = os.path.join(out_dir, "skipped")
    os.makedirs(d, exist_ok=True)
    written = 0
    for b in summaries or []:
        meta = b.get("_meta") or {}
        if meta.get("schema") != 4:
            continue
        iid = meta.get("id")
        year = str(meta.get("year") or "")
        if not iid or not year:
            continue
        src = os.path.join(ROOT, "pipeline", "dataset", year, f"{iid}.json")
        if not os.path.exists(src):
            continue
        rec = BD.load_item(src) or []
        if not rec:
            continue
        pub = {x.get("sid") for sec in (b.get("sections") or [])
               for x in (sec.get("sentences") or [])}
        skipped = [{"sid": s["sid"], "speaker": s.get("speaker") or "",
                    "attributed": bool(s.get("attributed")),
                    "text": s.get("text") or ""}
                   for s in rec if s["sid"] not in pub]
        if not skipped:
            continue
        # Written as a list, not an object, so it can be fetched as a simple array and
        # needs no schema negotiation on the client.
        write_text_atomic(os.path.join(d, f"{iid}.json"),
                          json.dumps(skipped, ensure_ascii=False, separators=(",", ":")))
        written += 1
    return written


def build_all(out_dir):
    sittings = load_sittings()
    if not sittings:
        print("no sittings in data/ -- nothing to build", file=sys.stderr)
        return 0
    summaries = load_summaries()
    os.makedirs(out_dir, exist_ok=True)
    sdir = os.path.join(out_dir, "sittings")
    os.makedirs(sdir, exist_ok=True)

    write_text_atomic(os.path.join(out_dir, "theme.css"), STYLE)

    # Pipeline dashboard: copy the template and the state it reads. The dashboard is
    # a static page over pipeline/state.json, so it needs no server and no build step
    # of its own -- but state.json must sit next to it to be fetchable.
    try:
        pipe_src = os.path.join(HERE, "pipeline", "index.html")
        pdir = os.path.join(out_dir, "pipeline")
        if os.path.exists(pipe_src):
            os.makedirs(pdir, exist_ok=True)
            write_text_atomic(os.path.join(pdir, "index.html"),
                              read_text(pipe_src))
            state_src = os.path.join(ROOT, "pipeline", "state.json")
            if os.path.exists(state_src):
                write_text_atomic(os.path.join(pdir, "state.json"),
                                  read_text(state_src))
    except Exception as exc:                                    # noqa: BLE001
        print(f"  (pipeline dashboard skipped: {exc})", file=sys.stderr)

    for s in sittings:
        page = render_sitting(s, css_href="../theme.css", home_href="../index.html",
                              archive_href="index.html", summaries=summaries)
        write_text_atomic(os.path.join(sdir, f"{s['date']}.html"), page)

    write_text_atomic(os.path.join(sdir, "index.html"),
                      render_archive(sittings, css_href="../theme.css",
                                     home_href="../index.html", archive_href="index.html",
                                     summaries=summaries))

    latest = sittings[-1]
    write_text_atomic(os.path.join(out_dir, "index.html"),
                      render_sitting(latest, css_href="theme.css", home_href="index.html",
                                     archive_href="sittings/index.html",
                                     summaries=summaries))

    n_skipped = build_skipped_payloads(out_dir, summaries)
    printed = sum(brief_substance(s) for s in summaries)
    print(f"built {len(sittings)} sitting page(s) + archive; latest = {latest['date']}; "
          f"{len(summaries)} briefs ({printed:,} sentences published)")
    return len(sittings)


def main(argv):
    out_dir = argv[1] if len(argv) > 1 else os.path.join(HERE, "dist")
    os.makedirs(out_dir, exist_ok=True)
    return 0 if build_all(out_dir) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
