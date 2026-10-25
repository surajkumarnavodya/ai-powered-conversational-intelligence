"""A restricted column's classification must survive being renamed.

UAT-readiness Phase 2, finding `SEC-006`. The result-governance gate
classifies the **returned column names** -- deliberately, because that
catches `SELECT *`, a `UNION`, and any projection that keeps the column's
own name (CLAUDE.md 5.4). What it could not see was a *rename*: a column
returned as `region` classifies as ordinary business data no matter what
it was computed from, so

    SELECT salary AS region FROM employees

returned the salary in the clear to a caller with no
`VIEW_RESTRICTED_COLUMNS` permission. Measured against the shipped
configuration before the fix, **13 of 14** rename and wrapper variants
defeated the gate, `SELECT password_hash AS note` among them -- a
`CREDENTIAL` column, which CLAUDE.md 5.4 says is withheld from *every*
caller including an admin.

`agent.sql_validator.projection_source_columns` supplies the names behind
each output column and `governance.result_policy._classification_masks`
now classifies an output by the strictest of (its own name, every name it
was computed from).

**What this file deliberately does not assert.** An aggregate over a
restricted column is still returned -- `AVG(salary) AS avg_pay` is
ordinary analytics, and over-blocking it is its own failure mode
(CLAUDE.md 5.8). `TestTheAggregateExclusionIsDeliberate` pins that as
intended behaviour and names the one residual it leaves
(`MAX(salary) WHERE id = 7`), so a later session finds a decision rather
than an oversight.
"""

from __future__ import annotations

import pytest

from agent.sql_validator import projection_source_columns
from governance.classification import reset_classification_cache
from governance.domains import BusinessDomain
from governance.result_policy import RESTRICTED_MASK, WITHHELD_MASK, govern_result_for_domain

#: A value distinctive enough that finding it in a governed row is
#: unambiguous evidence it was not protected.
SECRET = 185_000

#: No `VIEW_RESTRICTED_COLUMNS`. `agent.authz` grants that to `analyst`
#: and `admin` only, so an ordinary `user` is the caller this gate exists
#: for.
UNPRIVILEGED_ROLES = ("user",)


@pytest.fixture(autouse=True)
def _clear_classification_cache():
    """`classify_field` is `functools.cache`d on (table, column).

    Cleared around every test so neither the order these run in nor
    another module's rewritten `sensitive_columns.yaml` can decide the
    outcome here.
    """
    reset_classification_cache()
    yield
    reset_classification_cache()


def _govern(sql: str, columns: list[str], roles=UNPRIVILEGED_ROLES, domain=BusinessDomain.HR):
    """Governs one single-row result exactly as the live callers do.

    Mirrors `agent.nodes.execute_sql_node` and `POST /execute`: derive the
    projection sources from the statement that was executed, then pass
    both views into the domain gate.
    """
    sources = projection_source_columns(sql, dialect="postgres")
    return govern_result_for_domain(
        columns,
        [[SECRET] * len(columns)],
        roles,
        domain,
        0,
        sources.by_name,
        sources.in_order,
    )


#: (label, sql, the column names the driver reports back)
#: Every one of these returned `SECRET` in the clear before the fix,
#: except the first, which is the control showing the gate works at all
#: when the name is left alone.
_RENAMES: list[tuple[str, str, list[str]]] = [
    ("no rename at all (control)", "SELECT salary FROM employees", ["salary"]),
    ("aliased to a business term", "SELECT salary AS region FROM employees", ["region"]),
    ("aliased to an abbreviation", "SELECT salary AS sal FROM employees", ["sal"]),
    ("aliased to a positional name", "SELECT salary AS c1 FROM employees", ["c1"]),
    ("wrapped in UPPER", "SELECT UPPER(salary) AS region FROM employees", ["region"]),
    ("arithmetic no-op", "SELECT salary + 0 AS region FROM employees", ["region"]),
    ("cast to text", "SELECT CAST(salary AS VARCHAR) AS region FROM employees", ["region"]),
    (
        "renamed inside a CTE",
        "WITH x AS (SELECT salary AS region FROM employees) SELECT region FROM x",
        ["region"],
    ),
    (
        "one UNION branch carries it",
        "SELECT salary AS region FROM employees UNION ALL SELECT 0 AS region FROM employees",
        ["region"],
    ),
    # No alias at all: the driver invents the output name, so only the
    # positional view reaches this one.
    ("unaliased expression", "SELECT salary * 1 FROM employees", ["?column?"]),
    ("ssn renamed", "SELECT ssn AS code FROM employees", ["code"]),
    ("email renamed", "SELECT email AS label FROM customers", ["label"]),
]


