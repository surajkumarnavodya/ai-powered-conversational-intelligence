"""Validates a monitoring policy against the tenant's approved metrics.

**"Allow policies for approved metrics" is enforced here, structurally.**
A policy may only name a metric that is a *published* `METRIC` entry in
the asking tenant's own semantic catalog. Three separate refusals, not
one, because they mean different things to whoever has to fix the
policy: the concept does not exist, it exists but is still a draft, or
it exists and is published but is a dimension/entity rather than a
metric.

That matters more than it might look. A KPI alert asserts that *a
governed business number* moved. If a policy could name an arbitrary
concept key -- or a draft definition still under review -- the alert
would be asserting movement in something nobody has agreed is the
metric. Prompt 09/10 already built the approval workflow that decides
this; monitoring reuses that decision rather than taking its own view.

Authorization is the same restricted-column gate every other engine in
this series applies, re-derived locally for the same stated reason
(`optimization.validator._restricted_column_names`'s own note): a policy
whose `dimension_columns` name a column `config/sensitive_columns.yaml`
classifies "restricted" is refused unless the caller holds
`Permission.VIEW_RESTRICTED_COLUMNS`. Attribution breaks a KPI down *by*
that column and puts its values in a finding, so this is the same
exposure the restricted classification exists to prevent, reached by a
different route.

Every refusal is audit-logged with codes and identifiers only -- never a
metric's own `approved_expression`, never a row value.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from agent.authz import Permission, has_role_permission
from config.sensitive_columns import load_sensitive_columns
from config.settings import Settings, get_settings
from kpi_monitoring.models import MonitoringPolicy, PolicyViolation
from security.audit_log import log_security_event

logger = logging.getLogger(__name__)

#: The catalog `concept_type` a monitoring policy may target. A governed
#: dimension or entity is a real catalog concept, but it is not a number
#: that moves, so monitoring one would be meaningless rather than merely
#: unauthorized.
_METRIC_CONCEPT_TYPE = "metric"

#: The only catalog status whose definition is agreed. Mirrors
#: `retrieval.ingestion.sync_catalog_entry_to_vector_store`'s own rule
#: that a draft/reviewed entry never reaches a consumer.
_PUBLISHED_STATUS = "published"

#: One message per refusal code, so the three cases stay distinguishable
#: to whoever has to fix the policy. Names the concept key (not a
#: secret, and the one thing needed to act) and nothing else.
_REJECTION_DETAIL: dict[str, str] = {
    "metric_not_found": (
        "{key!r} is not in this tenant's semantic catalog at all; only an approved "
        "metric may be monitored"
    ),
    "metric_not_published": (
        "{key!r} exists in this tenant's semantic catalog but is not published; a "
        "metric's definition must be approved before it can be monitored"
    ),
    "metric_not_a_metric": (
        "{key!r} is a published catalog concept but not a metric; only a metric is a "
        "number that can move"
    ),
}


class ApprovedMetric:
    """One published catalog metric, reduced to what monitoring needs.

    A deliberately small projection rather than the ORM row or the full
    `CatalogEntrySnapshot`: this package never needs (and must never
    carry into a finding) the metric's own `approved_expression`. Keeping
    the projection narrow is what makes that impossible to leak by
    accident.
    """

    __slots__ = ("concept_key", "display_name", "dimensions", "version")

    def __init__(
        self,
        concept_key: str,
        display_name: str = "",
        dimensions: tuple[str, ...] = (),
        version: int = 1,
    ) -> None:
        self.concept_key = concept_key
        self.display_name = display_name or concept_key
        self.dimensions = dimensions
        self.version = version


class CatalogIndex:
    """Everything validation needs to know about a tenant's catalog.

    Two maps rather than one, so a refusal can say *which* of the three
    things went wrong (see this module's own docstring for why that
    distinction is worth keeping): `approved` holds the policies that may
    run, and `rejection_codes` remembers, for every other concept key
    actually present in the catalog, the reason it did not qualify. A key
    absent from both is genuinely unknown.
    """

    __slots__ = ("approved", "rejection_codes")

    def __init__(
        self, approved: dict[str, ApprovedMetric], rejection_codes: dict[str, str]
    ) -> None:
        self.approved = approved
        self.rejection_codes = rejection_codes

    def code_for(self, concept_key: str) -> str:
        """The violation code for a key that is not approved."""
        return self.rejection_codes.get(concept_key, "metric_not_found")


def index_catalog_entries(entries: Sequence[Any]) -> CatalogIndex:
    """Projects catalog rows/snapshots into a `CatalogIndex`.

    Accepts anything exposing `concept_key`/`concept_type`/`status` plus
    an optional `business_name`/`display_name` and `dimensions` --
    `identity.models.SemanticCatalogEntry` rows and
    `semantic.catalog.CatalogEntrySnapshot` objects both satisfy that,
    so a caller may pass whichever it already has without this module
    importing SQLAlchemy or depending on which one it got.

    A published metric lands in `approved`. A metric that exists but is
    still a draft is remembered as `metric_not_published`; a published
    concept that is a dimension/entity rather than a metric is remembered
    as `metric_not_a_metric`. A key seen in neither map was never in the
    catalog at all.
    """
    approved: dict[str, ApprovedMetric] = {}
    rejection_codes: dict[str, str] = {}

    for entry in entries:
        concept_key = getattr(entry, "concept_key", None)
        if not concept_key:
            continue
        key = str(concept_key)
        concept_type = str(getattr(entry, "concept_type", "") or "").lower()
        status = str(getattr(entry, "status", "") or "").lower()

        if concept_type != _METRIC_CONCEPT_TYPE:
            # Only record this against a key that has no better outcome:
            # a tenant may legitimately hold both a `metric` and a
            # same-named concept of another type across versions.
            rejection_codes.setdefault(key, "metric_not_a_metric")
            continue
        if status != _PUBLISHED_STATUS:
            rejection_codes.setdefault(key, "metric_not_published")
            continue

        dimensions = tuple(getattr(entry, "dimensions", ()) or ())
        display = (
            getattr(entry, "business_name", None) or getattr(entry, "display_name", None) or ""
        )
        approved[key] = ApprovedMetric(
            concept_key=key,
            display_name=str(display),
            dimensions=dimensions,
            version=int(getattr(entry, "version", 1) or 1),
        )
        # An approved version outranks any earlier non-approved one.
        rejection_codes.pop(key, None)

    return CatalogIndex(approved=approved, rejection_codes=rejection_codes)


def approved_metrics_from_entries(entries: Sequence[Any]) -> dict[str, ApprovedMetric]:
    """Just the approved map, for a caller that does not need the
    rejection detail (the report renderer, tests)."""
    return index_catalog_entries(entries).approved


def _restricted_column_names(settings: Settings) -> frozenset[str]:
    """Every column name (lowercased, table-agnostic) classified
    "restricted" anywhere in `config/sensitive_columns.yaml` -- the
    identical, independently re-derived helper
    `optimization.validator`/`analytics.scenario`/`recommendation.engine`
    already keep, each for the same stated reason (a local copy lets each
    authorization gate evolve independently rather than coupling them
    through a private name)."""
    del settings  # reserved for a future per-database override; unused today
    classifications = load_sensitive_columns()
    return frozenset(
        column.lower() for (_table, column), tier in classifications.items() if tier == "restricted"
    )


def validate_policies(
    policies: Sequence[MonitoringPolicy],
    catalog: CatalogIndex,
    *,
    caller_roles: tuple[str, ...] = (),
    tenant_id: str | None = None,
    settings: Settings | None = None,
) -> tuple[tuple[MonitoringPolicy, ...], tuple[PolicyViolation, ...]]:
    """Splits `policies` into the ones that may run and the ones refused.

    Reports **every** violation found rather than stopping at the first,
    the same convention `optimization.validator.validate_problem` and
    `analytics.scenario.validate_scenario_parameters` already establish
    -- an operator fixing a policy set wants the whole list.

    Unlike those two, one bad policy does **not** reject the whole run:
    policies are independent of each other, so refusing a hundred good
    ones because of a typo in the hundred-and-first would be its own
    outage. Each refusal is reported and the rest proceed.

    Args:
        policies: The configured policies.
        catalog: `index_catalog_entries`' output for this tenant.
        caller_roles: Roles of whoever is running the scan, for the
            restricted-dimension check.
        tenant_id: Recorded on the audit event only.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        `(runnable_policies, violations)`.
    """
    settings = settings or get_settings()
    restricted = _restricted_column_names(settings)
    may_see_restricted = has_role_permission(caller_roles, Permission.VIEW_RESTRICTED_COLUMNS)

    runnable: list[MonitoringPolicy] = []
    violations: list[PolicyViolation] = []

    limit = settings.kpi_monitor_max_policies_per_scan
    considered = list(policies)
    if len(considered) > limit:
        for extra in considered[limit:]:
            violations.append(
                PolicyViolation(
                    policy_id=extra.policy_id,
                    code="too_many_policies",
                    detail=(
                        f"this scan evaluates at most {limit} policies "
                        f"(KPI_MONITOR_MAX_POLICIES_PER_SCAN); this one was not evaluated"
                    ),
                )
            )
        considered = considered[:limit]

    for policy in considered:
        if not policy.enabled:
            continue

        metric = catalog.approved.get(policy.metric_concept_key)
        if metric is None:
            code = catalog.code_for(policy.metric_concept_key)
            violations.append(
                PolicyViolation(
                    policy_id=policy.policy_id,
                    code=code,  # type: ignore[arg-type]
                    detail=_REJECTION_DETAIL[code].format(key=policy.metric_concept_key),
                )
            )
            continue

        restricted_hits = sorted(
            column for column in policy.dimension_columns if column.lower() in restricted
        )
        if restricted_hits and not may_see_restricted:
            violations.append(
                PolicyViolation(
                    policy_id=policy.policy_id,
                    code="restricted_dimension",
                    detail=(
                        "this policy attributes the metric by a column classified "
                        "restricted, which this caller may not view"
                    ),
                )
            )
            log_security_event(
                "kpi_monitor_policy_denied",
                "warning",
                "A KPI monitoring policy was refused: it breaks a metric down by a "
                "restricted column and the caller lacks VIEW_RESTRICTED_COLUMNS.",
                policy_id=policy.policy_id,
                metric_concept_key=policy.metric_concept_key,
                restricted_columns=restricted_hits,
                tenant_id=tenant_id,
            )
            continue

        runnable.append(policy)

    return tuple(runnable), tuple(violations)


__all__ = [
    "ApprovedMetric",
    "CatalogIndex",
    "approved_metrics_from_entries",
    "index_catalog_entries",
    "validate_policies",
]
