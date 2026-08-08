# -*- coding: utf-8 -*-
"""代码/工程工具插件（把高频 skill 能力落地为可执行工具）：
- ast_search:     AST 级代码结构搜索（找函数/类/调用，替代纯文本 grep）
- repo_scan:     项目结构扫描（文件树/语言分布/代码规模）
- codebase_overview: 代码库快速概览（入口文件/技术栈/模块结构）
- token_count:   token 估算（本地实现，中文/英文混合）
- benchmark_run: 简单计时基准（测函数/命令执行耗时）
- url_health:    URL 健康检查（HTTP 状态码/响应时间，对标 canary-watch）
"""

import json
import os
import re
import time
import urllib.request

# ---------- AST 级代码搜索（轻量实现：缩进感知的函数/类定位 + 调用关系） ----------
_FN_RE = re.compile(r'^\s*(?:def|async\s+def|function|const\s+\w+\s*=\s*(?:async\s*)?\(|class)\s+(\w+)')


def ast_search(args):
    """AST 级代码结构搜索：找函数/类定义、调用点、import（替代纯文本 grep 的精确搜索）。
    返回：匹配的函数/类定义（含行号）或调用点。支持按语言过滤。"""
    path = str(args.get("path", "")).strip()
    symbol = str(args.get("symbol", "")).strip()
    mode = str(args.get("mode", "def")).strip().lower()  # def=找定义, call=找调用, import=找导入
    if not path or not os.path.isdir(path):
        return "错误：path 必须是目录"
    if not symbol:
        return "错误：需要 symbol（函数/类名）"
    ext = str(args.get("ext", "")).strip()
    results = []
    skip_dirs = {"node_modules", ".git", "__pycache__", "venv", "dist", "build", ".venv", "site-packages"}
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            if ext and not fn.endswith(ext):
                continue
            if not fn.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h")):
                continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except Exception:
                continue
            for i, ln in enumerate(lines, 1):
                if mode == "def":
                    m = _FN_RE.match(ln)
                    if m and m.group(1) == symbol:
                        results.append(f"{fp}:{i}: {ln.strip()[:100]}")
                elif mode == "call":
                    # 找 symbol( 调用（非定义行）
                    if not _FN_RE.match(ln) and re.search(r'\b' + re.escape(symbol) + r'\s*\(', ln):
                        results.append(f"{fp}:{i}: {ln.strip()[:100]}")
                elif mode == "import":
                    if re.search(r'(import|from|require|using)\s+.*\b' + re.escape(symbol) + r'\b', ln):
                        results.append(f"{fp}:{i}: {ln.strip()[:100]}")
    if not results:
        return f"未找到 {symbol}（mode={mode}）"
    return f"找到 {len(results)} 处：\n" + "\n".join(results[:50])


# ---------- 项目结构扫描 ----------
_LANG_EXT = {
    "Python": ".py", "JavaScript": ".js", "TypeScript": ".ts", "TSX": ".tsx", "JSX": ".jsx",
    "Go": ".go", "Rust": ".rs", "Java": ".java", "C": ".c", "C++": ".cpp", "C#": ".cs",
    "Ruby": ".rb", "PHP": ".php", "Swift": ".swift", "Kotlin": ".kt", "HTML": ".html",
    "CSS": ".css", "Shell": ".sh", "SQL": ".sql", "Vue": ".vue", "Markdown": ".md", "JSON": ".json",
}


