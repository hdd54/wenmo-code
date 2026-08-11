"""
Skills 加载器：与 opencode 工作流对齐 —— 自动扫描 opencode 的技能目录，
解析 SKILL.md（名称/描述/正文），聊天时按用户消息自动匹配注入相关技能。

opencode 的技能即"一组 Markdown 说明书"，AI 按需读取。
本模块让我们的聊天工具复用同一套技能库，行为对齐 opencode。
"""
import os
import re
import sys
import time
from extension_packages import component_dirs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 打包版：技能可写目录（skills_evolved 等）优先用 WENMO_DATA_DIR；只读技能库仍从资源目录读
_env_data = os.environ.get("WENMO_DATA_DIR")
if _env_data:
    os.makedirs(os.path.join(_env_data, "skills_evolved"), exist_ok=True)
    BASE_DIR = os.environ.get("WENMO_RES_DIR") or BASE_DIR   # 技能库读资源目录（只读）

# 技能目录：本地复制版优先（自包含），opencode 目录作为补充（新增技能自动同步）
SKILL_DIRS = []
SKILL_DIRS.extend(component_dirs("skills"))
# 内容分离：数据目录 content/skills（用户自定义技能优先，不随更新覆盖）
_env_data2 = os.environ.get("WENMO_DATA_DIR")
if _env_data2:
    _user_sk = os.path.join(_env_data2, "content", "skills")
    os.makedirs(_user_sk, exist_ok=True)
    SKILL_DIRS.append(_user_sk)
SKILL_DIRS += [
    os.path.join(BASE_DIR, "skills"),            # 复制到 agent 内的技能库（自包含）
    os.path.join(BASE_DIR, "seed", "skills"),   # 内容分离：出厂种子技能（seed/skills）
    os.path.expandvars(r"%USERPROFILE%\.config\opencode\skills"),
    os.path.expandvars(r"%USERPROFILE%\.opencode\skills"),
    os.path.join(BASE_DIR, ".agents", "skills"),
    os.path.join(BASE_DIR, ".opencode", "skills"),
    # 自演化试用区（evolve 蒸馏的新技能先在此，按使用率毕业/归档）
    os.path.join(BASE_DIR, "skills_evolved"),
]

# Development loads the complete local skill surface. Packaged clients do not
# inspect developer-machine caches unless a client user explicitly opts in.
if (not getattr(sys, "frozen", False)
        or os.environ.get("WENMO_ENABLE_OVERSEAS_SKILLS") == "1"):
    SKILL_DIRS += [
        os.path.expandvars(r"%USERPROFILE%\.claude\plugins\cache\claude-plugins-official\superpowers\6.1.1\skills"),
        os.path.join(sys.prefix, "Lib", "site-packages", "autoharness", "data", ".github", "skills"),
    ]

MAX_SKILLS_PER_REQUEST = 3   # 每次聊天最多注入几个技能
MAX_SKILL_CHARS = 3000       # 每个技能注入正文的上限

# 技能描述里的 cwd 生效条件（如「仅当 cwd 包含 'mao-zedong' 时生效」）→ 尊重它：
# 当前目录不满足时该技能完全不加载（不注入、不匹配），防止"通用场景误注入特定场景技能"
_CWD_GUARD_RE = re.compile(r"cwd[^\n，。;；,]*?包含\s*[‘'\"`]?([\w\-.]+)")


def _passes_cwd_guard(description):
    """技能描述声明了 cwd 条件 → 校验当前工作目录是否满足；未声明 → 放行"""
    m = _CWD_GUARD_RE.search(description or "")
    if not m:
        return True
    key = m.group(1)
    return key in os.getcwd()

_cache = {"time": 0, "skills": []}


def _parse_frontmatter(content):
    """解析 SKILL.md 的 YAML frontmatter，取 description（拿不到就返回空）"""
    m = re.match(r"^---\s*\n([\s\S]*?)\n---", content)
    if not m:
        return ""
    lines = m.group(1).splitlines()
    for i, line in enumerate(lines):
        ls = line.strip()
        if ls.startswith("description:"):
            rest = ls[len("description:"):].strip()
            if rest in ("|-", "|", ">-", ">"):
                # YAML 块标量：描述在多行缩进文本里（如 claude-api 的 SKILL.md）
                parts = []
                for ln in lines[i + 1:]:
                    if ln[:1] in (" ", "\t"):
                        parts.append(ln.strip())
                    else:
                        break
                return " ".join(parts).strip()
            return rest.strip().strip('"').strip("'")
    return ""


def _parse_frontmatter_trigger(content):
    """解析独立 trigger: 键（中文触发词，逗号分隔），供匹配增强。
    兼容外部技能包（如 Cybersecurity-Skills）用独立键声明触发词的惯例。"""
    m = re.match(r"^---\s*\n([\s\S]*?)\n---", content)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        ls = line.strip()
        if ls.startswith("trigger:") or ls.startswith("triggers:"):
            rest = ls.split(":", 1)[1].strip().strip('"').strip("'")
            # 去除 YAML 数组符号 [a, b]
            rest = rest.strip("[]")
            return rest
    return ""


def _parse_description(name, content):
    desc = _parse_frontmatter(content)
    if desc:
        return desc
    # 兜底：正文第一个非标题段落
    for para in content.split("\n\n"):
        p = para.strip()
        if p and not p.startswith("#") and not p.startswith("```"):
            return p[:200]
    return name


