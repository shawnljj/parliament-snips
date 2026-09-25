"""What counts as significant: the rules that decide which sentences are worth surfacing.

WHY THIS EXISTS AS A SHARED MODULE
Selection decides what the reader sees highlighted, and the same question is asked in two places:
when a brief is built (summariser/extractive.py pre-selects sentences the model may cite) and when
the reading view renders a sitting (rag/read_server.py marks the sentences a brief cited). If the
two answer differently, the page and the pipeline disagree about what is significant, which is the
class of bug this project has hit before (see extractive.py's note on the selector and the gate
disagreeing about source text). So the rules live here, with no imports beyond `re`, and both call
this module. Keeping it dependency-free matters: rag/read_server.py is a request-time path.

THE PROBLEM, MEASURED (owner's report, 2026-09-25)
A reader looking at 'Structuring Singapore for Flourishing Families' saw these two lines highlighted
as the last section of the brief:

    Mr Speaker    "Ms Chen, you have a clarification to make?"
    Ms Elysa Chen "I said that the statutory minimum annual leave under the Employment Act goes up
                   to a cap of 14 years."

Both are true to the record and neither is a policy move. The first is the Chair managing the
floor; the second restates what the Member had already said in her speech, which is already
highlighted earlier in the same brief. The owner's framing is the design rule: this is interesting
from a parliamentary-procedure point of view and has no value to a citizen asking what policies are
being moved.

CLASSES, AND WHY EACH IS ONE
The distinction the owner draws is not "boring vs interesting" -- it is between three different
things a sentence can be:

  * a POLICY MOVE -- a proposal, a commitment, a figure and the conditions attached to it;
  * CONTEXT -- history, rationale, comparison, a description of how something works. It explains a
    policy without being one, and the owner's decision is to KEEP it;
  * PROCEDURE -- the House managing itself: the Chair giving and taking the floor, reading a
    question, declaring a vote, and Members restating what they already said.

Only the third is filtered. A significance rule that tried to judge whether context was *good*
context would be re-litigating the summariser, which is not this module's job.

WHAT IS DELIBERATELY NOT DONE
  * No "no policy verb and no figure" rule. Measured over 134,393 highlights: 57.3% carry neither,
    and sampling showed the class is full of substance ("Clause 7 of the Bill amends Article 19 for
    this purpose", "Section 94A mandates that divorcing couples with children..."). A rule with a
    57% false-positive rate is not a filter.
  * A chair ruling is KEPT unless it is housekeeping. "Drop everything the Speaker said" is wrong;
    the Speaker explaining a Standing Order is substance. Measured: of 675 highlighted chair
    sentences, only 4 are floor-management phrasing, but the phrase list is incomplete -- see below.
  * Nothing is deleted. A hidden sentence stays in the record and in the database, and the page
    states how many it hid. Losing information is the one thing this project never trades for tidiness.

THE ANCHORING RULE, LEARNED THE HARD WAY
Phrase lists must be anchored or they fire inside ordinary content. extractive.py documents the
cost of getting this wrong: an unanchored `proc text` pattern matched a trailing parser marker
inside a 10,046-character ministerial speech and discarded the entire turn, emptying 44 Bill and
Budget items while every quote check still passed. So every pattern below is either anchored to the
start of the sentence or specific enough that it cannot occur in policy prose.
"""
import re

# ---------------------------------------------------------------- chair, and when it is housekeeping
# The Speaker and the Deputy Speaker, as the `speaker` field is recorded in Hansard.
CHAIR_SPEAKER_RE = re.compile(
    r"^(?:the\s+)?(?:mr|mdm|madam|the)\s+(?:deputy\s+)?speaker\b"
    r"|^\s*(?:mr|mdm|madam)\s+(?:deputy\s+)?speaker\s*$", re.I)

