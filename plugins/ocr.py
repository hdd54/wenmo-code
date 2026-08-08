"""OpenCodeReview 插件：让 AI 用阿里开源的 ocr CLI 对代码做行级审查。
移植自 alibaba/open-code-review 官方 OpenCode 插件（Apache-2.0）。
工具：
  plugin_ocr_review  —— 审查工作区改动 / 单 commit / 分支范围，返回结构化 JSON 行级评论
  plugin_ocr_health —— 检查 ocr 安装版本与 LLM 连接
安全：子进程在用户确认的工作目录执行；超时/输出上限/进程树清理（对标官方插件防护）。
"""

import json
import os
import signal
import subprocess
import sys
import time

# ocr 可执行文件：默认 PATH 里的 ocr；可用环境变量 OCR_BIN 覆盖（如指向 npm 全局安装路径）
_OCR_BIN = os.environ.get("OCR_BIN", "ocr")
# 输出安全上限（10 MiB，对标官方插件）
_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
# 单次审查默认超时（15 分钟）
_DEFAULT_TIMEOUT_MS = 15 * 60 * 1000


def _find_ocr():
    """定位 ocr 可执行文件；找不到返回 None（给模型可操作的报错信息）。
    优先找原生二进制（npm 全局包内的 .exe，Windows 下 .cmd 垫片无法被 Popen 直接执行）。"""
    if os.path.isabs(_OCR_BIN):
        return _OCR_BIN if os.path.isfile(_OCR_BIN) else None
    # ① 显式 OCR_BIN（用户指定）
    if os.environ.get("OCR_BIN"):
        b = os.environ["OCR_BIN"]
        if os.path.isfile(b):
            return b
    # ② npm 全局包内的原生二进制（@alibaba-group/ocr-<platform>/bin/*.exe）
    #    直接探测已知全局路径（不跑 npm 命令——npm 是 .cmd 垫片，Python 无法直接执行）
    for npm_root in (
        os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules"),
        os.path.join(os.path.expanduser("~"), ".npm-global", "node_modules"),
        os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "node_modules"),
    ):
        ocr_pkg = os.path.join(npm_root, "@alibaba-group", "open-code-review",
                               "node_modules", "@alibaba-group")
        if not os.path.isdir(ocr_pkg):
            continue
        for d in os.listdir(ocr_pkg):
            if not d.startswith("ocr-"):
                continue
            bin_dir = os.path.join(ocr_pkg, d, "bin")
            if os.path.isdir(bin_dir):
                for fn in os.listdir(bin_dir):
                    cand = os.path.join(bin_dir, fn)
                    if fn.endswith(".exe") or (os.name != "nt" and os.access(cand, os.X_OK)):
                        return cand
    # ③ PATH 查找（排除 .cmd/.ps1 垫片——它们需 shell 执行，Popen 直接跑会 WinError 193）
    pathext = os.environ.get("PATHEXT", "").lower().split(os.pathsep) if os.name == "nt" else []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        for fn in os.listdir(d) if os.path.isdir(d) else []:
            low = fn.lower()
            if low == "ocr" or low == "ocr.exe":
                cand = os.path.join(d, fn)
                if low.endswith(".exe") or os.name != "nt":
                    return cand
    return None


class OcrError(Exception):
    def __init__(self, message, exit_code=None, stderr="", stdout=""):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout


def _push_value(args, flag, value):
    """仅当 value 非空才追加 flag+value（对标官方 pushValue）"""
    if value is not None and str(value) != "":
        args.append(flag)
        args.append(str(value))


