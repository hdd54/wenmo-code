# -*- coding: utf-8 -*-
"""Git 集成插件（对标 opencode）：status / diff / commit / branch / log。
用 git 命令封装；cwd 可指定项目目录（默认本项目目录）。"""

import os
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=cwd or BASE,
                       capture_output=True, timeout=30, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def git_operation(args):
    """统一 Git 操作：action ∈ status / diff / commit / branch / branch_create / branch_switch / log"""
    action = str(args.get("action", "")).strip().lower()
    cwd = str(args.get("cwd", "")).strip() or BASE
    if not os.path.isdir(cwd):
        return "错误：目录不存在 %s" % cwd
    if not os.path.isdir(os.path.join(cwd, ".git")):
        return "错误：%s 不是 git 仓库（可用 git init 初始化，或指定项目目录）" % cwd
    try:
        if action == "status":
            rc, branch, err = _git(cwd, "branch", "--show-current")
            rc2, out, err2 = _git(cwd, "status", "--short")
            rc3, ahead, _ = _git(cwd, "status", "-sb")
            head = ""
            if branch:
                head = "分支: %s\n" % branch
            items = out.splitlines()
            return "Git 状态：%s\n%s%s" % (
                "已是最新" if not items else "%d 项变更" % len(items), head, out or "(无变更)")
        if action == "diff":
            rc, out, err = _git(cwd, "diff")
            if not out:
                rc, out, err = _git(cwd, "diff", "--cached")
            if not out:
                return "（无未提交的变更）"
            return "Git Diff（前 8000 字符）：\n" + out[:8000]
        if action == "commit":
            msg = str(args.get("message", "")).strip()
            if not msg:
                return "错误：commit 需要 message 参数（提交说明）"
            # 先暂存全部（对标工作流：改动提交）
            rc, _, err = _git(cwd, "add", "-A")
            if rc != 0:
                return "git add 失败: %s" % err
            rc, out, err = _git(cwd, "commit", "-m", msg)
            if rc != 0:
                return "提交失败: %s" % (err or out)
            return "已提交：\n%s" % out
        if action == "branch":
            rc, out, err = _git(cwd, "branch")
            return "分支列表：\n%s" % (out or "(无)")
        if action == "branch_create":
            name = str(args.get("name", "")).strip()
            if not name:
                return "错误：branch_create 需要 name 参数（新分支名）"
            rc, out, err = _git(cwd, "checkout", "-b", name)
            return "已创建并切换分支 %s：\n%s" % (name, out or err)
        if action == "branch_switch":
            name = str(args.get("name", "")).strip()
            if not name:
                return "错误：branch_switch 需要 name 参数（目标分支名）"
            rc, out, err = _git(cwd, "checkout", name)
            return "已切换分支 %s：\n%s" % (name, out or err)
        if action == "log":
            n = max(1, min(int(args.get("count", 10) or 10), 50))
            rc, out, err = _git(cwd, "log", "--oneline", "-n", str(n))
            return "最近 %d 条提交：\n%s" % (n, out or "(暂无提交)")
        return "错误：未知 action=%s（支持 status/diff/commit/branch/branch_create/branch_switch/log）" % action
    except subprocess.TimeoutExpired:
        return "错误：git 命令超时（30 秒）"
    except Exception as e:
        return "git 操作失败: %s" % str(e)[:200]


PLUGIN_TOOLS = [
    {
        "name": "git_operation",
        "description": "Git 版本控制操作（对标 opencode）：action 可选\n"
                       "status（当前分支+变更概览）| diff（查看未提交改动）| commit（暂存并提交，需 message 参数）|\n"
                       "branch（分支列表）| branch_create（新建分支，需 name）| branch_switch（切换分支，需 name）|\n"
                       "log（最近提交，count 可选默认 10）。\n"
                       "用于：查看/提交代码改动、管理分支、查看提交历史。cwd 可选指定仓库目录（默认本项目）。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "操作：status/diff/commit/branch/branch_create/branch_switch/log"},
                "message": {"type": "string", "description": "提交说明（commit 时需要）"},
                "name": {"type": "string", "description": "分支名（branch_create/branch_switch 时需要）"},
                "count": {"type": "integer", "description": "log 返回条数（默认 10）"},
                "cwd": {"type": "string", "description": "git 仓库目录（可选，默认本项目目录）"}
            },
            "required": ["action"]
        },
        "handler": git_operation,
    }
]
