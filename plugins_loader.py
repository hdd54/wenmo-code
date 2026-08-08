"""
插件系统：plugins/ 目录下的每个 .py 文件是一个插件。
插件契约：定义 PLUGIN_TOOLS = [{name, description, parameters, handler}]，
handler 是普通函数（参数 dict → 字符串结果）。
插件工具与 MCP 工具一样进入 agent 循环，工具名格式 plugin_<插件>_<工具>。
"""
import importlib.util
import os
import time

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
# 打包版：插件目录优先用资源目录（WENMO_RES_DIR）；若用户级插件目录存在则合并
_env_res = os.environ.get("WENMO_RES_DIR")
if _env_res:
    BASE = os.path.join(_env_res, "plugins")
os.makedirs(BASE, exist_ok=True)

_cache = {"time": 0, "plugins": []}
_tool_map = {}  # 完整工具名 -> (plugin, tool)


def load_plugins(force=False):
    """扫描 plugins/ 目录加载插件（30 秒缓存）"""
    now = time.time()
    if not force and now - _cache["time"] < 30 and _cache["plugins"]:
        return _cache["plugins"]
    plugins = []
    if os.path.isdir(BASE):
        for fn in sorted(os.listdir(BASE)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            name = fn[:-3]
            try:
                spec = importlib.util.spec_from_file_location(
                    f"agent_plugin_{name}", os.path.join(BASE, fn))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                tools = list(getattr(mod, "PLUGIN_TOOLS", []))
                # handler 兼容：字符串名 → 解析为模块函数（插件可能把 PLUGIN_TOOLS 定义在函数之前，
                # 函数引用会在模块加载时求值报 NameError，故用字符串并在加载后解析）
                for t in tools:
                    h = t.get("handler")
                    if isinstance(h, str):
                        t["handler"] = getattr(mod, h, None)
                    if not callable(t.get("handler")):
                        t["handler"] = None
                plugins.append({"name": name, "tools": tools, "error": None})
            except Exception as e:
                plugins.append({"name": name, "tools": [], "error": f"{type(e).__name__}: {e}"})
    _cache.update(time=now, plugins=plugins)
    return plugins


def openai_tools():
    """把插件工具转成 OpenAI 函数调用格式（与 MCP 工具合并进 agent 循环）"""
    tools = []
    _tool_map.clear()
    for p in load_plugins():
        for t in p.get("tools", []):
            full = f"plugin_{p['name']}_{t['name']}"
            _tool_map[full] = (p["name"], t["name"])
            tools.append({
                "type": "function",
                "function": {
                    "name": full,
                    "description": (t.get("description") or "")[:300],
                    "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                },
            })
    return tools or None


def call(full_name, arguments):
    """执行插件工具；full_name 形如 plugin_system_info_get_cpu_info"""
    pair = _tool_map.get(full_name)
    if pair is None:
        raise RuntimeError(f"插件工具不存在: {full_name}")
    plugin_name, tool_name = pair
    for p in load_plugins():
        if p["name"] != plugin_name:
            continue
        for t in p.get("tools", []):
            if t["name"] == tool_name:
                result = t["handler"](arguments or {})
                return str(result)
    raise RuntimeError(f"插件工具不存在: {full_name}")
