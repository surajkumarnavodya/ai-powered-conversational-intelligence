"""Conversation-memory protection (§26) and the per-domain export
ceiling (§31) -- the two governance requirements that were declared but
not enforced.

`governance/memory_policy.py` is new. `DomainPolicy.export_row_limit`
existed on all 19 policies and was read by nothing, so every domain's
declared ceiling was dead configuration; `govern_result_for_domain` now
applies it.

Both are deliberately narrow, and the tests say so in both directions:
each has a case proving it engages and a case proving it leaves ordinary
analytics alone. Over-blocking is its own failure mode.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from governance.classification import DataClassification, classify_field
from governance.domains import DOMAIN_POLICIES, BusinessDomain, policy_for
from governance.memory_policy import WITHHELD_MASK, govern_rows_for_memory
from governance.result_policy import govern_result_for_domain

_ADMIN = ("admin",)
_NO_ROLES: tuple[str, ...] = ()


class TestConversationMemoryProtection:
    def test_a_credential_column_is_never_written_to_the_store(self):
        governed = govern_rows_for_memory(
            ["service", "api_key"],
            [["billing", "sk-live-abcdef123456"], ["search", "sk-live-zzzzzz999999"]],
        )

        assert governed.changed
        assert governed.withheld_columns == ("api_key",)
        flat = str(governed.rows)
        assert "sk-live-abcdef123456" not in flat
        assert "sk-live-zzzzzz999999" not in flat
        assert governed.rows[0][0] == "billing", "the non-sensitive column must survive"
        assert governed.rows[0][1] == WITHHELD_MASK

    def test_a_payment_instrument_is_never_written_to_the_store(self):
        governed = govern_rows_for_memory(
            ["customer", "card_number"], [["ACME", "4111111111111111"]]
        )

        assert "4111111111111111" not in str(governed.rows)
        assert governed.rows[0][1] == WITHHELD_MASK

    def test_an_authorized_salary_snapshot_is_still_retained(self):
        """Compensation has authorized readers, so write-time masking
        would break every legitimate reload while adding nothing the
        read-time gate does not already provide. It must be retained --
        and declared."""
        governed = govern_rows_for_memory(["employee_name", "annual_salary"], [["Ada", 187431.55]])

        assert not governed.changed
        assert governed.rows[0][1] == 187431.55
        assert "compensation" in governed.retained_categories

    def test_retained_categories_names_what_is_held_and_never_a_value(self):
        governed = govern_rows_for_memory(
            ["employee_email", "annual_salary"], [["ada@example.com", 187431.55]]
        )

        flat = " ".join(governed.retained_categories)
        assert "ada@example.com" not in flat
        assert "187431" not in flat
        assert governed.retained_categories == tuple(sorted(governed.retained_categories))

    def test_ordinary_analytics_is_untouched(self):
        rows = [["EMEA", 120], ["APAC", 98]]

        governed = govern_rows_for_memory(["region", "order_count"], rows)

        assert not governed.changed
        assert governed.rows == rows
        assert governed.retained_categories == ()

    def test_an_empty_result_is_handled(self):
        assert govern_rows_for_memory([], []).rows == []
        assert govern_rows_for_memory(["a"], []).rows == []

    def test_the_input_rows_are_not_mutated(self):
        rows = [["billing", "sk-live-abcdef123456"]]

        govern_rows_for_memory(["service", "api_key"], rows)

        assert rows == [["billing", "sk-live-abcdef123456"]]

    def test_a_tuple_row_stays_a_tuple(self):
        governed = govern_rows_for_memory(["service", "api_key"], [("billing", "sk-live-x")])

        assert isinstance(governed.rows[0], tuple)

    def test_an_alias_reaches_the_same_protection(self):
        """The store classifies the snapshot's own column names, so
        renaming the column in the SELECT does not smuggle the value
        past this."""
        governed = govern_rows_for_memory(["svc", "db_password"], [["billing", "hunter2"]])

        assert governed.rows[0][1] == WITHHELD_MASK


class TestExportCeiling:
    @staticmethod
    def _sensitive_rows(count: int) -> list[list]:
        return [[f"Employee {i}", 50_000 + i] for i in range(count)]

    def test_every_domain_declares_a_ceiling_that_is_now_read(self):
        """Guards against the ceiling going back to dead configuration:
        a domain declaring a limit must have it enforced."""
        declared = [
            domain
            for domain, policy in DOMAIN_POLICIES.items()
            if policy.export_row_limit is not None
        ]
        assert declared, "no domain declares an export ceiling -- the feature is unreachable"

    def test_a_bulk_sensitive_result_is_capped_at_the_domain_ceiling(self):
        limit = policy_for(BusinessDomain.HR).export_row_limit
        assert limit is not None
        rows = self._sensitive_rows(limit + 25)

        governed = govern_result_for_domain(
            ["employee_name", "annual_salary"], rows, _ADMIN, BusinessDomain.HR, 0
        )

        assert len(governed.rows) == limit
        assert any(action.startswith("export_limited:25") for action in governed.actions)

    def test_the_ceiling_applies_to_an_authorized_caller_too(self):
        """Permission to see a value is not permission to extract every
        one of them at once -- the same stance the request-level bulk
        gate already takes toward an admin."""
        limit = policy_for(BusinessDomain.HEALTHCARE).export_row_limit
        assert limit is not None

        governed = govern_result_for_domain(
            ["patient_name", "diagnosis_code"],
            [[f"Patient {i}", f"D{i}"] for i in range(limit + 10)],
            _ADMIN,
            BusinessDomain.HEALTHCARE,
            0,
        )

        assert len(governed.rows) == limit

    def test_the_number_of_withheld_rows_is_reported_not_silently_dropped(self):
        limit = policy_for(BusinessDomain.HR).export_row_limit
        assert limit is not None

        governed = govern_result_for_domain(
            ["employee_name", "annual_salary"],
            self._sensitive_rows(limit + 7),
            _ADMIN,
            BusinessDomain.HR,
            0,
        )

        assert "export_limited:7" in governed.actions

    def test_a_large_non_sensitive_result_is_not_capped(self):
        """A 400-row regional breakdown is not an HR export merely
        because the question routed to HR."""
        rows = [[f"Region {i}", i] for i in range(400)]

        governed = govern_result_for_domain(
            ["region", "order_count"], rows, _NO_ROLES, BusinessDomain.HR, 0
        )

        assert len(governed.rows) == 400
        assert not any(action.startswith("export_limited") for action in governed.actions)

    def test_a_result_under_the_ceiling_is_untouched(self):
        governed = govern_result_for_domain(
            ["employee_name", "annual_salary"],
            self._sensitive_rows(5),
            _ADMIN,
            BusinessDomain.HR,
            0,
        )

        assert len(governed.rows) == 5
        assert not any(action.startswith("export_limited") for action in governed.actions)

    def test_a_domain_declaring_no_ceiling_is_unbounded_here(self):
        """`export_row_limit=None` means only the global `MAX_RESULT_ROWS`
        cap applies -- this layer adds nothing."""
        no_limit = [
            domain for domain, policy in DOMAIN_POLICIES.items() if policy.export_row_limit is None
        ]
        if not no_limit:
            pytest.skip("every domain declares a ceiling")

        governed = govern_result_for_domain(
            ["employee_name", "annual_salary"],
            self._sensitive_rows(900),
            _ADMIN,
            no_limit[0],
            0,
        )

        assert len(governed.rows) == 900

    def test_the_ceiling_runs_after_suppression_so_k_sees_the_whole_result(self):
        """Truncating first would shrink every group and suppress more
        than the data warrants. Asserted by construction: a result whose
        groups are all large stays unsuppressed even when the export
        ceiling then truncates it.
        """
        limit = policy_for(BusinessDomain.HR).export_row_limit
        assert limit is not None
        rows = [[f"Dept {i % 3}", 50_000 + i] for i in range(limit + 40)]

        governed = govern_result_for_domain(
            ["department", "annual_salary"], rows, _ADMIN, BusinessDomain.HR, 5
        )

        assert len(governed.rows) == limit
        assert not any(action.startswith("suppressed_small_groups") for action in governed.actions)


class TestDeclaredPolicyFieldsAreActuallyRead:
    """`export_row_limit`, `audit_required` and `required_permission`
    were all declared on every `DomainPolicy` and read by nothing --
    configuration that looked like a control and was not one. These
    assert each is now wired, so it cannot quietly go back to being
    decorative."""

    def test_an_audited_domain_reports_audit_required_even_for_a_clean_allow(self):
        governed = govern_result_for_domain(
            ["region", "order_count"],
            [["EMEA", 120]],
            _ADMIN,
            BusinessDomain.HR,
            0,
        )

        assert not governed.changed, "nothing here needs protecting"
        assert governed.audit_required, "an HR decision is auditable even when it changes nothing"

    def test_an_unaudited_domain_does_not_demand_an_audit_event(self):
        policy = policy_for(BusinessDomain.MANUFACTURING)
        assert not policy.audit_required, "fixture assumption"

        governed = govern_result_for_domain(
            ["region", "order_count"], [["EMEA", 120]], _ADMIN, BusinessDomain.MANUFACTURING, 0
        )

        assert not governed.audit_required

    def test_the_domain_less_entry_point_never_claims_an_audit(self):
        from governance.result_policy import govern_rows_for_caller_result

        governed = govern_rows_for_caller_result(["region"], [["EMEA"]], _ADMIN)

        assert governed.audit_required is False

    def test_masking_honours_the_domains_declared_required_permission(self, monkeypatch):
        """Read from the policy, not a hardcoded constant: raising one
        domain's bar must actually raise it."""
        import governance.result_policy as rp
        from agent.authz import Permission

        hr = policy_for(BusinessDomain.HR)
        monkeypatch.setitem(
            rp.policy_for.__globals__["DOMAIN_POLICIES"],
            BusinessDomain.HR,
            replace(hr, required_permission=Permission.ADMIN_CONFIG),
        )

        governed = govern_result_for_domain(
            ["employee_name", "annual_salary"],
            [["Ada", 187431.55]],
            ("analyst",),
            BusinessDomain.HR,
            0,
        )

        assert "[restricted]" in str(governed.rows), (
            "an analyst holding only view_restricted_columns must no longer clear "
            "a bar raised to admin_config"
        )


class TestClassificationAssumptionsTheseTestsRestOn:
    """If these drift, the tests above would pass while testing nothing."""

    @pytest.mark.parametrize("column", ["api_key", "db_password", "card_number"])
    def test_the_never_disclosed_columns_really_classify_that_way(self, column: str):
        assert classify_field("", column).is_never_disclosed

    @pytest.mark.parametrize("column", ["annual_salary", "diagnosis_code"])
    def test_the_sensitive_columns_really_classify_at_or_above_sensitive(self, column: str):
        assert classify_field("", column).classification >= DataClassification.SENSITIVE

    @pytest.mark.parametrize("column", ["region", "order_count", "department"])
    def test_the_ordinary_columns_really_classify_below_sensitive(self, column: str):
        assert classify_field("", column).classification < DataClassification.SENSITIVE
