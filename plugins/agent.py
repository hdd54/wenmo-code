"""Optional sub-agent tool backed by the server's in-process agent runtime."""

from agent_bridge import delegate


def delegate_to_agent_handler(arguments: dict) -> dict:
    task = str(arguments.get("task", "")).strip()
    if not task:
        return {"error": "task cannot be empty"}
    files = arguments.get("files") or []
    if isinstance(files, str):
        files = [files]
    request = {
        "task": task[:20_000],
        "context": str(arguments.get("context", ""))[:20_000],
        "provider": str(arguments.get("provider", "")).strip(),
        "model": str(arguments.get("model", "")).strip(),
        "files": [str(item).strip() for item in files if str(item).strip()][:8],
    }
    result = delegate(request)
    if not result.get("ok"):
        return {"error": result.get("detail") or result.get("error") or "agent call failed"}
    return {
        "ok": True,
        "agent_result": result.get("result", ""),
        "agent_model": "%s/%s" % (result.get("provider", ""), result.get("model", "")),
        "usage": result.get("usage", {}),
        "usage_committed": True,
        "note": "Agent usage has been merged into the current conversation when a conversation id is available.",
    }


PLUGIN_TOOLS = [{
    "name": "delegate_to_agent",
    "description": (
        "Delegate an explicitly requested independent subtask to a separate model. "
        "This can incur additional provider usage. Use only when delegation materially helps."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The bounded subtask."},
            "context": {"type": "string", "description": "Optional relevant background."},
            "files": {"type": "array", "items": {"type": "string"},
                      "description": "Up to 8 explicitly authorized read-only files."},
            "provider": {"type": "string", "description": "Optional provider override."},
            "model": {"type": "string", "description": "Optional model override."},
        },
        "required": ["task"],
    },
    "handler": delegate_to_agent_handler,
}]
