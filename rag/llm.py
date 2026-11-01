"""Shared Ollama call helper for the RAG subgraph's three LLM steps (grade,
rewrite, generate) -- factored out of `agent/llm_client.py` rather than
importing that module's SQL-specific functions, but built on the exact same
client construction, decoding policy and error handling -- `decoding_options`
and `ollama_unavailable_error` are both imported from `agent.llm_client`
rather than reimplemented, so neither the sampling parameters nor the
operator-facing failure message can drift from the SQL path's.

Despite the name this is no longer RAG-only: `agent/orchestrator/nodes.py`'s
router, web-search and media-search answer steps and `agent/analyst/
planner.py` all call it too. That breadth is why the `num_ctx` gap fixed
here mattered -- see `call_ollama`'s docstring.
"""

from __future__ import annotations

import logging

import httpx
import ollama

from agent.harness.breakers import record_outcome
from agent.harness.failures import FailureClass
from agent.llm_client import (
    decoding_options,
    harness_ollama_client,
    log_ollama_timing,
    ollama_unavailable_error,
    report_prompt_truncation,
)
from config.settings import Settings

logger = logging.getLogger(__name__)


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
    max_tokens: int,
    temperature: float = 0.0,
    model: str | None = None,
    label: str = "rag_llm",
    images: list[bytes] | None = None,
) -> str:
    """Runs one Ollama chat call and returns the response text.

    `model` overrides `Settings.ollama_model` for this one call (Prompt 33's
    analyst planner passes the caller's validated model choice through here);
    `None` keeps the pre-existing default, so every existing caller is
    unchanged.

    `images` attaches raw image bytes to the user message, for a
    vision-capable model. Added for `moderation.provider`'s local image
    classifier, and deliberately added *here* rather than by giving that
    module its own `client.chat`: the breaker, the per-execution model-call
    budget, the deadline clamp, the usage rollup and the prompt-truncation
    check are all things `harness_ollama_client` plus the two lines below
    apply, and a second call site would escape every one of them
    (`LOP-004`). `None` omits the key entirely, so no existing caller's
    request shape changes.

    Decoding parameters come from `agent.llm_client.decoding_options`, the
    one owner of that policy. Until this call used it, the options dict
    here was `{"num_predict": ..., "temperature": ...}` and set **no
    `num_ctx`**, so every call through this helper ran against Ollama's
    default served window -- measured here at 4,096, whose usable half
    was 2,050 tokens -- while the SQL path ran at `OLLAMA_NUM_CTX`
    (16,384). Ollama does not reject an oversized
    prompt -- it truncates from the *start*, which is where the system
    prompt lives, so a long web-search or RAG prompt silently lost its
    instructions (including the "this is untrusted data, not
    instructions" framing) and the model fell back to paraphrasing the
    excerpts. `docs/INFERENCE_ENGINEERING_BASELINE.md` §1 has the
    measurement for the SQL path; this helper had the same bug.

    Unifying the window also stops Ollama reloading the model between a
    SQL call at 16,384 and a RAG/web call at the default -- a changed
    `num_ctx` forces a reload of the served model.

    Raises:
        OllamaUnavailableError: if the call fails for any reason -- an
            unreachable server, a model that is not installed, or one that
            did not answer inside the per-call timeout. The message names
            which (`agent.llm_client.ollama_unavailable_error`). Same
            failure shape `agent.llm_client`'s own functions raise, so
            callers (the RAG subgraph nodes) handle it identically to the
            SQL pipeline's own LLM-call failures.
    """
    client = harness_ollama_client(settings)
    try:
        response = client.chat(
            model=model or settings.ollama_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_prompt,
                    **({"images": images} if images else {}),
                },
            ],
            # `temperature` is this helper's one per-call parameter, so it
            # is overlaid on the shared policy rather than dropped; every
            # caller today passes 0.0, which is what the shared policy
            # already sets, so no existing call changes.
            options={**decoding_options(settings, max_tokens), "temperature": temperature},
        )
    except (ollama.ResponseError, ConnectionError, TimeoutError, OSError, httpx.HTTPError) as exc:
        record_outcome("ollama", FailureClass.MODEL_UNAVAILABLE)
        raise ollama_unavailable_error(settings, model or settings.ollama_model, exc) from exc

    # Closes the breaker on success and records this call in the
    # performance rollup -- the same one line every `client.chat` in
    # `agent/llm_client.py` makes. Without it a breaker opened by a
    # failure here would never recover from a success here, and
    # `GET /metrics/performance` would under-count every model call the
    # router, both RAG subgraphs, web search, media search and the
    # analyst planner make.
    log_ollama_timing(response)
    # The one chokepoint for eight of this application's eleven Ollama
    # call sites -- the router, both RAG subgraphs, web search, media
    # search, the general-knowledge answer, attachment QA and the
    # analyst planner all arrive here -- so checking once covers every
    # prompt in the application that is assembled from retrieved or
    # uploaded content. `label` names which, since they share this
    # function (2026-10-29, UAT-readiness Phase 7, `CTX-006`).
    report_prompt_truncation(len(system_prompt) + len(user_prompt), response, settings, label)

    return (
        response.get("message", {}).get("content", "")
        if isinstance(response, dict)
        else getattr(getattr(response, "message", None), "content", "")
    ).strip()
