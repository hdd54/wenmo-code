"""智能体插件：把子任务委托给独立的智能体模型（并行处理，提高效能）。
智能体的模型在「设置 → 图像模型与上下文 → 智能体」里选择；
没选 → 默认用当前对话模型。"""

import json
import urllib.request

_SERVER = "http://127.0.0.1:8000"


def delegate_to_agent_handler(arguments: dict) -> dict:
    """把子任务委托给智能体模型（可用独立模型并行处理复杂子任务）。
    若带图片（image 参数），会先用智能体的图像模型读图，把描述作为背景交给智能体。"""
    task = str(arguments.get("task", "")).strip()
    if not task:
        return {"error": "task 不能为空"}
    image = str(arguments.get("image", "")).strip()
    context = str(arguments.get("context", ""))[:20000]
    provider = str(arguments.get("provider", "")).strip()
    model = str(arguments.get("model", "")).strip()
    files = arguments.get("files") or []
    if isinstance(files, str):
        files = [files]
    files = [str(f).strip() for f in files if str(f).strip()][:8]
    # 图片 → 先用智能体图像模型读图（see_image 的 agent 模式）
    if image:
        try:
            import vision
            desc = vision.see_image({"image": image, "agent": True,
                                     "question": "请详细描述这张图片（智能体任务用）"})
            context = (context + "\n\n【图片内容描述】\n" + desc)[:20000]
        except Exception as e:
            context = (context + f"\n\n（图片读取失败：{e}）")[:20000]
    body = json.dumps({
        "task": task,
        "context": context,
        "provider": provider,
        "model": model,
        "files": files,
    }).encode("utf-8")
    req = urllib.request.Request(
        _SERVER + "/api/agent/delegate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok"):
            return {"error": data.get("detail") or "智能体调用失败"}
        return {
            "ok": True,
            "agent_result": data.get("result", ""),
            "agent_model": f"{data.get('provider')}/{data.get('model')}",
            "note": "这是智能体返回的结果，请在最终回答中整合转述给用户。",
        }
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", str(e))
        except Exception:
            detail = str(e)
        return {"error": f"智能体调用失败: {detail}"}
    except Exception as e:
        return {"error": f"智能体调用失败: {e}"}


PLUGIN_TOOLS = [
    {
        "name": "delegate_to_agent",
        "description": "把子任务（如长文分析、代码审查、并行调研等）委托给独立的智能体模型处理，"
                       "返回智能体的结果。适合：主模型不想消耗自己上下文/想要并行处理多个子任务时。"
                       "参数：task=子任务描述；context=背景信息（可选）；image=图片路径（可选，"
                       "会先用智能体图像模型读图再交给智能体）；files=主模型授权智能体读取的本地文件路径列表"
                       "（可选，只读，限 workspace/files/项目目录内，最多 8 个——智能体默认没有文件权限，"
                       "由主模型按需授予）；provider/model=可选覆盖智能体模型"
                       "（不传用设置里配置的智能体模型，没配置则用当前对话模型）。",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "交给智能体的子任务描述"},
                "context": {"type": "string", "description": "背景信息（文件内容/对话要点等）"},
                "image": {"type": "string", "description": "可选：图片路径/文件名，智能体读图后处理"},
                "files": {"type": "array", "items": {"type": "string"}, "description": "可选：主模型授权智能体读取的本地文件路径（只读，限 workspace/files/项目目录）"},
                "provider": {"type": "string", "description": "可选：指定智能体供应商 key"},
                "model": {"type": "string", "description": "可选：指定智能体模型名"},
            },
            "required": ["task"],
        },
        "handler": delegate_to_agent_handler,
    }
]
