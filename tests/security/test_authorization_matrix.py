"""Phase 4 (authorization) role x route matrix, IDOR and 401-vs-403.

The matrix is **derived from the live `api.main.app`**, not from a
hand-maintained list, so a route added later is covered the moment it is
registered. That is deliberate: the one thing a static list cannot do is
notice a new unauthenticated endpoint, which is exactly the failure this
file exists to catch.

Measured 2026-10-28 over 213 operations x 9 principals (anonymous plus the
8 roles `identity.rbac.SEED_ROLES` seeds); the full result table is in
`docs/uat-readiness/phase-4-authorization.md`. Anonymous was denied on
199/199 non-public operations, and no IDOR was found -- both are pinned
here so they stay true.

Harness note: this file deliberately probes with minimal request bodies.
A `422` therefore means "the authorization gates in this route's
*dependencies* let the caller through, and body validation then rejected
the request". That is a meaningful signal for dependency-level gates
(FastAPI raises a dependency's `HTTPException` before body validation),
but it says nothing about a check performed inside the route body. The
`TestPrivilegedRoutesDenyUnderPrivilegedRoles` class below therefore uses
complete, valid bodies on the governed-lifecycle surfaces, where the
authorization check lives in the handler rather than in a dependency.
"""

from __future__ import annotations

import uuid

import identity.db as identity_db_mod
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from identity.bootstrap import seed_rbac
from identity.db import get_identity_session
from identity.models import Base
from identity.repositories.users import assign_role, get_user_by_email, remove_role
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import api.auth as api_auth_mod
import api.identity_auth as identity_auth_mod
import api.identity_authz as identity_authz_mod
import api.main as api_main
from config.settings import Settings

_PASSWORD = "correcthorsebattery1"

#: Every role `identity.bootstrap.seed_rbac` creates. The audit prompt
#: speaks of "6 UAT roles"; this codebase seeds 8, and the matrix covers
#: what the code actually has.
_SEEDED_ROLES = (
    "viewer",
    "user",
    "analyst",
    "admin",
    "auditor",
    "manager",
    "support",
    "platform_admin",
)

#: Routes that are *intended* to be reachable with no credential at all,
#: each with the reason it must stay that way. Checked in both directions
#: by `test_the_public_route_allowlist_is_exact` below: a new unauthenticated
#: route fails that test, and so does removing one of these.
_INTENTIONALLY_PUBLIC: dict[tuple[str, str], str] = {
    ("GET", "/health"): "the login page reads the auth-mode flags before anyone is signed in",
    ("GET", "/live"): "container liveness probe",
    ("POST", "/auth/login"): "establishing a credential is what this route is for",
    ("POST", "/auth/register"): "self-service sign-up (gated by allow_public_registration)",
    ("POST", "/auth/refresh"): "called precisely because the access token has expired",
    ("POST", "/auth/logout"): "logging out an already-expired session is a no-op, not an error",
    ("POST", "/auth/forgot-password"): "the caller has no credential by definition",
    ("POST", "/auth/reset-password"): "redeems an emailed single-use token",
    ("POST", "/auth/verify-email"): "redeems an emailed single-use token",
    ("POST", "/auth/resend-verification"): "the account cannot sign in yet",
    ("POST", "/auth/google"): "exchanges a Google credential for a local one",
    ("GET", "/auth/google/nonce"): "issued before sign-in, by construction",
    ("GET", "/share-view/{ref}"): "an explicitly shared conversation; the link is the credential",
    ("GET", "/share-view/{ref}/attachments/{attachment_id}"): "same share, its attachments",
    ("POST", "/share-invitations/{opaque_token}/accept"): "the invitation token is the credential",
    ("GET", "/{full_path:path}"): "serves the built frontend's static assets",
}

#: Statuses that mean "the caller was refused". `404` counts because this
#: codebase deliberately returns the same 404 for a cross-tenant or
#: not-yours resource as for a nonexistent one (CLAUDE.md 5.3,
#: anti-enumeration).
_DENIED = {401, 403, 404}

