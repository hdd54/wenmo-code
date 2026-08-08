"""
工作区文件插件：读/写/列文件，默认根目录 = 项目根目录（agent-tutorial/）。
AI 与用户在项目内对话，创建的文件默认直接放在项目根目录；
用户有特殊需求（如指定子目录）时，按 path 参数写入对应位置。
"""
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agent-tutorial/
WORKSPACE = BASE        # 默认根目录 = 项目根目录
PROJECTS_FILE = os.path.join(BASE, "projects.json")
os.makedirs(WORKSPACE, exist_ok=True)
MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1GB 纯技术防爆


def _allowed_roots():
    roots = [
        os.path.abspath(WORKSPACE),   # 项目根目录（含 src/ docs/ plugins/ skills/ 等）
    ]
    try:
        with open(PROJECTS_FILE, encoding="utf-8") as f:
            for p in json.load(f).get("projects", []):
                if p.get("path"):
                    roots.append(os.path.abspath(p["path"]))
    except Exception:
        pass
    return roots


def _resolve(path):
    p = os.path.abspath(str(path))
    for r in _allowed_roots():
        if p == r or p.startswith(r + os.sep):
            return p
    raise PermissionError(
        f"路径不在允许的工作区内。允许：{WORKSPACE} 或项目配置的路径")


def read_file(args):
    path = args.get("path", "")
    try:
        p = _resolve(path)
        if not os.path.isfile(p):
            return f"错误：文件不存在 {p}"
        size = os.path.getsize(p)
        if size > MAX_FILE_SIZE:
            return f"错误：文件过大（{size} 字节，上限 1MB）"
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"错误：{e}"


def _check_perm(action):
    try:
        import gui_server
        perms = (gui_server.load_settings().get("permissions") or {})
        return perms.get(action, "allow")
    except Exception:
        return "allow" if action == "write_files" else "ask"


