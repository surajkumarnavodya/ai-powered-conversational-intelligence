"""Proves the descriptor table did not change any routing behaviour.

`sources/descriptors.py` took over three structures that used to be
hand-written literals in `agent/orchestrator/nodes.py`:
`_SOURCE_DESCRIPTIONS`, `SOURCE_PERMISSIONS`, and the `if`-chain inside
`get_available_sources`. All three now derive from the descriptors.

That refactor is only safe if it is a pure move, so this file pins the
*old* values independently and compares. The expectations below are
transcribed from the pre-refactor source, not imported from the new table
-- importing the new table would make every test here vacuous, which is
exactly the failure mode `CLAUDE.md` 7 warns about.

The availability tests are the load-bearing ones: they reimplement the
deleted `if`-chain verbatim and compare it against the registry across two
sweeps -- all 64 combinations of the six feature flags, and all 16
combinations of the four config values each clause also required. Both
sweeps are needed: the first alone never produces a "flag on, config
absent" case, and a mutation dropping `and s.web_search_api_key` survived
an earlier version of this file that had only the first.
"""

from __future__ import annotations

import hashlib
import itertools

import pytest

from agent.authz import Permission
from agent.orchestrator.nodes import (
    _SOURCE_DESCRIPTIONS,
    SOURCE_PERMISSIONS,
    get_available_sources,
)
from security.secrets import SecretStr
from sources.registry import all_names
from tests.test_orchestrator import _settings

# --- The pre-refactor values, transcribed by hand ----------------------

#: Exactly the keys the literal `_SOURCE_DESCRIPTIONS` dict had, in the
#: order it declared them. Order matters: it is the order the classifier
#: prompt offers its options in.
_EXPECTED_ORDER = (
    "sql",
    "documents",
    "policy",
    "web",
    "generation",
    "media_search",
    "attachments",
)

#: Exactly the pre-refactor `SOURCE_PERMISSIONS` dict. Note what is absent:
#: sql, documents and attachments carried no source-level permission, and
#: must still carry none -- adding one here would silently start denying a
#: source that used to be open to every caller.
_EXPECTED_PERMISSIONS = {
    "policy": Permission.POLICY_RAG_QUERY,
    "generation": Permission.MEDIA_GENERATE,
    "web": Permission.WEB_SEARCH,
    "media_search": Permission.MEDIA_SEARCH,
}

#: sha256 (first 16 hex chars) of each description, captured from the
#: pre-refactor text. Pins the wording exactly while keeping this file
#: readable -- see `test_each_description_is_byte_for_byte_what_it_was`.
_EXPECTED_DIGEST = {
    "sql": "c2515d9faa5b8dc1",
    "documents": "8c132c849fd49160",
    "policy": "c1b87c9dd582b9b7",
    "web": "06e0d3967522a3c3",
    "generation": "c4d41af386386dba",
    "media_search": "96a26fde06ac8bf4",
    "attachments": "0fda9630c94c5d4c",
}

#: The first clause of each description, transcribed from the pre-refactor
#: literals. A full-text copy here would just be the same strings twice;
#: these prefixes are enough to catch a reworded or swapped description,
#: which is the actual risk -- this text is prompt input for the router.
_EXPECTED_DESCRIPTION_PREFIX = {
    "sql": "the company's structured databases -- customer data",
    "documents": "general uploaded PDF documents -- the default destination",
    "policy": "a separate, access-restricted collection of internal documents",
    "web": "live web search, for current or external information",
    "generation": "generating a brand-new image or video artifact",
    "media_search": "finding an EXISTING photo or video already sitting",
    "attachments": "a file the user attached directly to THIS specific question",
}


