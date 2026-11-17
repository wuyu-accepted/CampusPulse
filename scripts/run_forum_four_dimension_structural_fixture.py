"""Run the four-dimensional ForumTwin path with a non-publishable test source.

This script performs no network I/O and is never publication evidence.  Its
purpose is to exercise the exact V5 prompt, alias, forum-ledger, particle,
governance and four-dimensional analysis paths before a paid Provider run.
The deterministic source responds only from each rendered closed-set contract;
it does not read private corpus text or invent a second simulation kernel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping

from envs.campus_pulse_governance.forum_hero import (
    run_forum_four_dimension_resource_hero,
)
from envs.campus_pulse_governance.forum_realism import (
    build_calibrated_realism_benchmark,
    build_four_dimension_fidelity_report,
    extract_public_worlds,
    four_dimension_inputs_from_hero_result,
)
from envs.campus_pulse_governance.forum_relationships import (
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.mass_population import canonical_hash
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "hero-pilot-bridge-v6.json"
)
DEFAULT_FORMAL_POPULATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "semantic-llm-forum-agent-population-v2.json"
)
DEFAULT_ECOLOGY = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "forum-ecology-release-v1.json"
)
DEFAULT_CALIBRATION = (
    ROOT
    / "data"
    / "campus_pulse_workspace"
    / "forumtwin"
    / "releases"
    / "forum-ecology-calibration-v2.json"
)
DEFAULT_BASELINE = (
    ROOT
    / "evidence"
    / "forum-realism-calibration-baseline-v2"
    / "forum-realism-benchmark-v1.json"
)
DEFAULT_OUTPUT = (
    ROOT / "_run" / "four-dimension-v1" / "structural-fixture-current"
)
FIXTURE_MODEL = "forumtwin-zero-network-structural-fixture"
FIXTURE_TRACE_SHA256 = hashlib.sha256(
    b"forumtwin-nonpublishable-structural-fixture-v1"
).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def _hash_index(value: str, modulo: int) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:12], 16) % modulo


class StructuralSemanticSource:
    """Deterministic contract exerciser; never describe this as an LLM trace."""

    provenance = "reviewed_trace_replay"
    formal_evidence = False
    max_concurrency = 1

    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def _resident(rendered: Any) -> dict[str, Any]:
        prompt = json.loads(rendered.agent_prompt)
        contract = prompt["output_contract"]
        actions = list(contract["actions"])
        targets = contract["available_target_ids"]
        thread_ids = list(targets.get("thread_ids") or ())
        message_ids = list(targets.get("message_ids") or ())
        visible_pairs = [
            (
                str(row.get("relation") or "feed_summary"),
                str(row.get("thread_id")),
                str(row.get("message_id")),
            )
            for row in prompt.get("forum_context") or ()
            if isinstance(row, Mapping)
            and row.get("thread_id") in thread_ids
            and row.get("message_id") in message_ids
        ]
        visible_pairs.sort(
            key=lambda row: (
                {"direct_parent": 0, "quote": 1, "root": 2}.get(row[0], 3),
                row[1],
                row[2],
            )
        )
        required_topic = contract.get("required_thread_topic")
        independent_entry = bool(
            (prompt.get("scenario_context") or {}).get("forum_entry_mode")
        )
        governance_followup = bool(
            (prompt.get("scenario_context") or {}).get("governance_followup")
        )

        if actions == ["post"] or independent_entry:
            action = "post" if "post" in actions else "read"
        elif visible_pairs and "reply" in actions:
            action = "reply"
        elif "post" in actions:
            action = "post"
        elif "read" in actions:
            action = "read"
        else:
            action = actions[0]

        target_thread = visible_pairs[0][1] if action == "reply" and visible_pairs else None
        target_message = visible_pairs[0][2] if action == "reply" and visible_pairs else None
        topics = list(prompt.get("allowed_topics") or ())
        topic = str(required_topic or (topics[0] if topics else "campus-governance"))
        index = _hash_index(rendered.prompt_sha256, 3)
        stance = ("support", "oppose", "uncertain")[index]
        public = action not in {"read", "ignore"}

        if action == "post":
            visible_text = "暑期床位安排影响到我的时间和替代选择，想问清资格边界和复核办法。"
        elif action == "reply" and governance_followup:
            visible_text = "这条更新我看到了；已说明的部分有用，但未解决的范围和下一次更新时间还需要说清楚。"
        elif action == "reply":
            visible_text = "你提到的这一点也影响判断，我更想先确认适用范围，再决定是否认同这个解释。"
        else:
            visible_text = ""

        engagements = []
        if target_message is not None:
            engagements.append(
                {
                    "kind": "like",
                    "target_message_id": target_message,
                    "report_reason_tag": None,
                }
            )
            if _hash_index(rendered.prompt_sha256 + ":repost", 7) == 0:
                engagements.append(
                    {
                        "kind": "repost",
                        "target_message_id": target_message,
                        "report_reason_tag": None,
                    }
                )
            if _hash_index(rendered.prompt_sha256 + ":report", 13) == 0:
                engagements.append(
                    {
                        "kind": "report",
                        "target_message_id": target_message,
                        "report_reason_tag": "misinformation",
                    }
                )

        return {
            "action": action,
            "target_thread_id": target_thread,
            "target_message_id": target_message,
            "quote_message_id": None,
            "target_claim_id": None,
            "topic": topic,
            "subtopic": "资格边界与复核",
            "stance": stance,
            "urgency": 0.72 if index == 1 else 0.52,
            "emotion": "concerned" if index == 1 else "curious",
            "confidence": 0.68,
            "evidence_status": "uncertain",
            "claim_operation": "create" if action == "post" else "none",
            "claim_summary": "床位资格边界和复核路径需要进一步说明" if action == "post" else "",
            "correction_target_claim_id": None,
            "trust_delta": 0.08 if governance_followup else -0.03,
            "satisfaction_delta": 0.05 if governance_followup else -0.02,
            "memory_update": "记住了当前讨论中的资格边界和复核问题" if public else "",
            "visible_text": visible_text,
            "engagements": engagements[:6],
            "private_actions": [],
            "public_private_origin_claim_id": None,
            "public_identity_mode": (
                "anonymous"
                if public and _hash_index(rendered.prompt_sha256 + ":anon", 5) == 0
                else "named"
            ),
        }

    @staticmethod
    def _actor(rendered: Any) -> dict[str, Any]:
        preferred_by_actor = {
            "governance_authority": "verified_update",
            "service_operator": "support_channel",
            "community_bridge": "peer_feedback",
        }
        preferred = preferred_by_actor[rendered.actor_id]
        if preferred not in rendered.feasible_actions:
            preferred = rendered.feasible_actions[0] if rendered.feasible_actions else "noop"
        probabilities = {
            action: (1.0 if action == preferred else 0.0)
            for action in ("noop", *rendered.feasible_actions)
        }
        claim_id = None
        thread_id = None
        reference = None
        if preferred != "noop":
            if rendered.actor_id == "governance_authority" and rendered.claim_target_ids:
                claim_id = rendered.claim_target_ids[0]
                reference = claim_id
            elif rendered.thread_target_ids:
                thread_id = rendered.thread_target_ids[0]
                reference = thread_id if rendered.service_target_required else None
        messages = {
            "governance_authority": "发布当前证据支持的资格边界，未确认部分继续核验，并在下一时间步更新。",
            "service_operator": "已登记当前求助并给出处理窗口；回执仅表示进入队列，完成情况随后反馈。",
            "community_bridge": "把当前说明转给尚未触达的群体，收集具体疑问后带回同一讨论。",
        }
        return {
            "action_probabilities": probabilities,
            "preferred_action": preferred,
            "confidence": 0.82,
            "rationale_tags": ["visible_forum_signal", "bounded_followup"],
            "risk_level": "medium",
            "uncertainty": 0.24,
            "public_message": "" if preferred == "noop" else messages[rendered.actor_id],
            "target_claim_id": claim_id,
            "target_thread_id": thread_id,
            "evidence_or_service_reference": reference,
        }

    def acquire(
        self,
        *,
        slot_kind: str,
        prompt_sha256: str,
        rendered: Any = None,
        validator: Any = None,
    ) -> TurnEnvelope:
        self.calls += 1
        payload = (
            self._resident(rendered)
            if slot_kind == "resident"
            else self._actor(rendered)
        )
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        envelope = TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            provenance=self.provenance,
            content=content,
            returned_model=FIXTURE_MODEL,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            provider_calls=0,
            provider_attempts=0,
            provider_billed_tokens=0,
            cache_hit=True,
        )
        if validator is not None:
            validator(envelope)
        return envelope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument(
        "--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION
    )
    parser.add_argument("--ecology", type=Path, default=DEFAULT_ECOLOGY)
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    population = _load(arguments.population.resolve())
    formal_population = _load(arguments.formal_population.resolve())
    ecology = _load(arguments.ecology.resolve())
    calibration = _load(arguments.calibration.resolve())
    baseline = (
        _load(arguments.baseline.resolve())
        if arguments.baseline.resolve().is_file()
        else None
    )
    stakes = compile_scenario_stake_release_v1(
        population=formal_population,
        scenario_ids=("governance_legitimacy_dispute",),
    )
    relationships = compile_synthetic_relationship_release_v1(
        population=formal_population,
        stake_release=stakes,
    )
    source = StructuralSemanticSource()
    started = time.perf_counter()
    result = run_forum_four_dimension_resource_hero(
        population_release=population,
        relationship_release=relationships,
        scenario_stake_release=stakes,
        ecology_calibration_release=calibration,
        resident_source=source,
        governance_source=source,
        expected_model=FIXTURE_MODEL,
        reviewed_trace_release_sha256=FIXTURE_TRACE_SHA256,
    )
    elapsed = time.perf_counter() - started
    worlds = extract_public_worlds(result, source_label="structural_fixture")
    ecology_benchmark = build_calibrated_realism_benchmark(
        ecology=ecology,
        worlds=worlds,
        baseline_benchmark=baseline,
    )
    derived = four_dimension_inputs_from_hero_result(result)
    report = build_four_dimension_fidelity_report(
        ecology_benchmark=ecology_benchmark,
        population_frames=derived["population_frames"],
        governance_summary=derived["governance_summary"],
    )
    summary = {
        "schema_version": "campus-pulse-four-dimension-structural-fixture-summary-v2",
        "fixture_only": True,
        "publication_eligible": False,
        "network_accessed": False,
        "source_calls": source.calls,
        "primary_slots": result["primary_slots"],
        "elapsed_seconds": round(elapsed, 3),
        "dimension_status": {
            name: row["status"] for name, row in report["dimensions"].items()
        },
        "forum_ecology": report["dimensions"]["forum_ecology"],
        "population_heterogeneity": report["dimensions"]["population_heterogeneity"],
        "network_and_governance_response": report["dimensions"][
            "network_and_governance_response"
        ],
        "persona_dimension_expected_pending": True,
        "structural_result_sha256": result["result_sha256"],
    }
    summary["content_sha256"] = canonical_hash(summary)
    output_dir = arguments.output_dir.resolve()
    _write(output_dir / "structural-result.json", result)
    _write(output_dir / "ecology-benchmark.json", ecology_benchmark)
    _write(output_dir / "four-dimension-report.json", report)
    _write(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
