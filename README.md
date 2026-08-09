# 问墨·code

以墨载智，以问启思。一款本地优先的 AI 聊天 / 编码 Agent，支持多模型供应商、371+ 技能库、插件系统与自动更新。

## ✨ 核心功能

### 🤖 AI 对话与编码
- **多模型供应商**：DeepSeek、OpenCode Zen、通义千问、Kimi、智谱 GLM、硅基流动、Ollama 本地 GGUF 等 10+ 家
- **流式输出**：打字机效果，思考过程（think）与工具调用实时显示
- **多步骤规划**：AI 自动输出步骤规划，状态栏显示当前进度与耗时
- **计划模式**：先规划 → 你确认 → 再执行（对标 opencode Plan/Build）
- **联网搜索**：可开关，AI 实时搜索后回答
- **模型对比**：同一问题让两个模型分别回答

### 🛠 编码 Agent 能力
- **终端沙箱**：执行命令前弹窗确认，危险命令（rm -rf、关机、格式化等）直接拦截
- **代码审查**：阿里 open-code-review 集成（`ocr_review`/`ocr_scan`），行级精确评论
- **Checkpoint 快照**：改动前快照 → diff 审计 → 一键回滚
- **代码分析**：AST 搜索、repo 扫描、代码库总览、token 统计
- **记忆图**：跨会话语义召回（TF-IDF），让 AI 越用越懂你
- **智能体委托**：把子任务交给独立模型并行处理

### 📦 插件系统（31 个内置插件）
| 类别 | 插件 |
|---|---|
| **文档生成** | Word（docx）、Excel（xlsx）、PPT（pptx）、PDF、简历生成、markitdown 任意转 Markdown |
| **文件操作** | 读写/编辑/搜索/批量重命名/ZIP 打包/文件交付（带下载链接）|
| **代码工具** | AST 搜索、repo 扫描、代码符号、git 操作、终端、环境检查、MATLAB 运行 |
| **数据/分析** | 股票分析（实时行情+AI 建议）、天气、图表绘制、网页抓取、URL 转 Markdown |
| **AI 能力** | 看图（see_image）、文本摘要/关键词/差异、委托子任务 |
| **系统** | 系统信息、磁盘用量、数学计算、JSON 处理、Inno Setup 打包 |

### 🧩 小应用（apps/，可自由拖动的悬浮窗口）
内置 3 个示例应用，**你也可以让问墨自己创建/修改小应用**（对话里描述需求即可生成 HTML 小应用）：
- **时钟**：桌面时钟 + 番茄计时 + 历史记录 + 媒体播放
- **股票监控**：实时行情悬浮窗
- **系统监控**：CPU/内存/磁盘实时图表

### 🔌 MCP 工具集（10 个服务器）
websearch（搜索）、filesystem（文件）、playwright（浏览器自动化）、github（GitHub 操作）、kb-gui（知识库）、page-agent（网页 GUI 控制）、ppt-pipeline（PPT 生成）等

### 🎓 技能库（371+ 技能）
整合 opencode / ECC / Anthropic Skills 生态：前端设计（anti-slop）、网络安全审计、测试驱动、系统调试、文档写作、各语言框架（Python/JS/Go/Rust/Django/React）等。按需加载、中文触发词自动匹配。

## 🔄 自动更新

问墨通过 GitHub Releases 检查更新（**无需登录**，启动时自动检查）：
- 发布新版本：创建 Release（Tag 高于当前版本）+ 上传 `问墨_installer.exe`
- 用户启动时自动检测 → 弹窗"发现新版本"（含变更日志）→ 点击下载安装
- 更新不覆盖用户数据（数据存于独立目录）

## 🛠 开发调试

- **服务器调试**：`python gui_server.py` → http://localhost:8000；日志见 `gui_server.log`
- **API 调试**：内置 `/api/*` REST 端点 + 前端可视化（供应商/模型/用量/计费面板）
- **自动修复**：模型输出异常 JSON 自动修复、工具调用参数校验、重复调用风暴抑制
- **自演化**：从对话中蒸馏经验教训（lessons.json），AI 越用越少犯错

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
双击项目根目录 `启动问墨.bat`。

## 🗂 项目结构

```
agent-tutorial/
├── gui_server.py          # 服务器主程序（FastAPI）
├── gui/static/            # 前端（原生 JS，无框架）
├── auth.py                # 用户认证模块（预留）
├── updater.py             # 自动更新
├── memory_graph.py        # 记忆图（跨会话召回）
├── history.py             # 对话历史存储（按项目/用户分目录）
├── plugins/               # 插件系统（31 个）
├── skills/                # 技能库（371 个）
├── apps/                  # 小应用（时钟/股票/系统监控）
├── mcp_client.py          # MCP 客户端（10+ 工具集）
├── deps/                  # 可选依赖（股票分析等）
└── build_wenmo.py         # PyInstaller 打包脚本
```

## 📄 License

Apache-2.0（第三方组件见各依赖 LICENSE）
