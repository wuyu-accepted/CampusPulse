# Feature Specification: Multi-Actor Governance Orchestration

**Feature Branch**: `013-governance-orchestration`  
**Created**: 2026-07-27  
**Status**: Phase A compiler, Phase B resource-bounded orchestration and the
Phase C matched-comparison/controlled-gap core are implemented and internally
verified; cohort need-satisfaction, persisted narrative review, UI and
real-pilot layers remain unreleased  
**Depends on**: `006-evidence-snapshots`, `007-sensing-snapshots`,
`008-windowed-sensing`, `010-live-llm-runtime`

## Problem

CampusPulse can freeze evidence, construct a reviewed representative
population, replay two different historical shocks and compare a small set of
policy templates. The present policy layer is intentionally narrow: a branch
mostly selects one prebuilt intervention bundle. A real governance customer
needs to represent several responsible actors, the actions each actor is
allowed to take, timing and resource constraints, dependencies and conflicts,
and the needs that remain unresolved after a sequence of actions.

Without that layer, a visually compelling simulation risks becoming a generic
sentiment dashboard or an unconstrained model story. This slice turns policy
comparison into an auditable orchestration problem while preserving the
separation between historical observation, model-conditional stress testing
and real pilot evaluation.

## Product statement

> CampusPulse lets a governance team compose a versioned, resource-bounded
> program across multiple responsible actors, execute matched simulation
> branches over the same evidence-bound population, and explain which needs,
> groups and governance constraints remain unresolved—without presenting the
> simulation as a causal estimate or automatic policy decision.

## Non-negotiable boundaries

- Governance programs are declarative, typed and allow-listed. They contain no
  arbitrary Python, JavaScript, shell, URL, model prompt, file path or dynamic
  import.
- A policy action cannot mutate frozen evidence, sensing, population, scenario
  or historical-event records.
- Every simulation branch names the exact actor release, action-catalog
  release, program release, resource envelope, run plan and random seeds.
- Baseline and intervention branches share the same seeded pre-intervention
  state. No branch may receive information unavailable at its decision epoch.
- Resource/capacity use is reserved before an action is applied and can never
  exceed the actor or program envelope.
- The model may simulate how representative agents respond; it may not decide
  legal authority, create a real work order, contact a person, publish a
  notice, moderate content or trigger an emergency response.
- Historical shock fit is descriptive calibration. Simulation branch
  difference is `model_conditional_simulation`. Real-pilot outcomes are stored
  separately. Causal status changes only through a reviewed empirical design.
- Deterministic fixtures validate execution and accounting only. Their outputs
  are non-exportable and cannot generate a governance recommendation.
- Outputs default to aggregate, cohort-safe distributions and controlled gap
  codes. Synthetic utterances remain outside this feature.
- Human reviewers can accept, revise or reject an interpretation, but cannot
  edit the immutable underlying result.

## Domain model

### Governance actors

An actor release defines a role in the governance process, not an AI persona.
Each actor has:

- stable actor type and display label;
- jurisdiction and controlled responsibilities;
- permitted action types;
- available resource dimensions and capacity ceilings;
- decision cadence and response latency;
- escalation relationships;
- evidence requirements and approval requirements;
- release fingerprint and reviewer status.

The first catalog supports institution administration, functional department,
front-line service unit, communications/community moderation, student or
resident representative, and external coordinating organization. A scenario
uses only actors relevant to that problem.

### Governance actions

The first allow-listed action families are:

- `information_disclosure`;
- `clarification_and_rumor_control`;
- `listening_and_consultation`;
- `service_capacity_adjustment`;
- `procedure_or_policy_adjustment`;
- `targeted_support`;
- `mediation_and_coordination`;
- `monitoring_and_escalation`.

An action template defines actor eligibility, target cohorts, hypothesized
mechanism, timing, duration, prerequisites, resource consumption, capacity,
reach, possible side effects, measurable signals, evidence grade and claim
limitations. An instantiated action may choose only bounded parameters defined
by its reviewed template.

### Governance program

A program is a directed acyclic schedule of action instances. It contains:

