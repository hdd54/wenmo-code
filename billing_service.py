"""Pure billing aggregation and provider reconciliation views."""

from __future__ import annotations

import datetime
import time

import history as history_store
from pricing import get_billing_mode


def _number(value):
    return float(value) if isinstance(value, (int, float)) else None


def _local_window(conversations, provider, start_time, end_time):
    cost = 0.0
    tokens = {"input": 0, "output": 0, "cached": 0}
    event_count = 0
    legacy_cost = 0.0
    legacy_count = 0
    for conversation in conversations:
        usage = history_store.refresh_usage_prices(
            conversation.get("usage") or {}, conversation.get("provider") or "",
            conversation.get("model") or "")
        events = usage.get("events") or []
        matching_events = [event for event in events if isinstance(event, dict)
                           and event.get("provider") == provider]
        if matching_events:
            for event in matching_events:
                ts = _number(event.get("ts")) or 0
                if ts < start_time or ts >= end_time:
                    continue
                event_count += 1
                event_cost = _number(event.get("cost"))
                if event_cost is not None:
                    cost += event_cost
                for field in tokens:
                    tokens[field] += max(0, int(event.get(field, 0) or 0))
            continue
        # Legacy histories predate the per-call timeline. Attribute their
        # provider total to the conversation update time and label it as proxy.
        updated = _number(conversation.get("updated")) or 0
        if updated < start_time or updated >= end_time:
            continue
        for entry in (usage.get("by_model") or {}).values():
            if not isinstance(entry, dict) or entry.get("provider") != provider:
                continue
            legacy_count += 1
            entry_cost = _number(entry.get("cost"))
            if entry_cost is not None:
                legacy_cost += entry_cost
            for field in tokens:
                tokens[field] += max(0, int(entry.get(field, 0) or 0))
    return {
        "cost": round(cost + legacy_cost, 6),
        "tokens": tokens,
        "event_count": event_count,
        "legacy_proxy_count": legacy_count,
        "scope_quality": "exact_events" if event_count and not legacy_count else (
            "mixed_with_legacy_proxy" if event_count else "legacy_update_time_proxy"),
    }


def _reconciliation_rows(conversations, state):
    rows = []
    for provider, snapshot in ((state or {}).get("providers") or {}).items():
        if not isinstance(snapshot, dict):
            continue
        start_time = _number(snapshot.get("start_time")) or 0
        end_time = _number(snapshot.get("end_time")) or time.time()
        local = _local_window(conversations, provider, start_time, end_time)
        actual_cost = _number(snapshot.get("total_cny"))
        row = {
            "provider": provider,
            "kind": snapshot.get("kind") or "",
            "synced_at": snapshot.get("synced_at") or "",
            "start_time": start_time,
            "end_time": end_time,
            "invoice_reconciled": bool(snapshot.get("invoice_reconciled")),
            "usage_reconciled": bool(snapshot.get("usage_reconciled")),
            "actual_cost": actual_cost,
            "local_estimate": local["cost"],
            "local_tokens": local["tokens"],
            "provider_tokens": snapshot.get("tokens") or {},
            "scope_quality": local["scope_quality"],
            "event_count": local["event_count"],
            "legacy_proxy_count": local["legacy_proxy_count"],
            "currency": "CNY" if actual_cost is not None else "",
        }
        if actual_cost is not None:
            row["variance"] = round(local["cost"] - actual_cost, 6)
            row["variance_percent"] = (
                round((local["cost"] - actual_cost) / actual_cost * 100, 2)
                if actual_cost else None)
        rows.append(row)
    rows.sort(key=lambda item: item.get("provider") or "")
    return rows


def build_billing_stats(conversations=None, reconciliation_state=None, now=None):
    conversations = (history_store.scan_all_conversations()
                     if conversations is None else list(conversations))
    now = float(now or time.time())
    day_sec = 86400
    aggregates = {"today": 0.0, "week": 0.0, "month": 0.0, "year": 0.0}
    by_model = {}
    by_conversation = []
    daily = {}

    for conversation in conversations:
        provider = conversation.get("provider") or ""
        model = conversation.get("model") or ""
        usage = history_store.refresh_usage_prices(
            conversation.get("usage") or {}, provider, model)
        input_tokens = usage.get("input") or 0
        output_tokens = usage.get("output") or 0
        ledger = usage.get("by_model") or {}
        cost = _number(usage.get("cost"))
        if cost is None and not (input_tokens or output_tokens):
            continue
        billing_mode = "mixed" if len(ledger) > 1 else get_billing_mode(provider)
        if ledger:
            for entry in ledger.values():
                if not isinstance(entry, dict):
                    continue
                entry_cost = _number(entry.get("cost"))
                if entry_cost is None:
                    continue
                key = (entry.get("provider") or "", entry.get("model") or "")
                item = by_model.setdefault(key, {
                    "provider": key[0], "model": key[1], "cost": 0.0, "convs": 0})
                item["cost"] += entry_cost
                item["convs"] += 1
        elif cost is not None:
            key = (provider, model)
            item = by_model.setdefault(key, {
                "provider": provider, "model": model, "cost": 0.0, "convs": 0})
            item["cost"] += cost
            item["convs"] += 1
        updated = _number(conversation.get("updated")) or 0
        by_conversation.append({
            "cid": conversation.get("id") or "",
            "title": conversation.get("title") or "未命名",
            "provider": provider,
            "model": model,
            "cost": cost,
            "cost_est": bool(usage.get("cost_est", True)),
            "billing_mode": billing_mode,
            "cost_unavailable": bool(usage.get("cost_unavailable")),
            "model_costs": [
                {"provider": entry.get("provider") or "", "model": entry.get("model") or "",
                 "cost": entry.get("cost")}
                for entry in ledger.values() if isinstance(entry, dict)
            ] if ledger else ([{"provider": provider, "model": model, "cost": cost}]
                              if cost is not None else []),
            "tokens": input_tokens + output_tokens,
            "updated": updated,
        })
        if updated and cost is not None:
            for name, days in (("today", 1), ("week", 7), ("month", 30), ("year", 365)):
                if updated >= now - days * day_sec:
                    aggregates[name] += cost
            day = datetime.datetime.fromtimestamp(updated).strftime("%m-%d")
            daily[day] = daily.get(day, 0.0) + cost

    by_conversation.sort(key=lambda item: item.get("updated") or 0, reverse=True)
    models = sorted(by_model.values(), key=lambda item: item["cost"], reverse=True)
    total = sum(item["cost"] for item in by_conversation if item.get("cost") is not None)
    reconciliations = _reconciliation_rows(conversations, reconciliation_state or {})
    return {
        "total": round(total, 4),
        "by_conv": by_conversation[:200],
        "by_model": models,
        "windows": {key: round(value, 4) for key, value in aggregates.items()},
        "daily": [{"date": day, "cost": round(value, 4)}
                  for day, value in sorted(daily.items())],
        "billing_basis": "provider_reconciled" if any(
            item.get("invoice_reconciled") for item in reconciliations) else "local_estimate",
        "invoice_reconciled": any(item.get("invoice_reconciled") for item in reconciliations),
        "has_unpriced_usage": any(item.get("cost_unavailable") or item.get("cost") is None
                                  for item in by_conversation),
        "reconciliation": reconciliations,
    }
