"""Shared Ollama call helper for the RAG subgraph's three LLM steps (grade,
rewrite, generate) -- factored out of `agent/llm_client.py` rather than
importing that module's SQL-specific functions, but built on the exact same
client construction, decoding policy and error handling (see
`OllamaUnavailableError`'s use here, and `decoding_options`' below, both
matching `agent.llm_client`'s own).

Despite the name this is no longer RAG-only: `agent/orchestrator/nodes.py`'s
router, web-search and media-search answer steps and `agent/analyst/
planner.py` all call it too. That breadth is why the `num_ctx` gap fixed
here mattered -- see `call_ollama`'s docstring.
"""

from __future__ import annotations

import logging

import httpx
import ollama

from agent.exceptions import OllamaUnavailableError
from agent.llm_client import decoding_options
from config.settings import Settings

logger = logging.getLogger(__name__)


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
    max_tokens: int,
    temperature: float = 0.0,
    model: str | None = None,
) -> str:
    """Runs one Ollama chat call and returns the response text.

    `model` overrides `Settings.ollama_model` for this one call (Prompt 33's
    analyst planner passes the caller's validated model choice through here);
    `None` keeps the pre-existing default, so every existing caller is
    unchanged.

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
        OllamaUnavailableError: if the Ollama server can't be reached --
            same failure shape `agent.llm_client`'s own functions raise, so
            callers (the RAG subgraph nodes) handle it identically to the
            SQL pipeline's own LLM-call failures.
    """
    client = ollama.Client(
        host=settings.ollama_host, timeout=settings.ollama_request_timeout_seconds
    )
    try:
        response = client.chat(
            model=model or settings.ollama_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # `temperature` is this helper's one per-call parameter, so it
            # is overlaid on the shared policy rather than dropped; every
            # caller today passes 0.0, which is what the shared policy
            # already sets, so no existing call changes.
            options={**decoding_options(settings, max_tokens), "temperature": temperature},
        )
    except (ollama.ResponseError, ConnectionError, TimeoutError, OSError, httpx.HTTPError) as exc:
        raise OllamaUnavailableError(
            f"Could not reach Ollama at {settings.ollama_host} with model "
            f"'{settings.ollama_model}': {exc}."
        ) from exc

    return (
        response.get("message", {}).get("content", "")
        if isinstance(response, dict)
        else getattr(getattr(response, "message", None), "content", "")
    ).strip()
