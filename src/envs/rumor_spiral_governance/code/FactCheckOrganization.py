from typing import Any, List
import asyncio
from loguru import logger
from onesim.agent import GeneralAgent
from onesim.profile import AgentProfile
from onesim.memory import MemoryStrategy
from onesim.planning import PlanningBase
from onesim.events import Event
from onesim.relationship import RelationshipManager
from .events import FactCheckDoneEvent


class FactCheckOrganization(GeneralAgent):
    """事实核查机构：读取环境隐藏的真值证据包(case_bundle)，按治理模式发布辟谣。
    第一版核查结论固定(不做真伪判断)，只比较不同表达方式的干预效果。"""

    def __init__(self,
                 sys_prompt: str | None = None,
                 model_config_name: str = None,
                 event_bus_queue: asyncio.Queue = None,
                 profile: AgentProfile = None,
                 memory: MemoryStrategy = None,
                 planning: PlanningBase = None,
                 relationship_manager: RelationshipManager = None) -> None:
        super().__init__(sys_prompt, model_config_name, event_bus_queue, profile, memory, planning, relationship_manager)
        self.register_event("StartEvent", "publish_factcheck")

    async def publish_factcheck(self, event: Event) -> List[Event]:
        round_id = getattr(event, "round_id", 0)
        mode = await self.get_env_data("sim.intervention_mode", "none")
        try:
            intervention_round = int(await self.get_env_data("sim.intervention_round", 999))
        except Exception:
            intervention_round = 999

        active = mode in ("command_debunk", "evidence_debunk", "boost_visibility") and round_id >= intervention_round
        if not active:
            # 不干预：仍需回传结束信号，供回合计数
            return [FactCheckDoneEvent(self.profile_id, "ENV", published=False, text="")]

        cb = await self.get_env_data("case_bundle", {}) or {}
        claim = cb.get("claim", "该消息")
        if mode == "command_debunk":
            text = f"【辟谣】经核查，“{claim}”系不实信息，请勿传播。"
        else:  # evidence_debunk / boost_visibility 都给出证据式核查文案
            evidence = "；".join((cb.get("evidence", []) or [])[:3])
            correction = cb.get("correction_material", "")
            text = f"【辟谣】经核查，“{claim}”为不实信息。核查依据：{evidence}。{correction} 请以官方发布为准。"

        logger.info(f"[FactCheck] round={round_id} mode={mode} 发布辟谣")
        return [FactCheckDoneEvent(self.profile_id, "ENV", published=True, text=text)]