# The Chair's housekeeping: managing the floor, reading the question, declaring a vote.
#
# The phrase list is NOT the load-bearing part. Measured: of 675 highlighted chair sentences, only
# 4 match floor-management phrasing -- because the Chair's commonest lines in this corpus ("Order.
# Order.", "I will now proceed to declare the voting results.") are not in the list the old
# PROCEDURAL_RE carried, and the one the owner caught ("Ms Chen, you have a clarification to make?")
# is a NEWER phrasing than anything the list knew. That is the general case for phrase lists against
# a 10-year corpus: they date. So the rule is two-part -- the speaker must BE the Chair, and the
# sentence must be housekeeping by SHAPE or by phrase -- and a future Chair phrasing is caught by
# the shape test rather than needing a new pattern.
FLOOR_MGMT_RE = re.compile(
    r"\b(?:you have (?:a|the|one) \w+ to make"          # "you have a clarification to make?"
    r"|clarification to make|question to (?:ask|put)"
    r"|(?:please |you may |may I ask you to )?(?:proceed|continue)\b"
    r"|i (?:now )?(?:call|invite)\b"
    r"|(?:i (?:shall|will) now|the house will now|we will now)\b"
    r"|(?:has|have) the (?:hon|honourable|member)\b"
    r"|no further (?:questions|supplementar)"
    r"|(?:we|let us) (?:will )?move on"
    r"|(?:your |the )time is up"
    r"|speak(?:ing)?(?: to)?(?: the House)? ?later\b"
    r"|you (?:have|had) the floor"
    r"|order[,.]?\s+order"
    r"|(?:i will now|please) proceed\b)\b", re.I)

# Reading the question / declaring a division. These carry the House's arithmetic and not a policy.
QUESTION_READ_RE = re.compile(
    r"\b(?:the question is\b|question (?:put|proposed|agreed)"
    r"|i (?:will now )?proceed to declare the voting results"
    r"|there are \d+[ ]*\"?(?:ayes|noes|abstention)"
    r"|(?:ayes|noes)\"?, and no \"?abstentions"
    r"|not less than two-thirds of the (?:total number of )?members"
    r"|pursuant to article \d+\(\d+\)"
    r"|(?:bill|motion|amendment) (?:be|is) (?:now )?(?:read|put) a (?:second|third) time"
    r"|that the (?:bill|motion) be now (?:read|put)\b)\b", re.I)

# Consent / assent seeking, wherever it sits in the sentence. Kept from the original PROCEDURAL_RE:
# it was measured against 560 turns and is specific enough not to fire on debate.
CONSENT_RE = re.compile(
    r"\bseek(?:s|ing)? (?:your|the)\b[^.]{0,40}\b(?:consent|assent)\b"
    r"|\b(?:general )?assent of (?:hon )?members\b"
    r"|\bhave the general assent\b"
    r"|\bi give my consent\b"
    r"|\bpermission of the house\b", re.I)

# A restatement points BACK at a speech rather than making a move of its own.
#
# Anchored, because the phrase only signals a restatement when it OPENS the sentence: mid-sentence
# ("I said that because...") is ordinary argument. Measured: 465 highlights open this way.
RESTATEMENT_RE = re.compile(
    r"^(?:i said\b"
    r"|as i (?:have |had )?(?:said|mentioned|stated|noted)\b"
    r"|as i did\b"
    r"|i (?:have )?(?:mentioned|stated|noted)\b"
    r"|as (?:i )?(?:said|mentioned|stated) (?:earlier|later|above|before|just now)\b"
    r"|to repeat\b"
    r"|like i said\b"
    r"|as mentioned\b"
    r"|as (?:the|my) (?:hon|honourable) (?:member|friend)\b)", re.I)

