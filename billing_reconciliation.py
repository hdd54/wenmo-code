"""Official provider billing/usage connectors and tenant-scoped snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import time
import urllib.parse
import urllib.request

from network_safety import safe_urlopen
from secret_store import SecretStoreError, protect_secret, reveal_secret
from tenant_state import atomic_write_json, load_json, tenant_name


CONNECTORS = {
    "openai": {
        "label": "OpenAI 实际成本",
        "credential_env": "OPENAI_ADMIN_KEY",
        "kind": "provider_costs",
        "invoice_reconciled": True,
        "max_days": 180,
    },
    "anthropic": {
        "label": "Anthropic 官方使用量",
        "credential_env": "ANTHROPIC_ADMIN_KEY",
        "kind": "provider_usage",
        "invoice_reconciled": False,
        "max_days": 31,
    },
}


class BillingConnectorError(RuntimeError):
    pass


def _credential_scope(provider):
    return "%s:billing-admin:%s" % (tenant_name(), provider)


def _credential_payload():
    data = load_json("billing_credentials.json", {"version": 1, "providers": {}})
    return data if isinstance(data, dict) else {"version": 1, "providers": {}}


def save_admin_key(provider, value):
    if provider not in CONNECTORS:
        raise BillingConnectorError("unsupported billing connector")
    data = _credential_payload()
    providers = dict(data.get("providers") or {})
    value = str(value or "").strip()
    if value:
        providers[provider] = protect_secret(value, _credential_scope(provider))
    else:
        providers.pop(provider, None)
    atomic_write_json("billing_credentials.json", {"version": 1, "providers": providers})


def _admin_key(provider):
    value = str((_credential_payload().get("providers") or {}).get(provider) or "")
    if value:
        try:
            return reveal_secret(value, _credential_scope(provider))
        except SecretStoreError as exc:
            raise BillingConnectorError("stored admin key cannot be decrypted for this tenant") from exc
    # Machine environment credentials belong only to the local desktop tenant.
    if tenant_name() == "local":
        return os.environ.get(CONNECTORS[provider]["credential_env"], "").strip()
    return ""


def connector_status():
    result = []
    stored = _credential_payload().get("providers") or {}
    for provider, config in CONNECTORS.items():
        env_available = bool(
            tenant_name() == "local" and os.environ.get(config["credential_env"], "").strip())
        result.append({
            "provider": provider,
            "label": config["label"],
            "kind": config["kind"],
            "invoice_reconciled": config["invoice_reconciled"],
            "max_days": config["max_days"],
            "configured": bool(stored.get(provider) or env_available),
            "credential_source": "tenant_dpapi" if stored.get(provider) else (
                "process_environment" if env_available else "missing"),
        })
    return result


def get_state():
    data = load_json("billing_reconciliation.json", {"version": 1, "providers": {}})
    if not isinstance(data, dict):
        return {"version": 1, "providers": {}}
    data.setdefault("version", 1)
    data.setdefault("providers", {})
    return data


def _request_json(url, headers):
    request = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with safe_urlopen(request, timeout=30, https_only=True) as response:
        return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))


def normalize_openai_costs(pages, usd_cny_rate):
    daily = {}
    line_items = {}
    total_cny = 0.0
    result_count = 0
    for page in pages:
        for bucket in (page.get("data") or []):
            day = datetime.fromtimestamp(
                float(bucket.get("start_time") or 0), tz=timezone.utc).strftime("%Y-%m-%d")
            for result in (bucket.get("results") or []):
                amount = result.get("amount") or {}
                try:
                    value = float(amount.get("value") or 0)
                except (TypeError, ValueError):
                    continue
                currency = str(amount.get("currency") or "").upper()
                if currency == "USD":
                    cny = value * usd_cny_rate
                elif currency == "CNY":
                    cny = value
                else:
                    raise BillingConnectorError("unsupported OpenAI cost currency: %s" % currency)
                total_cny += cny
                daily[day] = daily.get(day, 0.0) + cny
                line = str(result.get("line_item") or "unclassified")[:200]
                line_items[line] = line_items.get(line, 0.0) + cny
                result_count += 1
    return {
        "total_cny": round(total_cny, 6),
        "daily": [{"date": key, "cost_cny": round(value, 6)}
                  for key, value in sorted(daily.items())],
        "line_items": [{"name": key, "cost_cny": round(value, 6)}
                       for key, value in sorted(line_items.items())],
        "result_count": result_count,
    }


def _sync_openai(key, start_time, end_time):
    try:
        usd_cny_rate = max(0.01, float(os.environ.get("WENMO_USD_CNY_RATE", "7.2")))
    except ValueError:
        usd_cny_rate = 7.2
    pages = []
    page = ""
    for _ in range(20):
        query = {
            "start_time": int(start_time), "end_time": int(end_time),
            "bucket_width": "1d", "limit": 180,
        }
        if page:
            query["page"] = page
        payload = _request_json(
            "https://api.openai.com/v1/organization/costs?" + urllib.parse.urlencode(query),
            {"Authorization": "Bearer " + key},
        )
        pages.append(payload)
        if not payload.get("has_more"):
            break
        page = str(payload.get("next_page") or "")
        if not page:
            break
    normalized = normalize_openai_costs(pages, usd_cny_rate)
    return {
        "kind": "provider_costs",
        "source": "https://api.openai.com/v1/organization/costs",
        "invoice_reconciled": True,
        "usage_reconciled": False,
        "usd_cny_rate": usd_cny_rate,
        **normalized,
    }


def normalize_anthropic_usage(pages):
    tokens = {"input": 0, "output": 0, "cached": 0, "cache_creation": 0}
    models = {}
    bucket_count = 0
    for page in pages:
        for bucket in (page.get("data") or []):
            bucket_count += 1
            for result in (bucket.get("results") or []):
                model = str(result.get("model") or "unknown")[:200]
                target = models.setdefault(model, {
                    "input": 0, "output": 0, "cached": 0, "cache_creation": 0})
                cache_creation = result.get("cache_creation") or {}
                values = {
                    "input": result.get("uncached_input_tokens") or 0,
                    "output": result.get("output_tokens") or 0,
                    "cached": result.get("cache_read_input_tokens") or 0,
                    "cache_creation": sum(int(value or 0) for value in cache_creation.values()),
                }
                for field, value in values.items():
                    value = max(0, int(value or 0))
                    tokens[field] += value
                    target[field] += value
    return {
        "tokens": tokens,
        "models": [{"model": key, **value} for key, value in sorted(models.items())],
        "bucket_count": bucket_count,
    }


def _sync_anthropic(key, start_time, end_time):
    pages = []
    page = ""
    starting_at = datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    ending_at = datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    for _ in range(20):
        query = [
            ("starting_at", starting_at), ("ending_at", ending_at),
            ("bucket_width", "1d"), ("limit", "31"), ("group_by[]", "model"),
        ]
        if page:
            query.append(("page", page))
        payload = _request_json(
            "https://api.anthropic.com/v1/organizations/usage_report/messages?"
            + urllib.parse.urlencode(query),
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        pages.append(payload)
        if not payload.get("has_more"):
            break
        page = str(payload.get("next_page") or "")
        if not page:
            break
    return {
        "kind": "provider_usage",
        "source": "https://api.anthropic.com/v1/organizations/usage_report/messages",
        "invoice_reconciled": False,
        "usage_reconciled": True,
        **normalize_anthropic_usage(pages),
    }


def sync_provider(provider, days=30):
    if provider not in CONNECTORS:
        raise BillingConnectorError("unsupported billing connector")
    key = _admin_key(provider)
    if not key:
        raise BillingConnectorError(
            "missing admin credential; configure it for this tenant or set %s"
            % CONNECTORS[provider]["credential_env"])
    days = max(1, min(int(days or 30), CONNECTORS[provider]["max_days"]))
    end_time = time.time()
    start_time = end_time - days * 86400
    if provider == "openai":
        snapshot = _sync_openai(key, start_time, end_time)
    else:
        snapshot = _sync_anthropic(key, start_time, end_time)
    snapshot.update({
        "provider": provider,
        "start_time": start_time,
        "end_time": end_time,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    })
    state = get_state()
    providers = dict(state.get("providers") or {})
    providers[provider] = snapshot
    atomic_write_json("billing_reconciliation.json", {"version": 1, "providers": providers})
    return snapshot