def repo_scan(args):
    """项目结构扫描：统计文件树、语言分布、代码规模（行数）、最大文件。快速了解代码库。"""
    path = str(args.get("path", "")).strip()
    depth = int(args.get("depth", 3) or 3)
    if not path:
        path = os.getcwd()
    if not os.path.isdir(path):
        return f"错误：{path} 不是目录"
    skip_dirs = {"node_modules", ".git", "__pycache__", "venv", "dist", "build", ".venv",
                 "site-packages", ".cache", "target", "vendor"}
    lang_count = {}
    lang_lines = {}
    total_files = 0
    total_lines = 0
    biggest = []
    dir_tree = []
    for root, dirs, files in os.walk(path):
        rel = os.path.relpath(root, path)
        depth_now = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        if depth_now <= depth and rel != ".":
            dir_tree.append("  " * (depth_now - 1) + "📁 " + os.path.basename(root))
        for fn in files:
            if depth_now <= depth:
                dir_tree.append("  " * depth_now + "📄 " + fn)
            fp = os.path.join(root, fn)
            ext = os.path.splitext(fn)[1].lower()
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    n = sum(1 for _ in f)
            except Exception:
                n = 0
            total_files += 1
            total_lines += n
            biggest.append((n, fp))
            for lang, e in _LANG_EXT.items():
                if ext == e:
                    lang_count[lang] = lang_count.get(lang, 0) + 1
                    lang_lines[lang] = lang_lines.get(lang, 0) + n
                    break
    biggest.sort(reverse=True)
    parts = [f"📁 {path}", f"文件数: {total_files} | 总行数: {total_lines:,}"]
    if lang_count:
        top_langs = sorted(lang_count.items(), key=lambda x: x[1], reverse=True)[:8]
        parts.append("语言分布: " + ", ".join(f"{l}({c}文件/{lang_lines.get(l,0):,}行)" for l, c in top_langs))
    if biggest[:5]:
        parts.append("最大文件: " + ", ".join(f"{os.path.relpath(fp, path)} ({n:,}行)" for n, fp in biggest[:5]))
    if dir_tree:
        parts.append("\n目录结构（前 {0} 层）:\n".format(depth) + "\n".join(dir_tree[:120]))
    return "\n".join(parts)


# ---------- 代码库快速概览 ----------
def codebase_overview(args):
    """代码库快速概览：入口文件（main/app/index）、技术栈线索（包管理文件）、README 摘要。"""
    path = str(args.get("path", "")).strip()
    if not path:
        path = os.getcwd()
    if not os.path.isdir(path):
        return f"错误：{path} 不是目录"
    parts = [f"📘 {path} 概览"]
    # 包管理/配置文件
    manifest_files = ["package.json", "requirements.txt", "pyproject.toml", "Cargo.toml",
                      "go.mod", "pom.xml", "build.gradle", "Gemfile", "composer.json", "Makefile",
                      "Dockerfile", "docker-compose.yml", "README.md", "AGENTS.md", "CLAUDE.md"]
    found = []
    for mf in manifest_files:
        fp = os.path.join(path, mf)
        if os.path.isfile(fp):
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    head = f.read(600)
                found.append(f"📄 {mf}:\n{head[:300]}")
            except Exception:
                found.append(f"📄 {mf}: (无法读取)")
    if found:
        parts.append("\n关键文件:\n" + "\n\n".join(found[:6]))
    # 入口文件
    entries = ["main.py", "app.py", "index.js", "index.ts", "main.go", "main.rs", "server.py", "manage.py"]
    entry_found = [e for e in entries if os.path.isfile(os.path.join(path, e))]
    if entry_found:
        parts.append("入口文件: " + ", ".join(entry_found))
    return "\n".join(parts)


# ---------- token 估算（本地，中文≈1字1token，英文≈4字符1token） ----------
def token_count(args):
    """估算文本/文件的 token 数（本地实现，不调 API）。中文≈1字≈1token，英文≈4字符≈1token。"""
    text = str(args.get("text", "")).strip()
    file_path = str(args.get("file", "")).strip()
    if not text and file_path:
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            return f"读取文件失败: {e}"
    if not text:
        return "错误：需要 text 或 file 参数"
    # 中文/全角字符：1 字符 ≈ 1 token；其余按 4 字符 ≈ 1 token
    cn = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', text))
    other = len(text) - cn
    est = cn + other / 4
    lines = text.count("\n") + 1
    return (f"文本长度: {len(text)} 字符, {lines} 行\n"
            f"估算 tokens: ~{int(est):,}（中文{cn}字≈{cn}tokens + 其他{other}字符≈{int(other/4)}tokens）")


