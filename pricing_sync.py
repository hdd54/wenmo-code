"""Normalize provider model catalogs into auditable local price overrides."""

from datetime import datetime, timezone
import json
import os
import urllib.parse
import urllib.request
from network_safety import safe_urlopen


def normalize_catalog(payload, source, currency="CNY", unit="per_million", usd_cny_rate=None):
    currency = str(currency or "").upper()
    unit = str(unit or "").lower()
    try:
        usd_cny_rate = float(usd_cny_rate or os.environ.get("WENMO_USD_CNY_RATE", "7.2"))
    except (TypeError, ValueError):
        usd_cny_rate = 7.2
    currency_factor = {"CNY": 1.0, "USD": max(0.01, usd_cny_rate)}.get(currency)
    unit_factor = {
        "per_token": 1_000_000.0,
        "per_thousand": 1_000.0,
        "per_million": 1.0,
    }.get(unit)
    price_fields_ignored = False
    raw_models = (payload.get("models") or payload.get("data")) if isinstance(payload, dict) else []
    if isinstance(raw_models, dict):
        raw_models = [{"id": key, **(value if isinstance(value, dict) else {})}
                      for key, value in raw_models.items()]
    models = {}
    for item in raw_models or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        cost = item.get("cost") or item.get("pricing") or {}
        if not isinstance(cost, dict):
            continue
        normalized = {}
        for target, candidates in {
            "input": ("input", "prompt"),
            "output": ("output", "completion"),
            "cached": ("cache_read", "cached", "input_cached"),
        }.items():
            value = next((cost.get(key) for key in candidates if cost.get(key) is not None), None)
            try:
                if value is not None:
                    normalized[target] = float(value)
            except (TypeError, ValueError):
                pass
        if normalized and currency_factor is not None and unit_factor is not None:
            models[str(item["id"])] = {
                key: round(value * currency_factor * unit_factor, 9)
                for key, value in normalized.items()
            }
        elif normalized:
            price_fields_ignored = True
    available = [str(item.get("id")) for item in (raw_models or [])
                 if isinstance(item, dict) and item.get("id")]
    return {
        "source": str(source),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "available_models": available,
        "currency": "CNY" if models else "",
        "unit": "per_million_tokens" if models else "",
        "source_currency": currency,
        "source_unit": unit,
        "price_fields_ignored": price_fields_ignored,
    }


def fetch_catalog(url, api_key="", timeout=20, currency="", unit=""):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme != "https":
        raise ValueError("catalog URL must use public HTTPS")
    headers = {"Accept": "application/json", "User-Agent": "Wenmo-PricingSync/1"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(url, headers=headers)
    with safe_urlopen(
            request, timeout=max(1, min(int(timeout), 60)), https_only=True) as response:
        payload = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
    return normalize_catalog(payload, url, currency=currency, unit=unit)


def save_provider_catalog(path, provider, catalog):
    existing = {}
    try:
        with open(path, encoding="utf-8") as handle:
            existing = json.load(handle)
    except Exception:
        pass
    providers = existing.get("providers") if isinstance(existing, dict) else None
    providers = dict(providers or {})
    providers[str(provider)] = catalog
    data = {"version": 1, "providers": providers}
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return data