- decision epochs and action ordering;
- responsible actor and optional co-actors;
- prerequisites and mutual exclusions;
- target cohort selectors from the reviewed taxonomy;
- resource/capacity reservations;
- observable trigger conditions based on aggregate simulation state;
- controlled fallback/escalation edges;
- success signals and stopping rules;
- explicit assumptions and non-claims.

Programs cannot branch on a private individual, a synthetic utterance or raw
forum text. Trigger conditions use controlled aggregate variables only.

### Governance gaps

The explanation layer reports controlled gap categories:

- `information_gap`;
- `trust_gap`;
- `service_capacity_gap`;
- `authority_gap`;
- `coordination_gap`;
- `timing_gap`;
- `access_or_equity_gap`;
- `evidence_gap`;
- `unresolved_need`;
- `model_uncertainty`.

Each gap links to affected representative cohorts, unmet need/response-demand
categories, action/resource evidence, seed sensitivity and a human-review
state. It is an explanation of the simulated branch, not proof of a real-world
deficiency.

## User stories

### US1 — Configure responsible actors and resources

An analyst selects a reviewed actor release and assigns scenario-specific
resource envelopes.

Acceptance:

- Only actor/action releases in `reviewed` state can enter a frozen run.
- The analyst cannot give an actor an action outside its jurisdiction.
- Resource dimensions, units, lower/upper bounds and replenishment rules come
  from the catalog; unknown units fail closed.
- Scenario assignments and envelopes are versioned, immutable after freeze and
  included in the run fingerprint.
- The UI distinguishes a modeled resource constraint from an observed real
  staffing or financial figure.

### US2 — Compose a multi-actor governance program

An analyst builds a sequence such as listening, clarification, service
adjustment and follow-up monitoring across several actors.

Acceptance:

- The editor validates cycles, unmet prerequisites, conflicting actions,
  actor jurisdiction, timing, capacity and target taxonomy before freeze.
- Parallel actions have deterministic ordering for shared-resource
  reservation and deterministic conflict resolution.
- Trigger and fallback expressions use a small typed expression grammar over
  allow-listed aggregate fields.
- A compiled program has a canonical intermediate representation and SHA-256;
  semantically identical input compiles byte-identically.
- Free-form notes are excluded from execution identity and model context.

### US3 — Run matched governance branches

An operator compares natural evolution with one or more governance programs.

Acceptance:

- All branches reuse the same evidence/population/scenario release and seeded
  pre-intervention state.
- Decision epochs are phase barriers. An action can use only aggregates sealed
  before that epoch.
- Action reservation, application, skipped/blocked status and resource balance
  are append-only and restart-safe.
- A crash/retry cannot apply or charge the same action twice.
- Branch output records exact applied actions and resources without exposing
  private agent state or generated text.

### US4 — Explain differential and remaining effects

A reviewer compares who improves, who does not, and why.

Acceptance:

- Results show policy-by-seed-by-phase differences in controlled action,
  stance, need-satisfaction and risk distributions with uncertainty across
  seeds.
- Cohort slices are limited to reviewed representative groups and subject to
  minimum effective-size/privacy suppression.
- The explanation engine derives gap codes from immutable action, resource,
  demand and aggregate result records; it cannot invent an unrecorded action
  or real-world fact.
- Every explanation links back to evidence, program, action and run
  fingerprints and states its model-conditional limitations.
- Human semantic review can approve/revise/reject the explanation text while
  preserving the underlying gap codes and result.

### US5 — Separate simulation from real pilot evaluation

A customer later executes a real governance pilot and records observed
outcomes.

Acceptance:

- Pilot registry, intervention log, observed metric definitions and analysis
  plan are separate from simulation results.
- The system labels evidence as `historical_descriptive`,
  `simulation_stress_test`, `pilot_descriptive` or
  `causal_candidate_reviewed`.
- No simulated branch difference populates an observed or causal field.
- A causal-candidate label requires a reviewed identification design,
  assumptions, diagnostics and reviewer identity; it does not arise from an
  LLM assessment.
