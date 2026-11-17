# Feature Specification: Competition RC Aggregate Reports

**Feature Branch**: `011-competition-rc-results`  
**Created**: 2026-07-26  
**Status**: Minimal implementation complete

## Problem

CampusPulse has a strict aggregate-result-v2 contract, but collaborators and
judges also need a portable, human-readable artifact. Copying internal runtime
state into an ad-hoc report would weaken the result's lineage, privacy and
claim boundaries.

## Product statement

> CampusPulse transforms one validated aggregate-result-v2 into byte-stable,
> auditable JSON and a self-contained HTML report without adding evidence,
> free text or broader claims.

## Boundaries

- Input must pass the exact aggregate-result-v2 validator against its frozen
  run, extension, launch and slot-set identities.
- Output remains `model_conditional_simulation`,
  `causal_status = not_identified`,
  `forecast_status = not_forecast`,
  `population_status = not_population_estimate` and
  `real_time_status = not_real_time`.
- A deterministic-transport report is visibly labelled
  `non_exportable_fixture` and says it cannot be used as a governance finding.
- JSON and HTML contain aggregate counts, integer ppm, usage, variation,
  identities, limitations and privacy declarations only.
- Raw or synthetic utterances, prompts, persona/agent rows, source identifiers,
  provider bodies, credentials, paths and arbitrary free text are forbidden.
- HTML is one UTF-8 file with inline CSS, no scripts, external resources,
  remote fonts, links, forms or active content.
- The report renderer remains a pure transformation. The frontend may project
  an already-returned aggregate-result-v2 into a minimal read-only comparison
  view, but adds no store table or API route and never synthesizes a report.

## Functional requirements

- **FR-001**: Produce a strict report-document-v1 from one validated
  aggregate-result-v2 and preserve every immutable lineage identity.
- **FR-002**: Copy the fixed claim boundary and limitation codes exactly; no
  caller-supplied title, note, interpretation or governance recommendation is
  accepted.
- **FR-003**: Repeat expected/completed turns, call/cache/token usage,
  seed-policy-phase distributions and controlled variation.
- **FR-004**: Include an explicit aggregate-only privacy declaration and
  provenance-specific publication status.
- **FR-005**: Canonical JSON uses UTF-8, sorted keys, compact separators and
  `allow_nan=False`; its report fingerprint excludes only itself.
- **FR-006**: HTML is rendered only from the validated report document, escapes
  all text and has deterministic ordering and bytes.
- **FR-007**: Identical validated input produces byte-identical JSON and HTML
  and stable SHA-256 identities.
- **FR-008**: Reject extra fields, booleans-as-integers, floats/non-finite
  values, aggregate conservation drift, claim broadening and recursive privacy
  violations.
- **FR-009**: The run panel recognizes only the frozen aggregate-result-v2
  schema for the new view and presents strategy × seed × phase distributions,
  controlled cross-seed variation, budget usage and the complete claim
  boundary without utterance, persona or source-identifier drill-down.
- **FR-010**: A deterministic fixture is prominently labelled
  `确定性测试夹具` and cannot expose a report-download action. JSON/HTML actions
  appear only for `authorized_live_llm` +
  `authorized_live_aggregate` + `exportable=true` with an exact claim
  boundary.
- **FR-011**: Live download actions call fixed future read-only routes
  `/runs/{run_id}/reports/aggregate.json` and
  `/runs/{run_id}/reports/aggregate.html`. Until those routes exist, the UI
  must surface the backend failure and must not generate a substitute file or
  report success.

## Acceptance

- Valid 672-turn fixture and 384-turn authorized-live results produce JSON and
  single-file HTML.
- Fixture HTML contains a prominent non-exportable/non-governance warning;
  authorized-live HTML remains explicitly model-conditional and non-causal.
- JSON revalidation reproduces the exact report fingerprint.
- Same input rendered repeatedly has identical bytes and hashes.
- Privacy scans find no forbidden fields/content or external HTML resources.
- The existing run execution panel remains parseable and buildable; fixture
  and authorized-live publication paths are covered by a Node contract test.

