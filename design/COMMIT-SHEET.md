# COMMIT-SHEET — parsnips case study

> Register: **build** (one surface, conventional executed at award level). Not
> `direct` — a case study a hiring manager scans in 90 seconds does not need a
> scroll-scrubbed film, and half-cinema is worse than either register done purely.
> Target file: `site/pipe-arch.html` (rebuild in place). Artifact class: portfolio
> case study. Voice: engineering notebook — it argues from evidence, and it shows
> the failures.

## 1. Signature element

**The provenance chain, made operable.** The page's peak is one real published
claim — *"The PSGA was introduced in 2018 to enable public agencies to share data
safely and purposefully."*, Ms Jasmin Lau, sitting 2026-01-12 — beside a
**four-step inspector the reader drives themselves**: claim → sentence id →
character offsets → a sha256 comparison that matches. Click through it and the
hash on the left equals the hash on the right, computed from a re-slice of the
actual archived source.

Everything else on the page supports that one object. It is the thing a visitor
describes to a friend ("you can re-derive the quote yourself, in the browser"),
and it is the whole thesis of the project in a single interaction — the site does
not ask you to trust a quotation, it hands you the means to check one.

> ✗ "beautiful pipeline animation"
> ✓ "the reader re-derives a real published quote from the archived source and watches two hashes match"

## 2. Color

**Tier: restrained, on the house palette the shipped site already uses** — this
page is part of a real product, so it inherits the product's tokens rather than
inventing a second identity. `--bg #fbfbfa`, `--ink #14181d`, `--accent #1c6b4a`
(deep green, the parsnip leaf), `--warm #b4622a`.

**Background lightness, as a number: mean L ≈ 0.98.** A lit page, and that is the
point. The reflex for an architecture write-up is a near-black grid-on-dark
diagram; every reference the previous version of this page came from did that
(and the page it is replacing did it too — `#020617`). This is a document about
*auditing a public record*: the reader is a person checking something, in
daylight, and the page should feel like an archival document, not a terminal.

**Why not the reflex.** Not the AI lavender/indigo, not the dark-blue developer
dashboard. Green is not decoration — it is the product's own leaf, and it
doubles as the "verified / passes" signal, which the page uses deliberately:
green marks a check that passed, `--warm` marks a withheld item, and no other hue
carries meaning. The four stage colours from the old page (cyan/amber/violet/rose)
are collapsed to **two semantic roles** (deterministic vs model) so colour is
load-bearing rather than decorative.

## 3. Type

**Display: a high-contrast serif for the case-study argument. Text: the system
grotesque stack the site already ships.**

The shipped product uses `-apple-system, BlinkMacSystemFont, "Segoe UI", Inter…`
for 16px body — correct for a reading product, and it stays. The case study gets
**one** added display face to signal "this is the long-form document, not the
product": a serif with real editorial weight at display sizes, loaded as a
variable font and used at three sizes (hero, section, pull-quote).

Axis: **high-contrast serif display × neutral UI grotesque.** Not a second sans
(reads as a mistake), not Inter for everything (the 2024–26 default — and it is
already here as the fallback, which is exactly why the display face must not be
another grotesque).

**Why not Instrument Serif or Playfair** — the reflex "elegant serif" pair on the
AI shortlist. The face has to hold up on dense technical prose and set a
2,000-word case study without fatigue.

## 4. Grid break

**The evidence ledger.** Every quantitative claim on the page is set as a
**two-column ledger row**: the figure, right-aligned in tabular figures, and its
provenance to the left in small text naming the file and command that produces it
(`data/manifest.json` → `python3 tools/metrics.py`). On narrow viewports the
ledger collapses to a stacked block, and the provenance line stays attached.

That ledger deliberately breaks the symmetric card grid: it is a **table of
receipts**, not a feature grid, and it is the mechanic that makes the page a
portfolio piece rather than a brochure — the reviewer can see the number and see
where it came from in the same glance.

Second break, smaller: the **failure case studies are full-bleed** (they escape
the prose measure) because each one shows *code and its consequence* side by side,
which needs more width than 68ch.

## 5. Motion budget

**Two families, both keyed to content.** Not uniform fade-up.

1. **Ledger count-in** — figures in the evidence ledger settle from a slightly
   offset position and a dim state to full ink as they enter the viewport, with a
   stagger of 40ms, because the ledger is a list and the motion says "read these in
   order". `IntersectionObserver`, `once: true`, `transform` + `opacity` only.
2. **Provenance reveal** — the four-step inspector's steps illuminate in sequence
   when the reader clicks *Run the check*, driven by the actual computation, not a
   timer. This is the one piece of motion on the page that is **storytelling**
   (motion.md's reason #2): it narrates a real verification.

Explicitly **not** used: scroll-jacked pinning, parallax, marquees, a custom
cursor, a scroll-instruction footer. `prefers-reduced-motion: reduce` removes the
transforms and keeps the opacity transitions and every state change — gentler, not
zero, which for the inspector means the steps light up without travel.

## 6. Reflex check

**a) What a generic AI produces for "architecture page for a data pipeline."**
Near-black `#0f172a`/`#020617` background, a monospace-everything treatment, an
SVG box diagram with grid pattern and glow, coloured status dots, sections
numbered `01 / 02 / 03`, and a card grid at the bottom summarising key points. I
know this precisely because **that is what the current `pipe-arch.html` is** — it
was built that way, and it is the reflex with receipts.

**b) What a generic AI avoiding (a) produces.** Cream/warm-beige editorial page,
serif headline, hairline rules, generous whitespace, "case study" as a template
with Problem → Approach → Results → Testimonial, no numbers in the body, and a
fade-up reveal on every section.

**c) The deviation, argued from this specific project.** This project's actual
thesis is *epistemic*: it is about whether a generated document can be checked
against its source. So the page is built as **an audit rather than a narrative** —
a lit archival document whose dominant visual device is the ledger row (figure +
the file that produces it), whose peak is a working verification rather than a
diagram, and which **documents its own four failures** as first-class content
instead of a results table of wins. The failure case studies are the reason this
is credible and not a marketing page: a portfolio piece that only shows the
finished state is indistinguishable from the reflex in (b).

## 7. House tells broken

1. **Near-black by default → a lit page at mean L ≈ 0.98.** The skill's own
   measurement says eight of nine showcase builds went dark at L ≈ 0.177. This
   page argues its way to daylight *because the subject is an archival audit*, and
   the drama is carried by contrast, typographic scale and the ledger rules
   instead of by a dark field.
2. **Mono service labels in the corners → the mono is confined to code, hashes and
   file paths**, where monospace is semantically correct (aligning hex digits, path
   strings). There are no tiny mono section labels, no `01 / 02 / 03` eyebrows, no
   status dot in the header.

Two further tells worth naming, since they are the ones this page was closest to:
the **"logo left / status centre / action right" header** is dropped (there is no
chrome; the case study opens on its own argument), and **amber as the one accent**
is replaced by the product's green, with `--warm` used only for withheld state.
