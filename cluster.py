# -*- coding: utf-8 -*-
"""分布式 Agent 集群 P1：本地多 Worker（进程内 asyncio 并发）+ 任务队列（SQLite 持久化）

架构：
  前端/对话 ──▶ TaskStore(SQLite 队列) ──▶ WorkerPool(asyncio 并发 N 个)
                  │                              │
                  └── /api/tasks API ◀───────────┘ 执行 run_task（非流式 agent 循环）

- run_task：非流式 agent 任务执行（复用 gui_server 核心：供应商/client/工具/循环）
- P2 将把 Worker 独立化（HTTP 拉任务），本模块的 TaskStore/API 已按协议设计
"""

import asyncio
import json
import os
import sqlite3
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "tasks.db")

MAX_LOOPS = 30          # 单任务工具循环上限（防失控）
MAX_TOOL_RESULT = 6000  # 工具结果截断
CLAIM_TIMEOUT = 300     # 任务认领后超时（秒）未回传 → 重置 pending（故障转移）


# ============ 任务存储（SQLite 队列，重启不丢） ============

class TaskStore:
    def __init__(self, db=DB_PATH):
        self.db = db
        self._conn = sqlite3.connect(db, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 0,
            provider TEXT, model TEXT,
            messages TEXT, online INTEGER DEFAULT 0, reasoning TEXT,
            result TEXT, error TEXT,
            claimed_by TEXT, claimed_at REAL,
            created REAL, updated REAL
        )""")
        self._conn.commit()

    def submit(self, task):
        tid = task["id"]
        self._conn.execute(
            "INSERT OR REPLACE INTO tasks (id,status,priority,provider,model,messages,online,reasoning,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, "pending", task.get("priority", 0), task["provider"], task.get("model", ""),
             json.dumps(task.get("messages", []), ensure_ascii=False),
             1 if task.get("online") else 0, task.get("reasoning", ""),
             time.time(), time.time()))
        self._conn.commit()
        return tid

    def claim(self, tid, worker_id=""):
        """取走一个任务（标记 running + claimed_by），返回任务 dict 或 None"""
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id=? AND status='pending'", (tid,)).fetchone()
        if not row:
            return None
        self._conn.execute(
            "UPDATE tasks SET status='running', claimed_by=?, claimed_at=?, updated=? WHERE id=?",
            (worker_id or "local", time.time(), time.time(), tid))
        self._conn.commit()
        return self._row_to_task(row)

    def next_pending(self):
        """取最早的 pending 任务 id（优先级高优先，其次 FIFO）"""
        row = self._conn.execute(
            "SELECT id FROM tasks WHERE status='pending' ORDER BY priority DESC, created ASC LIMIT 1").fetchone()
        return row["id"] if row else None

    def reset_stale(self, timeout=CLAIM_TIMEOUT):
        """故障转移：running 超时未回传 → 重置 pending（原 worker 可能宕机）"""
        cutoff = time.time() - timeout
        rows = self._conn.execute(
            "SELECT id FROM tasks WHERE status='running' AND claimed_at < ?", (cutoff,)).fetchall()
        for r in rows:
            self._conn.execute(
                "UPDATE tasks SET status='pending', claimed_by=NULL, claimed_at=NULL, updated=? WHERE id=?",
                (time.time(), r["id"]))
        if rows:
            self._conn.commit()
        return len(rows)

    def finish(self, tid, result=None, error=None, worker_id=""):
        self._conn.execute(
            "UPDATE tasks SET status=?, result=?, error=?, updated=?, claimed_by=? WHERE id=?",
            ("done" if error is None else "error",
             json.dumps(result, ensure_ascii=False) if result is not None else None,
             error, time.time(), worker_id or None, tid))
        self._conn.commit()

    def get(self, tid):
        row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return self._row_to_task(row) if row else None

    def list(self, limit=50):
        rows = self._conn.execute(
            "SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_task(r) for r in rows]

    def retry(self, tid):
        self._conn.execute("UPDATE tasks SET status='pending', result=NULL, error=NULL, updated=? WHERE id=?", (time.time(), tid))
        self._conn.commit()

    def _row_to_task(self, row):
        t = dict(row)
        try:
            t["messages"] = json.loads(t.get("messages") or "[]")
        except Exception:
            t["messages"] = []
        t["online"] = bool(t.get("online"))
        if t.get("result"):
            try:
                t["result"] = json.loads(t["result"])
            except Exception:
                pass
        return t


# ============ 非流式任务执行（复用 gui_server 核心） ============

def _build_system_prompt(online, project):
    """精简系统提示（任务执行用；与 chat 一致的关键约束）"""
    import sys
    sys.path.insert(0, BASE)
    import skills_loader as sl
    parts = [
        "你是本软件（问墨·code）的内置 AI 助手，保持真实、直接、实事求是。",
        "【简单问题直接回答】无需工具的请求直接回答，不调用工具。",
        "【工具纪律】需要文件/搜索/命令/生成文档时才调用工具；调用工具后向用户说明做了什么、得到什么结果。",
        "【步骤规划规范】多步任务先输出【步骤规划】块（全部步骤一气呵成，最后一步验证），每步完成输出【步骤完成：N】。",
    ]
    if online:
        parts.append("【联网模式已开启】可使用搜索工具 websearch_web_search（语义完整查询，不拆词）。")
    # 通用技能（精简：只注入核心 1 个全文）
    generic = sl.generic_skills()
    if generic:
        parts.append("【技能：%s】\n%s" % (generic[0]["name"], generic[0]["body"][:1200]))
        others = "；".join("【%s】%s" % (g["name"], g["desc"][:60]) for g in generic[1:])
        if others:
            parts.append("技能库还有：" + others)
    return "\n\n".join(parts)


async def run_task(task):
    """非流式 agent 任务：单轮或多轮工具循环，返回 {output, tool_calls, usage, error}"""
    import sys
    sys.path.insert(0, BASE)
    import gui_server as g
    import plugins_loader
    from chat import load_providers
    from openai import OpenAI

    provider = task["provider"]
    model = task.get("model") or ""
    cfg = load_providers().get(provider)
    if not cfg:
        return {"error": "未知供应商: %s" % provider}
    key = g.resolve_key(cfg)
    client = OpenAI(base_url=cfg["base_url"], api_key=key or "local", timeout=300)
    model = model or cfg["model"]

    msgs = [{"role": "system", "content": _build_system_prompt(task.get("online"), task.get("project", "default"))}]
    msgs += list(task.get("messages") or [])
    online = bool(task.get("online"))
    reasoning = task.get("reasoning", "")

    # 工具集（MCP + 插件 + 核心匹配；复用 gui_server 的匹配/展平）
    from mcp_client import mcp_openai_tools
    mcp_tools = await mcp_openai_tools() or []
    plugin_tools = plugins_loader.openai_tools() or []
    tools = plugin_tools + mcp_tools
    tools = g._flatten_tools(tools)
    last_user = ""
    for m in reversed(msgs):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            last_user = m["content"]
            break
    matched = g._match_tools_by_message(tools, last_user)
    if matched:
        tools = matched

    create_kwargs = {}
    if reasoning in ("low", "medium", "high"):
        create_kwargs["reasoning_effort"] = reasoning
    if provider not in ("local", "ollama"):
        create_kwargs["extra_body"] = {"promptCacheKey": ("task-" + str(task["id"]))[:64]}

    tool_count = 0
    try:
        for _round in range(MAX_LOOPS):
            try:
                resp = client.chat.completions.create(
                    model=model, messages=msgs, tools=tools or None, stream=False, **create_kwargs)
            except Exception as e:
                if "reasoning" in str(e).lower() and "reasoning_effort" in create_kwargs:
                    create_kwargs.pop("reasoning_effort", None)
                    resp = client.chat.completions.create(
                        model=model, messages=msgs, tools=tools or None, stream=False, **create_kwargs)
                else:
                    raise
            msg = resp.choices[0].message
            tcs = msg.tool_calls or []
            if not tcs:
                return {"output": (msg.content or "").strip(), "tool_calls": tool_count,
                        "usage": _usage(resp), "error": None}
            # 有工具调用：记录并执行
            tool_count += len(tcs)
            msgs.append({
                "role": "assistant", "content": msg.content or None,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tcs
                ],
            })
            for tc in tcs:
                tname = tc.function.name
                targs_raw = tc.function.arguments or "{}"
                try:
                    targs = json.loads(targs_raw)
                except Exception:
                    targs = g._repair_tool_args(targs_raw) or {}
                # re-nest（展平还原）
                _map = g._FLATTEN_MAP.get(tname)
                if _map:
                    targs = g._renest_args(targs, _map)
                # 搜索语义重构
                if tname == "websearch_web_search" and isinstance(targs, dict) and targs.get("query"):
                    targs["query"] = g._refine_search_query(targs["query"], last_user, client, model)
                try:
                    if tname.startswith("plugin_"):
                        result_text = await asyncio.to_thread(plugins_loader.call, tname, targs)
                    else:
                        result_text = await mcp_call(tname, targs)
                except Exception as e:
                    result_text = "工具错误: %s" % e
                result_text = str(result_text)[:MAX_TOOL_RESULT]
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})
        return {"output": "（任务达到工具循环上限，未完全结束）", "tool_calls": tool_count,
                "usage": None, "error": "tool loop limit"}
    except Exception as e:
        return {"error": "任务执行失败: %s" % str(e)[:200]}


def _usage(resp):
    try:
        u = resp.usage
        return {"input": getattr(u, "prompt_tokens", 0) or 0,
                "output": getattr(u, "completion_tokens", 0) or 0,
                "cached": (getattr(getattr(u, "prompt_tokens_details", None) or {}, "cached_tokens", 0) or 0)}
    except Exception:
        return {}


from mcp_client import mcp_call  # noqa: E402  （run_task 内用）


# ============ Worker 池（asyncio 并发，进程内） ============

class WorkerPool:
    def __init__(self, store, workers=2):
        self.store = store
        self.workers = workers
        self._running = set()
        self._loop = None

    async def pump_once(self):
        """单轮调度：有空闲 worker 且有 pending → 领任务执行（供泵循环调用）"""
        try:
            while len(self._running) < self.workers:
                tid = self.store.next_pending()
                if not tid:
                    break
                task = self.store.claim(tid, worker_id="local")
                if not task:
                    break
                fut = asyncio.get_running_loop().create_task(self._execute(task))
                self._running.add(fut)
                fut.add_done_callback(self._running.discard)
        except Exception:
            pass

    async def pump(self):
        """调度循环：有 pending 且有空闲 worker → 领取执行"""
        while True:
            try:
                await self.pump_once()
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def _execute(self, task):
        try:
            result = await run_task(task)
            if result.get("error"):
                self.store.finish(task["id"], error=result["error"])
            else:
                self.store.finish(task["id"], result={
                    "output": result.get("output", ""),
                    "tool_calls": result.get("tool_calls", 0),
                    "usage": result.get("usage"),
                })
        except Exception as e:
            self.store.finish(task["id"], error="worker 异常: %s" % str(e)[:200])
        finally:
            self._running.discard(asyncio.current_task())


# 全局单例
_store = TaskStore()
# 本地 worker 数可用环境变量控制（CLUSTER_LOCAL_WORKERS=0 时任务全由远程 worker 执行，便于验证/部署）
_pool = WorkerPool(_store, workers=int(os.environ.get("CLUSTER_LOCAL_WORKERS", "2")))
_pump_task = None

# ============ 节点注册表（P3：注册/心跳/负载） ============
NODES = {}   # worker_id -> {"name","load","last_heartbeat","addr"}
NODE_TIMEOUT = 60   # 心跳超时（秒）→ 视为离线


def register_node(worker_id, name="", addr=""):
    NODES[worker_id] = {"name": name or worker_id[:8], "load": 0, "last_heartbeat": time.time(), "addr": addr}
    return True


def heartbeat_node(worker_id):
    n = NODES.get(worker_id)
    if n:
        n["last_heartbeat"] = time.time()
    return bool(n)


def prune_nodes():
    """剔除心跳超时的节点（故障转移辅助）"""
    now = time.time()
    dead = [wid for wid, n in NODES.items() if now - n.get("last_heartbeat", 0) > NODE_TIMEOUT]
    for wid in dead:
        NODES.pop(wid, None)
    return dead


def start_pump():
    """启动后台调度（由 gui_server 启动时调用）"""
    global _pump_task
    if _pump_task is None:
        async def _pump_loop():
            while True:
                try:
                    _store.reset_stale()          # 故障转移：超时任务重置
                    prune_nodes()                  # 节点心跳清理
                    await _pool.pump_once()        # 领任务执行
                except Exception:
                    pass
                await asyncio.sleep(1)
        _pump_task = asyncio.get_event_loop().create_task(_pump_loop())
    return _pump_task
