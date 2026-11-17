"""Evidence-bounded four-dimension evaluation for a completed CMV run.

This module computes descriptive metrics from the committed forum and the
evaluation-only continuation. It never generates labels or imputes missing
denominators. Human persona review is an explicit optional input.
"""

from __future__ import annotations

from collections import Counter
import math
import re
import hashlib
import unicodedata
import numpy as np
from statistics import median
from typing import Any, Mapping, Sequence

from ..forum_twin import ForumLedger


CMV_EVALUATION_SCHEMA = "reddit-cmv-four-dimension-evaluation-v1"
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_REVIEW_FIELDS = {"agent_id", "profile_fidelity", "history_context_fit", "voice_distinctiveness"}


class CmvEvaluationError(ValueError):
    """An evaluation input is malformed or cannot be paired to the run."""


def independent_state_chain_metrics(
    records: Sequence[Mapping[str, Any]], review_release: Mapping[str, Any],
) -> dict[str, Any]:
    """Score frozen pre-decision forecasts against a separate blinded review.

    Post-decision alignment describes tracking, never predictive accuracy.
    Labels used by the filter are categorically excluded from this evaluator.
    """
    required_blinding = {"profile", "particle_state", "self_reported_stance", "condition"}
    if (review_release.get("role") != "independent_expression_review"
        or review_release.get("used_for_assimilation") is not False
        or not required_blinding.issubset(set(review_release.get("blinded_to", [])))):
        raise CmvEvaluationError("final review must be independent and blinded")
    reviews = review_release.get("records")
    if not isinstance(reviews, list) or not records:
        raise CmvEvaluationError("a nonempty frozen panel and review are required")
    by_id = {row["case_id"]: row for row in records}
    labels = {row["case_id"]: row for row in reviews}
    if len(by_id) != len(records) or len(labels) != len(reviews) or set(by_id) != set(labels):
        raise CmvEvaluationError("review must cover the exact panel once, including insufficient evidence")
    classes = ("support", "oppose", "neutral_or_mixed")
    for row in reviews:
        if row.get("expression_stance") not in (*classes, "insufficient_evidence"):
            raise CmvEvaluationError("unknown independent expression label")

    def validate_probabilities(value):
        if (not isinstance(value, Mapping) or set(value) != set(classes)
            or any(not math.isfinite(float(v)) or not -1e-12 <= float(v) <= 1 + 1e-12 for v in value.values())
            or abs(sum(float(v) for v in value.values())-1) > 1e-8):
            raise CmvEvaluationError("forecast probabilities must be a complete simplex")
        # A valid sum of particle weights can be 1+2e-16. Remove only
        # floating-point boundary noise, not meaningful invalid probabilities.
        clipped = {key: min(1.0, max(0.0, float(value[key]))) for key in classes}
        total = sum(clipped.values())
        return {key: clipped[key] / total for key in classes}

    def classify(prob):
        # Fixed ties: neutral first, then support, then oppose.
        return max(("neutral_or_mixed", "support", "oppose"), key=lambda key: prob[key])

    output = {"label_source": "independent_blinded_review", "conditions": {}}
    for condition in sorted({str(row["condition"]) for row in records}):
        panel = [row for row in records if row["condition"] == condition]
        history = Counter()
        scored = []
        for tick in sorted({row["tick"] for row in panel}):
            # Freeze the reference before examining any label at this tick.
            reference = {key: (history[key]+1)/(sum(history.values())+3) for key in classes}
            for row in sorted((r for r in panel if r["tick"] == tick), key=lambda r: r["case_id"]):
                prior = validate_probabilities(row["pre_decision_stance_probabilities"])
                post = validate_probabilities(row["post_decision_stance_probabilities"])
                target = labels[row["case_id"]]["expression_stance"]
                if target == "insufficient_evidence":
                    continue
                scored.append({"target": target, "prior": prior, "post": post, "reference": reference})
            history.update(labels[row["case_id"]]["expression_stance"] for row in panel
                           if row["tick"] == tick and labels[row["case_id"]]["expression_stance"] in classes)
        n = len(scored)
        present = [key for key in classes if any(row["target"] == key for row in scored)]
        recalls = {key: sum(classify(r["prior"]) == key for r in scored if r["target"] == key)
                   / sum(r["target"] == key for r in scored) for key in present}
        def accuracy(field):
            return sum(classify(r[field]) == r["target"] for r in scored)/n if n else None
        def brier(field):
            return sum(sum((r[field][key] - float(r["target"] == key))**2 for key in classes)
                       for r in scored)/n if n else None
        bins = []
        for index in range(5):
            subset = [r for r in scored if min(4, int(max(r["prior"].values())*5)) == index]
            bins.append({"lower": index/5, "upper": (index+1)/5, "n": len(subset),
                         "mean_confidence": sum(max(r["prior"].values()) for r in subset)/len(subset) if subset else None,
                         "accuracy": sum(classify(r["prior"]) == r["target"] for r in subset)/len(subset) if subset else None})
        output["conditions"][condition] = {
            "panel_n": len(panel), "reviewable_n": n, "coverage": n/len(panel),
            "label_counts": dict(Counter(labels[r["case_id"]]["expression_stance"] for r in panel)),
            "pre_decision_accuracy": accuracy("prior"), "pre_decision_multiclass_brier": brier("prior"),
            "balanced_accuracy_observed_classes": sum(recalls.values())/len(recalls) if recalls else None,
            "observed_class_count": len(present), "class_recalls": recalls,
            "past_only_majority_accuracy": accuracy("reference"), "past_only_majority_brier": brier("reference"),
            "post_decision_tracking_accuracy": accuracy("post"), "calibration_bins": bins,
        }
    return output


