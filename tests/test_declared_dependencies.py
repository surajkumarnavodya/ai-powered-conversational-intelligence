"""Every third-party package production code imports is declared.

UAT-readiness Phase 8 (`BP-009`).

`requirements.txt` already carried the scar of this class of bug:

    pyyaml==6.0.3   # config/table_descriptions.py -- was only a transitive dep before

Two more had the same shape and neither was declared. `requests` is
imported by `security/outbound_url.py` -- the SSRF validator -- and by
three `media_gen/` modules; `httpx` by eleven more including
`agent/llm_client.py`, `rag/llm.py`, `search/web_search.py` and
`security/oidc.py`. Both were installed only because unrelated packages
pulled them in (`requests` via CacheControl/detect-secrets/kubernetes/
langsmith/pip_audit/requests-oauthlib/requests-toolbelt, `httpx` via
chromadb/huggingface_hub/langchain-core/langgraph-sdk/ollama), so a bump
that dropped one of those would have been an `ImportError` in a module
the application cannot start without.

The test derives the requirement from the **source tree** rather than
from a list somebody maintains, which is the same reason
`tests/test_ci_gate_coverage.py` exists: a hand-written list is a list
that falls behind.

Scope and its limits, stated rather than implied:

* Only **top-level** imports of **production** packages are considered.
  `tests/`, `eval/` and `scripts/` are excluded -- the first is covered
  by `requirements-dev.txt` and the other two are manual harnesses that
  CI never runs.
* Import name to distribution name is resolved with
  `importlib.metadata.packages_distributions()`, so it reflects what is
  actually installed in this environment. A package that is imported but
  not installed cannot be mapped, and is reported separately rather than
  silently passing.
* This checks *declaration*, not version correctness. `pip-audit` and
  the resolver own that.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys
from importlib.metadata import packages_distributions

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_REQUIREMENTS = _REPO_ROOT / "requirements.txt"

#: Directories excluded from the scan, each for a stated reason.
_NON_PRODUCTION = frozenset(
    {
        "tests",  # covered by requirements-dev.txt
        "eval",  # manual harness, never run by CI
        "scripts",  # standalone CLI entry points
        ".venv",
        "env",
        ".claude",
        ".git",
        "node_modules",
        "frontend",
        "docs",
        "build",
        "dist",
    }
)

#: Import names that resolve to no distribution because they are this
#: repository's own top-level packages. Derived, not hand-listed.
_FIRST_PARTY = frozenset(
    path.name for path in _REPO_ROOT.iterdir() if path.is_dir() and (path / "__init__.py").exists()
) | {"ai_generator_client"}


def _declared_distributions() -> set[str]:
    """Distribution names in requirements.txt, normalised."""
    names: set[str] = set()
    for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[=<>!~\[;]", line, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def _production_python_files() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = []
    for line in out.splitlines():
        rel = line.strip().replace("\\", "/")
        if not rel:
            continue
        if rel.split("/", 1)[0] in _NON_PRODUCTION:
            continue
        files.append(_REPO_ROOT / rel)
    return files


def _imported_top_level_names() -> dict[str, str]:
    """Top-level module name -> the first production file importing it."""
    found: dict[str, str] = {}
    for path in _production_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # `level > 0` is a relative import: first-party by definition.
                names = [node.module] if node.module and not node.level else []
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                found.setdefault(top, str(path.relative_to(_REPO_ROOT)).replace("\\", "/"))
    return found


def _third_party_imports() -> dict[str, str]:
    """The imports that belong to an installed third-party distribution."""
    mapping = packages_distributions()
    stdlib = set(sys.stdlib_module_names)
    result: dict[str, str] = {}
    for top, source in _imported_top_level_names().items():
        if top in stdlib or top in _FIRST_PARTY or top.startswith("_"):
            continue
        if top in mapping:
            result[top] = source
    return result


def test_the_scan_finds_the_imports_we_know_are_there():
    # Vacuity guard: if the AST walk or the git listing breaks, the real
    # assertion below would pass having checked nothing.
    imports = _third_party_imports()

    for expected in ("httpx", "requests", "sqlglot", "pydantic", "fastapi"):
        assert expected in imports, f"{expected} was not discovered by the scan"
    assert len(imports) >= 20, f"only {len(imports)} third-party imports found: {sorted(imports)}"


def test_requirements_parsing_finds_the_known_pins():
    declared = _declared_distributions()

    for expected in ("fastapi", "sqlglot", "chromadb", "pyyaml"):
        assert expected in declared, f"{expected} not parsed out of requirements.txt"


@pytest.mark.parametrize("package", ["requests", "httpx"])
def test_the_two_http_clients_are_declared(package):
    # The specific regression. Both were transitive-only until
    # 2026-10-29, while `security/outbound_url.py` and ten other
    # production modules imported them directly.
    assert (
        package in _declared_distributions()
    ), f"{package} is imported by production code but is not in requirements.txt"


def test_every_directly_imported_third_party_package_is_declared():
    declared = _declared_distributions()
    mapping = packages_distributions()

    undeclared: list[str] = []
    for top, source in sorted(_third_party_imports().items()):
        distributions = {d.lower().replace("_", "-") for d in mapping.get(top, [])}
        if distributions & declared:
            continue
        undeclared.append(f"{top} (imported by {source}; provided by {sorted(distributions)})")

    assert not undeclared, (
        "These packages are imported directly by production code but are not declared "
        "in requirements.txt, so they are present only while some other package happens "
        "to depend on them:\n  " + "\n  ".join(undeclared)
    )
