"""媒体机构：浏览信息流，按编辑方针选材并作为广播帖(type=media)发布。"""
from typing import Any, List
import asyncio
from loguru import logger
from onesim.agent import GeneralAgent
from onesim.profile import AgentProfile
from onesim.memory import MemoryStrategy
from onesim.planning import PlanningBase
from onesim.events import Event
from onesim.relationship import RelationshipManager
from .events import UserActionEvent


class MediaOrganization(GeneralAgent):
    def __init__(self,
                 sys_prompt: str | None = None,
                 model_config_name: str = None,
                 event_bus_queue: asyncio.Queue = None,
                 profile: AgentProfile = None,
                 memory: MemoryStrategy = None,
                 planning: PlanningBase = None,
                 relationship_manager: RelationshipManager = None) -> None:
        super().__init__(sys_prompt, model_config_name, event_bus_queue, profile, memory, planning, relationship_manager)
        self.register_event("StartEvent", "select_and_publish")

    async def select_and_publish(self, event: Event) -> List[Event]:
        feed_text = getattr(event, "feed_text", "") or "（信息流无新内容）"
        policy = self.profile.get_data("editorial_policy", "以客观中立为方针，优先选材有公共价值的内容。") or ""

        observation = f"信息流：\n{feed_text}\n\n编辑方针：{policy}"
        instruction = """你是一家媒体机构的编辑。上面是本轮你看到的信息流和你的编辑方针。
请决定是否选材报道。如果信息流中有值得报道的内容，选择一条进行报道（可以转述、补充背景或提醒读者核实）；如果本轮没有适合的，也可以不报道。

严格按 JSON 格式输出：
{"private_note":"<编辑内部决策理由，一两句话>","action_type":"repost 或 no_action","target_post_id":"<目标帖ID,如P0,不选则为空>","public_text":"<实际发布的报道内容，如选材则必填>"}
"""
        result = await self.generate_reaction(instruction, observation)
        action_type = result.get("action_type", "no_action")
        public_text = (result.get("public_text", "") or "") if action_type == "repost" else ""

        # 私人笔记写入 profile
        self.profile.update_data("private_note", result.get("private_note", ""))
        self.profile.update_data("last_action_type", action_type)

        # 公开行为 + 结束信号回传 ENV（媒体帖由 SimEnv.ingest_action 以 type=media 入池）
        return [UserActionEvent(from_agent_id=self.profile_id, to_agent_id="ENV",
                                action_type=action_type, target_post_id=result.get("target_post_id", "") or "",
                                public_text=public_text)]
