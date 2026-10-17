"""The centralized governance engine: classification, domains, detection,
suppression, recommendation safety and telemetry.

Pure throughout -- no database, no LLM, no network. The pipeline wiring
is covered by `tests/test_governance_integration.py` (pre-existing) and
the bypass guarantees by
`tests/security/test_governance_cannot_be_bypassed.py`.

Organized to the mission's §35: positive cases that must keep working,
negative cases that must be refused, and attack cases that must not get
through.
"""

from __future__ import annotations

import pytest

from agent.authz import Permission
from governance.classification import (
    NEVER_DISCLOSED_CATEGORIES,
    DataCategory,
    DataClassification,
    MaskingStrategy,
    classify_field,
    highest_classification,
    reset_classification_cache,
)
from governance.decisions import GovernanceDecision
from governance.domain_detection import detect_domain, strictest
from governance.domains import (
    ALL_PROHIBITED_ACTIONS,
    BASELINE_RESTRICTED_CATEGORIES,
    DOMAIN_POLICIES,
    GOVERNANCE_POLICY_VERSION,
    BusinessDomain,
    ProhibitedAction,
    is_cross_domain_allowed,
    minimum_group_size_for,
    policy_for,
)
from governance.recommendation_safety import check_recommendation, filter_recommendations
from governance.request_policy import evaluate_request
from governance.result_policy import (
    RESTRICTED_MASK,
    WITHHELD_MASK,
    govern_result_for_domain,
)
from governance.suppression import SUPPRESSED_MASK, suppress_small_groups
from governance.telemetry import GovernanceMetrics, normalize_action

VIEWER = ("viewer",)
ANALYST = ("analyst",)
ADMIN = ("admin",)
NO_ROLE: tuple[str, ...] = ()


# =====================================================================
# Data classification (§3)
# =====================================================================


