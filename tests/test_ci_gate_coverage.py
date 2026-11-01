"""The CI gates cover every first-party package, and cannot fall behind.

Why this file exists (2026-10-29, UAT-readiness Phase 8). Two blocking CI
gates were scoped by a hand-written path list, and one of them had
quietly fallen 29 packages behind the repository:

* `bandit` named twelve packages on the command line (`agent api config db
  security media_gen moderation rag search voice media embeddings`), so it
  never looked at `identity/`, `governance/`, `guardrails/`, `jobs/`,
  `mcp_client/` or roughly twenty-five others -- about 60,000 lines,
  including the whole authentication package. Running it over the full
  tree found 24 findings in that gap, six of them in `identity/`
  (`BP-005`). That gate now reads its exclusions from `[tool.bandit]` in
  `pyproject.toml`, so a new package is covered the day it is created;
  the test below pins that the config keeps it that way.
* `mypy` had no blocking gate at all. It does now, scoped to production
  code, and that step *does* enumerate packages (`BP-001`) -- so the test
  below fails when a tracked top-level package is missing from it.

The lesson both findings share is the one CLAUDE.md 13 draws about
`detect-secrets`: a gate nobody enumerates is a gate nobody measures. A
list in a YAML file is not self-maintaining, so something has to check
it.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: Top-level directories that hold Python but are deliberately outside the
#: production type gate, each for a stated reason.
#:
#: * `tests` -- the 736-error hygiene backlog the report-only `mypy .` step
#:   still measures (CLAUDE.md 13). Blocking on it today would block
#:   everything.
#: * `eval` and `scripts` -- manual harnesses and CLI entry points that are
#:   not imported by a request path. They are still covered by `bandit`,
#:   `ruff`, `black` and the report-only `mypy .`.
_TYPE_GATE_EXEMPT = frozenset({"tests", "eval", "scripts"})


def _tracked_python_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def _tracked_top_level_packages() -> set[str]:
    """Every tracked directory that directly or transitively holds a module."""
    packages = set()
    for path in _tracked_python_files():
        head, _, tail = path.partition("/")
        if tail:
            packages.add(head)
    return packages


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    for step in _workflow()["jobs"][job]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in job {job!r}")


class TestTheProductionTypeGateEnumeratesEveryPackage:
    """`mypy` blocks on production code, and the list stays complete."""

    def test_the_blocking_step_exists_and_is_not_report_only(self) -> None:
        step = _step("test", "Type check, production code (mypy)")
        assert step.get("continue-on-error") is not True, (
            "The production mypy step must block. The report-only `mypy .` "
            "step above it is the one that carries the tests/ backlog."
        )

    def test_every_tracked_package_is_either_gated_or_exempt(self) -> None:
        step = _step("test", "Type check, production code (mypy)")
        listed = set(str(step["run"]).split()) - {"mypy"}
        expected = _tracked_top_level_packages() - _TYPE_GATE_EXEMPT
        missing = sorted(expected - listed)
        assert not missing, (
            "These tracked packages are not covered by the blocking mypy step "
            f"in {_WORKFLOW.name}: {missing}. Add them to that step's `run:` "
            "list, or add them to _TYPE_GATE_EXEMPT here with the reason."
        )

    def test_the_gate_does_not_name_a_package_that_no_longer_exists(self) -> None:
        # The opposite drift: a renamed or deleted package left in the list
        # makes the step fail with "no .py[i] files in directory", which
        # reads as a tooling problem rather than a stale gate.
        step = _step("test", "Type check, production code (mypy)")
        listed = set(str(step["run"]).split()) - {"mypy"}
        stale = sorted(listed - _tracked_top_level_packages())
        assert (
            not stale
        ), f"The blocking mypy step names packages that hold no tracked Python: {stale}"

    def test_the_exempt_set_is_not_a_place_to_hide_production_code(self) -> None:
        # A regression guard on this file rather than on the workflow: the
        # cheapest way to make the test above pass is to widen the exemption.
        assert frozenset({"tests", "eval", "scripts"}) == _TYPE_GATE_EXEMPT, (
            "The type-gate exemption grew. Every entry needs a documented "
            "reason in this module's docstring, and a production package is "
            "never a valid entry."
        )


class TestTheSastGateIsScopedByExclusion:
    """`bandit` covers the tree minus a short exclude list, not a package list."""

    def test_the_step_uses_the_pyproject_config(self) -> None:
        run = str(_step("test", "SAST (bandit)")["run"])
        assert "-c pyproject.toml" in run, (
            "The bandit step must read [tool.bandit] from pyproject.toml. A "
            "package list on the command line is what left 29 packages "
            "unscanned (BP-005)."
        )
        assert "-r ." in run, "bandit must walk the whole tree, not named packages."

    def test_the_exclude_list_drops_no_first_party_package_except_tests(self) -> None:
        config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        excluded = {entry.strip("./") for entry in config["tool"]["bandit"]["exclude_dirs"]}
        first_party = _tracked_top_level_packages()
        dropped = sorted(excluded & first_party)
        assert dropped == ["tests"], (
            "bandit's exclude list drops first-party code beyond tests/: "
            f"{dropped}. tests/ is excluded because pytest's own `assert` "
            "makes B101 noise there; nothing else has that excuse."
        )

    @pytest.mark.parametrize(
        "package", ["identity", "governance", "guardrails", "jobs", "mcp_client"]
    )
    def test_the_packages_the_old_list_missed_are_now_in_scope(self, package: str) -> None:
        # Named explicitly, because these are the ones the blind spot hid:
        # `identity` is the authentication package and accounted for six of
        # the 24 findings.
        config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        excluded = {entry.strip("./") for entry in config["tool"]["bandit"]["exclude_dirs"]}
        assert package in _tracked_top_level_packages()
        assert package not in excluded


class TestTheFrontendSuiteRuns:
    """`frontend/` ships 1,508 tests; CI ran none of them until `BP-008`."""

    def test_the_frontend_job_runs_vitest(self) -> None:
        step = _step("frontend", "Test (vitest)")
        assert "npm run test" in str(step["run"])
        assert step.get("continue-on-error") is not True

    def test_the_frontend_job_audits_its_shipped_dependencies(self) -> None:
        step = _step("frontend", "Dependency vulnerability scan (npm audit)")
        run = str(step["run"])
        assert "npm audit" in run
        assert "--omit=dev" in run, "Scope the audit to what ships, as pip-audit does."
        assert step.get("continue-on-error") is not True


class TestEveryActionIsPinnedToACommitSha:
    """A floating action tag is the `trivy-action` compromise's own shape."""

    def test_no_step_uses_a_floating_tag(self) -> None:
        floating = []
        for job, spec in _workflow()["jobs"].items():
            for step in spec["steps"]:
                uses = step.get("uses")
                if not uses:
                    continue
                _, _, ref = uses.partition("@")
                if not re.fullmatch(r"[0-9a-f]{40}", ref):
                    floating.append(f"{job}: {uses}")
        assert not floating, (
            "These actions are pinned to a mutable ref, which resolves at run "
            f"time to whatever the tag points at then: {floating}. "
            "CVE-2026-33634 force-pushed 76 of 77 `trivy-action` tags to "
            "credential-stealing commits. Resolve the tag to a commit SHA "
            "against GitHub's API and pin that, with the version in a comment."
        )


