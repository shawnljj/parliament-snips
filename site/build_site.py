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
import html
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")

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


def esc(s):
    return html.escape(s or "", quote=True)


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
  var RAIL_MQ = '(max-width: 760px)';
  // Numbering for the level-3 ticks, so a dot has a stable identity that can be
  // matched against the numbered jump list in the Oral answers section.
  var railOrdinal = 0;
  var rail = null, railFill = null, railPill = null, railEntries = [], railActive = -1;
  var railPillTimer = null, railResizeTimer = null, pendingIndex = -1;

  function railIsMobile() {
    return window.matchMedia && window.matchMedia(RAIL_MQ).matches;
  }
  function truncate(text, max) {
    max = max || 42;
    return text.length <= max ? text : text.slice(0, max).replace(/\s+$/, '') + '…';
  }

  function railCollect() {
    var seen = {};
    var out = [];
    railOrdinal = 0;
    [].slice.call(document.querySelectorAll('main h2, main h3')).forEach(function (h, i) {
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
      // Level 2 for the section headings, level 3 for everything nested under
      // one, so the tick column is scannable by size.
      var level = h.tagName === 'H2' ? 2 : 3;
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

      // Identity at rest, at no cost in width. Always-on name labels were tried
      // and made the rail 107px wide -- 27% of a 390px screen, with the names
      // truncated to uselessness ("What the G..."). Instead: section ticks keep
      // their names in the pill (active section) and the drag bubble (on demand),
      // and the 16 oral-answer ticks carry a NUMBER matching the numbered jump
      // list in the Oral answers section, so a dot can be looked up without
      // touching it.
      if (entry.level === 3) {
        var num = document.createElement('span');
        num.className = 'section-rail-num';
        num.textContent = String(entry.ordinal);
        b.appendChild(num);
      }

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
        // Two-step: the first tap PREVIEWS, the second commits. Previously every
        // tap navigated (the peek needed a 350ms hold that a drifting thumb
        // could not achieve), so the only way to learn what a dot was, was to
        // be teleported to it. Tapping must never move the reader by surprise.
        if (pendingIndex !== index) {
          ev.preventDefault();
          railPreview(index);
          return;
        }
        railClearPending();
        railGo(index);
      });
      b.addEventListener('focus', function () { railPreview(index); });
      b.addEventListener('blur', function () { railClearPending(); });

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
     column and paint above page content; that means JS has to say where. Centre
     them on the tick and keep them above the fold. */
  function railPlacePreview(btn) {
    var r = btn.getBoundingClientRect();
    var cy = Math.round(r.top + r.height / 2);
    var bubble = btn.querySelector('.section-rail-bubble');
    var go = btn.querySelector('.section-rail-go');
    var maxY = window.innerHeight - 8;
    if (bubble) {
      bubble.style.top = cy + 'px';
      // Nudge clear of the Go chip sitting just below it.
      go.style.top = Math.min(maxY - 24, cy + Math.round(r.height / 2) + 4) + 'px';
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
        railPillTimer = window.setTimeout(function () {
          railPill.classList.remove('is-visible');
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
    var off = stickyH() + 8;
    var cur = 0;
    railEntries.forEach(function (e, i) {
      if (e.el.getBoundingClientRect().top - off <= 1) cur = i;
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
    if (!railIsMobile()) {
      if (rail) rail.remove();
      rail = null; railFill = null; railPill = null;
      railEntries = []; railActive = -1;
      return;
    }
    railEnsure();
    railEntries = railCollect();
    railBuild();
    railSizeTicks();
    railWireScrub();
    railActive = -1;
    railSync();
  }

  window.addEventListener('scroll', function () {
    // Scrolling dismisses a pending preview: the reader has moved on, and a
    // stale "tap again to go" would be misleading.
    if (pendingIndex >= 0) railClearPending();
    railSync();
  }, { passive: true });
  window.addEventListener('resize', function () {
    if (!railIsMobile()) { railRebuild(); return; }
    railSizeTicks();
    if (railResizeTimer !== undefined) window.clearTimeout(railResizeTimer);
    railResizeTimer = window.setTimeout(railRebuild, 150);
  });
  if (window.matchMedia) {
    var railMq = window.matchMedia(RAIL_MQ);
    var railOnChange = function () { railRebuild(); };
    if (railMq.addEventListener) railMq.addEventListener('change', railOnChange);
    else if (railMq.addListener) railMq.addListener(railOnChange);
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
  window.addEventListener('load', function () {
    syncStickyVar(); railRebuild(); offerResume();
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
        if d in (meta.get("sitting_dates") or []):
            briefs.append(b)
    # order by what a citizen most needs to know
    ORDER = {"bill": 0, "statement": 1, "budget": 2, "motion": 3, "adjournment": 4}
    briefs.sort(key=lambda b: (ORDER.get(b.get("_meta", {}).get("group"), 9),
                               -len(b.get("key_points", []))))
    # A brief with no verified points is procedural business -- the summariser
    # correctly reports "no policy substance". Rendering it as a full card would
    # pad the page with headings that say nothing, so list those compactly and
    # keep the cards for things that actually decided or announced something.
    substantive = [b for b in briefs if b.get("key_points")]
    procedural = [b for b in briefs if not b.get("key_points")]
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
        return "".join(render_brief(b) for b in out)

    # The section rail builds ticks from h2/h3 headings, so the briefs need real
    # headings rather than being a run of <article>s. Without these the rail
    # would only ever show the four section titles.
    def brief_block(items, heading):
        if not items:
            return ""
        return (f'<h2 class="railhead">{esc(heading)}</h2>'
                + "".join(render_brief(b) for b in items))

    lead = reports[0] if reports else {}
    if substantive:
        lede_words = (f"{len(substantive)} polic{'y' if len(substantive) == 1 else 'ies'} "
                      f"and {len(reports):,} items of business")
    else:
        lede_words = f"{len(reports):,} items of business"

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
    response given. {MAPPING_NOTE}
    {f'<b>{n_summarised} of {len(qa_rows)} summarised so far</b> — the rest show the transcript.' if n_summarised else ''}</p>
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
        {f'({sum(len(b.get("key_points", [])) for b in substantive)} verified points)' if substantive else ''}</li>
      <li>Speaker attribution: <b>{attr_txt}</b> of turns carry an explicit speaker tag</li>
      <li>Any point whose quote could not be found in the transcript was discarded
        rather than shown.</li>
      {coverage_warn}
    </ul>
  </section>

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
        rows.append(f"""
      <li class="srow">
        <a href="{esc(d)}.html">
          <span class="sdate">{esc(pretty_date(d))}
            <span class="sdow">{esc(dt.strftime('%A'))}</span></span>
          <span class="slead">{lead_line}</span>
          <span class="sstats">{esc(detail)} &middot; {len(s['reports'])} items
            &middot; {coverage_text(cov)}</span>
        </a>
      </li>""")

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
    <ul class="slist">{''.join(rows) or '<li class="empty">No sittings yet.</li>'}</ul>
  </section>
  <footer><p>Parsnips &middot; an unofficial reader for the Official Report.
  Hansard is a public record; the full text is at sprs.parl.gov.sg.</p></footer>
</main>
</body>
</html>
"""


STYLE = """
:root{
  --ink:#14181d; --dim:#5c6773; --faint:#8b95a1; --line:#e3e7ec;
  --bg:#fbfbfa; --card:#ffffff; --accent:#1c6b4a; --accent-soft:#e8f2ec;
  --warm:#b4622a; --radius:14px;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1060px;margin:0 auto;padding:0 24px}
a{color:inherit;text-decoration:none}
h1,h2,h3{line-height:1.2;margin:0}

/* top bar */
.top{border-bottom:1px solid var(--line);background:rgba(251,251,250,.86);
  backdrop-filter:blur(10px);position:sticky;top:0;z-index:20}
.top .wrap{display:flex;align-items:center;justify-content:space-between;height:60px}
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
.section-rail{display:none}
@media (max-width:760px){
  .section-rail{display:block;position:fixed;top:50%;right:6px;transform:translateY(-50%);
    z-index:70;padding:10px 0;pointer-events:none;max-width:calc(100vw - 12px)}
  .section-rail.is-dormant{display:none}
  .section-rail-track{position:relative;display:flex;flex-direction:column;
    align-items:center;gap:var(--rail-gap,14px);padding:2px 8px;pointer-events:auto}
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
  /* progress fill runs behind the ticks */
  .section-rail-fill{position:absolute;top:12px;bottom:12px;left:50%;width:2px;
    margin-left:-1px;border-radius:2px;background:#e2e8e3;overflow:hidden}
  .section-rail-fill::after{content:'';position:absolute;inset:0 0 auto 0;
    height:calc(var(--rail-progress,0) * 100%);
    background:linear-gradient(180deg,var(--accent),#2f8f66);
    transition:height .25s ease}
  /* Ticks are laid out as a row: the dot, then its identity (a name for the
     section-level ticks, a number for the oral-answer ones). The rail answers
     "which section is this?" at rest rather than only after a gesture. */
  .section-rail-tick{position:relative;appearance:none;border:0;background:transparent;
    padding:0 2px;width:auto;min-width:24px;height:var(--rail-tick-h,22px);
    display:flex;align-items:center;justify-content:flex-end;gap:5px;cursor:pointer}
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
    padding:2px 4px}
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
    opacity:0;pointer-events:none;z-index:90;
    transition:opacity .15s ease,transform .15s ease}
  .section-rail-tick.is-pending .section-rail-bubble{opacity:1;
    transform:translateY(-50%) scale(1)}
  /* The explicit confirm affordance, visible while pending. */
  .section-rail-go{position:fixed;top:0;left:auto;right:10px;
    font:700 10px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;
    color:#fff;background:var(--accent);border-radius:6px;padding:6px 9px;
    opacity:0;pointer-events:none;z-index:91;
    transition:opacity .15s ease}
  .section-rail-tick.is-pending .section-rail-go{opacity:1}
}
@media (prefers-reduced-motion:reduce){
  .section-rail-fill::after,.section-rail-tick-mark,.section-rail-pill,
  .section-rail-bubble{transition:none}
}

/* Back to top: a long page needs one, and it doubles as "you are deep in". */
.totop{position:fixed;right:16px;bottom:16px;z-index:40;width:44px;height:44px;
  border-radius:50%;border:1px solid var(--line);background:var(--card);
  color:var(--accent);font-size:17px;line-height:1;cursor:pointer;
  box-shadow:0 2px 10px rgba(20,24,29,.12);opacity:0;visibility:hidden;
  transition:opacity .2s,visibility .2s}
.totop.on{opacity:1;visibility:visible}
@media (prefers-reduced-motion:reduce){.totop{transition:none}}
/* keep the rail and the back-to-top button from crowding each other */
@media (max-width:760px){.totop{bottom:16px;right:14px}}

/* "Resume where you left off" -- offered, never forced. Auto-dismisses. */
.resume{position:fixed;left:16px;right:16px;bottom:16px;z-index:45;
  display:flex;align-items:center;gap:10px;padding:12px 14px;
  background:var(--ink);color:#fff;border-radius:12px;font-size:13.5px;
  box-shadow:0 8px 26px rgba(20,24,29,.28)}
.resume span{flex:1;line-height:1.35}
.resume button{font:600 13px inherit;border-radius:8px;cursor:pointer;
  min-height:44px;padding:0 14px}
.resume .rgo{background:var(--accent);color:#fff;border:0}
.resume .rno{background:none;border:0;color:#aeb8c2;font-size:18px;padding:0 6px}
@media (min-width:761px){.resume{left:auto;right:16px;max-width:380px}}

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
  .qajump .qw{margin-left:26px;width:100%;white-space:normal}
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
  .wrap{padding-left:16px;padding-right:50px}
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
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def load_summaries():
    """Every summarised policy brief, or [] if none exist yet."""
    sdir = os.path.join(ROOT, "summaries")
    out = []
    if not os.path.isdir(sdir):
        return out
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith(".json") or fn == "index.json":
            continue
        try:
            with open(os.path.join(sdir, fn), encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"  warning: skipping summary {fn}: {exc}", file=sys.stderr)
    return out


def load_sittings():
    out = []
    for fn in sorted(os.listdir(DATA)):
        if not (fn.startswith("sitting_") and fn.endswith(".json")):
            continue
        try:
            with open(os.path.join(DATA, fn), encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"  warning: skipping {fn}: {exc}", file=sys.stderr)
    out.sort(key=lambda s: s["date"])
    return out


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

    printed = sum(len(s.get("key_points", [])) for s in summaries)
    print(f"built {len(sittings)} sitting page(s) + archive; latest = {latest['date']}; "
          f"{len(summaries)} briefs ({printed} verified points)")
    return len(sittings)


def main(argv):
    out_dir = argv[1] if len(argv) > 1 else os.path.join(HERE, "dist")
    os.makedirs(out_dir, exist_ok=True)
    return 0 if build_all(out_dir) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
