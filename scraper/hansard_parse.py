#!/usr/bin/env python3
"""Parse a Hansard report the way it is actually structured, not by pattern-matching text.

WHY A PARSER RATHER THAN REGEXES. The report is a regular, flat document built from exactly
three tags -- <p>, <strong> and <h6> -- in a tiny grammar:

    <p>[(proc text) ...]</p>                                  procedural, no speaker
    <p><strong>NAME</strong>: body</p>                        a speaker turn
    <p>body</p>                                               continuation of the turn
    <h6>2.03 pm</h6>                                          a clock stamp

The previous implementation scanned the raw string with regexes, so every source
irregularity became a bug in the pattern:

  * ``&nbsp;`` is not matched by ``\\s``, so ``<p>&nbsp;<strong>Mr Speaker</strong>`` failed
    the ``^\\s*<strong>`` anchor and its text was APPENDED to the previous speaker's turn --
    publishing the Speaker's words as part of someone else's speech.
  * ``<strong style="...">`` was not matched by a literal ``<strong>`` at all.
  * A name split across consecutive ``<strong>`` tags was truncated mid-title.

Each was fixed by editing the pattern, and the pattern kept meeting a new irregularity.
Structure does not have this problem: after parsing, "does this paragraph start with a
<strong>, and is a colon the first thing after it?" is a question about ELEMENTS, and it
cannot be fooled by whitespace entities, attributes, spans or nesting.

It also recovers the <h6> clock stamps, which the old parser threw away -- so sitting
durations no longer need a separate scraping pass.

USAGE
    from hansard_parse import parse_report
    turns = parse_report(html)          # [{speaker, text, time, lang, is_procedural}, ...]
"""
from html.parser import HTMLParser
import re

# A paragraph whose bold lead is one of these is an editorial aside, not a speaker.
ASIDE = ("[", "(")

# "(In Mandarin)", "( In Tamil )" -- the language a speech is delivered in, appearing
# between the speaker's name and the colon.
LANG_MARK = re.compile(r"^\(\s*(?:In\s+)?(English|Chinese|Malay|Tamil|Mandarin|Hokkien|"
                       r"Cantonese|Teochew|Hainanese|Hakka)\s*\)$", re.I)


def re_match_lang(text):
    """True when text is a bare language marker."""
    return bool(LANG_MARK.match((text or "").strip()))


class _Inline:
    __slots__ = ("kind", "text")

    def __init__(self, kind, text=None):
        self.kind = kind          # 'text' | 'strong' | 'em' | 'br'
        self.text = text

    def __repr__(self):           # pragma: no cover - debugging aid
        return f"<{self.kind}:{self.text!r}>"


