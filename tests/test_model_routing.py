"""Unit tests for `model_routing/` (Prompt 47,
`47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`).

Fully offline: no Ollama, no database, no network. The policy is a pure
function, so most of this file is table-driven assertions over it; the
router is exercised against a stub `call` that records which model it was
asked for and can be told to fail.

Adversarial/regression coverage for the invariants that matter most --
that routing never weakens a deterministic control, never reaches a model
outside the allowlist, and never overrides a caller's explicit choice --
lives in `tests/security/test_model_routing_never_weakens_deterministic_controls.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.exceptions import (
    MalformedLLMOutputError,
    OffTopicQuestionError,
    OllamaUnavailableError,
)
from agent.multiagent.policy import get_breaker, reset_breakers
from agent.provenance import DataTruthLevel
from config.settings import Settings
from model_routing.budget import escalation_allowed, new_request_budget, reset_escalation_limiters
from model_routing.catalog import build_routable_models, find_model
from model_routing.evidence import _quality_score, build_model_evaluation_evidence
from model_routing.metrics import RouteMetrics, get_route_metrics
from model_routing.models import (
    TASK_REQUIRED_CAPABILITIES,
    TASK_RISK,
    TIER_COST_UNITS,
    ModelTier,
    RequestProfile,
    RiskLevel,
    RoutableModel,
    RouteDecision,
    RouteFailureKind,
    RouteOutcome,
    RouteReason,
    RouteTask,
)
from model_routing.policy import decide_route
from model_routing.router import ModelRouter
from model_routing.signals import assess_request

# Every model named here has a real `config/ollama_models.yaml` entry, so
# the tier/capability facts under test are the ones this application
# actually ships -- not a fixture's own invented catalog.
SMALL = "llama3.2:3b"  # low resource_level, capabilities [text, sql]
DEFAULT = "llama3.1:8b"  # medium, [text, sql, reasoning, tools]
LARGE = "qwen2.5:14b"  # high, [text, sql, reasoning, tools]
SQL_ONLY = "sqlcoder:7b"  # medium, [sql] only


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "enable_model_routing": True,
        "ollama_model": DEFAULT,
        "ollama_allowed_models": f"{SMALL},{LARGE},{SQL_ONLY}",
        # Default off, so most tests here exercise the routing rules
        # themselves; the evidence gate has its own dedicated class below.
        "model_routing_require_evaluation_evidence": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _candidates(settings: Settings, evidence: dict | None = None) -> tuple[RoutableModel, ...]:
    return build_routable_models(settings, evidence)


HARD_QUESTION = "top 5 products per region with year-over-year growth, margin ratio and total cost"
SIMPLE_QUESTION = "how many employees are there"


class TestCatalog:
    def test_every_allowed_model_becomes_a_candidate_including_the_default(self):
        settings = _settings()
        ids = {candidate.model_id for candidate in _candidates(settings)}
        # Settings unions ollama_model into the allowlist itself, so the
        # configured default is always routable.
        assert ids == {SMALL, DEFAULT, LARGE, SQL_ONLY}

    def test_tiers_and_capabilities_come_from_the_real_shipped_catalog(self):
        candidates = _candidates(_settings())
        by_id = {candidate.model_id: candidate for candidate in candidates}
        assert by_id[SMALL].tier is ModelTier.SMALL
        assert by_id[DEFAULT].tier is ModelTier.MEDIUM
        assert by_id[LARGE].tier is ModelTier.LARGE
        assert by_id[DEFAULT].is_default is True
        assert "reasoning" in by_id[LARGE].capabilities
        assert "reasoning" not in by_id[SQL_ONLY].capabilities

    def test_an_uncatalogued_model_is_unknown_tier_not_a_guess(self):
        settings = _settings(ollama_allowed_models="phi4:14b")
        candidate = find_model(_candidates(settings), "phi4:14b")
        assert candidate is not None
        assert candidate.tier is ModelTier.UNKNOWN
        assert candidate.tier_known is False

    def test_candidate_order_is_deterministic(self):
        settings = _settings()
        first = [candidate.model_id for candidate in _candidates(settings)]
        second = [candidate.model_id for candidate in _candidates(settings)]
        assert first == second == sorted(first)


class TestSignals:
    def test_a_hard_question_scores_above_a_simple_one(self):
        assert assess_request(HARD_QUESTION).complexity_score >= 2
        assert assess_request(SIMPLE_QUESTION).complexity_score == 0

    def test_precomputed_signals_are_reused_rather_than_rescanned(self):
        # The live graph always passes `state["complexity_signals"]`; a
        # supplied list must win over whatever the question text would
        # have produced, so the two features can never disagree.
        profile = assess_request(SIMPLE_QUESTION, complexity_signals=["growth_comparison"])
        assert profile.complexity_signals == ("growth_comparison",)
        assert profile.complexity_score == 1

    def test_intent_implying_planning_and_ambiguity_each_add_one(self):
        profile = assess_request(
            SIMPLE_QUESTION,
            complexity_signals=[],
            analytical_intent={"intent": "root_cause", "ambiguity_flags": ["vague_time_range"]},
        )
        assert profile.implies_planning is True
        assert profile.has_ambiguity is True
        assert profile.complexity_score == 2
        assert profile.analytical_intent == "root_cause"

    def test_a_missing_intent_contributes_nothing_and_is_not_read_as_simple(self):
        profile = assess_request(HARD_QUESTION, analytical_intent=None)
        assert profile.implies_planning is False
        assert profile.complexity_score == len(profile.complexity_signals)


class TestPolicyFlagOff:
    def test_routing_disabled_always_returns_the_configured_default(self):
        settings = _settings(enable_model_routing=False)
        for task in RouteTask:
            decision = decide_route(
                task, assess_request(HARD_QUESTION), _candidates(settings), settings
            )
            assert decision.model_id == DEFAULT
            assert decision.reason is RouteReason.ROUTING_DISABLED
            assert decision.changed_from_baseline is False


class TestPolicyComplexity:
    def test_a_hard_question_escalates_sql_generation(self):
        settings = _settings()
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
        )
        assert decision.model_id == LARGE
        assert decision.tier is ModelTier.LARGE
        assert decision.reason is RouteReason.UPGRADED_FOR_COMPLEXITY

    def test_a_simple_question_keeps_the_default_for_a_high_risk_task(self):
        settings = _settings()
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(SIMPLE_QUESTION),
            _candidates(settings),
            settings,
        )
        assert decision.model_id == DEFAULT
        assert decision.reason is RouteReason.DEFAULT_NO_CHANGE

    def test_a_simple_question_downgrades_a_low_risk_task(self):
        settings = _settings()
        for task in (RouteTask.SQL_INSIGHT, RouteTask.ANALYTICAL_INTENT):
            decision = decide_route(
                task, assess_request(SIMPLE_QUESTION), _candidates(settings), settings
            )
            assert decision.model_id == SMALL, task
            assert decision.reason is RouteReason.DOWNGRADED_FOR_SIMPLICITY

    @pytest.mark.parametrize(
        "task",
        [
            RouteTask.SQL_GENERATION,
            RouteTask.SQL_REVIEW,
            RouteTask.METRIC_CONFORMANCE_REVIEW,
            RouteTask.QUERY_PLAN,
            RouteTask.ANALYTICAL_PLAN,
        ],
    )
    def test_no_task_above_low_risk_is_ever_downgraded(self, task):
        """The conservative direction, stated as a test rather than a
        comment: a weaker draft of the SQL itself, of the checks over it,
        or of the plan they are checked against is never traded for
        compute."""
        settings = _settings()
        decision = decide_route(
            task, assess_request(SIMPLE_QUESTION), _candidates(settings), settings
        )
        assert decision.reason is not RouteReason.DOWNGRADED_FOR_SIMPLICITY
        assert decision.model_id == DEFAULT

    def test_decisions_are_reproducible_for_identical_inputs(self):
        settings = _settings()
        candidates = _candidates(settings)
        profile = assess_request(HARD_QUESTION)
        first = decide_route(RouteTask.SQL_GENERATION, profile, candidates, settings)
        second = decide_route(RouteTask.SQL_GENERATION, profile, candidates, settings)
        assert first == second


class TestPolicyCapability:
    def test_a_model_lacking_a_required_capability_is_never_chosen(self):
        """`sqlcoder:7b` is tagged `[sql]` only, so it can serve
        generation but must never be picked for a reasoning task."""
        settings = _settings(ollama_model=SMALL, ollama_allowed_models=f"{SQL_ONLY},{SMALL}")
        candidates = _candidates(settings)
        decision = decide_route(
            RouteTask.QUERY_PLAN, assess_request(HARD_QUESTION), candidates, settings
        )
        assert decision.model_id == SMALL
        assert decision.reason is RouteReason.NO_ELIGIBLE_CANDIDATE
        # ...while a task that only needs `sql` may escalate to it.
        generation = decide_route(
            RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), candidates, settings
        )
        assert generation.model_id == SQL_ONLY
        assert generation.reason is RouteReason.UPGRADED_FOR_COMPLEXITY

    def test_the_configured_default_is_legal_even_with_no_catalog_entry(self):
        """`config/ollama_models.yaml`'s own docstring promises its
        metadata is never a gate. An operator's un-catalogued default must
        therefore still serve every task."""
        settings = _settings(ollama_model="phi4:14b", ollama_allowed_models="phi4:14b")
        for task in RouteTask:
            decision = decide_route(
                task, assess_request(HARD_QUESTION), _candidates(settings), settings
            )
            assert decision.model_id == "phi4:14b"

    def test_an_uncatalogued_model_is_never_an_escalation_target(self):
        settings = _settings(ollama_allowed_models="phi4:14b")
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
        )
        assert decision.model_id == DEFAULT
        assert decision.reason is RouteReason.NO_ELIGIBLE_CANDIDATE


class TestPolicyEvidenceGate:
    def test_without_governed_evidence_nothing_is_escalated(self):
        settings = _settings(model_routing_require_evaluation_evidence=True)
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
        )
        assert decision.model_id == DEFAULT
        assert decision.reason is RouteReason.NO_EVALUATION_EVIDENCE

    def test_with_governed_evidence_the_escalation_proceeds(self):
        settings = _settings(model_routing_require_evaluation_evidence=True)
        candidates = _candidates(settings, {LARGE: (True, 0.93, "version-1")})
        decision = decide_route(
            RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), candidates, settings
        )
        assert decision.model_id == LARGE
        assert decision.reason is RouteReason.UPGRADED_FOR_COMPLEXITY
        assert decision.evaluation_backed is True

    def test_a_downgrade_also_requires_evidence(self):
        """Swapping in a cheaper model is still a change to how answers
        are produced, so the governance gate applies in both directions."""
        settings = _settings(model_routing_require_evaluation_evidence=True)
        blocked = decide_route(
            RouteTask.SQL_INSIGHT,
            assess_request(SIMPLE_QUESTION),
            _candidates(settings),
            settings,
        )
        assert blocked.model_id == DEFAULT
        assert blocked.reason is RouteReason.NO_EVALUATION_EVIDENCE

        allowed = decide_route(
            RouteTask.SQL_INSIGHT,
            assess_request(SIMPLE_QUESTION),
            _candidates(settings, {SMALL: (True, 0.71, "version-2")}),
            settings,
        )
        assert allowed.model_id == SMALL
        assert allowed.reason is RouteReason.DOWNGRADED_FOR_SIMPLICITY

    def test_the_highest_recorded_score_wins_among_eligible_escalations(self):
        settings = _settings(
            model_routing_require_evaluation_evidence=True,
            ollama_model=SMALL,
            ollama_allowed_models=f"{SMALL},{DEFAULT},{LARGE}",
        )
        candidates = _candidates(
            settings, {DEFAULT: (True, 0.95, "v-default"), LARGE: (True, 0.60, "v-large")}
        )
        decision = decide_route(
            RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), candidates, settings
        )
        # The stronger *tier* loses to the better recorded evaluation --
        # the whole point of gating on evidence rather than on size.
        assert decision.model_id == DEFAULT


class TestPolicyLatency:
    def test_a_latency_sensitive_request_is_never_escalated(self):
        settings = _settings()
        profile = assess_request(HARD_QUESTION, latency_sensitive=True)
        decision = decide_route(RouteTask.SQL_GENERATION, profile, _candidates(settings), settings)
        assert decision.model_id == DEFAULT
        assert decision.reason is RouteReason.UPGRADE_BLOCKED_BY_LATENCY

    def test_a_measured_slow_model_is_dropped_from_the_escalation_set(self):
        settings = _settings(model_routing_max_p95_latency_ms=5000.0)
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
            observed_p95_latency_ms={LARGE: 9000.0},
        )
        assert decision.model_id == DEFAULT
        assert decision.reason is RouteReason.UPGRADE_BLOCKED_BY_LATENCY

    def test_an_unmeasured_model_is_not_treated_as_slow(self):
        settings = _settings(model_routing_max_p95_latency_ms=5000.0)
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
            observed_p95_latency_ms={SMALL: 9000.0},
        )
        assert decision.model_id == LARGE


class TestPolicyBreakerAndBudget:
    def test_an_open_circuit_on_the_default_moves_off_it(self):
        settings = _settings()
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(SIMPLE_QUESTION),
            _candidates(settings),
            settings,
            unavailable_model_ids=frozenset({DEFAULT}),
        )
        assert decision.model_id != DEFAULT
        assert decision.reason is RouteReason.CIRCUIT_OPEN

    def test_an_open_circuit_is_never_chosen_or_offered_as_a_fallback(self):
        settings = _settings()
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
            unavailable_model_ids=frozenset({LARGE}),
        )
        assert decision.model_id != LARGE
        assert LARGE not in decision.fallback_chain

    def test_a_spent_budget_blocks_the_escalation_without_failing_anything(self):
        settings = _settings()
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
            escalation_allowed=False,
        )
        assert decision.model_id == DEFAULT
        assert decision.reason is RouteReason.BUDGET_EXHAUSTED


class TestFallbackChain:
    def test_the_configured_default_comes_first_and_the_choice_is_absent(self):
        settings = _settings()
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
        )
        assert decision.model_id == LARGE
        assert decision.fallback_chain[0] == DEFAULT
        assert LARGE not in decision.fallback_chain

    def test_the_chain_is_capped_by_configuration(self):
        settings = _settings(model_routing_max_fallbacks=1)
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
        )
        assert len(decision.fallback_chain) == 1

    def test_zero_disables_fallback_entirely(self):
        settings = _settings(model_routing_max_fallbacks=0)
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
        )
        assert decision.fallback_chain == ()


class TestBudget:
    def test_a_request_budget_runs_out_of_cost_units(self):
        large = TIER_COST_UNITS[ModelTier.LARGE]
        settings = _settings(model_routing_max_cost_units_per_request=large + 1)
        budget = new_request_budget(settings)
        assert escalation_allowed(budget, None, large, settings) is True
        budget.charge(large)
        # One escalation's worth of units is gone, and the remaining
        # single unit cannot cover another -- so routing stops escalating
        # while the request itself carries on, on the default model.
        assert escalation_allowed(budget, None, large, settings) is False

    def test_a_budget_that_could_never_afford_an_escalation_blocks_it_up_front(self):
        settings = _settings(
            model_routing_max_cost_units_per_request=TIER_COST_UNITS[ModelTier.LARGE] - 1
        )
        budget = new_request_budget(settings)
        assert escalation_allowed(budget, None, TIER_COST_UNITS[ModelTier.LARGE], settings) is False

    def test_the_llm_call_cap_is_independent_of_the_cost_cap(self):
        settings = _settings(model_routing_max_llm_calls_per_request=1)
        budget = new_request_budget(settings)
        budget.charge(1)
        assert escalation_allowed(budget, None, 1, settings) is False

    def test_the_tenant_window_meters_escalations(self):
        reset_escalation_limiters()
        settings = _settings(model_routing_escalations_per_hour=2)
        assert escalation_allowed(None, "tenant-a", 5, settings) is True
        assert escalation_allowed(None, "tenant-a", 5, settings) is True
        assert escalation_allowed(None, "tenant-a", 5, settings) is False
        # A different tenant has its own window -- never a shared bucket.
        assert escalation_allowed(None, "tenant-b", 5, settings) is True

    def test_no_tenant_means_no_shared_bucket(self):
        reset_escalation_limiters()
        settings = _settings(model_routing_escalations_per_hour=1)
        for _ in range(5):
            assert escalation_allowed(None, None, 5, settings) is True


class TestMetrics:
    def test_outcomes_roll_up_per_route(self):
        metrics = RouteMetrics()
        for latency, ok in ((100.0, True), (300.0, True), (200.0, False)):
            metrics.record_outcome(
                RouteOutcome(
                    task=RouteTask.SQL_GENERATION,
                    model_id=DEFAULT,
                    tier=ModelTier.MEDIUM,
                    succeeded=ok,
                    latency_ms=latency,
                    failure_kind=None if ok else RouteFailureKind.QUALITY,
                    cost_units=2,
                )
            )
        snapshot = metrics.snapshot()
        assert len(snapshot["routes"]) == 1
        route = snapshot["routes"][0]
        assert route["calls"] == 3
        assert route["successes"] == 2
        assert route["quality_failures"] == 1
        assert route["quality_failure_rate"] == pytest.approx(1 / 3)
        assert route["cost_units"] == 6
        assert snapshot["total_cost_units"] == 6

    def test_availability_and_quality_failures_are_counted_separately(self):
        metrics = RouteMetrics()
        for kind in (RouteFailureKind.AVAILABILITY, RouteFailureKind.QUALITY):
            metrics.record_outcome(
                RouteOutcome(
                    task=RouteTask.SQL_REVIEW,
                    model_id=LARGE,
                    tier=ModelTier.LARGE,
                    succeeded=False,
                    latency_ms=10.0,
                    failure_kind=kind,
                )
            )
        route = metrics.snapshot()["routes"][0]
        assert route["availability_failures"] == 1
        assert route["quality_failures"] == 1

    def test_p95_is_aggregated_per_model_across_tasks(self):
        metrics = RouteMetrics()
        for task in (RouteTask.SQL_GENERATION, RouteTask.SQL_INSIGHT):
            metrics.record_outcome(
                RouteOutcome(
                    task=task,
                    model_id=DEFAULT,
                    tier=ModelTier.MEDIUM,
                    succeeded=True,
                    latency_ms=500.0,
                )
            )
        assert metrics.p95_latency_by_model() == {DEFAULT: 500.0}

    def test_a_no_change_decision_is_still_counted(self):
        metrics = RouteMetrics()
        settings = _settings()
        metrics.record_decision(
            decide_route(
                RouteTask.SQL_GENERATION,
                assess_request(SIMPLE_QUESTION),
                _candidates(settings),
                settings,
            )
        )
        snapshot = metrics.snapshot()
        assert snapshot["decisions"] == 1
        assert snapshot["escalations"] == 0
        assert snapshot["decisions_by_reason"] == {RouteReason.DEFAULT_NO_CHANGE.value: 1}

    def test_a_snapshot_contains_no_question_sql_or_answer_text(self):
        metrics = RouteMetrics()
        metrics.record_outcome(
            RouteOutcome(
                task=RouteTask.SQL_GENERATION,
                model_id=DEFAULT,
                tier=ModelTier.MEDIUM,
                succeeded=True,
                latency_ms=1.0,
            )
        )
        rendered = repr(metrics.snapshot())
        for forbidden in ("SELECT", "employees", HARD_QUESTION):
            assert forbidden not in rendered


class TestRouterFallback:
    def setup_method(self) -> None:
        reset_breakers()
        reset_escalation_limiters()

    def _router(self, settings: Settings) -> ModelRouter:
        return ModelRouter(settings, tenant_id=None, candidates=_candidates(settings))

    def test_an_unavailable_model_falls_through_to_the_next_candidate(self):
        settings = _settings()
        router = self._router(settings)
        attempted: list[str | None] = []

        def call(model_id: str | None) -> str:
            attempted.append(model_id)
            if model_id == LARGE:
                raise OllamaUnavailableError(f"{model_id} is not pulled")
            return "answer"

        result, decision = router.execute(
            RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), call
        )
        assert result == "answer"
        assert attempted == [LARGE, DEFAULT]
        assert decision.model_id == LARGE

    def test_every_candidate_failing_raises_the_last_real_error(self):
        settings = _settings()
        router = self._router(settings)

        def call(model_id: str | None) -> str:
            raise OllamaUnavailableError(f"{model_id} unreachable")

        with pytest.raises(OllamaUnavailableError) as excinfo:
            router.execute(RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), call)
        # The *last* attempt's error, not a stale first one.
        assert DEFAULT in str(excinfo.value) or SQL_ONLY in str(excinfo.value)

    def test_a_quality_failure_is_reraised_without_trying_another_model(self):
        settings = _settings()
        router = self._router(settings)
        attempted: list[str | None] = []

        def call(model_id: str | None) -> str:
            attempted.append(model_id)
            raise MalformedLLMOutputError("not parseable")

        with pytest.raises(MalformedLLMOutputError):
            router.execute(RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), call)
        assert attempted == [LARGE]

    def test_an_off_topic_refusal_is_recorded_as_a_success(self):
        """The model judging a question unanswerable as SQL is it doing its
        job -- `agent.input_guard`'s pre-filter and `generate_sql_node`'s
        backstop both exist to reach exactly that outcome. Counting it as a
        quality failure would inflate the route's own
        `quality_failure_rate` for correct behavior."""
        settings = _settings()
        router = self._router(settings)
        metrics = get_route_metrics()
        metrics.reset()

        def declines(model_id: str | None) -> str:
            raise OffTopicQuestionError("not answerable as SQL")

        with pytest.raises(OffTopicQuestionError):
            router.execute(RouteTask.SQL_GENERATION, assess_request(SIMPLE_QUESTION), declines)

        route = metrics.snapshot()["routes"][0]
        assert route["successes"] == 1
        assert route["quality_failures"] == 0
        assert route["availability_failures"] == 0

    def test_repeated_availability_failures_open_the_circuit(self):
        settings = _settings(model_routing_breaker_failure_threshold=2)
        router = self._router(settings)

        def failing(model_id: str | None) -> str:
            raise OllamaUnavailableError("down")

        for _ in range(2):
            with pytest.raises(OllamaUnavailableError):
                router.execute(RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), failing)

        breaker = get_breaker(f"ollama_model:{LARGE}")
        assert breaker.state in {"open", "half_open"}

        # With LARGE circuit-open, the next decision must not pick it.
        later = router.decide(RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION))
        assert later.model_id != LARGE

    def test_a_quality_failure_does_not_open_the_circuit(self):
        settings = _settings(model_routing_breaker_failure_threshold=1)
        router = self._router(settings)

        def malformed(model_id: str | None) -> str:
            raise MalformedLLMOutputError("nope")

        with pytest.raises(MalformedLLMOutputError):
            router.execute(RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), malformed)
        assert get_breaker(f"ollama_model:{LARGE}").state == "closed"

    def test_decisions_are_recorded_in_order(self):
        settings = _settings()
        router = self._router(settings)
        for task in (RouteTask.SQL_GENERATION, RouteTask.SQL_INSIGHT):
            router.execute(task, assess_request(SIMPLE_QUESTION), lambda _m: "ok")
        assert [decision.task for decision in router.decisions] == [
            RouteTask.SQL_GENERATION,
            RouteTask.SQL_INSIGHT,
        ]

    def test_a_pinned_model_is_used_for_every_task_with_no_fallback(self):
        settings = _settings()
        router = ModelRouter(
            settings, tenant_id=None, pinned_model=SMALL, candidates=_candidates(settings)
        )
        attempted: list[str | None] = []
        for task in RouteTask:
            router.execute(
                task, assess_request(HARD_QUESTION), lambda m: attempted.append(m) or "ok"
            )
        assert set(attempted) == {SMALL}
        assert all(
            decision.reason is RouteReason.CALLER_PINNED_MODEL for decision in router.decisions
        )
        assert all(decision.fallback_chain == () for decision in router.decisions)

    def test_the_per_request_budget_stops_further_escalation_within_one_run(self):
        settings = _settings(model_routing_max_cost_units_per_request=5)
        router = self._router(settings)
        first = router.decide(RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION))
        assert first.reason is RouteReason.UPGRADED_FOR_COMPLEXITY
        # Spend the budget by actually running a call, then ask again.
        router.execute(RouteTask.SQL_GENERATION, assess_request(HARD_QUESTION), lambda _m: "ok")
        later = router.decide(RouteTask.QUERY_PLAN, assess_request(HARD_QUESTION))
        assert later.reason is RouteReason.BUDGET_EXHAUSTED
        assert later.model_id == DEFAULT


class TestEvidenceReading:
    def test_a_recognized_score_is_read_and_an_unrecognized_shape_is_not_guessed(self):
        assert _quality_score({"accuracy": 0.9}) == 0.9
        assert _quality_score({"quality_score": 1, "accuracy": 0.2}) == 1.0
        assert _quality_score({"notes": "looked good"}) is None
        assert _quality_score({"passed": True}) is None
        assert _quality_score("not a dict") is None
        assert _quality_score(None) is None

    def test_only_approved_or_deployed_versions_with_results_count(self):
        class _Version:
            def __init__(self, key, results, version_id="v"):
                self.asset_key = key
                self.evaluation_results = results
                self.id = version_id

        rows = {
            "approved": [_Version(LARGE, {"accuracy": 0.8}, "v-approved")],
            "deployed": [_Version(SMALL, None, "v-no-results")],
        }

        class _Session:
            pass

        def fake_list_versions(session, tenant_id, *, asset_type, stage):
            assert asset_type == "model"
            assert tenant_id == "tenant-a"
            return rows.get(stage, [])

        import identity.repositories.governance as governance_repo

        original = governance_repo.list_versions
        governance_repo.list_versions = fake_list_versions  # type: ignore[assignment]
        try:
            evidence = build_model_evaluation_evidence(_Session(), "tenant-a")
        finally:
            governance_repo.list_versions = original  # type: ignore[assignment]

        assert evidence == {LARGE: (True, 0.8, "v-approved")}
        # A version with no recorded evaluation results is not evidence.
        assert SMALL not in evidence


class TestTypeLevelGuarantees:
    def test_a_decision_can_never_claim_to_be_an_observed_fact(self):
        settings = _settings()
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
        )
        assert decision.truth_level is DataTruthLevel.AI_INFERENCE
        with pytest.raises(ValidationError):
            decision.model_copy(update={"truth_level": DataTruthLevel.DATABASE_FACT}).model_dump()
            RouteDecision(**{**decision.model_dump(), "truth_level": DataTruthLevel.DATABASE_FACT})

    def test_every_decision_and_profile_is_frozen(self):
        settings = _settings()
        decision = decide_route(
            RouteTask.SQL_GENERATION,
            assess_request(HARD_QUESTION),
            _candidates(settings),
            settings,
        )
        with pytest.raises(ValidationError):
            decision.model_id = "something-else"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            RequestProfile().complexity_score = 9  # type: ignore[misc]

    def test_every_task_has_a_reviewed_risk_level_and_capability_set(self):
        """A new `RouteTask` member must be classified deliberately, not
        default to whatever `.get()` would have returned."""
        for task in RouteTask:
            assert task in TASK_RISK
            assert task in TASK_REQUIRED_CAPABILITIES
            assert isinstance(TASK_RISK[task], RiskLevel)
            assert TASK_REQUIRED_CAPABILITIES[task]

    def test_cost_units_are_defined_for_every_tier_and_are_ordinal(self):
        for tier in ModelTier:
            assert TIER_COST_UNITS[tier] >= 1
        assert (
            TIER_COST_UNITS[ModelTier.SMALL]
            < TIER_COST_UNITS[ModelTier.MEDIUM]
            < TIER_COST_UNITS[ModelTier.LARGE]
        )
