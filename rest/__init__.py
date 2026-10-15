"""REST/OpenAPI connector -- Batch 5 of
`docs/architecture/implementation-roadmap.md`.

Calls a fixed set of operator-declared, allowlisted HTTPS endpoints
read-only, behind the `sources.types.SourceConnector` lifecycle Batch 1
defined.

- `rest/types.py` -- `RestEndpoint`, the operator-declared call
- `rest/connector.py` -- `RestConnector`, the lifecycle implementation

Off by default (`Settings.enable_rest_connector`), and enabling the flag
without naming hosts in `REST_ALLOWED_HOSTS` is inert rather than open --
an allowlist with no entries permits nothing.

**Not routable yet**: no `sources/descriptors.py` entry, so `GET /sources`
does not advertise it and the router cannot reach it. See
`rest/connector.py`'s docstring.

The SSRF controls live in `security/outbound_url.py`, not here.
"""

from __future__ import annotations

from rest.connector import REST_DESCRIPTOR, RestConnector
from rest.types import RestEndpoint, RestMethod

__all__ = ["REST_DESCRIPTOR", "RestConnector", "RestEndpoint", "RestMethod"]