def write_file(args):
    perm = _check_perm("write_files")
    if perm == "deny":
        return "错误：文件写入权限被拒绝（可在 设置 → 通用 → 权限 中修改为「允许」）"
    if perm == "ask" and not args.get("_confirmed"):
        # 权限=询问：系统级弹窗确认（对标 opencode：写文件前询问用户）
        path_t = str(args.get("path", ""))
        return json.dumps({
            "error": "文件写入需要用户确认。",
            "need_confirmation": True,
            "safety": f"将写入文件：{path_t}（{len(str(args.get('content', '')))} 字符）。是否允许？",
        }, ensure_ascii=False)
    path = args.get("path", "")
    content = str(args.get("content", ""))
    # 办公格式必须用专门工具（手写文本 → 文件损坏/乱码）
    low = str(path).lower()
    if low.endswith((".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls")):
        return ("错误：.{ext} 是二进制办公格式，不能当作文本写入（会损坏/乱码）。"
                "PPT 请用 create_pptx 工具；其他办公格式可写 .md 或文本替代。").format(ext=low.rsplit(".", 1)[-1])
    try:
        p = _resolve(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            return "错误：内容超过 1MB 上限"
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        # 写入后验证（对标 opencode 文件变更观察）：重读文件确认字节/行数一致，防静默失败/截断
        verify = ""
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                read_back = f.read()
            ok = read_back == content
            size = os.path.getsize(p)
            lines = read_back.count("\n") + (1 if read_back and not read_back.endswith("\n") else 0)
            verify = "，已验证 %d 字节 / %d 行%s" % (size, lines, "（内容一致 ✓）" if ok else "（⚠ 内容不一致！）")
        except Exception as e:
            verify = "，验证失败：%s" % str(e)[:60]
        return "已写入 %s（%d 字符%s）" % (p, len(content), verify)
    except Exception as e:
        return f"错误：{e}"


def list_files(args):
    path = args.get("path", WORKSPACE)
    depth = max(0, min(int(args.get("depth", 2) or 2), 4))
    try:
        p = _resolve(path)
        if not os.path.isdir(p):
            return f"错误：目录不存在 {p}"
        out = []
        base_depth = p.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
            level = root.rstrip(os.sep).count(os.sep) - base_depth
            if level > depth:
                dirs[:] = []
                continue
            rel = os.path.relpath(root, p)
            prefix = "" if rel == "." else rel + os.sep
            for fn in sorted(files):
                if fn.startswith("."):
                    continue
                full = os.path.join(root, fn)
                out.append(f"{prefix}{fn} ({os.path.getsize(full)}B)")
        return "\n".join(out) if out else "（空目录）"
    except Exception as e:
        return f"错误：{e}"


def edit_file(args):
    """精确替换文件中的某段内容（对标 opencode edit）：只改片段，省 token"""
    perm = _check_perm("write_files")
    if perm == "deny":
        return "错误：文件写入权限被拒绝"
    if perm == "ask" and not args.get("_confirmed"):
        path_t = str(args.get("path", ""))
        return json.dumps({
            "error": "文件修改需要用户确认。",
            "need_confirmation": True,
            "safety": f"将修改文件：{path_t}。是否允许？",
        }, ensure_ascii=False)
    path = args.get("path", "")
    old = str(args.get("old", ""))
    new = str(args.get("new", ""))
    if not path or not old:
        return "错误：需要 path 和 old 参数"
    try:
        p = _resolve(path)
        if not os.path.isfile(p):
            return f"错误：文件不存在 {p}"
        with open(p, encoding="utf-8") as f:
            content = f.read()
        if content.count(old) == 0:
            return f"错误：old 内容在文件中未找到（请检查精确匹配）：{old[:60]}"
        if content.count(old) > 1:
            return f"错误：old 内容在文件中出现 {content.count(old)} 次，不唯一。请提供更多上下文让 old 唯一匹配。"
        new_content = content.replace(old, new, 1)
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_content)
        # 验证
        with open(p, encoding="utf-8", errors="replace") as f:
            verify = f.read() == new_content
        size = os.path.getsize(p)
        return ("已替换：%s（%d → %d 字节%s）\n【文件变更】%s +%d -%d\n%s" % (
            p, len(content), len(new_content), "，内容一致 ✓" if verify else "⚠ 验证异常",
            os.path.basename(p), new.count("\n") + 1, old.count("\n") + 1,
            "\n".join("+ " + l for l in new.split("\n"))))
    except Exception as e:
        return f"错误：{e}"


