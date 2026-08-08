# -*- coding: utf-8 -*-
"""文件操作插件：复制/移动/重命名/删除/列目录/另存为/存在检查/打包 exe（PyInstaller）。
对标终端文件操作：指定地址读取、修改、保存、复制、另存为等。"""

import os
import shutil
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES_DIR = os.path.join(BASE, "files")
os.makedirs(FILES_DIR, exist_ok=True)


def _resolve(path):
    """解析路径：files/ 相对 → FILES_DIR；项目相对 → BASE；其余原样"""
    s = str(path or "").strip()
    if not s:
        return ""
    p = os.path.abspath(s)
    if os.path.isfile(p) or os.path.isdir(p):
        return p
    alt = os.path.join(FILES_DIR, os.path.basename(s))
    if os.path.isfile(alt) or os.path.isdir(alt):
        return alt
    return p


def _allowed_roots():
    """允许操作的工作区根目录：项目目录 + files 下载区（对标沙箱：限制文件操作范围）"""
    roots = [BASE, FILES_DIR]
    try:
        import json as _json
        with open(os.path.join(BASE, "projects.json"), encoding="utf-8") as f:
            data = _json.load(f)
        for pr in data.get("projects", []):
            p = pr.get("path", "")
            if p and os.path.isdir(p):
                roots.append(os.path.abspath(p))
    except Exception:
        pass
    return roots


def _resolve_safe(path):
    """沙箱化路径解析：拒绝工作区之外的路径（防删除/移动任意文件）"""
    p = _resolve(path)
    if not p:
        return ""
    for r in _allowed_roots():
        try:
            if os.path.commonpath([p, r]) == os.path.abspath(r):
                return p
        except Exception:
            continue
    raise PermissionError("路径不在允许的工作区内（文件操作仅限项目目录与 files 下载区）")


def file_operation(args):
    """统一文件操作：action ∈ copy / move / rename / delete / save_as / list / exists"""
    action = str(args.get("action", "")).strip().lower()
    try:
        src = _resolve_safe(args.get("source") or args.get("src") or args.get("path") or "")
    except PermissionError as pe:
        return "错误：%s" % pe
    dst = str(args.get("target") or args.get("dest") or args.get("dst") or args.get("new_name") or "").strip()
    if not src:
        return "错误：需要 source 参数（源文件/目录路径）"
    try:
        if action in ("copy", "save_as"):
            if not dst:
                return "错误：copy/save_as 需要 target 参数（目标路径）"
            try:
                dst = _resolve_safe(dst)
            except PermissionError as pe:
                return "错误：%s" % pe
            dst = os.path.abspath(dst)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            return "已复制：%s → %s" % (src, dst)
        if action == "move":
            if not dst:
                return "错误：move 需要 target 参数"
            try:
                dst = _resolve_safe(dst)
            except PermissionError as pe:
                return "错误：%s" % pe
            dst = os.path.abspath(dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True) if os.path.dirname(dst) else None
            shutil.move(src, dst)
            return "已移动：%s → %s" % (src, dst)
        if action == "rename":
            if not dst:
                return "错误：rename 需要 target 参数（新名称/新路径）"
            try:
                dst = _resolve_safe(dst)
            except PermissionError as pe:
                return "错误：%s" % pe
            dst = os.path.abspath(dst)
            os.rename(src, dst)
            return "已重命名：%s → %s" % (src, dst)
        if action == "delete":
            if os.path.isdir(src):
                shutil.rmtree(src)
            elif os.path.isfile(src):
                os.remove(src)
            else:
                return "删除失败：路径不存在 %s" % src
            return "已删除：%s" % src
        if action in ("list", "ls", "dir"):
            if not os.path.isdir(src):
                # 尝试按文件所在目录
                src = os.path.dirname(src)
            entries = sorted(os.listdir(src))
            out = []
            for e in entries:
                full = os.path.join(src, e)
                tag = "📁" if os.path.isdir(full) else "📄"
                size = os.path.getsize(full) if os.path.isfile(full) else 0
                sz = ("%.1f KB" % (size / 1024)) if size < 1048576 else ("%.1f MB" % (size / 1048576))
                out.append("%s %s (%s)" % (tag, e, sz))
            return "目录 %s | %d 项:\n%s" % (src, len(entries), "\n".join(out) if out else "（空目录）")
        if action in ("exists", "exist"):
            return "存在：%s" % os.path.exists(src)
        if action == "info":
            if not os.path.exists(src):
                return "路径不存在：%s" % src
            if os.path.isfile(src):
                size = os.path.getsize(src)
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(src)))
                return "文件: %s\n大小: %d 字节 (%.1f KB)\n修改时间: %s" % (src, size, size / 1024, mtime)
            return "目录: %s" % src
        return "错误：未知 action=%s（支持 copy/move/rename/delete/save_as/list/exists/info）" % action
    except Exception as e:
        return "文件操作失败: %s" % str(e)[:200]


