"""Zero-Provider operator entrypoints for CampusPulse MassSim."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

try:
    import resource
except ImportError:  # pragma: no cover - Windows operator fallback
    resource = None

from src.envs.campus_pulse_governance.mass_engine import (
    build_aggregate_result_v3,
    build_run_plan_v5,
    evaluate_writeback_modes,
    run_seed_scenario,
)
from src.envs.campus_pulse_governance.mass_population import (
    build_synthetic_exposure_graph,
    compile_semantic_population_v2,
)
from src.envs.campus_pulse_governance.mass_simulation import (
    evaluate_scale_gap,
    load_scale_gap_cells,
)


DEFAULT_SCENARIOS = (
    "lecture_external_incident_shock",
    "tongzhou_governance_information_shock",
)
DEFAULT_SEEDS = (20260722, 20261731)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run zero-Provider CampusPulse MassSim gates."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scale_gap = subparsers.add_parser(
        "scale-gap",
        help="Evaluate budgeted recovery of reviewed 48-agent populations.",
    )
    scale_gap.add_argument(
        "--result",
        action="append",
        required=True,
        help="Reviewed 48-agent result JSON; repeat for multiple seeds.",
    )
    scale_gap.add_argument("--output", required=True)
    scale_gap.add_argument("--repetitions", type=int, default=200)
    scale_gap.add_argument("--seed", type=int, default=20260730)
    release = subparsers.add_parser(
        "build-release",
        help="Build the complete zero-Provider 1,000-episode MassSim release.",
    )
    release.add_argument(
        "--posts",
        default="data/private/campus_pulse/treehole_posts_with_realdate.csv",
    )
    release.add_argument(
        "--semantic",
        default="_run/semantic-pilot-v1_5_1-1000.json",
    )
    release.add_argument(
        "--taxonomy",
        default="_run/taxonomy-release-v2_3-reviewed-1000.json",
    )
    release.add_argument(
        "--scale-gap-report",
        default="_run/mass-sim-scale-gap-v1.json",
    )
    release.add_argument(
        "--output-dir",
        default="_run/mass-sim-v1",
    )
    release.add_argument(
        "--public-output",
        default="src/frontend/public/campus-pulse-data/mass-sim-v1.json",
    )
    release.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="Frozen shock scenario ID; repeat for multiple shocks.",
    )
    release.add_argument(
        "--seed",
        action="append",
        type=int,
        dest="seeds",
        help="Frozen simulation seed; repeat for multiple seeds.",
    )
    return parser


def _write_json(path: str | Path, payload: object) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


def _checkpoint_manifest(scenario_runs):
    records = []
    for scenario_id, runs in scenario_runs.items():
        for run in runs:
            for checkpoint in run["shared_baseline"]["checkpoints"]:
                records.append(
                    {
                        **checkpoint,
                        "seed": run["seed"],
                        "policy_branch": "shared",
                    }
                )
            for branch, branch_run in run["branches"].items():
                for checkpoint in branch_run["checkpoints"]:
                    records.append(
                        {
                            **checkpoint,
                            "seed": run["seed"],
                            "policy_branch": branch,
                        }
                    )
    return {
        "schema_version": "campus-pulse-population-checkpoint-set-v1",
        "record_count": len(records),
        "records": records,
    }


def _build_release(args) -> dict:
    scenarios = tuple(args.scenarios or DEFAULT_SCENARIOS)
    seeds = tuple(args.seeds or DEFAULT_SEEDS)
    if len(scenarios) != 2 or len(set(scenarios)) != 2:
        raise ValueError("MassSim v1 requires exactly two distinct shock scenarios")
    if len(seeds) != 2 or len(set(seeds)) != 2:
        raise ValueError("MassSim v1 requires exactly two distinct seeds")
    population = compile_semantic_population_v2(
        posts_path=args.posts,
        semantic_path=args.semantic,
        taxonomy_path=args.taxonomy,
    )
    graph = build_synthetic_exposure_graph(population)
    scale_gap = json.loads(
        Path(args.scale_gap_report).read_text(encoding="utf-8")
    )
    if not scale_gap.get("gate", {}).get("passed"):
        raise ValueError("Scale-Gap gate must pass before a MassSim release")
    selected_scale_gap = next(
        row
        for row in scale_gap["rows"]
        if row["budget"] == 32 and row["method"] == "greg_pps"
    )
    writeback = evaluate_writeback_modes(
        population=population,
        graph=graph,
        scenario_id=scenarios[0],
        seeds=seeds,
        descriptive_baseline_mae=float(
            selected_scale_gap["held_out_mae"]
        ),
    )
    formal_mode = writeback["formal_mode"]
    plan = build_run_plan_v5(
        population=population,
        graph=graph,
        scenario_ids=scenarios,
        seeds=seeds,
        writeback_mode=formal_mode,
    )

    baseline_peak_memory = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        if resource is not None
        else None
    )
    started = time.perf_counter()
    scenario_runs = {
        scenario_id: [
            run_seed_scenario(
                population=population,
                graph=graph,
                scenario_id=scenario_id,
                seed=seed,
                writeback_mode=formal_mode,
            )
            for seed in seeds
        ]
        for scenario_id in scenarios
    }
    elapsed = time.perf_counter() - started
    process_peak_memory = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        if resource is not None
        else None
    )
    peak_memory = (
        max(0, process_peak_memory - baseline_peak_memory)
        if (
            process_peak_memory is not None
            and baseline_peak_memory is not None
        )
        else None
    )
    public_writeback = {
        key: value for key, value in writeback.items() if not key.startswith("_")
    }
    result = build_aggregate_result_v3(
        plan=plan,
        scenario_runs=scenario_runs,
        writeback_evaluation=public_writeback,
        elapsed_seconds=elapsed,
        peak_additional_memory_bytes=peak_memory,
    )
    output_dir = Path(args.output_dir)
    _write_json(output_dir / "semantic-population-v2.json", population)
    _write_json(output_dir / "synthetic-exposure-graph-v1.json", graph)
    _write_json(
        output_dir / "writeback-evaluation-v1.json",
        public_writeback,
    )
    _write_json(output_dir / "campus-pulse-run-plan-v5.json", plan)
    _write_json(
        output_dir / "population-checkpoints-v1.json",
        _checkpoint_manifest(scenario_runs),
    )
    _write_json(output_dir / "aggregate-result-v3.json", result)
    _write_json(args.public_output, result)
    return {
        "schema_version": result["schema_version"],
        "population_size": population["population_size"],
        "particle_count": plan["particle_layer"]["particle_count"],
        "scenario_count": len(scenarios),
        "seed_count": len(seeds),
        "formal_writeback_mode": formal_mode,
        "planned_probe_turns_per_seed": plan["sampling"][
            "planned_cold_turns_per_seed"
        ],
        "provider_calls": result["usage"]["provider_calls"],
        "engine_elapsed_seconds": result["performance"][
            "engine_elapsed_seconds"
        ],
        "peak_additional_memory_bytes": peak_memory,
        "result_sha256": result["result_sha256"],
        "output_dir": str(output_dir),
        "public_output": str(args.public_output),
    }


def main() -> None:
    args = _parser().parse_args()
    if args.command == "scale-gap":
        report = evaluate_scale_gap(
            load_scale_gap_cells(args.result),
            repetitions=args.repetitions,
            seed=args.seed,
        )
        output = _write_json(args.output, report)
        print(
            json.dumps(
                {
                    "schema_version": report["schema_version"],
                    "gate": report["gate"],
                    "output": str(output),
                    "provider_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    elif args.command == "build-release":
        print(
            json.dumps(
                _build_release(args),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
