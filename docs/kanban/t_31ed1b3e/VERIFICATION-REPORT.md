# Kanban artifact routing — independent verification report

Task: t_31ed1b3e (verifier). Change under test: t_7cba6105 — kanban deliverables
route into the board's project repository and are committed there.

Board under test: `parsnips` (default_workdir `/Users/shawnlin/parsnips`,
DB `/Users/shawnlin/.hermes/kanban/boards/parsnips/kanban.db`).
Code under test: `/Users/shawnlin/.hermes/hermes-agent` (working tree, uncommitted;
modified files: 10 changed + 2 added, i.e. the t_7cba6105 diff).

Verdict: **routing works for every artifact type, and the artifacts that land in
the parsnips repo are git-tracked.** No failures found. All evidence below is
from commands actually run in this session.

## How verification was done (and why)

This worker is a `delegate_task`-fenced Kanban worker
(`HERMES_DELEGATED_CHILD_CONTEXT=/Users/shawnlin/.hermes`), so
`kanban_db._assert_not_delegated_child_mutation` refuses every board mutation
this process makes. Writing test artifacts into the live `parsnips` board was
therefore both impossible and undesirable.

Instead the production code path was driven end to end on **two sandbox boards**
under an isolated `HERMES_KANBAN_HOME`, using the same CLI commands and the same
`kanban_db` functions the dashboard and the dispatcher call:

    /Users/shawnlin/.hermes/profiles/dev1/cache/scratch/kanban-verify-home   (HERMES_KANBAN_HOME)
    /Users/shawnlin/.hermes/profiles/dev1/cache/scratch/parsnips-artifact-verify  (stand-in project repo, real git repo)
    board verify-routed    -> default_workdir = that repo   (routing ON)
    board verify-unrouted  -> no default_workdir            (routing OFF, old behaviour)

The live `parsnips` board was then checked read-only for integrity, and the
read-side consumers (gateway delivery gate, dashboard download guard, codex
sandbox roots, dispatcher env pinning) were exercised against the real board.

## Commands and observed output

### A. Live routing on the real board and repo (read-only)

    $ git -C /Users/shawnlin/parsnips log --oneline -- docs/kanban
    ade0e12e docs: kanban artifacts for t_7cba6105 — Redirect kanban artifact output to the parsnips repository

    $ git -C /Users/shawnlin/parsnips ls-files docs/kanban
    docs/kanban/t_7cba6105/CHANGELOG.md

    $ find /Users/shawnlin/parsnips/docs/kanban -type f
    /Users/shawnlin/parsnips/docs/kanban/t_7cba6105/CHANGELOG.md

    $ git -C /Users/shawnlin/parsnips status --porcelain | grep docs/kanban
    (no output — every routed path is committed, tree clean under docs/kanban)

    $ git -C /Users/shawnlin/parsnips check-ignore -v docs/kanban/t_7cba6105/CHANGELOG.md
    (exit 1, no output — NOT ignored)

    $ sqlite3 ~/.hermes/kanban/boards/parsnips/kanban.db \
        "select id,task_id,filename,stored_path from task_attachments order by id;"
    ...
    35|t_7cba6105|CHANGELOG.md|/Users/shawnlin/parsnips/docs/kanban/t_7cba6105/CHANGELOG.md
    (34 older rows still point at the board-local attachments dir — see "no losses")

### B. Isolation harness: routing resolution per board

    $ ./venv/bin/python 06-consumers.py
      parsnips           repo=/Users/shawnlin/parsnips            root=/Users/shawnlin/parsnips/docs/kanban
      sme-grants-checker repo=/Users/shawnlin/sme_grants_checker  root=/Users/shawnlin/sme_grants_checker/docs/kanban
      verify-routed      repo=None root=None        # (that probe deliberately ran against the real ~/.hermes, where the sandbox boards do not exist)
      verify-unrouted    repo=None root=None
      digital-products   repo=None root=None
      default            repo=None root=None

### C. Each artifact type, end to end (sandbox board `verify-routed`)

1. **`hermes kanban attach`** (CLI) — managed-scratch card t_9ba7849b:

       $ hermes kanban --board verify-routed claim t_9ba7849b
       Claimed t_9ba7849b
       Workspace: <HOME>/kanban/boards/verify-routed/workspaces/t_9ba7849b

       $ hermes kanban --board verify-routed attach t_9ba7849b <ws>/report.md --name report.md
       Attached report.md to t_9ba7849b (attachment 1, 43 bytes)

       attachment row: 1|report.md|dev1|<REPO>/docs/kanban/t_9ba7849b/report.md
       board-local attachments tree: (empty)
       NOT-IGNORED
       commit: 5dc1179 docs: kanban artifacts for t_9ba7849b — verify routed artifact (managed scratch)
                docs/kanban/t_9ba7849b/report.md | 1 +

