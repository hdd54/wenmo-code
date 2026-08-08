"""
对话历史存储：每条对话一个 JSON 文件，放在 history/ 目录。
对标 opencode：会话按"项目"分组保存、可切换、可删除，关闭软件不丢。
"""
import json
import os
import time
import uuid
import threading

# 模块级写锁：Windows 上并发 os.replace 到同一目标文件会 WinError 5（目标被占用）。
# 串行化写操作（单次写 <1ms，对吞吐无影响），保证并发保存同一对话不冲突。
_WRITE_LOCK = threading.Lock()

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")
PROJECTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects.json")
DEFAULT_PROJECT = "default"
# 打包版：数据目录优先用 WENMO_DATA_DIR（%APPDATA%/问墨），否则用项目内 history/
import os as _os
_env_data = _os.environ.get("WENMO_DATA_DIR")
if _env_data:
    BASE = _os.path.join(_env_data, "history")
    PROJECTS_FILE = _os.path.join(_env_data, "projects.json")
os.makedirs(BASE, exist_ok=True)


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


def _path(cid, project=None):
    """对话文件路径：按项目存子目录 history/<project>/<cid>.json（对标：对话记录保存在项目所在位置）。
    project 为空时：先查旧位置（history/<cid>.json 兼容迁移前的数据），找不到返回新位置。"""
    if project:
        pdir = os.path.join(BASE, _safe_dir(project))
        os.makedirs(pdir, exist_ok=True)
        return os.path.join(pdir, f"{cid}.json")
    # 旧位置（迁移前的平铺文件）
    return os.path.join(BASE, f"{cid}.json")


def _safe_dir(name):
    """项目 id → 安全目录名（防路径穿越）"""
    import re
    s = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', str(name or "default"))
    return s or "default"


def _find_file(cid, project=None):
    """查找对话文件：优先项目子目录，兼容旧平铺位置（迁移/兼容）"""
    if project:
        p = _path(cid, project)
        if os.path.isfile(p):
            return p
    # 旧位置
    old = _path(cid)
    if os.path.isfile(old):
        return old
    # 兜底：全目录搜索（项目不确定时）
    for root, dirs, files in os.walk(BASE):
        if f"{cid}.json" in files:
            return os.path.join(root, f"{cid}.json")
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
    排序：置顶优先，再按更新时间倒序；支持 q 全文搜索与分页"""
    out = []
    for root, dirs, files in os.walk(BASE):
        if os.path.basename(root) == "_preview_cache":
            continue
        for fn in files:
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, encoding="utf-8") as f:
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


def save_conversation(messages, cid=None, title=None, project=DEFAULT_PROJECT, provider="", model="", usage=None):
    """保存（或更新）一条对话；自动用第一条用户消息做标题；保留原置顶状态；完整保存用量。
    原子写入（临时文件 + rename）：防中断导致 JSON 损坏（历史数据丢失根因）。"""
    cid = cid or uuid.uuid4().hex[:12]
    existing = get_conversation(cid, project) or {}
    pin = bool(existing.get("pin", False))
    if messages:
        first_user = next(
            (m["content"] for m in messages
             if m.get("role") == "user" and isinstance(m.get("content"), str)),
            "",
        )
        title = (title or first_user or "新对话")[:30]
    final_path = _path(cid, project)
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    data = {"id": cid, "title": title, "updated": time.time(), "project": project,
            "provider": provider, "model": model, "pin": pin,
            "usage": usage or existing.get("usage") or {},
            "version": 2, "schema": "wenmo-chat-v2",
            "messages": messages}
    _atomic_write(final_path, data)
    return cid


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
    """读取对话：兼容项目子目录 + 旧平铺位置。
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
        return d
    except Exception:
        return None


def delete_conversation(cid):
    try:
        fp = _find_file(cid)
        if fp:
            os.remove(fp)
        return True
    except Exception:
        return False


# ---------------- 项目 ----------------

def list_projects():
    """返回 [{id, name, path, created}]，始终包含默认项目"""
    out = []
    try:
        with open(PROJECTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        out = data.get("projects", [])
    except Exception:
        pass
    if not any(p.get("id") == DEFAULT_PROJECT for p in out):
        out.insert(0, {"id": DEFAULT_PROJECT, "name": "默认项目", "path": "", "created": time.time()})
        _save_projects(out)
    return out


def _save_projects(projects):
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"projects": projects}, f, ensure_ascii=False, indent=2)


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
    projects = [p for p in projects if p.get("id") != pid]
    _save_projects(projects)
    # 删除该项目下的所有对话
    for conv in list_conversations(project=pid)[0]:
        delete_conversation(conv["id"])
    return True
