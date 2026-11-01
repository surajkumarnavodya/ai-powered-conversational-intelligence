"""The Chroma client must be constructed exactly once per process, even
when several request threads reach it simultaneously on a cold cache.

`embeddings.schema_indexer._cached_chroma_client`'s own docstring records
why this matters: more than one `chromadb.PersistentClient` against the
*same* on-disk directory in one process corrupts Chroma's HNSW segment
reader, and `embeddings.retriever.select_database` swallows that
per-database by design, so the symptom is **silent misrouting to the
wrong database** rather than a visible error.

`functools.cache` does not serialise construction: concurrent callers
that all miss a cold cache each run the wrapped function. The `/ask`
path reaches `get_chroma_client` from `embeddings.retriever
.select_database` and `.retrieve_relevant_schema`, which run on
`api.main._get_ask_executor`'s bounded thread pool -- real threads, so
the race is reachable with two simultaneous first questions, and
`api.main.lifespan` does not pre-warm this client the way it pre-warms
the engines, the Ollama client and the compiled graphs.

These tests use real threads deliberately (CLAUDE.md 7): a sequential
test passes even with the bug present.
`test_the_lock_is_what_prevents_the_duplicate` is the non-vacuity proof
-- it drives the same concurrency at the *unlocked* builder and shows it
does duplicate, so the two tests above cannot pass for some unrelated
reason.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from embeddings import schema_indexer

_THREADS = 8

#: Long enough that every spawned thread is inside the constructor at
#: once when nothing serialises them, short enough to keep the suite
#: fast. Without it a trivially fast constructor can finish before the
#: next thread starts and hide the race.
_CONSTRUCTION_WINDOW_SECONDS = 0.05


@pytest.fixture
def construction_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Replaces `chromadb.PersistentClient` with a counting stand-in."""
    calls: list[str] = []
    lock = threading.Lock()

    class _FakeClient:
        def __init__(self, path: str) -> None:
            with lock:
                calls.append(path)
            time.sleep(_CONSTRUCTION_WINDOW_SECONDS)

    monkeypatch.setattr(schema_indexer.chromadb, "PersistentClient", _FakeClient)
    schema_indexer.clear_chroma_client_cache()
    yield calls
    schema_indexer.clear_chroma_client_cache()


def _call_concurrently(target: Callable[[str], object], persist_dir: str) -> list[object]:
    """Runs `target(persist_dir)` on `_THREADS` real threads; returns results."""
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            value = target(persist_dir)
        except BaseException as exc:  # pragma: no cover - surfaced by the caller
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(_THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"worker raised: {errors!r}"
    assert not any(thread.is_alive() for thread in threads), "a worker thread did not finish"
    return results


def test_concurrent_cold_cache_constructs_one_client_only(
    construction_log: list[str], tmp_path: Path
) -> None:
    """Eight threads, cold cache, one directory -> exactly one client."""
    persist_dir = str(tmp_path / "chroma")

    _call_concurrently(schema_indexer._cached_chroma_client, persist_dir)

    assert construction_log == [persist_dir], (
        "chromadb.PersistentClient was constructed "
        f"{len(construction_log)} time(s) for one directory; more than one "
        "client against the same directory corrupts Chroma's HNSW segment "
        "reader and silently misroutes database selection"
    )


def test_concurrent_callers_all_receive_the_same_instance(
    construction_log: list[str], tmp_path: Path
) -> None:
    """Every concurrent caller must get the *same* object back.

    Construction happening once is not sufficient on its own -- a caller
    handed a second, uncached instance would still hold a client whose
    segment reader conflicts with the cached one.
    """
    persist_dir = str(tmp_path / "chroma")

    clients = _call_concurrently(schema_indexer._cached_chroma_client, persist_dir)

    assert len(clients) == _THREADS
    assert (
        len({id(client) for client in clients}) == 1
    ), "concurrent callers received different client instances"


def test_the_lock_is_what_prevents_the_duplicate(
    construction_log: list[str], tmp_path: Path
) -> None:
    """Non-vacuity proof: the unlocked builder *does* duplicate.

    `_build_chroma_client` carries the same `functools.cache` the public
    helper used to carry on its own. Driving it directly -- i.e. without
    `_CHROMA_CLIENT_LOCK` -- reproduces the original defect, so the two
    tests above are demonstrably passing because of the lock and not
    because the threads failed to overlap.
    """
    persist_dir = str(tmp_path / "chroma")

    _call_concurrently(schema_indexer._build_chroma_client, persist_dir)

    assert len(construction_log) > 1, (
        "the unlocked builder constructed only once, so these tests are not "
        "actually exercising concurrent cold-cache access and the locked "
        "tests above prove nothing"
    )


def test_distinct_directories_still_get_distinct_clients(
    construction_log: list[str], tmp_path: Path
) -> None:
    """The lock must not collapse two different persist directories into one."""
    first = schema_indexer._cached_chroma_client(str(tmp_path / "a"))
    second = schema_indexer._cached_chroma_client(str(tmp_path / "b"))

    assert first is not second
    assert construction_log == [str(tmp_path / "a"), str(tmp_path / "b")]


def test_clearing_the_cache_lets_the_next_call_rebuild(
    construction_log: list[str], tmp_path: Path
) -> None:
    """`tests/conftest.py` clears this cache between tests.

    Without a working clear, a test that monkeypatches Chroma silently
    keeps whatever the previous test cached.
    """
    persist_dir = str(tmp_path / "chroma")

    first = schema_indexer._cached_chroma_client(persist_dir)
    schema_indexer.clear_chroma_client_cache()
    second = schema_indexer._cached_chroma_client(persist_dir)

    assert first is not second
    assert construction_log == [persist_dir, persist_dir]
