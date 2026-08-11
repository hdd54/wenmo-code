# -*- coding: utf-8 -*-
"""自动更新模块：启动时检查更新 → 下载 → 自替换。
更新源：UPDATE_URL/update.json，格式：
{
  "version": "1.0.0",
  "notes": "修复了xxx，新增了yyy",
  "url": "https://example.com/问墨_v1.0.0.exe",   # 完整安装包
  "sha256": "..."                                  # 可选，校验用
}
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import shutil

# 更新源：Gitee（国产优先）/ GitHub Releases / 自建服务器
# Gitee 方案：设置 GITEE_REPO = "用户名/仓库名"，自动用 Gitee Releases API（国内网络友好）
# GitHub 方案：设置 GITHUB_REPO = "用户名/仓库名"，自动用 GitHub Releases API
# 自建服务器方案：设置 UPDATE_URL（指向含 update.json 的目录）
GITEE_REPO = os.environ.get("WENMO_GITEE_REPO", "").strip()
GITHUB_REPO = os.environ.get("WENMO_GITHUB_REPO", "").strip() or "hdd54/wenmo-code"
UPDATE_URL = os.environ.get("WENMO_UPDATE_URL", "").rstrip("/")
APP_VERSION = "1.0.0"   # 当前版本（与打包脚本同步）

_STATE_FILE = None   # 初始化时设置


def _asset_digest(asset):
    """Read GitHub's immutable asset digest when the Releases API provides it."""
    digest = str((asset or {}).get("digest") or "").strip().lower()
    if digest.startswith("sha256:"):
        value = digest.split(":", 1)[1]
        if len(value) == 64 and all(c in "0123456789abcdef" for c in value):
            return value
    return ""


def _release_checksum(assets, target):
    """Resolve a target SHA-256 from asset metadata or checksums.sha256."""
    direct = _asset_digest(target)
    if direct:
        return direct
    target_name = str((target or {}).get("name") or "")
    checksum_asset = next((a for a in (assets or []) if a.get("name") == "checksums.sha256"), None)
    if not checksum_asset or not target_name:
        return ""
    url = checksum_asset.get("browser_download_url", "") or checksum_asset.get("download_url", "")
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wenmo-updater"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read(256 * 1024).decode("ascii", errors="replace")
        for line in text.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            digest, name = parts[0].lower(), parts[1].lstrip("* ")
            if name == target_name and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                return digest
    except Exception:
        pass
    return ""


def set_state_file(path):
    """设置更新状态文件路径（%APPDATA%/问墨/update_state.json）"""
    global _STATE_FILE
    _STATE_FILE = path


def _select_release_asset(assets):
    """Prefer the Authenticode-signed installer.

    ZIP/delta updates do not carry a locally verified detached signature yet,
    so production clients refuse them unless an explicit development override
    is set. SHA-256 beside an asset is integrity metadata, not publisher auth.
    """
    for asset in assets or []:
        name = str(asset.get("name", ""))
        lowered = name.lower()
        if lowered.endswith(".exe") and ("install" in lowered or "setup" in lowered):
            return asset
    if os.environ.get("WENMO_ALLOW_UNSIGNED_ZIP_UPDATE") == "1":
        for asset in assets or []:
            name = str(asset.get("name", "")).lower()
            if name == "update.zip" or name.endswith("_update.zip"):
                return asset
    return None