class TestTheContainerBuildIsABlockingGate:
    """`docker-scan` builds the image this project ships, and that step
    blocks even though the Trivy step beside it does not.

    UAT-readiness Phase 11 (`OPS-021`). The distinction had never been
    written down, and the consequence was a real S1 going unnoticed:
    `aquasecurity/trivy-action` only fails a step when its `exit-code`
    input is set, which it deliberately is not, so both `CLAUDE.md` 13
    and `SECURITY.md` described "the Trivy container scan" as
    report-only -- which reads as "the container job is advisory". The
    plain `run: docker build` step above it is not advisory, and it had
    been failing since 2026-10-29, because `numpy==2.5.3` declares
    `Requires-Python >=3.12` while this image targets 3.11. The image
    could not be built at all.

    This asserts the build step is present and blocking, so nobody can
    quietly make it `continue-on-error` and restore that invisibility.
    It cannot assert the build *passes* -- that needs a Docker daemon,
    which CI has and the mocked suite does not.
    """

    def test_the_image_is_built_and_the_build_step_blocks(self) -> None:
        step = _step("docker-scan", "Build image")
        assert "docker build" in step.get("run", ""), step
        assert step.get("continue-on-error") is not True, (
            "The container build must block. The Trivy scan step beside it is "
            "report-only by design (its `exit-code` input is unset), which is "
            "not a statement about this step -- see OPS-021."
        )

    def test_the_build_targets_the_repository_dockerfile(self) -> None:
        """A build of some other context would pass while the shipped
        image stayed broken."""
        run = _step("docker-scan", "Build image")["run"]
        assert run.strip().endswith("."), run
