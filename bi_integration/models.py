"""Pure typed vocabulary for `bi_integration/` -- Prompt 48
(`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`). Zero I/O, zero ORM/
SQLAlchemy dependency -- mirrors `semantic.catalog.CatalogEntrySnapshot`'s
own "deliberately ORM-independent" precedent exactly, for the identical
reason: this shape is what a future caller (a REST route, a test, a
script) should build and read, never `identity.models.BiConnector`
itself.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from agent.provenance import DataTruthLevel


class BiTargetType(str, Enum):
    """Which external BI/productivity tool a connector targets.

    `POWER_BI`/`LOOKER` are **push** targets: a refresh POSTs the
    governed, already-masked rows to an external REST endpoint
    immediately, and the data leaves this system the moment refresh
    succeeds. `TABLEAU`/`EXCEL` are **pull** targets: a refresh renders
    and caches a file (CSV/XLSX/JSON) in this process; the external tool
    reads it later via `GET /bi/connectors/{id}/export`, authenticated
    exactly like every other route in this app -- there is no native
    Tableau/Excel plugin here, only an honestly-scoped authenticated
    file/feed a tool's own generic "from the web"/"text file" connector
    can read, see this module's own docstring in
    `48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md` for why that is the
    real, implementable shape of this integration without a live
    Tableau/Excel tenant to verify a native plugin against.
    """

    POWER_BI = "power_bi"
    LOOKER = "looker"
    TABLEAU = "tableau"
    EXCEL = "excel"


#: Which capability each target actually has -- `bi_integration.policy`/
#: `api/bi_integration.py` read this rather than hardcoding a second
#: if/elif chain per route.
PUSH_TARGETS: frozenset[BiTargetType] = frozenset({BiTargetType.POWER_BI, BiTargetType.LOOKER})
PULL_TARGETS: frozenset[BiTargetType] = frozenset({BiTargetType.TABLEAU, BiTargetType.EXCEL})


class BiExportFormat(str, Enum):
    CSV = "csv"
    XLSX = "xlsx"
    JSON = "json"


class ConnectorStatus(str, Enum):
    """A connector's own current health -- independent of whether its
    *last refresh* succeeded (see `RefreshOutcome` below): `DISABLED` is
    a caller-chosen soft-disable (never deleted, so its history/evidence
    stays inspectable), distinct from `ERROR` (the connector is enabled
    but its last attempted refresh failed)."""

    NEVER_REFRESHED = "never_refreshed"
    HEALTHY = "healthy"
    ERROR = "error"
    DISABLED = "disabled"


class RefreshOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class GovernedMetricBinding(BaseModel):
    """A connector's optional link to a **published** semantic-catalog
    `METRIC` entry -- the mission's own "reuse governed metrics... do
    not create duplicate metric definitions" requirement, satisfied
    structurally: this is a reference (concept_key + version), never a
    copy of the metric's own `approved_expression`/formula. Only ever
    constructed by `metric_binding.validate_governed_metric_binding`
    from a real `semantic.catalog.CatalogEntrySnapshot` the API layer
    already looked up -- this module never fetches one itself (stays
    ORM-independent, per this module's own docstring).
    """

    model_config = ConfigDict(frozen=True)

    concept_key: str
    version: int
    business_name: str
    truth_level: DataTruthLevel


class BiExportEvidence(BaseModel):
    """Provenance stamped onto every refresh -- the mission's own
    "preserve evidence and provenance" requirement. `data_truth_level`
    is always `DATABASE_FACT` (a real, executed, read-only query's own
    rows are an observed fact regardless of whether a governed metric
    backs them); `governed_metric`, when present, carries its own
    separate `truth_level` for the metric *definition* -- the two are
    deliberately never conflated into one field (master rules 9/10: an
    inference/governance judgment and an observed fact are different
    kinds of claim).
    """

    model_config = ConfigDict(frozen=True)

    sql_text: str
    database_id: str
    executed_at: datetime
    row_count: int
    data_truth_level: DataTruthLevel = DataTruthLevel.DATABASE_FACT
    governed_metric: GovernedMetricBinding | None = None


class BiConnectorSnapshot(BaseModel):
    """The ORM-independent read shape of one `identity.models.BiConnector`
    row -- see this module's own docstring for why.

    `governed_metric_concept_key`/`governed_metric_version` are the
    connector's own **stored reference**, not a freshly-resolved
    `GovernedMetricBinding` -- the catalog entry it names may have been
    superseded since this connector was created or last refreshed, so
    this snapshot never fabricates a `truth_level` for it. A caller that
    needs the metric's own *current* status resolves it fresh via
    `identity.repositories.semantic_catalog` (the same thing
    `api/bi_integration.py`'s own refresh handler does to build a
    `BiExportEvidence.governed_metric` for this specific refresh).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    target_type: BiTargetType
    display_name: str
    sql_text: str
    database_id: str
    governed_metric_concept_key: str | None
    governed_metric_version: int | None
    status: ConnectorStatus
    last_refresh_at: datetime | None
    last_refresh_status: RefreshOutcome | None
    last_error: str | None
    last_row_count: int | None
    owner_user_id: str | None
    created_at: datetime
    updated_at: datetime
