# Board note — 2026-09-21

## Cards the board completed by itself, with no work done

Two cards were moved to `done` while nobody was working on them:

    t_608872bf  "Fix 2016: 672 defects across 24 stale briefs"   completed 14:28:50
    t_f8aac388  "Record the 9 items that produced no brief..."   completed 14:29:18

Both carry the same signature, which is how they were identified:

    task_events.kind = 'completed'
    payload          = {"result_len": 4, "summary": "done"}
    task_runs        = profile NULL, step_key NULL, worker_pid NULL,
                       started_at == ended_at (0 seconds elapsed)

Meanwhile, as of the same moment:

    summaries/2016 gate    FAIL — 672 defect(s)   (the card's DONE WHEN, unmet)
    pipeline/withheld/     7 records               (the 9 items still unrecorded)

So neither card's DONE WHEN was satisfied. Both were reverted to `ready` by hand,
then moved to `review` once evidence was attached.

## Why this matters more than the two cards

The board reported success for work that had not happened, and the DONE WHEN on
each card is a runnable command that returns a failure. A tracking surface that
completes cards on its own is worse than no tracking surface, because the failure
is indistinguishable from a pass at a glance — the same class as the `no_summary`
defect in `check_selection.py` (measured, printed, then excluded from the verdict).

The signature is detectable: a completed run with zero elapsed time and a null
profile. That is worth a regression case (card `t_b309479b`).

## Not established

The actor was not identified. The gateway's embedded dispatcher is the only
component observed handling these tasks (`kanban dispatcher: embedded in gateway,
interval=60.0s`, holding the singleton lock), and both completions fall inside its
60-second tick. Its dry run reports all six cards as "Skipped (unassigned)", so
`kanban.dispatch_in_gateway: true` alone does not reproduce it. No log line in
`gateway.log`, `gateway.error.log` or `agent.log` names either task id.

`kanban.default_assignee` is unset in `config.yaml`, which rules out the documented
auto-assign path.

Until the actor is found, treat any `done` on this board as unverified without its
DONE WHEN having been run.

## Lane movement

`hermes kanban request-review` refuses `done -> review`:

    cannot request review for t_a43d345b: task is not in running/ready

It accepts only `running` or `ready`. The four evidenced cards were therefore moved
to `review` by a direct `UPDATE` on the board DB, with the reason recorded as a
comment on each card. A DB backup was taken first:
`kanban.db.bak-<epoch>`.

Verified the dispatcher leaves them alone: a dry run after the move reports all of
them under "Skipped (unassigned)".

## Also found

`sdlc/1-objectives.md` prose says "11 of 37 objectives", but only **36** are
declared. Off by one, unfixed; `tools/check_artifacts.py` reports 36.
