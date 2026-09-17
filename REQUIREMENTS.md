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

## 9. Open questions

Recorded as unanswered rather than assumed:

1. **Atomic unit of provenance** — sentence, or turn? A sentence is the citation unit;
   a turn is the attribution unit. Likely both, but not yet decided.
2. **Failure policy** — if one item fails validation, does the whole batch fail, or
   does the item fail and the rest proceed?
3. **Verification strictness** — must a quotation match byte-for-byte after
   normalisation, or is whitespace-insensitive equality sufficient? (Source uses curly
   quotes and non-breaking spaces.)
4. **Model choice** — cloud vs local; deferred, pending benchmark. See SUMMARISATION.md.
5. **Publication policy for unverified items** — publish marked-unverified, or withhold
   entirely?

## 10. Traceability

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
