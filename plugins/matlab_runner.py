# -*- coding: utf-8 -*-
"""MATLAB 集成插件：检测 MATLAB 安装位置、运行 .m 脚本并返回运行输出与生成的结果文件。
解决场景：用户有 MATLAB 代码（如 OCT 仿真脚本），需要直接运行验证结果、拿到图和数据。
实现：使用 matlab -batch 模式（R2019a+ 支持，本机 R2024a 可用），自动定位可执行文件，
     捕获 stdout/stderr，并自动收集脚本运行后生成的结果文件（png/fig/mat/csv 等）。
"""

import os
import shutil
import subprocess
import time
import glob

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.join(BASE, "workspace")

# 常见 MATLAB 安装路径（Windows）
COMMON_PATHS = [
    r"C:\Program Files\MATLAB",
    r"C:\Program Files\Polyspace",
    r"D:\Matlab2024a",
    r"D:\MATLAB",
    r"D:\Program Files\MATLAB",
    r"E:\MATLAB",
]


def _find_matlab():
    """定位 matlab 可执行文件：优先 PATH，其次常见安装路径"""
    exe = shutil.which("matlab")
    if exe:
        return exe
    for base in COMMON_PATHS:
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                if "matlab.exe" in files:
                    return os.path.join(root, "matlab.exe")
                # 只深入 bin 目录与版本目录，避免全盘深挖
                dirs[:] = [d for d in dirs if d.lower() in ("bin", "matlab") or d.lower().startswith("r20")]
    return ""


def _available():
    return bool(_find_matlab())


def matlab_available_handler(arguments: dict) -> dict:
    """检查 MATLAB 是否可用，返回可执行文件路径与版本"""
    exe = _find_matlab()
    if not exe:
        return {
            "available": False,
            "matlab_path": "",
            "hint": "未找到 MATLAB。可检查是否安装，或在设置中补充安装路径后重试。",
        }
    ver = "未知"
    try:
        proc = subprocess.run(
            [exe, "-batch", "disp(version)"],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip()]
        if lines:
            ver = lines[-1]
    except Exception:
        pass
    return {"available": True, "matlab_path": exe, "version": ver}


def run_matlab_script_handler(arguments: dict) -> dict:
    """运行 .m 脚本文件，返回运行输出与生成的结果文件列表"""
    script = str(arguments.get("script", "")).strip()
    timeout = int(arguments.get("timeout", 120) or 120)
    if not script:
        return {"error": "需要 script 参数（.m 脚本路径）"}
    exe = _find_matlab()
    if not exe:
        return {"error": "未找到 MATLAB 可执行文件，无法运行脚本",
                "hint": "请先安装 MATLAB 或检查安装路径"}
    # 解析脚本路径：相对路径 → workspace 下找；否则按原样
    if not os.path.isabs(script):
        cand = os.path.join(WORKSPACE, script)
        if os.path.isfile(cand):
            script = cand
    if not os.path.isfile(script):
        return {"error": f"脚本文件不存在: {script}"}
    script = os.path.abspath(script)
    # matlab -batch "run('C:/path/script.m')" —— 路径统一为正斜杠，避免反斜杠转义问题
    mcode = f"run('{script.replace(os.sep, '/')}'); disp('===RUN_OK===');"
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
        proc = subprocess.run(
            [exe, "-batch", mcode],
            cwd=os.path.dirname(script),
            capture_output=True, text=True,
            timeout=max(30, min(timeout, 600)),
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or "")[-30000:]
        err = (proc.stderr or "")[-15000:]
        ok = proc.returncode == 0
        # 收集脚本目录下最近 10 分钟内生成的结果文件
        results = []
        d = os.path.dirname(script)
        try:
            for ext in ("*.png", "*.fig", "*.mat", "*.csv", "*.jpg", "*.jpeg", "*.txt", "*.xlsx"):
                for f in sorted(glob.glob(os.path.join(d, ext))):
                    if time.time() - os.path.getmtime(f) < 600:
                        results.append(f)
        except Exception:
            pass
        return {
            "exit_code": proc.returncode,
            "ok": ok,
            "stdout": out,
            "stderr": err,
            "generated_files": results[:20],
            "note": "MATLAB 首次启动较慢（约30-60s），若超时可增大 timeout。",
        }
    except subprocess.TimeoutExpired:
        return {"error": f"MATLAB 执行超时（{timeout}s）", "note": "MATLAB 首次启动较慢，可尝试增大 timeout（最大 600s）"}
    except Exception as e:
        return {"error": f"MATLAB 执行失败: {e}"}


PLUGIN_TOOLS = [
    {
        "name": "matlab_available",
        "description": "检查本机 MATLAB 是否可用，返回可执行文件路径与版本号。"
                       "当用户提到『用 MATLAB 运行/仿真/验证』或需要执行 .m 脚本前，可先调用本工具确认环境。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "handler": matlab_available_handler,
    },
    {
        "name": "run_matlab_script",
        "description": "运行 MATLAB 脚本（.m 文件），返回运行输出（stdout/stderr）、退出码，"
                       "并自动收集脚本运行后生成的结果文件（png/fig/mat/csv 等，最近10分钟内）。"
                       "参数：script=脚本路径（相对 workspace 或绝对路径）；timeout=超时秒数（默认120，最大600）。"
                       "注意：MATLAB 首次启动较慢（约30-60s）；脚本内若有 figure，建议用 saveas/print 保存为图片。",
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": ".m 脚本路径（相对 workspace 或绝对路径）"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 120，最大 600"},
            },
            "required": ["script"],
        },
        "handler": run_matlab_script_handler,
    },
]