def _gitee_latest_release():
    """从 Gitee Releases（国产托管）获取最新版本信息。返回 {version, notes, url, sha256} 或 None"""
    if not GITEE_REPO:
        return None
    try:
        api = f"https://gitee.com/api/v5/repos/{GITEE_REPO}/releases/latest"
        req = urllib.request.Request(api, headers={"User-Agent": "wenmo-updater",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")   # 如 "v1.0.1"
        version = tag.lstrip("v")
        if not version:
            return None
        # Gitee assets 在 attach_files 里（name / browser_download_url）
        assets = data.get("attach_files", []) or []
        target = _select_release_asset(assets)
        if not target:
            return None
        return {"version": version, "notes": data.get("body", ""),
                "url": target.get("browser_download_url", ""),
                "sha256": _release_checksum(assets, target), "asset_name": target.get("name", ""),
                "assets": assets}
    except Exception:
        return None


def _github_latest_release():
    """从 GitHub Releases 获取最新版本信息。返回 {version, notes, url, sha256} 或 None"""
    if not GITHUB_REPO:
        return None
    try:
        api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(api, headers={"User-Agent": "wenmo-updater",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")   # 如 "v1.0.1"
        version = tag.lstrip("v")
        if not version:
            return None
        assets = data.get("assets", [])
        target = _select_release_asset(assets)
        if not target:
            return None
        return {"version": version, "notes": data.get("body", ""),
                "url": target.get("browser_download_url", ""),
                "sha256": _release_checksum(assets, target), "asset_name": target.get("name", ""),
                "assets": data.get("assets", [])}
    except Exception:
        return None


def _latest_release_info():
    """并行查 Gitee + GitHub 最新版本（国内优先、海外兜底），返回先到者或 None"""
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as _ex:
            _futures = []
            if GITEE_REPO:
                _futures.append(_ex.submit(_gitee_latest_release))
            _futures.append(_ex.submit(_github_latest_release))
            for _f in _futures:
                try:
                    _r = _f.result(timeout=20)
                    if _r:
                        return _r
                except Exception:
                    continue
    except Exception:
        pass
    # 兜底：串行
    if GITEE_REPO:
        _r = _gitee_latest_release()
        if _r:
            return _r
    return _github_latest_release()


def _version_gt(a, b):
    """语义化版本比较：a > b 返回 True"""
    try:
        aa = [int(x) for x in a.split(".")]
        bb = [int(x) for x in b.split(".")]
        for i in range(max(len(aa), len(bb))):
            x = aa[i] if i < len(aa) else 0
            y = bb[i] if i < len(bb) else 0
            if x != y:
                return x > y
        return False
    except Exception:
        return False



# ==================== 增量更新（Delta Update）====================
# 三件套：manifest.json（文件指纹）+ delta-vX.Y.Z.zip（增量包）+ update.zip（全量兜底）
# 流程：下载 manifest → 与本地文件对比 → 相邻版本走 delta，跨版本走全量


def _local_app_dir():
    """程序目录（打包版 = exe 所在目录）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def download_manifest(assets):
    """从 Release 资产下载 manifest.json，返回 dict 或 None"""
    for a in assets or []:
        if a.get("name", "") == "manifest.json":
            url = a.get("browser_download_url", "")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "wenmo-updater"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:
                return None
    return None


def compare_files(manifest):
    """对比本地文件与 manifest，返回 (to_download, to_delete, same_count)"""
    app_dir = _local_app_dir()
    mfiles = manifest.get("files", {})
    to_download = []
    to_delete = []
    same = 0
    for rel, info in mfiles.items():
        fp = os.path.join(app_dir, rel.replace("/", os.sep))
        if os.path.isfile(fp) and _sha256(fp) == info.get("sha256"):
            same += 1
        else:
            to_download.append(rel)
    # 本地有、manifest 没有 → 待删除（排除用户数据目录 & 隐藏/临时文件）
    # User/install extension surfaces are DLC state, not application-update
    # payload. Updates must never delete or overwrite MCP, skills or plugins.
    skip_prefixes = (
        "files/", "workspace/", "history/", "deps/", "content/",
        "seed/plugins/", "seed/skills/", "seed/extensions/",
        "_internal/seed/plugins/", "_internal/seed/skills/",
        "_internal/seed/extensions/",
    )
    skip_files = {
        "mcp.json", "mcp.local.json", "websearch_mcp_server.py",
        "file_mcp_server.py", "ppt_pipeline_mcp_server.py",
    }
    for root, dirs, files in os.walk(app_dir):
        for fn in files:
            if fn.startswith(".") or fn.endswith(".tmp") or fn.endswith(".bak"):
                continue
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, app_dir).replace("\\", "/")
            if rel in skip_files or rel.startswith(skip_prefixes):
                continue
            if rel not in mfiles:
                to_delete.append(rel)
    return to_download, to_delete, same


def download_delta(version, progress_cb=None):
    """从 Release（Gitee/GitHub 并行）下载 delta-v{version}.zip，返回路径或 None（带进度回调）"""
    info = _latest_release_info()
    if not info:
        return None
    for a in info.get("assets", []):
        if a.get("name", "") == "delta-v%s.zip" % version:
            url = a.get("browser_download_url", "")
            try:
                tmp = tempfile.mkdtemp(prefix="wenmo_delta_")
                path = os.path.join(tmp, a["name"])
                # 智能下载：真实字节进度 + 3 次重试（根治 0% 卡死）
                _download_with_progress(url, path, progress_cb=progress_cb)
                return path
            except Exception:
                return None
    return None


def apply_delta_update(delta_zip, manifest):
    """应用 delta 更新：备份 → 解压覆盖 → 删除多余 → 失败回滚。
    返回错误字符串或 None（成功）。"""
    import zipfile
    app_dir = _local_app_dir()
    data_root = os.environ.get("APPDATA", os.path.expanduser("~"))
    backup_dir = os.path.join(data_root, "问墨", "update_backup", APP_VERSION)
    # 打包版不能让正在运行的主进程覆盖自身。先完整校验，再交给独立
    # PowerShell updater 在主进程退出后完成替换/删除/失败回滚。
    if getattr(sys, "frozen", False):
        return _schedule_delta_update(delta_zip, manifest, app_dir, backup_dir)
    try:
        deleted = []
        with zipfile.ZipFile(delta_zip) as zf:
            if "delete_list.json" in zf.namelist():
                deleted = json.loads(zf.read("delete_list.json").decode("utf-8")).get("deleted", [])
        # 备份将被覆盖的文件
        with zipfile.ZipFile(delta_zip) as zf:
            for name in zf.namelist():
                if name == "delete_list.json":
                    continue
                dst = os.path.join(app_dir, name.replace("/", os.sep))
                if os.path.isfile(dst):
                    bak = os.path.join(backup_dir, name.replace("/", os.sep))
                    os.makedirs(os.path.dirname(bak), exist_ok=True)
                    shutil.copy2(dst, bak)
            # 解压覆盖（先校验每个文件 sha256 与 manifest 一致）
            for name in zf.namelist():
                if name == "delete_list.json":
                    continue
                info = manifest.get("files", {}).get(name)
                if info and info.get("sha256"):
                    raw = zf.read(name)
                    if hashlib.sha256(raw).hexdigest() != info["sha256"]:
                        raise RuntimeError("校验失败: " + name)
            zf.extractall(app_dir)
        # 删除多余文件
        for rel in deleted:
            fp = os.path.join(app_dir, rel.replace("/", os.sep))
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass
        return None
    except Exception as e:
        # 回滚：从备份恢复
        try:
            if os.path.isdir(backup_dir):
                for root, dirs, files in os.walk(backup_dir):
                    for fn in files:
                        rel = os.path.relpath(os.path.join(root, fn), backup_dir).replace("\\", "/")
                        dst = os.path.join(app_dir, rel.replace("/", os.sep))
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(os.path.join(root, fn), dst)
        except Exception:
            pass
        return str(e)


def _schedule_delta_update(delta_zip, manifest, app_dir, backup_dir):
    """Validate a delta and schedule transactional replacement outside the app."""
    import zipfile
    try:
        with zipfile.ZipFile(delta_zip) as zf:
            names = [n for n in zf.namelist() if n != "delete_list.json" and not n.endswith("/")]
            for name in names:
                info = manifest.get("files", {}).get(name) or {}
                expected = info.get("sha256")
                if not expected or hashlib.sha256(zf.read(name)).hexdigest() != expected:
                    return "校验失败: " + name
            deleted = []
            if "delete_list.json" in zf.namelist():
                deleted = json.loads(zf.read("delete_list.json").decode("utf-8")).get("deleted", [])

        exe_name = os.path.basename(sys.executable)
        exe_stem = os.path.splitext(exe_name)[0]
        stage_dir = tempfile.mkdtemp(prefix="wenmo_delta_stage_")
        script = os.path.join(tempfile.gettempdir(), "wenmo_apply_delta_%s.ps1" % os.getpid())
        changed_json = json.dumps(names, ensure_ascii=False)
        deleted_json = json.dumps(deleted, ensure_ascii=False)
        lines = [
            "$ErrorActionPreference='Stop'",
            "$zip='" + delta_zip.replace("'", "''") + "'",
            "$dst='" + app_dir.replace("'", "''") + "'",
            "$stage='" + stage_dir.replace("'", "''") + "'",
            "$backup='" + backup_dir.replace("'", "''") + "'",
            "$changed=ConvertFrom-Json @'\n" + changed_json + "\n'@",
            "$deleted=ConvertFrom-Json @'\n" + deleted_json + "\n'@",
            "Start-Sleep -Seconds 2",
            "Get-Process -Name '" + exe_stem.replace("'", "''") + "' -ErrorAction SilentlyContinue | Stop-Process -Force",
            "Start-Sleep -Milliseconds 700",
            "try {",
            "  New-Item -ItemType Directory -Force -Path $stage,$backup | Out-Null",
            "  Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force",
            "  foreach($rel in @($changed)+@($deleted)) { $old=Join-Path $dst $rel; if(Test-Path -LiteralPath $old){ $bak=Join-Path $backup $rel; New-Item -ItemType Directory -Force -Path (Split-Path $bak) | Out-Null; Copy-Item -LiteralPath $old -Destination $bak -Force } }",
            "  foreach($rel in $changed) { $src=Join-Path $stage $rel; $out=Join-Path $dst $rel; New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null; Copy-Item -LiteralPath $src -Destination $out -Force }",
            "  foreach($rel in $deleted) { $out=Join-Path $dst $rel; if(Test-Path -LiteralPath $out){ Remove-Item -LiteralPath $out -Force } }",
            "} catch {",
            "  Get-ChildItem -LiteralPath $backup -Recurse -File | ForEach-Object { $rel=$_.FullName.Substring($backup.Length+1); $out=Join-Path $dst $rel; New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null; Copy-Item -LiteralPath $_.FullName -Destination $out -Force }",
            "  Add-Content -Path (Join-Path $env:TEMP 'wenmo_update_error.txt') -Value $_.Exception.Message -Encoding UTF8",
            "}",
            "Start-Process -FilePath (Join-Path $dst '" + exe_name.replace("'", "''") + "')",
        ]
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write("\r\n".join(lines))
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return None
    except Exception as e:
        return str(e)


def try_delta_update(progress_cb=None):
    """尝试增量更新。返回 (成功标志, 信息)。失败时调用方走全量兜底。
    progress_cb(downloaded, total)：下载 delta 时上报进度。"""
    if os.environ.get("WENMO_ALLOW_UNSIGNED_DELTA_UPDATE") != "1":
        return False, "增量包尚无客户端可验证的发布者签名，已改用代码签名安装包"
    try:
        info = _latest_release_info()
        if not info:
            return False, "无最新版本信息"
        new_version = info.get("version", "")
        if not _version_gt(new_version, APP_VERSION):
            return False, "无新版本"
        # 相邻版本判断：新版本 == 当前版本 + 1（如 1.0.4 → 1.0.5）
        try:
            cur = [int(x) for x in APP_VERSION.split(".")]
            new = [int(x) for x in new_version.split(".")]
            adjacent = (new[0] == cur[0] and new[1] == cur[1] and new[2] == cur[2] + 1)
        except Exception:
            adjacent = False
        if not adjacent:
            return False, "跨版本升级，走全量"
        manifest = download_manifest(info.get("assets", []))
        if not manifest:
            return False, "无 manifest.json，走全量"
        to_download, to_delete, same = compare_files(manifest)
        if not to_download:
            return True, "文件已是最新（相同 %d 个）" % same
        delta_zip = download_delta(new_version, progress_cb=progress_cb)
        if not delta_zip:
            return False, "无 delta 包，走全量"
        err = apply_delta_update(delta_zip, manifest)
        if err:
            return False, "delta 应用失败: %s" % err
        return True, "增量更新完成（跳过 %d 个相同文件，更新 %d 个，删除 %d 个）" % (same, len(to_download), len(to_delete))
    except Exception as e:
        return False, "增量更新异常: %s" % e


def check_update(timeout=10):
    """检查是否有新版本。返回 None（无更新/失败）或 {version, notes, url}"""
    # Gitee 与 GitHub 并行查（国内优先、海外兜底）：谁先有结果用谁，避免串行 15s+15s
    info = _latest_release_info()
    if info:
        # 只有远端版本高于当前版本才算有更新（避免版本相同也提示）
        if _version_gt(info.get("version", ""), APP_VERSION):
            # 相邻版本（如 1.0.4 → 1.0.5）且有 delta 包 → 优先返回增量包地址（小几十 MB）
            # 而不是全量 update.zip（190MB+）。前端按 url 下载，delta 下载走 download_delta。
            try:
                if os.environ.get("WENMO_ALLOW_UNSIGNED_DELTA_UPDATE") != "1":
                    return info
                _cur = [int(x) for x in APP_VERSION.split(".")]
                _new = [int(x) for x in info.get("version", "").split(".")]
                if len(_cur) >= 3 and len(_new) >= 3 and _new[0] == _cur[0] and _new[1] == _cur[1] and _new[2] == _cur[2] + 1:
                    for _a in (info.get("assets") or []):
                        _an = _a.get("name", "")
                        if _an == "delta-v%s.zip" % info["version"] and _a.get("browser_download_url"):
                            info["delta_url"] = _a["browser_download_url"]
                            info["delta_name"] = _an
                            info["delta"] = True
                            break
            except Exception:
                pass
            return info
        return None
    # 自建服务器方案
    if not UPDATE_URL:
        return None
    try:
        req = urllib.request.Request(UPDATE_URL + "/update.json", method="GET",
                                     headers={"User-Agent": "wenmo-updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("version") and data["version"] != APP_VERSION:
            # 版本号比较（简单语义化比较）
            try:
                cur = [int(x) for x in APP_VERSION.split(".")]
                new = [int(x) for x in data["version"].split(".")]
                if new <= cur:
                    return None
            except Exception:
                return None
            return {"version": data["version"], "notes": data.get("notes", ""),
                    "url": data["url"], "sha256": data.get("sha256", "")}
    except Exception:
        return None
    return None


def _sha256(path, chunk=65536):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _probe_size(url, timeout=10):
    """HEAD 请求探测文件大小（国内 GitHub CDN 常不返回 Content-Length，
    用 HEAD 兜底取 total，供进度条真实计算）。返回 int 或 0（未知）。"""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "wenmo-updater"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def _download_with_progress(url, dest_path, progress_cb=None, retries=3, timeout=300):
    """智能下载：真实字节计算进度（根治 0% 卡死）。
    - 优先用响应 Content-Length；缺失则先 HEAD 探测（_probe_size）
    - 仍未知 → total=0，进度回调仍按已下载字节上报（前端显示"已下载 X MB"而非卡 0%）
    - 3 次重试（瞬断重连），失败抛异常
    返回 None（成功）。"""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wenmo-updater"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                if total <= 0:
                    total = _probe_size(url)          # HEAD 兜底拿真实大小
                downloaded = 0
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            # 永远用真实字节算进度：total 已知 → 百分比；未知 → 传字节让前端显示 MB
                            progress_cb(downloaded, total if total > 0 else 0)
            return None                               # 成功
        except Exception as e:
            last_err = e
            # 部分下载 → 删掉重来（无断点续传，但重试至少能续）
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
            except Exception:
                pass
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))       # 退避重试
    raise last_err or RuntimeError("下载失败")


def _verify_authenticode(path):
    """Require a valid signature pinned to Wenmo's build certificate."""
    if os.name != "nt":
        return False, "Authenticode verification requires Windows"
    script = (
        "& { param([string]$p) "
        "$s=Get-AuthenticodeSignature -LiteralPath $p; "
        "[pscustomobject]@{Status=[string]$s.Status; Subject=[string]$s.SignerCertificate.Subject; "
        "Thumbprint=[string]$s.SignerCertificate.Thumbprint} "
        "| ConvertTo-Json -Compress }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script, path],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode:
            return False, (proc.stderr or proc.stdout or "signature check failed").strip()
        result = json.loads((proc.stdout or "{}").strip())
        if result.get("Status") != "Valid":
            return False, "Authenticode status: %s" % (result.get("Status") or "unknown")
        policy = {}
        policy_path = os.path.join(
            os.environ.get("WENMO_RES_DIR") or os.path.dirname(os.path.abspath(__file__)),
            "signing_policy.json")
        try:
            with open(policy_path, encoding="utf-8") as handle:
                policy = json.load(handle)
        except Exception:
            policy = {}
        expected_subject = (os.environ.get("WENMO_SIGNING_SUBJECT", "").strip()
                            or str(policy.get("subject") or "").strip()).lower()
        expected_thumbprint = (os.environ.get("WENMO_SIGNING_THUMBPRINT", "").strip()
                               or str(policy.get("thumbprint") or "").strip())
        expected_thumbprint = "".join(c for c in expected_thumbprint.lower() if c.isalnum())
        subject = str(result.get("Subject") or "")
        thumbprint = "".join(c for c in str(result.get("Thumbprint") or "").lower()
                             if c.isalnum())
        if not expected_thumbprint and getattr(sys, "frozen", False):
            return False, "packaged updater is missing its pinned publisher thumbprint"
        if expected_thumbprint and thumbprint != expected_thumbprint:
            return False, "publisher certificate thumbprint mismatch"
        if expected_subject and expected_subject not in subject.lower():
            return False, "publisher mismatch: %s" % subject
        return True, "%s [%s]" % (subject, thumbprint)
    except Exception as exc:
        return False, str(exc)


def download_update(url, expected_sha256="", progress_cb=None):
    """下载新版本到临时目录，校验 sha256。返回 (exe路径, 错误或None)"""
    try:
        if not expected_sha256:
            return None, "更新包缺少 SHA-256 校验，已拒绝安装"
        if url.lower().endswith(".zip") and os.environ.get("WENMO_ALLOW_UNSIGNED_ZIP_UPDATE") != "1":
            return None, "ZIP 更新包没有可验证的发布者签名，已拒绝；请使用代码签名安装包"
        tmp = tempfile.mkdtemp(prefix="wenmo_update_")
        fname = "问墨_update.zip" if url.lower().endswith(".zip") else "问墨_setup.exe"
        exe_path = os.path.join(tmp, fname)
        # 智能下载：真实字节进度（根治 0% 卡死）+ 3 次重试
        _download_with_progress(url, exe_path, progress_cb=progress_cb)
        if expected_sha256:
            actual = _sha256(exe_path)
            if actual != expected_sha256:
                return None, '校验失败（sha256 不匹配）'
        if exe_path.lower().endswith(".exe"):
            signature_ok, signature_detail = _verify_authenticode(exe_path)
            if not signature_ok:
                return None, "发布者签名验证失败：" + signature_detail
        # 去 MOTW（Mark-of-the-Web），防 SmartScreen 拦截；不影响 sha256 校验
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "& { param([string]$p) Unblock-File -LiteralPath $p }", exe_path],
                timeout=15, capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        return exe_path, None
    except Exception as e:
        return None, str(e)


def apply_zip_update(zip_path):
    """文件级替换更新（方案 A）：解压 update.zip 覆盖安装目录，自动重启问墨。
    返回错误字符串或 None。"""
    try:
        if getattr(sys, "frozen", False):
            app_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            app_dir = os.path.dirname(os.path.abspath(__file__))
        exe_name = os.path.basename(sys.executable) if getattr(sys, "frozen", False) else "问墨.exe"
        if not os.path.exists(os.path.join(app_dir, exe_name)):
            return "未找到程序文件: " + os.path.join(app_dir, exe_name)
        script = os.path.join(tempfile.gettempdir(), "wenmo_apply_update.ps1")
        exe_stem = os.path.splitext(exe_name)[0]
        lines = [
            "$ErrorActionPreference='Continue'",
            "Start-Sleep -Seconds 2",
            "Get-Process -Name '" + exe_stem + "' -ErrorAction SilentlyContinue | Stop-Process -Force",
            "Start-Sleep -Seconds 1",
            # 防御性备份（内容分离方案 4.7）：解压前备份数据目录 content（用户自定义）
            "$dataDir = Join-Path $env:APPDATA ''问墨''",
            "$contentDir = Join-Path $dataDir ''content''",
            "if (Test-Path $contentDir) {",
            "  $bakDir = Join-Path $dataDir (''content_backup_'' + (Get-Date -Format ''yyyyMMdd_HHmmss''))",
            "  Copy-Item -Path $contentDir -Destination $bakDir -Recurse -Force",
            "}",
            "$zip = '" + zip_path.replace("'", "''") + "'",
            "$dst = '" + app_dir.replace("'", "''") + "'",
            "try { Expand-Archive -Path $zip -DestinationPath $dst -Force -ErrorAction Stop }",
            "catch { Add-Content -Path (Join-Path $env:TEMP 'wenmo_update_error.txt') -Value ('更新解压失败: ' + $_.Exception.Message) -Encoding UTF8 }",
            "Start-Process -FilePath (Join-Path $dst '" + exe_name + "')",
        ]
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write("\r\n".join(lines))
        if _STATE_FILE:
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"pending_update": True}, f)
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        os._exit(0)
    except Exception as e:
        return str(e)
    return None


def apply_update(exe_path):
    """应用更新：启动安装包（或自替换），退出当前进程。
    这里用「启动新安装包 + 退出」方式（安装包负责覆盖自身）。"""
    try:
        # 记录"更新后启动"标记，让新版本知道是更新来的
        if _STATE_FILE:
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"pending_update": os.path.basename(exe_path)}, f)
        # 启动安装包：静默安装（无向导界面），带 --updated 参数
        subprocess.Popen([exe_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "--updated"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # 退出当前
        os._exit(0)
    except Exception as e:
        return str(e)
    return None


if __name__ == "__main__":
    # 自测
    print("APP_VERSION:", APP_VERSION)
    print("UPDATE_URL:", UPDATE_URL or "（未配置，跳过检查）")
    info = check_update()
    print("检查结果:", info if info else "无更新")


_DOWNLOADED_PATH = None


def set_downloaded_path(path):
    """记录已下载的安装包路径（供 apply 使用）"""
    global _DOWNLOADED_PATH
    _DOWNLOADED_PATH = path


def get_downloaded_path():
    """获取已下载的安装包路径"""
    return _DOWNLOADED_PATH