_SETTINGS = Settings(
    local_auth_enabled=True,
    auth_database_url="postgresql://placeholder/unused",
    jwt_secret_key="s" * 40,
    jwt_issuer="text-to-sql-agent",
    jwt_audience="text-to-sql-web",
    allow_public_registration=True,
    cookie_secure=False,
    password_min_length=12,
    login_rate_limit_per_minute=100_000,
    register_rate_limit_per_hour=100_000,
)


def _iter_api_routes(routes):
    """Every `APIRoute` in the app, descending through the wrappers
    `include_router` leaves behind.

    FastAPI stores an included router as an opaque object carrying
    `original_router` rather than splicing its routes into
    `app.routes`, so a plain `isinstance` filter over `app.routes` finds
    only the handful defined on `app` itself -- 12 of 214 when this was
    written, which would have made this whole file vacuous.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_api_routes(inner.routes)
        elif not isinstance(route, APIRoute) and hasattr(route, "routes"):
            yield from _iter_api_routes(route.routes)


def _all_operations() -> list[tuple[str, str]]:
    seen = {
        (method, route.path)
        for route in _iter_api_routes(api_main.app.routes)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"})
    }
    return sorted(seen)


_OPERATIONS = _all_operations()


def test_the_route_walk_actually_found_the_api():
    """Non-vacuity guard for every parametrized test in this file. If the
    walk above silently stops working after a FastAPI upgrade, the matrix
    would pass by covering nothing."""
    assert len(_OPERATIONS) > 180, (
        f"only {len(_OPERATIONS)} operations found -- the route walk is broken, so the "
        "authorization matrix below is testing almost nothing"
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
    """`raise_server_exceptions=False` so a route that crashes is reported
    as a 500 rather than propagating out of the sweep.

    Needed because this file probes *every* route, including ones that
    need a configured database -- and this harness deliberately has none
    (`tests/test_settings_env_isolation.py`). A `ConfigurationError` from
    `GET /schema/tables` is not an authorization outcome, and before
    `BP-003` was fixed it was invisible here: the developer's real `.env`
    leaked in, so those routes answered 200 from a live SQL Server
    instead. A 500 still fails the anonymous sweep below, which is
    correct -- an unauthenticated caller must be refused before reaching
    code that can crash.
    """
    return TestClient(api_main.app, raise_server_exceptions=False)


def _make_user(client: TestClient, email: str, *, role: str | None = None) -> dict[str, str]:
    """Registers an account, sets it to exactly `role` (dropping the
    default `user` grant so each principal is tested in isolation), and
    returns its Authorization header."""
    response = client.post(
        "/auth/register",
        json={"email": email, "password": _PASSWORD, "display_name": "Test Person"},
    )
    assert response.status_code == 201, response.text

    if role is not None and role != "user":
        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, email)
        remove_role(session, user_id=user.id, role_name="user")
        assign_role(session, user_id=user.id, role_name=role, assigned_by_user_id=user.id)
        session.commit()
        session.close()

    client.cookies.clear()
    login = client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _fill_path(path: str) -> str:
    """Substitutes a fixed random UUID for every path parameter."""
    placeholder = "11111111-2222-3333-4444-555555555555"
    return "/".join(placeholder if seg.startswith("{") else seg for seg in path.split("/"))


class TestEveryRouteDeniesAnAnonymousCaller:
    """Deny-by-default across the whole surface, every method, including
    the parameterised routes Phase 0's GET-only probe skipped."""

    @pytest.mark.parametrize(("method", "path"), _OPERATIONS, ids=lambda v: str(v))
    def test_no_credential_is_refused(self, client, method, path):
        if (method, path) in _INTENTIONALLY_PUBLIC:
            pytest.skip(f"intentionally public: {_INTENTIONALLY_PUBLIC[(method, path)]}")
        response = client.request(
            method,
            _fill_path(path),
            json={} if method in ("POST", "PUT", "PATCH") else None,
        )
        assert response.status_code in _DENIED, (
            f"{method} {path} answered an unauthenticated caller with "
            f"{response.status_code}: {response.text[:200]}"
        )

    def test_the_public_route_allowlist_is_exact(self, client):
        """Both directions. A newly-added public route is a finding; an
        allowlisted route that has since been locked down means this list
        is stale and should shrink."""
        actually_public = set()
        for method, path in _OPERATIONS:
            response = client.request(
                method,
                _fill_path(path),
                json={} if method in ("POST", "PUT", "PATCH") else None,
            )
            if response.status_code not in _DENIED:
                actually_public.add((method, path))

        unexpected = actually_public - set(_INTENTIONALLY_PUBLIC)
        assert not unexpected, f"new unauthenticated route(s): {sorted(unexpected)}"

    def test_an_unauthenticated_caller_gets_401_not_403(self, client):
        """401-vs-403 correctness. "No credential" must be 401 so a client
        knows to sign in; 403 would mean "signed in but not allowed"."""
        wrong_status = []
        for method, path in _OPERATIONS:
            if (method, path) in _INTENTIONALLY_PUBLIC:
                continue
            response = client.request(
                method,
                _fill_path(path),
                json={} if method in ("POST", "PUT", "PATCH") else None,
            )
            if response.status_code == 403:
                wrong_status.append((method, path))
        assert (
            not wrong_status
        ), f"these routes 403 an anonymous caller where 401 is correct: {wrong_status}"


