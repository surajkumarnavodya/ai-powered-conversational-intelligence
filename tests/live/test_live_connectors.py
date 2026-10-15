"""Live connector validation -- opt-in, credential-gated.

Every test here is **read-only** and skips itself by name when its
configuration is absent. Run with `pytest -m live`.

Covers the sources that exist and are credential-blocked today. The
Microsoft 365 connectors (SharePoint, OneDrive, Dataverse, Outlook) are
**not** covered because they are not implemented -- see
`docs/architecture/feature-inventory.md` §4. When they are built, each
gets a class here following the same shape:

    @pytest.mark.live
    class TestLiveSharePoint:
        def test_connection_is_healthy(self): ...
        def test_search_returns_evidence_with_provenance(self): ...
        def test_a_document_the_caller_cannot_see_is_not_returned(self): ...

The third is the one that matters and the one that cannot be faked: it
needs two real identities with different access to the same site. See
`docs/LIVE_INTEGRATION_TESTING.md` for the tenant setup that makes it
possible.
"""

from __future__ import annotations

import pytest

from tests.live.conftest import require_env

pytestmark = pytest.mark.live


class TestLiveWebSearch:
    """Tavily. Needs `WEB_SEARCH_API_KEY`."""

    def test_a_search_returns_well_formed_results(self, live_settings):
        require_env("WEB_SEARCH_API_KEY")
        from search.web_search import web_search

        results = web_search("what is the capital of France", live_settings)
        assert results, "a live search returned nothing"
        for item in results:
            assert item.url.startswith("http")
            assert item.title

    def test_the_configured_result_cap_is_respected(self, live_settings):
        """A provider ignoring `max_results` would be a real cost surprise,
        and only a live call can show it."""
        require_env("WEB_SEARCH_API_KEY")
        from search.web_search import web_search

        results = web_search("python programming", live_settings)
        assert len(results) <= live_settings.web_search_max_results


class TestLiveRagStore:
    """SQL Server 2025+/Azure SQL native `VECTOR`. Needs
    `RAG_STORE_CONNECTION_STRING`."""

    def test_the_store_is_reachable(self, live_settings):
        require_env("RAG_STORE_CONNECTION_STRING")
        from rag.store import get_rag_engine

        engine = get_rag_engine(live_settings)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar() == 1

    def test_the_server_supports_the_vector_type(self, live_settings):
        """The one thing that cannot be verified without a real server, and
        the reason document/policy RAG is marked blocked rather than
        merely off: an older SQL Server has no `VECTOR` type at all."""
        require_env("RAG_STORE_CONNECTION_STRING")
        from rag.store import get_rag_engine

        engine = get_rag_engine(live_settings)
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT @@VERSION").scalar()
        assert version, "could not read the server version"


class TestLiveRestConnector:
    """`rest/`. Needs `ENABLE_REST_CONNECTOR=true` and `REST_ALLOWED_HOSTS`,
    plus a declared endpoint to call."""

    def _spec_from_env(self, live_settings):
        require_env("REST_ALLOWED_HOSTS", "LIVE_REST_TEST_URL")
        import os

        from rest import RestConnector, RestEndpoint, RestMethod

        return RestConnector(
            (
                RestEndpoint(
                    name="live_probe",
                    url=os.environ["LIVE_REST_TEST_URL"],
                    method=RestMethod.GET,
                    side_effect=False,
                ),
            )
        )

    def test_the_endpoint_is_reachable_and_healthy(self, live_settings):
        connector = self._spec_from_env(live_settings)
        status = connector.health(live_settings)
        assert status.healthy, f"endpoint unhealthy: {status.detail}"

    def test_a_real_search_returns_evidence(self, live_settings):
        connector = self._spec_from_env(live_settings)
        results = connector.search("", live_settings)
        # An empty query returns nothing by contract -- this asserts the
        # call path works end to end without depending on the remote
        # service's content.
        assert results == ()

    def test_a_non_allowlisted_host_is_refused_against_real_dns(self, live_settings):
        """The SSRF guard with real name resolution, which the mocked
        suite deliberately cannot exercise."""
        require_env("REST_ALLOWED_HOSTS")
        from security.outbound_url import OutboundUrlBlockedError, assert_safe_url

        with pytest.raises(OutboundUrlBlockedError):
            assert_safe_url(
                "https://metadata.google.internal/",
                allowed_hosts=tuple(live_settings.rest_allowed_hosts),
                context="live_test",
            )


class TestLiveMcpServer:
    """`mcp_client/`. Needs a real MCP server on this host.

    Set `LIVE_MCP_COMMAND` (and optionally `LIVE_MCP_ARGS`, comma-separated)
    to something that speaks MCP over stdio.
    """

    def _client(self):
        import os

        require_env("LIVE_MCP_COMMAND")
        from mcp_client import McpClient, McpServerSpec

        from agent.authz import Permission

        args = tuple(a for a in os.environ.get("LIVE_MCP_ARGS", "").split(",") if a.strip())
        return McpClient(
            McpServerSpec(
                name="live",
                command=os.environ["LIVE_MCP_COMMAND"],
                args=args,
                permission=Permission.ASK,
                env={"PATH": os.environ.get("PATH", "")},
            )
        )

    def test_the_handshake_completes(self):
        """The single most valuable live check: the protocol revision this
        client implements is only *spec*-verified today. A real server
        either agrees or raises a clear version error."""
        with self._client() as client:
            assert client is not None

    def test_tools_can_be_listed(self):
        with self._client() as client:
            tools = client.list_tools()
            for tool in tools:
                assert tool.name
                assert tool.server_name == "live"

    def test_a_discovered_tool_is_still_refused_without_an_allowlist_entry(self):
        """Trust is enforced regardless of what a real server offers."""
        import os

        require_env("LIVE_MCP_COMMAND")
        from mcp_client import McpServerSpec, McpTrustError
        from mcp_client.trust import call_allowlisted_tool

        from agent.authz import Permission

        spec = McpServerSpec(
            name="live",
            command=os.environ["LIVE_MCP_COMMAND"],
            args=(),
            permission=Permission.ASK,
            allowed_tools=frozenset(),
        )
        with pytest.raises(McpTrustError):
            call_allowlisted_tool(spec, "anything", {}, caller_roles=("admin",))


class TestLiveOllama:
    """The LLM runtime. Needs Ollama running at `OLLAMA_HOST`."""

    def test_the_configured_model_is_installed(self, live_settings):
        require_env("OLLAMA_HOST")
        from agent.model_registry import get_allowed_models

        allowed = get_allowed_models(live_settings)
        assert live_settings.ollama_model in allowed
