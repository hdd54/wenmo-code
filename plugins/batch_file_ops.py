# -*- coding: utf-8 -*-
"""批量文件操作插件：批量重命名 / 批量移动 / 批量内容替换 / 批量列出。
场景：用户有一批数据文件（如 OCT 测量数据、实验记录），需要统一整理。
安全：仅允许在软件工作区、files 下载区与项目目录内操作（复用 file_ops 沙箱思路），
     批量移动/重命名会逐个检查目标冲突，不覆盖已有文件。
"""

import os
import re
import shutil
import fnmatch
import json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES_DIR = os.path.join(BASE, "files")


def _allowed_roots():
    """允许操作的工作区根目录：软件根 + files 下载区 + 各项目目录"""
    roots = [BASE, FILES_DIR]
    try:
        with open(os.path.join(BASE, "projects.json"), encoding="utf-8") as f:
            data = json.load(f)
        for pr in data.get("projects", []):
            p = pr.get("path", "")
            if p and os.path.isdir(p):
                roots.append(os.path.abspath(p))
    except Exception:
        pass
    return roots


def _in_roots(p):
    p = os.path.abspath(p)
    for r in _allowed_roots():
        try:
            if os.path.commonpath([p, r]) == os.path.abspath(r):
                return True
        except Exception:
            continue
    return False


def _safe_dir(path, param_name):
    if not os.path.isdir(path):
        raise PermissionError(f"{param_name} 不是有效目录: {path}")
    if not _in_roots(path):
        raise PermissionError(f"{param_name} 不在允许的工作区内（仅限项目目录、workspace 与 files 下载区）: {path}")


def batch_list_handler(args):
    """列出目录下的文件（可过滤扩展名/模式）"""
    directory = str(args.get("directory", "")).strip()
    ext = str(args.get("ext", "") or args.get("extension", "")).strip().lower()
    pattern = str(args.get("pattern", "") or args.get("glob", "*")).strip() or "*"
    try:
        if not directory:
            directory = FILES_DIR
        _safe_dir(directory, "directory")
        names = sorted(os.listdir(directory))
        files = [n for n in names if os.path.isfile(os.path.join(directory, n))]
        if ext:
            if not ext.startswith("."):
                ext = "." + ext
            files = [n for n in files if n.lower().endswith(ext)]
        if pattern and pattern != "*":
            rx = re.compile(pattern) if args.get("regex") else None
            if rx:
                files = [n for n in files if rx.search(n)]
            else:
                files = [n for n in files if fnmatch.fnmatch(n, pattern)]
        return {"directory": directory, "count": len(files), "files": files}
    except PermissionError as pe:
        return {"error": str(pe)}
    except Exception as e:
        return {"error": f"列目录失败: {e}"}


def batch_rename_handler(args):
    """批量重命名：mode ∈ prefix / suffix / replace / regex / case"""
    directory = str(args.get("directory", "")).strip()
    mode = str(args.get("mode", "replace")).strip().lower()
    find = str(args.get("find", "") or args.get("old", ""))
    repl = str(args.get("replace", "") or args.get("new", ""))
    ext = str(args.get("ext", "") or args.get("extension", "")).strip().lower()
    if not directory:
        return {"error": "需要 directory 参数"}
    try:
        _safe_dir(directory, "directory")
        names = sorted(os.listdir(directory))
        files = [n for n in names if os.path.isfile(os.path.join(directory, n))]
        if ext:
            if not ext.startswith("."):
                ext = "." + ext
            files = [n for n in files if n.lower().endswith(ext)]
        if not files:
            return {"directory": directory, "renamed": 0, "changes": [], "note": "没有匹配的文件"}
        changed = []
        for name in files:
            stem, dot = os.path.splitext(name)
            new_stem = stem
            if mode == "prefix":
                new_stem = find + stem
            elif mode == "suffix":
                new_stem = stem + find
            elif mode == "replace":
                if not find:
                    return {"error": "replace 模式需要 find 参数（要被替换的文本）"}
                new_stem = stem.replace(find, repl)
            elif mode == "regex":
                if not find:
                    return {"error": "regex 模式需要 find 参数（正则表达式）"}
                try:
                    new_stem = re.sub(find, repl, stem)
                except re.error as re_err:
                    return {"error": f"正则表达式错误: {re_err}"}
            elif mode == "case":
                new_stem = stem.upper() if repl == "upper" else stem.lower()
            else:
                return {"error": f"未知模式: {mode}（支持 prefix / suffix / replace / regex / case）"}
            new_name = new_stem + dot
            if new_name != name:
                new_path = os.path.join(directory, new_name)
                if os.path.exists(new_path):
                    return {"error": f"目标已存在，已停止（避免覆盖）: {new_name}"}
                os.rename(os.path.join(directory, name), new_path)
                changed.append((name, new_name))
        return {"directory": directory, "mode": mode, "renamed": len(changed), "changes": changed}
    except PermissionError as pe:
        return {"error": str(pe)}
    except Exception as e:
        return {"error": f"批量重命名失败: {e}"}