# The opener alone is not enough to drop a sentence.
#
# Measured failure of the naive version: "As I said, the GST rate will not rise in 2026" is classified
# a restatement and lost, but it is a POLICY COMMITMENT with a date attached -- exactly what the
# citizen reader came for. What separates it from "I said that the statutory minimum annual leave
# goes up to a cap of 14 years" is that the second only describes an existing rule while the first
# commits to something forward-looking.
#
# So a restatement survives when its remainder carries a commitment the reader does not already have:
# a future modal, or a proposal/review verb. This is deliberately the narrow test -- it errs toward
# KEEPING, because hiding a policy move is the failure the owner reported and hiding context is a
# smaller sin than losing a commitment.
COMMITMENT_RE = re.compile(
    r"\b(?:will|shall|would|going to|intend|propose[sd]?|proposal|commit(?:s|ted|ment)?"
    r"|plan(?:s|ned)?|target(?:s|ted)?|review(?:ing)?|consider(?:ing)?|introduc(?:e|ing)"
    r"|implement(?:ing)?|extend(?:ing)?|increas(?:e|ing)|reduc(?:e|ing)|from \d{4}\b|by \d{4}\b"
    r"|effective\b|later this year|next year|in the coming)\b", re.I)

# Substance that is neither a modal nor a policy verb, but still the reader's reason to be here: the
# figure that was named, the horizon it was named against, or the Government as the actor.
#
# This was added after probing four real sentences the narrower test would have hidden:
#   "I said that the resale grant is up to $180,000."
#   "I said in my Budget Statement that we expect to fund the expenditure for the remainder of this
#    term of Government."
#   "I said yesterday that we are looking at the option of pushing up for vocational PWM..."
#   "I said earlier that payouts would grow from $731 monthly in 2026."
# Every one is a specific number or a stated horizon -- the most usable thing a citizen can take from
# a sitting. A restatement rule that eats the figures is worse than the nitpicking it replaces.
SUBSTANCE_EXTRA_RE = re.compile(
    r"[$£]\d"
    r"|\b\d[\d,.]*\s*(?:million|billion|per cent|percent|%|gigawatts?|days?|months?|years?"
    r"|monthly|daily|a month|a year|per year)\b"
    r"|\b(?:last|this|next) (?:year|month|week|term)\b|\byesterday\b|\bBudget Statement\b"
    r"|\bthe (?:Government|Ministry|Board|House) (?:will|expects|plans|intends)\b", re.I)

# A speaker correcting their OWN words, with nothing but the retraction in the sentence.
#
# The owner's judgement (2026-09-25): these are not policy moves, and highlighting them makes the
# tool sound nitpicking. Measured: 7 highlights in the whole corpus open this way. The narrow test is
# deliberate -- "What I meant to say was that several Members have asked if the Government will
# consider reducing fuel duty" KEEPS its place, because the correction is the frame and the policy is
# the content. What is held back is the sentence that only says "I misspoke" or restates a figure
# already on the page.
SELF_CORRECTION_RE = re.compile(
    r"^\s*(?:yes,?\s+(?:speaker|mr speaker|mdm speaker)\.?\s*)?"
    r"(?:i (?:misspoke|mis-spoke)\b"
    r"|i (?:meant|mean) to say\b"
    r"|what i (?:meant|mean)(?: to say)?\b"
    r"|i should have said\b"
    r"|i (?:was|am) wrong\b"
    r"|my mistake\b"
    r"|i correct myself\b"
    r"|to correct myself\b)", re.I)


def opener_remainder(text):
    """The sentence with its restatement opener removed."""
    return RESTATEMENT_RE.sub("", (text or "").strip(), count=1).lstrip(" ,:;-")


def is_chair(speaker):
    """True when the `speaker` field is the Chair."""
    return bool(CHAIR_SPEAKER_RE.match((speaker or "").strip()))


def is_housekeeping(text, speaker=None):
    """The Chair managing the House -- giving the floor, reading a question, declaring a vote.

    Requires BOTH that the speaker is the Chair and that the sentence is housekeeping. Either alone
    is wrong: housekeeping phrasing can appear in a member's speech, and the Chair's substantive
    rulings are worth keeping (owner's decision, 2026-09-25).

    `speaker=None` skips the speaker test and judges the phrasing alone, for callers that do not have
    it. That direction errs toward hiding, so callers with the speaker should always pass it.
    """
    tx = (text or "").strip()
    if not tx:
        return False
    if speaker is not None and not is_chair(speaker):
        return False
    return bool(FLOOR_MGMT_RE.search(tx)
                or QUESTION_READ_RE.search(tx)
                or CONSENT_RE.search(tx))


