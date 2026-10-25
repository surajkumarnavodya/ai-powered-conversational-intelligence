"""Loads the labelled corpus and scores `resolve_context` against it.

See this package's `__init__.py` for what each metric is for and why this
benchmark can run without infrastructure.

Deliberately has no LLM tier in the loop. The deterministic layer is what
runs for the overwhelming majority of real questions (and tier 2 is off by
default -- `Settings.enable_context_resolution_llm`), so measuring it
alone is measuring what users actually get. A tier-2 figure would also not
be reproducible: it would depend on which local model happened to be
installed.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent.context_resolution import ContextRelation, resolve_context
from agent.state import ConversationExchange

CASES_PATH = Path(__file__).resolve().parent / "cases.yaml"

#: The window the benchmark resolves against. Matches
#: `Settings.context_resolution_recent_turns`' default so the measured
#: numbers describe the shipped configuration, not a tuned one.
RECENT_TURNS = 6


@dataclass(frozen=True)
class Case:
    """One labelled message. Mirrors `cases.yaml`'s own fields."""

    id: str
    history: tuple[ConversationExchange, ...]
    message: str
    relation: ContextRelation
    antecedent: int | None = None
    resolved_contains: tuple[str, ...] = ()
    resolved_excludes: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class CaseOutcome:
    """What one case produced, and every way it could have been wrong.

    Attributes:
        case: The case as labelled.
        predicted: The relation the resolver assigned.
        resolved_question: The standalone form it produced.
        relation_ok: Whether the relation matched the label.
        antecedent_ok: None when the case does not label one.
        contains_ok: None when the case names no required substrings.
        leaked: None when the case names no forbidden substrings; True
            when one appeared anyway.
        missing: The required substrings that were absent, for the report.
        leaked_terms: The forbidden substrings that appeared.
        duration_ms: Wall-clock time for this one resolution.
    """

    case: Case
    predicted: ContextRelation
    resolved_question: str
    relation_ok: bool
    antecedent_ok: bool | None
    contains_ok: bool | None
    leaked: bool | None
    missing: tuple[str, ...]
    leaked_terms: tuple[str, ...]
    duration_ms: float

    @property
    def passed(self) -> bool:
        """Whether every check this case actually makes came out right."""
        return (
            self.relation_ok
            and self.antecedent_ok is not False
            and self.contains_ok is not False
            and self.leaked is not True
        )


@dataclass(frozen=True)
class RelationMetrics:
    """Precision, recall and F1 for one relation."""

    relation: ContextRelation
    support: int
    predicted: int
    true_positives: int

    @property
    def precision(self) -> float:
        return self.true_positives / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.support if self.support else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass(frozen=True)
class BenchmarkOutcome:
    """Everything one run measured."""

    outcomes: tuple[CaseOutcome, ...]
    per_relation: tuple[RelationMetrics, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def exact_match(self) -> float:
        """Fraction of cases where every check the case makes passed.

        The strictest single number: relation, antecedent, required
        substrings and absence of leaked ones, all at once.
        """
        return self._ratio(o.passed for o in self.outcomes)

    @property
    def relation_accuracy(self) -> float:
        return self._ratio(o.relation_ok for o in self.outcomes)

    @property
    def macro_f1(self) -> float:
        """Unweighted mean F1 over relations that appear in the corpus.

        Unweighted on purpose: `followup` is the most common label by far,
        and a weighted average would let a resolver that gets follow-ups
        right and corrections wrong look almost perfect.
        """
        scored = [m for m in self.per_relation if m.support]
        return statistics.mean(m.f1 for m in scored) if scored else 0.0

    @property
    def entity_resolution_accuracy(self) -> float:
        """Of the cases that require substrings, how many carried them."""
        checked = [o for o in self.outcomes if o.contains_ok is not None]
        return self._ratio(o.contains_ok for o in checked) if checked else 1.0

    @property
    def context_leakage_rate(self) -> float:
        """Of the cases that forbid substrings, how many leaked. Target 0."""
        checked = [o for o in self.outcomes if o.leaked is not None]
        return self._ratio(o.leaked for o in checked) if checked else 0.0

    @property
    def antecedent_accuracy(self) -> float:
        """Of the cases that label an antecedent, how many picked it."""
        checked = [o for o in self.outcomes if o.antecedent_ok is not None]
        return self._ratio(o.antecedent_ok for o in checked) if checked else 1.0

    def accuracy_for(self, *relations: ContextRelation) -> float:
        """Relation accuracy restricted to the labelled relations given.

        Used for the two decisions a user notices most -- a new topic
        answered independently, and an unresolvable reference asked about
        rather than guessed.
        """
        wanted = set(relations)
        checked = [o for o in self.outcomes if o.case.relation in wanted]
        return self._ratio(o.relation_ok for o in checked) if checked else 1.0

    @property
    def median_latency_ms(self) -> float:
        return statistics.median(o.duration_ms for o in self.outcomes) if self.outcomes else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.outcomes:
            return 0.0
        ordered = sorted(o.duration_ms for o in self.outcomes)
        return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]

    @property
    def failures(self) -> tuple[CaseOutcome, ...]:
        return tuple(o for o in self.outcomes if not o.passed)

    @staticmethod
    def _ratio(flags: Iterable[bool | None]) -> float:
        values = [bool(f) for f in flags]
        return sum(values) / len(values) if values else 0.0


def _exchange(raw: dict[str, Any]) -> ConversationExchange:
    exchange: ConversationExchange = {
        "question": str(raw["q"]),
        "sql": raw.get("sql"),
        "tables": list(raw.get("tables") or []),
        "status": str(raw.get("status", "succeeded")),
    }
    answer = raw.get("a")
    if answer:
        exchange["answer"] = str(answer)
    return exchange


