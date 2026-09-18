# Parsnips — Requirements

**Status:** Sprint 1, requirements engineering. This document is the authority for
WHAT the system must do. Design follows from it; implementation follows from design.

**Why this exists.** Prior work started from implementation (a scraper, then a
summariser, then a dataset format) and discovered its requirements by tripping over
them. That produced a string of defects that no test caught — an unanchored regex that
emptied 44 Bills and Budget items while the verification gate reported 100%, four
defects in a single selector in one session, and a documented figure that was wrong by
16×. The failure was not carelessness; it was **having no stated requirements to test
against**. So requirements first.

**Domain.** Singapore Parliament Hansard is a *verbatim record of legislative
proceedings*. What we publish is a derived, summarised document about that record. A
single misattributed quotation or fabricated claim is not a bug in a blog post — it is
a defect in a document that presents itself as a faithful account of what Parliament
said. Requirements here are therefore graded by **epistemic** risk, not by feature
value.

---

## 1. Problem statement

### 1.1 The objective, in the owner's words

> My objective of this app is that a user who is interested in SG Government moves and
> has some faith that the government is indeed doing things for public good, is able to
> get a bit more insight into government's moves through parliament proceedings. But, to
> watch through parliament or read through entire transcript is tough for most
> non-legal or non-political science individuals like myself, so i need a little bit of
> summarization. but news outlets only focus on sound bites or short phrases. I would
> like to have something in between. Summaries of every exchange, which is easy to
> navigate, so that i could read through an entire parliamentary sitting maybe in a
> matter of half an hour to an hour instead of 8 hours.

### 1.2 What that commits us to

| Requirement implied | Consequence |
|---|---|
| Reader is **interested but not expert** | No legal or procedural jargon without explanation. R-3.4. |
| Reader arrives with **some faith in government** | The product must not be a gotcha machine, and must not be a press release either. Neutral reporting is the contract. R-3.1, R-3.2. |
| **Summaries of every exchange**, not highlights | Coverage is the product. Not "the interesting bits" — every exchange gets a summary. R-3.3, R-5.5. |
| **Easy to navigate** | Navigation is a primary feature, not chrome. R-5.4. |
| **30–60 minutes for a sitting** | An ILLUSTRATION of the order of magnitude, not a
ceiling. See 1.4. |
| More than a sound bite, less than a transcript | The summary unit must be substantive but bounded. See 1.3. |

### 1.3 The gap we fill, stated exactly

| | Unit | Reader cost | Problem |
|---|---|---|---|
| Full Hansard | 68,861 words / sitting | ~8 hours | Correct, unreadable |
| News coverage | A phrase or two | Seconds | Readable, incomplete, framed by news value |
| **Parsnips** | One summary per turn | **30–60 minutes** | Must be both complete and neutral |

### 1.4 The reading budget, measured

**This section was originally read as a hard ceiling, and that was wrong.** The owner's
clarification: 30–60 minutes is there to illustrate that a sitting reduces to *much less
than its full length* — roughly 8 hours of transcript down to under an hour. It is not a
budget that output must fit inside. If a faithful brief runs longer than 60 minutes, that
is acceptable.

