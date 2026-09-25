# CHANGELOG — kanban artifact routing into the parsnips repository

Card `t_7cba6105`. All paths relative to the Hermes code tree
`/Users/shawnlin/.hermes/hermes-agent`.

## What changed

Kanban's **declared deliverables** — the files a worker lists in
`kanban_complete(artifacts=[...])` / `kanban_request_review`, plus every explicit
`kanban_attach` — are now written into the board's project repository at
`<repo>/<kanban.artifact_dir>/<task_id>/` and committed there.

Runtime state deliberately does **not** move: `kanban.db` (+wal/shm), the
`workspaces/` tree (measured 19.6 GB), `logs/`, locks and pidfiles stay in the
board directory. The inventory card (`t_54e7c21c`) measured those as runtime
state rather than artifacts, and dragging them into a working tree would put a
live SQLite DB and 20 GB of throwaway clones under version control.

Resolution order for the repository (`hermes_cli/kanban_artifacts.py`):
`HERMES_KANBAN_ARTIFACT_REPO` env pin (injected into every worker by the
dispatcher) → `kanban.artifact_repo` config → the board's `default_workdir`
(`board.json`), used only when it is a git work tree. For board `parsnips` that
is `/Users/shawnlin/parsnips` → `docs/kanban/<task-id>/`, matching the existing
`docs/layout-*/` convention the repo-inspection card (`t_a604d36d`) identified.

When nothing resolves — no config, no `default_workdir`, or a workdir that is not
a git repo — behaviour is byte-for-byte the old one: the board's own
`attachments/<task_id>/`.

## Files modified (10) / added (2)

| File | Change |
|---|---|
| `hermes_cli/kanban_artifacts.py` | **NEW.** The resolver: repo/dir/task-dir resolution, the copy into the repo, the path-limited commit, the `artifact_commit` toggle. No kanban imports at module level (reads `kanban_db.read_board_metadata` lazily), so no cycle. |
| `hermes_cli/kanban_db.py` | `attachment_target_dir()` — the one "where does this blob go" decision, used by `store_attachment_bytes()`; `_persist_scratch_completion_artifacts()` routes staged copies into the repo (falling back to board-local) and gains a second path for declared artifacts outside a scratch workspace; `_stage_completion_artifacts()` records the routed path; `_commit_task_artifacts()` + `_current_board_for_conn()` commit a card's artifacts after `complete_task` / `request_review`; `_discard_staged_copies()` takes roots (board + repo) instead of assuming the board dir. |
| `hermes_cli/config_defaults.py` | Three new `kanban.*` keys: `artifact_repo` (`""` = infer), `artifact_dir` (`docs/kanban`), `artifact_commit` (`true`). |
| `hermes_cli/kanban_db_dispatch.py` | Worker env pin: `HERMES_KANBAN_ARTIFACT_REPO` + `HERMES_KANBAN_ARTIFACTS_ROOT`, next to the existing DB/workspaces pins. |
| `gateway/platforms/base.py` | `_kanban_attachment_roots()` also returns each board's routed root. Without this the notifier's delivery allowlist would silently refuse to upload a routed artifact. |
| `plugins/kanban/dashboard/plugin_api.py` | Upload writes to `attachment_target_dir()` and commits a routed upload; the download guard accepts a blob under the board's routed root as well as its attachments root. |
| `hermes_cli/kanban_transfer.py` | Import rehomes rows from either location; export carries the routed `<task_id>/` trees into the archive's `attachments/` (same layout the import side rehomes from). |
| `agent/transports/codex_app_server.py` | Codex sandbox writable roots gain the routed artifact root (the repo is usually outside the workspace, so the copy would fail without the grant). |
| `hermes_cli/kanban.py` | `attach` passes the global `--board` through, so a routed write targets the right repository when it is not the active board. |
| `hermes_cli/backup.py` | Comment only — routed artifacts are covered by the repo's own git history; the quick-snapshot set is unchanged. |
| `tools/kanban_tools_schemas.py` | `kanban_complete` / `kanban_request_review` artifact descriptions now name the routed destination. |
| `tests/hermes_cli/test_kanban_artifact_routing.py` | **NEW.** 11 tests: routing + commit, forced tracking of an ignored extension, pathspec isolation (no `git add -A`, `.worktrees/` untouched, unrelated staged work preserved), no push, fallback with no repo, non-git workdir, env override, `..`/absolute sanitization, gateway allowlist, `artifact_commit: false`, declared artifact outside scratch. |

## .gitignore

No change required or made. `git check-ignore -v` in the parsnips worktree
confirms `docs/kanban/<task>/CHANGELOG.md`, `raw-*.log`, `*.png`, `*.json` are
all tracked by default. The one repo-wide rule that could swallow a deliverable
is `*.tmp` (line 14), so the commit uses `git add -f -- <explicit paths>` — a
declared artifact is never silently excluded by an ignore rule. The inverse
hazard flagged by the discovery cards is covered too: paths are staged
explicitly (never `git add -A`), so `.worktrees/`, `dev2-parsnips/cache/scratch/`
and other untracked runtime state cannot be swept in.

## Git behaviour

Commit only, never push: `main` is 40 commits ahead of `origin/main` and
publishing the branch is the operator's call (flagged by `t_a604d36d`). The
commit is path-limited on both `add` and `commit`, so an operator's half-staged
index cannot ride along; a repository with no `user.email` gets a one-off
`-c user.name/hermes-kanban -c user.email/kanban@hermes.local` identity instead
of failing. All git work runs outside the completion transaction and all failures
are logged and swallowed — artifact routing never blocks a card from closing.

## Verified

* New suite: `tests/hermes_cli/test_kanban_artifact_routing.py` — 11 passed.
* Regression set (`test_kanban_transfer`, `test_kanban_boards`,
  `test_kanban_board_project`, `test_kanban_db`, `test_kanban_review_lifecycle*`,
  `tests/plugins/test_kanban_attachments`, `tests/plugins/test_kanban_dashboard_plugin`,
  `tests/tools/test_kanban_tools`, `tests/agent/transports/test_codex_app_server_runtime`):
  pass except `test_kanban_db.py::test_infrastructure_spawn_refusal_never_charges_the_card`,
  which fails identically on the unmodified HEAD (verified in a detached worktree).
* `tests/hermes_cli/ -k kanban` run against a pristine `git worktree` of HEAD as
  the baseline: no failure attributable to this change.
* End-to-end through the real CLI on a throwaway home + repo: `hermes kanban
  attach` and `hermes kanban complete --metadata '{"artifacts": [...]}'` both
  wrote into `<repo>/docs/kanban/<task>/`, each produced its own commit, the
  working tree stayed clean, and the board's old `attachments/` dir was never
  created. `hermes kanban boards export` carries them; import rehomes the rows
  into the new board.
* Live boards: `parsnips` → `/Users/shawnlin/parsnips/docs/kanban`,
  `sme-grants-checker` → `/Users/shawnlin/sme_grants_checker/docs/kanban`,
  `default` / `digital-products` (no workdir) → unchanged board-local behaviour.

## Not in scope

Worker stdout/stderr logs (24 MB of transcript, no env override today), the board
DB, export archives (a snapshot to hand around, not a deliverable), and
per-profile transcripts/spill. All are listed as runtime state in the inventory
and stay where they are.
