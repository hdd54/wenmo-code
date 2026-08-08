"""Checkpoint 插件：AI 改动前的快照 + diff 审计 + 回滚（对标 t3code 的 checkpointing）。
不依赖 git（workspace 可能是非 git 目录）：用文件哈希快照 + 备份副本实现。
工具：
  plugin_checkpoint_snapshot   —— 创建快照（记录文件哈希 + 备份原文件）
  plugin_checkpoint_diff       —— 对比当前文件 vs 某快照（审计 AI 改了什么）
  plugin_checkpoint_revert     —— 回滚到某快照
  plugin_checkpoint_list       —— 列出所有快照
"""

import hashlib
import io
import json
import os
import shutil
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CKPT_DIR = os.path.join(BASE_DIR, ".checkpoints")
INDEX_FILE = os.path.join(CKPT_DIR, "index.json")
# 快照备份存储：.checkpoints/backups/<snap_id>/<相对路径>
# 快照清单：index.json = [{id, ts, note, files: [{path, hash, size}]}]

_ALLOWED_ROOTS = [BASE_DIR, os.path.join(BASE_DIR, "workspace")]


def _in_roots(p):
    ap = os.path.abspath(p)
    return any(ap == r or ap.startswith(r + os.sep) for r in _ALLOWED_ROOTS)


