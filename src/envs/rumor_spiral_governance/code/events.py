from onesim.events import Event
from typing import Any


class StartEvent(Event):
    """环境每轮向用户投递的个性化信息流事件。"""
    def __init__(self,
        from_agent_id: str,
        to_agent_id: str,
        feed_text: str = "",
        round_id: int = 0,
        **kwargs: Any
    ) -> None:
        super().__init__(from_agent_id=from_agent_id, to_agent_id=to_agent_id, **kwargs)
        self.feed_text = feed_text
        self.round_id = round_id


class UserActionEvent(Event):
    """用户浏览后产生的公开行为，回传环境；同时作为本轮该用户的结束信号。"""
    def __init__(self,
        from_agent_id: str,
        to_agent_id: str,
        action_type: str = "no_action",
        target_post_id: str = "",
        public_text: str = "",
        **kwargs: Any
    ) -> None:
        allowed = {"no_action", "like", "repost", "comment", "reply", "report"}
        if action_type not in allowed:
            action_type = "no_action"
        super().__init__(from_agent_id=from_agent_id, to_agent_id=to_agent_id, **kwargs)
        self.action_type = action_type
        self.target_post_id = target_post_id
        self.public_text = public_text


class FactCheckDoneEvent(Event):
    """事实核查机构每轮的产出：published=True 时携带辟谣文案(将作为广播帖入池)；
    同时作为本轮该机构的结束信号(回传 ENV)。"""
    def __init__(self,
        from_agent_id: str,
        to_agent_id: str,
        published: bool = False,
        text: str = "",
        **kwargs: Any
    ) -> None:
        super().__init__(from_agent_id=from_agent_id, to_agent_id=to_agent_id, **kwargs)
        self.published = published
        self.text = text
