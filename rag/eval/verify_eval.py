"""Verify the eval set: every gold passage must resolve, every fact must appear in it.

An eval set with unverified gold is worse than no eval set -- it measures the wrong
thing and reports success. This checks each claim against the real corpus.
"""
import json, glob, sys

EVAL = "/Users/shawnlin/parsnips/rag/eval/questions.json"
files = sorted(glob.glob('/Users/shawnlin/parsnips/data/*/sitting_*.json'))

# index every turn by report_id for exact lookup
index = {}
for f in files:
    d = json.load(open(f))
    date = (d.get("coverage") or {}).get("date")
    for r in (d.get("reports") or []):
        rid = r.get("report_id")
        for i, t in enumerate(r.get("turns") or []):
            index.setdefault(rid, []).append({
                "turn": i, "date": date, "group": r.get("group"),
                "speaker": t.get("speaker"), "text": t.get("text") or "",
            })
print(f"indexed {len(index):,} report ids from {len(files)} sittings\n")

spec = json.load(open(EVAL))
bad = 0
for q in spec["questions"]:
    ok = True
    msgs = []
    if q.get("expect") == "refuse":
        msgs.append("refusal case (no gold required)")
    else:
        if not q["gold"]:
            ok = False; msgs.append("NO GOLD")
        # A gold entry may be a SET of turns that answer the question -- including a
        # correction pair, where the later turn supersedes an earlier one and the two
        # therefore do NOT contain the same figures. A fact is satisfied when it appears in
        # AT LEAST ONE gold turn, not in all of them: requiring every fact in every turn
        # rejects a valid correction pair (measured on A1: the original says $15.5 billion,
        # the clarification says $10.5 billion, and both are legitimately gold).
        turn_haystacks = []
        for g in q["gold"]:
            turns = index.get(g["report_id"])
            if turns is None:
                ok = False; msgs.append(f"report {g['report_id']} NOT FOUND"); continue
            match = [t for t in turns if t["turn"] == g["turn"]]
            if not match:
                ok = False; msgs.append(f"{g['report_id']}#t{g['turn']} turn missing"); continue
            t = match[0]
            # Class B asks WHO said it, so the fact may live in the speaker field.
            hay = (t["text"] + " || " + (t["speaker"] or "")).lower()
            turn_haystacks.append(hay)
            where = ("text" if all(f.lower() in t["text"].lower() for f in q["gold_facts"])
                     else "partly speaker field")
            msgs.append(f"{g['report_id']}#t{g['turn']} {t['date']} {t['group']} "
                        f"{len(t['text'])}ch OK [{where}]")
            msgs.append(f"      speaker: {t['speaker']}")

        if turn_haystacks:
            for fact in q["gold_facts"]:
                if not any(fact.lower() in hay for hay in turn_haystacks):
                    ok = False
                    msgs.append(f"fact {fact!r} appears in NO gold turn")

            # accept_any: groups of ALTERNATIVES. Satisfied when ANY member is present. Used
            # where the question honestly has several correct answers, so measuring one of
            # them as THE answer would punish a correct reply.
            for i, group in enumerate(q.get("accept_any") or [], 1):
                if not any(any(m.lower() in hay for hay in turn_haystacks) for m in group):
                    ok = False
                    msgs.append(f"accept_any group {i} has NO member in any gold turn")
                else:
                    msgs.append(f"accept_any group {i}: {len(group)} alternatives, "
                                f"at least one present")

            # one_per_turn: the DEFINITION of class D. Group i must appear in gold turn i and
            # be ABSENT from every other gold turn -- that is what makes the question require
            # BOTH passages. Without this assertion a "class D" question is often class A
            # twice, answerable from a single passage, and it silently measures nothing new.
            opt = q.get("one_per_turn") or []
            if opt:
                if len(opt) != len(q["gold"]):
                    ok = False
                    msgs.append(f"one_per_turn has {len(opt)} groups for {len(q['gold'])} "
                                f"gold turns -- must be aligned")
                else:
                    for i, group in enumerate(opt):
                        present_here = any(m.lower() in turn_haystacks[i] for m in group)
                        elsewhere = [j for j in range(len(turn_haystacks))
                                     if j != i and any(m.lower() in turn_haystacks[j]
                                                       for m in group)]
                        if not present_here:
                            ok = False
                            msgs.append(f"one_per_turn group {i+1} NOT in its own turn")
                        elif elsewhere:
                            ok = False
                            msgs.append(f"one_per_turn group {i+1} also in turn(s) "
                                        f"{elsewhere} -- not unique, question is answerable "
                                        f"from one passage")
                        else:
                            msgs.append(f"one_per_turn group {i+1}: unique to turn {i+1} OK")
    status = "PASS" if ok else "FAIL"
    if not ok: bad += 1
    print(f"  [{status}] {q['id']:<3} {q['q'][:60]}")
    for m in msgs:
        print(f"          {m}")

print()
print(f"questions: {len(spec['questions'])}   failing: {bad}")
sys.exit(1 if bad else 0)
