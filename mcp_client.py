"""
MCP 客户端管理器：把 mcp.json 里配置的 MCP 服务器接进来，
把它们的工具转换成 OpenAI 函数调用格式，并执行工具调用。

教学点：
1. MCP = 一个标准协议：服务器通过 stdio 管道与客户端用 JSON-RPC 通信
2. 客户端三件事：连接(initialize) → 列出工具(list_tools) → 调用工具(call_tool)
3. 职责分工：聊天后端 = MCP 客户端；模型 = 决策者；工具 = 执行器
"""
import asyncio
import json
import os
import threading

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Windows 下给 MCP 子进程注入 CREATE_NO_WINDOW，避免弹出一堆黑色控制台窗口
if os.name == "nt":
    _orig_open_process = anyio.open_process

    async def _open_process_no_window(*args, **kwargs):
        kwargs.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
        return await _orig_open_process(*args, **kwargs)

    anyio.open_process = _open_process_no_window

# ============================================================================
# MCP 客户端跑在独立的事件循环线程上：mcp 2.0 的 cancel scope 与
# Starlette/FastAPI 的事件循环不兼容（会抛 "Attempted to exit a cancel scope"），
# 隔离后各用各的循环，互不干扰。
# ============================================================================
_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=True, name="mcp-loop")
_loop_thread.start()


async def run_on_loop(coro):
    """把 MCP 协程提交到专用循环执行（不阻塞调用方的循环）"""
    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    return await asyncio.wrap_future(fut)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 打包版：配置读数据目录（mcp.json 可用户自定义）
CONFIG_FILE = os.path.join(os.environ.get("WENMO_DATA_DIR") or BASE_DIR, "mcp.json")


def _interpolate(value, env):
    """把 ${VAR} 替换成环境变量的值（支持字符串与列表）"""
    if isinstance(value, str):
        out = value
        for k, v in env.items():
            out = out.replace("${" + k + "}", v)
        return out
    if isinstance(value, list):
        return [_interpolate(x, env) for x in value]
    if isinstance(value, dict):
        return {k: _interpolate(v, env) for k, v in value.items()}
    return value


def load_mcp_config():
    """读取 mcp.json 里的 servers，并自动导入 opencode 配置中已启用的本地 MCP 服务器"""
    servers = {}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        servers.update(data.get("servers", {}))
        import_path = data.get("importFromOpencode", "")
        if import_path and os.path.isfile(import_path):
            with open(import_path, encoding="utf-8") as f:
                oc = json.load(f)
            env = dict(os.environ)
            for name, entry in (oc.get("mcp") or {}).items():
                if entry.get("enabled") is False:
                    continue          # 尊重 opencode 里的禁用开关
                if entry.get("type", "local") != "local":
                    continue          # 仅支持 stdio 本地服务器
                cmd = _interpolate(entry.get("command") or [], env)
                if not cmd:
                    continue
                servers.setdefault(name, {
                    "command": cmd,
                    "env": _interpolate(entry.get("environment") or {}, env),
                })
    except Exception:
        pass
    return servers


class _Handle:
    """一个 MCP 服务器的连接句柄"""

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.session = None     # mcp.ClientSession
        self.exit_stack = None  # stdio 上下文（保持打开）
        self.tools = []         # mcp 的 Tool 对象列表
        self.error = None


