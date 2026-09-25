"""Adversarial test for the new eval-expectation logic (accept_any + one_per_turn).

The scorer decides PASS/FAIL for every question, so a bug in it silently mis-scores an entire
sprint. These cases each assert a specific behaviour, including the failure directions:
a right answer must pass, a WRONG answer must fail, and a partially-correct answer must not
be rounded up to correct.

Run:  python test_expect_logic.py
"""
import sys
sys.path.insert(0, '/Users/shawnlin/parsnips/rag/eval')

CASES = []


def score(expect, hay, facts=(), accept_any=(), one_per_turn=(), speakers=""):
    """Re-implement the scorer's fact logic exactly, and return the coverage verdict.

    Mirrors run_answer_eval.py: evidence = quotes ('hay') + cited speakers; required groups =
    accept_any (any member) + one_per_turn (any member of each group); coverage = met/total.
    """
    import re

    def B_norm(s):
        return re.sub(r"\s+", " ", (s or "").replace("\u2019", "'")).strip().lower()

    h = B_norm(hay) + " " + B_norm(speakers)
    found = [f for f in facts if B_norm(f) in h]
    groups = list(accept_any) + list(one_per_turn)
    g_ok = [g for g in groups if any(B_norm(m) in h for m in g)]
    n_all = len(facts) + len(groups)
    n_hit = len(found) + len(g_ok)
    coverage = (n_hit / n_all) if n_all else 1.0
    return coverage >= 0.999, coverage


# --- accept_any: the B3 shape -----------------------------------------------------------------
B3_SPK = ["Zaqy Mohamad", "Zainal Sapari", "Josephine Teo", "Grace Fu", "Masagos Zulkifli"]

CASES.append((
    "B3 accepts a valid alternative speaker",
    score("", "some quote text", accept_any=[B3_SPK], speakers="Mr Zaqy Mohamad"), True))
CASES.append((
    "B3 accepts the originally designated speaker",
    score("", "some quote text", accept_any=[B3_SPK], speakers="Mr Masagos Zulkifli B M M"),
    True))
CASES.append((
    "B3 REJECTS a speaker who said none of it",
    score("", "some quote text", accept_any=[B3_SPK], speakers="Mr Chee Hong Tat"), False))

# --- one_per_turn: the class-D shape ----------------------------------------------------------
D1 = [["470,000 households", "470,000"], ["electric by 2030"]]

CASES.append((
    "D1 passes when BOTH facts are quoted",
    score("", "470,000 households own cars. About half will be electric by 2030.",
          one_per_turn=D1), True))
CASES.append((
    "D1 FAILS when only the first fact is quoted",
    score("", "470,000 households own cars.", one_per_turn=D1), False))
CASES.append((
    "D1 FAILS when only the second fact is quoted",
    score("", "About half will be electric by 2030.", one_per_turn=D1), False))
CASES.append((
    "D1 passes with the bare number (an alternative spelling)",
    score("", "There are 470,000 car-owning households; electric by 2030.", one_per_turn=D1),
    True))

# --- the trivial-pass hole this was written to close ------------------------------------------
CASES.append((
    "a D question with NO gold_facts and NO groups would score a trivial 100% (hole)",
    score("", "anything at all"), True))
CASES.append((
    "so a D question MUST carry groups: with them, an empty answer fails",
    score("", "", one_per_turn=D1), False))

# --- facts and groups combine ---------------------------------------------------------------
CASES.append((
    "one fact + one group: missing the group fails",
    score("", "$10.5 billion to SMEs", facts=["$10.5 billion"],
          one_per_turn=[["73%"]]), False))
CASES.append((
    "one fact + one group: both present passes",
    score("", "$10.5 billion to SMEs, covering 73%", facts=["$10.5 billion"],
          one_per_turn=[["73%"]]), True))

# --- speakers may supply a fact -------------------------------------------------------------
CASES.append((
    "a fact found in the cited speaker field counts",
    score("", "unrelated quote", facts=["Masagos Zulkifli"], speakers="Mr Masagos Zulkifli"),
    True))

fails = 0
for name, (got, cov), want in CASES:
    ok = got == want
    if not ok:
        fails += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + ("" if ok else f"   (got pass={got} coverage={cov:.0%}, wanted pass={want})"))

print()
print(f"cases: {len(CASES)}   failing: {fails}")
sys.exit(1 if fails else 0)
