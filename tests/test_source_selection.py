"""Per-conversation source selection -- Batch 3.

The load-bearing property is **R4** from
`docs/architecture/feature-inventory.md` §7: `AskRequest.sources` is a
*filter* over what the caller may already reach, never a grant. A client
that names a source it is not entitled to must not get it.

`TestSelectionCannotGrant` is the security regression for that, and
`TestTheGuardIsNotVacuous` proves those assertions would actually fail if
the filter were turned into a union. `TestNoSelectionIsUnchanged` pins
that omitting the field leaves today's behaviour byte-identical.
"""

from __future__ import annotations

import pytest

from agent.orchestrator.nodes import (
    _ALWAYS_AVAILABLE_SOURCE,
    SOURCE_PERMISSIONS,
    _apply_source_selection,
    get_available_sources,
)
from security.secrets import SecretStr
from sources.registry import all_names
from tests.test_orchestrator import _settings


def _all_sources_settings(**overrides):
    """Settings with every optional source configured, so filtering is
    actually exercised rather than hidden behind "only sql exists"."""
    return _settings(
        enable_document_rag=True,
        enable_policy_rag=True,
        rag_store_connection_string=SecretStr("x"),
        enable_web_search=True,
        web_search_api_key=SecretStr("tvly-x"),
        enable_media_generation=True,
        ima_api_key=SecretStr("ima-x"),
        enable_media_search=True,
        media_library_path="/tmp/media",
        enable_chat_attachments=True,
        **overrides,
    )


class TestSelectionCannotGrant:
    """R4: the selection narrows, never widens."""

    @pytest.mark.parametrize("name", sorted(all_names()))
    def test_naming_an_unconfigured_source_does_not_make_it_available(self, name):
        """With nothing optional configured, only `sql` is available.
        Selecting any other source must not conjure it into the route."""
        available = get_available_sources(_settings())
        assert available == ["sql"]

        sources, notice = _apply_source_selection(available, [name])
        assert set(sources) <= set(available), f"{name} was granted by selection"
        if name != "sql":
            assert notice is not None, f"{name} was dropped without disclosure"

    def test_the_result_is_always_a_subset_of_available(self):
        available = get_available_sources(_all_sources_settings())
        for selection in (
            ["sql"],
            ["web"],
            ["sql", "web"],
            ["policy", "generation"],
            list(all_names()),
        ):
            sources, _ = _apply_source_selection(available, selection)
            assert set(sources) <= set(available), selection

    def test_selecting_everything_is_not_broader_than_available(self):
        """The widest possible selection still cannot exceed configuration."""
        available = get_available_sources(_settings())
        sources, _ = _apply_source_selection(available, list(all_names()))
        assert sources == available

    def test_a_permission_gated_source_is_still_gated_after_selection(self):
        """Selection runs *before* the role check, which is unchanged. The
        filter never removes a source from `SOURCE_PERMISSIONS`' reach, so
        a selected-but-unpermitted source is still dropped downstream."""
        available = get_available_sources(_all_sources_settings())
        sources, _ = _apply_source_selection(available, ["policy"])
        assert "policy" in sources
        # The role check downstream is what decides, and it still applies
        # to exactly this name.
        assert "policy" in SOURCE_PERMISSIONS