class MCPManager:
    def __init__(self):
        self._handles = {}
        self._tool_map = {}   # 完整工具名(server_tool) -> (server, tool)
        self._lock = asyncio.Lock()

    async def refresh(self):
        """读配置并（重新）连接所有服务器；已连接的跳过"""
        config = load_mcp_config()
        async with self._lock:
            for name in list(self._handles):
                if name not in config:
                    await self._disconnect(name)
            for name, cfg in config.items():
                h = self._handles.get(name)
                if h is None or h.session is None:
                    await self._connect(name, cfg)
            return await self.snapshot()

    async def _disconnect(self, name):
        h = self._handles.pop(name, None)
        if h and h.exit_stack is not None:
            try:
                await h.exit_stack.__aexit__(None, None, None)
            except Exception:
                pass

    async def _connect(self, name, cfg):
        h = _Handle(name, cfg)
        self._handles[name] = h
        try:
            # 连接整体限时（默认 30 秒；服务器可配 timeout 字段放宽，如 273MB 的 codebase-memory-mcp）
            timeout = float(cfg.get("timeout") or 30)
            await asyncio.wait_for(self._do_connect(h), timeout=timeout)
        except asyncio.TimeoutError:
            h.error = "连接超时（%s 秒，服务器可能无法启动）" % (cfg.get("timeout") or 30)
        except Exception as e:
            h.error = f"{type(e).__name__}: {e}"

    async def _do_connect(self, h):
        cfg = h.cfg
        cmd = cfg.get("command") or []
        if not cmd:
            raise ValueError("缺少 command")
        env = dict(os.environ)
        for k, v in (cfg.get("env") or {}).items():
            env[k] = str(v)
        params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)
        # 协议版本兼容：部分服务器（如 codebase-memory-mcp 0.9）只支持 2025-06-18 及以前，
        # SDK 2.0 默认用 LATEST_HANDSHAKE_VERSION(2026-07-28) 握手会失败。配置 protocol_version
        # 可指定兼容版本（如 "2025-06-18"），patch 覆盖整个连接+initialize 过程。
        proto_version = cfg.get("protocol_version")
        _restore = None
        if proto_version:
            import mcp_types.version as _ver
            _restore = _ver.LATEST_HANDSHAKE_VERSION
            _ver.LATEST_HANDSHAKE_VERSION = proto_version
        try:
            exit_stack = stdio_client(params)
            read, write = await exit_stack.__aenter__()
            h.exit_stack = exit_stack
            session = ClientSession(read, write)
            await session.__aenter__()
            try:
                await session.initialize()   # 新版本 SDK 在 __aenter__ 已自动初始化
            except Exception:
                pass
        finally:
            if _restore is not None:
                import mcp_types.version as _ver
                _ver.LATEST_HANDSHAKE_VERSION = _restore
        # list_tools 单独限时（慢服务器如 codebase-memory 建索引可能阻塞）
        tools_result = await asyncio.wait_for(session.list_tools(), timeout=60)
        h.session = session
        h.tools = list(tools_result.tools)
        h.error = None

    async def snapshot(self):
        """返回 [{name, connected, error, tools:[{name, description}]}]"""
        out = []
        for name, h in self._handles.items():
            out.append({
                "name": name,
                "connected": h.session is not None,
                "error": h.error,
                "tools": [
                    {"name": t.name, "description": (t.description or "")[:200]}
                    for t in h.tools
                ],
            })
        return out

    async def openai_tools(self):
        """把已连接服务器的工具转成 OpenAI 函数调用格式（供模型选择）。
        工具名用 "server_tool" 格式：OpenAI 系 API 只允许 [a-zA-Z0-9_-]。
        设上限 + 截断描述：全部 74 个工具的 schema 会占 1.3 万+ token，
        本地模型上下文吃不消（会报 exceed context size）。"""
        await self.refresh()
        # 服务器优先级：搜索工具必须可用（websearch 排第 2）；本地常用工具优先
        priority = ["demo", "websearch", "kb-gui-mcp", "filesystem-mcp", "playwright-mcp", "github-mcp"]
        ordered = sorted(
            self._handles.items(),
            key=lambda kv: priority.index(kv[0]) if kv[0] in priority else 99,
        )
        max_desc = 150
        tools = []
        self._tool_map.clear()
        for name, h in ordered:
            if h.session is None:
                continue
            for t in h.tools:
                full = f"{name}_{t.name}"
                self._tool_map[full] = (name, t.name)
                desc = (t.description or "")[:max_desc]
                tools.append({
                    "type": "function",
                    "function": {
                        "name": full,
                        "description": desc,
                        "parameters": t.input_schema or {"type": "object", "properties": {}},
                    },
                })
        return tools or None

    async def call(self, full_name, arguments):
        """执行工具调用；full_name 形如 'kb-gui-mcp_kb_search_notes'"""
        pair = self._tool_map.get(full_name)
        if pair is None:
            server, _, tool = full_name.partition("_")
        else:
            server, tool = pair
        h = self._handles.get(server)
        if h is None or h.session is None:
            raise RuntimeError(f"MCP 服务器未连接: {server}")
        try:
            result = await asyncio.wait_for(
                h.session.call_tool(tool, arguments or {}), timeout=60,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(f"工具 {full_name} 调用超时（60 秒）")
        parts = []
        for c in getattr(result, "content", []) or []:
            text = getattr(c, "text", None)
            if text:
                parts.append(text)
            elif getattr(c, "type", "") == "image":
                parts.append("[图像结果]")
        if not parts:
            parts.append(str(result))
        return "\n".join(parts)


manager = MCPManager()


# ---- 供 gui_server 调用的桥接函数（全部跑在专用循环上）----

async def mcp_refresh():
    return await run_on_loop(manager.refresh())


async def mcp_snapshot():
    return await run_on_loop(manager.snapshot())


async def mcp_openai_tools():
    return await run_on_loop(manager.openai_tools())


async def mcp_call(full_name, arguments):
    return await run_on_loop(manager.call(full_name, arguments))
