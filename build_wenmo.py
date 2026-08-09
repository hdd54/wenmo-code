# -*- coding: utf-8 -*-
"""问墨·code PyInstaller 打包脚本（修复版：收集 latex2mathml / docx 数据文件 + 问墨 Logo 图标）。
用法：python build_wenmo.py
产出：dist/问墨/（文件夹，可压缩为 zip 分发或做成安装包）
包含：gui_server + 前端资源 + 插件 + 技能 + updater
排除：llama 模型 / workspace / history / files / deps股票模块（运行时在 %APPDATA% 创建）
图标：使用问墨现有 Logo（gui/static/logo.png 转出的 问墨.ico）
"""
import json
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "dist")
BUILD = os.path.join(BASE, "build")
ICON = os.path.join(BASE, "问墨.ico")   # 问墨 Logo 图标（exe 图标）

# 打包用 Python：优先干净 venv（控制体积，避免 Anaconda 全量污染）
VENV_PY = os.environ.get("WENMO_VENV_PY") or r"C:\Users\25300\AppData\Local\Temp\opencode\wenmo_venv\Scripts\python.exe"
PYTHON = VENV_PY if os.path.isfile(VENV_PY) else sys.executable

# 需要打包进 exe 的资源（相对路径，会被复制到 dist 目录）
RESOURCE_DIRS = ["gui", "plugins", "skills", "mcp-servers", "apps"]
RESOURCE_FILES = ["providers.json", "settings.json", "mcp.json", "projects.json",
                  "memory_graph.py", "updater.py", "packaging_env.py", "auth.py",
                  "skills_loader.py", "plugins_loader.py", "mcp_client.py",
                  "cluster.py", "history.py", "pricing.py", "websearch_mcp_server.py",
                  "file_mcp_server.py", "mcp_demo_server.py", "ppt_pipeline_mcp_server.py"]

def build():
    print("=== 1. 清理旧构建 ===")
    for d in [DIST, BUILD]:
        if os.path.isdir(d):
            shutil.rmtree(d)

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
        "--add-data", f"{os.path.join(BASE, 'gui')};gui",
        "--add-data", f"{os.path.join(BASE, 'plugins')};plugins",
        "--add-data", f"{os.path.join(BASE, 'skills')};skills",
        "--add-data", f"{os.path.join(BASE, 'apps')};apps",
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

    # 配置脱敏：清空所有 API key / secret / token
    _sanitize_json("providers.json", {"api_key", "key", "secret", "token", "api_key_env"})
    _sanitize_json("settings.json", {"github_client_id", "github_client_secret", "api_key", "secret", "token"})
    _sanitize_json("mcp.json", {"LLM_API_KEY", "API_KEY", "api_key", "key", "secret", "token", "GITHUB_PERSONAL_ACCESS_TOKEN"})  # 子串匹配已覆盖全部，这里保留显式清单兼容

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

    print("\n=== 5. 生成版本信息 ===")
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(app_dir) for f in fs) / 1e6
    print(f"  打包完成: dist/问墨/ ({size:.0f} MB)")
    print("  分发: 将 dist/问墨 压缩为 zip，或做成 Inno Setup 安装包")
    print("  更新: 部署 update.json 到你的服务器")

if __name__ == "__main__":
    build()
