"""Wires the orchestrator nodes into a compiled top-level LangGraph state machine.

    router -+-> sql_subgraph  -+
            |   (agent.graph.run_agent -- unmodified)
            +-> document_rag   +
            |   (rag.graph.run_rag, collection="documents")
            +-> policy_rag     +-> synthesis -> END
            |   (rag.graph.run_rag, collection="policies")
            +-> web_search     |
            |   (search.web_search.web_search)
            +-> generation     +
            |   (media_gen.generate_image/generate_video -- off by
            |    default, Settings.enable_media_generation; image
            |    generation confirmed working end-to-end against a
            |    real IMA account, see media_gen/client.py)
            +-> media_search   +
            |   (media.search.search_media -- off by default,
            |    Settings.enable_media_search; local CLIP embeddings
            |    over an untagged image/video library, see media/)
            +-> attachments   -+
            |   (attachments.graph.run_attachment_qa -- files the caller
            |    attached directly to this question, e.g. an uploaded
            |    image/PDF/DOCX/XLSX/PPTX/TXT/CSV/JSON, see attachments/.
            |    Unlike every other source above, availability depends on
            |    the request, not standing config, and router_node forces
            |    it into the route unconditionally once available -- see
            |    that node's own docstring)
            +---------------->+
                (no source at all -- the `general_knowledge` and
                 `conversation` routing outcomes, which
                 `router_contract.ROUTED_SOURCE_NAMES` maps to no source
                 on purpose. `synthesis` answers them directly; there is
                 nothing to look up. This edge was missing until
                 2026-10-22 and `route_after_router` raised
                 `NotImplementedError` on the empty list instead -- see
                 that function's own docstring for why it only became
                 reachable then)

`route_after_router` returns a *list* of destination node names -- LangGraph
runs every one of them as a parallel branch before the graph proceeds to
`synthesis`, so a genuinely multi-source question ("compare policy X with
the database") fans out to two (or more) subgraphs in the same graph step,
not one after another (confirmed against this project's pinned LangGraph
version before this was built, not assumed).

`run_orchestrated` is the single public entry point `api/main.py` calls
in place of `agent.graph.run_agent` directly. It is deliberately a
two-path function, not a graph with one trivial branch:

  - `Settings.enable_multi_source_router` is False (the default -- see
    `.env.example`) AND the caller attached no files: `run_orchestrated`
    calls `agent.graph.run_agent` directly and returns its result
    completely unwrapped. This is not "the orchestrator graph with one
    destination" -- it is the exact same function call the API made before
    this package existed, so a fresh clone with today's `.env` behaves
    identically to today's app, per CLAUDE.md's Part 7 constraint.
    `eval/runner.py` and the standalone scripts also keep calling
    `run_agent` directly and are entirely unaffected by anything in this
    package. A non-empty `attachment_ids` breaks this short-circuit
    deliberately (see `run_orchestrated`'s own docstring) -- attaching a
    file needs the orchestrator graph to actually run regardless of the
    router flag, since there is no other path that would ever call
    `attachments.graph.run_attachment_qa`.
  - True: the orchestrator graph below actually runs. With only `sql`
    configured (the common case even with the flag on), `router_node`
    short-circuits to `sql_subgraph_node`, which itself just calls
    `run_agent` -- so a SQL-only setup's *results* are unchanged even with
    the router on; what changes is that routing now happens through an
    inspectable graph step (logged, and shown in the UI's "Sources used"
    panel) instead of a bare function call.

See `agent/orchestrator/nodes.py` for what each node does and
`agent/orchestrator/state.py` for why `OrchestratorState` extends
`AgentState` rather than replacing it.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph

from agent.graph import RECURSION_LIMIT_MESSAGE, run_agent
from agent.harness import (
    HarnessTerminated,
    ProviderUnavailable,
    guard_node,
    terminal_state_for,
)
from agent.harness.context import reset_current_envelope, set_current_envelope
from agent.orchestrator.nodes import (
    attachment_node,
    document_rag_node,
    generation_node,
    media_search_node,
    policy_rag_node,
    resolve_context_node,
    route_after_context,
    route_after_router,
    router_node,
    sql_subgraph_node,
    synthesis_node,
    web_search_node,
)
from agent.orchestrator.state import OrchestratorState
from agent.state import AgentState, ConversationExchange
from config.settings import get_settings
from security.audit_log import reset_audit_tenant_id, set_audit_tenant_id

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def build_orchestrator_graph():
    """Constructs and compiles the orchestrator's top-level LangGraph graph,
    once per process -- see `agent.graph.build_graph`'s docstring for why
    this is safe (a compiled graph is stateless; shape never depends on
    which sources happen to be configured, only `route_after_router`'s
    per-invocation decision does).

    Returns:
        A compiled LangGraph graph exposing `.invoke(state)`.
    """
    graph = StateGraph(OrchestratorState)

    # Every node goes through `guard_node`, for the reason
    # `agent.harness.control` gives for wrapping at the registration
    # site rather than decorating each function: the guard becomes the
    # default, and an unguarded node cannot be added by accident.
    #
    # 2026-10-28 (UAT-readiness Phase 6, `LOP-003`): these ten were the
    # unguarded ones. `agent/graph.py` wrapped its nineteen from the
    # start, but the orchestrator -- the graph that runs *first*, and
    # the only graph that runs at all for a document, policy, web,
    # media or attachment question -- registered its nodes bare. So on
    # every non-SQL route there was no step budget, no deadline check
    # and, most visibly, no cancellation checkpoint: a cancelled or
    # timed-out request kept retrieving and answering to completion
    # because nothing between the router and synthesis ever looked at
    # the envelope. Wrapping them puts those three checks on every
    # route, at the same node boundaries the SQL path has always had.
    graph.add_node("resolve_context", guard_node("resolve_context", resolve_context_node))
    graph.add_node("router", guard_node("router", router_node))
    graph.add_node("sql_subgraph", guard_node("sql_subgraph", sql_subgraph_node))
    graph.add_node("document_rag", guard_node("document_rag", document_rag_node))
    graph.add_node("policy_rag", guard_node("policy_rag", policy_rag_node))
    graph.add_node("web_search", guard_node("web_search", web_search_node))
    graph.add_node("generation", guard_node("generation", generation_node))
    graph.add_node("media_search", guard_node("media_search", media_search_node))
    graph.add_node("attachments", guard_node("attachments", attachment_node))
    graph.add_node("synthesis", guard_node("synthesis", synthesis_node))

    # `resolve_context` is the entry point, not `router`: the router picks
    # sources from the question's text, and so do web search, both RAG
    # collections and media search, so the standalone form has to exist
    # before any of them run. See `resolve_context_node`.
    graph.set_entry_point("resolve_context")
    graph.add_conditional_edges(
        "resolve_context",
        route_after_context,
        {
            "router": "router",
            # Both end the run before a router call or any retrieval is
            # spent: a reference nothing could pin down, and a resolved
            # question request governance refuses. Straight to END rather
            # than through `synthesis`, which has nothing to compose --
            # `status` and `clarification_message`/`rejection_message` are
            # already set, and the API reads them directly.
            "needs_clarification": END,
            "rejected": END,
        },
    )
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {
            "sql_subgraph": "sql_subgraph",
            "document_rag": "document_rag",
            "policy_rag": "policy_rag",
            "web_search": "web_search",
            "generation": "generation",
            "media_search": "media_search",
            "attachments": "attachments",
            # The no-retrieval route. `synthesis` is both a conditional
            # destination here and the unconditional successor of every
            # source node below -- LangGraph is fine with that, and the
            # alternative (a dedicated pass-through node that only forwards
            # to synthesis) would add a node to the graph for nothing.
            "synthesis": "synthesis",
        },
    )
    for destination in (
        "sql_subgraph",
        "document_rag",
        "policy_rag",
        "web_search",
        "generation",
        "media_search",
        "attachments",
    ):
        graph.add_edge(destination, "synthesis")
    graph.add_edge("synthesis", END)

    return graph.compile()


def run_orchestrated(
    question: str,
    conversation_history: list[ConversationExchange] | None = None,
    enable_insight: bool = True,
    session_id: str | None = None,
    caller_roles: tuple[str, ...] = (),
    caller_subject: str | None = None,
    attachment_ids: list[str] | None = None,
    selected_sources: list[str] | None = None,
    model: str | None = None,
    tenant_id: str | None = None,
    forecast_horizon: int | None = None,
    model_explicitly_selected: bool = False,
    envelope: object | None = None,
) -> AgentState | OrchestratorState:
    """Routes a question to one or more sources and returns the combined result.

    Args:
        question: The user's natural-language question -- passed through
            unmodified to whichever source(s) end up handling it (each
            source does its own input handling; `agent.graph.run_agent`
            already sanitizes on the SQL path exactly as it always has).
        conversation_history: Same shape and meaning as
            `agent.graph.run_agent`'s parameter of the same name.
        enable_insight: Same shape and meaning as `agent.graph.run_agent`'s
            parameter of the same name -- only consulted by the SQL
            destination today.
        session_id: A real (if untrusted) per-conversation correlation
            token -- `api/main.py`'s `/ask` passes its own
            `AskRequest.session_id`. Used only to scope `router_node`'s
            session-level expensive-source cost ceiling (see
            `agent.rate_limit.get_session_expensive_source_limiter`);
            `None` (the default, e.g. for `eval/runner.py` or standalone
            scripts) simply means that ceiling doesn't apply.
        caller_roles: Same shape and meaning as `agent.graph.run_agent`'s
            parameter of the same name -- also consulted by
            `router_node`'s per-source authorization check (2026 Phase 2
            security review, closing the "no authorization layer between
            routing and execution" gap -- see `docs/AUTHORIZATION.md`).
        caller_subject: The authenticated caller's `security.oidc
            .AuthIdentity.subject`, when a real one exists (OIDC mode).
            `router_node` prefers this over `session_id` to scope the
            session-level expensive-source cost ceiling, since unlike
            `session_id` it can't be reset by the caller at will.
        attachment_ids: Files the caller attached directly to this question
            (`AskRequest.attachment_ids`) -- `None`/empty means no
            attachments, the common case and the only case that preserves
            this function's pre-existing "flag off -> call run_agent
            directly" short-circuit below. A non-empty value makes this
            function run the orchestrator graph *even when*
            `Settings.enable_multi_source_router` is off -- attaching a
            file is a per-request opt-in the caller makes explicitly, not a
            standing multi-source-routing decision, so it must not require
            also opting into the (unrelated) multi-source router feature.
            See `agent.orchestrator.nodes.attachment_node`/`attachments/graph.py`.
        selected_sources: Source name(s) this question is restricted to
            (`AskRequest.sources`), already validated for name correctness
            by `api.schemas`, or None for no restriction. A non-None value
            makes this graph run even with
            `Settings.enable_multi_source_router` off, for the same reason
            a non-empty `attachment_ids` does -- see the short-circuit
            below. Threaded to `router_node` via
            `OrchestratorState["selected_sources"]`.

            SECURITY: a filter, never a grant. See
            `agent.orchestrator.nodes._apply_source_selection`; the
            pre-existing role check in `router_node` still runs on
            whatever survives the filter.
        model: Same shape and meaning as `agent.graph.run_agent`'s parameter
            of the same name -- already validated by `api/main.py`'s `/ask`
            handler before this function is ever called. `None` uses
            `Settings.ollama_model`. Threaded through to `sql_subgraph_node`
            via `OrchestratorState["selected_model"]` on the multi-source
            path; passed directly to `run_agent` on the short-circuit path
            below.
        tenant_id: Same shape and meaning as `agent.graph.run_agent`'s
            parameter of the same name -- forwarded unchanged on both the
            short-circuit path (directly to `run_agent`) and the
            multi-source path (`OrchestratorState["tenant_id"]`). Read
            (Prompt 20) by `retrieve_schema_node`'s database routing,
            `retrieve_golden_examples_node`, `retrieve_business_context_node`,
            `generate_recommendations_node`'s performance snapshot, and the
            per-tenant metrics rollup -- plus bound here as the tenant every
            `security.audit_log` event from this run is stamped with.
        model_explicitly_selected: Same shape and meaning as
            `agent.graph.run_agent`'s parameter of the same name (Prompt
            47) -- whether `model` above is the caller's own explicit
            choice rather than the server's resolved default. Forwarded
            unchanged on both paths: directly to `run_agent` on the
            short-circuit path, and via
            `OrchestratorState["model_explicitly_selected"]` on the
            multi-source path, which `sql_subgraph_node` reads back.
        forecast_horizon: Same shape and meaning as `agent.graph.run_agent`'s
            parameter of the same name -- already validated by `api/main.py`'s
            `/ask` handler. Passed directly to `run_agent` on the
            short-circuit path below; threaded through
            `OrchestratorState["forecast_horizon"]` to `sql_subgraph_node` on
            the multi-source path.

    Returns:
        When `Settings.enable_multi_source_router` is off AND no
        attachments were given, exactly what `agent.graph.run_agent`
        returns (an `AgentState`). Otherwise an `OrchestratorState` -- a
        superset of `AgentState`'s keys, plus `route_decision`,
        `sources_used`, and (when 2+ sources fired) `synthesized_answer` --
        so `state["status"]`, `state["sql"]`, etc. are readable identically
        either way.
    """
    settings = get_settings()
    has_attachments = bool(attachment_ids)
    # A caller-supplied source restriction breaks the short-circuit for the
    # same reason a non-empty attachment_ids does: there is no other path
    # that would apply the filter or report an unhonorable selection, and
    # restricting sources is a per-request choice rather than a standing
    # routing decision. `sources=None` (every caller predating the field,
    # and the UI whenever the user has not narrowed anything) still takes
    # the unchanged direct-to-run_agent path below.
    has_source_selection = bool(selected_sources)
    if not settings.enable_multi_source_router and not has_attachments and not has_source_selection:
        return run_agent(
            question,
            conversation_history,
            enable_insight,
            caller_roles,
            model,
            tenant_id,
            forecast_horizon,
            model_explicitly_selected,
            envelope=envelope,  # type: ignore[arg-type]
        )

    logger.info(
        "Starting orchestrated run for question=%r (attachment_count=%d)",
        question,
        len(attachment_ids or []),
    )
    compiled_graph = build_orchestrator_graph()
    # OrchestratorState inherits every AgentState field (see state.py), so
    # its accumulator channels (error_history/attempt_history/stage_timings/
    # sources_used, each an `operator.add` reducer) are initialized here too
    # -- mirroring agent.graph.run_agent's own initial_state exactly, even
    # on the common single-branch path. Same defensive convention, same
    # reason: never rely on a reducer channel's implicit empty state.
    initial_state: OrchestratorState = {
        "question": question,
        "caller_roles": caller_roles,
        "caller_subject": caller_subject,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "pending_attachment_ids": attachment_ids or [],
        "selected_sources": selected_sources or None,
        "source_selection_notice": None,
        "rejection_reason": None,
        "rejection_message": None,
        "rate_limit_message": None,
        "conversation_history": conversation_history or [],
        "followup_classification": None,
        "followup_resolved_against": None,
        # Filled by `resolve_context_node` (the entry point) and read by
        # the router, every source node, and `sql_subgraph_node`, which
        # forwards them so the SQL graph reuses this resolution rather
        # than making a second one.
        "resolved_question": None,
        "context_resolution": None,
        "clarification_message": None,
        "selected_database": None,
        "selected_model": model,
        "model_explicitly_selected": model_explicitly_selected,
        "forecast_horizon": forecast_horizon,
        "forecast_result": None,
        "enable_insight": enable_insight,
        "insight": None,
        "insight_summary": None,
        "schema_anomaly_tables": [],
        "cost_estimate": None,
        "cost_notice": None,
        "low_confidence_notice": None,
        "retry_count": 0,
        "error_history": [],
        "attempt_history": [],
        "last_error_category": None,
        "failure_explanation": None,
        "status": "pending",
        # Threaded so the SQL subgraph (`sql_source_node` -> `run_agent`)
        # runs under the same execution budget and deadline as the
        # single-source path -- otherwise the multi-source route would
        # be the one way to get an unguarded run.
        "harness_envelope": envelope,
        "harness_termination_reason": None,
        "stage_timings": [],
        "route_decision": None,
        "sources_used": [],
        "document_result": None,
        "policy_result": None,
        "web_result": None,
        "generation_result": None,
        "attachment_result": None,
        "synthesized_answer": None,
    }
    # Prompt 20: binds this run's tenant for every `security.audit_log
    # .log_security_event` any routed source emits (document/policy RAG, web
    # search, media, attachments -- not just the SQL path, which
    # `agent.graph.run_agent` binds for itself). Nested binding is safe:
    # `run_agent` sets and resets its own token inside this one.
    audit_token = set_audit_tenant_id(tenant_id)
    # Binds this run's envelope on the worker thread, for the same reason
    # and with the same reset discipline as `agent.graph.run_agent`: the
    # `/ask` pool is a raw `ThreadPoolExecutor`, which does not copy
    # `contextvars` across `submit`. Until this existed, only the SQL
    # subgraph bound it, so every LLM call a *non-SQL* route made
    # (`rag.llm.call_ollama`, reached from the router, both RAG
    # subgraphs, web search, media search and the general-knowledge
    # answer) ran with no deadline clamp and counted against no
    # per-execution model-call budget -- `LOP-004`. Nested binding is
    # safe: `run_agent` sets and resets its own token inside this one.
    envelope_token = set_current_envelope(envelope)  # type: ignore[arg-type]
    try:
        final_state = compiled_graph.invoke(initial_state)
    except HarnessTerminated as exc:
        # An orderly stop the harness demanded -- a budget ran out, the
        # deadline passed, or the caller cancelled. Converted into the
        # same terminal shape a normally-failed run returns, exactly as
        # `agent.graph.run_agent` does, because letting it escape would
        # turn a designed termination into an unhandled 500. Before
        # `LOP-003` wrapped the nodes above there was nothing here to
        # catch; now there is, so this is not optional.
        if envelope is None:  # pragma: no cover - guard_node only raises with one
            raise
        final_state = {**initial_state, **terminal_state_for(envelope, exc)}
    except ProviderUnavailable as exc:
        # Same designed terminal condition `agent.graph.run_agent`
        # handles, caught here too because a non-SQL route never reaches
        # that function -- a document, policy, web or media question
        # that trips the breaker would otherwise still be an HTTP 500
        # (`LOP-004`).
        logger.error("Orchestrated run refused: the %s circuit breaker is open", exc.provider)
        final_state = {
            **initial_state,
            "status": "failed",
            "failure_explanation": exc.safe_message,
        }
    except GraphRecursionError:
        # This graph is acyclic -- `resolve_context -> router ->` a
        # parallel fan-out `-> synthesis -> END` -- so this is
        # unreachable today. Caught for the same reason
        # `agent.graph.run_agent` catches it: a future re-routing edge
        # would otherwise make the failure an HTTP 500 rather than a
        # refusal, and `LOP-001` is this repo's evidence that the
        # difference is worth a few lines.
        logger.error(
            "Orchestrated run hit LangGraph's recursion_limit -- the orchestrator "
            "graph has grown a cycle; failing the run cleanly rather than raising"
        )
        final_state = {
            **initial_state,
            "status": "failed",
            "failure_explanation": RECURSION_LIMIT_MESSAGE,
        }
    finally:
        reset_current_envelope(envelope_token)
        reset_audit_tenant_id(audit_token)
    logger.info(
        "Orchestrated run finished: status=%s sources_used=%s",
        final_state.get("status"),
        final_state.get("sources_used"),
    )
    return final_state
