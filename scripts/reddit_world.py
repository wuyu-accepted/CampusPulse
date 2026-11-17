"""Single entry point for the private CMV data acquisition pipeline."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from envs.campus_pulse_governance.reddit.ingest import (  # noqa: E402
    CMV_ARCHIVE_URL,
    RedditIngestError,
    download_archive,
    import_utterance_stream,
    inspect_private_corpus,
    safe_extract_utterances,
    split_threads_temporally,
    audit_thread_integrity,
    backfill_author_keys_from_stream,
    import_conversation_metadata,
    safe_extract_conversations,
)
from envs.campus_pulse_governance.reddit.semantics import (  # noqa: E402
    CmvSemanticError,
    embed_full_corpus,
    embed_lightweight_corpus,
    fetch_embedding_assets,
)
from envs.campus_pulse_governance.reddit.author_features import (  # noqa: E402
    CmvAuthorFeatureError,
    build_author_feature_release,
)
from envs.campus_pulse_governance.reddit.clustering import (  # noqa: E402
    CmvClusteringError,
    build_author_clustering_release,
)
from envs.campus_pulse_governance.reddit.lightweight import (  # noqa: E402
    CmvLightweightError,
    build_lightweight_release,
    validate_lightweight_release,
)
from envs.campus_pulse_governance.reddit.lightweight_clustering import (  # noqa: E402
    CmvLightweightClusteringError,
    build_lightweight_author_clusters,
    compile_fixed_world_candidates,
)
from envs.campus_pulse_governance.reddit.personas import (  # noqa: E402
    CmvPersonaError,
    build_persona_preflight,
    compile_world_from_reviewed_personas,
    execute_persona_compilation,
    _campaign_budget,
)
from envs.campus_pulse_governance.reddit.world import (  # noqa: E402
    CmvWorldError,
    verify_fixed_world_release,
)
from envs.campus_pulse_governance.reddit.episodes import (  # noqa: E402
    CmvEpisodeError,
    build_episode_scenario_context,
)
from envs.campus_pulse_governance.reddit.particle_model import (  # noqa: E402
    CmvParticleError,
    initialize_episode_particles,
    load_particle_snapshot,
    predict_tick,
)
from envs.campus_pulse_governance.reddit.runtime import (  # noqa: E402
    initialize_cmv_event_ledger,
    CmvRuntimeError,
    build_cmv_feed_snapshot,
    render_cmv_population_turns,
)
from envs.campus_pulse_governance.forum_twin import ForumLedger  # noqa: E402
from envs.campus_pulse_governance.reddit.run_plan import (  # noqa: E402
    CmvRunPlanError,
    build_cmv_small_run_plan,
    persist_cmv_small_run_plan,
)
from backend.campus_pulse.reddit_cmv_execution import (  # noqa: E402
    CmvBranchState,
    CmvRunRepository,
    execute_cmv_small_run,
    validate_cmv_execution_plan,
)
from backend.campus_pulse.reddit_cmv_trace import (  # noqa: E402
    CmvExactTraceSource,
    CmvTraceError,
)
from envs.campus_pulse_governance.forum_twin import AgentMemory  # noqa: E402
from envs.campus_pulse_governance.reddit.evaluation import (  # noqa: E402
    CmvEvaluationError,
    evaluate_cmv_paired_run,
)


def _config_path(value: str | None) -> Path:
    return Path(value).resolve() if value else ROOT / "config" / "reddit" / "cmv.json"


def _load_config(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RedditIngestError("CMV configuration could not be read") from exc
    if not isinstance(loaded, dict) or loaded.get("community") != "changemyview":
        raise RedditIngestError("CMV configuration has the wrong community contract")
    return loaded


def _private_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (ROOT / path).resolve()
    private_root = (ROOT / "data" / "private" / "reddit" / "cmv").resolve()
    if private_root != resolved and private_root not in resolved.parents:
        raise RedditIngestError("CMV inputs and outputs must remain under data/private/reddit/cmv")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acquire and inspect the private CMV archive.")
    parser.add_argument("--config", help="CMV JSON configuration path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("download", help="resume/download and checksum the official archive")
    commands.add_parser("extract", help="CRC-check and safely extract utterances and conversation metadata")
    commands.add_parser("import", help="stream utterances into the private SQLite corpus")
    commands.add_parser("inspect", help="show aggregate corpus integrity counts")
    commands.add_parser("split", help="assign whole threads to chronological train/dev/test sets")
    commands.add_parser("audit", help="summarize reply graph integrity without source identifiers")
    commands.add_parser("backfill-authors", help="repair private author keys from the verified source stream")
    commands.add_parser("metadata", help="load private conversation titles and metadata")
    commands.add_parser("fetch-model", help="download the pinned local English embedding model")
    embed_command = commands.add_parser("embed", help="build resumable private float16 corpus embeddings")
    embed_command.add_argument("--shard-rows", type=int, default=5000)
    embed_command.add_argument("--batch-size", type=int, default=16)
    embed_command.add_argument("--execution-provider", choices=("auto", "cpu", "directml"))
    commands.add_parser("author-features", help="build training-only author features and private evidence index")
    commands.add_parser("cluster-authors", help="fit train-only semantic author strata and stability diagnostics")
    commands.add_parser("select-lightweight", help="select the compact six-month history and three episodes directly from ZIP")
    commands.add_parser("verify-lightweight", help="verify compact data and event isolation without the original database")
    commands.add_parser("verify-world", help="verify the fixed 24-agent world and all 240 initial particles")
    commands.add_parser(
        "audit-runtime",
        help="render all fixed-world event inputs and run privacy/contract checks without Provider calls",
    )
    prepare_run = commands.add_parser(
        "prepare-small-run",
        help="freeze the paired three-tick CMV run plan locally; does not call a Provider",
    )
    prepare_run.add_argument("--period", choices=("development", "held_out"), default="development")
    prepare_run.add_argument("--seed", type=int, default=20260924)
    prepare_run.add_argument("--panel-size", type=int, default=12)
    replay_run = commands.add_parser(
        "run-replay",
        help="resume a frozen CMV run from an exact prompt-bound reviewed trace; never calls a Provider",
    )
    replay_run.add_argument("--plan", required=True, help="private frozen run-plan JSON path")
    replay_run.add_argument("--trace", required=True, help="private reviewed trace-release JSON path")
    replay_run.add_argument("--max-workers", type=int, default=4)
    evaluate_run = commands.add_parser(
        "evaluate-run",
        help="compute local four-dimension diagnostics from a completed Natural/D checkpoint",
    )
    evaluate_run.add_argument("--plan", required=True, help="private frozen run-plan JSON path")
    evaluate_run.add_argument("--blind-reviews", help="optional score-only review JSON under the private CMV directory")
    preflight_run = commands.add_parser(
        "preflight-small-run",
        help="audit a frozen run's slot, privacy and token-reservation readiness without Provider calls",
    )
    preflight_run.add_argument("--plan", required=True, help="private frozen run-plan JSON path")
    preflight_run.add_argument("--token-limit", type=int, default=300_000)
    light_embed = commands.add_parser("embed-lightweight", help="embed only the compact selection into resumable shards")
    light_embed.add_argument("--shard-rows", type=int, default=5000)
    light_embed.add_argument("--batch-size", type=int, default=32)
    light_embed.add_argument("--execution-provider", choices=("cpu", "directml"), default="cpu")
    commands.add_parser("cluster-lightweight", help="cluster compact pre-event author histories")
    commands.add_parser("compile-world-candidates", help="select 24 grounded candidates and 240 initial particles")
    commands.add_parser("persona-preflight", help="freeze 24 evidence-bound persona prompts without calling a provider")
    persona_run = commands.add_parser("run-personas", help="execute the 24 frozen persona prompts with a shared budget ledger")
    persona_run.add_argument("--api-key-file", default="../API.txt", help="local key file path; key text is never printed or persisted")
    persona_run.add_argument("--token-limit", type=int, default=200_000, help="persona-stage token ceiling, bounded by preflight and campaign limits")
    persona_run.add_argument("--request-limit", type=int, default=48, help="persona-stage HTTP request ceiling, including one format repair per slot")
    persona_run.add_argument("--timeout", type=float, default=90.0)
    persona_compile = commands.add_parser("compile-reviewed-world", help="compile the fixed world from 24 reviewed LLM receipts")
    persona_compile.add_argument("--receipts", default="latest", help="private receipt file path or latest completed persona run")
    persona_compile.add_argument("--output-dir", help="new private world directory; existing world assets are never overwritten")
    args = parser.parse_args(argv)

    try:
        config_path = _config_path(args.config)
        config = _load_config(config_path)
        compiled_dir = _private_path(config["world"].get("compiled_dir", config["lightweight"]["output_dir"]))
        source = config["source"]
        if args.command == "download":
            target = _private_path(source["archive_path"])
            result = download_archive(
                str(source.get("url") or CMV_ARCHIVE_URL),
                target,
                minimum_free_gib=float(source.get("minimum_free_gib", 20)),
                max_retries=int(source.get("download_retries", 3)),
            )
            print(json.dumps({"status": "downloaded", "bytes": result.byte_size, "sha256": result.sha256, "final_url": result.final_url}, indent=2))
        elif args.command == "extract":
            archive = _private_path(source["archive_path"])
            target_dir = _private_path(source["extraction_path"])
            utterances = safe_extract_utterances(archive, target_dir)
            conversations = safe_extract_conversations(archive, target_dir)
            print(json.dumps({
                "status": "extracted",
                "utterances_bytes": utterances.stat().st_size,
                "conversation_metadata_bytes": conversations.stat().st_size,
            }, indent=2))
        elif args.command == "import":
            archive = _private_path(source["archive_path"])
            extraction = _private_path(source["extraction_path"])
            utterances = safe_extract_utterances(archive, extraction)
            conversations = safe_extract_conversations(archive, extraction)
            import hashlib
            source_sha256 = hashlib.sha256()
            with archive.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    source_sha256.update(chunk)
            result = import_utterance_stream(
                utterances,
                _private_path(source["database_path"]),
                salt_path=_private_path(source["author_salt_path"]),
                source_sha256=source_sha256.hexdigest(),
                source_byte_size=archive.stat().st_size,
                source_url=str(source.get("url") or CMV_ARCHIVE_URL),
                batch_size=int(source.get("import_batch_size", 2000)),
            )
            author_backfill = backfill_author_keys_from_stream(
                utterances,
                _private_path(source["database_path"]),
                salt_path=_private_path(source["author_salt_path"]),
            )
            metadata_result = import_conversation_metadata(
                conversations,
                _private_path(source["database_path"]),
            )
            print(json.dumps({
                "status": "imported",
                "records_seen": result.records_seen,
                "records_inserted": result.records_inserted,
                "exact_duplicates": result.exact_duplicates,
                "conflicts": result.conflicts,
                "tombstones": result.tombstones,
                "author_backfill": author_backfill,
                "conversation_metadata": metadata_result,
            }, indent=2))
        elif args.command == "inspect":
            result = inspect_private_corpus(_private_path(source["database_path"]))
            print(json.dumps(result, indent=2))
        elif args.command == "split":
            split_config = config.get("temporal_split", {})
            result = split_threads_temporally(
                _private_path(source["database_path"]),
                train_end_utc=str(split_config.get("train_end_utc", "2016-12-31T23:59:59Z")),
                development_end_utc=str(split_config.get("development_end_utc", "2017-12-31T23:59:59Z")),
            )
            print(json.dumps(result, indent=2))
        elif args.command == "audit":
            print(json.dumps(audit_thread_integrity(_private_path(source["database_path"])), indent=2))
        elif args.command == "backfill-authors":
            archive = _private_path(source["archive_path"])
            extraction = _private_path(source["extraction_path"])
            utterances = safe_extract_utterances(archive, extraction)
            print(json.dumps(backfill_author_keys_from_stream(
                utterances,
                _private_path(source["database_path"]),
                salt_path=_private_path(source["author_salt_path"]),
            ), indent=2))
        elif args.command == "metadata":
            archive = _private_path(source["archive_path"])
            extraction = _private_path(source["extraction_path"])
            safe_extract_utterances(archive, extraction)
            conversations = safe_extract_conversations(archive, extraction)
            print(json.dumps(import_conversation_metadata(
                conversations,
                _private_path(source["database_path"]),
            ), indent=2))
        elif args.command == "fetch-model":
            embedding = config["embedding"]
            print(json.dumps(fetch_embedding_assets(_private_path(embedding["model_dir"])), indent=2))
        elif args.command == "embed":
            embedding = config["embedding"]
            provider = args.execution_provider or str(embedding.get("execution_provider", "auto"))
            private_libs = _private_path(embedding.get("python_libs", "data/private/reddit/cmv/python-libs"))
            if provider in {"auto", "directml"}:
                directml_libs = _private_path(embedding.get("python_libs_directml", "data/private/reddit/cmv/python-libs-directml"))
                if directml_libs.is_dir():
                    sys.path.insert(0, str(directml_libs))
            if private_libs.is_dir():
                sys.path.insert(1 if sys.path and sys.path[0].endswith("python-libs-directml") else 0, str(private_libs))
            def report_progress(event: dict) -> None:
                print(json.dumps(event, ensure_ascii=False), flush=True)

            print(json.dumps(embed_full_corpus(
                _private_path(source["database_path"]),
                _private_path(embedding["model_dir"]),
                _private_path(embedding["shards_dir"]),
                shard_rows=args.shard_rows,
                batch_size=args.batch_size,
                execution_provider=provider,
                progress_callback=report_progress,
            ), indent=2))
        elif args.command == "author-features":
            world = config["world"]
            result = build_author_feature_release(
                _private_path(source["database_path"]),
                _private_path(config.get("author_analysis_dir", "data/private/reddit/cmv/analysis")),
                salt_path=_private_path(source["author_salt_path"]),
                min_history=int(world.get("min_history", 12)),
                min_threads=int(world.get("min_threads", 4)),
                min_span_days=float(world.get("min_span_days", 60)),
                evidence_limit=int(world.get("evidence_limit", 12)),
            )
            print(json.dumps(result, indent=2))
        elif args.command == "cluster-authors":
            author_dir = _private_path(config.get("author_analysis_dir", "data/private/reddit/cmv/analysis"))
            embedding = config["embedding"]
            clustering = config.get("clustering", {})
            result = build_author_clustering_release(
                author_features_path=author_dir / "author-features.private.jsonl",
                author_evidence_path=author_dir / "author-evidence.private.jsonl",
                embedding_dir=_private_path(embedding["shards_dir"]),
                output_dir=author_dir,
                candidate_k=clustering.get("candidate_k", [4, 6, 8]),
                min_cluster_authors=int(clustering.get("min_cluster_authors", 30)),
                content_weight=float(clustering.get("content_weight", 0.70)),
                seed=int(config["world"].get("seed", 20260924)),
                silhouette_sample_size=int(clustering.get("silhouette_sample_size", 1500)),
                stability_repeats=int(clustering.get("stability_repeats", 3)),
            )
            print(json.dumps(result, indent=2))
        elif args.command == "select-lightweight":
            print(json.dumps(build_lightweight_release(
                _private_path(source["archive_path"]),
                _private_path(source["author_salt_path"]),
                _private_path(config["lightweight"]["output_dir"]),
                seed=int(config["world"].get("seed", 20260924)),
            ), indent=2))
        elif args.command == "verify-lightweight":
            print(json.dumps(validate_lightweight_release(
                _private_path(config["lightweight"]["output_dir"])
            ), indent=2))
        elif args.command == "verify-world":
            release_dir = _private_path(config["lightweight"]["output_dir"])
            print(json.dumps(verify_fixed_world_release(
                compiled_dir / "fixed-world.private.json",
                compiled_dir / "initial-particles.private.npz",
            ), indent=2))
        elif args.command == "audit-runtime":
            release_dir = _private_path(config["lightweight"]["output_dir"])
            verify_fixed_world_release(
                compiled_dir / "fixed-world.private.json",
                compiled_dir / "initial-particles.private.npz",
            )
            world = json.loads((compiled_dir / "fixed-world.private.json").read_text(encoding="utf-8"))
            observations = json.loads((release_dir / "episode-observations.private.json").read_text(encoding="utf-8"))
            if not isinstance(observations, list) or len(observations) != 3:
                raise CmvEpisodeError("the fixed CMV release must contain three visible event observations")
            base_particles = load_particle_snapshot(
                compiled_dir / "initial-particles.private.npz",
                expected_sha256=str(world.get("particle_asset_sha256") or ""),
            )
            model = str(config.get("provider", {}).get("model") or "")
            if not model:
                raise CmvRuntimeError("configured Provider model is missing")
            rendered_count = 0
            prompt_char_lengths: list[int] = []
            prompt_byte_lengths: list[int] = []
            system_char_lengths: list[int] = []
            resident_char_lengths: list[int] = []
            payload_section_max: dict[str, int] = {}
            episode_summaries = []
            for observation in observations:
                scenario_context = build_episode_scenario_context(observation)
                event_particles = initialize_episode_particles(
                    base_particles,
                    {},
                    event_id=str(observation["episode_id"]),
                    world_seed=int(world.get("world_seed", config["world"].get("seed", 20260924))),
                )
                event_ledger = initialize_cmv_event_ledger(scenario_context)
                feed = build_cmv_feed_snapshot(world=world, ledger=event_ledger, scenario_id=str(observation["episode_id"]), branch="shared_baseline", tick=1, relationship_release=world["relationships"])
                event_particles = predict_tick(event_particles, tick=1, exposures={agent_id: min(1.0, sum(row.rank_score for row in rows[:6]) / 2.0) for agent_id, rows in feed.exposures_by_agent.items()}, seed=int(world.get("world_seed", 20260924)))
                rendered = render_cmv_population_turns(
                    world=world,
                    particles=event_particles,
                    episode_context=scenario_context,
                    ledger=event_ledger,
                    scenario_id=str(observation["episode_id"]),
                    branch="natural",
                    tick=1,
                    expected_model=model,
                    exposures_by_agent=feed.exposures_by_agent, public_board=feed.public_board,
                )
                if len(rendered) != 24:
                    raise CmvRuntimeError("runtime audit did not render every fixed resident")
                for turn in rendered.values():
                    prompt = turn.system_prompt + "\n" + turn.agent_prompt
                    if len(turn.prompt_sha256) != 64:
                        raise CmvRuntimeError("runtime audit found an invalid prompt fingerprint")
                    prompt_char_lengths.append(len(prompt))
                    prompt_byte_lengths.append(len(prompt.encode("utf-8")))
                    system_char_lengths.append(len(turn.system_prompt))
                    resident_char_lengths.append(len(turn.agent_prompt))
                    payload = json.loads(turn.agent_prompt)
                    root_text = scenario_context["observed_prefix"][0]["text"]
                    if not any(row.get("visible_text") == root_text for row in payload.get("forum_context", [])):
                        raise CmvRuntimeError("opened event root was truncated or omitted")
                    state_view = payload.get("posterior_state", {})
                    if len(state_view.get("state_means", {})) != 12 or state_view.get("particle_count") != 10:
                        raise CmvRuntimeError("rendered input lost its actual particle constraint")
                    if not payload.get("profile", {}).get("reviewed_behavior_examples"):
                        raise CmvRuntimeError("rendered input lost its reviewed historical evidence")
                    for key, value in payload.items():
                        section_size = len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                        payload_section_max[key] = max(payload_section_max.get(key, 0), section_size)
                rendered_count += len(rendered)
                episode_summaries.append({
                    "period": str(observation.get("period") or ""),
                    "topic_family": str(observation.get("topic_family") or ""),
                    "rendered_resident_turns": len(rendered),
                    "privacy_and_contract_checks": "passed",
                    "full_root_and_particle_constraint_checks": "passed",
                })
            print(json.dumps({
                "status": "passed",
                "provider_calls_started": False,
                "episode_count": len(episode_summaries),
                "rendered_resident_turns": rendered_count,
                "prompt_chars_per_turn": {
                    "minimum": min(prompt_char_lengths),
                    "maximum": max(prompt_char_lengths),
                },
                "prompt_utf8_bytes_per_turn": {
                    "minimum": min(prompt_byte_lengths),
                    "maximum": max(prompt_byte_lengths),
                },
                "prompt_parts_chars_per_turn": {
                    "system": {"minimum": min(system_char_lengths), "maximum": max(system_char_lengths)},
                    "resident_input": {"minimum": min(resident_char_lengths), "maximum": max(resident_char_lengths)},
                },
                "largest_payload_sections_max_chars": dict(sorted(payload_section_max.items(), key=lambda item: (-item[1], item[0]))[:8]),
                "episodes": episode_summaries,
            }, ensure_ascii=False, indent=2))
        elif args.command == "prepare-small-run":
            release_dir = _private_path(config["lightweight"]["output_dir"])
            verify_fixed_world_release(
                compiled_dir / "fixed-world.private.json",
                compiled_dir / "initial-particles.private.npz",
            )
            world = json.loads((compiled_dir / "fixed-world.private.json").read_text(encoding="utf-8"))
            observations = json.loads((release_dir / "episode-observations.private.json").read_text(encoding="utf-8"))
            if not isinstance(observations, list):
                raise CmvEpisodeError("episode observation release must be a list")
            selected = [row for row in observations if isinstance(row, dict) and row.get("period") == args.period]
            if len(selected) != 1:
                raise CmvEpisodeError("requested period must resolve to exactly one frozen event")
            observation = selected[0]
            # Build once before writing the plan so privacy/visibility failures
            # cannot leave a superficially valid run directory behind.
            build_episode_scenario_context(observation)
            plan = build_cmv_small_run_plan(
                world=world,
                episode_id=str(observation["episode_id"]),
                seed=args.seed,
                panel_size=args.panel_size,
            )
            result = persist_cmv_small_run_plan(
                plan,
                _private_path("data/private/reddit/cmv/runs"),
            )
            result.update({
                "period": args.period,
                "timeline_ticks": plan["timeline"]["ticks"],
                "primary_request_count": plan["provider_policy"]["primary_request_count"],
                "maximum_http_requests": plan["provider_policy"]["maximum_http_requests"],
                "format_repair_policy": "one repair per settled invalid response",
                "provider_calls_started": False,
                "provider_ready": False,
            })
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "run-replay":
            if not 1 <= args.max_workers <= 8:
                raise CmvRunPlanError("replay worker count must be between one and eight")
            plan_path = _private_path(args.plan)
            trace_path = _private_path(args.trace)
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                trace_release = json.loads(trace_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RedditIngestError("private CMV replay inputs could not be read") from exc
            if not isinstance(plan, dict) or not isinstance(trace_release, dict):
                raise RedditIngestError("private CMV replay inputs must be JSON objects")
            plan = validate_cmv_execution_plan(plan)
            run_id = "cmv-run-" + str(plan["plan_sha256"])[:20]
            if plan_path.name != "run-plan.private.json" or plan_path.parent.name != run_id:
                raise CmvRunPlanError("replay plan is not in its content-bound private run directory")
            release_dir = _private_path(config["lightweight"]["output_dir"])
            world_path = compiled_dir / "fixed-world.private.json"
            particle_path = compiled_dir / "initial-particles.private.npz"
            verify_fixed_world_release(world_path, particle_path)
            world = json.loads(world_path.read_text(encoding="utf-8"))
            world_sha256 = str(world.get("world_sha256") or "")
            if plan.get("world_sha256") != world_sha256:
                raise CmvRunPlanError("replay plan does not bind to the installed fixed world")
            observations = json.loads((release_dir / "episode-observations.private.json").read_text(encoding="utf-8"))
            matching = [
                row for row in observations
                if isinstance(row, dict) and row.get("episode_id") == plan.get("episode_id")
            ]
            if len(matching) != 1:
                raise CmvEpisodeError("replay plan must reference exactly one frozen visible episode")
            episode_context = build_episode_scenario_context(matching[0])
            neutral = load_particle_snapshot(
                particle_path,
                expected_sha256=str(world.get("particle_asset_sha256") or ""),
            )
            episode_particles = initialize_episode_particles(
                neutral,
                {},
                event_id=str(plan["episode_id"]),
                world_seed=int(world.get("world_seed", config["world"].get("seed", 20260924))),
            )
            initial_state = CmvBranchState(
                scenario_id=str(plan["episode_id"]),
                branch="shared_baseline",
                tick=0,
                particles=episode_particles,
                ledger=initialize_cmv_event_ledger(episode_context),
                memories_by_agent={
                    agent_id: AgentMemory(agent_id=agent_id)
                    for agent_id in episode_particles.agent_ids
                },
            )
            model = str(config.get("provider", {}).get("model") or "")
            source = CmvExactTraceSource(
                trace_release,
                expected_model=model,
                expected_plan_sha256=str(plan["plan_sha256"]),
                expected_world_sha256=world_sha256,
            )
            repository = CmvRunRepository(
                plan_path.parent,
                run_id=run_id,
                plan_sha256=str(plan["plan_sha256"]),
                world_sha256=world_sha256,
            )
            result = execute_cmv_small_run(
                plan=plan,
                world=world,
                initial_state=initial_state,
                episode_context=episode_context,
                relationship_release=world["relationships"],
                source=source,
                expected_model=model,
                run_id=run_id,
                repository=repository,
                reviewed_trace_release_sha256=source.trace_release_sha256,
                max_workers=args.max_workers,
            )
            print(json.dumps({
                "status": "complete",
                "run_id": result["run_id"],
                "episode_id": result["episode_id"],
                "source_provenance": result["source_provenance"],
                "provider_calls_started": False,
                "trace_release_sha256": source.trace_release_sha256,
                "governance_rounds_committed": result["governance_rounds_committed"],
                "branches": {
                    branch: {
                        "tick": row["tick"],
                        "public_messages": row["public_messages"],
                        "private_messages": row["private_messages"],
                        "interactions": row["interactions"],
                    }
                    for branch, row in result["branches"].items()
                },
                "completion_event_sha256": result["completion_event_sha256"],
            }, ensure_ascii=False, indent=2))
        elif args.command == "evaluate-run":
            plan_path = _private_path(args.plan)
            try:
                plan = validate_cmv_execution_plan(json.loads(plan_path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RedditIngestError("private CMV evaluation plan could not be read") from exc
            run_id = "cmv-run-" + str(plan["plan_sha256"])[:20]
            if plan_path.name != "run-plan.private.json" or plan_path.parent.name != run_id:
                raise CmvRunPlanError("evaluation plan is not in its content-bound private run directory")
            release_dir = _private_path(config["lightweight"]["output_dir"])
            world_path = compiled_dir / "fixed-world.private.json"
            particle_path = compiled_dir / "initial-particles.private.npz"
            verify_fixed_world_release(world_path, particle_path)
            world = json.loads(world_path.read_text(encoding="utf-8"))
            world_sha256 = str(world.get("world_sha256") or "")
            if plan.get("world_sha256") != world_sha256:
                raise CmvRunPlanError("evaluation plan does not bind to the installed fixed world")
            repository = CmvRunRepository(
                plan_path.parent,
                run_id=run_id,
                plan_sha256=str(plan["plan_sha256"]),
                world_sha256=world_sha256,
            )
            completion = repository.runtime_artifacts(
                "run_completed",
                scenario_id=str(plan["episode_id"]),
                branch="D",
                tick=3,
            )
            natural_state = repository.restore_latest("natural")
            governed_state = repository.restore_latest("D")
            if (
                not completion
                or natural_state is None
                or governed_state is None
                or natural_state.tick != 3
                or governed_state.tick != 3
            ):
                raise CmvRunPlanError("four-dimension evaluation requires a completed paired Natural/D run")
            evaluation_path = release_dir / "episode-evaluation-only.private.jsonl.gz"
            target_rows = None
            try:
                with gzip.open(evaluation_path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        row = json.loads(line)
                        if isinstance(row, dict) and row.get("episode_id") == plan["episode_id"]:
                            target_rows = row.get("targets")
                            break
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CmvEpisodeError("evaluation-only continuation could not be read") from exc
            if not isinstance(target_rows, list):
                raise CmvEpisodeError("evaluation-only continuation for this run was not found")
            blind_reviews = None
            if args.blind_reviews:
                review_path = _private_path(args.blind_reviews)
                try:
                    blind_reviews = json.loads(review_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise CmvEvaluationError("blind review file could not be read") from exc
                if not isinstance(blind_reviews, list):
                    raise CmvEvaluationError("blind review file must contain a score-only JSON array")
            report = evaluate_cmv_paired_run(
                natural_state=natural_state,
                governed_state=governed_state,
                world=world,
                reference_targets=target_rows,
                blind_persona_reviews=blind_reviews,
            )
            report_artifact = {
                "run_id": run_id,
                "episode_id": str(plan["episode_id"]),
                "report": report,
            }
            report_event = repository.append_runtime_artifact(
                "evaluation_report",
                report_artifact,
                scenario_id=str(plan["episode_id"]),
                branch="D",
                tick=3,
            )
            print(json.dumps({
                "status": "evaluated",
                "provider_calls_started": False,
                "run_id": run_id,
                "episode_id": str(plan["episode_id"]),
                "dimension_status": {
                    key: value["status"] for key, value in report["dimensions"].items()
                },
                "natural": report["dimensions"]["governance_response"]["natural"],
                "D": report["dimensions"]["governance_response"]["D"],
                "evaluation_event_sha256": report_event["event_sha256"],
            }, ensure_ascii=False, indent=2))
        elif args.command == "preflight-small-run":
            if isinstance(args.token_limit, bool) or args.token_limit <= 0:
                raise CmvRunPlanError("preflight token limit must be a positive integer")
            plan_path = _private_path(args.plan)
            try:
                plan = validate_cmv_execution_plan(json.loads(plan_path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RedditIngestError("private CMV preflight plan could not be read") from exc
            run_id = "cmv-run-" + str(plan["plan_sha256"])[:20]
            if plan_path.name != "run-plan.private.json" or plan_path.parent.name != run_id:
                raise CmvRunPlanError("preflight plan is not in its content-bound private run directory")
            release_dir = _private_path(config["lightweight"]["output_dir"])
            world_path = compiled_dir / "fixed-world.private.json"
            particle_path = compiled_dir / "initial-particles.private.npz"
            verify_fixed_world_release(world_path, particle_path)
            world = json.loads(world_path.read_text(encoding="utf-8"))
            if str(world.get("world_sha256") or "") != plan["world_sha256"]:
                raise CmvRunPlanError("preflight plan does not bind to the installed fixed world")
            observations = json.loads((release_dir / "episode-observations.private.json").read_text(encoding="utf-8"))
            matching = [row for row in observations if isinstance(row, dict) and row.get("episode_id") == plan["episode_id"]]
            if len(matching) != 1:
                raise CmvEpisodeError("preflight plan must reference one frozen visible episode")
            episode_context = build_episode_scenario_context(matching[0])
            particles = initialize_episode_particles(
                load_particle_snapshot(particle_path, expected_sha256=str(world.get("particle_asset_sha256") or "")),
                {},
                event_id=str(plan["episode_id"]),
                world_seed=int(world.get("world_seed", config["world"].get("seed", 20260924))),
            )
            # Preflight must render the same first-tick input as the executor:
            # global hot/latest board, recipient exposures, and the particle
            # prediction for tick 1. Rendering the tick-0 prior directly would
            # both fail the tick contract and understate the live prompt.
            relationship_release = world["relationships"]
            initial_ledger = initialize_cmv_event_ledger(episode_context)
            feed = build_cmv_feed_snapshot(
                world=world,
                ledger=initial_ledger,
                scenario_id=str(plan["episode_id"]),
                branch="shared_baseline",
                tick=1,
                relationship_release=relationship_release,
            )
            exposure_signal = {
                agent_id: min(
                    1.0,
                    sum(row.rank_score for row in feed.exposures_by_agent[agent_id][:6]) / 2.0,
                )
                for agent_id in particles.agent_ids
            }
            tick_one_particles = predict_tick(
                particles,
                tick=1,
                exposures=exposure_signal,
                contested_claim_signal={},
                correction_signal={},
                service_resolution={},
                seed=int(plan["seed"]),
            )
            shared_slots = [row for row in plan["resident_slots"] if row.get("stage") == "shared_prefix"]
            rendered = render_cmv_population_turns(
                world=world,
                particles=tick_one_particles,
                episode_context=episode_context,
                ledger=initial_ledger,
                scenario_id=str(plan["episode_id"]),
                branch="shared_baseline",
                tick=1,
                expected_model=str(config.get("provider", {}).get("model") or ""),
                activated_agent_ids=tuple(sorted(str(row["agent_id"]) for row in shared_slots)),
                exposures_by_agent=feed.exposures_by_agent,
                memories_by_agent={
                    agent_id: AgentMemory(agent_id=agent_id)
                    for agent_id in particles.agent_ids
                },
                public_board=feed.public_board,
            )
            if len(rendered) != len(shared_slots):
                raise CmvRuntimeError("preflight could not render the exact shared-prefix resident slots")
            prompt_bytes = [
                len((turn.system_prompt + "\n" + turn.agent_prompt).encode("utf-8"))
                for turn in rendered.values()
            ]
            budget_path = _private_path("data/private/reddit/cmv/budget/cmv-implementation/provider-budget.private.json")
            campaign = _campaign_budget(budget_path)
            token_room = int(campaign["max_total_tokens"]) - int(campaign["tokens_committed"]) - int(campaign["tokens_reserved"])
            request_room = int(campaign["max_http_generation_requests"]) - int(campaign["requests_dispatched"])
            provider = config.get("provider", {})
            tokenizer_release = provider.get("tokenizer_release")
            tokenizer_ready_fields = {
                "model",
                "tokenizer_id",
                "revision",
                "files_sha256",
                "verification_method",
                "verified_for_model",
            }
            tokenization_ready = (
                isinstance(tokenizer_release, dict)
                and tokenizer_ready_fields <= set(tokenizer_release)
                and tokenizer_release.get("verified_for_model") is True
                and tokenizer_release.get("model") == provider.get("model")
                and isinstance(tokenizer_release.get("tokenizer_id"), str)
                and bool(tokenizer_release.get("tokenizer_id").strip())
                and isinstance(tokenizer_release.get("revision"), str)
                and bool(tokenizer_release.get("revision").strip())
                and isinstance(tokenizer_release.get("files_sha256"), list)
                and bool(tokenizer_release.get("files_sha256"))
                and all(
                    isinstance(value, str)
                    and len(value) == 64
                    and all(character in "0123456789abcdef" for character in value)
                    for value in tokenizer_release.get("files_sha256", [])
                )
                and isinstance(tokenizer_release.get("verification_method"), str)
                and bool(tokenizer_release.get("verification_method").strip())
            )
            requested_requests = int(plan["provider_policy"]["maximum_http_requests"])
            report = {
                "schema_version": "reddit-cmv-provider-preflight-v1",
                "run_id": run_id,
                "plan_sha256": plan["plan_sha256"],
                "world_sha256": world["world_sha256"],
                "episode_id": plan["episode_id"],
                "provider": {
                    "endpoint": provider.get("endpoint"),
                    "model": provider.get("model"),
                    "reasoning": provider.get("reasoning"),
                    "timeout_seconds": provider.get("timeout_seconds"),
                    "concurrency": provider.get("concurrency"),
                },
                "slots": dict(plan["slot_counts"]),
                "request_ceiling": requested_requests,
                "requested_token_ceiling": args.token_limit,
                "remaining_campaign_requests": request_room,
                "remaining_campaign_tokens": token_room,
                "shared_prefix_prompt_utf8_bytes": {
                    "minimum": min(prompt_bytes),
                    "maximum": max(prompt_bytes),
                    "rendered_count": len(prompt_bytes),
                },
                "fields_in_scope": [
                    "synthetic persona and selected pre-event history",
                    "qualitative current state and scenario stake",
                    "visible four-message historical event prefix",
                    "time-visible synthetic public feed and eligible private inbox",
                    "ForumTwin resident or role-limited governance JSON contract",
                ],
                "fields_excluded": [
                    "raw source identifiers and account names",
                    "held-out continuation and evaluation labels",
                    "hidden population truth",
                    "filesystem paths and credentials",
                ],
                "checks": {
                    "plan_world_and_event_binding": "passed",
                    "visible_prompt_render_and_privacy_contract": "passed",
                    "tokenizer_release": "passed" if tokenization_ready else "blocked: no model-verified tokenizer release is configured",
                    "request_budget": "passed" if requested_requests <= request_room else "blocked: campaign request room is insufficient",
                    "token_budget": "passed" if args.token_limit <= token_room else "blocked: campaign token room is insufficient",
                    "single_use_live_authorization": "not_present",
                },
                "provider_calls_started": False,
                "ready": bool(tokenization_ready and requested_requests <= request_room and args.token_limit <= token_room),
            }
            report["status"] = "ready_for_separate_authorization" if report["ready"] else "blocked"
            repository = CmvRunRepository(
                plan_path.parent,
                run_id=run_id,
                plan_sha256=str(plan["plan_sha256"]),
                world_sha256=str(world["world_sha256"]),
            )
            event = repository.append_runtime_artifact(
                "provider_preflight",
                report,
                scenario_id=str(plan["episode_id"]),
                branch="shared_baseline",
                tick=0,
            )
            report["preflight_event_sha256"] = event["event_sha256"]
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "embed-lightweight":
            release_dir = _private_path(config["lightweight"]["output_dir"])
            verified = validate_lightweight_release(release_dir)
            embedding = config["embedding"]
            private_libs = _private_path(embedding.get("python_libs", "data/private/reddit/cmv/python-libs"))
            if args.execution_provider == "directml":
                directml_libs = _private_path(embedding.get("python_libs_directml", "data/private/reddit/cmv/python-libs-directml"))
                if directml_libs.is_dir():
                    sys.path.insert(0, str(directml_libs))
            if private_libs.is_dir():
                sys.path.insert(1 if args.execution_provider == "directml" else 0, str(private_libs))
            def report_light_progress(event: dict) -> None:
                print(json.dumps(event, ensure_ascii=False), flush=True)
            print(json.dumps(embed_lightweight_corpus(
                release_dir / "background.private.jsonl.gz",
                _private_path(embedding["model_dir"]),
                release_dir / f"embeddings-{args.execution_provider}",
                expected_rows=int(verified["background_rows"]),
                shard_rows=args.shard_rows,
                batch_size=args.batch_size,
                execution_provider=args.execution_provider,
                progress_callback=report_light_progress,
            ), indent=2))
        elif args.command == "cluster-lightweight":
            print(json.dumps(build_lightweight_author_clusters(
                _private_path(config["lightweight"]["output_dir"]),
                seed=int(config["world"].get("seed", 20260924)),
                candidate_k=tuple(int(k) for k in config.get("clustering", {}).get("candidate_k", [4, 6, 8])),
                min_cluster_authors=int(config.get("clustering", {}).get("min_cluster_authors", 30)),
            ), indent=2))
        elif args.command == "compile-world-candidates":
            print(json.dumps(compile_fixed_world_candidates(
                _private_path(config["lightweight"]["output_dir"]),
                seed=int(config["world"].get("seed", 20260924)),
            ), indent=2))
        elif args.command == "persona-preflight":
            print(json.dumps(build_persona_preflight(
                _private_path(config["lightweight"]["output_dir"]),
                model=str(config["provider"]["model"]),
            ), indent=2))
        elif args.command == "run-personas":
            print(json.dumps(execute_persona_compilation(
                _private_path(config["lightweight"]["output_dir"]),
                api_key_file=Path(args.api_key_file).resolve(),
                campaign_dir=_private_path("data/private/reddit/cmv/budget/cmv-implementation"),
                max_total_tokens=args.token_limit,
                request_limit=args.request_limit,
                timeout_seconds=args.timeout,
            ), indent=2))
        elif args.command == "compile-reviewed-world":
            release_dir = _private_path(config["lightweight"]["output_dir"])
            if args.receipts == "latest":
                current_preflight = json.loads((release_dir / "persona-preflight.private.json").read_text(encoding="utf-8"))
                receipts_path = release_dir / "persona-runs" / current_preflight["preflight_sha256"] / "persona-receipts.private.json"
            else:
                receipts_path = _private_path(args.receipts)
            print(json.dumps(compile_world_from_reviewed_personas(
                release_dir,
                receipts_path,
                output_dir=_private_path(args.output_dir) if args.output_dir else None,
            ), indent=2))
        return 0
    except (RedditIngestError, CmvSemanticError, CmvAuthorFeatureError, CmvClusteringError, CmvLightweightError, CmvLightweightClusteringError, CmvPersonaError, CmvWorldError, CmvEpisodeError, CmvParticleError, CmvRuntimeError, CmvRunPlanError, CmvTraceError, CmvEvaluationError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"CMV pipeline stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
