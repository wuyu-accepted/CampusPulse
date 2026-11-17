#!/usr/bin/env python3
"""Build the Century Gym video-demo projection without changing v1 evidence.

The reviewed live run stores the population baseline for Tick 0--2 inside the
first committed Tick 3 snapshot.  This builder materialises those three
already-recorded states as public, message-free snapshots so the product demo
can show the complete project lifecycle from population initialisation through
the first forum post.  It never invents public language or alters the v1 run.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "evidence" / "century-gym-live-v1" / "public-progress"
DEFAULT_OUTPUT = ROOT / "evidence" / "century-gym-demo-v2" / "public-progress"


DISPLAY_STAGES = [
    {
        "tick": 0,
        "key": "population_load",
        "title_zh": "人口装载",
        "title_en": "Population loaded",
        "detail_zh": "载入 1,000 个异质 Agent、人物档案和状态粒子。",
        "detail_en": "Load 1,000 heterogeneous Agents, profiles, and state particles.",
    },
    {
        "tick": 1,
        "key": "forum_initialization",
        "title_zh": "论坛初始化",
        "title_en": "Forum initialised",
        "detail_zh": "建立全局热榜、最新帖 Feed 与公开互动计数器。",
        "detail_en": "Initialise the global hot list, latest-post feed, and public engagement counters.",
    },
    {
        "tick": 2,
        "key": "shared_baseline",
        "title_zh": "共享基线冻结",
        "title_en": "Shared baseline frozen",
        "detail_zh": "Natural 与主动治理分支共享同一人口、记忆和起点。",
        "detail_en": "Natural and governed branches share the same population, memory, and starting state.",
    },
    {
        "tick": 3,
        "key": "event_injection",
        "title_zh": "事件进入论坛",
        "title_en": "Event enters the forum",
        "detail_zh": "学生发布“现场空置但系统不可预约”的首批帖子。",
        "detail_en": "Students publish the first reports of visibly empty but unavailable venues.",
    },
    {
        "tick": 4,
        "key": "discussion_formation",
        "title_zh": "讨论结构形成",
        "title_en": "Discussion structure forms",
        "detail_zh": "不同 Agent 围绕取消释放、未签到和维护安排展开追问。",
        "detail_en": "Agents question cancellation release, no-shows, and maintenance arrangements.",
    },
    {
        "tick": 5,
        "key": "parallel_fork",
        "title_zh": "平行分支启动",
        "title_en": "Parallel branches start",
        "detail_zh": "主动治理主体发布证据卡、服务工单与跨项目触达。",
        "detail_en": "Governance actors introduce evidence cards, service tickets, and cross-group outreach.",
    },
    {
        "tick": 10,
        "key": "final_observation",
        "title_zh": "末端观察",
        "title_en": "Final observation",
        "detail_zh": "对照两个平行论坛的帖子、互动、Claim 与治理对象。",
        "detail_en": "Compare posts, engagement, claims, and governance objects across both forums.",
    },
]


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def content_hash(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def load_verified(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("content_sha256") != content_hash(value):
        raise ValueError(f"content hash mismatch: {path}")
    return value


def write_canonical(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def baseline_snapshot(tick_three: dict[str, Any], tick: int) -> dict[str, Any]:
    public = tick_three["public_branch"]
    timeline = [copy.deepcopy(row) for row in public["timeline"] if row.get("tick") <= tick]
    if not timeline or timeline[-1].get("tick") != tick:
        raise ValueError(f"Tick {tick} baseline is absent from the reviewed snapshot")

    zero_usage = {key: 0 for key in (public.get("usage") or {})}
    snapshot = {
        "schema_version": tick_three["schema_version"],
        "scenario_id": tick_three["scenario_id"],
        "seed": tick_three["seed"],
        "branch": "shared_baseline",
        "tick": tick,
        "tick_committed": True,
        "public_content_provenance_rule": tick_three.get("public_content_provenance_rule"),
        "dynamics_public_messages": 0,
        "public_branch": {
            "branch": "shared_baseline",
            "messages": [],
            "threads": [],
            "claims": [],
            "governance_artifacts": [],
            "governance_rounds": [],
            "timeline": timeline,
            "usage": zero_usage,
            "unique_activated_agents": 0,
            "activated_macro_roles": [],
            "public_display_profiles": [],
            "feed_recommendation": copy.deepcopy(public.get("feed_recommendation") or {}),
            "mechanism_chains": {
                "correction_chains": [],
                "governance_uptake_chains": [],
                "help_service_feedback_chains": [],
            },
            "parallel_interactions": {
                "schema_version": "campus-pulse-forum-agent-turn-v2",
                "speech_messages_per_agent_tick": 1,
                "independent_engagements_per_turn": 6,
                "kinds": ["like", "repost", "report"],
                "engaged_turn_count": 0,
                "multi_kind_engagement_turn_count": 0,
                "compound_speech_and_engagement_turn_count": 0,
                "public_interaction_count": 0,
                "counts_by_kind": {"like": 0, "repost": 0, "report": 0},
                "temporal_aggregation": "anonymous_message_deltas_by_tick",
                "record_level_interaction_identities_public": False,
            },
        },
    }
    snapshot["content_sha256"] = content_hash(snapshot)
    return snapshot


def build(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    if source == output or ROOT.resolve() not in output.parents:
        raise ValueError("output must be a new directory inside this repository")

    source_manifest = load_verified(source / "latest.json")
    tick_three = load_verified(source / "shared_baseline" / "T03.json")

    for relative in [entry["path"] for entry in source_manifest["committed_ticks"]]:
        source_path = source / relative
        load_verified(source_path)
        target_path = output / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)

    baseline_entries = []
    for tick in range(3):
        relative = f"shared_baseline/T{tick:02d}.json"
        snapshot = baseline_snapshot(tick_three, tick)
        write_canonical(output / relative, snapshot)
        baseline_entries.append(
            {
                "branch": "shared_baseline",
                "tick": tick,
                "path": relative,
                "content_sha256": snapshot["content_sha256"],
            }
        )

    manifest = copy.deepcopy(source_manifest)
    manifest["committed_ticks"] = baseline_entries + manifest["committed_ticks"]
    manifest["display_contract"] = {
        "schema_version": "campus-pulse-century-gym-display-v1",
        "mode": "operator_started_verified_tick_replay",
        "interface_ticks": list(range(11)),
        "shared_baseline_through_tick": 4,
        "branch_fork_tick": 5,
        "branches_after_fork": ["Natural", "D"],
        "default_branch": "D",
        "playback_interval_ms": 1900,
        "provider_calls_during_playback": 0,
        "case_center_entry": False,
        "stages": DISPLAY_STAGES,
    }
    manifest["content_sha256"] = content_hash(manifest)
    write_canonical(output / "latest.json", manifest)

    readme = """# 世纪馆视频演示公开运行投影\n\n该目录面向比赛视频中的“新建项目 → 启动模拟 → 逐时间步观察”流程。它从已审计的 `century-gym-live-v1` 运行派生，不修改原始运行或重新调用 Provider。\n\n- `shared_baseline/T00.json`–`T02.json`：从原 Tick 3 快照内已保存的真实基线状态拆出的无公开发言时间步；\n- `shared_baseline/T03.json`、`T04.json`：事件进入论坛后的共享前缀；\n- `Natural/T05.json`–`T10.json`：自然演化；\n- `D/T05.json`–`T10.json`：主动治理；\n- `latest.json`：17 个公开快照、来源哈希和界面展示合同。\n\n`latest.json.display_contract` 是前端与视频脚本共享的机器可读口径：演示者手动启动，界面依次播放 Tick 0–10，在 Tick 5 从共享论坛分成 Natural 与主动治理 D，默认先展示 D；播放间隔为 1.9 秒，播放期间 Provider 调用为 0，世纪馆不进入案例中心。\n\nTick 0–2 只展示人口装载、论坛初始化和共享基线冻结，不新增帖子、Claim、调用或证据。所有 Tick 3–10 公开语言仍逐字来自原 authorized-live-LLM 运行。\n"""
    (output / "README.md").write_text(readme, encoding="utf-8", newline="\n")

    files = sorted(
        path for path in output.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    sums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}\n"
        for path in files
    )
    (output / "SHA256SUMS.txt").write_text(sums, encoding="utf-8", newline="\n")

    print(json.dumps({
        "output": str(output),
        "snapshots": len(manifest["committed_ticks"]),
        "manifest_sha256": manifest["content_sha256"],
        "display_contract": manifest["display_contract"]["schema_version"],
        "provider_calls": 0,
    }, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
