"""Per-task Git worktree lifecycle and review surfaces."""

from dataclasses import dataclass
import os
import pathlib
import re
import subprocess
import threading


@dataclass(frozen=True)
class TaskWorkspace:
    task_id: str
    repo: pathlib.Path
    path: pathlib.Path
    branch: str


class TaskWorkspaceManager:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._items = {}
        self._lock = threading.RLock()

    @staticmethod
    def _safe_task_id(task_id):
        value = re.sub(r"[^A-Za-z0-9._-]", "-", str(task_id or "")).strip("-.")
        if not value:
            raise ValueError("invalid task id")
        return value[:80]

    @staticmethod
    def _git(cwd, *args):
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout or "git failed").strip())
        return proc.stdout

    def create(self, task_id, repo):
        safe_id = self._safe_task_id(task_id)
        repo = pathlib.Path(repo).resolve()
        if not repo.is_dir():
            raise ValueError("repository does not exist")
        top = pathlib.Path(self._git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
        if top != repo:
            repo = top
        path = (self.root / safe_id).resolve()
        if self.root not in path.parents:
            raise ValueError("worktree path escaped root")
        branch = "wenmo/task-" + safe_id
        with self._lock:
            if path.exists():
                actual_top = pathlib.Path(
                    self._git(path, "rev-parse", "--show-toplevel").strip()).resolve()
                actual_branch = self._git(path, "branch", "--show-current").strip()
                if actual_top != path or actual_branch != branch:
                    raise RuntimeError("existing task workspace does not match its recorded branch")
                existing = TaskWorkspace(safe_id, repo, path, branch)
                self._items[safe_id] = existing
                return existing
            branch_exists = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", "refs/heads/" + branch],
                cwd=str(repo), capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).returncode == 0
            if branch_exists:
                self._git(repo, "worktree", "add", str(path), branch)
            else:
                self._git(repo, "worktree", "add", "-b", branch, str(path), "HEAD")
            item = TaskWorkspace(safe_id, repo, path, branch)
            self._items[safe_id] = item
            return item

    def get(self, task_id):
        with self._lock:
            return self._items.get(self._safe_task_id(task_id))

    def attach(self, task_id, repo, path, branch):
        safe_id = self._safe_task_id(task_id)
        item = TaskWorkspace(safe_id, pathlib.Path(repo).resolve(), pathlib.Path(path).resolve(), str(branch))
        if not item.path.is_dir():
            raise ValueError("worktree does not exist")
        with self._lock:
            self._items[safe_id] = item
        return item

    def diff(self, task_id):
        item = self.get(task_id)
        if not item:
            raise KeyError("unknown task workspace")
        diff = self._git(item.path, "diff", "HEAD", "--no-ext-diff", "--binary")
        untracked = self._git(item.path, "ls-files", "--others", "--exclude-standard").splitlines()
        chunks = [diff]
        for relative in untracked:
            proc = subprocess.run(
                ["git", "diff", "--no-index", "--binary", "--", os.devnull, relative],
                cwd=str(item.path), capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode not in (0, 1):
                raise RuntimeError((proc.stderr or "git diff failed").strip())
            chunks.append(proc.stdout)
        result = "".join(chunks)
        if len(result.encode("utf-8")) > 5 * 1024 * 1024:
            raise RuntimeError("diff exceeds 5 MiB review limit")
        return result

    def status(self, task_id):
        item = self.get(task_id)
        if not item:
            raise KeyError("unknown task workspace")
        return self._git(item.path, "status", "--short", "--branch")