def population_state_metrics(snapshot: Any, world: Mapping[str, Any], *, seed: int = 20260722) -> dict[str, Any]:
    """Paper operators on parent-Agent expectations, never on pooled particles."""
    from .particle_model import STATE_FIELDS
    snapshot.validate()
    weights = snapshot.conditional_weights / snapshot.conditional_weights.sum(axis=1, keepdims=True)
    matrix = np.sum(snapshot.values * weights[:, :, None], axis=1)
    if not np.all(np.isfinite(matrix)):
        raise CmvEvaluationError("non-finite parent state")
    columns = {name: matrix[:, i] for i, name in enumerate(STATE_FIELDS)}
    net = columns["support"] - columns["opposition"]

    def distribution(values: np.ndarray) -> dict[str, Any]:
        mean, sd = float(values.mean()), float(values.std(ddof=0))
        result = {"mean": mean, "standard_deviation": sd, "BC": None, "KDE_peaks": None, "entropy_20_bins": None}
        if sd**2 < 1e-10:
            return result
        z = (values-mean)/sd
        result["BC"] = float((float(np.mean(z**3))**2+1)/np.mean(z**4))
        counts, _ = np.histogram(values, bins=np.linspace(-1, 1, 21))
        probs = counts[counts > 0]/len(values)
        result["entropy_20_bins"] = float(-np.sum(probs*np.log(probs)))
        bandwidth = sd * len(values)**(-1/5)
        grid = np.linspace(float(values.min())-3*bandwidth, float(values.max())+3*bandwidth, 512)
        density = np.exp(-0.5*((grid[:, None]-values[None, :])/bandwidth)**2).mean(axis=1)
        candidates = np.flatnonzero((density[1:-1] > density[:-2]) & (density[1:-1] > density[2:]))+1
        # Prominence is height above the higher adjacent basin, not peak height.
        peaks = []
        for index in candidates:
            left, right = index, index
            while left > 0 and density[left-1] <= density[index]:
                left -= 1
            while right < len(density)-1 and density[right+1] <= density[index]:
                right += 1
            prominence = density[index]-max(float(density[left:index+1].min()), float(density[index:right+1].min()))
            if prominence >= density.max()*0.05:
                peaks.append({"position": float(grid[index]), "relative_prominence": float(prominence/density.max())})
        result["KDE_peaks"] = len(peaks)
        result["KDE_peak_details"] = peaks
        result["KDE_definition"] = "512 grid points; population SD*n^(-1/5) bandwidth; prominence >= 5% maximum density"
        result["KDE_bandwidth"] = bandwidth
        return result

    from ..forum_fidelity_v2 import _effective_rank
    covariance = np.cov(matrix, rowvar=False, ddof=0)
    rank = _effective_rank(covariance) if np.any(np.diag(covariance) > 1e-10) else None
    by_id = {row["agent_id"]: row for row in world["agents"]}
    clusters = np.array([by_id[agent_id]["semantic_cluster"] for agent_id in snapshot.agent_ids])
    total = float(np.sum((net-net.mean())**2))

    def r_squared(labels: np.ndarray) -> float:
        return sum(float(np.sum(labels == group))*(float(net[labels == group].mean())-float(net.mean()))**2
                   for group in np.unique(labels))/total

    r2, p = None, None
    if total/len(net) >= 1e-10:
        r2 = r_squared(clusters)
        rng = np.random.default_rng(seed)
        p = (1+sum(r_squared(rng.permutation(clusters)) >= r2-1e-12 for _ in range(199)))/200
    return {"parent_agent_count": len(matrix), "particle_count": int(snapshot.values.shape[0]*snapshot.values.shape[1]),
            "aggregation": "normalize particle weights within parent, then equal-weight parent Agents",
            "effective_rank_12d": rank, "net_stance": distribution(net),
            "continuous_states": {name: distribution(columns[name]) for name in ("trust", "satisfaction", "attention", "concern")},
            "semantic_cluster_R2": r2, "semantic_cluster_permutation_p": p,
            "interest_group_R2": None, "interest_group_status": "scenario interest-group labels not supplied"}