def carries_substance(remainder):
    """Does the part after an opener give the reader anything new to act on?

    Two tests, because a commitment needs a verb and substance often does not: a figure, a horizon,
    or the Government as the actor is enough. See SUBSTANCE_EXTRA_RE for the four measured sentences
    that forced the second test.
    """
    return bool(COMMITMENT_RE.search(remainder) or SUBSTANCE_EXTRA_RE.search(remainder))


def is_restatement(text):
    """A sentence that points back at what was already said AND adds nothing the reader can use.

    The opener alone does not decide it: see COMMITMENT_RE / SUBSTANCE_EXTRA_RE for the measured
    cases this guards ("As I said, the GST rate will not rise in 2026" must stay).
    """
    tx = (text or "").strip()
    if not RESTATEMENT_RE.match(tx):
        return False
    return not carries_substance(opener_remainder(tx))


def split_sentences(text):
    """Minimal sentence split, used only to ask whether a turn contains a retraction.

    Deliberately not the pipeline's splitter: this needs to find a phrase, not to reproduce the
    selection, and importing the selection code here would create a cycle (extractive imports this).
    """
    return [p for p in re.split(r'(?<=[.!?])\s+', (text or '').strip()) if p]


def is_self_correction(text):
    """A speaker retracting their own words, with nothing but the retraction in the sentence.

    Owner's judgement: not significant, and surfacing it reads as nitpicking. The correction is
    PROCEDURE about the speaker, not a move by the House -- unless the sentence goes on to say
    something the reader needs, which `carries_substance` decides.
    """
    tx = (text or "").strip()
    if not SELF_CORRECTION_RE.match(tx):
        return False
    return not carries_substance(opener_remainder(tx))


def correction_span(sentences, speakers=None):
    """Indices of the sentences that are a speaker correcting their own words.

    A correction comes in a PAIR, and neither half is significant alone:

        "I said that the statutory minimum annual leave ... goes up to a cap of 14 years."
        "I meant to say 14 days."

    The first half looks exactly like the figure-bearing restatements we deliberately keep ("I said
    that the resale grant is up to $180,000"), so no single-sentence test can separate them -- the
    only durable signal is that a retraction sits next to it. Hence a turn-scoped rule.

    Measured: 7 retractions corpus-wide, and the owner's annual-leave sentence was one half of one.
    Both halves go; the reader keeps the corrected figure that follows, which is the useful one.

    Speakers are compared when given, so one Member retracting does not sweep away the next Member's
    sentences -- a turn can carry several speakers in a written answer.
    """
    n = len(sentences)
    anchors = {i for i, t in enumerate(sentences) if is_self_correction(t)}
    if not anchors:
        return set()
    span = set(anchors)
    for i in sorted(anchors):
        # The retracted claim sits on either side: a Member may restate then correct, or retract then
        # restate. Only a restatement-SHAPED neighbour is drawn in, so the correction cannot reach out
        # and swallow the surrounding policy text.
        for j in (i - 1, i + 1):
            if 0 <= j < n and j not in anchors:
                if speakers and speakers[j] != speakers[i]:
                    continue
                if RESTATEMENT_RE.match(sentences[j].strip()):
                    span.add(j)
    return span


def classify_turn(sentences, speakers=None):
    """Per-sentence reasons for a whole turn, so the correction rule can see adjacent sentences.

    Returns a list aligned with `sentences`; None means the sentence is surfaced. Callers that hold
    whole turns must use this rather than classify(), or the pair rule cannot fire.
    """
    span = correction_span(sentences, speakers)
    out = []
    for i, t in enumerate(sentences):
        sp = speakers[i] if speakers else None
        out.append("self_correction" if i in span else classify(t, sp))
    return out


