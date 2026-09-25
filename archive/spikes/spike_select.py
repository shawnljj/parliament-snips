#!/usr/bin/env python3
"""SPIKE (throwaway): can a model select sentences by id, with zero writing?

The finding that motivates this: three rounds of prompt rules did not reduce the
"added a specific the source doesn't state" defect rate (0.967 -> 0.970 supported of
judged, statistically identical). The model does not experience itself as adding
things, so the rule cannot reach the behaviour.

This tests the alternative -- the model returns ONLY a list of sentence ids, and every
word published is copied from the archive by id. Then:
  * fabrication is not caught, it is UNREPRESENTABLE
  * an unresolvable id is a bug, not a lie
  * the only remaining failure is picking the WRONG sentence, which is checkable

Deliberately small: two items, one prompt, no pipeline changes. It prints the
measurements and writes a phone-readable page.
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "summariser"))
import build_briefs as B  # noqa: E402

OUT_HTML = os.path.join(ROOT, "site", "dist", "spike-select.html")

SELECT_SYSTEM = """You are selecting the sentences that matter from a record of a \
Singapore Parliament sitting.

Return JSON only, in this exact shape:

{"keep": ["s00002", "s00003"],
 "what_it_is": "one sentence naming the kind of business this is"}

Rules:
- "keep" holds sentence ids ONLY, copied exactly as they appear.
- Choose the sentences a reader MUST see to know what happened: what was decided,
  committed, answered, or quantified. Figures, dates, amounts and named schemes are
  the most valuable things to capture.
- SKIP procedural and social text: thanks, welcomes, greetings, "I beg to move",
  points of order, and sentences that only introduce what comes next.
- SKIP a sentence whose only content is referring a question on to another Minister.
- Keep 3 to 12 sentences for a substantial record, 1 to 4 for a short one.
- Never write, quote or reword a sentence. You are only pointing at ids. The words
  are looked up from the record afterwards, so anything you type other than the ids
  and "what_it_is" is discarded.
- If the record supports nothing worth keeping, return an empty "keep" list. That is
  a correct and expected answer for procedural text."""

SELECT_TMPL = """Sentences from the record, each prefixed with its id:

{excerpt}

