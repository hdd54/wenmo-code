# -*- coding: utf-8 -*-
"""代码符号工具（对标 opencode 的 LSP 集成，轻量实现：正则提取 + 全局引用搜索）：
- list：列出文件的符号大纲（函数/类/方法定义）
- def：定位符号的定义（文件+行号）
- ref：搜索符号的所有引用位置（排除定义行）
支持常见语言：Python / JS / TS / Java / Kotlin / C / C++ / Go / Rust。"""

import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (扩展名匹配, 定义正则, 语言名, 符号组索引)
_DEF_PATTERNS = [
    (r"\.(py|pyi)$", r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)", "Python", 1),
    (r"\.(js|jsx|ts|tsx|mjs|cjs)$",
     r"^(?:export\s+default\s+)?(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$]\w*)|"
     r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$]\w*)\s*[=:]|"
     r"^(?:export\s+)?class\s+([A-Za-z_$]\w*)",
     "JS/TS", 1),
    (r"\.(java|kt|kts)$",
     r"\b(?:public|private|protected|internal)\s+(?:static\s+)?(?:final\s+)?(?:fun\s+|[\w<>\[\],?.\s]+)\s*([A-Za-z_]\w*)\s*(?:\(|\b)",
     "Java/Kotlin", 1),
    (r"\.(c|cpp|h|hpp|cc|hxx)$",
     r"^\s*[\w:*<>,\s]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?",
     "C/C++", 1),
    (r"\.go$", r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", "Go", 1),
    (r"\.(rs)$", r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait|impl)\s+([A-Za-z_]\w*)", "Rust", 1),
]


def _match_def(ext, line):
    for pat, regex, lang, group in _DEF_PATTERNS:
        if re.search(pat, ext):
            m = re.search(regex, line)
            if m:
                sym = m.group(group) or next((g for g in m.groups() if g), None)
                if sym:
                    return sym, lang
            return None, None
    return None, None


def _walk_files(path):
    if os.path.isfile(path):
        yield path
        return
    if os.path.isdir(path):
        skip = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv", "files", ".agents", ".opencode"}
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in skip]
            for fn in files:
                yield os.path.join(root, fn)


def _read_lines(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except Exception:
        return []


def code_symbols(args):
    action = str(args.get("action", "list")).strip().lower()
    path = str(args.get("path", "")).strip()
    symbol = str(args.get("symbol", "")).strip()
    if not path:
        return "错误：需要 path 参数（文件或目录路径）"
    p = os.path.abspath(path)
    if not os.path.exists(p):
        return "错误：路径不存在 %s" % p
    try:
        if action in ("list", "outline"):
            if not os.path.isfile(p):
                return "错误：list 需要指向单个文件"
            lines = _read_lines(p)
            ext = os.path.splitext(p)[1].lower()
            out = []
            for i, line in enumerate(lines, 1):
                sym, lang = _match_def(ext, line)
                if sym:
                    out.append("L%d  %s  (%s)" % (i, sym, lang))
            return "符号大纲 %s（%d 个）：\n%s" % (os.path.basename(p), len(out), "\n".join(out) or "(未识别到符号)")
        if action in ("def", "definition"):
            if not symbol:
                return "错误：def 需要 symbol 参数（符号名）"
            hits = []
            for fp in _walk_files(p):
                ext = os.path.splitext(fp)[1].lower()
                for i, line in enumerate(_read_lines(fp), 1):
                    sym, lang = _match_def(ext, line)
                    if sym == symbol:
                        hits.append("%s:%d  %s" % (os.path.relpath(fp, p if os.path.isdir(p) else os.path.dirname(p)), i, line.strip()[:80]))
            return "「%s」的定义（%d 处）：\n%s" % (symbol, len(hits), "\n".join(hits) or "(未找到定义)")
        if action in ("ref", "references"):
            if not symbol:
                return "错误：ref 需要 symbol 参数（符号名）"
            hits = []
            for fp in _walk_files(p):
                ext = os.path.splitext(fp)[1].lower()
                rel = os.path.relpath(fp, p if os.path.isdir(p) else os.path.dirname(p))
                for i, line in enumerate(_read_lines(fp), 1):
                    if symbol in line:
                        sym, _ = _match_def(ext, line)
                        if sym != symbol:   # 排除定义行
                            hits.append("%s:%d  %s" % (rel, i, line.strip()[:80]))
            return "「%s」的引用（%d 处）：\n%s" % (symbol, len(hits), "\n".join(hits) or "(未找到引用)")
        return "错误：未知 action=%s（支持 list/def/ref）" % action
    except Exception as e:
        return "符号搜索失败: %s" % str(e)[:200]


PLUGIN_TOOLS = [
    {
        "name": "code_symbols",
        "description": "代码符号分析（对标 opencode 的 LSP 能力，轻量版）：\n"
                       "list（列出文件的函数/类/方法大纲）| def（定位符号定义位置，需 symbol）|\n"
                       "ref（搜索符号的所有引用，需 symbol，排除定义行）。\n"
                       "支持 Python/JS/TS/Java/Kotlin/C/C++/Go/Rust。\n"
                       "用于：快速定位代码中的函数/类定义、查看符号被哪些地方引用。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "操作：list/def/ref"},
                "path": {"type": "string", "description": "文件或目录路径（list 需要文件；def/ref 可目录）"},
                "symbol": {"type": "string", "description": "符号名（def/ref 时需要）"}
            },
            "required": ["action", "path"]
        },
        "handler": code_symbols,
    }
]