def build_review_args(input_, repo):
    """构造 ocr review 参数（对标官方 buildReviewArgs，含参数冲突校验）"""
    has_range = input_.get("from") is not None or input_.get("to") is not None
    if has_range and (not input_.get("from") or not input_.get("to")):
        raise ValueError("分支对比必须同时提供 from 和 to。")
    if input_.get("commit") and has_range:
        raise ValueError("commit 与 from/to 范围二选一，不能同时使用。")
    if input_.get("resume") and (input_.get("commit") or has_range):
        raise ValueError("resume 不能与 commit 或 from/to 范围组合。")
    if input_.get("preview") and input_.get("resume"):
        raise ValueError("preview 与 resume 不能同时使用。")

    args = ["review", "--audience", "agent"]
    if not input_.get("preview"):
        args.append("--format")
        args.append("json")
    args.append("--repo")
    args.append(repo)

    _push_value(args, "--commit", input_.get("commit"))
    _push_value(args, "--from", input_.get("from"))
    _push_value(args, "--to", input_.get("to"))
    _push_value(args, "--resume", input_.get("resume"))
    _push_value(args, "--background", input_.get("background"))
    _push_value(args, "--exclude", input_.get("exclude"))
    _push_value(args, "--model", input_.get("model"))
    _push_value(args, "--concurrency", input_.get("concurrency"))
    _push_value(args, "--timeout", input_.get("timeoutMinutes"))
    _push_value(args, "--max-tools", input_.get("maxTools"))
    _push_value(args, "--max-git-procs", input_.get("maxGitProcesses"))

    if input_.get("preview"):
        args.append("--preview")
    return args


def _run_ocr(args, cwd, timeout_ms=None):
    """运行 ocr 子进程（对标官方 runOcr）：
    - shell=False（防注入）
    - 超时终止 + 3 秒后强制杀进程树（Windows 用 taskkill /T /F）
    - 输出上限 10 MiB
    """
    timeout_ms = timeout_ms or _DEFAULT_TIMEOUT_MS
    ocr = _find_ocr()
    if ocr is None:
        raise OcrError(
            "OpenCodeReview 未安装：PATH 中找不到 'ocr'。请先安装：\n"
            "  npm install -g @alibaba-group/open-code-review\n"
            "（或下载 GitHub Release 二进制，并用环境变量 OCR_BIN 指定路径）"
        )
    cmd = [ocr] + args
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            shell=False,
            text=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        raise OcrError(f"启动 OpenCodeReview 失败: {e}")

    # 收集输出并限流
    stdout_chunks = []
    stderr_chunks = []
    total_bytes = 0
    limit_exceeded = False

    def _read_stream(stream, sink):
        nonlocal total_bytes, limit_exceeded
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > _MAX_OUTPUT_BYTES:
                    limit_exceeded = True
                    break
                sink.append(chunk)
        except Exception:
            pass

    deadline = time.time() + timeout_ms / 1000.0
    force_killed = False
    while proc.poll() is None:
        if time.time() > deadline:
            # 超时：先终止进程组，3 秒后再强杀
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=5,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            force_killed = True
            break
        # 边等边读（防止管道写满阻塞子进程）
        _read_stream(proc.stdout, stdout_chunks)
        _read_stream(proc.stderr, stderr_chunks)
        time.sleep(0.05)
    _read_stream(proc.stdout, stdout_chunks)
    _read_stream(proc.stderr, stderr_chunks)

    if limit_exceeded:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        proc.wait()
        raise OcrError(f"OCR 输出超过 {_MAX_OUTPUT_BYTES} 字节安全上限，已终止。")

    exit_code = proc.wait()
    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()

    if force_killed:
        raise OcrError(
            f"OpenCodeReview 超过 {int(timeout_ms / 1000)} 秒超时，已终止。"
            f"\n（可尝试：拆小审查范围，或用 concurrency 限制并发）",
            exit_code=exit_code, stderr=stderr, stdout=stdout,
        )
    if exit_code != 0:
        raise OcrError(
            stderr or stdout or f"OpenCodeReview 退出码 {exit_code}",
            exit_code=exit_code, stderr=stderr, stdout=stdout,
        )
    return stdout, stderr


