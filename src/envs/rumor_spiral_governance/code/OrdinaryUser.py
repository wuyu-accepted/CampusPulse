from typing import Any, List, Optional
import asyncio
from loguru import logger
from onesim.agent import GeneralAgent
from onesim.profile import AgentProfile
from onesim.memory import MemoryStrategy
from onesim.planning import PlanningBase
from onesim.events import Event
from onesim.relationship import RelationshipManager
from .events import UserActionEvent


class OrdinaryUser(GeneralAgent):
    def __init__(self,
                 sys_prompt: str | None = None,
                 model_config_name: str = None,
                 event_bus_queue: asyncio.Queue = None,
                 profile: AgentProfile = None,
                 memory: MemoryStrategy = None,
                 planning: PlanningBase = None,
                 relationship_manager: RelationshipManager = None) -> None:
        super().__init__(sys_prompt, model_config_name, event_bus_queue, profile, memory, planning, relationship_manager)
        self.register_event("StartEvent", "browse_and_act")

    async def browse_and_act(self, event: Event) -> List[Event]:
        """浏览本轮个性化信息流，形成私人想法，并决定一个公开平台行为。"""
        feed_text = getattr(event, "feed_text", "") or "（信息流暂时没有新内容）"

        observation = f"你打开微博，看到以下信息流：\n{feed_text}"
        instruction = """你正在刷微博。上面是你刚刷到的信息流。请像你平时使用微博时那样，自然地决定接下来做什么——可能什么都不做，也可能点赞、转发、评论、回复或举报某条内容。

真实用户看到和自己生活相关、又有不少人在转发的消息时，常常会有反应：比如转发提醒家人朋友、评论表达看法、或质疑求证来源。你不知道消息真假，只凭当前看到的内容按直觉反应即可，不要写成分析报告，也不要给治理建议。

可选行为：no_action（不操作）/ like（点赞）/ repost（转发并附一句话）/ comment（评论）/ reply（回复某评论）/ report（举报）。

请严格按以下 JSON 格式输出（private_note 是只给你自己看的想法，不会被其他用户看到）：
{
    "private_note": "<你此刻真实的私人想法，一两句话>",
    "action_type": "no_action/like/repost/comment/reply/report",
    "target_post_id": "<目标内容编号，例如 P0；不涉及则为空字符串>",
    "public_text": "<你实际会公开发布的文字；no_action 或 like 时为空字符串>"
}
"""

        result = await self.generate_reaction(instruction, observation)

        private_note = result.get("private_note", "")
        action_type = result.get("action_type", "no_action")
        target_post_id = result.get("target_post_id", "") or ""
        public_text = result.get("public_text", "") or ""

        # 私人通道：写入自身 profile，不对外暴露
        self.profile.update_data("private_note", private_note)
        self.profile.update_data("last_action_type", action_type)

        # 公开通道 + 本轮结束信号：向 ENV 发送唯一一个 UserActionEvent
        action_event = UserActionEvent(
            from_agent_id=self.profile_id,
            to_agent_id="ENV",
            action_type=action_type,
            target_post_id=target_post_id,
            public_text=public_text,
        )
        return [action_event]