def load_cases(path: Path | None = None) -> tuple[Case, ...]:
    """Loads and validates the corpus.

    Raises rather than skipping on a malformed case: a silently dropped
    case would quietly shrink the corpus and inflate every ratio computed
    from it, which is the one failure mode a benchmark must not have.
    """
    raw = yaml.safe_load((path or CASES_PATH).read_text(encoding="utf-8"))
    cases: list[Case] = []
    seen: set[str] = set()
    for entry in raw["cases"]:
        case_id = str(entry["id"])
        if case_id in seen:
            raise ValueError(f"duplicate case id {case_id!r}")
        seen.add(case_id)
        cases.append(
            Case(
                id=case_id,
                history=tuple(_exchange(turn) for turn in entry.get("history") or []),
                message=str(entry["message"]),
                relation=ContextRelation(str(entry["relation"])),
                antecedent=entry.get("antecedent"),
                resolved_contains=tuple(entry.get("resolved_contains") or []),
                resolved_excludes=tuple(entry.get("resolved_excludes") or []),
                notes=str(entry.get("notes", "")).strip(),
            )
        )
    return tuple(cases)


def _score(case: Case) -> CaseOutcome:
    started = time.perf_counter()
    result = resolve_context(case.message, list(case.history), recent_turns=RECENT_TURNS, llm=None)
    duration_ms = (time.perf_counter() - started) * 1000

    resolved = result.resolved_question
    lowered = resolved.casefold()

    missing = tuple(term for term in case.resolved_contains if term.casefold() not in lowered)
    leaked_terms = tuple(term for term in case.resolved_excludes if term.casefold() in lowered)

    antecedent_ok: bool | None = None
    if case.antecedent is not None:
        antecedent_ok = result.antecedent_index == case.antecedent

    return CaseOutcome(
        case=case,
        predicted=result.relation,
        resolved_question=resolved,
        relation_ok=result.relation is case.relation,
        antecedent_ok=antecedent_ok,
        contains_ok=(not missing) if case.resolved_contains else None,
        leaked=bool(leaked_terms) if case.resolved_excludes else None,
        missing=missing,
        leaked_terms=leaked_terms,
        duration_ms=duration_ms,
    )


def run_benchmark(cases: Sequence[Case] | None = None) -> BenchmarkOutcome:
    """Scores every case and aggregates the metrics."""
    corpus = tuple(cases) if cases is not None else load_cases()
    outcomes = tuple(_score(case) for case in corpus)

    per_relation = tuple(
        RelationMetrics(
            relation=relation,
            support=sum(1 for o in outcomes if o.case.relation is relation),
            predicted=sum(1 for o in outcomes if o.predicted is relation),
            true_positives=sum(
                1 for o in outcomes if o.predicted is relation and o.case.relation is relation
            ),
        )
        for relation in ContextRelation
    )
    return BenchmarkOutcome(outcomes=outcomes, per_relation=per_relation)


def format_report(outcome: BenchmarkOutcome) -> str:
    """A plain-text report, for the CLI script and for a failure message."""
    lines = [
        "Context resolution benchmark",
        "=" * 64,
        f"cases                      {outcome.total}",
        f"exact match                {outcome.exact_match:.1%}",
        f"relation accuracy          {outcome.relation_accuracy:.1%}",
        f"macro F1                   {outcome.macro_f1:.3f}",
        f"antecedent accuracy        {outcome.antecedent_accuracy:.1%}",
        f"entity resolution          {outcome.entity_resolution_accuracy:.1%}",
        f"context leakage            {outcome.context_leakage_rate:.1%}  (target 0)",
        f"new-topic accuracy         {outcome.accuracy_for(ContextRelation.NEW_TOPIC):.1%}",
        "clarification accuracy     "
        f"{outcome.accuracy_for(ContextRelation.AMBIGUOUS, ContextRelation.MISSING_CONTEXT):.1%}",
        f"latency median / p95       {outcome.median_latency_ms:.3f} / "
        f"{outcome.p95_latency_ms:.3f} ms",
        "",
        f"{'relation':<18}{'support':>8}{'prec':>8}{'recall':>8}{'F1':>8}",
        "-" * 64,
    ]
    for metrics in outcome.per_relation:
        if not metrics.support and not metrics.predicted:
            continue
        lines.append(
            f"{metrics.relation.value:<18}{metrics.support:>8}"
            f"{metrics.precision:>8.2f}{metrics.recall:>8.2f}{metrics.f1:>8.2f}"
        )

    if outcome.failures:
        lines += ["", f"{len(outcome.failures)} case(s) did not pass every check:", "-" * 64]
        for failed in outcome.failures:
            lines.append(f"  {failed.case.id}")
            lines.append(f"    message   {failed.case.message!r}")
            if not failed.relation_ok:
                lines.append(
                    f"    relation  expected {failed.case.relation.value}, "
                    f"got {failed.predicted.value}"
                )
            if failed.antecedent_ok is False:
                lines.append(f"    antecedent expected {failed.case.antecedent}")
            if failed.missing:
                lines.append(f"    missing   {list(failed.missing)}")
            if failed.leaked_terms:
                lines.append(f"    leaked    {list(failed.leaked_terms)}")
            lines.append(f"    resolved  {failed.resolved_question!r}")
    return "\n".join(lines)


__all__ = [
    "CASES_PATH",
    "RECENT_TURNS",
    "BenchmarkOutcome",
    "ContextRelation",
    "Case",
    "CaseOutcome",
    "RelationMetrics",
    "format_report",
    "load_cases",
    "run_benchmark",
]
