"""JSON-RPC 2.0 framing and response parsing for MCP -- Batch 8.

**Pure functions, no I/O.** Every wire shape this package relies on is
decided here, so the protocol can be tested exhaustively without a
subprocess, a socket, or a real server. `mcp_client/transport.py` does the
I/O; `mcp_client/client.py` sequences the calls.

Protocol version: this targets **2025-06-18**, whose shapes were verified
against the MCP specification on 2026-10-15:

- `initialize` params carry `protocolVersion`, `capabilities`
  (`ClientCapabilities`) and `clientInfo` (`Implementation`: name,
  optional title, version). `InitializeResult` carries `protocolVersion`
  (which **may differ** from the one requested -- a client that cannot
  support the returned version must disconnect), `capabilities`,
  `serverInfo`, and an optional `instructions` string. The client then
  sends a `notifications/initialized` notification.
- `tools/list` returns the available tools and an optional `nextCursor`
  for pagination.
- `tools/call` returns a `content` array plus `isError`, which is
  "assumed to be false" when absent.
- stdio transport is newline-delimited JSON on the child's stdin/stdout,
  messages **MUST NOT** contain embedded newlines, and the server must
  write only valid MCP messages to stdout (logging goes to stderr).

**Known version caveat, not an oversight.** The 2026-07-28 revision makes
MCP stateless: it removes the `initialize`/`initialized` handshake and
moves protocol version and client capabilities into per-request `_meta`
fields. A server that only speaks that revision will not work with this
client. `negotiate_protocol_version` is where that is detected and
reported, rather than the mismatch surfacing as a confusing parse failure
later.
"""

from __future__ import annotations

import json
from typing import Any

from mcp_client.types import McpProtocolError, McpServerError, McpToolDescriptor

#: The revision this client implements. See the module docstring on why a
#: server answering with a different one is a hard stop rather than a
#: best-effort continue.
PROTOCOL_VERSION = "2025-06-18"

#: Sent as `clientInfo`. A fixed in-code constant, never read from
#: configuration -- it identifies this application to a third party, and
#: nothing about it should be operator-tunable.
CLIENT_INFO: dict[str, str] = {
    "name": "text-to-sql-agent",
    "version": "1.0.0",
}

#: Declared `capabilities`. Deliberately empty: this client does not offer
#: roots, sampling or elicitation. `sampling` in particular would let a
#: remote server ask *this* app to run an LLM call on its behalf -- an
#: obvious abuse channel, and declaring it absent is how a server is told
#: not to try.
CLIENT_CAPABILITIES: dict[str, Any] = {}

#: Longest tool name accepted from a server. A name is compared against
#: the operator's allowlist and used in logs; an unbounded one is a
#: log-flooding and display-breaking vector for no legitimate benefit.
MAX_TOOL_NAME_LENGTH = 128

#: Longest description retained. Operator-facing display only, never a
#: prompt instruction -- see `McpToolDescriptor`.
MAX_DESCRIPTION_LENGTH = 2000

#: Cap on how many tools one server may contribute, across all pages.
#: Without it, a server could page forever and this client would keep
#: asking.
MAX_TOOLS_PER_SERVER = 200

#: Cap on `tools/list` pagination round-trips, independent of the tool
#: cap: a server returning an endless stream of empty pages with a fresh
#: `nextCursor` each time would otherwise loop forever.
MAX_LIST_PAGES = 20


def encode_request(request_id: int, method: str, params: dict[str, Any] | None = None) -> str:
    """Builds one newline-terminated JSON-RPC request line.

    The spec forbids embedded newlines in a stdio message, and
    `json.dumps` cannot emit a raw newline inside a string (it escapes
    them as `\\n`), so compact single-line output satisfies that by
    construction rather than by a post-hoc check.
    """
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    line = json.dumps(payload, separators=(",", ":"))
    _reject_embedded_newline(line)
    return line + "\n"


class ProtocolFramingError(RuntimeError):
    """A line this client was about to write would break JSON-RPC framing."""


def _reject_embedded_newline(line: str) -> None:
    """Refuses a payload that would split into two JSON-RPC messages.

    The transport is newline-delimited, so an embedded newline in an
    encoded line is message smuggling: the far side reads one line as
    two messages. `json.dumps` escapes newlines inside strings, so this
    never fires on well-formed input -- which is exactly why it was an
    `assert` until 2026-10-29 (UAT-readiness Phase 8, `BP-005`), and
    exactly why it must not be one. `python -O` deletes an `assert`
    outright, and this is a framing control on data that reaches a
    remote process, not a developer note. CLAUDE.md 4.2 asks a safety
    control to fail closed; a statement the interpreter is free to
    remove cannot promise that.
    """
    if "\n" in line or "\r" in line:
        raise ProtocolFramingError(
            "a JSON-RPC line must never contain an embedded newline"
        )


def encode_notification(method: str, params: dict[str, Any] | None = None) -> str:
    """Builds one newline-terminated JSON-RPC notification line.

    A notification has no `id` and expects no response -- that absence is
    what distinguishes it, so it is never given one.
    """
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    line = json.dumps(payload, separators=(",", ":"))
    _reject_embedded_newline(line)
    return line + "\n"


