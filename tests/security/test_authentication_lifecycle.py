"""Phase 3 (authentication) adversarial regressions.

Every test here reproduces a finding measured against the running app on
2026-10-28 and recorded in `docs/uat-readiness/phase-3-authentication.md`.
Each one *fails* on the code as it stood before that phase's fixes -- see
each class's own docstring for the measured before/after, and
`docs/uat-readiness/FINDINGS_REGISTER.md` for the finding IDs.

Harness: a real `fastapi.testclient.TestClient` over the full
`api.main.app`, with the identity engine pointed at an in-memory SQLite
database -- the same pattern `tests/test_api_identity_auth.py` establishes
and for the same reason (see that file's docstring).
"""

from __future__ import annotations

import identity.db as identity_db_mod
import identity.security as identity_security_mod
import pytest
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.db import get_identity_session
from identity.models import Base
from identity.repositories.tokens import create_password_reset_token
from identity.repositories.users import assign_role, get_user_by_email
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
from config.settings import Settings

_PASSWORD = "correcthorsebattery1"

_SETTINGS = Settings(
    local_auth_enabled=True,
    auth_database_url="postgresql://placeholder/unused",
    jwt_secret_key="s" * 40,
    jwt_issuer="text-to-sql-agent",
    jwt_audience="text-to-sql-web",
    allow_public_registration=True,
    cookie_secure=False,  # TestClient runs over plain HTTP
    password_min_length=12,
    max_login_attempts=3,
    login_lockout_minutes=15,
    login_rate_limit_per_minute=100_000,
    register_rate_limit_per_hour=100_000,
    password_reset_rate_limit_per_hour=100_000,
)


@pytest.fixture(autouse=True)
def _identity_test_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(identity_db_mod, "get_identity_engine", lambda settings=None: engine)
    for module in (api_auth_mod, identity_auth_mod, identity_authz_mod):
        monkeypatch.setattr(module, "get_settings", lambda: _SETTINGS)

    session = get_identity_session(_SETTINGS)
    seed_rbac(session)
    session.close()

    for name in dir(identity_auth_mod):
        if name.endswith("_limiters"):
            getattr(identity_auth_mod, name).clear()
    yield engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app)


def _register(client: TestClient, email: str, password: str = _PASSWORD) -> dict:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": "Test Person"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str, password: str = _PASSWORD):
    client.cookies.clear()
    return client.post("/auth/login", json={"email": email, "password": password})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestLoginSpendsTheSamePasswordWorkOnEveryPath:
    """`AUN-001` -- login was a fully separable account-enumeration oracle
    by response time.

    Measured before the fix, 25 samples per group: **5.94 ms** median for
    an email with no account, **80.87 ms** for one with a password, and
    **6.73 ms** for a Google-only account (`password_hash IS NULL`) --
    `min(present) = 73.49 ms > max(absent) = 9.68 ms`, i.e. the two sample
    sets did not overlap at all. After: 78.99 / 76.59 / 80.62 ms, ratio
    1.0x.

    These tests assert the *mechanism* rather than a wall-clock duration,
    because a timing assertion would be flaky in CI while telling us less:
    the invariant the fix establishes is that every login path performs
    exactly one Argon2id verification, and that is what is checked.
    """

    @pytest.fixture
    def verify_calls(self, monkeypatch):
        """Counts real Argon2id verifications.

        Counted at `identity.security.verify_password` -- the one
        primitive that actually does the work -- rather than at the
        route's call sites, because
        `verify_password_against_decoy` delegates to it. So "one
        verification" means one Argon2id hash comparison regardless of
        which path reached it, which is exactly the invariant under test.
        """
        calls: list[str] = []
        real_verify = identity_security_mod.verify_password

        def _counting_verify(password, password_hash):
            calls.append(password_hash)
            return real_verify(password, password_hash)

        monkeypatch.setattr(identity_security_mod, "verify_password", _counting_verify)
        monkeypatch.setattr(identity_auth_mod, "verify_password", _counting_verify)
        return calls

    def test_a_nonexistent_account_still_performs_one_verification(self, client, verify_calls):
        verify_calls.clear()
        response = _login(client, "no-such-person@example.com")
        assert response.status_code == 401
        assert len(verify_calls) == 1, (
            "login returned without hashing anything, which is the timing oracle: "
            f"{len(verify_calls)} verification(s)"
        )

    def test_an_existing_account_performs_one_verification(self, client, verify_calls):
        _register(client, "alice@example.com")
        verify_calls.clear()
        response = _login(client, "alice@example.com", "wrong-password-entirely")
        assert response.status_code == 401
        assert len(verify_calls) == 1

    def test_a_google_only_account_still_performs_one_verification(self, client, verify_calls):
        """A row with `password_hash IS NULL` used to short-circuit, which
        distinguished "this email is Google-only" from "this email has a
        local password" -- the side channel `api/identity_auth.py`'s own
        comment says it closed at the status-code level."""
        _register(client, "google@example.com")
        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, "google@example.com")
        user.password_hash = None
        session.commit()
        session.close()

        verify_calls.clear()
        response = _login(client, "google@example.com")
        assert response.status_code == 401
        assert len(verify_calls) == 1, (
            "a hash-less account short-circuited, which distinguishes it by timing: "
            f"{len(verify_calls)} verification(s)"
        )

    def test_the_decoy_never_accepts_any_password(self):
        """The decoy exists for its cost, not its answer -- if it could
        ever return True it would be an authentication bypass."""
        for candidate in ("", "decoy", identity_security_mod._DECOY_PASSWORD, "x" * 200):
            assert identity_security_mod.verify_password_against_decoy(candidate) is False

    def test_every_login_failure_returns_the_identical_body(self, client):
        """The response body must stay as uninformative as the timing now
        is: absent, wrong-password, locked and not-active all share
        `identity.exceptions._GENERIC_LOGIN_FAILURE`."""
        _register(client, "alice@example.com")
        bodies = {_login(client, "ghost@example.com").text}
        bodies.add(_login(client, "alice@example.com", "wrong-password-entirely").text)
        for _ in range(_SETTINGS.max_login_attempts):
            _login(client, "alice@example.com", "wrong-password-entirely")
        bodies.add(_login(client, "alice@example.com").text)  # now locked
        assert len(bodies) == 1, f"login failures are distinguishable by body: {bodies}"