def deduplicated_style_metrics(ledger: ForumLedger, *, scenario_id: str, branch: str) -> dict[str, Any]:
    texts: dict[str, list[str]] = {}
    removed = 0
    for message in ledger.messages.values():
        if (ledger.threads[message.thread_id].scenario_id != scenario_id
                or ledger.threads[message.thread_id].branch not in {branch, "shared_baseline"}
                or message.provenance.kind == "external_event_seed"
                or message.source_agent_display_id.startswith("governance:") or message.superseded):
            continue
        text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", message.visible_text).lower())
        if not text:
            continue
        bucket = texts.setdefault(message.source_agent_display_id, [])
        if text in bucket:
            removed += 1
        else:
            bucket.append(text)

    def feature(text: str) -> np.ndarray:
        x = np.zeros(4096)
        for width in (1, 2, 3):
            for i in range(len(text)-width+1):
                index = int.from_bytes(hashlib.sha256(text[i:i+width].encode()).digest()[:4], "big") % len(x)
                x[index] += 1
        norm = np.linalg.norm(x)
        return x/norm if norm else x

    scores = []
    vectors_by_agent = {identity:[feature(text) for text in bucket] for identity,bucket in texts.items()}
    for identity, bucket in texts.items():
        if len(bucket) >= 2:
            vectors = vectors_by_agent[identity]
            scores.append(float(np.mean([np.dot(a, b) for i, a in enumerate(vectors) for b in vectors[i+1:]])))
    identities = sorted(vectors_by_agent)
    between = [float(np.mean([np.dot(a,b) for a in vectors_by_agent[left] for b in vectors_by_agent[right]]))
               for index,left in enumerate(identities) for right in identities[index+1:]]
    return {"eligible_agents": len(scores), "duplicates_removed": removed,
            "mean_within_agent_cosine": float(np.mean(scores)) if len(scores) >= 10 else None,
            "mean_between_agent_cosine": float(np.mean(between)) if len(scores) >= 10 and between else None,
            "within_minus_between_cosine": float(np.mean(scores)-np.mean(between)) if len(scores) >= 10 and between else None,
            "status": "computed" if len(scores) >= 10 else "not_applicable_fewer_than_10_agents",
            "feature_definition": "NFKC/lowercase/remove whitespace; SHA256 4096-bucket character 1-3grams"}


def _safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    value = float(numerator) / float(denominator)
    if not math.isfinite(value):
        raise CmvEvaluationError("evaluation produced a non-finite rate")
    return round(value, 6)


def _hhi(counts: Mapping[str, int]) -> float | None:
    total = sum(counts.values())
    if total <= 0:
        return None
    return round(sum((count / total) ** 2 for count in counts.values()), 6)