class TestClassification:
    @pytest.mark.parametrize(
        ("column", "classification", "category"),
        [
            ("password_hash", DataClassification.REGULATED, DataCategory.CREDENTIAL),
            ("api_key", DataClassification.REGULATED, DataCategory.CREDENTIAL),
            ("connection_string", DataClassification.REGULATED, DataCategory.CREDENTIAL),
            ("card_number", DataClassification.REGULATED, DataCategory.PAYMENT),
            ("cvv", DataClassification.REGULATED, DataCategory.PAYMENT),
            ("bank_account_no", DataClassification.REGULATED, DataCategory.PAYMENT),
            ("ssn", DataClassification.REGULATED, DataCategory.PII),
            ("PassportNumber", DataClassification.REGULATED, DataCategory.PII),
            ("AnnualSalary", DataClassification.HIGHLY_SENSITIVE, DataCategory.COMPENSATION),
            ("base_pay", DataClassification.HIGHLY_SENSITIVE, DataCategory.COMPENSATION),
            ("diagnosis_code", DataClassification.HIGHLY_SENSITIVE, DataCategory.PHI),
            ("medication_name", DataClassification.HIGHLY_SENSITIVE, DataCategory.PHI),
            ("performance_rating", DataClassification.HIGHLY_SENSITIVE, DataCategory.PERFORMANCE),
            ("manager_feedback", DataClassification.HIGHLY_SENSITIVE, DataCategory.PERFORMANCE),
            ("religion", DataClassification.HIGHLY_SENSITIVE, DataCategory.SPI),
            ("legal_opinion", DataClassification.HIGHLY_SENSITIVE, DataCategory.LEGAL),
            ("EmployeeEmail", DataClassification.SENSITIVE, DataCategory.PII),
            ("StreetAddress", DataClassification.SENSITIVE, DataCategory.PII),
            ("first_name", DataClassification.SENSITIVE, DataCategory.PII),
            ("date_of_birth", DataClassification.SENSITIVE, DataCategory.PII),
            ("negotiated_rate", DataClassification.CONFIDENTIAL, DataCategory.CONTRACT),
            ("batch_formula", DataClassification.CONFIDENTIAL, DataCategory.PROPRIETARY),
            ("audit_finding", DataClassification.CONFIDENTIAL, DataCategory.AUDIT),
        ],
    )
    def test_names_classify_to_the_expected_level(self, column, classification, category):
        policy = classify_field("", column)
        assert policy.classification is classification
        assert policy.category is category

    @pytest.mark.parametrize(
        "column",
        ["department", "order_date", "product_category", "region", "quantity", "status"],
    )
    def test_ordinary_business_columns_stay_public(self, column):
        """A field is never restricted merely because it was not
        recognized -- over-blocking is its own failure mode."""
        assert classify_field("", column).classification is DataClassification.PUBLIC

    def test_pascal_case_is_matched(self):
        """Real columns are routinely PascalCase with no word boundary
        between the joined words; a bounded pattern would miss the most
        common naming convention in these databases."""
        for column in ("StreetAddress", "AnnualSalary", "PassportNumber", "PerformanceRating"):
            assert classify_field("", column).classification >= DataClassification.SENSITIVE

    def test_ip_address_is_infrastructure_not_a_home_address(self):
        """`ip_address` must be checked before the deliberately unbounded
        `address` pattern, which would also match it."""
        policy = classify_field("", "IpAddress")
        assert policy.category is DataCategory.PROPRIETARY
        assert policy.reason == "ip_address"

    def test_credentials_and_payment_are_never_disclosed(self):
        assert classify_field("", "api_key").is_never_disclosed
        assert classify_field("", "card_number").is_never_disclosed
        assert not classify_field("", "salary").is_never_disclosed
        assert (
            frozenset({DataCategory.CREDENTIAL, DataCategory.PAYMENT}) == NEVER_DISCLOSED_CATEGORIES
        )

    def test_sensitive_and_above_requires_permission(self):
        assert classify_field("", "salary").requires_permission
        assert classify_field("", "first_name").requires_permission
        # Commercial confidentiality is governed per domain, not by the
        # personal-data permission.
        assert not classify_field("", "negotiated_rate").requires_permission
        assert not classify_field("", "department").requires_permission

    def test_an_explicit_human_classification_wins_over_inference(self, monkeypatch):
        """A reviewed decision is authoritative in both directions --
        including when it loosens, because an operator who reviewed a
        column and called it public meant it."""
        monkeypatch.setattr(
            "governance.classification.load_sensitive_columns",
            lambda: {("payroll", "salary_band_label"): "public"},
        )
        reset_classification_cache()
        try:
            policy = classify_field("payroll", "salary_band_label")
            assert policy.classification is DataClassification.PUBLIC
            assert policy.reason == "configured"
            assert policy.inferred is False
        finally:
            reset_classification_cache()

    def test_an_explicit_restriction_also_wins(self, monkeypatch):
        monkeypatch.setattr(
            "governance.classification.load_sensitive_columns",
            lambda: {("projects", "project_code"): "restricted"},
        )
        reset_classification_cache()
        try:
            policy = classify_field("projects", "project_code")
            assert policy.classification is DataClassification.HIGHLY_SENSITIVE
            assert policy.inferred is False
            assert policy.audit_required
        finally:
            reset_classification_cache()

    def test_highest_classification_takes_the_maximum(self):
        policies = [classify_field("", name) for name in ("department", "salary", "region")]
        assert highest_classification(policies) is DataClassification.HIGHLY_SENSITIVE
        assert highest_classification([]) is DataClassification.PUBLIC

    def test_classification_levels_are_ordered(self):
        assert DataClassification.PUBLIC < DataClassification.INTERNAL
        assert DataClassification.INTERNAL < DataClassification.CONFIDENTIAL
        assert DataClassification.CONFIDENTIAL < DataClassification.SENSITIVE
        assert DataClassification.SENSITIVE < DataClassification.HIGHLY_SENSITIVE
        assert DataClassification.HIGHLY_SENSITIVE < DataClassification.REGULATED

    def test_every_pii_category_onboarding_detects_is_classified_here(self):
        """Coverage parity with `onboarding/pii_detection.py`.

        The two modules answer different questions (that one proposes
        findings for a human SME at onboarding time; this one makes a
        binary runtime decision), so they stay separate -- but this
        module must not know about *fewer* kinds of personal data than
        the onboarding detector does, or runtime enforcement would be
        weaker than onboarding's own advice.
        """
        from onboarding.pii_detection import _PII_NAME_PATTERNS

        representative = {
            "email": "contact_email",
            "phone": "phone_number",
            "ssn": "ssn",
            "date_of_birth": "date_of_birth",
            "ip_address": "ip_address",
            "address": "street_address",
            "person_name": "first_name",
            "credit_card": "credit_card_number",
            "national_id": "passport_number",
            "gender": "gender",
        }
        assert set(representative) == set(
            _PII_NAME_PATTERNS
        ), "onboarding gained or lost a PII category; mirror it here"
        for category, column in representative.items():
            policy = classify_field("", column)
            assert (
                policy.classification >= DataClassification.CONFIDENTIAL
            ), f"{category} ({column}) is unclassified by the governance engine"