def search_in_directory(args):
    """目录级递归搜索（对标 opencode grep）"""
    path = str(args.get("path", "")).strip() or WORKSPACE
    pattern = str(args.get("pattern", "")).strip()
    max_results = max(1, min(int(args.get("max_results", 50) or 50), 200))
    if not pattern:
        return "错误：需要 pattern 参数"
    try:
        p = _resolve(path)
        if not os.path.isdir(p):
            p = os.path.dirname(p)
        rx = re.compile(pattern, re.IGNORECASE) if not _is_plain(pattern) else None
        plain = pattern.lower() if rx is None else None
        hits = []
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git", "files", "dist", "build"))
            for fn in sorted(files):
                if fn.startswith("."):
                    continue
                fp = os.path.join(root, fn)
                try:
                    with open(fp, encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if rx and rx.search(line):
                                hit = True
                            elif plain is not None and plain in line.lower():
                                hit = True
                            else:
                                hit = False
                            if hit:
                                rel = os.path.relpath(fp, WORKSPACE if fp.startswith(WORKSPACE) else p)
                                hits.append("%s:%d: %s" % (rel, i, line.strip()[:90]))
                                if len(hits) >= max_results:
                                    return "搜索「%s」命中 %d+ 处（已截断）：\n%s" % (pattern, len(hits), "\n".join(hits))
                except Exception:
                    continue
        return "搜索「%s」命中 %d 处：\n%s" % (pattern, len(hits), "\n".join(hits) if hits else "（未找到）")
    except Exception as e:
        return f"搜索失败：{e}"


def _is_plain(s):
    """是否纯文本关键词（无正则元字符）"""
    return not re.search(r"[.*+?^${}()|\[\]\\]", s)


def glob_files(args):
    """按文件名模式查找文件（对标 opencode glob）"""
    path = str(args.get("path", "")).strip() or WORKSPACE
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return "错误：需要 pattern 参数（如 *.py、**/*.ts）"
    try:
        p = _resolve(path)
        if not os.path.isdir(p):
            p = os.path.dirname(p)
        # 转 glob：** 递归、* 任意、? 单字符
        rx = re.compile("^" + re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/\\\\]*").replace(r"\?", ".") + "$")
        out = []
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git", "files"))
            for fn in sorted(files):
                if rx.match(fn):
                    rel = os.path.relpath(os.path.join(root, fn), p)
                    out.append(rel)
        return "匹配「%s」共 %d 个文件：\n%s" % (pattern, len(out), "\n".join(out) if out else "（未找到）")
    except Exception as e:
        return f"glob 失败：{e}"


PLUGIN_TOOLS = [
    {"name": "read_file", "description": "读取项目内的文件内容（默认根目录 = 项目根目录，也可指定子目录）",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string", "description": "文件路径"}},
                    "required": ["path"]}, "handler": read_file},
    {"name": "write_file", "description": "在项目内创建或覆盖写入文件（默认写入项目根目录，可指定子目录）。"
                                          "⚠️ 注意：本工具只写入工作区，不提供下载/预览链接。"
                                          "如果用户要求『下载/发送/预览文件』或『把内容给我』，必须改用 deliver 插件的 "
                                          "write_file_with_link 工具（写完后会给用户可点击的下载链接）。"
                                          "本工具用于项目内文件修改、代码落盘等场景。",
     "parameters": {"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "文件内容"}
                    }, "required": ["path", "content"]}, "handler": write_file},
    {"name": "list_files", "description": "列出项目内的文件（可指定目录和深度）",
     "parameters": {"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径，默认项目根目录"},
                        "depth": {"type": "integer", "description": "递归深度，默认 2"}
                    }}, "handler": list_files},
    {"name": "edit_file", "description": "精确替换文件中的某段内容（对标 opencode 的 edit：只改片段，不重写整个文件）。"
                                       "参数：path=文件路径；old=要替换的原文（必须精确匹配且唯一）；new=替换后的内容。"
                                       "适合修改大文件中的一小段（比全量 write_file 更省 token）。"
                                       "替换后自动验证并返回文件变更 diff。",
     "parameters": {"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "old": {"type": "string", "description": "要替换的原文（精确匹配，需唯一）"},
                        "new": {"type": "string", "description": "替换后的内容"}
                    }, "required": ["path", "old", "new"]}, "handler": edit_file},
    {"name": "search_in_directory", "description": "在目录下递归搜索关键词/正则（对标 opencode 的 grep），返回 文件:行号:内容。"
                                       "参数：path=目录（默认项目根目录）；pattern=关键词或正则；max_results=最多结果数（默认 50）。",
     "parameters": {"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径，默认项目根目录"},
                        "pattern": {"type": "string", "description": "关键词或正则表达式"},
                        "max_results": {"type": "integer", "description": "最多返回条数，默认 50"}
                    }, "required": ["pattern"]}, "handler": search_in_directory},
    {"name": "glob_files", "description": "按文件名模式查找文件（对标 opencode 的 glob）：如 *.py、**/*.test.ts、app.*。"
                                         "参数：path=起始目录（默认项目根目录）；pattern=文件名模式。",
     "parameters": {"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "起始目录，默认项目根目录"},
                        "pattern": {"type": "string", "description": "文件名模式，如 *.py 或 **/*.ts"}
                    }, "required": ["pattern"]}, "handler": glob_files},
]
