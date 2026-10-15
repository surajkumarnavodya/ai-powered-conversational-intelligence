"""Server-sent events for `POST /ask` -- closes `CLAUDE.md` §13's "no
streaming" gap.

`POST /ask/stream` answers the same question as `POST /ask` and returns the
same `AskResponse`, but emits per-node progress while the run is still
going, so a long multi-source question stops looking hung.

**It calls `api.main.ask` rather than reimplementing it.** That function is
~270 lines of governed preamble -- model validation, rate limiting,
admission control, the execution envelope, governance, chat-history
persistence, recommendation persistence -- and a second copy would drift
from it the first time one of those changed. The route below declares the
*same* `Depends(require_permission(Permission.ASK))` and passes the
resolved identity straight through, so every control applies identically
because it is literally the same code (`CLAUDE.md` §4.5: reuse the governed
path, never build a second one).

What this module adds is only: a progress sink bound around that call, an
SSE framing, and disconnect handling.

**Two honest limitations, stated because they are inherent rather than
oversights.**

1. *Progress is per-node, not per-token.* The LLM's own tokens are not
   streamed. `agent/llm_client.py` makes one blocking call per step, and
   the generation and insight prompts are benchmark-pinned
   (`CLAUDE.md` §12), so changing that call's shape is a separate,
   evidence-gated change. What streams here is the pipeline's own
   progress, which is what makes a slow run legible.

2. *Errors after the stream opens are SSE events, not HTTP statuses.* Once
   `StreamingResponse` has sent its headers the status is already `200`.
   A rate-limit or validation failure raised inside `ask` therefore
   arrives as an `event: error` carrying the status it *would* have been.
   A client must read the event stream, not just the HTTP status. This is
   how SSE works everywhere, but it is worth saying plainly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent.authz import Permission
from agent.progress import (
    ProgressSink,
    reset_progress_sink,
    set_progress_sink,
)
from api.authz import require_permission
from api.schemas import AskRequest
from security.oidc import AuthIdentity

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ask"])

#: How often the stream wakes to forward buffered progress. Small enough
#: that a node transition reaches the UI promptly, large enough that an
#: idle run is not a busy-loop.
_POLL_INTERVAL_SECONDS = 0.15

#: Sent if nothing has been emitted for this long, so an intermediary that
#: closes idle connections (a proxy, a load balancer) does not drop a
#: legitimately slow run.
_KEEPALIVE_SECONDS = 15.0


def _sse(event: str, data: dict[str, Any]) -> str:
    """Frames one SSE message.

    `json.dumps` cannot emit a raw newline inside a string, so the
    single-line `data:` form is safe by construction -- an embedded
    newline would otherwise split one message into two malformed ones.
    """
    payload = json.dumps(data, separators=(",", ":"), default=str)
    assert "\n" not in payload, "an SSE data line must never contain a newline"
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/ask/stream")
async def ask_stream(
    payload: AskRequest,
    request: Request,
    identity: AuthIdentity = Depends(require_permission(Permission.ASK)),
) -> StreamingResponse:
    """Streams progress for one question, then its full `AskResponse`.

    Event types, in order:

    - `event: open` -- the run has been accepted and started.
    - `event: progress` -- one node finished. `{stage, attempt,
      duration_ms, outcome, error_category}`. Metadata only; see
      `agent/progress.py` on why the payload is deliberately narrow.
    - `event: error` -- the run failed. `{status, detail}`, where `status`
      is the HTTP status this would have been on `POST /ask`.
    - `event: result` -- the complete `AskResponse`, identical to what
      `POST /ask` returns for the same request.
    - `event: done` -- terminal. `{dropped}` reports how many progress
      events were discarded because the buffer filled, so a client can say
      its view is incomplete rather than silently missing steps.

    `result` and `error` are mutually exclusive, and `done` is always last.
    """
    # Imported here, not at module scope: `api.main` imports this module to
    # register the router, so a module-level import would be circular.
    from api.main import ask

    sink = ProgressSink()
    loop = asyncio.get_running_loop()

    def _run() -> Any:
        """Runs the governed handler with progress bound.

        The sink is bound *on this worker thread*, which is the thread the
        graph actually runs on -- a `ContextVar` set at the request
        boundary would not be visible here. Same reason, same pattern as
        `agent.graph.run_agent` binding its execution envelope and audit
        tenant id inside the call rather than outside it.
        """
        token = set_progress_sink(sink)
        try:
            return ask(payload, request, identity)
        finally:
            reset_progress_sink(token)
            sink.close()

    async def _stream() -> AsyncIterator[str]:
        yield _sse("open", {"status": "running"})

        future = loop.run_in_executor(None, _run)
        idle = 0.0

        try:
            while not future.done():
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

                events = sink.drain()
                for event in events:
                    yield _sse(
                        "progress",
                        {
                            "stage": event.stage,
                            "attempt": event.attempt,
                            "duration_ms": round(event.duration_ms, 2),
                            "outcome": event.outcome,
                            "error_category": event.error_category,
                        },
                    )

                idle = 0.0 if events else idle + _POLL_INTERVAL_SECONDS
                if idle >= _KEEPALIVE_SECONDS:
                    idle = 0.0
                    # A comment line: valid SSE, ignored by every client,
                    # and enough to keep an intermediary from closing a
                    # connection it thinks is idle.
                    yield ": keepalive\n\n"

                if await request.is_disconnected():
                    # Stop forwarding. The run itself is left to finish and
                    # release its own admission slots through `ask`'s own
                    # `on_done` callback -- abandoning it mid-flight would
                    # leak those slots, which is worse than finishing work
                    # nobody reads.
                    sink.close()
                    logger.info("[api] /ask/stream: client disconnected; stopped streaming")
                    return

            # Anything emitted between the last poll and completion.
            for event in sink.drain():
                yield _sse(
                    "progress",
                    {
                        "stage": event.stage,
                        "attempt": event.attempt,
                        "duration_ms": round(event.duration_ms, 2),
                        "outcome": event.outcome,
                        "error_category": event.error_category,
                    },
                )

            try:
                response = await future
            except HTTPException as exc:
                # The status the non-streaming route would have returned.
                # `detail` is already a safe message there -- these are
                # this app's own constants (rate-limit, validation), never
                # a raw exception string.
                yield _sse("error", {"status": exc.status_code, "detail": str(exc.detail)})
            except Exception:
                # Deliberately generic. `api/main.py`'s global handlers
                # exist precisely so an unexpected error never reaches a
                # caller as internal detail (`CLAUDE.md` §5.6); the stream
                # must honour the same rule, and the traceback goes to the
                # log instead.
                logger.exception("[api] /ask/stream: unhandled error")
                yield _sse(
                    "error",
                    {"status": 500, "detail": "The request could not be completed."},
                )
            else:
                yield _sse("result", response.model_dump(mode="json"))
        finally:
            sink.close()
            yield _sse("done", {"dropped": sink.dropped})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            # Without this an intermediary proxy may buffer the whole
            # response, which would defeat the entire point.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
        },
    )
