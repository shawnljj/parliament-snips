"""Unit tests for the debate ingester's record mapping -- offline, against a saved fixture.

    python3 -m pytest ingest/test_debates.py -q
    python3 ingest/test_debates.py          # same thing without pytest

WHAT THIS TESTS, AND WHY IT IS SHAPED THIS WAY. `map_report()` is the function that turns a
source record into a normalized document, and it is where every silent-corruption bug would
live: a speaker attributed to the wrong member, a date left in the source's D-M-YYYY, a
record with no text dropped instead of kept, the wrong partition's records emitted. Every
test here exists because of a specific way that could go wrong, and each one asserts the
behaviour, not the implementation.

The fixture (`ingest/fixtures.py`) is a real slice of the corpus, built by
`docs/probes/ingest_fixture_build.py` and re-checked against the corpus by
`docs/probes/ingest_fixture_check.py`. So these tests run in under a second with no network
and no `data/`, while still being tests about real records rather than about invented ones.

The schema is validated for real (`jsonschema`, the same library `eval/test_schema.py`
uses). A mapping function that emits rows its own schema rejects is the failure this whole
task is graded on, so it is asserted here rather than assumed.
"""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import debates as D                                                # noqa: E402
import fixtures as FX                                              # noqa: E402
import rag_schema as S                                             # noqa: E402

# The "asked the Minister" opening, asserted here rather than imported from the ingester:
# a test that borrows the implementation's own pattern cannot catch the pattern being wrong.
QUESTION_OPEN_MARK = re.compile(r"^\W*asked\s+the\s+\S", re.I)


def mapped(doc_id, stats=None):
    """Map one fixture record through the real mapper, offline."""
    rep = FX.report(doc_id)
    ctx = FX.context_for(S.iso_date(rep["sitting_date"]))
    roster = D.Roster(FX.ROSTER)
    doc, skipped = D.map_report(rep, ctx, roster, stats=stats)
    return doc, skipped, roster


class TestDateConversion(unittest.TestCase):
    """spec 3.2: the source serves D-M-YYYY and a raw source date must not reach a consumer."""

    def test_source_date_is_converted_to_iso(self):
        doc, _, _ = mapped("budget-855")
        self.assertEqual(doc["date"], "2018-03-01")

    def test_unpadded_day_and_month_are_zero_padded(self):
        # the corpus really contains "3-3-2026" and "15-1-2016"
        self.assertEqual(S.iso_date("3-3-2026"), "2026-03-03")
        self.assertEqual(S.iso_date("15-1-2016"), "2016-01-15")
        self.assertEqual(S.iso_date("2026-03-03"), "2026-03-03")

    def test_a_raw_source_date_is_rejected_by_the_schema(self):
        """The reason conversion is mandatory: the pattern catches a leaked raw date."""
        doc, _, _ = mapped("budget-855")
        bad = dict(doc, date="1-3-2018")
        errs = S.validate_document(bad)
        self.assertTrue(any("date" in e for e in errs),
                        f"schema accepted a raw source date: {errs}")

    def test_impossible_calendar_dates_are_not_parsed(self):
        self.assertIsNone(S.iso_date("31-2-2016"))
        self.assertIsNone(S.iso_date("not a date"))

    def test_a_record_with_no_parseable_date_is_an_error_not_a_row(self):
        """A wrong date is worse than a missing record: range filters fail silently."""
        rep = FX.report("budget-855")
        rep["sitting_date"] = "garbage"
        ctx = {"date": "not a date", "coverage": {}}
        doc, skipped = D.map_report(rep, ctx, D.Roster(FX.ROSTER))
        self.assertIsNone(doc)
        self.assertEqual(skipped[0], "bad_date")


class TestPartition(unittest.TestCase):
    """spec 8: bills and motions are ingest/legislation.py's records, not debates'."""

    def test_a_bill_is_refused(self):
        doc, skipped, _ = mapped("bill-194")
        self.assertIsNone(doc)
        self.assertEqual(skipped, "legislation_partition")

    def test_a_motion_is_refused(self):
        rep = FX.report("bill-194")
        rep["group"] = "motion"
        doc, skipped = D.map_report(rep, FX.context_for("2016-02-29"), D.Roster(FX.ROSTER))
        self.assertIsNone(doc)
        self.assertEqual(skipped, "legislation_partition")

    def test_every_other_section_is_kept_and_gets_the_right_doc_type(self):
        for section, doc_type in (("oral", "debate"), ("written", "debate"),
                                  ("budget", "debate"), ("tribute", "debate"),
                                  ("other", "debate")):
            self.assertEqual(S.doc_type_for(section), doc_type)
        # bill/motion map to themselves -- and are the only two that do
        self.assertEqual(S.doc_type_for("bill"), "bill")
        self.assertEqual(S.doc_type_for("motion"), "motion")


