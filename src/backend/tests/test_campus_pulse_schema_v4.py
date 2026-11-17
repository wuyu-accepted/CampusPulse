import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from backend.campus_pulse.store import SCHEMA_VERSION, WorkbenchStore


TENANT_ID = "campus-pulse-schema-v4-test"
SOURCE_SNAPSHOT_ID = "source-snapshot"
SOURCE_MANIFEST = "1" * 64
WINDOWED_TABLES = (
    "sensing_window_profiles",
    "sensing_window_segments",
    "sensing_window_issue_aggregates",
    "sensing_window_archetype_aggregates",
    "sensing_window_stance_aggregates",
    "sensing_window_response_aggregates",
    "sensing_window_event_aggregates",
)
WINDOW_IDS = (
    "matched_nonshock_reference",
    "tongzhou_governance_shock",
    "lecture_external_shock",
)


class CampusPulseSchemaV4Tests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "workbench.sqlite3"
        )
        self.store = WorkbenchStore(self.database_path)
        self._insert_source_snapshot()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _insert_source_snapshot(self):
        with self.store._write_transaction() as connection:
            connection.execute(
                """
                INSERT INTO evidence_snapshots(
                    tenant_id, snapshot_id, release_key, label,
                    manifest_schema_version, manifest_sha256, status,
                    privacy_mode, parser_contract_version, time_start,
                    time_end_exclusive, source_rows, source_bytes,
                    quality_json, claim_coverage_json,
                    limitation_codes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    TENANT_ID,
                    SOURCE_SNAPSHOT_ID,
                    "source-release",
                    "aggregate source",
                    "evidence-v1",
                    SOURCE_MANIFEST,
                    "sealed",
                    "aggregate_only",
                    "parser-v1",
                    "2026-01-01T00:00:00+08:00",
                    "2026-07-01T00:00:00+08:00",
                    10000,
                    1000000,
                    "{}",
                    "{}",
                    "[]",
                    "2026-07-26T00:00:00.000Z",
                ),
            )

    @staticmethod
    def _insert_sensing_parent(
        connection,
        snapshot_id,
        sample_size,
        schema_version,
    ):
        connection.execute(
            """
            INSERT INTO sensing_snapshots(
                tenant_id, snapshot_id, release_key, label,
                generated_at_utc, schema_version, manifest_sha256,
                artifact_sha256, status, source_snapshot_id,
                source_manifest_sha256, taxonomy_release_sha256,
                persona_evidence_sha256, persona_release_sha256,
                evidence_stage, estimation_mode, causal_status,
                forecast_status, risk_validity, unit_of_analysis,
                stance_target_status, sample_size, source_lineage_json,
                scope_json, distributions_json, review_summary_json,
                privacy_json, claim_boundary_json, limitation_codes_json,
                created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                TENANT_ID,
                snapshot_id,
                f"{snapshot_id}-release",
                snapshot_id,
                "2026-07-26T00:00:00.000Z",
                schema_version,
                ("2" if schema_version.endswith("v1") else "3") * 64,
                ("4" if schema_version.endswith("v1") else "5") * 64,
                "reviewed_aggregate",
                SOURCE_SNAPSHOT_ID,
                SOURCE_MANIFEST,
                "6" * 64,
                "7" * 64,
                "8" * 64,
                "reviewed_descriptive_evidence",
                "probability_weighted",
                "not_identified",
                "not_evaluated",
                "descriptive_only",
                "forum_post_episode",
                "controlled_target_coded",
                sample_size,
                "{}",
                "{}",
                "{}",
                "{}",
                '{"publication_mode":"aggregate_only"}',
                "{}",
                "[]",
                "2026-07-26T00:00:00.000Z",
            ),
        )

    def _insert_windowed_package(self, snapshot_id="windowed-package"):
        with self.store._write_transaction() as connection:
            connection.execute("PRAGMA defer_foreign_keys = ON")
            for index, window_id in enumerate(WINDOW_IDS):
                connection.execute(
                    """
                    INSERT INTO sensing_windows(
                        tenant_id, snapshot_id, window_id, label,
                        window_role, start_inclusive, end_exclusive, timezone,
                        sample_count, denominator_count, coverage_status,
                        estimation_mode, month_support_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        TENANT_ID,
                        snapshot_id,
                        window_id,
                        window_id,
                        "reference" if index == 0 else "shock",
                        f"2026-0{index + 3}-01T00:00:00+08:00",
                        f"2026-0{index + 3}-07T00:00:00+08:00",
                        "Asia/Shanghai",
                        400,
                        1000 + index,
                        "complete",
                        "stratified_hajek",
                        "[]",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO sensing_window_profiles(
                        tenant_id, snapshot_id, window_id, eligible_count,
                        estimate_sample_count, supplemental_review_count,
                        kish_effective_sample_size_milli, review_status,
                        direct_review_count, unresolved_count,
                        direct_review_coverage_ppm,
                        window_novelty_or_no_fit_ppm,
                        max_temporal_block_novelty_or_no_fit_ppm,
                        package_novelty_or_no_fit_ppm,
                        segment_standardization_json, sampling_contract_json,
                        uncertainty_contract_json, review_route_counts_json,
                        comparison_eligibility_json,
                        window_distributions_json, limitation_codes_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?
                    )
                    """,
                    (
                        TENANT_ID,
                        snapshot_id,
                        window_id,
                        1000 + index,
                        400,
                        0,
                        350000,
                        "review_complete",
                        40,
                        0,
                        100000,
                        40000,
                        80000,
                        40000,
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                        "{}",
                        "[]",
                    ),
                )

            segments = (
                (
                    WINDOW_IDS[0],
                    "march-match",
                    "matched_reference",
                    "tongzhou_governance_shock",
                    200,
                    1,
                    2,
                ),
                (
                    WINDOW_IDS[0],
                    "may-match",
                    "matched_reference",
                    "lecture_external_shock",
                    200,
                    1,
                    2,
                ),
                (
                    WINDOW_IDS[1],
                    "governance-window",
                    "shock_window",
                    None,
                    400,
                    1,
                    1,
                ),
                (
                    WINDOW_IDS[2],
                    "external-window",
                    "shock_window",
                    None,
                    400,
                    1,
                    1,
                ),
            )
            for (
                window_id,
                segment_id,
                segment_role,
                matched_scenario_id,
                sample_count,
                weight_numerator,
                weight_denominator,
            ) in segments:
                connection.execute(
                    """
                    INSERT INTO sensing_window_segments(
                        tenant_id, snapshot_id, window_id, segment_id, label,
                        segment_role, matched_scenario_id, start_inclusive,
                        end_exclusive, timezone, comment_regime,
                        eligible_count, estimate_sample_count,
                        supplemental_review_count,
                        standardization_weight_numerator,
                        standardization_weight_denominator, coverage_status,
                        temporal_blocks_json, sampling_contract_json,
                        comparison_eligibility_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        TENANT_ID,
                        snapshot_id,
                        window_id,
                        segment_id,
                        segment_id,
                        segment_role,
                        matched_scenario_id,
                        "2026-03-01T00:00:00+08:00",
                        "2026-03-07T00:00:00+08:00",
                        "Asia/Shanghai",
                        "declared_regime",
                        500,
                        sample_count,
                        0,
                        weight_numerator,
                        weight_denominator,
                        "complete",
                        "[]",
                        "{}",
                        "{}",
                    ),
                )

            aggregate_tables = (
                (
                    "sensing_window_issue_aggregates",
                    "issue_domain_id, label",
                    ("learning_and_academic", "learning and academic"),
                ),
                (
                    "sensing_window_archetype_aggregates",
                    "archetype_id, issue_domain_id, label",
                    (
                        "archetype-01",
                        "learning_and_academic",
                        "academic information seeker",
                    ),
                ),
                (
                    "sensing_window_stance_aggregates",
                    "stance_target, stance_orientation",
                    ("governance_measure_or_rule", "supportive"),
                ),
                (
                    "sensing_window_response_aggregates",
                    "response_need_type, label",
                    (
                        "authoritative_information",
                        "authoritative information",
                    ),
                ),
                (
                    "sensing_window_event_aggregates",
                    "event_relation, label",
                    ("ordinary_background", "ordinary background"),
                ),
            )
            for window_id in WINDOW_IDS:
                for table_name, category_columns, category_values in (
                    aggregate_tables
                ):
                    placeholders = ", ".join("?" for _ in category_values)
                    connection.execute(
                        f"""
                        INSERT INTO {table_name}(
                            tenant_id, snapshot_id, window_id,
                            {category_columns}, sample_count,
                            denominator_count, estimate_ppm, lower_bound_ppm,
                            upper_bound_ppm, effective_sample_size_milli,
                            suppressed, estimation_contract_json,
                            review_support_json
                        ) VALUES (
                            ?, ?, ?, {placeholders}, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            TENANT_ID,
                            snapshot_id,
                            window_id,
                            *category_values,
                            400,
                            400,
                            1000000,
                            1000000,
                            1000000,
                            350000,
                            0,
                            "{}",
                            "{}",
                        ),
                    )
            self._insert_sensing_parent(
                connection,
                snapshot_id,
                1200,
                "campus-pulse-windowed-sensing-v1",
            )

    @staticmethod
    def _table_rows(connection, table_name):
        return [
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table_name} ORDER BY rowid"
            ).fetchall()
        ]

    def test_v3_migrates_once_without_backfill_or_old_row_drift(self):
        old_snapshot_id = "p2-2-single-window"
        with self.store._write_transaction() as connection:
            connection.execute("PRAGMA defer_foreign_keys = ON")
            connection.execute(
                """
                INSERT INTO sensing_windows(
                    tenant_id, snapshot_id, window_id, label, window_role,
                    start_inclusive, end_exclusive, timezone, sample_count,
                    denominator_count, coverage_status, estimation_mode,
                    month_support_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    TENANT_ID,
                    old_snapshot_id,
                    "baseline",
                    "baseline",
                    "representative_reference",
                    "2025-12-01T00:00:00+08:00",
                    "2026-07-21T00:00:00+08:00",
                    "Asia/Shanghai",
                    1000,
                    1000,
                    "reviewed",
                    "unweighted_descriptive",
                    "[]",
                ),
            )
            self._insert_sensing_parent(
                connection,
                old_snapshot_id,
                1000,
                "campus-pulse-sensing-release-v1",
            )

        old_tables = (
            "evidence_snapshots",
            "sensing_snapshots",
            "sensing_windows",
        )
        with self.store._connection() as connection:
            before = {
                table: self._table_rows(connection, table)
                for table in old_tables
            }

        with closing(sqlite3.connect(self.database_path)) as connection, (
            connection
        ):
            for table in reversed(WINDOWED_TABLES):
                connection.execute(f"DROP TABLE {table}")
            connection.execute(
                """
                CREATE TABLE schema_v3_migration_audit (
                    old_version INTEGER NOT NULL,
                    new_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER schema_v3_migration_audit_once
                AFTER UPDATE OF schema_version ON schema_meta
                WHEN OLD.schema_version = 3 AND NEW.schema_version = 4
                BEGIN
                    INSERT INTO schema_v3_migration_audit(
                        old_version, new_version
                    ) VALUES (OLD.schema_version, NEW.schema_version);
                END
                """
            )
            connection.execute(
                """
                UPDATE schema_meta SET schema_version = 3
                WHERE singleton = 1
                """
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            stores = list(
                executor.map(
                    lambda _: WorkbenchStore(self.database_path),
                    range(8),
                )
            )

        self.assertEqual(SCHEMA_VERSION, 14)
        self.assertEqual(
            [store.schema_version() for store in stores],
            [SCHEMA_VERSION] * 8,
        )
        with stores[0]._connection() as connection:
            after = {
                table: self._table_rows(connection, table)
                for table in old_tables
            }
            migration_count = connection.execute(
                "SELECT COUNT(*) FROM schema_v3_migration_audit"
            ).fetchone()[0]
            windowed_counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in WINDOWED_TABLES
            }
            trigger_count = connection.execute(
                f"""
                SELECT COUNT(*) FROM sqlite_master
                WHERE type = 'trigger'
                  AND tbl_name IN (
                      {", ".join("?" for _ in WINDOWED_TABLES)}
                  )
                """,
                WINDOWED_TABLES,
            ).fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(migration_count, 1)
        self.assertEqual(windowed_counts, dict.fromkeys(WINDOWED_TABLES, 0))
        self.assertEqual(trigger_count, len(WINDOWED_TABLES) * 3)

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "cannot accept new window profiles",
        ):
            with stores[0]._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO sensing_window_profiles(
                        tenant_id, snapshot_id, window_id, eligible_count,
                        estimate_sample_count, supplemental_review_count,
                        kish_effective_sample_size_milli, review_status,
                        direct_review_count, unresolved_count,
                        direct_review_coverage_ppm,
                        window_novelty_or_no_fit_ppm,
                        max_temporal_block_novelty_or_no_fit_ppm,
                        package_novelty_or_no_fit_ppm,
                        segment_standardization_json, sampling_contract_json,
                        uncertainty_contract_json, review_route_counts_json,
                        comparison_eligibility_json,
                        window_distributions_json, limitation_codes_json
                    ) VALUES (
                        ?, ?, ?, 1000, 400, 0, 350000, 'review_complete',
                        40, 0, 100000, 0, 0, 0,
                        '{}', '{}', '{}', '{}', '{}', '{}', '[]'
                    )
                    """,
                    (TENANT_ID, old_snapshot_id, "retroactive-window"),
                )

    def test_windowed_children_are_immutable_after_parent_is_sealed(self):
        self._insert_windowed_package()
        append_keys = {
            "sensing_window_profiles": ("window_id", "append-window"),
            "sensing_window_segments": ("segment_id", "append-segment"),
            "sensing_window_issue_aggregates": (
                "issue_domain_id",
                "append-issue",
            ),
            "sensing_window_archetype_aggregates": (
                "archetype_id",
                "append-archetype",
            ),
            "sensing_window_stance_aggregates": (
                "stance_orientation",
                "critical",
            ),
            "sensing_window_response_aggregates": (
                "response_need_type",
                "append-response",
            ),
            "sensing_window_event_aggregates": (
                "event_relation",
                "append-event",
            ),
        }
        for table_name, (key_column, key_value) in append_keys.items():
            with self.subTest(table=table_name, operation="update"):
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "immutable",
                ):
                    with self.store._connection() as connection:
                        connection.execute(
                            f"""
                            UPDATE {table_name}
                            SET snapshot_id = snapshot_id
                            WHERE tenant_id = ?
                            """,
                            (TENANT_ID,),
                        )
            with self.subTest(table=table_name, operation="delete"):
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "immutable",
                ):
                    with self.store._connection() as connection:
                        connection.execute(
                            f"DELETE FROM {table_name} WHERE tenant_id = ?",
                            (TENANT_ID,),
                        )
            with self.subTest(table=table_name, operation="append"):
                with self.store._connection() as connection:
                    columns = [
                        row["name"]
                        for row in connection.execute(
                            f"PRAGMA table_info({table_name})"
                        ).fetchall()
                    ]
                    selected = [
                        "?" if column == key_column else f'"{column}"'
                        for column in columns
                    ]
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "cannot accept new",
                    ):
                        connection.execute(
                            f"""
                            INSERT INTO {table_name}(
                                {", ".join(f'"{item}"' for item in columns)}
                            )
                            SELECT {", ".join(selected)}
                            FROM {table_name}
                            LIMIT 1
                            """,
                            (key_value,),
                        )

    def test_profile_release_gates_fail_closed(self):
        cases = (
            (
                "kish_effective_sample_size_milli",
                349999,
                "kish_effective_sample_size_milli",
            ),
            ("review_status", "review_pending", "review_status"),
            ("window_novelty_or_no_fit_ppm", 100001, "no_fit"),
            (
                "max_temporal_block_novelty_or_no_fit_ppm",
                100001,
                "no_fit",
            ),
            ("package_novelty_or_no_fit_ppm", 50001, "no_fit"),
        )
        profile_columns = (
            "tenant_id",
            "snapshot_id",
            "window_id",
            "eligible_count",
            "estimate_sample_count",
            "supplemental_review_count",
            "kish_effective_sample_size_milli",
            "review_status",
            "direct_review_count",
            "unresolved_count",
            "direct_review_coverage_ppm",
            "window_novelty_or_no_fit_ppm",
            "max_temporal_block_novelty_or_no_fit_ppm",
            "package_novelty_or_no_fit_ppm",
            "segment_standardization_json",
            "sampling_contract_json",
            "uncertainty_contract_json",
            "review_route_counts_json",
            "comparison_eligibility_json",
            "window_distributions_json",
            "limitation_codes_json",
        )
        valid = {
            "tenant_id": TENANT_ID,
            "snapshot_id": "unsealed-gate-test",
            "window_id": "gate-window",
            "eligible_count": 1000,
            "estimate_sample_count": 400,
            "supplemental_review_count": 0,
            "kish_effective_sample_size_milli": 350000,
            "review_status": "review_complete",
            "direct_review_count": 40,
            "unresolved_count": 0,
            "direct_review_coverage_ppm": 100000,
            "window_novelty_or_no_fit_ppm": 50000,
            "max_temporal_block_novelty_or_no_fit_ppm": 100000,
            "package_novelty_or_no_fit_ppm": 50000,
            "segment_standardization_json": "{}",
            "sampling_contract_json": "{}",
            "uncertainty_contract_json": "{}",
            "review_route_counts_json": "{}",
            "comparison_eligibility_json": "{}",
            "window_distributions_json": "{}",
            "limitation_codes_json": "[]",
        }
        placeholders = ", ".join("?" for _ in profile_columns)
        for field, invalid_value, message in cases:
            with self.subTest(field=field):
                candidate = dict(valid)
                candidate[field] = invalid_value
                with self.store._connection() as connection:
                    connection.execute("BEGIN")
                    connection.execute("PRAGMA defer_foreign_keys = ON")
                    try:
                        with self.assertRaisesRegex(
                            sqlite3.IntegrityError,
                            message,
                        ):
                            connection.execute(
                                f"""
                                INSERT INTO sensing_window_profiles(
                                    {", ".join(profile_columns)}
                                ) VALUES ({placeholders})
                                """,
                                tuple(
                                    candidate[column]
                                    for column in profile_columns
                                ),
                            )
                    finally:
                        connection.rollback()


if __name__ == "__main__":
    unittest.main()