def _depths_from_edges(rows: Sequence[Mapping[str, Any]], *, id_key: str, parent_key: str) -> list[int]:
    parents = {
        str(row[id_key]): (str(row[parent_key]) if row.get(parent_key) else None)
        for row in rows
    }
    if len(parents) != len(rows) or "" in parents:
        raise CmvEvaluationError("reply graph contains duplicate or empty message IDs")
    depths: dict[str, int] = {}
    for message_id in parents:
        path: list[str] = []
        seen: set[str] = set()
        current: str | None = message_id
        while current is not None and current in parents and current not in depths:
            if current in seen:
                raise CmvEvaluationError("reply graph contains a cycle")
            seen.add(current)
            path.append(current)
            current = parents[current]
        base = depths.get(current, -1) if current is not None else -1
        for value in reversed(path):
            base += 1
            depths[value] = base
    return [depths[message_id] for message_id in parents]


def _depth_histogram(depths: Sequence[int]) -> dict[str, float] | None:
    if not depths:
        return None
    buckets = Counter("3+" if depth >= 3 else str(depth) for depth in depths)
    return {key: round(buckets.get(key, 0) / len(depths), 6) for key in ("0", "1", "2", "3+")}


def _text_shape(texts: Sequence[str]) -> dict[str, float | int | None]:
    lengths = [len(_WORD.findall(text)) for text in texts]
    return {
        "message_count": len(texts),
        "median_word_count": round(float(median(lengths)), 3) if lengths else None,
    }


def _reference_forum_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    required = {"source_id_private", "author_key_private", "parent_source_id_private", "text_private"}
    if any(not isinstance(row, Mapping) or required - set(row) for row in rows):
        raise CmvEvaluationError("evaluation continuation rows do not match the private target schema")
    graph_rows = [
        {
            "message_id": str(row["source_id_private"]),
            "parent_id": row.get("parent_source_id_private"),
        }
        for row in rows
    ]
    depths = _depths_from_edges(graph_rows, id_key="message_id", parent_key="parent_id")
    authors = Counter(str(row["author_key_private"]) for row in rows if row.get("author_key_private"))
    if not authors:
        authors = Counter()
    return {
        "messages": len(rows),
        "unique_authors": len(authors),
        "participant_hhi": _hhi(authors),
        "reply_depth_histogram": _depth_histogram(depths),
        **_text_shape([str(row["text_private"]) for row in rows]),
        "source": "held_out_observed_continuation",
    }