class TestSpeakerAttribution(unittest.TestCase):
    """The asset that makes this corpus worth ingesting (README, spec 3.3)."""

    def test_a_portfolio_parenthetical_resolves_to_the_member(self):
        """spec 3.3: 'The Minister for ... (Mr X)' -> X. The audit trail is speaker_raw."""
        roster = D.Roster(FX.ROSTER)
        kind, mid = roster.resolve(
            "The Minister for Social and Family Development (Mr Tan Chuan-Jin)")
        self.assertEqual(kind, "MEMBER")
        self.assertEqual(mid, "sg-mp:tan-chuan-jin")
        # and a double parenthetical, which the corpus really produces
        kind, mid = roster.resolve("The Senior Minister of State for Home Affairs "
                                  "(Mr Desmond Lee) (for the Minister for Home Affairs)")
        self.assertEqual((kind, mid), ("MEMBER", "sg-mp:desmond-lee"))

    def test_a_chamber_officer_never_gets_a_member_id(self):
        """spec 3.3 step 1: officers are not members and must never be given an id."""
        roster = D.Roster(FX.ROSTER)
        for raw in ("Mr Speaker", "Mdm Speaker", "The Chairman", "Deputy Speaker"):
            kind, mid = roster.resolve(raw)
            self.assertEqual(kind, "OFFICER", raw)
            self.assertIsNone(mid, raw)

    def test_the_officer_test_runs_before_honorifics_are_stripped(self):
        """The order IS the trick -- stripping first makes 'Mr Speaker' unmatchable.

        spec 3.3 records the cost of getting this wrong: the apparent unresolved rate went
        from 0.1% to 14.6%.
        """
        roster = D.Roster(FX.ROSTER)
        kind, _ = roster.resolve("Mr Speaker")
        self.assertEqual(kind, "OFFICER")
        # ...and the normalized form of "Mr Speaker", which has lost the honorific, is a
        # different string and is not what the officer test sees.
        self.assertEqual(D.norm_name("Mr Speaker"), "speaker")

    def test_a_bracketed_string_is_not_a_person(self):
        roster = D.Roster(FX.ROSTER)
        for raw in ("[Mr Speaker in the Chair]", "(proc text)", "Hon Members"):
            kind, mid = roster.resolve(raw)
            self.assertIn(kind, ("NON_PERSON",), raw)
            self.assertIsNone(mid)

    def test_an_unresolvable_name_keeps_speaker_raw_and_gets_a_null_id(self):
        """spec 3.3 step 5: never guess an id."""
        doc, _, _ = mapped("admin-oaths-240")
        roster = D.Roster(FX.ROSTER)
        kind, mid = roster.resolve("Ms Totally Made Up Name")
        self.assertEqual(kind, "UNRESOLVED")
        self.assertIsNone(mid)

    def test_the_name_variant_fallback_resolves_and_refuses_when_ambiguous(self):
        """The one measured delta between spec 3.3's prose and its own quoted numbers."""
        roster = D.Roster(FX.ROSTER)
        # "Mohd Fahmi Bin Aliman" against the roster's "Mohd Fahmi Aliman": the middle
        # token differs, so only a first+last match finds it. The string is real -- it is on
        # the record FX.BRANCHES["variant_fallback"].
        turns = FX.report(FX.BRANCHES["variant_fallback"])["turns"]
        raws = [t["speaker"] for t in turns
                if (t["speaker"] or "").startswith("Mr Mohd Fahmi Bin Aliman")]
        self.assertEqual(len(raws), 1, "the variant branch record lost its variant speaker")
        kind, mid = roster.resolve(raws[0])
        self.assertEqual(kind, "MEMBER", raws[0])
        self.assertEqual(mid, "sg-mp:mohd-fahmi-aliman")
        # the same person written plainly also resolves, through step 3 rather than step 4
        self.assertEqual(roster.resolve("Mohd Fahmi Aliman"),
                         ("MEMBER", "sg-mp:mohd-fahmi-aliman"))
        # two roster names sharing the first and last token -> refuse, do not pick one
        ambiguous = D.Roster({"mpVOList": [{"id": 0, "fullName": "Ali Bin Ahmad"},
                                           {"id": 0, "fullName": "Ali Bakar Ahmad"}],
                              "formerMPVOList": []})
        kind, mid = ambiguous.resolve("Mr Ali Ahmad")
        self.assertEqual(kind, "UNRESOLVED")
        self.assertIsNone(mid)

    def test_the_two_roster_names_with_one_normalized_form_are_the_same_person(self):
        """spec 3.3 step 4: the whole roster has exactly one normalized collision, so a
        collision is a warning rather than a failure -- and both forms must resolve."""
        roster = D.Roster(FX.ROSTER)
        self.assertEqual(roster.collisions, {"r ravindran": ["R Ravindran", "R. Ravindran"]})
        for raw in ("R Ravindran", "R. Ravindran"):
            self.assertEqual(roster.resolve(raw), ("MEMBER", "sg-mp:r-ravindran"), raw)

    def test_the_document_speaker_is_the_first_attributed_turn(self):
        doc, _, _ = mapped("written-answer-na-6512")
        self.assertEqual(doc["speaker_raw"], "Ms Indranee Rajah (for the Prime Minister)")
        self.assertEqual(doc["member_id"], "sg-mp:indranee-rajah")

    def test_an_officer_principal_speaker_gets_a_null_member_id_on_the_document(self):
        doc, _, _ = mapped("admin-oaths-240")
        self.assertEqual(doc["speaker_raw"], "Mdm Speaker")
        self.assertIsNone(doc["member_id"])

    def test_every_turn_keeps_its_own_speaker_verbatim_and_in_order(self):
        doc, _, _ = mapped("president-address-242")
        speakers = [t["speaker"] for t in doc["turns"]]
        self.assertEqual(speakers, [t["speaker"] for t in FX.report("president-address-242")["turns"]])
        # and the unattributed turn is still there, at its own position
        self.assertIn(None, speakers)

    def test_a_turn_with_no_speaker_is_kept_not_dropped(self):
        """spec 3.4: 2,410 turns (2.6%) carry no speaker, and `speaker: null` is valid."""
        doc, _, _ = mapped("president-address-242")
        self.assertTrue(any(t["speaker"] is None for t in doc["turns"]))

    def test_an_unattributed_turn_does_not_inherit_the_previous_speaker(self):
        doc, _, _ = mapped("president-address-242")
        nulls = [t for t in doc["turns"] if t["speaker"] is None]
        self.assertTrue(nulls)

    def test_a_resolved_member_is_recorded_as_an_alias(self):
        """What makes a speaker filter work: one member, every way the corpus spells them."""
        doc, _, roster = mapped("written-answer-na-6512")
        self.assertIn("Ms Indranee Rajah (for the Prime Minister)",
                      roster.aliases["sg-mp:indranee-rajah"])

    def test_a_speaker_string_with_a_zero_width_mark_still_resolves(self):
        """The source glues an invisible mark to 10 speaker strings in the corpus.

        `'\\ufeffMdm Speaker'` is `Mdm Speaker` in every visible sense, but the officer pattern
        is `^`-anchored, so as served the string matched neither the officer test nor the
        roster and the turn lost its attribution. Measured by
        docs/probes/ingest_doc_numbers.py: 10 strings, 4 of which resolve differently once the
        mark is removed (2 to OFFICER, 2 to NON_PERSON) -- the other 6 resolved anyway via the
        roster, so this is a narrow fix, not a reclassification.
        """
        roster = D.Roster(FX.ROSTER)
        self.assertEqual(roster.resolve("\ufeffMdm Speaker"), ("OFFICER", None))
        self.assertEqual(roster.resolve("Mr Deputy Speaker\ufeff"), ("OFFICER", None))
        self.assertEqual(roster.resolve("\ufeffThe Chairman"), ("OFFICER", None))
        self.assertEqual(roster.resolve("[Mr Speaker in the Chair]\ufeff"),
                         ("NON_PERSON", None))
        # a mark INSIDE a name does not stop the roster match either
        self.assertEqual(roster.resolve("\ufeffMr Murali Pillai\ufeff"),
                         ("MEMBER", "sg-mp:murali-pillai"))

    def test_stripping_invisible_marks_never_invents_a_resolution(self):
        """The narrow claim that makes this safe: it can only move a string INTO a
        resolution, never out of UNRESOLVED into a guessed member."""
        roster = D.Roster(FX.ROSTER)
        for raw in ("Ms Totally Made Up Name", "\ufeffMs Totally Made Up Name",
                    "\ufeffZzz Zzz", "13\ufeff", "\ufeff"):
            kind, mid = roster.resolve(raw)
            self.assertIsNone(mid, f"{raw!r} produced an id")
            self.assertNotEqual(kind, "MEMBER", raw)

    def test_the_speaker_field_keeps_the_marks_the_source_served(self):
        """Resolution strips them; the record does not. `speaker` is the audit trail."""
        rep = FX.report("budget-855")
        rep["turns"][0]["speaker"] = "\ufeffMdm Speaker"
        doc, _ = D.map_report(rep, FX.context_for("2018-03-01"), D.Roster(FX.ROSTER))
        self.assertEqual(doc["turns"][0]["speaker"], "\ufeffMdm Speaker")
        self.assertIsNone(doc["turns"][0]["member_id"])       # an officer: no id, by design


