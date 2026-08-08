"""headroom 轻量集成：Rust 核心 JSON 压缩（SmartCrusher）。
绕过 torch（本机 c10.dll 环境损坏）——直接调 headroom 的 Rust 二进制核心 _core.pyd，
对工具结果中的 JSON 做无损+紧凑压缩，节省 token。
torch 修复后可用完整 compress() 管线（含 ML 文本压缩），本模块作为降级路径常驻。
"""

import io
import json
import sys

_CORE = None


def _get_core():
    """懒加载 headroom Rust 核心（不 import torch）"""
    global _CORE
    if _CORE is None:
        try:
            import headroom._core as core
            _CORE = core
        except Exception as e:
            return None
    return _CORE


def _looks_like_json_array(text):
    """判断是否是可压缩的 JSON 数组/对象"""
    if not text or len(text) < 200:
        return False
    t = text.strip()
    return t.startswith(("[", "{")) and t.endswith(("]", "}"))


def compress_json(text, min_len=200):
    """用 Rust SmartCrusher 压缩 JSON 工具结果。返回压缩后文本或原文。
    规则：仅压缩足够大（≥min_len）的 JSON 数组/对象；压缩后更小才采用。"""
    if not _looks_like_json_array(text):
        return text
    core = _get_core()
    if core is None:
        return text
    try:
        sc = core.SmartCrusher()
        result = sc.crush(text)
        out = getattr(result, "compressed", None) or str(result)
        # 压缩后必须更小才采用（防劣化）
        if isinstance(out, str) and 0 < len(out) < len(text):
            return out
        return text
    except Exception:
        return text


def compress_messages(messages, min_len=300):
    """压缩消息列表中的大 JSON 工具结果（原地过滤不改变结构）。
    返回 (压缩后消息, 节省字符数)。"""
    saved = 0
    if not isinstance(messages, list):
        return messages, 0
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        content = m.get("content")
        if isinstance(content, str) and len(content) >= min_len:
            compressed = compress_json(content)
            if compressed is not content:
                saved += len(content) - len(compressed)
                m = dict(m, content=compressed)
        out.append(m)
    return out, saved


def try_full_compress(messages, model="deepseek-v4-flash"):
    """尝试完整 headroom 管线（需 torch 正常）；失败回退 Rust SmartCrusher。"""
    try:
        import torch  # noqa: F401   # 触发导入测试
        from headroom import compress as hr_compress
        result = hr_compress(messages, model=model, model_limit=200000,
                             kompress_model="disabled")
        cm = result.messages
        if cm and getattr(result, "transforms_applied", None):
            return cm
        return messages
    except Exception:
        pass
    # 降级：Rust SmartCrusher
    compressed, _ = compress_messages(messages)
    return compressed


def health():
    """检查压缩能力状态"""
    core = _get_core()
    torch_ok = False
    try:
        import torch
        torch_ok = True
    except Exception:
        pass
    return {"rust_core": core is not None, "torch": torch_ok,
            "mode": "full" if torch_ok else "rust-smartcrusher"}