class TestARenamedRestrictedColumnIsStillProtected:
    @pytest.mark.parametrize(("label", "sql", "columns"), _RENAMES, ids=[r[0] for r in _RENAMES])
    def test_the_value_never_reaches_an_unprivileged_caller(
        self, label: str, sql: str, columns: list[str]
    ) -> None:
        governed = _govern(sql, columns)

        assert (
            SECRET not in governed.rows[0]
        ), f"{label}: the restricted value was returned in the clear via {sql!r}"
        assert governed.changed

    @pytest.mark.parametrize(("label", "sql", "columns"), _RENAMES, ids=[r[0] for r in _RENAMES])
    def test_the_masking_is_reported_in_actions(
        self, label: str, sql: str, columns: list[str]
    ) -> None:
        """Protection that is not disclosed is its own defect.

        `GovernedResult.actions` is what the response and the audit event
        are built from, so a value masked without a corresponding action
        would be a silent change to the data the caller is reading.
        """
        governed = _govern(sql, columns)

        assert governed.actions
        assert any("mask" in action or "withh" in action for action in governed.actions)


class TestANeverDisclosedCategoryIsWithheldFromEveryone:
    """`CREDENTIAL`/`PAYMENT` are withheld from every caller, admin included.

    CLAUDE.md 5.4: "no role unlocks them". A rename unlocked them before
    the fix, which is why this gets its own class: the other renames
    escalate from "masked" to "visible", while this one escalates past a
    boundary that is supposed to have no key at all.
    """

    @pytest.mark.parametrize("roles", [("user",), ("analyst",), ("admin",)])
    def test_a_renamed_credential_column_is_withheld_whatever_the_role(self, roles) -> None:
        governed = _govern("SELECT password_hash AS note FROM users", ["note"], roles=roles)

        assert governed.rows[0][0] == WITHHELD_MASK
        # Withheld, not merely masked -- the two mean different things and
        # `governance.result_policy` keeps separate markers for exactly
        # that reason.
        assert governed.rows[0][0] != RESTRICTED_MASK

    def test_an_admin_sees_a_renamed_salary_because_their_role_permits_it(self) -> None:
        """The counter-case, so this file is not just "mask more".

        An `admin` holds `VIEW_RESTRICTED_COLUMNS`, so a renamed
        compensation column is theirs to see. If this ever started
        masking, the fix would have become over-blocking rather than
        correct.
        """
        governed = _govern("SELECT salary AS region FROM employees", ["region"], roles=("admin",))

        assert governed.rows[0][0] == SECRET


