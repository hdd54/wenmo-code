"""Resolve each conversation to an isolated Git worktree when applicable."""

from dataclasses import dataclass
import hashlib
import pathlib
import subprocess

from task_workspace import TaskWorkspaceManager


@dataclass(frozen=True)
class ConversationWorkspace:
    path: pathlib.Path
    isolated: bool
    task_key: str = ""
    repo: pathlib.Path | None = None
    branch: str = ""


def _git_root(path):
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=str(path),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode:
        return None
    return pathlib.Path(proc.stdout.strip()).resolve()


def conversation_task_key(tenant, conversation_id):
    identity = "%s\0%s" % (tenant or "local", conversation_id or "")
    return "chat-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def resolve_conversation_workspace(manager: TaskWorkspaceManager, conversation_id, tenant, project_path):
    project = pathlib.Path(project_path).resolve() if project_path else None
    if project is None or not project.is_dir():
        return ConversationWorkspace(path=project or pathlib.Path(), isolated=False)
    repo = _git_root(project)
    if repo is None:
        return ConversationWorkspace(path=project, isolated=False)
    if not conversation_id:
        raise RuntimeError("Git 项目会话缺少 conversation_id，已拒绝在原仓库执行工具")
    task_key = conversation_task_key(tenant, conversation_id)
    isolated = manager.create(task_key, repo)
    return ConversationWorkspace(
        path=isolated.path, isolated=True, task_key=task_key,
        repo=isolated.repo, branch=isolated.branch,
    )
