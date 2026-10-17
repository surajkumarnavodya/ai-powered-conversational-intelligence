"""Tests for `agent/translation.py` -- locale translation of a governed answer.

The tests that matter here are the ones proving a translation which
*fails* to preserve an invariant never reaches the caller. A passing
happy path says little; the protocol is only worth having if the
verification step genuinely rejects.
"""

from __future__ import annotations

import pytest

from agent.translation import (
    SUPPORTED_LOCALES,
    protect,
    translate_answer,
    verify_restored,
)
from governance.result_policy import RESTRICTED_MASK


def _settings():
    from config.settings import Settings

    return Settings()


class TestProtect:
    """What must never reach the model."""

    def test_numbers_are_protected(self):
        p = protect("Revenue rose to 1,234.56 last year.")
        assert "1,234.56" in p.spans
        assert "1,234.56" not in p.masked

    def test_signs_and_percentages_are_kept_with_the_number(self):
        p = protect("Margin moved -12.5% against +3%.")
        assert "-12.5%" in p.spans
        assert "+3%" in p.spans

    def test_currency_is_protected_with_its_symbol(self):
        for text, span in (
            ("Total was $1,200 overall.", "$1,200"),
            ("Total was ₹4,500 overall.", "₹4,500"),
            ("Total was €99.99 overall.", "€99.99"),
        ):
            assert span in protect(text).spans, text

    def test_dates_are_protected_whole_not_split_into_numbers(self):
        p = protect("Measured on 2026-10-17 across the estate.")
        assert "2026-10-17" in p.spans

    def test_sql_in_a_fenced_block_is_protected(self):
        text = "Here is the query:\n```sql\nSELECT TOP 10 Name FROM DimProduct\n```\nIt returns ten rows."
        p = protect(text)
        assert any("SELECT TOP 10 Name FROM DimProduct" in s for s in p.spans)
        assert "SELECT" not in p.masked

    def test_inline_code_is_protected(self):
        p = protect("The column `ListPrice` carries it.")
        assert "`ListPrice`" in p.spans

    def test_citations_and_source_links_are_protected_whole(self):
        p = protect("See [the 2026 filing](https://example.com/a?b=1) for detail.")
        assert "[the 2026 filing](https://example.com/a?b=1)" in p.spans
        assert "example.com" not in p.masked

    def test_the_governance_mask_is_protected(self):
        # A translated mask could read as content rather than as a
        # withheld value.
        p = protect(f"The owner is {RESTRICTED_MASK} for this role.")
        assert RESTRICTED_MASK in p.spans
        assert RESTRICTED_MASK not in p.masked

    def test_a_number_inside_a_code_block_is_protected_once(self):
        # Not twice: the block wins, so restoration cannot interleave.
        p = protect("```\nSELECT 42\n```")
        assert len(p.spans) == 1

    def test_prose_survives_for_the_model_to_translate(self):
        p = protect("Revenue rose to 1,234 in 2026-01-01 terms.")
        assert "Revenue rose to" in p.masked


class TestRestoreAndVerify:
    def test_a_faithful_round_trip_restores_the_original(self):
        text = "Revenue was $1,200 on 2026-10-17, see [source](https://x.test/a)."
        p = protect(text)
        assert p.restore(p.masked) == text

    def test_verification_accepts_a_translation_that_moved_a_figure(self):
        # Word order legitimately changes between languages; the
        # invariant is the multiset of spans, not their positions.
        original = "Revenue was 1,200 in 2026."
        moved = "In 2026, revenue was 1,200."
        assert verify_restored(original, moved) is True

    @pytest.mark.parametrize(
        "corrupted, why",
        [
            ("Revenue was 1.200 in 2026.", "decimal separator localised"),
            ("Revenue was 1,20 in 2026.", "digit dropped"),
            ("Revenue was 1,200.", "a figure removed entirely"),
            ("Revenue was 1,200 in 2026 and 1,200 again.", "a figure duplicated"),
            ("Revenue was 1,300 in 2026.", "a figure altered"),
        ],
    )
    def test_verification_rejects_every_way_a_figure_can_drift(self, corrupted, why):
        original = "Revenue was 1,200 in 2026."
        assert verify_restored(original, corrupted) is False, why

    def test_verification_rejects_a_dropped_citation(self):
        original = "See [the filing](https://x.test/a) for detail."
        assert verify_restored(original, "See the filing for detail.") is False

    def test_verification_rejects_a_rewritten_sql_block(self):
        original = "```sql\nSELECT a FROM t\n```"
        assert verify_restored(original, "```sql\nSELECT b FROM t\n```") is False


