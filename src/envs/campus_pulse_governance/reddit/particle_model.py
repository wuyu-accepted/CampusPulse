"""CMV-specific 10-particle state model that feeds the existing ForumTwin.

This is an explicit structural state model, not a claim that latent attitudes
are identified from Reddit text.  The runtime keeps per-agent particle weights
separate from population design weights and records each observation once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


STATE_FIELDS = (
    "attention", "concern", "trust", "satisfaction", "support", "opposition",
    "activity", "voice", "service_strain", "rumor_belief", "verified_belief",
    "evidence_acceptance",
)
STATE_INDEX = {name: index for index, name in enumerate(STATE_FIELDS)}
STANCE_VALUES = {
    "support", "oppose", "mixed", "neutral", "uncertain", "not_applicable"
}
ACTION_VALUES = {
    "ignore", "read", "post", "reply", "quote", "repost", "verify",
    "correct", "seek_help", "report_risk", "react",
}
PARTICLE_MODEL_ID = "cmv-structural-particle-model-v1"


class CmvParticleError(ValueError):
    """Invalid CMV particle model input or state transition."""


@dataclass
class CmvParticleSnapshot:
    agent_ids: tuple[str, ...]
    values: np.ndarray
    conditional_weights: np.ndarray
    design_weights: np.ndarray
    tick: int = 0
    innovations: list[set[str]] = field(default_factory=list)
    resampling_count: np.ndarray | None = None

    def validate(self, *, particles_per_agent: int = 10) -> None:
        n = len(self.agent_ids)
        if len(set(self.agent_ids)) != n or n == 0:
            raise CmvParticleError("particle snapshot agent IDs must be nonempty and unique")
        if self.values.shape != (n, particles_per_agent, len(STATE_FIELDS)):
            raise CmvParticleError("particle value shape does not match the world contract")
        if self.conditional_weights.shape != (n, particles_per_agent):
            raise CmvParticleError("conditional particle weights have the wrong shape")
        if self.design_weights.shape != (n,):
            raise CmvParticleError("population design weights have the wrong shape")
        if not np.isfinite(self.values).all() or not np.isfinite(self.conditional_weights).all():
            raise CmvParticleError("particle snapshot contains a non-finite value")
        if np.any(self.values < 0.0) or np.any(self.values > 1.0):
            raise CmvParticleError("particle state is outside [0,1]")
        if np.any(self.conditional_weights < 0.0):
            raise CmvParticleError("particle weights cannot be negative")
        if not np.allclose(self.conditional_weights.sum(axis=1), 1.0, atol=1e-10):
            raise CmvParticleError("per-agent conditional particle weights must sum to one")
        if np.any(self.design_weights <= 0) or not math.isclose(float(self.design_weights.sum()), 1.0, abs_tol=1e-10):
            raise CmvParticleError("population design weights must be positive and sum to one")
        if not np.allclose(self.values[:, :, 4] + self.values[:, :, 5], np.minimum(1.0, self.values[:, :, 4] + self.values[:, :, 5]), atol=1e-10):
            raise CmvParticleError("support/opposition mass exceeds one")
        if self.innovations and len(self.innovations) != n:
            raise CmvParticleError("innovation ledgers must align with agent ordering")
        if self.resampling_count is not None and self.resampling_count.shape != (n,):
            raise CmvParticleError("resampling counters must align with agent ordering")


def _seed(world_seed: int, agent_id: str, suffix: str = "init") -> int:
    digest = hashlib.sha256(f"{world_seed}\0{agent_id}\0{suffix}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _feature(agent: Mapping[str, Any], name: str, default: float = 0.5) -> float:
    raw = agent.get("features", {}).get(name, default) if isinstance(agent.get("features"), Mapping) else default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return min(1.0, max(0.0, value))


def initialize_fixed_particles(
    agents: Sequence[Mapping[str, Any]],
    *,
    world_seed: int = 20260924,
    particles_per_agent: int = 10,
) -> CmvParticleSnapshot:
    """Create immutable-seed event-neutral particles for a compiled world."""
    if particles_per_agent != 10:
        raise CmvParticleError("the current CMV world release is frozen at ten particles per agent")
    agent_ids = tuple(str(row.get("agent_id") or "") for row in agents)
    if not agent_ids or any(not value for value in agent_ids) or len(set(agent_ids)) != len(agent_ids):
        raise CmvParticleError("compiled agents need unique synthetic IDs")
    raw_weights = np.asarray([float(row.get("design_weight", 0.0)) for row in agents], dtype=np.float64)
    if not np.isfinite(raw_weights).all() or np.any(raw_weights <= 0):
        raise CmvParticleError("each agent needs a finite positive design weight")
    design_weights = raw_weights / raw_weights.sum()
    n = len(agents)
    values = np.empty((n, particles_per_agent, len(STATE_FIELDS)), dtype=np.float64)
    weights = np.full((n, particles_per_agent), 1.0 / particles_per_agent, dtype=np.float64)
    for i, agent in enumerate(agents):
        rng = np.random.default_rng(_seed(world_seed, agent_ids[i]))
        activity = _feature(agent, "activity_score")
        voice = _feature(agent, "public_voice_ratio")
        values[i, :, 0] = rng.beta(2.0 + 3.0 * activity, 2.0 + 3.0 * (1.0 - activity), particles_per_agent)
        values[i, :, 1] = rng.beta(2.0, 2.0, particles_per_agent)
        values[i, :, 2] = rng.beta(2.0, 2.0, particles_per_agent)
        values[i, :, 3] = rng.beta(2.0, 2.0, particles_per_agent)
        simplex = rng.dirichlet(np.array([2.0, 2.0, 2.5]), size=particles_per_agent)
        values[i, :, STATE_INDEX["support"]] = simplex[:, 0]
        values[i, :, STATE_INDEX["opposition"]] = simplex[:, 1]
        values[i, :, STATE_INDEX["activity"]] = rng.beta(1.5 + 4.0 * activity, 1.5 + 4.0 * (1.0 - activity), particles_per_agent)
        values[i, :, STATE_INDEX["voice"]] = rng.beta(1.5 + 4.0 * voice, 1.5 + 4.0 * (1.0 - voice), particles_per_agent)
        values[i, :, STATE_INDEX["service_strain"]] = 0.0
        values[i, :, STATE_INDEX["rumor_belief"]] = rng.beta(2.0, 2.0, particles_per_agent)
        values[i, :, STATE_INDEX["verified_belief"]] = rng.beta(2.0, 2.0, particles_per_agent)
        values[i, :, STATE_INDEX["evidence_acceptance"]] = rng.beta(2.0, 2.0, particles_per_agent)
    snapshot = CmvParticleSnapshot(
        agent_ids=agent_ids,
        values=values,
        conditional_weights=weights,
        design_weights=design_weights,
        innovations=[set() for _ in agent_ids],
        resampling_count=np.zeros(n, dtype=np.int64),
    )
    snapshot.validate(particles_per_agent=particles_per_agent)
    return snapshot


def initialize_episode_particles(
    world_snapshot: CmvParticleSnapshot,
    event_priors: Mapping[str, Mapping[str, float]],
    *,
    event_id: str,
    world_seed: int = 20260924,
) -> CmvParticleSnapshot:
    """Copy persistent state and initialize proposition state for one event."""
    world_snapshot.validate()
    output = CmvParticleSnapshot(
        agent_ids=world_snapshot.agent_ids,
        values=world_snapshot.values.copy(),
        conditional_weights=world_snapshot.conditional_weights.copy(),
        design_weights=world_snapshot.design_weights.copy(),
        tick=0,
        innovations=[set() for _ in world_snapshot.agent_ids],
        resampling_count=np.zeros(len(world_snapshot.agent_ids), dtype=np.int64),
    )
    for i, agent_id in enumerate(output.agent_ids):
        prior = event_priors.get(agent_id, {})
        alpha = np.array([
            max(0.25, float(prior.get("support_alpha", 2.0))),
            max(0.25, float(prior.get("opposition_alpha", 2.0))),
            max(0.25, float(prior.get("uncertain_alpha", 2.5))),
        ])
        if not event_id:
            raise CmvParticleError("event ID is required to initialize event-specific particles")
        rng = np.random.default_rng(_seed(world_seed, agent_id, f"episode-prior:{event_id}"))
        simplex = rng.dirichlet(alpha, size=output.values.shape[1])
        output.values[i, :, STATE_INDEX["support"]] = simplex[:, 0]
        output.values[i, :, STATE_INDEX["opposition"]] = simplex[:, 1]
        # There is no observed claim at episode start, so belief in a rumor and
        # belief in supplied evidence start broad and are not filled from labels.
        output.values[i, :, STATE_INDEX["rumor_belief"]] = 0.5
        output.values[i, :, STATE_INDEX["verified_belief"]] = 0.5
    output.validate()
    return output


def _clip(values: np.ndarray) -> np.ndarray:
    return np.clip(values, 0.0, 1.0)


def predict_tick(
    snapshot: CmvParticleSnapshot,
    *,
    tick: int,
    exposures: Mapping[str, float],
    contested_claim_signal: Mapping[str, float] | None = None,
    correction_signal: Mapping[str, float] | None = None,
    service_resolution: Mapping[str, float] | None = None,
    governance_effects_by_agent: Mapping[str, Mapping[str, float]] | None = None,
    seed: int = 0,
    noise_scale: float = 0.015,
) -> CmvParticleSnapshot:
    """Advance all agents, including unactivated agents, through one frozen tick."""
    snapshot.validate()
    if tick != snapshot.tick + 1 or not 0.0 <= noise_scale <= 0.1:
        raise CmvParticleError("particle tick must advance exactly once with bounded noise")
    output = CmvParticleSnapshot(
        agent_ids=snapshot.agent_ids,
        values=snapshot.values.copy(),
        conditional_weights=snapshot.conditional_weights.copy(),
        design_weights=snapshot.design_weights.copy(),
        tick=tick,
        innovations=[set(values) for values in snapshot.innovations],
        resampling_count=(snapshot.resampling_count.copy() if snapshot.resampling_count is not None else np.zeros(len(snapshot.agent_ids), dtype=np.int64)),
    )
    contested_claim_signal = contested_claim_signal or {}
    correction_signal = correction_signal or {}
    service_resolution = service_resolution or {}
    governance_effects_by_agent = governance_effects_by_agent or {}
    allowed_effects = {"verification", "reassurance", "service", "voice", "bridge", "prebunk"}
    if set(governance_effects_by_agent) - set(snapshot.agent_ids):
        raise CmvParticleError("governance effects refer to an unknown Agent")
    for i, agent_id in enumerate(snapshot.agent_ids):
        exposure = min(1.0, max(0.0, float(exposures.get(agent_id, 0.0))))
        contested = min(1.0, max(0.0, float(contested_claim_signal.get(agent_id, 0.0))))
        corrected = min(1.0, max(0.0, float(correction_signal.get(agent_id, 0.0))))
        resolved = min(1.0, max(0.0, float(service_resolution.get(agent_id, 0.0))))
        raw_effects = governance_effects_by_agent.get(agent_id, {})
        if not isinstance(raw_effects, Mapping) or set(raw_effects) - allowed_effects:
            raise CmvParticleError("governance effect keys are invalid")
        effects = {key: float(value) for key, value in raw_effects.items()}
        if any(not math.isfinite(value) or not 0.0 <= value <= 0.8 for value in effects.values()):
            raise CmvParticleError("governance effect value is outside [0, 0.8]")
        x = output.values[i]
        x[:, STATE_INDEX["attention"]] = 0.70 * x[:, STATE_INDEX["attention"]] + 0.30 * exposure
        x[:, STATE_INDEX["concern"]] = _clip(0.96 * x[:, STATE_INDEX["concern"]] + 0.06 * contested - 0.025 * corrected)
        x[:, STATE_INDEX["trust"]] = _clip(0.985 * x[:, STATE_INDEX["trust"]] + 0.025 * resolved - 0.01 * contested)
        x[:, STATE_INDEX["satisfaction"]] = _clip(0.98 * x[:, STATE_INDEX["satisfaction"]] + 0.025 * resolved - 0.015 * contested)
        x[:, STATE_INDEX["activity"]] = _clip(0.90 * x[:, STATE_INDEX["activity"]] + 0.10 * exposure)
        x[:, STATE_INDEX["voice"]] = _clip(0.97 * x[:, STATE_INDEX["voice"]] + 0.03 * exposure)
        x[:, STATE_INDEX["service_strain"]] = _clip(0.98 * x[:, STATE_INDEX["service_strain"]] + 0.05 * contested - 0.12 * resolved)
        x[:, STATE_INDEX["rumor_belief"]] = _clip(x[:, STATE_INDEX["rumor_belief"]] - 0.05 * corrected * x[:, STATE_INDEX["evidence_acceptance"]])
        x[:, STATE_INDEX["verified_belief"]] = _clip(x[:, STATE_INDEX["verified_belief"]] + 0.04 * corrected * x[:, STATE_INDEX["evidence_acceptance"]])
        # Delayed governance resources change state only after a public action
        # is both due and visible in this Agent's frozen feed. Policy actions do
        # not directly change support/opposition or guarantee a favorable result.
        verification = effects.get("verification", 0.0)
        reassurance = effects.get("reassurance", 0.0)
        service = effects.get("service", 0.0)
        voice = effects.get("voice", 0.0)
        bridge = effects.get("bridge", 0.0)
        prebunk = effects.get("prebunk", 0.0)
        x[:, STATE_INDEX["concern"]] = _clip(
            x[:, STATE_INDEX["concern"]] - 0.035 * reassurance - 0.018 * voice
        )
        x[:, STATE_INDEX["trust"]] = _clip(
            x[:, STATE_INDEX["trust"]] + 0.025 * verification + 0.018 * service
        )
        x[:, STATE_INDEX["satisfaction"]] = _clip(
            x[:, STATE_INDEX["satisfaction"]] + 0.025 * service
        )
        x[:, STATE_INDEX["service_strain"]] = _clip(
            x[:, STATE_INDEX["service_strain"]] - 0.04 * service
        )
        x[:, STATE_INDEX["rumor_belief"]] = _clip(
            x[:, STATE_INDEX["rumor_belief"]]
            - 0.04 * (verification + prebunk) * x[:, STATE_INDEX["evidence_acceptance"]]
        )
        x[:, STATE_INDEX["verified_belief"]] = _clip(
            x[:, STATE_INDEX["verified_belief"]]
            + 0.025 * verification * x[:, STATE_INDEX["evidence_acceptance"]]
        )
        x[:, STATE_INDEX["voice"]] = _clip(
            x[:, STATE_INDEX["voice"]] + 0.025 * (voice + bridge)
        )
        rng = np.random.default_rng(_seed(seed + tick, agent_id, "transition"))
        x += rng.normal(0.0, noise_scale, size=x.shape)
        x[:] = _clip(x)
        # Preserve the support / opposition / undecided simplex after noise.
        pair = x[:, [STATE_INDEX["support"], STATE_INDEX["opposition"]]]
        total = pair.sum(axis=1)
        over = total > 1.0
        pair[over] /= total[over, None]
        x[:, STATE_INDEX["support"]] = pair[:, 0]
        x[:, STATE_INDEX["opposition"]] = pair[:, 1]
    output.validate()
    return output


def _stance_likelihood(particles: np.ndarray, stance: str) -> np.ndarray:
    if stance not in STANCE_VALUES:
        raise CmvParticleError("observation stance is invalid")
    if stance == "neutral":
        # A neutral expression carries no directional evidence. Leave the
        # stance component of the particle weights unchanged.
        return np.ones(len(particles), dtype=np.float64)
    support = particles[:, STATE_INDEX["support"]]
    oppose = particles[:, STATE_INDEX["opposition"]]
    uncertain = np.maximum(0.0, 1.0 - support - oppose)
    probabilities = np.stack([support, oppose, uncertain], axis=1)
    probabilities = 0.05 + 0.85 * probabilities
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    if stance == "mixed":
        # Mixed observations are more likely when the particle expects both
        # supporting and opposing reactions, rather than either side alone.
        return np.sqrt(probabilities[:, 0] * probabilities[:, 1])
    index = {"support": 0, "oppose": 1, "uncertain": 2, "not_applicable": 2}[stance]
    return probabilities[:, index]


def _action_likelihood(particles: np.ndarray, action: str) -> np.ndarray:
    if action not in ACTION_VALUES:
        raise CmvParticleError("observation action is invalid")
    attention = particles[:, STATE_INDEX["attention"]]
    activity = particles[:, STATE_INDEX["activity"]]
    voice = particles[:, STATE_INDEX["voice"]]
    concern = particles[:, STATE_INDEX["concern"]]
    if action == "ignore":
        probability = 1.0 - 0.55 * attention * activity
    elif action == "read":
        probability = 0.15 + 0.75 * attention
    elif action in {"post", "reply", "quote", "repost"}:
        probability = 0.03 + 0.92 * attention * activity * voice
    elif action in {"seek_help", "report_risk"}:
        probability = 0.03 + 0.92 * attention * concern * activity
    else:
        probability = 0.03 + 0.92 * attention * activity
    return np.clip(probability, 0.01, 0.99)


def assimilate_observation(
    snapshot: CmvParticleSnapshot,
    *,
    agent_id: str,
    innovation_id: str,
    action: str,
    stance: str,
    likelihood_floor: float = 1e-6,
    resample_ess_ratio: float = 0.5,
    seed: int = 0,
    observation_model: str = "legacy",
    expression_observed: bool = True,
    rejuvenate: bool = True,
    observation_confidence: float = 1.0,
) -> tuple[CmvParticleSnapshot, dict[str, float]]:
    """Weight particles once from one validated LLM decision receipt.

    This updates particle weights from the observed action/stance pair; it does
    not also apply a second heuristic state-delta assimilation.
    """
    snapshot.validate()
    if observation_model not in {"legacy", "directional-v2", "scope-calibrated-v3"}:
        raise CmvParticleError("unknown stance observation model")
    if not np.isfinite(observation_confidence) or not 0.0 <= observation_confidence <= 1.0:
        raise CmvParticleError("observation confidence must be finite and within [0, 1]")
    if not innovation_id or not 0.0 < likelihood_floor < 1.0 or not 0.0 < resample_ess_ratio <= 1.0:
        raise CmvParticleError("observation update parameters are invalid")
    if agent_id not in snapshot.agent_ids:
        raise CmvParticleError("observation agent is not in the fixed world")
    i = snapshot.agent_ids.index(agent_id)
    if innovation_id in snapshot.innovations[i]:
        raise CmvParticleError("observation innovation has already been assimilated")
    action_likelihood = _action_likelihood(snapshot.values[i], action)
    stance_likelihood = _stance_likelihood(snapshot.values[i], stance)
    if observation_model in {"directional-v2", "scope-calibrated-v3"}:
        # Mixed/uncertain is not evidence that support and opposition are equal.
        # Silent turns carry action evidence only. A directional observation is
        # about the whole frozen proposition, not agreement with the parent.
        if not expression_observed or stance not in {"support", "oppose"}:
            stance_likelihood = np.ones(len(action_likelihood))
        else:
            net = snapshot.values[i, :, STATE_INDEX["support"]]-snapshot.values[i, :, STATE_INDEX["opposition"]]
            sign = 1.0 if stance == "support" else -1.0
            stance_likelihood = 0.05+0.90/(1.0+np.exp(-sign*net/0.15))
            if observation_model == "scope-calibrated-v3":
                stance_likelihood = stance_likelihood ** observation_confidence
        likelihood = np.maximum(likelihood_floor, action_likelihood*stance_likelihood)
    else:
        likelihood = np.maximum(likelihood_floor, np.sqrt(action_likelihood * stance_likelihood))
    log_weights = np.log(np.maximum(snapshot.conditional_weights[i], likelihood_floor)) + np.log(likelihood)
    log_weights -= float(np.max(log_weights))
    updated_weights = np.exp(log_weights)
    updated_weights /= updated_weights.sum()
    ess = 1.0 / float(np.sum(updated_weights**2))
    output = CmvParticleSnapshot(
        agent_ids=snapshot.agent_ids,
        values=snapshot.values.copy(),
        conditional_weights=snapshot.conditional_weights.copy(),
        design_weights=snapshot.design_weights.copy(),
        tick=snapshot.tick,
        innovations=[set(values) for values in snapshot.innovations],
        resampling_count=(snapshot.resampling_count.copy() if snapshot.resampling_count is not None else np.zeros(len(snapshot.agent_ids), dtype=np.int64)),
    )
    output.conditional_weights[i] = updated_weights
    output.innovations[i].add(innovation_id)
    threshold = resample_ess_ratio * output.values.shape[1]
    if ess < threshold:
        rng = np.random.default_rng(_seed(seed + snapshot.tick, agent_id, innovation_id))
        count = output.values.shape[1]
        positions = (rng.random() + np.arange(count)) / count
        cumulative = np.cumsum(updated_weights)
        indexes = np.searchsorted(cumulative, positions, side="right")
        output.values[i] = output.values[i, np.clip(indexes, 0, count - 1)]
        if observation_model in {"directional-v2", "scope-calibrated-v3"} and rejuvenate:
            # Liu-West shrinkage/kernel rejuvenation prevents clone-only SMC.
            # Use pre-resampling weighted moments; never move toward a desired
            # label a second time. This is an approximate bounded state kernel.
            mean = np.average(snapshot.values[i], axis=0, weights=updated_weights)
            variance = np.average((snapshot.values[i]-mean)**2, axis=0, weights=updated_weights)
            shrink = 0.98
            output.values[i] = _clip(shrink*output.values[i]+(1-shrink)*mean
                + rng.normal(0, np.sqrt((1-shrink**2)*variance), size=output.values[i].shape))
            pair = output.values[i, :, 4:6]
            pair /= np.maximum(1.0, pair.sum(axis=1))[:, None]
            output.values[i, :, 4:6] = pair
        output.conditional_weights[i] = 1.0 / count
        output.resampling_count[i] += 1
    output.validate()
    return output, {
        "effective_sample_size_before_resample": ess,
        "particle_count": float(snapshot.values.shape[1]),
        "resampled": float(ess < threshold),
        "update_source": "single_llm_decision_receipt",
        "observation_model": observation_model,
        "directional_evidence_used": bool(expression_observed and stance in {"support", "oppose"}
                                          and (observation_model != "scope-calibrated-v3" or observation_confidence > 0)),
    }


def stance_predictive_probabilities(
    snapshot: CmvParticleSnapshot, agent_id: str, *, neutral_radius: float = 0.05,
) -> dict[str, float]:
    """Read a forecast before observing text, using a fixed net-belief boundary.

    This is an operational particle forecast, not an expression classifier.
    Keep the boundary frozen across conditions; never fit it to their labels.
    """
    snapshot.validate()
    if not np.isfinite(neutral_radius) or not 0 <= neutral_radius < 1:
        raise CmvParticleError("invalid predictive neutral boundary")
    if agent_id not in snapshot.agent_ids:
        raise CmvParticleError("predictive Agent is not in the fixed world")
    i = snapshot.agent_ids.index(agent_id)
    net = snapshot.values[i, :, STATE_INDEX["support"]] - snapshot.values[i, :, STATE_INDEX["opposition"]]
    weights = snapshot.conditional_weights[i]
    return {
        "support": float(weights[net > neutral_radius].sum()),
        "oppose": float(weights[net < -neutral_radius].sum()),
        "neutral_or_mixed": float(weights[np.abs(net) <= neutral_radius].sum()),
    }


def decode_agent_state(snapshot: CmvParticleSnapshot, agent_id: str) -> dict[str, Any]:
    """Return weighted state summaries and stance probabilities for display."""
    snapshot.validate()
    try:
        i = snapshot.agent_ids.index(agent_id)
    except ValueError as exc:
        raise CmvParticleError("agent is not in the particle world") from exc
    values = snapshot.values[i]
    weights = snapshot.conditional_weights[i]
    mean = np.average(values, axis=0, weights=weights)
    quantiles: dict[str, list[float]] = {}
    for name, field_index in STATE_INDEX.items():
        order = np.argsort(values[:, field_index])
        ordered_values = values[order, field_index]
        ordered_weights = weights[order]
        cumulative = np.cumsum(ordered_weights)
        quantiles[name] = [
            float(ordered_values[min(len(order) - 1, int(np.searchsorted(cumulative, q)))])
            for q in (0.1, 0.5, 0.9)
        ]
    probabilities = {
        "support": float(np.average(_stance_likelihood(values, "support"), weights=weights)),
        "oppose": float(np.average(_stance_likelihood(values, "oppose"), weights=weights)),
        "uncertain": float(np.average(_stance_likelihood(values, "uncertain"), weights=weights)),
    }
    probability_total = sum(probabilities.values())
    probabilities = {key: value / probability_total for key, value in probabilities.items()}
    belief_simplex = {
        "support": float(mean[STATE_INDEX["support"]]),
        "oppose": float(mean[STATE_INDEX["opposition"]]),
        "undecided": max(0.0, float(1-mean[STATE_INDEX["support"]]-mean[STATE_INDEX["opposition"]])),
    }
    return {
        "tick": snapshot.tick,
        "posterior_mean": {name: float(mean[index]) for name, index in STATE_INDEX.items()},
        "posterior_quantiles_10_50_90": quantiles,
        "action_stance_probability": probabilities,
        "belief_simplex": belief_simplex,
        "belief_interpretation": "Latent proposition belief mass; action_stance_probability is the legacy observation likelihood, not belief mass.",
        "kish_ess": float(1.0 / np.sum(weights**2)),
        "particle_count": int(len(weights)),
    }


def save_particle_snapshot(snapshot: CmvParticleSnapshot, path: str | Path) -> str:
    """Persist the complete particle state and return its content SHA-256."""
    snapshot.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as stream:
        np.savez_compressed(
            stream,
            agent_ids=np.asarray(snapshot.agent_ids, dtype=np.str_),
            values=snapshot.values.astype(np.float32),
            conditional_weights=snapshot.conditional_weights.astype(np.float64),
            design_weights=snapshot.design_weights.astype(np.float64),
            tick=np.asarray([snapshot.tick], dtype=np.int64),
            innovations=np.asarray([json_innovation_dump(row) for row in snapshot.innovations], dtype=np.str_),
            resampling_count=(snapshot.resampling_count if snapshot.resampling_count is not None else np.zeros(len(snapshot.agent_ids), dtype=np.int64)),
            state_fields=np.asarray(STATE_FIELDS, dtype=np.str_),
            model_id=np.asarray([PARTICLE_MODEL_ID], dtype=np.str_),
        )
        stream.flush()
    tmp.replace(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def load_particle_snapshot(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
) -> CmvParticleSnapshot:
    """Load and validate a persisted particle snapshot without pickle support."""
    path = Path(path)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise CmvParticleError("particle snapshot checksum does not match")
        with np.load(path, allow_pickle=False) as asset:
            model_id = str(asset["model_id"].tolist()[0])
            fields = tuple(str(value) for value in asset["state_fields"].tolist())
            if model_id != PARTICLE_MODEL_ID or fields != STATE_FIELDS:
                raise CmvParticleError("particle snapshot model contract does not match")
            agent_ids = tuple(str(value) for value in asset["agent_ids"].tolist())
            innovations = [
                set(str(value) for value in json.loads(encoded))
                for encoded in asset["innovations"].tolist()
            ]
            tick_values = asset["tick"].tolist()
            if len(tick_values) != 1:
                raise CmvParticleError("particle snapshot tick field is malformed")
            snapshot = CmvParticleSnapshot(
                agent_ids=agent_ids,
                values=asset["values"].astype(np.float64, copy=True),
                conditional_weights=asset["conditional_weights"].astype(np.float64, copy=True),
                design_weights=asset["design_weights"].astype(np.float64, copy=True),
                tick=int(tick_values[0]),
                innovations=innovations,
                resampling_count=asset["resampling_count"].astype(np.int64, copy=True),
            )
    except CmvParticleError:
        raise
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, EOFError) as exc:
        raise CmvParticleError("particle snapshot could not be decoded") from exc
    snapshot.validate()
    return snapshot


def json_innovation_dump(values: set[str]) -> str:
    import json
    return json.dumps(sorted(values), separators=(",", ":"))
