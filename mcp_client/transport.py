"""stdio transport for the MCP client -- Batch 8.

Launches an operator-declared MCP server as a subprocess and exchanges
newline-delimited JSON-RPC over its stdin/stdout, per the spec's stdio
transport (verified 2026-10-15): messages are newline-delimited, must not
contain embedded newlines, and the server writes only valid MCP messages
to stdout, using stderr for logging.

**This is the first production code in this repository to spawn a
subprocess.** A sweep of `agent api config db voice media media_gen
security` found no `subprocess` import outside `scripts/` and `tests/`.
That makes it a new capability class, which is why it is off by default
and why the two controls below are not optional details:

1. **No shell, ever.** `shell=False` is passed explicitly and the command
   is an argv list. Nothing an operator writes in `.env` is interpreted by
   a shell -- no globbing, no `;`, no `$(...)`. An operator who can edit
   `.env` can already run code on this host, so this is not a privilege
   boundary; it is about a stray character in a config value not silently
   becoming a different command.

2. **The child inherits no environment.** `env` is passed as a complete
   replacement, not an overlay, so the subprocess receives exactly what
   `McpServerSpec.env` names and nothing more. This matters a great deal:
   this process's own `os.environ` holds `DB_PASSWORD`,
   `RAG_STORE_CONNECTION_STRING`, `JWT_SECRET_KEY`, `WEB_SEARCH_API_KEY`
   and every other secret in `.env`. Inheriting it -- which is
   `subprocess`'s default when `env=None` -- would hand every one of them
   to a third-party binary. `CLAUDE.md` 5.6 forbids logging a connection
   string; passing it to someone else's process is worse.

Both are pinned by tests, because both are the kind of control that works
until someone "simplifies" it.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import IO

from mcp_client.types import McpServerSpec, McpTransportError

logger = logging.getLogger(__name__)

#: Grace period for a terminated child to exit before it is killed.
_TERMINATE_GRACE_SECONDS = 5.0


class StdioTransport:
    """A line-oriented JSON-RPC channel to one MCP server subprocess.

    Not reusable after `close`. Intended to be used as a context manager so
    the child is always reaped, including on an exception mid-exchange --
    a leaked MCP server process would keep running with no owner.
    """

    def __init__(self, spec: McpServerSpec) -> None:
        self._spec = spec
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> StdioTransport:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> None:
        """Spawns the server.

        Raises:
            McpTransportError: the executable is missing or could not be
                started. The underlying OS error is logged but not placed
                in `safe_message` -- it can contain local filesystem
                paths.
        """
        if self._process is not None:
            raise McpTransportError("This transport has already been started")

        argv = [self._spec.command, *self._spec.args]
        try:
            self._process = subprocess.Popen(  # noqa: S603 - argv list, shell=False, see module docstring
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # A complete replacement, never an overlay -- see the
                # module docstring. `{}` means the child gets nothing.
                env=dict(self._spec.env),
                shell=False,
                text=True,
                encoding="utf-8",
                # Line-buffered: the protocol is newline-delimited, so a
                # block-buffered pipe would deadlock -- the client waits
                # for a line the child has written but not flushed.
                bufsize=1,
            )
        except OSError as exc:
            logger.warning(
                "[mcp] could not start server %r (command=%r): %s",
                self._spec.name,
                self._spec.command,
                exc,
            )
            raise McpTransportError(
                f"Could not start MCP server {self._spec.name!r}: {exc}"
            ) from exc

        # Drain stderr continuously on a daemon thread. Two reasons, both
        # real: a child that fills the stderr pipe buffer blocks forever
        # on its next write (a deadlock that looks like a hang), and its
        # diagnostics are worth having in our log. Logged at debug and
        # truncated -- it is third-party output, so it is data, and it is
        # never parsed for meaning.
        threading.Thread(
            target=self._drain_stderr, name=f"mcp-stderr-{self._spec.name}", daemon=True
        ).start()

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                logger.debug("[mcp:%s] %s", self._spec.name, line.rstrip()[:500])
        except (ValueError, OSError):
            # The pipe closed while we were reading it -- ordinary at
            # shutdown, not worth a warning.
            return

    def _streams(self) -> tuple[IO[str], IO[str]]:
        process = self._process
        if process is None:
            raise McpTransportError("This transport has not been started")
        if process.stdin is None or process.stdout is None:
            raise McpTransportError("The MCP server's pipes are unavailable")
        if process.poll() is not None:
            raise McpTransportError(
                f"MCP server {self._spec.name!r} exited with code {process.returncode}"
            )
        return process.stdin, process.stdout

    def send(self, line: str) -> None:
        """Writes one already-newline-terminated JSON-RPC line."""
        stdin, _ = self._streams()
        try:
            stdin.write(line)
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpTransportError(
                f"MCP server {self._spec.name!r} closed its input: {exc}"
            ) from exc

    def receive(self) -> str:
        """Reads one line, bounded by the spec's timeout and byte cap.

        The read runs on a daemon thread and is joined with a timeout --
        the same idiom `db.execution._execute_with_timeout` and
        `agent.tools.registry._run_with_timeout` already use, and for the
        same reason: it is the only cross-platform way to bound a blocking
        read's wall-clock time. A timed-out read leaves the transport
        unusable, so the caller must close it rather than retry; a
        half-consumed line would desynchronize the stream.

        Raises:
            McpTransportError: timeout, EOF, or a line over the cap.
        """
        _, stdout = self._streams()
        box: dict[str, str] = {}
        error: dict[str, BaseException] = {}

        def _read() -> None:
            try:
                box["line"] = stdout.readline()
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
                error["error"] = exc

        thread = threading.Thread(target=_read, name=f"mcp-read-{self._spec.name}", daemon=True)
        thread.start()
        thread.join(self._spec.timeout_seconds)

        if thread.is_alive():
            raise McpTransportError(
                f"MCP server {self._spec.name!r} did not reply within "
                f"{self._spec.timeout_seconds}s"
            )
        if "error" in error:
            raise McpTransportError(
                f"Error reading from MCP server {self._spec.name!r}: {error['error']}"
            )

        line = box.get("line", "")
        if not line:
            raise McpTransportError(
                f"MCP server {self._spec.name!r} closed its output without replying"
            )
        # Checked after the read rather than during it: `readline` has no
        # bounded form here, so this is a post-hoc cap. It still prevents
        # an unbounded line being *parsed* and retained, which is the
        # expensive part; a single oversized line is already in memory by
        # the time we see it, which is why the cap is sized well below
        # anything that would matter.
        if len(line) > self._spec.max_response_bytes:
            raise McpTransportError(
                f"MCP server {self._spec.name!r} sent a line over the "
                f"{self._spec.max_response_bytes}-byte cap"
            )
        return line

    def close(self) -> None:
        """Terminates the child, then kills it if it will not go. Idempotent."""
        process = self._process
        if process is None:
            return
        self._process = None

        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("[mcp] server %r ignored terminate; killing it", self._spec.name)
                process.kill()
                try:
                    process.wait(timeout=_TERMINATE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    logger.error("[mcp] server %r could not be killed", self._spec.name)