def initialize_params() -> dict[str, Any]:
    """The `initialize` request params for this client."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": CLIENT_CAPABILITIES,
        "clientInfo": dict(CLIENT_INFO),
    }


def decode_response(line: str, *, expected_id: int) -> dict[str, Any]:
    """Parses one response line and returns its `result`.

    Validates, in order: that the line is JSON, that it is a JSON object,
    that its `id` matches the request, and that it carries `result` rather
    than `error`. Each is a separate check with its own message because
    each means something different operationally -- a mismatched id is a
    desynchronized stream, a JSON-RPC error is a server that understood
    and refused.

    Raises:
        McpProtocolError: malformed, non-object, or mismatched id.
        McpServerError: a well-formed JSON-RPC error response.
    """
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        # The raw line is untrusted and is not echoed -- it could be
        # anything, including text crafted to be read as instructions.
        raise McpProtocolError(f"Server sent a line that is not valid JSON: {exc}") from exc

    if not isinstance(message, dict):
        raise McpProtocolError("Server sent a JSON value that is not an object")

    if message.get("id") != expected_id:
        raise McpProtocolError(
            f"Server replied with id {message.get('id')!r}, expected {expected_id!r} -- "
            "the message stream is out of sync"
        )

    if "error" in message:
        error = message["error"]
        code = error.get("code") if isinstance(error, dict) else None
        raise McpServerError(f"Server returned JSON-RPC error (code={code!r})")

    if "result" not in message:
        raise McpProtocolError("Server response carried neither 'result' nor 'error'")

    result = message["result"]
    if not isinstance(result, dict):
        raise McpProtocolError("Server 'result' is not an object")
    return result


def negotiate_protocol_version(initialize_result: dict[str, Any]) -> str:
    """Returns the server's protocol version, or raises if unusable.

    The spec allows the server to answer with a version other than the one
    requested, and requires a client that cannot support it to disconnect.
    This client supports exactly `PROTOCOL_VERSION`, so anything else is a
    hard stop -- continuing would mean guessing at shapes, which is how a
    protocol mismatch turns into a silent data bug instead of a clear
    error.
    """
    version = initialize_result.get("protocolVersion")
    if not isinstance(version, str) or not version:
        raise McpProtocolError("InitializeResult did not carry a protocolVersion string")
    if version != PROTOCOL_VERSION:
        raise McpProtocolError(
            f"Server speaks MCP {version!r}; this client implements {PROTOCOL_VERSION!r}. "
            "Refusing to continue rather than guess at message shapes."
        )
    return version


def server_declares_tools(initialize_result: dict[str, Any]) -> bool:
    """Whether the server declared the `tools` capability.

    The spec requires a server exposing tools to declare it, so its
    absence means `tools/list` should not be called at all.
    """
    capabilities = initialize_result.get("capabilities")
    return isinstance(capabilities, dict) and isinstance(capabilities.get("tools"), dict)


def _clean(value: object, *, limit: int) -> str:
    """Coerces an untrusted value to a bounded, control-character-free string.

    Control characters are stripped rather than escaped: these strings
    reach logs and operator-facing UI, where a raw `\\r` or an ANSI escape
    can rewrite a line or forge surrounding output. Newlines go too -- a
    tool name or description is a single line by definition, and allowing
    one through would also let a value break the line-delimited framing
    assumptions of anything that logs it.
    """
    text = value if isinstance(value, str) else ""
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:limit]


def parse_tool_descriptor(raw: object, *, server_name: str) -> McpToolDescriptor:
    """Validates one entry from a `tools/list` result.

    Every field is untrusted. The name is required, bounded and cleaned,
    because it is compared against the operator's allowlist -- a name
    carrying control characters could make an audit line misleading about
    which tool was actually approved.

    Raises:
        McpProtocolError: the entry is not an object, or has no usable name.
    """
    if not isinstance(raw, dict):
        raise McpProtocolError("A tools/list entry is not an object")

    name = _clean(raw.get("name"), limit=MAX_TOOL_NAME_LENGTH)
    if not name:
        raise McpProtocolError("A tools/list entry has no usable 'name'")

    schema = raw.get("inputSchema")
    return McpToolDescriptor(
        name=name,
        description=_clean(raw.get("description"), limit=MAX_DESCRIPTION_LENGTH),
        input_schema=schema if isinstance(schema, dict) else {},
        server_name=server_name,
    )


def parse_tools_page(
    result: dict[str, Any], *, server_name: str
) -> tuple[tuple[McpToolDescriptor, ...], str | None]:
    """Parses one `tools/list` page into descriptors plus its next cursor.

    A malformed individual entry is skipped rather than failing the whole
    page: one bad tool should not make a server's other tools unusable,
    and the entry is dropped rather than guessed at.
    """
    raw_tools = result.get("tools")
    if not isinstance(raw_tools, list):
        raise McpProtocolError("tools/list result has no 'tools' array")

    tools: list[McpToolDescriptor] = []
    for entry in raw_tools:
        try:
            tools.append(parse_tool_descriptor(entry, server_name=server_name))
        except McpProtocolError:
            continue

    cursor = result.get("nextCursor")
    return tuple(tools), cursor if isinstance(cursor, str) and cursor else None


def parse_tool_result(result: dict[str, Any]) -> tuple[str, bool, tuple[str, ...]]:
    """Parses a `tools/call` result into `(text, is_error, warnings)`.

    Only `text` content blocks are read. Other block types (image,
    audio, embedded resource) are **dropped with a warning** rather than
    rendered: this client has no safe path for displaying third-party
    binary content, and silently discarding it would leave a caller
    thinking they saw the whole response.

    `isError` defaults to False when absent, per the spec.
    """
    warnings: list[str] = []
    content = result.get("content")
    if not isinstance(content, list):
        return "", bool(result.get("isError", False)), ("Response carried no content array.",)

    parts: list[str] = []
    dropped = 0
    for block in content:
        if not isinstance(block, dict):
            dropped += 1
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        else:
            dropped += 1

    if dropped:
        warnings.append(f"{dropped} non-text content block(s) were not displayed.")

    return "\n".join(parts), bool(result.get("isError", False)), tuple(warnings)
