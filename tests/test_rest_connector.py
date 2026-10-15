"""Tests for `rest/connector.py` -- Batch 5.

The SSRF controls themselves are tested in `tests/test_outbound_url.py`;
this file covers what the *connector* adds: configuration validity, the
read-only posture, side-effect refusal, and that a question's text never
reaches an outbound URL.

The connector is also enrolled in the Batch 1 conformance suite
(`tests/test_sources_conformance.py`), so the lifecycle contract itself is
checked there rather than duplicated here.
"""

from __future__ import annotations

import socket

import pytest
from rest import RestConnector, RestEndpoint, RestMethod

import security.outbound_url as outbound
from sources.types import SourceNotConfiguredError
from tests.test_orchestrator import _settings


def _endpoint(**overrides) -> RestEndpoint:
    base = dict(
        name="orders",
        url="https://api.example.com/orders",
        method=RestMethod.GET,
        side_effect=False,
    )
    base.update(overrides)
    return RestEndpoint(**base)  # type: ignore[arg-type]


def _rest_settings(**overrides):
    return _settings(
        enable_rest_connector=True,
        rest_allowed_hosts=("api.example.com",),
        **overrides,
    )


class _Response:
    def __init__(self, *, status=200, headers=None, body=b"{}"):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    def iter_content(self, chunk_size=8192):
        yield self._body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(
        outbound.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )


@pytest.fixture
def serve(monkeypatch):
    def _serve(body: bytes):
        seen: list[str] = []

        def _get(url, **kwargs):
            seen.append(url)
            return _Response(body=body)

        monkeypatch.setattr(outbound.requests, "get", _get)
        return seen

    return _serve


class TestConfigurationValidity:
    def test_the_flag_off_is_not_configured(self):
        connector = RestConnector((_endpoint(),))
        with pytest.raises(SourceNotConfiguredError):
            connector.validate(_settings(enable_rest_connector=False))

    def test_an_empty_allowlist_is_not_configured(self):
        """Enabling the flag without naming hosts is inert, not open."""
        connector = RestConnector((_endpoint(),))
        with pytest.raises(SourceNotConfiguredError, match="allowlist"):
            connector.validate(_settings(enable_rest_connector=True, rest_allowed_hosts=()))

    def test_no_declared_endpoints_is_not_configured(self):
        with pytest.raises(SourceNotConfiguredError, match="endpoints"):
            RestConnector(()).validate(_rest_settings())

    def test_an_endpoint_naming_a_non_allowlisted_host_is_a_config_error(self):
        """An endpoint left behind after its host was removed from the
        allowlist must make configuration *invalid*, so an operator finds
        out rather than a user hitting a confusing silent failure."""
        connector = RestConnector((_endpoint(url="https://other.example.net/x"),))
        with pytest.raises(SourceNotConfiguredError, match="REST_ALLOWED_HOSTS"):
            connector.validate(_rest_settings())

    def test_a_fully_configured_connector_validates(self):
        RestConnector((_endpoint(),)).validate(_rest_settings())

    def test_validate_makes_no_network_call(self, monkeypatch):
        """The contract separates `validate` (shape) from `health`
        (reachability), so a misconfiguration stays distinguishable from
        an outage."""

        def _boom(*a, **k):
            raise AssertionError("validate resolved DNS")

        monkeypatch.setattr(outbound.socket, "getaddrinfo", _boom)
        RestConnector((_endpoint(),)).validate(_rest_settings())


class TestHealth:
    def test_a_healthy_connector_reports_healthy(self, public_dns):
        status = RestConnector((_endpoint(),)).health(_rest_settings())
        assert status.healthy is True

    def test_health_issues_no_request(self, public_dns, monkeypatch):
        """A health check must not become a way to make this server hit an
        external service repeatedly."""

        def _boom(*a, **k):
            raise AssertionError("health issued an HTTP request")

        monkeypatch.setattr(outbound.requests, "get", _boom)
        assert RestConnector((_endpoint(),)).health(_rest_settings()).healthy is True

    def test_an_internally_resolving_endpoint_is_unhealthy(self, monkeypatch):
        monkeypatch.setattr(
            outbound.socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))],
        )
        status = RestConnector((_endpoint(),)).health(_rest_settings())
        assert status.healthy is False

    def test_an_unconfigured_connector_is_unhealthy_not_an_exception(self):
        status = RestConnector(()).health(_settings(enable_rest_connector=False))
        assert status.healthy is False

    def test_health_detail_leaks_no_url_or_credential(self, monkeypatch):
        monkeypatch.setattr(
            outbound.socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
        )
        endpoint = _endpoint(url="https://api.example.com/orders?token=super-secret")
        detail = RestConnector((endpoint,)).health(_rest_settings()).detail
        assert "super-secret" not in detail
        assert "https://" not in detail


class TestDiscover:
    def test_it_returns_endpoint_names_only(self, public_dns):
        connector = RestConnector((_endpoint(), _endpoint(name="customers")))
        assert connector.discover(_rest_settings()) == ("orders", "customers")

    def test_it_never_returns_a_url(self, public_dns):
        """A URL can carry a token in its query string."""
        endpoint = _endpoint(url="https://api.example.com/x?key=secret-value")
        ids = RestConnector((endpoint,)).discover(_rest_settings())
        assert all("secret-value" not in i for i in ids)
        assert all("https://" not in i for i in ids)


