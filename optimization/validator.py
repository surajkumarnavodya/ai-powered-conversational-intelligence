"""Structural and authorization validation for an `OptimizationProblem`
-- Prompt 39 (`39_OPTIMIZATION_ENGINE_CONTRACT.md`).

`validate_problem` is the mandatory first step of `optimization.engine
.solve_optimization_problem` -- "reject invalid or unauthorized
assumptions" (the identical mission-level requirement
`analytics.scenario.validate_scenario_parameters` already enforces for
its own engine). Pure, deterministic, no LLM, no I/O beyond reading the
small, hand-authored `config/sensitive_columns.yaml` file every other
authorization check in this codebase already reads the same way.
"""

from __future__ import annotations

import math

from agent.authz import Permission, has_role_permission
from config.sensitive_columns import load_sensitive_columns
from config.settings import Settings, get_settings
from optimization.models import OptimizationProblem, OptimizationValidation
from security.audit_log import log_security_event


def _restricted_column_names(settings: Settings) -> frozenset[str]:
    """Every column name (lowercased, table-agnostic) classified
    "restricted" anywhere in `config/sensitive_columns.yaml` -- the
    identical, independently re-derived helper `analytics.scenario
    ._restricted_column_names`/`recommendation.engine
    ._restricted_column_names` already establish (each module keeps its
    own small copy rather than importing a private name across a module
    boundary, so each authorization gate can evolve independently)."""
    del settings  # reserved for a future per-database override; unused today
    classifications = load_sensitive_columns()
    return frozenset(
        column.lower() for (_table, column), tier in classifications.items() if tier == "restricted"
    )


def validate_problem(
    problem: OptimizationProblem,
    caller_roles: tuple[str, ...] = (),
    settings: Settings | None = None,
) -> OptimizationValidation:
    """Validates `problem` as a whole -- structural references, bounds,
    payload size, and restricted-column authorization -- reporting
    *every* violation found, never stopping at the first.

    Args:
        problem: The fully-specified problem to validate.
        caller_roles: The roles of whoever is asking to solve it --
            used only for the restricted-column authorization check.
        settings: Defaults to `config.settings.get_settings()`.

    Returns:
        An `OptimizationValidation` -- `is_valid=False` if `violations`
        is non-empty, derived, never set independently of it.
    """
    settings = settings or get_settings()
    violations: list[str] = []
    warnings: list[str] = []
    is_unauthorized = False

    if len(problem.decision_variables) > settings.optimization_max_variables:
        violations.append(
            f"too many decision variables ({len(problem.decision_variables)}); the configured "
            f"maximum is {settings.optimization_max_variables}"
        )
    if len(problem.constraints) > settings.optimization_max_constraints:
        violations.append(
            f"too many constraints ({len(problem.constraints)}); the configured maximum is "
            f"{settings.optimization_max_constraints}"
        )

    declared_names = [v.name for v in problem.decision_variables]
    declared_set = set(declared_names)
    duplicates = {name for name in declared_set if declared_names.count(name) > 1}
    if duplicates:
        violations.append("duplicate decision variable name(s): " + ", ".join(sorted(duplicates)))

    for variable in problem.decision_variables:
        if not math.isfinite(variable.lower_bound):
            violations.append(f"variable {variable.name!r}: lower_bound must be finite")
        if variable.upper_bound is not None and not math.isfinite(variable.upper_bound):
            violations.append(f"variable {variable.name!r}: upper_bound must be finite")
        if (
            variable.upper_bound is not None
            and math.isfinite(variable.lower_bound)
            and math.isfinite(variable.upper_bound)
            and variable.lower_bound > variable.upper_bound
        ):
            violations.append(f"variable {variable.name!r}: lower_bound exceeds upper_bound")

    referenced_names: set[str] = set()
    for term in problem.objective.terms:
        referenced_names.add(term.variable)
        if term.variable not in declared_set:
            violations.append(f"objective references undeclared variable {term.variable!r}")
        if not math.isfinite(term.coefficient):
            violations.append(f"objective term for {term.variable!r} has a non-finite coefficient")
    if not math.isfinite(problem.objective.constant):
        violations.append("objective.constant must be finite")

    constraint_names = [c.name for c in problem.constraints]
    duplicate_constraints = {
        name for name in set(constraint_names) if constraint_names.count(name) > 1
    }
    if duplicate_constraints:
        violations.append(
            "duplicate constraint name(s): " + ", ".join(sorted(duplicate_constraints))
        )

    for constraint in problem.constraints:
        if not math.isfinite(constraint.rhs):
            violations.append(f"constraint {constraint.name!r}: rhs must be finite")
        for cterm in constraint.terms:
            referenced_names.add(cterm.variable)
            if cterm.variable not in declared_set:
                violations.append(
                    f"constraint {constraint.name!r} references undeclared variable "
                    f"{cterm.variable!r}"
                )
            if not math.isfinite(cterm.coefficient):
                violations.append(
                    f"constraint {constraint.name!r}: term for {cterm.variable!r} has a "
                    "non-finite coefficient"
                )

    unused = declared_set - referenced_names
    if unused:
        warnings.append(
            "decision variable(s) declared but never referenced by the objective or any "
            "constraint: " + ", ".join(sorted(unused))
        )

    if not has_role_permission(caller_roles, Permission.RUN_OPTIMIZATION):
        violations.append("not authorized: caller lacks Permission.RUN_OPTIMIZATION")
        is_unauthorized = True
        log_security_event(
            "optimization_unauthorized",
            "info",
            "An optimization problem was rejected for a caller lacking RUN_OPTIMIZATION.",
            problem_id=problem.problem_id,
            domain=problem.domain.value,
        )

    if problem.source_columns:
        restricted = _restricted_column_names(settings)
        restricted_hits = [c for c in problem.source_columns if c.lower() in restricted]
        if restricted_hits and not has_role_permission(
            caller_roles, Permission.VIEW_RESTRICTED_COLUMNS
        ):
            violations.append(
                "not authorized: source_columns references a restricted column "
                "(" + ", ".join(restricted_hits) + ")"
            )
            is_unauthorized = True
            log_security_event(
                "optimization_unauthorized",
                "info",
                "An optimization problem referencing a restricted source column was "
                "rejected for a caller lacking VIEW_RESTRICTED_COLUMNS.",
                problem_id=problem.problem_id,
                domain=problem.domain.value,
                columns=restricted_hits,
            )

    return OptimizationValidation(
        problem_id=problem.problem_id,
        is_valid=not violations,
        is_unauthorized=is_unauthorized,
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
