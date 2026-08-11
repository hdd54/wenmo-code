# -*- coding: utf-8 -*-
"""问墨·code PyInstaller 打包脚本（修复版：收集 latex2mathml / docx 数据文件 + 问墨 Logo 图标）。
用法：python build_wenmo.py
产出：dist/问墨/（文件夹，可压缩为 zip 分发或做成安装包）
包含：gui_server + 前端资源 + 插件 + 技能 + updater
排除：llama 模型 / workspace / history / files / deps股票模块（运行时在 %APPDATA% 创建）
图标：使用问墨现有 Logo（gui/static/logo.png 转出的 问墨.ico）
"""
import json
import io
import os
import re
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "dist")
BUILD = os.path.join(BASE, "build")
ICON = os.path.join(BASE, "问墨.ico")   # 问墨 Logo 图标（exe 图标）

# 打包用 Python：优先干净 venv（控制体积，避免 Anaconda 全量污染）
VENV_PY = os.environ.get("WENMO_VENV_PY", "")
PYTHON = VENV_PY if os.path.isfile(VENV_PY) else sys.executable

# 需要打包进 exe 的资源（相对路径，会被复制到 dist 目录）
RESOURCE_DIRS = ["gui", "plugins", "skills", "mcp-servers", "apps"]
RESOURCE_FILES = ["settings.json", "mcp.json", "projects.json",
                  "memory_graph.py", "updater.py", "packaging_env.py", "auth.py",
                  "skills_loader.py", "plugins_loader.py", "mcp_client.py",
                  "extension_packages.py",
                  "cluster.py", "history.py", "pricing.py", "websearch_mcp_server.py",
                  "file_mcp_server.py", "ppt_pipeline_mcp_server.py"]

EXTENSION_UPDATE_FILES = {
    "mcp.json", "mcp.local.json", "websearch_mcp_server.py",
    "file_mcp_server.py", "ppt_pipeline_mcp_server.py",
}
EXTENSION_UPDATE_PREFIXES = (
    "seed/plugins/", "seed/skills/", "seed/extensions/",
    "_internal/seed/plugins/", "_internal/seed/skills/",
    "_internal/seed/extensions/",
)


def _is_extension_update_payload(path):
    rel = str(path).replace("\\", "/").lstrip("./")
    return rel in EXTENSION_UPDATE_FILES or rel.startswith(EXTENSION_UPDATE_PREFIXES)