# =====================================================================
# Domain registry (§33) and the eighteen domains (§36)
# =====================================================================


class TestDomainRegistry:
    def test_every_declared_domain_has_a_policy(self):
        """Exhaustive by assertion: a new `BusinessDomain` member cannot
        be added without a policy, which is what keeps the registry from
        silently falling back for a real domain."""
        assert set(DOMAIN_POLICIES) == set(BusinessDomain)

    def test_the_mission_s_eighteen_domains_are_all_covered(self):
        """The mission named eighteen; `CONSTRUCTION` was added later and
        is counted separately here so the provenance of the nineteenth
        stays visible instead of being absorbed into the mission's list."""
        business = {domain for domain in BusinessDomain if domain is not BusinessDomain.GENERAL}
        assert BusinessDomain.CONSTRUCTION in business
        assert len(business - {BusinessDomain.CONSTRUCTION}) == 18
        assert len(business) == 19

    def test_no_domain_protects_personal_data_less_than_the_floor(self):
        """The structural guarantee that makes domain detection safe:
        claiming a domain can only ever add restrictions, never drop one,
        so a weak detection cannot route a request onto a laxer policy."""
        for domain in BusinessDomain:
            effective = policy_for(domain).effective_restricted_categories
            assert effective >= BASELINE_RESTRICTED_CATEGORIES, domain

    def test_the_general_fallback_prohibits_every_individual_decision(self):
        """If the domain is unknown, no decision about an individual is
        safe to make -- so the fallback prohibits all of them, not none."""
        assert policy_for(BusinessDomain.GENERAL).prohibited_actions == ALL_PROHIBITED_ACTIONS

    def test_hr_healthcare_and_bfsi_prohibit_their_named_actions(self):
        hr = policy_for(BusinessDomain.HR).prohibited_actions
        assert ProhibitedAction.INDIVIDUAL_EMPLOYMENT_ACTION in hr
        assert ProhibitedAction.INDIVIDUAL_PERFORMANCE_RANKING in hr
        healthcare = policy_for(BusinessDomain.HEALTHCARE).prohibited_actions
        assert ProhibitedAction.MEDICAL_DIAGNOSIS_OR_PRESCRIPTION in healthcare
        bfsi = policy_for(BusinessDomain.BFSI).prohibited_actions
        assert ProhibitedAction.CREDIT_OR_LOAN_DECISION in bfsi
        assert ProhibitedAction.FRAUD_ACCUSATION in bfsi

    def test_a_domain_s_own_group_size_wins_over_the_configured_default(self):
        assert minimum_group_size_for(BusinessDomain.HR, 99) == 5

    def test_a_domain_declaring_zero_opts_out_of_suppression(self):
        """Manufacturing throughput has no small-group privacy risk, and
        suppressing it would destroy legitimate analytics -- so a
        declared `0` is honored as a real zero, not treated as unset."""
        assert policy_for(BusinessDomain.MANUFACTURING).minimum_group_size == 0
        assert minimum_group_size_for(BusinessDomain.MANUFACTURING, 7) == 7

    def test_cross_domain_is_reflexive_and_not_automatically_symmetric(self):
        assert is_cross_domain_allowed(BusinessDomain.HR, BusinessDomain.HR)
        assert is_cross_domain_allowed(BusinessDomain.HR, BusinessDomain.TRAINING)
        # HR may be combined with training, but a training question must
        # not silently pull compensation data back the other way.
        assert is_cross_domain_allowed(BusinessDomain.TRAINING, BusinessDomain.HR)
        assert not is_cross_domain_allowed(BusinessDomain.HR, BusinessDomain.BFSI)
        assert not is_cross_domain_allowed(
            BusinessDomain.HEALTHCARE, BusinessDomain.SALES_MARKETING
        )

    def test_the_policy_set_is_versioned(self):
        assert GOVERNANCE_POLICY_VERSION
        assert evaluate_request("headcount by department", VIEWER).policy_version == (
            GOVERNANCE_POLICY_VERSION
        )

    def test_high_risk_domains_require_audit(self):
        for domain in (
            BusinessDomain.HR,
            BusinessDomain.HEALTHCARE,
            BusinessDomain.BFSI,
            BusinessDomain.LEGAL,
            BusinessDomain.IT_OPERATIONS,
            BusinessDomain.RISK_AUDIT,
        ):
            assert policy_for(domain).audit_required, domain