class TestSearchIsReadOnly:
    def test_a_side_effecting_endpoint_is_never_called(self, public_dns, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("a side-effecting endpoint was called on a read path")

        monkeypatch.setattr(outbound.requests, "get", _boom)
        connector = RestConnector((_endpoint(side_effect=True),))
        assert connector.search("orders", _rest_settings()) == ()

    def test_only_get_is_issued(self, public_dns, monkeypatch):
        """`safe_get_json` is the only transport this connector uses, and
        it issues GET. A HEAD-declared endpoint is skipped rather than
        silently upgraded to a GET."""
        monkeypatch.setattr(outbound.requests, "get", lambda url, **k: _Response(body=b'{"a":1}'))
        connector = RestConnector((_endpoint(method=RestMethod.HEAD),))
        assert connector.search("a", _rest_settings()) == ()

    def test_the_descriptor_declares_no_side_effects(self):
        assert RestConnector().descriptor.capabilities.has_side_effects is False


class TestSearchDoesNotInterpolateTheQuestion:
    def test_the_query_never_reaches_the_url(self, public_dns, serve):
        """Letting a question's text into an outbound URL would hand an end
        user partial control of a server-side request."""
        seen = serve(b'{"v": "../../etc/passwd"}')
        connector = RestConnector((_endpoint(),))
        connector.search("../../etc/passwd", _rest_settings())
        assert seen == ["https://api.example.com/orders"]

    def test_a_crafted_query_cannot_add_a_parameter(self, public_dns, serve):
        seen = serve(b'{"v": "x"}')
        RestConnector((_endpoint(),)).search("x&host=evil.test", _rest_settings())
        assert seen == ["https://api.example.com/orders"]


class TestSearchResults:
    def test_a_matching_body_becomes_evidence(self, public_dns, serve):
        serve(b'{"status": "shipped"}')
        results = RestConnector((_endpoint(),)).search("shipped", _rest_settings())
        assert len(results) == 1
        assert results[0].source_name == "rest"
        assert results[0].source_id == "orders"
        assert "shipped" in results[0].excerpt

    def test_a_non_matching_body_is_filtered_out(self, public_dns, serve):
        serve(b'{"status": "shipped"}')
        assert RestConnector((_endpoint(),)).search("refunded", _rest_settings()) == ()

    def test_an_empty_query_returns_nothing(self, public_dns, serve):
        serve(b'{"status": "shipped"}')
        assert RestConnector((_endpoint(),)).search("  ", _rest_settings()) == ()

    def test_the_locator_is_the_declared_url_not_a_response_value(self, public_dns, serve):
        """A citation must point at operator configuration, never at
        something the response chose."""
        serve(b'{"status": "shipped", "self": "https://evil.test/x"}')
        results = RestConnector((_endpoint(),)).search("shipped", _rest_settings())
        assert results[0].locator == "https://api.example.com/orders"

    def test_a_json_pointer_selects_a_fragment(self, public_dns, serve):
        serve(b'{"data": {"items": "widget-42"}}')
        connector = RestConnector((_endpoint(json_pointer="data.items"),))
        results = connector.search("widget", _rest_settings())
        assert results[0].excerpt == "widget-42"

    def test_a_missing_pointer_path_is_disclosed_not_silent(self, public_dns, serve):
        """A third-party API changing shape is an ordinary condition, and
        an empty result a reader would read as "nothing matched" is worse
        than a warning."""
        serve(b'{"other": 1}')
        connector = RestConnector((_endpoint(json_pointer="data.items"),))
        results = connector.search("anything", _rest_settings())
        assert len(results) == 1
        assert results[0].warnings
        assert results[0].excerpt == ""

    def test_a_failing_endpoint_is_disclosed_not_silent(self, public_dns, monkeypatch):
        def _get(url, **kwargs):
            raise outbound.requests.RequestException("boom")

        monkeypatch.setattr(outbound.requests, "get", _get)
        results = RestConnector((_endpoint(),)).search("x", _rest_settings())
        assert len(results) == 1
        assert results[0].warnings
        assert results[0].excerpt == ""

    def test_a_failure_warning_carries_no_internal_detail(self, public_dns, monkeypatch):
        def _get(url, **kwargs):
            raise outbound.requests.RequestException("connect to 10.0.0.5 failed")

        monkeypatch.setattr(outbound.requests, "get", _get)
        results = RestConnector((_endpoint(),)).search("x", _rest_settings())
        assert "10.0.0.5" not in results[0].warnings[0]

    def test_limit_is_a_ceiling(self, public_dns, serve):
        serve(b'{"status": "shipped"}')
        connector = RestConnector(tuple(_endpoint(name=f"e{i}") for i in range(5)))
        assert len(connector.search("shipped", _rest_settings(), limit=2)) <= 2

    def test_search_validates_configuration_first(self):
        with pytest.raises(SourceNotConfiguredError):
            RestConnector((_endpoint(),)).search("x", _settings(enable_rest_connector=False))


class TestNotRoutableYet:
    def test_rest_is_not_a_registered_source(self):
        """No descriptor entry, so `GET /sources` does not advertise it and
        the router cannot reach it -- there is no orchestrator node for it.
        Advertising a source nothing can answer would be worse than
        omitting it."""
        from sources.registry import all_names

        assert "rest" not in all_names()
