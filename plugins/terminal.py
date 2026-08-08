"""终端插件：让 AI 能执行电脑终端指令。
安全机制：执行任何命令前，必须先用 ask_user 弹窗向用户说明命令内容、
用途与可能的影响，获得用户确认后才能执行；危险命令直接拒绝。
"""

import subprocess
import re
import os
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.join(BASE_DIR, "workspace")

# 危险命令：直接拒绝执行（无论如何都太危险）
DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+[/~]", r"\brm\s+-rf\s+\*", r"\bformat\s+[a-z]:",
    r"\bshutdown\b", r"\breboot\b", r"\bdel\s+/[fqs]*\s+[a-z]:\\",
    r"\bdiskpart\b", r"\breg\s+delete\b", r":\(\)\s*\{", r"\bmkfs\b", r"\bdd\s+if=",
]
# 高风险命令：必须强制用户确认
HIGH_RISK_PATTERNS = [
    r"\brm\b", r"\bdel\b", r"\bremove-item\b", r"\bgit\s+push\s+--force", r"\bgit\s+reset\s+--hard",
    r"\bgit\s+clean\s+-f", r"\bpip\s+uninstall\b", r"\bpip\s+install\b", r"\bnpm\s+install\b",
    r"\bchmod\b", r"\bmove\b", r"\bren\b", r"\bpython\s+[^\s]+\s+-c\b", r"\bcurl\b", r"\bInvoke-WebRequest\b",
]

_approved = {}   # 已确认的命令（hash -> 过期时间），同命令 5 分钟内免重复确认
_APPROVE_TTL = 300


def _check_perm(action):
    try:
        import gui_server
        perms = (gui_server.load_settings().get("permissions") or {})
        return perms.get(action, "allow" if action == "write_files" else "ask")
    except Exception:
        return "allow" if action == "write_files" else "ask"


def run_command_handler(arguments: dict) -> dict:
    """执行终端指令。必须先确认安全：模型应先用 ask_user 询问用户，
    获得用户明确同意后再调用本工具执行。权限可在设置里改为「自动允许/拒绝」。"""
    command = str(arguments.get("command", "")).strip()
    timeout = int(arguments.get("timeout", 30) or 30)
    if not command:
        return {"error": "命令不能为空"}
    perm = _check_perm("run_command")
    if perm == "deny":
        return {"error": "终端命令权限被拒绝（可在 设置 → 通用 → 权限 中修改）"}
    # 1) 危险命令直接拒绝
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command, re.IGNORECASE):
            return {
                "error": f"命令被安全机制拒绝（危险操作）：{command}",
                "safety": "该命令可能造成不可逆的破坏（格式化/删除系统文件/关机等），本软件一律不允许执行。",
            }
    # 统一初始化，避免分支未定义变量（修复 UnboundLocalError: now/h）
    h = hash(command)
    now = time.time()
    # 2) 权限=自动允许：跳过确认直接执行（高危命令仍被危险黑名单拦截）
    if perm == "allow":
        approved = True
    else:
        # 检查用户是否已确认（5 分钟内同命令免重复确认）
        if h in _approved and _approved[h] > now:
            approved = True
        else:
            approved = False
    # 3) 高风险命令未确认 → 要求先 ask_user（除非已通过系统级权限确认 _confirmed）
    is_high_risk = any(re.search(p, command, re.IGNORECASE) for p in HIGH_RISK_PATTERNS)
    if not approved and is_high_risk and not arguments.get("_confirmed"):
        return {
            "error": "该命令属于高风险操作，尚未获得用户确认。",
            "need_confirmation": True,
            "safety": "请先调用 ask_user 工具，向用户说明：要执行的命令、用途、可能的影响"
                      "（如删除文件、修改系统、联网下载、安装软件等），并请用户明确回答是否执行。"
                      "用户确认后再次调用本工具执行。",
        }
    # 4) 执行
    try:
        os.makedirs(WORKSPACE, exist_ok=True)
        proc = subprocess.run(
            command,
            shell=True,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=max(5, min(timeout, 300)),
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or "")[-20000:]
        err = (proc.stderr or "")[-10000:]
        _approved[h] = now + _APPROVE_TTL   # 执行成功记入已确认
        return {
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"命令执行超时（{timeout}s）"}
    except Exception as e:
        return {"error": f"命令执行失败: {e}"}


PLUGIN_TOOLS = [
    {
        "name": "run_command",
        "description": "执行电脑终端指令（Shell）。安全要求：执行前必须先用 ask_user 弹窗向用户说明"
                       "命令内容、用途与可能的影响（删除文件/修改系统/联网下载等），用户明确同意后才能执行。"
                       "危险命令（格式化、删除系统文件、关机等）会被安全机制直接拒绝。"
                       "参数：command=要执行的命令；timeout=超时秒数（默认30，最大300）。"
                       "工作目录为 workspace/。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的终端命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30，最大 300"},
            },
            "required": ["command"],
        },
        "handler": run_command_handler,
    }
]
