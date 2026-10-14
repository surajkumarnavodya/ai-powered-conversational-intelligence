"""Unit tests for `sources/registry.py`.

Covers the three questions the registry answers -- what exists, what is
configured, what this caller may use -- and the fail-closed behaviour for
an unknown name.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent.authz import Permission, permissions_for
from security.secrets import SecretStr
from sources.registry import (
    all_descriptors,
    all_names,
    authorized_descriptors,
    available_descriptors,
    get_descriptor,
    is_registered,
)
from sources.types import SourceKind, SourceOperation, UnknownSourceError
from tests.test_orchestrator import _settings


class TestLookup:
    def test_every_declared_name_resolves(self):
        for name in all_names():
            assert get_descriptor(name).name == name

    def test_an_unknown_name_raises_rather_than_returning_a_default(self):
        """`CLAUDE.md` 5.3: unknown means deny, never "default". A `None`
        return would invite a caller to fall back to something."""
        with pytest.raises(UnknownSourceError):
            get_descriptor("sharepoint")

    def test_the_error_does_not_echo_the_requested_name_to_the_caller(self):
        """The internal message may name it for the log; `safe_message` is
        what a response body uses and must stay a fixed string, so a
        refusal cannot be used as an oracle (`CLAUDE.md` 5.5)."""
        with pytest.raises(UnknownSourceError) as exc:
            get_descriptor("../../etc/passwd")
        assert "etc/passwd" not in exc.value.safe_message

    def test_is_registered_never_raises(self):
        assert is_registered("sql") is True
        assert is_registered("nope") is False

    def test_no_mock_connector_is_registered(self):
        """`sources/mock.py` must never become routable -- `GET /sources`
        would advertise a source the router cannot reach."""
        assert "mock" not in all_names()

    def test_names_are_unique(self):
        assert len(set(all_names())) == len(all_names())


class TestAvailability:
    def test_only_sql_with_everything_off(self):
        names = [d.name for d in available_descriptors(_settings())]
        assert names == ["sql"]

    def test_a_configured_source_appears(self):
        settings = _settings(enable_web_search=True, web_search_api_key=SecretStr("tvly-x"))
        assert "web" in [d.name for d in available_descriptors(settings)]

    def test_a_flag_without_config_does_not(self):
        settings = _settings(enable_web_search=True, web_search_api_key=None)
        assert "web" not in [d.name for d in available_descriptors(settings)]

    def test_per_request_source_is_gated_on_the_request(self):
        settings = _settings(enable_chat_attachments=True)
        assert "attachments" not in [d.name for d in available_descriptors(settings)]
        assert "attachments" in [
            d.name for d in available_descriptors(settings, has_attachments=True)
        ]

    def test_declaration_order_is_preserved(self):
        settings = _settings(
            enable_web_search=True,
            web_search_api_key=SecretStr("tvly-x"),
            enable_policy_rag=True,
            rag_store_connection_string=SecretStr("x"),
        )
        names = [d.name for d in available_descriptors(settings)]
        assert names == ["sql", "policy", "web"]


class TestAuthorization:
    """The configured/permitted split. These are different failures with
    different remedies and must not collapse into one."""

    def _settings_with_policy_and_web(self):
        return _settings(
            enable_policy_rag=True,
            rag_store_connection_string=SecretStr("x"),
            enable_web_search=True,
            web_search_api_key=SecretStr("tvly-x"),
        )

    def test_a_viewer_loses_policy_and_web_but_keeps_sql(self):
        settings = self._settings_with_policy_and_web()
        names = [
            d.name for d in authorized_descriptors(settings, permissions_for(frozenset({"viewer"})))
        ]
        assert names == ["sql"]

    def test_an_analyst_keeps_what_its_role_grants(self):
        settings = self._settings_with_policy_and_web()
        perms = permissions_for(frozenset({"analyst"}))
        names = [d.name for d in authorized_descriptors(settings, perms)]
        assert "sql" in names
        # Only assert against what the role map actually grants, rather
        # than hardcoding an expectation that would break if roles change.
        assert ("web" in names) == (Permission.WEB_SEARCH in perms)
        assert ("policy" in names) == (Permission.POLICY_RAG_QUERY in perms)

    def test_an_unrecognized_role_grants_zero_optional_sources(self):
        """`CLAUDE.md` 5.2: an unrecognized role grants zero permissions.
        `sql` survives because it carries no source-level permission -- it
        is bounded by SQL validation and result governance instead."""
        settings = self._settings_with_policy_and_web()
        perms = permissions_for(frozenset({"not-a-real-role"}))
        names = [d.name for d in authorized_descriptors(settings, perms)]
        assert names == ["sql"]

    def test_authorization_cannot_widen_availability(self):
        """An admin still cannot reach an unconfigured source. Permission
        never substitutes for configuration."""
        settings = _settings()  # nothing configured
        perms = permissions_for(frozenset({"admin"}))
        names = [d.name for d in authorized_descriptors(settings, perms)]
        assert names == ["sql"]

    def test_empty_permissions_still_leaves_sql(self):
        names = [d.name for d in authorized_descriptors(_settings(), frozenset())]
        assert names == ["sql"]


class TestDescriptorInvariants:
    """Properties every descriptor must hold, so a new one cannot be added
    carelessly."""

    @pytest.mark.parametrize("descriptor", all_descriptors(), ids=lambda d: d.name)
    def test_has_a_nonempty_name_and_description(self, descriptor):
        assert descriptor.name.strip()
        assert descriptor.description.strip()

    @pytest.mark.parametrize("descriptor", all_descriptors(), ids=lambda d: d.name)
    def test_declares_at_least_one_operation(self, descriptor):
        assert descriptor.capabilities.operations

    @pytest.mark.parametrize("descriptor", all_descriptors(), ids=lambda d: d.name)
    def test_kind_is_a_real_enum_member(self, descriptor):
        assert isinstance(descriptor.kind, SourceKind)

    @pytest.mark.parametrize("descriptor", all_descriptors(), ids=lambda d: d.name)
    def test_no_implemented_source_declares_a_side_effect(self, descriptor):
        """Every source implemented today is read-only. A connector that
        genuinely mutates a source system must be off by default, which is
        a decision this assertion forces someone to make deliberately."""
        assert descriptor.capabilities.has_side_effects is False

    @pytest.mark.parametrize("descriptor", all_descriptors(), ids=lambda d: d.name)
    def test_capabilities_are_frozen(self, descriptor):
        """A mutable capability set would let one caller's edit leak into
        every later read, since descriptors are module-level singletons."""
        with pytest.raises(AttributeError):
            descriptor.capabilities.operations.add(SourceOperation.READ)  # type: ignore[attr-defined]

    @pytest.mark.parametrize("descriptor", all_descriptors(), ids=lambda d: d.name)
    def test_descriptor_itself_is_frozen(self, descriptor):
        with pytest.raises(FrozenInstanceError):
            descriptor.name = "mutated"  # type: ignore[misc]
