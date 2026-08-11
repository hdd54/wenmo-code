"""Task command execution in a real OCI or WSL+bubblewrap sandbox.

The module deliberately fails closed when no verified backend is available. A
Windows Job Object can limit a process tree, but it is not a filesystem or
network sandbox and is therefore not represented as one here.
"""

from dataclasses import dataclass
import os
import pathlib
import re
import shutil
import subprocess


class SandboxUnavailable(RuntimeError):
    pass


_LAST_WSL_PROBE_ERROR = ""


@dataclass(frozen=True)
class SandboxStatus:
    available: bool
    runtime: str = ""
    mode: str = "unavailable"
    detail: str = ""


def discover_container_runtime():
    for name in ("docker", "podman"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _decode_wsl_output(raw):
    if not raw:
        return ""
    if b"\x00" in raw:
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    return raw.decode("utf-8", errors="replace")


def discover_wsl_distro():
    """Return a WSL2 distro with a working bubblewrap sandbox, or None.

    Bare WSL is not considered a sandbox because it exposes Windows drives and
    the host network. bubblewrap must successfully create all namespaces.
    """
    global _LAST_WSL_PROBE_ERROR
    _LAST_WSL_PROBE_ERROR = ""
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        return None
    requested = os.environ.get("WENMO_WSL_DISTRO", "").strip()
    try:
        if requested:
            candidates = [requested]
        else:
            listed = subprocess.run(
                [wsl, "--list", "--quiet"], capture_output=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if listed.returncode:
                return None
            candidates = [line.strip().strip("\x00")
                          for line in _decode_wsl_output(listed.stdout).splitlines()
                          if line.strip().strip("\x00")]
        resource_root = pathlib.Path(
            os.environ.get("WENMO_RES_DIR") or pathlib.Path(__file__).resolve().parent)
        entrypoint = (resource_root / "sandbox" / "wsl_entrypoint.sh").resolve()
        workspace = pathlib.Path(__file__).resolve().parent

        def mount_path(path):
            matched = re.match(r"^([A-Za-z]):[\\/](.*)$", str(path))
            if not matched:
                return ""
            return "/mnt/%s/%s" % (
                matched.group(1).lower(), matched.group(2).replace("\\", "/"))

        entrypoint_wsl = mount_path(entrypoint)
        workspace_wsl = mount_path(workspace)
        if not entrypoint.is_file() or not entrypoint_wsl or not workspace_wsl:
            return None
        for distro in candidates:
            # Probe the same trusted wrapper used for real commands. This also
            # covers merged-/usr layouts where /bin and /lib are symlinks.
            identity = subprocess.run(
                [wsl, "-d", distro, "--", "id", "-u"],
                capture_output=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            uid = _decode_wsl_output(identity.stdout).strip().strip("\x00")
            if identity.returncode or not uid or uid == "0":
                detail = _decode_wsl_output(identity.stderr or identity.stdout).strip()
                _LAST_WSL_PROBE_ERROR = "%s: %s" % (
                    distro, (detail or "默认 WSL 用户不能是 root")[:300])
                continue
            probe = subprocess.run(
                [wsl, "-d", distro, "--", "sh", entrypoint_wsl,
                 workspace_wsl, "true", "", ""],
                capture_output=True, timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if probe.returncode == 0:
                return distro
            detail = _decode_wsl_output(probe.stderr or probe.stdout).strip()
            if detail:
                _LAST_WSL_PROBE_ERROR = "%s: %s" % (distro, detail[:300])
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def discover_sandbox_backend():
    runtime = discover_container_runtime()
    if runtime:
        return ("oci", runtime)
    distro = discover_wsl_distro()
    if distro:
        return ("wsl-bubblewrap", distro)
    return None


def get_sandbox_status():
    backend = discover_sandbox_backend()
    if not backend:
        detail = "未检测到 Docker/Podman，或 WSL2 中缺少可工作的 bubblewrap；required 模式拒绝执行"
        if _LAST_WSL_PROBE_ERROR:
            detail += "（%s）" % _LAST_WSL_PROBE_ERROR
        return SandboxStatus(False, detail=detail)
    kind, runtime = backend
    if kind == "oci":
        return SandboxStatus(True, runtime=runtime, mode="oci-container",
                             detail="network=none, read-only rootfs, task worktree only")
    return SandboxStatus(True, runtime=runtime, mode="wsl-bubblewrap",
                         detail="WSL2 bubblewrap user+mount+network+PID namespaces, task worktree only")


def get_sandbox_diagnostics():
    """Return actionable, non-mutating setup diagnostics for the settings UI."""
    status = get_sandbox_status()
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    docker = shutil.which("docker")
    podman = shutil.which("podman")
    distro = status.runtime if status.mode == "wsl-bubblewrap" else ""
    if not distro and wsl:
        requested = os.environ.get("WENMO_WSL_DISTRO", "").strip()
        distro = requested or "Ubuntu"
    setup_command = ""
    if wsl:
        setup_command = (
            'wsl.exe -d "%s" -- sh -lc "sudo apt-get update && '
            'sudo apt-get install -y bubblewrap"' % distro.replace('"', ""))
    return {
        **status.__dict__,
        "checks": {
            "docker": bool(docker),
            "podman": bool(podman),
            "wsl": bool(wsl),
            "bubblewrap_verified": status.mode == "wsl-bubblewrap",
        },
        "probe_error": _LAST_WSL_PROBE_ERROR,
        "setup_command": setup_command,
        "setup_requires_confirmation": True,
        "setup_note": (
            "复制命令后在 PowerShell 中手动执行；sudo 会要求 WSL 密码。"
            if setup_command else
            "请先从 Windows 可选功能/商店安装 WSL2，或安装 Docker Desktop/Podman。"),
    }


def build_container_command(runtime, workspace, argv, image="wenmo-agent:locked"):
    if not runtime:
        raise SandboxUnavailable("没有可用的 Docker/Podman 运行时")
    workspace = pathlib.Path(workspace).resolve()
    if not workspace.is_dir():
        raise SandboxUnavailable("任务工作区不存在")
    if not isinstance(argv, (list, tuple)) or not argv:
        raise ValueError("argv must be a non-empty list")
    mount = "type=bind,source=%s,target=/workspace" % str(workspace)
    return [
        str(runtime), "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "256", "--memory", "2g", "--cpus", "2",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
        "--mount", mount, "--workdir", "/workspace", "--user", "65532:65532",
        image, *[str(item) for item in argv],
    ]


def _wsl_path(wsl, distro, path):
    raw_path = str(path)
    drive_match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw_path)
    if drive_match:
        tail = drive_match.group(2).replace("\\", "/")
        return "/mnt/%s/%s" % (drive_match.group(1).lower(), tail)
    proc = subprocess.run(
        [wsl, "-d", distro, "--", "wslpath", "-a", raw_path],
        capture_output=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode:
        raise SandboxUnavailable(_decode_wsl_output(proc.stderr or proc.stdout).strip()
                                 or "无法转换 WSL 路径")
    return _decode_wsl_output(proc.stdout).strip().strip("\x00")


def _git_metadata(workspace):
    marker = pathlib.Path(workspace) / ".git"
    # A normal checkout keeps metadata inside the mounted workspace already.
    # Only Windows worktrees need a synthetic Linux-readable gitdir pointer.
    if not marker.is_file():
        return None, None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    def query(name):
        proc = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", name],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=flags,
        )
        if proc.returncode:
            raise SandboxUnavailable((proc.stderr or "无法解析 Git worktree").strip())
        return pathlib.Path(proc.stdout.strip()).resolve()
    git_dir = query("--git-dir")
    common = query("--git-common-dir")
    try:
        relative = git_dir.relative_to(common).as_posix()
    except ValueError as exc:
        raise SandboxUnavailable("Git worktree 元数据不在 common dir 内") from exc
    return common, relative


def build_wsl_command(distro, workspace, command_text, entrypoint=None):
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl or not distro:
        raise SandboxUnavailable("没有可用的 WSL2 发行版")
    workspace = pathlib.Path(workspace).resolve()
    if not workspace.is_dir():
        raise SandboxUnavailable("任务工作区不存在")
    entrypoint = pathlib.Path(entrypoint or (
        pathlib.Path(os.environ.get("WENMO_RES_DIR") or pathlib.Path(__file__).resolve().parent)
        / "sandbox" / "wsl_entrypoint.sh")).resolve()
    if not entrypoint.is_file():
        raise SandboxUnavailable("缺少 WSL 沙箱入口脚本")
    wsl_workspace = _wsl_path(wsl, distro, workspace)
    wsl_entrypoint = _wsl_path(wsl, distro, entrypoint)
    common, relative = _git_metadata(workspace)
    wsl_common = _wsl_path(wsl, distro, common) if common else ""
    return [
        wsl, "-d", distro, "--", "sh", wsl_entrypoint,
        wsl_workspace, str(command_text), wsl_common,
        relative or "",
    ]


def run_sandboxed(argv, workspace, timeout=60, image="wenmo-agent:locked", runtime=None):
    backend = runtime or discover_sandbox_backend()
    if isinstance(backend, str):
        backend = ("oci", backend)
    if not backend:
        raise SandboxUnavailable("没有可用的任务级 OS 沙箱")
    kind, value = backend
    if kind == "wsl-bubblewrap":
        if not isinstance(argv, (list, tuple)) or len(argv) < 3 or argv[:2] != ["/bin/sh", "-lc"]:
            raise ValueError("WSL sandbox expects ['/bin/sh', '-lc', command]")
        command = build_wsl_command(value, workspace, argv[2])
    else:
        command = build_container_command(value, workspace, argv, image=image)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=max(1, min(int(timeout), 600)),
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
