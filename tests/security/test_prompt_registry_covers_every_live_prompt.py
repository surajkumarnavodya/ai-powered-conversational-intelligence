"""Every prompt in this application is in the registry's inventory.

UAT-readiness Phase 7 (`CTX-001`).

`tests/security/test_prompt_registry_cannot_weaken_security.py` already
pins that the inventory's expected-invariant table covers "exactly the
live prompt set". That check runs in one direction only: the *live prompt
set* is `governance_center.inventory._PROMPT_SOURCES`, a hand-maintained
tuple, and nothing compared that tuple against the repository. So a
prompt added anywhere else was invisible to the inventory **and** to the
drift suite that reads it -- which is how nine of them accumulated.

This file closes the other direction. It discovers prompts from the
source tree with `ast` rather than by importing every module, so a module
with heavy or optional imports (Chroma, LangGraph, a vision backend)
still gets scanned, and a prompt in a module nobody imports is still
found.

Two deliberate scope choices:

* **Module-level string constants only.** A prompt assembled inside a
  function is not discoverable this way, which is exactly why
  `agent.orchestrator.nodes`' web-answer prompt was hoisted to a module
  constant in the same commit as this file.
  `test_no_prompt_is_built_inside_a_function` below is what stops that
  pattern coming back.
* **Name-based discovery** (`*PROMPT*`). A prompt is not distinguishable
  from any other long string by shape alone, so this rests on the naming
  convention every prompt in the repo already follows.
  `_ALLOWED_NON_PROMPTS` carries the few `*PROMPT*` names that are
  genuinely not prompts, each with a reason -- it is an exception list,
  not a suppression: adding to it is visible in review.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from governance_center.inventory import inventory_prompt_templates

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Directories that are not this application's own runtime source.
_SKIP_PARTS = frozenset(
    {
        ".venv",
        "env",
        ".claude",
        ".git",
        "tests",
        "node_modules",
        "__pycache__",
        "frontend",
        "docs",
        "eval",
        "scripts",
    }
)

#: `*PROMPT*` module constants that are not prompt templates, with the
#: reason each is exempt. Deliberately tiny and deliberately explicit.
_ALLOWED_NON_PROMPTS: dict[tuple[str, str], str] = {
    ("agent.context_resolution", "_PROMPT_PATH"): "A Path to a prompt file, not the text.",
    ("agent.orchestrator.general_knowledge", "_PROMPT_PATH"): "A Path, not the text.",
    ("agent.orchestrator.nodes", "_ROUTER_PROMPT_PATH"): "A Path, not the text.",
    (
        "agent.llm_client",
        "_SYSTEM_PROMPT_RESERVE_CHARS",
    ): "An int character reserve for the context budget.",
    (
        "governance.request_policy",
        "_PROMPT_BYPASS_MESSAGE",
    ): "A fixed refusal message shown to a caller; never sent to a model.",
    (
        "agent.orchestrator.nodes",
        "_WEB_SEARCH_ANSWER_SYSTEM_PROMPT_TEMPLATE",
    ): "The template behind `_web_search_answer_system_prompt`, the registered entry.",
    (
        "agent.orchestrator.nodes",
        "_CLASSIFY_SOURCES_BASE_PROMPT",
    ): "The base behind `_classify_sources_system_prompt`, the registered entry.",
}


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(_REPO_ROOT).as_posix()
    return rel[: -len(".py")].replace("/", ".")


def _iter_app_sources():
    """Yields `(path, parsed_tree)` for every application source file."""
    for path in sorted(_REPO_ROOT.rglob("*.py")):
        parts = path.relative_to(_REPO_ROOT).parts
        if any(part in _SKIP_PARTS for part in parts):
            continue
        try:
            yield path, ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not our source
            continue


def _discover_prompt_constants() -> dict[tuple[str, str], int]:
    """Every module-level `*PROMPT*` assignment in the application source.

    Returns a mapping of `(module, symbol)` to the constant's character
    length, `-1` when the value is a concatenation or f-string whose
    length cannot be read statically (which most of this repo's prompts
    are -- they are implicit string concatenations), and `-2` for a
    non-string constant.
    """
    found: dict[tuple[str, str], int] = {}
    for path, tree in _iter_app_sources():
        for node in tree.body:
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name) or "PROMPT" not in target.id.upper():
                    continue
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found[(_module_name(path), target.id)] = len(value.value)
                elif isinstance(value, ast.BinOp | ast.JoinedStr):
                    found[(_module_name(path), target.id)] = -1
                elif isinstance(value, ast.Constant):
                    found[(_module_name(path), target.id)] = -2
    return found


def _registered() -> set[tuple[str, str]]:
    return {(entry.source_module, entry.source_symbol) for entry in inventory_prompt_templates()}


def test_the_scanner_itself_finds_the_prompts_we_know_exist():
    # A discovery test that silently found nothing would pass the real
    # assertion below vacuously. These four are in four different
    # packages and are not going away.
    discovered = _discover_prompt_constants()

    for expected in (
        ("agent.llm_client", "_PLAN_SYSTEM_PROMPT"),
        ("rag.graph", "_GENERATE_SYSTEM_PROMPT"),
        ("voice.correction", "_CORRECTION_SYSTEM_PROMPT"),
        ("media_gen.providers.svg_llm", "_SYSTEM_PROMPT"),
    ):
        assert expected in discovered, f"{expected} was not discovered by the AST scan"
    assert len(discovered) >= 20, f"only {len(discovered)} found -- the scan likely broke"


def test_every_prompt_constant_in_the_repository_is_in_the_inventory():
    discovered = _discover_prompt_constants()
    registered = _registered()

    unregistered = sorted(
        key for key in discovered if key not in registered and key not in _ALLOWED_NON_PROMPTS
    )

    assert not unregistered, (
        "These prompt constants are live in the application but absent from "
        "`governance_center.inventory._PROMPT_SOURCES`, so no drift detection and no "
        "safety-invariant check applies to them:\n  "
        + "\n  ".join(f"{module}.{symbol}" for module, symbol in unregistered)
        + "\n\nAdd an entry to `_PROMPT_SOURCES` (and its invariants to "
        "EXPECTED_LIVE_INVARIANTS in test_prompt_registry_cannot_weaken_security.py), "
        "or, if it is genuinely not a prompt, to `_ALLOWED_NON_PROMPTS` here with a reason."
    )


def test_the_exception_list_has_no_stale_entries():
    # An exemption for a constant that no longer exists would quietly
    # license a future prompt that happens to reuse the name.
    discovered = _discover_prompt_constants()

    stale = sorted(key for key in _ALLOWED_NON_PROMPTS if key not in discovered)

    assert not stale, f"_ALLOWED_NON_PROMPTS names constants that no longer exist: {stale}"


def test_no_prompt_is_built_inside_a_function():
    """A function-local prompt cannot be registered, so it cannot be governed.

    This is how `agent.orchestrator.nodes`' web-answer prompt -- the one
    prompt that puts third-party web content in front of a model --
    stayed outside the registry. It is now a module-level template plus a
    builder, and this keeps it that way.

    Scoped to assignments named `system_prompt`, the name the pattern
    actually used, rather than every local string: a node that builds a
    *user* prompt from per-request data is normal and expected, and only
    the system prompt is the governed artifact.
    """
    offenders: list[str] = []
    for path, tree in _iter_app_sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Assign):
                    continue
                for target in inner.targets:
                    if not isinstance(target, ast.Name) or target.id != "system_prompt":
                        continue
                    # A reference to a module constant or a builder call
                    # is the correct pattern; an inline literal is not.
                    if isinstance(inner.value, ast.Constant | ast.BinOp | ast.JoinedStr):
                        offenders.append(
                            f"{_module_name(path)}.{node.name} assigns an inline "
                            f"system_prompt literal (line {inner.lineno})"
                        )

    assert not offenders, (
        "A system prompt assembled inside a function is invisible to "
        "`governance_center.inventory`:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "asset_key",
    [
        "source_router",
        "web_search_answer",
        "context_resolution",
        "context_resolution_template",
        "general_knowledge_answer",
        "general_knowledge_template",
        "voice_transcript_correction",
        "profile_picture_review",
        "svg_generation",
        "answer_translation",
    ],
)
def test_each_prompt_added_by_this_phase_resolves_and_fingerprints(asset_key):
    # The nine code/file prompts plus the hoisted web one. A resolver
    # that raised would make the whole inventory route 500, so each is
    # exercised individually.
    entry = next(e for e in inventory_prompt_templates() if e.asset_key == asset_key)

    assert entry.spec.template.strip()
    assert len(entry.fingerprint) == 16
    assert entry.source_module and entry.source_symbol


def test_the_file_backed_prompts_resolve_through_the_runtimes_own_loader():
    """`prompts/*.md` is read at runtime, so it can change with no code change.

    The inventory must read it the way the application does, not by
    re-reading the path itself -- otherwise the registry could report a
    file the running code never loads.
    """
    from agent.context_resolution import _prompt_template as context_loader
    from agent.orchestrator.general_knowledge import _prompt_template as gk_loader
    from agent.orchestrator.nodes import _router_prompt_template

    entries = {e.asset_key: e.spec.template for e in inventory_prompt_templates()}

    assert entries["source_router"] == _router_prompt_template()
    assert entries["context_resolution_template"] == context_loader()
    assert entries["general_knowledge_template"] == gk_loader()