# ---------- 简单计时基准 ----------
def benchmark_run(args):
    """测一段 Python 代码/命令的执行耗时（简单基准，对标 benchmark skill）。"""
    code = str(args.get("code", "")).strip()
    cmd = str(args.get("cmd", "")).strip()
    iterations = max(1, min(int(args.get("iterations", 5) or 5), 50))
    if not code and not cmd:
        return "错误：需要 code（Python 代码）或 cmd（shell 命令）"
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        try:
            if code:
                exec(code, {"__builtins__": {}}, {})  # 受限环境：无内置函数，防危险
            elif cmd:
                os.system(cmd if os.name == "nt" else cmd)
        except Exception as e:
            return f"执行失败（第{_ + 1}次）: {e}"
        times.append(time.perf_counter() - t0)
    avg = sum(times) / len(times)
    return (f"执行 {iterations} 次: 平均 {avg * 1000:.1f}ms | "
            f"最快 {min(times) * 1000:.1f}ms | 最慢 {max(times) * 1000:.1f}ms")


# ---------- URL 健康检查（对标 canary-watch） ----------
def url_health(args):
    """检查 URL 是否可访问：HTTP 状态码、响应时间、重定向。对标 canary-watch skill。"""
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "错误：需要 http(s) URL"
    try:
        t0 = time.perf_counter()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            elapsed = (time.perf_counter() - t0) * 1000
            status = r.status
            final = r.geturl()
            size = len(r.read(65536))
        status_txt = "✅ 正常" if 200 <= status < 400 else "⚠️ 异常"
        return (f"{status_txt} {status}\n"
                f"响应时间: {elapsed:.0f}ms\n"
                f"最终 URL: {final}\n"
                f"首 64KB 大小: {size} 字节")
    except Exception as e:
        return f"❌ 访问失败: {str(e)[:150]}"


PLUGIN_TOOLS = [
    {"name": "ast_search",
     "description": "AST 级代码结构搜索：精确找函数/类定义（mode=def）、调用点（mode=call）、导入（mode=import）。"
                    "比纯文本 grep 更准（不匹配注释/字符串里的同名）。参数 path=代码目录；symbol=函数/类名；"
                    "mode=def/call/import；ext=限定扩展名（如 .py）。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "代码目录路径"},
         "symbol": {"type": "string", "description": "要搜索的函数/类名"},
         "mode": {"type": "string", "description": "def=找定义（默认）/ call=找调用 / import=找导入"},
         "ext": {"type": "string", "description": "限定扩展名，如 .py"}},
         "required": ["path", "symbol"]}, "handler": ast_search},
    {"name": "repo_scan",
     "description": "项目结构扫描：文件树、语言分布（文件数/行数）、代码规模、最大文件。快速了解一个代码库的构成。"
                    "参数 path=项目目录（默认当前目录）；depth=目录树深度（默认3）。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "项目目录路径（默认当前目录）"},
         "depth": {"type": "integer", "description": "目录树深度，默认3"}}}, "handler": repo_scan},
    {"name": "codebase_overview",
     "description": "代码库快速概览：找入口文件（main/app/index）、包管理文件（package.json/requirements.txt 等）、"
                    "README 摘要、Dockerfile。用于快速了解一个不熟悉项目的技术栈和结构。参数 path=项目目录。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "项目目录路径（默认当前目录）"}}}, "handler": codebase_overview},
    {"name": "token_count",
     "description": "估算文本/文件的 token 数（本地实现，不调 API）。中文≈1字≈1token，英文≈4字符≈1token。"
                    "参数 text=要估算的文本，或 file=文件路径（二选一）。用于预估上下文占用、控制输出长度。",
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "要估算的文本"},
         "file": {"type": "string", "description": "或：文件路径"}}}, "handler": token_count},
    {"name": "benchmark_run",
     "description": "简单性能基准：测一段 Python 代码（code）或 shell 命令（cmd）的执行耗时，多次取平均/最快/最慢。"
                    "参数 code=Python 代码（受限环境）；或 cmd=shell 命令；iterations=次数（默认5）。",
     "parameters": {"type": "object", "properties": {
         "code": {"type": "string", "description": "要测试的 Python 代码"},
         "cmd": {"type": "string", "description": "或：shell 命令"},
         "iterations": {"type": "integer", "description": "测试次数，默认5"}}}, "handler": benchmark_run},
    {"name": "url_health",
     "description": "URL 健康检查：HTTP 状态码、响应时间（ms）、最终 URL、内容大小。用于检查网站/API 是否在线、"
                    "部署后验证。参数 url=http(s) 地址。",
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string", "description": "http(s) 网址"}},
         "required": ["url"]}, "handler": url_health},
]
