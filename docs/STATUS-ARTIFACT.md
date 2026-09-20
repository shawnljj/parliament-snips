# Project status artifact — the contract

## Why this exists

COS cannot see project context. It should not need to. A bot that *has* the context
compresses it into a small artifact; COS reads artifacts and nothing else. That way
compression happens where the knowledge is, not at the reader.

**The rule that makes it work: every project bot writes this file on every run — including
when nothing happened.** A missing artifact is itself a signal (COS reports staleness), so
silence is never mistaken for success.

## The file

`status.json`, at the project root. Machine-read, human-tolerable.

```json
{
  "project": "parsnips",
  "written_at": "2026-09-20T14:05:00+08:00",
  "by": "parsnips-implementor",
  "status": "working",
  "changed_since_last": [
    "2022 re-fetched (35 sittings, 99.4% attributed) and dataset rebuilt",
    "2022 briefs generating"
  ],
  "gates": [
    {"name": "check_selection", "scope": "summaries/2022", "result": "pending"}
  ],
  "blocked": false,
  "blocked_on": null,
  "needs_decision": null,
  "severity": "low"
}
```

## Field rules

**`status`** — one of `working`, `blocked`, `idle`, `done`, `failed`. Not prose.

**`changed_since_last`** — what a reader would notice. Bullet-sized, not a changelog. If
nothing changed, use `[]` — do not invent activity.

**`gates`** — every check that ran, with its actual result. `check_selection: PASS, defects 0`
is a fact. `"all good"` is not a gate. **A gate that did not run is listed as `not_run`, never
omitted** — that is the difference between "passed" and "we didn't look".

**`blocked` / `blocked_on`** — true only when work cannot proceed without someone else acting.

**`needs_decision`** — the escalation channel. Non-null means Shawn must choose something.
**This field is what makes COS useful**: the project declares its own significance, because
only the project knows whether a 3% coverage drop matters.

**`severity`** — `low` | `normal` | `high`. Used by COS to decide whether to interrupt.

## Rules for the writer

1. **Write on every run**, even a no-op. Stale is worse than empty.
2. **Measure, never assert.** Every number in `gates` came from a command run in that session.
3. **Never write a gate result you did not run.** `not_run` is honest; a guessed PASS is not.
4. **Keep it under ~40 lines.** If it needs more, it is a report, not a status.

## Rules for COS

1. **Read only.** Never write a project's status — the project owns it.
2. **Report staleness.** No artifact in 3 days = a signal worth one line.
3. **Route `needs_decision`, stay silent otherwise.** An artifact with `needs_decision: null`
   and `severity: low` should not reach Shawn's phone.
4. **Never restate the artifact verbatim.** Say what changed since yesterday, not everything.
5. **Never judge significance the project already judged.** `severity` is the project's call.
