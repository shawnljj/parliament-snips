# PRD — <project name>

> Status: DRAFT | AGREED | IN ITERATION N
> Owner: Shawn
> Last updated: YYYY-MM-DD
>
> **This document is append-only.** UAT feedback lands in `addenda/` and
> supersedes requirements here; it never rewrites them. A phase cites the
> requirement it implements; an addendum cites the delta it answers.

## 1. Problem

What is wrong today, for whom, and what does it cost them. One paragraph. If
this cannot be written in a paragraph, the problem is not yet understood.

## 2. Users

Who this is for, named concretely. "Everyone" is not a user. Note which user the
acceptance criteria below are written for.

## 3. Scope — and explicit non-scope

In scope for v1 is listed in the phases below. Non-scope is listed here, with
the reason, so that a later request to add it is a decision rather than a drift.

- **Not doing:** <thing> — <why>

## 4. Phases

Each phase is independently usable and independently UAT-able. A phase that
cannot be demonstrated to a human is not a phase, it is a task list.

| Phase | Deliverable | Exit criteria (UAT tests these) |
|---|---|---|
| 1 | <what ships> | <observable, testable statements> |
| 2 | | |
| 3 | | |

**Exit criteria must be observable by a human without reading the code.** "The
brief shows every sentence with its source link" is testable in UAT; "the
retrieval layer is correct" is not.

## 5. Acceptance criteria

Numbered, `AC1`, `AC2`, …, so UAT can report PASS/FAIL per item and an addendum
can cite the one it changes.

- **AC1** — <statement>
- **AC2** — <statement>

## 6. Constraints

Budget, deadline, platform, data sources, things that must not change.

## 7. Risks and open questions

Anything unresolved goes here as `OPEN` with the question. An invented answer is
worse than an open one — it makes a real question look settled.

- **OPEN** — <question>

## 8. Decision log

| Date | Decision | Why | Supersedes |
|---|---|---|---|
| | | | |