def _branch_forum_summary(ledger: ForumLedger, *, scenario_id: str, branch: str) -> dict[str, Any]:
    threads = {
        thread_id: thread
        for thread_id, thread in ledger.threads.items()
        if thread.scenario_id == scenario_id and thread.branch in {branch, "shared_baseline"}
    }
    messages = [
        message
        for message in ledger.messages.values()
        if message.thread_id in threads and not message.superseded
        and message.provenance.kind != "external_event_seed"
    ]
    if any(message.provenance.kind not in {"authorized_live_llm", "reviewed_trace_replay"} for message in messages):
        raise CmvEvaluationError("forum evaluation contains non-publishable message provenance")
    graph_messages = [message for message in ledger.messages.values() if message.thread_id in threads and not message.superseded]
    graph_depths = _depths_from_edges(
        [
            {"message_id": message.message_id, "parent_id": message.parent_message_id}
            for message in graph_messages
        ],
        id_key="message_id",
        parent_key="parent_id",
    )
    depths = [depth for message, depth in zip(graph_messages, graph_depths) if message.provenance.kind != "external_event_seed"]
    authors = Counter(message.source_agent_display_id for message in messages if not message.source_agent_display_id.startswith("governance:"))
    text_list = [message.visible_text for message in messages if message.visible_text]
    exact_repeated = len(text_list) - len(set(" ".join(text.split()).casefold() for text in text_list))
    interactions = [
        event for event in ledger.interactions.values()
        if event.scenario_id == scenario_id and event.branch in {branch, "shared_baseline"}
    ]
    conversation_by_id = ledger.private_conversations
    private_messages = [
        message for message in ledger.private_messages.values()
        if message.conversation_id in conversation_by_id
        and conversation_by_id[message.conversation_id].scenario_id == scenario_id
        and conversation_by_id[message.conversation_id].branch in {branch, "shared_baseline"}
    ]
    claims = [claim for claim in ledger.claims.values() if claim.thread_id in threads]
    help_threads = {message.thread_id for message in messages if message.action == "seek_help"}
    serviced_threads = {message.thread_id for message in messages if message.action == "service_receipt"}
    corrected = sum(claim.status in {"corrected", "verified"} for claim in claims)
    unsupported = sum(claim.status in {"unverified", "contested"} for claim in claims)
    conversations = [
        row for row in ledger.private_conversations.values()
        if row.scenario_id == scenario_id and row.branch in {branch, "shared_baseline"}
    ]
    action_counts = Counter(message.action for message in messages)
    stance_counts = Counter(message.stance for message in messages if not message.source_agent_display_id.startswith("governance:"))
    governance_messages = [message for message in messages if message.action == "governance_message"]
    governance_ids = {message.message_id for message in governance_messages}
    last_tick = max((message.created_tick for message in graph_messages), default=0)
    due_ticks = [
        message.governance_artifact.due_tick
        for message in governance_messages
        if message.governance_artifact is not None
    ]
    report = {
        "threads": len(threads),
        "public_messages": len(messages),
        "external_event_roots": sum(message.provenance.kind == "external_event_seed" for message in ledger.messages.values() if message.thread_id in threads),
        "unique_resident_speakers": len(authors),
        "participant_hhi": _hhi(authors),
        "reply_depth_histogram": _depth_histogram(depths),
        **_text_shape(text_list),
        "exact_repeat_messages": exact_repeated,
        "action_counts": dict(sorted(action_counts.items())),
        "resident_stance_counts": dict(sorted(stance_counts.items())),
        "governance_messages": len(governance_messages),
        "governance_artifacts_due_by_last_message": sum(tick <= last_tick for tick in due_ticks),
        "governance_artifacts_with_followup_tick": sum(tick < last_tick for tick in due_ticks),
        "direct_resident_replies_to_governance": sum(
            message.source_agent_display_id not in {"external-event-author"}
            and not message.source_agent_display_id.startswith("governance:")
            and (message.parent_message_id in governance_ids or message.quote_message_id in governance_ids)
            for message in messages
        ),
        "interaction_counts": dict(sorted(Counter(row.kind for row in interactions).items())),
        "private_messages": len(private_messages),
        "active_private_groups": sum(row.conversation_type == "group" and row.status == "active" for row in conversations),
        "private_to_public_retellings": sum(message.private_hearsay_origin for message in messages),
        "claims": len(claims),
        "unsupported_or_contested_claims": unsupported,  # Legacy alias: unresolved, not false.
        "corrected_or_verified_claims": corrected,
        "unsupported_or_contested_share": _safe_rate(unsupported, len(claims)),
        "unverified_claim_share": _safe_rate(sum(claim.status == "unverified" for claim in claims), len(claims)),
        "contested_claim_share": _safe_rate(sum(claim.status == "contested" for claim in claims), len(claims)),
        "unsupported_factual_claim_share": None,
        "claim_metric_interpretation": "Claim status records verification/disagreement, not factual falsity; factual support requires a separate content review.",
        "correction_share": _safe_rate(corrected, len(claims)),
        "help_requests": len(help_threads),
        "service_receipts": len(help_threads & serviced_threads),
        "help_receipt_ratio": _safe_rate(len(help_threads & serviced_threads), len(help_threads)),
        "source": "committed_simulation_ledger",
    }
    return report