class TestAPasswordResetClearsTheLockout:
    """`AUN-002` -- a lockout outlived a completed password reset, which
    broke account recovery and made the lockout a denial-of-service
    primitive against any account whose email is known.

    Measured before the fix: 3 wrong guesses, then
    `POST /auth/reset-password` -> 200, then login with the new password
    -> **401**, with `failed_login_count=3` and `locked_until` unchanged.
    """

    def _lock_out(self, client, email):
        for _ in range(_SETTINGS.max_login_attempts):
            _login(client, email, "wrong-password-entirely")
        assert _login(client, email).status_code == 401, "expected the account to be locked"

    def test_reset_password_restores_access_immediately(self, client):
        _register(client, "alice@example.com")
        self._lock_out(client, "alice@example.com")

        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, "alice@example.com")
        raw_token = create_password_reset_token(session, user.id, expire_minutes=60)
        session.close()

        reset = client.post(
            "/auth/reset-password",
            json={"token": raw_token, "new_password": "brandnewpassword987"},
        )
        assert reset.status_code == 200, reset.text

        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, "alice@example.com")
        assert user.failed_login_count == 0
        assert user.locked_until is None
        session.close()

        assert _login(client, "alice@example.com", "brandnewpassword987").status_code == 200

    def test_change_password_also_clears_the_lockout(self, client):
        """Same fact, one owner: the clear lives in
        `identity.repositories.users.change_password`, so the
        authenticated self-service route gets it too."""
        body = _register(client, "alice@example.com")
        token = body["access_token"]
        self._lock_out(client, "alice@example.com")

        changed = client.post(
            "/auth/change-password",
            json={"current_password": _PASSWORD, "new_password": "brandnewpassword987"},
            headers=_bearer(token),
        )
        assert changed.status_code == 200, changed.text
        assert _login(client, "alice@example.com", "brandnewpassword987").status_code == 200

    def test_the_lockout_itself_still_works(self, client):
        """Non-vacuity guard: the fix must not have disabled the lockout
        it clears. Without this, deleting `record_login_failure`'s lock
        entirely would still pass the two tests above."""
        _register(client, "alice@example.com")
        for _ in range(_SETTINGS.max_login_attempts):
            _login(client, "alice@example.com", "wrong-password-entirely")
        assert (
            _login(client, "alice@example.com").status_code == 401
        ), "the correct password was accepted during the lockout window"
        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, "alice@example.com")
        assert user.locked_until is not None
        session.close()


