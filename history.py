"""
对话历史存储：每条对话一个 JSON 文件。
存储位置【跟随项目文件夹】：<项目文件夹>/.wenmo/<项目id>/<对话id>.json
（项目文件在哪，历史就存在那个文件夹里的 .wenmo 隐藏文件夹中）
兼容旧集中目录（BASE/history 及 BASE/<pid>），读取时自动回退，历史数据不丢。
启动时调用 migrate_history_to_projects() 把旧历史搬进各自项目文件夹。
"""
import json
import hashlib
import os
import re
import shutil
import time
import uuid
import threading

from execution_context import current_tenant

# 模块级写锁：Windows 上并发 os.replace 到同一目标文件会 WinError 5（目标被占用）。
# 串行化写操作（单次写 <1ms，对吞吐无影响），保证并发保存同一对话不冲突。
_WRITE_LOCK = threading.RLock()
_PENDING_USAGE = {}

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")
PROJECTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects.json")
DEFAULT_PROJECT = "default"
# 打包版：数据目录优先用 WENMO_DATA_DIR（%APPDATA%/问墨），否则用项目内 history/
# 可选的进程级数据目录覆盖；HTTP 登录本身只提供本机身份显示，不能在并发请求间切换此全局目录。
import os as _os
_env_data = _os.environ.get("WENMO_DATA_DIR")
_env_user = _os.environ.get("WENMO_USER_DIR")
if _env_user:
    # 已登录用户：数据存 <data>/users/<login>/
    BASE = _os.path.join(_env_user, "history")
    PROJECTS_FILE = _os.path.join(_env_user, "projects.json")
elif _env_data:
    BASE = _os.path.join(_env_data, "history")
    PROJECTS_FILE = _os.path.join(_env_data, "projects.json")
os.makedirs(BASE, exist_ok=True)


def _tenant_name():
    return _safe_dir(current_tenant.get() or "local")


def _base_dir():
    tenant = _tenant_name()
    if tenant == "local":
        return BASE
    data_root = _os.environ.get("WENMO_DATA_DIR") or os.path.dirname(os.path.abspath(BASE))
    path = os.path.join(data_root, "users", tenant, "history")
    os.makedirs(path, exist_ok=True)
    return path


def _projects_file():
    tenant = _tenant_name()
    if tenant == "local":
        return PROJECTS_FILE
    data_root = _os.environ.get("WENMO_DATA_DIR") or os.path.dirname(os.path.abspath(BASE))
    path = os.path.join(data_root, "users", tenant, "projects.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _tmp_path(final_path):
    """原子写入的临时文件路径：加唯一后缀，防多线程/多进程并发写同一对话时
    tmp 文件名冲突（Permission denied）。替换目标始终是 final_path。"""
    return "%s.tmp.%s" % (final_path, uuid.uuid4().hex[:8])


def _atomic_write(fp, data):
    """原子写盘：写唯一 tmp → fsync → os.replace 到目标。
    全程持模块级写锁，避免 Windows 并发 replace 同一目标文件时 WinError 5。"""
    tmp = _tmp_path(fp)
    with _WRITE_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, fp)   # 原子替换：中断也不会产生半写文件
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
    except Exception:
        pass


