"""Integrity of the UAT golden question set (`eval/uat_golden/`).

**What this asserts, and what it deliberately does not.** It asserts the
set's *integrity*: that it loads, that ids are unique, that every journey
tag names a journey that really exists in the UAT script, that both
dialects are represented, and that a case expecting a refusal always
names the reason it expects. It asserts nothing about accuracy -- no
mocked test can, because accuracy needs a live database and Ollama, and
claiming otherwise would be the "documented capability that does not
exist" CLAUDE.md 1 warns about.

The journey list is **parsed out of
`docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md` itself** rather than
copied here. A copy would drift the first time a journey is added, and
the whole value of tagging cases by journey is that the two agree.

Added 2026-10-28, UAT-readiness Phase 5.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from eval.dataset_loader import load_benchmark
from eval.schema import KNOWN_CATEGORIES

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_DIR = _REPO_ROOT / "eval" / "uat_golden"
_UAT_SCRIPT = _REPO_ROOT / "docs" / "certification" / "UAT_SCRIPTS_AND_SIGNOFF.md"

#: The minimum the Phase 5 brief asks for. A floor, not a target -- the
#: assertion exists so that deleting cases is a visible decision.
_MINIMUM_QUESTIONS = 50

#: Journeys that legitimately contain no natural-language question, each
#: with the reason. Asserted as an exact complement of what the set
#: covers, so a journey that *gains* a question-shaped step cannot be
#: silently left uncovered.
_JOURNEYS_WITHOUT_A_QUESTION = {
    "J1": "authentication and access control -- sign-in flows, no question asked",
    "J2": "database onboarding -- a connection wizard",
    "J3": "semantic review and metric approval -- a governed-lifecycle UI workflow",
    "J12": "admin, audit and observability -- dashboards, not questions",
    "J13": "error handling and provider failure -- an operator coordinates an outage",
    "J14": "responsive layout and accessibility -- rendering and keyboard navigation",
    "J16": "news, README and documentation accuracy -- has no in-product screen at all",
}


@pytest.fixture(scope="module")
def dataset():
    return load_benchmark(_GOLDEN_DIR)


@pytest.fixture(scope="module")
def journeys_in_the_uat_script() -> set[str]:
    """Every journey heading in the UAT script, e.g. `{"J1", ... "J18"}`."""
    text = _UAT_SCRIPT.read_text(encoding="utf-8")
    found = set(re.findall(r"^### (J\d+) —", text, flags=re.MULTILINE))
    # Sanity on the parse itself: if the headings' format changes this
    # fixture would silently return an empty set and every assertion
    # built on it would pass vacuously.
    assert (
        len(found) >= 15
    ), f"parsed only {len(found)} journey heading(s) -- has the format changed?"
    return found


class TestTheSetLoadsAndIsWellFormed:
    def test_it_loads_through_the_existing_loader(self, dataset):
        """Reuse, not a second format: `eval.dataset_loader` is unmodified
        apart from carrying the two new optional metadata fields."""
        assert dataset.standalone_cases
        assert dataset.followup_cases

    def test_it_holds_at_least_the_minimum_number_of_questions(self, dataset):
        turns = sum(len(case.turns) for case in dataset.followup_cases)
        total = len(dataset.standalone_cases) + turns
        assert total >= _MINIMUM_QUESTIONS, f"{total} question(s)"

    def test_it_is_separate_from_the_main_benchmark_directory(self):
        """`load_benchmark` merges every `*.yaml` in the directory it is
        given, so a golden-set file placed in `eval/benchmark/` would
        silently change every number `docs/EVALUATION_CURRENT.md`
        reports. This pins the separation rather than trusting it."""
        benchmark_dir = _REPO_ROOT / "eval" / "benchmark"
        golden_names = {path.name for path in _GOLDEN_DIR.glob("*.yaml")}
        benchmark_names = {path.name for path in benchmark_dir.glob("*.yaml")}
        assert golden_names
        assert not (golden_names & benchmark_names)
        main = load_benchmark(benchmark_dir)
        golden_ids = {case.id for case in load_benchmark(_GOLDEN_DIR).standalone_cases}
        assert not (golden_ids & {case.id for case in main.standalone_cases})

    def test_every_case_id_is_prefixed_so_a_report_is_readable(self, dataset):
        for case in dataset.standalone_cases:
            assert case.id.startswith("uat_"), case.id
        for case in dataset.followup_cases:
            assert case.id.startswith("uat_"), case.id

    def test_every_category_is_one_the_report_can_group_by(self, dataset):
        """The loader only *warns* on an unknown category, which would
        leave a case ungrouped in a report rather than failing. For this
        set that is worth asserting."""
        for case in dataset.standalone_cases:
            assert case.category in KNOWN_CATEGORIES, f"{case.id}: {case.category}"


class TestJourneyCoverage:
    def test_every_journey_tag_names_a_real_journey(self, dataset, journeys_in_the_uat_script):
        tagged = {case.journey for case in dataset.standalone_cases}
        tagged |= {case.journey for case in dataset.followup_cases}
        assert "" not in tagged, "a case carries no journey tag"
        unknown = tagged - journeys_in_the_uat_script
        assert not unknown, f"tagged journeys that do not exist in the UAT script: {unknown}"

    def test_every_question_shaped_journey_is_covered(self, dataset, journeys_in_the_uat_script):
        """The complement is asserted, not the set.

        Listing the covered journeys would pass forever; asserting that
        the *uncovered* ones are exactly the documented
        no-question journeys means a new journey with a question-shaped
        step fails this test until it is either covered or explicitly
        excused.
        """
        tagged = {case.journey for case in dataset.standalone_cases}
        tagged |= {case.journey for case in dataset.followup_cases}
        uncovered = journeys_in_the_uat_script - tagged
        assert uncovered == set(_JOURNEYS_WITHOUT_A_QUESTION), (
            f"uncovered journeys {sorted(uncovered)} does not match the documented "
            f"exclusions {sorted(_JOURNEYS_WITHOUT_A_QUESTION)}"
        )

    def test_the_ask_a_question_journey_has_real_depth(self, dataset):
        """J5 is the journey UAT spends most of its time in, so a single
        token case there would make the set look broader than it is."""
        j5 = [case for case in dataset.standalone_cases if case.journey == "J5"]
        assert len(j5) >= 8, f"only {len(j5)} J5 case(s)"


class TestBothDialectsAreRepresented:
    def test_each_case_names_its_dialect(self, dataset):
        for case in dataset.standalone_cases:
            assert case.dialect in {"mssql", "postgresql"}, f"{case.id}: {case.dialect!r}"

    def test_both_supported_primary_dialects_appear(self, dataset):
        dialects = {case.dialect for case in dataset.standalone_cases}
        assert dialects == {"mssql", "postgresql"}

    def test_the_postgres_cases_are_marked_unverified(self, dataset):
        """Every connection on the machine that authored this set was
        `mssql`, so no Postgres case has ever been executed. That has to
        be visible in the data, not only in a report -- a case silently
        assumed to pass is exactly what Shared Rule 4 forbids."""
        postgres = [c for c in dataset.standalone_cases if c.dialect == "postgresql"]
        assert postgres
        for case in postgres:
            assert "UNVERIFIED" in case.notes, case.id


class TestTheBehaviourCasesAssertSomethingPrecise:
    def test_a_rejection_case_always_names_the_reason_it_expects(self, dataset):
        """`expected_behavior: reject_*` without a reason would pass on
        the wrong refusal -- e.g. an injection case satisfied by an
        off-topic verdict, which would hide a real detection gap."""
        for case in dataset.standalone_cases:
            if case.expected_behavior.startswith("reject_"):
                assert case.expect_rejection_reason, case.id

    def test_the_off_topic_control_case_exists(self, dataset):
        """The false-refusal measurement is meaningless without a
        genuinely off-topic case to compare against: a refusal there is
        correct and a refusal on the `uat_j15_*` phrasings is not."""
        off_topic = [
            case for case in dataset.standalone_cases if case.expect_rejection_reason == "off_topic"
        ]
        assert off_topic, "no off-topic control case"

    def test_the_known_false_refusal_phrasings_are_all_present(self, dataset):
        """CLAUDE.md 13 records two phrasings that fail and one that
        works. All three belong in the set, so the next model change
        re-measures them instead of rediscovering them."""
        questions = {case.question.casefold() for case in dataset.standalone_cases}
        for phrasing in ("worst selling products", "most popular products", "top selling products"):
            assert phrasing in questions, phrasing

    def test_every_adversarial_case_is_classified_as_one(self, dataset):
        """A security case left at the default `benign` classification
        would be averaged into the accuracy metrics instead of the
        security ones."""
        for case in dataset.standalone_cases:
            if case.difficulty == "adversarial":
                assert case.security_classification == "adversarial", case.id

    def test_no_case_expecting_a_refusal_also_demands_rows(self, dataset):
        """A contradictory case would fail whatever the product did,
        which reads as a defect in the product rather than in the
        dataset."""
        for case in dataset.standalone_cases:
            if case.expected_behavior != "succeed":
                assert case.min_rows is None, case.id


class TestTheSetIsHonestAboutWhatItCannotCheckYet:
    def test_every_accuracy_case_without_gold_sql_says_so(self, dataset):
        """`eval.evaluators.fetch_gold_result` derives the gold result set
        by executing `expected_sql`. A case expecting success with no
        gold SQL is a *structure* check only, and its notes must say so --
        otherwise a green run would be read as an accuracy result.

        Three markers are accepted, because there are three honest
        reasons: `GOLD SQL REQUIRED` (a human must author it on the UAT
        environment), `NOT EXECUTABLE` (the SQL runner cannot drive this
        journey at all) and `DISCLOSURE ONLY` (a gold *result set* could
        not express what the case checks -- that a cap was disclosed, or
        that a value was suppressed without a row disappearing).
        """
        markers = ("GOLD SQL REQUIRED", "NOT EXECUTABLE", "DISCLOSURE ONLY")
        for case in dataset.standalone_cases:
            if case.expected_behavior != "succeed" or case.expected_sql:
                continue
            assert any(marker in case.notes for marker in markers), case.id

    def test_the_cases_the_sql_runner_cannot_execute_are_marked(self, dataset):
        """J8 and J10 run through the orchestrator's RAG subgraph, and
        `eval.runner` drives `run_agent` -- the SQL graph. CLAUDE.md 13
        records that the eval harness covers the SQL path only, so these
        cases must not look runnable."""
        for case in dataset.standalone_cases:
            if case.journey in {"J8", "J10"}:
                assert "NOT EXECUTABLE" in case.notes, case.id
