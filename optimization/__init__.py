"""The governed Optimization Engine -- Prompt 39
(`39_OPTIMIZATION_ENGINE_CONTRACT.md`).

An extensible, deterministic decision-optimization framework (pricing,
resource allocation, inventory, workforce, capacity) sitting alongside
`analytics/` (deterministic analytics/forecasting/causal/scenario
engines) and `recommendation/` (the evidence-first recommendation
engine) as a peer top-level package, not a submodule of either -- it
solves a different kind of problem (optimal decision-variable values
subject to constraints) than either already solves, and composes with
both rather than extending one.

See `optimization/models.py` for the full typed contract and
`optimization/engine.py` for the solver itself.
"""

from __future__ import annotations
