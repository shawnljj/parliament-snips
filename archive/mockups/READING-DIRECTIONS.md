# Reading mode — design directions

**Open on your phone:** http://100.93.66.68:8443/reading-directions.html

Three complete directions, same real content: the sitting of **7 February 2024** — 23 briefs,
91,757 words, 387 turns. Including the motion with **202 sections** and a written answer of
**54 words**. Nothing invented, no lorem ipsum.

## What each direction is

| | Margin | Index | Fold |
|---|---|---|---|
| **idea** | the sitting as a printed document you annotate | the sitting as a catalogue you navigate | the sitting as a stack of briefs that open where you tap |
| **reading type** | Georgia serif 16/1.62 | system sans 13.5/1.55 | system sans 14.5/1.58 |
| **the summary** | a marginal note above the transcript | a numbered section head | the closed state of a fold |
| **transcript** | always visible, sentence-highlighted | always visible, in a bordered rail | behind a tap, 2 sections open by default |
| **accent** | `#1c6b4a` shipped green | `#0b5c8a` blue | `#14532d` deep green |
| **read view, the 202-section motion** | **13.0 screens** | 9.6 screens | **2.8 screens** |
| **home view** | 3.0 screens | 3.5 screens | 4.0 screens |

Measured at 390×844 in-browser, not estimated. For comparison, the **shipped** sitting page is
**211 screens** at the same viewport.

## Verified, all three

- **0 horizontal overflow** at 360px and 390px
- **0 tap targets under 44px** (minimum exactly 44)
- **Text contrast 10.4–18.7:1** — including 11.6:1 for text on the amber highlight
- All screens reachable: search → home → read → answer → jump back into the passage
- **Slop audit: 0/10** on every direction (no gradients, no glassmorphism, no icon toppers,
  no centred stacks, no indigo default, no feature-tile grid)

Files: `reading-margin.html`, `reading-index.html`, `reading-fold.html`, `reading-data.js`
(shared data), `shots/` (phone screenshots of each).

## The one structural finding the design has to handle

Brief length on this sitting is **bimodal**:

- `motion-2310+2312+2315` — *Advancing Mental Health* — **10,636 words, 202 sections**
- `motion-2316+2318` — *Public Finances* — **8,420 words, 152 sections**
- the next 21 items — **54 to 578 words each**, 1–12 sections

So two items hold 80% of the reading weight and twenty-one are one-screen reads. A design that
treats all 23 the same will either drown the reader in the two big ones or undersell them. Each
direction signals weight differently — that's the main thing to judge.

## Decisions I need

1. **Which direction to build** — or which parts to take from more than one. My read: Margin has
   the best reading comfort, Fold has the best phone behaviour (2.8 screens vs 13.0 on the big
   motion), Index is the fastest to find something. A hybrid of Fold's home + Margin's read view
   is defensible and I'd build that if you don't prefer one whole.
2. **Should the summary sit above its transcript, or fold behind it?** This is the single biggest
   reading difference between Margin and Fold, and it changes how much of the page is "the
   government's summary" versus "the actual record".
3. **Is 26 sections the right visible cap** before "open the full transcript"? All three use 26,
   which is a third of the smallest big motion and 26× the largest small item.

## Not yet built (deliberately)

- The search box is present and styled but **not wired to the RAG pipeline** — the mockups show
  where it goes and how an answer reads, not a live query. Wiring it is part of the build.
- The "read this in context" jump lands on the right item but not yet at the exact sentence —
  the sentence id is carried, so the anchor is a build step.
- No dark mode. The shipped site is light-only and these inherit that; say if you want dark.