def _old_get_available_sources(settings, *, has_attachments: bool = False) -> list[str]:
    """The deleted `if`-chain, reproduced exactly as it was.

    Kept deliberately ugly and literal. This is the oracle the registry is
    compared against; rewriting it more elegantly would risk "fixing" a
    behaviour the original had.
    """
    sources = ["sql"]
    if settings.enable_document_rag and settings.rag_store_connection_string:
        sources.append("documents")
    if settings.enable_policy_rag and settings.rag_store_connection_string:
        sources.append("policy")
    if settings.enable_web_search and settings.web_search_api_key:
        sources.append("web")
    if settings.enable_media_generation and settings.ima_api_key:
        sources.append("generation")
    if settings.enable_media_search and settings.media_library_path:
        sources.append("media_search")
    if has_attachments and settings.enable_chat_attachments:
        sources.append("attachments")
    return sources


class TestDescriptionsAreUnchanged:
    def test_same_names_in_the_same_order(self):
        assert tuple(_SOURCE_DESCRIPTIONS) == _EXPECTED_ORDER

    def test_registry_declares_the_same_names_in_the_same_order(self):
        assert all_names() == _EXPECTED_ORDER

    @pytest.mark.parametrize("name,prefix", sorted(_EXPECTED_DESCRIPTION_PREFIX.items()))
    def test_each_description_still_starts_as_it_did(self, name, prefix):
        assert _SOURCE_DESCRIPTIONS[name].startswith(prefix)

    def test_no_description_is_empty_or_whitespace(self):
        for name, text in _SOURCE_DESCRIPTIONS.items():
            assert text.strip(), f"{name} has an empty routing description"

    @pytest.mark.parametrize("name,digest", sorted(_EXPECTED_DIGEST.items()))
    def test_each_description_is_byte_for_byte_what_it_was(self, name, digest):
        """Pins the exact text, not just its prefix.

        This is prompt input the classifier LLM reads, so any reword --
        even one that looks harmless -- is a routing change that should be
        made deliberately. A hash pins it without transcribing seven long
        strings into this file a second time.

        If this fails because you *meant* to change the wording: confirm
        the routing effect, then update the digest here in the same commit.
        """
        assert hashlib.sha256(_SOURCE_DESCRIPTIONS[name].encode()).hexdigest()[:16] == digest


class TestPermissionsAreUnchanged:
    def test_mapping_matches_the_pre_refactor_dict_exactly(self):
        assert SOURCE_PERMISSIONS == _EXPECTED_PERMISSIONS

    @pytest.mark.parametrize("name", ["sql", "documents", "attachments"])
    def test_an_ungated_source_is_still_ungated(self, name):
        """These three were absent from the old dict. `router_node` looks
        them up with `.get(name)`, so appearing here would start gating a
        source that every caller could previously reach."""
        assert name not in SOURCE_PERMISSIONS


