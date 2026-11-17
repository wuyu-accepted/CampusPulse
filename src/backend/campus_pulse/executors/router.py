"""Provider-free executor router preserving v3 while adding MassSim v5."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from ..llm_population_contract import LLM_POPULATION_EXECUTION_MODE
from ..forum_twin_contract import FORUM_TWIN_EXECUTION_MODE
from ..forum_twin_v2_contract import FORUM_TWIN_V2_EXECUTION_MODE
from ..runtime_contract import (
    FAKE_ADAPTER_MODE,
    FrozenRunPlan,
    MASS_EXECUTION_MODE,
    GOVERNANCE_V2_EXECUTION_MODE,
    validate_frozen_run_plan,
)
from .fake import DeterministicFakeExecutor
from .mass_sim import BudgetedPopulationExecutor
from .governance_v2 import AdaptiveParticlePopulationExecutor
from .yulan_scale import BudgetedLLMAgentPopulationExecutor
from .forum_twin import ForumTwinDevelopmentExecutor
from .forum_twin_v2 import ForumTwinV2DevelopmentExecutor


class DeterministicExecutionRouter:
    adapter_mode = FAKE_ADAPTER_MODE
    network_enabled = False
    provider_enabled = False

    def __init__(
        self,
        *,
        mass_sim_release_dir: str | Path,
        yulan_scale_release_dir: str | Path | None = None,
        yulan_scale_authorized_live_source_factory=None,
    ):
        self.fixture = DeterministicFakeExecutor()
        self.mass_sim = BudgetedPopulationExecutor(mass_sim_release_dir)
        self.governance_v2 = AdaptiveParticlePopulationExecutor(
            mass_sim_release_dir
        )
        self.yulan_scale = BudgetedLLMAgentPopulationExecutor(
            yulan_scale_release_dir
            if yulan_scale_release_dir is not None
            else os.environ.get(
                "CAMPUS_PULSE_YULAN_SCALE_RELEASE_DIR",
                "_run/yulan-scale-v1",
            ),
            authorized_live_source_factory=(
                yulan_scale_authorized_live_source_factory
            ),
        )
        self.forum_twin = ForumTwinDevelopmentExecutor()
        self.forum_twin_v2 = ForumTwinV2DevelopmentExecutor()
        self.network_enabled = (
            yulan_scale_authorized_live_source_factory is not None
        )
        self.provider_enabled = self.network_enabled

    def execute(
        self,
        *,
        run_plan: Mapping[str, Any] | FrozenRunPlan,
        **kwargs,
    ):
        plan = (
            run_plan
            if isinstance(run_plan, FrozenRunPlan)
            else validate_frozen_run_plan(run_plan)
        )
        if plan.execution_mode == MASS_EXECUTION_MODE:
            executor = self.mass_sim
        elif plan.execution_mode == GOVERNANCE_V2_EXECUTION_MODE:
            executor = self.governance_v2
        elif plan.execution_mode == LLM_POPULATION_EXECUTION_MODE:
            executor = self.yulan_scale
        elif plan.execution_mode == FORUM_TWIN_EXECUTION_MODE:
            executor = self.forum_twin
        elif plan.execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
            executor = self.forum_twin_v2
        else:
            executor = self.fixture
        return executor.execute(run_plan=plan, **kwargs)
