"""终端插件：让 AI 能执行电脑终端指令。
安全机制：执行任何命令前，必须先用 ask_user 弹窗向用户说明命令内容、
用途与可能的影响，获得用户确认后才能执行；危险命令直接拒绝。
"""

import subprocess
import re
import os

from execution_context import current_workspace
from permission_engine import evaluate_permission, load_runtime_policy
from sandbox_runner import SandboxUnavailable, discover_sandbox_backend, run_sandboxed
from tenant_state import load_json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.join(BASE_DIR, "workspace")

# 危险命令：直接拒绝执行（无论如何都太危险）
DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+[/~]", r"\brm\s+-rf\s+\*", r"\bformat\s+[a-z]:",
    r"\bshutdown\b", r"\breboot\b", r"\bdel\s+/[fqs]*\s+[a-z]:\\",
    r"\bdiskpart\b", r"\breg\s+delete\b", r":\(\)\s*\{", r"\bmkfs\b", r"\bdd\s+if=",
]
def _sandbox_settings():
    settings = load_json("settings.json", {})
    return settings.get("sandbox_mode", "required"), settings.get("sandbox_image", "wenmo-agent:locked")


def run_command_handler(arguments: dict) -> dict:
    """执行终端指令。必须先确认安全：模型应先用 ask_user 询问用户，
    获得用户明确同意后再调用本工具执行。权限可在设置里改为「自动允许/拒绝」。"""
    command = str(arguments.get("command", "")).strip()
    timeout = int(arguments.get("timeout", 30) or 30)
    if not command:
        return {"error": "命令不能为空"}
    decision = evaluate_permission(
        "plugin_terminal_run_command", arguments, load_runtime_policy())
    if decision.effect == "deny":
        return {"error": "终端命令被权限矩阵拒绝", "permission": decision.__dict__}
    # 1) 危险命令直接拒绝
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command, re.IGNORECASE):
            return {
                "error": f"命令被安全机制拒绝（危险操作）：{command}",
                "safety": "该命令可能造成不可逆的破坏（格式化/删除系统文件/关机等），本软件一律不允许执行。",
            }
    # 2) ask means every command requires confirmation in addition to OS isolation.
    if decision.effect == "ask" and not arguments.get("_confirmed"):
        return {
            "error": "该命令属于高风险操作，尚未获得用户确认。",
            "need_confirmation": True,
            "safety": (
                f"权限规则 {decision.rule or 'default'} 要求确认。"
                f"将按当前任务沙箱策略执行：{command}。是否允许？"),
        }
    # 3) 执行：默认要求真正的 OCI 或 WSL+bubblewrap 隔离；没有运行时时失败关闭。
    try:
        workspace = current_workspace.get() or WORKSPACE
        os.makedirs(workspace, exist_ok=True)
        sandbox_mode, sandbox_image = _sandbox_settings()
        runtime = discover_sandbox_backend()
        if sandbox_mode in ("required", "prefer") and runtime:
            proc = run_sandboxed(
                ["/bin/sh", "-lc", command], workspace, timeout=timeout,
                image=sandbox_image, runtime=runtime)
            sandboxed = True
        elif sandbox_mode in ("required", "prefer"):
            raise SandboxUnavailable(
                "任务级沙箱为 required，但未检测到可用的 Docker/Podman 或 WSL2 用户命名空间；命令未执行")
        else:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=max(5, min(timeout, 300)),
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            sandboxed = False
        out = (proc.stdout or "")[-20000:]
        err = (proc.stderr or "")[-10000:]
        return {
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
            "sandboxed": sandboxed,
        }
    except SandboxUnavailable as e:
        return {"error": str(e), "sandboxed": False, "fail_closed": True}
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
