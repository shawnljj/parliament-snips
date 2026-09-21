# Card format — what makes a card effective

A card is effective when a worker with **no access to the conversation that produced it**
can complete the work, and a verifier can decide **deterministically** whether it was
done. Anything only you or I remember belongs on the card.

Written for the parsnips board; the shape applies to any project.

---

## The seven fields

```
WHAT       one sentence. The change, not the goal.
WHERE      file paths / directories. Absolute or repo-relative, never vague.
WHY        the requirement or defect id this serves.
DONE WHEN  a copy-pasteable command + the expected output. Deterministic.
NOT DONE   the plausible-but-wrong result that would also pass. Deterministic.
VERIFIED   which check decides it; which model, if any; or "none".
OPEN       questions that block. Empty is a claim.
```

**"Not done when" is the load-bearing field.** A card with only a success criterion
can be satisfied by the wrong change. Naming the wrong-but-passing outcome is what
makes the card self-verifying — and it is the same rule as `D-1` (*a gate that has
never been observed failing has not been shown to work*), applied at card level.

---

## Rules

1. **Commands must be exact and runnable.** `python3 tools/check_selection.py summaries/2016 2016`
   — never "run the gate". If you cannot write the command, the card is not ready.
2. **Expected output is quoted literally.** `PASS — 0 defect(s)`, not "passes".
3. **Every claim of fact carries its source.** "672 defects (status.json, gates[0])" —
   not "many defects". A card that asserts a number makes it checkable.
4. **An open question blocks.** If something is genuinely undecided, say so and stop;
   do not write a card that requires a guess. Guessing is how the wrong thing gets built
   correctly.
5. **No conversation references.** "As discussed" is not information. Restate it.
6. **Prefer deterministic verification.** If a human or model must judge, say so
   explicitly in VERIFIED, because unstated judgment is the failure mode this format exists
   to prevent.

---

## Template

```markdown
## WHAT
<one sentence>

## WHERE
- `path/to/file.py`

## WHY
<defect id or requirement id, with the source that establishes it>

## DONE WHEN
    <exact command>
Expected: `<literal expected output>`

## NOT DONE WHEN
<a plausible wrong outcome that would also satisfy DONE WHEN>

## VERIFIED BY
<check name> — deterministic | <model> judge | human

## OPEN
- [ ] <blocking question, or "none">
```

---

## Worked example

```markdown
## WHAT
Regenerate the 24 stale 2016 briefs so their sentence ids match the rebuilt dataset.

## WHERE
- `summaries/2016/` (24 affected files)
- source: `data/2016/`

## WHY
A-1 (verbatim citation at a recorded position). status.json gates[0]: check_selection
on summaries/2016 reports FAIL, 672 defects.

## DONE WHEN
    python3 tools/check_selection.py summaries/2016 2016
Expected: `PASS — 0 defect(s)`

## NOT DONE WHEN
The 24 briefs are deleted instead of regenerated. check_selection then reports
`PASS — 0 defect(s)` while 24 items have silently left the corpus. Guard:
    ls summaries/2016/*.json | wc -l
Expected: `>= 349` (the pre-fix count, from status.json corpus.2016.briefs)

## VERIFIED BY
check_selection.py — deterministic. No model.

## OPEN
- [ ] none
```

That card is complete without me in the room: it names the files, the requirement, the
exact command, the expected string, and the specific way it could pass while being wrong.