def load_skills(force=False):
    """扫描技能目录（带 60 秒缓存）→ [{name, description, source, content}]；按名称去重，本地优先"""
    now = time.time()
    if not force and now - _cache["time"] < 60 and _cache["skills"]:
        return _cache["skills"]
    skills = []
    seen = set()
    for root in SKILL_DIRS:
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in seen:
                continue   # 同名技能：本地复制版优先
            md = os.path.join(entry.path, "SKILL.md")
            if not os.path.isfile(md):
                continue
            try:
                with open(md, encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            seen.add(entry.name)
            desc = _parse_description(entry.name, content)
            # 独立 trigger: 键（外部技能包惯例）→ 合并进描述，供 _trigger_words 提取
            _trig = _parse_frontmatter_trigger(content)
            if _trig:
                desc = (desc + " 触发词：" + _trig).strip()
            if not _passes_cwd_guard(desc):
                continue   # 技能自述的生效目录不满足 → 不加载（如"选调素材每日推送"仅限 mao-zedong 目录）
            skills.append({
                "name": entry.name,
                "description": desc,
                "source": os.path.basename(os.path.dirname(entry.path)) or root,
                "content": content,
            })
    _cache.update(time=now, skills=skills)
    return skills


def _text_grams(text):
    """抽取匹配特征：中文二元组 + 英文单词（小写，长度≥3）"""
    grams = set()
    for i in range(len(text) - 1):
        pair = text[i:i + 2]
        if all("\u4e00" <= c <= "\u9fff" for c in pair):
            grams.add(pair)
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower()):
        grams.add("w:" + w)
    return grams


# 通用型技能白名单：工作规范/方法论类，与具体问题无关，时刻常驻注入（底层技能）
GENERIC_SKILL_NAMES = {
    "coding-standards", "error-handling", "api-design", "test-driven-development",
    "systematic-debugging", "git-workflow", "frontend-patterns", "backend-patterns",
    "python-patterns", "react-patterns", "docker-patterns", "database",
    "refactoring", "verification-before-completion", "requesting-code-review",
    "security-review", "performance",
}


def is_generic(name):
    """是否为通用型技能（底层常驻）；其余为特点型（按关键词/语义匹配才激活）"""
    return name in GENERIC_SKILL_NAMES


def generic_skills(skills=None):
    """返回全部通用型技能 [{name, desc, body}]（正文截短；由调用方决定全文/摘要注入）"""
    skills = skills if skills is not None else load_skills()
    by_name = {s["name"]: s for s in skills}
    out = []
    for name in GENERIC_SKILL_NAMES:
        sk = by_name.get(name)
        if not sk:
            continue
        out.append({
            "name": name,
            "desc": sk["description"][:150],
            "body": sk["content"][:2200],
        })
    return out


def _skill_name_words(name):
    """技能名按 -/_ 分词（小写，≥3 字符）：python-patterns → {python, patterns}"""
    return {w for w in re.split(r"[\-_]+", name.lower()) if len(w) >= 3}


_TRIGGER_SEG_RE = re.compile(r"(?:触发词|TRIGGER)[：:]\s*([^\n。]+)")


def _trigger_words(description):
    """提取技能描述「触发词：…」段的特征（中文词 2-6 字 + 英文词），
    作为强匹配特征：命中触发词才允许激活，未命中 → 特定触发技能直接不召回（防误加载）。"""
    words = set()
    m = _TRIGGER_SEG_RE.search(description or "")
    if not m:
        return words
    seg = m.group(1)
    for w in re.findall(r"[\u4e00-\u9fff]{2,6}", seg):
        words.add(w)
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", seg.lower()):
        words.add("w:" + w)
    return words


def _trigger_hit_count(trig, message, msg_words):
    """消息命中触发词的数量（中文子串包含 / 英文词匹配）"""
    if not trig:
        return -1   # 无触发词声明 → 不受限
    n = 0
    for w in trig:
        if w.startswith("w:"):
            if w[2:] in msg_words:
                n += 1
        elif w in message:
            n += 1
    return n


def match_skills(message, skills=None, max_n=None):
    """按用户消息召回候选技能（字面特征重叠），最多 max_n 个。

    评分：
    - 中文二元组重叠：1 分
    - 英文词重叠：技能名直接点名的词 3 分（强信号），否则 1 分
    - 命中技能「触发词」段：每个 +2 分
    触发词门禁：技能声明了「触发词/TRIGGER」但消息完全未命中 → 不召回
    （防特定触发技能被泛词误加载，如"女娲技能"只在用户提到 造skill/蒸馏/女娲 时出现）。
    本函数只做【字面候选召回】，语义相关性判断由上层 LLM 完成。
    """
    skills = skills if skills is not None else load_skills()
    if not message:
        return []
    max_n = max_n or MAX_SKILLS_PER_REQUEST
    msg_grams = _text_grams(message)
    if not msg_grams:
        return []
    msg_words = {g[2:] for g in msg_grams if g.startswith("w:")}
    scored = []
    for sk in skills:
        trig = _trigger_words(sk.get("description", ""))
        trig_hit = _trigger_hit_count(trig, message, msg_words)
        if trig and trig_hit == 0:
            continue   # 特定触发技能：触发词未命中 → 不召回
        hay = sk["name"] + " " + sk["description"]
        hay_grams = _text_grams(hay)
        cn = len(hay_grams & msg_grams)                       # 中文二元组重叠
        name_words = _skill_name_words(sk["name"])
        en_strong = len(msg_words & name_words)                # 点名技能名
        en_other = len(msg_words & {g[2:] for g in hay_grams if g.startswith("w:")}) - en_strong
        score = cn + en_strong * 3 + en_other + max(0, trig_hit) * 2
        if score >= 1:   # 候选召回（语义筛选交给上层 LLM）
            scored.append((score, sk))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [sk for _, sk in scored[:max_n]]