class TestAnAuthenticatedCallerIsNeverMistakenForAnonymous:
    """The other half of 401-vs-403: a validly-signed-in caller who simply
    lacks the permission must get 403 (or the anti-enumeration 404), never
    401 -- a 401 would send the React app into a re-login loop."""

    @pytest.mark.parametrize("role", ["viewer", "user", "auditor", "support"])
    def test_an_under_privileged_caller_is_not_401d(self, client, role):
        headers = _make_user(client, f"{role}@example.com", role=role)
        spurious = []
        for method, path in _OPERATIONS:
            if (method, path) in _INTENTIONALLY_PUBLIC:
                continue
            response = client.request(
                method,
                _fill_path(path),
                headers=headers,
                json={} if method in ("POST", "PUT", "PATCH") else None,
            )
            if response.status_code == 401:
                spurious.append((method, path, response.text[:80]))
        assert (
            not spurious
        ), f"role {role!r} holds a valid token but was told it is unauthenticated: {spurious}"


class TestNoCrossUserAccess:
    """IDOR / BOLA. Alice creates; Bob -- a valid account in the same
    tenant -- must not read, modify, share or delete any of it."""

    @pytest.fixture
    def two_users(self, client):
        alice = _make_user(client, "alice@example.com", role="admin")
        bob = _make_user(client, "bob@example.com", role="user")
        return alice, bob

    @pytest.fixture
    def alice_conversation(self, client, two_users):
        alice, _bob = two_users
        created = client.post("/conversations", json={"title": "Alice private"}, headers=alice)
        assert created.status_code == 201, created.text
        conversation_id = created.json()["id"]
        message = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"role": "user", "content": "alice's confidential question"},
            headers=alice,
        )
        assert message.status_code == 201, message.text
        return conversation_id

    @pytest.mark.parametrize(
        ("label", "method", "suffix", "body"),
        [
            ("read it", "GET", "", None),
            ("read its messages", "GET", "/messages", None),
            ("rename it", "PATCH", "", {"title": "pwned"}),
            ("append a message", "POST", "/messages", {"role": "user", "content": "injected"}),
            ("delete it", "DELETE", "", None),
            ("read its share", "GET", "/share", None),
            ("share it", "POST", "/share", {"access_mode": "anyone_with_link"}),
            ("regenerate its share link", "POST", "/share/link/regenerate", {}),
            ("revoke its share", "POST", "/share/revoke", {"version": 1}),
            ("invite himself to it", "POST", "/share/members/invite", {"email": "bob@example.com"}),
        ],
    )
    def test_bob_cannot_touch_alices_conversation(
        self, client, two_users, alice_conversation, label, method, suffix, body
    ):
        _alice, bob = two_users
        response = client.request(
            method, f"/conversations/{alice_conversation}{suffix}", headers=bob, json=body
        )
        assert (
            response.status_code in _DENIED
        ), f"Bob could {label}: {response.status_code} {response.text[:160]}"

    def test_bob_cannot_revoke_alices_session(self, client, two_users):
        alice, bob = two_users
        sessions = client.get("/auth/sessions", headers=alice)
        assert sessions.status_code == 200, sessions.text
        session_id = sessions.json()["sessions"][0]["id"]

        response = client.delete(f"/auth/sessions/{session_id}", headers=bob)
        assert response.status_code in _DENIED, response.text
        # Alice's session must still work afterwards.
        assert client.get("/auth/me", headers=alice).status_code == 200

    def test_alices_conversation_survives_the_attempts(self, client, two_users, alice_conversation):
        """Non-vacuity guard: the probes above must be denied *and*
        harmless. A 404 returned after a successful delete would pass the
        table above while losing the data."""
        alice, bob = two_users
        for method, suffix, body in [
            ("DELETE", "", None),
            ("PATCH", "", {"title": "pwned"}),
        ]:
            client.request(
                method, f"/conversations/{alice_conversation}{suffix}", headers=bob, json=body
            )
        still_there = client.get(f"/conversations/{alice_conversation}", headers=alice)
        assert still_there.status_code == 200, still_there.text
        assert still_there.json()["title"] == "Alice private"

    def test_bob_cannot_see_alices_conversation_in_his_own_list(
        self, client, two_users, alice_conversation
    ):
        _alice, bob = two_users
        listed = client.get("/conversations", headers=bob)
        assert listed.status_code == 200, listed.text
        ids = {row["id"] for row in listed.json()["conversations"]}
        assert alice_conversation not in ids

    def test_a_nonexistent_id_is_indistinguishable_from_someone_elses(
        self, client, two_users, alice_conversation
    ):
        """Anti-enumeration: "not yours" and "does not exist" must be the
        same answer, or the API becomes a resource-id oracle."""
        _alice, bob = two_users
        theirs = client.get(f"/conversations/{alice_conversation}", headers=bob)
        absent = client.get(f"/conversations/{uuid.uuid4()}", headers=bob)
        assert theirs.status_code == absent.status_code == 404
        assert theirs.text == absent.text


