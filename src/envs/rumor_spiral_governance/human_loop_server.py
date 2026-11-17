# -*- coding: utf-8 -*-
"""人在回路 Web 服务：FastAPI + 手动模拟。启动: python human_loop_server.py → http://localhost:8765"""
import asyncio, json, os, sys, traceback, importlib.util
from loguru import logger

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.append(SRC)
ROOT = os.path.abspath(os.path.join(SRC, ".."))

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Breaking the Spiral — Human in the Loop")

state = {"env": None, "task": None, "running": False, "done": False, "history": [], "agents": {}}
INTERVENTIONS = [
    {"id":"none","label":"不干预 (继续观察)","desc":"本轮什么都不做"},
    {"id":"delete","label":"删除/限流谣言","desc":"移除谣言帖及其转发"},
    {"id":"command_debunk","label":"命令式辟谣","desc":"发布'经核查，该消息不实'"},
    {"id":"evidence_debunk","label":"证据式辟谣","desc":"发布带来源和证据的辟谣"},
    {"id":"boost_visibility","label":"提升质疑曝光","desc":"置顶辟谣+多展示质疑"},
]


async def init_sim():
    import onesim
    await onesim.init(model_config_path=os.path.join(ROOT,"config","model_config.json"), components=["model"])
    config = onesim.load_simulation_config(
        os.path.join(ROOT,"config","rumor_config.json"),
        os.path.join(ROOT,"config","model_config.json"), env_name="rumor_spiral_governance")
    config.simulator_config.max_steps = 4
    config.simulator_config.bus_idle_timeout = 120.0

    from onesim.simulator import AgentFactory
    from onesim.utils.work_graph import WorkGraph
    from onesim.events import get_event_bus
    from onesim.events.eventbus import reset_event_bus
    from main import build_graph, init_agents, parse_json

    reset_event_bus()
    agents, agent_factory = await init_agents(config)
    env_path = os.path.join(SRC, "envs", "rumor_spiral_governance")
    events_path = os.path.join(env_path,"events.json"); actions_path = os.path.join(env_path,"actions.json")
    events, actions = parse_json(events_path), parse_json(actions_path)
    work_graph = build_graph(events, actions)
    start_ids = agent_factory.get_agent_profile_ids(work_graph.get_start_agent_types())
    end_ids = agent_factory.get_agent_profile_ids(work_graph.get_end_agent_types())

    # 绕过 code/__init__.py 与 stdlib 'code' 冲突: 临时 rename → 从文件加载 SimEnv → rename 回
    ci = os.path.join(env_path,"code","__init__.py"); cib = ci+".bak"
    try:
        if os.path.exists(ci): os.rename(ci, cib)
        spec = importlib.util.spec_from_file_location("SimEnv_mod", os.path.join(env_path,"code","SimEnv.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        SimEnv = mod.SimEnv
    finally:
        if os.path.exists(cib): os.rename(cib, ci)

    eb = get_event_bus()
    env = SimEnv(name="ENV", config=config.simulator_config, event_bus=eb,
                 agents=agents, env_path=env_path, start_agent_ids=start_ids,
                 end_agent_ids=end_ids, mode=config.simulator_config.mode,
                 max_steps=config.simulator_config.max_steps)
    env.event_bus = eb
    await env.initialize()
    for atype, ad in agents.items():
        for aid, agent in ad.items():
            eb.register_agent(aid, agent)
            agent.set_env(env)
    env.enable_manual()
    state.update(env=env, agents=agents, running=True, history=[])
    logger.info("[HumanLoop] 初始化完成，手动模式已开启")


async def run_loop():
    env, eb = state["env"], state["env"].event_bus
    agent_tasks = []
    for atype, ad in (state.get("agents") or {}).items():
        for aid, agent in ad.items():
            agent_tasks.append(asyncio.create_task(agent.run()))
    bus_task = asyncio.create_task(eb.run())
    env_tasks = await env.run()
    all_tasks = [bus_task] + agent_tasks + env_tasks
    try:
        done, pending = await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)
    except Exception:
        logger.error(traceback.format_exc())
    finally:
        for t in all_tasks:
            if not t.done(): t.cancel()
        state["done"] = True; state["running"] = False


@app.on_event("startup")
async def startup():
    await init_sim()
    state["task"] = asyncio.create_task(run_loop())


@app.get("/state")
async def get_state():
    env = state.get("env")
    if not env: return JSONResponse({"error":"not initialized"})
    return {
        "round": env.current_step, "max_steps": env.max_steps,
        "running": state["running"], "done": state["done"],
        "pool_size": len(env.data.get("post_pool",[]) or []),
        "intervention_mode": (env.data.get("sim",{}) or {}).get("intervention_mode","none"),
        "history": state["history"][-10:], "interventions": INTERVENTIONS,
        "agent_count": sum(len(v) for v in (state.get("agents") or {}).values()),
        "post_preview": [{"author": p.get("author","?"), "text": p.get("text","")[:100], "type": p.get("type","")}
                         for p in (env.data.get("post_pool",[]) or [])[-6:]],
    }


@app.post("/intervene")
async def intervene(data: dict):
    if not state["running"]: raise HTTPException(400,"模拟未运行")
    env = state["env"]; mode = data.get("mode","none")
    await env.continue_next_round({"mode": mode})
    rec = {"round": env.current_step, "mode": mode, "pool_size": len(env.data.get("post_pool",[]) or [])}
    state["history"].append(rec)
    return {"ok": True, "record": rec}


@app.get("/final")
async def final():
    env = state.get("env")
    if not env: return {"error":"no simulation"}
    pool = env.data.get("post_pool",[]) or []
    return {"total_rounds": env.current_step, "final_pool_size": len(pool),
            "history": state["history"],
            "recent_posts": [{"id":p.get("post_id"),"author":p.get("author"),"text":p.get("text","")[:100],"type":p.get("type")} for p in pool[-8:]]}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")