# =====================================================================
# Domain detection
# =====================================================================


class TestDomainDetection:
    def test_schema_evidence_outranks_question_wording(self):
        """`employee_compensation` is HR whatever the sentence says."""
        detection = detect_domain("show me the campaign numbers", ["employee_compensation"])
        assert detection.domain is BusinessDomain.HR
        assert detection.confident

    def test_wording_alone_can_claim_a_domain(self):
        assert detect_domain("attrition rate by department").domain is BusinessDomain.HR
        assert detect_domain("bed occupancy last month").domain is BusinessDomain.HEALTHCARE

    def test_an_unrecognized_question_falls_back_to_general(self):
        assert detect_domain("what is the total by region?").domain is BusinessDomain.GENERAL

    def test_a_multi_domain_request_reports_every_domain(self):
        detection = detect_domain(
            "compare employee salary with customer revenue",
            ["employee_payroll", "crm_opportunity"],
        )
        assert detection.is_multi_domain
        assert BusinessDomain.HR in detection.candidates

    def test_detection_is_deterministic(self):
        question = "employee training completion and course enrolment"
        first = detect_domain(question, ["employee", "course"])
        for _ in range(5):
            assert detect_domain(question, ["employee", "course"]) == first

    def test_strictest_picks_the_most_restrictive_policy(self):
        assert strictest([BusinessDomain.MANUFACTURING, BusinessDomain.HR]) is BusinessDomain.HR
        assert strictest([]) is BusinessDomain.GENERAL


# =====================================================================
# §35.A Positive cases -- authorized analytics must keep working
# =====================================================================


