# -*- coding: utf-8 -*-
"""技能分类工具插件（核心优化：skill 不注入 prompt，按需加载）。

背景：原实现把匹配到的 skill 完整正文（每个最长 12000 字符）注入 user 消息——
     (1) 注入内容随消息变化 → 破坏前缀缓存，每轮缓存不命中；
     (2) 6-10 个 skill 正文 = 数万 tokens 固定开销，即使与本次任务无关。
方案：把 199 个 skill 按功能归为 14 类，每类一个"分类工具"（描述极短），
     模型需要时才调用 skill_load 按名加载完整正文 → 工具描述只占 ~2KB 固定部分
     （工具列表稳定 → 前缀缓存命中），正文按需一次性获取，不再注入 prompt。

工具：
  skill_list(category)    列出某类全部技能名+一句话描述（轻量索引）
  skill_load(name)        按名加载单个技能的完整 SKILL.md 正文（按需）
  skill_overview()        列出 14 类全景（每类数量 + 代表技能）

分类 = 技能名关键词匹配（name 是可靠分类依据，desc 仅中文兜底）：
     手工覆盖 > 名称关键词（顺序敏感，高特异在前）> desc 中文关键词 > plan。
"""

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE))  # 上级目录（agent-tutorial）→ 可 import skills_loader

# 手工覆盖（真实技能名 → 分类；命中优先于规则）
_OVERRIDES = {
    "programming": "lang",
    "frontend": "frontend",
    "frontend-design": "frontend",
    "frontend-design-direction": "frontend",
    "frontend-slides": "frontend",
    "refactor": "plan",
    "refactoring": "plan",
    "remove-ai-slops": "plan",
    "visual-qa": "testing",
    "browser-qa": "testing",
    "windows-desktop-e2e": "testing",
    "e2e-testing": "testing",
    "performance": "debug",
    "debugging": "debug",
    "systematic-debugging": "debug",
    "error-handling": "debug",
    "database": "data",
    "data-analysis": "data",
    "xlsx": "data",
    "docx": "doc",
    "pptx": "doc",
    "pdf": "doc",
    "devops": "devops",
    "mobile": "lang",
    "huashu-nuwa": "perspective",
    "claude-api": "plan",
    "coding-agent-sessions": "plan",
    "选调素材每日推送": "research",
    "uncloud": "devops",
    "flox-environments": "devops",
    "github-ops": "git",
    "jira-integration": "git",
    "project-flow-ops": "git",
    "opensource-pipeline": "git",
    "using-git-worktrees": "git",
    "finishing-a-development-branch": "git",
    "git-master": "git",
    "git-workflow": "git",
    "terminal-ops": "devops",
    "generating-python-installer": "devops",
    "canary-watch": "devops",
    "unified-notifications-ops": "devops",
    "google-workspace-ops": "doc",
    "internal-comms": "doc",
    "article-writing": "doc",
    "brand-voice": "research",
    "brand-discovery": "research",
    "knowledge-ops": "research",
    "doc-coauthoring": "doc",
    "slack-gif-creator": "doc",
    "video-editing": "doc",
    "videodb": "doc",
    "manim-video": "doc",
    "remotion-video-creation": "doc",
    "fal-ai-media": "doc",
    "visa-doc-translate": "doc",
    "email-ops": "research",
    "messages-ops": "research",
    "finance-billing-ops": "research",
    "investor-materials": "research",
    "investor-outreach": "research",
    "lead-intelligence": "research",
    "marketing-campaign": "research",
    "content-engine": "research",
    "crosspost": "research",
    "social-publisher": "research",
    "social-graph-ranker": "research",
    "connections-optimizer": "research",
    "x-api": "research",
    "seo": "research",
    "deep-research": "research",
    "research-ops": "research",
    "market-research": "research",
    "exa-search": "research",
    "search-first": "research",
    "data-scraper-agent": "research",
    "documentation-lookup": "research",
    "iterative-retrieval": "research",
    "competitive-platform-analysis": "research",
    "competitive-report-structure": "research",
    "benchmark-methodology": "research",
    "ultimate-browsing": "research",
    "ulw-research": "research",
    "ultraresearch": "research",
    "pubmed-database": "research",
    "uspto-database": "research",
    "gget": "research",
    "literature-review": "research",
    "scholar-evaluation": "research",
}