class TestSelectionFilters:
    def test_a_single_selection_narrows_the_route(self):
        available = get_available_sources(_all_sources_settings())
        assert len(available) > 1
        sources, notice = _apply_source_selection(available, ["sql"])
        assert sources == ["sql"]
        assert notice is None

    def test_a_multi_selection_keeps_only_those(self):
        available = get_available_sources(_all_sources_settings())
        sources, notice = _apply_source_selection(available, ["sql", "web"])
        assert sources == ["sql", "web"]
        assert notice is None

    def test_declaration_order_is_preserved_not_the_callers_order(self):
        """So the classification prompt's option order stays stable
        regardless of what order a client happens to send."""
        available = get_available_sources(_all_sources_settings())
        forward, _ = _apply_source_selection(available, ["sql", "web"])
        backward, _ = _apply_source_selection(available, ["web", "sql"])
        assert forward == backward == ["sql", "web"]

    def test_a_partially_unavailable_selection_keeps_the_rest_and_discloses(self):
        settings = _settings(enable_web_search=True, web_search_api_key=SecretStr("tvly-x"))
        available = get_available_sources(settings)
        assert "policy" not in available

        sources, notice = _apply_source_selection(available, ["sql", "policy"])
        assert sources == ["sql"]
        assert notice is not None
        assert "rest of your selection was used" in notice

    def test_a_fully_unavailable_selection_falls_back_and_discloses(self):
        """Falls back rather than refusing -- a stale frontend or an
        operator switching a source off must not become an outage for a
        question that is still answerable."""
        available = get_available_sources(_settings())
        sources, notice = _apply_source_selection(available, ["policy"])
        assert sources == available
        assert notice is not None
        assert "not available on this deployment" in notice

    def test_the_notice_is_singular_or_plural_correctly(self):
        available = get_available_sources(_settings())
        _, one = _apply_source_selection(available, ["policy"])
        _, many = _apply_source_selection(available, ["policy", "web"])
        assert one is not None and " is not available" in one
        assert many is not None and " are not available" in many


class TestNoSelectionIsUnchanged:
    """Omitting the field must leave today's behaviour exactly as it was."""

    @pytest.mark.parametrize("empty", [None, []])
    def test_no_selection_returns_available_untouched(self, empty):
        for settings in (_settings(), _all_sources_settings()):
            available = get_available_sources(settings)
            sources, notice = _apply_source_selection(available, empty)
            assert sources == available
            assert notice is None

    def test_no_selection_returns_the_same_list_object_contents(self):
        available = get_available_sources(_all_sources_settings())
        sources, _ = _apply_source_selection(available, None)
        assert sources == available


class TestAttachmentsAreExempt:
    """An attached file is itself an explicit per-request choice, and the
    attachment feature's own rule is that an attachment is never silently
    discarded."""

    def test_attachments_survive_a_selection_that_omits_them(self):
        settings = _all_sources_settings()
        available = get_available_sources(settings, has_attachments=True)
        assert _ALWAYS_AVAILABLE_SOURCE in available

        sources, _ = _apply_source_selection(available, ["sql"])
        assert _ALWAYS_AVAILABLE_SOURCE in sources
        assert "sql" in sources

    def test_the_exemption_does_not_apply_when_no_file_is_attached(self):
        """The exemption keeps an *available* attachment source; it never
        makes one available."""
        settings = _all_sources_settings()
        available = get_available_sources(settings, has_attachments=False)
        assert _ALWAYS_AVAILABLE_SOURCE not in available

        sources, _ = _apply_source_selection(available, [_ALWAYS_AVAILABLE_SOURCE])
        assert _ALWAYS_AVAILABLE_SOURCE not in sources

    def test_selecting_only_attachments_still_keeps_attachments(self):
        settings = _all_sources_settings()
        available = get_available_sources(settings, has_attachments=True)
        sources, _ = _apply_source_selection(available, [_ALWAYS_AVAILABLE_SOURCE])
        assert sources == [_ALWAYS_AVAILABLE_SOURCE]


class TestTheGuardIsNotVacuous:
    """Proves the R4 assertions above would fail if the filter became a
    union. Without this, they could all be passing trivially."""

    @staticmethod
    def _granting_filter(available, selected):
        """A deliberately broken implementation: unions instead of
        intersects, which is exactly the mistake R4 warns about."""
        if not selected:
            return available, None
        return sorted(set(available) | set(selected)), None

    def test_a_union_implementation_grants_an_unconfigured_source(self):
        available = get_available_sources(_settings())
        assert available == ["sql"]

        granted, _ = self._granting_filter(available, ["policy"])
        assert "policy" in granted, "the broken filter should grant, so the check can catch it"

        safe, _ = _apply_source_selection(available, ["policy"])
        assert "policy" not in safe

    def test_the_subset_assertion_rejects_the_broken_filter(self):
        available = get_available_sources(_settings())
        granted, _ = self._granting_filter(available, ["web", "generation"])
        assert not set(granted) <= set(available)

        safe, _ = _apply_source_selection(available, ["web", "generation"])
        assert set(safe) <= set(available)
