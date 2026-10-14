"""A fingerprint of everything that decides what the model sees and does.

### Why this exists

`docs/INFERENCE_ENGINEERING_BASELINE.md` found a defect that had been
live for a long time without anyone noticing: every Ollama call ran
against a 2,048-token window, so the system prompt was being truncated
away. It went unnoticed because **nothing recorded what configuration
produced a given answer**. A quality regression and a configuration
change looked identical in the logs.

This closes that. One short hash over the inputs that determine model
behaviour, carried in telemetry, so "did something change?" is a
lookup rather than an investigation.

### What goes into it, and what deliberately does not

In: the system prompt text, the decoding options, the context budget,
and the retrieval top-k values. Those are exactly the things that
change what the model is shown and how it samples.

Out: anything request-specific -- the question, the schema, the tenant,
the chosen model for one call. A fingerprint that changed per request
would identify a request, not a configuration, and could not answer
"are these two runs comparable?".

Also out: secrets. The inputs are prompt text and numeric tunables; no
`SecretStr` is read, and the output is a hash, so this is safe to log
(CLAUDE.md §5.6).

### Three separate fingerprints, not one

A single combined hash would tell you *that* something changed but not
*what*. Splitting prompt / decoding / retrieval means a telemetry diff
points at the subsystem, which is the difference between a five-minute
and a five-hour investigation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

#: Bumped by hand when the *meaning* of a fingerprint changes -- a new
#: input added, or an existing one redefined. Without it, two hashes
#: computed by different versions of this module would be compared as
#: though they meant the same thing.
FINGERPRINT_SCHEMA_VERSION = "1"

#: Hash prefix length. 12 hex characters is ~48 bits: comfortably
#: collision-free for the handful of distinct configurations a
#: deployment ever has, and short enough to read in a log line.
_DIGEST_CHARS = 12


def _digest(*parts: Any) -> str:
    hasher = hashlib.sha256()
    hasher.update(FINGERPRINT_SCHEMA_VERSION.encode())
    for part in parts:
        hasher.update(b"\x00")
        hasher.update(repr(part).encode("utf-8", errors="replace"))
    return hasher.hexdigest()[:_DIGEST_CHARS]


@dataclass(frozen=True)
class InferenceFingerprint:
    """Three hashes plus the human-readable values behind them.

    `values` exists so a log line can say *what* the decoding
    configuration is, not only that it hashes to `a1b2c3`. It carries
    only numeric tunables and the model name -- never prompt text,
    which would defeat the point of hashing it.
    """

    prompt: str
    decoding: str
    retrieval: str
    values: dict[str, Any]

    @property
    def combined(self) -> str:
        """One hash over all three, for a coarse 'did anything change?'."""
        return _digest(self.prompt, self.decoding, self.retrieval)

    def summary(self) -> dict[str, Any]:
        """Log- and metric-safe. Hashes and numeric tunables only."""
        return {
            "fingerprint_version": FINGERPRINT_SCHEMA_VERSION,
            "prompt_fingerprint": self.prompt,
            "decoding_fingerprint": self.decoding,
            "retrieval_fingerprint": self.retrieval,
            "combined_fingerprint": self.combined,
            **self.values,
        }


def _prompt_fingerprint() -> str:
    """Hashes the system prompt text for every supported dialect.

    Every dialect, not just the configured one: a change to the Oracle
    system prompt should be visible even on a deployment currently
    pointed at SQL Server, because the next deployment to switch
    dialects needs to know the text moved.

    Imported lazily to keep this module importable from anywhere --
    `agent.llm_client` is a heavy import and a fingerprint helper
    should not drag it into every caller's import graph.
    """
    from agent.llm_client import _INSIGHT_SYSTEM_PROMPT, _system_prompt

    texts = [_system_prompt(dialect) for dialect in ("postgres", "mysql", "mssql", "oracle")]
    texts.append(_INSIGHT_SYSTEM_PROMPT)
    return _digest(*texts)


def compute_fingerprint(settings: Any) -> InferenceFingerprint:
    """Fingerprints the inference configuration `settings` describes.

    Reads only public, non-secret fields, and uses `getattr` defaults
    throughout so a settings object missing a newer field still
    produces a fingerprint rather than raising. A fingerprint helper
    that can fail is a fingerprint helper that gets wrapped in
    `try/except` at every call site and then quietly stops working.
    """
    decoding_values = {
        "model": getattr(settings, "ollama_model", None),
        "num_ctx": getattr(settings, "ollama_num_ctx", None),
        "seed": getattr(settings, "ollama_seed", None),
        "top_p": getattr(settings, "ollama_top_p", None),
        "llm_max_tokens": getattr(settings, "llm_max_tokens", None),
    }
    retrieval_values = {
        "schema_top_k": getattr(settings, "schema_top_k", None),
        "golden_examples_top_k": getattr(settings, "golden_examples_top_k", None),
        "retrieval_top_k_tables": getattr(settings, "retrieval_top_k_tables", None),
        "context_budget_max_chars": getattr(settings, "context_budget_max_chars", None),
    }
    return InferenceFingerprint(
        prompt=_prompt_fingerprint(),
        decoding=_digest(sorted(decoding_values.items())),
        retrieval=_digest(sorted(retrieval_values.items())),
        values={**decoding_values, **retrieval_values},
    )


def fingerprint_summary(settings: Any) -> dict[str, Any]:
    """`compute_fingerprint(settings).summary()`, the shape telemetry wants."""
    return compute_fingerprint(settings).summary()


__all__ = [
    "FINGERPRINT_SCHEMA_VERSION",
    "InferenceFingerprint",
    "compute_fingerprint",
    "fingerprint_summary",
]
