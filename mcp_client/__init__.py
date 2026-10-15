"""MCP client -- Batch 8 of `docs/architecture/implementation-roadmap.md`.

A client *of* operator-declared remote MCP servers. Not to be confused
with `agent/tools/`, which is MCP-*shaped* so this app's own capabilities
could one day be exposed *to* an external MCP client (`docs/TOOLS.md`) --
opposite direction, no shared code.

- `mcp_client/types.py` -- specs, descriptors, errors
- `mcp_client/protocol.py` -- JSON-RPC 2.0 framing, pure and I/O-free
- `mcp_client/transport.py` -- stdio subprocess transport
- `mcp_client/client.py` -- initialize / tools/list / tools/call
- `mcp_client/trust.py` -- **the administrator-controlled trust gate**

Off by default (`Settings.enable_mcp_client`). Targets MCP revision
2025-06-18; see `mcp_client/protocol.py` on the 2026-07-28 stateless
revision, which this client does not speak.

Three things that are true by construction, not by convention: a remote
tool is never auto-registered into `agent.tools.registry.ToolRegistry`; an
empty per-server allowlist approves nothing; and a server's own metadata
-- its tool descriptions, and the `instructions` field it may return from
`initialize` -- is data, never an instruction.
"""

from __future__ import annotations

from mcp_client.client import McpClient, describe_server
from mcp_client.trust import (
    call_allowlisted_tool,
    is_tool_allowlisted,
    partition_by_trust,
)
from mcp_client.types import (
    McpError,
    McpProtocolError,
    McpServerError,
    McpServerSpec,
    McpToolDescriptor,
    McpToolResult,
    McpTransportError,
    McpTrustError,
)

__all__ = [
    "McpClient",
    "McpError",
    "McpProtocolError",
    "McpServerError",
    "McpServerSpec",
    "McpToolDescriptor",
    "McpToolResult",
    "McpTransportError",
    "McpTrustError",
    "call_allowlisted_tool",
    "describe_server",
    "is_tool_allowlisted",
    "partition_by_trust",
]
