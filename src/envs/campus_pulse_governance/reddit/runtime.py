"""Adapters from the fixed CMV world into existing ForumTwin turn contracts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from ..forum_turns import RenderedForumAgentTurnV3, render_forum_agent_turn_v3
from ..forum_twin import AgentMemory, FeedExposure, ForumLedger, ForumMessage, ForumThread, ExternalStimulusProvenance
from .particle_model import CmvParticleSnapshot, decode_agent_state


class CmvRuntimeError(ValueError):
    """The fixed CMV world could not be bound to a ForumTwin resident turn."""


CMV_INPUT_CONDITIONS = frozenset({"full", "context_only", "profile_only", "state_only"})


def apply_cmv_input_condition(turn: RenderedForumAgentTurnV3, condition: str, *, input_revision: int = 1) -> RenderedForumAgentTurnV3:
    """A 2x2 input ablation; keep output targets and temporal memory identical.

    Shadow particles remain in checkpoints for diagnostics when state input is
    disabled. This is an input-constraint ablation, not removal of the SMC engine.
    """
    if condition not in CMV_INPUT_CONDITIONS:
        raise CmvRuntimeError("unknown CMV input condition")
    payload = json.loads(turn.agent_prompt)
    original_brief = payload.get("participation_brief", {})
    # Derived participation suggestions can leak the omitted profile/state.
    # Replace them with the same forum-choice instructions in all four cells.
    payload["participation_brief"] = {
        "choice": "Read the visible discussion. You may contribute a distinct relevant point, read, or ignore.",
        "stance_reference": "Report your stance on the event decision_question, not agreement with the parent author.",
        "continuity": "Use your actual earlier simulated memory when relevant; do not invent personal experiences.",
    }
    if input_revision not in {1, 2, 3}:
        raise CmvRuntimeError("unknown input revision")
    if input_revision >= 2:
        payload["participation_brief"]["stance_reference"] = (
            "Report your position on the whole event proposition, separate from agreement with a reply. "
            "Qualified support is support even with implementation/cost concerns; qualified rejection is oppose. "
            "Mixed means genuinely holding competing positions, not politeness or mentioning a tradeoff. "
            "Uncertain means you cannot yet choose a position. Your visible text and reported stance should "
            "express the same position; questions without a position may remain uncertain."
        )
    if input_revision == 3:
        # These are common action rules, not derived Profile/state values.
        for key in ("public_choice", "contribution", "voice", "evidence"):
            if key in original_brief:
                payload["participation_brief"][key] = original_brief[key]
    # Private actions are disabled in this public-forum experiment. Their choice
    # brief otherwise contains personality and state-derived motives.
    payload["private_channel"] = {"limits": {"maximum_private_actions": 0}}
    payload["relationship_context"] = []
    if condition in {"context_only", "state_only"}:
        payload.pop("profile", None)
        contract = payload.get("agent_contract", {})
        for key in tuple(contract):
            if "profile" in key:
                contract.pop(key)
    if condition in {"context_only", "profile_only"}:
        payload.pop("posterior_state", None)
        payload.pop("background_state_summary", None)
        payload.get("decision_constraint_context", {}).pop("prior_event_belief", None)
        payload.get("decision_constraint_context", {}).pop("pre_decision_state", None)
    prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(json.dumps({
        "renderer": "campus-pulse-forum-agent-turn-v3", "language": turn.language,
        "expected_model": turn.expected_model, "system_prompt": turn.system_prompt,
        "agent_prompt": prompt,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return replace(turn, agent_prompt=prompt, prompt_sha256=digest)


def select_cmv_opened_message(delivered: Sequence[FeedExposure], *, agent_id: str,
                              tick: int, ledger: ForumLedger,
                              read_history: Mapping[str, int] | None = None) -> ForumMessage | None:
    """Reproducible attention-weighted opening, without changing public rank."""
    candidates = sorted((row for row in delivered if row.delivered_tick <= tick
                         and ledger.messages[row.message_id].created_tick < tick),
                        key=lambda row: (-row.rank_score, row.exposure_id))[:6]
    if read_history is not None:
        def attention_weight(row):
            last_read = read_history.get(row.message_id)
            novelty = 1.0 if last_read is None else (0.15 if tick-last_read <= 2 else 0.4)
            notification = 1.5 if row.exposure_source == "direct_reply_notification" else 1.0
            return max(0.01, row.rank_score) * novelty * notification
        # Weight before truncation so unread cold items can compete for attention.
        unique = {row.message_id: row for row in delivered if row.delivered_tick <= tick
                  and ledger.messages[row.message_id].created_tick < tick
                  and not ledger.messages[row.message_id].superseded}
        candidates = sorted(unique.values(), key=lambda row: (-attention_weight(row), row.exposure_id))[:12]
    if not candidates:
        return None
    weights = [(attention_weight(row) if read_history is not None else max(0.01, row.rank_score)) ** 2 for row in candidates]
    digest = hashlib.sha256(f"cmv-open-v2:{agent_id}:{tick}".encode()).digest()
    draw = int.from_bytes(digest[:8], "big") / 2**64 * sum(weights)
    for row, weight in zip(candidates, weights):
        draw -= weight
        if draw < 0:
            return ledger.messages[row.message_id]
    return ledger.messages[candidates[-1].message_id]


def initialize_cmv_event_ledger(episode_context: Mapping[str, Any]) -> ForumLedger:
    """Place only the sanitized observed root at time zero; no held-out replies.

    Prefix comments remain historical context, not simulated messages with
    fabricated timestamps. The real root is now a legal reply/quote target.
    """
    if episode_context.get("context_type") != "historical_forum_observation":
        raise CmvRuntimeError("event seed requires a frozen historical observation")
    prefix = episode_context.get("observed_prefix") or []
    if not prefix or not isinstance(prefix[0], Mapping):
        raise CmvRuntimeError("event has no observed root")
    text = str(prefix[0].get("text") or "").strip()
    if not text or len(text) > 4_000:
        raise CmvRuntimeError("event root must be nonempty and fit the full-text budget")
    event_id = str(episode_context["episode_id"])
    digest = hashlib.sha256(json.dumps(dict(episode_context), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    thread_id, message_id = "cmv-event-thread-" + digest[:24], "cmv-event-message-" + digest[:24]
    provenance = ExternalStimulusProvenance(
        kind="external_event_seed", prompt_sha256=digest,
        response_sha256=hashlib.sha256(text.encode()).hexdigest(), model="historical_event_input",
    )
    ledger = ForumLedger()
    ledger.add_thread(ForumThread(
        thread_id=thread_id, board="changemyview", root_message_id=message_id,
        scenario_id=event_id, branch="shared_baseline", created_tick=0,
        status="open", topic="current_event", need="discussion", provenance_kind=provenance.kind,
        last_active_tick=0, participant_count=1,
    ), ForumMessage(
        message_id=message_id, thread_id=thread_id, source_agent_display_id="external-event-author",
        action="post", visible_text=text, topic="current_event", stance="uncertain",
        emotion="neutral", evidence_status="unverified", confidence=0.5, created_tick=0,
        ttl=23, provenance=provenance, visible_text_max_chars=4_000,
    ))
    return ledger


_RELATION_TYPE = {
    "strong": "close_friend",
    "familiar": "familiar_peer",
    "cross_group": "cross_group_acquaintance",
}


@dataclass(frozen=True)
class CmvFeedSnapshot:
    """One time-frozen global board plus recipient-specific private deliveries."""

    tick: int
    public_board: Mapping[str, Any]
    exposures_by_agent: Mapping[str, tuple[FeedExposure, ...]]
    exposure_manifest: tuple[Mapping[str, Any], ...]
    ranking_sha256: str
    hot_message_ids: tuple[str, ...]
    latest_message_ids: tuple[str, ...]


def build_cmv_feed_snapshot(
    *,
    world: Mapping[str, Any],
    ledger: ForumLedger,
    scenario_id: str,
    branch: str,
    tick: int,
    relationship_release: Mapping[str, Any],
    hot_limit: int = 10,
    latest_limit: int = 10,
) -> CmvFeedSnapshot:
    """Freeze the public hot/latest board and synthetic recipient deliveries.

    The ranking is global and identical for every resident. Relationship ties
    and topic fit affect only private delivery/opening scores; they never alter
    public rank. Only messages from earlier ticks are eligible.
    """
    agents = world.get("agents")
    if (
        not isinstance(agents, list)
        or not agents
        or not 1 <= int(tick) <= 23
        or not 1 <= int(hot_limit) <= 10
        or not 1 <= int(latest_limit) <= 10
        or not scenario_id
        or branch not in {"shared_baseline", "natural", "D"}
    ):
        raise CmvRuntimeError("CMV feed snapshot identity or limits are invalid")
    agent_by_id = {
        str(row.get("agent_id") or ""): row
        for row in agents
        if isinstance(row, Mapping)
    }
    if len(agent_by_id) != len(agents) or "" in agent_by_id:
        raise CmvRuntimeError("CMV feed world has duplicate or empty Agent IDs")
    relationships = relationship_context_by_agent(relationship_release)
    if set(relationships) != set(agent_by_id):
        raise CmvRuntimeError("CMV feed relations do not match the fixed world")

    eligible = [
        message
        for message in ledger.messages.values()
        if message.created_tick < tick
        and message.active_at(tick - 1)
        and message.provenance.kind in {"authorized_live_llm", "reviewed_trace_replay", "external_event_seed"}
        and message.source_agent_display_id != ""
        and ledger.threads[message.thread_id].scenario_id == scenario_id
        and ledger.threads[message.thread_id].branch in {branch, "shared_baseline"}
    ]
    ranked_rows: list[tuple[float, int, str, Any, dict[str, int]]] = []
    for message in eligible:
        counts = ledger.interaction_counts(message.message_id, through_tick=tick - 1)
        thread = ledger.threads[message.thread_id]
        age = max(0, (tick - 1) - message.created_tick)
        # Replies are represented by the thread's earlier committed reply
        # count; reports are deliberately excluded from ranking.
        score = (
            1.0 / (1.0 + age)
            + 0.10 * math.log1p(max(0, thread.reply_count))
            + 0.08 * math.log1p(counts.get("like", 0))
            + 0.12 * math.log1p(counts.get("repost", 0))
        )
        ranked_rows.append((score, message.created_tick, message.message_id, message, counts))
    hot = sorted(ranked_rows, key=lambda row: (-row[0], -row[1], row[2]))[:hot_limit]
    latest = sorted(ranked_rows, key=lambda row: (-row[1], row[2]))[:latest_limit]

    board_message_rows = hot + [row for row in latest if row[2] not in {item[2] for item in hot}]
    board_message_indices = {row[2]: index for index, row in enumerate(board_message_rows)}
    public_board = {
        "tick": tick,
        "ranking_identical_for_all_residents": True,
        "ranking_policy": "global_hot_top_10_and_latest_posts; relationships_only_affect_private_delivery",
        # Index references avoid duplicating posts that occur in both lists.
        "messages": [
            {
                "post_ref": f"Board post {index + 1}",
                "tick": item[3].created_tick,
                "action": item[3].action,
                "text": item[3].visible_text[:36],
            }
            for index, item in enumerate(board_message_rows)
        ],
        "hot_top_10": [board_message_indices[row[2]] for row in hot],
        "latest_posts": [board_message_indices[row[2]] for row in latest],
    }
    ranking_sha = hashlib.sha256(
        json.dumps(
            {"hot": [row[2] for row in hot], "latest": [row[2] for row in latest]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    board_message_ids = {row[2] for row in hot} | {row[2] for row in latest}
    contacts_by_agent = {
        agent_id: {
            str(row["contact_agent_id"]): float(row["strength"])
            for row in rows
        }
        for agent_id, rows in relationships.items()
    }
    exposures: dict[str, tuple[FeedExposure, ...]] = {}
    manifest: list[dict[str, Any]] = []
    for agent_id, agent in sorted(agent_by_id.items()):
        profile = agent.get("provider_profile_bundle")
        if not isinstance(profile, Mapping):
            raise CmvRuntimeError("CMV feed Agent lacks its fixed profile bundle")
        topics = {
            str(value).casefold()
            for value in profile.get("topic_portfolio", [])
        }
        participated = {
            thread_id
            for thread_id in ledger.threads
            if any(
                message.thread_id == thread_id
                and message.source_agent_display_id == agent_id
                and not message.superseded
                for message in ledger.messages.values()
            )
        }
        rows: list[FeedExposure] = []
        for message in eligible:
            if message.source_agent_display_id == agent_id:
                continue
            thread = ledger.threads[message.thread_id]
            relation_strength = contacts_by_agent[agent_id].get(
                message.source_agent_display_id, 0.0
            )
            same_thread = message.thread_id in participated
            notified = any(
                target is not None and target in ledger.messages
                and ledger.messages[target].source_agent_display_id == agent_id
                for target in (message.parent_message_id, message.quote_message_id)
            )
            board_rank = message.message_id in board_message_ids
            topic_fit = float(message.topic.casefold() in topics)
            # Public board membership is shared by all; topic and relationship
            # relevance change which item is opened from that common board.
            if not (board_rank or same_thread or relation_strength > 0.0 or notified):
                continue
            age = max(0, (tick - 1) - message.created_tick)
            source = (
                "direct_reply_notification" if notified else "thread_participant"
                if same_thread
                else "synthetic_contact"
                if relation_strength > 0.0
                else "global_hot_or_latest"
            )
            score = (
                0.35 * (1.0 / (1.0 + age))
                + 0.25 * float(board_rank)
                + 0.20 * relation_strength
                + 0.15 * float(same_thread)
                + 0.05 * topic_fit
                + 0.20 * float(notified)
            )
            exposure_id = "cmv-exposure-" + hashlib.sha256(
                f"{scenario_id}\0{branch}\0{tick}\0{agent_id}\0{message.message_id}".encode("utf-8")
            ).hexdigest()[:24]
            rows.append(
                FeedExposure(
                    exposure_id=exposure_id,
                    recipient_agent_id=agent_id,
                    message_id=message.message_id,
                    delivered_tick=tick,
                    exposure_source=source,
                    rank_score=score,
                )
            )
            manifest.append(
                {
                    "recipient_agent_id": agent_id,
                    "message_id": message.message_id,
                    "delivered_tick": tick,
                    "exposure_source": source,
                    "rank_score": round(score, 8),
                    "read_state": "unread",
                }
            )
        exposures[agent_id] = tuple(
            sorted(rows, key=lambda item: (-item.rank_score, item.exposure_id))
        )
    return CmvFeedSnapshot(
        tick=tick,
        public_board=public_board,
        exposures_by_agent=exposures,
        exposure_manifest=tuple(manifest),
        ranking_sha256=ranking_sha,
        hot_message_ids=tuple(row[2] for row in hot),
        latest_message_ids=tuple(row[2] for row in latest),
    )


def relationship_context_by_agent(
    relationship_release: Mapping[str, Any],
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Create each Agent's closed contact set from the synthetic relation graph."""
    ids = relationship_release.get("world_agent_ids")
    edges = relationship_release.get("edges")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise CmvRuntimeError("relationship release has invalid world identities")
    if not isinstance(edges, list) or relationship_release.get("edge_count") != len(edges):
        raise CmvRuntimeError("relationship release has no edge list")
    relationship_body = {
        key: value for key, value in relationship_release.items() if key != "release_sha256"
    }
    try:
        relationship_digest = hashlib.sha256(
            json.dumps(
                relationship_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise CmvRuntimeError("relationship release is not strict JSON") from exc
    if (
        relationship_release.get("schema_version") != "reddit-cmv-synthetic-relationship-release-v1"
        or relationship_digest != relationship_release.get("release_sha256")
        or relationship_release.get("edge_count") != 120
    ):
        raise CmvRuntimeError("relationship release integrity check failed")
    context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise CmvRuntimeError("relationship release contains a malformed edge")
        left = str(edge.get("agent_a") or "")
        right = str(edge.get("agent_b") or "")
        relation = str(edge.get("relationship_type") or "")
        if left not in ids or right not in ids or left == right or relation not in _RELATION_TYPE:
            raise CmvRuntimeError("relationship edge is outside its fixed world")
        strength = float(edge.get("strength", -1))
        if not 0 <= strength <= 1:
            raise CmvRuntimeError("relationship strength is invalid")
        record = {
            "relation_type": _RELATION_TYPE[relation],
            "strength": strength,
            # Trust is an explicit synthetic prior, not an observed personal tie.
            "trust": strength,
            "recent_shared_context": "No shared simulation messages yet.",
        }
        context[left].append({"contact_agent_id": right, **record})
        if relation == "strong" and edge.get("directed") is False:
            context[right].append({"contact_agent_id": left, **record})
    expected = set(ids)
    if set(context) != expected or any(len(context[agent_id]) != 6 for agent_id in ids):
        raise CmvRuntimeError("each resident must have two friends and four directed contacts")
    if any(
        len({row["contact_agent_id"] for row in rows}) != len(rows)
        for rows in context.values()
    ):
        raise CmvRuntimeError("relationship contact set repeats a resident")
    return {
        agent_id: tuple(sorted(rows, key=lambda row: (row["relation_type"], row["contact_agent_id"])))
        for agent_id, rows in context.items()
    }


def render_cmv_population_turns(
    *,
    world: Mapping[str, Any],
    particles: CmvParticleSnapshot,
    episode_context: Mapping[str, Any],
    ledger: ForumLedger,
    scenario_id: str,
    branch: str,
    tick: int,
    expected_model: str,
    activated_agent_ids: Sequence[str] | None = None,
    exposures_by_agent: Mapping[str, Sequence[FeedExposure]] | None = None,
    memories_by_agent: Mapping[str, AgentMemory] | None = None,
    private_conversations_by_agent: Mapping[str, Sequence[str]] | None = None,
    public_board: Mapping[str, Any] | None = None,
    private_actions_enabled: bool = True,
    input_condition: str | None = None,
    input_revision: int = 1,
    previous_states_by_agent: Mapping[str, Mapping[str, Any]] | None = None,
    attention_history_by_agent: Mapping[str, Mapping[str, int]] | None = None,
) -> dict[str, RenderedForumAgentTurnV3]:
    """Render the actual per-Agent V3 inputs for one frozen population tick.

    This performs no network calls. Historical CMV messages remain explicitly
    labeled event observations in scenario context; they are not materialized as
    synthetic Agent speech or given invented ForumLedger lineage.
    """
    if input_revision not in {1, 2, 3}:
        raise CmvRuntimeError("unknown input revision")
    agents = world.get("agents")
    relationships = world.get("relationships")
    if not isinstance(agents, list) or not isinstance(relationships, Mapping):
        raise CmvRuntimeError("fixed world lacks Agents or relationship release")
    if (
        episode_context.get("context_type") != "historical_forum_observation"
        or episode_context.get("episode_id") != scenario_id
    ):
        raise CmvRuntimeError("episode context is not bound to the selected CMV event")
    by_id = {str(row.get("agent_id") or ""): row for row in agents if isinstance(row, Mapping)}
    if len(by_id) != len(agents) or set(by_id) != set(particles.agent_ids):
        raise CmvRuntimeError("fixed world and particle Agent ordering do not align")
    if particles.tick != tick:
        raise CmvRuntimeError("particle state tick does not match rendered resident tick")
    updates = episode_context.get("scenario_updates", [])
    if not isinstance(updates, list) or any(
        not isinstance(row, Mapping) or row.get("provenance") != "scenario_assumption"
        or type(row.get("released_tick")) is not int or not 1 <= row["released_tick"] <= tick
        or not isinstance(row.get("text"), str) or not row["text"].strip()
        for row in updates
    ):
        raise CmvRuntimeError("scenario assumptions are malformed or visible before release")
    relationships_by_agent = relationship_context_by_agent(relationships)
    # An explicitly supplied empty panel is a caller error, not a request to
    # silently turn a budgeted activation into a full-population census.
    selected = tuple(sorted(
        particles.agent_ids if activated_agent_ids is None else activated_agent_ids
    ))
    if not selected or len(selected) != len(set(selected)) or set(selected) - set(by_id):
        raise CmvRuntimeError("activated Agent set is empty, duplicated, or outside the world")
    exposures_by_agent = exposures_by_agent or {}
    memories_by_agent = memories_by_agent or {}
    private_conversations_by_agent = private_conversations_by_agent or {}
    if public_board is None:
        public_board = {
            "tick": tick,
            "ranking_identical_for_all_residents": True,
            "hot_top_10": [],
            "latest_posts": [],
        }

    rendered: dict[str, RenderedForumAgentTurnV3] = {}
    for agent_id in selected:
        world_agent = by_id[agent_id]
        profile = world_agent.get("provider_profile_bundle")
        if not isinstance(profile, Mapping):
            raise CmvRuntimeError("fixed Agent is missing its reviewed ForumTwin profile")
        agent_view = dict(profile)
        agent_view["agent_id"] = agent_id
        agent_view["synthetic_display_id"] = agent_id
        agent_view["provider_turn_contract"] = str(profile.get("provider_turn_contract") or "")
        # CMV source material and persona compilations are English. The
        # renderer's default remains Chinese for all existing campus runs.
        agent_view["language"] = "en"
        memory = memories_by_agent.get(agent_id) or AgentMemory(agent_id=agent_id)
        if memory.agent_id != agent_id:
            raise CmvRuntimeError("Agent memory belongs to another world member")
        delivered = list(exposures_by_agent.get(agent_id, ()))
        read_history = (attention_history_by_agent or {}).get(agent_id, {})
        opened = select_cmv_opened_message(delivered, agent_id=agent_id, tick=tick, ledger=ledger,
            read_history=read_history if input_revision == 3 else None)
        if opened is not None:
            root_id = ledger.threads[opened.thread_id].root_message_id
            if root_id not in {row.message_id for row in delivered}:
                delivered.append(FeedExposure(
                    exposure_id=f"cmv-open-root-{agent_id}-{tick}", recipient_agent_id=agent_id,
                    message_id=root_id, delivered_tick=tick, exposure_source="opened_thread_root", rank_score=1.0,
                ))
        rendered[agent_id] = render_forum_agent_turn_v3(
            agent=agent_view,
            posterior_state=decode_agent_state(particles, agent_id),
            memory=memory,
            ledger=ledger,
            exposures=tuple(delivered),
            current_thread_id=opened.thread_id if opened else None,
            direct_parent_message_id=opened.message_id if opened else None,
            relationship_context=relationships_by_agent[agent_id],
            private_conversation_ids=tuple(private_conversations_by_agent.get(agent_id, ())),
            scenario_id=scenario_id,
            branch=branch,
            tick=tick,
            expected_model=expected_model,
            scenario_context=episode_context,
            scenario_context_max_chars=5_000,
            public_board=public_board,
            # CMV supplies public threads but no observed private messages.
            # Keep synthetic ties for attention; do not invent a private-chat
            # benchmark or force the unrelated campus private-action contract.
            private_actions_enabled=private_actions_enabled,
            language="en",
        )
        if input_revision == 3:
            turn = rendered[agent_id]
            payload = json.loads(turn.agent_prompt)
            prior = (previous_states_by_agent or {}).get(agent_id)
            if prior is None:
                raise CmvRuntimeError("structure v3 requires the actual previous state view")
            question = str(episode_context.get("decision_question") or episode_context.get("event_title") or "").strip()
            if not question or int(prior["tick"]) >= tick:
                raise CmvRuntimeError("structure v3 decision scope or previous state tick invalid")
            payload["decision_constraint_context"] = {
                "decision_question": question,
                "stable_person": "Your Profile describes enduring interests, expression and evidence habits across topics. Historical episodes inform those habits; this event has its own judgment.",
                "prior_event_belief": {"source_tick": prior["tick"], "state_means": prior["posterior_mean"],
                    "uncertainty_10_50_90": prior["posterior_quantiles_10_50_90"],
                    "scope": "Tentative inclination about this whole proposition before this reading opportunity; not a prescribed answer."},
                "pre_decision_state": payload.get("posterior_state", {}),
                "implementation_conditions": "Visible scenario updates describe costs, access and feasibility. Distinguish views on the principle from views on an implementation; revising either requires a visible reason.",
            }
            own = sorted((m for m in ledger.messages.values() if m.source_agent_display_id == agent_id
                and ledger.threads[m.thread_id].scenario_id == scenario_id
                and ledger.threads[m.thread_id].branch in {branch, "shared_baseline"}
                and m.created_tick < tick and not m.superseded), key=lambda m: (m.created_tick, m.message_id))[-3:]
            payload["discussion_context"] = {
                "opened_before": bool(opened and opened.message_id in read_history),
                "prior_public_contributions": [{"tick": m.created_tick, "thread": m.thread_id, "text": m.visible_text[:300]} for m in own],
                "choice": "Selection gives a reading opportunity. Choose whether there is a reason to speak now; read, ignore, react and public speech are separate possibilities.",
                "new_thread": "A distinct question or argument can start a post. A response to an existing point belongs in its thread. Repeating your earlier contribution does not add a new point.",
            }
            prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(json.dumps({"renderer": "campus-pulse-forum-agent-turn-v3", "language": turn.language,
                "expected_model": turn.expected_model, "system_prompt": turn.system_prompt, "agent_prompt": prompt},
                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            rendered[agent_id] = replace(turn, agent_prompt=prompt, prompt_sha256=digest)
        if input_condition is not None:
            if private_actions_enabled:
                raise CmvRuntimeError("CMV input ablations require private actions disabled")
            rendered[agent_id] = apply_cmv_input_condition(rendered[agent_id], input_condition, input_revision=input_revision)
    return rendered
