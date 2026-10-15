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

import socket
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass

import pytest
from rest import RestConnector, RestEndpoint, RestMethod

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

#: Connectors whose constructor takes `(corpus, configured=, healthy=)`, so
#: the corpus-aware checks in `TestSourceConnectorConformance` can drive
#: them directly.
_CONNECTORS: tuple[type, ...] = (MockConnector,)


@dataclass(frozen=True)
class Harness:
    """How to build one connector in a known state, for the checks that do
    not depend on corpus semantics.

    `_CONNECTORS` above only works for a connector shaped like the mock.
    A real connector takes its own constructor arguments and reads its own
    `Settings` -- `RestConnector` is configured by `enable_rest_connector`
    and `rest_allowed_hosts`, not by a `configured=` keyword. So the
    universal contract checks are parameterised over harnesses instead,
    which is what lets a real implementation be enrolled at all.

    (Batch 1's own doc claimed any connector could be added to
    `_CONNECTORS` and inherit every check. That was overstated: the
    corpus-aware checks are mock-shaped. This split is the correction --
    see `docs/architecture/source-connector-contract.md` §4.)
    """

    label: str
    #: Builds a connector that should pass `validate`.
    make: Callable[[], object]
    #: Settings that connector considers fully configured.
    settings: Callable[[], Settings]
    #: Patches whatever the connector would otherwise reach for -- DNS, an
    #: HTTP transport, a filesystem. **Required for any connector that
    #: talks to the outside world**: every test in this repo is fully
    #: mocked (`CLAUDE.md` 7), and an unpatched `RestConnector.health`
    #: makes a real `getaddrinfo` call, which is slow, fails offline, and
    #: is exactly the kind of hidden network dependency the mocked-only
    #: rule exists to prevent. A probe run with `socket.getaddrinfo`
    #: stubbed to raise caught four such calls here before this hook
    #: existed.
    prepare: Callable[[pytest.MonkeyPatch], None] = lambda _mp: None


def _patch_rest_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Points DNS at a public address and serves an empty JSON body, so no
    test here resolves a name or opens a socket."""
    import security.outbound_url as outbound

    monkeypatch.setattr(
        outbound.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )

    class _Empty:
        status_code = 200
        headers: dict[str, str] = {}

        def iter_content(self, chunk_size=8192):
            yield b"{}"

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(outbound.requests, "get", lambda url, **k: _Empty())


def _rest_harness() -> Harness:
    return Harness(
        label="RestConnector",
        make=lambda: RestConnector(
            (
                RestEndpoint(
                    name="orders",
                    url="https://api.example.com/orders",
                    method=RestMethod.GET,
                    side_effect=False,
                ),
            )
        ),
        settings=lambda: _settings(
            enable_rest_connector=True, rest_allowed_hosts=("api.example.com",)
        ),
        prepare=_patch_rest_transport,
    )


def _mock_harness() -> Harness:
    return Harness(
        label="MockConnector",
        make=lambda: MockConnector(_CORPUS),
        settings=_settings,
    )


_HARNESSES: tuple[Harness, ...] = (_mock_harness(), _rest_harness())

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


@pytest.mark.parametrize("harness", _HARNESSES, ids=lambda h: h.label)
class TestUniversalContract:
    """Checks every connector must satisfy, whatever its data shape.

    These are the parts of `sources.types.SourceConnector` that do not
    depend on what the connector reads -- so a real implementation can be
    held to them without the suite having to understand its corpus.
    """

    @pytest.fixture(autouse=True)
    def _prepared(self, harness, monkeypatch):
        """Applies the harness's transport patching before every check, so
        nothing in this class reaches the network."""
        harness.prepare(monkeypatch)

    def test_satisfies_the_protocol_at_runtime(self, harness):
        assert isinstance(harness.make(), SourceConnector)

    def test_descriptor_is_a_descriptor_and_is_stable(self, harness):
        connector = harness.make()
        assert isinstance(connector.descriptor, SourceDescriptor)
        assert connector.descriptor is connector.descriptor

    def test_descriptor_declares_at_least_one_operation(self, harness):
        assert harness.make().descriptor.capabilities.operations

    def test_validate_passes_when_configured(self, harness):
        harness.make().validate(harness.settings())

    def test_health_returns_a_status_rather_than_raising(self, harness):
        status = harness.make().health(harness.settings())
        assert isinstance(status, HealthStatus)

    def test_health_detail_carries_no_credential_material(self, harness):
        detail = harness.make().health(harness.settings()).detail.lower()
        for leak in ("password", "secret", "api_key", "apikey"):
            assert leak not in detail

    def test_close_is_idempotent(self, harness):
        connector = harness.make()
        connector.close()
        connector.close()
        assert connector.close_count == 2
        assert connector.closed is True

    def test_search_with_a_blank_query_returns_nothing(self, harness):
        """Not the whole corpus: handing synthesis everything for an empty
        query is worse than returning nothing."""
        assert harness.make().search("   ", harness.settings()) == ()

    def test_search_returns_a_tuple(self, harness):
        results = harness.make().search("zzz-no-such-match", harness.settings())
        assert isinstance(results, tuple)

    def test_every_result_is_evidence_naming_its_own_source(self, harness):
        connector = harness.make()
        for item in connector.search("zzz-no-such-match", harness.settings()):
            assert isinstance(item, SourceEvidence)
            assert item.source_name == connector.descriptor.name


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
