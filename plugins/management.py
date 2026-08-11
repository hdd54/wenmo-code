"""Read-only extension inventory.

Runtime code/skill/MCP mutation used to be exposed to the model here. That made
the plugin surface self-modifying, machine-wide, and difficult to audit. Wenmo
now keeps installation and configuration as an explicit user/admin operation.
"""

import json
import os


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(BASE, "plugins")
SKILLS_DIR = os.path.join(BASE, "skills")
MCP_FILE = os.path.join(BASE, "mcp.json")


def list_extensions(_arguments):
    plugins = sorted(
        filename[:-3] for filename in os.listdir(PLUGINS_DIR)
        if filename.endswith(".py") and not filename.startswith("_"))
    skills = []
    if os.path.isdir(SKILLS_DIR):
        skills = sorted(
            name for name in os.listdir(SKILLS_DIR)
            if os.path.isfile(os.path.join(SKILLS_DIR, name, "SKILL.md")))
    servers = []
    try:
        with open(MCP_FILE, encoding="utf-8") as handle:
            config = json.load(handle)
        servers = [
            {"name": name, "enabled": entry.get("enabled", True)}
            for name, entry in sorted((config.get("servers") or {}).items())
            if isinstance(entry, dict)
        ]
    except Exception:
        pass
    return {"plugins": plugins, "skills": skills, "mcp_servers": servers}


PLUGIN_TOOLS = [{
    "name": "list_extensions",
    "description": "Read the installed plugin, skill, and MCP server inventory.",
    "parameters": {"type": "object", "properties": {}},
    "handler": list_extensions,
}]
