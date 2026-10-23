"""Result-level governance: masking restricted columns and redacting secret-shaped cells."""

from __future__ import annotations

import pytest

from governance.domains import BusinessDomain
from governance.result_policy import (
    RESTRICTED_MASK,
    govern_result,
    govern_rows_for_caller,
    narrative_withhold_reason,
)

COLUMNS = ["department", "employee_name", "salary"]
ROWS = [
    ("Engineering", "Asha Rao", 120000),
    ("Sales", "Ben Ortiz", 95000),
]


def test_restricted_column_is_masked_for_a_caller_without_permission():
    result = govern_result(COLUMNS, ROWS, {"salary"}, can_view_restricted=False)
    assert [row[2] for row in result.rows] == [RESTRICTED_MASK, RESTRICTED_MASK]
    assert [row[0] for row in result.rows] == ["Engineering", "Sales"]
    assert result.actions == ("masked_restricted_columns:salary",)


def test_restricted_column_is_visible_to_a_caller_with_permission():
    result = govern_result(COLUMNS, ROWS, {"salary"}, can_view_restricted=True)
    assert result.rows == [("Engineering", "Asha Rao", 120000), ("Sales", "Ben Ortiz", 95000)]
    assert result.actions == ()


def test_column_name_matching_is_case_insensitive():
    result = govern_result(["Salary"], [(120000,)], {"salary"}, can_view_restricted=False)
    assert result.rows == [(RESTRICTED_MASK,)]


def test_null_in_a_masked_column_is_masked_too():
    result = govern_result(["salary"], [(None,)], {"salary"}, can_view_restricted=False)
    assert result.rows == [(RESTRICTED_MASK,)]


def test_row_shape_is_preserved_for_tuples_and_lists():
    as_list = govern_result(["a"], [["x"]], set(), can_view_restricted=False)
    as_tuple = govern_result(["a"], [("x",)], set(), can_view_restricted=False)
    assert as_list.rows == [["x"]]
    assert isinstance(as_list.rows[0], list)
    assert as_tuple.rows == [("x",)]
    assert isinstance(as_tuple.rows[0], tuple)


def test_input_rows_are_never_mutated():
    rows = [("Engineering", "Asha Rao", 120000)]
    govern_result(COLUMNS, rows, {"salary"}, can_view_restricted=False)
    assert rows == [("Engineering", "Asha Rao", 120000)]


def test_ordinary_data_passes_through_unchanged_with_no_actions():
    result = govern_result(
        ["department", "headcount"], [("Sales", 14)], set(), can_view_restricted=False
    )
    assert result.rows == [("Sales", 14)]
    assert result.actions == ()
    assert not result.changed


@pytest.mark.parametrize(
    "secret_value",
    [
        "postgresql://app:hunter2@db.internal/prod",
        "Server=db;Database=hr;User Id=sa;password=hunter2",
    ],
)
def test_secret_shaped_text_in_a_cell_is_redacted(secret_value):
    result = govern_result(["note"], [(secret_value,)], set(), can_view_restricted=True)
    assert "hunter2" not in result.rows[0][0]
    assert result.actions == ("redacted_secret_cells:1",)


def test_secret_redaction_counts_cells_without_recording_values():
    rows = [("password=one",), ("password=two",), ("ok",)]
    result = govern_result(["note"], rows, set(), can_view_restricted=True)
    assert result.actions == ("redacted_secret_cells:2",)
    assert all("one" not in action and "two" not in action for action in result.actions)


def test_numeric_and_null_cells_are_never_treated_as_secrets():
    result = govern_result(["n", "x"], [(42, None)], set(), can_view_restricted=True)
    assert result.rows == [(42, None)]
    assert result.actions == ()


def test_live_entry_point_masks_a_classified_column_by_caller_roles(monkeypatch):
    monkeypatch.setattr(
        "governance.result_policy.load_sensitive_columns",
        lambda: {("hr_employee", "salary"): "restricted", ("hr_employee", "dept"): "internal"},
    )
    rows = [("Sales", 95000)]
    columns = ["dept", "salary"]
    assert govern_rows_for_caller(columns, rows, ["viewer"]) == [("Sales", RESTRICTED_MASK)]
    assert govern_rows_for_caller(columns, rows, ["analyst"]) == [("Sales", 95000)]


def test_live_entry_point_leaves_unclassified_columns_alone_with_shipped_config():
    rows = [("Sales", 95000)]
    columns = ["department", "salary"]
    # The shipped config/sensitive_columns.yaml classifies nothing yet.
    assert govern_rows_for_caller(columns, rows, ["viewer"]) == [("Sales", 95000)]


def test_live_entry_point_returns_empty_rows_unchanged():
    assert govern_rows_for_caller(["a"], [], ["viewer"]) == []


# --- narrative_withhold_reason -------------------------------------------------
# Free text derived from a governed result (an insight, a synthesized
# answer) must not outlive the decision that masked the rows it was
# written from. See the function's own docstring.


def test_narrative_is_withheld_from_a_caller_who_may_not_see_the_column():
    reason = narrative_withhold_reason(["employee_name", "annual_salary"], ["viewer"])
    assert reason is not None
    assert "annual_salary" in reason


def test_narrative_is_disclosable_to_a_caller_who_may_see_the_column():
    assert narrative_withhold_reason(["employee_name", "annual_salary"], ["analyst"]) is None


def test_narrative_is_disclosable_when_no_column_is_protected():
    """Over-blocking is its own failure mode: ordinary analytics must
    keep its prose."""
    assert narrative_withhold_reason(["region", "units_sold"], ["viewer"]) is None


def test_no_column_evidence_returns_no_decision():
    """A turn with no stored result carries nothing to classify. Those
    paths are bounded by their own gates, not by this one."""
    assert narrative_withhold_reason([], ["viewer"]) is None


def test_the_reason_names_columns_only_and_never_a_value():
    """The reason is returned to the caller it withheld from -- including
    an anonymous share visitor -- so it must not echo what it protected."""
    reason = narrative_withhold_reason(["patient_name", "diagnosis_code"], ["viewer"])
    assert reason is not None
    assert reason.startswith("derived_from_protected_columns:")
    # Every token after the prefix is one of the column names it was
    # given -- nothing else can have reached the string.
    named = reason.split(":", 1)[1].split(",")
    assert set(named) <= {"patient_name", "diagnosis_code"}


def test_the_general_fallback_is_strict_not_permissive():
    """An unknown domain applies the strictest policy in the registry,
    so an undetected domain can never loosen this."""
    assert (
        narrative_withhold_reason(["annual_salary"], ["viewer"], BusinessDomain.GENERAL) is not None
    )


def test_an_operators_explicit_restricted_entry_is_honoured(monkeypatch):
    """`_classification_masks` does not read
    `config/sensitive_columns.yaml`; layer 1 does, and an explicit human
    classification has to count here exactly as it counts for the rows."""
    monkeypatch.setattr(
        "governance.result_policy.load_sensitive_columns",
        lambda: {("projects", "codename"): "restricted"},
    )
    reason = narrative_withhold_reason(["codename"], ["viewer"])
    assert reason is not None
    assert "codename" in reason
    # And still disclosable to a caller who holds the permission.
    assert narrative_withhold_reason(["codename"], ["analyst"]) is None
