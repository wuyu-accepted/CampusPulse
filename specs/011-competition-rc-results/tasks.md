# Tasks

- [x] **T001** Freeze the report-document-v1, claim, privacy, fixture warning,
  deterministic JSON and single-file HTML boundaries.
- [x] **T010** Implement pure aggregate-result-v2 to report-document
  projection with canonical fingerprint.
- [x] **T020** Implement canonical UTF-8 JSON bytes and strict revalidation.
- [x] **T030** Implement deterministic escaped single-file HTML bytes with
  fixed fixture/live labels.
- [x] **T040** Add 672 fixture, 384 live, byte stability, hash, privacy, claim,
  strict-type, tamper and no-I/O tests.
- [x] **T050** Pass the independent report tests and related aggregate-result
  contract regression.
- [x] **T060** Freeze and test the tenant/project/run-scoped service material
  validator, terminal/identity checks and fixture external-download denial.
- [x] **T065** Add the minimal aggregate-result-v2 run-panel projection,
  prominent deterministic-fixture boundary, authorized-live-only future
  report actions, privacy assertions and frontend Node contract coverage.
- [x] **T068** Implement the exact
  `get_live_aggregate_v2_report_material(tenant_id, project_id, run_id)`
  single-transaction Store read and satisfy its fail-closed acceptance suite.
- [x] **T070** Add JSON and HTML attachment routes with explicit
  `application/json; charset=utf-8` and `text/html; charset=utf-8` content
  types after an atomic Store aggregate-v2 read seam exists.
- [x] **T080** Add a loopback-only, zero-API competition launcher with build
  identity, private-file and sensitive-marker gates, SPA deep-link fallback,
  deliberate offline workbench notice and deterministic HTTP smoke tests.
- [x] **T085** Rehearse the dual-shock, four-stage, policy, persona, isolated
  workbench and 390x844 mobile paths in a real browser; freeze the evidence in
  `browser-qa-20260727.md`.
- [x] **T090** Rerun every backend, simulation, frontend and production-build
  gate on the synchronized RC tree and publish the machine-readable final RC
  audit.
- [x] **T095** Build the deterministic allow-listed handoff ZIP with
  Windows/Unix entrypoints, per-member SHA-256 manifest and companion package
  checksum; rebuild it twice and verify identical bytes.

Closure evidence:

- independent report-export suite: 7/7;
- aggregate-result-v2 plus report-export regression: 15/15;
- JSON/HTML repeat-byte checks and module compilation: pass;
- network, filesystem and environment access: denied in the pure-pipeline test.
- service material-boundary suite: 6/6; missing, non-terminal, fixture-external,
  cross-scope and identity-tampered material all fail closed.
- atomic Store read suite: 9/9, including fixture/live shape, cross-scope,
  non-terminal, slot/usage/result tamper, private-table denial and the
  authorized-only service download path.
- result/report/service-related regression: 31/31; service module compilation
  passes.
- frontend aggregate-result-v2 contract: 11/11 locally and on the complete
  remote tree; remote Vite production build: 2,154 modules transformed, pass.
- JSON/HTML routes return only report bytes revalidated from the atomic
  aggregate-v2 read, include SHA-256/nosniff attachment headers, and reject
  deterministic fixtures as external downloads.
- the attachment routes remain an internal loopback release surface; public
  internet authentication/RBAC remains deferred to T100.
- offline competition launcher tests: 4/4; real production build audit and
  loopback HTTP smoke pass with build fingerprint
  `641638e8167433fb65cc5ae70ed3cca802a96c67cf2aa64ce7432a727b56dcef`.
- desktop and 390x844 browser rehearsal pass without console errors or
  horizontal overflow; the isolated workbench created the reviewed dual-shock
  project and a frozen 48-agent plan while reporting zero Provider calls.
- final synchronized RC gate: backend 299/299, simulation 152/152, frontend
  11/11, Vite 2,154 modules and offline build audit all pass; machine-readable
  evidence is frozen in `competition-rc-audit-v1.json`.
- handoff package contains 62 members under one fixed root; a second build
  produced the same SHA-256, internal manifest verification passed, and
  the companion `.zip.sha256` verified from the release directory.

Deferred:

- [ ] **T100** Connect report downloads to an authenticated API after the
  persistence and authorization release boundary is approved.
