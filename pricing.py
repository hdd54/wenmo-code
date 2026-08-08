# -*- coding: utf-8 -*-
"""供应商模型价格表（人民币 元 / 百万 tokens）。

计费公式：费用 = 输入tokens×输入价 + 输出tokens×输出价 + 缓存命中tokens×缓存价
  缓存命中 = input 中命中的部分（cached_tokens），按缓存价（通常远低于全价输入）计。
  无缓存价字段的供应商：缓存命中按全价输入的 10% 计（近似官方缓存折扣惯例）。

价格来源：各供应商官方定价（按模型档位）。本地模型（ollama/local）免费。
未收录的模型/供应商：默认按 输入¥2 / 输出¥8（保守估算），并在 UI 标注"估"。
"""

# PRICING[provider_key][model] = (input_元/Mtok, output_元/Mtok, cached_元/Mtok)
# cached 为 None 表示按 input 的 10% 近似
PRICING = {
    # ---------- DeepSeek 官方（2026-07 官方价目，人民币 元/百万 tokens）----------
    # deepseek-v4-flash: 输入1元/输出2元/缓存命中0.02元（缓存命中=未命中的1/50）
    # deepseek-v4-pro:   输入3元/输出6元/缓存命中0.025元
    # deepseek-chat = v4-flash 非思考模式别名；deepseek-reasoner = v4-flash 思考模式 → 同价
    "deepseek": {
        "deepseek-v4-flash": (1.0, 2.0, 0.02),
        "deepseek-v4-pro": (3.0, 6.0, 0.025),
        "deepseek-chat": (1.0, 2.0, 0.02),
        "deepseek-reasoner": (1.0, 2.0, 0.02),   # 思考模式与 flash 同价表
    },
    # ---------- OpenCode Zen（含免费模型） ----------
    "zen": {
        "deepseek-v4-flash-free": (0.0, 0.0, 0.0),   # 免费
        "deepseek-v4-flash": (1.0, 2.0, 0.02),       # Zen 的 flash 对标官方价
        "deepseek-v4-pro": (3.0, 6.0, 0.025),
        "kimi-k3": (2.0, 8.0, 0.2),                  # Kimi 官方 K3 档位
        "kimi-k2.7-code": (2.0, 8.0, 0.2),
        "kimi-k2.6": (2.0, 8.0, 0.2),
        "kimi-k2.5": (2.0, 8.0, 0.2),
        "glm-5.2": (2.0, 6.0, 0.2),                  # 智谱 GLM 档位
        "glm-5.1": (2.0, 6.0, 0.2),
        "glm-5": (2.0, 6.0, 0.2),
        "minimax-m3": (2.0, 8.0, 0.2),
        "minimax-m2.7": (2.0, 8.0, 0.2),
        "minimax-m2.5": (2.0, 8.0, 0.2),
        "mimo-v2.5-free": (0.0, 0.0, 0.0),           # 免费档
        "laguna-s-2.1-free": (0.0, 0.0, 0.0),
        "ling-3.0-flash-free": (0.0, 0.0, 0.0),
        "north-mini-code-free": (0.0, 0.0, 0.0),
        "nemotron-3-ultra-free": (0.0, 0.0, 0.0),
    },
    # ---------- OpenCode Go ----------
    "opencode_go": {
        "deepseek-v4-flash": (1.0, 2.0, 0.02),
        "deepseek-v4-pro": (3.0, 6.0, 0.025),
        "kimi-k3": (2.0, 8.0, 0.2),
        "kimi-k2.7-code": (2.0, 8.0, 0.2),
        "kimi-k2.6": (2.0, 8.0, 0.2),
        "glm-5.2": (2.0, 6.0, 0.2),
        "glm-5.1": (2.0, 6.0, 0.2),
        "grok-4.5": (3.0, 15.0, 0.3),                # xAI Grok 档位
        "hy3": (2.0, 8.0, 0.2),
        "mimo-v2.5": (2.0, 8.0, 0.2),
        "mimo-v2.5-pro": (3.0, 12.0, 0.3),
    },
    # ---------- 通义千问 ----------
    "qianwen": {
        "qwen-max": (2.4, 9.6, 0.24),
        "qwen-plus": (0.8, 2.0, 0.08),
        "qwen-turbo": (0.3, 0.6, 0.03),
        "qwen-long": (0.5, 2.0, 0.05),
        "qwen3-max": (2.4, 9.6, 0.24),
        "qwen3-plus": (0.8, 2.0, 0.08),
    },
    # ---------- 智谱 GLM ----------
    "zhipu": {
        "glm-4-plus": (2.0, 6.0, 0.2),
        "glm-4-air": (0.6, 1.8, 0.06),
        "glm-4-flash": (0.0, 0.0, 0.0),              # 免费档
        "glm-5-flash": (0.0, 0.0, 0.0),
    },
    # ---------- 硅基流动 ----------
    "siliconflow": {
        "deepseek-ai/DeepSeek-V3": (1.0, 2.0, 0.02),
        "deepseek-ai/DeepSeek-V3.1": (1.0, 2.0, 0.02),
        "deepseek-ai/DeepSeek-R1": (1.0, 2.0, 0.02),
        "Qwen/Qwen2.5-72B-Instruct": (0.56, 1.2, 0.056),
        "Qwen/QwQ-32B": (0.6, 2.4, 0.06),
        "Qwen/Qwen3-235B-A22B": (0.6, 2.4, 0.06),
        "THUDM/glm-4-9b-chat": (0.1, 0.1, 0.01),
    },
    # ---------- Kimi ----------
    "kimi": {
        "kimi-latest": (2.0, 8.0, 0.2),
        "kimi-k3": (2.0, 8.0, 0.2),
        "moonshot-v1-8k": (1.2, 6.0, 0.12),
        "moonshot-v1-32k": (2.4, 12.0, 0.24),
        "moonshot-v1-128k": (6.0, 24.0, 0.6),
    },
    # ---------- 豆包 ----------
    "doubao": {
        "doubao-pro-32k": (0.8, 2.0, 0.08),
        "doubao-lite-32k": (0.3, 0.6, 0.03),
    },
    # ---------- 本地模型：免费 ----------
    "local": {"*": (0.0, 0.0, 0.0)},
    "ollama": {"*": (0.0, 0.0, 0.0)},
}

