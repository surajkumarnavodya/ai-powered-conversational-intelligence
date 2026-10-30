"""`docs/API.md` must name every field of the four request/response models a
programmatic caller actually builds against, and must not claim an endpoint
the application does not expose.

**Why this file exists.** UAT-readiness Phase 10 (`DOC-021`, 2026-10-30)
diffed the document against `app.openapi()` and `api/schemas.py`:

* **26 of `AskResponse`'s 53 fields** were not named anywhere in it,
  `synthesized_answer` among them. That is the field carrying the prose
  answer for every non-SQL route — a web, document, policy or no-match
  reply — while the documented `insight` is the SQL path's one-sentence
  summary and is `None` there. A client written from this page alone
  renders those answers blank, which looks like a server fault and is not
  one.
* 6 of `ExecuteResponse`'s 14 were missing, including `normalized_sql`:
  the statement that *actually ran*, which is not necessarily the text
  the caller submitted.

`api/schemas.py`'s response models are frozen and widely consumed
(`CLAUDE.md` §6.3), so a new field is additive — which is exactly how a
field reaches production undocumented. This test makes adding one
without a line in `docs/API.md` fail.

**Scope is deliberately these four models.** `api/schemas.py` holds 79;
pinning all of them would turn every admin-surface field into a
documentation chore for a page that says plainly it describes 82 of 212
operations. These four are the public programmatic contract the README's
`curl` examples and `docs/API.md`'s own sections are written against.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from api.schemas import AskRequest, AskResponse, ExecuteRequest, ExecuteResponse

_API_DOC = Path(__file__).resolve().parent.parent / "docs" / "API.md"

_PINNED_MODELS: dict[str, type[BaseModel]] = {
    "AskRequest": AskRequest,
    "AskResponse": AskResponse,
    "ExecuteRequest": ExecuteRequest,
    "ExecuteResponse": ExecuteResponse,
}


@pytest.fixture(scope="module")
def api_doc() -> str:
    return _API_DOC.read_text(encoding="utf-8")


def _mentions(text: str, name: str) -> bool:
    return re.search(r"\b" + re.escape(name) + r"\b", text) is not None


class TestEveryPublicContractFieldIsDocumented:
    @pytest.mark.parametrize("model_name", sorted(_PINNED_MODELS))
    def test_no_field_is_absent_from_the_document(
        self, model_name: str, api_doc: str
    ) -> None:
        model = _PINNED_MODELS[model_name]
        missing = sorted(f for f in model.model_fields if not _mentions(api_doc, f))
        assert not missing, (
            f"{model_name} field(s) named nowhere in docs/API.md: {missing}. "
            f"Add them -- a grouped table row is enough. A frozen response "
            f"model's new field is additive, which is how one reaches "
            f"production undocumented (CLAUDE.md 6.3)."
        )

    def test_the_field_that_made_this_worth_pinning(self, api_doc: str) -> None:
        """`synthesized_answer` is where the prose answer lives for every
        non-SQL route; `insight` is the SQL path's summary and is None there.
        Documenting the first and not the second is the trap."""
        assert "synthesized_answer" in AskResponse.model_fields
        assert _mentions(api_doc, "synthesized_answer")
        # and the document must say which is which, not merely name both.
        # Matched across the line wrap, since this is prose.
        assert re.search(r"`insight`\s+is not", api_doc), (
            "docs/API.md must say that `insight` is NOT the prose answer "
            "field, not merely mention both names"
        )


class TestTheDocumentDoesNotClaimEndpointsThatDoNotExist:
    """A `METHOD /path` in prose that the app does not serve sends a caller
    somewhere real routes are not. Checked against the live schema, with the
    two deliberately unlisted routes allowed."""

    #: Served with `include_in_schema=False`, so absent from `/openapi.json`
    #: but genuinely reachable. `/live` is the liveness probe; the catch-all
    #: serves `frontend/dist` and is why a 200 from this app is never on its
    #: own evidence that a route exists.
    HIDDEN_BUT_REAL = {
        ("GET", "/live"),
        ("GET", "/{}"),
        # FastAPI's own built-ins, served but absent from the schema they
        # describe.
        ("GET", "/openapi.json"),
        ("GET", "/docs"),
        ("GET", "/redoc"),
    }

    @staticmethod
    def _normalise(path: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", path).rstrip("/") or "/"

    def test_every_documented_endpoint_is_served(self, api_doc: str) -> None:
        from api.main import app

        real = {
            (method.upper(), self._normalise(path))
            for path, ops in app.openapi()["paths"].items()
            for method in ops
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        } | self.HIDDEN_BUT_REAL

        # Prose writes shorthand such as "GET/POST/DELETE /documents" for
        # "/documents/{id}", so a documented collection path whose
        # parameterised sibling exists counts as served.
        collections = {(m, p.rsplit("/{}", 1)[0]) for m, p in real if p.endswith("/{}")}

        documented = {
            (m, self._normalise(p.rstrip(".,;:)`")))
            for m, p in re.findall(
                r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_\-/{}.:]*)", api_doc
            )
        }
        # A prefix of a real parameterised path, e.g. "POST /governance/assets/{}"
        # standing in for ".../{id}/submit-for-testing".
        prefixes = {(m, p) for m, p in real}

        def served(entry: tuple[str, str]) -> bool:
            m, p = entry
            if entry in real or entry in collections:
                return True
            return any(rm == m and rp.startswith(p + "/") for rm, rp in prefixes)

        phantom = sorted(e for e in documented if not served(e))
        assert not phantom, (
            f"docs/API.md documents endpoint(s) the application does not "
            f"serve: {phantom}"
        )

    def test_the_index_counts_match_the_live_schema(self, api_doc: str) -> None:
        """The index's header states 187 paths / 212 operations. If the app
        grows a route, the number in the prose is wrong and must be
        regenerated rather than left to rot -- the whole point of Phase 10."""
        from api.main import app

        spec = app.openapi()
        paths = len(spec["paths"])
        operations = sum(
            1
            for ops in spec["paths"].values()
            for m in ops
            if m.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        )
        assert f"**{paths} paths / {operations} operations**" in api_doc, (
            f"docs/API.md's coverage note must state the live figures "
            f"({paths} paths / {operations} operations). Regenerate the "
            f"Full endpoint index too -- both are measured, not prose."
        )
