# -*- coding: utf-8 -*-
"""开发/工程工具插件（第三批 skill 能力落地）：
- env_check:        开发环境检测（Python/Node/Go/Rust/Git/Docker 版本）
- db_query:         轻量数据库查询（SQLite 只读；MySQL/PostgreSQL 若已装驱动）
- find_duplicate:   找重复文件（内容哈希，对标 data-throughput / repo 清理）
- git_status_tool:  Git 仓库状态速览（分支/改动/最近提交，封装 git）
- dir_size:         目录占用分析（找出大文件/大目录）
- which_command:    查找命令/可执行文件路径
"""

import hashlib
import os
import re
import shutil
import subprocess


def env_check(args):
    """开发环境检测：列出已安装的工具链及版本（Python/Node/Go/Rust/Git/Docker 等）。"""
    tools = ["python", "python3", "node", "npm", "go", "cargo", "rustc", "git", "docker",
             "gcc", "g++", "java", "pip", "curl", "ffmpeg", "ollama", "npx"]
    results = []
    for t in tools:
        try:
            r = subprocess.run([t, "--version"], capture_output=True, text=True, timeout=5)
            ver = (r.stdout or r.stderr or "").strip().split("\n")[0][:80]
            results.append(f"✅ {t}: {ver}")
        except Exception:
            results.append(f"❌ {t}: 未安装/不在 PATH")
    return "\n".join(results)


def db_query(args):
    """轻量数据库查询：SQLite 只读（安全）。MySQL/PostgreSQL 需已安装驱动且有连接串。
    参数：db=SQLite 文件路径 + sql=查询语句（只读，禁止写操作）。"""
    db_path = str(args.get("db", "")).strip()
    sql = str(args.get("sql", "")).strip()
    if not db_path or not os.path.isfile(db_path):
        return "错误：需要 db（SQLite 文件路径）"
    if not sql:
        return "错误：需要 sql 查询语句"
    sql_lower = sql.lower().strip()
    if not sql_lower.startswith(("select", "pragma", "explain")):
        return "安全限制：仅允许 SELECT/PRAGMA/EXPLAIN（只读查询）"
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return "（查询无结果）"
        cols = list(rows[0].keys()) if rows else []
        parts = [" | ".join(cols)]
        for r in rows[:30]:
            parts.append(" | ".join(str(r[c])[:40] for c in cols))
        if len(rows) > 30:
            parts.append(f"…（共 {len(rows)} 行，仅显示前 30）")
        return "\n".join(parts)
    except ImportError:
        return "Python 未内置 sqlite3（异常）"
    except Exception as e:
        return f"查询失败: {str(e)[:150]}"


def find_duplicate(args):
    """找目录中的重复文件（按内容 SHA-256，非仅文件名）。用于清理冗余。"""
    path = str(args.get("path", "")).strip()
    if not path:
        path = os.getcwd()
    if not os.path.isdir(path):
        return f"错误：{path} 不是目录"
    skip_dirs = {"node_modules", ".git", "__pycache__", "venv", "dist", "build", ".venv", "site-packages"}
    hashes = {}
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                if os.path.getsize(fp) > 50 * 1024 * 1024:
                    continue  # 跳过超大文件
                h = hashlib.sha256()
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                hashes.setdefault(h.hexdigest()[:16], []).append(fp)
                total += 1
            except Exception:
                continue
    dupes = {k: v for k, v in hashes.items() if len(v) > 1}
    if not dupes:
        return f"扫描 {total} 个文件，未发现重复"
    parts = [f"扫描 {total} 个文件，发现 {len(dupes)} 组重复:"]
    for k, v in dupes.items():
        parts.append(f"  重复组 ({len(v)}个):")
        for fp in v:
            parts.append(f"    - {os.path.relpath(fp, path)}")
    return "\n".join(parts[:80])


def git_status_tool(args):
    """Git 仓库状态速览：当前分支、工作区改动、最近提交。封装 git 命令。"""
    path = str(args.get("path", "")).strip()
    if not path:
        path = os.getcwd()
    if not os.path.isdir(os.path.join(path, ".git")):
        return f"错误：{path} 不是 Git 仓库"
    def git(*args2):
        try:
            r = subprocess.run(["git", "-C", path] + list(args2), capture_output=True, text=True, timeout=10)
            return (r.stdout or r.stderr).strip()
        except Exception as e:
            return f"(git 错误: {e})"
    parts = [f"📁 {path}"]
    branch = git("branch", "--show-current")
    parts.append(f"分支: {branch or '(detached)'}")
    status = git("status", "--porcelain")
    lines = status.split("\n") if status else []
    modified = [l for l in lines if l and l[:1] in "MARC"]
    untracked = [l for l in lines if l and l[:1] == "?"]
    if modified:
        parts.append(f"已修改/暂存: {len(modified)} 个文件")
        for l in modified[:15]:
            parts.append(f"  {l[:80]}")
    if untracked:
        parts.append(f"未跟踪: {len(untracked)} 个文件")
        for l in untracked[:10]:
            parts.append(f"  {l[:80]}")
    if not modified and not untracked:
        parts.append("工作区干净 ✅")
    log = git("log", "--oneline", "-5")
    if log:
        parts.append("最近提交:")
        for l in log.split("\n"):
            parts.append(f"  {l[:80]}")
    return "\n".join(parts)


