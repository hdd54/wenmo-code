# -*- coding: utf-8 -*-
"""Local, auditable token-cost estimates.

All prices returned by this module are CNY per million tokens.  Remote-provider
amounts are estimates, never invoice truth.  A synced catalog is only applied
when its currency and unit were normalized by :mod:`pricing_sync`.
"""

import json
import math
import os
import threading
from tenant_state import tenant_file


try:
    USD_CNY_RATE = max(0.01, float(os.environ.get("WENMO_USD_CNY_RATE", "7.2")))
except (TypeError, ValueError):
    USD_CNY_RATE = 7.2


def _usd(input_price, output_price, cached_price):
    """Convert a USD quote to a CNY estimate using the configured local rate."""
    return tuple(round(value * USD_CNY_RATE, 6) for value in
                 (input_price, output_price, cached_price))


NON_TOKEN_BILLING = {"opencode_go": "subscription"}

# Values are (input, output, cached) CNY / million tokens.
PRICING = {
    "deepseek": {
        "deepseek-v4-flash": _usd(0.14, 0.28, 0.0028),
        "deepseek-v4-pro": _usd(0.435, 0.87, 0.003625),
        "deepseek-chat": _usd(0.14, 0.28, 0.0028),
        "deepseek-reasoner": _usd(0.14, 0.28, 0.0028),
    },
    "zen": {
        "deepseek-v4-flash-free": (0.0, 0.0, 0.0),
        "deepseek-v4-flash": _usd(0.14, 0.28, 0.028),
        "deepseek-v4-pro": _usd(1.74, 3.48, 0.145),
        "kimi-k3": _usd(3.0, 15.0, 0.30),
        "kimi-k2.7-code": _usd(0.95, 4.0, 0.19),
        "kimi-k2.6": _usd(0.95, 4.0, 0.16),
        "kimi-k2.5": _usd(0.60, 3.0, 0.10),
        "glm-5.2": _usd(1.40, 4.40, 0.26),
        "glm-5.1": _usd(1.40, 4.40, 0.26),
        "glm-5": _usd(1.0, 3.20, 0.20),
        "minimax-m3": _usd(0.30, 1.20, 0.06),
        "minimax-m2.7": _usd(0.30, 1.20, 0.06),
        "minimax-m2.5": _usd(0.30, 1.20, 0.06),
        "mimo-v2.5-free": (0.0, 0.0, 0.0),
        "laguna-s-2.1-free": (0.0, 0.0, 0.0),
        "ling-3.0-flash-free": (0.0, 0.0, 0.0),
        "north-mini-code-free": (0.0, 0.0, 0.0),
        "nemotron-3-ultra-free": (0.0, 0.0, 0.0),
    },
    "free": {
        # 聚合各平台免费档（官方确认免费，价格全 0）
        "deepseek-v4-flash-free": (0.0, 0.0, 0.0),
        "deepseek-chat": (0.0, 0.0, 0.0),
        "deepseek-reasoner": (0.0, 0.0, 0.0),
        "glm-4.7-flash": (0.0, 0.0, 0.0),
        "deepseek-ai/DeepSeek-OCR": (0.0, 0.0, 0.0),
        "tencent/Hunyuan-MT-7B": (0.0, 0.0, 0.0),
        "kimi-latest": (0.0, 0.0, 0.0),
        "kimi-k3": (0.0, 0.0, 0.0),
        "qwen-turbo": (0.0, 0.0, 0.0),
        "qwen-plus": (0.0, 0.0, 0.0),
        "doubao-lite-32k": (0.0, 0.0, 0.0),
    },
    "qianwen": {
        "qwen-max": (2.4, 9.6, 0.24),
        "qwen-plus": (0.8, 2.0, 0.08),
        "qwen-turbo": (0.3, 0.6, 0.03),
        "qwen-long": (0.5, 2.0, 0.05),
        "qwen3-max": (2.4, 9.6, 0.24),
        "qwen3-plus": (0.8, 2.0, 0.08),
    },
    "zhipu": {
        "glm-4-plus": (2.0, 6.0, 0.2),
        "glm-4-air": (0.6, 1.8, 0.06),
        "glm-4-flash": (0.0, 0.0, 0.0),
        "glm-5-flash": (0.0, 0.0, 0.0),
    },
    "siliconflow": {
        "deepseek-ai/DeepSeek-V3": (1.0, 2.0, 0.02),
        "deepseek-ai/DeepSeek-V3.1": (1.0, 2.0, 0.02),
        "deepseek-ai/DeepSeek-R1": (1.0, 2.0, 0.02),
        "Qwen/Qwen2.5-72B-Instruct": (0.56, 1.2, 0.056),
        "Qwen/QwQ-32B": (0.6, 2.4, 0.06),
        "Qwen/Qwen3-235B-A22B": (0.6, 2.4, 0.06),
        "THUDM/glm-4-9b-chat": (0.1, 0.1, 0.01),
    },
    "kimi": {
        "kimi-latest": (2.0, 8.0, 0.2),
        "kimi-k3": (2.0, 8.0, 0.2),
        "moonshot-v1-8k": (1.2, 6.0, 0.12),
        "moonshot-v1-32k": (2.4, 12.0, 0.24),
        "moonshot-v1-128k": (6.0, 24.0, 0.6),
    },
    "doubao": {
        "doubao-pro-32k": (0.8, 2.0, 0.08),
        "doubao-lite-32k": (0.3, 0.6, 0.03),
    },
    "local": {"*": (0.0, 0.0, 0.0)},
    "ollama": {"*": (0.0, 0.0, 0.0)},
}

