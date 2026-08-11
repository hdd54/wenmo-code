"""Normalize provider and tool usage into Wenmo's cumulative token schema."""

import json


def from_openai_usage(usage):
    if usage is None:
        return {}
    if isinstance(usage, dict):
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        return {
            "input": usage.get("prompt_tokens", 0) or 0,
            "output": usage.get("completion_tokens", 0) or 0,
            "cached": prompt_details.get("cached_tokens", 0) or 0,
            "reasoning": completion_details.get("reasoning_tokens", 0) or 0,
        }
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    if isinstance(prompt_details, dict):
        cached = prompt_details.get("cached_tokens", 0) or 0
    else:
        cached = getattr(prompt_details, "cached_tokens", 0) or 0
    if isinstance(completion_details, dict):
        reasoning = completion_details.get("reasoning_tokens", 0) or 0
    else:
        reasoning = getattr(completion_details, "reasoning_tokens", 0) or 0
    return {
        "input": getattr(usage, "prompt_tokens", 0) or 0,
        "output": getattr(usage, "completion_tokens", 0) or 0,
        "cached": cached,
        "reasoning": reasoning,
    }


def reported_tool_usage(result_text):
    """Return a normalized usage report embedded in a structured tool result."""
    try:
        payload = json.loads(result_text) if isinstance(result_text, str) else result_text
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    normalized = {key: max(0, int(usage.get(key, 0) or 0))
                  for key in ("input", "output", "cached", "reasoning")}
    if not any(normalized.values()):
        return None
    return {
        "provider": str(payload.get("provider") or ""),
        "model": str(payload.get("model") or ""),
        "usage": normalized,
        "usage_committed": bool(payload.get("usage_committed")),
    }
