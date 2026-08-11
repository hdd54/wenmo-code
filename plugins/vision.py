"""Explicit, metered image-understanding tool.

The tool uses the configured vision model (or a loaded local mmproj model). It
does not silently fan out across several paid providers.
"""

import base64
import ipaddress
import json
import mimetypes
import os
import socket
import sys
import urllib.parse
import urllib.request

from execution_context import current_workspace
from network_safety import safe_urlopen
from tenant_state import files_dir, load_json
from usage_accounting import from_openai_usage


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _safe_remote_image_url(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("remote images must use an HTTPS URL without embedded credentials")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        if not ip.is_global:
            raise ValueError("private, loopback, and link-local image hosts are blocked")
    return value


def _read_image_bytes(src):
    value = str(src or "").strip()
    if value.startswith("data:"):
        if "," not in value or ";base64," not in value[:100]:
            raise ValueError("invalid image data URL")
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
    elif value.startswith("http://") or value.startswith("https://"):
        request = urllib.request.Request(
            _safe_remote_image_url(value), headers={"User-Agent": "Wenmo-Image/1"})
        with safe_urlopen(request, timeout=30, https_only=True) as response:
            raw = response.read(MAX_IMAGE_BYTES + 1)
    else:
        scoped = current_workspace.get()
        candidate = os.path.abspath(
            value if os.path.isabs(value) else os.path.join(scoped or files_dir(), value))
        roots = [os.path.abspath(files_dir())]
        if scoped:
            roots.append(os.path.abspath(scoped))
        if not any(os.path.commonpath([candidate, root]) == root for root in roots):
            raise PermissionError("image path is outside the current workspace and tenant files")
        with open(candidate, "rb") as handle:
            raw = handle.read(MAX_IMAGE_BYTES + 1)
    if not raw:
        raise ValueError("image is empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds 20 MB")
    return raw


def _data_url(image_bytes, source="image.png"):
    mime = mimetypes.guess_type(source)[0] or "image/png"
    return "data:%s;base64,%s" % (mime, base64.b64encode(image_bytes).decode("ascii"))


def _server_module():
    module = sys.modules.get("gui_server")
    if module is None:
        candidate = sys.modules.get("__main__")
        module = candidate if hasattr(candidate, "LOCAL_STATE") else None
    return module


def _local_vision(question, image_bytes):
    server = _server_module()
    state = getattr(server, "LOCAL_STATE", {}) if server else {}
    if state.get("status") != "ready" or not state.get("mmproj"):
        return None
    body = json.dumps({
        "model": state["name"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": _data_url(image_bytes)}},
        ]}],
        "max_tokens": 8192,
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:%s/v1/chat/completions" % state["port"], data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    text = (payload["choices"][0]["message"]["content"] or "").strip()
    return {"result": text, "provider": "local", "model": state["name"],
            "usage": from_openai_usage(payload.get("usage"))} if text else None


def _remote_vision(provider_key, model, question, image_bytes):
    from chat import load_providers
    from openai import OpenAI

    providers = load_providers()
    config = providers.get(provider_key)
    if not config:
        raise ValueError("configured vision provider does not exist")
    key = str(config.get("api_key") or "").strip()
    if (not key and config.get("api_key_env")
            and not getattr(sys, "frozen", False)):
        key = os.environ.get(config["api_key_env"], "").strip()
    if not key and provider_key not in ("local", "ollama"):
        raise ValueError("configured vision provider has no API key")
    client = OpenAI(base_url=config["base_url"], api_key=key or "local", timeout=180)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": _data_url(image_bytes)}},
        ]}],
    )
    text = (response.choices[0].message.content or "").strip()
    return {"result": text, "provider": provider_key, "model": model,
            "usage": from_openai_usage(getattr(response, "usage", None))} if text else None


def see_image(arguments):
    source = str(arguments.get("image", "")).strip()
    question = str(arguments.get("question", "")).strip() or "请详细描述图片中的内容、文字和布局。"
    if not source:
        return {"error": "image is required"}
    try:
        image_bytes = _read_image_bytes(source)
    except Exception as exc:
        return {"error": str(exc)}

    settings = load_json("settings.json", {})
    agent_mode = bool(arguments.get("agent"))
    if agent_mode:
        provider = settings.get("agent_vision_provider") or settings.get("vision_provider") or ""
        model = settings.get("agent_vision_model") or settings.get("vision_model") or ""
    else:
        provider = settings.get("vision_provider") or ""
        model = settings.get("vision_model") or ""

    errors = []
    if provider and model:
        try:
            result = _remote_vision(provider, model, question, image_bytes)
            if result:
                result["cost_notice"] = "This separate vision-model usage is recorded in the conversation."
                return result
        except Exception as exc:
            errors.append("%s/%s: %s" % (provider, model, str(exc)[:160]))
    try:
        result = _local_vision(question, image_bytes)
        if result:
            return result
    except Exception as exc:
        errors.append("local: %s" % str(exc)[:160])
    return {"error": "no configured vision model succeeded", "details": errors,
            "hint": "Configure one vision provider/model or load a local model with mmproj."}


PLUGIN_TOOLS = [{
    "name": "see_image",
    "description": (
        "Inspect an explicitly supplied image using the configured vision model or a local mmproj model. "
        "A remote vision call incurs separate usage, which is recorded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "Tenant file, workspace path, data URL, or public HTTPS URL."},
            "question": {"type": "string", "description": "What to inspect."},
            "agent": {"type": "boolean", "description": "Use the configured agent vision model."},
        },
        "required": ["image"],
    },
    "handler": see_image,
}]
