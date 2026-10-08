"""Unit tests for `schema_drift.snapshot_store` (Prompt 43) -- real file
I/O against a `tmp_path`-backed `chroma_persist_dir`, not mocked (this
module's entire job is that round trip)."""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings
from db.schema_introspection import ColumnInfo, ForeignKeyInfo, TableSchemaInfo
from schema_drift.snapshot_store import load_snapshot, save_snapshot
from security.secrets import SecretStr

_BASE = Settings(
    ollama_host="http://localhost:11434",
    ollama_model="llama3.1:8b",
    db_type="postgresql",
    db_host="db.example.com",
    db_port=5432,
    db_name="mydb",
    db_user="reader",
    db_password=SecretStr("secret"),
    db_odbc_driver="x",
    chroma_persist_dir=Path("/tmp/chroma"),
    chroma_collection_name="schema_ddl",
    embedding_model_name="all-MiniLM-L6-v2",
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
    cost_estimation_enabled=False,
    log_level="INFO",
    log_redaction_level="standard",
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(**{**_BASE.__dict__, "chroma_persist_dir": tmp_path})


def _tables() -> list[TableSchemaInfo]:
    return [
        TableSchemaInfo(
            table_name="FactSales",
            columns=(
                ColumnInfo(
                    "SalesAmount",
                    "decimal",
                    True,
                    False,
                    default="0",
                    is_computed=False,
                    is_identity=False,
                    length=None,
                    precision=18,
                    scale=2,
                ),
                ColumnInfo("ProductKey", "int", False, False),
            ),
            foreign_keys=(ForeignKeyInfo(("ProductKey",), "DimProduct", ("ProductKey",)),),
            ddl="CREATE TABLE FactSales (...)",
            is_view=False,
            row_count_estimate=12345,
            unique_constraints=(("ProductKey",),),
        ),
        TableSchemaInfo(
            table_name="VProductSummary",
            columns=(ColumnInfo("ProductKey", "int", False, False),),
            foreign_keys=(),
            ddl="CREATE VIEW VProductSummary (...)",
            is_view=True,
        ),
    ]


class TestRoundTrip:
    def test_loading_before_any_save_returns_none(self, tmp_path):
        assert load_snapshot("default", _settings(tmp_path)) is None

    def test_save_then_load_round_trips_every_field(self, tmp_path):
        settings = _settings(tmp_path)
        tables = _tables()
        save_snapshot("default", tables, settings)
        loaded = load_snapshot("default", settings)
        assert loaded == tables

    def test_two_databases_do_not_share_a_snapshot_file(self, tmp_path):
        settings = _settings(tmp_path)
        save_snapshot("db1", _tables(), settings)
        assert load_snapshot("db2", settings) is None
        assert load_snapshot("db1", settings) is not None

    def test_a_corrupt_snapshot_file_fails_open_as_none(self, tmp_path):
        settings = _settings(tmp_path)
        settings.chroma_persist_dir.mkdir(parents=True, exist_ok=True)
        path = settings.chroma_persist_dir / ".schema_drift_snapshot__default.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert load_snapshot("default", settings) is None

    def test_saving_again_overwrites_the_previous_snapshot(self, tmp_path):
        settings = _settings(tmp_path)
        save_snapshot("default", _tables(), settings)
        save_snapshot("default", [], settings)
        assert load_snapshot("default", settings) == []
