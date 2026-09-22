#!/usr/bin/env python3
"""
Unit tests for ingest/legislation.py — the vote-breakdown mapping and the rules it rests on.

The card's acceptance criterion names this file: "unit tests cover the vote-breakdown
mapping". What that means here, given the spec superseded the card's per-member
requirement (docs/rag_spec.md §1.4, §8), is that the mapping from a declaration *sentence*
to the schema's `division` object is pinned by tests, including the cases that are easy to
get wrong and are not visible from a happy-path sample:

  * totals stated as words ("six", "nine", "two") vs digits, and mixed in one sentence
  * "no"/"zero" for abstentions, and an abstention clause the Chair never stated (null,
    which is NOT the same fact as a stated zero)
  * the two-reading record: same numbers twice is one outcome, different numbers are two
  * `per_member` is null on every declaration, always — the schema types it null and its own
    must-reject test asserts that populating it is an error
  * the division-dedup key is (doc_id, ayes, noes, abstentions), so a doc-level dedup and a
    count-level dedup can be told apart (bill-213 declares twice identically; bill-367 twice
    differently)

Run:  python3 -m unittest ingest.test_legislation -v
      python3 ingest/test_legislation.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import legislation as L  # noqa: E402


def decls_of(text):
    return L.dedupe_declarations(L.extract_declarations(text))


class TestDeclarationParsing(unittest.TestCase):
    """The Chair's declaration -> (ayes, noes, abstentions, raw)."""

    def test_digits_with_quotes(self):
        d = decls_of('There are 72 "Ayes", 9 "Noes", and no "Abstentions".')
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["ayes"], d[0]["noes"], d[0]["abstentions"]), (72, 9, 0))

    def test_words_mixed_with_digits(self):
        """The real bill-230 sentence. 'six' must not become None."""
        d = decls_of('There are 77 "Ayes", six "Noes", and zero "Abstentions".')
        self.assertEqual((d[0]["ayes"], d[0]["noes"], d[0]["abstentions"]), (77, 6, 0))

    def test_capitalised_number_word(self):
        """bill-367 again: 'Nine' capitalised mid-sentence."""
        d = decls_of('There are 72 "Ayes", Nine "Noes" and three "Abstentions".')
        self.assertEqual((d[0]["ayes"], d[0]["noes"], d[0]["abstentions"]), (72, 9, 3))

    def test_singular_abstention(self):
        d = decls_of('There are 74 "Ayes", nine "Noes", and one "Abstention".')
        self.assertEqual((d[0]["ayes"], d[0]["noes"], d[0]["abstentions"]), (74, 9, 1))

    def test_missing_abstention_clause_is_null_not_zero(self):
        """Absent is a different fact from stated-zero, so it must be None, not 0."""
        d = decls_of('There are 80 "Ayes, zero "Noes".')
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["abstentions"], None)

    def test_unquote_mismatched_ayes(self):
        """The malformed source: 'Ayes' opens without a closing quote (motion-883)."""
        d = decls_of('There are 80 "Ayes, zero "Noes", and zero "Abstentions".')
        self.assertEqual((d[0]["ayes"], d[0]["noes"], d[0]["abstentions"]), (80, 0, 0))

    def test_literal_zero(self):
        d = decls_of('There are 84 "Ayes", 0 "Noes", and 0 "Abstentions".')
        self.assertEqual((d[0]["ayes"], d[0]["noes"], d[0]["abstentions"]), (84, 0, 0))

    def test_no_match_returns_empty(self):
        self.assertEqual(decls_of("The Ayes have it."), [])

    def test_unparseable_count_is_dropped_not_guessed(self):
        """A count the rule cannot read must yield nothing, never a partial object."""
        self.assertEqual(decls_of('There are many "Ayes", 9 "Noes".'), [])

    def test_raw_is_the_verbatim_sentence(self):
        text = 'I will proceed to declare the voting results. There are 75 "Ayes", 11 "Noes" and two "Abstentions". The "Ayes" have it.'
        d = decls_of(text)
        self.assertEqual(d[0]["raw"], 'There are 75 "Ayes", 11 "Noes" and two "Abstentions"')
        self.assertIn(d[0]["raw"], text)