def batch_move_handler(args):
    """按扩展名/模式批量移动文件到目标目录"""
    directory = str(args.get("directory", "")).strip()
    target = str(args.get("target", "")).strip()
    ext = str(args.get("ext", "") or args.get("extension", "")).strip().lower()
    pattern = str(args.get("pattern", "") or args.get("glob", "*")).strip()
    if not directory or not target:
        return {"error": "需要 directory 与 target 参数"}
    try:
        _safe_dir(directory, "directory")
        if not os.path.exists(target):
            os.makedirs(target, exist_ok=True)
        _safe_dir(target, "target")
        names = sorted(os.listdir(directory))
        files = [n for n in names if os.path.isfile(os.path.join(directory, n))]
        if ext:
            if not ext.startswith("."):
                ext = "." + ext
            files = [n for n in files if n.lower().endswith(ext)]
        if pattern and pattern != "*":
            files = [n for n in files if fnmatch.fnmatch(n, pattern)]
        if not files:
            return {"directory": directory, "target": target, "moved": 0, "files": [], "note": "没有匹配的文件"}
        moved = []
        for name in files:
            src = os.path.join(directory, name)
            dst = os.path.join(target, name)
            if os.path.exists(dst):
                return {"error": f"目标已存在，已停止（避免覆盖）: {name}"}
            shutil.move(src, dst)
            moved.append(name)
        return {"directory": directory, "target": target, "moved": len(moved), "files": moved}
    except PermissionError as pe:
        return {"error": str(pe)}
    except Exception as e:
        return {"error": f"批量移动失败: {e}"}


def batch_replace_handler(args):
    """批量替换文件内容（文本类文件，UTF-8）"""
    directory = str(args.get("directory", "")).strip()
    find = str(args.get("find", "") or args.get("old", ""))
    repl = str(args.get("replace", "") or args.get("new", ""))
    ext = str(args.get("ext", "") or args.get("extension", "txt")).strip().lower()
    if not directory or not find:
        return {"error": "需要 directory 与 find 参数"}
    try:
        _safe_dir(directory, "directory")
        names = sorted(os.listdir(directory))
        if ext:
            if not ext.startswith("."):
                ext = "." + ext
            files = [n for n in names if os.path.isfile(os.path.join(directory, n)) and n.lower().endswith(ext)]
        else:
            files = [n for n in names if os.path.isfile(os.path.join(directory, n))]
        if not files:
            return {"directory": directory, "replaced_files": 0, "files": [], "note": "没有匹配的文件"}
        replaced = []
        for name in files:
            path = os.path.join(directory, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
            if find in content:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content.replace(find, repl))
                replaced.append(name)
        return {"directory": directory, "replaced_files": len(replaced), "files": replaced}
    except PermissionError as pe:
        return {"error": str(pe)}
    except Exception as e:
        return {"error": f"批量替换失败: {e}"}


PLUGIN_TOOLS = [
    {
        "name": "batch_list",
        "description": "列出目录下的文件清单（支持扩展名过滤与通配符/正则匹配）。"
                       "参数：directory=目录路径；ext=扩展名过滤（如 csv）；pattern=通配符（如 *.dat）或正则（regex=true 时）。",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "目录路径"},
                "ext": {"type": "string", "description": "扩展名过滤（可选，如 csv/dat）"},
                "pattern": {"type": "string", "description": "通配符或正则模式（可选）"},
                "regex": {"type": "boolean", "description": "pattern 是否为正则（默认 false）"},
            },
            "required": ["directory"],
        },
        "handler": batch_list_handler,
    },
    {
        "name": "batch_rename",
        "description": "批量重命名文件。mode：prefix=加前缀（find 为前缀文本）；suffix=加后缀；"
                       "replace=替换文件名中的文本（find=旧文本, replace=新文本）；regex=正则替换；case=大小写（replace=upper/lower）。"
                       "可选 ext 限制只处理某扩展名。逐个检查目标冲突，不覆盖已有文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "目录路径"},
                "mode": {"type": "string", "description": "prefix/suffix/replace/regex/case，默认 replace"},
                "find": {"type": "string", "description": "被替换的文本（replace/regex/prefix/suffix 用）"},
                "replace": {"type": "string", "description": "新文本（replace/regex/case 用）"},
                "ext": {"type": "string", "description": "扩展名过滤（可选）"},
            },
            "required": ["directory"],
        },
        "handler": batch_rename_handler,
    },
    {
        "name": "batch_move",
        "description": "批量移动文件到目标目录（按扩展名 ext 或通配符 pattern 过滤）。"
                       "目标目录不存在会自动创建；逐个检查冲突，不覆盖已有文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "源目录路径"},
                "target": {"type": "string", "description": "目标目录路径"},
                "ext": {"type": "string", "description": "扩展名过滤（可选）"},
                "pattern": {"type": "string", "description": "通配符过滤（可选，如 *.dat）"},
            },
            "required": ["directory", "target"],
        },
        "handler": batch_move_handler,
    },
    {
        "name": "batch_replace",
        "description": "批量替换文本文件内容（UTF-8，默认只处理 txt，可用 ext 指定其他扩展名）。"
                       "参数：directory=目录；find=要查找的文本；replace=替换为；ext=扩展名（默认 txt）。",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "目录路径"},
                "find": {"type": "string", "description": "要查找的文本"},
                "replace": {"type": "string", "description": "替换为的文本"},
                "ext": {"type": "string", "description": "扩展名（默认 txt）"},
            },
            "required": ["directory", "find"],
        },
        "handler": batch_replace_handler,
    },
]
