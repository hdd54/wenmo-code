# 问墨·code · WenMo Code

<div align="center">

**以墨载智，以问启思。本地优先的 AI 聊天 / 编码 Agent 桌面软件**

[![Version](https://img.shields.io/badge/version-v1.0.6-blue)]()
[![License](https://img.shields.io/badge/license-Apache--2.0-green)]()
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)]()
[![Skills](https://img.shields.io/badge/skills-373+-orange)]()
[![Plugins](https://img.shields.io/badge/plugins-34-purple)]()

支持多模型供应商、373+ 技能库、34 个内置插件、9 个 MCP 服务器，MMC功能，打包零密钥、更新签名验证，是一款**安全、可扩展、本地优先**的 AI 生产力工具。（MCP,插件,技能可以自行添加,初次下载因人而异但可以正常运行）

</div>

---

## ✨ 核心特性

### 🤖 多供应商 AI 对话

- **云端模型**：DeepSeek、OpenCode Zen、通义千问、Kimi、智谱 GLM、硅基流动等，按需切换
- **本地模型**：Ollama / llama.cpp 加载 GGUF 模型，离线可用
- **深度思考档位**：多档推理强度，轻量问答与深度分析自由切换
- **流式输出**：打字机效果，思考过程（think）与工具调用实时可见
- **发送队列**：多请求排队，不丢消息
- **图片识别**：视觉模型看图，截图报错直接丢给 AI 分析
- **公式转 Word**：LaTeX 公式一键转 OMML，插入 Word 文档
- **对话历史按项目分组**：多项目并行，上下文互不串扰

### 🛠 编码 Agent 能力

- **终端沙箱**：执行命令前弹窗确认，危险命令（rm -rf / 关机 / 格式化）直接拦截
- **文件系统**：读写、编辑、搜索、批量重命名、ZIP 打包，文件交付带下载/预览链接
- **代码分析**：AST 搜索、repo 扫描、代码库总览、token 统计
- **Checkpoint 快照**：改动前自动快照 → diff 审计 → 一键回滚
- **代码审查**：阿里 open-code-review 集成，行级精确评论
- **智能体委托**：把子任务交给独立模型并行处理，主模型不被拖垮
- **自我修复**：工具调用失败自动诊断重试，插件/MCP 异常自动修复
- **自演化**：从对话中蒸馏经验教训（lessons.json），越用越少犯错

### 🧩 技能库（373+ 技能 · 按需加载）

整合 opencode / ECC / Anthropic Skills 生态，按 **14 大类**组织，**按需加载不占上下文**（查目录≈免费，加载正文才消耗 token）：

| 分类 | 覆盖内容 |
|---|---|
| frontend / backend | React、Next.js、API 设计、后端架构、数据库 |
| data / lang | SQL、Python/JS/Go/Rust/Django 等语言与数据规范 |
| testing / security | TDD、代码评审、安全审计、凭据防护 |
| debug / git / devops | 系统化调试、Git 工作流、Docker 部署 |
| doc / research | 文档协作、文章写作、调研方法论 |
| agent / plan / perspective | Agent 编排、规划工作流、思维视角 |

### 📦 插件系统（34 个内置插件）

| 类别 | 插件 |
|---|---|
| **文档生成** | Word（docx）、Excel（xlsx）、PPT（pptx）、PDF、公式转 Word |
| **文件操作** | 读写、编辑、ZIP 打包、文件交付（带下载链接） |
| **代码工具** | 终端命令、AST 搜索、repo 扫描、Git 操作、Inno Setup 打包、exe 打包 |
| **数据/分析** | 图表绘制、系统信息、磁盘用量、数学计算、JSON 处理 |
| **AI 能力** | 看图（see_image）、智能体委托、文本摘要/关键词/差异 |

### 🔌 MCP 工具集（9 个服务器）

| MCP | 能力 |
|---|---|
| **websearch** | 多源联网搜索（必应+百度+搜狗聚合） |
| **file-mcp / filesystem-mcp** | 文件系统读写、zip 打包 |
| **playwright / playwright-mcp** | 浏览器自动化、网页截图 |
| **github-mcp** | GitHub 仓库/Release 操作 |
| **ppt-pipeline** | PPT 生成流水线 |
| **kb-gui** | 知识库图形界面 |
| **page-agent** | 网页 GUI 自动化控制（真实浏览器操作） |

### 🧩 小应用（打包内置：时钟 + 系统状态）

内置可自由拖动的悬浮小应用，**你也可以让问墨直接创建/修改小应用**（对话里描述需求即可生成 HTML 小应用）：

- **⏰ 时钟**：桌面时钟 + 番茄计时 + 历史记录 + 媒体播放
- **📊 系统状态**：CPU / 内存 / 磁盘实时监控图表

> v1.0.6 安装包内置以上 2 个核心小应用，后续版本可扩展。

### 🧠 智能上下文管理（opencode 式）

- **按需压缩**：上下文占用达配置窗口 90% 触发，早期消息折叠为结构化摘要（目标/细节/工作状态/下一步/相关文件），保留最近 15% 完整轮次
- **实测优先**：用上一轮真实 prompt_tokens 判定（含系统前缀），估算系数自动校准逼近原生 tokenizer
- **防误触发**：summary 标记防重复压缩 + 20k 缓冲预留 + 模型真实窗口校准

---

## 🔒 安全设计（v1.0.6 重点强化）

问墨·code 在打包与更新链路上做了**完整的安全加固**：

### 打包零密钥

- 安装包内 `providers.json` 的 API key **全部为空**——密钥只在用户自己的数据目录里，绝不随包分发
- 打包前**二进制扫描 fail-fast**：exe / PYZ 内嵌字符串也扫描，命中疑似密钥直接构建失败
- 配置脱敏双匹配：字段名 + 值 pattern 双重校验
- 开发版 `permissions=allow` 自动降级为 `ask`，不含开发环境授权

### 更新签名验证

- 全量更新包**强制 Authenticode 签名验证** + 发布者指纹（thumbprint）匹配 `signing_policy.json`，无信任根拒绝更新
- 哈希校验独立于元数据（checksums.sha256），杜绝同源篡改
- 增量更新排除固化资源（技能/插件/小应用/MCP 不随更新变动），**用户自由发挥的数据永不被覆盖**

### 运行时防护

- 更新/Worker API 加 **Host + Origin + Referer 三重校验**，防 DNS rebinding 静默攻击
- 危险命令拦截、终端执行弹窗确认
- 集群令牌去硬编码：环境变量 → settings → 运行时随机生成三级读取

---

## 📦 安装

### 方式一：一键安装包（推荐）

下载 Release 页面的 `Setup_问墨·code_v1.0.6.exe`，双击安装：

- Inno Setup 封装 · LZMA2 压缩 · **中文界面** · 桌面图标 · per-user 安装（干净卸载）
- 首次启动自动展开固化资源（小应用 / 技能库 / 插件 / MCP 配置）
- 安装后**在设置页填写你自己的 API Key**（云端或本地模型）即可使用
- 安装包**不含任何开发版数据**（无对话历史 / 无工作区 / 无密钥）

### 方式二：源码运行

```bash
# 需要 Python 3.10+
pip install -r requirements.lock
python gui_server.py
# 浏览器打开 http://localhost:8000
```

---

## 🚀 快速开始

1. **安装**：运行安装包，桌面出现"问墨·code"快捷方式
2. **启动**：双击打开，首次启动自动完成 seed 资源初始化
3. **配置模型**：设置 → 模型供应商 → 填入你的 API Key（DeepSeek / OpenCode Zen / 通义 / 智谱等任选）
4. **开始对话**：输入问题，AI 自动规划 → 执行 → 交付（可写文件、跑终端、出文档）
5. **自由扩展**：对话中让 AI 创建小应用、装技能、配 MCP——**用户数据独立于程序目录，更新不丢失**

---

## 🔄 自动更新

- 启动时自动检查 GitHub Releases（无需登录）
- 新版本发布 → 弹窗提示（含变更日志）→ 点击下载安装
- **签名验证 + 哈希校验**通过才会应用更新
- 更新只替换程序本体，**用户数据（对话/配置/技能/小应用）原样保留**

---

## 🗂 项目结构

```
wenmo-code/
├── gui_server.py          # 服务器主程序（FastAPI）
├── gui/static/            # 前端（原生 JS，无框架）
├── updater.py             # 自动更新（签名验证 + 增量更新）
├── memory_graph.py        # 记忆图（跨会话语义召回）
├── history.py             # 对话历史存储（按项目/用户分目录）
├── auth.py                # GitHub OAuth 多用户登录
├── cluster.py             # Worker 集群（令牌三级读取）
├── permission_engine.py   # 权限引擎（ask/allow）
├── network_safety.py      # 网络与命令安全防护
├── plugins/               # 插件系统（34 个）
├── plugins_loader.py      # 插件加载器
├── skills/                # 技能库（373 个，14 类）
├── skills_loader.py       # 技能加载器（按需加载）
├── apps/                  # 小应用（时钟/系统状态）
├── mcp_client.py          # MCP 客户端
├── mcp.json               # MCP 服务器配置（9 个）
├── websearch_mcp_server.py    # 搜索 MCP
├── file_mcp_server.py         # 文件 MCP
├── ppt_pipeline_mcp_server.py # PPT 流水线 MCP
├── secret_store.py        # 密钥存储（脱敏）
├── billing_service.py     # 用量计费
├── signing_policy.json    # 签名信任策略（发布者指纹）
├── build_wenmo.py         # PyInstaller 打包脚本（二进制扫描 + checksums）
├── wenmo_installer.iss    # Inno Setup 安装脚本
├── .github/workflows/     # CI 流水线（release.yml）
└── 问墨.ico               # 应用图标
```

---

## ⚙️ 构建与发布（CI）

问墨的完整发布链路（GitHub Actions `release.yml`）：

```
代码推送 + 打 tag v* 
   → ① 凭据扫描（防密钥入库）→ ② 签名证书校验（fail-fast）
   → ③ 回归测试 → ④ PyInstaller 编译（Qt 排除）
   → ⑤ Authenticode 签名 + 时间戳 → ⑥ 增量包 + manifest + checksums
   → ⑦ Inno Setup 封装安装包 → ⑧ 安装烟测 → ⑨ Sigstore 证明 → 发布 Release
```

本地打安装包：

```bash
python build_wenmo.py                 # PyInstaller 编译（含脱敏/扫描/checksums）
ISCC.exe wenmo_installer.iss          # Inno Setup 封装安装包
```

---

## 🛠 开发调试

- **服务器调试**：`python gui_server.py` → http://localhost:8000；日志见 `gui_server.log`
- **API 调试**：内置 `/api/*` REST 端点 + 前端可视化面板（供应商/模型/用量/计费）
- **自动修复**：模型输出异常 JSON 自动修复、工具调用参数校验、重复调用风暴抑制
- **多用户登录**：GitHub OAuth，详见 [README_AUTH.md](README_AUTH.md)

---

## 📄 License

[Apache-2.0](LICENSE)（第三方组件见各依赖 LICENSE）

---

*问墨·code v1.0.6 — 安全加固 · 零密钥打包 · 签名验证更新 · 时钟/系统状态小应用 · 全量技能库/插件/MCP*
