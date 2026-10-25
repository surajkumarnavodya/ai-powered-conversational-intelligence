"""A client-supplied `X-Correlation-ID` must never reach a log line raw.

`api.main._correlation_id_middleware` binds this header for the whole
request and `security.audit_log.CorrelationIdLogFilter` stamps it onto
**every** log record, so it is untrusted input that reaches every log
line -- the case `security.sanitization.truncate_for_log`'s docstring
says must never be logged unbounded.

Measured before the fix, against the real app via `TestClient`: an id of
`x<TAB>level=CRITICAL<TAB>event=admin_login_success` was accepted and
produced log lines reading
`correlation_id=x	level=CRITICAL	event=admin_login_success | tenant_id=- | ...`,
so a shipper parsing `key=value` pairs would ingest two forged fields; an
ANSI escape sequence was written verbatim; and a 4 KiB id was accepted
and stamped onto every record for the request. All three were echoed back
in the response header too.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from security.audit_log import _CORRELATION_ID_MAX_CHARS, sanitize_correlation_id


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


class TestSanitizeCorrelationId:
    """Unit coverage for the accept/reject decision itself."""

    @pytest.mark.parametrize(
        "value",
        [
            "3f7c1e92-4b6a-4a1d-9c3e-2f8b7a6d5e40",  # uuid4
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",  # traceparent
            "req_12345",
            "a.b:c-d_e",
            "A" * _CORRELATION_ID_MAX_CHARS,
        ],
    )
    def test_legitimate_ids_pass_through_unchanged(self, value: str) -> None:
        assert sanitize_correlation_id(value) == value

    @pytest.mark.parametrize(
        ("label", "value"),
        [
            ("tab-separated field forgery", "x\tlevel=CRITICAL\tevent=admin_login_success"),
            ("pipe field forgery", "x | tenant_id=other-tenant"),
            ("ansi escape", "\x1b[31mFAKE\x1b[0m"),
            ("newline", "a\nfabricated log line"),
            ("carriage return", "a\rfabricated log line"),
            ("null byte", "a\x00b"),
            ("unicode bidi override", "abc‮GNINRAW"),
            ("zero-width space", "a​b"),
            ("space", "two words"),
            ("over the length cap", "A" * (_CORRELATION_ID_MAX_CHARS + 1)),
            ("empty", ""),
        ],
    )
    def test_hostile_ids_are_rejected(self, label: str, value: str) -> None:
        assert sanitize_correlation_id(value) is None, label

    def test_absent_header_is_none(self) -> None:
        assert sanitize_correlation_id(None) is None


class TestCorrelationIdMiddleware:
    """End-to-end: what the running app does with the header."""

    def test_a_legitimate_id_is_honoured_and_echoed(self, client: TestClient) -> None:
        correlation_id = str(uuid.uuid4())

        response = client.get("/health", headers={"X-Correlation-ID": correlation_id})

        assert response.headers["X-Correlation-ID"] == correlation_id

    @pytest.mark.parametrize(
        ("label", "value"),
        [
            ("tab-separated field forgery", "x\tlevel=CRITICAL\tevent=admin_login_success"),
            ("ansi escape", "\x1b[31mFAKE\x1b[0m"),
            ("unbounded length", "A" * 4096),
        ],
    )
    def test_a_hostile_id_is_replaced_not_reflected(
        self, client: TestClient, label: str, value: str
    ) -> None:
        """The response must not carry the hostile value back."""
        response = client.get("/health", headers={"X-Correlation-ID": value})

        echoed = response.headers["X-Correlation-ID"]
        assert echoed != value, label
        # Replaced with a server-generated id, so correlation still works.
        assert sanitize_correlation_id(echoed) == echoed
        uuid.UUID(echoed)

    def test_a_hostile_id_never_reaches_a_log_record(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The forged fields must not appear in any emitted log record."""
        forged = "x\tlevel=CRITICAL\tevent=admin_login_success"

        with caplog.at_level("DEBUG"):
            client.get("/health", headers={"X-Correlation-ID": forged})

        stamped = {getattr(record, "correlation_id", None) for record in caplog.records}
        assert forged not in stamped
        assert not any(
            "level=CRITICAL" in str(getattr(record, "correlation_id", ""))
            for record in caplog.records
        )

    def test_no_header_still_produces_one(self, client: TestClient) -> None:
        response = client.get("/health")

        echoed = response.headers["X-Correlation-ID"]
        assert sanitize_correlation_id(echoed) == echoed