2. **`kanban_complete(artifacts=[...])`** (the tool's declared-deliverable path) —
   same card, two artifacts: one inside the managed scratch workspace
   (`<ws>/second.txt`) and one at an absolute path outside it:

       attachment rows: 2|second.txt|kanban_complete|<REPO>/docs/kanban/t_9ba7849b/second.txt
                        3|t_9ba7849b-abs.md|kanban_complete|/…/kanban-verify/t_9ba7849b-abs.md
       repo tree: docs/kanban/t_9ba7849b/{report.md,second.txt}
       board-local attachments tree: (empty)

   Observed split, worth knowing but not a defect: the artifact that lived
   **inside** the managed scratch workspace is routed into the repo; the one at
   an absolute path **outside** the workspace is stored board-locally by the
   pre-existing preservation path (`kanban_db.py:3092-3095`). Routing of
   out-of-tree absolute paths happens on the worktree/dir branch
   (`_route_declared_artifacts`, `kanban_db.py:3151-3184`) — confirmed next.

3. **`kanban_complete` on a worktree card** t_aa63a776 (workspace =
   worktree with `.worktrees/<id>`, `workspace_path` present):

       attachment rows: 4|report.md|dev1|<REPO>/docs/kanban/t_aa63a776/report.md
                        5|second.txt|kanban_complete|<REPO>/docs/kanban/t_aa63a776/second.txt
                        6|t_aa63a776-abs.md|kanban_complete|<REPO>/docs/kanban/t_aa63a776/t_aa63a776-abs.md
       repo tree: docs/kanban/t_aa63a776/{report.md,second.txt,t_aa63a776-abs.md}
       commits: fe48910 (report.md), 71ff42a (second.txt + t_aa63a776-abs.md)
       repo status: "?? .worktrees/" only — the worktree was NOT swept into the commit

   So on the worktree branch the out-of-tree absolute artifact *is* routed.

4. **`kanban_request_review(artifacts=[...])`** — card t_5ee6e7de:

       $ hermes kanban --board verify-routed request-review t_5ee6e7de --metadata '{"artifacts":["<ws>/review-evidence.md"]}'
       Requested review for t_5ee6e7de: routing verification (review handoff)

       attachment row: 7|review-evidence.md|kanban_request_review|<REPO>/docs/kanban/t_5ee6e7de/review-evidence.md
       commit: 3d16d60 docs: kanban artifacts for t_5ee6e7de — verify routed artifact (request-review path)

5. **Dashboard upload** — the real coroutine behind
   `POST /api/kanban/tasks/{task_id}/attachments`
   (`plugins/kanban/dashboard/plugin_api.py:434-467`) called in-process with an
   `UploadFile`, card t_98052555:

       route return: {"attachment": {"id": 8, "task_id": "t_98052555",
                      "filename": "dashboard-upload.md", …,
                      "stored_path": "<REPO>/docs/kanban/t_98052555/dashboard-upload.md"}}
       stored_path: <REPO>/docs/kanban/t_98052555/dashboard-upload.md
       exists: True size: 25
       inside repo: True
       under routed root: True
       board-local dir exists: False

6. **Fallback (board with no repo) — old behaviour intact** — card t_0fd9b17c
   on `verify-unrouted`:

       attachment rows: 1|report.md|dev1|<HOME>/kanban/boards/verify-unrouted/attachments/t_0fd9b17c/report.md
                        2|second.txt|kanban_complete|<HOME>/kanban/boards/verify-unrouted/attachments/t_0fd9b17c/second.txt
       target repo tree: (empty — nothing written into any repo)
       board-local dir: created, as before

7. **Old destination quiet on the real board**: the newest file still under
   `~/.hermes/kanban/boards/parsnips/attachments/` is
   `t_a604d36d/…-repo-inspection.md`, mtime 2026-09-22T10:34:07 — i.e. the last
   write before routing was applied. Nothing has landed there since.

### D. Commit semantics (adversarial, sandbox repo)

    # operator's half-staged index + an ignore rule + untracked noise
    $ git -C <REPO> add unrelated-staged.txt          # pre-staged, must NOT ride along
    $ cat <REPO>/.gitignore                           # *.tmp
    $ hermes kanban --board verify-routed attach t_19867f0b <src>/…-tmp-report.tmp --name report.tmp
    Attached report.tmp to t_19867f0b (attachment 9, 46 bytes)

    $ git -C <REPO> log --oneline -1 --stat
    8db7b77 docs: kanban artifacts for t_19867f0b — verify adversarial commit semantics
     docs/kanban/t_19867f0b/report.tmp | 1 +
     1 file changed, 1 insertion(+)

    $ git -C <REPO> diff --cached --name-only
    unrelated-staged.txt          # still staged, NOT committed — path-limited commit holds
    $ git -C <REPO> ls-files docs/kanban/t_19867f0b/
    docs/kanban/t_19867f0b/report.tmp   # `git add -f` beat the *.tmp ignore rule

### E. Read-side consumers against the real board

    $ ./venv/bin/python 06-consumers.py
    == gateway delivery allowlist (gateway/platforms/base.py) ==
      /Users/shawnlin/.hermes/kanban/attachments
      /Users/shawnlin/.hermes/kanban/boards/digital-products/attachments
      /Users/shawnlin/.hermes/kanban/boards/sme-grants-checker/attachments
      /Users/shawnlin/.hermes/kanban/boards/parsnips/attachments
      /Users/shawnlin/sme_grants_checker/docs/kanban
      /Users/shawnlin/parsnips/docs/kanban
    parsnips routed root present: True

    $ ./venv/bin/python 11-delivery-gate.py     # the real predicate, not just the root list
    == mode=default ==
      /Users/shawnlin/parsnips/docs/kanban/t_7cba6105/CHANGELOG.md    (accepted)
      /Users/shawnlin/.hermes/kanban/boards/parsnips/attachments/…md (accepted)
      /etc/passwd                                                    (None — denied)
    == mode=strict (HERMES_MEDIA_DELIVERY_STRICT=1) ==
      same three results — the routed artifact is accepted in strict mode too

`validate_media_delivery_path` returns the routed artifact in both modes, so the
completion-notification upload cannot silently drop a routed deliverable.

### F. Dispatcher worker env (what a real worker is handed)

    $ ./venv/bin/python 09-worker-env.py
    HERMES_KANBAN_ARTIFACT_REPO=/Users/shawnlin/parsnips
    HERMES_KANBAN_ARTIFACTS_ROOT=/Users/shawnlin/parsnips/docs/kanban
    ARTIFACTS_ROOT == routed root: True

Both pins match `kanban_db_dispatch.py:2860-2870`; `HERMES_KANBAN_ARTIFACTS_ROOT`
is the same env var the codex sandbox reads
(`agent/transports/codex_app_server.py`: `spawn_env.get("HERMES_KANBAN_ARTIFACTS_ROOT")`).

### G. No losses / no regressions

    $ ./venv/bin/python 08-integrity.py
    attachment rows=35 routed-in-repo=1 board-local=34 missing=0
    git-tracked under docs/kanban: ['docs/kanban/t_7cba6105/CHANGELOG.md']
    newest file still in board-local store: 2026-09-22T10:34:07 t_a604d36d/…
    total working-tree entries=1027 entries under docs/kanban=0

Every one of the 35 attachment rows on the live board resolves to a file that
exists (**0 missing**); the 34 pre-redirect rows kept their original paths and
contents. The pre-existing unrelated working-tree noise in the primary checkout
is still exactly 1027 entries, and the routed commit added nothing to it.

    $ ./venv/bin/python -m pytest tests/hermes_cli/test_kanban_artifact_routing.py -q
    12 passed

    $ pytest tests/hermes_cli -k kanban -q -rf     (modified tree)  -> 21 failed, 451 passed, 10 skipped
    $ pytest tests/hermes_cli -k kanban -q -rf     (pristine HEAD worktree) -> 21 failed, 439 passed, 10 skipped
    only in MODIFIED (new regressions): NONE
    only in BASELINE (newly fixed):     NONE
    identical failure sets: True

A/B against a pristine worktree of HEAD (`524041b9d0`): the failure set is
byte-identical, so the 21 failures are environmental (pre-existing hook / WAL
host dependencies), not caused by this change. The change adds exactly the 12
new tests, all passing.

## Acceptance criteria

- All artifact types confirmed present in the parsnips repo: **yes** for
  `kanban_complete`, `kanban_request_review`, `kanban_attach` and the dashboard
  upload — proven on the sandbox boards, with the live t_7cba6105
  `CHANGELOG.md` as the real-board instance of the `kanban_attach` path.
- Artifacts git-tracked, not ignored: **yes** — `git ls-files` lists the routed
  file, `git check-ignore` returns exit 1, and the routed commit is on `main`.
- Nothing still writes to the old destination: **yes** — the board-local
  attachments store is untouched since 10:34:07, and the fallback board still
  gets board-local storage by design.
- No broken paths / errors / lost artifacts: **yes** — 0 missing files of 35
  rows, and the pytest failure set is identical to pristine HEAD.

## Notes (not failures)

1. Scope: only *declared deliverables* are routed. Board DB + WAL/SHM,
   `workspaces/`, `logs/`, locks and export archives still live under
   `~/.hermes/kanban/` — that is the documented intent of the change, and the
   inventory task t_54e7c21c flagged those as runtime state. The user's original
   request ("move all artifacts") is only partially satisfied by this change if
   the raw worker logs were also meant to be versioned; those still have no
   routing path or env override (`kanban_db.py:530-533`).
2. An artifact declared by absolute path outside a **managed scratch** workspace
   stays board-local (case 2); the same artifact on a **worktree** card is routed
   (case 3). Consistent with the code, but surprising for workers that write to
   `/tmp`.
3. `docs/kanban/*` is not covered by a `.gitignore` rule, but note that
   `parsnips/.gitignore:14` (`*.tmp`) would match a routed deliverable named
   `*.tmp`; `commit_artifacts` uses `git add -f` with explicit paths, and that
   case was tested (section D).
