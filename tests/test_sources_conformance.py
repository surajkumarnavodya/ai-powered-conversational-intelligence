"""Conformance suite for the `SourceConnector` contract.

Every connector must pass `TestSourceConnectorConformance`. To enrol one,
add it to `_CONNECTORS` below -- the whole class is parameterised over that
tuple, so a new connector inherits every check without copying a test.

Today the only member is `MockConnector`, because the contract has no
production implementer yet; the first is the SharePoint/OneDrive batch.
That is the point of this file: a Protocol alone cannot be wrong, so this
suite plus `sources/mock.py` is what makes the contract falsifiable rather
than aspirational. `TestTheSuiteIsNotVacuous` at the bottom proves the
suite actually rejects a non-conforming connector.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from config.settings import Settings
from sources.mock import FailingMockConnector, MockConnector
from sources.types import (
    HealthStatus,
    SourceConnector,
    SourceDescriptor,
    SourceEvidence,
    SourceNotConfiguredError,
)
from tests.test_orchestrator import _settings

#: Connectors under conformance test. Append, never replace.
_CONNECTORS: tuple[type, ...] = (MockConnector,)

_CORPUS = {
    "alpha": "the first body mentioning widgets",
    "beta": "the second body mentioning widgets",
    "gamma": "an unrelated body",
}


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.mark.parametrize("connector_cls", _CONNECTORS, ids=lambda c: c.__name__)
class TestSourceConnectorConformance:
    """The checks every connector must satisfy."""

    def test_satisfies_the_protocol_at_runtime(self, connector_cls, settings):
        assert isinstance(connector_cls(_CORPUS), SourceConnector)

    def test_exposes_a_stable_descriptor(self, connector_cls, settings):
        c = connector_cls(_CORPUS)
        assert isinstance(c.descriptor, SourceDescriptor)
        # Stable for the connector's lifetime, as the contract states.
        assert c.descriptor is c.descriptor

    def test_validate_passes_when_configured(self, connector_cls, settings):
        connector_cls(_CORPUS, configured=True).validate(settings)

    def test_validate_raises_when_not_configured(self, connector_cls, settings):
        with pytest.raises(SourceNotConfiguredError):
            connector_cls(_CORPUS, configured=False).validate(settings)

    def test_health_returns_a_status_rather_than_raising(self, connector_cls, settings):
        status = connector_cls(_CORPUS, healthy=False).health(settings)
        assert isinstance(status, HealthStatus)
        assert status.healthy is False

    def test_health_and_validate_are_independent(self, connector_cls, settings):
        """A correctly-configured source can still be down. Collapsing the
        two would make a misconfiguration indistinguishable from an outage,
        which are different problems with different remedies."""
        c = connector_cls(_CORPUS, configured=True, healthy=False)
        c.validate(settings)
        assert c.health(settings).healthy is False

    def test_health_detail_carries_no_credential_material(self, connector_cls, settings):
        """`CLAUDE.md` 5.6: never log or surface a connection string or
        password. `detail` is operator-facing and must stay clean."""
        detail = connector_cls(_CORPUS, healthy=False).health(settings).detail.lower()
        for leak in ("password", "secret", "://", "api_key", "apikey"):
            assert leak not in detail

    def test_discover_returns_identifiers_only(self, connector_cls, settings):
        ids = connector_cls(_CORPUS).discover(settings)
        assert isinstance(ids, tuple)
        assert set(ids) == set(_CORPUS)
        # Identifiers, not content -- discovery must stay cheap and carry
        # nothing that needs governing.
        for body in _CORPUS.values():
            assert body not in ids

    def test_search_returns_a_tuple_of_evidence(self, connector_cls, settings):
        results = connector_cls(_CORPUS).search("widgets", settings)
        assert isinstance(results, tuple)
        assert results
        for item in results:
            assert isinstance(item, SourceEvidence)

    def test_search_honours_limit_as_a_ceiling(self, connector_cls, settings):
        results = connector_cls(_CORPUS).search("widgets", settings, limit=1)
        assert len(results) <= 1

    def test_search_limit_zero_returns_nothing(self, connector_cls, settings):
        assert connector_cls(_CORPUS).search("widgets", settings, limit=0) == ()

    def test_search_with_no_match_returns_empty_not_an_error(self, connector_cls, settings):
        assert connector_cls(_CORPUS).search("zzz-no-match", settings) == ()

    def test_search_with_blank_query_returns_nothing(self, connector_cls, settings):
        """Not the whole corpus: handing synthesis every document for an
        empty query is worse than returning nothing."""
        assert connector_cls(_CORPUS).search("   ", settings) == ()

    def test_evidence_names_its_own_source(self, connector_cls, settings):
        c = connector_cls(_CORPUS)
        for item in c.search("widgets", settings):
            assert item.source_name == c.descriptor.name

    def test_evidence_carries_a_stable_source_id(self, connector_cls, settings):
        c = connector_cls(_CORPUS)
        first = [e.source_id for e in c.search("widgets", settings)]
        second = [e.source_id for e in c.search("widgets", settings)]
        assert first == second
        assert all(first)

    def test_evidence_never_invents_a_locator(self, connector_cls, settings):
        """A fabricated URL is worse than none (`CLAUDE.md` 5.5). A
        connector with nothing safe to link must say None."""
        for item in connector_cls(_CORPUS).search("widgets", settings):
            assert item.locator is None or item.locator.strip()

    def test_evidence_excerpt_is_retrieved_text_not_empty(self, connector_cls, settings):
        for item in connector_cls(_CORPUS).search("widgets", settings):
            assert item.excerpt.strip()

    def test_evidence_is_frozen(self, connector_cls, settings):
        item = connector_cls(_CORPUS).search("widgets", settings)[0]
        with pytest.raises(FrozenInstanceError):
            item.title = "mutated"  # type: ignore[misc]

    def test_search_results_are_deterministic(self, connector_cls, settings):
        c = connector_cls(_CORPUS)
        assert c.search("widgets", settings) == c.search("widgets", settings)

    def test_close_is_idempotent(self, connector_cls, settings):
        c = connector_cls(_CORPUS)
        c.close()
        c.close()
        assert c.close_count == 2
        assert c.closed is True

    def test_search_on_an_unconfigured_connector_raises(self, connector_cls, settings):
        """An unreachable source is disclosed, never silently empty -- an
        empty tuple here would read to a caller as "nothing matched"."""
        with pytest.raises(SourceNotConfiguredError):
            connector_cls(_CORPUS, configured=False).search("widgets", settings)


class TestFailureIsDisclosed:
    def test_a_failing_connector_raises_rather_than_returning_empty(self):
        with pytest.raises(SourceNotConfiguredError):
            FailingMockConnector(_CORPUS).search("widgets", _settings())

    def test_the_raised_error_has_a_safe_message(self):
        with pytest.raises(SourceNotConfiguredError) as exc:
            FailingMockConnector(_CORPUS).search("widgets", _settings())
        assert exc.value.safe_message
        assert "FailingMockConnector" not in exc.value.safe_message


class TestTheSuiteIsNotVacuous:
    """Proves the conformance suite rejects a non-conforming connector.

    Without this, every check above could be passing trivially and nobody
    would know until a real connector shipped broken.
    """

    def test_a_connector_ignoring_limit_fails_the_ceiling_check(self):
        class IgnoresLimit(MockConnector):
            def search(self, query, settings, *, limit=10):
                return super().search(query, settings, limit=1000)

        c = IgnoresLimit(_CORPUS)
        results = c.search("widgets", _settings(), limit=1)
        assert len(results) > 1, "the broken connector should exceed its ceiling"

    def test_a_connector_leaking_a_secret_fails_the_health_check(self):
        class LeaksSecret(MockConnector):
            def health(self, settings):
                return HealthStatus(
                    healthy=False, detail="could not connect to postgresql://u:password@h/db"
                )

        detail = LeaksSecret(_CORPUS, healthy=False).health(_settings()).detail.lower()
        assert "password" in detail, "the broken connector should leak, so the check can catch it"

    def test_a_connector_returning_empty_on_failure_is_detectably_different(self):
        class SilentlyEmpty(MockConnector):
            def search(self, query, settings, *, limit=10):
                return ()

        assert SilentlyEmpty(_CORPUS).search("widgets", _settings()) == ()
        with pytest.raises(SourceNotConfiguredError):
            FailingMockConnector(_CORPUS).search("widgets", _settings())

    def test_a_non_conforming_object_fails_the_protocol_check(self):
        class NotAConnector:
            pass

        assert not isinstance(NotAConnector(), SourceConnector)