def _activation_rates(state: Any, world: Mapping[str, Any]) -> dict[str, Any]:
    cluster_by_agent = {
        str(agent["agent_id"]): str(agent.get("semantic_cluster") or "unclassified")
        for agent in world["agents"]
    }
    activations: Counter[str] = Counter()
    for tick in state.resident_ticks or []:
        if int(tick.get("tick", 0)) == 1 or tick.get("branch") == state.branch:
            activations.update(str(agent_id) for agent_id in tick.get("activation_agent_ids", []))
    emissions = Counter(
        message.source_agent_display_id
        for message in state.ledger.messages.values()
        if message.source_agent_display_id in cluster_by_agent
        and message.thread_id in state.ledger.threads
        and state.ledger.threads[message.thread_id].branch in {state.branch, "shared_baseline"}
    )
    per_cluster: dict[str, dict[str, Any]] = {}
    for agent_id, cluster in cluster_by_agent.items():
        row = per_cluster.setdefault(cluster, {"population": 0, "activated_agent_ticks": 0, "public_messages": 0})
        row["population"] += 1
        row["activated_agent_ticks"] += activations[agent_id]
        row["public_messages"] += emissions[agent_id]
    for row in per_cluster.values():
        row["public_messages_per_activation"] = _safe_rate(row["public_messages"], row["activated_agent_ticks"])
    rates = [row["public_messages_per_activation"] for row in per_cluster.values() if row["public_messages_per_activation"] is not None]
    return {
        "per_cluster": dict(sorted(per_cluster.items())),
        "activated_agent_ticks": sum(activations.values()),
        "resident_messages_per_activation": _safe_rate(sum(emissions.values()), sum(activations.values())),
        "between_cluster_emission_rate_range": round(max(rates) - min(rates), 6) if len(rates) >= 2 else None,
        "clusters_with_observed_activation": len(rates),
    }


def _review_summary(
    rows: Sequence[Mapping[str, Any]] | None,
    *,
    allowed_agent_ids: set[str],
) -> dict[str, Any]:
    if rows is None:
        return {"status": "not_provided", "reviewed_agents": 0, "scores": None}
    by_agent: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _REVIEW_FIELDS:
            raise CmvEvaluationError("blind review row does not match the score-only schema")
        agent_id = str(row["agent_id"])
        if agent_id not in allowed_agent_ids:
            raise CmvEvaluationError("blind review references an Agent outside the fixed world")
        if agent_id in by_agent:
            raise CmvEvaluationError("blind review must contain at most one pre-aggregated row per Agent")
        scores: dict[str, float] = {}
        for key in ("profile_fidelity", "history_context_fit", "voice_distinctiveness"):
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
                raise CmvEvaluationError("blind review score must be in [0, 1]")
            scores[key] = float(value)
        by_agent[agent_id] = scores
    metric_names = ("profile_fidelity", "history_context_fit", "voice_distinctiveness")
    score_summary = {
        key: round(sum(agent[key] for agent in by_agent.values()) / len(by_agent), 6)
        if by_agent else None
        for key in metric_names
    }
    return {"status": "scored" if by_agent else "empty", "reviewed_agents": len(by_agent), "review_rows": len(rows), "scores": score_summary}


