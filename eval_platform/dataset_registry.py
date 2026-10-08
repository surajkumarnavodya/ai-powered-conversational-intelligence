"""Dataset/baseline versioning for the AI Evaluation Benchmarking
platform -- Prompt 46.

Deliberately not a second dataset-storage mechanism: the dataset itself
already lives wherever it always has (`eval/benchmark/*.yaml`,
`eval/security_benchmark/dataset.py`, `eval/baselines
/component_benchmark_latest.json`). `fingerprint_baseline` only computes
a stable, deterministic identifier for *which version* of an
already-existing dataset/baseline a given run was graded against --
the identical `sha256`-of-already-serialized-content convention
`db.schema_introspection.get_schema_fingerprint`/`analytics.predictive
.PredictiveModelMetadata.data_fingerprint` already establish elsewhere
in this codebase, applied here for the first time to an evaluation
dataset/baseline rather than a database schema or a training dataset.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint_baseline(baseline: dict[str, Any]) -> str:
    """A short, stable hash of a baseline/dataset dict's own content --
    `json.dumps(..., sort_keys=True)` first, so key order never changes
    the fingerprint, matching `report_to_baseline_dict`'s own
    deterministic-serialization intent.

    Two calls with the same logical content (regardless of key
    insertion order) always produce the identical fingerprint; any real
    content change (a case added/removed/flipped) always produces a
    different one.
    """
    serialized = json.dumps(baseline, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
