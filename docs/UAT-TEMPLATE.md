# UAT addendum N — <project name>, phase N

> Iteration: N
> Date: YYYY-MM-DD
> Testing: <what was demoed, and the exact revision/URL used>
> PRD revision tested: <commit sha of PRD.md>
>
> **Append-only.** This addendum supersedes requirements; it does not edit them.
> Do not change PRD.md to match what was built. Record the delta here.

## 1. What was tested

How it was exercised — the actual path taken, on which device or viewport, using
which data. "I tried it" is not a UAT record. Include what you did NOT test, so
the gaps are known rather than assumed covered.

## 2. Acceptance criteria results

| AC | Result | What I saw |
|---|---|---|
| AC1 | PASS / FAIL | <the observation, concretely> |
| AC2 | | |

A FAIL with no observation is not actionable. Write what happened, not that it
was wrong.

## 3. Deltas

New or changed requirements, numbered so a phase can implement them and a later
addendum can supersede them. `A<N>.<n>` — addendum N, delta n.

- **A1.1** — <new or changed requirement>
  - Why: <what in UAT provoked it>
  - Supersedes: <AC/requirement id, or `none`>

## 4. Defects

Things that are broken rather than undesired. Distinct from deltas: a defect is
"this does not do what we agreed", a delta is "we now want something else".

| # | What happens | Expected | Severity |
|---|---|---|---|
| D1 | | | blocks phase / defer / cosmetic |

## 5. What surprised me

Optional but the most valuable section over time. Where the build disagreed with
what the PRD assumed. This is where the next PRD gets better.

## 6. Next phase

Scope for the next iteration, as read from the PRD + every addendum to date.
State explicitly which deltas it absorbs and which remain open.