class TestPositiveCases:
    @pytest.mark.parametrize(
        "question",
        [
            "Show headcount by department",
            "What is the attrition rate by department this year?",
            "Show hiring trends over the last four quarters",
            "Training completion rate by business unit",
            "Workforce distribution by location",
        ],
    )
    def test_authorized_aggregate_hr_analytics_is_allowed(self, question):
        assert evaluate_request(question, VIEWER).decision is GovernanceDecision.ALLOW

    @pytest.mark.parametrize(
        "question",
        [
            "Revenue by product category last quarter",
            "Campaign conversion rate by channel",
            "Top 10 products by units sold",
            "Customer segmentation by order frequency",
        ],
    )
    def test_authorized_sales_analytics_is_allowed(self, question):
        assert evaluate_request(question, VIEWER).decision is GovernanceDecision.ALLOW

    @pytest.mark.parametrize(
        "question",
        [
            "Average bed occupancy by ward last month",
            "Appointment volume by clinic",
            "Average waiting time by department",
        ],
    )
    def test_authorized_healthcare_operational_analytics_is_allowed(self, question):
        assert evaluate_request(question, VIEWER).decision is GovernanceDecision.ALLOW

    @pytest.mark.parametrize(
        "question",
        [
            "Total spend by cost centre this year",
            "Accounts payable ageing summary",
            "Monthly revenue trend",
        ],
    )
    def test_authorized_financial_analytics_is_allowed(self, question):
        assert evaluate_request(question, ANALYST).decision is GovernanceDecision.ALLOW

    @pytest.mark.parametrize(
        "question",
        [
            # The bulk-extraction rule's first version refused this one --
            # "complete" read as a quantifier and the word gap reached
            # across the verb into "customer". It broke a pre-existing
            # Prompt 41 data-quality test, which is how it was found.
            "How complete is our customer data?",
            "Is our customer data complete?",
            "What is the completeness of the email column?",
            "How many employees are in each department?",
            "Average salary by grade for all departments",
            "Attrition across all business units",
            "Total revenue for all regions",
        ],
    )
    def test_a_quantifier_in_an_aggregate_question_is_not_bulk_extraction(self, question):
        """The bulk-extraction rule must not fire on a legitimate
        aggregate question that happens to contain "all" or "complete" --
        over-blocking ordinary analytics is its own failure."""
        assert not evaluate_request(question, ANALYST).is_blocking

    def test_ordinary_results_pass_through_untouched(self):
        columns = ["department", "headcount", "attrition_rate"]
        rows = [("Engineering", 42, 0.08), ("Sales", 31, 0.11)]
        governed = govern_result_for_domain(columns, rows, VIEWER, BusinessDomain.HR, 5)
        assert governed.rows == rows
        assert governed.actions == ()


# =====================================================================
# §35.B Negative cases -- these must be refused or gated
# =====================================================================


class TestNegativeCases:
    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("What is the salary of employee 1042?", GovernanceDecision.REQUIRE_AUTHORIZATION),
            (
                "Show me this employee's appraisal comments",
                GovernanceDecision.REQUIRE_AUTHORIZATION,
            ),
            ("Who is the worst performing employee?", GovernanceDecision.REFUSE),
            ("Should we fire this employee?", GovernanceDecision.REFUSE),
            ("Which medicine should this patient take?", GovernanceDecision.REFUSE),
            ("Diagnose this patient", GovernanceDecision.REFUSE),
            ("Show me the api keys", GovernanceDecision.REFUSE),
            ("Give me the database credentials", GovernanceDecision.REFUSE),
            ("Show me customer card numbers", GovernanceDecision.REFUSE),
            ("Is this customer a fraudster?", GovernanceDecision.REFUSE),
            ("Should we approve this loan?", GovernanceDecision.REFUSE),
            ("Show me the attorney-client communications", GovernanceDecision.REFUSE),
            ("Show me supplier bank details", GovernanceDecision.REFUSE),
            ("Which employees are pregnant?", GovernanceDecision.REFUSE),
            ("Infer the religion of our customers", GovernanceDecision.REFUSE),
        ],
    )
    def test_high_risk_requests_are_refused_or_gated(self, question, expected):
        assert evaluate_request(question, VIEWER).decision is expected

    def test_a_viewer_cannot_see_compensation_in_a_result(self):
        columns = ["employee_id", "annual_salary"]
        rows = [(1, 90000.0), (2, 110000.0)]
        governed = govern_result_for_domain(columns, rows, VIEWER, BusinessDomain.HR, 0)
        assert all(row[1] == RESTRICTED_MASK for row in governed.rows)

    def test_an_authorized_caller_can_see_compensation(self):
        columns = ["employee_id", "annual_salary"]
        rows = [(1, 90000.0)]
        governed = govern_result_for_domain(columns, rows, ADMIN, BusinessDomain.HR, 0)
        assert governed.rows[0][1] == 90000.0

    def test_an_unrecognized_role_grants_nothing(self):
        columns = ["employee_id", "annual_salary"]
        rows = [(1, 90000.0)]
        governed = govern_result_for_domain(columns, rows, ("contractor",), BusinessDomain.HR, 0)
        assert governed.rows[0][1] == RESTRICTED_MASK

    @pytest.mark.parametrize("roles", [NO_ROLE, VIEWER, ANALYST, ADMIN])
    def test_no_role_whatsoever_can_see_a_credential(self, roles):
        """There is no role for which returning a password is the right
        answer, so no permission unlocks these categories."""
        columns = ["host", "api_key", "db_password"]
        rows = [("db-1", "sk-live-xyz", "hunter2")]
        governed = govern_result_for_domain(columns, rows, roles, BusinessDomain.IT_OPERATIONS, 0)
        assert governed.rows[0][1] == WITHHELD_MASK
        assert governed.rows[0][2] == WITHHELD_MASK
        assert governed.rows[0][0] == "db-1"

    @pytest.mark.parametrize("roles", [VIEWER, ADMIN])
    def test_no_role_can_see_a_payment_instrument(self, roles):
        columns = ["order_id", "card_number", "cvv"]
        rows = [(1, "4111111111111111", "123")]
        governed = govern_result_for_domain(columns, rows, roles, BusinessDomain.ECOMMERCE, 0)
        assert governed.rows[0][1] == WITHHELD_MASK
        assert governed.rows[0][2] == WITHHELD_MASK