class TestANonActiveAccountLosesAccessOnItsNextRequest:
    """`AUN-003` -- `api.identity_authz.require_local_user` re-read the
    caller's tenant status on every request but never the caller's *own*
    `users.status`, so suspending an account did nothing until its access
    token expired.

    Measured before the fix, with `status` flipped to `suspended` and the
    token untouched: `GET /auth/me` **200**, `GET /conversations` **200**.
    """

    @pytest.mark.parametrize("status_value", ["suspended", "inactive", "pending_verification"])
    @pytest.mark.parametrize("path", ["/auth/me", "/conversations", "/auth/sessions"])
    def test_a_live_token_stops_working(self, client, status_value, path):
        token = _register(client, "alice@example.com")["access_token"]
        assert client.get(path, headers=_bearer(token)).status_code == 200

        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, "alice@example.com")
        user.status = status_value
        session.commit()
        session.close()

        response = client.get(path, headers=_bearer(token))
        assert (
            response.status_code == 401
        ), f"a {status_value!r} account still reached {path}: {response.status_code}"

    def test_the_refusal_does_not_say_why(self, client):
        """A caller must not learn "suspended" versus "no such account"
        versus "wrong tenant" from the refusal -- the same
        anti-enumeration rule `security.tenancy` applies."""
        token = _register(client, "alice@example.com")["access_token"]
        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, "alice@example.com")
        user.status = "suspended"
        session.commit()
        session.close()

        body = client.get("/auth/me", headers=_bearer(token)).text
        assert "suspend" not in body.lower()
        assert "tenant" not in body.lower()

    def test_an_active_account_is_unaffected(self, client):
        """Non-vacuity guard: the new check must refuse only non-active
        accounts, not everyone."""
        token = _register(client, "alice@example.com")["access_token"]
        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, "alice@example.com")
        assign_role(session, user_id=user.id, role_name="admin", assigned_by_user_id=user.id)
        session.commit()
        session.close()
        assert client.get("/auth/me", headers=_bearer(token)).status_code == 200
        assert client.get("/conversations", headers=_bearer(token)).status_code == 200


#: Sentinel for "leave this claim out entirely" in the forged-token table
#: above -- distinct from `None`, which is itself a claim value worth
#: testing.
class _Omit:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<omit>"


_OMIT = _Omit()


class TestJwtValidationRejectsEveryForgery:
    """No finding -- a positive result recorded as a regression so it
    stays true. All 15 forgeries below were measured as 401 on
    2026-10-28; `docs/uat-readiness/phase-3-authentication.md` has the
    full table.
    """

    @staticmethod
    def _claims(subject: str, **overrides):
        import time
        import uuid

        now = int(time.time())
        claims = {
            "sub": subject,
            "roles": ["admin"],
            "tid": "default",
            "token_type": "access",
            "iat": now,
            "exp": now + 900,
            "jti": str(uuid.uuid4()),
            "iss": "text-to-sql-agent",
            "aud": "text-to-sql-web",
        }
        claims.update(overrides)
        return {k: v for k, v in claims.items() if v is not _OMIT}

    @pytest.mark.parametrize(
        ("label", "algorithm", "key", "overrides"),
        [
            ("unsigned alg=none", "none", "", {}),
            ("HS256 with the wrong secret", "HS256", "w" * 40, {}),
            ("HS384 with the right secret", "HS384", "s" * 40, {}),
            ("HS512 with the right secret", "HS512", "s" * 40, {}),
            ("forged issuer", "HS256", "s" * 40, {"iss": "https://evil.example"}),
            ("forged audience", "HS256", "s" * 40, {"aud": "evil"}),
            ("missing issuer", "HS256", "s" * 40, {"iss": _OMIT}),
            ("missing audience", "HS256", "s" * 40, {"aud": _OMIT}),
            ("expired", "HS256", "s" * 40, {"exp": 1_000_000}),
            ("not yet valid", "HS256", "s" * 40, {"nbf": 4_000_000_000}),
            (
                "a refresh token presented as an access token",
                "HS256",
                "s" * 40,
                {"token_type": "refresh"},
            ),
            ("no token_type at all", "HS256", "s" * 40, {"token_type": _OMIT}),
        ],
    )
    def test_a_forged_token_is_refused(self, client, label, algorithm, key, overrides):
        import jwt

        subject = _register(client, "alice@example.com")["user"]["id"]
        token = jwt.encode(self._claims(subject, **overrides), key=key, algorithm=algorithm)
        response = client.get("/auth/me", headers=_bearer(token))
        assert response.status_code == 401, f"{label} was accepted"

    def test_stripping_the_signature_is_refused(self, client):
        token = _register(client, "alice@example.com")["access_token"]
        assert client.get("/auth/me", headers=_bearer(token)).status_code == 200
        unsigned = token.rsplit(".", 1)[0] + "."
        assert client.get("/auth/me", headers=_bearer(unsigned)).status_code == 401

    def test_rewriting_the_alg_header_to_none_is_refused(self, client):
        import base64
        import json

        token = _register(client, "alice@example.com")["access_token"]
        header_b64, payload_b64, _ = token.split(".")
        header = json.loads(base64.urlsafe_b64decode(header_b64 + "=="))
        header["alg"] = "none"
        rewritten = (
            base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
            + f".{payload_b64}."
        )
        assert client.get("/auth/me", headers=_bearer(rewritten)).status_code == 401


