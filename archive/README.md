# archive/ — retired work, kept on purpose

Nothing here runs, and nothing here deploys. It is kept because it was reviewed, or
because it records a decision, and deleting it would destroy work no command reproduces.
If you are looking for the live product, you want `site/dist/` (see `site/README.md`) and
`rag/` (the reading-mode pipeline that builds it).

```
archive/
├── site-v1/     old site v1 — its generator, its hand-made pages, its design docs
├── mockups/     reading-mode design explorations (the directions that were not chosen)
└── spikes/      one-off feasibility scripts from before the pipeline existed
```

## site-v1/ — the superseded site

| path | what it is |
|---|---|
| `build_site.py` | old site v1 generator: index + archive + `sittings/<date>.html`. Its output is 404 on the live host. |
| `build_arch.py` → `pipe-arch.html` | the pipeline-architecture page, built from `parts/` |
| `build_case_study.py` → `case-study.html` | the long-form case study. **Its default output also wrote `site/dist/case-study.html`** — a retired page aimed at the live directory. See `site/README.md`. |
| `parts/` | hand-authored SVG diagram, ERD and schema tables used by `build_arch.py` |
| `pipeline/index.html` | a hand-made pipeline dashboard page |
| `docs/site-dist.md` | **the record of why tracking `dist/` is dangerous.** Written about v1, but its lesson carried over to the current site unchanged. |
| `docs/cleanup-triage.md` | the 2026-09-25 triage: what was kept, dropped and restored, with the evidence for each. |
| `DESIGN.md`, `COMMIT-SHEET.md` | the style contract for `case-study.html` |

The two docs are the reason this directory is worth keeping at all: they document
failures that cost days, and the failures were in the *process*, not in v1's code.

## mockups/ — reading-mode design explorations

`reading-directions.html`, `reading-index.html`, `reading-fold.html`, `reading-margin.html`
are four complete layouts of the same real sitting (7 Feb 2024, 23 briefs), used to choose
a direction. `READING-DIRECTIONS.md` is the write-up; `shots/` holds the captures;
`mockup-hero.html` and `reading-data.js` are the shared scaffolding.

The chosen direction shipped — see `rag/read_server.py` and the live site. These are the
ones that did not, kept so the comparison is still inspectable.

## spikes/ — pre-pipeline feasibility work

`spike_select.py` (does selection work at all?), `spike_funnel.py` (the selection funnel),
`spike_panels.py` (panel extraction). Each answered one question before the pipeline
existed. Superseded by `summariser/`, which is what actually builds briefs.

## Recovering anything else

Everything archived here is reachable from git history too:

```bash
git log --follow -- archive/site-v1/build_site.py     # the full history of a moved file
git show <sha>:<old-path>                             # any earlier version
```
