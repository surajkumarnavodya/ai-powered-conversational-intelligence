"""Schema-level tests for `AskRequest.sources` and the router wiring -- Batch 3.

Covers what `api/schemas.py` accepts and rejects, and that the selection
reaches `router_node` and comes back out as a route decision. The filter's
own security property is tested in `tests/test_source_selection.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.orchestrator.nodes import router_node
from api.schemas import AskRequest, AskResponse
from security.secrets import SecretStr
from sources.registry import all_names
from tests.test_orchestrator import _settings


def _ask(**overrides) -> AskRequest:
    return AskRequest(question="how many orders?", **overrides)


class TestSchemaValidation:
    def test_omitting_sources_is_the_default(self):
        assert _ask().sources is None

    @pytest.mark.parametrize("name", sorted(all_names()))
    def test_every_registered_name_is_accepted(self, name):
        assert _ask(sources=[name]).sources == [name]

    def test_an_unknown_name_is_rejected(self):
        with pytest.raises(ValidationError) as exc:
            _ask(sources=["sharepoint"])
        assert "Unknown source" in str(exc.value)

    def test_the_rejection_names_the_valid_options(self):
        """An unknown *name* is a client bug worth being explicit about.
        This leaks no configuration or permission state -- every registered
        name is already public via `GET /sources`."""
        with pytest.raises(ValidationError) as exc:
            _ask(sources=["nope"])
        assert "sql" in str(exc.value)

    def test_an_empty_list_is_rejected_rather_than_meaning_nothing(self):
        """`[]` would be ambiguous between "no restriction" and "no
        sources at all", the latter making the question unanswerable.
        Callers must omit the field instead."""
        with pytest.raises(ValidationError):
            _ask(sources=[])

    def test_names_are_normalized_and_deduped(self):
        assert _ask(sources=["SQL", " sql ", "sql"]).sources == ["sql"]

    def test_dedupe_preserves_first_seen_order(self):
        assert _ask(sources=["web", "sql", "web"]).sources == ["web", "sql"]

    def test_an_oversized_selection_is_rejected(self):
        with pytest.raises(ValidationError):
            _ask(sources=["sql"] * 17)

    def test_a_non_list_is_rejected(self):
        with pytest.raises(ValidationError):
            _ask(sources="sql")

    def test_the_field_does_not_break_extra_forbid(self):
        """`AskRequest` is `extra="forbid"`; a typo'd field name must still
        be refused rather than silently ignored."""
        with pytest.raises(ValidationError):
            _ask(source=["sql"])


class TestResponseField:
    def test_source_selection_notice_defaults_to_none(self):
        """Additive with a default, so every existing consumer of
        `AskResponse` keeps working (CLAUDE.md 6.3)."""
        assert "source_selection_notice" in AskResponse.model_fields
        assert AskResponse.model_fields["source_selection_notice"].default is None

    def test_it_is_distinct_from_permission_denied_notice(self):
        """Two causes, two remedies, two fields -- one field for both would
        leave the caller unable to tell configuration from permission."""
        assert "permission_denied_notice" in AskResponse.model_fields


class TestRouterWiring:
    """The selection actually reaches `router_node` and shapes the route."""

    def _state(self, **overrides):
        state = {
            "question": "how many orders?",
            "caller_roles": ("admin",),
            "caller_subject": None,
            "session_id": None,
            "pending_attachment_ids": [],
            "selected_sources": None,
        }
        state.update(overrides)
        return state

    def _settings_all(self):
        return _settings(
            enable_policy_rag=True,
            rag_store_connection_string=SecretStr("x"),
            enable_web_search=True,
            web_search_api_key=SecretStr("tvly-x"),
        )

    def test_a_selection_narrows_the_route_without_a_classification_call(self, monkeypatch):
        """Narrowing to one source should short-circuit, so no LLM call is
        paid for a decision the caller already made."""
        import agent.orchestrator.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "get_settings", self._settings_all)

        def _boom(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("classify_sources was called despite a single selection")

        monkeypatch.setattr(nodes_mod, "classify_sources", _boom)

        out = router_node(self._state(selected_sources=["sql"]))
        assert out["route_decision"]["sources"] == ["sql"]
        assert out["route_decision"]["short_circuited"] is True
        assert out["source_selection_notice"] is None

    def test_an_unhonorable_selection_reports_a_notice(self, monkeypatch):
        import agent.orchestrator.nodes as nodes_mod

        # Nothing optional configured -> only sql.
        monkeypatch.setattr(nodes_mod, "get_settings", lambda: _settings())
        out = router_node(self._state(selected_sources=["policy"]))
        assert out["route_decision"]["sources"] == ["sql"]
        assert out["source_selection_notice"] is not None
        assert "not available" in out["source_selection_notice"]

    def test_no_selection_leaves_the_notice_unset(self, monkeypatch):
        import agent.orchestrator.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "get_settings", lambda: _settings())
        out = router_node(self._state(selected_sources=None))
        assert out["source_selection_notice"] is None

    def test_a_selected_but_unpermitted_source_is_still_denied(self, monkeypatch):
        """The whole point of R4, end to end through the node: a viewer
        naming `policy` gets the permission denial, not the source."""
        import agent.orchestrator.nodes as nodes_mod

        monkeypatch.setattr(nodes_mod, "get_settings", self._settings_all)
        out = router_node(self._state(caller_roles=("viewer",), selected_sources=["policy"]))
        assert "policy" not in out["route_decision"]["sources"]
        assert out["permission_denied_notice"] is not None
