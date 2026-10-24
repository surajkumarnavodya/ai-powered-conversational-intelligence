"""Scores conversational context resolution against its labelled corpus.

Unlike `scripts/run_benchmark.py`, this needs **no infrastructure** -- no
database, no Ollama, no vector store -- because
`agent.context_resolution.resolve_context` is a pure function of
(message, history). It runs in well under a second, which is why
`tests/test_context_resolution_benchmark.py` also asserts on it in CI.

    python -m scripts.run_context_resolution_benchmark
    python -m scripts.run_context_resolution_benchmark --json
    python -m scripts.run_context_resolution_benchmark --cases path/to/cases.yaml

Run as a module (`-m`), like `scripts/ingest_schema.py` and
`scripts/rebuild_index.py`: it imports from `eval/`, and `python
scripts/....py` puts `scripts/` on `sys.path` rather than the repo root.

Exits non-zero when any case fails a check it makes, so it is usable as a
gate as well as a report.

`print` is deliberate here: this is one of the standalone CLI scripts
CLAUDE.md 6.2 exempts, meant to be read as terminal output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.context_resolution_benchmark.runner import (
    ContextRelation,
    format_report,
    load_cases,
    run_benchmark,
)


def _as_json(outcome) -> str:
    return json.dumps(
        {
            "cases": outcome.total,
            "exact_match": round(outcome.exact_match, 4),
            "relation_accuracy": round(outcome.relation_accuracy, 4),
            "macro_f1": round(outcome.macro_f1, 4),
            "antecedent_accuracy": round(outcome.antecedent_accuracy, 4),
            "entity_resolution_accuracy": round(outcome.entity_resolution_accuracy, 4),
            "context_leakage_rate": round(outcome.context_leakage_rate, 4),
            "new_topic_accuracy": round(outcome.accuracy_for(ContextRelation.NEW_TOPIC), 4),
            "clarification_accuracy": round(
                outcome.accuracy_for(ContextRelation.AMBIGUOUS, ContextRelation.MISSING_CONTEXT),
                4,
            ),
            "median_latency_ms": round(outcome.median_latency_ms, 4),
            "p95_latency_ms": round(outcome.p95_latency_ms, 4),
            "per_relation": {
                m.relation.value: {
                    "support": m.support,
                    "precision": round(m.precision, 4),
                    "recall": round(m.recall, 4),
                    "f1": round(m.f1, 4),
                }
                for m in outcome.per_relation
                if m.support or m.predicted
            },
            "failures": [
                {
                    "id": f.case.id,
                    "message": f.case.message,
                    "expected_relation": f.case.relation.value,
                    "predicted_relation": f.predicted.value,
                    "missing": list(f.missing),
                    "leaked": list(f.leaked_terms),
                    "resolved_question": f.resolved_question,
                }
                for f in outcome.failures
            ],
        },
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=None, help="Alternative corpus file.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args()

    cases = load_cases(args.cases) if args.cases else load_cases()
    outcome = run_benchmark(cases)

    print(_as_json(outcome) if args.json else format_report(outcome))
    return 1 if outcome.failures else 0


if __name__ == "__main__":
    sys.exit(main())
