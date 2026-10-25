"""Supporting excerpts on a citation: `CitationOut.excerpt`.

The excerpt exists so the evidence disclosure can show *why* a source
backs a claim instead of re-listing the same URLs the Sources section
already shows. It is third-party text, so the boundary that produces it
is what these tests pin: bounded, redacted, whitespace-normalised, and
never produced at all for restricted material.
"""

from __future__ import annotations

from api.main import _MAX_CITATION_EXCERPT_CHARS, _citation_excerpt_out
from config.settings import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)


class TestExcerptBoundary:
    def test_passes_a_short_excerpt_through(self) -> None:
        assert _citation_excerpt_out("Clashes broke out.", _settings()) == "Clashes broke out."

    def test_empty_stays_empty(self) -> None:
        assert _citation_excerpt_out("", _settings()) == ""

    def test_collapses_whitespace_so_a_chunk_cannot_reshape_the_panel(self) -> None:
        assert _citation_excerpt_out("a\n\n  b\tc", _settings()) == "a b c"

    def test_bounds_a_long_excerpt_and_marks_the_truncation(self) -> None:
        out = _citation_excerpt_out("x" * 2000, _settings())
        # The ellipsis is one character on top of the budget.
        assert len(out) == _MAX_CITATION_EXCERPT_CHARS + 1
        assert out.endswith("…")

    def test_a_boundary_length_excerpt_is_not_truncated(self) -> None:
        exact = "y" * _MAX_CITATION_EXCERPT_CHARS
        assert _citation_excerpt_out(exact, _settings()) == exact


class TestExcerptIsRedacted:
    def test_a_secret_shaped_string_does_not_survive_verbatim(self) -> None:
        # The excerpt is content this application did not author, so it
        # goes through the same redaction the answer text does
        # (CLAUDE.md §5.6). Asserted as "not verbatim" rather than against
        # one exact replacement, so this stays true if the redactor's
        # placeholder wording changes.
        planted = "connect with password=hunter2 and proceed"
        out = _citation_excerpt_out(planted, _settings())
        assert "hunter2" not in out


class TestRestrictedMaterialProducesNoExcerpt:
    def test_a_sensitivity_tagged_chunk_yields_no_citations_at_all(self, monkeypatch) -> None:
        """The gate that makes the excerpt safe to add.

        `rag.graph._generate_node` returns `citations: []` as soon as any
        retrieved chunk carries a `sensitivity_category`, *before* the
        citation list (and therefore any excerpt) is built. So a
        restricted document's text cannot reach a client through this
        field -- which is the whole reason adding it does not weaken the
        policy gate.
        """
        import rag.graph as rag_graph
        from rag.store import ChunkResult

        restricted = ChunkResult(
            chunk_text="Confidential: the merger closes in March.",
            similarity=0.9,
            document_id="doc-1",
            filename="board-minutes.pdf",
            chunk_index=0,
            page_number=1,
            sensitivity_category="confidential",
            has_pdf_bytes=True,
        )

        def _fail_if_called(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("the model must not see a restricted chunk")

        monkeypatch.setattr("rag.llm.call_ollama", _fail_if_called)

        result = rag_graph._generate_node(
            {"question": "what is in the minutes?", "chunks": [restricted]},
            settings=_settings(),
        )

        assert result["citations"] == []
        assert result["status"] == "restricted"
        assert "Confidential" not in result["answer"]


class TestExcerptReachesTheClient:
    def test_a_web_citation_carries_the_result_snippet(self, monkeypatch) -> None:
        from pydantic import SecretStr

        import agent.orchestrator.nodes as orchestrator_nodes
        import rag.llm
        import search.web_search
        from agent.orchestrator.nodes import web_search_node
        from search.web_search import WebResult

        settings = Settings(
            _env_file=None, enable_web_search=True, web_search_api_key=SecretStr("tvly-x")
        )
        monkeypatch.setattr(orchestrator_nodes, "get_settings", lambda: settings)
        monkeypatch.setattr(
            search.web_search,
            "web_search",
            lambda query, settings: [
                WebResult(
                    title="Why protests spread",
                    url="https://example.test/a",
                    snippet="Clashes were reported in five cities overnight.",
                    retrieved_at="now",
                )
            ],
        )
        monkeypatch.setattr(rag.llm, "call_ollama", lambda *a, **k: "Protests spread.")

        result = web_search_node({"question": "why are there protests?"})

        citation = result["web_result"]["citations"][0]
        assert citation["excerpt"] == "Clashes were reported in five cities overnight."
        assert citation["title"] == "Why protests spread"
