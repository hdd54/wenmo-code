# -*- coding: utf-8 -*-
"""自动更新模块：启动时检查更新 → 下载 → 自替换。
更新源：UPDATE_URL/update.json，格式：
{
  "version": "1.1.0",
  "notes": "修复了xxx，新增了yyy",
  "url": "https://example.com/问墨_v1.1.0.exe",   # 完整安装包
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

# 更新源：GitHub Releases 或自建服务器
# GitHub 方案：设置 GITHUB_REPO = "用户名/仓库名"，自动用 GitHub Releases API
# 自建服务器方案：设置 UPDATE_URL（指向含 update.json 的目录）
GITHUB_REPO = os.environ.get("WENMO_GITHUB_REPO", "").strip() or "hdd54/wenmo-code"
UPDATE_URL = os.environ.get("WENMO_UPDATE_URL", "").rstrip("/")
APP_VERSION = "1.0.1"   # 当前版本（与打包脚本同步）

_STATE_FILE = None   # 初始化时设置


def set_state_file(path):
    """设置更新状态文件路径（%APPDATA%/问墨/update_state.json）"""
    global _STATE_FILE
    _STATE_FILE = path


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
        # 找 windows 安装包 asset（install.exe 或 zip）
        assets = data.get("assets", [])
        target = None
        for a in assets:
            name = a.get("name", "")
            if "install" in name.lower() or "setup" in name.lower() or name.endswith(".exe"):
                target = a
                break
        if not target:
            for a in assets:
                if a.get("name", "").endswith(".zip"):
                    target = a
                    break
        if not target:
            return None
        return {"version": version, "notes": data.get("body", ""),
                "url": target.get("browser_download_url", ""),
                "sha256": "", "asset_name": target.get("name", "")}
    except Exception:
        return None


def check_update(timeout=10):
    """检查是否有新版本。返回 None（无更新/失败）或 {version, notes, url}"""
    # GitHub 方案优先
    info = _github_latest_release()
    if info:
        return info
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


def download_update(url, expected_sha256=""):
    """下载新版本到临时目录，校验 sha256。返回 (exe路径, 错误或None)"""
    try:
        tmp = tempfile.mkdtemp(prefix="wenmo_update_")
        exe_path = os.path.join(tmp, "问墨_setup.exe")
        req = urllib.request.Request(url, headers={"User-Agent": "wenmo-updater"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(exe_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
        if expected_sha256:
            actual = _sha256(exe_path)
            if actual != expected_sha256:
                return None, "校验失败（sha256 不匹配）"
        return exe_path, None
    except Exception as e:
        return None, str(e)


def apply_update(exe_path):
    """应用更新：启动安装包（或自替换），退出当前进程。
    这里用「启动新安装包 + 退出」方式（安装包负责覆盖自身）。"""
    try:
        # 记录"更新后启动"标记，让新版本知道是更新来的
        if _STATE_FILE:
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"pending_update": os.path.basename(exe_path)}, f)
        # 启动安装包（带 --updated 参数）
        subprocess.Popen([exe_path, "--updated"],
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