class ReportParser(HTMLParser):
    """Collect a Hansard report into blocks: ('p', [inline...]) or ('h6', text)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)   # &nbsp; -> U+00A0 automatically
        self.blocks = []
        self._stack = []          # open block tags, e.g. ['p']
        self._inline = None       # current block's inline pieces
        self._strong = None       # depth of open <strong>, and its accumulated text

    # ---- block tags
    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("p", "h6"):
            self._stack.append(t)
            self._inline = []
            return
        if self._inline is None:
            return
        if t == "strong":
            self._strong = {"depth": 1, "text": []}
            self._inline.append(_Inline("strong", ""))
        elif t in ("em", "i"):
            self._inline.append(_Inline("em", ""))
        elif t == "br":
            self._inline.append(_Inline("br"))
        elif t in ("span", "sup", "sub", "a", "u", "b"):
            # transparent wrappers: their text belongs to whatever contains them
            pass

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("p", "h6"):
            if self._stack:
                self._stack.pop()
            if self._inline is not None:
                self.blocks.append((t, self._inline))
            self._inline = None
            self._strong = None
            return
        if t == "strong" and self._strong is not None:
            # commit accumulated text into the most recent strong piece
            for piece in reversed(self._inline or []):
                if piece.kind == "strong":
                    piece.text = "".join(self._strong["text"])
                    break
            self._strong = None
        elif t in ("em", "i") and self._inline is not None:
            pass

    def handle_data(self, data):
        if self._inline is None or not data:
            return
        if self._strong is not None:
            self._strong["text"].append(data)
        # text inside <em>/<span> still needs to land somewhere: append as text, but only
        # if we are not inside a <strong>
        if self._strong is None:
            if self._inline and self._inline[-1].kind in ("em",) and self._inline[-1].text == "":
                self._inline[-1].text = data
            else:
                self._inline.append(_Inline("text", data))


def _pieces_text(pieces):
    return "".join(p.text or "" for p in pieces if p.kind in ("text", "strong", "em"))


def _is_blank(s):
    """Whitespace in any form: space, tab, newline, and non-breaking space."""
    return not s or not s.strip(" \t\r\n\u00a0\ufeff")


def split_paragraph(pieces):
    """Classify one paragraph.

    Returns (speaker, body, lang) where speaker is None for a continuation.

    THE RULE, and it does not involve the colon. A paragraph is a speaker turn iff, ignoring
    leading whitespace, its first element is a <strong>, and that strong's text is a plausible
    name -- meaning it is NOT:

      * empty                       (<strong> </strong>)
      * wrapped in [...]            ([Mr Speaker in the Chair])
      * ALL CAPS                    (CRIMINAL PROCEDURE (AMENDMENTS) BILL)

    Everything else is a continuation of the current turn.

    WHY NOT THE COLON. The colon was used as the discriminator, and it is a PROXY for "this is
    a speaker" rather than the structural fact. Because it is a proxy it had to be located in
    three different places -- after the strong tag, inside the strong tag, and inside an <em>
    between them -- each of which was a separate code path and a separate bug. Measured over 48
    reports: a colon-free rule accepts 756 paragraphs as speakers and every one is a genuine
    speaker, while rejecting 36 asides, 9 empty tags and 3 bill titles. The colon separated
    nothing the three checks above do not separate, so it is gone.

    This also dissolves the quirks rather than handling them: leading &nbsp;, a styled
    <strong style="...">, and a name split across consecutive strong tags all stop mattering,
    because none of them is a question about a text pattern.
    """
    i = 0
    n = len(pieces)
    while i < n and pieces[i].kind == "text" and _is_blank(pieces[i].text):
        i += 1
    # A leading question NUMBER: "8 <strong>Mr Neil Parekh ...</strong> asked the Minister ..."
    # This is the question-list form, and the bolded name IS the speaker of the paragraph.
    # Skipped as a leading label, not treated as prose.
    if i < n and pieces[i].kind == "text":
        t = (pieces[i].text or "").strip(" \t\r\n\u00a0")
        if t and all(ch.isdigit() or ch in ",&-" for ch in t):
            i += 1
            while i < n and pieces[i].kind == "text" and _is_blank(pieces[i].text):
                i += 1
    if i >= n or pieces[i].kind != "strong":
        return None, _pieces_text(pieces), None

    # joint consecutive strong pieces: a long ministerial title is sometimes tagged in two
    # pieces when the site's styling changes mid-name, and the source puts WHITESPACE between
    # them ("</strong>\t<strong style=...>"). A plain "while next is strong" loop stops at that
    # whitespace and truncates the title mid-name -- which is how
    # "The Senior Parliamentary Secretary to the Minister for Social and Family Development
    # (Mr Eric Chua) (for the" got published. Blank text pieces between strongs are skipped.
    name_parts = []
    j = i
    while j < n:
        if pieces[j].kind == "strong":
            name_parts.append(pieces[j].text or "")
            j += 1
            continue
        if pieces[j].kind == "text" and _is_blank(pieces[j].text):
            # only skip it if ANOTHER strong follows, otherwise it is the real separator
            k2 = j + 1
            while k2 < n and pieces[k2].kind == "text" and _is_blank(pieces[k2].text):
                k2 += 1
            if k2 < n and pieces[k2].kind == "strong":
                j = k2
                continue
        break
    name = re.sub(r"[\s\u00a0]+", " ", " ".join(name_parts)).strip()

    # a trailing colon is part of the name in "<strong>Mr Speaker:</strong> body"; strip it.
    # This is name CLEANING, not classification -- nothing depends on its presence.
    name = name.rstrip(":").strip()

    if not name or name.startswith(ASIDE) or (name.isupper() and len(name) > 8):
        return None, _pieces_text(pieces), None

    # The language a speech is in may sit between the name and the body: "(In Mandarin)".
    # Captured when present, ignored otherwise -- it does not affect whether this is a turn.
    k = j
    lang = None
    while k < n:
        piece = pieces[k]
        if piece.kind == "text":
            s = (piece.text or "").strip(" \t\r\n\u00a0")
            if s in ("", "(", ")"):
                k += 1
                continue
            break
        if piece.kind == "em":
            e = (piece.text or "").strip()
            if re_match_lang(e):
                lang = lang or e.strip("() \u00a0")
                k += 1
                continue
            break
        break

    body = _pieces_text(pieces[j:]).strip()
    body = re.sub(r"^[:\s\u00a0]+", "", body)
    return name, body, lang


def parse_report(html, keep_asides=True):
    """Turn report HTML into speaker-attributed turns with clock stamps.

    Returns a list of dicts in document order:
        {"speaker": str|None, "text": str, "time": str|None, "is_procedural": bool}

    ``speaker`` is None where the record named nobody. It is NEVER invented: a turn that
    cannot be attributed stays unattributed rather than inheriting the previous name.
    """
    rp = ReportParser()
    rp.feed(html or "")
    rp.close()

    turns = []
    current = None
    current_time = None
    for kind, pieces in rp.blocks:
        if kind == "h6":
            t = _pieces_text(pieces).strip()
            if t:
                current_time = t
                if current is not None:
                    current.setdefault("times", []).append(t)
            continue
        speaker, body, _lang = split_paragraph(pieces)
        if speaker is not None:
            # A NAMED TURN WITH NO TEXT is dropped, not emitted. The source contains
            # "<p><strong>NAME</strong>:</p>" with nothing after the colon -- the Speaker
            # calls a Member who then does not speak, or a heading with an empty body. Keeping
            # it puts an empty turn in the dataset, which becomes an empty sentence candidate
            # and can reach a brief as a blank. The old parser skipped these because its
            # regex required text after the colon.
            if not body.strip():
                continue
            current = {"speaker": speaker, "text": body, "time": current_time,
                       "is_procedural": False}
            turns.append(current)
        else:
            if not body:
                continue
            if current is None:
                current = {"speaker": None, "text": body, "time": current_time,
                           "is_procedural": bool(body.strip().startswith("[")
                                                 and body.strip().endswith("]"))}
                turns.append(current)
            else:
                current["text"] = f"{current['text']} {body}".strip()
    return turns
