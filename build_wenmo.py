# -*- coding: utf-8 -*-
"""问墨·code PyInstaller 打包脚本。
用法：python build_wenmo.py
产出：dist/问墨/（文件夹，可压缩为 zip 分发或做成安装包）
包含：gui_server + 前端资源 + 插件 + 技能 + updater
排除：llama 模型 / workspace / history / files / deps股票模块（运行时在 %APPDATA% 创建）
"""
import os
import shutil
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "dist")
BUILD = os.path.join(BASE, "build")

# 打包用 Python：优先干净 venv（控制体积，避免 Anaconda 全量污染）
VENV_PY = os.environ.get("WENMO_VENV_PY") or r"C:\Users\25300\AppData\Local\Temp\opencode\wenmo_venv\Scripts\python.exe"
PYTHON = VENV_PY if os.path.isfile(VENV_PY) else sys.executable

# 需要打包进 exe 的资源（相对路径，会被复制到 dist 目录）
RESOURCE_DIRS = ["gui", "plugins", "skills", "mcp-servers", "apps"]
RESOURCE_FILES = ["providers.json", "settings.json", "mcp.json", "projects.json",
                  "memory_graph.py", "updater.py", "packaging_env.py",
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
        "--exclude-module", "matplotlib.backends.backend_qt5agg",
        "--exclude-module", "matplotlib.backends.backend_qtagg",
        "--add-data", f"{os.path.join(BASE, 'gui')};gui",
        "--add-data", f"{os.path.join(BASE, 'plugins')};plugins",
        "--add-data", f"{os.path.join(BASE, 'skills')};skills",
        "--add-data", f"{os.path.join(BASE, 'apps')};apps",
        # 隐藏导入（插件动态加载）
        "--hidden-import", "docx", "--hidden-import", "openpyxl",
        "--hidden-import", "pptx", "--hidden-import", "fitz",
        "--hidden-import", "pypdf", "--hidden-import", "markitdown",
        "--hidden-import", "latex2mathml", "--hidden-import", "matplotlib",
        "--hidden-import", "mcp", "--hidden-import", "uvicorn",
        entry,
    ]
    print("  ", " ".join(cmd))
    r = subprocess.run(cmd, cwd=BASE)
    if r.returncode != 0:
        print("!! PyInstaller 失败")
        sys.exit(1)

    print("\n=== 3. 复制配置文件到 dist ===")
    app_dir = os.path.join(DIST, "问墨")
    for f in RESOURCE_FILES:
        src = os.path.join(BASE, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(app_dir, f))
    # 数据目录（默认项目用）
    for d in ["history", "files", "workspace", "deps"]:
        os.makedirs(os.path.join(app_dir, d), exist_ok=True)

    print("\n=== 4. 生成版本信息 ===")
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(app_dir) for f in fs) / 1e6
    print(f"  打包完成: dist/问墨/ ({size:.0f} MB)")
    print("  分发: 将 dist/问墨 压缩为 zip，或做成 Inno Setup 安装包")
    print("  更新: 部署 update.json 到你的服务器")

if __name__ == "__main__":
    build()