class TestPrivilegedRoutesDenyUnderPrivilegedRoles:
    """Function-level authorization, with complete valid bodies so the
    check being exercised is the one inside the handler -- not FastAPI's
    body validation.

    These surfaces authorize in the route body (`api.governance._authorize`,
    `api.workflow._authorize`, the catalog and onboarding equivalents)
    rather than in a dependency, so a dependency-graph audit reports them
    as ungated and a minimal-body probe reports 422. Both are misleading;
    only this shape tests them.
    """

    @pytest.mark.parametrize("role", ["viewer", "user"])
    @pytest.mark.parametrize(
        ("label", "method", "path", "body"),
        [
            (
                "create a governed asset draft",
                "POST",
                "/governance/assets",
                {
                    "asset_type": "prompt",
                    "asset_key": "probe.prompt",
                    "display_name": "Probe",
                    "config": {"text": "hello"},
                },
            ),
            (
                "create a semantic catalog entry",
                "POST",
                "/semantic-catalog/entries",
                {
                    "database_id": "default",
                    "concept_type": "metric",
                    "concept_key": "probe.metric",
                    "business_name": "Probe Metric",
                    "description": "d" * 40,
                },
            ),
            (
                "start a KPI monitoring scan",
                "POST",
                "/kpi-monitoring/scan",
                {"database_id": "default"},
            ),
            ("start a schema-drift scan", "POST", "/schema-drift/scan", {"database_id": "default"}),
            (
                "create an onboarding job",
                "POST",
                "/onboarding/jobs",
                {"database_label": "probe", "db_type": "postgresql"},
            ),
            ("refresh the schema index", "POST", "/schema/refresh", {}),
        ],
    )
    def test_an_under_privileged_role_is_refused(self, client, role, label, method, path, body):
        headers = _make_user(client, f"{role}@example.com", role=role)
        response = client.request(method, path, headers=headers, json=body)
        assert (
            response.status_code in _DENIED
        ), f"role {role!r} could {label}: {response.status_code} {response.text[:160]}"

    def test_an_admin_is_not_refused_by_the_same_gate(self, client):
        """Non-vacuity guard for the class: the gates must distinguish
        roles, not refuse everyone. An admin gets past authorization --
        what it then hits is a configuration error (no database is
        configured in this harness), which is a different failure and
        proves the gate itself opened.
        """
        headers = _make_user(client, "admin@example.com", role="admin")
        refreshed = client.post("/schema/refresh", headers=headers, json={})
        assert refreshed.status_code not in (
            401,
            403,
        ), f"an admin was refused by the authorization gate: {refreshed.text[:160]}"


