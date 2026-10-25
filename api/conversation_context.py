"""Loads a conversation's prior turns from this server's own store.

### The limitation this addresses

`/ask` has always taken its context from `AskRequest.conversation_history`
-- an array the *client* resends on every request, because the API has no
server-side session of its own (`api.schemas.ConversationExchangeIn`'s
docstring says so). Two consequences follow, and they are different in
kind:

- **Correctness.** Editing or deleting an earlier message changes the next
  answer's context only because the client happens to send a different
  array. Nothing server-side recomputes it, and a client that does not
  update its copy keeps resolving against text the user has removed.
- **Trust.** The content of every prior turn is caller-supplied. That is
  *not* a privilege escalation -- the caller is authenticated, the turns
  are their own conversation, `agent.input_guard
  .sanitize_conversation_history` normalizes and injection-scans them, and
  the prompt framing treats them as data either way (CLAUDE.md 5.5). What
  they can do by fabricating a prior turn, they can already do in the
  question itself. But it does mean the text the resolver reasons over is
  not necessarily the text the conversation actually contains.

This module lets an operator make the server's own record authoritative
instead, reusing `identity.repositories.history.list_messages` -- the same
ownership- and tenant-scoped query `GET /conversations/{id}/messages`
uses, so there is no second access path to get wrong.

### The trade-off, stated plainly

`CONVERSATION_HISTORY_SOURCE` defaults to `"client"`, which is the
unchanged behaviour. Setting it to `"server"` buys the two properties
above and **costs one real feature**: the chat UI's per-turn "remove from
context" control. That control works precisely because the client is what
sends the history (`frontend/src/lib/conversationContext.ts` documents
this as "the one class of context the client genuinely controls"), so a
server-authoritative history silently ignores it. That is a product
decision, not a bug, which is why it is a setting and why the default
leaves it alone.

### What it never does

- It never loads history for a caller who has none to load: an OIDC or
  unauthenticated caller has no `identity.users` row, and nothing is
  persisted for them (CLAUDE.md 13's standing limitation). Those callers
  keep using the client array whatever the setting says.
- It never widens access. `list_messages` carries `user_id` in its own
  `WHERE` clause and matches the owner's tenant, so a borrowed
  `conversation_id` returns nothing -- and `/ask` has already refused one
  through `require_owned_conversation` before this runs.
- It never fails a question. An unreachable identity database, a
  malformed id or any repository error resolves to "no server history",
  and the caller's own array is used instead. Same fail-open contract
  `api/chat_persistence.py` sets out for the write side: history is a
  convenience, and losing it must not take answering down.
"""

from __future__ import annotations

import logging
import uuid

from agent.state import ConversationExchange
from config.settings import Settings, get_settings
from security.oidc import AuthIdentity

logger = logging.getLogger(__name__)

#: How many stored turns are read. A ceiling on the query, not the
#: resolution window -- `Settings.context_resolution_recent_turns` bounds
#: that, and `sanitize_conversation_history` applies
#: `max_conversation_history_turns` on top. Read in `sequence_number`
#: order from the end, so this is "the most recent N".
_MAX_LOADED_TURNS = 20


def _exchange_status(status: str | None) -> str:
    """Maps a stored turn's status onto `ConversationExchange`'s.

    Anything other than a recorded success is reported as "failed". The
    distinction that matters downstream is only whether the turn
    *established* anything -- `agent.conversation_memory.build_memory`
    admits successful turns alone -- and collapsing the rest avoids
    inventing a finer-grained vocabulary the store does not actually
    carry.
    """
    return "succeeded" if status == "succeeded" else "failed"


def load_server_history(
    identity: AuthIdentity,
    conversation_id: str | None,
    settings: Settings | None = None,
) -> list[ConversationExchange] | None:
    """The conversation's prior turns from this server's store, or None.

    Args:
        identity: The authenticated caller. Only a `local` identity with a
            UUID subject can own a conversation here.
        conversation_id: `AskRequest.conversation_id`. None means the
            first turn of a new conversation -- nothing stored yet.
        settings: Injected for testability; resolved when omitted.

    Returns:
        Prior turns, oldest first, ready to pass where the client array
        would have gone. **None** means "no server history to use", which
        is not an error: the setting is `"client"`, the caller owns
        nothing, there is no conversation yet, or the store could not be
        read. Every one of those falls back to the caller's own array.

        An empty list is returned only for a conversation that genuinely
        has no prior turns, and is distinct from None on purpose -- it
        means "the server's record is authoritative and it is empty",
        which must not silently re-admit a client-supplied array.
    """
    settings = settings or get_settings()
    if settings.conversation_history_source != "server":
        return None
    if not conversation_id:
        return None
    if identity.mode != "local" or not settings.local_auth_enabled:
        # No `identity.users` row, so nothing was ever persisted for them.
        return None

    try:
        user_id = uuid.UUID(identity.subject)
        parsed_conversation_id = uuid.UUID(conversation_id)
    except ValueError:
        # Same non-UUID guard `require_owned_conversation` and
        # `persist_ask_turn` apply: such a caller owns nothing.
        return None

    try:
        from identity.db import get_identity_session
        from identity.repositories.history import list_messages

        session = get_identity_session(settings)
    except Exception as exc:  # noqa: BLE001 - identity DB may be unreachable
        logger.warning(
            "[conversation_context] could not open an identity session; falling back "
            "to the caller-supplied history: %s",
            exc,
        )
        return None

    try:
        rows, _total = list_messages(
            session,
            conversation_id=parsed_conversation_id,
            user_id=user_id,
            limit=_MAX_LOADED_TURNS,
            offset=0,
        )
    except Exception as exc:  # noqa: BLE001 - history is never worth a failed answer
        logger.warning(
            "[conversation_context] could not read stored history; falling back to the "
            "caller-supplied history: %s",
            exc,
        )
        return None
    finally:
        session.close()

    return _pair_rows(rows)


def _pair_rows(rows: list) -> list[ConversationExchange]:  # noqa: ANN001 - MessageRow
    """Pairs user and assistant rows into exchanges, in sequence order.

    The store keeps the two sides separately (`Prompt` and `AiOutput`,
    merged into one ordered view by `list_messages`), so a turn is a user
    row followed by the assistant row that answered it. A user row with no
    answer after it -- the last turn of a conversation whose answer was
    never persisted, or one that failed before persistence -- still
    becomes an exchange, marked failed, so it is visible as an earlier
    topic without being treated as something the conversation established.

    An assistant row with no user row before it is skipped rather than
    guessed at: there is no question to attribute it to.
    """
    exchanges: list[ConversationExchange] = []
    pending: ConversationExchange | None = None

    for row in sorted(rows, key=lambda r: r.sequence_number):
        if row.role == "user":
            if pending is not None:
                exchanges.append(pending)
            pending = ConversationExchange(
                question=row.content or "",
                sql=None,
                tables=[],
                # Until its answer is seen, a question established nothing.
                status="failed",
            )
            continue

        if row.role != "assistant" or pending is None:
            continue

        metadata = row.metadata or {}
        pending["answer"] = row.content or None
        pending["sql"] = metadata.get("sql")
        tables = metadata.get("tables") or metadata.get("schema_tables") or []
        pending["tables"] = [
            str(table.get("table_name") if isinstance(table, dict) else table) for table in tables
        ]
        pending["status"] = _exchange_status(row.status)
        exchanges.append(pending)
        pending = None

    if pending is not None:
        exchanges.append(pending)
    return [exchange for exchange in exchanges if exchange["question"].strip()]


__all__ = ["load_server_history"]