def ocr_review_handler(arguments: dict) -> dict:
    """执行 ocr review。返回结构化 JSON 行级评论（字符串）。"""
    try:
        cwd = str(arguments.get("repo") or arguments.get("cwd") or "").strip() or os.getcwd()
        if not os.path.isdir(cwd):
            return {"error": f"仓库目录不存在: {cwd}（请传绝对路径；不传默认当前项目目录）"}
        preview = bool(arguments.get("preview"))
        args = build_review_args(arguments, cwd)
        stdout, stderr = _run_ocr(args, cwd)
        if preview:
            return {"ok": True, "preview": stdout or "没有文件变更。"}
        if not stdout:
            return {"ok": True, "result": "没有检测到变更；OCR 无输出。"}
        # 校验是合法 JSON（对标官方 formatReviewResult）
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return {"error": "OCR 返回了非法 JSON（可能模型未配置或输出异常）。", "raw": stdout[:2000], "stderr": stderr[:1000]}
        return {
            "ok": True,
            "findings": parsed,
            "note": "这是 ocr 返回的结构化行级审查结果（JSON）。请按严重级别（critical/high/medium/low）"
                    "向用户汇总关键问题，附精确文件与行号；不要原样倾倒整个 JSON。",
        }
    except OcrError as e:
        return {"error": str(e), "exit_code": e.exit_code, "stderr": e.stderr[:1000]}
    except ValueError as e:
        return {"error": f"参数错误: {e}"}
    except Exception as e:
        return {"error": f"审查失败: {e}"}


def ocr_health_handler(arguments: dict) -> dict:
    """检查 ocr 版本与 LLM 连接。"""
    cwd = str(arguments.get("cwd") or "").strip() or os.getcwd()
    out = []
    # 版本检查
    try:
        v_stdout, v_stderr = _run_ocr(["version"], cwd, timeout_ms=30000)
        out.append(v_stdout or v_stderr or "（版本信息为空）")
    except OcrError as e:
        out.append(f"版本检查失败: {e}")
    # LLM 连接检查
    try:
        l_stdout, l_stderr = _run_ocr(["llm", "test"], cwd, timeout_ms=60000)
        out.append(l_stdout or l_stderr or "（LLM 连接正常）")
    except OcrError as e:
        out.append(f"LLM 连接检查失败: {e}")
    return {"ok": True, "result": "\n".join(out)}


def build_scan_args(input_, repo):
    """构造 ocr scan 参数（全文件扫描，无需 git diff）"""
    args = ["scan", "--audience", "agent"]
    if not input_.get("preview"):
        args.append("--format")
        args.append("json")
    args.append("--repo")
    args.append(repo)
    _push_value(args, "--path", input_.get("path"))
    _push_value(args, "--exclude", input_.get("exclude"))
    _push_value(args, "--model", input_.get("model"))
    _push_value(args, "--resume", input_.get("resume"))
    _push_value(args, "--provider", input_.get("provider"))
    if input_.get("noPlan"):
        args.append("--no-plan")
    if input_.get("preview"):
        args.append("--preview")
    return args


def ocr_scan_handler(arguments: dict) -> dict:
    """执行 ocr scan：全文件扫描审查（无需 git diff，适合非 git 项目）。"""
    try:
        cwd = str(arguments.get("repo") or arguments.get("cwd") or "").strip() or os.getcwd()
        if not os.path.isdir(cwd):
            return {"error": f"仓库目录不存在: {cwd}（请传绝对路径；不传默认当前项目目录）"}
        preview = bool(arguments.get("preview"))
        args = build_scan_args(arguments, cwd)
        stdout, stderr = _run_ocr(args, cwd)
        if preview:
            return {"ok": True, "preview": stdout or "没有可扫描的文件。"}
        if not stdout:
            return {"ok": True, "result": "扫描完成，无发现。"}
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return {"error": "OCR 返回了非法 JSON。", "raw": stdout[:2000], "stderr": stderr[:1000]}
        return {
            "ok": True,
            "findings": parsed,
            "note": "这是 ocr scan 返回的结构化审查结果（JSON）。请按严重级别向用户汇总关键问题，"
                    "附精确文件与行号；不要原样倾倒整个 JSON。",
        }
    except OcrError as e:
        return {"error": str(e), "exit_code": e.exit_code, "stderr": e.stderr[:1000]}
    except ValueError as e:
        return {"error": f"参数错误: {e}"}
    except Exception as e:
        return {"error": f"扫描失败: {e}"}


