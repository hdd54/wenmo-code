"""
插件系统：plugins/ 目录下的每个 .py 文件是一个插件。
插件契约：定义 PLUGIN_TOOLS = [{name, description, parameters, handler}]，
handler 是普通函数（参数 dict → 字符串结果）。
插件工具与 MCP 工具一样进入 agent 循环，工具名格式 plugin_<插件>_<工具>。
"""
import importlib.util
import json
import os
import time
from extension_packages import component_dirs

_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
_env_res = os.environ.get("WENMO_RES_DIR")
if _env_res:
    _BASE = os.path.join(_env_res, "plugins")
    if not os.path.isdir(_BASE):
        _seed_plugins = os.path.join(_env_res, "seed", "plugins")   # 内容分离：插件在 seed/plugins
        if os.path.isdir(_seed_plugins):
            _BASE = _seed_plugins
# 内容分离：数据目录 content/plugins（用户自定义优先）+ seed/plugins（兜底）
USER_PLUGIN_DIR = ""
_env_data = os.environ.get("WENMO_DATA_DIR")
if _env_data:
    _user_p = os.path.join(_env_data, "content", "plugins")
    os.makedirs(_user_p, exist_ok=True)
    USER_PLUGIN_DIR = _user_p
# 扫描顺序：用户目录优先，seed 兜底；同名用户插件覆盖 seed 插件
PLUGIN_ROOTS = []
PLUGIN_ROOTS.extend(component_dirs("plugins"))
if USER_PLUGIN_DIR:
    PLUGIN_ROOTS.append(USER_PLUGIN_DIR)
if _BASE and os.path.isdir(_BASE):
    PLUGIN_ROOTS.append(_BASE)
if not PLUGIN_ROOTS:
    PLUGIN_ROOTS = [_BASE]

_cache = {"time": 0, "plugins": []}
_tool_map = {}  # 完整工具名 -> (plugin, tool)


def load_plugins(force=False):
    """扫描 plugins/ 目录加载插件（30 秒缓存）"""
    now = time.time()
    if not force and now - _cache["time"] < 30 and _cache["plugins"]:
        return _cache["plugins"]
    plugins = []
    seen = set()
    for _root in PLUGIN_ROOTS:
        if not os.path.isdir(_root):
            continue
        for fn in sorted(os.listdir(_root)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            name = fn[:-3]
            if name in seen:
                continue   # 用户插件优先（同名 seed 跳过）
            seen.add(name)
            try:
                spec = importlib.util.spec_from_file_location(
                    f"agent_plugin_{name}", os.path.join(_root, fn))
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
    from permission_engine import evaluate_permission, load_runtime_policy
    arguments = arguments or {}
    decision = evaluate_permission(full_name, arguments, load_runtime_policy())
    if decision.effect == "deny":
        return json.dumps({
            "error": "权限策略拒绝了该工具调用",
            "permission": {"effect": decision.effect, "reason": decision.reason, "rule": decision.rule},
        }, ensure_ascii=False)
    if decision.effect == "ask" and not arguments.get("_confirmed"):
        return json.dumps({
            "error": "该工具调用需要用户确认",
            "need_confirmation": True,
            "safety": f"权限规则 {decision.rule} 要求确认：{full_name}",
        }, ensure_ascii=False)
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
                if isinstance(result, (dict, list)):
                    return json.dumps(result, ensure_ascii=False)
                return str(result)
    raise RuntimeError(f"插件工具不存在: {full_name}")
