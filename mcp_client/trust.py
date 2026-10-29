"""The trust gate for remote MCP tools -- Batch 8.

This module is the point of the batch. The roadmap states the requirement
in one line: **"administrator-controlled trust only -- an LLM must never
register a remote tool."** Everything here exists to make that true by
construction rather than by convention.

Three properties, each tested:

1. **A remote tool is never auto-registered.** There is no code path that
   takes a `tools/list` response and puts it into
   `agent.tools.registry.ToolRegistry`. Discovery produces descriptors;
   bridging one is a separate call an operator's configuration has to have
   already approved. A model can request a call; it can never cause a
   registration.

2. **The allowlist is per server and per tool name, and empty means none.**
   A server being configured and reachable does not make its tools
   callable. This is the failing-closed default an allowlist must have --
   the same reasoning `Settings.rest_allowed_hosts` uses, where unset
   means nothing rather than everything.

3. **A remote tool's own metadata can never widen its access.** The
   required `Permission` comes from the operator's `McpServerSpec`, never
   from anything the server said. A server that returns
   `{"name": "admin_tool", "description": "requires no permission"}` is
   describing itself, and a description is data.
"""

from __future__ import annotations

import logging

from agent.authz import has_role_permission
from mcp_client.client import McpClient
from mcp_client.types import (
    McpServerSpec,
    McpToolDescriptor,
    McpToolResult,
    McpTrustError,
)
from security.audit_log import log_security_event

logger = logging.getLogger(__name__)


def is_tool_allowlisted(spec: McpServerSpec, tool_name: str) -> bool:
    """Whether an administrator has approved `tool_name` on this server.

    Exact match against `spec.allowed_tools`. Not a prefix or glob match:
    an operator approving `read_file` must not thereby approve
    `read_file_and_delete`, and a wildcard would make the allowlist
    unreviewable.
    """
    return tool_name in spec.allowed_tools


def partition_by_trust(
    spec: McpServerSpec, discovered: tuple[McpToolDescriptor, ...]
) -> tuple[tuple[McpToolDescriptor, ...], tuple[McpToolDescriptor, ...]]:
    """Splits discovered tools into `(approved, unapproved)`.

    Both halves are returned so an admin surface can show what a server
    offers *and* what has been approved, which is the information needed
    to approve something. Hiding the unapproved half would make the
    allowlist impossible to extend from the UI that displays it.
    """
    approved = tuple(t for t in discovered if is_tool_allowlisted(spec, t.name))
    unapproved = tuple(t for t in discovered if not is_tool_allowlisted(spec, t.name))
    return approved, unapproved


def call_allowlisted_tool(
    spec: McpServerSpec,
    tool_name: str,
    arguments: dict[str, object],
    *,
    caller_roles: tuple[str, ...] = (),
    actor: str | None = None,
    client: McpClient | None = None,
) -> McpToolResult:
    """Calls a remote tool, enforcing both gates in order.

    The order is deliberate and the cheaper, more conclusive check runs
    first:

    1. **Trust** -- has an administrator approved this tool at all? If
       not, nothing about the caller can change that, so there is no point
       resolving permissions.
    2. **Permission** -- does *this* caller's role carry the permission
       the operator assigned to this server? The same
       `agent.authz.has_role_permission` call `ToolRegistry.execute` and
       `router_node` already use, so an unrecognized role grants nothing.

    Both refusals are audit-logged with their own event type, because they
    mean different things: one is a configuration gap an administrator
    closes, the other is a caller reaching for something their role does
    not carry.

    Args:
        client: Injected for testing. A real call opens a session for the
            duration and closes it.

    Raises:
        McpTrustError: `tool_name` is not allowlisted for this server.
        ToolPermissionError: `caller_roles` lacks `spec.permission`.
    """
    if not is_tool_allowlisted(spec, tool_name):
        log_security_event(
            "mcp_tool_not_allowlisted",
            "warning",
            "A remote MCP tool was requested that no administrator has approved for "
            "this server; it was refused before any connection was made.",
            server=spec.name,
            tool=tool_name,
            approved_count=len(spec.allowed_tools),
            actor=actor,
        )
        raise McpTrustError(
            f"Tool {tool_name!r} is not allowlisted for MCP server {spec.name!r}",
            safe_message="That external tool is not available.",
        )

    if not has_role_permission(caller_roles, spec.permission):
        # Imported here rather than at module top: this is the one place
        # the package raises an `agent.tools` error type, and reusing it
        # keeps a caller's `except` clause the same whether a tool was
        # local or remote.
        from agent.tools.types import ToolPermissionError

        log_security_event(
            "mcp_tool_permission_denied",
            "warning",
            "A remote MCP tool call was denied because the caller's role(s) do not "
            "grant the permission the operator assigned to that server.",
            server=spec.name,
            tool=tool_name,
            required_permission=spec.permission.value,
            caller_roles=list(caller_roles),
            actor=actor,
        )
        raise ToolPermissionError(
            f"Caller roles {caller_roles!r} do not grant {spec.permission.value!r}, "
            f"required by MCP server {spec.name!r}."
        )

    log_security_event(
        "mcp_tool_invoked",
        "info",
        "An allowlisted remote MCP tool was called.",
        server=spec.name,
        tool=tool_name,
        actor=actor,
    )

    if client is not None:
        return client.call_tool(tool_name, arguments)
    with McpClient(spec) as session:
        return session.call_tool(tool_name, arguments)


class RemoteToolRegistrationError(RuntimeError):
    """A remote MCP tool was found in the local tool registry."""


def assert_no_remote_tool_is_registered(registry: object) -> None:
    """Asserts no remote tool has leaked into a local `ToolRegistry`.

    A deliberate belt-and-braces check, callable from a test or a startup
    self-check. Property 1 in this module's docstring is currently true
    because no code does the registration -- which is the right way for it
    to be true, but it is also the kind of property a future convenience
    helper could quietly break. A remote tool would be named
    `<server>/<tool>` (`McpToolDescriptor.qualified_name`), so any local
    tool name containing `/` is the signature of exactly that mistake.

    Raises:
        RemoteToolRegistrationError: a registered tool name looks remote.
    """
    lister = getattr(registry, "list_tools", None)
    if not callable(lister):
        return
    for tool in lister():
        # Was an `assert` until 2026-10-29 (UAT-readiness Phase 8,
        # `BP-005`). This is the check that keeps a remote server's tools
        # out of the local registry -- a trust boundary, not a developer
        # note -- and `python -O` deletes an `assert` entirely, so the
        # boundary would vanish with it.
        if "/" in getattr(tool, "name", ""):
            raise RemoteToolRegistrationError(
                f"Tool {getattr(tool, 'name', '?')!r} looks like a bridged remote MCP "
                "tool. Remote tools must never be registered in the local ToolRegistry "
                "-- see mcp_client/trust.py."
            )
