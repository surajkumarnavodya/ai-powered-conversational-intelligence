"""The Data Quality Command Center -- Prompt 41
(`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`).

A new top-level package, a peer of `analytics/`/`optimization/`/
`recommendation/`, not a submodule of any of them -- it answers a
different question than those do ("is this data trustworthy," not "what
does this data show," "what should we decide," or "what should we do
about it"), though it reuses their machinery wherever the same
computation already exists (`analytics.engine.compute_analytics_result`
for per-column null/outlier statistics, `analytics.predictive
.detect_feature_drift` for distribution-stability testing) rather than
reimplementing either.

See `data_quality/models.py` for the full typed contract,
`data_quality/engine.py` for the seven-dimension detection/scoring
engine, and `data_quality/lineage.py` for connecting findings to
affected governed metrics via the semantic catalog.
"""

from __future__ import annotations