The distinction matters because the pipeline derived a cap from the old reading and the
cap bounded the very metric the gates score. A ceiling on reading time is a ceiling on
coverage, and coverage is the product (§1.2: "summaries of every exchange, not
highlights"). Where the two conflict, coverage wins.

What survives from this section is the arithmetic, which is still how we judge whether a
sitting's output is sane:

Measured across 331 sittings: **median 287 turns per sitting** (mean 285, range 23–706).
Words per turn: median **67**, p90 506, p99 2,878, max 16,209.

| Seconds per turn summary | Median sitting (287 turns) | Busiest (706 turns) | Quietest (23) |
|---|---|---|---|
| 5s | 24 min | 59 min | 2 min |
| **10s** | **48 min** | **118 min** | **4 min** |
| 15s | 72 min | 176 min | 6 min |
| 20s | 96 min | 235 min | 8 min |

**Conclusion: ~10 seconds per turn puts a median sitting at about 48 minutes**, which is
where the 30–60 minute illustration came from. Treat it as a description of the expected
size of the output, NOT as a limit on it.

**What replaces the hard cap** is a navigation requirement, and it is the same thing the
busiest days always needed: a reader must be able to reach a defensible stopping point, or
skim at the section level, without losing the ability to see what they skipped (R-5.6).
Length is handled by making the long items navigable, not by cutting them short.

**Note on verbatim summaries (2026-09).** When the published text is selected from the
record rather than written by a model, nothing compresses: a 12,000-word oral answer
selects ~1,700 words, about 9 minutes. That is longer than the old target and is the
correct outcome — the alternative was compressing away most of the record, which is
exactly where the fabricated-specifics defect came from.

### 1.6 The pipeline, as specified by the owner

```
  download (2016 → today)                     scheduled job, incremental
        ↓
  SITTING
        ↓  cut into sections, following how Parliament is actually run
  SECTION   motion | ministerial statement | oral question | bill | ...
        ↓  cut by speaker turn  (only one speaker speaks at a time)
  TURN
        ↓  group within a debate (owner decision D-5)
  GROUP
        ↓  summarise each turn
  TURN SUMMARY  (≈10s of reading)  +  verbatim transcript in a collapsible
```

**Why this is a better unit than the previous design.** The prior model grouped work by
debate *title* and merged across sitting days. That produced a real bug: Budget 2016 and
Budget 2026 were welded into one 271,095-word item, because titles repeat across years.
Turns cannot do that — a turn belongs to exactly one report, is ordered by the record
itself, and represents one speaker. The owner's unit is structurally safer, not just
more readable.

**Section boundaries.** The existing parser already classifies reports into groups
(`bill`, `statement`, `motion`, `budget`, `adjournment`, `oral`, `written`, `correction`,
`tribute`), so section division is mostly a matter of surfacing it. Refining this to
match parliamentary procedure exactly is a design question.

**Incremental ingestion.** A scheduled job must detect and fetch new sittings and run
them through the same pipeline. This makes R-4.4 (resumability) and R-1.3 (declared
coverage) daily operational concerns, not backfill concerns.

### 1.5 Problem statement (original framing, retained)

Reading Singapore's Hansard is impractical for the general public. A sitting averages
**68,861 words** across **63 reports**; the archive now spans **331 sittings
(2016–2026), 22,793,049 words**. Existing coverage is either the full transcript
(unreadable) or news snippets (too thin to be useful). Nothing sits between them.

Parsnips must occupy that gap without ever asserting something the record does not
support.

## 2. Scope

**In scope**
- Ingest Hansard sittings from the official portal into a local archive.
- Derive, per policy item, a neutral brief: what was asked, what was decided or
  announced, who said it, and the verbatim words it rests on.
- Publish those briefs as a static, mobile-first website.
- Make the derivation auditable: any published claim traceable to its source.

**Out of scope (stated so it is not assumed)**
- Hansard before **2016** (the API's format changes; see R-6.3).
- Written answers as full briefs (≈135 per sitting; they are listed, not summarised).
- Any judgement of whether a speaker answered adequately, committed in good faith, or
  evaded. The site maps and reports; it does not assess.
- Real-time publication. Sittings are processed in batches after publication.

## 3. Stakeholders

| Role | Concern |
|---|---|
| Public reader | Is this accurate, and can I check it? |
| Maintainer (owner) | Can I run, resume and trust this unattended? |
| Fresh contributor | Can I understand the model without prior context? |
| Cited party (MP/Ministry) | Is anything attributed to me that I did not say? |

## 4. Definitions — the units, pinned down

Prior confusion came from these being used loosely. They are distinct.

| Term | Definition | Measured |
|---|---|---|
| **Sitting** | One calendar day Parliament sat | 331 |
| **Report** | One record in the Hansard API; a debate may span several | 20,962 |
| **Item** | A *policy item*: one report, or several merged because they are the same business on consecutive sitting days | 3,912 |
| **Turn** | One speaker's continuous contribution within a report | 40,945 |
| **Sentence** | One sentence within a turn; the unit of citation | 754,082 |
| **Brief** | The published summary of one item | 291 (2026 only) |
| **Claim** | One factual assertion in a brief, carrying a verbatim quote | — |

**Reports vs items must never be used interchangeably.** The manifest counts
summarisable *reports* (4,283); the dataset counts *items* (3,912). The 371 difference
is merged multi-day debates. Both numbers are correct; they answer different questions.

## 5. Functional requirements

Graded: **MUST** = without it the product is wrong or unusable. **SHOULD** = real
value, can be deferred. **MAY** = optional.

### 5.1 Archival integrity (the foundation)

| ID | Pri | Requirement |
|---|---|---|
| R-1.1 | MUST | Every sitting published must be reproducible from the official record: fetching the same sitting again must yield the same content, or the difference must be detectable. |
| R-1.2 | MUST | Fetching must be resumable. An interrupted run must lose only the sittings in flight, never corrupt completed ones. |
| R-1.3 | MUST | Coverage must be reported per sitting, not assumed. The API silently under- and over-reports its own result counts; a sitting is only complete when a declared enumeration strategy says so. |
| R-1.4 | MUST | Writes must be atomic. A reader must never observe a partially written artifact. |
| R-1.5 | SHOULD | The archive should be diffable in review: one sitting, one readable change. |

### 5.2 Provenance and correctness (why this project exists)

| ID | Pri | Requirement |
|---|---|---|
| R-2.1 | MUST | **Every claim in a brief must cite a verbatim quotation from the source record.** |
| R-2.2 | MUST | A quotation must be **verifiable against the source**, not merely asserted: the system must be able to demonstrate the quoted text exists at a known position in the source. |
| R-2.3 | MUST | A claim whose quotation cannot be verified **must not be published**. Dropping is the safe direction; a wrong citation is worse than a missing point. |
| R-2.4 | MUST | Every claim must carry an **attribution** (speaker). Where the record does not supply one, the system must mark it as inferred rather than present a guess as fact. |
| R-2.5 | MUST | **No content may be silently discarded.** Any exclusion (procedural business, noise, oversized text) must be recorded with a reason, so absence is auditable. |
| R-2.6 | MUST | The derivation must be **complete**: a brief must be derived from the whole item, not a truncated part of it. |
| R-2.7 | SHOULD | Any published claim should be traceable by a reader to the official record. |

### 5.3 Summary quality

| ID | Pri | Requirement |
|---|---|---|
| R-3.1 | MUST | Tone must be neutral and factual. No editorialising, no framing, no adjectives of judgement. |
| R-3.2 | MUST | The system must never assert whether a speaker answered adequately, committed in good faith, or evaded. Open ground is reported as open. |
| R-3.3 | MUST | Briefs must cover all business types: Bills, ministerial statements, policy announcements, motions, Budget/Committee of Supply, and oral answers. |
| R-3.4 | MUST | Output must be legible to a reader who has never read Hansard: plain language, no procedural jargon unexplained. |
| R-3.5 | SHOULD | A brief should be shorter than the transcript it summarises, by a wide margin, without losing substance. |
| R-3.6 | SHOULD | Item-level fields (what it is, why it matters, what happens next) should be derived from the record only — never invented to fill a template. |

### 5.4 Pipeline operation

| ID | Pri | Requirement |
|---|---|---|
| R-4.1 | MUST | The pipeline must be stage-separated, each stage independently runnable and reviewable. |
| R-4.2 | MUST | Deterministic stages must be deterministic: same input, same output, no model involved. |
| R-4.3 | MUST | Model use must be confined to stages that genuinely require judgement, and this must be visible. |
| R-4.4 | MUST | A stage must be resumable. Re-running a completed stage must not redo completed work. |
| R-4.5 | MUST | Processing state (queued / done / failed) must be inspectable without reading logs. |
| R-4.6 | SHOULD | Failures must be isolated: one bad item must not halt a corpus run, and must be recorded for review. |
| R-4.7 | SHOULD | Cost and call volume should be predictable before a run, not discovered after. |

### 5.5 Presentation

| ID | Pri | Requirement |
|---|---|---|
| R-5.1 | MUST | The site must work well at a mobile viewport; readers judge it on a phone. |
| R-5.2 | MUST | Every page must state its own coverage and method, so a reader can calibrate trust. |
| R-5.3 | MUST | The site must be a static build, deployable without a server. |
| R-5.4 | SHOULD | A long sitting page must be navigable without reading it linearly. |
| R-5.5 | SHOULD | The build must be honest about incomplete work; missing briefs must appear as missing, not as silent gaps. |

### 5.6 Data model and maintainability

| ID | Pri | Requirement |
|---|---|---|
| R-6.1 | MUST | Entities must be **normalized** with stable ids: sitting, report, turn, sentence, speaker, item, claim. No entity duplicated as a string where an id will do. |
| R-6.2 | MUST | Every derived artifact must reference source entities **by id**, never by copying text. |
| R-6.3 | MUST | The data model must record the **format era** of each report, so a source link or validation pass never infers it. |
| R-6.4 | MUST | The schema must be **versioned**, and a schema change must be detectable by a consumer. |
| R-6.5 | MUST | The schema must be **validated** before use. Invalid data must fail loudly, at the boundary, not downstream. |
| R-6.6 | SHOULD | Storage should be proportionate to content: no container overhead dominating payload. |

## 6. Non-functional requirements

| ID | Pri | Requirement |
|---|---|---|
| N-1 | MUST | **Correctness over completeness.** Where the two conflict, publish less. |
| N-2 | MUST | **No fabricated content, ever.** Absence of information must be represented as absence, never filled. |
| N-3 | MUST | Any number the system reports about itself (counts, coverage, sizes) must be computed from data, never hand-written into documentation. |
| N-4 | MUST | The system must be polite to the source: rate-limited, cached, fetched once. |
| N-5 | MUST | Runs must not require the owner's attention to complete safely. |
| N-6 | SHOULD | The whole archive should be reproducible from source by a third party. |
| N-7 | SHOULD | Processing should be affordable to run over the full corpus. |

## 7. Acceptance criteria

A release is acceptable only when all of the following are **demonstrated**, not
asserted:

| # | Criterion | Evidence required |
|---|---|---|
| A-1 | Every published quotation exists in the source at a recorded position | Automated check over all claims |
| A-2 | No content is dropped without a recorded reason | Reconciliation of source text vs derived artifacts |
| A-3 | Every brief is derived from its complete item | Per-item completeness check |
| A-4 | Every claim carries an attribution or an explicit inferred flag | Schema validation |
| A-5 | Every reported metric matches a recomputation from data | Test that recomputes and compares |
| A-6 | An interrupted run resumes without loss or corruption | Simulated interruption test |
| A-7 | A new contributor can explain the pipeline from the repo alone | Documented stage model |

## 8. Known risks

| Risk | Impact | Status |
|---|---|---|
| Hansard's own result counts are unreliable | Silent under-collection | R-1.3; union enumeration in place |
| Speaker attribution is incomplete in the source | Claims without speakers | Measured: 4.0% of turns carry no speaker (R-2.4) |
| Model reworded quotations | Wrong citations | Addressed by citing sentence ids, not retyped text (R-6.2) |
| Verification passes while content is missing | False confidence | **Observed.** The quote gate read 100% while 44 items were emptied (R-2.5, A-2) |
| Documentation drifts from data | Wrong decisions from wrong numbers | **Observed.** A doc claimed 1.1M words/sitting; real figure is 68,861 (N-3) |
| Pre-2016 format differs | Scope creep | Declared out of scope (R-6.3) |

## 9. Decisions taken (17 Sep 2026, owner)

These were open questions; they are now requirements. Each is recorded with the
evidence that informed it, so a future reader can see why rather than just what.

### D-1 Publication is fully automated with hard gates — no human in the loop

**Decision.** Nothing publishes unless it passes validation. There is no manual review
step.

**Consequence, and it is the sharpest constraint in this document.** This inverts the
usual safety model: we cannot rely on a person noticing a bad brief, so **the gates ARE
the quality process**. A gate that merely reports is worthless here; a gate that
reports and lets the item through is worse than no gate, because it manufactures
confidence.

It follows that:
- Every MUST in §5.2 must be **enforced**, not monitored.
- A gate must fail *closed*. Unknown state = do not publish.
- The gates must be testable, because an untested gate is an assumption. Each needs a
  test that deliberately violates it and asserts the failure is caught. A gate that has
  never been observed failing has not been shown to work.

**Risk this creates, stated plainly.** Automated publication of a document about
legislative proceedings means a gate bug becomes a published inaccuracy that nobody
catches. The mitigating requirement is R-2.2 (recorded provenance): provenance can be
*re-verified independently* after publication, whereas a judgement cannot.

### D-2 Item-level failure isolation

**Decision.** An item that fails validation fails alone. It is recorded for review; the
remaining items proceed.

**Consequence.** Publishing becomes per-item, not per-batch. Two states only: **verified
and published**, or **failed and recorded**. There must be no third "probably fine"
state, because D-1 removes the human who would resolve it.

The failed set must be durable and inspectable — it is the only artefact a human ever
sees, so it is the one thing that must not be lossy. Requirement R-4.6 upgraded from
SHOULD to MUST.

### D-3 An unvalidatable item is withheld entirely

**Decision.** If an item's dataset cannot be fully validated, publish nothing for it.
Not a partial brief, not a marked-unverified brief.

**Consequence, and this one costs something visible.** Withholding is not neutral — it
leaves a hole in a sitting page. So R-5.5 becomes load-bearing: the page must show the
hole *as* a hole ("2 of 16 items could not be verified and are withheld"), never as
silent absence. A reader must never mistake "we could not verify this" for "nothing
happened here."

That distinction is the whole reason D-3 is safe. Without honest reporting of absence,
withholding would be indistinguishable from incompleteness.

### D-4 Unattributed claims are published, marked as inferred

**Decision.** Where the source carries no speaker, the claim is published with its
speaker marked as inferred from context.

**Evidence.** Measured: 3,802 of 94,272 turns (4.0%) carry no speaker, and some are
substantive (a 438-word response with no name). The record itself is incomplete, so
excluding these would drop real content.

**Consequence.** R-2.4 must be enforced structurally, not by convention:
- every claim records whether its attribution came **from the record** or was
  **inferred**;
- the inference method must be deterministic and stated (currently: carry the last
  known speaker forward within the same report);
- the published page must render the distinction, so a reader knows which is which;
- an inferred attribution must never be presented with the same confidence as a
  recorded one.

### D-5 `why_it_matters` is removed

**Decision.** The field is dropped. It is the one field the record frequently cannot
support, making it the largest fabrication risk in the schema.

**Evidence, which supports the decision beyond the principle.** Of 291 existing briefs,
only 70 ever populated it — and of those, **20 (29%) contain the placeholder dodge**
("The record does not set out the practical impact."). A field that is absent 76% of the
time and evasive 29% of the time it appears is not carrying its weight, and the field
that remains is precisely the one where a model would be tempted to editorialise.

**Consequence — this changes the product, not just the schema.** `why_it_matters` is
the narrative hook: it was the answer to "why should I care?" Removing it means a brief
must earn a reader's attention with the substance itself — what was decided, who pushed
for what, what the figures were. That is a harder editorial bar, and it is the right one
for a document that must not editorialise.

It also means the page hierarchy loses its middle layer: `what_it_is` states the
business, and the key points must now carry the significance. R-5.4 (navigability) and
the presentation design need to absorb that.

**Supersedes:** the 50 substantive `why_it_matters` values in existing briefs. They were
written under the old schema; whether to migrate or discard them is a design question.

### D-6 Navigation is a deterministic tree: sitting → section → speaker → content

**Decision.** The navigation structure is a tree derived from the record's own structure:

```
SITTING
  └── SECTION      motion | ministerial statement | oral question | bill | Budget …
        └── SPEAKER      the speaker label AS RECORDED — no identity resolution
              └── CONTENT      their turn — summarised, verbatim collapsible
                    └── POINTS      (deferred — see D-7)
```

**Correction to an earlier framing in this document.** This was previously recorded as
"the highest-risk open question: a heuristic doing structural work". That was wrong, and
the owner correctly pushed back. Nothing here requires inference:

- A **turn** is one contiguous block of text under one speaker label. Measured: 96.0% of
  94,272 turns carry a speaker; structurally there is one speaker per turn by
  construction. The unattributed 4% is handled by D-4.
- A **section** is the report's own type, which the parser already assigns.
- The **speaker** level is the recorded label itself.

No theme inference, no clustering, no guessing. The tree is a transformation of the
record, not an interpretation of it.

**Do NOT resolve speaker identities at this stage.** The owner's decision, and it is the
safer default: treat this as a data transformation first. Group by the speaker label
exactly as recorded; deduplication, cross-sitting linking and MP-level analysis are a
later concern, to be built on top of preserved data rather than folded into ingestion.

This matters because identity resolution is the step that could introduce a *new* error —
merging two different people would be misattribution, the serious failure. Grouping by
the raw label is information-preserving: it cannot misattribute anybody.

**Known consequence, accepted deliberately.** One person appears under several labels
because Hansard introduces them with their portfolio. Measured per sitting: **89.6
distinct speaker strings**, of which ~83 are distinct people; **325 of 331 sittings**
contain at least one such collapse; worst case 157 strings → 139 people. Example, all one
person:

```
'Mr Dinesh Vasu Dash'
'The Minister of State for Culture, Community and Youth, and Manpower (Mr Dinesh Vasu Dash)'
'The Minister of State for Manpower (Mr Dinesh Vasu Dash)'
```

So a reader may see the same person as more than one branch. That is a **display**
imperfection, not a data defect — the label is verbatim from the record, and no
information is lost or wrongly attributed. It is revisitable without re-ingesting
anything, which is exactly why deferring it is cheap.

**The role is information, not noise.** The portfolio in the label ("The Minister for
Home Affairs") tells a reader what standing the speaker had. It is retained as recorded.

**Procedural "speakers" are not people.** `Mr Speaker`, `[Mr Speaker in the Chair]`,
`The Chairman`, `[Madam Deputy Speaker]` are chair roles — section furniture rather than
branches of a speaker tree. Whether they are filtered for display is a presentation
question, not a data one; they are retained in the data.

### D-7 A fifth level (content → points) is deferred

**Decision.** Do not attempt to split a turn's content into points yet. Summarise the
whole turn. Revisit sub-structure later.

**Rationale.** Correctness first. Turn-level summarisation is a well-understood unit:
one speaker, one contiguous text, one summary, verbatim collapsible. Splitting content
into points within a turn introduces a heuristic with no ground truth — exactly the class
of thing that has already produced four defects in this project. Deferring it costs
nothing, because the tree already gives a reader a place to stop reading.

**Consequence.** The summary unit is the turn (or a sub-split of an over-long turn, D-9).
The tree must be designed so a fifth level can be added without restructuring: a content
node should be able to carry child point nodes later.

### D-8 Short turns are folded into context, not summarised

**Decision.** Turns under ~15 words are not summarised. They are folded into the
neighbouring turn as context.

**Evidence.** Measured: **22,584 of 94,272 turns (24%) are under 20 words**, and 39% are
under 50. These are interjections, clarifications and procedural replies. Summarising
each would burn reading budget on noise — and the reading budget is the product
(§1.4).

**Consequence.** Folding means a summary's context may include text from a turn other
than its own, so the attribution rule (R-2.4) must still hold: a folded turn's words must
not appear to be spoken by the summarised turn's speaker. This is an attribution-
correctness risk introduced by the decision, and it needs a gate.

### D-9 Long turns are sub-split at ~1,500 words

**Decision.** A turn over ~1,500 words is split into sub-summaries at natural breaks.

**Evidence.** The largest single turn is **16,209 words** — one ministerial speech, which
would be ~27 minutes of reading as one unit and would blow the per-turn budget. At 1,500
words the longest turn yields ~11 sub-summaries, each inside budget.

**Consequence.** Sub-splitting reintroduces the chunking problem the previous design
fought with, but at a bounded scale: sub-splits are *within* a turn, so attribution is
unambiguous (one speaker). The verbatim must remain complete regardless — only the
summary is split.

### D-10 The floor stays at 2016

**Decision.** Keep 2016 as the ingestion floor. The 2015 fetch works, but re-fetching is
not worth the cost.

**Evidence.** 2015-01-19 fetched cleanly: **86/85 reports, 55,270 words, 287 turns, 97.2%
attribution**. Nothing is wrong with 2015.

**Consequence — and a correction to the record.** The 2016 floor was originally set from
a boundary probe, not an actual fetch. That probe concluded pre-2016 was unreadable; the
fetch disproves it for 2015. So the floor is now a **deliberate scope choice, not a
technical limit**, and the requirement is restated accordingly. Extending to 2015 later
is a batch re-run, not an investigation.

## 10. Amended requirements

| ID | Change |
|---|---|
| R-2.4 | **Strengthened.** Attribution must record provenance (recorded vs inferred) and the inference method; the page must render the distinction (D-4). Also must survive **folding** short turns into a neighbour's context (D-8) |
| R-2.8 | **New.** A gate must fail closed: unknown or unvalidated state results in non-publication (D-1, D-3) |
| R-2.9 | **New.** Every gate must have a test that deliberately violates it and asserts the violation is caught. An untested gate is an assumption, not a control (D-1) |
| R-3.6 | **Replaced.** Item-level fields must be derivable from the record; `why_it_matters` is removed (D-5) |
| R-3.7 | **New.** A turn summary must fit a **~10-second reading budget** (roughly 25–35 words), because the 30–60-minute sitting target depends on it (§1.4) |
| R-3.8 | **New.** Long turns (>~1,500 words) are sub-split; the summary may be split but the **verbatim must remain complete** (D-9) |
| R-4.6 | **Upgraded SHOULD → MUST.** Failures must be isolated per item and durably recorded; the failed set is the only artefact a human reviews (D-2) |
| R-4.8 | **New.** Ingestion must be **incremental**: a scheduled job detects and fetches new sittings and runs them through the same pipeline, unattended (§2) |
| R-5.4 | **Strengthened.** Navigation is a **tree**: sitting → section → speaker → content, derived from the record rather than inferred (D-6) |
| R-5.5 | **Upgraded SHOULD → MUST.** Withheld items must be reported as withheld, with counts, so absence is never mistaken for silence in the record (D-3) |
| R-5.6 | **New.** On a very long sitting a reader must be able to reach a defensible stopping point or skim at section level, while still seeing what they skipped (§1.4) |
| R-6.7 | **New.** The schema must carry attribution provenance as a first-class field, not a convention (D-4) |
| R-6.9 | **New.** Ingestion must be **information-preserving**: it must not resolve speaker identities or merge labels. Grouping is by the recorded label; identity resolution is a later concern built on preserved data (D-6) |

## 11. Open questions

Still unanswered, recorded rather than assumed:

1. **Atomic unit of provenance** — sentence or turn? A sentence is the citation unit; a
   turn is the attribution unit. Likely both, not yet decided.
2. **Verification strictness** — must a quotation match byte-for-byte after
   normalisation, or is whitespace-insensitive equality sufficient? The source uses
   curly quotes and non-breaking spaces.
3. **Model choice** — cloud vs local; deferred pending benchmark. See SUMMARISATION.md.
4. **Section taxonomy** — how closely should sections mirror parliamentary procedure
   (motions, ministerial statements, oral questions, Bills, Budget/Committee of Supply)?
   The parser already groups reports by type; whether that grouping is the right
   *reader-facing* taxonomy is undecided.
5. **Migration of the 50 substantive `why_it_matters` values** written under the old
   schema — migrate into key points, or discard.
6. **What happens to the 291 existing briefs** built under the old (item-level) schema,
   given the pipeline is now turn-level. Re-derive, or keep as a legacy format?

**Resolved and removed from this list:** identity resolution of speakers. It was
previously an open question; D-6 settles it — do not resolve identities during
ingestion. Group by the recorded label, preserve everything, and leave deduplication
and MP-level analysis to a later layer built on preserved data.

## 12. Traceability

| Requirement | Current state |
|---|---|
| R-1.1–1.5 Archival | Largely met; coverage reporting and atomic writes verified |
| R-2.1, 2.3 Gate | Partially met — gate exists, but quotes are strings, not references |
| R-2.2 Provenance | **Not met** — no position or hash recorded; cannot demonstrate a quote's origin |
| R-2.5 No silent drops | **Partially met** — exclusions counted, not individually recorded |
| R-2.6 Completeness | **Not met as a check** — no per-item completeness verification |
| R-2.4 Attribution | Partially met — carried-forward speakers flagged, not proven |
| R-3.x Quality | Met by the 291 existing briefs; not yet enforced by test |
| R-4.x Pipeline | Stage 1 exists; stages 2–4 not built |
| R-5.x Presentation | Largely met for existing pages |
| R-6.1, 6.2 Normalization | **Not met** — sentences hold copies of speaker strings; claims hold copied text |
| R-6.5 Validation | **Not met** — no schema validation boundary |

**The gap that matters most:** R-2.2 and R-6.5. Without recorded provenance and a
validating boundary, every other correctness claim rests on inspection by hand — which
is precisely how the defects so far were found, and precisely why they recurred.
