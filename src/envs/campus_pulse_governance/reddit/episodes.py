"""Leakage-controlled historical discussion episodes for CMV runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping


EPISODE_SCHEMA = "reddit-cmv-episode-release-v1"
_RESOURCE = re.compile(r"\b(resource|allocation|access|fee|price|eligib|benefit|public transit|transport|facility|service)\b", re.I)
_PROCESS = re.compile(r"\b(moderat|censor|appeal|ban|rule|policy|content|speech|platform|reddit)\b", re.I)
_ENVIRONMENT = re.compile(r"\b(environment|pollution|noise|parking|traffic|shared space|public space|litter|commut|climate)\b", re.I)


class CmvEpisodeError(ValueError):
    """Historical episode selection failed a no-leakage requirement."""


def remove_later_edits(text: str) -> tuple[str, bool]:
    """Conservatively omit explicit edit/update suffixes; not historical recovery."""
    match = re.search(r"(?im)^\s*(?:\*{0,2})(?:update|edit)(?:\s*\d+)?\s*[:\-]", text)
    if match is None:
        return text, False
    prefix = text[:match.start()].strip()
    if not prefix:
        raise CmvEpisodeError("edited observation has no usable pre-update prefix")
    return prefix, True


def build_episode_scenario_context(
    observation: Mapping[str, Any],
    *,
    max_chars: int = 5_000,
) -> dict[str, Any]:
    """Build a prompt-safe event context from the visible four-message prefix.

    This adapter accepts only the observation release. It deliberately has no
    parameter for evaluator targets or private lineage, so held-out replies and
    source identifiers cannot accidentally enter a resident prompt.
    """
    if not isinstance(observation, Mapping):
        raise CmvEpisodeError("episode observation must be an object")
    allowed_top_level = {
        "episode_id",
        "period",
        "topic_family",
        "title",
        "historical_seed_messages",
        "input_cutoff_timestamp",
        "historical_prefix_size",
    }
    if set(observation) - allowed_top_level:
        raise CmvEpisodeError("episode observation contains non-observation fields")
    episode_id = str(observation.get("episode_id") or "")
    if re.fullmatch(r"cmv-(?:episode-)?[a-f0-9]{16}", episode_id) is None:
        raise CmvEpisodeError("episode ID is invalid")
    if observation.get("period") not in {"development", "held_out"}:
        raise CmvEpisodeError("episode period is invalid")
    topic_family = str(observation.get("topic_family") or "")
    if topic_family not in {
        "public_resource_and_rules",
        "platform_process_and_expression",
        "environment_and_shared_norms",
    }:
        raise CmvEpisodeError("episode topic family is invalid")
    from .personas import CmvPersonaError, _redact_excerpt

    try:
        title, title_redactions = _redact_excerpt(str(observation.get("title") or ""))
    except CmvPersonaError as exc:
        raise CmvEpisodeError("episode title failed the privacy screen") from exc
    title = " ".join(title.split())
    if not title or len(title) > 180:
        raise CmvEpisodeError("episode title is missing or too long")
    rows = observation.get("historical_seed_messages")
    if not isinstance(rows, list) or len(rows) != 4:
        raise CmvEpisodeError("episode must contain the frozen four-message observation prefix")
    visible_rows: list[dict[str, Any]] = []
    redacted_spans = title_redactions
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != {
            "message_ref", "source_author_alias", "text", "created_order"
        }:
            raise CmvEpisodeError("historical observation message fields are invalid")
        message_ref = str(row.get("message_ref") or "")
        alias = str(row.get("source_author_alias") or "")
        try:
            text, redactions = _redact_excerpt(str(row.get("text") or ""), max_chars=None)
        except CmvPersonaError as exc:
            raise CmvEpisodeError("historical observation failed the privacy screen") from exc
        text = " ".join(text.split())
        redacted_spans += redactions
        if message_ref != f"seed-message-{index + 1}" or not re.fullmatch(
            r"Historical participant(?: [1-9][0-9]*)?", alias
        ):
            raise CmvEpisodeError("historical observation alias or order is invalid")
        if not text or len(text) > 4_000 or row.get("created_order") != index:
            raise CmvEpisodeError("historical observation message is invalid")
        visible_rows.append(
            {
                "sequence": index + 1,
                "speaker_alias": alias,
                "text": text,
            }
        )
    context = {
        "context_type": "historical_forum_observation",
        "episode_id": episode_id,
        "topic_family": topic_family,
        "event_title": title,
        "decision_question": re.sub(r"^CMV\s*:\s*", "", title, flags=re.I),
        "observed_prefix": visible_rows,
        "redacted_span_count": redacted_spans,
        "interpretation_rule": (
            "These are historical source observations supplied as event context, not new "
            "messages written by the simulated agents. Treat only the displayed text as seen."
        ),
    }
    try:
        serialized = json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CmvEpisodeError("episode context is not strict JSON") from exc
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1 or len(serialized) > max_chars:
        raise CmvEpisodeError("historical observation context exceeds its configured limit")
    # Reuse the forum prompt scanner without persisting or exposing a source ID.
    from ..forum_twin import validate_prompt_payload

    validate_prompt_payload(context, current_tick=0)
    return context


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _topic_family(title: str, root: str) -> tuple[str | None, str]:
    text = f"{title}\n{root}"
    matches = []
    if _RESOURCE.search(text):
        matches.append("public_resource_and_rules")
    if _PROCESS.search(text):
        matches.append("platform_process_and_expression")
    if _ENVIRONMENT.search(text):
        matches.append("environment_and_shared_norms")
    # Ambiguous roots are not forced into a human-interest category.
    if len(matches) != 1:
        return None, "ambiguous_or_overlapping_root_keywords"
    return matches[0], "frozen_lexical_screen_v1"


def build_episode_candidates(
    database_path: str | Path,
    output_dir: str | Path,
    *,
    seed: int = 20260924,
    minimum_messages: int = 20,
    maximum_messages: int = 200,
    minimum_future_messages: int = 4,
    early_prefix_replies: int = 3,
    max_candidates_per_family: int = 1,
) -> dict[str, Any]:
    """Select held-out whole threads by root-only content and frozen rules.

    Public observation prefix and evaluator-only continuation are written to
    separate local files.  No continuation outcome is used in topic selection.
    """
    if minimum_messages < 5 or maximum_messages < minimum_messages or minimum_future_messages < 1 or max_candidates_per_family < 1:
        raise CmvEpisodeError("episode size or future-target thresholds are invalid")
    database_path = Path(database_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True, timeout=120)
    connection.row_factory = sqlite3.Row
    try:
        meta = dict(connection.execute("SELECT key,value FROM corpus_meta"))
        if meta.get("split_development_end_utc") is None:
            raise CmvEpisodeError("whole-thread temporal split must be frozen first")
        eligible = connection.execute(
            "SELECT conversation_key,COUNT(*) AS message_count,COUNT(DISTINCT author_key) AS author_count "
            "FROM utterances WHERE split='test' AND deleted=0 AND author_key IS NOT NULL "
            "GROUP BY conversation_key HAVING COUNT(*) BETWEEN ? AND ? AND COUNT(DISTINCT author_key)>=4",
            (minimum_messages, maximum_messages),
        ).fetchall()
        ordered = sorted(
            eligible,
            key=lambda row: hashlib.sha256(f"{seed}\0{row['conversation_key']}".encode()).hexdigest(),
        )
        selected: dict[str, dict[str, Any]] = {}
        rejected_counts: dict[str, int] = {}
        for candidate in ordered:
            if len(selected) >= max_candidates_per_family * 3:
                break
            rows = connection.execute(
                "SELECT rowid,source_id,author_key,reply_to_source_id,timestamp,text_authored,content_sha256 "
                "FROM utterances WHERE conversation_key=? AND split='test' AND deleted=0 "
                "ORDER BY timestamp,rowid",
                (candidate["conversation_key"],),
            ).fetchall()
            if len(rows) < minimum_messages or len({row["author_key"] for row in rows if row["author_key"]}) < 4:
                rejected_counts["candidate_changed_under_thread_filter"] = rejected_counts.get("candidate_changed_under_thread_filter", 0) + 1
                continue
            root_indexes = [index for index, row in enumerate(rows) if row["reply_to_source_id"] is None or str(row["source_id"]) == str(candidate["conversation_key"])]
            if not root_indexes:
                rejected_counts["missing_root"] = rejected_counts.get("missing_root", 0) + 1
                continue
            root_index = root_indexes[0]
            root_row = rows[root_index]
            metadata = connection.execute(
                "SELECT title FROM conversation_metadata WHERE source_id=?",
                (str(candidate["conversation_key"]),),
            ).fetchone()
            title = str(metadata["title"] or "") if metadata else ""
            root_text = str(root_row["text_authored"] or "")
            family, family_reason = _topic_family(title, root_text)
            if family is None:
                rejected_counts[family_reason] = rejected_counts.get(family_reason, 0) + 1
                continue
            if sum(1 for item in selected.values() if item["topic_family"] == family) >= max_candidates_per_family:
                continue
            # The root plus three chronologically early replies is the entire
            # model-visible historical prefix.  The rest never enters it.
            early_replies = [row for index, row in enumerate(rows) if index != root_index and row["reply_to_source_id"] is not None]
            early_replies.sort(key=lambda row: (float(row["timestamp"] or 0), int(row["rowid"])))
            prefix_rows = [root_row, *early_replies[:early_prefix_replies]]
            if len(prefix_rows) != early_prefix_replies + 1:
                rejected_counts["insufficient_early_replies"] = rejected_counts.get("insufficient_early_replies", 0) + 1
                continue
            prefix_rowids = {int(row["rowid"]) for row in prefix_rows}
            future_rows = [row for row in rows if int(row["rowid"]) not in prefix_rowids]
            if len(future_rows) < minimum_future_messages:
                rejected_counts["insufficient_future_target"] = rejected_counts.get("insufficient_future_target", 0) + 1
                continue
            private_aliases: dict[str, str] = {}
            visible = []
            private_lineage = []
            for index, row in enumerate(prefix_rows, 1):
                author_key = str(row["author_key"] or "")
                if author_key and author_key not in private_aliases:
                    private_aliases[author_key] = f"Historical participant {len(private_aliases) + 1}"
                message_ref = f"seed-message-{index}"
                visible.append({
                    "message_ref": message_ref,
                    "source_author_alias": private_aliases.get(author_key, "Historical participant"),
                    "text": str(row["text_authored"] or ""),
                    "historical_seed": True,
                    "created_order": index - 1,
                })
                private_lineage.append({
                    "message_ref": message_ref,
                    "source_id_private": str(row["source_id"]),
                    "source_rowid_private": int(row["rowid"]),
                    "content_sha256": str(row["content_sha256"]),
                })
            episode_id = "cmv-episode-" + hashlib.sha256(f"{seed}\0{candidate['conversation_key']}".encode()).hexdigest()[:16]
            selected[episode_id] = {
                "episode_id": episode_id,
                "topic_family": family,
                "topic_classification_rule": family_reason,
                "title": title,
                "root_text": root_text,
                "topic_family_basis": "title_and_root_only",
                "historical_seed_messages": visible,
                "input_cutoff_timestamp": float(max(float(row["timestamp"] or 0) for row in prefix_rows)),
                "source_thread_ref_private": str(candidate["conversation_key"]),
                "source_thread_message_count_private": len(rows),
                "source_distinct_author_count_private": len({row["author_key"] for row in rows if row["author_key"]}),
                "private_lineage": private_lineage,
                "future_targets": [
                    {
                        "source_id_private": str(row["source_id"]),
                        "source_rowid_private": int(row["rowid"]),
                        "author_key_private": str(row["author_key"] or ""),
                        "timestamp": float(row["timestamp"] or 0),
                        "text_private": str(row["text_authored"] or ""),
                        "content_sha256": str(row["content_sha256"]),
                    }
                    for row in future_rows
                ],
                "future_target_count": len(future_rows),
            }
        families = {family: sum(item["topic_family"] == family for item in selected.values()) for family in (
            "public_resource_and_rules", "platform_process_and_expression", "environment_and_shared_norms"
        )}
        public_observations = []
        evaluation_only = []
        for item in selected.values():
            public_observations.append({key: value for key, value in item.items() if key not in {
                "source_thread_ref_private", "source_thread_message_count_private", "source_distinct_author_count_private", "private_lineage", "future_targets", "future_target_count"
            }})
            evaluation_only.append({
                "episode_id": item["episode_id"],
                "future_target_count": item["future_target_count"],
                "targets": item["future_targets"],
            })
        observation_path = output_dir / "episode-observations.private.json"
        evaluation_path = output_dir / "episode-evaluation-only.private.json"
        lineage_path = output_dir / "episode-lineage.private.json"
        observation_path.write_text(json.dumps(public_observations, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        evaluation_path.write_text(json.dumps(evaluation_only, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        lineage_path.write_text(json.dumps([{key: item[key] for key in ("episode_id", "source_thread_ref_private", "private_lineage", "source_thread_message_count_private", "source_distinct_author_count_private")} for item in selected.values()], ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        body = {
            "schema_version": EPISODE_SCHEMA,
            "community": "changemyview",
            "source_snapshot_sha256": str(meta["source_sha256"]),
            "temporal_split": "test",
            "seed": int(seed),
            "selection_uses": ["test split", "message-count range", "distinct author threshold", "root-plus-three-prefix", "title-and-root-only frozen keyword screen"],
            "future_targets_visible_to_resident": False,
            "topic_family_counts": families,
            "rejection_counts": rejected_counts,
            "episodes_selected": len(selected),
            "private_paths": {"observation": observation_path.name, "evaluation_only": evaluation_path.name, "lineage": lineage_path.name},
        }
        release = {**body, "release_sha256": _hash(body)}
        (output_dir / "episode-release.private.json").write_text(json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return {"episodes_selected": len(selected), "topic_family_counts": families, "rejection_counts": rejected_counts, "release_sha256": release["release_sha256"]}
    finally:
        connection.close()