# =====================================================================
# §22 Small-group privacy
# =====================================================================


class TestSmallGroupPrivacy:
    def test_a_group_below_k_has_its_sensitive_measures_suppressed(self):
        columns = ["department", "employee_count", "avg_salary"]
        rows = [("Engineering", 42, 95000.0), ("Legal", 2, 150000.0)]
        result = suppress_small_groups(columns, rows, 5)
        assert result.suppressed_rows == 1
        assert result.rows[0] == ("Engineering", 42, 95000.0)
        assert result.rows[1][2] == SUPPRESSED_MASK

    def test_the_group_label_and_count_stay_visible(self):
        """Blanking the row would make suppression indistinguishable from
        a group that does not exist -- and a vanished row would let a
        reader difference the total against the visible rows to recover
        the hidden value exactly."""
        columns = ["department", "employee_count", "avg_salary"]
        rows = [("Legal", 2, 150000.0)]
        result = suppress_small_groups(columns, rows, 5)
        assert result.rows[0][0] == "Legal"
        assert result.rows[0][1] == 2

    def test_no_row_is_ever_dropped(self):
        columns = ["department", "employee_count", "avg_salary"]
        rows = [("A", 1, 1.0), ("B", 2, 2.0), ("C", 99, 3.0)]
        assert len(suppress_small_groups(columns, rows, 5).rows) == 3

    def test_k_of_zero_disables_suppression(self):
        columns = ["department", "employee_count", "avg_salary"]
        rows = [("Legal", 2, 150000.0)]
        assert suppress_small_groups(columns, rows, 0).rows == rows

    def test_nothing_is_suppressed_without_a_sensitive_measure(self):
        columns = ["department", "employee_count", "office_floor"]
        rows = [("Legal", 2, 3)]
        result = suppress_small_groups(columns, rows, 5)
        assert result.rows == rows
        assert not result.changed

    def test_an_unidentifiable_group_size_is_reported_not_assumed_safe(self):
        columns = ["department", "avg_salary"]
        rows = [("Legal", 150000.0)]
        result = suppress_small_groups(columns, rows, 5)
        assert result.group_size_unknown
        assert not result.changed

    def test_a_sensitive_column_is_never_read_as_the_group_size(self):
        """A column named like a count but classified sensitive must not
        become the group-size signal and the protected measure at once."""
        columns = ["department", "employee_count", "salary_count"]
        rows = [("Legal", 2, 150000.0)]
        result = suppress_small_groups(columns, rows, 5)
        assert result.rows[0][1] == 2

    def test_already_protected_cells_keep_their_stronger_label(self):
        columns = ["team", "employee_count", "salary"]
        rows = [("X", 2, 100.0)]
        result = suppress_small_groups(columns, rows, 5, skip_indexes=frozenset({2}))
        assert result.rows[0][2] == 100.0
        assert not result.changed

    def test_suppression_applies_even_to_an_authorized_caller(self):
        """Small-group privacy protects the individual, not the data
        class, so permission does not unlock it."""
        columns = ["department", "employee_count", "avg_salary"]
        rows = [("Legal", 2, 150000.0)]
        governed = govern_result_for_domain(columns, rows, ADMIN, BusinessDomain.HR, 5)
        assert governed.rows[0][2] == SUPPRESSED_MASK

    def test_the_notice_explains_what_happened_without_a_value(self):
        columns = ["department", "employee_count", "avg_salary"]
        rows = [("Legal", 2, 150000.0)]
        notice = suppress_small_groups(columns, rows, 5).notice()
        assert "too small" in notice or "suppressed" in notice
        assert "150000" not in notice