def _file_hash(path):
    """SHA-256 文件哈希"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _load_index():
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_index(idx):
    os.makedirs(CKPT_DIR, exist_ok=True)
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, INDEX_FILE)


def _walk_files(root):
    """收集目录下所有文件（相对路径 + 哈希 + 大小），跳过 checkpoints/虚拟目录"""
    out = []
    for cur, dirs, files in os.walk(root):
        # 跳过 .checkpoints 自身和 .git
        dirs[:] = [d for d in dirs if d not in (".checkpoints", ".git", "node_modules", "__pycache__", ".tmp")]
        for fn in files:
            if fn.endswith(".tmp"):
                continue
            fp = os.path.join(cur, fn)
            rel = os.path.relpath(fp, root)
            try:
                size = os.path.getsize(fp)
            except Exception:
                size = 0
            out.append({"path": rel.replace("\\", "/"), "hash": _file_hash(fp), "size": size})
    return out


def checkpoint_snapshot_handler(arguments: dict) -> dict:
    """创建快照：记录当前项目文件的哈希 + 备份文件副本。"""
    try:
        target = str(arguments.get("path") or arguments.get("repo") or "").strip() or \
                 os.path.join(BASE_DIR, "workspace")
        if not os.path.isdir(target):
            return {"error": f"目录不存在: {target}（请传绝对路径；不传默认 workspace/）"}
        if not _in_roots(target):
            return {"error": f"目录不在允许范围（仅限项目目录/workspace）: {target}"}
        note = str(arguments.get("note") or "").strip()[:100]
        files = _walk_files(target)
        if not files:
            return {"error": f"目录下没有可快照的文件: {target}"}
        snap_id = time.strftime("%Y%m%d_%H%M%S") + "_" + hashlib.md5(str(time.time()).encode()).hexdigest()[:4]
        # 备份文件副本（只备份文本类，避免大二进制）
        backup_dir = os.path.join(CKPT_DIR, "backups", snap_id)
        os.makedirs(backup_dir, exist_ok=True)
        copied = 0
        TEXT_EXT = {".py", ".js", ".ts", ".html", ".css", ".json", ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".csv", ".sh", ".bat", ".ps1"}
        for f in files:
            ext = os.path.splitext(f["path"])[1].lower()
            if ext not in TEXT_EXT or f.get("size", 0) > 500000:
                continue   # 只备份文本文件（可 diff/可 revert 的）
            src = os.path.join(target, f["path"].replace("/", os.sep))
            dst = os.path.join(backup_dir, f["path"].replace("/", os.sep))
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                f["backed"] = True
                copied += 1
            except Exception:
                f["backed"] = False
        idx = _load_index()
        idx.append({"id": snap_id, "ts": time.time(), "note": note,
                    "target": target, "file_count": len(files), "backed_count": copied,
                    "files": files})
        idx = idx[-50:]   # 最多 50 个快照
        _save_index(idx)
        return {"ok": True, "snapshot_id": snap_id, "files": len(files),
                "backed_files": copied,
                "note": "快照已创建。可用 plugin_checkpoint_diff 查看改动，"
                        "plugin_checkpoint_revert 回滚，plugin_checkpoint_list 查看所有快照。"}
    except Exception as e:
        return {"error": f"快照创建失败: {e}"}


def _find_snapshot(snap_id):
    idx = _load_index()
    for s in idx:
        if s["id"] == snap_id:
            return s
    return None


def checkpoint_list_handler(arguments: dict) -> dict:
    """列出所有快照。"""
    idx = _load_index()
    if not idx:
        return {"ok": True, "snapshots": [], "note": "还没有快照。创建：plugin_checkpoint_snapshot。"}
    snaps = [{"id": s["id"], "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime(s["ts"])),
              "note": s.get("note", ""), "files": s.get("file_count", 0),
              "backed": s.get("backed_count", 0)} for s in reversed(idx)]
    return {"ok": True, "snapshots": snaps,
            "note": "用 snapshot_id 配合 plugin_checkpoint_diff / plugin_checkpoint_revert。"}


def checkpoint_diff_handler(arguments: dict) -> dict:
    """对比当前文件 vs 某快照：显示改了什么（新增/修改/删除）。"""
    try:
        snap_id = str(arguments.get("snapshot_id") or "").strip()
        if not snap_id:
            return {"error": "缺少 snapshot_id（用 plugin_checkpoint_list 查看）"}
        snap = _find_snapshot(snap_id)
        if not snap:
            return {"error": f"快照不存在: {snap_id}"}
        target = snap.get("target") or os.path.join(BASE_DIR, "workspace")
        if not os.path.isdir(target):
            return {"error": f"快照目标目录已不存在: {target}"}
        # 当前文件哈希表
        cur = {f["path"]: f["hash"] for f in _walk_files(target)}
        old = {f["path"]: f["hash"] for f in snap.get("files", [])}
        added, modified, removed = [], [], []
        for path, h in old.items():
            if path not in cur:
                removed.append(path)
            elif cur[path] != h:
                modified.append(path)
        for path in cur:
            if path not in old:
                added.append(path)
        lines = [f"快照 {snap_id}（{time.strftime('%Y-%m-%d %H:%M', time.localtime(snap['ts']))}）"
                 f" vs 当前：新增 {len(added)}，修改 {len(modified)}，删除 {len(removed)}"]
        if added:
            lines.append("【新增】" + "\n".join("  + " + p for p in added[:30]))
        if modified:
            lines.append("【修改】" + "\n".join("  ~ " + p for p in modified[:30]))
        if removed:
            lines.append("【删除】" + "\n".join("  - " + p for p in removed[:30]))
        if not (added or modified or removed):
            lines.append("（无变化）")
        # 修改文件的旧内容预览（diff 细节，取自备份）
        detail = []
        backup_dir = os.path.join(CKPT_DIR, "backups", snap_id)
        for p in modified[:5]:
            bp = os.path.join(backup_dir, p.replace("/", os.sep))
            if os.path.isfile(bp):
                try:
                    with open(bp, encoding="utf-8", errors="replace") as f:
                        old_text = f.read()[:2000]
                    detail.append(f"=== {p} 快照时内容（前 2000 字） ===\n{old_text}")
                except Exception:
                    pass
        if detail:
            lines.append("\n\n" + "\n\n".join(detail))
        return {"ok": True, "added": len(added), "modified": len(modified),
                "removed": len(removed),
                "added_files": added[:30], "modified_files": modified[:30],
                "removed_files": removed[:30], "detail": "\n".join(lines)}
    except Exception as e:
        return {"error": f"diff 失败: {e}"}


def checkpoint_revert_handler(arguments: dict) -> dict:
    """回滚：用快照备份恢复被修改/删除的文本文件。"""
    try:
        snap_id = str(arguments.get("snapshot_id") or "").strip()
        if not snap_id:
            return {"error": "缺少 snapshot_id（用 plugin_checkpoint_list 查看）"}
        snap = _find_snapshot(snap_id)
        if not snap:
            return {"error": f"快照不存在: {snap_id}"}
        target = snap.get("target") or os.path.join(BASE_DIR, "workspace")
        if not os.path.isdir(target):
            return {"error": f"快照目标目录已不存在: {target}"}
        backup_dir = os.path.join(CKPT_DIR, "backups", snap_id)
        if not os.path.isdir(backup_dir):
            return {"error": f"快照 {snap_id} 没有备份文件（可能当时没有文本文件被备份）"}
        # 恢复所有备份文件
        restored = 0
        for cur, dirs, files in os.walk(backup_dir):
            for fn in files:
                bp = os.path.join(cur, fn)
                rel = os.path.relpath(bp, backup_dir).replace("\\", "/")
                dst = os.path.join(target, rel.replace("/", os.sep))
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(bp, dst)
                    restored += 1
                except Exception:
                    pass
        return {"ok": True, "restored_files": restored, "snapshot_id": snap_id,
                "note": f"已从快照 {snap_id} 恢复 {restored} 个文件。"
                        "新增但不在快照里的文件未删除（防止误删用户新文件）；如需彻底还原请手动删除。"}
    except Exception as e:
        return {"error": f"回滚失败: {e}"}


PLUGIN_TOOLS = [
    {
        "name": "checkpoint_snapshot",
        "description": "创建代码快照：记录当前项目/workspace 所有文件的内容哈希，并备份文本文件副本。"
                       "用途：AI 开始修改代码前先快照，之后可以审计改了什么、出问题时回滚。"
                       "参数：path=目录（可选，默认 workspace/，可传项目目录）；note=备注（可选）。"
                       "返回 snapshot_id 供后续 diff/revert 使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要快照的目录绝对路径（可选；默认 workspace/）"},
                "note": {"type": "string", "description": "快照备注（可选，如『修改前』『升级前』）"},
            },
            "required": [],
        },
        "handler": checkpoint_snapshot_handler,
    },
    {
        "name": "checkpoint_list",
        "description": "列出所有代码快照（id、时间、备注、文件数）。用 snapshot_id 做 diff/revert。",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": checkpoint_list_handler,
    },
    {
        "name": "checkpoint_diff",
        "description": "对比当前文件 vs 指定快照：显示新增/修改/删除的文件列表 + 被修改文件的旧内容。"
                       "用途：审计 AI 自快照以来改了什么。参数：snapshot_id=快照 ID（必填）。",
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "string", "description": "快照 ID（用 plugin_checkpoint_list 查看）"},
            },
            "required": ["snapshot_id"],
        },
        "handler": checkpoint_diff_handler,
    },
    {
        "name": "checkpoint_revert",
        "description": "回滚代码：把指定快照备份的文本文件恢复到目标目录。"
                       "用途：AI 改坏了代码，回到改动前的状态。参数：snapshot_id=快照 ID（必填）。"
                       "注意：快照之后新增的文件不会被删除（防止误删用户新文件）。",
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot_id": {"type": "string", "description": "快照 ID（用 plugin_checkpoint_list 查看）"},
            },
            "required": ["snapshot_id"],
        },
        "handler": checkpoint_revert_handler,
    },
]
