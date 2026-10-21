# Context map — which document owns which topic

This is the index for everything the root [`CLAUDE.md`](../../CLAUDE.md) does
not carry itself. The root file holds the rules that apply to **every** task;
this map says where to look for anything narrower, and which document wins
when two disagree.

**Only `CLAUDE.md` is loaded into a session automatically.** Every document
listed here is read on demand. That is the whole point of the split: the root
file went from ~394,000 characters to ~36,000 without losing any of the
material below — see
[`claude-context-migration-report.md`](claude-context-migration-report.md).

---

## 1. Read this before every task

| Source | Why |
|---|---|
| [`CLAUDE.md`](../../CLAUDE.md) §4 Core architecture rules | Breaking one is a regression even if tests pass |
| [`CLAUDE.md`](../../CLAUDE.md) §5 Mandatory security and privacy rules | Never weakened to simplify a change |
| [`CLAUDE.md`](../../CLAUDE.md) §6 Coding standards, §10 workflow, §11 definition of done | How work is expected to be done here |
| [`CLAUDE.md`](../../CLAUDE.md) §12 Never touch | Secrets, applied migrations, generated files, benchmark-pinned prompts |

Nothing else needs to be read speculatively. Read a document below when the
task actually touches its area.

---

## 2. Topic → document

### Architecture and code layout