# 未收录默认（保守估算）
DEFAULT_PRICE = (2.0, 8.0, 0.2)

# 缓存折扣：无 explicit 缓存价时，按输入价的 10%（行业惯例）
CACHE_DISCOUNT = 0.10


def get_price(provider_key, model):
    """返回 (input_元/Mtok, output_元/Mtok, cached_元/Mtok)。
    未收录 → 默认价格 + is_est=True；本地模型 → 全 0。"""
    prov = PRICING.get(provider_key or "")
    if not prov:
        return DEFAULT_PRICE + (True,)
    # 精确模型匹配
    if model and model in prov:
        p = prov[model]
        cached = p[2] if p[2] is not None else round(p[0] * CACHE_DISCOUNT, 4)
        return (p[0], p[1], cached, False)
    # 通配（本地）
    if "*" in prov:
        p = prov["*"]
        return (p[0], p[1], p[2], False)
    # 模型未收录：用该供应商最高价档兜底（保守）
    prices = [p for p in prov.values() if p[0] > 0 or p[1] > 0]
    if prices:
        hi = max(prices, key=lambda p: p[1])
        cached = hi[2] if hi[2] is not None else round(hi[0] * CACHE_DISCOUNT, 4)
        return (hi[0], hi[1], cached, True)
    return DEFAULT_PRICE + (True,)


def calc_cost(provider_key, model, input_tokens, output_tokens, cached_tokens):
    """按 元/百万 tokens 计算费用（人民币元）。返回 (cost, is_est)。
    费用 = 输入×输入价 + 输出×输出价 + 缓存×缓存价
    缓存命中 tokens 已包含在 input 中，但按缓存价计（只计一次，不重复计输入价）。"""
    pin, pout, pcached, is_est = get_price(provider_key, model)
    # 输入按"未命中部分"计全价：input 里除 cached 外都是新输入
    new_input = max(0, (input_tokens or 0) - (cached_tokens or 0))
    cost = (new_input * pin + (output_tokens or 0) * pout + (cached_tokens or 0) * pcached) / 1_000_000
    return cost, is_est
