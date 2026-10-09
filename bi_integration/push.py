"""Push connectors: Power BI's push-dataset REST API and a generic
Looker webhook push -- Prompt 48.

**Disclosed, not live-verified** -- the identical posture
`search/web_search.py` (Tavily) and `media_gen/` (IMA Studio) already
carry for their own external providers: no live Power BI tenant or
Looker instance is available in this sandboxed environment, so these
are covered by mocked-`httpx` tests only, never a confirmed real call.

Uses `httpx` (already a dependency, the same HTTP client `search
/web_search.py` uses) rather than `requests` (which `media_gen/download.py`
uses for its own, different, streaming-download need) -- a plain POST
with no streaming/redirect-following requirement is `search/web_search
.py`'s own shape, not `media_gen/download.py`'s.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from bi_integration.outbound_safety import assert_safe_outbound_url
from bi_integration.rendering import json_safe_value
from config.settings import ConfigurationError, Settings


@dataclass(frozen=True)
class PushResult:
    success: bool
    detail: str
    row_count: int


class BiPushNotConfiguredError(ConfigurationError):
    """Raised when a push-style connector's own target has no
    operator-configured credential/endpoint -- a Power BI connector
    with `Settings.power_bi_push_base_url`/`power_bi_push_api_key`
    unset, or a Looker connector with no `webhook_url` in its own
    `push_config`."""


def push_power_bi(
    settings: Settings,
    dataset_id: str,
    table_name: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> PushResult:
    """`POST {base_url}/datasets/{dataset_id}/tables/{table_name}/rows`
    -- Power BI's own real, documented push-dataset shape
    (`{"rows": [{"col": value, ...}, ...]}`). `base_url`/`api_key` are
    operator-configured once (`Settings.power_bi_push_base_url`/
    `power_bi_push_api_key`), never per-connector/per-tenant -- see
    those fields' own docstrings in `config/settings.py` for why.
    """
    if not settings.power_bi_push_base_url or not settings.power_bi_push_api_key:
        raise BiPushNotConfiguredError(
            "A Power BI connector requires POWER_BI_PUSH_BASE_URL and "
            "POWER_BI_PUSH_API_KEY to be set in .env -- see .env.example's "
            "Enterprise BI integrations section."
        )
    assert_safe_outbound_url(settings.power_bi_push_base_url, context="power_bi_push")
    url = (
        f"{settings.power_bi_push_base_url.rstrip('/')}/datasets/{dataset_id}/tables/"
        f"{table_name}/rows"
    )
    payload = {
        "rows": [
            {column: json_safe_value(value) for column, value in zip(columns, row, strict=True)}
            for row in rows
        ]
    }
    response = httpx.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {settings.power_bi_push_api_key.get_secret_value()}"},
        timeout=settings.bi_outbound_request_timeout_seconds,
    )
    response.raise_for_status()
    return PushResult(
        success=True, detail=f"Pushed {len(rows)} row(s) to Power BI.", row_count=len(rows)
    )


def push_looker_webhook(
    settings: Settings,
    webhook_url: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> PushResult:
    """POSTs a plain `{"fields": [...], "data": [{"col": value, ...}]}`
    JSON body -- the shape Looker's own Action Hub webhook actions
    receive and this app's connector contract documents to a tenant
    configuring their own Looker Action. `webhook_url` is per-connector
    (each tenant's own Looker instance), re-validated against SSRF on
    *every* call (`assert_safe_outbound_url`), never only once at
    connector-creation time.
    """
    assert_safe_outbound_url(webhook_url, context="looker_webhook_push")
    payload = {
        "fields": list(columns),
        "data": [
            {column: json_safe_value(value) for column, value in zip(columns, row, strict=True)}
            for row in rows
        ],
    }
    response = httpx.post(
        webhook_url, json=payload, timeout=settings.bi_outbound_request_timeout_seconds
    )
    response.raise_for_status()
    return PushResult(
        success=True, detail=f"Pushed {len(rows)} row(s) to Looker webhook.", row_count=len(rows)
    )