| Topic | Document | Authoritative for |
|---|---|---|
| Where code lives, package boundaries and why each is drawn | [`repository-map.md`](repository-map.md) | package responsibilities |
| Cross-cutting design decisions and their invariants (graph/retry, schema and business-context retrieval, SQL validation, read-only layering, config, exceptions, caching, singletons, optional sources) | [`overview.md`](overview.md) | the decisions and invariants |
| Per-node graph walkthrough, mermaid diagram, failure-category → retry routing table | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | runtime flow |
| API surface and data contracts | [`../API.md`](../API.md) | request/response shapes |
| Every configuration setting | [`../CONFIGURATION.md`](../CONFIGURATION.md) | settings reference |
| Tool/MCP abstraction | [`../TOOLS.md`](../TOOLS.md) | the tool contract |
| Multi-source setup and use | [`../MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md) | configuring each source |
| Asynchronous question queue, per-user concurrency, deterministic source routing, per-task model pins, the guardrail façade and the grounding gate | [`../CONCURRENCY.md`](../CONCURRENCY.md) | concurrency limits and behaviour, routing rules, model fallback, guardrail configuration, hallucination controls, scaling guidance |
| Per-capability implementation **status**, file evidence and priority | [`feature-inventory.md`](feature-inventory.md) | what state a capability is actually in |
| Source-type **capability/auth/ACL comparison**, including unimplemented sources | [`data-source-matrix.md`](data-source-matrix.md) | per-source-type capabilities, auth model and verification status |
| Source-connectivity batch plan, prerequisites, rollback | [`implementation-roadmap.md`](implementation-roadmap.md) | sequencing of the connector programme |
| The `sources/` contract, `GET /sources`, and the descriptor refactor | [`source-connector-contract.md`](source-connector-contract.md) | the connector contract's design and invariants |
| Domain analytics skills: what a skill is, its boundaries, and how the next batch is added | [`domain-skills.md`](domain-skills.md) | the domain-skill contract |

### Features — invariants and known limitations

| Topic | Document |
|---|---|
| Analytics, anomaly/root cause, forecasting, visualization, scenario, optimization, predictive, data quality, lineage, schema drift, KPI monitoring, briefing, decision center, recommendations, governed metrics, Query Store | [`features-analytics.md`](features-analytics.md) |
| Identity, authn/authz, multi-tenancy, chat history, sharing, onboarding, semantic catalog, governance center, evaluation platform, workflow, BI integrations, model routing, admin dashboards, the multi-agent/analyst layers, platform security and readiness passes | [`features-governance-and-admin.md`](features-governance-and-admin.md) |
| The React dashboard: UI redesign, role-based navigation, chat-workspace panels, analytics panels, artifact workspace, follow-up chips, execution timeline | [`features-frontend.md`](features-frontend.md) |

Each entry states what the feature does, the invariants that must not
regress, and the disclosed limitations. **For one feature's full design, read
its own contract (§3).**

### Security and privacy

| Topic | Document | Authoritative for |
|---|---|---|
| Overall security posture and controls | [`../../SECURITY.md`](../../SECURITY.md) | the posture |
| Threat model | [`../security/THREAT_MODEL.md`](../security/THREAT_MODEL.md), [`../THREAT_MODEL.md`](../THREAT_MODEL.md) | threats and mitigations |
| **Domain privacy, prohibited outputs, 18-domain policy matrix, classification matrix** | [`../DATA_PRIVACY_GOVERNANCE_ENGINE.md`](../DATA_PRIVACY_GOVERNANCE_ENGINE.md) | **domain safety — read before changing what may be asked, returned or recommended** |
| Authentication (4 modes, local accounts, Google sign-in) | [`../AUTHENTICATION.md`](../AUTHENTICATION.md) | auth behaviour |
| Resource → permission mapping | [`../AUTHORIZATION.md`](../AUTHORIZATION.md) | who may do what |
| Tenant isolation rules and limitations | [`../MULTI_TENANCY.md`](../MULTI_TENANCY.md) | tenancy |
| Data governance and classification policy | [`../GOVERNANCE.md`](../GOVERNANCE.md) | classification policy |
| Prompt-injection benchmark results per category | [`../security/PROMPT_INJECTION_BENCHMARK_GAP_REPORT.md`](../security/PROMPT_INJECTION_BENCHMARK_GAP_REPORT.md) | live benchmark results |
| CVE triage and dependency decisions | [`../security/CVE_TRIAGE.md`](../security/CVE_TRIAGE.md), [`../DEPENDENCY_SECURITY.md`](../DEPENDENCY_SECURITY.md) | vulnerability posture |
| Dated audit trail of security changes | [`../security-changelog.md`](../security-changelog.md) | change history |
| File-upload security | [`../FILE_UPLOAD_SECURITY.md`](../FILE_UPLOAD_SECURITY.md), [`../FILE_UPLOAD_THREAT_MODEL.md`](../FILE_UPLOAD_THREAT_MODEL.md) | upload controls |
| Conversation sharing security | [`../SHARING_SECURITY.md`](../SHARING_SECURITY.md) | share tokens and projection |
| Responsible-AI disclosures | [`../RESPONSIBLE_AI.md`](../RESPONSIBLE_AI.md) | disclosed model limits |
| Incident response | [`../security/INCIDENT_RESPONSE.md`](../security/INCIDENT_RESPONSE.md) | process |

### Operations, evaluation and readiness

| Topic | Document |
|---|---|
| **Current** production-readiness verdict and gate matrix | [`../PRODUCTION_READINESS_RELEASE_GATE.md`](../PRODUCTION_READINESS_RELEASE_GATE.md) |
| Deployment, backup/recovery, rollback | [`../DEPLOYMENT.md`](../DEPLOYMENT.md) |
| Observability, correlation ids, `[trace]` lines, tenant propagation, per-model token usage | [`../OBSERVABILITY.md`](../OBSERVABILITY.md) |
| Release history and what changed when | [`../../CHANGELOG.md`](../../CHANGELOG.md) (the README's "News and Updates" shows only the three newest) |
| Text-to-SQL accuracy numbers (dated — see §5) | [`../EVALUATION_CURRENT.md`](../EVALUATION_CURRENT.md), [`../EVALUATION.md`](../EVALUATION.md) |
| Performance baselines and why further backend tuning is unjustified | [`../PERFORMANCE_BASELINE.md`](../PERFORMANCE_BASELINE.md), [`../PERFORMANCE_RESULTS.md`](../PERFORMANCE_RESULTS.md) |
| Load/scale baseline and honest capacity statement | [`../SCALE_BASELINE.md`](../SCALE_BASELINE.md) |
| Risk register | [`../RISK_REGISTER.md`](../RISK_REGISTER.md) |
| Reliability | [`../RELIABILITY.md`](../RELIABILITY.md) |
| Troubleshooting | [`../TROUBLESHOOTING.md`](../TROUBLESHOOTING.md) |
| Opt-in live integration tests and the M365 tenant runbook | [`../LIVE_INTEGRATION_TESTING.md`](../LIVE_INTEGRATION_TESTING.md) |

### Frontend specifics

| Topic | Document |
|---|---|
| Design tokens and shared UI primitives | [`../ui-design-system.md`](../ui-design-system.md) |
| Conversation reading width, responsive breakpoints, device verification | [`../ui-responsive-layout.md`](../ui-responsive-layout.md) |
| One-action-one-owner table for every shell control | [`../navigation-and-actions.md`](../navigation-and-actions.md) |
| Chat history UI, search components | [`../chat-history-ui.md`](../chat-history-ui.md), [`../chat-history-search.md`](../chat-history-search.md) |
| Chat-history data model and what a reload does not restore | [`../chat-history-architecture.md`](../chat-history-architecture.md) |
| Conversational-intelligence chat workspace (panels, lifecycle rail, evidence, artifacts) | [`../conversational-intelligence-ui.md`](../conversational-intelligence-ui.md) |
| Image editing and attachment flows | [`../image-editing-architecture.md`](../image-editing-architecture.md), [`../image-attachment-flow.md`](../image-attachment-flow.md) |
| Vector-retrieval design and its disclosed gaps | [`../vector-retrieval-design.md`](../vector-retrieval-design.md) |
| Accessibility: static lint, runtime axe checks, and the contrast gap | [`features-frontend.md`](features-frontend.md) |
| Generated-SQL structural description in the query-metadata grid | [`features-frontend.md`](features-frontend.md), [`../API.md`](../API.md) |

---

## 3. The numbered contracts

Each numbered prompt produced one contract at the repository root. **A
feature's contract is authoritative for that feature** — it holds the
inspection findings, the design, the full testing summary, and the
security/tenant-isolation/performance review.

| # | Feature | Contract |
|---|---|---|
| 00 | Master implementation contract — the rules all numbered prompts followed | [`00_MASTER_IMPLEMENTATION_CONTRACT.md`](../../00_MASTER_IMPLEMENTATION_CONTRACT.md) |
| 01 | Baseline architecture and reuse inventory | [`01_BASELINE_ARCHITECTURE_AND_REUSE_INVENTORY.md`](../../01_BASELINE_ARCHITECTURE_AND_REUSE_INVENTORY.md) |
| 02 | Target architecture and incremental migration | [`02_TARGET_ARCHITECTURE.md`](../../02_TARGET_ARCHITECTURE.md) |
| 03 | Generic database adapter core | [`03_DATABASE_ADAPTER_CONTRACT.md`](../../03_DATABASE_ADAPTER_CONTRACT.md) |
| 04 | SQL Server adapter | [`04_SQL_SERVER_ADAPTER_CONTRACT.md`](../../04_SQL_SERVER_ADAPTER_CONTRACT.md) |
| 05 | SQL Server dialect validation | [`05_SQL_SERVER_DIALECT_VALIDATION_CONTRACT.md`](../../05_SQL_SERVER_DIALECT_VALIDATION_CONTRACT.md) |
| 06 | Database discovery (views, column metadata, incremental indexing) | [`06_DATABASE_DISCOVERY_CONTRACT.md`](../../06_DATABASE_DISCOVERY_CONTRACT.md) |
| 07 | Relationship intelligence (inferred candidate FKs) | [`07_RELATIONSHIP_INTELLIGENCE_CONTRACT.md`](../../07_RELATIONSHIP_INTELLIGENCE_CONTRACT.md) |
| 08 | Client-database onboarding engine | [`08_ONBOARDING_ENGINE_CONTRACT.md`](../../08_ONBOARDING_ENGINE_CONTRACT.md) |
| 09 | Tenant-aware semantic catalog | [`09_SEMANTIC_CATALOG_CONTRACT.md`](../../09_SEMANTIC_CATALOG_CONTRACT.md) |
| 10 | Governed metrics, dimensions and semantic contracts | [`10_GOVERNED_METRICS_CONTRACT.md`](../../10_GOVERNED_METRICS_CONTRACT.md) |
| 11 | Analytical intent classification | [`11_ANALYTICAL_INTENT_CONTRACT.md`](../../11_ANALYTICAL_INTENT_CONTRACT.md) |
| 12 | Analytical planning layer | [`12_ANALYTICAL_PLANNING_CONTRACT.md`](../../12_ANALYTICAL_PLANNING_CONTRACT.md) |
| 13 | Deterministic analytical result engine | [`13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md`](../../13_ANALYTICAL_RESULT_ENGINE_CONTRACT.md) |
| 14 | Anomaly detection and evidence-based root cause | [`14_ANOMALY_ROOT_CAUSE_CONTRACT.md`](../../14_ANOMALY_ROOT_CAUSE_CONTRACT.md) |
| 15 | Visualization and analytical presentation engine | [`15_VISUALIZATION_ENGINE_CONTRACT.md`](../../15_VISUALIZATION_ENGINE_CONTRACT.md) |
| 16 | Deterministic forecasting | [`16_FORECASTING_CONTRACT.md`](../../16_FORECASTING_CONTRACT.md) |
| 17 | Evidence-first recommendation engine | [`17_RECOMMENDATION_ENGINE_CONTRACT.md`](../../17_RECOMMENDATION_ENGINE_CONTRACT.md) |
| 18 | Recommendation governance and feedback | [`18_RECOMMENDATION_GOVERNANCE_CONTRACT.md`](../../18_RECOMMENDATION_GOVERNANCE_CONTRACT.md) |
| 19 | SQL Server Query Store performance intelligence | [`19_QUERY_STORE_PERFORMANCE_CONTRACT.md`](../../19_QUERY_STORE_PERFORMANCE_CONTRACT.md) |
| 20 | Multi-tenant enterprise architecture | [`20_MULTI_TENANT_ENTERPRISE_ARCHITECTURE_CONTRACT.md`](../../20_MULTI_TENANT_ENTERPRISE_ARCHITECTURE_CONTRACT.md) |
| 21 | Enterprise security and data governance hardening | [`21_ENTERPRISE_SECURITY_DATA_GOVERNANCE_CONTRACT.md`](../../21_ENTERPRISE_SECURITY_DATA_GOVERNANCE_CONTRACT.md) |
| 23 | Observability, evaluation and reliability | [`23_OBSERVABILITY_EVALUATION_RELIABILITY_CONTRACT.md`](../../23_OBSERVABILITY_EVALUATION_RELIABILITY_CONTRACT.md) |
| 24 | Full integration and regression | [`24_FULL_INTEGRATION_REGRESSION_CONTRACT.md`](../../24_FULL_INTEGRATION_REGRESSION_CONTRACT.md) |
| 25 | Production readiness and release gate | [`25_PRODUCTION_READINESS_RELEASE_GATE_CONTRACT.md`](../../25_PRODUCTION_READINESS_RELEASE_GATE_CONTRACT.md) |
| 26 | Database onboarding portal (UI) | [`26_DATABASE_ONBOARDING_PORTAL_CONTRACT.md`](../../26_DATABASE_ONBOARDING_PORTAL_CONTRACT.md) |
| 27 | SME semantic review dashboard | [`27_SME_SEMANTIC_REVIEW_DASHBOARD_CONTRACT.md`](../../27_SME_SEMANTIC_REVIEW_DASHBOARD_CONTRACT.md) |
| 28 | Global platform admin dashboard | [`28_GLOBAL_PLATFORM_ADMIN_DASHBOARD_CONTRACT.md`](../../28_GLOBAL_PLATFORM_ADMIN_DASHBOARD_CONTRACT.md) |
| 29 | Client/tenant admin dashboard | [`29_TENANT_ADMIN_DASHBOARD_CONTRACT.md`](../../29_TENANT_ADMIN_DASHBOARD_CONTRACT.md) |
| 30 | Analytics and insights panels in the answered turn | [`30_ANALYTICS_INSIGHTS_DASHBOARD_CONTRACT.md`](../../30_ANALYTICS_INSIGHTS_DASHBOARD_CONTRACT.md) |
| 31 | Recommendation and action dashboard | [`31_RECOMMENDATION_ACTION_DASHBOARD_CONTRACT.md`](../../31_RECOMMENDATION_ACTION_DASHBOARD_CONTRACT.md) |
| 32 | Role-based navigation and server-driven access | [`32_ROLE_BASED_NAVIGATION_CONTRACT.md`](../../32_ROLE_BASED_NAVIGATION_CONTRACT.md) |
| 33 | AI data analyst agent | [`33_AI_DATA_ANALYST_AGENT_CONTRACT.md`](../../33_AI_DATA_ANALYST_AGENT_CONTRACT.md) |
| 34 | Multi-agent supervisor | [`34_MULTI_AGENT_SUPERVISOR_CONTRACT.md`](../../34_MULTI_AGENT_SUPERVISOR_CONTRACT.md) |
| 35 | Advanced semantic intelligence | [`35_SEMANTIC_INTELLIGENCE_CONTRACT.md`](../../35_SEMANTIC_INTELLIGENCE_CONTRACT.md) |
| 37 | Advanced hierarchical root-cause engine | [`37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md`](../../37_ADVANCED_ROOT_CAUSE_ENGINE_CONTRACT.md) |
| 38 | Governed what-if scenario engine | [`38_WHATIF_SCENARIO_ENGINE_CONTRACT.md`](../../38_WHATIF_SCENARIO_ENGINE_CONTRACT.md) |
| 39 | Governed optimization engine | [`39_OPTIMIZATION_ENGINE_CONTRACT.md`](../../39_OPTIMIZATION_ENGINE_CONTRACT.md) |
| 40 | Governed predictive analytics engine | [`40_PREDICTIVE_ANALYTICS_CONTRACT.md`](../../40_PREDICTIVE_ANALYTICS_CONTRACT.md) |
| 41 | Data quality command center | [`41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md`](../../41_DATA_QUALITY_COMMAND_CENTER_CONTRACT.md) |
| 42 | Data lineage platform | [`42_DATA_LINEAGE_PLATFORM_CONTRACT.md`](../../42_DATA_LINEAGE_PLATFORM_CONTRACT.md) |
| 43 | Schema drift impact analysis | [`43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md`](../../43_SCHEMA_DRIFT_IMPACT_ANALYSIS_CONTRACT.md) |
| 44 | AI governance center | [`44_AI_GOVERNANCE_CENTER_CONTRACT.md`](../../44_AI_GOVERNANCE_CENTER_CONTRACT.md) |
| 45 | Prompt/agent/policy registry | [`45_PROMPT_AGENT_REGISTRY_CONTRACT.md`](../../45_PROMPT_AGENT_REGISTRY_CONTRACT.md) |
| 46 | AI evaluation benchmarking platform | [`46_AI_EVALUATION_BENCHMARKING_CONTRACT.md`](../../46_AI_EVALUATION_BENCHMARKING_CONTRACT.md) |
| 47 | Governed model routing and cost optimization | [`47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`](../../47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md) |
| 48 | Enterprise BI integrations | [`48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md`](../../48_ENTERPRISE_BI_INTEGRATIONS_CONTRACT.md) |
| 49 | Enterprise workflow automation | [`49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md`](../../49_ENTERPRISE_WORKFLOW_AUTOMATION_CONTRACT.md) |
| 50 | Proactive AI analyst (KPI monitoring) | [`50_PROACTIVE_AI_ANALYST_CONTRACT.md`](../../50_PROACTIVE_AI_ANALYST_CONTRACT.md) |
| 51 | Executive AI briefing | [`51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`](../../51_EXECUTIVE_AI_BRIEFING_CONTRACT.md) |
| 52 | Executive decision center | [`52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`](../../52_EXECUTIVE_DECISION_CENTER_CONTRACT.md) |

There is no contract numbered 22 or 36 — 22 (result cache and per-database
execution limiter) is recorded in [`overview.md`](overview.md), and 36 was
never issued. The analysis-artifact workspace and the conversational-intelligence
UI series are recorded in
[`../conversational-intelligence-ui.md`](../conversational-intelligence-ui.md)
rather than as numbered contracts.

---

## 4. Resolving a conflict

When two sources disagree, prefer in this order:

1. **The code**, plus the test that pins the behaviour. A test asserting a
   property is the strongest statement of intent in this repo.
2. **The root `CLAUDE.md`** for a cross-cutting rule (architecture, security,
   privacy, conventions). It is the deliberate, current distillation.
3. **The feature's own `NN_*_CONTRACT.md`** for that feature's design.
4. **The reference documents in this directory** for invariants and
   limitations.
5. **Dated reports** — the most recent dated document wins, and several older
   ones carry an explicit "superseded by" pointer. Where they conflict with
   the current gate, [`../PRODUCTION_READINESS_RELEASE_GATE.md`](../PRODUCTION_READINESS_RELEASE_GATE.md)
   supersedes the repo-root `SECURITY_*.md` files and the 2026-09 readiness
   reports.

If a genuine contradiction remains, **say so in your report** rather than
picking silently. Several were resolved that way already, and the reasoning
was recorded.

---

## 5. Documents that are snapshots, not current state

These are kept as evidence and must not be read as current:

| Document | Status |
|---|---|
| [`../PRODUCTION_READINESS_REPORT.md`](../PRODUCTION_READINESS_REPORT.md) | 2026-09-01 snapshot; predates the entire auth system. Carries a "superseded by" pointer |
| [`../security/PRODUCTION_SECURITY_READINESS_REPORT.md`](../security/PRODUCTION_SECURITY_READINESS_REPORT.md) | 2026-09-18 snapshot; predates prompts 19-24 |
| [`../EVALUATION_CURRENT.md`](../EVALUATION_CURRENT.md) | Real live numbers, but dated 2026-09-16 — many pipeline changes ago. Flagged as a P1 in the current gate |
| [`../PHASE1_*.md`](../), [`../PHASE2_*.md`](../), `SECURITY_*.md` at the repo root | Historical pass reports |
| [`../SCALE_OUT_PROMPT.md`](../SCALE_OUT_PROMPT.md) | The committed 11-phase program; only Phase 0 is done |
| [`../frontend-ui-audit.md`](../frontend-ui-audit.md), [`../functional-ui-audit.md`](../functional-ui-audit.md) | The audits that drove the UI passes |

---

## 6. Keeping this map honest

- A new feature adds its contract to §3 and its invariants to the matching
  `features-*.md` — **not** to the root `CLAUDE.md`, unless it changes a
  cross-cutting rule.
- A new cross-cutting rule goes in the root `CLAUDE.md` §4 or §5, stated once.
- If the root file grows past roughly 60,000 characters again, the growth is
  almost certainly per-feature detail that belongs in a `features-*.md`.
  `python scripts/check_instruction_size.py` reports the current sizes.
