# CampusPulse Governance Arena v2 — Release Audit

## Release identity

- Package: `CampusPulse-Governance-Arena-v2`
- Execution mode: `adaptive_particle_population`
- Run plan: `campus-pulse-run-plan-v6`
- Public result: `campus-pulse-live-aggregate-result-v4`
- Domain result: `campus-pulse-governance-arena-result-v2`
- Visualization: `campus-pulse-governance-visualization-v2`
- Provider authorization: disabled, hard limit `0`
- Real governance actions: `0`

The package is additive. It does not replace or modify Competition Final,
Competition RC, MassSim v1, or Governance Arena v1. Their frozen identities are
recorded in `baseline-hashes-v1.json`; the packaging command can additionally
bind any existing audited ZIP with repeated `--preserve` arguments and fails if
one changes.

## Frozen workload

The formal release has exactly:

- 1,000 reviewed forum-behavior episode parents;
- 10 state particles per parent, for 10,000 particle hypotheses;
- two synthetic scenarios;
- five paired branches (`natural`, `A`, `B`, `C`, and adaptive `D`);
- eight paired seeds;
- 24 ticks per scenario and branch;
- 1,728 `population_branch_tick` work units;
- 8,192 selected-only fixture semantic slots at budget `B=32`;
- 240 anonymous visualization frames from the canonical display seed.

The runtime executor validates these counts before sealing the result. The
offline animation uses only the canonical seed, while displayed statistical
intervals and paired differences are derived from all eight seeds.

## Model and evidence boundaries

The 1,000 parent units are reviewed forum-behavior episodes. They are not 1,000
identified or unique students. The 10,000 particles are state-uncertainty
hypotheses and do not increase the number of evidence subjects.

The exposure graph, message propagation delays, distortions, governance actors,
actor utilities, Quantal Response decisions, and policy outcomes are synthetic
model mechanisms. The graph is not an observed social or following network.
Results are model-conditional stress-test outputs, not causal policy effects,
real-world forecasts, governance advice, or evidence that any branch is
optimal.

Semantic probes use reviewed deterministic fixtures in this release. They are
Provider-ready sensors, but are not live Provider observations. No network or
Provider call is authorized or performed. Nontrivial calibration remains
disabled under
`identity_fallback_due_to_missing_paired_calibration_evidence`.

## Statistical and mechanism gates

Formal execution requires frozen Scale-Gap evidence with a passing gate. The
engine admits the release only when its `B=32` GREG/PPS finite-population
recovery and full-budget checks satisfy the frozen contract. It also evaluates
all three writeback modes and binds the formal mode before the run. A mode
cannot be promoted merely for visual effect.

The implementation enforces:

- parent and particle weight conservation;
- parent-local ESS resampling only;
- maximum three processed messages and one public emission slot per parent and
  tick;
- acyclic, time-forward message lineage and bounded TTL;
- positive inclusion probability for every selected probe;
- selected-only semantic fixture generation;
- idempotent posterior innovation writeback;
- actor-specific observations without hidden `truth` fields;
- resource conservation;
- no hard-coded scheme reward;
- deterministic checkpoint and replay identities.

These mechanisms remain simulation assumptions even when their software
invariants pass.

## Public artifact binding

The public release consists of:

- `governance-arena-v2.json`;
- `governance-arena-v2.visualization.json`;
- `governance-arena-v2.visualization.sha256`;
- `governance-arena-v2.frames.bin`;
- the public run-plan-v6;
- the static SPA, README, and audit records.

The result-v4 binds the domain-result hash and visualization binary hash. The
canonical visualization manifest binds the outer result hash, domain-result
hash, binary hash, byte length, stride, frame count, and every frame range. The
`.visualization.sha256` file binds the exact canonical manifest bytes. The
frontend fails closed on malformed JSON, a missing asset, or any hash/range
mismatch.

## Privacy boundary

The public result and ZIP must not contain:

- raw or redacted forum text;
- source identifiers;
- sample identities or selected parent identities;
- parent-particle mappings;
- record-level graph edges;
- private actor observations or private message batches;
- SQLite databases or runtime checkpoints;
- Provider request/response bodies;
- credentials, secrets, or local runtime paths.

The four server-side engine inputs remain in the private sanitized release
directory and are never copied into the offline ZIP. The package script
validates the public result contract, cross-checks all visualization bindings,
rejects forbidden private path classes, and writes per-file SHA-256 evidence.

## Reproduction

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src python3 \
  scripts/campus_pulse/run_governance_arena_v2.py

npm --prefix src/frontend run build

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src python3 \
  scripts/campus_pulse/package_governance_arena_v2.py \
  --preserve _release/campus-pulse-competition-final.zip \
  --preserve _release/campus-pulse-competition-rc.zip
```

The first command is the formal two-scenario, eight-seed execution and may take
several minutes. It emits a run summary with result, manifest, binary, workload,
usage, elapsed-time, and memory evidence. The second command creates the
frontend production build. The third command creates a deterministic ZIP and
verifies that every supplied preserved artifact remains byte-identical.

Actual pass/fail evidence for a concrete release is stored in the generated
`governance-arena-v2-run-summary.json`, package `AUDIT.json`, ZIP checksum
sidecar, test logs, and browser acceptance record. This document defines the
audit contract; it does not substitute for those generated results.