class TestDeduplication(unittest.TestCase):
    """spec §2: deduplicate on (doc_id, ayes, noes, abstentions)."""

    def test_identical_declaration_twice_collapses(self):
        """bill-213 / bill-230: the same result read at two readings."""
        text = ('There are 72 "Ayes", 9 "Noes", and no "Abstentions". '
                'There are 72 "Ayes", 9 "Noes", and no "Abstentions".')
        self.assertEqual(len(decls_of(text)), 1)

    def test_different_declaration_twice_is_kept(self):
        """bill-367: 74/9/1 at second reading, 72/9/3 at third. Two outcomes, not one."""
        text = ('There are 74 "Ayes", nine "Noes", and one "Abstention". '
                'There are 72 "Ayes", Nine "Noes" and three "Abstentions".')
        d = decls_of(text)
        self.assertEqual(len(d), 2)
        self.assertEqual([(x["ayes"], x["noes"], x["abstentions"]) for x in d],
                         [(74, 9, 1), (72, 9, 3)])

    def test_order_is_document_order(self):
        text = ('There are 72 "Ayes", Nine "Noes" and three "Abstentions". '
                'There are 74 "Ayes", nine "Noes", and one "Abstention".')
        d = decls_of(text)
        self.assertEqual([x["ayes"] for x in d], [72, 74])

    def test_dedup_key_is_counts_not_doc_id(self):
        """Two different docs declaring the same numbers are two rows; the same doc
        declaring two different results is also two rows. Both directions matter."""
        a = L.division_metadata([{"ayes": 72, "noes": 9, "abstentions": 0, "raw": "r"}])
        self.assertEqual(a[0]["ayes"], 72)
        both = L.division_metadata([{"ayes": 74, "noes": 9, "abstentions": 1, "raw": "r1"},
                                    {"ayes": 72, "noes": 9, "abstentions": 3, "raw": "r2"}])
        self.assertEqual(len(both[1]), 2)


class TestDivisionMetadataShape(unittest.TestCase):
    """The object written into `metadata.division` / `metadata.divisions`."""

    def test_per_member_is_always_null(self):
        """The superseded card requirement. `null` is required by the schema; populating
        it is a schema error, so it is asserted here rather than left to review."""
        _, divisions = L.division_metadata(
            [{"ayes": 72, "noes": 9, "abstentions": 0, "raw": "r"}])
        for d in divisions:
            self.assertIn("per_member", d)
            self.assertIsNone(d["per_member"])

    def test_required_keys_present(self):
        """schema: division requires ayes, noes, raw."""
        d = L.division_metadata([{"ayes": 1, "noes": 2, "abstentions": None, "raw": "x"}])[0]
        for k in ("ayes", "noes", "raw"):
            self.assertIn(k, d)
        self.assertIsNone(d["abstentions"])

    def test_no_declarations_gives_nulls(self):
        self.assertEqual(L.division_metadata([]), (None, None))

    def test_division_is_the_first_and_divisions_is_all(self):
        """A two-reading record cannot fit both declarations in the single-object field,
        so `division` carries the first and `divisions` the ordered list."""
        first, allof = L.division_metadata(
            [{"ayes": 74, "noes": 9, "abstentions": 1, "raw": "r1"},
             {"ayes": 72, "noes": 9, "abstentions": 3, "raw": "r2"}])
        self.assertEqual(first["ayes"], 74)
        self.assertEqual([d["ayes"] for d in allof], [74, 72])


class TestTextJoining(unittest.TestCase):
    """The declaration lives in the joined turn text, which is what the rule scans."""

    def test_declaration_split_across_turns_is_not_lost(self):
        turns = ['I will now proceed to declare the voting results.',
                 'There are 82 "Ayes", 11 "Noes", zero "Abstentions".']
        self.assertEqual(len(decls_of("\n".join(turns))), 1)

    def test_declaration_inside_procedural_text(self):
        """bill-367's declaration sits in a turn that also carries bracketed chair text."""
        text = ('There are 74 "Ayes", nine "Noes", and one "Abstention". The "Ayes" have it. '
                '[(proc text) Bill accordingly read a Second time and committed to a '
                'Committee of the whole House. (proc text)]')
        d = decls_of(text)
        self.assertEqual((d[0]["ayes"], d[0]["noes"], d[0]["abstentions"]), (74, 9, 1))


