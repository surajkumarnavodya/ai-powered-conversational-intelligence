"""Context budgeting (Prompt 02) and decoding policy (Prompt 05).

The property that matters most is the *negative* one: when the prompt
fits, budgeting must change nothing at all. A context-budget layer that
quietly reorders or reshapes the common-case prompt would invalidate
`docs/EVALUATION_CURRENT.md`'s accuracy numbers, which were measured
against the exact prompt text and cannot be re-measured in this
environment.

The second property is that safety-critical context is never the thing
that gets dropped -- which is the failure the baseline measured, where
Ollama's own truncation removed the system prompt.
"""

from __future__ import annotations

import pytest

from agent.context_budget import (
    CHARS_PER_TOKEN,
    BudgetResult,
    Section,
    SectionPriority,
    apply_budget,
    estimate_tokens,
)
from agent.llm_client import _build_user_prompt, _decoding_options, _system_prompt
from config.settings import Settings
from embeddings.golden_examples import GoldenExample


def _ddl(tables: int, cols: int) -> str:
    return "\n\n".join(
        "CREATE TABLE dbo.T{} (\n{}\n);".format(
            i, "\n".join(f"    Col{j}_LongBusinessName NVARCHAR(255) NULL," for j in range(cols))
        )
        for i in range(tables)
    )


def _golden(n: int) -> list[GoldenExample]:
    return [GoldenExample(question=f"q{i}", sql=f"SELECT {i}", score=0.9) for i in range(n)]


def _ctx(n: int) -> list[dict]:
    return [
        {"chunk_type": "documentation", "text": "context " * 200, "table": f"T{i}"}
        for i in range(n)
    ]


class TestBudgetIsInertWhenItFits:
    """The negative property: no change in the common case."""

    @pytest.mark.parametrize(("tables", "cols"), [(4, 10), (4, 20), (4, 40), (2, 60)])
    def test_a_realistic_prompt_is_byte_identical_with_and_without_budgeting(self, tables, cols):
        kwargs = dict(
            question="total revenue by region?",
            schema_context=_ddl(tables, cols),
            previous_sql=None,
            error_feedback=None,
            golden_examples=_golden(3),
            retrieved_context=_ctx(5),
        )

        unbudgeted = _build_user_prompt(**kwargs, settings=Settings(context_budget_max_chars=0))
        budgeted = _build_user_prompt(**kwargs, settings=Settings())

        assert unbudgeted == budgeted

    def test_zero_disables_budgeting_entirely(self):
        sections = [Section("a", "x" * 10_000, SectionPriority.BUSINESS_CONTEXT)]

        result = apply_budget(sections, max_chars=0)

        assert not result.changed
        assert result.sections == sections

    def test_nothing_is_dropped_when_everything_fits(self):
        sections = [
            Section("schema", "x" * 100, SectionPriority.CRITICAL),
            Section("ctx", "y" * 100, SectionPriority.BUSINESS_CONTEXT),
        ]

        result = apply_budget(sections, max_chars=10_000)

        assert result.dropped == []
        assert result.final_chars == 200


class TestCriticalContextSurvivesBudgetPressure:
    """Prompt 02's explicit requirement: never silently remove
    safety-critical context."""

    def test_schema_survives_even_at_an_absurd_budget(self):
        result = apply_budget(
            [
                Section("schema", "S" * 50_000, SectionPriority.CRITICAL),
                Section("ctx", "C" * 5_000, SectionPriority.BUSINESS_CONTEXT),
            ],
            max_chars=100,
        )

        names = [s.name for s in result.sections]
        assert "schema" in names
        assert "ctx" not in names

    def test_governing_metrics_are_never_dropped(self):
        """A mandatory governance block. Dropping one to save space
        would let the model answer with its own definition of a metric
        the business has already defined."""
        result = apply_budget(
            [
                Section("governing_metrics", "M" * 20_000, SectionPriority.CRITICAL),
                Section("golden_examples", "G" * 5_000, SectionPriority.GOLDEN_EXAMPLES),
            ],
            max_chars=100,
        )

        assert [s.name for s in result.sections] == ["governing_metrics"]

    def test_retry_error_feedback_is_never_dropped(self):
        """A retry without its error is just the same failing call."""
        result = apply_budget(
            [
                Section("retry", "E" * 9_000, SectionPriority.CRITICAL),
                Section("business_context", "B" * 9_000, SectionPriority.BUSINESS_CONTEXT),
            ],
            max_chars=5_000,
        )

        assert [s.name for s in result.sections] == ["retry"]

    def test_an_over_budget_critical_set_is_reported_not_trimmed(self):
        """Dropping a schema to satisfy a number would produce confident
        SQL against tables the model never saw."""
        result = apply_budget(
            [Section("schema", "S" * 50_000, SectionPriority.CRITICAL)], max_chars=1_000
        )

        assert result.over_budget
        assert len(result.sections) == 1

    def test_the_live_prompt_keeps_schema_and_question_under_pressure(self):
        budgeted = _build_user_prompt(
            question="total revenue by region?",
            schema_context=_ddl(8, 120),
            previous_sql=None,
            error_feedback=None,
            golden_examples=_golden(3),
            retrieved_context=_ctx(5),
            settings=Settings(),
        )

        assert "Schema:" in budgeted
        assert "Question: total revenue by region?" in budgeted