def dir_size(args):
    """目录占用分析：找出最大的子目录/文件（磁盘空间视角）。"""
    path = str(args.get("path", "")).strip()
    top_n = max(1, min(int(args.get("top_n", 10) or 10), 30))
    if not path:
        path = os.getcwd()
    if not os.path.isdir(path):
        return f"错误：{path} 不是目录"
    skip_dirs = {"node_modules", ".git", "__pycache__", "venv", ".venv", "site-packages", "target", "dist", "build"}
    items = []
    total = 0
    for entry in os.scandir(path):
        try:
            if entry.is_dir():
                if entry.name in skip_dirs:
                    continue
                size = 0
                for root, dirs, files in os.walk(entry.path):
                    dirs[:] = [d for d in dirs if d not in skip_dirs]
                    for fn in files:
                        try:
                            size += os.path.getsize(os.path.join(root, fn))
                        except Exception:
                            pass
                items.append((size, "📁 " + entry.name))
            else:
                size = os.path.getsize(entry.path)
                items.append((size, "📄 " + entry.name))
            total += size
        except Exception:
            continue
    items.sort(reverse=True)
    parts = [f"📁 {path} 总占用: {total/1024/1024:.1f} MB"]
    for size, name in items[:top_n]:
        parts.append(f"  {size/1024/1024:8.1f} MB  {name}")
    return "\n".join(parts)


def which_command(args):
    """查找命令/可执行文件在系统 PATH 中的位置（对标 shell which/where）。"""
    cmd = str(args.get("command", "")).strip()
    if not cmd:
        return "错误：需要 command（命令名）"
    found = shutil.which(cmd)
    if found:
        return f"✅ {cmd} → {found}"
    # Windows where 兜底
    try:
        r = subprocess.run(["where", cmd], capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            return f"✅ {cmd}:\n" + "\n".join(r.stdout.strip().split("\n")[:5])
    except Exception:
        pass
    return f"❌ 未找到 {cmd}（不在 PATH 中）"


PLUGIN_TOOLS = [
    {"name": "env_check",
     "description": "开发环境检测：列出已安装工具链及版本（Python/Node/Go/Rust/Git/Docker/GCC/FFmpeg/Ollama 等）。"
                    "用于确认环境就绪、排查缺依赖。",
     "parameters": {"type": "object", "properties": {}}, "handler": env_check},
    {"name": "db_query",
     "description": "SQLite 数据库只读查询（安全：仅允许 SELECT/PRAGMA）。参数 db=数据库文件路径；sql=查询语句。"
                    "用于快速查看数据、验证数据。",
     "parameters": {"type": "object", "properties": {
         "db": {"type": "string", "description": "SQLite 数据库文件路径"},
         "sql": {"type": "string", "description": "只读 SQL 查询（SELECT/PRAGMA）"}},
         "required": ["db", "sql"]}, "handler": db_query},
    {"name": "find_duplicate",
     "description": "按内容哈希找目录中的重复文件（非仅文件名）。用于清理冗余、释放空间。参数 path=目录。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "要扫描的目录（默认当前目录）"}}}, "handler": find_duplicate},
    {"name": "git_status_tool",
     "description": "Git 仓库状态速览：当前分支、已修改/未跟踪文件、最近 5 条提交。参数 path=仓库目录（默认当前目录）。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Git 仓库路径（默认当前目录）"}}}, "handler": git_status_tool},
    {"name": "dir_size",
     "description": "目录占用分析：列出最大的子目录/文件（MB）。用于找大文件、清理空间。参数 path=目录；top_n=展示数量。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "要分析的目录（默认当前目录）"},
         "top_n": {"type": "integer", "description": "展示数量，默认10"}}}, "handler": dir_size},
    {"name": "which_command",
     "description": "查找命令/可执行文件的完整路径（封装 which/where）。参数 command=命令名。",
     "parameters": {"type": "object", "properties": {
         "command": {"type": "string", "description": "命令名（如 python、node、git）"}},
         "required": ["command"]}, "handler": which_command},
]
