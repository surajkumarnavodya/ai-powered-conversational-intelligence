"""Enterprise BI integrations -- Prompt 48
(`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`): governed connectors to
Power BI/Looker (push) and Tableau/Excel (authenticated file/feed pull),
built on top of this platform's own governed SQL execution and semantic
catalog -- never a second query/export engine.

See `models.py`/`policy.py`/`metric_binding.py`/`rendering.py`/
`outbound_safety.py`/`push.py`/`export_cache.py`'s own docstrings for the
full design, and `identity/repositories/bi_integration.py` for the
persistence layer.
"""