def evaluate_cmv_paired_run(
    *,
    natural_state: Any,
    governed_state: Any,
    world: Mapping[str, Any],
    reference_targets: Sequence[Mapping[str, Any]] | None = None,
    blind_persona_reviews: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize four evaluation dimensions and paired Natural/D mechanisms."""
    if (
        natural_state.branch != "natural"
        or governed_state.branch != "D"
        or natural_state.tick != governed_state.tick
        or natural_state.tick < 1
        or natural_state.scenario_id != governed_state.scenario_id
        or not isinstance(world.get("agents"), list)
    ):
        raise CmvEvaluationError("Natural/D checkpoints are not a paired fixed-world comparison")
    allowed_agents = {str(row["agent_id"]) for row in world["agents"]}
    natural = _branch_forum_summary(natural_state.ledger, scenario_id=natural_state.scenario_id, branch="natural")
    governed = _branch_forum_summary(governed_state.ledger, scenario_id=governed_state.scenario_id, branch="D")
    reference = _reference_forum_summary(reference_targets or [])
    natural_heterogeneity = _activation_rates(natural_state, world)
    governed_heterogeneity = _activation_rates(governed_state, world)
    reviews = _review_summary(blind_persona_reviews, allowed_agent_ids=allowed_agents)

    def input_audit(state: Any) -> dict[str, Any]:
        rows = state.resident_ticks or []
        observed = sum(int(row.get("activation_count", len(row.get("activation_agent_ids", [])))) for row in rows)
        audited = sum(int(row.get("particle_constraint_input_count", 0)) for row in rows)
        history = sum(int(row.get("reviewed_history_input_count", 0)) for row in rows)
        return {"observed_slots": observed, "particle_constraint_slots": audited, "reviewed_history_slots": history,
                "particle_input_coverage": _safe_rate(audited, observed), "history_input_coverage": _safe_rate(history, observed),
                "status": "input_archive_available" if audited else "legacy_run_not_audited",
                "meaning": "Checks actual input delivery; behavioral persona fidelity requires the separate review."}

    return {
        "schema_version": CMV_EVALUATION_SCHEMA,
        "episode_id": natural_state.scenario_id,
        "completed_tick": natural_state.tick,
        "dimensions": {
            "forum_realism": {
                "status": "descriptive_reference_comparison" if reference else "run_metrics_only",
                "simulated_natural": natural,
                "simulated_governed": governed,
                "observed_continuation": reference,
                "comparison_note": "Run length and sampled exposure differ from the historical continuation; report structural measures side by side, not as a calibrated human-level score.",
            },
            "persona_fidelity": {
                "status": reviews["status"],
                "blind_review": reviews,
                "input_constraint_audit": {"natural": input_audit(natural_state), "D": input_audit(governed_state)},
                "deduplicated_style": {branch: deduplicated_style_metrics(state.ledger, scenario_id=state.scenario_id, branch=branch)
                                       for branch, state in (("natural", natural_state), ("D", governed_state))},
            },
            "population_heterogeneity": {
                "status": "activated_agent_descriptives",
                "natural": natural_heterogeneity,
                "governed": governed_heterogeneity,
                "parent_state_metrics": {branch: population_state_metrics(state.particles, world)
                                         if getattr(state, "particles", None) is not None else None
                                         for branch, state in (("natural", natural_state), ("D", governed_state))},
                "interpretation": "Cluster differences describe the fixed synthetic population and this run's activations; they are not an independent estimate of Reddit population heterogeneity.",
            },
            "governance_response": {
                "status": "paired_mechanism_comparison",
                "observation_window": {
                    "completed_tick": governed_state.tick,
                    "governance_messages": governed["governance_messages"],
                    "artifacts_due_by_last_message": governed["governance_artifacts_due_by_last_message"],
                    "artifacts_with_followup_tick": governed["governance_artifacts_with_followup_tick"],
                    "direct_resident_replies": governed["direct_resident_replies_to_governance"],
                },
                "natural": {
                    key: natural[key]
                    for key in ("unverified_claim_share", "contested_claim_share", "unsupported_factual_claim_share", "unsupported_or_contested_share", "correction_share", "help_receipt_ratio", "private_to_public_retellings", "public_messages", "private_messages")
                },
                "D": {
                    key: governed[key]
                    for key in ("unverified_claim_share", "contested_claim_share", "unsupported_factual_claim_share", "unsupported_or_contested_share", "correction_share", "help_receipt_ratio", "private_to_public_retellings", "public_messages", "private_messages")
                },
                "difference_D_minus_natural": {
                    key: round(governed[key] - natural[key], 6)
                    if isinstance(governed[key], (int, float)) and not isinstance(governed[key], bool)
                    and isinstance(natural[key], (int, float)) and not isinstance(natural[key], bool)
                    else None
                    for key in ("unverified_claim_share", "contested_claim_share", "unsupported_factual_claim_share", "unsupported_or_contested_share", "correction_share", "help_receipt_ratio", "private_to_public_retellings", "public_messages", "private_messages")
                },
                "interpretation": "A missing denominator remains null. Differences describe this paired replay and are not causal estimates beyond the frozen synthetic run.",
                "legacy_aliases": {"unsupported_or_contested_share": "Unverified plus contested share; this legacy name does not measure false or unsupported factual content."},
            },
        },
        "uncertainty": {
            "intervals": None,
            "reason": "One development episode and one paired run do not support independent episode-level confidence intervals.",
        },
    }


__all__ = ["CMV_EVALUATION_SCHEMA", "CmvEvaluationError", "evaluate_cmv_paired_run"]
