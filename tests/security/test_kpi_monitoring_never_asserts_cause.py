"""Proactive monitoring may report that a governed number moved. It may
never assert *why*, never reach an unapproved metric, and never produce
a row of its own (Prompt 50,
`50_PROACTIVE_AI_ANALYST_CONTRACT.md`).

Four cross-cutting guarantees, none of which belongs to a single
module's own test file:

1. **Nothing it emits asserts a cause.** A contributor is a contribution
   to an arithmetic total. Asserted at the type level, in the rendered
   text, and across the whole package's source.
2. **It cannot produce data.** No module in `kpi_monitoring/` imports
   the SQL validator, the execution path, a database connection or an
   LLM client -- asserted by parsing the real source, the same approach
   `test_schema_drift_never_rewrites_catalog.py` and
   `test_model_routing_never_weakens_deterministic_controls.py` use.
   That is what makes "monitoring cannot bypass the deterministic SQL
   controls" structural rather than conventional: it has no way to
   obtain a row except from its caller.
3. **Only an approved metric can be monitored**, and a restricted
   dimension is refused without `VIEW_RESTRICTED_COLUMNS`.
4. **Tenant isolation**, over a real identity database: one tenant's
   published metric never makes another tenant's policy runnable, and
   one tenant's findings never appear in another's queue.

Fully offline apart from an in-memory SQLite identity database.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import identity.db as identity_db_mod
import pytest
from identity.bootstrap import seed_rbac
from identity.models import Base
from identity.repositories.semantic_intelligence import list_findings, upsert_findings
from identity.repositories.tenants import create_tenant
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from config.settings import Settings
from kpi_monitoring.detect import detect_change
from kpi_monitoring.engine import MetricSeries, monitor_kpis
from kpi_monitoring.evidence import investigate
from kpi_monitoring.findings import change_to_finding
from kpi_monitoring.models import ChangeEvidence, MonitoringPolicy
from kpi_monitoring.policy import ApprovedMetric, index_catalog_entries, validate_policies
from kpi_monitoring.reports import render_markdown

_PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "kpi_monitoring"

#: Modules whose whole purpose is to produce or gate real data. Monitoring
#: must not be able to reach any of them: it reports on rows it was
#: handed, and has no path of its own to a database.
_FORBIDDEN_IMPORTS = (
    "agent.sql_validator",
    "agent.graph",
    "agent.nodes",
    "agent.llm_client",
    "db.execution",
    "db.connection",
    "db.schema_introspection",
    "sqlalchemy",
    "identity.models",
    "identity.db",
)

#: Names that would mean this package is calling a model.
_FORBIDDEN_LLM_NAMES = ("ollama", "call_ollama", "get_ollama_client")

#: Phrasings that would state a cause rather than a contribution.
_CAUSAL_PHRASES = (
    "caused by",
    "the cause of",
    "because of",
    "due to",
    "is responsible for",
    "explains the drop",
    "driven by",
)

REVENUE = ApprovedMetric("revenue", "Net Revenue")
SERIES = [
    ("2026-01", 1000.0),
    ("2026-02", 1010.0),
    ("2026-03", 995.0),
    ("2026-04", 1005.0),
    ("2026-05", 1000.0),
    ("2026-06", 640.0),
]
COLUMNS = ["region", "revenue"]
BASELINE_ROWS = [("East", 500.0), ("West", 300.0), ("North", 200.0)]
CURRENT_ROWS = [("East", 160.0), ("West", 290.0), ("North", 190.0)]


class CatalogRow:
    def __init__(self, concept_key, concept_type="metric", status="published", name=""):
        self.concept_key = concept_key
        self.concept_type = concept_type
        self.status = status
        self.business_name = name
        self.dimensions = ()
        self.version = 1


def _policy(**overrides: object) -> MonitoringPolicy:
    base: dict[str, object] = {"policy_id": "p1", "metric_concept_key": "revenue"}
    base.update(overrides)
    return MonitoringPolicy(**base)  # type: ignore[arg-type]


def _package_modules() -> list[pathlib.Path]:
    modules = sorted(path for path in _PACKAGE.glob("*.py"))
    assert len(modules) >= 9, f"expected the full kpi_monitoring package, found {modules}"
    return modules


class TestStructurallyCannotProduceData:
    def test_no_module_imports_the_sql_or_database_layer(self):
        offenders: list[str] = []
        for path in _package_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    for forbidden in _FORBIDDEN_IMPORTS:
                        if name == forbidden or name.startswith(forbidden + "."):
                            offenders.append(f"{path.name}: {name}")
        assert offenders == [], (
            "kpi_monitoring/ must not be able to produce or execute data -- it reports on "
            f"rows supplied by its caller: {offenders}"
        )

    def test_no_module_calls_a_model(self):
        offenders: list[str] = []
        for path in _package_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import | ast.ImportFrom):
                    module = (
                        (node.module or "")
                        if isinstance(node, ast.ImportFrom)
                        else node.names[0].name
                    )
                    if module.split(".")[0] in _FORBIDDEN_LLM_NAMES:
                        offenders.append(f"{path.name}: {module}")
        assert offenders == [], offenders

    def test_no_module_contains_sql_text(self):
        """A SELECT literal anywhere here would mean something intends to
        query -- the one thing this package must never do."""
        offenders: list[str] = []
        for path in _package_modules():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    upper = node.value.upper()
                    if "SELECT " in upper and " FROM " in upper:
                        offenders.append(f"{path.name}: {node.value[:60]!r}")
        assert offenders == [], offenders


class TestNeverAssertsCause:
    def _finding_with_evidence(self):
        change, _ = detect_change(_policy(dimension_columns=("region",)), REVENUE, SERIES)
        assert change is not None
        evidence = investigate(
            change,
            _policy(dimension_columns=("region",)),
            current_breakdown=(COLUMNS, CURRENT_ROWS),
            baseline_breakdown=(COLUMNS, BASELINE_ROWS),
            value_column="revenue",
        )
        assert evidence.has_sufficient_evidence is True
        return change, evidence, change_to_finding(change, evidence)

    def test_the_finding_text_states_a_contribution_not_a_cause(self):
        _change, _evidence, finding = self._finding_with_evidence()
        text = f"{finding.title} {finding.detail}".lower()
        for phrase in _CAUSAL_PHRASES:
            assert phrase not in text, f"finding text asserts causation: {phrase!r}"
        assert "not a demonstrated cause" in finding.detail

    def test_the_title_never_names_an_attribution_at_all(self):
        """A reviewer scanning a list sees only titles; a cause named
        there would read as established long before the evidence is
        opened."""
        _change, _evidence, finding = self._finding_with_evidence()
        assert "East" not in finding.title

    def test_the_rendered_report_states_a_contribution_not_a_cause(self):
        run = monitor_kpis(
            database_id="default",
            policies=[_policy(dimension_columns=("region",))],
            catalog_entries=[CatalogRow("revenue", name="Net Revenue")],
            series_by_policy={
                "p1": MetricSeries(
                    policy_id="p1",
                    points=SERIES,
                    current_breakdown=(COLUMNS, CURRENT_ROWS),
                    baseline_breakdown=(COLUMNS, BASELINE_ROWS),
                    value_column="revenue",
                )
            },
        )
        report = render_markdown(run).lower()
        for phrase in _CAUSAL_PHRASES:
            assert phrase not in report, f"report asserts causation: {phrase!r}"

    def test_no_causal_phrasing_is_hardcoded_in_any_user_facing_string(self):
        """Scoped to strings a reader could actually be shown.

        Docstrings are excluded deliberately: they explain the design to
        whoever maintains this package and legitimately discuss
        causation (including why this package refuses to assert it).
        What must never contain causal phrasing is the text that reaches
        a finding, a report or an API response.
        """
        offenders: list[str] = []
        for path in _package_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                if id(node) in docstrings:
                    continue
                lowered = node.value.lower()
                for phrase in _CAUSAL_PHRASES:
                    # "not a demonstrated cause" is the disclaimer itself
                    # and must be allowed to contain the word.
                    if phrase in lowered and "not a demonstrated cause" not in lowered:
                        offenders.append(f"{path.name}: {phrase!r} in {node.value[:60]!r}")
        assert offenders == [], offenders

    def test_a_finding_is_always_an_inference_even_with_strong_evidence(self):
        _change, _evidence, finding = self._finding_with_evidence()
        assert finding.truth_level == "ai_inference"

    def test_attribution_without_evidence_is_unrepresentable(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ChangeEvidence(has_sufficient_evidence=False, limitations=("x",), confidence=0.99)


class TestOnlyApprovedMetrics:
    def test_an_unapproved_metric_is_never_monitored(self):
        catalog = index_catalog_entries(
            [CatalogRow("draft", status="draft"), CatalogRow("dim", concept_type="dimension")]
        )
        runnable, violations = validate_policies(
            [
                _policy(policy_id="a", metric_concept_key="draft"),
                _policy(policy_id="b", metric_concept_key="dim"),
                _policy(policy_id="c", metric_concept_key="ghost"),
            ],
            catalog,
        )
        assert runnable == ()
        assert len(violations) == 3

    def test_the_engine_produces_nothing_for_an_unapproved_metric(self):
        run = monitor_kpis(
            database_id="default",
            policies=[_policy(metric_concept_key="not_approved")],
            catalog_entries=[CatalogRow("revenue")],
            series_by_policy={"p1": MetricSeries(policy_id="p1", points=SERIES)},
        )
        assert run.findings == ()
        assert run.result.violations

    def test_a_restricted_dimension_is_refused_without_permission(self, monkeypatch):
        monkeypatch.setattr(
            "kpi_monitoring.policy.load_sensitive_columns",
            lambda: {("employees", "salary"): "restricted"},
        )
        catalog = index_catalog_entries([CatalogRow("revenue")])
        runnable, violations = validate_policies(
            [_policy(dimension_columns=("salary",))], catalog, caller_roles=("viewer",)
        )
        assert runnable == ()
        assert [violation.code for violation in violations] == ["restricted_dimension"]

    def test_a_restricted_dimension_is_allowed_with_permission(self, monkeypatch):
        monkeypatch.setattr(
            "kpi_monitoring.policy.load_sensitive_columns",
            lambda: {("employees", "salary"): "restricted"},
        )
        catalog = index_catalog_entries([CatalogRow("revenue")])
        runnable, violations = validate_policies(
            [_policy(dimension_columns=("salary",))], catalog, caller_roles=("admin",)
        )
        assert len(runnable) == 1
        assert violations == ()

    def test_a_refusal_never_echoes_the_metrics_definition(self):
        catalog = index_catalog_entries(
            [CatalogRow("secret_metric", status="draft", name="Secret")]
        )
        _runnable, violations = validate_policies(
            [_policy(metric_concept_key="secret_metric")], catalog
        )
        rendered = repr([violation.model_dump() for violation in violations])
        assert "SELECT" not in rendered.upper()
        assert "approved_expression" not in rendered


_SETTINGS = Settings(
    local_auth_enabled=True,
    auth_database_url="postgresql://placeholder/unused",
    jwt_secret_key="s" * 40,
)


@pytest.fixture
def identity_session(monkeypatch) -> Iterator[object]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    session = identity_db_mod.get_identity_session(_SETTINGS)
    seed_rbac(session)
    create_tenant(session, tenant_id="tenant-a", name="A")
    create_tenant(session, tenant_id="tenant-b", name="B")
    session.commit()
    yield session
    session.close()


class TestTenantIsolation:
    def _finding(self, period: str = "2026-06"):
        series = [*SERIES[:-1], (period, 640.0)]
        change, _ = detect_change(_policy(), REVENUE, series)
        assert change is not None
        return change_to_finding(change, investigate(change, _policy()))

    def test_one_tenants_findings_never_appear_in_anothers_queue(self, identity_session):
        upsert_findings(
            identity_session,
            tenant_id="tenant-a",
            database_id="default",
            findings=[self._finding()],
            now=datetime.now(UTC),
        )
        assert len(list_findings(identity_session, tenant_id="tenant-a")) == 1
        assert list_findings(identity_session, tenant_id="tenant-b") == []

    def test_one_tenants_published_metric_does_not_approve_anothers_policy(self):
        """Approval is resolved from the catalog rows the *caller's own*
        tenant-scoped query returned; a tenant with no such row gets a
        refusal, not another tenant's approval."""
        tenant_a_catalog = index_catalog_entries([CatalogRow("revenue")])
        tenant_b_catalog = index_catalog_entries([])

        runnable_a, _ = validate_policies([_policy()], tenant_a_catalog)
        runnable_b, violations_b = validate_policies([_policy()], tenant_b_catalog)

        assert len(runnable_a) == 1
        assert runnable_b == ()
        assert [violation.code for violation in violations_b] == ["metric_not_found"]

    def test_the_same_movement_in_two_tenants_is_two_independent_rows(self, identity_session):
        finding = self._finding()
        now = datetime.now(UTC)
        for tenant in ("tenant-a", "tenant-b"):
            upsert_findings(
                identity_session,
                tenant_id=tenant,
                database_id="default",
                findings=[finding],
                now=now,
            )
        rows_a = list_findings(identity_session, tenant_id="tenant-a")
        rows_b = list_findings(identity_session, tenant_id="tenant-b")
        assert len(rows_a) == 1 and len(rows_b) == 1
        assert rows_a[0].id != rows_b[0].id

    def test_duplicate_suppression_is_per_tenant_not_global(self, identity_session):
        """Tenant B's first sighting must not be suppressed because
        tenant A already saw the same shape."""
        finding = self._finding()
        now = datetime.now(UTC)
        first = upsert_findings(
            identity_session,
            tenant_id="tenant-a",
            database_id="default",
            findings=[finding],
            now=now,
        )
        second = upsert_findings(
            identity_session,
            tenant_id="tenant-b",
            database_id="default",
            findings=[finding],
            now=now,
        )
        assert first.created == 1
        assert second.created == 1
        assert second.unchanged == 0