class TestAvailabilityIsUnchanged:
    """The load-bearing equivalence proof, over the full flag matrix."""

    #: The six flags the old `if`-chain read, each paired with the extra
    #: config its clause also required. `media_search` and `documents`
    #: share no config; `documents`/`policy` share one, which is why a
    #: combinatorial sweep is worth more than per-source spot checks.
    _FLAGS = (
        ("enable_document_rag", {"rag_store_connection_string": SecretStr("x")}),
        ("enable_policy_rag", {"rag_store_connection_string": SecretStr("x")}),
        ("enable_web_search", {"web_search_api_key": SecretStr("tvly-x")}),
        ("enable_media_generation", {"ima_api_key": SecretStr("ima-x")}),
        ("enable_media_search", {"media_library_path": "/tmp/media"}),
        ("enable_chat_attachments", {}),
    )

    #: The four config values the clauses require, swept independently of
    #: the flags. Sweeping only flags-with-their-config would never produce
    #: a "flag on, config absent" case -- and that is precisely the `and`
    #: half of each clause. A mutation dropping `and s.web_search_api_key`
    #: survived an earlier version of this test for exactly that reason.
    _CONFIG = (
        ("rag_store_connection_string", SecretStr("x")),
        ("web_search_api_key", SecretStr("tvly-x")),
        ("ima_api_key", SecretStr("ima-x")),
        ("media_library_path", "/tmp/media"),
    )

    @pytest.mark.parametrize("bits", list(itertools.product([False, True], repeat=6)))
    @pytest.mark.parametrize("has_attachments", [False, True])
    def test_registry_matches_the_old_if_chain(self, bits, has_attachments):
        """Sweeps the six flags with every config present."""
        overrides: dict[str, object] = {}
        for (flag, extra), on in zip(self._FLAGS, bits, strict=True):
            overrides[flag] = on
            if on:
                overrides.update(extra)
        settings = _settings(**overrides)

        assert get_available_sources(settings, has_attachments=has_attachments) == (
            _old_get_available_sources(settings, has_attachments=has_attachments)
        )

    @pytest.mark.parametrize("cfg_bits", list(itertools.product([False, True], repeat=4)))
    @pytest.mark.parametrize("has_attachments", [False, True])
    def test_registry_matches_the_old_if_chain_with_config_absent(self, cfg_bits, has_attachments):
        """Sweeps the four config values with every flag ON.

        This is the dimension the flag sweep above cannot reach: it is what
        pins the `and <config>` half of each original clause, so turning a
        clause into a bare flag check fails here.
        """
        overrides: dict[str, object] = {flag: True for flag, _ in self._FLAGS}
        for (key, value), present in zip(self._CONFIG, cfg_bits, strict=True):
            overrides[key] = value if present else None
        settings = _settings(**overrides)

        assert get_available_sources(settings, has_attachments=has_attachments) == (
            _old_get_available_sources(settings, has_attachments=has_attachments)
        )

    def test_a_flag_without_its_config_does_not_make_a_source_available(self):
        """The rule the `if`-chain enforced with its `and`: a flag alone is
        not enough, because routing to a flagged-on but unconfigured source
        just fails."""
        settings = _settings(
            enable_document_rag=True,
            enable_policy_rag=True,
            enable_web_search=True,
            enable_media_generation=True,
            enable_media_search=True,
            rag_store_connection_string=None,
            web_search_api_key=None,
            ima_api_key=None,
            media_library_path=None,
        )
        assert get_available_sources(settings) == ["sql"]

    def test_sql_is_always_available(self):
        assert "sql" in get_available_sources(_settings())

    def test_attachments_needs_both_the_flag_and_the_request(self):
        on = _settings(enable_chat_attachments=True)
        assert "attachments" not in get_available_sources(on, has_attachments=False)
        assert "attachments" in get_available_sources(on, has_attachments=True)

        off = _settings(enable_chat_attachments=False)
        assert "attachments" not in get_available_sources(off, has_attachments=True)


class TestTheGuardIsNotVacuous:
    """Proves the equivalence test above would actually catch a drift.

    Without this, `test_registry_matches_the_old_if_chain` could pass
    because both sides are wrong in the same way. Here the oracle is
    perturbed deliberately and the comparison must fail.
    """

    def test_a_perturbed_oracle_disagrees_with_the_registry(self):
        settings = _settings(enable_web_search=True, web_search_api_key=SecretStr("tvly-x"))

        def _wrong_oracle(s, *, has_attachments=False):
            # Drops "web" -- the exact shape of a missed clause.
            return [
                n
                for n in _old_get_available_sources(s, has_attachments=has_attachments)
                if n != "web"
            ]

        assert get_available_sources(settings) == _old_get_available_sources(settings)
        assert get_available_sources(settings) != _wrong_oracle(settings)

    def test_a_missing_permission_would_be_caught(self):
        """If a descriptor stopped declaring POLICY_RAG_QUERY, the
        permission test would fail rather than silently pass."""
        mutated = dict(_EXPECTED_PERMISSIONS)
        del mutated["policy"]
        assert mutated != SOURCE_PERMISSIONS
