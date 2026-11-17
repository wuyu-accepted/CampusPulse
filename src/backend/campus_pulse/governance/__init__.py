"""Typed, allow-listed multi-actor governance orchestration contracts."""

from ._canonical import GovernanceContractError
from .actor_catalog import (
    REVIEWED_ACTOR_RELEASE_FINGERPRINT,
    REVIEWED_ACTOR_RELEASE_ID,
    get_reviewed_actor_release,
)
from .action_catalog import (
    REVIEWED_ACTION_RELEASE_FINGERPRINT,
    REVIEWED_ACTION_RELEASE_ID,
    get_reviewed_action_release,
)
from .compiler import (
    canonical_compiled_json,
    compile_governance_program,
    validate_compiled_governance_program,
)
from .comparison import (
    GOVERNANCE_COMPARISON_SCHEMA_VERSION,
    GOVERNANCE_RUN_SNAPSHOT_SCHEMA_VERSION,
    build_governance_comparison,
    build_governance_run_snapshot,
    validate_governance_comparison,
    validate_governance_run_snapshot,
)
from .explanation import (
    GOVERNANCE_EXPLANATION_DRAFT_SCHEMA_VERSION,
    GOVERNANCE_EXPLANATION_REVIEW_SCHEMA_VERSION,
    build_governance_explanation_draft,
    review_governance_explanation,
)
from .gaps import (
    GOVERNANCE_GAP_RESULT_SCHEMA_VERSION,
    derive_governance_gaps,
    validate_governance_gap_result,
)
from .program_schema import (
    COMPILED_PROGRAM_SCHEMA_VERSION,
    PROGRAM_SCHEMA_VERSION,
    SCENARIO_ASSIGNMENT_SCHEMA_VERSION,
)
from .projection import (
    merge_action_projections,
    project_compiled_action,
)
from .runtime import (
    evaluate_compiled_trigger,
    normalize_aggregate_checkpoint,
)
from .sqlite_repository import (
    GovernancePersistenceError,
    GovernanceSqliteRepository,
)

__all__ = [
    "COMPILED_PROGRAM_SCHEMA_VERSION",
    "GovernanceContractError",
    "GovernancePersistenceError",
    "GovernanceSqliteRepository",
    "GOVERNANCE_COMPARISON_SCHEMA_VERSION",
    "GOVERNANCE_EXPLANATION_DRAFT_SCHEMA_VERSION",
    "GOVERNANCE_EXPLANATION_REVIEW_SCHEMA_VERSION",
    "GOVERNANCE_GAP_RESULT_SCHEMA_VERSION",
    "GOVERNANCE_RUN_SNAPSHOT_SCHEMA_VERSION",
    "PROGRAM_SCHEMA_VERSION",
    "REVIEWED_ACTOR_RELEASE_FINGERPRINT",
    "REVIEWED_ACTOR_RELEASE_ID",
    "REVIEWED_ACTION_RELEASE_FINGERPRINT",
    "REVIEWED_ACTION_RELEASE_ID",
    "SCENARIO_ASSIGNMENT_SCHEMA_VERSION",
    "canonical_compiled_json",
    "build_governance_comparison",
    "build_governance_explanation_draft",
    "build_governance_run_snapshot",
    "compile_governance_program",
    "derive_governance_gaps",
    "evaluate_compiled_trigger",
    "get_reviewed_actor_release",
    "get_reviewed_action_release",
    "merge_action_projections",
    "normalize_aggregate_checkpoint",
    "project_compiled_action",
    "review_governance_explanation",
    "validate_governance_comparison",
    "validate_compiled_governance_program",
    "validate_governance_gap_result",
    "validate_governance_run_snapshot",
]