PLUGIN_TOOLS = [
    {
        "name": "ocr_review",
        "description": "对代码做 AI 行级审查（阿里 open-code-review，Apache-2.0）。"
                       "审查工作区改动 / 单个 commit / 分支范围，返回结构化 JSON（含文件、行号、严重级别、问题说明）。"
                       "用法：不传任何参数 → 审查当前项目工作区未提交改动；"
                       "传 commit=xxx → 审查单个 commit；传 from=a&to=b → 审查分支/版本范围对比；"
                       "传 preview=true → 先列出将审查的文件（不消耗 LLM）。"
                       "可选：background=业务/需求背景；exclude=排除模式（逗号分隔 gitignore 风格）；"
                       "model=覆盖模型；concurrency=最大并发文件审查数；timeoutMinutes=单文件超时分钟数。"
                       "返回的 JSON 请按严重级别向用户汇总，附精确文件与行号，不要原样倾倒。",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库目录绝对路径（可选；默认当前项目目录）"},
                "commit": {"type": "string", "description": "审查单个 commit（与其父提交对比）"},
                "from": {"type": "string", "description": "分支/范围对比的基准 ref（必须与 to 成对）"},
                "to": {"type": "string", "description": "分支/范围对比的目标 ref（必须与 from 成对）"},
                "resume": {"type": "string", "description": "按会话 ID 恢复之前的审查"},
                "background": {"type": "string", "description": "业务/需求背景，审查应满足的上下文"},
                "exclude": {"type": "string", "description": "排除模式（逗号分隔 gitignore 风格）"},
                "model": {"type": "string", "description": "覆盖 ocr 配置中的模型"},
                "concurrency": {"type": "integer", "description": "最大并发文件审查数"},
                "timeoutMinutes": {"type": "integer", "description": "单文件审查超时分钟数"},
                "preview": {"type": "boolean", "description": "true=只列出将审查的文件，不调用 LLM"},
            },
            "required": [],
        },
        "handler": ocr_review_handler,
    },
    {
        "name": "ocr_health",
        "description": "检查 OpenCodeReview（ocr）是否安装、版本号，以及它配置的 LLM 连接是否正常。"
                       "安装缺失或连接失败时会给出具体报错。",
        "parameters": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "仓库目录绝对路径（可选）"},
            },
            "required": [],
        },
        "handler": ocr_health_handler,
    },
    {
        "name": "ocr_scan",
        "description": "对项目做全文件 AI 代码审查（阿里 open-code-review 的 scan 模式，无需 git diff，适合非 git 项目/旧代码库）。"
                       "返回结构化 JSON（含文件、行号、严重级别、问题说明）。"
                       "用法：不传参数 → 扫描当前项目全部文件（建议传 exclude 排除 node_modules/构建产物/大文件）；"
                       "path=指定目录或文件（逗号分隔多个）；exclude=排除模式（gitignore 风格，如 '**/node_modules/**,**/dist/**,*.pyc'）；"
                       "preview=true → 先列出将扫描的文件（不消耗 LLM）。"
                       "返回的 JSON 请按严重级别向用户汇总，附精确文件与行号，不要原样倾倒。",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库目录绝对路径（可选；默认当前项目目录）"},
                "path": {"type": "string", "description": "扫描范围：目录或文件，逗号分隔多个（可选）"},
                "exclude": {"type": "string", "description": "排除模式（gitignore 风格，逗号分隔，如 '**/node_modules/**,**/dist/**'）"},
                "model": {"type": "string", "description": "覆盖 ocr 配置中的模型"},
                "provider": {"type": "string", "description": "覆盖 ocr 配置中的 provider"},
                "resume": {"type": "string", "description": "按会话 ID 恢复之前的扫描"},
                "noPlan": {"type": "boolean", "description": "跳过每文件的 PLAN_TASK 预扫描"},
                "preview": {"type": "boolean", "description": "true=只列出将扫描的文件，不调用 LLM"},
            },
            "required": [],
        },
        "handler": ocr_scan_handler,
    },
]