def build():
    print("=== 1. 清理旧构建 ===")
    for d in [DIST, BUILD]:
        if os.path.isdir(d):
            # 保留 prev_manifest.json（增量更新对比用，放 dist 里会被 rmtree 误删）
            _prev_bak = None
            _prev_p = os.path.join(d, "prev_manifest.json")
            if os.path.isfile(_prev_p):
                try:
                    import shutil as _sh
                    _prev_bak = os.path.join(BASE, "_prev_manifest_bak.json")
                    _sh.copy2(_prev_p, _prev_bak)
                except Exception:
                    pass
            shutil.rmtree(d)
            if _prev_bak and os.path.isfile(_prev_bak):
                try:
                    os.makedirs(d, exist_ok=True)
                    import shutil as _sh
                    _sh.copy2(_prev_bak, _prev_p)
                    os.remove(_prev_bak)
                except Exception:
                    pass

    print("=== 2. PyInstaller 打包（onedir）===")
    entry = os.path.join(BASE, "gui_server.py")
    cmd = [
        PYTHON, "-m", "PyInstaller",
        "--name", "问墨",
        "--onedir",
        "--noconfirm",
        "--clean",
        "--windowed",          # GUI 应用，无控制台
        "--icon", ICON,        # 问墨 Logo 图标（exe / 快捷方式 / 任务栏）
        "--exclude-module", "matplotlib.backends.backend_qt5agg",
        "--exclude-module", "matplotlib.backends.backend_qtagg",
        "--add-data", f"{os.path.join(BASE, 'gui')};seed/gui",
        "--add-data", f"{os.path.join(BASE, 'plugins')};seed/plugins",
        "--add-data", f"{os.path.join(BASE, 'skills')};seed/skills",
        "--add-data", f"{os.path.join(BASE, 'apps')};seed/apps",
        "--add-data", f"{os.path.join(BASE, 'sandbox')};sandbox",
        "--add-data", f"{os.path.join(BASE, 'signing_policy.json')};.",
        "--add-data", f"{ICON};.",   # 问墨图标随包分发（桌面窗口标题栏图标）
        # 隐藏导入（插件动态加载）
        "--hidden-import", "docx", "--hidden-import", "openpyxl",
        "--hidden-import", "pptx", "--hidden-import", "fitz",
        "--hidden-import", "pypdf", "--hidden-import", "markitdown",
        "--hidden-import", "latex2mathml", "--hidden-import", "matplotlib",
        "--hidden-import", "mcp", "--hidden-import", "uvicorn",
        # 收集包内数据文件（latex2mathml 的符号表 / python-docx 的默认模板，
        # PyInstaller 默认不收集非代码文件，缺了会导致运行时 FileNotFoundError）
        "--collect-data", "latex2mathml",
        "--collect-data", "docx",
        "--collect-all", "webview",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
        "--copy-metadata", "pythonnet",
        "--copy-metadata", "clr_loader",
   # pywebview 桌面窗口（平台后端/资源）
        "--hidden-import", "webview",
        entry,
    ]
    print("  ", " ".join(cmd))
    r = subprocess.run(cmd, cwd=BASE)
    if r.returncode != 0:
        print("!! PyInstaller 失败")
        sys.exit(1)

    print("\n=== 3. 复制配置文件到 dist（脱敏：不带 API key / 历史记录）===")
    app_dir = os.path.join(DIST, "问墨")

    def _sanitize_json(fname, scrub_keys):
        """复制后脱敏：把 JSON 里指定的敏感字段清空"""
        dst = os.path.join(app_dir, fname)
        if not os.path.isfile(dst):
            return
        try:
            with open(dst, encoding="utf-8") as f:
                obj = json.load(f)

            def _scrub(o):
                if isinstance(o, dict):
                    for k, v in list(o.items()):
                        if isinstance(v, str) and _is_sensitive(k, v):
                            o[k] = ""          # 清空明文 key/secret/token
                        else:
                            _scrub(v)
                elif isinstance(o, list):
                    for item in o:
                        _scrub(item)

            def _is_sensitive(k, v):
                """字段名含 key/token/secret/api_key 且值为明文（非 ${} 环境变量引用）→ 敏感"""
                kl = k.lower()
                if not any(s in kl for s in ("key", "token", "secret", "api_key")):
                    return False
                v = v.strip()
                if not v:
                    return False      # 空值：无需清空
                if v.startswith("${") or v.startswith("env:") or v.startswith("%"):
                    return False      # 环境变量引用：无明文、保留
                return True

            _scrub(obj)
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            print(f"  [脱敏] {fname}")
        except Exception as e:
            print(f"  [脱敏失败] {fname}: {e}")

    # 复制非敏感代码文件（历史数据文件不复制）
    for f in RESOURCE_FILES:
        src = os.path.join(BASE, f)
        if os.path.isfile(src):
            if f == "projects.json":
                continue   # 历史项目记录：不打包，首次启动引导创建空模板
            shutil.copy2(src, os.path.join(app_dir, f))

    # Never copy the developer's ignored providers.json: it may contain real,
    # DPAPI-bound credentials. Releases always start from the tracked empty template.
    provider_template = os.path.join(BASE, "providers.example.json")
    if not os.path.isfile(provider_template):
        raise RuntimeError("providers.example.json is required for a safe release")
    shutil.copy2(provider_template, os.path.join(app_dir, "providers.json"))

    # 配置脱敏：清空所有 API key / secret / token
    _sanitize_json("providers.json", {"api_key", "key", "secret", "token", "api_key_env"})
    _sanitize_json("settings.json", {"github_client_id", "github_client_secret", "api_key", "secret", "token"})
    _sanitize_json("mcp.json", {"LLM_API_KEY", "API_KEY", "api_key", "key", "secret", "token", "GITHUB_PERSONAL_ACCESS_TOKEN"})  # 子串匹配已覆盖全部，这里保留显式清单兼容


    # ---- 本地模型运行库：复制整个 llama 目录（llama-server.exe + 依赖的 51 个 dll，缺一不可）----
    _llama_src_dir = os.path.join(BASE, "llama")
    if os.path.isdir(_llama_src_dir):
        _llama_dst_dir = os.path.join(app_dir, "llama")
        os.makedirs(_llama_dst_dir, exist_ok=True)
        _n = 0
        for _f in os.listdir(_llama_src_dir):
            _fp = os.path.join(_llama_src_dir, _f)
            if os.path.isfile(_fp):
                shutil.copy2(_fp, os.path.join(_llama_dst_dir, _f))
                _n += 1
        print(f"  [llama] 已复制 {_n} 个运行库文件（llama-server.exe + 全部 dll）")

    # ---- 内置 MCP server（内置集成）：不再逐个打包 exe，改为 问墨.exe --mcp-server <名> 模式 ----
    # 可选 server 脚本由 RESOURCE_FILES 复制到程序目录；默认仅启用经过审计的 websearch。
    # 运行时 mcp_client 以 [问墨.exe --mcp-server xxx] 启动（打包版）或 [python xxx.py]（开发版）。
    # 体积 0 增加、打包时间 0 增加、依赖全在包内、进程隔离。
    # 数据目录（默认项目用；history 只建空目录，不携带任何对话历史）
    for d in ["history", "files", "workspace", "deps"]:
        os.makedirs(os.path.join(app_dir, d), exist_ok=True)
    # 空 projects.json 模板（避免打包版启动缺文件）
    projects_tpl = os.path.join(app_dir, "projects.json")
    if not os.path.isfile(projects_tpl):
        with open(projects_tpl, "w", encoding="utf-8") as f:
            json.dump({"projects": []}, f, ensure_ascii=False, indent=2)

    print("\n=== 4. 验证关键数据文件 ===")
    checks = [
        os.path.join(app_dir, "_internal", "latex2mathml", "unimathsymbols.txt"),
        os.path.join(app_dir, "问墨.exe"),
    ]
    for c in checks:
        ok = os.path.isfile(c)
        print(f"  [{'OK' if ok else 'MISSING'}] {c}")
        if not ok:
            print("!! 关键文件缺失，打包失败")
            sys.exit(1)

    print("\n=== 5. 生成 update.zip（文件级更新包，用于自动更新）===")
    import zipfile
    update_zip = os.path.join(DIST, "update.zip")
    if os.path.exists(update_zip):
        os.remove(update_zip)
    _zcount = 0
    with zipfile.ZipFile(update_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for _root, _dirs, _files in os.walk(app_dir):
            for _fn in _files:
                _fp = os.path.join(_root, _fn)
                _rel = os.path.relpath(_fp, app_dir)
                if _is_extension_update_payload(_rel):
                    continue
                zf.write(_fp, _rel)
                _zcount += 1
    _zsize = os.path.getsize(update_zip) / 1e6
    print(f"  update.zip: {_zsize:.0f} MB（{_zcount} 个文件）-> {update_zip}")

    print("\n=== 6. 版本信息 ===")
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(app_dir) for f in fs) / 1e6
    # 清理垃圾文件：技能库 .git（38MB）+ 各种 .bak 备份（10MB+）
    _removed_git = 0
    _removed_bak = 0
    for _root, _dirs, _files in os.walk(DIST):
        if ".git" in _dirs:
            shutil.rmtree(os.path.join(_root, ".git"), ignore_errors=True)
            _dirs.remove(".git")
            _removed_git += 1
        for _f in _files:
            if ".bak" in _f.lower():
                try:
                    os.remove(os.path.join(_root, _f))
                    _removed_bak += 1
                except OSError:
                    pass
    if _removed_git or _removed_bak:
        print(f"  已清理 {_removed_git} 个 .git 目录 + {_removed_bak} 个 .bak 备份")
    print(f"  打包完成: dist/问墨/ ({size:.0f} MB)")
    print("  分发: 将 dist/问墨 压缩为 zip，或做成 Inno Setup 安装包")
    print("  更新: 部署 update.json 到你的服务器")

    print("\n=== 7. 增量更新：manifest + delta（发布侧）===")
    version = _read_version()
    print("  当前版本: %s" % version)
    new_manifest = generate_manifest(app_dir, version)
    prev_path = os.path.join(BASE, "prev_manifest.json")   # 放项目根（dist 会被清理，这里保留才能对比 delta）
    if os.path.isfile(prev_path):
        try:
            with open(prev_path, encoding="utf-8") as f:
                prev_manifest = json.load(f)
            print("  上一版 manifest: v%s" % prev_manifest.get("version", "?"))
            generate_delta(prev_manifest, new_manifest, app_dir, version)
        except Exception as e:
            print("  delta 生成跳过: %s" % e)
    else:
        print("  无上一版 manifest，跳过 delta（首次发布：先上传 manifest.json，下次打包自动出 delta）")
    # 保存本次 manifest 供下次对比
    shutil.copy2(os.path.join(DIST, "manifest.json"), prev_path)
    print("  增量更新产物: manifest.json + delta-v%s.zip（客户端优先走 delta，找不到走全量 update.zip）" % version)


