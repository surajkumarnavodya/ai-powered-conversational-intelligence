"""Request/response Pydantic models for `api/bi_integration.py` -- Prompt
48 (`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateConnectorRequest(BaseModel):
    """`push_config`'s required keys depend on `target_type` (checked by
    the route, not by this schema, since the requirement is conditional
    on another field): `power_bi` needs `dataset_id`/`table_name`;
    `looker` needs `webhook_url`; `tableau`/`excel` need neither (pull
    targets have no destination to configure)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_type: Literal["power_bi", "looker", "tableau", "excel"]
    display_name: str = Field(min_length=1, max_length=200)
    sql_text: str = Field(min_length=1)
    database: str | None = None
    push_config: dict[str, str] = Field(default_factory=dict)
    governed_metric_concept_key: str | None = Field(default=None, max_length=200)
    governed_metric_version: int | None = Field(default=None, ge=1)


class RefreshConnectorRequest(BaseModel):
    """Every field optional -- a caller needs no request body at all to
    trigger a refresh. See `api/bi_integration.py`'s own module-level
    `_DEFAULT_REFRESH_REQUEST` singleton default (mirroring
    `api/governance.py`'s identical `_DEFAULT_APPROVE_REQUEST`/
    `_DEFAULT_RETIRE_REQUEST` pattern, there to avoid FastAPI requiring
    *some* JSON body for a bodied-model parameter even when every field
    on it is optional)."""

    model_config = ConfigDict(extra="forbid")


class BiConnectorOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    target_type: str
    display_name: str
    sql_text: str
    database_id: str
    governed_metric_concept_key: str | None
    governed_metric_version: int | None
    status: str
    last_refresh_at: datetime | None
    last_refresh_status: str | None
    last_error: str | None
    last_row_count: int | None
    owner_user_id: str | None
    created_at: datetime
    updated_at: datetime
