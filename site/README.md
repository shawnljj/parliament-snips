# site/ — the deployed site

**`site/dist/` is the live site.** It is what Vercel serves
(https://parliament-snips.vercel.app/), and it is committed on purpose: `vercel.json`
sets `outputDirectory: site/dist` and `buildCommand: null`, so the deploy publishes
these exact bytes rather than building anything.

```
site/
├── dist/          the deployed site — 331 sitting pages + index.html + read.css
└── archive/       old site v1 — not deployed, kept for reference
```

## Rebuilding dist

```bash
python3 rag/export_read.py --out site/dist        # all 331 sittings, ~30s
```

That is the only command that should write into `site/dist/`. It prints its own
verification block and ends in `RESULT: EXPORT VERIFIED`; the export refuses to call
itself verified if any page carries a live ask form, a broken internal link, or a
missing year-menu anchor. `tools/check_year.py` runs the same builder to a throwaway
directory as its RENDERABLE check, so the gate and the deploy measure the same thing.

`--depth 0` means "these pages sit at the site root", which is what makes the relative
links (`index.html#2016`) resolve on the static host. Do not change it for a root build.

## What is NOT here

The site went through two generations, and only the second one deploys:

| generation | builder | output | status |
|---|---|---|---|
| old site v1 | `archive/site-v1/build_site.py` | index + archive + `sittings/<date>.html` | **archived** — 404 on the live host |
| reading mode | `rag/export_read.py` | 331 sitting pages at the root | **live** |

The old builder is kept because its output was reviewed and its design notes are still
worth reading, not because anything runs it. Its routes (`/sittings/`, `/theme.css`) were
confirmed 404 on the live deploy before it was archived.

## Two traps worth knowing

**`archive/site-v1/build_case_study.py` writes into `dist/`.** Its default output is
`site/case-study.html` *and* `site/dist/case-study.html` (see its `OUT` / `OUT_DIST`).
The path it writes to is now stale — it would land in `archive/site-v1/` — but the
intent is the hazard: a generator for a retired page pointed at the live directory.
Nothing in the deployed site is produced by it. The Parsnips case study that ships
publicly lives in the portfolio repo, not here.

**`dist/` is tracked, so a build and a commit can disagree.** That is the failure
`archive/site-v1/docs/site-dist.md` was written about: two generators, one artifact, and
24 of 25 deployed 2017 pages ended up with pre-grid markup. The rule that came out of it
still applies — after rebuilding, commit the output in the same commit as the change
that produced it, and read the export's verification block rather than trusting a zero
exit code.