class TestOrdinaryQueriesAreUnaffected:
    """The fix must not mask a column merely because it sits in a query.

    `_classification_masks` reads the names a projection *computes from*,
    never every column the statement mentions, so a filter or a join key
    on a restricted column does not taint an unrelated output.
    """

    def test_an_unrelated_output_column_is_untouched(self) -> None:
        governed = _govern(
            "SELECT department AS region FROM employees", ["region"], roles=("user",)
        )

        assert governed.rows[0][0] == SECRET
        assert not governed.changed

    def test_a_restricted_column_in_the_where_clause_does_not_mask_the_output(self) -> None:
        governed = _govern(
            "SELECT department AS region FROM employees WHERE salary > 100000",
            ["region"],
        )

        assert (
            governed.rows[0][0] == SECRET
        ), "a WHERE-clause reference tainted an output column it does not feed"

    def test_a_restricted_join_key_does_not_mask_the_output(self) -> None:
        governed = _govern(
            "SELECT e.department AS region FROM employees e " "JOIN payroll p ON p.ssn = e.ssn",
            ["region"],
        )

        assert governed.rows[0][0] == SECRET

    def test_a_select_star_still_behaves_exactly_as_before(self) -> None:
        """A wildcard preserves base names, so nothing new applies.

        `projection_source_columns` returns an empty positional view for a
        wildcard (it cannot know the expansion width), and the name-based
        classification handles it unchanged.
        """
        sources = projection_source_columns("SELECT * FROM employees", dialect="postgres")

        assert sources.in_order == ()
        assert sources.by_name == {}

        governed = _govern("SELECT * FROM employees", ["department", "salary"])

        assert governed.rows[0][0] == SECRET
        assert governed.rows[0][1] == RESTRICTED_MASK


class TestTheAggregateExclusionIsDeliberate:
    """Pinned as intended behaviour, with its residual named.

    An aggregated projection contributes no source names, so an aggregate
    over a restricted column is returned. That is the decision, not a
    miss: "average pay by department" is the normal case and refusing it
    would be the over-blocking CLAUDE.md 5.8 warns about.
    """

    def test_an_aggregate_contributes_no_source_names(self) -> None:
        sources = projection_source_columns(
            "SELECT department, AVG(salary) AS avg_pay FROM employees GROUP BY department",
            dialect="postgres",
        )

        assert sources.by_name["department"] == frozenset({"department"})
        assert sources.by_name.get("avg_pay", frozenset()) == frozenset()

    def test_an_aggregate_over_one_row_is_the_known_residual(self) -> None:
        """`MAX(salary) WHERE id = 7` still returns the individual value.

        Recorded as an open item rather than fixed here: closing it means
        deciding when an aggregate is too narrow to be an aggregate, which
        is what small-group suppression already exists to decide, and
        extending suppression to this shape is a policy change rather than
        a defect fix.

        This test asserts the *current* behaviour so the decision is
        visible. When the policy question is answered, this is the test to
        change -- and its failure will say so.
        """
        governed = _govern("SELECT MAX(salary) AS region FROM employees WHERE id = 7", ["region"])

        assert governed.rows[0][0] == SECRET


class TestTheHelperDegradesWithoutWeakening:
    """An unresolvable statement must not relax anything.

    `projection_source_columns` only ever *adds* names to classify, so its
    empty result has to be a no-op. That is what makes it safe for it to
    return empty on anything it cannot model.
    """

    def test_unparseable_sql_yields_empty_views_and_changes_nothing(self) -> None:
        sources = projection_source_columns("this is not sql at all", dialect="postgres")

        assert sources.by_name == {}
        assert sources.in_order == ()

    def test_omitting_the_sources_entirely_reproduces_the_old_behaviour(self) -> None:
        """The parameters are additive, which backward compatibility needs.

        `govern_result_for_domain` is called from six surfaces; the two
        new arguments default to `None` and every existing caller keeps
        working unchanged.
        """
        with_sources = _govern("SELECT salary FROM employees", ["salary"])
        without_sources = govern_result_for_domain(
            ["salary"],
            [[SECRET]],
            UNPRIVILEGED_ROLES,
            BusinessDomain.HR,
            0,
        )

        assert with_sources.rows == without_sources.rows == [[RESTRICTED_MASK]]

    def test_a_deeply_nested_statement_does_not_raise_here_either(self) -> None:
        """Same `RecursionError` exposure as `validate_sql` had (`SEC-002`).

        This helper parses too, so it carries the same guard. Without it a
        governance call could crash on a statement the validator had
        already refused to let through -- a different route to the same
        "boundary goes down instead of saying no" failure.
        """
        sources = projection_source_columns(
            "SELECT " + "(" * 1000 + "1" + ")" * 1000, dialect="postgres"
        )

        assert sources.by_name == {}
        assert sources.in_order == ()