class TestEvictionOrder:
    def test_lowest_priority_goes_first(self):
        result = apply_budget(
            [
                Section("schema", "S" * 100, SectionPriority.CRITICAL),
                Section("plan", "P" * 100, SectionPriority.PLAN),
                Section("golden", "G" * 100, SectionPriority.GOLDEN_EXAMPLES),
                Section("business", "B" * 100, SectionPriority.BUSINESS_CONTEXT),
            ],
            max_chars=250,
        )

        assert result.dropped == ["business", "golden"]
        assert [s.name for s in result.sections] == ["schema", "plan"]

    def test_within_one_priority_the_largest_goes_first(self):
        """One oversized block should go before several small ones."""
        result = apply_budget(
            [
                Section("small", "s" * 10, SectionPriority.BUSINESS_CONTEXT),
                Section("huge", "h" * 5_000, SectionPriority.BUSINESS_CONTEXT),
            ],
            max_chars=100,
        )

        assert result.dropped == ["huge"]

    def test_eviction_is_deterministic_for_identical_input(self):
        """A budget that evicted different blocks on different runs
        would make a regression impossible to reproduce."""
        sections = [
            Section("a", "x" * 500, SectionPriority.BUSINESS_CONTEXT),
            Section("b", "y" * 500, SectionPriority.BUSINESS_CONTEXT),
            Section("c", "z" * 500, SectionPriority.GOLDEN_EXAMPLES),
        ]

        runs = [apply_budget(list(sections), max_chars=600).dropped for _ in range(20)]

        assert all(run == runs[0] for run in runs)

    def test_surviving_sections_keep_their_original_order(self):
        result = apply_budget(
            [
                Section("first", "1" * 50, SectionPriority.CRITICAL),
                Section("drop_me", "d" * 5_000, SectionPriority.BUSINESS_CONTEXT),
                Section("last", "2" * 50, SectionPriority.CRITICAL),
            ],
            max_chars=200,
        )

        assert [s.name for s in result.sections] == ["first", "last"]


class TestBudgetObservability:
    def test_the_summary_reports_what_was_dropped_and_never_the_text(self):
        secret = "CREATE TABLE Salaries (employee NVARCHAR(50), amount DECIMAL);"
        result = apply_budget(
            [
                Section("schema", "S" * 100, SectionPriority.CRITICAL),
                Section("business_context", secret * 200, SectionPriority.BUSINESS_CONTEXT),
            ],
            max_chars=200,
        )

        summary = result.summary()
        assert summary["dropped_sections"] == ["business_context"]
        assert summary["dropped_chars"] > 0
        assert "Salaries" not in str(summary)

    def test_the_reserve_is_subtracted_before_measuring(self):
        """The system prompt is what Ollama's truncation eats first, so
        its space has to be taken off the top."""
        sections = [Section("ctx", "x" * 900, SectionPriority.BUSINESS_CONTEXT)]

        without = apply_budget(list(sections), max_chars=1_000)
        with_reserve = apply_budget(list(sections), max_chars=1_000, reserved_chars=500)

        assert not without.changed
        assert with_reserve.changed

    def test_token_estimate_matches_the_documented_ratio(self):
        assert estimate_tokens("x" * 400) == 400 // CHARS_PER_TOKEN

    def test_an_empty_section_list_is_handled(self):
        result = apply_budget([], max_chars=100)

        assert isinstance(result, BudgetResult)
        assert result.sections == []
        assert not result.over_budget


class TestDecodingPolicy:
    """Prompt 05. The measured problem: `num_ctx` was never set, so every
    call ran against Ollama's small default window and long prompts were
    silently truncated from the start -- taking the system prompt."""

    def test_num_ctx_is_always_sent(self):
        options = _decoding_options(Settings(), 100)

        assert options["num_ctx"] == Settings().ollama_num_ctx
        assert options["num_ctx"] > 2048, "the measured default that caused truncation"

    def test_temperature_stays_zero(self):
        """Preserved, not introduced -- every call site already used it."""
        assert _decoding_options(Settings(), 100)["temperature"] == 0.0

    def test_the_seed_is_pinned_for_reproducibility(self):
        assert _decoding_options(Settings(), 100)["seed"] == Settings().ollama_seed

    def test_num_predict_stays_per_task(self):
        """An insight is one or two sentences; SQL is not."""
        assert _decoding_options(Settings(), 64)["num_predict"] == 64
        assert _decoding_options(Settings(), 2048)["num_predict"] == 2048

    def test_operator_overrides_are_honoured(self):
        options = _decoding_options(
            Settings(ollama_num_ctx=32768, ollama_seed=7, ollama_top_p=0.9), 10
        )

        assert options["num_ctx"] == 32768
        assert options["seed"] == 7
        assert options["top_p"] == 0.9

    def test_every_option_is_json_safe_for_the_ollama_client(self):
        import json

        json.dumps(_decoding_options(Settings(), 10))