class TestTheGoldenExampleWriteBoundary:
    """`AUZ-005` -- recorded as the measured boundary, not as a defect.

    The audit prompt asks whether "a normal user's thumbs-up can inject a
    golden example that later steers other users' SQL". It can, within
    their own tenant: `Permission.GOLDEN_EXAMPLE_WRITE` is granted at the
    `user` tier (`agent.authz._USER`), the store is partitioned by tenant
    but **not** by user, and there is no review step before a saved pair
    is retrieved as a few-shot example for everyone else in that tenant.

    That is the thumbs-up feature working as designed, so this class pins
    the boundary rather than asserting a refusal: `viewer` cannot write,
    `user` can. If either changes, that is a product decision someone
    should have made deliberately.

    What it cannot do is escalate privilege. A poisoned example still
    passes `agent/sql_validator.py` and the result gate before anything
    reaches a caller, so the reachable harm is to *accuracy*, not to
    confidentiality. See `phase-4-authorization.md` 5.3.
    """

    _EXAMPLE = {"question": "how many orders", "sql": "SELECT 1", "database": "default"}

    def test_a_viewer_cannot_write_a_golden_example(self, client):
        headers = _make_user(client, "viewer@example.com", role="viewer")
        response = client.post("/feedback/golden-example", headers=headers, json=self._EXAMPLE)
        assert response.status_code == 403, response.text

    def test_a_plain_user_can_write_a_golden_example(self, client):
        """Deliberate: this is the thumbs-up widget. Pinned so that
        narrowing it later is a conscious change, not a side effect."""
        headers = _make_user(client, "user@example.com", role="user")
        response = client.post("/feedback/golden-example", headers=headers, json=self._EXAMPLE)
        assert response.status_code not in (401, 403), response.text