- Calibration compares distributions and failure modes, not fabricated
  real-time trajectories.

## Functional requirements

- **FR-001**: Add immutable reviewed releases for actor catalog, action
  catalog, governance programs and scenario assignments.
- **FR-002**: Define a canonical declarative program schema and compiler with
  no executable code or unbounded expression.
- **FR-003**: Validate actor jurisdiction, action parameter bounds,
  prerequisites, DAG order, target taxonomy, timing and resource units before
  freeze and again before execution.
- **FR-004**: Add versioned resource envelopes and an atomic reservation/
  settlement ledger per actor, program, branch and action instance.
- **FR-005**: Add decision-epoch checkpoints so actions read only prior sealed
  aggregates and matched branches begin from the same seeded state.
- **FR-006**: Extend run-plan identity with actor/action/program/resource
  fingerprints while preserving evidence, sensing, population and Provider
  identities.
- **FR-007**: Persist append-only action states:
  `planned`, `eligible`, `reserved`, `applied`, `skipped`, `blocked`,
  `released` and `failed_controlled`.
- **FR-008**: Make action execution idempotent and lease/CAS protected;
  recovery may replay validation but cannot double-apply or double-charge.
- **FR-009**: Extend aggregate results with action/resource conservation,
  cohort-safe need satisfaction, governance gap codes and seed sensitivity.
- **FR-010**: Generate explanations only from allow-listed aggregate and
  ledger fields, attach complete lineage and require human semantic review for
  release-facing narrative.
- **FR-011**: Keep deterministic fixture results non-exportable and prohibit
  governance conclusions when execution provenance is a fixture.
- **FR-012**: Add a separate real-pilot registry, metric dictionary,
  intervention log and analysis-plan version; never overwrite simulation.
- **FR-013**: Require reviewed empirical design metadata before exposing
  `causal_candidate_reviewed`; all other pilot results remain descriptive.
- **FR-014**: Provide project/scenario/program/run/report APIs with
  idempotency, expected-version CAS and authenticated tenant scope once
  `012-authenticated-tenancy` is released.
- **FR-015**: Emit privacy-safe action/gap/audit telemetry and hard resource/
  cost counters without cohort labels in globally shared metric dimensions.

## First demonstrable slice

The first release proves generality with the two existing, substantively
different shocks:

1. a governance-related information shock, where communication,
   consultation, procedure and service actions are plausible;
2. a predominantly external incident, where the honest baseline may contain
   monitoring, support and communication but no invented policy lever.

The competition demo primarily shows the governance-related branch and uses
the external incident as a negative-control-style stress case: the platform
must be able to say that available governance actions have limited modeled
leverage instead of forcing a positive effect.

This is a simulation-design contrast, not causal identification.

## Success criteria

- Compiler produces byte-identical canonical programs across key order and
  harmless formatting changes; invalid cycles/units/targets/actions fail.
- For every run and branch:
  `opening resources + replenishment = reserved + available + settled loss`
  and every action has exactly one terminal ledger outcome.
- Same seeds and same program reproduce identical action schedules,
  checkpoints and aggregate hashes under crash/restart.
- Baseline branch is bit-equivalent to the existing engine when no governance
  action is eligible.
- Matched-branch tests detect any pre-intervention state drift or future-data
  leakage.
- Gap explanations contain only recorded action/resource/result facts and all
  claim labels/lineage; mutation tests cannot inject raw text or identities.
- The two-shock acceptance demonstrates one meaningful governance program and
  one limited-leverage external case without overstating either.
- Human reviewers judge a stratified sample of cohort/action/gap narratives
  for semantic fidelity; reviewer disagreement and revisions are retained.
- Existing evidence, sensing, population, runtime, privacy and zero-network
  fixture gates remain green.

## Out of scope

- Autonomous real-world action or direct contact with students/residents;
- legal or disciplinary decision-making;
- optimizing a single scalar “social stability” score;
- hidden individual risk scoring;
- arbitrary workflow engines or executable user scripts;
- treating LLM-generated explanation as empirical evidence;
- causal claims without a separately reviewed real-pilot design.