class TestBaselineAssumptionsTheseTestsRestOn:
    """If these drift, the tests above would pass while testing nothing."""

    def test_the_system_prompt_is_large_enough_to_need_a_reserve(self):
        from agent.llm_client import _SYSTEM_PROMPT_RESERVE_CHARS

        size = len(_system_prompt("mssql"))
        assert size > 5_000, "the measured baseline was 8,064 characters"
        assert size <= _SYSTEM_PROMPT_RESERVE_CHARS

    def test_a_wide_schema_really_does_exceed_the_default_budget(self):
        """Guards the eviction tests above: if a wide schema fitted the
        default budget, they would pass while exercising nothing."""
        settings = Settings()
        from agent.llm_client import _SYSTEM_PROMPT_RESERVE_CHARS

        usable = settings.context_budget_max_chars - _SYSTEM_PROMPT_RESERVE_CHARS
        assert len(_ddl(8, 120)) > usable

    def test_a_medium_schema_fits_the_default_budget(self):
        """The other half: the default must not evict in the common
        case, or every realistic question loses its business context."""
        settings = Settings()
        from agent.llm_client import _SYSTEM_PROMPT_RESERVE_CHARS

        usable = settings.context_budget_max_chars - _SYSTEM_PROMPT_RESERVE_CHARS
        assert len(_ddl(4, 40)) < usable


class TestInferenceFingerprint:
    """Prompts 08/14/18. The baseline's defect went unnoticed for a long
    time because nothing recorded which configuration produced a given
    answer -- a quality regression and a config change looked identical
    in the logs."""

    def test_identical_settings_fingerprint_identically(self):
        from agent.inference_fingerprint import compute_fingerprint

        assert compute_fingerprint(Settings()).combined == compute_fingerprint(Settings()).combined

    def test_a_decoding_change_moves_only_the_decoding_fingerprint(self):
        """Split hashes so a telemetry diff points at the subsystem --
        the difference between a five-minute and a five-hour
        investigation."""
        from agent.inference_fingerprint import compute_fingerprint

        base = compute_fingerprint(Settings())
        changed = compute_fingerprint(Settings(ollama_num_ctx=32768))

        assert changed.decoding != base.decoding
        assert changed.prompt == base.prompt
        assert changed.retrieval == base.retrieval
        assert changed.combined != base.combined

    def test_a_retrieval_change_moves_only_the_retrieval_fingerprint(self):
        from agent.inference_fingerprint import compute_fingerprint

        base = compute_fingerprint(Settings())
        changed = compute_fingerprint(Settings(context_budget_max_chars=99_000))

        assert changed.retrieval != base.retrieval
        assert changed.decoding == base.decoding

    def test_the_prompt_fingerprint_tracks_the_real_system_prompt_text(self):
        """Proves it hashes the live text rather than a constant that
        could drift away from it."""
        import agent.inference_fingerprint as fp_mod
        from agent.inference_fingerprint import compute_fingerprint

        before = compute_fingerprint(Settings()).prompt
        original = fp_mod._prompt_fingerprint
        try:
            fp_mod._prompt_fingerprint = lambda: fp_mod._digest("a different system prompt")
            after = compute_fingerprint(Settings()).prompt
        finally:
            fp_mod._prompt_fingerprint = original

        assert after != before

    def test_the_summary_carries_no_prompt_text_and_no_secret(self):
        from agent.inference_fingerprint import fingerprint_summary

        flat = str(fingerprint_summary(Settings()))

        assert "You are" not in flat, "system prompt text leaked into telemetry"
        assert "password" not in flat.lower()
        assert "SecretStr" not in flat

    def test_a_settings_object_missing_a_newer_field_still_fingerprints(self):
        """A fingerprint helper that can raise gets wrapped in
        try/except at every call site and then quietly stops working."""
        from agent.inference_fingerprint import compute_fingerprint

        class Partial:
            ollama_model = "llama3.1:8b"

        result = compute_fingerprint(Partial())

        assert result.combined
        assert result.values["num_ctx"] is None

    def test_the_summary_is_json_safe(self):
        import json

        from agent.inference_fingerprint import fingerprint_summary

        json.dumps(fingerprint_summary(Settings()))