## Non-goals

- Writing files, publishing URLs or adding download endpoints.
- Report editing, arbitrary Markdown/HTML, synthetic transcript browsing or
  individual-agent drill-down.
- Causal inference, forecasting, population estimation or real-time sensing.

## Service integration boundary

The service may build an external report only from one tenant/project/run
scoped, atomic repository read containing the succeeded runtime, schema-v7
usage and immutable aggregate-result-v2 record. The launch, input, extension,
slot-set, provenance, completion and result fingerprints must agree.

The Store now exposes the aggregate-v2 atomic read seam below. The HTTP
JSON/HTML routes use only this material, revalidate every result and report
identity, and fail closed for incomplete, cross-scope or tampered rows.
Deterministic fixtures may pass the material validator only for internal report
generation and always fail the external-download gate.

## Store atomic-read contract (implemented)

The required repository seam is exactly:

```python
get_live_aggregate_v2_report_material(
    tenant_id,
    project_id,
    run_id,
)
```

It returns `None` for an unknown tenant/project/run scope. If scoped rows exist
but are incomplete, non-terminal or inconsistent, it raises
`StoreConflictError`. One SQLite read transaction must return an exact
top-level object with these keys only:

- `run`: the canonical decoded frozen run returned by `get_run`;
- `extension`: the canonical nested live-plan extension object;
- `runtime`: exactly `tenant_id`, `project_id`, `run_id`, `status`,
  `state_version`, `adapter_mode`, `launch_fingerprint`,
  `input_fingerprint`, `token_limit`, `effective_concurrency_limit`,
  `turn_limit`, `turns_completed`, `active_units`, `peak_concurrency` and
  `finished_at`;
- `usage`: exactly tenant/project/run ownership, `usage_schema_version`,
  `usage_version`, provenance and launch/input/extension identities, the
  denormalized `authorization_id`/`authorization_fingerprint`,
  `transport_config_fingerprint`, `slot_set_fingerprint`, and these nineteen
  controlled aggregate fields: `expected_agent_turns`, `turns_completed`,
  `effective_concurrency_limit`, `plan_provider_call_limit`,
  `authorization_provider_call_cap`, `effective_provider_call_limit`,
  `provider_calls_reserved`, `provider_calls_completed`,
  `provider_calls_known`, `provider_calls_unknown`, `plan_token_limit`,
  `authorization_token_cap`, `effective_token_limit`, `tokens_in_flight`,
  `provider_reported_tokens`, `risk_charged_tokens`, `cache_hits`,
  `active_attempts` and `peak_concurrency`;
- `slot_set_identity`: exactly `slot_set_schema_version`,
  `slot_set_fingerprint` and `slot_count`;
- `result_record`: exactly tenant/project/run ownership,
  `result_schema_version`, `launch_fingerprint`, `result_sha256`,
  `completeness_ppm`, `created_at` and `result`.

The read must jointly validate a succeeded runtime, schema-v7 usage,
fixture/live adapter-provenance identity, frozen run/extension identities,
quiescent and complete usage, the stored slot count/fingerprint and the
immutable aggregate-result-v2 fingerprint. It may use the authorization
identity already denormalized into usage, but must not read
`run_live_authorizations`, authorization lifecycle or secret material. It must
not read provider attempts, decision cache/checkpoints, prompts, raw
utterances or individual decision rows.

## Frontend release-candidate boundary

The frontend integration is deliberately conditional and minimal. It keeps
the existing execution panel, reads only aggregate-result-v2 fields already
returned by the run result endpoint, and adds no client-side interpretation.
The fixture warning and full credibility boundary remain visible beside every
comparison. A fixed route helper prepares later JSON/HTML attachment wiring,
but a 404/409/501 response is rendered as unavailable and no browser-created
fallback report is permitted.
