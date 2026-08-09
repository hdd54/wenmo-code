# 问墨·code

以墨载智，以问启思。一款本地优先的 AI 聊天 / 编码 Agent，支持多模型供应商、371+ 技能库、插件系统、多用户 GitHub 登录与自动更新。

## ✨ 功能特性

- **多模型供应商**：DeepSeek、OpenCode Zen、通义千问、Kimi、智谱 GLM、硅基流动、Ollama 本地 GGUF 等 10+ 家
- **AI 编码 Agent**：终端沙箱（危险命令拦截）、代码审查（阿里 open-code-review）、checkpoint 快照回滚、repo 扫描
- **371+ 技能库**：整合 opencode / ECC / Anthropic Skills 生态，按需加载、中文触发词匹配
- **插件系统**：31 个内置插件（文档转换 markitdown、股票分析、OCR 审查、PDF/PPT/Word 生成等）
- **记忆图**：跨会话语义召回（TF-IDF），让 AI 越用越懂你
- **多用户登录**：GitHub OAuth，每账号独立数据目录
- **自动更新**：GitHub Releases 检查，启动时提示新版本
- **上下文优化**：headroom JSON 压缩（省 40%+ token）、长对话自动压缩、工具按需注入、前缀缓存

## 📦 安装

### 方式一：一键安装包（推荐）
下载 `问墨_installer.exe`，双击安装，桌面生成快捷方式。

### 方式二：源码运行
```bash
# 需要 Python 3.10+
pip install -r requirements.lock
python gui_server.py
# 浏览器打开 http://localhost:8000
```

### 方式三：直接运行（Windows）
双击项目根目录 `启动问墨.bat`（自动用 Anaconda Python 启动）。

## 🔐 登录（GitHub OAuth）

问墨支持 GitHub 账号登录，每账号数据独立（对话历史 / 工作区 / 文件互不干扰）。

1. 在 [GitHub 开发者设置](https://github.com/settings/developers) 创建 OAuth App：
   - Homepage URL: `http://localhost:8000`
   - Callback URL: `http://localhost:8000/api/auth/github/callback`
2. 配置凭据（写入 `settings.json` 或环境变量）：
```json
{
  "github_client_id": "你的ClientID",
  "github_client_secret": "你的ClientSecret",
  "base_url": "http://localhost:8000"
}
```
3. 重启后点击界面右上角「登录」→ GitHub 授权 → 自动回跳

> 详细说明见 `README_AUTH.md`

## 🔄 自动更新

问墨通过 GitHub Releases 检查更新：
- 发布新版本时，创建 Release（Tag 高于当前版本）+ 上传 `问墨_installer.exe`
- 用户启动问墨时自动检测 → 弹窗提示 → 点击下载安装
- 更新不覆盖用户数据（数据存于独立目录）

## 🗂 项目结构

```
agent-tutorial/
├── gui_server.py          # 服务器主程序（FastAPI）
├── gui/static/            # 前端（原生 JS，无框架）
├── auth.py                # 用户登录（GitHub OAuth）
├── updater.py             # 自动更新
├── memory_graph.py        # 记忆图（跨会话召回）
├── history.py             # 对话历史存储（按项目/用户分目录）
├── plugins/               # 插件系统（31 个）
├── skills/                # 技能库（371 个）
├── mcp_client.py          # MCP 客户端（10+ 工具集）
├── deps/                  # 可选依赖（股票分析等）
└── build_wenmo.py         # PyInstaller 打包脚本
```

## 🛠 开发 / 打包

```bash
# 打包（用干净 venv 避免体积膨胀）
C:\...\wenmo_venv\Scripts\python.exe build_wenmo.py
# 生成安装包（需 Inno Setup 6）
ISCC.exe wenmo_installer.iss
```

## 📄 License

Apache-2.0（第三方组件见各依赖 LICENSE）