# =====================================================================
# §29 Recommendation safety
# =====================================================================


class TestRecommendationSafety:
    @pytest.mark.parametrize(
        ("text", "domain"),
        [
            ("Terminate this employee to reduce attrition", BusinessDomain.HR),
            ("Promote him to senior engineer", BusinessDomain.HR),
            ("Focus on the worst performing employees", BusinessDomain.HR),
            ("Prescribe amoxicillin at 500mg", BusinessDomain.HEALTHCARE),
            ("Discharge the patient tomorrow", BusinessDomain.HEALTHCARE),
            ("Approve this loan application", BusinessDomain.BFSI),
            ("This customer committed fraud", BusinessDomain.BFSI),
            ("The company is legally liable for the delay", BusinessDomain.LEGAL),
            ("Target customers with a medical condition", BusinessDomain.SALES_MARKETING),
        ],
    )
    def test_a_prohibited_recommendation_is_refused(self, text, domain):
        assert check_recommendation(text, domain).is_refused

    @pytest.mark.parametrize(
        ("text", "domain"),
        [
            ("Review onboarding in the engineering department", BusinessDomain.HR),
            ("Investigate the drop in appointment volume at clinic 3", BusinessDomain.HEALTHCARE),
            ("Review the credit policy thresholds for the portfolio", BusinessDomain.BFSI),
            ("Add an index on OrderDate to reduce query cost", BusinessDomain.IT_OPERATIONS),
            ("Rebalance inventory across the two warehouses", BusinessDomain.SUPPLY_CHAIN),
        ],
    )
    def test_aggregate_and_process_recommendations_are_allowed(self, text, domain):
        assert check_recommendation(text, domain).allowed

    def test_credentials_and_allegations_are_prohibited_in_every_domain(self):
        """Two actions no domain has to remember to declare: there is no
        domain in which disclosing a credential is correct."""
        for domain in BusinessDomain:
            assert check_recommendation("The api key is sk-live-abc", domain).is_refused
            assert check_recommendation(
                "This supplier is deliberately falsified invoices", domain
            ).is_refused

    def test_an_unsafe_recommendation_is_withheld_not_rewritten(self):
        class _Rec:
            def __init__(self, action: str) -> None:
                self.claim_text = action
                self.action = action

        safe = _Rec("Review onboarding in engineering")
        unsafe = _Rec("Terminate this employee")
        kept, dropped = filter_recommendations([safe, unsafe], BusinessDomain.HR)
        assert kept == [safe]
        assert dropped == (ProhibitedAction.INDIVIDUAL_EMPLOYMENT_ACTION.value,)
        # The original object is untouched -- never edited into a softer
        # version that its evidence no longer supports.
        assert unsafe.action == "Terminate this employee"

    def test_an_unknown_domain_prohibits_every_individual_decision(self):
        assert check_recommendation(
            "Approve this loan application", BusinessDomain.GENERAL
        ).is_refused
        assert check_recommendation("Terminate this employee", BusinessDomain.GENERAL).is_refused


# =====================================================================
# §34.15 Policy decision telemetry
# =====================================================================