Return the JSON described in your instructions."""


def load_item(year, item_id):
    p = os.path.join(ROOT, "pipeline", "dataset", str(year), f"{item_id}.json")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


# A selected sentence can open with a pronoun or connective whose antecedent was not
# selected, and that is unreadable ("It balances long-term investment..." -- what is
# "it"?). This is the ONE problem selection introduces that paraphrasing never had.
# It is also fixable by rule: also take the immediately preceding sentence. No model.
DANGLING = re.compile(
    r"^\s*(This|That|These|Those|It|They|He|She|We|I|There|Such|"
    r"However|Therefore|Thus|Hence|So|And|But|Also|Then|Now)\b", re.I)


def words(s):
    return len(re.findall(r"[A-Za-z0-9']+", s or ""))


def run(item, model):
    sents = item.get("sentences") or []
    by_sid = {s["sid"]: s for s in sents}
    excerpt = "\n".join(f"{s['sid']}: {s['text']}" for s in sents)
    t0 = time.time()
    raw, usage = B.ask(SELECT_TMPL.format(excerpt=excerpt), SELECT_SYSTEM, model)
    dt = time.time() - t0
    got = B.parse_json(raw) or {}

    proposed = [str(x) for x in (got.get("keep") or [])]
    resolved = [s for s in proposed if s in by_sid]
    unresolved = [s for s in proposed if s not in by_sid]

    # rule-based dangling repair: pull in the predecessor of a dangling sentence
    keep = set(resolved)
    added = []
    for sid in resolved:
        if DANGLING.match(by_sid[sid]["text"]):
            i = sents.index(by_sid[sid])
            if i > 0 and sents[i - 1]["sid"] not in keep:
                keep.add(sents[i - 1]["sid"])
                added.append(sents[i - 1]["sid"])

    final = [s for s in sents if s["sid"] in keep]      # document order, always
    return {
        "id": item.get("id"), "group": item.get("group"),
        "sentences_total": len(sents),
        "proposed": proposed, "resolved": resolved, "unresolved": unresolved,
        "repaired": sorted(added), "final": final,
        "what_it_is": got.get("what_it_is") or "",
        "seconds": dt, "in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
        "raw": raw,
    }


def render(results):
    rows = []
    for r in results:
        items = []
        for s in r["final"]:
            flags = []
            if s["sid"] in r["repaired"]:
                flags.append("added to fix a dangling opening")
            items.append(
                f'<li><div class="sid">{s["sid"]}'
                + (f'<span class="flag">{flags[0]}</span>' if flags else "")
                + f'</div><div class="spk">{s["speaker"] or ""}</div>'
                f'<div class="txt">{s["text"]}</div></li>')
        kept_w = sum(words(s["text"]) for s in r["final"])
        rows.append(f"""
  <section>
    <h2>{r['id']}</h2>
    <p class="meta">{r['group']} &middot; {r['sentences_total']} sentences in the record
      &middot; kept <b>{len(r['final'])}</b> &middot; {kept_w} words
      &middot; {r['seconds']:.0f}s &middot; {r['out']} out tokens</p>
    <p class="what"><b>{r['what_it_is']}</b></p>
    <ol class="sel">{''.join(items)}</ol>
    <p class="prov">Unresolvable ids proposed: {r['unresolved'] or 'none'}
      &middot; sentences added by the dangling rule: {r['repaired'] or 'none'}</p>
  </section>""")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spike — sentence selection by id</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#fbfbfa;color:#14181d;font:16px/1.6 -apple-system,BlinkMacSystemFont,
  "Segoe UI",Inter,sans-serif;padding:20px 14px 60px}}
h1{{font-size:1.24rem;line-height:1.3;margin-bottom:.4rem}}
.lede{{color:#5c6773;font-size:.86rem;margin-bottom:1.6rem}}
h2{{font-size:1rem;margin:1.8rem 0 .3rem;padding-top:1rem;border-top:1px solid #e3e7ec}}
.meta{{color:#657080;font-size:.76rem;margin-bottom:.5rem}}
.what{{font-size:.92rem;margin-bottom:.8rem;color:#14181d}}
ol.sel{{list-style:none}}
ol.sel li{{border-left:3px solid #1c6b4a;padding:.1rem 0 .1rem .8rem;margin-bottom:1rem}}
.sid{{font:600 .68rem/1.4 ui-monospace,Menlo,monospace;color:#1c6b4a;
  letter-spacing:.04em;text-transform:uppercase}}
.sid .flag{{margin-left:.5rem;color:#a4551f;text-transform:none;font-weight:500}}
.spk{{font-size:.74rem;color:#657080;margin:.15rem 0 .25rem}}
.txt{{font-size:.95rem;line-height:1.62}}
.prov{{font-size:.74rem;color:#657080;margin-top:.7rem}}
@media (min-width:620px){{body{{max-width:760px;margin:0 auto;padding:36px 24px}}}}
</style></head><body>
<h1>Can the model select, instead of write?</h1>
<p class="lede">The model returned only sentence ids. Every word below was copied from
the archived Hansard record by id &mdash; the model never wrote any of it.</p>
{''.join(rows)}
</body></html>"""


def main():
    model = B.DEFAULT_MODEL
    # one tiny record and one mid-size one, so the contrast is visible:
    # 5 sentences (fits one call) vs 40 sentences (~1,000 words, needs care)
    targets = [("2026", "oral-answer-4101"), ("2026", "oral-answer-3964")]
    results = []
    for year, iid in targets:
        try:
            item = load_item(year, iid)
        except OSError:
            print(f"{iid}: not found"); continue
        r = run(item, model)
        results.append(r)
        print(f"\n{'=' * 74}\n{iid}  ({r['sentences_total']} sentences, {r['seconds']:.0f}s, "
              f"{r['out']} out tokens)")
        print(f"  proposed ids : {len(r['proposed'])}  -> resolved {len(r['resolved'])}"
              f"  unresolved {len(r['unresolved'])}")
        print(f"  dangling fix : {r['repaired'] or 'none needed'}")
        print(f"  final        : {len(r['final'])} sentences, "
              f"{sum(words(s['text']) for s in r['final'])} words")
        print(f"  what_it_is   : {r['what_it_is'][:110]}")
        print("  --- the selected sentences, verbatim ---")
        for s in r["final"]:
            print(f"    {s['sid']}  {s['text'][:150]}")
    html = render(results)
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"\nwrote {OUT_HTML}  ({len(html):,} chars)")
    print(f"total out tokens: {sum(r['out'] or 0 for r in results)}")


if __name__ == "__main__":
    main()