def classify_turn_text(selected, speakers, turn_text):
    """Reasons for the SELECTED sentences of a turn, given the turn's own words.

    Needed because the retraction is usually NOT itself selected -- the selector picks the claim, not
    the correction. The owner's case is the pure form: the whole turn reads

        "Yes, Speaker. I misspoke earlier. I said that the statutory minimum annual leave under the
         Employment Act goes up to a cap of 14 years. I meant to say 14 days."

    and the only sentence the selector chose was the middle one. A rule that pairs SELECTED sentences
    therefore finds no pair and holds nothing back, while the page still shows a correction as if it
    were a policy statement.

    So the turn's full text decides, and the selected restatement inside it goes. Measured: this is
    what separates the owner's annual-leave line from the 300 figure-bearing restatements we keep --
    those sit in turns that contain no retraction at all.

    `selected` and `speakers` are aligned, in spoken order.
    """
    out = classify_turn(selected, speakers)
    if not turn_text:
        return out
    if not any(is_self_correction(x) for x in split_sentences(turn_text)):
        return out
    # The turn corrects something, so a bare restatement in it is the thing being corrected.
    for i, t in enumerate(selected):
        if out[i] is None and RESTATEMENT_RE.match(t.strip()):
            out[i] = "self_correction"
    return out


def classify(text, speaker=None):
    """The reason a highlighted sentence is NOT significant, or None when it stands.

    Returns a short machine-readable reason so the count of exclusions can be reported BY REASON --
    an exclusion that is not recorded is indistinguishable from a bug, which is how an unanchored
    pattern once emptied whole items without anything noticing.
    """
    if is_housekeeping(text, speaker):
        return "chair_housekeeping"
    if is_self_correction(text):
        return "self_correction"
    if is_restatement(text):
        return "restatement"
    return None


# ---------------------------------------------------------------- near-duplicates
# Two highlights saying the same thing are one highlight. This is the class the phrase rules cannot
# reach: a Member re-arguing a point the Minister already made needs no shared wording, only shared
# content.
#
# Threshold measured, not chosen: at >=75% token overlap within a brief, 1,937 of 134,393 highlights
# (1.44%) are near-duplicates of an earlier one in the same brief. The samples are genuine repeats
# ("The Bill gives the Courts powers to order divorcing parents..." against a near-identical earlier
# line), not paraphrase-of-substance.
DUP_OVERLAP = 0.75
_STOP = {
    "the", "and", "for", "that", "this", "with", "have", "has", "had", "not", "are", "was", "were",
    "but", "from", "they", "their", "there", "which", "will", "would", "shall", "should", "been",
    "would", "could", "can", "may", "might", "must", "our", "its", "his", "her", "them", "these",
    "those", "than", "then", "when", "where", "what", "who", "whom", "how", "why", "all", "any",
    "more", "most", "other", "some", "such", "only", "also", "into", "over", "under", "about",
    "because", "while", "since", "after", "before", "being", "does", "did", "done", "very",
}


def tokens(text):
    """Content words of a sentence: >=4 letters, stopwords removed.

    Stopwords matter here. Keeping them lets two sentences sharing nothing but "the government will"
    score as overlapping; measured on the corpus this is what separates a working near-duplicate
    test from one that eats 20% of the highlights.
    """
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOP}


def overlap(a, b):
    """How much of the SHORTER sentence's content appears in the other.

    Asymmetric on purpose: a long paragraph that mentions everything should not be called a
    duplicate of every short line inside it. Dividing by the shorter side asks "is one of these two
    fully contained in the other", which is the restatement shape.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def duplicate_of(text, earlier, threshold=DUP_OVERLAP):
    """True when `text` restates any of `earlier` (a list of texts) above the threshold."""
    return any(overlap(text, e) >= threshold for e in earlier)