class TestStorageLevelDuplicateSuppression:
    """The other half of duplicate suppression: re-running an unchanged
    scan must not version anything."""

    def _finding(self, value: float = 640.0):
        series = [*SERIES[:-1], ("2026-06", value)]
        change, _ = detect_change(_policy(), REVENUE, series)
        assert change is not None
        return change_to_finding(change, investigate(change, _policy()))

    def test_an_identical_rerun_records_unchanged(self, identity_session):
        now = datetime.now(UTC)
        first = upsert_findings(
            identity_session,
            tenant_id="tenant-a",
            database_id="default",
            findings=[self._finding()],
            now=now,
        )
        second = upsert_findings(
            identity_session,
            tenant_id="tenant-a",
            database_id="default",
            findings=[self._finding()],
            now=now,
        )
        assert first.created == 1
        assert second.created == 0
        assert second.unchanged == 1
        assert len(list_findings(identity_session, tenant_id="tenant-a")) == 1

    def test_a_restated_value_versions_the_same_row_and_reopens_it(self, identity_session):
        now = datetime.now(UTC)
        upsert_findings(
            identity_session,
            tenant_id="tenant-a",
            database_id="default",
            findings=[self._finding(640.0)],
            now=now,
        )
        summary = upsert_findings(
            identity_session,
            tenant_id="tenant-a",
            database_id="default",
            findings=[self._finding(600.0)],
            now=now,
        )
        rows = list_findings(identity_session, tenant_id="tenant-a")
        assert summary.updated == 1
        assert len(rows) == 1, "a restatement must version the same row, not add a second"
        assert rows[0].version == 2
        assert rows[0].status == "open"


class TestNoSecretsInOutput:
    def test_a_report_contains_no_credential_shaped_text(self):
        run = monitor_kpis(
            database_id="default",
            policies=[_policy()],
            catalog_entries=[CatalogRow("revenue", name="Net Revenue")],
            series_by_policy={"p1": MetricSeries(policy_id="p1", points=SERIES)},
        )
        report = render_markdown(run)
        for forbidden in ("password", "secret", "token", "connection string", "pwd="):
            assert forbidden not in report.lower()

    def test_a_finding_key_is_an_opaque_hash_not_the_inputs(self):
        """The key is stored and returned over HTTP; it must not be a
        readable join of business identifiers."""
        finding = TestTenantIsolation()._finding()
        assert len(finding.key) == 64
        assert "revenue" not in finding.key
        assert "2026-06" not in finding.key
        uuid.UUID(hex=finding.key[:32])  # parseable as hex throughout