# 分类规则：(分类, [技能名关键词...]) —— 按顺序匹配技能名（小写），命中即归
_NAME_RULES = [
    # 高特异分类先匹配（避免被通用词误吸）
    ("perspective", ["perspective", "思维", "视角", "心智", "表达方式"]),
    ("security", ["security", "secur", "safety", "vulnerab", "bounty", "hipaa",
                  "phi-compliance", "defi-amm", "keccak", "evm-token", "payment-x402",
                  "trading-agent-security"]),
    ("git", ["git", "github", "worktree", "jira", "branch", "commit", "pull-request"]),
    ("frontend", ["frontend", "motion", "design-system", "accessib", "algorithmic-art",
                  "brand-guidelines", "canvas", "chinese-web", "ios-icon", "make-interfaces",
                  "theme", "ui-demo", "ui-to-vue", "web-artifacts", "liquid-glass",
                  "frontend-", "ui-"]),
    ("backend", ["api", "mcp", "graphql", "backend", "server", "fastapi", "django",
                 "spring", "quarkus", "nestjs", "laravel", "hexagonal", "grpc",
                 "connector-builder"]),
    ("data", ["database", "data-", "data_", "sql", "mysql", "postgres", "redis",
              "clickhouse", "xlsx", "excel", "pandas", "etl", "ml-", "mle", "pytorch",
              "recsys", "dashboard"]),
    ("lang", ["python", "golang", "rust", "kotlin", "swift", "dart", "flutter", "react",
              "vue", "angular", "java", "cpp", "csharp", "fsharp", "perl", "typescript",
              "node", "bun", "mobile", "programming", "testing-pattern"]),
    ("testing", ["test", "e2e", "qa", "benchmark", "verification", "review", "eval",
                 "codehealth", "plankton", "santa", "visual", "browser", "windows-"]),
    ("debug", ["debug", "error", "latency", "lsp", "ast-grep", "diagnos", "systematic-",
               "click-path", "performance"]),
    ("devops", ["docker", "kubernetes", "k8s", "deploy", "network", "homelab",
                "terminal", "installer", "canary", "uncloud", "flox", "devops",
                "notifications", "cisco", "netmiko", "bgp"]),
    ("doc", ["docx", "pptx", "pdf", "doc-", "video", "manim", "remotion", "fal-ai",
             "slack-gif", "visa", "workspace-ops", "internal-comms", "article",
             "video-"]),
    ("research", ["research", "search", "market", "content", "seo", "brand", "investor",
                  "outreach", "scraper", "lead", "competitive", "social", "marketing",
                  "crosspost", "newsletter", "exa", "literature", "pubmed", "uspto",
                  "gget", "scholar", "iterative", "knowledge", "connections", "email",
                  "messages", "finance", "billing"]),
    ("agent", ["agent", "agentic", "autonomous", "council", "brainstorm", "dispatch",
               "dmux", "gan-style", "subagent", "team", "openclaw", "orchestr",
               "harness", "loop", "continuous", "ralphinho", "cluster"]),
    ("plan", ["plan", "prompt", "skill", "onboarding", "code-tour", "context", "budget",
              "cost", "compact", "feedback", "delivery", "meta", "growth", "rules",
              "writing", "execute", "start", "superpowers", "gateguard", "hookify",
              "lcx", "init", "install", "tune", "verify", "intent", "product",
              "audit", "repo", "strategic", "token", "workspace", "automation",
              "claude-api", "superpowers"]),
]

# desc 中文关键词兜底（技能名完全无信息时用）
_DESC_CN_RULES = [
    ("perspective", ["思维", "视角", "心智模型", "表达方式"]),
    ("security", ["安全", "漏洞", "合规", "渗透", "权限", "加密"]),
    ("frontend", ["前端", "样式", "动效", "动画", "设计系统", "网页"]),
    ("backend", ["后端", "服务端", "接口", "中间件"]),
    ("data", ["数据库", "数据分析", "机器学习", "报表", "表格"]),
    ("testing", ["测试", "质量", "审查", "验证"]),
    ("debug", ["调试", "性能", "错误", "定位"]),
    ("git", ["版本", "分支", "提交"]),
    ("devops", ["部署", "运维", "网络", "容器"]),
    ("doc", ["文档", "办公", "媒体", "视频", "演示文稿"]),
    ("research", ["研究", "搜索", "营销", "内容", "投资", "品牌"]),
    ("agent", ["智能体", "多智能体", "编排", "团队"]),
    ("plan", ["规划", "工作流", "技能", "知识库", "成本"]),
]

CATEGORY_LABELS = {
    "frontend": "前端/UI/设计/动效",
    "backend": "后端/服务端/API/框架",
    "data": "数据库/数据分析/ML",
    "lang": "编程语言模式",
    "testing": "测试/质量/审查",
    "security": "安全/合规",
    "debug": "调试/性能/问题定位",
    "git": "Git/GitHub/工程流程",
    "devops": "DevOps/部署/网络运维",
    "doc": "文档/办公/媒体生成",
    "research": "研究/搜索/内容营销",
    "agent": "Agent/多智能体编排",
    "plan": "规划/工作流/元技能",
    "perspective": "思维视角/人物",
}

CATEGORIES = list(CATEGORY_LABELS.keys())

_skills_cache = None