def _load_projects_data():
    """读取 projects.json 里的项目原始数据（不触发默认项目兜底插入）"""
    try:
        with open(_projects_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data.get("projects", []) or []
    except Exception:
        return []


def _safe_dir(name):
    """项目 id → 安全目录名（防路径穿越）"""
    s = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', str(name or "default"))
    return s or "default"


def project_hist_dir(pid):
    """项目专用历史目录：<项目文件夹>/.wenmo/<项目id>/
    历史跟随项目文件夹：项目文件在哪，历史就存在那个文件夹里的 .wenmo 隐藏文件夹中。
    项目 path 为空/失效/不存在时，回退到集中目录 BASE/<项目id>（历史不丢）。"""
    pid = str(pid or DEFAULT_PROJECT)
    for p in _load_projects_data():
        if str(p.get("id")) == pid:
            path = (p.get("path") or "").strip()
            if path and os.path.isdir(path):
                if _tenant_name() == "local":
                    return os.path.join(path, ".wenmo", _safe_dir(pid))
                return os.path.join(path, ".wenmo", "users", _tenant_name(), _safe_dir(pid))
    return os.path.join(_base_dir(), _safe_dir(pid))


def iter_history_dirs():
    """所有可能存放历史文件的目录（去重）：
    1) 每个项目的 <项目文件夹>/.wenmo/<项目id>
    2) 旧集中目录 BASE（兼容迁移前/无法迁移的数据）"""
    seen = set()
    dirs = []
    # New conversations always use BASE/default when the default project has no path.
    # Include it even before projects.json has been initialized, otherwise immediate
    # rename/delete lookups can miss a conversation that was just saved there.
    base_dir = _base_dir()
    default_dir = os.path.join(base_dir, _safe_dir(DEFAULT_PROJECT))
    default_key = os.path.normcase(os.path.normpath(default_dir))
    seen.add(default_key)
    dirs.append(default_dir)
    for p in _load_projects_data():
        d = project_hist_dir(p.get("id"))
        key = os.path.normcase(os.path.normpath(d))
        if key not in seen:
            seen.add(key)
            dirs.append(d)
    key = os.path.normcase(os.path.normpath(base_dir))
    if key not in seen:
        seen.add(key)
        dirs.append(base_dir)
    return dirs


def _path(cid, project=None):
    """对话文件路径：优先项目文件夹 .wenmo/<项目id>/<cid>.json。
    project 为空时：先查旧位置（BASE/<cid>.json 兼容迁移前的数据），找不到返回新位置。"""
    if project:
        pdir = project_hist_dir(project)
        os.makedirs(pdir, exist_ok=True)
        return os.path.join(pdir, f"{cid}.json")
    # 旧位置（迁移前的平铺文件）
    return os.path.join(_base_dir(), f"{cid}.json")


def _find_file(cid, project=None):
    """查找对话文件：
    项目 .wenmo → 旧集中 BASE/<pid> → 旧平铺 BASE/<cid> → 全历史目录兜底搜索"""
    if project:
        p = os.path.join(project_hist_dir(project), f"{cid}.json")
        if os.path.isfile(p):
            return p
        # 旧位置：BASE/<pid>/<cid>.json
        old = os.path.join(_base_dir(), _safe_dir(project), f"{cid}.json")
        if os.path.isfile(old):
            return old
    # 旧平铺位置
    old = os.path.join(_base_dir(), f"{cid}.json")
    if os.path.isfile(old):
        return old
    # 兜底：遍历所有历史目录（项目不确定/历史在别处时）
    for d in iter_history_dirs():
        fp = os.path.join(d, f"{cid}.json")
        if os.path.isfile(fp):
            return fp
    return None


def _conv_text(messages):
    """对话全文（搜索用）：拼接所有文本内容"""
    parts = []
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.append(" ".join(p.get("text", "") for p in c if p.get("type") == "text"))
    return "\n".join(parts)


def _last_preview(messages):
    """最后一条消息的预览（副行显示用，≤36 字）"""
    for m in reversed(messages or []):
        c = m.get("content")
        if m.get("role") in ("user", "assistant") and isinstance(c, str) and c.strip():
            return c.strip().replace("\n", " ")[:36]
        if m.get("role") in ("user", "assistant") and isinstance(c, list):
            t = " ".join(p.get("text", "") for p in c if p.get("type") == "text").strip()
            if t:
                return t.replace("\n", " ")[:36]
    return ""


def list_conversations(project=None, q=None, limit=None, offset=0):
    """返回 ({id,title,updated,messages,project,provider,model,pin,preview}, total)
    排序：置顶优先，再按更新时间倒序；支持 q 全文搜索与分页
    遍历：项目 .wenmo 目录 + 旧集中目录（seen 去重，防同一文件被扫两次）"""
    out = []
    seen = set()
    for d in iter_history_dirs():
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            if os.path.basename(root) == "_preview_cache":
                continue
            for fn in files:
                if not fn.endswith(".json"):
                    continue
                fp = os.path.normcase(os.path.normpath(os.path.join(root, fn)))
                if fp in seen:
                    continue
                seen.add(fp)
                try:
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        d = json.load(f)
                except Exception:
                    continue   # 跳过损坏文件（防单个坏文件导致对话"消失"）
                if project and d.get("project", DEFAULT_PROJECT) != project:
                    continue
                if q:
                    hay = (d.get("title") or "") + " " + _conv_text(d.get("messages", []))
                    if q.lower() not in hay.lower():
                        continue
                out.append({
                    "id": d.get("id", fn[:-5]),
                    "title": d.get("title") or "新对话",
                    "updated": d.get("updated", 0),
                    "messages": len(d.get("messages", [])),
                    "project": d.get("project", DEFAULT_PROJECT),
                    "provider": d.get("provider", ""),
                    "model": d.get("model", ""),
                    "pin": bool(d.get("pin", False)),
                    "preview": _last_preview(d.get("messages", [])),
                })
    out.sort(key=lambda x: (not x["pin"], -x["updated"]))
    total = len(out)
    if offset:
        out = out[offset:]
    if limit:
        out = out[:limit]
    return out, total


def merge_cumulative_usage(previous, current):
    """Merge one completed model request into authoritative conversation totals."""
    previous = previous or {}
    current = current or {}
    merged = dict(previous)
    for key in ("input", "output", "output_formal", "cached", "reasoning"):
        merged[key] = (previous.get(key) or 0) + (current.get(key) or 0)
    if "cost" in previous or "cost" in current:
        merged["cost"] = (previous.get("cost") or 0) + (current.get("cost") or 0)
    else:
        merged.pop("cost", None)
    for key in ("context", "last_input", "last_cached"):
        if key in current:
            merged[key] = current.get(key) or 0
    merged["cost_est"] = bool(previous.get("cost_est") or current.get("cost_est"))
    merged["cost_unavailable"] = bool(
        previous.get("cost_unavailable") or current.get("cost_unavailable"))
    if current.get("cost_source"):
        merged["cost_source"] = current["cost_source"]
    breakdown = {}
    for source in (previous.get("by_model") or {}, current.get("by_model") or {}):
        if not isinstance(source, dict):
            continue
        for key, entry in source.items():
            if not isinstance(entry, dict):
                continue
            breakdown[key] = merge_cumulative_usage(breakdown.get(key), entry)
            breakdown[key]["provider"] = entry.get("provider") or breakdown[key].get("provider", "")
            breakdown[key]["model"] = entry.get("model") or breakdown[key].get("model", "")
    if breakdown:
        merged["by_model"] = breakdown
    events = []
    for source in (previous.get("events") or [], current.get("events") or []):
        if isinstance(source, list):
            events.extend(item for item in source if isinstance(item, dict))
    if events:
        # Keep an auditable per-call timeline for provider reconciliation while
        # placing a hard bound on very old conversations.
        merged["events"] = events[-5000:]
    return merged


def reprice_usage(usage, provider, model):
    """Rebuild cost from cumulative tokens so legacy/double-counted values cannot linger."""
    usage = dict(usage or {})
    if not usage or not provider:
        return usage
    try:
        from pricing import calc_cost, get_price_source
        cost, estimated = calc_cost(
            provider, model, usage.get("input", 0), usage.get("output", 0),
            usage.get("cached", 0))
        usage["cost_source"] = get_price_source(provider, model)
    except Exception:
        return usage
    usage["cost_est"] = bool(estimated)
    if cost is None:
        usage.pop("cost", None)
        usage["cost_unavailable"] = True
    else:
        usage["cost"] = cost
        usage.pop("cost_unavailable", None)
    return usage


def price_usage_delta(usage, provider, model):
    """Price one provider call and attach an auditable per-model ledger entry."""
    priced = reprice_usage(usage, provider, model)
    if not priced or not provider:
        return priced
    key = "%s/%s" % (provider, model or "")
    entry = {field: priced.get(field, 0) for field in (
        "input", "output", "output_formal", "cached", "reasoning")}
    for field in ("cost", "cost_est", "cost_unavailable", "cost_source"):
        if field in priced:
            entry[field] = priced[field]
    entry["provider"] = provider
    entry["model"] = model or ""
    priced["by_model"] = {key: entry}
    event = dict(entry)
    event["ts"] = float(usage.get("ts") or time.time())
    event["request_id"] = str(usage.get("request_id") or "")[:128]
    priced["events"] = [event]
    return priced


def refresh_usage_prices(usage, provider="", model=""):
    """Refresh estimates per model without applying the last model to all tokens."""
    usage = dict(usage or {})
    ledger = usage.get("by_model")
    if not isinstance(ledger, dict) or not ledger:
        return reprice_usage(usage, provider, model)
    refreshed = {}
    total_cost = 0.0
    has_cost = False
    unavailable = False
    estimated = False
    for key, entry in ledger.items():
        if not isinstance(entry, dict):
            continue
        entry_provider = str(entry.get("provider") or "")
        entry_model = str(entry.get("model") or "")
        priced = reprice_usage(entry, entry_provider, entry_model)
        priced["provider"] = entry_provider
        priced["model"] = entry_model
        refreshed[key] = priced
        if isinstance(priced.get("cost"), (int, float)):
            total_cost += priced["cost"]
            has_cost = True
        unavailable = unavailable or bool(priced.get("cost_unavailable"))
        estimated = estimated or bool(priced.get("cost_est"))
    usage["by_model"] = refreshed
    usage["cost_est"] = estimated
    usage["cost_unavailable"] = unavailable
    usage["cost_source"] = {
        "kind": "mixed_model_ledger" if len(refreshed) > 1 else "model_ledger",
        "invoice_reconciled": False,
        "entries": len(refreshed),
    }
    if has_cost:
        usage["cost"] = round(total_cost, 10)
    else:
        usage.pop("cost", None)
    return usage


def normalize_messages(messages):
    """Return a stable, oldest-to-newest message list.

    New messages carry a client generated id and millisecond timestamp.  Legacy
    records did not, so they receive deterministic sequence metadata and a
    timestamp just before the first dated message.  ``seq`` is retained as the
    final tie breaker so equal timestamps never reorder a conversation.
    """
    source = [dict(message) for message in (messages or []) if isinstance(message, dict)]
    numeric_ts = [m.get("ts") for m in source if isinstance(m.get("ts"), (int, float))]
    if numeric_ts:
        missing_base = int(min(numeric_ts)) - len(source) - 1
    else:
        # Pure legacy conversations must normalize identically on every read.
        missing_base = 0
    missing_index = 0
    normalized = []
    for index, message in enumerate(source):
        message.setdefault("seq", index + 1)
        if not isinstance(message.get("ts"), (int, float)):
            message["ts"] = missing_base + missing_index
            missing_index += 1
        if not message.get("id"):
            identity = json.dumps(
                [index, message.get("role"), message.get("content")],
                ensure_ascii=False, sort_keys=True, default=str,
            )
            message["id"] = "legacy-" + hashlib.sha256(
                identity.encode("utf-8")).hexdigest()[:24]
        normalized.append(message)
    normalized.sort(key=lambda item: (item.get("ts", 0), item.get("seq", 0), item.get("id", "")))
    return normalized


def append_message(cid, message, project=DEFAULT_PROJECT, provider="", model="", title=None,
                   usage_delta=None):
    """Atomically append one durable message before any model work starts.

    Message ids make browser retries idempotent.  This narrow append operation
    avoids the stale full-snapshot overwrite race that previously occurred when
    navigation and a streaming response completed at the same time.
    """
    if not isinstance(message, dict) or message.get("role") not in ("user", "assistant", "tool"):
        raise ValueError("invalid message")
    incoming = dict(message)
    incoming.setdefault("id", "m-" + uuid.uuid4().hex)
    incoming.setdefault("ts", int(time.time() * 1000))
    with _WRITE_LOCK:
        cid = cid or uuid.uuid4().hex[:12]
        existing = get_conversation(cid, project) or {}
        messages = normalize_messages(existing.get("messages") or [])
        duplicate = next((m for m in messages if m.get("id") == incoming["id"]), None)
        if duplicate is not None:
            return cid, duplicate
        incoming.setdefault("seq", max((m.get("seq", 0) for m in messages), default=0) + 1)
        messages.append(incoming)
        save_conversation(
            messages,
            cid=cid,
            title=title or existing.get("title"),
            project=project,
            provider=provider or existing.get("provider") or "",
            model=model or existing.get("model") or "",
            usage=existing.get("usage") or {},
            usage_delta=usage_delta,
        )
        stored = next(m for m in normalize_messages(messages) if m.get("id") == incoming["id"])
        return cid, stored


def save_conversation(messages, cid=None, title=None, project=DEFAULT_PROJECT, provider="", model="",
                      usage=None, usage_delta=None):
    """保存（或更新）一条对话；自动用第一条用户消息做标题；保留原置顶状态；完整保存用量。
    原子写入（临时文件 + rename）：防中断导致 JSON 损坏（历史数据丢失根因）。
    文件写到项目文件夹的 .wenmo/<项目id>/ 下（历史跟随项目文件夹）。"""
    cid = cid or uuid.uuid4().hex[:12]
    messages = normalize_messages(messages)
    with _WRITE_LOCK:
        existing = get_conversation(cid, project) or {}
        pin = bool(existing.get("pin", False))
        if messages:
            first_user = next(
                (m["content"] for m in messages
                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                "",
            )
            title = (title or existing.get("title") or first_user or "新对话")[:30]
        provider = provider or existing.get("provider") or ""
        model = model or existing.get("model") or ""
        effective_usage = existing.get("usage") or usage or {}
        if not existing and effective_usage:
            effective_usage = price_usage_delta(effective_usage, provider, model)
        pending = _PENDING_USAGE.pop((_tenant_name(), cid), None)
        if pending:
            effective_usage = merge_cumulative_usage(effective_usage, pending)
        if usage_delta:
            effective_usage = merge_cumulative_usage(
                effective_usage, price_usage_delta(usage_delta, provider, model))
        final_path = _path(cid, project)
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        data = {"id": cid, "title": title, "updated": time.time(), "project": project,
                "provider": provider, "model": model, "pin": pin,
                "usage": effective_usage,
                "version": 2, "schema": "wenmo-chat-v2",
                "messages": messages}
        _atomic_write(final_path, data)
        return cid


def add_conversation_usage(cid, usage_delta, project=None, provider="", model=""):
    """Atomically add model usage without replacing conversation messages.

    Comparison/branch requests use this path. If the main stream has not created the
    conversation yet, keep the delta in memory and merge it into the first save.
    """
    if not cid or not usage_delta:
        return False
    with _WRITE_LOCK:
        priced_delta = price_usage_delta(usage_delta, provider, model)
        conv = get_conversation(cid, project)
        if conv is None:
            usage_key = (_tenant_name(), cid)
            _PENDING_USAGE[usage_key] = merge_cumulative_usage(
                _PENDING_USAGE.get(usage_key), priced_delta)
            return True
        fp = _find_file(cid, project or conv.get("project"))
        if not fp:
            return False
        conv["usage"] = merge_cumulative_usage(conv.get("usage"), priced_delta)
        conv["updated"] = time.time()
        _atomic_write(fp, conv)
        return True


def set_pin(cid, pin):
    """置顶 / 取消置顶"""
    conv = get_conversation(cid)
    if conv is None:
        return False
    conv["pin"] = bool(pin)
    fp = _find_file(cid, conv.get("project"))
    if not fp:
        return False
    _atomic_write(fp, conv)
    return True


def rename_conversation(cid, title):
    """重命名对话"""
    conv = get_conversation(cid)
    if conv is None:
        return False
    conv["title"] = (title or "").strip()[:30] or "新对话"
    fp = _find_file(cid, conv.get("project"))
    if not fp:
        return False
    _atomic_write(fp, conv)
    return True


def get_conversation(cid, project=None):
    """读取对话：兼容项目 .wenmo + 旧集中/平铺位置。
    版本兼容：v1 旧文件（无 version 字段）读取时自动补 version=1 标记，
    便于上层判断字段可用性（v2 才有 usage 明细/stepHistory 等）。"""
    try:
        fp = _find_file(cid, project)
        if not fp:
            return None
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("version", 1)
            d.setdefault("schema", "wenmo-chat-v1")
            d["messages"] = normalize_messages(d.get("messages") or [])
        return d
    except Exception:
        return None


def delete_conversation(cid):
    try:
        fp = _find_file(cid)
        if not fp:
            return False
        os.remove(fp)
        return True
    except Exception:
        return False


def migrate_history_to_projects():
    """把旧集中目录 BASE/<项目id>/*.json 迁移到 <项目文件夹>/.wenmo/<项目id>/（幂等）。
    逐文件移动（shutil.move 跨盘安全）；目标已有同名文件则跳过（以新为准）；
    单文件失败自动跳过（读取时仍兼容旧位置，历史不丢）。
    返回移动的文件数。"""
    moved = 0
    for p in _load_projects_data():
        pid = str(p.get("id"))
        old_dir = os.path.join(_base_dir(), _safe_dir(pid))
        if not os.path.isdir(old_dir):
            continue
        path = (p.get("path") or "").strip()
        if not (path and os.path.isdir(path)):
            continue  # 项目文件夹不可用 → 保留在集中目录（读取兼容）
        new_dir = project_hist_dir(pid)
        try:
            os.makedirs(new_dir, exist_ok=True)
        except Exception:
            continue
        for fn in os.listdir(old_dir):
            if not fn.endswith(".json") or ".tmp" in fn:
                continue
            src = os.path.join(old_dir, fn)
            dst = os.path.join(new_dir, fn)
            try:
                if os.path.isfile(dst):
                    continue
                shutil.move(src, dst)
                moved += 1
            except Exception:
                pass
    return moved


def scan_all_conversations():
    """扫描所有历史目录（项目 .wenmo + 旧集中目录）读完整 JSON（含 usage/cost）。
    用于用量统计等需要完整字段的场景；跳过 .tmp 与 _preview_cache。"""
    convs = []
    seen = set()
    for d in iter_history_dirs():
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            if os.path.basename(root) == "_preview_cache":
                continue
            for fn in files:
                if not fn.endswith(".json") or ".tmp" in fn:
                    continue
                fp = os.path.normcase(os.path.normpath(os.path.join(root, fn)))
                if fp in seen:
                    continue
                seen.add(fp)
                try:
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        c = json.load(f)
                    if isinstance(c, dict) and c.get("id"):
                        convs.append(c)
                except Exception:
                    pass
    return convs


def cleanup_tmp_files():
    """清理所有历史目录（项目 .wenmo + 旧集中目录）里的残留 .tmp* 临时文件"""
    for d in iter_history_dirs():
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            for fn in files:
                if ".tmp" in fn:
                    try:
                        os.remove(os.path.join(root, fn))
                    except Exception:
                        pass


# ---------------- 项目 ----------------

def list_projects():
    """返回 [{id, name, path, created}]，始终包含默认项目"""
    out = []
    try:
        with open(_projects_file(), encoding="utf-8") as f:
            data = json.load(f)
        out = data.get("projects", [])
    except Exception:
        pass
    if not any(p.get("id") == DEFAULT_PROJECT for p in out):
        out.insert(0, {"id": DEFAULT_PROJECT, "name": "默认项目", "path": "", "created": time.time()})
        _save_projects(out)
    return out


def _save_projects(projects):
    # 原子写：防服务器崩溃/断电时 projects.json 半写损坏（直接 open("w") 覆盖可能截断文件，
    # 导致 json.load 失败 → 项目列表全丢。复用 _atomic_write（tmp + fsync + replace）。
    _atomic_write(_projects_file(), {"projects": projects})


def add_project(name, path=""):
    name = (name or "").strip()[:40] or "未命名项目"
    projects = list_projects()
    pid = uuid.uuid4().hex[:12]
    projects.append({"id": pid, "name": name, "path": path.strip(), "created": time.time()})
    _save_projects(projects)
    return pid


def rename_project(pid, name):
    name = (name or "").strip()[:40] or "未命名项目"
    projects = list_projects()
    for p in projects:
        if p.get("id") == pid:
            p["name"] = name
            _save_projects(projects)
            return True
    return False


def update_project(pid, **fields):
    """更新项目自定义属性：icon_color(图标颜色) / icon_text(图标显示文字) / launch_cmd(启动脚本) / path(文件夹路径)"""
    projects = list_projects()
    for p in projects:
        if p.get("id") == pid:
            for k, v in fields.items():
                if v is not None and k in ("icon_color", "icon_text", "launch_cmd", "path"):
                    p[k] = str(v).strip()
            _save_projects(projects)
            return True
    return False


def delete_project(pid):
    if pid == DEFAULT_PROJECT:
        return False  # 默认项目不可删
    projects = list_projects()
    target = next((p for p in projects if p.get("id") == pid), None)
    if target is None:
        return False

    # 必须在项目注册表仍存在时解析/删除历史；先删注册表会让 project_hist_dir
    # 回退到 BASE，造成项目目录中的对话成为孤儿数据。
    conversations = list_conversations(project=pid)[0]
    try:
        for conv in conversations:
            fp = _find_file(conv["id"], pid)
            if fp and os.path.isfile(fp):
                os.remove(fp)

        dirs = {os.path.join(_base_dir(), _safe_dir(pid))}
        path = (target.get("path") or "").strip()
        if path and os.path.isdir(path):
            if _tenant_name() == "local":
                dirs.add(os.path.join(path, ".wenmo", _safe_dir(pid)))
            else:
                dirs.add(os.path.join(path, ".wenmo", "users", _tenant_name(), _safe_dir(pid)))
        for hist_dir in dirs:
            if os.path.isdir(hist_dir):
                shutil.rmtree(hist_dir)

        _save_projects([p for p in projects if p.get("id") != pid])
        return True
    except OSError:
        return False
