"""Unit tests for `scripts/check_secrets_baseline.py` -- CI's blocking
detect-secrets comparison.

This script had no test file. It is the thing standing between a committed
secret and a green build, and it is run only in CI, so a regression in it
would be invisible until it mattered. Added during the UAT-readiness audit
(Phase 2) alongside two fixes to it:

* `OPS-005` -- it compared raw baseline keys, and `detect-secrets` writes
  **OS-native** path separators. A developer running the gate on Windows
  against the Linux-generated committed baseline saw every entry as new.
  That cost this audit a wrong conclusion before the cause was found.
* It printed only "New, un-baselined finding(s) detected" with no detail,
  so a failure could not be acted on without reproducing the whole scan by
  hand. It now names what changed, grouped by what the change means.

Everything here is pure JSON in temp files -- no `detect-secrets` run, no
network, no repository state.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_secrets_baseline import main


#: The shape `detect-secrets` writes. Only the fields the script reads are
#: filled in; the rest of a real baseline is irrelevant to the comparison.
def _baseline(results: dict, generated_at: str = "2026-01-01T00:00:00Z") -> dict:
    return {
        "version": "1.5.0",
        "plugins_used": [{"name": "KeywordDetector"}],
        "generated_at": generated_at,
        "results": results,
    }


def _finding(line: int, hashed: str, kind: str = "Secret Keyword") -> dict:
    return {"type": kind, "line_number": line, "hashed_secret": hashed, "is_verified": False}


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(tmp_path: Path, before: dict, after: dict) -> int:
    return main(
        [
            str(_write(tmp_path, "before.json", before)),
            "--baseline",
            str(_write(tmp_path, "after.json", after)),
        ]
    )


class TestTheGatePasses:
    def test_identical_baselines_pass(self, tmp_path: Path) -> None:
        results = {"tests/a.py": [_finding(10, "aaa")]}

        assert _run(tmp_path, _baseline(results), _baseline(results)) == 0

    def test_a_changed_generated_at_alone_passes(self, tmp_path: Path) -> None:
        """`--baseline` always rewrites the timestamp, on every run.

        Without stripping it, this gate would fail on every build even with
        zero new findings -- which is the whole reason the script exists
        rather than a `git diff --exit-code`.
        """
        results = {"tests/a.py": [_finding(10, "aaa")]}

        exit_code = _run(
            tmp_path,
            _baseline(results, generated_at="2026-01-01T00:00:00Z"),
            _baseline(results, generated_at="2026-10-25T12:34:56Z"),
        )

        assert exit_code == 0

    def test_path_separators_do_not_decide_the_verdict(self, tmp_path: Path) -> None:
        """`OPS-005`. The same findings under either spelling must pass.

        A Linux-generated baseline uses `tests/a.py`; a Windows scan of the
        identical repository writes `tests\\a.py`. Those describe one file,
        so the gate must not call it a new finding.
        """
        linux = {"tests/a.py": [_finding(10, "aaa")]}
        windows = {"tests\\a.py": [_finding(10, "aaa")]}

        assert _run(tmp_path, _baseline(linux), _baseline(windows)) == 0
        assert _run(tmp_path, _baseline(windows), _baseline(linux)) == 0


class TestTheGateFails:
    def test_a_new_secret_hash_fails(self, tmp_path: Path) -> None:
        """The case that must never pass: a value not previously reviewed."""
        before = {"tests/a.py": [_finding(10, "aaa")]}
        after = {"tests/a.py": [_finding(10, "aaa"), _finding(11, "bbb")]}

        assert _run(tmp_path, _baseline(before), _baseline(after)) == 1

    def test_a_secret_in_a_brand_new_file_fails(self, tmp_path: Path) -> None:
        before: dict = {}
        after = {"app/leaked.py": [_finding(3, "ccc", "Private Key")]}

        assert _run(tmp_path, _baseline(before), _baseline(after)) == 1

    def test_a_moved_finding_still_fails(self, tmp_path: Path) -> None:
        """Code movement is benign but still needs a baseline refresh.

        The gate deliberately does not pass this: the baseline on disk no
        longer describes the repository, and letting it drift is how a
        baseline stops being a review record. The *message* distinguishes
        it from a real new secret -- that is what the grouping is for.
        """
        before = {"tests/a.py": [_finding(10, "aaa")]}
        after = {"tests/a.py": [_finding(42, "aaa")]}

        assert _run(tmp_path, _baseline(before), _baseline(after)) == 1

    def test_a_changed_plugin_list_fails(self, tmp_path: Path) -> None:
        """A different detector set means a different question was asked.

        Dropping a plugin would quietly stop looking for a whole class of
        secret while the gate still went green, so it has to fail.
        """
        results = {"tests/a.py": [_finding(10, "aaa")]}
        before = _baseline(results)
        after = _baseline(results)
        after["plugins_used"] = []

        assert _run(tmp_path, before, after) == 1


class TestTheFailureSaysWhatChanged:
    """A blocking gate that fails without a reason cannot be acted on.

    This is not cosmetic. The gate was red at HEAD during this audit and
    the bare message gave no way to tell a leaked credential from a
    renamed test fixture, so diagnosing it meant rebuilding the whole
    comparison by hand.
    """

    def test_a_new_hash_is_reported_as_needing_review(self, tmp_path: Path, capsys) -> None:
        before = {"tests/a.py": [_finding(10, "aaa")]}
        after = {"tests/a.py": [_finding(10, "aaa"), _finding(11, "b" * 40)]}

        _run(tmp_path, _baseline(before), _baseline(after))
        out = capsys.readouterr().out

        assert "REVIEW" in out
        assert "tests/a.py" in out
        # Truncated, never the whole hash: the message is printed into CI
        # logs, and a full hash of a real secret is a crackable artifact.
        assert "b" * 40 not in out
        assert "b" * 12 in out

    def test_a_new_file_is_listed_with_its_detector_and_line(self, tmp_path: Path, capsys) -> None:
        _run(
            tmp_path,
            _baseline({}),
            _baseline({"tests/new_fixture.py": [_finding(7, "ddd")]}),
        )
        out = capsys.readouterr().out

        assert "files newly carrying a finding (1)" in out
        assert "tests/new_fixture.py" in out
        assert "Secret Keyword" in out
        assert "7" in out

    def test_pure_code_movement_is_named_as_such(self, tmp_path: Path, capsys) -> None:
        _run(
            tmp_path,
            _baseline({"tests/a.py": [_finding(10, "aaa")]}),
            _baseline({"tests/a.py": [_finding(99, "aaa")]}),
        )
        out = capsys.readouterr().out

        assert "code movement" in out
        assert "REVIEW" not in out, "code movement must not be reported as a new secret"

    def test_a_removed_file_is_reported_too(self, tmp_path: Path, capsys) -> None:
        """A finding disappearing is also drift worth seeing.

        Usually a deleted or cleaned-up fixture -- but it could equally be
        a file that stopped being scanned, which would silently shrink
        coverage.
        """
        _run(
            tmp_path,
            _baseline({"tests/gone.py": [_finding(1, "eee")]}),
            _baseline({}),
        )
        out = capsys.readouterr().out

        assert "no longer carrying a finding (1)" in out
        assert "tests/gone.py" in out

    def test_a_metadata_change_is_called_out_separately(self, tmp_path: Path, capsys) -> None:
        results = {"tests/a.py": [_finding(10, "aaa")]}
        after = _baseline(results)
        after["plugins_used"] = []

        _run(tmp_path, _baseline(results), after)
        out = capsys.readouterr().out

        assert "metadata outside `results`" in out

    def test_several_kinds_of_change_are_all_reported_in_one_run(
        self, tmp_path: Path, capsys
    ) -> None:
        """One run must not stop at the first group it finds.

        A real refresh typically mixes all of them at once, and a message
        that showed only the first would send someone back for another
        round.
        """
        before = {
            "tests/moved.py": [_finding(10, "aaa")],
            "tests/gone.py": [_finding(1, "bbb")],
        }
        after = {
            "tests/moved.py": [_finding(20, "aaa")],
            "tests/added.py": [_finding(5, "ccc")],
        }

        _run(tmp_path, _baseline(before), _baseline(after))
        out = capsys.readouterr().out

        assert "code movement" in out
        assert "newly carrying" in out
        assert "no longer carrying" in out