DEFAULT_PRICE = (2.0, 8.0, 0.2)
CACHE_DISCOUNT = 0.10
_CATALOG_LOCK = threading.Lock()
_CATALOG_CACHE = {"path": "", "mtime_ns": None, "data": {}}


def _catalog_path():
    return tenant_file("pricing_catalogs.json")


def _load_catalogs():
    path = _catalog_path()
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        mtime_ns = None
    with _CATALOG_LOCK:
        if (_CATALOG_CACHE["path"], _CATALOG_CACHE["mtime_ns"]) == (path, mtime_ns):
            return _CATALOG_CACHE["data"]
        data = {}
        if mtime_ns is not None:
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                data = payload.get("providers", {}) if isinstance(payload, dict) else {}
            except (OSError, ValueError, TypeError):
                data = {}
        _CATALOG_CACHE.update(path=path, mtime_ns=mtime_ns, data=data)
        return data


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _synced_price(provider_key, model):
    catalog = _load_catalogs().get(str(provider_key or ""), {})
    if not isinstance(catalog, dict):
        return None, None
    # Refuse ambiguous data even if a malformed file was written by hand.
    if catalog.get("currency") != "CNY" or catalog.get("unit") != "per_million_tokens":
        return None, catalog
    row = (catalog.get("models") or {}).get(str(model or ""), {})
    if not isinstance(row, dict):
        return None, catalog
    input_price, output_price = _number(row.get("input")), _number(row.get("output"))
    if input_price is None or output_price is None:
        return None, catalog
    cached_price = _number(row.get("cached"))
    if cached_price is None:
        cached_price = round(input_price * CACHE_DISCOUNT, 9)
    return (input_price, output_price, cached_price), catalog


def get_price_source(provider_key, model):
    """Describe the active estimate source without implying invoice reconciliation."""
    if provider_key in NON_TOKEN_BILLING:
        return {
            "kind": "subscription", "label": "subscription; token cost unavailable",
            "invoice_reconciled": False,
        }
    synced, catalog = _synced_price(provider_key, model)
    if synced is not None:
        return {
            "kind": "synced_catalog", "label": "synced catalog estimate",
            "source": catalog.get("source", ""), "synced_at": catalog.get("synced_at", ""),
            "currency": "CNY", "unit": "per_million_tokens",
            "invoice_reconciled": False,
        }
    if provider_key in ("local", "ollama"):
        return {"kind": "local_free", "label": "local runtime", "invoice_reconciled": False}
    return {
        "kind": "bundled_estimate", "label": "bundled local estimate",
        "usd_cny_rate": USD_CNY_RATE, "invoice_reconciled": False,
    }


def get_price(provider_key, model):
    """Return ``(input, output, cached, estimated)`` in CNY / million tokens."""
    if provider_key in NON_TOKEN_BILLING:
        return None, None, None, True
    synced, _catalog = _synced_price(provider_key, model)
    if synced is not None:
        return synced + (True,)
    prov = PRICING.get(provider_key or "")
    if not prov:
        return DEFAULT_PRICE + (True,)
    if model and model in prov:
        row = prov[model]
        cached = row[2] if row[2] is not None else round(row[0] * CACHE_DISCOUNT, 4)
        estimated = provider_key not in ("local", "ollama") and any(row)
        return row[0], row[1], cached, estimated
    if "*" in prov:
        row = prov["*"]
        return row[0], row[1], row[2], False
    priced = [row for row in prov.values() if row[0] > 0 or row[1] > 0]
    if priced:
        row = max(priced, key=lambda item: item[1])
        cached = row[2] if row[2] is not None else round(row[0] * CACHE_DISCOUNT, 4)
        return row[0], row[1], cached, True
    return DEFAULT_PRICE + (True,)


def calc_cost(provider_key, model, input_tokens, output_tokens, cached_tokens):
    """Calculate an estimate; return ``(None, True)`` for non-token billing."""
    input_price, output_price, cached_price, estimated = get_price(provider_key, model)
    if input_price is None or output_price is None or cached_price is None:
        return None, True
    new_input = max(0, (input_tokens or 0) - (cached_tokens or 0))
    cost = (
        new_input * input_price
        + (output_tokens or 0) * output_price
        + (cached_tokens or 0) * cached_price
    ) / 1_000_000
    return cost, estimated


def get_billing_mode(provider_key):
    if provider_key in NON_TOKEN_BILLING:
        return NON_TOKEN_BILLING[provider_key]
    if provider_key in ("local", "ollama"):
        return "free"
    return "token"
