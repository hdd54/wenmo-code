"""
自我改进插件（对标 opencode 的自扩展能力）：
AI 可以通过这些工具，自己给软件添加插件 / 技能 / MCP 服务器。
安全边界：所有写操作严格限定在 agent-tutorial 自己的目录内。
"""
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agent-tutorial/
PLUGINS_DIR = os.path.join(BASE, "plugins")
SKILLS_DIR = os.path.join(BASE, "skills")
MCP_FILE = os.path.join(BASE, "mcp.json")


def _safe_name(name):
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(name))[:40]


def list_extensions(args):
    """查看当前已安装的插件、技能和 MCP 服务器"""
    plugins = [f[:-3] for f in os.listdir(PLUGINS_DIR)
               if f.endswith(".py") and not f.startswith("_")]
    skills = []
    if os.path.isdir(SKILLS_DIR):
        skills = [d for d in os.listdir(SKILLS_DIR)
                  if os.path.isfile(os.path.join(SKILLS_DIR, d, "SKILL.md"))]
    mcp = []
    try:
        with open(MCP_FILE, encoding="utf-8") as f:
            mcp = list(json.load(f).get("servers", {}).keys())
    except Exception:
        pass
    return f"已安装插件: {plugins}\n已安装技能: {skills}\nMCP 服务器: {mcp}"


def create_plugin(args):
    """创建一个新 Python 插件（写入 plugins/ 目录，下次对话自动加载）"""
    name = _safe_name(args.get("name", ""))
    code = str(args.get("code", ""))
    if not name:
        return "错误：需要 name"
    if len(code) < 20:
        return "错误：code 太短（需要定义 PLUGIN_TOOLS 的完整插件代码）"
    if len(code) > 20000:
        return "错误：代码超过 20KB 上限"
    path = os.path.join(PLUGINS_DIR, name + ".py")
    if os.path.exists(path):
        return f"错误：插件 {name} 已存在，换个名字或先删除"
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return f"插件 {name} 已创建：{path}（下次对话自动加载，可在设置→插件查看）"


def create_skill(args):
    """创建一个新技能（Markdown 说明书，写入 skills/ 目录）"""
    name = _safe_name(args.get("name", ""))
    description = str(args.get("description", ""))[:300]
    content = str(args.get("content", ""))
    if not name:
        return "错误：需要 name"
    if len(content) < 10:
        return "错误：content 太短"
    if len(content) > 30000:
        return "错误：内容超过 30KB 上限"
    d = os.path.join(SKILLS_DIR, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(f"---\nname: {name}\ndescription: {description}\n---\n\n{content}")
    return f"技能 {name} 已创建（下次对话按内容自动匹配注入）"


def update_mcp_server(args):
    """在 mcp.json 中添加或修改一个 MCP 服务器配置"""
    name = _safe_name(args.get("name", ""))
    command = args.get("command", [])
    env = args.get("env", {}) or {}
    if not name:
        return "错误：需要 name"
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        return "错误：command 需要是非空字符串列表（例如 ['python', 'server.py']）"
    with open(MCP_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("servers", {})[name] = {"command": command, "env": env}
    with open(MCP_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return f"MCP 服务器 {name} 已配置（设置→MCP 刷新后生效）"


PLUGIN_TOOLS = [
    {"name": "list_extensions", "description": "查看当前软件已安装的插件、技能和 MCP 服务器清单",
     "parameters": {"type": "object", "properties": {}}, "handler": list_extensions},
    {"name": "create_plugin", "description": "创建一个新的 Python 插件写入 plugins/ 目录（自我改进：扩展软件能力）",
     "parameters": {"type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "插件名（字母数字下划线）"},
                        "code": {"type": "string", "description": "完整插件代码，需定义 PLUGIN_TOOLS = [{name, description, parameters, handler}]"}
                    }, "required": ["name", "code"]}, "handler": create_plugin},
    {"name": "create_skill", "description": "创建一个新技能（Markdown 操作说明书）写入 skills/ 目录",
     "parameters": {"type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string", "description": "技能用途描述（用于匹配）"},
                        "content": {"type": "string", "description": "技能正文（Markdown）"}
                    }, "required": ["name", "description", "content"]}, "handler": create_skill},
    {"name": "update_mcp_server", "description": "在 mcp.json 中添加/修改 MCP 服务器配置（连接外部工具服务）",
     "parameters": {"type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "command": {"type": "array", "items": {"type": "string"}, "description": "启动命令（可执行文件+参数）"},
                        "env": {"type": "object", "description": "环境变量"}
                    }, "required": ["name", "command"]}, "handler": update_mcp_server},
]
