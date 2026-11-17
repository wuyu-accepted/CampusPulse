"""Fixed synthetic Reddit discussion-world compiler contracts."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import re
from typing import Any, Mapping, Sequence

from .particle_model import (
    CmvParticleSnapshot,
    STATE_FIELDS,
    initialize_fixed_particles,
    save_particle_snapshot,
)


WORLD_SCHEMA = "reddit-cmv-fixed-world-v1"
RELATIONSHIP_SCHEMA = "reddit-cmv-synthetic-relationship-release-v1"


class CmvWorldError(ValueError):
    """A fixed-world population or release violates the CMV contract."""


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _largest_remainder(capacities: Mapping[tuple[str, str], int], slots: int) -> dict[tuple[str, str], int]:
    total = sum(capacities.values())
    if slots <= 0 or total < slots:
        raise CmvWorldError("eligible population is smaller than the fixed world")
    exact = {key: slots * count / total for key, count in capacities.items()}
    allocation = {key: min(capacities[key], int(value)) for key, value in exact.items()}
    remaining = slots - sum(allocation.values())
    order = sorted(capacities, key=lambda key: (-(exact[key] - int(exact[key])), key))
    while remaining:
        progressed = False
        for key in order:
            if allocation[key] < capacities[key]:
                allocation[key] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
        if not progressed:
            raise CmvWorldError("stratified population allocation could not fill the fixed world")
    return allocation


def _positive_stratum_allocation(capacities: Mapping[tuple[str, str], int], slots: int) -> dict[tuple[str, str], int]:
    """Keep every observed stratum eligible for design-based inference."""
    if slots < len(capacities):
        raise CmvWorldError("too few slots to give every observed stratum positive inclusion probability")
    allocation = {key: 1 for key in capacities}
    residual = slots - len(capacities)
    if residual:
        available = {key: capacity - 1 for key, capacity in capacities.items() if capacity > 1}
        if sum(available.values()) < residual:
            raise CmvWorldError("stratified allocation exceeds author capacities")
        for key, count in _largest_remainder(available, residual).items():
            allocation[key] += count
    return allocation


def select_fixed_agents(
    candidates: Sequence[Mapping[str, Any]],
    *,
    population_size: int = 24,
    seed: int = 20260924,
    min_history: int = 12,
    min_threads: int = 4,
    min_span_days: float = 60.0,
) -> list[dict[str, Any]]:
    """Select distinct authors proportionally within semantic/activity strata.

    Candidate records are private and may contain author keys.  Returned rows
    preserve those keys only for private linking; callers must remove them from
    the public world artifact.
    """
    if population_size < 8:
        raise CmvWorldError("population is too small to support the configured relation layers")
    eligible: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        key = str(raw.get("author_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            history_count = int(raw.get("history_count", 0))
            thread_count = int(raw.get("thread_count", 0))
            span_days = float(raw.get("span_days", 0.0))
        except (TypeError, ValueError):
            continue
        if history_count < min_history or thread_count < min_threads or span_days < min_span_days:
            continue
        if bool(raw.get("is_bot", False)) or bool(raw.get("is_deleted", False)):
            continue
        cluster = str(raw.get("semantic_cluster") or "")
        activity_tier = str(raw.get("activity_tier") or "")
        if not cluster or not activity_tier:
            raise CmvWorldError("each eligible author needs a frozen semantic cluster and activity tier")
        row = dict(raw)
        row["author_key"] = key
        row["history_count"] = history_count
        row["thread_count"] = thread_count
        row["span_days"] = span_days
        row["stratum_id"] = f"{cluster}|{activity_tier}"
        eligible.append(row)
    if len(eligible) < population_size:
        raise CmvWorldError("qualified author pool cannot fill the fixed world without cloning")
    cluster_counts: dict[str, int] = defaultdict(int)
    for row in eligible:
        cluster_counts[str(row["semantic_cluster"])] += 1
    supported_clusters = {cluster for cluster, count in cluster_counts.items() if count >= 3}
    if len(supported_clusters) < 2:
        raise CmvWorldError("at least two semantic clusters with three qualified authors each are required")
    if len(supported_clusters) * 3 > population_size:
        raise CmvWorldError("reduce the fitted cluster count before selecting this smaller world size")
    eligible = [row for row in eligible if str(row["semantic_cluster"]) in supported_clusters]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        grouped[(str(row["semantic_cluster"]), str(row["activity_tier"]))].append(row)
    capacities_by_cluster: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    for stratum, rows in grouped.items():
        capacities_by_cluster[stratum[0]][stratum] = len(rows)
    cluster_capacity = {cluster: sum(strata.values()) for cluster, strata in capacities_by_cluster.items()}
    cluster_allocation = {cluster: 3 for cluster in cluster_capacity}
    remaining = population_size - sum(cluster_allocation.values())
    residual_capacities = {cluster: capacity - 3 for cluster, capacity in cluster_capacity.items()}
    if remaining:
        residual = _largest_remainder(residual_capacities, remaining)
        for cluster, amount in residual.items():
            cluster_allocation[cluster] += amount
    allocation: dict[tuple[str, str], int] = {}
    for cluster, strata in capacities_by_cluster.items():
        allocation.update(_positive_stratum_allocation(strata, cluster_allocation[cluster]))
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    for key in sorted(grouped):
        population = grouped[key]
        rng.shuffle(population)
        take = allocation[key]
        stratum_population = len(population)
        stratum_weight = stratum_population / (len(eligible) * take)
        for row in population[:take]:
            chosen = dict(row)
            chosen["stratum_population"] = stratum_population
            chosen["stratum_sample"] = take
            chosen["design_weight"] = stratum_weight
            chosen["qualified_author_pool_size"] = len(eligible)
            selected.append(chosen)
    selected.sort(key=lambda row: (str(row["stratum_id"]), str(row["author_key"])))
    total_weight = sum(float(row["design_weight"]) for row in selected)
    if abs(total_weight - 1.0) > 1e-9:
        raise CmvWorldError("stratified design weights do not sum to one")
    for index, row in enumerate(selected, 1):
        row["agent_id"] = f"CMV-A{index:02d}"
    return selected


def _interleave_by_cluster(agents: Sequence[Mapping[str, Any]], seed: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in agents:
        grouped[str(raw.get("semantic_cluster") or "")].append(dict(raw))
    rng = random.Random(seed)
    for rows in grouped.values():
        rng.shuffle(rows)
    cluster_ids = sorted(grouped)
    rng.shuffle(cluster_ids)
    order: list[dict[str, Any]] = []
    while any(grouped.values()):
        for cluster in cluster_ids:
            if grouped[cluster]:
                order.append(grouped[cluster].pop())
    return order


def build_synthetic_relationship_release(
    agents: Sequence[Mapping[str, Any]],
    *,
    seed: int = 20260924,
) -> dict[str, Any]:
    """Build three fixed degree-two synthetic contact layers, disjoint by edge."""
    ids = [str(row.get("agent_id") or "") for row in agents]
    if len(set(ids)) != len(ids) or any(not value for value in ids) or len(ids) < 8:
        raise CmvWorldError("relationship world requires at least eight unique synthetic agents")
    clusters = {str(row.get("agent_id")): str(row.get("semantic_cluster") or "") for row in agents}
    if not all(clusters.values()) or len(set(clusters.values())) < 2:
        raise CmvWorldError("cross-cluster contacts require at least two semantic groups")
    rng = random.Random(seed)
    order = _interleave_by_cluster(agents, seed)
    n = len(order)
    if n % 2:
        raise CmvWorldError("degree-two close-friend layer requires an even population")
    edges: list[dict[str, Any]] = []
    used_pairs: set[frozenset[str]] = set()
    outgoing: dict[tuple[str, str], int] = {(agent_id, layer): 0 for agent_id in ids for layer in ("familiar", "cross_group")}
    incoming_cross: dict[str, int] = {agent_id: 0 for agent_id in ids}

    # Close-friend ties form a symmetric ring.  Ring ordering is interleaved by
    # semantic group, but the edge provenance remains explicitly synthetic.
    for index, row in enumerate(order):
        other = order[(index + 1) % n]
        left_id, right_id = str(row["agent_id"]), str(other["agent_id"])
        pair = frozenset((left_id, right_id))
        if pair in used_pairs:
            continue
        used_pairs.add(pair)
        edges.append({"agent_a": left_id, "agent_b": right_id, "relationship_type": "strong", "directed": False, "strength": 0.85, "provenance": "synthetic_community_assumption"})

    # Familiar and bridge contacts have two explicit outgoing slots per agent.
    # This lets every node have the requested audience even when cluster sizes
    # are unequal; it does not claim reciprocal real-world friendship.
    by_id = {str(row["agent_id"]): row for row in agents}
    for source_id in sorted(ids):
        source_cluster = clusters[source_id]
        familiar_candidates = [
            target_id for target_id in ids
            if target_id != source_id
            and clusters[target_id] == source_cluster
            and frozenset((source_id, target_id)) not in used_pairs
        ]
        fallback_candidates = [
            target_id for target_id in ids
            if target_id != source_id
            and target_id not in familiar_candidates
            and frozenset((source_id, target_id)) not in used_pairs
        ]
        rng.shuffle(familiar_candidates)
        rng.shuffle(fallback_candidates)
        candidates = familiar_candidates + fallback_candidates
        chosen = candidates[:2]
        if len(chosen) != 2:
            raise CmvWorldError("fixed population cannot supply two familiar contacts per agent")
        for target_id in chosen:
            used_pairs.add(frozenset((source_id, target_id)))
            edges.append({"agent_a": source_id, "agent_b": target_id, "relationship_type": "familiar", "directed": True, "strength": 0.55, "provenance": "synthetic_community_assumption"})
            outgoing[(source_id, "familiar")] += 1

    for source_id in sorted(ids):
        source_cluster = clusters[source_id]
        bridge_candidates = [
            target_id for target_id in ids
            if target_id != source_id
            and clusters[target_id] != source_cluster
            and frozenset((source_id, target_id)) not in used_pairs
        ]
        bridge_candidates.sort(key=lambda target_id: (incoming_cross[target_id], target_id))
        if len(bridge_candidates) < 2:
            raise CmvWorldError("fixed population cannot supply two cross-group contacts per agent")
        # Rotate stable ties by seed without changing the objective or weights.
        rng.shuffle(bridge_candidates)
        bridge_candidates.sort(key=lambda target_id: incoming_cross[target_id])
        for target_id in bridge_candidates[:2]:
            used_pairs.add(frozenset((source_id, target_id)))
            edges.append({"agent_a": source_id, "agent_b": target_id, "relationship_type": "cross_group", "directed": True, "strength": 0.30, "provenance": "synthetic_community_assumption"})
            outgoing[(source_id, "cross_group")] += 1
            incoming_cross[target_id] += 1

    degree: dict[str, int] = {agent_id: 0 for agent_id in ids}
    for edge in edges:
        if edge["relationship_type"] == "strong":
            degree[edge["agent_a"]] += 1
            degree[edge["agent_b"]] += 1
    if any(value != 2 for value in degree.values()) or any(value != 2 for value in outgoing.values()):
        raise CmvWorldError("synthetic contact graph did not meet exact relationship degrees")
    body = {
        "schema_version": RELATIONSHIP_SCHEMA,
        "world_agent_ids": sorted(ids),
        "edge_count": len(edges),
        "edges": sorted(edges, key=lambda row: (row["relationship_type"], row["agent_a"], row["agent_b"])),
        "layer_degree": {"strong_undirected": 2, "familiar_outgoing": 2, "cross_group_outgoing": 2},
        "seed": int(seed),
        "meaning": "synthetic contact assumptions; not observed friendships",
    }
    return {**body, "release_sha256": _canonical_hash(body)}


def build_world_release(
    selected_agents: Sequence[Mapping[str, Any]],
    *,
    world_seed: int = 20260924,
    particle_asset_path: str | Path,
) -> tuple[dict[str, Any], CmvParticleSnapshot]:
    """Create a profile-bound synthetic world and its complete initial particles."""
    if len(selected_agents) != 24:
        raise CmvWorldError("the initial compiled Reddit world is frozen at 24 agents")
    public_agents: list[dict[str, Any]] = []
    for row in selected_agents:
        persona = " ".join(str(row.get("persona_text") or "").split())
        if not 150 <= len(persona.split()) <= 300:
            raise CmvWorldError("each natural-language persona must contain 150-300 words")
        evidence_hashes = row.get("evidence_fingerprints")
        if not isinstance(evidence_hashes, (list, tuple)) or len(evidence_hashes) < 3:
            raise CmvWorldError("each persona needs at least three private evidence fingerprints")
        if any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in evidence_hashes):
            raise CmvWorldError("persona evidence fingerprints are invalid")
        agent = {
            "agent_id": str(row["agent_id"]),
            "display_name": str(row.get("display_name") or row["agent_id"]),
            "semantic_cluster": str(row["semantic_cluster"]),
            "activity_tier": str(row["activity_tier"]),
            "design_weight": float(row["design_weight"]),
            "persona_text": persona,
            "stable_profile": dict(row.get("stable_profile") or {}),
            "topic_portfolio": list(row.get("topic_portfolio") or []),
            "history_focus": str(row.get("history_focus") or ""),
            "evidence_fingerprints": sorted(set(evidence_hashes)),
            "features": {
                "activity_score": float(row.get("activity_score", 0.5)),
                "public_voice_ratio": float(row.get("public_voice_ratio", 0.5)),
                "history_reply_share": float(row.get("reply_ratio", 0.5)),
                "history_question_share": float(row.get("question_ratio", 0.0)),
            },
            "feature_sources": {
                "activity_score": "bounded_history_volume_proxy",
                "public_voice_ratio": "unidentified_speech_probability_prior",
                "history_reply_share": "observed_public_message_type",
                "history_question_share": "observed_history_question_fraction",
            },
        }
        provider_profile = row.get("forum_agent_profile")
        if provider_profile is not None:
            if not isinstance(provider_profile, Mapping) or provider_profile.get("agent_id") != agent["agent_id"]:
                raise CmvWorldError("ForumTwin profile is not bound to its world agent")
            if provider_profile.get("evidence_binding") != "reviewed_evidence_match":
                raise CmvWorldError("ForumTwin profile evidence was not reviewed")
            agent["provider_profile_bundle"] = dict(provider_profile)
        public_agents.append(agent)
    if len({row["agent_id"] for row in public_agents}) != 24:
        raise CmvWorldError("world agent IDs must be unique")
    design_sum = sum(row["design_weight"] for row in public_agents)
    if abs(design_sum - 1.0) > 1e-9:
        raise CmvWorldError("world design weights must sum to one")
    relationships = build_synthetic_relationship_release(public_agents, seed=world_seed)
    snapshot = initialize_fixed_particles(public_agents, world_seed=world_seed, particles_per_agent=10)
    particle_sha256 = save_particle_snapshot(snapshot, particle_asset_path)
    body = {
        "schema_version": WORLD_SCHEMA,
        "community": "changemyview",
        "language": "en",
        "population_size": 24,
        "world_seed": int(world_seed),
        "agents": sorted(public_agents, key=lambda row: row["agent_id"]),
        "relationships": relationships,
        "particle_model_id": "cmv-structural-particle-model-v1",
        "particle_shape": [24, 10, 12],
        "particle_fields": ["attention", "concern", "trust", "satisfaction", "support", "opposition", "activity", "voice", "service_strain", "rumor_belief", "verified_belief", "evidence_acceptance"],
        "particle_asset_sha256": particle_sha256,
        "particle_asset_private": True,
        "social_edge_provenance": "synthetic_community_assumption",
    }
    return {**body, "world_sha256": _canonical_hash(body)}, snapshot


def verify_fixed_world_release(
    world_path: str | Path,
    particle_path: str | Path,
) -> dict[str, Any]:
    """Verify a compiled world and its private particle asset as one release.

    The report intentionally contains aggregate counts only. It never emits
    persona text, evidence fingerprints, profile contents, or record-level IDs.
    """
    import numpy as np

    world_path = Path(world_path)
    particle_path = Path(particle_path)
    try:
        world = json.loads(world_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CmvWorldError("fixed world release could not be read") from exc
    if not isinstance(world, Mapping):
        raise CmvWorldError("fixed world release must be a JSON object")
    body = {key: value for key, value in world.items() if key != "world_sha256"}
    if world.get("schema_version") != WORLD_SCHEMA or _canonical_hash(body) != world.get("world_sha256"):
        raise CmvWorldError("fixed world schema or content hash is invalid")

    agents = world.get("agents")
    if not isinstance(agents, list) or len(agents) != 24:
        raise CmvWorldError("fixed world must contain exactly 24 agents")
    agent_ids = [str(agent.get("agent_id") or "") for agent in agents if isinstance(agent, Mapping)]
    if len(agent_ids) != 24 or any(not value for value in agent_ids) or len(set(agent_ids)) != 24:
        raise CmvWorldError("fixed world agent identities are missing or duplicated")
    if agent_ids != sorted(agent_ids):
        raise CmvWorldError("fixed world agent order is not canonical")

    design_weights: list[float] = []
    persona_texts: list[str] = []
    profile_hashes: list[str] = []
    evidence_counts: list[int] = []
    for agent in agents:
        if not isinstance(agent, Mapping):
            raise CmvWorldError("fixed world contains an invalid agent record")
        try:
            design_weights.append(float(agent["design_weight"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CmvWorldError("fixed world has an invalid design weight") from exc
        persona = " ".join(str(agent.get("persona_text") or "").split())
        if not 150 <= len(persona.split()) <= 300:
            raise CmvWorldError("fixed world contains an incomplete natural-language persona")
        persona_texts.append(persona)
        fingerprints = agent.get("evidence_fingerprints")
        if not isinstance(fingerprints, list) or len(set(fingerprints)) < 3 or any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in fingerprints
        ):
            raise CmvWorldError("fixed world evidence binding is invalid")
        evidence_counts.append(len(set(fingerprints)))
        profile = agent.get("provider_profile_bundle")
        if not isinstance(profile, Mapping) or profile.get("agent_id") != agent.get("agent_id"):
            raise CmvWorldError("fixed world ForumTwin profile is not bound to its agent")
        if profile.get("evidence_binding") != "reviewed_evidence_match":
            raise CmvWorldError("fixed world ForumTwin profile lacks reviewed evidence binding")
        profile_hash = str(profile.get("profile_prompt_sha256") or "")
        if re.fullmatch(r"[0-9a-f]{64}", profile_hash) is None:
            raise CmvWorldError("fixed world ForumTwin profile fingerprint is invalid")
        profile_hashes.append(profile_hash)

    if any(not math.isfinite(weight) or weight <= 0 for weight in design_weights) or not math.isclose(
        sum(design_weights), 1.0, abs_tol=1e-9
    ):
        raise CmvWorldError("fixed world design weights are invalid")

    relationships = world.get("relationships")
    if not isinstance(relationships, Mapping):
        raise CmvWorldError("fixed world relationship release is missing")
    relationship_body = {
        key: value for key, value in relationships.items() if key != "release_sha256"
    }
    if (
        relationships.get("schema_version") != RELATIONSHIP_SCHEMA
        or _canonical_hash(relationship_body) != relationships.get("release_sha256")
        or relationships.get("world_agent_ids") != sorted(agent_ids)
    ):
        raise CmvWorldError("fixed world relationship release is invalid")
    edges = relationships.get("edges")
    if not isinstance(edges, list) or len(edges) != 120 or relationships.get("edge_count") != 120:
        raise CmvWorldError("fixed world relationship edge count is invalid")
    pairs: set[frozenset[str]] = set()
    strong_degree = {agent_id: 0 for agent_id in agent_ids}
    directed_degree: dict[tuple[str, str], int] = defaultdict(int)
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise CmvWorldError("fixed world contains an invalid relationship edge")
        left, right = str(edge.get("agent_a") or ""), str(edge.get("agent_b") or "")
        kind = str(edge.get("relationship_type") or "")
        pair = frozenset((left, right))
        if left not in strong_degree or right not in strong_degree or left == right or pair in pairs:
            raise CmvWorldError("fixed world relationship edge is duplicated or out of range")
        pairs.add(pair)
        if edge.get("provenance") != "synthetic_community_assumption":
            raise CmvWorldError("fixed world relationship provenance is invalid")
        if kind == "strong" and edge.get("directed") is False:
            strong_degree[left] += 1
            strong_degree[right] += 1
        elif kind in {"familiar", "cross_group"} and edge.get("directed") is True:
            directed_degree[(left, kind)] += 1
        else:
            raise CmvWorldError("fixed world relationship layer is invalid")
    if any(value != 2 for value in strong_degree.values()) or any(
        directed_degree[(agent_id, kind)] != 2
        for agent_id in agent_ids
        for kind in ("familiar", "cross_group")
    ):
        raise CmvWorldError("fixed world relationship degree contract failed")

    try:
        particle_bytes = particle_path.read_bytes()
        particle_sha256 = hashlib.sha256(particle_bytes).hexdigest()
        if particle_sha256 != world.get("particle_asset_sha256"):
            raise CmvWorldError("fixed world particle asset hash does not match")
        with np.load(particle_path, allow_pickle=False) as asset:
            particle_ids = tuple(str(value) for value in asset["agent_ids"].tolist())
            values = asset["values"]
            conditional_weights = asset["conditional_weights"]
            particle_design_weights = asset["design_weights"]
            state_fields = tuple(str(value) for value in asset["state_fields"].tolist())
            model_id = str(asset["model_id"].tolist()[0])
    except CmvWorldError:
        raise
    except (OSError, ValueError, KeyError, EOFError) as exc:
        raise CmvWorldError("fixed world particle asset is invalid") from exc
    if particle_ids != tuple(agent_ids):
        raise CmvWorldError("particle ordering does not match fixed-world agents")
    if values.shape != (24, 10, len(STATE_FIELDS)) or tuple(world.get("particle_shape") or ()) != values.shape:
        raise CmvWorldError("particle state shape does not match the fixed-world contract")
    if state_fields != STATE_FIELDS or model_id != "cmv-structural-particle-model-v1":
        raise CmvWorldError("particle state fields or model contract are invalid")
    if not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1):
        raise CmvWorldError("particle state contains non-finite or out-of-range values")
    if conditional_weights.shape != (24, 10) or not np.isfinite(conditional_weights).all() or not np.allclose(
        conditional_weights.sum(axis=1), 1.0, atol=1e-10
    ):
        raise CmvWorldError("conditional particle weights are invalid")
    if particle_design_weights.shape != (24,) or not np.allclose(
        particle_design_weights, np.asarray(design_weights) / sum(design_weights), atol=1e-10
    ):
        raise CmvWorldError("particle design weights do not align with the fixed world")
    return {
        "verified": True,
        "agents": 24,
        "persona_bundles": len(set(profile_hashes)),
        "distinct_personas": len(set(persona_texts)),
        "minimum_evidence_fingerprints": min(evidence_counts),
        "relationship_edges": len(edges),
        "particle_count": int(values.shape[0] * values.shape[1]),
        "particle_shape": list(values.shape),
        "world_sha256": world["world_sha256"],
        "particle_sha256": particle_sha256,
    }