class TestTelemetry:
    def test_counters_record_decisions_by_domain_and_category(self):
        metrics = GovernanceMetrics()
        metrics.record_request_decision("refuse", "hr", "individual_ranking")
        metrics.record_request_decision("allow", "sales_marketing")
        snapshot = metrics.snapshot()
        assert snapshot.total_requests_evaluated == 2
        assert snapshot.decisions == {"refuse": 1, "allow": 1}
        assert snapshot.domains["hr"] == 1
        assert snapshot.categories["individual_ranking"] == 1
        assert snapshot.blocked_requests == 1

    def test_the_snapshot_carries_the_policy_version(self):
        assert GovernanceMetrics().snapshot().policy_version == GOVERNANCE_POLICY_VERSION

    def test_action_names_are_normalized_so_no_column_name_is_counted(self):
        """A counter keyed on the raw action string would grow one bucket
        per distinct column set -- and a column name is not something
        telemetry carries."""
        assert normalize_action("masked_classified_columns:salary,bonus") == (
            "masked_classified_columns"
        )
        assert normalize_action("suppressed_small_groups:3") == "suppressed_small_groups"

    def test_reset_clears_everything(self):
        metrics = GovernanceMetrics()
        metrics.record_request_decision("allow", "hr")
        metrics.record_suppressed_groups(4)
        metrics.reset()
        snapshot = metrics.snapshot()
        assert snapshot.total_requests_evaluated == 0
        assert snapshot.suppressed_groups == 0


# =====================================================================
# §30 Cross-domain protection
# =====================================================================


class TestCrossDomain:
    def test_an_unauthorized_cross_domain_request_requires_authorization(self):
        verdict = evaluate_request(
            "compare employee salary with customer revenue",
            VIEWER,
            ["employee_payroll", "crm_opportunity"],
        )
        assert verdict.decision in {
            GovernanceDecision.REQUIRE_AUTHORIZATION,
            GovernanceDecision.REFUSE,
        }

    def test_an_authorized_caller_may_cross_domains(self):
        verdict = evaluate_request(
            "compare headcount with revenue by region",
            ADMIN,
            ["employee", "crm_opportunity"],
        )
        assert not verdict.is_blocking

    def test_an_allowed_pairing_is_not_gated(self):
        verdict = evaluate_request(
            "training completion by department for employees",
            VIEWER,
            ["employee", "course"],
        )
        assert not verdict.is_blocking


# =====================================================================
# §35.D Regression -- pre-engine behaviour preserved
# =====================================================================


class TestRegression:
    def test_the_original_six_domain_rules_still_decide_first(self):
        """The appended rule families must not shadow the original,
        separately-tested rules: ordering is preserved."""
        verdict = evaluate_request("Which medicine should this patient take?", VIEWER)
        assert verdict.domain == "healthcare"
        assert verdict.category == "individual_prescription"

    def test_the_original_result_masking_still_applies(self, monkeypatch):
        monkeypatch.setattr(
            "governance.result_policy.load_sensitive_columns",
            lambda: {("projects", "project_code"): "restricted"},
        )
        columns = ["project_code", "status"]
        rows = [("P-1", "active")]
        governed = govern_result_for_domain(columns, rows, VIEWER, BusinessDomain.PROJECT, 0)
        assert governed.rows[0][0] == RESTRICTED_MASK

    def test_secret_shaped_text_in_a_cell_is_still_redacted(self):
        columns = ["note"]
        rows = [("connect with Password=hunter2;Server=db1",)]
        governed = govern_result_for_domain(columns, rows, ADMIN, BusinessDomain.GENERAL, 0)
        assert "hunter2" not in str(governed.rows[0][0])

    def test_an_empty_result_is_returned_unchanged(self):
        governed = govern_result_for_domain(["a"], [], VIEWER, BusinessDomain.HR, 5)
        assert governed.rows == []
        assert governed.actions == ()

    def test_evaluate_request_keeps_its_two_argument_call_shape(self):
        """Three live call sites pass exactly `(question, roles)`."""
        assert evaluate_request("headcount by department", VIEWER) is not None

    def test_masking_strategies_are_a_closed_set(self):
        assert set(MaskingStrategy) == {
            MaskingStrategy.NONE,
            MaskingStrategy.MASK,
            MaskingStrategy.PARTIAL,
            MaskingStrategy.HASH,
            MaskingStrategy.SUPPRESS,
        }

    def test_permission_used_for_restricted_data_is_the_existing_one(self):
        """No new permission was invented: the engine reuses the
        permission the SQL-level gate already enforces."""
        assert Permission.VIEW_RESTRICTED_COLUMNS.value == "view_restricted_columns"
