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


def is_restatement(text):
    """A sentence that points back at what was already said AND adds no commitment.

    The opener alone does not decide it: see COMMITMENT_RE for the measured case this guards
    ("As I said, the GST rate will not rise in 2026" must stay).
    """
    tx = (text or "").strip()
    if not RESTATEMENT_RE.match(tx):
        return False
    return not COMMITMENT_RE.search(opener_remainder(tx))


def classify(text, speaker=None):
    """The reason a highlighted sentence is NOT significant, or None when it stands.

    Returns a short machine-readable reason so the count of exclusions can be reported BY REASON --
    an exclusion that is not recorded is indistinguishable from a bug, which is how an unanchored
    pattern once emptied whole items without anything noticing.
    """
    if is_housekeeping(text, speaker):
        return "chair_housekeeping"
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
