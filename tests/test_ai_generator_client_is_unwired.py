"""`ai_generator_client.py` is not wired in, and must not be wired in as it stands.

UAT-readiness Phase 8 (`BP-010`). **Open and flagged for an owner --
this file pins the current state, which is a defect.**

`ai_generator_client.py` is 593 lines at the repository root, the only
top-level Python module in a repo that otherwise keeps every entry point
under `scripts/`. Measured 2026-10-29: **zero importers and zero
tests**. It implements image generation across three providers
(pixazo / cloudflare / ima_studio), which is the job
`media_gen/providers/` already does.

It is harmless today precisely because nothing imports it. What makes it
worth a guard rather than a shrug is that wiring it up would
simultaneously reintroduce four controls this codebase enforces
everywhere else:

1. **SSRF validation.** It builds and requests provider URLs with
   `requests` directly and never calls `security/outbound_url.py`.
   CLAUDE.md 5.6 requires every outbound URL to be validated against the
   host allowlist and re-validated on each use.
2. **`Settings`.** It reads credentials straight from `os.environ`
   (`_env` at line 140) rather than through `config.settings.Settings`,
   so its API keys are plain `str` and not `SecretStr` -- the mechanism
   CLAUDE.md 5.6 relies on to keep a key out of a log line.
3. **Logging configuration.** Line 57 calls `logging.basicConfig()` at
   **module import time**. Importing this module anywhere would
   reconfigure the root logger for the whole process, overriding the
   application's own `LOG_LEVEL` handling.
4. **One owner per capability.** CLAUDE.md 6.1.2: "If two
   implementations would answer the same question, that is a defect
   regardless of test results."

**Why this is flagged and not deleted.** Deleting 593 lines of
provider-integration code is not a local, reversible call -- it may be a
staging ground for a provider `media_gen/` does not support yet, and
that is a product decision. The alternative fix (route it through
`Settings` and `outbound_url`, move it under `scripts/`, give it tests)
is real work with a real design question behind it: whether it should
exist at all alongside `media_gen/providers/`.

**Decision needed:** delete it, or adopt it properly into
`media_gen/providers/` behind the existing controls.

The first test is the guard. It will fail the moment somebody imports
this module, which is the point: the import is what turns four latent
findings into live ones.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MODULE = _REPO_ROOT / "ai_generator_client.py"
_SKIP = {".venv", "env", ".claude", ".git", "node_modules", "frontend", "docs"}


def _tracked_python_files() -> list[pathlib.Path]:
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
        if not rel or rel == "ai_generator_client.py":
            continue
        if rel.split("/", 1)[0] in _SKIP:
            continue
        files.append(_REPO_ROOT / rel)
    return files


def test_the_module_still_exists_so_this_file_is_not_vacuous():
    # If it is deleted (one of the two acceptable outcomes), this whole
    # file should be deleted with it rather than left passing silently.
    assert _MODULE.exists(), (
        "ai_generator_client.py is gone -- good, that was one of the two "
        "options. Delete this test file too; it has nothing left to guard."
    )


def test_nothing_imports_it():
    """The guard. An import here reintroduces four controls at once.

    See this module's docstring. If you are reading this because the test
    failed, the question to answer is not "how do I satisfy the test" but
    "has this module been given `Settings`, SSRF validation and its own
    tests, and should it exist alongside `media_gen/providers/`".
    """
    importers: list[str] = []
    for path in _tracked_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module and not node.level else []
            else:
                continue
            if any(name.split(".")[0] == "ai_generator_client" for name in names):
                importers.append(str(path.relative_to(_REPO_ROOT)).replace("\\", "/"))

    assert not importers, (
        "ai_generator_client.py is now imported by "
        f"{sorted(set(importers))}, but it bypasses security/outbound_url.py "
        "(SSRF), reads credentials from os.environ instead of Settings, and "
        "calls logging.basicConfig() at import time. See BP-010 and this "
        "module's docstring before wiring it in."
    )


def test_the_four_bypasses_are_still_the_ones_recorded():
    """Pins the evidence, so the finding cannot drift from its write-up.

    If someone fixes one of these in place, this fails and the register
    entry gets corrected rather than quietly becoming wrong.
    """
    source = _MODULE.read_text(encoding="utf-8")

    assert "outbound_url" not in source, "it now uses the SSRF validator -- update BP-010"
    assert "get_settings" not in source, "it now uses Settings -- update BP-010"
    assert "logging.basicConfig(" in source, "the import-time logging call is gone -- update BP-010"
    assert "os.environ.get" in source, "it no longer reads os.environ -- update BP-010"