def _sha256(path, chunk=65536):
    """计算文件 sha256"""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _read_version():
    """从 updater.py 读取当前 APP_VERSION"""
    try:
        src = io.open(os.path.join(BASE, "updater.py"), encoding="utf-8").read()
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', src)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"


def generate_manifest(app_dir, version):
    """生成 manifest.json：每个文件的 sha256 + 大小（增量更新三件套①）"""
    manifest = {"version": version, "files": {}}
    for root, dirs, files in os.walk(app_dir):
        for fn in files:
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, app_dir).replace("\\", "/")
            if _is_extension_update_payload(rel):
                continue
            manifest["files"][rel] = {"sha256": _sha256(fp), "size": os.path.getsize(fp)}
    mpath = os.path.join(DIST, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("  manifest.json: %.1f KB（%d 个文件指纹）" % (os.path.getsize(mpath) / 1024, len(manifest["files"])))
    return manifest


def generate_delta(prev_manifest, new_manifest, app_dir, version):
    """对比新旧 manifest 生成 delta-vX.Y.Z.zip（增量更新三件套②）"""
    import zipfile
    prev_files = prev_manifest.get("files", {})
    new_files = new_manifest.get("files", {})
    changed = [rel for rel, info in new_files.items()
               if rel not in prev_files or prev_files[rel].get("sha256") != info.get("sha256")]
    deleted = [rel for rel in prev_files if rel not in new_files]
    if not changed and not deleted:
        print("  无差异，无需 delta")
        return None
    delta_name = "delta-v%s.zip" % version
    delta_path = os.path.join(DIST, delta_name)
    with zipfile.ZipFile(delta_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in changed:
            fp = os.path.join(app_dir, rel.replace("/", os.sep))
            if os.path.isfile(fp):
                zf.write(fp, rel)
        zf.writestr("delete_list.json", json.dumps(
            {"version": version, "deleted": deleted}, ensure_ascii=False, indent=1))
    print("  %s: %.2f MB（变更 %d，删除 %d）" % (delta_name, os.path.getsize(delta_path) / 1e6, len(changed), len(deleted)))
    return delta_path


def refresh_update_artifacts(previous_manifest_path=""):
    """Rebuild update artifacts after Authenticode signing changes the EXE bytes."""
    import glob
    import zipfile

    app_dir = os.path.join(DIST, "问墨")
    if not os.path.isfile(os.path.join(app_dir, "问墨.exe")):
        raise RuntimeError("signed application directory is missing")
    os.makedirs(DIST, exist_ok=True)
    update_zip = os.path.join(DIST, "update.zip")
    temporary = update_zip + ".tmp"
    if os.path.exists(temporary):
        os.remove(temporary)
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for root, _dirs, files in os.walk(app_dir):
            for filename in files:
                path = os.path.join(root, filename)
                relative = os.path.relpath(path, app_dir)
                if _is_extension_update_payload(relative):
                    continue
                archive.write(path, relative)
    os.replace(temporary, update_zip)

    version = _read_version()
    manifest = generate_manifest(app_dir, version)
    for stale in glob.glob(os.path.join(DIST, "delta-*.zip")):
        os.remove(stale)
    if previous_manifest_path and os.path.isfile(previous_manifest_path):
        with open(previous_manifest_path, encoding="utf-8") as handle:
            previous = json.load(handle)
        generate_delta(previous, manifest, app_dir, version)
    print("Refreshed signed update artifacts for v%s" % version)


if __name__ == "__main__":
    if "--refresh-update-artifacts" in sys.argv:
        previous = ""
        if "--previous-manifest" in sys.argv:
            index = sys.argv.index("--previous-manifest")
            if index + 1 >= len(sys.argv):
                raise SystemExit("--previous-manifest requires a path")
            previous = sys.argv[index + 1]
        refresh_update_artifacts(previous)
    else:
        build()