class TestMemberRule(unittest.TestCase):
    """spec §3.3 — the ORDER is the whole trick, so it is tested, not just used."""

    def setUp(self):
        # A roster fixture. Deliberately contains no member id anywhere, mirroring the
        # source (`id: 0` on all 574 entries) — the id is derived from the spelling.
        self.members = L.MemberSet({
            "mpVOList": [{"id": 0, "fullName": "K Shanmugam"},
                         {"id": 0, "fullName": "Pritam Singh"}],
            "formerMPVOList": [{"id": 0, "fullName": "Ravindran R"}],
        })

    def test_officer_tested_before_honorific_stripping(self):
        """'Mr Speaker' is only recognisable WITH the honorific. Stripping first was the
        bug that made an earlier probe report a 14.6% unresolved rate."""
        for s in ("Mr Speaker", "Mdm Speaker", "The Chairman", "Mr Deputy Speaker"):
            self.assertEqual(self.members.resolve(s), (None, L.K_OFFICER), s)

    def test_officers_never_get_a_member_id(self):
        self.assertIsNone(self.members.member_id_of("Mr Speaker"))
        self.assertIsNone(self.members.member_id_of("The Chairman"))

    def test_plain_name_resolves(self):
        self.assertEqual(self.members.member_id_of("Mr K Shanmugam"), "sg-mp:k-shanmugam")

    def test_bare_name_resolves(self):
        self.assertEqual(self.members.member_id_of("Pritam Singh"), "sg-mp:pritam-singh")

    def test_portfolio_prefix_parenthetical_resolves(self):
        """The one that matters most: the parenthetical is the NAME, the prefix is the office."""
        for s in ("The Minister for Home Affairs (Mr K Shanmugam)",
                  "The Minister for Law (Mr K Shanmugam)"):
            self.assertEqual(self.members.member_id_of(s), "sg-mp:k-shanmugam", s)

    def test_constituency_parenthetical_resolves(self):
        self.assertEqual(self.members.member_id_of("Mr Pritam Singh (Aljunied)"),
                         "sg-mp:pritam-singh")

    def test_bracketed_is_non_person(self):
        self.assertEqual(self.members.resolve("[Mr Speaker in the Chair]")[0], None)
        self.assertEqual(self.members.resolve("[Mr Speaker in the Chair]")[1], L.K_NON_PERSON)

    def test_unknown_name_is_unresolved_not_guessed(self):
        mid, kind = self.members.resolve("Mr Alex Yam Ziming")
        self.assertIsNone(mid)
        self.assertEqual(kind, L.K_UNRESOLVED)

    def test_hon_members_is_the_house_not_a_person(self):
        for s in ("Hon Members", "Some hon Members", "An hon Member"):
            self.assertEqual(self.members.resolve(s)[0], None, s)
            self.assertEqual(self.members.resolve(s)[1], L.K_NON_PERSON, s)

    def test_roster_name_beginning_with_hon_is_not_shadowed(self):
        """Regression. The non-person rule runs BEFORE the roster match, so a broad
        `^(hon|honourable)` prefix made the roster's own Hon Sui Sen unresolvable to
        himself -- his own full name came back `non_person`. Caught by the audit that
        re-resolves every alias to its own member."""
        m = L.MemberSet({"mpVOList": [{"id": 0, "fullName": "Hon Sui Sen"}],
                         "formerMPVOList": []})
        self.assertEqual(m.member_id_of("Hon Sui Sen"), "sg-mp:hon-sui-sen")
        self.assertEqual(m.resolve("Hon Sui Sen")[1], "member:raw")

    def test_no_roster_name_is_shadowed_by_the_non_person_heuristic(self):
        """The general form of the bug above: a roster name must resolve to itself.

        The officer rule is deliberately NOT included in this guarantee. Measured against
        the real corpus, reordering to put the roster first would change exactly 9
        member_id verdicts, and every one is a string like
        'Mr Deputy Speaker  (Mr Lim Biow Chuan)' — the source writing the OFFICE with the
        member's name in parentheses. Spec §3.3 mandates officer classification for those
        ("they are not members and must never be given a member id"), and the spec's
        published resolution figures depend on that order, so it is left alone: a spec
        decision, not something to quietly re-decide here.
        """
        m = L.MemberSet({"mpVOList": [{"id": 0, "fullName": "Hon Sui Sen"},
                                      {"id": 0, "fullName": "K Shanmugam"}],
                         "formerMPVOList": []})
        for name in ("Hon Sui Sen", "K Shanmugam"):
            self.assertEqual(m.member_id_of(name), L.slug_member_id(name), name)

    def test_officer_prefixed_speaker_stays_an_officer(self):
        """The spec order, pinned so a later 'fix' has to argue with a test."""
        m = L.MemberSet({"mpVOList": [{"id": 0, "fullName": "Lim Biow Chuan"}],
                         "formerMPVOList": []})
        self.assertIsNone(m.member_id_of("Mr Deputy Speaker  (Mr Lim Biow Chuan)"))

    def test_resolution_never_mints_an_id(self):
        """Every non-null id must be one the roster actually produced."""
        roster_ids = {m["member_id"] for m in self.members.records()}
        for s in ("Mr K Shanmugam", "Mr Speaker", "Nobody At All", "The Minister (Mr K Shanmugam)"):
            mid = self.members.member_id_of(s)
            if mid is not None:
                self.assertIn(mid, roster_ids)

    def test_duplicate_roster_entries_collapse_to_one_row(self):
        """574 name entries -> 569 ids. A duplicate row for one person is a defect."""
        m = L.MemberSet({"mpVOList": [{"id": 0, "fullName": "Mark Lee"}],
                         "formerMPVOList": [{"id": 0, "fullName": "Mark Lee"}]})
        self.assertEqual(len(m.records()), 1)

    def test_aliases_are_kept_per_member(self):
        for s in ("Mr K Shanmugam", "The Minister for Law (Mr K Shanmugam)"):
            self.members.observe(s)
        rec = next(r for r in self.members.records() if r["member_id"] == "sg-mp:k-shanmugam")
        self.assertIn("Mr K Shanmugam", rec["aliases"])
        self.assertIn("The Minister for Law (Mr K Shanmugam)", rec["aliases"])

    def test_portfolio_harvested_from_the_prefix(self):
        self.members.observe("The Minister for Law (Mr K Shanmugam)")
        rec = next(r for r in self.members.records() if r["member_id"] == "sg-mp:k-shanmugam")
        self.assertEqual(rec["portfolio"], "The Minister for Law")

    def test_constituency_harvested_from_trailing_paren(self):
        self.members.observe("Mr Pritam Singh (Aljunied)")
        rec = next(r for r in self.members.records() if r["member_id"] == "sg-mp:pritam-singh")
        self.assertEqual(rec["constituency"], "Aljunied")


