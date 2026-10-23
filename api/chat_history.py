"""`GET/POST/PATCH/DELETE /conversations`, `GET /conversations/{id}/messages`,
`GET /chat/search` -- the server-side, universal, cross-device chat-history
API this app's frontend now reads from instead of its own in-memory-only
Zustand store (see `docs/chat-history-architecture.md`).

Every route requires a local account (`Depends(require_local_user)`, the
same dependency `api/identity_auth.py`'s own self-service routes use) and
resolves the authenticated user's `id` from the verified access token --
**never** from a client-supplied `user_id`, a header, or a query parameter.
Ownership is enforced inside `identity/repositories/history.py`'s own
queries (`user_id` is part of the `WHERE` clause itself), not as a
permission check layered on top of an unscoped fetch -- see that module's
own docstring for why this makes a forged/guessed `conversation_id`
structurally unable to return another user's data.

Only reachable when `Settings.local_auth_enabled` is on (`require_local_user`
already 404s otherwise, via `require_local_auth_enabled` -- see
`api/identity_authz.py`) -- an OIDC-authenticated or unauthenticated caller
gets no server-side chat history today (documented, not silently missing --
see `docs/chat-history-architecture.md`'s "Known limitations").
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from identity.models import User
from identity.repositories.history import (
    ConversationSearchHit,
    append_single_message,
    create_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    search_history,
    soft_delete_conversation,
    update_conversation,
)
from identity.repositories.users import get_user_roles
from identity.schemas import (
    ConversationListResponse,
    ConversationOut,
    CreateConversationRequest,
    CreateMessageRequest,
    MessageListResponse,
    MessageOut,
    MessageResponse,
    SearchHitOut,
    SearchResponse,
    UpdateConversationRequest,
)
from sqlalchemy.orm import Session

from api.identity_authz import require_local_user
from api.shares import _no_evidence_reason
from config.settings import Settings, get_settings
from governance.domain_detection import detect_domain
from governance.result_policy import govern_result_for_domain, narrative_withhold_reason

logger = logging.getLogger(__name__)

router = APIRouter()


def _not_found() -> HTTPException:
    # One shared, generic 404 for "doesn't exist" *and* "exists but belongs
    # to someone else" -- see this module's own docstring: never confirming
    # another user's conversation even exists is the point, the same
    # account-enumeration-avoidance principle `identity.exceptions
    # .InvalidCredentialsError` applies to login.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _not_found() from None


def _conversation_out(
    conversation,
) -> ConversationOut:  # noqa: ANN001 - identity.models.Conversation
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        feature_type=conversation.feature_type,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        last_message_at=conversation.last_message_at,
        archived_at=conversation.archived_at,
    )


def _clamp_page_size(requested: int, default: int, maximum: int) -> int:
    if requested <= 0:
        return default
    return min(requested, maximum)


@router.get("/conversations", response_model=ConversationListResponse)
def list_conversations_route(
    limit: int = Query(default=0, ge=0),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ConversationListResponse:
    user, session = user_and_session
    settings = get_settings()
    page_size = _clamp_page_size(
        limit, settings.chat_history_page_size, settings.chat_history_max_page_size
    )
    conversations, total = list_conversations(
        session,
        user_id=user.id,
        limit=page_size,
        offset=offset,
        include_archived=include_archived,
    )
    return ConversationListResponse(
        conversations=[_conversation_out(c) for c in conversations],
        total=total,
        limit=page_size,
        offset=offset,
    )


@router.post("/conversations", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation_route(
    payload: CreateConversationRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ConversationOut:
    user, session = user_and_session
    conversation = create_conversation(
        session, user_id=user.id, title=payload.title, feature_type=payload.feature_type
    )
    return _conversation_out(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation_route(
    conversation_id: str,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ConversationOut:
    user, session = user_and_session
    conversation = get_conversation(
        session, conversation_id=_parse_uuid(conversation_id), user_id=user.id
    )
    if conversation is None:
        raise _not_found()
    return _conversation_out(conversation)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation_route(
    conversation_id: str,
    payload: UpdateConversationRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> ConversationOut:
    user, session = user_and_session
    conversation = get_conversation(
        session, conversation_id=_parse_uuid(conversation_id), user_id=user.id
    )
    if conversation is None:
        raise _not_found()
    updated = update_conversation(
        session, conversation=conversation, title=payload.title, archived=payload.archived
    )
    return _conversation_out(updated)


@router.delete("/conversations/{conversation_id}", response_model=MessageResponse)
def delete_conversation_route(
    conversation_id: str,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> MessageResponse:
    """Soft-deletes -- see `identity.repositories.history
    .soft_delete_conversation`'s own docstring for why this is never a real
    `DELETE`, and `docs/chat-history-architecture.md`'s retention section
    for the full permanent-deletion story. Explicit, user-initiated action
    only -- never triggered by logout, session expiry, or a browser/device
    change (see that same doc's "chat history must not be deleted" section)."""
    user, session = user_and_session
    conversation = get_conversation(
        session, conversation_id=_parse_uuid(conversation_id), user_id=user.id
    )
    if conversation is None:
        raise _not_found()
    soft_delete_conversation(session, conversation=conversation)
    return MessageResponse(message="Conversation deleted.")


#: Mirrors `api.shares._NARRATIVE_WITHHELD_TEXT`. A fixed module constant,
#: never built from the turn, so it cannot report what matched.
_NARRATIVE_WITHHELD_TEXT = (
    "This answer's wording is not available to you: it was written from "
    "columns your current permissions protect. The result table shows what you may see."
)


def _governed_metadata(
    metadata: dict | None,
    viewer_roles: tuple[str, ...],
    settings: Settings,
) -> tuple[dict | None, str | None]:
    """One persisted turn's metadata, re-governed for the caller reading now.

    Reloading a conversation is the fourth surface serving an
    already-persisted result, after `api.artifacts._result_out`,
    `api.shares._governed_result_snapshot` and
    `api.jobs._regovern_job_result`. It is the only one whose caller is
    always the data's own owner, which is why it was missed: ownership
    *is* enforced here (`identity/repositories/history.py` scopes every
    query by `user_id`), and ownership looked like enough.

    It is not. Ownership answers "whose conversation is this"; result
    governance answers "may this caller see these values *now*". The rows
    were governed for the roles the owner held when they confirmed the
    run, so an analyst who ran a salary query and was later demoted kept
    a durable, unprivileged window onto it -- the same thing
    `tests/security/test_artifacts_cannot_bypass_authorization.py`
    refuses on the artifact saved from that very turn. Reclassifying a
    column in `config/sensitive_columns.yaml` has the same effect
    without any role changing at all.

    Returns `(metadata, narrative_withheld_reason)`. The narrative is
    judged with the rows, because `insight`/`synthesized_answer` were
    written from them before governing and would otherwise state a
    figure the table now masks.
    """
    if metadata is None or not settings.enable_result_governance:
        return metadata, None

    snapshot = metadata.get("result_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    columns = snapshot.get("columns")
    rows = snapshot.get("rows")
    if not isinstance(columns, list) or not columns:
        # No confirmed result, but the turn may still carry the evidence
        # recorded at capture. See `api.shares._no_evidence_reason`.
        columns = extra_columns = metadata.get("insight_source_columns")
        rows = None if extra_columns else rows
    if not isinstance(columns, list) or not columns:
        reason = _no_evidence_reason(metadata, viewer_roles, settings)
        if reason is None:
            return metadata, None
        protected = dict(metadata)
        protected["narrative_withheld_reason"] = reason
        protected["insight"] = None
        protected["synthesized_answer"] = None
        return protected, reason

    column_names = [str(column) for column in columns]
    stored_sql = snapshot.get("normalized_sql") or metadata.get("sql") or ""
    domain = detect_domain(str(stored_sql), column_names).domain
    withheld_reason = narrative_withhold_reason(column_names, viewer_roles, domain)

    governed_snapshot = snapshot
    if isinstance(rows, list) and rows:
        governed = govern_result_for_domain(
            column_names,
            rows,
            viewer_roles,
            domain,
            settings.governance_min_group_size,
        )
        if governed.changed:
            governed_snapshot = dict(snapshot)
            governed_snapshot["rows"] = [list(row) for row in governed.rows]
            # The export ceiling can remove rows, so the bookkeeping has to
            # follow -- the identical reasoning `api/shares.py` applies.
            if len(governed.rows) != len(rows):
                governed_snapshot["returned_rows"] = len(governed.rows)
                governed_snapshot["truncated"] = True
            logger.info(
                "[chat_history] result-level governance re-applied on reload "
                "(domain=%s, actions=%d)",
                domain.value,
                len(governed.actions),
            )

    if governed_snapshot is snapshot and withheld_reason is None:
        return metadata, None

    protected = dict(metadata)
    protected["result_snapshot"] = governed_snapshot
    protected["narrative_withheld_reason"] = withheld_reason
    if withheld_reason is not None:
        protected["insight"] = None
        protected["synthesized_answer"] = None
        logger.info("[chat_history] narrative text withheld on reload (%s)", withheld_reason)
    return protected, withheld_reason


#: Replaces a snippet cut from narrative the caller may not read. A fixed
#: constant, never built from the hit, so it cannot report what matched.
_SNIPPET_WITHHELD_TEXT = "[Preview hidden - this answer references protected columns.]"


def _governed_snippet(
    hit: ConversationSearchHit,
    viewer_roles: tuple[str, ...],
    settings: Settings,
) -> str:
    """`hit.snippet`, or a fixed placeholder when it was cut from prose
    this caller may not read.

    `AiOutput.content` holds the insight text (see
    `api.chat_persistence._answer_text_for_history`), so a search snippet
    is a fragment of exactly what `_governed_metadata` withholds on
    reload -- selected, worse, by the caller's own query terms. Governing
    the reload but not the search would just move the channel.

    Fail-closed on a *message* hit whose metadata is missing or carries
    no evidence: the snippet is withheld rather than served, because an
    assistant turn with no evidence is precisely the case the gate cannot
    judge. The hit itself is always returned -- title, conversation and
    message id -- so search still finds the conversation and the user can
    open it, where the reload surface applies its own decision.

    Title and question matches are returned untouched: they are the
    user's own words, not text derived from a result.
    """
    if not settings.enable_result_governance or hit.matched_in != "message":
        return hit.snippet
    metadata = hit.output_metadata
    if metadata is None:
        # A question match rather than an assistant turn.
        return hit.snippet
    governed, withheld_reason = _governed_metadata(metadata, viewer_roles, settings)
    del governed
    if withheld_reason is not None:
        return _SNIPPET_WITHHELD_TEXT
    return hit.snippet


def _message_content(
    row,  # noqa: ANN001 - identity.repositories.history.MessageRow
    viewer_roles: tuple[str, ...],
    settings: Settings,
) -> str:
    """`row.content`, or the fixed placeholder when the prose was withheld.

    `api.chat_persistence._answer_text_for_history` stores the insight
    itself as `ai_outputs.content`, so withholding only
    `metadata["insight"]` would leave the same sentence in a second field.
    """
    if row.role != "assistant":
        return row.content
    _metadata, withheld_reason = _governed_metadata(row.metadata, viewer_roles, settings)
    return _NARRATIVE_WITHHELD_TEXT if withheld_reason is not None else row.content


@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
def list_messages_route(
    conversation_id: str,
    limit: int = Query(default=0, ge=0),
    offset: int = Query(default=0, ge=0),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> MessageListResponse:
    user, session = user_and_session
    settings = get_settings()
    parsed_id = _parse_uuid(conversation_id)
    # Ownership check first -- list_messages itself is also ownership-scoped
    # (see that function's own docstring), but checking here too lets this
    # route return the correct 404 instead of a confusing "empty messages
    # list" for a conversation that either doesn't exist or belongs to
    # someone else.
    conversation = get_conversation(session, conversation_id=parsed_id, user_id=user.id)
    if conversation is None:
        raise _not_found()

    page_size = _clamp_page_size(
        limit, settings.chat_history_page_size, settings.chat_history_max_page_size
    )
    rows, total_turns = list_messages(
        session, conversation_id=parsed_id, user_id=user.id, limit=page_size, offset=offset
    )
    # Re-governed against the roles this caller holds *now*, not the ones
    # they held when the result was captured. See `_governed_metadata`.
    viewer_roles = tuple(get_user_roles(session, user.id))
    return MessageListResponse(
        messages=[
            MessageOut(
                id=row.id,
                conversation_id=row.conversation_id,
                role=row.role,  # type: ignore[arg-type]
                content=_message_content(row, viewer_roles, settings),
                sequence_number=row.sequence_number,
                created_at=row.created_at,
                status=row.status,
                model_name=row.model_name,
                error_code=row.error_code,
                metadata=_governed_metadata(row.metadata, viewer_roles, settings)[0],
            )
            for row in rows
        ],
        total_turns=total_turns,
        limit=page_size,
        offset=offset,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
def create_message_route(
    conversation_id: str,
    payload: CreateMessageRequest,
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> MessageOut:
    """See `CreateMessageRequest`'s own docstring -- a direct single-message
    append, distinct from (and not used by) this app's real `/ask`
    persistence path."""
    user, session = user_and_session
    conversation = get_conversation(
        session, conversation_id=_parse_uuid(conversation_id), user_id=user.id
    )
    if conversation is None:
        raise _not_found()

    row = append_single_message(
        session,
        conversation=conversation,
        user_id=user.id,
        role=payload.role,
        content=payload.content,
    )
    is_prompt = payload.role == "user"
    return MessageOut(
        id=row.id,
        conversation_id=conversation.id,
        role=payload.role,
        content=payload.content,
        sequence_number=row.sequence_number,
        created_at=row.created_at,
        status="completed" if is_prompt else row.status,  # type: ignore[union-attr]
        model_name=None if is_prompt else row.model_name,  # type: ignore[union-attr]
        error_code=None if is_prompt else row.error_code,  # type: ignore[union-attr]
    )


@router.get("/chat/search", response_model=SearchResponse)
def search_chat_history_route(
    q: str = Query(default="", max_length=500),
    limit: int = Query(default=0, ge=0),
    offset: int = Query(default=0, ge=0),
    user_and_session: tuple[User, Session] = Depends(require_local_user),
) -> SearchResponse:
    """Searches the *authenticated caller's own* conversation titles and
    message content, server-side, across every conversation they've ever
    had -- not just whatever happens to be loaded in the frontend at the
    moment (see `identity.repositories.history.search_history`'s own
    docstring for the query approach and why it's safe/portable). An empty
    or whitespace-only `q` returns an empty result set rather than an
    error -- a cleared search box is a normal UI state, not a client
    mistake."""
    user, session = user_and_session
    settings = get_settings()
    page_size = _clamp_page_size(
        limit, settings.chat_search_page_size, settings.chat_search_max_page_size
    )
    hits, total = search_history(session, user_id=user.id, query=q, limit=page_size, offset=offset)
    # Governed against the roles this caller holds *now*, like every other
    # surface that replays stored narrative. See `_governed_snippet`.
    viewer_roles = tuple(get_user_roles(session, user.id))
    return SearchResponse(
        results=[
            SearchHitOut(
                conversation_id=hit.conversation_id,
                title=hit.title,
                matched_in=hit.matched_in,  # type: ignore[arg-type]
                snippet=_governed_snippet(hit, viewer_roles, settings),
                message_id=hit.message_id,
                updated_at=hit.updated_at,
            )
            for hit in hits
        ],
        total=total,
        limit=page_size,
        offset=offset,
        query=q,
    )