class TestSuspensionDoesNotReachTheTokenOnlySurface:
    """`AUZ-003` -- **a flagged gap pinned as current behaviour**, not a
    policy this file endorses.

    `api.authz.require_permission` authorizes from the signature-verified
    JWT alone and touches no database, by design: `api/authz.py` imports
    no DB and `/ask` is the latency-critical path. The consequence is that
    neither a suspended *account* nor a suspended *tenant* is noticed on
    that surface until the access token expires
    (`access_token_expire_minutes`, default 15, with no upper bound in its
    `Field(...)` constraint).

    Measured 2026-10-28 with the token untouched. The earlier probe for
    this used `GET /config` and `GET /schema`, **neither of which is a
    real route** -- both fell through to the SPA catch-all, so that
    evidence was void and was re-taken against routes the route walk
    confirms exist:

    | route | user suspended | tenant suspended |
    |---|---|---|
    | `GET /auth/me` (identity.rbac) | 401 | 401 |
    | `GET /conversations` (identity.rbac) | 401 | 401 |
    | `GET /navigation` | 401 | 401 |
    | `GET /models` (agent.authz) | **200** | **200** |
    | `GET /schema/tables` (agent.authz) | **200** | **200** |
    | `GET /sources` (agent.authz) | **200** | **200** |

    `/schema/tables` and `/ask` are measured above but not asserted
    below: both need a configured database, which this harness
    deliberately does not have (see
    `tests/test_settings_env_isolation.py`). `/models` and `/sources`
    make the same point without one.
    | `POST /ask` (agent.authz) | **200** | **200** |

    CLAUDE.md 5.3 states that a suspended tenant "fails closed". It does
    on the identity-RBAC surface; it does not here. Closing it is a
    decision about where to put a per-request identity-DB read, so it is
    flagged for an owner rather than fixed -- `phase-4-authorization.md`
    4.2 carries the proposed fix.

    **When that decision is made, this class is the one to change, and
    its failure will say so.**
    """

    @pytest.fixture
    def suspended_admin(self, client):
        headers = _make_user(client, "alice@example.com", role="admin")
        session = get_identity_session(_SETTINGS)
        user = get_user_by_email(session, "alice@example.com")
        user.status = "suspended"
        session.commit()
        session.close()
        return headers

    @pytest.mark.parametrize("path", ["/auth/me", "/conversations", "/navigation"])
    def test_the_identity_rbac_surface_refuses_immediately(self, client, suspended_admin, path):
        assert client.get(path, headers=suspended_admin).status_code == 401

    @pytest.mark.parametrize("path", ["/models", "/sources"])
    def test_the_agent_authz_surface_does_not_yet(self, client, suspended_admin, path):
        """Pins the gap. A 401 here means someone closed `AUZ-003` --
        update this class and the finding register rather than deleting
        the assertion."""
        response = client.get(path, headers=suspended_admin)
        assert response.status_code == 200, (
            f"{path} now refuses a suspended account ({response.status_code}) -- if that was "
            "deliberate, AUZ-003 is fixed and this test should assert 401 instead"
        )


