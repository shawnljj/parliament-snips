"""Fixture: real Hansard records, copied out verbatim by
`docs/probes/ingest_fixture_build.py` and re-checked by
`docs/probes/ingest_fixture_check.py`.

WHY A SAVED FIXTURE. The unit test has to exercise the record-mapping function, and the
mapping function is exactly the thing that must not depend on `data/`, the network or the
roster endpoint to be testable. So this holds real records, chosen one per branch
`map_report` has. The only edits are which reports are picked, a long turn's text truncated
at a sentence boundary, and the record's `words` recomputed for the truncated text.

Nothing here is typed by hand. An earlier hand-written version of this file was checked
against the corpus and 23 of its fields disagreed with the records they claimed to be --
invented speakers, wrong dates, wrong titles -- which is why the builder and the checker
exist and why `docs/probes/ingest_fixture_check.py` is a gate rather than a nicety.

8 records, one per mapper branch (see PROVENANCE)

The `ROSTER` slice is not hand-picked either: the builder resolves every speaker string in
the fixture through the real roster and keeps exactly the names those resolutions land on.

`REPORTS` was copied out of the corpus on 2026-09-22; the records are stable (report ids are
unique across 20,884 and never reused), and ingest_fixture_check.py re-asserts them.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ROSTER = {
    "mpVOList": [
        {"id": 0, "fullName": 'Alex Yam'},
        {"id": 0, "fullName": 'Christopher de Souza'},
        {"id": 0, "fullName": 'Denise Phua Lay Peng'},
        {"id": 0, "fullName": 'Desmond Lee'},
        {"id": 0, "fullName": 'Indranee Rajah'},
        {"id": 0, "fullName": 'Jessica Tan Soon Neo'},
        {"id": 0, "fullName": 'Joan Pereira'},
        {"id": 0, "fullName": 'K Shanmugam'},
        {"id": 0, "fullName": 'Kwek Hian Chuan Henry'},
        {"id": 0, "fullName": 'Murali Pillai'},
        {"id": 0, "fullName": 'Pritam Singh'},
        {"id": 0, "fullName": 'Rachel Ong'},
        {"id": 0, "fullName": 'Rahayu Mahzam'},
        {"id": 0, "fullName": 'Saktiandi Supaat'},
        {"id": 0, "fullName": 'Seah Kian Peng'},
        {"id": 0, "fullName": 'Sun Xueling'},
        {"id": 0, "fullName": 'Sylvia Lim'},
        {"id": 0, "fullName": 'Tin Pei Ling'},
        {"id": 0, "fullName": 'Vikram Nair'},
        {"id": 0, "fullName": 'Wan Rizal'},
        {"id": 0, "fullName": 'Yeo Wan Ling'},
        {"id": 0, "fullName": 'Zaqy Mohamad'},
        {"id": 0, "fullName": 'Zhulkarnain Abdul Rahim'},
    ],
    "formerMPVOList": [
        {"id": 0, "fullName": 'Chong Kee Hiong'},
        {"id": 0, "fullName": 'Daniel Goh Pei Siong'},
        {"id": 0, "fullName": 'Don Wee'},
        {"id": 0, "fullName": 'Heng Chee How'},
        {"id": 0, "fullName": 'Heng Swee Keat'},
        {"id": 0, "fullName": 'Kuik Shiao-Yin'},
        {"id": 0, "fullName": 'Lee Bee Wah'},
        {"id": 0, "fullName": 'Leon Perera'},
        {"id": 0, "fullName": 'Lily Neo'},
        {"id": 0, "fullName": 'Lim Biow Chuan'},
        {"id": 0, "fullName": 'Louis Ng Kok Kwang'},
        {"id": 0, "fullName": 'Mohd Fahmi Aliman'},
        {"id": 0, "fullName": 'Ng Eng Hen'},
        {"id": 0, "fullName": 'R Ravindran'},
        {"id": 0, "fullName": 'R. Ravindran'},
        {"id": 0, "fullName": 'Tan Chuan-Jin'},
        {"id": 0, "fullName": 'Tony Tan Keng Yam'},
    ],
    "ministryVOList": [{"id": 0, "name": "Ministry of Law"}],
}

# The sitting contexts the fixture's records came from: each sitting file's
# own `coverage` block, verbatim. metadata.coverage_ratio and
# metadata.speaker_attribution are propagated from here.
CONTEXTS = {
    "2016-01-15": {"date": "2016-01-15", "coverage": {"ratio": 1.0, "speaker_attribution": 0.939}},
    "2016-01-27": {"date": "2016-01-27", "coverage": {"ratio": 1.0, "speaker_attribution": 0.967}},
    "2016-02-29": {"date": "2016-02-29", "coverage": {"ratio": 1.024, "speaker_attribution": 0.974}},
    "2018-03-01": {"date": "2018-03-01", "coverage": {"ratio": 1.0, "speaker_attribution": 0.983}},
    "2020-10-06": {"date": "2020-10-06", "coverage": {"ratio": 1.024, "speaker_attribution": 0.983}},
    "2022-03-03": {"date": "2022-03-03", "coverage": {"ratio": 1.074, "speaker_attribution": 0.986}},
}

# One record per branch the mapper has, each copied verbatim out of the
# archive (long turns truncated at a sentence boundary, `words` recomputed).
# BRANCHES maps each mapper branch to the fixture record that exercises it, so
# a missing branch is a test failure rather than a test that passes vacuously.
BRANCHES = {
    'bill': 'bill-194',
    'division': 'budget-855',
    'no_text': 'attendance-29022016',
    'officer': 'admin-oaths-240',
    'oral_question': 'oral-answer-495',
    'unattributed': 'president-address-242',
    'variant_fallback': 'budget-1851',
    'written_parenthetical': 'written-answer-na-6512',
}

PROVENANCE = {
    'bill': "bill-194 is section=bill, so map_report must REFUSE it (it is ingest/legislation.py's record)",
    'division': "budget-855: the Chair's declaration",
    'no_text': 'attendance-29022016 has 0 turns',
    'officer': "admin-oaths-240 principal 'Mdm Speaker'",
    'oral_question': 'oral-answer-495 opens "asked the Minister"',
    'unattributed': 'president-address-242: unattributed + procedural',
    'variant_fallback': "budget-1851 speaker 'Mr Mohd Fahmi Bin Aliman (Marine Parade)' resolves only through the name-variant fallback (roster spelling: Mohd Fahmi Aliman)",
    'written_parenthetical': "written-answer-na-6512 speaker 'Ms Indranee Rajah (for the Prime Minister)'",
}

REPORTS = [
    {'report_id': 'bill-194',
     'report_type': 'Second Reading Bills',
     'group': 'bill',
     'title': "Women's Charter (Amendment) Bill",
     'sitting_date': '29-2-2016',
     'parliament_no': '13',
     'sitting_no': '7',
     'volume_no': '94',
     'report_version': 'sprs3',
     'mp_names': 'Er Dr Lee Bee Wah (Nee Soon),Mr Alex Yam,Mr Seah Kian Peng (Marine Parade),Ms '
                 'Sun Xueling,Mr Alex Yam (Marsiling-Yew Tee),Mr Tan Chuan-Jin,Ms Sun Xueling '
                 '(Pasir Ris-Punggol),Ms Rahayu Mahzam (Jurong),Dr Lily Neo (Jalan Besar),Assoc '
                 'Prof Daniel Goh Pei Siong (Non-Constituency Member),Ms Tin Pei Ling '
                 '(MacPherson),Mdm Speaker,Ms Jessica Tan Soon Neo (East Coast),Dr Lily Neo,Mr '
                 'Louis Ng Kok Kwang,Assoc Prof Daniel Goh Pei Siong,Mr Louis Ng Kok Kwang (Nee '
                 'Soon)',
     'words': 995,
     'turns': [{'speaker': None,
                'text': '[(proc text) Order for Second Reading read. (proc text)]',
                'lang': 'English',
                'is_procedural': True,
                'time': None,
                'words': 9},
               {'speaker': 'The Minister for Social and Family Development (Mr Tan Chuan-Jin)',
                'text': 'Mdm Speaker, I beg to move, "That the Bill be now read a Second time." '
                        'With your permission, Mdm Speaker, may I ask the Clerks to distribute the '
                        'documents that will illustrate the points covered in my speech?',
                'lang': 'English',
                'is_procedural': False,
                'time': '1.33 pm',
                'words': 38},
               {'speaker': 'Mdm Speaker',
                'text': 'Yes, please. [Handouts were distributed to hon Members.]',
                'lang': 'English',
                'is_procedural': False,
                'time': '1.33 pm',
                'words': 8},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': "The Women's Charter was passed in 1961. It is a historical and "
                        'significant Act. It instituted the rights of women, and protected '
                        'vulnerable women and girls exposed to vice.',
                'lang': 'English',
                'is_procedural': False,
                'time': '1.33 pm',
                'words': 29},
               {'speaker': 'Mdm Speaker',
                'text': 'Er Dr Lee Bee Wah.',
                'lang': 'English',
                'is_procedural': False,
                'time': '1.33 pm',
                'words': 5},
               {'speaker': 'Er Dr Lee Bee Wah (Nee Soon)',
                'text': 'Mdm Speaker, gender equality has long been a heavily debated issue across '
                        'national borders, cultures and generations. Regardless of how far we have '
                        'come, how much further we should go, one thing is for sure – as a nation, '
                        'we pledged to build an equal and democratic society.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.02 pm',
                'words': 48},
               {'speaker': 'Mdm Speaker',
                'text': 'Ms Sun Xueling.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.02 pm',
                'words': 3},
               {'speaker': 'Ms Sun Xueling (Pasir Ris-Punggol)',
                'text': 'Mdm Speaker, I thank the Minister for his exposition on the amendments to '
                        'the Bill.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.14 pm',
                'words': 15},
               {'speaker': 'Mdm Speaker',
                'text': 'Dr Lily Neo.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.14 pm',
                'words': 3},
               {'speaker': 'Dr Lily Neo (Jalan Besar)',
                'text': "Mdm Speaker, I stand to support the Women's Charter (Amendment) Bill. "
                        'However, I would like to comment and seek clarification on some issues.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.17 pm',
                'words': 23},
               {'speaker': 'Mdm Speaker',
                'text': 'Assoc Prof Daniel Goh.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.17 pm',
                'words': 4},
               {'speaker': 'Assoc Prof Daniel Goh Pei Siong (Non-Constituency Member)',
                'text': 'Mdm Speaker, this Bill takes steps to adjust the 55-year old legislation '
                        'to the changing social environment affecting family life.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.30 pm',
                'words': 20},
               {'speaker': 'Mdm Speaker',
                'text': 'Ms Tin Pei Ling.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.30 pm',
                'words': 4},
               {'speaker': 'Ms Tin Pei Ling (MacPherson)',
                'text': "Mdm Speaker, the Women's Charter is an important piece of legislation in "
                        'Singapore. It was passed in 1961 to protect and advance the rights of '
                        'women and girls in Singapore.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.44 pm',
                'words': 30},
               {'speaker': 'Mdm Speaker',
                'text': 'Mr Alex Yam.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.44 pm',
                'words': 3},
               {'speaker': 'Mr Alex Yam (Marsiling-Yew Tee)',
                'text': '"This Bill is making a very great change in the personal lives of the '
                        'greater majority of our citizens. It marks a complete break from the past '
                        'and it is a big step forward.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.57 pm',
                'words': 34},
               {'speaker': 'Mdm Speaker',
                'text': 'Ms Rahayu Mahzam.',
                'lang': 'English',
                'is_procedural': False,
                'time': '2.57 pm',
                'words': 3},
               {'speaker': 'Ms Rahayu Mahzam (Jurong)',
                'text': 'Mdm Speaker, at the outset, I would like to declare that I am a family '
                        'lawyer. In my practice, I have assisted many clients in their divorce '
                        'proceedings.',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.12 pm',
                'words': 28},
               {'speaker': 'Mdm Speaker',
                'text': 'Ms Jessica Tan.',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.12 pm',
                'words': 3},
               {'speaker': 'Ms Jessica Tan Soon Neo (East Coast)',
                'text': "Madam, thank you for allowing me to speak on this Bill. The Women's "
                        'Charter was enacted in 1961 with the intent to protect and advance the '
                        'rights of women and girls in Singapore.',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.21 pm',
                'words': 33},
               {'speaker': 'Mdm Speaker',
                'text': 'Mr Seah Kian Peng. Page: 63',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.21 pm',
                'words': 6},
               {'speaker': 'Mr Seah Kian Peng (Marine Parade)',
                'text': 'Mdm Speaker, I am in support of the proposed amendments to the Bill. The '
                        'last time this Bill was debated in January 2011, I also spoke it. Then, '
                        'amongst other things, I was asking for men to be given reasonable access '
                        'to their children by their ex-wives.',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.32 pm',
                'words': 47},
               {'speaker': 'Mdm Speaker',
                'text': 'Mr Louis Ng.',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.32 pm',
                'words': 3},
               {'speaker': 'Mr Louis Ng Kok Kwang (Nee Soon)',
                'text': "Madam, I would like to speak on four issues on this Women's Charter "
                        'Amendment Bill and how we can make improvements. Firstly, on Parenting '
                        'Programme on Divorce – domestic violence situations.',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.38 pm',
                'words': 31},
               {'speaker': 'Mdm Speaker',
                'text': 'Minister Tan Chuan-Jin.',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.38 pm',
                'words': 3},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': 'Mdm Speaker, I thank Members for the robust and heartfelt debate and '
                        'especially for the support of the Bill and the direction in which we are '
                        'going. Like many Bills and policies, all of us would like more to be '
                        'done.',
                'lang': 'English',
                'is_procedural': False,
                'time': '3.46 pm',
                'words': 41},
               {'speaker': 'Mdm Speaker',
                'text': 'Mr Alex Yam, please keep your clarification short.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 8},
               {'speaker': 'Mr Alex Yam',
                'text': 'Thank you, Madam. Just three quick clarifications for the Minister. I '
                        "note the Minister's point on the mandatory parenting component for minor "
                        'couples. Will the Minister consider extending both programmes for all '
                        'couples before marriage and not just minor couples?',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 40},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': 'Mdm Speaker, can I seek a clarification on the second question?',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 11},
               {'speaker': 'Mr Alex Yam',
                'text': 'The second clarification is on whether a Tribunal for Maintenance will be '
                        'considered by the Ministry in the near future.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 20},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': 'Mdm Speaker, with regard to whether we should extend MPP to all couples '
                        'and not just minor couples, what we are looking at now is just minor '
                        'couples.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 28},
               {'speaker': 'Mdm Speaker',
                'text': 'Dr Lily Neo.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 3},
               {'speaker': 'Dr Lily Neo',
                'text': 'May I ask the Minister whether he would consider a register to keep track '
                        'of children from disadvantaged families, from divorces, so that we can '
                        "keep them on MSF's radar to give them long-term assistance, hopefully, to "
                        'adulthood, so that they do not get into problems due to',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 48},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': 'Mdm Speaker, I fully understand the intent that Dr Lily Neo has raised in '
                        'terms of maintaining a register for all children who are caught up in '
                        'divorce.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 28},
               {'speaker': 'Mdm Speaker',
                'text': 'Mr Louis Ng.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 3},
               {'speaker': 'Mr Louis Ng Kok Kwang',
                'text': 'Can I ask the Minister for a clarification with regard to section 160? Is '
                        'it solely the Director of Social Welfare who makes a decision to detain a '
                        'girl or is it up to a team of people?',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 38},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': 'Mdm Speaker, the Director of Social Welfare would be advised by the staff '
                        'and that is the approach that we are taking for this.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 24},
               {'speaker': 'Mdm Speaker',
                'text': 'Assoc Prof Daniel Goh.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 4},
               {'speaker': 'Assoc Prof Daniel Goh Pei Siong',
                'text': 'May I ask the Minister what are the calibration considerations when it '
                        'comes to not extending the PPO application powers to family members '
                        'beyond the victims, immediate relatives and, perhaps, the Police?',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 32},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': 'Mdm Speaker, the consideration we have is this: if the family members are '
                        'perpetrators of violence, it would obviously not be appropriate for them '
                        'to be involved in the application process.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 31},
               {'speaker': 'Mdm Speaker',
                'text': 'Dr Lily Neo.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 3},
               {'speaker': 'Dr Lily Neo',
                'text': 'I would just like to qualify that, earlier on, what I said was a register '
                        'more for vulnerable children, and I did not really say for all children '
                        'of divorces.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 30},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': 'Mdm Speaker, as I had mentioned, there are many different vulnerable '
                        "children. So, I hear the Member's point that it is not just all children "
                        'who are affected by divorce. We do not have plans at this point in time '
                        'to put all these children on a register.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 48},
               {'speaker': 'Mdm Speaker',
                'text': 'Ms Sun Xueling.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 3},
               {'speaker': 'Ms Sun Xueling',
                'text': 'I have a question: would the MRO have any enforcement ability? I read '
                        "that the role of the MRO is to obtain information on parties' financial "
                        'circumstances to assist the Court in its fact-finding process and also '
                        'have a standing in Court to tender the information obtained.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 47},
               {'speaker': 'Mr Tan Chuan-Jin',
                'text': 'Mdm Speaker, the MRO is to facilitate the legal process of the Courts and '
                        'to bring to bear. There is a range of options available. Jail will be one '
                        'option.',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 30},
               {'speaker': 'Mdm Speaker',
                'text': 'Order. I propose to take the break now. I suspend the Sitting and will '
                        'take the Chair again at 4.35 pm. \xa0Sitting accordingly suspended \xa0at '
                        '4.15 pm until 4.35 pm. Sitting resumed at 4.35 pm [Mdm Speaker in the '
                        'Chair] Page: 75',
                'lang': 'English',
                'is_procedural': False,
                'time': '4.01 pm',
                'words': 42}]},
    {'report_id': 'budget-855',
     'report_type': 'Budget',
     'group': 'budget',
     'title': 'Debate on Annual Budget Statement',
     'sitting_date': '1-3-2018',
     'parliament_no': '13',
     'sitting_no': '63',
     'volume_no': '94',
     'report_version': 'sprs3',
     'mp_names': 'Ms Sylvia Lim (Aljunied),Ms Sylvia Lim,Mr Leon Perera (Non-Constituency '
                 'Member),Mr Murali Pillai (Bukit Batok),Mr Saktiandi Supaat,Mr Pritam Singh '
                 '(Aljunied),Ms Kuik Shiao-Yin (Nominated Member),The Minister for Finance (Mr '
                 'Heng Swee Keat),Mr Saktiandi Supaat (Bishan-Toa Payoh),The Minister for Home '
                 'Affairs and Minister for Law (Mr K Shanmugam),Mr K Shanmugam,Mr Heng Swee Keat',
     'words': 1289,
     'turns': [{'speaker': None,
                'text': '[(proc text) Order read for Resumption of Debate on Question [19 February '
                        '2018], (proc text)] [(proc text) "That Parliament approves the financial '
                        'policy of the Government for the financial year 1 April 2018 to 31 March '
                        '2019." – [Minister for Finance.] (proc text)] [(proc text)',
                'lang': 'English',
                'is_procedural': True,
                'time': None,
                'words': 45},
               {'speaker': 'Mr Murali Pillai (Bukit Batok)',
                'text': 'Mr Speaker, Sir, being the penultimate speaker in this marathon Budget '
                        'Debate stretching to a third day, there is always this pressure not to '
                        'cover the same ground as the speakers who have gone before me. I had to '
                        'revise my speech several times.',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.17 am',
                'words': 44},
               {'speaker': 'Mr Speaker',
                'text': 'And finally, we have Mr Saktiandi.',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.17 am',
                'words': 6},
               {'speaker': 'Mr Saktiandi Supaat (Bishan-Toa Payoh)',
                'text': 'Mr Speaker, Sir, like what Mr Murali Pillai has mentioned, I think it is '
                        'a great honour to be the last speaker. But I think Mr Murali has done '
                        'great justice in terms of trying to wrap up some of the points made by '
                        'other Members.',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.17 am',
                'words': 46},
               {'speaker': 'Mr Speaker',
                'text': 'Brief is always good.',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.17 am',
                'words': 4},
               {'speaker': 'Mr Saktiandi Supaat',
                'text': 'But I find that the Budget takes a very detailed approach to the '
                        'challenges that Singapore will face in the next five years and the '
                        'Minister for Finance has scrutinised the various revenue platforms and '
                        'carefully considered how best we should manage this against our '
                        'expenditure,',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.17 am',
                'words': 46},
               {'speaker': 'Mr Speaker',
                'text': 'After 54 speakers and a short detour on how speakers are rostered, '
                        'Minister for Finance.',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.17 am',
                'words': 15},
               {'speaker': 'The Minister for Finance (Mr Heng Swee Keat)',
                'text': 'Mr Speaker, Sir, first, let me thank Members of the House for the '
                        'thoughtful and wide-ranging debate over the past two and a half days.',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.46 am',
                'words': 25},
               {'speaker': 'Mr Speaker',
                'text': 'Thank you.',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.46 am',
                'words': 2},
               {'speaker': 'Mr Heng Swee Keat',
                'text': 'I am glad that the Budget has sparked many thoughtful conversations among '
                        'citizens and fellow Singaporeans. Many Singaporeans have shared their '
                        'perspectives with me in the course of various consultations and '
                        'engagements, as well as with the other Ministers.',
                'lang': 'English',
                'is_procedural': False,
                'time': '10.46 am',
                'words': 39},
               {'speaker': 'Mr Speaker',
                'text': 'The Question is, "That Parliament approves the financial policy of the '
                        'Government for the financial year 1 April 2018—". Yes, Ms Sylvia Lim.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 23},
               {'speaker': 'Ms Sylvia Lim (Aljunied)',
                'text': 'Yes, Sir, I have clarifications for the Finance Minister. I have four '
                        'clarifications for the Finance Minister. But before that, I would like to '
                        'thank him for touching briefly on longitudinal studies. I will be taking '
                        'this matter up further with MOE at the COS.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 45},
               {'speaker': 'Mr Speaker',
                'text': 'Minister.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 1},
               {'speaker': 'Mr Heng Swee Keat',
                'text': 'I thank the Member, Ms Sylvia Lim, for her question. First, on the first '
                        "point about my reference to Mr Low Thia Khiang's point.",
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 24},
               {'speaker': 'Some hon Members',
                'text': 'Education Minister.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 2},
               {'speaker': 'Mr Heng Swee Keat',
                'text': 'Sorry? Education Minister, yes. Did I say "Finance"? Oh okay. I am not '
                        'hinting at any changes! I got distracted! [Laughter.] Well, they continue '
                        'to build on this good work.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 30},
               {'speaker': 'Mr Speaker',
                'text': 'Mr Pritam Singh.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 3},
               {'speaker': 'Mr Pritam Singh (Aljunied)',
                'text': 'I would like to thank the Finance Minister for his round-up speech and '
                        'the additional figures that the Finance Minister gave. Sir, I have some '
                        'questions for the Finance Minister.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 30},
               {'speaker': 'Mr Heng Swee Keat',
                'text': "I thank Mr Pritam Singh for his questions. The Member's first question, "
                        'about the fact that our NIRC is, in fact, becoming an increasing source '
                        'of revenue for the Government: that is correct.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 33},
               {'speaker': 'Mr Speaker',
                'text': 'Minister Shanmugam.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.14 pm',
                'words': 2},
               {'speaker': 'The Minister for Home Affairs and Minister for Law (Mr K Shanmugam)',
                'text': 'Thank you, Mr Speaker, Sir.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 5},
               {'speaker': 'Mr Speaker',
                'text': 'Ms Sylvia Lim.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 3},
               {'speaker': 'Ms Sylvia Lim',
                'text': 'Mr Speaker, I would like to thank the Law Minister for his questions.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 13},
               {'speaker': 'Mr K Shanmugam',
                'text': 'Seeing what the facts are, as I have set them out, would Ms Lim agree '
                        'that the suggestions are baseless, and are you prepared to withdraw that '
                        'this Government behaved dishonestly?',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 31},
               {'speaker': 'Ms Sylvia Lim',
                'text': 'Sir, I never said that the Government behaved dishonestly. I said that '
                        'the Government is stuck with the announcement that they have enough money '
                        'for the decade.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 27},
               {'speaker': 'Mr K Shanmugam',
                'text': 'The implication, based on what you have said, is that a trial balloon was '
                        'floated with the obvious intention that a tax increase was going to be '
                        'announced now, but because of the public reaction being so severe, the '
                        'Government has backtracked and has changed its mind, and has',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 49},
               {'speaker': 'Ms Sylvia Lim',
                'text': 'Sir, I clearly said that it was my suspicion. I clearly said that. You '
                        'can check the Hansard. And it is my honest suspicion.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 24},
               {'speaker': 'Mr Speaker',
                'text': 'If Members can wait until I call them.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 8},
               {'speaker': 'Ms Sylvia Lim',
                'text': 'So, am I not entitled to have a view?',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 9},
               {'speaker': 'Mr Speaker',
                'text': 'Minister Shanmugam.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 2},
               {'speaker': 'Mr K Shanmugam',
                'text': 'We now have confirmation that there was a suspicion.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 9},
               {'speaker': 'Mr Speaker',
                'text': 'Ms Sylvia Lim. Unfortunately, we do not have translators for Latin.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 11},
               {'speaker': 'Ms Sylvia Lim',
                'text': 'Mr Speaker, Sir, as far as I know, there is such a thing as parliamentary '
                        "privilege. And if I recall earlier debates, even People's Action Party "
                        '(PAP) MPs were encouraged to come to the House to convey even rumours, so '
                        'that the Government has the opportunity to refute them.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 49},
               {'speaker': 'Mr Speaker',
                'text': 'Within limits. Minister for Finance.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 5},
               {'speaker': 'Mr Heng Swee Keat',
                'text': 'Mr Speaker, thank you. I want to thank my colleague, Minister Shanmugam, '
                        "for reminding me that I have not answered this part of Ms Lim's question. "
                        'Ms Lim says that she is saying this on suspicion. I believe Ms Lim is a '
                        'lawyer and also a Police Officer before.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 49},
               {'speaker': 'Mr Speaker',
                'text': 'Mr Sylvia Lim.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 3},
               {'speaker': 'Ms Sylvia Lim',
                'text': "Sir, I have listened to the Finance Minister's response. I still feel "
                        'that there is nothing wrong with what I have said. But I have noted his '
                        'answer.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 28},
               {'speaker': 'Mr Speaker',
                'text': 'Are there any other clarifications? Ms Kuik Shiao-Yin.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 8},
               {'speaker': 'Ms Kuik Shiao-Yin (Nominated Member)',
                'text': 'I thank the Finance Minister for the extended explanation he has given on '
                        'taxes, borrowing and Reserves, I will definitely be referring to the '
                        'Hansard when I prepare for future General Paper (GP) lectures. I have a '
                        'request and a clarifying question.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 42},
               {'speaker': 'Mr Heng Swee Keat',
                'text': 'I thank Ms Kuik for her questions. On the first request: can we make the '
                        'slides and the information available to our people and our educators? The '
                        'answer is, certainly.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 30},
               {'speaker': 'Mr Speaker',
                'text': 'And on that the note, the Question is, "That Parliament approves the '
                        'financial policy of the Government for the financial year —"',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 22},
               {'speaker': 'Mr Leon Perera (Non-Constituency Member)',
                'text': 'Sir, can I seek a clarification?',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 6},
               {'speaker': 'Mr Speaker',
                'text': 'I am moving on. The Question is, "That Parliament approves the financial '
                        'policy of the Government for the financial year 1 April 2018 to 31 March '
                        '2019." As many as are of the opinion say "Aye".',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 36},
               {'speaker': 'Hon Members',
                'text': 'Aye.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 1},
               {'speaker': 'Mr Speaker',
                'text': 'To the contrary say "No". Yes, Minister for Finance.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 9},
               {'speaker': 'Mr Heng Swee Keat',
                'text': 'Mr Speaker, Sir, this is an important Motion on a very important issue. '
                        "And after the debate that we have in Parliament, the Workers' Party has "
                        "not made clear its stance on the Government's financial policy.",
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 36},
               {'speaker': 'Mr Speaker',
                'text': 'Ms Sylvia Lim, would you like to respond?',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 8},
               {'speaker': 'Ms Sylvia Lim',
                'text': 'Yes, Sir, I thought I made it clear earlier that we support this Budget, '
                        'as far as the measures in the Budget go. But the GST announcement was an '
                        'announcement which is not being implemented in this Budget.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 38},
               {'speaker': 'Mr Speaker',
                'text': 'Minister for Finance, would you like to proceed with a Division?',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 11},
               {'speaker': 'Mr Heng Swee Keat',
                'text': 'Mr Speaker, Sir, this debate is a debate on the financial policy of the '
                        'Government, and I have articulated in this Budget the financial policy of '
                        'the Government. Therefore, the financial policy includes the policy to '
                        'raise GST in the coming years, between 2021 and 2025.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 46},
               {'speaker': 'Mr Speaker',
                'text': 'Let me state my opinion on the collected voices. As many as are of that '
                        'opinion say "Aye".',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 18},
               {'speaker': 'Hon Members',
                'text': 'Aye.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 1},
               {'speaker': 'Mr Speaker',
                'text': 'To the contrary say "No".',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 5},
               {'speaker': 'Some hon Members',
                'text': 'No.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 1},
               {'speaker': 'Ms Sylvia Lim',
                'text': 'Speaker, to make clear, we are unable to support the announcement on the '
                        'GST hike.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 15},
               {'speaker': 'Mr Speaker',
                'text': 'For this particular Motion, we are voting on the Budget Statement. We '
                        'either vote "Yes", "No" or "Abstain". So, we will proceed with the '
                        'Division. Will hon Members who support the Division, please rise in their '
                        'places? [(proc text) More than five\xa0hon Members\xa0rose.\xa0\xa0 (proc '
                        'text)]',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 47},
               {'speaker': 'Mr Speaker',
                'text': 'Thank you. Clerk, ring the division bells. [(proc text) After two minutes '
                        '–\xa0\xa0 (proc text)]',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 15},
               {'speaker': 'Mr Speaker',
                'text': 'Serjeant-at-Arms, lock the doors. [(proc text) Question put, "That '
                        'Parliament approves the financial policy of the Government for the '
                        'financial year 1 April 2018 to 31 March 2019."\xa0\xa0(proc text)]',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 30},
               {'speaker': 'Mr Speaker',
                'text': 'Minister for Finance, you have called for a Division, would you like to '
                        'proceed with a Division?',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 17},
               {'speaker': 'Mr Heng Swee Keat',
                'text': 'Yes, Sir.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 2},
               {'speaker': 'Mr Speaker',
                'text': 'May I remind Members to please sit at your designated seats. You should '
                        'only start to vote when the voting buttons on your armrests start to '
                        'blink. Members may now begin to vote.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 33},
               {'speaker': 'Mr Speaker',
                'text': 'I will proceed to declare the voting results now. There are 89 "Ayes", '
                        'eight "Noes", and zero "Abstentions". The "Ayes" have it.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.43 pm',
                'words': 22}]},
    {'report_id': 'attendance-29022016',
     'report_type': 'attendance',
     'group': 'other',
     'title': 'Attendance',
     'sitting_date': '29-2-2016',
     'parliament_no': None,
     'sitting_no': None,
     'volume_no': None,
     'report_version': 'sprs3',
     'mp_names': None,
     'words': 0,
     'turns': []},
    {'report_id': 'admin-oaths-240',
     'report_type': 'Admin Oaths',
     'group': 'other',
     'title': 'Administration of Oaths',
     'sitting_date': '15-1-2016',
     'parliament_no': '13',
     'sitting_no': '1',
     'volume_no': '94',
     'report_version': 'sprs3',
     'mp_names': 'Mdm Speaker',
     'words': 28,
     'turns': [{'speaker': 'Mdm Speaker',
                'text': 'Hon Members will now proceed to take their Oath or Affirmation of '
                        'Allegiance at the Table, starting with the Prime Minister and thereafter '
                        'in the order as arranged.',
                'lang': 'English',
                'is_procedural': False,
                'time': None,
                'words': 28}]},
    {'report_id': 'oral-answer-495',
     'report_type': 'Oral Answers to Questions',
     'group': 'oral',
     'title': 'Ensuring Security in Light of Recent Terrorist Attacks',
     'sitting_date': '27-1-2016',
     'parliament_no': '13',
     'sitting_no': '4',
     'volume_no': '94',
     'report_version': 'sprs3',
     'mp_names': 'The Senior Minister of State for Home Affairs (Mr Desmond Lee),Ms Sylvia Lim '
                 '(Aljunied),Mr Christopher de Souza,Ms Tin Pei Ling (MacPherson),Mdm Speaker,Mr '
                 'Christopher de Souza (Holland-Bukit Timah),Mr Desmond Lee,Ms Joan Pereira,Mr '
                 'Cedric Foo Chee Keng (Pioneer)',
     'words': 153,
     'turns': [{'speaker': 'Mr Christopher de Souza',
                'text': 'asked\tthe Minister for Home Affairs in light of the 14 January 2016 '
                        "bombing in Jakarta, what is being done to (i) step up Singapore's border "
                        'security and internal security and (ii) increase the vigilance of '
                        'Singaporeans, the resident population and the Home Team.',
                'lang': 'English',
                'is_procedural': False,
                'time': None,
                'words': 44},
               {'speaker': 'Ms Joan Pereira',
                'text': 'asked\tthe Minister for Home Affairs in view of the number of terrorist '
                        "incidents globally and Singapore's status as a travel hub, what new "
                        'measures are put in place to ensure our domestic security while '
                        'maintaining our lead as a travel destination and attraction.',
                'lang': 'English',
                'is_procedural': False,
                'time': None,
                'words': 44},
               {'speaker': 'The Senior Minister of State for Home Affairs (Mr Desmond Lee) (for '
                           'the Minister for Home Affairs)',
                'text': 'Mdm Speaker, there is a question set down by Ms Tin Pei Ling tomorrow, '
                        'but it is on a related topic. So, if Mdm Speaker would permit, I would '
                        'like to answer Question Nos 1 and 2 together.',
                'lang': 'English',
                'is_procedural': False,
                'time': None,
                'words': 38},
               {'speaker': 'Mdm Speaker',
                'text': 'I will leave the second part to Ms Tin. But I agree to the merger of '
                        'Question Nos 1 and 2, for them to be taken together.',
                'lang': 'English',
                'is_procedural': False,
                'time': None,
                'words': 27}]},
    {'report_id': 'president-address-242',
     'report_type': "President's Address",
     'group': 'budget',
     'title': "President's Address",
     'sitting_date': '15-1-2016',
     'parliament_no': '13',
     'sitting_no': '1',
     'volume_no': '94',
     'report_version': 'sprs3',
     'mp_names': None,
     'words': 75,
     'turns': [{'speaker': None,
                'text': '[(proc text) The President (accompanied by two ADCs) entered the Chamber, '
                        'accompanied by Mdm Speaker, who was preceded by the Serjeant-at-Arms '
                        '(without the Mace), the Principal Assistant Clerk, the Deputy Clerk and '
                        'the Clerk.',
                'lang': 'English',
                'is_procedural': True,
                'time': None,
                'words': 34},
               {'speaker': 'The President (Dr Tony Tan Keng Yam)',
                'text': "SG50 was a defining year for Singaporeans. Mr Lee Kuan Yew's passing, the "
                        'National Day Parade and other special events helped us appreciate how far '
                        'we have come since Independence and how precious and unique is the home '
                        'we have built.',
                'lang': 'English',
                'is_procedural': False,
                'time': None,
                'words': 41}]},
    {'report_id': 'budget-1851',
     'report_type': 'Budget',
     'group': 'budget',
     'title': 'Committee of Supply – Head J (Ministry of Defence)',
     'sitting_date': '3-3-2022',
     'parliament_no': '14',
     'sitting_no': '53',
     'volume_no': '95',
     'report_version': 'sprs3',
     'mp_names': 'Mr Vikram Nair,Ms Denise Phua Lay Peng (Jalan Besar),Ms Yeo Wan Ling (Pasir '
                 'Ris-Punggol),Mr Don Wee (Chua Chu Kang),Mr Seah Kian Peng (Marine Parade),Dr Wan '
                 'Rizal (Jalan Besar),Mr Vikram Nair (Sembawang),Mr Lim Biow Chuan '
                 '(Mountbatten),The Chairman,The Senior Minister of State for Defence (Mr Zaqy '
                 'Mohamad),Miss Rachel Ong (West Coast),Mr Kwek Hian Chuan Henry (Kebun Baru),Mr '
                 'Mohd Fahmi Aliman (Marine Parade),Mr Zhulkarnain Abdul Rahim (Chua Chu Kang),Mr '
                 'Chong Kee Hiong (Bishan-Toa Payoh),The Minister for Defence (Dr Ng Eng Hen),The '
                 'Senior Minister of State for Defence (Mr Heng Chee How)',
     'words': 833,
     'turns': [{'speaker': None,
                'text': '[(proc text) Head J (cont) – (proc text)] [(proc text) Resumption of '
                        'Debate on Question [2 March 2022], (proc text)] [(proc text) "That the '
                        'total sum to be allocated for Head J of the Estimates be reduced by '
                        '$100." – [Mr Vikram Nair]. (proc text)]',
                'lang': 'English',
                'is_procedural': True,
                'time': None,
                'words': 45},
               {'speaker': 'Mr Mohd Fahmi Bin Aliman (Marine Parade)',
                'text': 'Chairman, at the COS debate last year, the Minister for Defence mentioned '
                        'that the Singapore Armed Forces (SAF) would use, update and refresh the '
                        'medical classification system to better match vocations and deployment of '
                        'National Servicemen.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Medical Classification System',
                'words': 36},
               {'speaker': 'Mr Lim Biow Chuan (Mountbatten)',
                'text': 'Sir, during my National Service, I was trained as an Infantry Officer. I '
                        'was subsequently posted to a maintenance battalion, to be converted to an '
                        'ordnance officer.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Functional Assessment of Deployment',
                'words': 27},
               {'speaker': 'Mr Chong Kee Hiong (Bishan-Toa Payoh)',
                'text': 'Chairman, the pandemic has disrupted our economy and our way of life and '
                        'in particular, greatly affected the education, training and learning '
                        'opportunities for our youths. Over the last two years, many of these '
                        'programmes and lessons had to be shifted online.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Work-Learn Scheme (WLS)',
                'words': 42},
               {'speaker': 'Mr Seah Kian Peng (Marine Parade)',
                'text': 'Mr Chairman, there are many of us in this House who have once upon a time '
                        "donned uniforms and held guns. Two years' fulltime, during my time, it "
                        'was two and a half years, and many years after that, serving our country '
                        'in the Armed Forces.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Deployment of Experts',
                'words': 46},
               {'speaker': 'Mr Chairman',
                'text': 'Mr Gan Thiam Poh. Not here. Mr Chong Kee Hiong.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Deployment of Experts',
                'words': 10},
               {'speaker': 'Mr Chong Kee Hiong',
                'text': 'Chairman, digital technology has evolved exponentially in the last '
                        'decade. It has changed the way people and organisations interact and '
                        'exchange information and transact. The pandemic has accelerated this '
                        'trend as people and organisations seek ways to minimise physical contact.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Leverage Technology for NS Services',
                'words': 40},
               {'speaker': 'Miss Rachel Ong (West Coast)',
                'text': 'Chairman, National Service is an irreplaceable component for our '
                        "nation's\xa0defence. All men in Singapore commit two years of their youth "
                        'to\xa0safeguard our country as full-time NSmen or NSFs.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'NS Recognition',
                'words': 30},
               {'speaker': 'Mr Kwek Hian Chuan Henry (Kebun Baru)',
                'text': 'Mr Chairman, for the past 55 years, NSmen have been a pillar of '
                        "Singapore's defence\xa0– in fact, a key pillar. It is important for us "
                        "not to take our NSmen's contributions to our nation's security for "
                        'granted.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Review of NS System',
                'words': 38},
               {'speaker': 'Mr Lim Biow Chuan',
                'text': 'Sir, I am a firm believer that our soldiers must train to fight under '
                        'realistic conditions. Only when our soldiers are competent, can they '
                        'fight effectively and achieve their mission to be a strong deterrent to '
                        'unfriendly forces.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Training Safety in NS',
                'words': 38},
               {'speaker': 'Dr Wan Rizal (Jalan Besar)',
                'text': 'Chairman, safety is not something that should gain salience only when '
                        'incidents occur. We must always keep safety at the forefront of our '
                        'minds.\xa0The SAF must ensure that our servicemen and women return home '
                        'to their families safe and sound.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'External Review Panel on SAF Safety',
                'words': 41},
               {'speaker': 'Mr Don Wee (Chua Chu Kang)',
                'text': "Sir, the colours of my outfit today emphasise the importance of SAF's "
                        'realistic training and its safety record.\xa0For families who have sent '
                        'their children to serve National Service, they must be assured of safe '
                        'training.',
                'lang': 'English',
                'is_procedural': False,
                'time': "Inspector-General's Audit Findings",
                'words': 36},
               {'speaker': 'Dr Wan Rizal',
                'text': 'Sir, in this digital age, we are witnessing a proliferation of technology '
                        'driven solutions across various industries. Of course, the defence '
                        'industry or ecosystem is no exception.\xa0Militaries around the world '
                        'have been leveraging on technology to transform themselves.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Technology and Training Safety',
                'words': 39},
               {'speaker': 'The Chairman',
                'text': 'Miss Cheng Li Hui. Not here. Ms Denise Phua.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Technology and Training Safety',
                'words': 9},
               {'speaker': 'Ms Denise Phua Lay Peng (Jalan Besar)',
                'text': 'The concept of Total Defence has taken on a different complexion since it '
                        'was first introduced in 1984 in Singapore. Back then, Total Defence was a '
                        'national defence concept that rallies all Singaporeans behind the '
                        'Singapore Armed Forces should there be a military threat.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Total Defence and Future Challenges',
                'words': 44},
               {'speaker': 'The Chairman',
                'text': 'Miss Rachel Ong. Please take your two cuts together.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Total Defence and Future Challenges',
                'words': 9},
               {'speaker': 'Miss Rachel Ong',
                'text': 'Thank you. Chairman, one key demographic for outreach efforts on\xa0'
                        'defence and security issues is our younger generation of Singaporeans '
                        'here.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Engaging Youth',
                'words': 21},
               {'speaker': 'Mr Chong Kee Hiong',
                'text': 'Chairman, the Singapore Discovery Centre (SDC) is one of the venues built '
                        'by the Government to present National Education in an interesting and '
                        "engaging way.\xa0Here, visitors learn and experience Singapore's history "
                        'and visualise its future in a fun and immersive way.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'Singapore Discovery Centre',
                'words': 42},
               {'speaker': 'The Chairman',
                'text': 'Ms Carrie Tan. Not here. Ms Yeo Wan Ling.',
                'lang': 'English',
                'is_procedural': False,
                'time': '11.30 am',
                'words': 9},
               {'speaker': 'Ms Yeo Wan Ling (Pasir Ris-Punggol)',
                'text': 'At the heart of national defence is the amicable community relations '
                        'Singaporean families have with our military.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'ACCORD Initiatives',
                'words': 17},
               {'speaker': 'Mr Zhulkarnain Abdul Rahim (Chua Chu Kang)',
                'text': 'Chairman, I am a Member of ACCORD. It is an honour to serve alongside '
                        'other Members to promote the support for NS amongst families and '
                        'employers.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'ACCORD and Support for NS',
                'words': 26},
               {'speaker': 'The Chairman',
                'text': 'Mr Heng Chee How.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'ACCORD and Support for NS',
                'words': 4},
               {'speaker': 'The Senior Minister of State for Defence (Mr Heng Chee How)',
                'text': 'Mr Chairman, Defence Minister Dr Ng Eng Hen spoke yesterday about the '
                        'challenging landscape in which the SAF would have to operate.',
                'lang': 'English',
                'is_procedural': False,
                'time': 'ACCORD and Support for NS',
                'words': 22},
               {'speaker': 'The Chairman',
                'text': 'Senior Minister of State Zaqy Mohamad.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.00 pm',
                'words': 6},
               {'speaker': 'The Senior Minister of State for Defence (Mr Zaqy Mohamad)',
                'text': 'Mr Chairman, Minister for Defence Dr Ng spoke about the global '
                        'geopolitical shifts, transnational threats and the attendant impact on '
                        'Singapore.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.00 pm',
                'words': 21},
               {'speaker': 'The Chairman',
                'text': 'Time for clarifications.\xa0Mr Vikram Nair.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.15 pm',
                'words': 6},
               {'speaker': 'Mr Vikram Nair (Sembawang)',
                'text': 'Just two clarifications.\xa0One is in relation to the 4G Army and the '
                        'development, I think the changes are quite interesting to hear. I just '
                        'want to check what steps will be taken to train reservists and upskill '
                        'them. I am one myself.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.15 pm',
                'words': 43},
               {'speaker': 'The Minister for Defence (Dr Ng Eng Hen)',
                'text': 'I thank Member Vikram Nair for the questions.\xa0Most militaries will '
                        'face that problem as they modernise\xa0– how do they keep their '
                        'Servicemen current.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.15 pm',
                'words': 25},
               {'speaker': 'The Chairman',
                'text': 'Mr Vikram Nair, would you like to withdraw your amendment?',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.15 pm',
                'words': 10},
               {'speaker': 'Mr Vikram Nair',
                'text': 'I would like to thank Minister Ng Eng Hen, Senior Minister of State Heng '
                        'Chee How and Senior Minister of State\xa0Zaqy Mohamad for sharing with us '
                        'the latest developments in MINDEF and to the whole team at MINDEF and the '
                        'SAF for keeping us safe even during times of COVID-19.',
                'lang': 'English',
                'is_procedural': False,
                'time': '12.15 pm',
                'words': 51}]},
    {'report_id': 'written-answer-na-6512',
     'report_type': 'Written Answers to Questions for Oral Answer Not Answered by End of Question '
                    'Time',
     'group': 'written',
     'title': 'Proposal for Extra Childcare Leave for Parents of Differently Abled Children',
     'sitting_date': '6-10-2020',
     'parliament_no': '14',
     'sitting_no': '8',
     'volume_no': '95',
     'report_version': 'sprs3',
     'mp_names': 'Ms Indranee Rajah,Mr Louis Ng Kok Kwang',
     'words': 30,
     'turns': [{'speaker': 'Ms Indranee Rajah (for the Prime Minister)',
                'text': 'We recognise that caregiving can be challenging, especially for those who '
                        'have to juggle between work and caregiving roles, some of whom may be '
                        'parents of children with special needs.',
                'lang': 'English',
                'is_procedural': False,
                'time': None,
                'words': 30}]},
]


# Ids the schema's doc_id pattern must REJECT. See docs/probes/ingest_doc_ids.py for what
# each one tests, and for the two real ids (Miscellaneous-<n>) the pattern was revised to
# accept.
MALFORMED_DOC_IDS = ["", "abcd", "bill 367", "../bill-367", "bill-", "367",
                     "bill-367#x", "BILL_367"]

# A shape the pattern CANNOT reject, in either the original or the revised form, recorded
# because the probe found it rather than because it is harmless:
#
#   "deadbeef1234"  a synthetic hash looks exactly like "deadbeef" + "1234", which is the
#                   shape of a real id. No grammar over the id string can separate them.
#
# What actually prevents a synthetic id is not the pattern -- it is that `map_report` takes
# the source's own `report_id` verbatim and never mints one (spec 1.4: "never mint a
# synthetic id: a citation must be checkable against the source's own namespace"). This list
# exists so the gap is visible instead of being assumed closed.
UNREJECTABLE_DOC_ID_SHAPES = ["deadbeef1234"]


def report(doc_id):
    """One fixture report by id. A deep copy, so a test cannot mutate the fixture."""
    for r in REPORTS:
        if r["report_id"] == doc_id:
            return json.loads(json.dumps(r))
    raise KeyError(doc_id)


def context_for(sitting_date):
    """The `ctx` for the sitting a record came from.

    A record's date is a property of the RECORD (`sitting_date`), not of the sitting file it
    was read out of, so the lookup is by the record's own ISO date. Raises rather than
    guessing a context: a wrong `ctx` silently changes coverage_ratio on the row.
    """
    for iso, ctx in CONTEXTS.items():
        if iso == sitting_date:
            return ctx
    raise KeyError(f"no fixture context for {sitting_date!r}; have {sorted(CONTEXTS)}")


def load_schema():
    with open(os.path.join(ROOT, "docs", "schema.json"), encoding="utf-8") as fh:
        return json.load(fh)
