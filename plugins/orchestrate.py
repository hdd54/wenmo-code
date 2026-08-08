"""
多模型协调插件：让当前模型把子任务委托给其他已配置的模型。
例如：主对话用本地模型，翻译/改写任务委托给通义/DeepSeek。
安全：只能调用软件里已配置的供应商，不能指向任意地址。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openai import OpenAI  # noqa: E402


def list_providers(args):
    """列出软件已配置的所有模型供应商（供选择委托对象）"""
    import gui_server
    try:
        provs = gui_server.list_providers()["providers"]
        return "、".join(f"{p['key']}({p['name']})" for p in provs)
    except Exception as e:
        return f"错误：{e}"


def ask_model(args):
    """把一段提示词交给另一个已配置的模型处理，返回其回答"""
    provider = str(args.get("provider", "")).strip()
    prompt = str(args.get("prompt", "")).strip()
    system = str(args.get("system", "")).strip()
    if not provider:
        return "错误：需要 provider（可用 list_providers 查看）"
    if not prompt:
        return "错误：prompt 不能为空"
    if len(prompt) > 20000:
        return "错误：prompt 超过 20KB"

    import gui_server
    try:
        provs = gui_server.list_providers()["providers"]
        if provider not in [p["key"] for p in provs]:
            return f"错误：未知供应商 {provider}，可选：{', '.join(p['key'] for p in provs)}"
        if provider == "local":
            st = gui_server.LOCAL_STATE
            if st["status"] != "ready":
                return "错误：本地模型未加载"
            base, model, key = f"http://127.0.0.1:{st['port']}/v1", st["name"], "local"
        else:
            cfg = gui_server.load_providers()[provider]
            base, model = cfg["base_url"], cfg["model"]
            key = gui_server.resolve_key(cfg) or "local"
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        client = OpenAI(base_url=base, api_key=key)
        r = client.chat.completions.create(model=model, messages=msgs, max_tokens=2048)
        content = r.choices[0].message.content
        return content if content else "（该模型返回空回复）"
    except Exception as e:
        return f"错误：{e}"


PLUGIN_TOOLS = [
    {"name": "list_providers", "description": "列出软件已配置的所有模型供应商，用于选择委托对象",
     "parameters": {"type": "object", "properties": {}}, "handler": list_providers},
    {"name": "ask_model", "description": "把子任务委托给另一个已配置的模型处理并返回结果（多模型协调）",
     "parameters": {"type": "object",
                    "properties": {
                        "provider": {"type": "string", "description": "目标供应商 key（用 list_providers 查看）"},
                        "prompt": {"type": "string", "description": "交给该模型的任务提示词"},
                        "system": {"type": "string", "description": "可选的系统指令"}
                    }, "required": ["provider", "prompt"]}, "handler": ask_model},
]
