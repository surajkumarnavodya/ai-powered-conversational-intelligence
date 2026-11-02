"""Unit tests for rag/graph.py's `_generate_node` -- the actual enforcement
point for both of this app's per-document RAG access-control gates
(sensitivity_category, and the 2026 Phase 3 restricted_roles addition).
Fully mocked (`rag.llm.call_ollama`) -- no real Ollama/database. This
module previously had no dedicated test file at all (a pre-existing,
documented gap -- CLAUDE.md's "Known gaps" note); added alongside the
restricted_roles feature since it's the security-critical function that
feature actually changes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from config.settings import Settings
from rag.graph import _generate_node
from rag.store import ChunkResult
from security.secrets import SecretStr

_BASE_SETTINGS = Settings(
    ollama_host="http://localhost:11434",
    ollama_model="llama3.1:8b",
    ollama_request_timeout_seconds=60,
    db_type="postgresql",
    db_host="db.example.com",
    db_port=5432,
    db_name="mydb",
    db_user="reader",
    db_password=SecretStr("secret"),
    db_connection_string=None,
    db_schema=None,
    db_odbc_driver="x",
    chroma_persist_dir=Path("/tmp/chroma"),
    chroma_collection_name="x",
    embedding_model_name="x",
    schema_top_k=4,
    max_retries=3,
    complex_query_max_retry_bonus=2,
    max_result_rows=1000,
    query_timeout_seconds=15,
    llm_max_tokens=1024,
    insight_max_tokens=120,
    max_question_length=500,
    question_rate_limit_per_minute=10,
    llm_call_rate_limit_per_minute=20,
    cost_estimation_enabled=True,
    cost_estimation_timeout_seconds=3,
    cost_moderate_row_threshold=50_000,
    cost_high_row_threshold=1_000_000,
    log_level="INFO",
    log_redaction_level="standard",
)


def _chunk(
    text: str = "some content",
    sensitivity_category: str | None = None,
    restricted_roles: tuple[str, ...] | None = None,
) -> ChunkResult:
    return ChunkResult(
        chunk_text=text,
        similarity=0.9,
        document_id="doc-1",
        filename="f.pdf",
        chunk_index=0,
        page_number=1,
        sensitivity_category=sensitivity_category,
        has_pdf_bytes=False,
        restricted_roles=restricted_roles,
    )


class TestUnrestrictedContentGeneratesNormally:
    def test_no_restrictions_at_all(self):
        with patch("rag.llm.call_ollama", return_value="The answer."):
            result = _generate_node(
                {"question": "q", "chunks": [_chunk()], "caller_roles": ()},
                settings=_BASE_SETTINGS,
            )
        assert result["status"] == "succeeded"
        assert result["answer"] == "The answer."
        assert len(result["citations"]) == 1


class TestSensitivityCategoryGate:
    """Regression coverage for the pre-existing gate -- no dedicated test
    file covered this directly before (only indirectly, through the
    orchestrator's own mocked tests)."""

    def test_sensitive_chunk_blocks_generation_entirely(self):
        with patch("rag.llm.call_ollama") as mock_ollama:
            result = _generate_node(
                {
                    "question": "q",
                    "chunks": [_chunk(sensitivity_category="compensation")],
                    "caller_roles": ("admin",),  # even admin -- no role bypasses this gate
                },
                settings=_BASE_SETTINGS,
            )
        mock_ollama.assert_not_called()
        assert result["status"] == "restricted"
        assert result["citations"] == []
        assert "compensation" in result["answer"]

    def test_one_sensitive_chunk_among_several_is_dropped_not_fatal(self):
        """The sensitive chunk is withheld; the rest still answers.

        This replaced `test_one_sensitive_chunk_among_several_still_blocks`
        on 2026-11-01. Blocking the whole answer made the policy source
        unusable in practice, because retrieval returns the top-k for the
        *collection*: one sensitivity-tagged document meant every
        unrelated policy question refused, and the refusal named that
        document's category. Measured on a live deployment -- a freshly
        uploaded remote-work policy carrying no category at all came back
        as "policy content classified 'legal'", which was a different
        file entirely. CLAUDE.md 5.8 names over-blocking as its own
        failure mode.

        What must not change is covered by the three tests below: the
        sensitive chunk reaches neither the prompt nor the citations, and
        a retrieval with nothing usable in it still refuses.
        """
        chunks = [_chunk(text="ordinary"), _chunk(text="secret", sensitivity_category="legal")]
        with patch("rag.llm.call_ollama", return_value="Answer.") as mock_ollama:
            result = _generate_node(
                {"question": "q", "chunks": chunks, "caller_roles": ()}, settings=_BASE_SETTINGS
            )
        assert result["status"] == "succeeded"
        mock_ollama.assert_called_once()
        assert len(result["citations"]) == 1

    def test_the_withheld_chunk_never_reaches_the_prompt(self):
        """Non-vacuous: asserts on the exact prompt text that was sent."""
        chunks = [
            _chunk(text="ordinary business content"),
            _chunk(text="SENSITIVE-SALARY-DETAIL", sensitivity_category="compensation"),
        ]
        with patch("rag.llm.call_ollama", return_value="Answer.") as mock_ollama:
            _generate_node(
                {"question": "q", "chunks": chunks, "caller_roles": ()}, settings=_BASE_SETTINGS
            )
        sent = (
            " ".join(str(a) for a in mock_ollama.call_args[0]) + " " + str(mock_ollama.call_args[1])
        )
        assert "SENSITIVE-SALARY-DETAIL" not in sent
        assert "ordinary business content" in sent

    def test_the_withheld_chunk_never_reaches_the_citations(self):
        chunks = [
            _chunk(text="ordinary"),
            _chunk(text="secret", sensitivity_category="compensation"),
        ]
        with patch("rag.llm.call_ollama", return_value="Answer."):
            result = _generate_node(
                {"question": "q", "chunks": chunks, "caller_roles": ()}, settings=_BASE_SETTINGS
            )
        excerpts = [c.get("excerpt") for c in result["citations"]]
        assert "secret" not in excerpts
        assert excerpts == ["ordinary"]

    def test_all_chunks_sensitive_still_refuses(self):
        """The fail-closed case the narrower scope must preserve."""
        chunks = [
            _chunk(text="a", sensitivity_category="legal"),
            _chunk(text="b", sensitivity_category="compensation"),
        ]
        with patch("rag.llm.call_ollama") as mock_ollama:
            result = _generate_node(
                {"question": "q", "chunks": chunks, "caller_roles": ("admin",)},
                settings=_BASE_SETTINGS,
            )
        mock_ollama.assert_not_called()
        assert result["status"] == "restricted"
        assert result["citations"] == []
        # Every blocking category is named, not just the first one found.
        assert "compensation" in result["answer"]
        assert "legal" in result["answer"]

    def test_a_partial_answer_says_so(self):
        """Withholding evidence is disclosed, for the same reason the
        per-domain export ceiling is (CLAUDE.md 5.4) -- a reader must be
        able to tell a complete answer from one assembled after material
        was removed."""
        chunks = [_chunk(text="ordinary"), _chunk(text="secret", sensitivity_category="legal")]
        with patch("rag.llm.call_ollama", return_value="Answer."):
            result = _generate_node(
                {"question": "q", "chunks": chunks, "caller_roles": ()}, settings=_BASE_SETTINGS
            )
        assert "withheld" in result["answer"]

    def test_an_unrestricted_answer_carries_no_withholding_notice(self):
        with patch("rag.llm.call_ollama", return_value="Answer."):
            result = _generate_node(
                {"question": "q", "chunks": [_chunk(text="ordinary")], "caller_roles": ()},
                settings=_BASE_SETTINGS,
            )
        assert result["answer"] == "Answer."


class TestRestrictedRolesGate:
    """2026 Phase 3 security review: the new, more general per-document
    role-restriction mechanism."""

    def test_caller_without_matching_role_is_blocked(self):
        with patch("rag.llm.call_ollama") as mock_ollama:
            result = _generate_node(
                {
                    "question": "q",
                    "chunks": [_chunk(restricted_roles=("analyst", "admin"))],
                    "caller_roles": ("user",),
                },
                settings=_BASE_SETTINGS,
            )
        mock_ollama.assert_not_called()
        assert result["status"] == "restricted"
        assert result["citations"] == []

    def test_caller_with_matching_role_is_allowed(self):
        with patch("rag.llm.call_ollama", return_value="Answer."):
            result = _generate_node(
                {
                    "question": "q",
                    "chunks": [_chunk(restricted_roles=("analyst", "admin"))],
                    "caller_roles": ("analyst",),
                },
                settings=_BASE_SETTINGS,
            )
        assert result["status"] == "succeeded"
        assert result["answer"] == "Answer."

    def test_caller_with_no_roles_at_all_is_blocked(self):
        """Fail closed on a missing/empty caller_roles, not fail open."""
        with patch("rag.llm.call_ollama") as mock_ollama:
            result = _generate_node(
                {
                    "question": "q",
                    "chunks": [_chunk(restricted_roles=("analyst",))],
                    # caller_roles omitted entirely -- state.get(..., ()) default
                },
                settings=_BASE_SETTINGS,
            )
        mock_ollama.assert_not_called()
        assert result["status"] == "restricted"

    def test_one_role_restricted_chunk_among_several_is_dropped_not_fatal(self):
        """Same narrowing as the sensitivity gate, for the same reason --
        see `TestSensitivityCategoryGate
        ::test_one_sensitive_chunk_among_several_is_dropped_not_fatal`.
        The restricted chunk is withheld from the prompt and the
        citations; what remains answers."""
        chunks = [
            _chunk(text="ordinary"),
            _chunk(text="ROLE-SECRET", restricted_roles=("admin",)),
        ]
        with patch("rag.llm.call_ollama", return_value="Answer.") as mock_ollama:
            result = _generate_node(
                {"question": "q", "chunks": chunks, "caller_roles": ("user",)},
                settings=_BASE_SETTINGS,
            )
        assert result["status"] == "succeeded"
        sent = " ".join(str(a) for a in mock_ollama.call_args[0])
        assert "ROLE-SECRET" not in sent
        assert [c.get("excerpt") for c in result["citations"]] == ["ordinary"]
        assert "withheld" in result["answer"]

    def test_all_chunks_role_restricted_still_refuses(self):
        with patch("rag.llm.call_ollama") as mock_ollama:
            result = _generate_node(
                {
                    "question": "q",
                    "chunks": [_chunk(restricted_roles=("admin",))],
                    "caller_roles": ("user",),
                },
                settings=_BASE_SETTINGS,
            )
        mock_ollama.assert_not_called()
        assert result["status"] == "restricted"
        assert result["citations"] == []

    def test_no_restricted_roles_set_is_unaffected(self):
        """restricted_roles=None (the default, pre-existing-document case)
        must never be treated as "restricted to nobody" -- the gate only
        fires when the field is actually set."""
        with patch("rag.llm.call_ollama", return_value="Answer."):
            result = _generate_node(
                {
                    "question": "q",
                    "chunks": [_chunk(restricted_roles=None)],
                    "caller_roles": (),
                },
                settings=_BASE_SETTINGS,
            )
        assert result["status"] == "succeeded"

    def test_audit_event_emitted_on_role_block(self):
        with (
            patch("rag.llm.call_ollama"),
            patch("rag.graph.log_security_event") as mock_log,
        ):
            _generate_node(
                {
                    "question": "q",
                    "chunks": [_chunk(restricted_roles=("admin",))],
                    "caller_roles": ("user",),
                },
                settings=_BASE_SETTINGS,
            )
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        assert args[0] == "rag_role_restricted_content_blocked"
        # The event now reports what was withheld and what survived,
        # because the gate drops chunks rather than refusing the answer --
        # "blocked" alone no longer says whether anything was returned.
        assert kwargs["document_ids"] == ["doc-1"]
        assert kwargs["chunks_withheld"] == 1
        assert kwargs["chunks_usable"] == 0
        assert kwargs["caller_roles"] == ["user"]

    def test_sensitivity_category_gate_takes_precedence_check_order_is_irrelevant(self):
        """Both gates independently block -- if a chunk somehow triggered
        both (not possible via the current upload form, which lets an
        operator set both fields on the same document), either gate
        blocking is correct; this just confirms neither gate accidentally
        short-circuits the other into allowing generation."""
        chunk = _chunk(sensitivity_category="legal", restricted_roles=("admin",))
        with patch("rag.llm.call_ollama") as mock_ollama:
            result = _generate_node(
                {"question": "q", "chunks": [chunk], "caller_roles": ()}, settings=_BASE_SETTINGS
            )
        mock_ollama.assert_not_called()
        assert result["status"] == "restricted"
