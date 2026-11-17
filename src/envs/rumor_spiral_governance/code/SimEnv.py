from typing import Any, List, Optional, Dict, Set
import os
import csv
import json
from datetime import datetime
from loguru import logger

from onesim.simulator import BasicSimEnv
from onesim.events import Event
from .events import StartEvent

# 广播型帖子（谣言/中立媒体/辟谣）：人人可见。
BROADCAST_TYPES = {"rumor", "neutral", "media", "factcheck", "leader"}
# 顶层帖（会作为信息流主条目）：广播帖 + 用户转发。评论/回复不是顶层，挂在父条目下。
TOP_TYPES = BROADCAST_TYPES | {"repost"}


class SimEnv(BasicSimEnv):
    """谣言/沉默螺旋场景环境：维护线程化帖子池(帖子+评论+回复) + 社交图谱，
    每轮给每个用户组装微博式的局部信息流（帖子及其评论线程的真实内容 + 关注圈氛围），
    并回收用户公开行为更新帖子池。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # 手动模式：每轮结束后暂停，等待外部调 continue_next_round()
        import asyncio as _asyncio
        self._manual = False
        self._manual_evt: _asyncio.Event | None = None
        self._pending_intervention: dict = {}
        # 同步预加载 env_data.json，避免 start() 抢在异步 load_initial_data 之前运行导致首轮 feed 空。
        if getattr(self, "env_path", None) and "post_pool" not in self.data:
            _p = os.path.join(self.env_path, "env_data.json")
            if os.path.exists(_p):
                try:
                    with open(_p, "r", encoding="utf-8") as f:
                        self.data.update(json.load(f))
                    logger.info(f"[SimEnv] 预加载 env_data，post_pool={len(self.data.get('post_pool', []))} 条")
                except Exception as e:
                    logger.error(f"[SimEnv] 预加载 env_data 失败: {e}")
        # 加载社交图谱：following[x] = x 能看到其内容的人(即 x 关注的对象)集合。
        self.following: Dict[str, Set[str]] = self._load_social_graph()
        self.register_event("UserActionEvent", "ingest_action")
        self.register_event("FactCheckDoneEvent", "ingest_factcheck")

    def _load_social_graph(self) -> Dict[str, Set[str]]:
        graph: Dict[str, Set[str]] = {}
        if not getattr(self, "env_path", None):
            return graph
        rel_path = os.path.join(self.env_path, "profile", "data", "Relationship.csv")
        if not os.path.exists(rel_path):
            return graph
        try:
            with open(rel_path, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    s = str(row.get("source_id", "")).strip()
                    t = str(row.get("target_id", "")).strip()
                    direction = (row.get("direction", "") or "").strip()
                    if not s or not t:
                        continue
                    graph.setdefault(s, set()).add(t)          # s 关注 t
                    if direction == "bidirectional":
                        graph.setdefault(t, set()).add(s)      # 互相关注
            logger.info(f"[SimEnv] 加载社交图谱：{sum(len(v) for v in graph.values())} 条关注边")
        except Exception as e:
            logger.error(f"[SimEnv] 加载社交图谱失败: {e}")
        return graph

    # ---- 每轮向单个用户投递微博式局部信息流（帖子 + 评论线程真实内容）----
    async def _create_start_event(self, target_id: str) -> StartEvent:
        source_id = self.data.get("environment_id", "ENV")
        post_pool = self.data.get("post_pool", []) or []
        sim_cfg = self.data.get("sim", {}) or {}
        feed_size = sim_cfg.get("feed_size", 4)
        max_comments = sim_cfg.get("max_comments_shown", 4)
        mode = sim_cfg.get("intervention_mode", "none")
        try:
            intervention_round = int(sim_cfg.get("intervention_round", 999))
        except Exception:
            intervention_round = 999
        active = mode != "none" and self.current_step >= intervention_round
        uid = str(target_id)
        neighbors = self.following.get(uid, set())

        # 评论/回复按父条目建索引
        children: Dict[str, List[dict]] = {}
        for p in post_pool:
            if p.get("type") in ("comment", "reply"):
                children.setdefault(str(p.get("parent", "")), []).append(p)

        # 可见顶层帖 = 广播帖(人人可见) + 我关注的人/我自己发的转发
        visible_tops = []
        for p in post_pool:
            ptype = p.get("type", "user")
            if ptype not in TOP_TYPES:
                continue
            # 治理模式 delete：干预轮起，从信息流移除谣言帖及其转发（平台删帖/限流）
            if active and mode == "delete" and ptype in ("rumor", "repost"):
                continue
            author = str(p.get("author", ""))
            if ptype in BROADCAST_TYPES or author in neighbors or author == uid:
                visible_tops.append(p)
        recent_tops = visible_tops[-feed_size:] if visible_tops else []

        # 治理模式 boost_visibility：干预轮起，置顶辟谣帖并多展示评论(让质疑与纠正更可见)
        if active and mode == "boost_visibility":
            fact_posts = [p for p in visible_tops if p.get("type") == "factcheck"]
            others = [p for p in recent_tops if p.get("type") != "factcheck"]
            recent_tops = (fact_posts + others)[:feed_size + len(fact_posts)]
            max_comments = max(max_comments, 6)

        neighbor_talkers: Set[str] = set()
        lines: List[str] = []
        for post in recent_tops:
            eng = post.get("engagement", {}) or {}
            reported = post.get("reported", 0)
            flag = f" [已被举报{reported}次]" if reported else ""
            author = str(post.get("author", "?"))
            src = "你关注的人" if author in neighbors else ("你自己" if author == uid else "")
            src_tag = f"（{src}）" if src else ""
            if author in neighbors and post.get("type") == "repost":
                neighbor_talkers.add(author)
            lines.append(
                f"[{post.get('post_id', '?')}] @{author}{src_tag}：{post.get('text', '')}"
                f"（点赞{eng.get('likes', 0)} 转发{eng.get('reposts', 0)} 评论{eng.get('comments', 0)}）{flag}"
            )
            # 展示该帖的评论线程：优先关注的人的评论，其余补充网友评论
            cs = children.get(str(post.get("post_id", "")), [])
            neigh_cs = [c for c in cs if str(c.get("author", "")) in neighbors]
            other_cs = [c for c in cs if str(c.get("author", "")) not in neighbors and str(c.get("author", "")) != uid]
            shown = (neigh_cs + other_cs)[:max_comments]
            for c in shown:
                cauthor = str(c.get("author", "?"))
                ctag = "你关注的人" if cauthor in neighbors else "网友"
                if cauthor in neighbors:
                    neighbor_talkers.add(cauthor)
                lines.append(
                    f"    ↳[{c.get('post_id', '?')}] {'回复' if c.get('type') == 'reply' else '评论'} @{cauthor}（{ctag}）：{c.get('text', '')}"
                )
                # 该评论下的回复（两级线程）
                replies = children.get(str(c.get("post_id", "")), [])
                for r in replies[:2]:
                    rauthor = str(r.get("author", "?"))
                    rtag = "你关注的人" if rauthor in neighbors else "网友"
                    if rauthor in neighbors:
                        neighbor_talkers.add(rauthor)
                    lines.append(
                        f"        ↳↳[{r.get('post_id', '?')}] 回复 @{rauthor}（{rtag}）：{r.get('text', '')}"
                    )

        feed_text = "\n".join(lines) if lines else "（信息流暂时没有新内容）"

        # 关注圈氛围信号（沉默螺旋的社会压力来源）
        k = len(neighbor_talkers)
        if k > 0:
            feed_text += f"\n\n【关注圈氛围】你关注的人里，最近有 {k} 位在公开转发或评论这条相关消息。"
        else:
            feed_text += "\n\n【关注圈氛围】你关注的人里，暂时没什么人公开谈论这件事。"

        return StartEvent(
            from_agent_id=source_id,
            to_agent_id=target_id,
            feed_text=feed_text,
            round_id=self.current_step,
        )

    # ---- 回收用户公开行为，更新线程化帖子池（read-modify-write 顶层 key）----
    async def ingest_action(self, event: Event) -> None:
        action_type = getattr(event, "action_type", "no_action")
        target_post_id = getattr(event, "target_post_id", "") or ""
        public_text = getattr(event, "public_text", "") or ""
        author = str(getattr(event, "from_agent_id", "?"))

        post_pool = self.data.get("post_pool", []) or []
        if not isinstance(post_pool, list):
            post_pool = []
        pool = list(post_pool)

        def _bump_target(key: str):
            nonlocal pool
            pool = [self._bump(p, key) if p.get("post_id") == target_post_id else p for p in pool]

        if action_type == "repost" and public_text:
            _bump_target("reposts")
            # 媒体和领袖帖标为 broadcast type（BROADCAST_TYPES 包含 media/leader → 人人可见）
            pool.append(self._make_item(pool, author, public_text, "repost", target_post_id, self.current_step))
        elif action_type == "comment" and public_text:
            _bump_target("comments")
            pool.append(self._make_item(pool, author, public_text, "comment", target_post_id, self.current_step))
        elif action_type == "reply" and public_text:
            pool.append(self._make_item(pool, author, public_text, "reply", target_post_id, self.current_step))
        elif action_type == "like" and target_post_id:
            _bump_target("likes")
        elif action_type == "report" and target_post_id:
            pool = [dict(p, reported=p.get("reported", 0) + 1) if p.get("post_id") == target_post_id else p for p in pool]
        # no_action：不改池

        await self.update_data("post_pool", pool)
        logger.info(f"[ingest] round={self.current_step} action={action_type} by={author} target={target_post_id} pool_size={len(pool)}")

    # ---- 事实核查机构发布的辟谣帖入池（广播型，人人可见）----
    async def ingest_factcheck(self, event: Event) -> None:
        published = getattr(event, "published", False)
        text = getattr(event, "text", "") or ""
        if not published or not text:
            return
        post_pool = self.data.get("post_pool", []) or []
        pool = list(post_pool)
        pool.append({
            "post_id": f"P{len(pool)}",
            "author": "事实核查中心",
            "text": text,
            "type": "factcheck",
            "parent": None,
            "round": self.current_step,
            "engagement": {"likes": 0, "reposts": 0, "comments": 0},
        })
        await self.update_data("post_pool", pool)
        logger.info(f"[factcheck] round={self.current_step} 辟谣帖入池 pool_size={len(pool)}")

    @staticmethod
    def _make_item(pool: list, author: str, text: str, itype: str, parent: str, round_id: int) -> dict:
        return {
            "post_id": f"P{len(pool)}",
            "author": author,
            "text": text,
            "type": itype,
            "parent": parent,
            "round": round_id,
            "engagement": {"likes": 0, "reposts": 0, "comments": 0},
        }

    @staticmethod
    def _bump(post: dict, key: str) -> dict:
        eng = dict(post.get("engagement", {}) or {})
        eng[key] = eng.get(key, 0) + 1
        return dict(post, engagement=eng)

    # ====== 手动逐轮模式（人在回路） ======
    async def start(self, **kwargs: Any) -> None:
        """重写：手动模式下除首轮外每轮启动前暂停，等待 continue_next_round()。"""
        if self._manual and self.current_step >= 1:
            if self._manual_evt:
                self._manual_evt.clear()
                await self._manual_evt.wait()
        return await super().start(**kwargs)

    def enable_manual(self):
        """开启手动模式：每轮结束后暂停等待 continue_next_round()。"""
        import asyncio as _a
        self._manual = True
        self._manual_evt = _a.Event()

    async def continue_next_round(self, intervention: dict | None = None) -> dict:
        """外部调用：注入本轮干预参数，解除暂停进入下一轮。返回本轮状态快照。"""
        if intervention:
            sim = self.data.setdefault("sim", {})
            sim["intervention_mode"] = intervention.get("mode", sim.get("intervention_mode", "none"))
            if "custom_debunk" in intervention:
                sim["custom_debunk_text"] = intervention["custom_debunk"]
            self._pending_intervention = intervention
        if self._manual_evt:
            self._manual_evt.set()
        return self.get_round_state()

    def get_round_state(self) -> dict:
        """返回当前轮的状态快照（供前端仪表盘展示）。"""
        pool = self.data.get("post_pool", []) or []
        # 简单地统计各类行为和帖子数
        rumor_posts = sum(1 for p in pool if p.get("type") in ("rumor", "repost"))
        factcheck_posts = sum(1 for p in pool if p.get("type") == "factcheck")
        query_posts = sum(1 for p in pool if p.get("type") == "comment" and any(
            kw in str(p.get("text", "")).lower() for kw in ["官方", "来源", "真的吗", "求证", "辟谣", "质疑"]))
        return {
            "round": self.current_step,
            "max_steps": self.max_steps,
            "post_pool_size": len(pool),
            "rumor_posts": rumor_posts,
            "factcheck_posts": factcheck_posts,
            "query_posts": query_posts,
            "intervention_mode": (self.data.get("sim", {}) or {}).get("intervention_mode", "none"),
            "following_edges": sum(len(v) for v in (self.following or {}).values()),
            "agent_count": sum(len(v) for v in (self.start_targets or {}).values()),
        }
