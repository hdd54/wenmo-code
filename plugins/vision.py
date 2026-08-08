"""
图像识别插件 see_image：给 AI 一个看图工具（对标 opencode-see-image 移植）。
路由链（依次尝试，失败自动降级，全部用本机已有的密钥）：
  1. 用户/智能体配置的图像模型（设置 → 图像模型与上下文 → 图像模型）—— 优先
  2. 本地已加载且带 mmproj 的模型（免费、离线）
  3. opencode 免费视觉模型 mimo-v2.5-free（zen key，免费）
  4. opencode-go 订阅 minimax-m3（opencode-go key，付费订阅）
未配置图像模型时 = 默认 see-image 链路（本地 → 免费 → 订阅）。
智能体场景：调用方可传 agent 模式（agent_vision_provider/agent_vision_model 优先）。
"""

import base64
import json
import mimetypes
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# opencode 免费视觉模型（opencode-see-image 同款路由，密钥复用 providers.json 的 zen）
FREE_MODELS = [
    ("zen", "mimo-v2.5-free"),
]
# opencode-go 订阅视觉模型
GO_MODELS = [
    ("opencode_go", "minimax-m3"),
]


def _load_settings():
    try:
        with open(os.path.join(BASE, "settings.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_image_bytes(src):
    """从路径 / data URL / http URL 读取图片字节"""
    s = str(src).strip()
    if s.startswith("data:"):
        b64 = s.split(",", 1)[1]
        return base64.b64decode(b64)
    if s.startswith("http://") or s.startswith("https://"):
        with urllib.request.urlopen(s, timeout=30) as r:
            return r.read()
    # 相对路径 → 优先在 files/ 目录找（前端上传的附件）
    p = os.path.abspath(s)
    if not os.path.isfile(p):
        alt = os.path.join(BASE, "files", s)
        if os.path.isfile(alt):
            p = alt
    if os.path.isfile(p):
        with open(p, "rb") as f:
            return f.read()
    raise FileNotFoundError(f"图片不存在：{s}")


def _b64data(img_bytes):
    mime = mimetypes.guess_type("x.png")[0] or "image/png"
    return "data:" + mime + ";base64," + base64.b64encode(img_bytes).decode()


def _local_vision(question, img_bytes):
    """路由 1：本地带 mmproj 的模型（免费离线）"""
    sys.path.insert(0, BASE)
    import gui_server
    st = gui_server.LOCAL_STATE
    if st["status"] != "ready" or not st.get("mmproj"):
        return None
    body = json.dumps({
        "model": st["name"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": _b64data(img_bytes)}},
        ]}],
        "max_tokens": 8192,   # 本地 llama.cpp 需显式给足：看图描述不限字数（原 1024 截断过长描述）
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{st['port']}/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    return (data["choices"][0]["message"]["content"] or "").strip() or None


def _remote_vision(provider_key, model, question, img_bytes):
    """用指定供应商+模型看图（路由 2/3/4 共用）"""
    sys.path.insert(0, BASE)
    import gui_server
    from openai import OpenAI
    providers = gui_server.load_providers()
    if provider_key not in providers:
        return None
    cfg = providers[provider_key]
    key = gui_server.resolve_key(cfg) or "local"
    client = OpenAI(base_url=cfg["base_url"], api_key=key, timeout=180)
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": _b64data(img_bytes)}},
        ]}],
    )   # 远端看图不设 max_tokens：描述不限字数（原 1024 会截断长文档/长截图的描述）
    return (r.choices[0].message.content or "").strip() or None


def see_image(args):
    """完整路由链：本地 → opencode 免费 → opencode-go 订阅 → 用户指定图像模型"""
    src = str(args.get("image", "")).strip()
    question = str(args.get("question", "")).strip() or "请详细描述这张图片的内容（截图请描述界面、文字和布局）。"
    agent_mode = bool(args.get("agent", False))   # 智能体场景：优先用智能体图像模型
    if not src:
        return "错误：需要 image 参数（图片路径 / data URL / 图片 URL）"
    try:
        img_bytes = _read_image_bytes(src)
    except Exception as e:
        return f"错误：{e}"

    errors = []

    s = _load_settings()
    # 智能体场景：智能体图像模型优先（agent_vision_*），否则回落到普通图像模型
    if agent_mode:
        vp = s.get("agent_vision_provider", "") or s.get("vision_provider", "")
        vm = s.get("agent_vision_model", "") or s.get("vision_model", "")
    else:
        vp = s.get("vision_provider", "")
        vm = s.get("vision_model", "")

    # 路由 1：用户/智能体配置的图像模型（优先使用；没配置才走默认链路）
    if vp and vm:
        try:
            text = _remote_vision(vp, vm, question, img_bytes)
            if text:
                return f"{text}\n\n（识别方式：{vp}/{vm}）"
            errors.append(f"{vp}/{vm}：未返回描述")
        except Exception as e:
            errors.append(f"{vp}/{vm}：{str(e)[:100]}")

    # 路由 2：本地 mmproj（免费离线）
    try:
        text = _local_vision(question, img_bytes)
        if text:
            return f"{text}\n\n（识别方式：本地模型）"
        errors.append("本地模型：无 mmproj 或未返回描述")
    except Exception as e:
        errors.append(f"本地模型失败：{str(e)[:100]}")

    # 路由 3：opencode 免费视觉模型（mimo-v2.5-free）
    for pk, mdl in FREE_MODELS:
        try:
            text = _remote_vision(pk, mdl, question, img_bytes)
            if text:
                return f"{text}\n\n（识别方式：免费模型 {mdl}）"
            errors.append(f"{pk}/{mdl}：未返回描述")
        except Exception as e:
            errors.append(f"{pk}/{mdl}：{str(e)[:100]}")

    # 路由 4：opencode-go 订阅（minimax-m3，有订阅 key 才试）
    for pk, mdl in GO_MODELS:
        try:
            text = _remote_vision(pk, mdl, question, img_bytes)
            if text:
                return f"{text}\n\n（识别方式：{mdl}）"
            errors.append(f"{pk}/{mdl}：未返回描述")
        except Exception as e:
            errors.append(f"{pk}/{mdl}：{str(e)[:100]}")

    return ("错误：所有图像识别路由都失败了。\n"
            + "\n".join(" - " + e for e in errors)
            + "\n\n建议：1) 加载带 mmproj 的本地模型；2) 在 设置→图像模型与上下文 里配置图像模型；"
              "3) 检查 opencode 账户余额。")


PLUGIN_TOOLS = [
    {
        "name": "see_image",
        "description": "查看一张图片（本地文件路径 / data URL / 网络图片 URL），返回图片内容描述。"
                       "当用户附带了截图或图片、或需要查看本地图片文件时使用，"
                       "即使是纯文本模型也能借助视觉模型理解图片。"
                       "内部会自动依次尝试：本地视觉模型 → 免费视觉模型 → 订阅模型 → 配置的图像模型。",
        "parameters": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "图片来源：本地文件绝对路径 / 文件名（在 files 目录下）/ data:image/...;base64,... / http(s) 图片 URL"},
                "question": {"type": "string", "description": "想了解图片的什么问题（可选，默认详细描述）"},
                "agent": {"type": "boolean", "description": "是否为智能体场景（用智能体配置的图像模型），一般由智能体调用时传 true"},
            },
            "required": ["image"],
        },
        "handler": see_image,
    }
]