class TestTranslateAnswer:
    """The two-outcome contract: a verified translation, or the original."""

    def test_an_unsupported_locale_returns_the_original_untranslated(self):
        out, translated = translate_answer("Revenue rose.", "de", _settings())
        assert (out, translated) == ("Revenue rose.", False)

    def test_every_supported_locale_is_a_real_ui_locale(self):
        # If these drift apart, the UI would offer a language the answer
        # cannot be translated into, or vice versa.
        assert set(SUPPORTED_LOCALES) == {"en", "es", "fr", "hi", "mr"}

    def test_empty_text_is_returned_unchanged_without_a_model_call(self):
        called = False

        def _never(*_args, **_kwargs):
            nonlocal called
            called = True
            return "x"

        out, translated = translate_answer("   ", "fr", _settings(), call_model=_never)
        assert (out, translated) == ("   ", False)
        assert called is False

    def test_text_with_no_prose_skips_the_model_entirely(self):
        # A bare figure has nothing to translate; calling a model would
        # be pure cost.
        called = False

        def _never(*_args, **_kwargs):
            nonlocal called
            called = True
            return "x"

        out, translated = translate_answer("1,234", "fr", _settings(), call_model=_never)
        assert translated is False
        assert called is False

    def test_a_faithful_model_produces_a_translation(self):
        text = "Revenue was 1,200 in 2026."

        def _faithful(_system, user, _settings, _max_tokens=0):
            # Mimics a well-behaved model: prose translated, placeholders
            # left exactly where they were.
            return user.replace("Revenue was", "Les revenus etaient de").replace(" in ", " en ")

        out, translated = translate_answer(text, "fr", _settings(), call_model=_faithful)
        assert translated is True
        assert "1,200" in out and "2026" in out
        assert "Les revenus" in out

    def test_a_model_that_corrupts_a_figure_is_discarded(self):
        # The central guarantee. A model that localises the decimal
        # separator inside a placeholder must not reach the caller.
        text = "Revenue was 1,200 in 2026."

        def _corrupting(_system, _user, _settings, _max_tokens=0):
            return "Les revenus etaient de 1.200 en 2026."

        out, translated = translate_answer(text, "fr", _settings(), call_model=_corrupting)
        assert translated is False
        assert out == text

    def test_a_model_that_drops_a_placeholder_is_discarded(self):
        text = "Revenue was 1,200 in 2026."

        def _dropping(_system, user, _settings, _max_tokens=0):
            return user.split("⟦")[0]

        out, translated = translate_answer(text, "fr", _settings(), call_model=_dropping)
        assert translated is False
        assert out == text

    def test_a_model_that_translates_the_restricted_mask_is_discarded(self):
        text = f"The owner is {RESTRICTED_MASK} for this role."

        def _translating_the_mask(_system, _user, _settings, _max_tokens=0):
            return "Le proprietaire est [restreint] pour ce role."

        out, translated = translate_answer(
            text, "fr", _settings(), call_model=_translating_the_mask
        )
        assert translated is False
        assert out == text
        assert RESTRICTED_MASK in out

    def test_a_model_error_returns_the_original_rather_than_failing(self):
        def _raising(*_args, **_kwargs):
            raise RuntimeError("ollama unreachable")

        out, translated = translate_answer("Revenue rose.", "fr", _settings(), call_model=_raising)
        assert (out, translated) == ("Revenue rose.", False)

    def test_an_empty_model_response_returns_the_original(self):
        out, translated = translate_answer(
            "Revenue rose.", "fr", _settings(), call_model=lambda *a, **k: "   "
        )
        assert translated is False

    def test_sql_and_citations_survive_a_faithful_translation(self):
        text = (
            "The query below returned 42 rows on 2026-10-17.\n"
            "```sql\nSELECT TOP 10 Name FROM DimProduct\n```\n"
            "See [the catalogue](https://x.test/cat) for the definition."
        )

        def _faithful(_system, user, _settings, _max_tokens=0):
            return user.replace("The query below returned", "La requete ci-dessous a renvoye")

        out, translated = translate_answer(text, "fr", _settings(), call_model=_faithful)
        assert translated is True
        assert "SELECT TOP 10 Name FROM DimProduct" in out
        assert "[the catalogue](https://x.test/cat)" in out
        assert "42" in out and "2026-10-17" in out

    @pytest.mark.parametrize("locale", ["es", "fr", "hi", "mr"])
    def test_the_contract_holds_for_every_non_english_locale(self, locale):
        text = "Revenue was 1,200 in 2026."

        def _faithful(_system, user, _settings, _max_tokens=0):
            return user

        out, translated = translate_answer(text, locale, _settings(), call_model=_faithful)
        assert translated is True
        assert "1,200" in out