class TestMpNamesSplit(unittest.TestCase):
    """A comma inside a parenthetical is not a separator — measured, this mangled 108
    name fragments when the split was naive."""

    def test_parenthetical_comma_is_not_a_separator(self):
        got = L.split_mp_names("The Second Minister for Law (Mr Edwin Tong Chun Fai),Mr Alex Yam")
        self.assertEqual(got, ["The Second Minister for Law (Mr Edwin Tong Chun Fai)",
                               "Mr Alex Yam"])

    def test_list_wrapped_in_brackets(self):
        got = L.split_mp_names("[Ms Anthea Ong, Mr Desmond Lee]")
        self.assertEqual(got, ["Ms Anthea Ong", "Mr Desmond Lee"])

    def test_empty_and_none(self):
        self.assertEqual(L.split_mp_names(None), [])
        self.assertEqual(L.split_mp_names(""), [])


class TestIsoDate(unittest.TestCase):
    """spec §3.2: the source is NOT zero-padded and a raw date breaks range filters."""

    def test_conversion(self):
        self.assertEqual(L.iso_date("15-1-2016"), "2016-01-15")
        self.assertEqual(L.iso_date("3-3-2026"), "2026-03-03")

    def test_lexicographic_order_after_conversion(self):
        """The reason for converting: raw D-M-YYYY strings mis-sort, ISO strings do not.

        Counterexample measured, not asserted from the spec's prose: '9-1-2016' (9 Jan)
        sorts AFTER '15-1-2016' (15 Jan) as raw strings, because '9' > '1' at position 0.
        Converted, they order correctly. (docs/rag_spec.md §3.2 illustrates this with
        "'15-1' > '3-3'", which is false as written — character 0 compares '1' < '3'. The
        claim that raw dates mis-sort is right; that particular example is not, so the
        test uses one that reproduces.)
        """
        self.assertGreater("9-1-2016", "15-1-2016")            # the raw source, broken
        self.assertLess(L.iso_date("9-1-2016"), L.iso_date("15-1-2016"))

    def test_unparseable_returns_input_unchanged(self):
        self.assertEqual(L.iso_date("not-a-date"), "not-a-date")


