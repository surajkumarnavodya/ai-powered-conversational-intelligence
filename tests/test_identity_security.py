"""Unit tests for identity/security.py -- password hashing, locally-issued
JWT access tokens, and opaque refresh tokens."""

from __future__ import annotations

from datetime import UTC

import pytest
from identity.exceptions import LocalTokenValidationError
from identity.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    looks_like_local_token,
    validate_local_token,
    verify_password,
)

from config.settings import Settings


def _hs256_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        local_auth_enabled=True,
        auth_database_url="postgresql://placeholder",
        jwt_secret_key="s" * 40,
        jwt_issuer="text-to-sql-agent",
        jwt_audience="text-to-sql-web",
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestPasswordHashing:
    def test_hash_is_not_the_plaintext(self):
        assert hash_password("hunter2hunter2") != "hunter2hunter2"

    def test_verify_accepts_correct_password(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", h) is True

    def test_verify_rejects_wrong_password(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("wrong password", h) is False

    def test_verify_never_raises_on_malformed_hash(self):
        assert verify_password("anything", "not-a-real-argon2-hash") is False


class TestAccessTokens:
    def test_round_trips_subject_and_roles(self):
        settings = _hs256_settings()
        token = create_access_token("user-123", ("user", "analyst"), settings)
        claims = validate_local_token(token, settings)
        assert claims.subject == "user-123"
        assert claims.roles == ("user", "analyst")

    def test_looks_like_local_token_true_for_own_issuer(self):
        settings = _hs256_settings()
        token = create_access_token("user-123", ("user",), settings)
        assert looks_like_local_token(token, settings) is True

    def test_looks_like_local_token_false_for_different_issuer(self):
        settings = _hs256_settings()
        token = create_access_token("user-123", ("user",), settings)
        other_settings = _hs256_settings(jwt_issuer="a-different-issuer")
        assert looks_like_local_token(token, other_settings) is False

    def test_looks_like_local_token_false_for_garbage(self):
        settings = _hs256_settings()
        assert looks_like_local_token("not.a.jwt", settings) is False

    def test_validate_rejects_wrong_audience(self):
        settings = _hs256_settings()
        token = create_access_token("user-123", ("user",), settings)
        wrong_audience_settings = _hs256_settings(jwt_audience="some-other-app")
        with pytest.raises(LocalTokenValidationError):
            validate_local_token(token, wrong_audience_settings)

    def test_validate_rejects_wrong_signing_secret(self):
        settings = _hs256_settings()
        token = create_access_token("user-123", ("user",), settings)
        wrong_secret_settings = _hs256_settings(jwt_secret_key="t" * 40)
        with pytest.raises(LocalTokenValidationError):
            validate_local_token(token, wrong_secret_settings)

    def test_validate_rejects_expired_token(self):
        settings = _hs256_settings(access_token_expire_minutes=1)
        # Crafts a token with `exp` already in the past directly, rather
        # than sleeping in a unit test.
        from datetime import datetime, timedelta

        import jwt as pyjwt

        past_claims = {
            "sub": "user-123",
            "roles": ["user"],
            "token_type": "access",
            "iat": datetime.now(UTC) - timedelta(hours=1),
            "exp": datetime.now(UTC) - timedelta(minutes=1),
            "jti": "test",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        }
        expired_token = pyjwt.encode(
            past_claims, settings.jwt_secret_key.get_secret_value(), algorithm="HS256"
        )
        with pytest.raises(LocalTokenValidationError):
            validate_local_token(expired_token, settings)


class TestRefreshTokens:
    def test_generated_tokens_are_high_entropy_and_unique(self):
        a = generate_refresh_token()
        b = generate_refresh_token()
        assert a != b
        assert len(a) > 32

    def test_hash_is_deterministic(self):
        token = generate_refresh_token()
        assert hash_refresh_token(token) == hash_refresh_token(token)

    def test_hash_differs_for_different_tokens(self):
        assert hash_refresh_token(generate_refresh_token()) != hash_refresh_token(
            generate_refresh_token()
        )

    def test_hash_is_not_the_raw_token(self):
        token = generate_refresh_token()
        assert hash_refresh_token(token) != token


class TestTheJwtKeyChecksSurviveOptimisedBytecode:
    """UAT-readiness Phase 8, `BP-006`.

    `_signing_key` and `_verification_key` guarded their required key
    with four bare `assert` statements. `Settings`' own validator does
    guarantee both branches, so they never fired -- but `python -O` and
    `PYTHONOPTIMIZE` remove an `assert` from the bytecode entirely, and
    what follows is not "nothing happens": the next line called
    `.get_secret_value()` on `None`, turning a clear configuration
    failure into an `AttributeError` surfacing as an opaque HTTP 500 on
    every login.

    A subprocess is the only honest way to test this, because `-O` is
    interpreter start-up state and cannot be toggled from inside a
    running test.
    """

    def _probe(self, body: str) -> str:
        import subprocess
        import sys
        from pathlib import Path

        result = subprocess.run(
            [sys.executable, "-O", "-c", body],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert (
            result.returncode == 0
        ), f"probe crashed: stdout={result.stdout!r} stderr={result.stderr[-600:]!r}"
        return result.stdout

    def test_asserts_really_are_stripped_under_dash_o(self):
        # The premise the rest of this class rests on. Without it, a
        # passing test below would prove nothing about `-O`.
        out = self._probe(
            "try:\n"
            "    assert False, 'should have been stripped'\n"
            "except AssertionError:\n"
            "    print('ASSERT STILL LIVE')\n"
            "else:\n"
            "    print('ASSERT STRIPPED')\n"
        )

        assert "ASSERT STRIPPED" in out

    def test_a_missing_signing_key_is_a_clear_refusal_not_an_attribute_error(self):
        out = self._probe(
            "from identity.security import _signing_key\n"
            "from config.settings import Settings\n"
            "s = Settings(_env_file=None, local_auth_enabled=True, jwt_algorithm='HS256')\n"
            "s = s.model_copy(update={'jwt_secret_key': None})\n"
            "try:\n"
            "    _signing_key(s)\n"
            "except RuntimeError as exc:\n"
            "    print('REFUSED:' + ('not configured' in str(exc) and 'CLEAR' or 'VAGUE'))\n"
            "except AttributeError:\n"
            "    print('ATTRIBUTE_ERROR')\n"
            "else:\n"
            "    print('RETURNED_SOMETHING')\n"
        )

        assert "REFUSED:CLEAR" in out, out

    def test_a_missing_verification_key_is_a_clear_refusal(self):
        out = self._probe(
            "from identity.security import _verification_key\n"
            "from config.settings import Settings\n"
            "s = Settings(_env_file=None, local_auth_enabled=True, jwt_algorithm='HS256')\n"
            "s = s.model_copy(update={'jwt_secret_key': None})\n"
            "try:\n"
            "    _verification_key(s)\n"
            "except RuntimeError as exc:\n"
            "    print('REFUSED')\n"
            "except AttributeError:\n"
            "    print('ATTRIBUTE_ERROR')\n"
            "else:\n"
            "    print('RETURNED_SOMETHING')\n"
        )

        assert "REFUSED" in out, out

    def test_the_refusal_never_echoes_the_key_itself(self):
        # The message names the *kind* of key and the algorithm, never a
        # value -- CLAUDE.md 5.6 forbids logging a secret, and an
        # exception message reaches logs and error handlers.
        from identity.security import _MISSING_KEY_MESSAGE

        rendered = _MISSING_KEY_MESSAGE.format(kind="secret key", algorithm="HS256")

        assert "secret key" in rendered
        assert "HS256" in rendered
        assert "get_secret_value" not in rendered

    def test_a_configured_key_is_returned_unchanged(self):
        # The compatibility half: the guard must not alter the normal path.
        from identity.security import _signing_key

        settings = _hs256_settings()

        assert _signing_key(settings) == settings.jwt_secret_key.get_secret_value()