class TestTurnsAndText(unittest.TestCase):
    def test_turns_preserve_lang_time_and_procedural_flag(self):
        doc, _, _ = mapped("budget-855")
        for t in doc["turns"]:
            self.assertEqual(t["lang"], "English")
            self.assertIn("is_procedural", t)
            self.assertIn("time", t)

    def test_a_procedural_turn_is_kept_and_flagged(self):
        """spec 3.4: 1,541 turns are bracketed chair/clerical text. Dropping them loses
        gq-026 and gq-028 (they carry division declarations), so they are kept and flagged."""
        doc, _, _ = mapped("president-address-242")
        self.assertTrue(any(t["is_procedural"] for t in doc["turns"]))

    def test_document_text_is_speaker_prefixed_lines(self):
        """The embedded form: a question naming a member is otherwise unretrievable."""
        doc, _, _ = mapped("budget-855")
        lines = doc["text"].split("\n")
        self.assertEqual(len(lines), len(doc["turns"]))
        for line, turn in zip(lines, doc["turns"]):
            expected = f'{turn["speaker"]}: {turn["text"]}' if turn["speaker"] else turn["text"]
            self.assertEqual(line, expected)

    def test_clock_stamps_are_kept_verbatim_and_not_parsed(self):
        """spec 3.4: '10.17 am' is source-local, has no timezone, and order comes from the
        array, not from this field. Only 28,302 of 92,446 turns have one."""
        doc, _, _ = mapped("budget-855")
        rep = FX.report("budget-855")
        self.assertEqual([t["time"] for t in doc["turns"]],
                         [t["time"] for t in rep["turns"]])
        self.assertIn("10.17 am", [t["time"] for t in doc["turns"]])

    def test_the_time_field_can_hold_a_non_clock_string_and_is_still_kept_verbatim(self):
        """A real source quirk found while building the fixture: some records carry a topic
        label where the clock stamp should be ('Medical Classification System' on
        budget-1851). The field is display metadata, nothing parses it, and spec 3.4 says
        order comes from array position -- so it is kept as served rather than filtered."""
        doc, _, _ = mapped("budget-1851")
        times = [t["time"] for t in doc["turns"] if t["time"]]
        self.assertTrue(times)
        rep = FX.report("budget-1851")
        self.assertEqual([t["time"] for t in doc["turns"]],
                         [t["time"] for t in rep["turns"]])

    def test_turn_count_and_word_count_are_consistent_with_turns(self):
        for doc_id in ("budget-855", "president-address-242", "written-answer-na-6512"):
            doc, _, _ = mapped(doc_id)
            self.assertEqual(doc["turn_count"], len(doc["turns"]), doc_id)
            self.assertEqual(doc["word_count"], sum(t["words"] for t in doc["turns"]), doc_id)

    def test_a_record_with_no_text_still_gets_a_row(self):
        """spec 3.5: the 2.5% with no turns stay citable by title and link."""
        doc, skipped, _ = mapped("attendance-29022016")
        self.assertIsNone(skipped)
        self.assertEqual(doc["text"], "")
        self.assertEqual(doc["turns"], [])
        self.assertEqual(doc["turn_count"], 0)
        self.assertEqual(doc["word_count"], 0)
        self.assertTrue(doc["title"])

    def test_a_blank_turn_is_dropped_but_counted(self):
        stats = D.blank_stats()
        rep = FX.report("budget-855")
        rep["turns"] = ([dict(t) for t in rep["turns"]]
                        + [{"speaker": "Mr X", "text": "   ", "lang": "English",
                            "is_procedural": False, "time": None, "words": 0}])
        doc, _ = D.map_report(rep, FX.context_for("2018-03-01"), D.Roster(FX.ROSTER),
                              stats=stats)
        self.assertEqual(doc["turn_count"], len(FX.report("budget-855")["turns"]))
        self.assertEqual(stats["turns_dropped_blank"], 1)