class TestDocumentMapping(unittest.TestCase):
    """End-to-end on a saved fixture: one raw report -> one schema-shaped document."""

    def test_fixture_maps_to_expected_document(self):
        import json
        fx = os.path.join(HERE, "fixtures", "legislation_fixture.json")
        with open(fx, encoding="utf-8") as fh:
            payload = json.load(fh)
        members = L.MemberSet(payload["roster"])
        rep = payload["report"]
        doc = L.document_from(rep, members, payload["coverage"])

        self.assertEqual(doc["doc_id"], payload["expect"]["doc_id"])
        self.assertEqual(doc["source"], "sg-hansard")
        self.assertEqual(doc["doc_type"], payload["expect"]["doc_type"])
        self.assertEqual(doc["section"], payload["expect"]["section"])
        self.assertEqual(doc["date"], payload["expect"]["date"])
        self.assertEqual(doc["title"], payload["expect"]["title"])
        self.assertEqual(doc["url"], payload["expect"]["url"])
        # the cross-link the card asks for
        self.assertEqual(doc["member_id"], payload["expect"]["member_id"])
        self.assertEqual(doc["metadata"]["sponsor"]["member_id"],
                         payload["expect"]["sponsor_member_id"])
        # the vote breakdown
        div = doc["metadata"]["division"]
        self.assertEqual((div["ayes"], div["noes"], div["abstentions"]),
                         tuple(payload["expect"]["division"]))
        self.assertIsNone(div["per_member"])
        self.assertEqual(len(doc["metadata"]["divisions"]),
                         payload["expect"]["distinct_declarations"])

    def test_every_turn_member_id_comes_from_the_roster(self):
        import json
        fx = os.path.join(HERE, "fixtures", "legislation_fixture.json")
        with open(fx, encoding="utf-8") as fh:
            payload = json.load(fh)
        members = L.MemberSet(payload["roster"])
        roster_ids = {m["member_id"] for m in members.records()}
        doc = L.document_from(payload["report"], members, payload["coverage"])
        for t in doc["turns"]:
            if t["member_id"]:
                self.assertIn(t["member_id"], roster_ids)

    def test_no_field_outside_the_schema(self):
        """`additionalProperties: false` on the document, so a stray key is a hard fail."""
        import json
        allowed = {"doc_id", "source", "doc_type", "section", "date", "title", "speaker_raw",
                   "member_id", "text", "turn_count", "word_count", "url", "turns", "metadata"}
        with open(os.path.join(HERE, "fixtures", "legislation_fixture.json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        doc = L.document_from(payload["report"], L.MemberSet(payload["roster"]), payload["coverage"])
        self.assertFalse(set(doc) - allowed, set(doc) - allowed)

    def test_document_text_keeps_speaker_prefixes(self):
        import json
        with open(os.path.join(HERE, "fixtures", "legislation_fixture.json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        doc = L.document_from(payload["report"], L.MemberSet(payload["roster"]), payload["coverage"])
        # bill-367's first turn is unattributed procedural text, so the leading line has no
        # prefix; every attributed turn after it must carry "<speaker>: ".
        self.assertTrue(doc["text"].startswith("[(proc text)"))
        for t in doc["turns"]:
            if t["speaker"]:
                self.assertIn(f'{t["speaker"]}: ', doc["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