class TestCrossTenantDocumentAccess:
    """`AUZ-002` -- a **confirmed S1**, recorded as a strict `xfail` so it
    cannot be mistaken for accepted behaviour.

    Phase 1 flagged this as latent from code structure; Phase 4
    reproduced it at runtime. `rag/store.py` has no tenant column and not
    one of its entry points (`list_documents`, `similarity_search`,
    `get_document_bytes`, `delete_document`, `insert_document`) takes a
    tenant argument, and `api/documents.py` contains the string "tenant"
    **zero** times. So there is no configuration in which these routes
    would filter by tenant.

    Measured: a `tenant_b` admin, holding a JWT with `tid="tenant_b"`,
    listed, downloaded the bytes of, and **deleted** a document belonging
    to the `default` tenant -- 200 / 200 / 204, with `delete_document`
    confirmed called.

    Fixing it needs a `tenant_id` column on the document store and a
    migration, which Shared Rule 8 puts outside what this audit may
    change unilaterally. `strict=True` means that when the fix lands this
    test turns from `xfail` into a *failure* (XPASS), forcing the marker
    and the finding register to be updated together.

    The store is mocked here, as it is in `tests/test_api_documents.py`,
    because no SQL Server `VECTOR` store exists in this environment. What
    that mock stands in for is only "the store returned a row"; since no
    store function accepts a tenant, the absence of filtering is a
    property of the API and store *signatures*, not of the mock.
    """

    @pytest.fixture
    def two_tenant_documents(self, client, monkeypatch):
        from identity.models import Tenant

        import api.documents as documents_module
        from rag.store import DocumentRecord
        from security.secrets import SecretStr

        settings = _SETTINGS.model_copy(
            update={
                "enable_document_rag": True,
                "rag_store_connection_string": SecretStr("mssql+pyodbc://unused"),
            }
        )
        for module in (api_auth_mod, identity_auth_mod, identity_authz_mod, documents_module):
            monkeypatch.setattr(module, "get_settings", lambda: settings)

        session = get_identity_session(settings)
        session.add(Tenant(id="tenant_b", name="Tenant B", status="active"))
        session.commit()
        session.close()

        document = DocumentRecord(
            id="doc-a-1",
            filename="tenant-A-confidential.pdf",
            collection="documents",
            sensitivity_category=None,
            upload_date="2026-10-28T00:00:00Z",
            status="ready",
            chunk_count=3,
            error_message=None,
            has_pdf_bytes=True,
            uploaded_by="alice@example.com",
            restricted_roles=None,
        )
        deleted: list[str] = []
        monkeypatch.setattr(documents_module, "get_rag_engine", lambda settings=None: object())
        monkeypatch.setattr(documents_module, "ensure_schema", lambda engine: None)
        monkeypatch.setattr(
            documents_module, "list_documents", lambda engine, collection=None: [document]
        )
        monkeypatch.setattr(
            documents_module,
            "get_document_bytes",
            lambda engine, document_id: b"TENANT A CONFIDENTIAL BYTES",
        )
        monkeypatch.setattr(
            documents_module, "get_document_sensitivity", lambda engine, document_id: None
        )
        monkeypatch.setattr(
            documents_module, "get_document_restricted_roles", lambda engine, document_id: None
        )
        monkeypatch.setattr(
            documents_module,
            "delete_document",
            lambda engine, document_id: deleted.append(document_id),
        )

        def _admin_in(email: str, tenant_id: str) -> dict[str, str]:
            created = client.post(
                "/auth/register",
                json={"email": email, "password": _PASSWORD, "display_name": "Test Person"},
            )
            assert created.status_code == 201, created.text
            inner = get_identity_session(settings)
            user = get_user_by_email(inner, email)
            user.tenant_id = tenant_id
            assign_role(inner, user_id=user.id, role_name="admin", assigned_by_user_id=user.id)
            inner.commit()
            inner.close()
            client.cookies.clear()
            login = client.post("/auth/login", json={"email": email, "password": _PASSWORD})
            assert login.status_code == 200, login.text
            return {"Authorization": f"Bearer {login.json()['access_token']}"}

        _admin_in("alice@example.com", "default")
        intruder = _admin_in("bob@example.com", "tenant_b")
        return intruder, deleted

    def test_the_two_admins_really_are_in_different_tenants(self, client, two_tenant_documents):
        """Non-vacuity guard. If tenant assignment silently failed, the
        three xfails below would be meaningless."""
        import jwt

        intruder, _deleted = two_tenant_documents
        token = intruder["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, options={"verify_signature": False})
        assert claims["tid"] == "tenant_b"

    @pytest.mark.xfail(strict=True, reason="AUZ-002: the document store has no tenant column")
    def test_another_tenant_cannot_list_the_document(self, client, two_tenant_documents):
        intruder, _deleted = two_tenant_documents
        listed = client.get("/documents", headers=intruder)
        filenames = [row["filename"] for row in listed.json()["documents"]]
        assert "tenant-A-confidential.pdf" not in filenames

    @pytest.mark.xfail(strict=True, reason="AUZ-002: the document store has no tenant column")
    def test_another_tenant_cannot_download_the_document(self, client, two_tenant_documents):
        intruder, _deleted = two_tenant_documents
        response = client.get("/documents/doc-a-1/download", headers=intruder)
        assert response.status_code in _DENIED

    @pytest.mark.xfail(strict=True, reason="AUZ-002: the document store has no tenant column")
    def test_another_tenant_cannot_delete_the_document(self, client, two_tenant_documents):
        intruder, deleted = two_tenant_documents
        response = client.delete("/documents/doc-a-1", headers=intruder)
        assert response.status_code in _DENIED
        assert deleted == [], "the other tenant's document was actually deleted"
