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

QA_PREVIEW = 4          # mappings shown before the expand control
SPEAKER_PREVIEW = 4     # speaker groups shown per brief before the expand control


def render_mapping(q):
    """One question -> response mapping card."""
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

    return f"""
      <li class="map" id="{esc(q['report_id'])}">
        <a class="mapt" href="{esc(hansard_url(q))}" target="_blank" rel="noopener">{esc(q['title'])}</a>
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
      <article class="brief" id="{esc(meta.get('report_ids', [''])[0])}">
        <header class="bhead">
          <h3>{esc(title)}</h3>
          <div class="bmeta">{stage_html}<span class="bdate">{dates_txt}</span>
            <span class="bsrc">{', '.join(esc(x) for x in (meta.get('report_ids') or [])[:4])}</span>
          </div>
        </header>
        <p class="whatis">{esc(brief.get('what_it_is', ''))}</p>
        {why_html}
        {points_html}
        {next_html}
        {ns_html}
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
    # Oral answers are rendered IN FULL on the page -- no disclosure, no expand
    # control. They are the most readable business Parliament transacts (a
    # question and its answer), and hiding them behind a collapsed group card
    # meant a sitting page showed 16 title-only rows. Content stays on the page;
    # only the verbatim quote chips and the "Everything else" groups collapse.
    qa_html = "\n".join(render_mapping(q) for q in qa_rows)

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
<body>
<header class="top">
  <div class="wrap">
    <a class="logo" href="{esc(home_href)}"><span class="veg">🌱</span> Parsnips</a>
    {nav(home_href, archive_href)}
  </div>
</header>

<main class="wrap">

  <section class="lede">
    <p class="kicker">Singapore Parliament &middot; Sitting No. {esc(str(lead.get("sitting_no") or ""))}</p>
    <h1><time datetime="{esc(d)}">{esc(pretty_date(d))}</time></h1>
    <p class="dek">{lede_words}, read so you don't have to.</p>
  </section>

  {f'''
  <section class="substance">
    <div class="sec-head"><h2>What the Government is doing</h2>
      <p class="sub">What was decided or announced, who said it, and where it goes next.
      Every point carries the words it was taken from.</p></div>
    {brief_list(bills) if bills else ''}
    {brief_list(others) if others else ''}
    {proc_html}
  </section>''' if substantive else f'''
  <section class="substance">
    <div class="sec-head"><h2>What the Government is doing</h2></div>
    <p class="empty">This sitting's business was entirely procedural.</p>
    {proc_html}
  </section>'''}

  {f'''
  <section class="oral">
    <div class="sec-head"><h2>Oral answers</h2>
      <p class="sub">{len(qa_rows)} questions put to Ministers, each paired with the
      response given. {MAPPING_NOTE}</p></div>
    <nav class="qajump" aria-label="Jump to an oral answer">
      <ol>{''.join(f'<li><a href="#{esc(q["report_id"])}">{esc(q["title"])}</a>'
                   f'<span class="qw">{esc(q["asker"])}</span></li>' for q in qa_rows)}</ol>
    </nav>
    <ul class="maplist">{qa_html}</ul>
  </section>''' if qa_rows else ''}

  <section class="everything">
    <div class="sec-head"><h2>Everything else</h2>
      <p class="sub">The rest of the sitting, grouped. {len(reports)} items.</p></div>
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

  <section class="method">
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
var maps = document.querySelector('.reveal-maps');
if (maps) {{
  maps.addEventListener('click', function () {{
    var more = document.querySelector('.qa-more');
    if (more) {{
      more.classList.remove('hidden');
      more.classList.remove('qa-more');
      maps.remove();
    }}
  }});
}}
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
.brief{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:26px 28px 22px;margin-bottom:18px;box-shadow:0 1px 2px rgba(20,24,29,.03)}
.bhead{border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:16px}
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
"""


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

    with open(os.path.join(out_dir, "theme.css"), "w", encoding="utf-8") as fh:
        fh.write(STYLE)

    for s in sittings:
        page = render_sitting(s, css_href="../theme.css", home_href="../index.html",
                              archive_href="index.html", summaries=summaries)
        with open(os.path.join(sdir, f"{s['date']}.html"), "w", encoding="utf-8") as fh:
            fh.write(page)

    with open(os.path.join(sdir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(render_archive(sittings, css_href="../theme.css",
                                home_href="../index.html", archive_href="index.html",
                                summaries=summaries))

    latest = sittings[-1]
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(render_sitting(latest, css_href="theme.css", home_href="index.html",
                                archive_href="sittings/index.html", summaries=summaries))

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
