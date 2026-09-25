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

## 8. Correction, 2026-09-25 (late): the cleanup was not on `origin/main`

The commit that carried out this triage was **reset away** (`git reflog`: `fe6ffcb6` → `reset: moving
to origin/main` → committed again). On re-check, the tree held four paths still dirty, all pointing
the *wrong* way:

| path | state found | what it was |
|---|---|---|
| `site/build_site.py` | 3,420 lines vs HEAD's 4,022 | the **pre-`94737f6e` generator**, recovered from a reset. Not a candidate for re-commit: its output is for the `data/` layout (`no sittings in data/`). |
| `tools/check_artifacts.py` | 265 lines vs HEAD's 409 | same class — a pre-reset copy. |
| `docs/completeness-check.md`, `docs/site-dist.md` | deleted from disk, never committed | this doc's §3 called them removable; both are referenced by `tools/verify_sitting.py:780` and `tools/test_deterministic.py:513` as the explanation for a "not lost text" finding. **Kept.** |
| `docs/kanban/t_393dfbc8/` | untracked, 8 files, ~200 KB | the debates/legislation ingest card from 22 Sep. Committed below. |

All four were resolved toward HEAD rather than against it: the two scripts restored, the two docs
restored, the card committed. Nothing here is a judgement that §1–§7 got wrong — it is that the tree
they were applied to was rebuilt underneath them, and the re-check read the files' *contents* rather
than their timestamps.

## 9. What the re-check turned up (open, not fixed here)

**The 42 `site/dist/skipped/*.json` payloads for 2016 are not reproducible from this tree's data.**
`docs/site-dist.md` §"skipped" holds these back from a commit because the fresh build and the
committed bytes disagree. Measured on HEAD's builder, building 331 pages in 18s:

| payload | committed rows | fresh build | relation of the two sets |
|---|---|---|---|
| `bill-223+224+225+226+227+228+229+230.json` | 3,814 | 3,928 | disjoint (`0` shared rows) |
| `bill-236.json` | 464 | 465 | `2` shared; 462 vs 463 differ |
| `oral-answer-1392.json` | 5 | 4 | 4 shared; 1 committed-only row |

The payload is `pipeline/dataset/<year>/<brief>.json` minus the summary's own sentences. The dataset
items are **gitignored derived artifacts** (`summariser/build_dataset.py`), and the committed payloads
predate the current dataset: every 2016 dataset file is dated **20 Sep**, the deployed payloads
**13:24 on 25 Sep**. So the two sides were built from different dataset generations, and the
divergence is inherited staleness rather than a defect in the generator. Reproducing the deployed
bytes would need the 20 Sep dataset, which no longer exists on this machine.

Consequence: the deployed payload for `bill-236` omits the sentence containing "Page: 71", which
today's dataset item carries. That is a reader-visible content difference on 42 of 3,863 briefs.
Closing it needs the owner's call — re-summarise and redeploy the 42, or accept them. It is **not**
fixed by committing a rebuild: that would change deployed content for briefs whose summaries were not
re-generated, which is the failure §1 exists to prevent.

**The `t_393dfbc8` card is committed but cannot be run in-tree.** Its suite imports `rag_schema` and
reads `ingest/test_debates.py`; neither exists in this tree (`ingest/` is absent, `tools/` holds no
`rag_schema.py`). The card was written against a layout that was not landed, and its `ingest_gates.py`
also calls four `docs/probes/ingest_*.py` probes that were removed with the layout-QA tree. Committed
because 200 KB of corpus-derived fixtures cannot be regenerated, and labelled here because "it is in
the repo" must not be read as "it runs". The live ingest path is `rag/build_db.py` (91,263 turns).

**`status.json` was four days stale** and reported `blocked_on: 10 unpushed commits` against what is
in fact a clean, fully-pushed tree. Refreshed — `tools/write_status.py` takes 2.3s including all 11
gate runs. Its `dirty_files` count is measured *before* the write that refreshes it, so it reads `2`
on a tree that the same commit leaves clean.

## Recover anything

Everything removed is at HEAD (`bd161d3e`) or earlier:

```bash
git show bd161d3e:docs/layout-qa/QA-REPORT.md
git show bd161d3e:tools/qa_pixels.py > /tmp/qa_pixels.py
```
