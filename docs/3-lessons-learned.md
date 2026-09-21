# Corrections — what the build assumed, and what the owner changed

The record behind the case study's "what the build got wrong" section. Each item
names the assumption the build made, what it cost, the owner's correction in their
own words, and the rule that replaced it.

Written from the session transcript, not reconstructed from the artifacts. Where a
claim is measured, the measurement is named so it can be re-checked.

---

## 1. Model diversity read as a quality property

**The build's assumption.** A verifier from a different model family is *better* at
verifying. `gpt-oss:20b` was chosen for the verifier because it was the only
non-DeepSeek model installed.

**What it cost.** Independence was bought by dropping to a 20B model, and the choice
was never justified in `MODELS.md` — the document records the pipeline's model
reasoning and says nothing about the verifier's.

**The owner's correction.** *"If someone used deepseek to verify a Gemma
implementation, deepseek would catch it right?"*

**What is actually true.** Family diversity is protection against **self-agreement**,
not a quality signal. The same model checking its own output over-rates it; that is
the pathology the rule exists to prevent. The assistant is DeepSeek, the pipeline
model is DeepSeek — so when the assistant reviews pipeline output it is
self-verifying, and the verifier's value is that it is not the assistant.

**The rule.** A verifier should be different **and** competent. Different-family is a
control, not an improvement. State which one you are buying.

---

## 2. A mechanism asserted without evidence

**The build's assumption.** The same model "can't see its own blind spots" — that it
recognises its own work and goes easy on it.

**What it cost.** The explanation was wrong even where the conclusion held. An
intuitive story was presented as the reason for a design rule.

**The owner's correction.** *"I am quite skeptical. This isn't a single developer
which has biases... I don't really agree that it'd somehow miss its own blind spots.
Is there some proof that this would happen at a reasonably significant level?"*

**What is actually true.** Self-preference is real and measured — positive for every
model tested, mean **+0.14** — but it operates as a **stylistic affinity**, not as
recognition of authorship: only one of four models could identify its own output above
chance, while all four still preferred their own. On reference-based tasks most
self-preference is *legitimate*. Inference-time scaling — a long chain of thought
before judging — measurably reduces the harmful part.

**The rule.** Name the measured mechanism, not the plausible one. If the honest answer
is "this is still open," write that.

---

## 3. A verifier that could only report "I found nothing"

**The build's assumption.** A verifier reads the work and reports what is wrong with it.

**What it cost.** "I found nothing" is indistinguishable from success. This is the same
failure family as the gates that reported PASS while doing nothing — `check_selection`
passed 72 blank sections, and `verify_citations` read schema-3 fields, found zero
points, and exited successfully.

**The owner's correction.** *"Just like a human tester is not simply asked to 'go find
bugs' but has a list of test cases that validate if the developed thing met the
requirements, a verifier is supposed to do the same."*

**The rule.** The verifier is a **test-case runner**, not an inspector. A case with an
expected verdict makes a miss visible; open-ended inspection makes it silent. The
project's own `D-1` already says it — *"a gate that has never been observed failing has
not been shown to work"* — and it was written down while unimplemented.

---

## 4. A new lifecycle that was not new

**The build's assumption.** AI-era development needs its own SDLC, with novel stages and
an evaluation loop back into requirements.

**What it cost.** Standard practice was presented as a discovery. The claim was
retracted mid-conversation.

**The owner's correction.** *"What's the difference between this and simply using SDLC
but using AI to fulfill each stage?"* and *"Agile settles the monitoring loop thing.
SDLC is not necessarily the waterfall model right"*

**What is actually true.** SDLC is the umbrella; Waterfall, Agile, Spiral and DevOps are
*process models within it*. Feedback from operation into requirements is a property of
any iterative model and has been for decades. The phase list is not a lifecycle model.

**The rule.** Do not rename standard practice. If something genuinely changes, it will
survive being stated as a delta from what already exists.

---

## 5. The real difference, named by the owner

**The build's assumption.** The difference was structural — new stages, new artifact
types.

**The owner's correction.** *"The only difference I see if I ignored the labels is that
there are now some test cases that cannot be asserted with a fixed expected output, but
rather an expected output intent, and then there's an extra step now that we need an LLM
to read the output and check if the intent is close enough. This seems more like an extra
step that we have to do because we want to use AI to make some outputs, rather than a
deterministic one."*

**What is actually true.** This is the **test oracle problem** (Barr et al., *The Oracle
Problem in Software Testing: A Survey*), and the literature frames it exactly as the
owner did — as a **cost**. An oracle is a resource; when correctness cannot be computed
mechanically, someone must supply the expected outcome, and that supply is expensive.

The reduction collapses the whole claim to one line: **not a new step, a new bill.** We
chose to route output through a component whose correctness can't be computed, so we now
pay for an oracle where previously we paid nothing.

**The rule.** Minimise the surface that needs an oracle. Where a deterministic assertion
is possible, make it one — `select-by-sentence-id` means published text is *copied*, so
byte-equality is checkable with no model at all. The judge is needed only for the
irreducible residue.

---

## 6. Infrastructure built for a workload that did not exist

**The build's assumption.** A fleet earns its place: a portfolio chief-of-staff plus a
per-project implementor/verifier pair.

**What it cost.** Two profiles with no recurring job. The dispatcher was already being
served in this conversation.

**The owner's correction.** *"So far you do my dev implementation perfectly fine. And you
give me status updates too, So Dev and COS bots seem useless."* And on the chief of
staff: *"COS is only useful if there's some autonomous long-term development happening,
as in over many days without interaction, and multiple parallel streams of that."*

**The rule.** One bot = one job with a closed output space. A bot that wraps
deterministic tools adds risk without capability — a probabilistic layer with
discretionary control over deterministic evidence is how a `VERDICT: PASS` got
fabricated after running from the wrong directory. Add an agent only when there is
recurring *judgment* a script cannot do.

---

## 7. A number stated without recomputing it

**The build's assumption.** "100+MB of docs."

**What it cost.** It alarmed the owner and had to be corrected. It also violated the
project's own rule — **N-3**: every number the system reports about itself must be
computed from data.

**The owner's correction.** *"Wait, I have 100+MB of docs?"*

**What is actually true.** Documentation totals **140KB**. The 315MB is build output:
`sittings/` 179MB and `skipped/` 134MB. All of it is product, not waste — `skipped/` is
lazy-loaded by all 332 sitting pages precisely because inlining it would mean 52,698
sentences of inline bloat. The real issue was never size; it is that build output is
tracked in git.

**The rule.** Compute the number before speaking it. A figure stated from memory is a
claim that will be checked, and being approximately right is not the standard the rest
of the project is held to.

---

## What these have in common

Six of the seven are the same failure: **a plausible mechanism stated in place of a
measured one.** The design intuitions were often right — the verifier does earn its
place — but the *reasons* given for them were frequently invented, and the owner caught
it every time by asking for evidence rather than accepting the story.

The rule that falls out: **the reason is part of the claim.** An unsupported justification
is not a harmless embellishment on a correct decision; it is the thing that makes the next
decision wrong.
