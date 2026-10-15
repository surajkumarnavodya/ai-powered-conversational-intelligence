"""The MCP client: initialize, list tools, call a tool -- Batch 8.

Sequences the three operations against one server, using
`mcp_client/protocol.py` for every wire shape and
`mcp_client/transport.py` for the I/O.

**`InitializeResult.instructions` is read and deliberately discarded.**
The spec describes it as a string telling a client how to use the server.
Feeding it to a model would make a third party's response text part of
this app's prompt -- prompt injection with the reach of whatever consumed
it. `CLAUDE.md` 5.5 already settles the general case: the prompt registry
never hot-swaps a live prompt, "because making a database row the system
prompt would turn every write into prompt injection with full pipeline
reach." A remote server's `instructions` field is that exact hazard with a
less trustworthy author. It is logged at debug for operators and never
returned. `tests/test_mcp_client.py` pins that it does not reach any
caller.
"""

from __future__ import annotations

import logging

from mcp_client import protocol
from mcp_client.transport import StdioTransport
from mcp_client.types import (
    McpProtocolError,
    McpServerSpec,
    McpToolDescriptor,
    McpToolResult,
)

logger = logging.getLogger(__name__)


class McpClient:
    """A client for one operator-declared MCP server.

    Owns a transport and a monotonic request-id counter. One client, one
    server, one session -- a shared client across servers would make a
    desynchronized stream ambiguous about which server caused it.

    Args:
        spec: The operator's declaration.
        transport: Injected for testing. Defaults to a real
            `StdioTransport` over `spec`.
    """

    def __init__(self, spec: McpServerSpec, transport: object | None = None) -> None:
        self._spec = spec
        self._transport = transport if transport is not None else StdioTransport(spec)
        self._next_id = 0
        self._initialized = False

    def __enter__(self) -> McpClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        """Sends one request and returns its `result`."""
        self._next_id += 1
        request_id = self._next_id
        self._transport.send(  # type: ignore[attr-defined]
            protocol.encode_request(request_id, method, params)
        )
        line = self._transport.receive()  # type: ignore[attr-defined]
        return protocol.decode_response(line, expected_id=request_id)

    def start(self) -> None:
        """Starts the transport and performs the initialize handshake.

        Raises:
            McpProtocolError: the server speaks a different protocol
                revision (see `protocol.negotiate_protocol_version`).
            McpTransportError: the server could not be started or reached.
        """
        if self._initialized:
            return
        starter = getattr(self._transport, "start", None)
        if callable(starter):
            starter()

        result = self._request("initialize", protocol.initialize_params())
        protocol.negotiate_protocol_version(result)

        # Read, logged, and dropped. See the module docstring.
        instructions = result.get("instructions")
        if isinstance(instructions, str) and instructions:
            logger.debug(
                "[mcp] server %r sent %d chars of 'instructions'; discarded "
                "(a remote server does not get to write this app's prompts)",
                self._spec.name,
                len(instructions),
            )

        self._declares_tools = protocol.server_declares_tools(result)
        self._transport.send(  # type: ignore[attr-defined]
            protocol.encode_notification("notifications/initialized")
        )
        self._initialized = True
        logger.info(
            "[mcp] initialized server=%r declares_tools=%s",
            self._spec.name,
            self._declares_tools,
        )

    def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        """Lists the server's tools, following pagination.

        Returns an empty tuple when the server did not declare the `tools`
        capability -- the spec requires a server exposing tools to declare
        it, so calling `tools/list` anyway would be asking a question the
        server has already answered.

        Bounded twice, by tool count and by page count: a server returning
        an endless stream of pages (empty ones, each with a fresh cursor)
        would otherwise loop forever. Hitting either cap is a warning, not
        a failure -- the tools gathered so far are still usable.

        **The returned descriptors are untrusted data**, including each
        `description`. Nothing here may be used as an instruction.
        """
        if not self._initialized:
            self.start()
        if not getattr(self, "_declares_tools", False):
            return ()

        collected: list[McpToolDescriptor] = []
        cursor: str | None = None
        for page in range(protocol.MAX_LIST_PAGES):
            params: dict[str, object] | None = {"cursor": cursor} if cursor else None
            result = self._request("tools/list", params)
            tools, cursor = protocol.parse_tools_page(result, server_name=self._spec.name)
            collected.extend(tools)

            if len(collected) >= protocol.MAX_TOOLS_PER_SERVER:
                logger.warning(
                    "[mcp] server %r offered more than %d tools; the rest were ignored",
                    self._spec.name,
                    protocol.MAX_TOOLS_PER_SERVER,
                )
                collected = collected[: protocol.MAX_TOOLS_PER_SERVER]
                break
            if not cursor:
                break
            if page == protocol.MAX_LIST_PAGES - 1:
                logger.warning(
                    "[mcp] server %r paged past the %d-page cap; the rest were ignored",
                    self._spec.name,
                    protocol.MAX_LIST_PAGES,
                )

        return tuple(collected)

    def call_tool(self, name: str, arguments: dict[str, object]) -> McpToolResult:
        """Calls `name` with `arguments`.

        **This method does not check the allowlist.** Trust is enforced by
        `mcp_client.trust.call_allowlisted_tool`, which is the only path a
        caller should use. Keeping the check out of here is deliberate:
        a transport-level method that silently also enforced policy would
        invite a second, unchecked call path to appear beside it. This one
        is the plumbing; `trust.py` is the gate.

        The result's text is untrusted data.
        """
        if not self._initialized:
            self.start()
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text, is_error, warnings = protocol.parse_tool_result(result)
        if is_error:
            logger.warning("[mcp] tool %r on server %r reported isError", name, self._spec.name)
        return McpToolResult(content_text=text, is_error=is_error, warnings=warnings)

    def close(self) -> None:
        """Closes the transport. Idempotent."""
        self._initialized = False
        closer = getattr(self._transport, "close", None)
        if callable(closer):
            closer()


def describe_server(spec: McpServerSpec) -> tuple[McpToolDescriptor, ...]:
    """Connects, lists tools, disconnects. For operator-facing discovery.

    A one-shot helper so an admin surface can answer "what does this
    server offer, and which of those have I approved?" without holding a
    session open. Raises whatever the client raises -- discovery failing
    is information an operator needs, not something to swallow.
    """
    with McpClient(spec) as client:
        return client.list_tools()


__all__ = ["McpClient", "McpProtocolError", "describe_server"]
