"""Explicit, metered delegation to another configured model."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI  # noqa: E402
from usage_accounting import from_openai_usage  # noqa: E402


def list_providers(_arguments):
    """List configured model providers available to this tenant."""
    import gui_server

    try:
        providers = gui_server.list_providers()["providers"]
        return {
            "providers": [
                {"key": item["key"], "name": item["name"]}
                for item in providers
            ]
        }
    except Exception as exc:
        return {"error": str(exc)}


def ask_model(arguments):
    """Send one bounded subtask to a configured model and report its usage."""
    provider = str(arguments.get("provider", "")).strip()
    prompt = str(arguments.get("prompt", "")).strip()
    system = str(arguments.get("system", "")).strip()
    if not provider:
        return {"error": "provider is required; call list_providers first"}
    if not prompt:
        return {"error": "prompt cannot be empty"}
    if len(prompt.encode("utf-8")) > 20 * 1024:
        return {"error": "prompt exceeds 20 KiB"}
    if len(system.encode("utf-8")) > 8 * 1024:
        return {"error": "system prompt exceeds 8 KiB"}

    import gui_server

    try:
        available = gui_server.list_providers()["providers"]
        if provider not in {item["key"] for item in available}:
            return {
                "error": "unknown provider",
                "provider": provider,
                "available": [item["key"] for item in available],
            }
        if provider == "local":
            state = gui_server.LOCAL_STATE
            if state["status"] != "ready":
                return {"error": "local model is not loaded"}
            base_url = "http://127.0.0.1:%s/v1" % state["port"]
            model = state["name"]
            api_key = "local"
        else:
            config = gui_server.load_providers()[provider]
            base_url = config["base_url"]
            model = config["model"]
            api_key = gui_server.resolve_key(config) or "local"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = OpenAI(base_url=base_url, api_key=api_key, timeout=180).chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=2048,
        )
        content = (response.choices[0].message.content or "").strip()
        return {
            "result": content,
            "provider": provider,
            "model": model,
            "usage": from_openai_usage(getattr(response, "usage", None)),
            "cost_notice": "This delegated model usage is recorded in the conversation.",
        }
    except Exception as exc:
        return {"error": str(exc), "provider": provider}


PLUGIN_TOOLS = [
    {
        "name": "list_providers",
        "description": "List model providers configured for the current tenant.",
        "parameters": {"type": "object", "properties": {}},
        "handler": list_providers,
    },
    {
        "name": "ask_model",
        "description": (
            "Delegate one explicit subtask to another configured model. "
            "The separate provider usage is recorded in the current conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Provider key from list_providers."},
                "prompt": {"type": "string", "description": "Bounded subtask prompt."},
                "system": {"type": "string", "description": "Optional system instruction."},
            },
            "required": ["provider", "prompt"],
        },
        "handler": ask_model,
    },
]