def package_exe(args):
    """把 Python 脚本打包为独立 exe（PyInstaller --onefile）。"""
    script = str(args.get("script", "")).strip()
    if not script:
        return "错误：需要 script 参数（.py 脚本路径）"
    sp = _resolve(script)
    if not os.path.isfile(sp) or not sp.lower().endswith(".py"):
        return "错误：脚本不存在或不是 .py 文件: %s" % sp
    name = str(args.get("name", "")).strip() or os.path.splitext(os.path.basename(sp))[0]
    # 输出到 files/ 下的 exe 目录
    out_dir = os.path.join(FILES_DIR, "exe_" + str(int(time.time())))
    os.makedirs(out_dir, exist_ok=True)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--onefile", "--name", name,
             "--distpath", out_dir, "--workpath", os.path.join(out_dir, "build"),
             "--specpath", out_dir, "--noconfirm", sp],
            capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"error": "打包超时（10 分钟）。脚本可能过大，或 PyInstaller 首次运行下载依赖较慢。"}
    except Exception as e:
        return {"error": "打包启动失败: %s" % str(e)[:200]}
    exe_path = os.path.join(out_dir, name + ".exe")
    if not os.path.isfile(exe_path):
        err = (r.stderr or b"").decode("utf-8", "ignore")
        return {"error": "打包失败（无产出 exe）:\n%s" % err[-500:]}
    # 复制到 files 根目录方便下载
    final_name = "%s_%d.exe" % (name, int(time.time()))
    final_path = os.path.join(FILES_DIR, final_name)
    shutil.copy2(exe_path, final_path)
    url = "http://127.0.0.1:8000/files/" + final_name
    return {"ok": True, "file": final_name, "url": url,
            "note": "已打包为 exe：%s（独立单文件，可分发到其他 Windows 电脑直接运行）。请在回答里引用链接。" % final_name}


PLUGIN_TOOLS = [
    {
        "name": "file_operation",
        "description": "文件/目录操作（对标终端命令）：action 可选\n"
                       "copy/save_as（复制/另存为：source → target）| move（移动）| rename（重命名）|\n"
                       "delete（删除）| list（列目录）| exists（判断存在）| info（大小/时间）。\n"
                       "用于：指定地址读取、修改、保存、复制、另存为等文件管理需求。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "操作类型：copy/move/rename/delete/save_as/list/exists/info"},
                "source": {"type": "string", "description": "源文件或目录路径（绝对路径 / files 目录文件名 / 项目相对路径）"},
                "target": {"type": "string", "description": "目标路径（copy/move/rename 需要）；save_as 时传新文件名"}
            },
            "required": ["action", "source"]
        },
        "handler": file_operation,
    },
    {
        "name": "package_exe",
        "description": "把 Python 脚本打包为独立 Windows exe（PyInstaller --onefile）。"
                       "当用户要求『打包成 exe/生成可执行程序/打包软件』时使用。"
                       "参数：script=要打包的 .py 脚本路径；name=程序名（可选，默认脚本名）。"
                       "打包较慢（1-5 分钟），完成后返回 exe 下载链接。",
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Python 脚本路径（.py）"},
                "name": {"type": "string", "description": "输出程序名（可选，默认用脚本名）"}
            },
            "required": ["script"]
        },
        "handler": package_exe,
    }
]