class TestMetadata(unittest.TestCase):
    def test_the_question_is_extracted_from_an_asked_the_opening(self):
        doc, _, _ = mapped("oral-answer-495")
        self.assertIsNotNone(doc["metadata"]["question"])
        self.assertTrue(doc["metadata"]["question"].lstrip().lower().startswith("asked"))

    def test_a_tab_after_asked_is_matched(self):
        """Thousands of records serve `asked\\tthe Minister ...`; a space-anchored pattern
        misses 43% of the questions that are actually there."""
        doc, _, _ = mapped("oral-answer-495")
        self.assertIn("\t", doc["metadata"]["question"])

    def test_a_question_is_not_extracted_from_a_non_answer_section(self):
        doc, _, _ = mapped("president-address-242")
        self.assertIsNone(doc["metadata"]["question"])

    def test_a_nbsp_before_asked_is_tolerated(self):
        rep = FX.report("oral-answer-495")
        rep["turns"][0]["text"] = "\ufeff\xa0" + rep["turns"][0]["text"]
        doc, _ = D.map_report(rep, FX.context_for("2016-01-27"), D.Roster(FX.ROSTER))
        self.assertIsNotNone(doc["metadata"]["question"])

    def test_the_division_is_extracted_with_per_member_always_null(self):
        """spec 1.4: the source publishes no per-member votes, and populating the field is a
        schema error. The one declaration inside the debates partition is budget-855's."""
        doc, _, _ = mapped("budget-855")
        div = doc["metadata"]["division"]
        self.assertIsNotNone(div)
        self.assertEqual(div["ayes"], 89)
        self.assertEqual(div["noes"], 8)
        self.assertEqual(div["abstentions"], 0)
        self.assertIsNone(div["per_member"])
        self.assertIn("89", div["raw"])
        self.assertIn("Ayes", div["raw"])

    def test_the_real_2016_declarations_parse_to_their_measured_totals(self):
        """spec 2's measured yield, spot-checked against the two records it names:
        bill-213 declares 72/9 and bill-367 declares 74/9 then 72/9 at two readings."""
        self.assertEqual(
            [(d["ayes"], d["noes"], d["abstentions"]) for d in D.extract_division(
                ['There are 72 "Ayes", 9 "Noes", and no "Abstentions".'])],
            [(72, 9, 0)])
        two = D.extract_division(['There are 74 "Ayes", 9 "Noes", and one "Abstention".',
                                 'There are 72 "Ayes", 9 "Noes", and three "Abstentions".'])
        self.assertEqual([(d["ayes"], d["noes"], d["abstentions"]) for d in two],
                         [(74, 9, 1), (72, 9, 3)])

    def test_a_record_with_no_declaration_has_a_null_division(self):
        doc, _, _ = mapped("president-address-242")
        self.assertIsNone(doc["metadata"]["division"])

    def test_a_populated_per_member_is_rejected_by_the_schema(self):
        doc, _, _ = mapped("budget-855")
        doc["metadata"]["division"]["per_member"] = {"sg-mp:k-shanmugam": "aye"}
        errs = S.validate_document(doc)
        self.assertTrue(any("per_member" in e for e in errs),
                        f"schema accepted a per-member vote: {errs}")

    def test_the_declaration_is_deduplicated_on_the_totals(self):
        """spec 2: a two-reading record declares twice; dedupe on (ayes, noes, abstentions)."""
        rep = FX.report("budget-855")
        rep["turns"] = [dict(t) for t in rep["turns"]]
        rep["turns"].append({"speaker": "Mr Speaker",
                             "text": 'There are 89 "Ayes", 8 "Noes", and no "Abstentions".',
                             "lang": "English", "is_procedural": False, "time": None,
                             "words": 8})
        doc, _ = D.map_report(rep, FX.context_for("2018-03-01"), D.Roster(FX.ROSTER))
        self.assertEqual(doc["metadata"]["division"]["ayes"], 89)
        self.assertEqual(len(D.extract_division([t["text"] for t in rep["turns"]])), 1)

    def test_word_form_totals_are_parsed(self):
        """The corpus states some results in words: 'six \"Noes\"', 'no \"Abstentions\"'."""
        found = D.extract_division(['There are 77 "Ayes", six "Noes", and zero "Abstentions".'])
        self.assertEqual([(d["ayes"], d["noes"], d["abstentions"]) for d in found],
                         [(77, 6, 0)])

    def test_coverage_ratio_is_propagated_never_discarded(self):
        """spec: below 0.95 the listing API dropped rows and the set is known-incomplete."""
        doc, _, _ = mapped("oral-answer-495")
        self.assertEqual(doc["metadata"]["coverage_ratio"], 1.0)
        self.assertAlmostEqual(doc["metadata"]["speaker_attribution"], 0.967)

    def test_source_specific_fields_live_in_metadata_only(self):
        doc, _, _ = mapped("written-answer-na-6512")
        for field in ("parliament_no", "sitting_no", "volume_no", "report_version",
                      "report_type", "mp_names_raw"):
            self.assertIn(field, doc["metadata"])
            self.assertNotIn(field, doc)   # top-level would break additionalProperties:false

    def test_the_reader_url_is_built_from_the_doc_id(self):
        doc, _, _ = mapped("budget-855")
        self.assertEqual(doc["url"],
                         "https://sprs.parl.gov.sg/search/#/sprs3topic?reportid=budget-855")


