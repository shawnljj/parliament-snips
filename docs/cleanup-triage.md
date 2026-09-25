# Triage: what to keep, what to drop

Run 2026-09-25, after the RAG pipeline commit (`95aeae4f`). The working tree held 379 modified,
325 deleted and 6 untracked paths left over from the layout-QA work of 21–22 Sep. This is the
assessment behind what was kept and what was removed.

Method: for every candidate, establish (a) what it is, (b) whether anything still references it,
(c) whether it is recoverable, and (d) whether it is reproducible. Judgement follows the evidence,
not the file's age.

---

## 1. `site/dist` — 376 modified files: NOT COMMITTED, reverted

These looked like a legitimate rebuild, and the reproduction test appeared to confirm it: a fresh
`python3 site/build_site.py /tmp/dist_fresh` matched the on-disk tree to within 5 files.

**That test asked the wrong question.** It confirmed the output was reproducible *from the working
tree* — but the working tree's `site/build_site.py` was itself a **stale version, dated 19 Sep
against HEAD's 22 Sep** (687 lines of documented rail/outline fixes missing). Building from the
*committed* source tells a different story:

| built from | differs from the on-disk `site/dist` by |
|---|---|
| the working tree's `build_site.py` | 5 files |
| **HEAD's `build_site.py`** | **339 files** |

So the on-disk `site/dist` is output that no committed source produces. Committing it would have
republished 339 files the repo cannot regenerate, and made the deployed site disagree with the code
in the same commit. It was reverted back to byte-identical with the original (0 differences), and
`site/build_site.py` is left uncommitted and untouched.

**The lesson, and it is the transferable one:** "reproducible" is only meaningful against the
version you intend to ship. Reproducing an artifact from the tree that happens to sit beside it
proves the two agree — not that either matches the source of truth. When a generator and its output
are both in the tree, check the generator's version against the one you are committing against.

**Separately, and unresolved:** `site/build_site.py` on disk is *older* than HEAD's. That is a
real condition worth the owner's attention — the working tree is behind on the site builder, so any
local build now produces the old layout. Left alone here because reverting it forward is a separate
decision from a cleanup.

## 2. The 5 files a fresh build does not produce: KEEP, they are deliberate

`case-study.html`, `pipe-arch.html`, `spike-funnel.html`, `spike-panels.html`, `spike-select.html`
exist only on disk. But all 5 are **tracked and byte-identical to HEAD**, and `build_site.py` has
zero references to their names.

That combination means they are not build output — they are hand-made pages that were committed
deliberately at some point and are not regenerated. Deleting them would destroy committed work
that no command can reproduce. Kept.

## 3. The layout-QA documentation: DELETE

Six directories under `docs/` — `layout-qa` (139 files, 9.3 MB), `layout-root` (44), `layout-after`
(30), `layout-audit` (28), `layout-tools-path` (26), `layout-outline` (14). 281 files, ~23 MB,
mostly screenshots.

Evidence for removal:

- **Nothing references them.** Zero matches for any of the six names across `README.md`,
  `PLAN.md`, `docs/`, `sdlc/`, `tools/`, `site/`, `scraper/`, `summariser/`.
- **They are the evidence trail of a finished investigation.** They were written 21–22 Sep during
  the layout/column-guide work, whose last commit is `bd161d3e` ("Widen the column-guide
  clearance…"). That work is done and its outcome is in the code.
- **They are recoverable.** Every file is at HEAD; `git show <commit>:<path>` restores any of them.

What survives: `docs/card-verification-chain.md` (referenced by README and PLAN), `STATUS-ARTIFACT.md`,
`CARD-FORMAT.md`, `3-lessons-learned.md`, and the `docs/kanban/` tickets with their code and reports.

## 4. 41 one-off `tools/` scripts: DELETE, except the verification chain

`tools/` had 55 files at HEAD and 14 on disk. The 41 removed are the layout-QA spike apparatus:
`qa_pixels.py`, `qa_frames.py`, `qa_gates.py`, `measure_layout.py`, `compare_layout.py`,
`compare_mobile*.py`, `probe_outline.py`, `check_rail_geometry.py`, `check_pbar.py`, the `_d15_*.sh`
session scripts, and similar.

These are **one-off probes**, evidenced by their shape: they hardcode viewport widths and pixel
coordinates for one specific layout question, and several are named after a single investigation
(`resolve_merge_a15_d13.py`, `qa_d15_pixels.py`). None is referenced anywhere surviving. All are at
HEAD.

**The exception — restore two files.** `tools/verify_sitting.py` and `tools/test_deterministic.py`
are not layout QA:

- They are the tooling behind `pipeline/verification.jsonl`, which commit `f3e5a10c` calls *"the
  mandatory Hansard completeness check"*.
- `docs/card-verification-chain.md` **still documents `tools/verify_sitting.py` as the way to run
  it**, and that card is referenced by `README.md` and `PLAN.md`.
- `README.md`'s status table lists "Verification chain ⬜ next".

So the mandatory check presently has **no script and no tracker in the tree** — the documented
entry point points at a file that is gone. Restored from HEAD.

## 5. `pipeline/verification.jsonl`: RESTORE

The tracker for the check above (1 line, scoped to 3 of 331 sittings, generated 2026-09-21). Its
writer was deleted alongside it. Restored so the verification chain is runnable and its history
intact — and because deleting a tracker whose script is being restored would make the chain look
like it had never run.

It is *partial* scope (`partial_scope: true`, 3 of 331 sittings) and records 2 failures. That is a
finding, not staleness — see `docs/card-verification-chain.md`.

## 6. `.worktrees/compare_phone.py`: DELETE

An 8 KB untracked script in a `.worktrees/` directory that is not a git worktree (`git worktree
list` shows only the main checkout). A stray from a removed worktree, referenced by nothing.

## 7. Kept as-is

- **`SPRINT-01-CLOSED.md`** (untracked, 8 KB) — the Sprint 01 closure record. Genuine project state.
  Committed.
- **`pipeline/chunk_summary.json`**, **`pipeline/para_store_summary.json`** — build outputs written
  by `rag/build_chunks.py` and `rag/build_para_store.py`; small, and the readable record of what
  those builds produced. Committed.
- **`pipeline/uat_verdicts.jsonl`** — 0 bytes, written and read by `rag/uat_server.py`. Kept as the
  empty UAT ledger; harmless and referenced.
- **`.hermes/`** — agent scratch, now gitignored. Belongs to the profile, not the repo.

---

## Recover anything

Everything removed is at HEAD (`bd161d3e`) or earlier:

```bash
git show bd161d3e:docs/layout-qa/QA-REPORT.md
git show bd161d3e:tools/qa_pixels.py > /tmp/qa_pixels.py
```
