"""Result-level governance: protects SQL result values before anyone else sees them.

Runs on the rows execution returned, before they are stored in agent state.
That means the insight step, the recommendation engine, and the response
all receive the protected values, not the raw ones. The LLM never sees a
restricted value merely because the UI would later hide it.

Five protections, all no-ops for ordinary data:

1. Restricted columns. A column whose name is classified "restricted" in
   `config/sensitive_columns.yaml` has its values replaced with
   `RESTRICTED_MASK` for a caller without `VIEW_RESTRICTED_COLUMNS`. Matching
   is by result column name, case-insensitively, because a result set names
   columns by alias. This is a second layer behind the SQL-level gate in
   `agent.sql_validator`, which runs first and catches direct references.
2. Secret-shaped text. Any string cell containing a connection string or
   password pattern is redacted (`security.redaction.redact_secrets`). This
   covers data that happens to contain a credential.

3. Classified fields. Any column whose name classifies at or above
   SENSITIVE (`governance.classification`) is masked for a caller without
   `VIEW_RESTRICTED_COLUMNS`, and a column in a never-disclosed category
   (credential, payment instrument) is suppressed for **every** caller,
   including an admin -- there is no role for which returning a password
   is the right answer. This is what makes protection work on a database
   whose columns nobody has hand-classified yet: `config/sensitive_columns
   .yaml` ships empty, so protection 1 alone covers nothing.
4. Small groups. A grouped result whose group is smaller than the
   domain's k threshold has its sensitive measures suppressed
   (`governance.suppression`).
5. Bulk extraction. A result carrying the domain's own sensitive
   categories is capped at that domain's `export_row_limit`, so a query
   that slipped past the request-level bulk-extraction gate still cannot
   return an unbounded number of sensitive records. The cap applies to an
   authorized caller too -- permission to see a value is not permission
   to extract every one of them at once -- and the number of rows held
   back is reported, never silently dropped.

Row values are data, not instructions. Nothing in a cell can change how this
module decides, and the module never logs cell values, only counts.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from agent.authz import Permission, has_role_permission
from config.sensitive_columns import load_sensitive_columns
from governance.classification import (
    DataClassification,
    MaskingStrategy,
    classify_columns,
)
from governance.domains import BusinessDomain, minimum_group_size_for, policy_for
from governance.suppression import suppress_small_groups
from governance.telemetry import get_governance_metrics, normalize_action
from security.redaction import redact_secrets

logger = logging.getLogger(__name__)

RESTRICTED_MASK = "[restricted]"


@dataclass(frozen=True)
class GovernedResult:
    """A protected copy of a result set. `actions` describes what changed, with no values."""

    columns: list[str]
    rows: list[Any]
    actions: tuple[str, ...]
    #: True when the detected domain declares `audit_required`, meaning
    #: every decision in it is an auditable event **including an allow**
    #: that changed nothing. The caller emits the event -- this module
    #: stays side-effect-free apart from its own counters -- so a domain
    #: like HR or healthcare leaves an audit trail for a clean query,
    #: not only for a protected one. Always False for the domain-less
    #: entry points, which have no policy to read it from.
    audit_required: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.actions)


def govern_result(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    restricted_column_names: Collection[str],
    can_view_restricted: bool,
) -> GovernedResult:
    """Applies result-level protection. Returns new rows and leaves the input untouched.

    Args:
        columns: The result's column names, as returned by execution.
        rows: The result's rows. Tuples stay tuples and lists stay lists.
        restricted_column_names: Column names classified "restricted", in any case.
        can_view_restricted: True if the caller holds `VIEW_RESTRICTED_COLUMNS`.
    """
    restricted_lower = {name.lower() for name in restricted_column_names}
    masked_indexes: dict[int, str] = {}
    if not can_view_restricted:
        for index, name in enumerate(columns):
            if name.lower() in restricted_lower:
                masked_indexes[index] = name

    redacted_cells = 0
    governed_rows: list[Any] = []
    for row in rows:
        values: list[Any] = []
        for index, value in enumerate(row):
            if index in masked_indexes:
                values.append(RESTRICTED_MASK)
                continue
            if isinstance(value, str):
                redacted = redact_secrets(value)
                if redacted != value:
                    redacted_cells += 1
                    value = redacted
            values.append(value)
        governed_rows.append(tuple(values) if isinstance(row, tuple) else values)

    actions: list[str] = []
    if masked_indexes:
        actions.append("masked_restricted_columns:" + ",".join(sorted(masked_indexes.values())))
    if redacted_cells:
        actions.append(f"redacted_secret_cells:{redacted_cells}")

    return GovernedResult(columns=list(columns), rows=governed_rows, actions=tuple(actions))


def govern_rows_for_caller(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    caller_roles: Sequence[str],
) -> list[Any]:
    """The live entry point: governs executed rows for one caller.

    Reads the restricted-column classification fresh (`config/sensitive_columns
    .yaml`, same as the SQL-level gate) and derives the caller's permission from
    their roles. Logs what changed, never the values. Returns the rows unchanged
    when there are none.
    """
    if not rows:
        return list(rows)
    restricted = {
        column
        for (_table, column), tier in load_sensitive_columns().items()
        if tier == "restricted"
    }
    can_view = has_role_permission(caller_roles, Permission.VIEW_RESTRICTED_COLUMNS)
    governed = govern_result(columns, rows, restricted, can_view)
    if governed.changed:
        logger.info("[result_governance] applied: %s", ", ".join(governed.actions))
    return governed.rows


#: What a never-disclosed value is replaced with. Distinct from
#: `RESTRICTED_MASK`, which means "your role may not see this": this one
#: means "no role sees this", and conflating them would misreport why.
WITHHELD_MASK = "[withheld]"


def _classification_masks(
    columns: Sequence[str],
    can_view_restricted: bool,
    restricted_categories: Collection[str] | None = None,
) -> tuple[dict[int, str], dict[int, str]]:
    """Which column indexes to mask, and which to withhold outright.

    Returns `(masked, withheld)` as index -> column name. A column is
    withheld when its category has no authorized viewer at all; it is
    masked when the caller simply lacks the permission.

    `restricted_categories` narrows masking to the categories the
    detected domain actually restricts. `None` means "apply the
    classification level alone", which is the behaviour for a caller
    that supplies no domain.
    """
    policies = classify_columns(list(columns))
    masked: dict[int, str] = {}
    withheld: dict[int, str] = {}
    for index, name in enumerate(columns):
        policy = policies[name]
        if policy.is_never_disclosed:
            withheld[index] = name
            continue
        if can_view_restricted:
            continue
        if policy.classification < DataClassification.SENSITIVE:
            continue
        if restricted_categories is not None and policy.category not in restricted_categories:
            continue
        if policy.masking is MaskingStrategy.NONE:
            continue
        masked[index] = name
    return masked, withheld


def _carries_restricted_data(
    columns: Sequence[str],
    restricted_categories: Collection[str],
) -> bool:
    """Whether this result actually contains the domain's sensitive data.

    The export ceiling exists to stop bulk extraction of sensitive
    records, not to cap ordinary analytics. A 400-row breakdown of
    orders per region is not an export of HR data merely because the
    question was routed to the HR domain, so the ceiling only engages
    when a column classifies at SENSITIVE or above *and* falls in a
    category this domain restricts. Over-blocking ordinary aggregates is
    its own failure mode.
    """
    policies = classify_columns(list(columns))
    return any(
        policy.classification >= DataClassification.SENSITIVE
        and policy.category.value in restricted_categories
        for policy in policies.values()
    )


def govern_result_for_domain(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    caller_roles: Sequence[str],
    domain: BusinessDomain = BusinessDomain.GENERAL,
    minimum_group_size: int = 0,
) -> GovernedResult:
    """Full result-level governance for one caller in one domain.

    Applies, in order: the pre-existing configured-restricted-column
    masking and secret-shaped-text redaction (`govern_result`), then
    classification-driven masking and never-disclosed withholding, then
    small-group suppression.

    Order matters. Masking runs before suppression so a suppressed small
    group cannot be read through a column that masking would also have
    protected, and suppression runs last so it sees the final column set.

    Args:
        columns: The result's column names.
        rows: The result's rows, left untouched.
        caller_roles: The caller's roles. Permission is derived from
            these only -- never from anything in the question or the data.
        domain: The detected business domain, deciding which categories
            are restricted and the default k threshold.
        minimum_group_size: The operator's configured k default. The
            domain's own threshold wins when it declares one.

    Returns:
        A `GovernedResult` whose `actions` name what changed, with no
        values -- safe to log and to return in a response.
    """
    if not rows:
        return GovernedResult(columns=list(columns), rows=list(rows), actions=())

    metrics = get_governance_metrics()
    base = govern_rows_for_caller_result(columns, rows, caller_roles)
    actions = list(base.actions)
    working_rows = base.rows

    policy = policy_for(domain)
    # The domain's own declared requirement, not a hardcoded constant.
    # Every domain currently declares `VIEW_RESTRICTED_COLUMNS`, so this
    # is behaviour-identical today -- but an operator who raises one
    # domain's bar should have that honoured rather than silently
    # ignored, which is what reading the field makes true.
    can_view = has_role_permission(caller_roles, policy.required_permission)
    restricted_category_values = {
        category.value for category in policy.effective_restricted_categories
    }
    masked, withheld = _classification_masks(columns, can_view, restricted_category_values)

    if masked or withheld:
        protected: list[Any] = []
        for row in working_rows:
            values = list(row)
            for index in masked:
                if index < len(values):
                    values[index] = RESTRICTED_MASK
            for index in withheld:
                if index < len(values):
                    values[index] = WITHHELD_MASK
            protected.append(tuple(values) if isinstance(row, tuple) else values)
        working_rows = protected
        if masked:
            actions.append("masked_classified_columns:" + ",".join(sorted(masked.values())))
        if withheld:
            actions.append("withheld_columns:" + ",".join(sorted(withheld.values())))

    threshold = minimum_group_size_for(domain, minimum_group_size)
    suppression = suppress_small_groups(
        columns,
        working_rows,
        threshold,
        skip_indexes=frozenset(masked) | frozenset(withheld),
    )
    if suppression.changed:
        working_rows = suppression.rows
        actions.append(f"suppressed_small_groups:{suppression.suppressed_rows}")
        metrics.record_suppressed_groups(suppression.suppressed_rows)

    # Per-domain export ceiling (§31). Runs last, after suppression, so
    # k-anonymity is computed over the whole result rather than over an
    # already-truncated prefix -- truncating first would shrink every
    # group and suppress more than the data warrants.
    #
    # Unlike masking, this applies to an authorized caller too: holding
    # `VIEW_RESTRICTED_COLUMNS` means a caller may see sensitive values,
    # not that they may extract an unbounded number of them in one
    # request. The row count the caller was going to receive is reported
    # in `actions`, so a truncated set is never silently passed off as a
    # complete one.
    limit = policy.export_row_limit
    if (
        limit is not None
        and len(working_rows) > limit
        and _carries_restricted_data(columns, restricted_category_values)
    ):
        withheld_rows = len(working_rows) - limit
        working_rows = list(working_rows[:limit])
        actions.append(f"export_limited:{withheld_rows}")

    for action in actions:
        metrics.record_result_action(normalize_action(action))

    if actions:
        logger.info("[result_governance] domain=%s applied: %s", domain.value, ", ".join(actions))
    return GovernedResult(
        columns=list(columns),
        rows=working_rows,
        actions=tuple(actions),
        audit_required=policy.audit_required,
    )


def govern_rows_for_caller_result(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    caller_roles: Sequence[str],
) -> GovernedResult:
    """`govern_rows_for_caller`, returning the full result instead of rows.

    Exists so `govern_result_for_domain` can read the `actions` the
    original entry point only logs. The original keeps its row-returning
    signature, which four call sites depend on.
    """
    restricted = {
        column
        for (_table, column), tier in load_sensitive_columns().items()
        if tier == "restricted"
    }
    can_view = has_role_permission(caller_roles, Permission.VIEW_RESTRICTED_COLUMNS)
    return govern_result(columns, rows, restricted, can_view)