class TestRefreshTokenRotationAndReuseDetection:
    """No finding -- measured correct on 2026-10-28 and pinned here."""

    def test_a_refresh_rotates_the_token(self, client):
        _register(client, "alice@example.com")
        first = client.cookies.get("refresh_token")
        assert client.post("/auth/refresh").status_code == 200
        assert client.cookies.get("refresh_token") != first

    def test_replaying_a_rotated_token_revokes_the_whole_family(self, client):
        _register(client, "alice@example.com")
        first = client.cookies.get("refresh_token")
        assert client.post("/auth/refresh").status_code == 200
        newest = client.cookies.get("refresh_token")

        client.cookies.set("refresh_token", first)
        assert client.post("/auth/refresh").status_code == 401

        # The legitimate newest token must die with the family, otherwise
        # reuse detection only annoys the attacker.
        client.cookies.set("refresh_token", newest)
        assert client.post("/auth/refresh").status_code == 401

    def test_logout_invalidates_the_refresh_token(self, client):
        _register(client, "alice@example.com")
        assert client.post("/auth/logout").status_code == 200
        assert client.post("/auth/refresh").status_code == 401


class TestPasswordPolicyIsEnforcedServerSide:
    """`AUN-004` for the sequential-run case; the rest are positive
    results pinned as regressions."""

    @pytest.mark.parametrize(
        "password",
        [
            "short",  # under the minimum
            "a" * 11,  # one short of the minimum
            "aaaaaaaaaaaa",  # a repeated pattern
            "password1234",  # a known-weak base word
            "qwertyuiop12",  # a keyboard pattern
            "123456789012",  # AUN-004: a sequential run with a digit appended
            "1234560abcde",  # AUN-004: the minimal bypass shape
            "12345678901234567890",  # AUN-004: long, still sequential
        ],
    )
    def test_a_weak_password_is_refused_at_registration(self, client, password):
        response = client.post(
            "/auth/register",
            json={
                "email": "weak@example.com",
                "password": password,
                "display_name": "Test Person",
            },
        )
        assert response.status_code == 400, f"{password!r} was accepted: {response.text}"

    def test_a_password_containing_the_users_own_email_is_refused(self, client):
        response = client.post(
            "/auth/register",
            json={
                "email": "alice@example.com",
                "password": "Alice@Example.COM",
                "display_name": "Test Person",
            },
        )
        assert response.status_code == 400

    def test_a_strong_password_is_still_accepted(self, client):
        """Non-vacuity guard for the whole class: the policy must reject
        weak passwords, not all of them."""
        response = client.post(
            "/auth/register",
            json={
                "email": "strong@example.com",
                "password": "tangerine-walrus-9134",
                "display_name": "Test Person",
            },
        )
        assert response.status_code == 201, response.text


class TestPrivilegedFieldsCannotBeSetByTheClient:
    """No finding -- `extra="forbid"` on the request models blocked every
    mass-assignment attempt measured on 2026-10-28. Pinned so a future
    model without it is caught."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("role", "admin"),
            ("roles", ["admin"]),
            ("tenant_id", "other-tenant"),
            ("status", "active"),
            ("is_email_verified", True),
            ("is_admin", True),
            ("id", "00000000-0000-0000-0000-000000000001"),
            ("user_id", "00000000-0000-0000-0000-000000000001"),
            ("password_hash", "$argon2id$v=19$m=65536,t=3,p=4$forged"),
            ("failed_login_count", 0),
        ],
    )
    def test_registration_refuses_an_injected_privileged_field(self, client, field, value):
        response = client.post(
            "/auth/register",
            json={
                "email": "evil@example.com",
                "password": _PASSWORD,
                "display_name": "Test Person",
                field: value,
            },
        )
        assert response.status_code == 422, f"{field!r} was accepted: {response.text}"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("roles", ["admin"]),
            ("email", "hijacked@example.com"),
            ("status", "active"),
            ("tenant_id", "other-tenant"),
            ("is_email_verified", True),
        ],
    )
    def test_profile_update_refuses_an_injected_privileged_field(self, client, field, value):
        token = _register(client, "alice@example.com")["access_token"]
        response = client.patch(
            "/auth/me",
            json={"display_name": "New Name", field: value},
            headers=_bearer(token),
        )
        assert response.status_code == 422, f"{field!r} was accepted: {response.text}"

    def test_a_clean_profile_update_still_works(self, client):
        """Non-vacuity guard: `extra="forbid"` must reject the extra
        field, not the whole request shape."""
        token = _register(client, "alice@example.com")["access_token"]
        response = client.patch(
            "/auth/me", json={"display_name": "New Name"}, headers=_bearer(token)
        )
        assert response.status_code == 200, response.text
        assert response.json()["display_name"] == "New Name"
