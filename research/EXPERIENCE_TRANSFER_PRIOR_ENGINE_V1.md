# Experience Transfer / Prior Engine V1

## Current architecture audit

- `CURRENT_MEMORY_MODEL`: `AdsMemory` plus `AdsMemoryEvidence`; current retrieval is PRIVATE/channel or promotion scoped.
- `CURRENT_MEMORY_EVIDENCE_MODEL`: immutable snapshot/evidence references and closed-loop outcomes; technical canaries are not business evidence.
- `CURRENT_CONTEXT_AVAILABLE`: provider, destination/object type, campaign strategy/goal, budget/CPA bands, attribution/data trust, sample and experiment policy fields in `AdsAnalysisSnapshot.facts_json`.
- `CURRENT_DECISION_INPUTS`: current provider metrics, exact business outcomes, freshness, attribution coverage, action capability and existing guardrails.
- `CURRENT_CROSS_CLIENT_CAPABILITY`: absent; no prior is eligible without verified tenant scope.
- `CURRENT_EXTERNAL_DATA_CAPABILITY`: absent; no external data is persisted in PostgreSQL.

## V1 design

`build_context_signature()` creates a canonical, hashed `experiment-context-v1` signature. `similarity()` is weighted and deterministic; unknown fields are explicit and never treated as equal. `aggregate_evidence()` deduplicates by experiment, excludes `TECHNICAL`, and counts independent contexts/clients/cohorts. Promotion is LOCAL until the configured cohort/global thresholds are met (5/3/2 for candidate, 10/5 for cohort, 30/10/3 for global). `rank_priors()` is tenant-safe, deterministic, decayed (180-day half-life), and read-only. `replay()` is an offline/shadow evaluator.

Local evidence is preferred over broader scope. Priors can only rank/risk-adjust candidates; they cannot create local SUCCESS, advance maturity, authorize a provider write, or bypass Data Trust, attribution, capability, stop-loss, one-active-experiment, or campaign-scope gates.

## Storage and flags

Migration `0096_experience_transfer_prior_engine_v1` adds nullable context/prior metadata to existing `ads_analysis_snapshots`, `ads_recommendations`, `ads_memories`, and `ads_memory_evidence` only. ADS table count remains 16. Unknown historical context remains unknown; no semantic backfill or Experiment #2 is created. External knowledge is represented only by a versioned manifest outside PostgreSQL.

All prior/cross-client/external flags default OFF; shadow and replay are read-only. No provider writes, Autopilot activation, or production deployment are part of V1 preparation.