def _all_skills():
    """加载全部 skill（带缓存），返回 {name: {description, content}}"""
    global _skills_cache
    if _skills_cache is None:
        try:
            import skills_loader
            _skills_cache = {s["name"]: s for s in skills_loader.load_skills(force=True)}
        except Exception:
            _skills_cache = {}
    return _skills_cache


def _category_of(name, desc=""):
    """技能 → 分类：手工覆盖 > 技能名关键词 > desc 中文关键词 > plan"""
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    n = name.lower()
    for cat, kws in _NAME_RULES:
        for kw in kws:
            if kw in n:
                return cat
    d = desc or ""
    for cat, kws in _DESC_CN_RULES:
        for kw in kws:
            if kw in d:
                return cat
    return "plan"


def _categorize_all():
    """返回 {category: [(name, description)]}（按名字排序）"""
    out = {c: [] for c in CATEGORIES}
    for name, sk in _all_skills().items():
        cat = _category_of(name, sk.get("description") or "")
        out.setdefault(cat, []).append((name, (sk.get("description") or "")[:120]))
    for c in out:
        out[c].sort(key=lambda x: x[0])
    return out


def skill_list(args):
    """列出某类全部技能（名称+一句话描述），用于选择要加载的技能"""
    cat = str(args.get("category", "")).strip().lower()
    if cat not in CATEGORIES:
        return ("未知分类。可用分类：\n" + "\n".join(
            f"  {c} = {CATEGORY_LABELS.get(c, c)}" for c in CATEGORIES))
    items = _categorize_all().get(cat, [])
    if not items:
        return f"分类 [{cat}] 暂无技能"
    lines = [f"【{CATEGORY_LABELS.get(cat, cat)}】共 {len(items)} 个技能："]
    for name, desc in items:
        lines.append(f"• {name} — {desc}")
    return "\n".join(lines)


def skill_load(args):
    """按名加载单个技能的完整 SKILL.md 正文（一次性，供当前任务遵循规范执行）"""
    name = str(args.get("name", "")).strip()
    if not name:
        return "错误：需要 name 参数（技能名）。先用 skill_list 查看某类技能清单。"
    sk = _all_skills().get(name)
    if not sk:
        cands = [n for n in _all_skills() if name.lower() in n.lower()]
        if cands:
            return f"未找到 [{name}]。相似技能：{', '.join(sorted(cands)[:10])}"
        return f"未找到技能 [{name}]。可用分类：{', '.join(CATEGORIES)}"
    body = sk.get("content") or ""
    return f"【技能：{name}】（分类：{_category_of(name, sk.get('description') or '')}）\n{body}"


def skill_list_all(args):
    """列出全部 14 个分类（每类技能数 + 代表技能），用于了解技能库全景"""
    cats = _categorize_all()
    lines = ["问墨·code 技能库（按需加载，调用 skill_load 获取正文）："]
    for c in CATEGORIES:
        items = cats.get(c, [])
        if not items:
            continue
        sample = "、".join(n for n, _ in items[:6])
        lines.append(f"• {c}（{CATEGORY_LABELS.get(c, c)}，{len(items)}个）：{sample}")
    return "\n".join(lines)


PLUGIN_TOOLS = [
    {"name": "skill_list",
     "description": "列出某类技能的全部技能名+一句话描述（轻量索引）。"
                    "技能库按功能分 14 类：frontend(前端/UI)/backend(后端/API)/data(数据库/ML)/lang(编程语言)"
                    "/testing(测试/质量)/security(安全)/debug(调试/性能)/git(Git/GitHub)/devops(部署/网络)"
                    "/doc(文档/办公/媒体)/research(研究/搜索/内容营销)/agent(Agent编排)/plan(规划/工作流)/perspective(思维视角)。"
                    "当任务涉及某领域（如写前端代码、做安全审计）且需要规范指导时，先调本工具查看该类有哪些技能，"
                    "再用 skill_load 加载具体技能正文。参数 category=分类名。",
     "parameters": {"type": "object", "properties": {
         "category": {"type": "string", "description": "分类名：frontend/backend/data/lang/testing/security/debug/git/devops/doc/research/agent/plan/perspective"}},
         "required": ["category"]}, "handler": skill_list},
    {"name": "skill_load",
     "description": "按名加载单个技能的完整规范正文（一次性获取，供当前任务遵循执行）。"
                    "技能库按 14 类组织（见 skill_list）。当任务需要某领域专业规范/方法论时调用。"
                    "参数 name=技能名（先用 skill_list 查到准确名称）。",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "技能名（如 debugging、api-design、writing-plans）"}},
         "required": ["name"]}, "handler": skill_load},
    {"name": "skill_overview",
     "description": "列出技能库全景：14 个分类各有多少技能、代表技能名。用于了解可用能力，"
                    "或当不确定用哪个技能时先看全局再定位。",
     "parameters": {"type": "object", "properties": {}}, "handler": skill_list_all},
]
