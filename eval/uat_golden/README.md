# The UAT golden question set

Added 2026-10-28 by the UAT-readiness audit, Phase 5
(`docs/uat-readiness/phase-5-hallucination.md`).

## What this is

A versioned set of questions tied to the journeys in
[`docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md`](../../docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md),
so that "did this release change how the product answers?" has an answer
that is a dataset rather than a memory. It reuses
`eval.schema.BenchmarkCase`, `eval.dataset_loader.load_benchmark` and
`eval.evaluators` unchanged — there is no second dataset format and no
second runner.

It lives in **its own directory** rather than in `eval/benchmark/`
because `load_benchmark` merges every `*.yaml` in whatever directory it
is given. Dropping these cases in beside the existing benchmark files
would silently change every number
[`docs/EVALUATION_CURRENT.md`](../../docs/EVALUATION_CURRENT.md) reports,
which is a different dataset answering a different question.

```python
from pathlib import Path
from eval.dataset_loader import load_benchmark

dataset = load_benchmark(Path("eval/uat_golden"))
```

## What is machine-checkable today, and what is not

**Be precise about this** — a gate that is believed to assert more than
it does is worse than no gate.

| Case kind | Checkable without a live environment? | How |
|---|---|---|
| Behaviour (refusal, cap, disclosure, clarification) | **Yes** | `expected_behavior` + `expect_rejection_reason` are compared against the terminal `AgentState`; no knowledge of the target schema is needed |
| Structure (which tables the SQL must touch) | **Yes**, given a live DB | `expected_tables` is matched against the executed SQL text |
| Accuracy (is the answer *right*) | **No, not yet** | needs `expected_sql`, which `eval.evaluators.fetch_gold_result` executes to derive a gold result set |

Most accuracy cases here deliberately carry **no `expected_sql`**. The
audit that authored them had no introspected schema for the UAT
databases, and inventing gold SQL that does not run would make this set
actively misleading — `eval/benchmark/easy.yaml`'s own header records
that every one of *its* `expected_sql` values was executed against a
live database during authoring, and that bar is the right one.

**So the first task for whoever owns the UAT environment is to fill in
`expected_sql` for every case whose `notes` say `GOLD SQL REQUIRED`.**
Until then this set is a behaviour and structure gate, not an accuracy
gate, and `docs/uat-readiness/phase-5-hallucination.md` says so in its
verdict.

## Dialects

Each case names the dialect it is authored for. The questions are
natural language and therefore dialect-independent; the `dialect` field
records which engine the case's *expected SQL and expected tables* are
written against, because `TOP` versus `LIMIT` and the catalog names
differ. Both `mssql` and `postgresql` are represented so that a
Postgres UAT environment is not left running a SQL-Server-only set.

Every connection configured on the machine this set was authored on is
`mssql`, so the `postgresql` cases have **never been executed** here.
That is recorded rather than hidden.

## Running it

Needs a live database and Ollama, like every other `eval/` dataset:

```powershell
.venv\Scripts\Activate.ps1
python -m scripts.run_benchmark --dataset-dir eval/uat_golden
```

`tests/test_uat_golden_set.py` is the part that runs in CI. It asserts
the set's *integrity* — that it loads, that ids are unique, that every
journey tag is a real journey parsed out of the UAT script itself, that
both dialects appear, and that a refusal case always names the reason it
expects. It asserts nothing about accuracy, which no mocked test can.