class TestSchemaConformance(unittest.TestCase):
    """An ingester whose rows fail its own schema is the failure this task is graded on."""

    def test_every_fixture_record_maps_to_a_schema_valid_document(self):
        failures = []
        for rep in FX.REPORTS:
            ctx = FX.context_for(S.iso_date(rep["sitting_date"]))
            doc, skipped = D.map_report(rep, ctx, D.Roster(FX.ROSTER))
            if doc is None:
                self.assertIn(skipped, ("legislation_partition", "bad_date"))
                continue
            errs = S.validate_document(doc, FX.load_schema())
            if errs:
                failures.append(f"{doc['doc_id']}: {errs}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_every_required_top_level_field_is_present(self):
        for rep in FX.REPORTS:
            ctx = FX.context_for(S.iso_date(rep["sitting_date"]))
            doc, skipped = D.map_report(rep, ctx, D.Roster(FX.ROSTER))
            if doc is None:
                continue
            missing = S.validate_document_required_keys(doc)
            self.assertEqual(missing, [], f'{doc["doc_id"]} missing {missing}')

    def test_the_schema_rejects_an_invented_top_level_field(self):
        doc, _, _ = mapped("budget-855")
        errs = S.validate_document(dict(doc, invented_field=1))
        self.assertTrue(any("invented_field" in e for e in errs), errs)

    def test_the_schema_rejects_an_empty_title(self):
        doc, _, _ = mapped("budget-855")
        errs = S.validate_document(dict(doc, title=""))
        self.assertTrue(any("title" in e for e in errs), errs)

    def test_the_schema_rejects_an_invented_doc_type(self):
        doc, _, _ = mapped("budget-855")
        errs = S.validate_document(dict(doc, doc_type="speech"))
        self.assertTrue(any("doc_type" in e for e in errs), errs)

    def test_the_doc_id_pattern_accepts_every_real_fixture_id(self):
        import re
        pat = FX.load_schema()["$defs"]["document"]["properties"]["doc_id"]["pattern"]
        rx = re.compile(pat)
        for rep in FX.REPORTS:
            self.assertTrue(rx.match(rep["report_id"]),
                            f'{rep["report_id"]!r} rejected by {pat!r}')

    def test_the_doc_id_pattern_rejects_the_malformed_set(self):
        import re
        pat = FX.load_schema()["$defs"]["document"]["properties"]["doc_id"]["pattern"]
        rx = re.compile(pat)
        accepted = [b for b in FX.MALFORMED_DOC_IDS if rx.match(b)]
        self.assertEqual(accepted, [], f"schema accepted malformed ids {accepted}")

    def test_the_pattern_gap_is_recorded_not_assumed_closed(self):
        """A synthetic hash has a real id's shape, so no grammar can reject it. The thing
        that prevents one is that map_report takes the source's id verbatim -- asserted by
        test_the_doc_id_is_the_sources_id_verbatim."""
        import re
        pat = FX.load_schema()["$defs"]["document"]["properties"]["doc_id"]["pattern"]
        rx = re.compile(pat)
        for shape in FX.UNREJECTABLE_DOC_ID_SHAPES:
            self.assertTrue(rx.match(shape),
                            f"{shape!r} is now rejected; re-measure and update the list")

    def test_the_doc_id_is_the_sources_id_verbatim(self):
        """spec 1.4: never mint a synthetic id -- a citation must be checkable against the
        source's own namespace."""
        for doc_id in ("budget-855", "president-address-242", "written-answer-na-6512"):
            doc, _, _ = mapped(doc_id)
            self.assertEqual(doc["doc_id"], doc_id)

    def test_a_trailing_hash_on_the_source_id_is_stripped(self):
        rep = FX.report("budget-855")
        rep["report_id"] = "budget-855#"
        doc, _ = D.map_report(rep, FX.context_for("2018-03-01"), D.Roster(FX.ROSTER))
        self.assertEqual(doc["doc_id"], "budget-855")

    def test_a_record_with_no_id_is_an_error(self):
        rep = FX.report("budget-855")
        rep["report_id"] = ""
        doc, skipped = D.map_report(rep, FX.context_for("2018-03-01"), D.Roster(FX.ROSTER))
        self.assertIsNone(doc)
        self.assertEqual(skipped[0], "no_doc_id")

    def test_an_untitled_record_still_ships_but_is_counted(self):
        """spec 3.5's posture: a record stays citable rather than disappearing. Measured:
        0 of 20,884 records are untitled, so a substitution must be visible if it happens."""
        stats = D.blank_stats()
        rep = FX.report("budget-855")
        rep["title"] = "   "
        doc, _ = D.map_report(rep, FX.context_for("2018-03-01"), D.Roster(FX.ROSTER),
                              stats=stats)
        self.assertEqual(doc["title"], "budget-855")
        self.assertEqual(stats["title_fallback"], 1)
        self.assertEqual(S.validate_document(doc), [])


class TestRosterRecords(unittest.TestCase):
    """The referential-integrity surface for spec P4."""

    def test_member_ids_are_deterministic_slugs_of_the_roster_spelling(self):
        roster = D.Roster(FX.ROSTER)
        self.assertEqual(roster.resolve("Murali Pillai"), ("MEMBER", "sg-mp:murali-pillai"))
        self.assertEqual(D.member_slug("Grace Fu Hai Yien"), "sg-mp:grace-fu-hai-yien")
        # the slug rule collapses non-alphanumerics to single hyphens and strips the ends
        self.assertEqual(D.member_slug("R. Ravindran"), "sg-mp:r-ravindran")
        self.assertEqual(D.member_slug("Mohd Fahmi Aliman"), "sg-mp:mohd-fahmi-aliman")
        # the roster spelling is authoritative, and the id is derivable from it alone
        for m in roster.members_jsonl():
            self.assertEqual(m["member_id"], D.member_slug(m["full_name"]))

    def test_status_comes_from_which_roster_list_the_name_is_on(self):
        roster = D.Roster(FX.ROSTER)
        self.assertEqual(roster.status["sg-mp:lee-bee-wah"], "former")
        self.assertEqual(roster.status["sg-mp:joan-pereira"], "current")

    def test_the_normalized_collision_is_recorded_not_fatal(self):
        """spec 3.3 step 4: one collision in the whole roster, and it is the same person."""
        roster = D.Roster(FX.ROSTER)
        self.assertIn("r ravindran", roster.collisions)
        self.assertEqual(sorted(roster.collisions["r ravindran"]),
                         ["R Ravindran", "R. Ravindran"])

    def test_every_written_member_id_exists_in_the_roster(self):
        """spec P4, checked on the fixture: a member_id that is not in the roster is a bug."""
        roster = D.Roster(FX.ROSTER)
        written = set()
        for rep in FX.REPORTS:
            ctx = FX.context_for(S.iso_date(rep["sitting_date"]))
            doc, skipped = D.map_report(rep, ctx, D.Roster(FX.ROSTER))
            if doc is None:
                continue
            for mid in [doc["member_id"]] + [t["member_id"] for t in doc["turns"]]:
                if mid:
                    written.add(mid)
        self.assertTrue(written)
        unknown = written - set(roster.member_id.values())
        self.assertEqual(unknown, set(), f"member_ids not in the roster: {unknown}")

    def test_members_jsonl_is_a_valid_member_record_per_member(self):
        doc, _, roster = mapped("written-answer-na-6512")
        members = roster.members_jsonl()
        self.assertTrue(members)
        import jsonschema
        schema = FX.load_schema()
        validator = jsonschema.Draft202012Validator(
            {"$ref": "#/$defs/member", "$defs": schema["$defs"]})
        for m in members:
            errs = list(validator.iter_errors(m))
            self.assertEqual(errs, [], f'{m["member_id"]}: {[e.message for e in errs]}')

    def test_the_member_record_carries_the_observed_aliases(self):
        doc, _, roster = mapped("written-answer-na-6512")
        members = {m["member_id"]: m for m in roster.members_jsonl()}
        self.assertIn("Ms Indranee Rajah (for the Prime Minister)",
                      members["sg-mp:indranee-rajah"]["aliases"])


class TestOutputWriting(unittest.TestCase):
    """One JSON object per line, written atomically."""

    def test_jsonl_is_one_object_per_line(self):
        import tempfile
        docs = []
        for doc_id in ("budget-855", "president-address-242"):
            d, _, _ = mapped(doc_id)
            docs.append(d)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.jsonl")
            D.write_jsonl(path, docs)
            with open(path, encoding="utf-8") as fh:
                lines = [l for l in fh.read().split("\n") if l]
            self.assertEqual(len(lines), 2)
            for line, doc in zip(lines, docs):
                self.assertEqual(json.loads(line)["doc_id"], doc["doc_id"])

    def test_the_write_is_atomic(self):
        """A half-written output is worse than no output: a consumer cannot tell."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub", "out.jsonl")   # parent dir must be created
            D.write_jsonl(path, [{"a": 1}])
            self.assertTrue(os.path.exists(path))
            self.assertFalse(os.path.exists(path + ".tmp"))


class TestRunReport(unittest.TestCase):
    def test_the_run_report_counts_every_category(self):
        """Counts that are asserted, not narrated -- this is what makes coverage visible."""
        stats = D.blank_stats()
        roster = D.Roster(FX.ROSTER)
        rows = []
        for rep in FX.REPORTS:
            ctx = FX.context_for(S.iso_date(rep["sitting_date"]))
            rows.extend(D.map_sitting([rep], ctx, roster, stats))
        self.assertEqual(stats["turns_dropped_blank"], 0)
        self.assertEqual(stats["title_fallback"], 0)
        self.assertEqual(len(rows), len(FX.REPORTS) - 1)   # bill-194 is the other partition
        self.assertEqual(len(stats["division_rows"]), 1)
        self.assertEqual(stats["division_rows"][0]["doc_id"], "budget-855")
        self.assertEqual(stats["questions"], 1)
        self.assertEqual(stats["skipped"]["legislation_partition"], 1)
        self.assertEqual(stats["reports_read"], len(FX.REPORTS))
        self.assertTrue(stats["by_section"])
        self.assertTrue(stats["turns_by_kind"]["MEMBER"])

    def test_a_refused_record_is_counted_not_silently_dropped(self):
        """The half that matters: an ingester that drops records quietly reports success."""
        stats = D.blank_stats()
        roster = D.Roster(FX.ROSTER)
        bill = FX.report(FX.BRANCHES["bill"])
        rows = list(D.map_sitting([bill], FX.context_for("2016-02-29"), roster, stats))
        self.assertEqual(rows, [])
        self.assertEqual(stats["reports_read"], 1)
        self.assertEqual(stats["skipped"]["legislation_partition"], 1)

    def test_a_mapping_error_is_counted_with_an_example_not_swallowed(self):
        stats = D.blank_stats()
        rep = FX.report("budget-855")
        rep["sitting_date"] = "garbage"
        rows = list(D.map_sitting([rep], {"date": None, "coverage": {}},
                                  D.Roster(FX.ROSTER), stats))
        self.assertEqual(rows, [])
        self.assertEqual(stats["errors"]["bad_date"], 1)
        self.assertEqual(stats["error_examples"][0]["why"], "bad_date")


class TestFixtureIntegrity(unittest.TestCase):
    """The fixture is real, and these assertions hold even with no `data/` present."""

    def test_the_fixture_covers_every_mapper_branch(self):
        """Every branch must be present, or the test covering it is testing nothing.

        This asserts the branches by ID, not by prose: an earlier version of the fixture was
        truncated to "the first six turns" and silently lost budget-855's division
        declaration, at which point its division test was asserting a record that no longer
        had one.
        """
        ids = {r["report_id"] for r in FX.REPORTS}
        expected = {"no_text", "division", "bill", "oral_question",
                    "written_parenthetical", "officer", "unattributed", "variant_fallback"}
        self.assertEqual(set(FX.BRANCHES), expected,
                         "a mapper branch has no fixture record")
        for branch, doc_id in FX.BRANCHES.items():
            self.assertIn(doc_id, ids, f"the {branch} branch points at a missing record")
        self.assertEqual(len(FX.PROVENANCE), len(FX.REPORTS))
        self.assertEqual(set(FX.PROVENANCE), set(FX.BRANCHES))

    def test_every_fixture_branch_survives_the_truncation(self):
        """What each branch record still has to carry after the fixture was trimmed."""
        # the division declaration
        div = D.extract_division([t["text"] for t in FX.report("budget-855")["turns"]])
        self.assertEqual([(d["ayes"], d["noes"], d["abstentions"]) for d in div], [(89, 8, 0)])
        # a clock stamp
        self.assertTrue(any(t["time"] for t in FX.report("budget-855")["turns"]))
        # the "asked the Minister" opening
        self.assertTrue(QUESTION_OPEN_MARK.match(FX.report("oral-answer-495")["turns"][0]["text"]))
        # a procedural turn and an unattributed turn
        self.assertTrue(any(t["is_procedural"] for t in FX.report("president-address-242")["turns"]))
        self.assertTrue(any(t["speaker"] is None for t in FX.report("president-address-242")["turns"]))
        # the roster's own variant spelling, on the record the variant branch names
        variant_id = FX.BRANCHES["variant_fallback"]
        self.assertTrue(any((t["speaker"] or "").startswith("Mr Mohd Fahmi Bin Aliman")
                            for t in FX.report(variant_id)["turns"]),
                        f"{variant_id} lost its variant speaker")

    def test_the_fixture_contains_the_name_variant_speaker(self):
        roster = D.Roster(FX.ROSTER)
        variants = {t["speaker"] for r in FX.REPORTS for t in r["turns"]
                    if (t["speaker"] or "").startswith("Mr Mohd Fahmi Bin Aliman")}
        self.assertTrue(variants, "the fixture has no name-variant speaker string")
        for raw in variants:
            self.assertEqual(roster.resolve(raw), ("MEMBER", "sg-mp:mohd-fahmi-aliman"))

    def test_every_fixture_report_has_a_context(self):
        for rep in FX.REPORTS:
            ctx = FX.context_for(S.iso_date(rep["sitting_date"]))
            self.assertEqual(ctx["date"], S.iso_date(rep["sitting_date"]))

    def test_a_missing_context_raises_rather_than_guessing(self):
        with self.assertRaises(KeyError):
            FX.context_for("1999-01-01")

    def test_the_fixture_reports_are_copies(self):
        a = FX.report("budget-855")
        a["title"] = "mutated"
        self.assertNotEqual(FX.report("budget-855")["title"], "mutated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
