"""Endpoint declarations for the REST/OpenAPI connector -- Batch 5 of
`docs/architecture/implementation-roadmap.md`.

A `RestEndpoint` is an **operator-declared** call this connector may make.
Three properties of that sentence are load-bearing:

- **Operator-declared.** An endpoint is configuration, never something a
  model or an end user can add. The roadmap's connector rules state it
  directly for MCP and it applies identically here: "an LLM must never
  register a remote tool -- administrator-controlled trust only". There is
  deliberately no API route that creates one of these.
- **A call, not a capability.** Each endpoint names one method and one URL
  template. There is no generic "call any path on this host" endpoint,
  because that would make the allowlist a host allowlist only, and the
  interesting attack surface on an internal API is the path.
- **Read or side-effecting, declared.** `side_effect` is a required field
  with no default, so whoever adds an endpoint has to state which it is.
  A side-effecting endpoint is refused unless the deployment has opted in
  (see `rest/connector.py`), matching how every other side-effecting
  capability in this platform is off by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RestMethod(str, Enum):
    """HTTP methods an endpoint may declare.

    `GET` and `HEAD` only for now. The write verbs are absent rather than
    present-but-blocked: adding `POST` here is the change that should
    require review, and an enum member nothing can execute is the "empty
    abstraction" the platform's own rules warn against. `rest/connector.py`
    issues `GET` and nothing else today.
    """

    GET = "GET"
    HEAD = "HEAD"


@dataclass(frozen=True)
class RestEndpoint:
    """One declared outbound REST call.

    Attributes:
        name: Stable identifier, used in evidence and logs. Unique within
            the registry.
        url: Absolute HTTPS URL. Its hostname must also appear in
            `Settings.rest_allowed_hosts` -- declaring an endpoint is not
            itself permission to call its host, so that an endpoint left
            in configuration after a host is revoked stops working rather
            than quietly continuing.
        method: See `RestMethod`.
        side_effect: Whether calling this changes state in the remote
            system. **Required, no default** -- the person adding an
            endpoint states it rather than inheriting an assumption.
        description: What this endpoint returns, for operators and for the
            evidence the connector produces. Never used as a prompt
            instruction: a description is data like any other
            configuration string.
        json_pointer: Optional dotted path into the response to use as the
            evidence body, e.g. `"data.items"`. None means the whole
            parsed body. Pure navigation -- it cannot transform or
            execute anything.
    """

    name: str
    url: str
    method: RestMethod
    side_effect: bool
    description: str = ""
    json_pointer: str | None = None
