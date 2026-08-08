# -*- coding: utf-8 -*-
"""自演化 Harness P1：经验蒸馏器（evolve）

对话结束后，用 LLM 从会话中蒸馏【可复用经验】——用户纠正提炼为教训、
成功方法沉淀为技能——写入技能库，让 agent 越用越懂用户。

安全原则（对标 Autoharness）：
- 只提案 + 格式校验后写入（写 SKILL.md 前校验 frontmatter 完整性）
- 只写自生成技能（frontmatter 带 self-authored 标记），绝不碰用户/系统技能
- P2 将引入试用区/生命周期，本模块预留（skills 目录可切换）
"""

import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 正式技能库（用户/系统技能所在，只读不写自演化技能之外）
SKILLS_DIR = os.path.join(BASE, "skills")
# 自演化试用区（P2：新蒸馏技能先进试用区，按使用率毕业/归档）
SKILLS_EVOLVED_DIR = os.path.join(BASE, "skills_evolved")
# 归档区（长期未用，移入而非删除，可复活）
SKILLS_ARCHIVE_DIR = os.path.join(BASE, "skills_archive")
for _d in (SKILLS_DIR, SKILLS_EVOLVED_DIR, SKILLS_ARCHIVE_DIR):
    os.makedirs(_d, exist_ok=True)

USAGE_STATS_FILE = os.path.join(BASE, "usage_stats.json")

# 生命周期阈值
GRADUATE_USES = 3        # 试用期激活 ≥3 次 → 毕业入正式库
PROBATION_DAYS = 7       # 试用期 7 天 0 使用 → 归档
ARCHIVE_DAYS = 30        # 正式库 30 天 0 使用 → 归档

_MAX_INPUT_CHARS = 16000   # 蒸馏输入上限（防超长会话爆 token）


def _build_client(provider, model):
    """构建 LLM client（复用问墨·code 供应商配置）"""
    sys.path.insert(0, BASE)
    from chat import load_providers
    from openai import OpenAI
    providers = load_providers()
    if provider not in providers:
        return None, None
    cfg = providers[provider]
    key = cfg.get("api_key", "").strip()
    if not key:
        env = cfg.get("api_key_env", "").strip()
        if env:
            key = os.environ.get(env, "").strip()
    client = OpenAI(base_url=cfg["base_url"], api_key=key or "local", timeout=120)
    return client, cfg.get("model", model)


def _compact(messages, max_turns=20):
    """压缩会话为蒸馏输入：最近 max_turns 轮，含工具调用名"""
    lines = []
    for m in messages[-max_turns * 2:]:
        role = m.get("role")
        c = m.get("content")
        if isinstance(c, list):
            c = "[附件/图片]"
        if not isinstance(c, str):
            c = str(c)
        if role == "user":
            lines.append("用户: " + c[:400])
        elif role == "assistant":
            tools = ""
            tcs = m.get("tool_calls") or []
            if tcs:
                names = []
                for tc in tcs:
                    fn = tc.get("function") or {}
                    names.append(fn.get("name", ""))
                tools = " [调用工具: " + ", ".join(n for n in names if n) + "]"
            lines.append("AI: " + c[:200] + tools)
        elif role == "tool":
            lines.append("工具结果: " + c[:120])
    text = "\n".join(lines)
    return text[:_MAX_INPUT_CHARS]


def distill_episode(messages, provider, model, existing_skills=None):
    """从会话蒸馏可复用经验。返回 {"lesson": str|None, "skill": {name,description,content}|None}
    existing_skills：现有技能名+描述列表，供 LLM 判断是否与已有技能重复（防堆叠）"""
    conv = _compact(messages or [])
    if not conv.strip():
        return {"lesson": None, "skill": None}
    client, mdl = _build_client(provider, model)
    if not client:
        return {"lesson": None, "skill": None}
    existing_block = ""
    if existing_skills:
        names = [s.get("name", "") for s in existing_skills[:40]]
        if names:
            existing_block = "现有技能库（如果新技能与其中某个高度重叠，应返回 null 或给出更独特的视角）：\n" + \
                             "、".join(names[:40]) + "\n\n"
    prompt = (
        "你是经验蒸馏器。从这段 AI 对话中提取【可复用经验】，用于改进 AI 助手未来表现。\n\n"
        "对话：\n" + conv + "\n\n"
        + existing_block +
        "分析要点：\n"
        "① 用户是否纠正/批评了 AI？如果是，提炼成一条教训（AI 下次遇到类似情况应怎么做）；\n"
        "② 是否有可复用的成功方法（多步流程、有效的工具组合、用户偏好的格式/风格）？"
        "如果是，值得沉淀为一个技能（步骤明确、可复用）；与现有技能高度重复时不要新建；\n"
        "③ 是否出现反复失败的模式？如果是，提炼避坑要点并入教训或技能。\n\n"
        "没有值得沉淀的经验就输出 null，宁缺毋滥。\n"
        "输出 JSON（严格，只输出 JSON 本身）：\n"
        '{"lesson": "教训文本（无则 null）", '
        '"skill": {"name": "技能名-英文小写-连字符", '
        '"description": "技能描述-含适用场景和触发条件", '
        '"content": "技能正文-Markdown-含具体步骤"}} '
        "skill 为 null 或对象。"
    )
    try:
        resp = client.chat.completions.create(
            model=mdl, messages=[{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=600,
        )
        raw = (resp.choices[0].message.content or "") if resp.choices else ""
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return {"lesson": None, "skill": None}
        data = json.loads(m.group(0))
        lesson = str(data.get("lesson") or "").strip() or None
        skill = None
        sk = data.get("skill")
        if isinstance(sk, dict):
            name = re.sub(r"[^a-z0-9-]", "-", str(sk.get("name") or "").lower()).strip("-")
            desc = str(sk.get("description") or "").strip()
            content = str(sk.get("content") or "").strip()
            if name and desc and content and len(desc) >= 10 and len(content) >= 50:
                skill = {"name": name, "description": desc, "content": content}
        return {"lesson": lesson, "skill": skill}
    except Exception:
        return {"lesson": None, "skill": None}


def apply_evolve_result(result, save_lesson_fn, source="evolve"):
    """把蒸馏结果落到技能库/教训。save_lesson_fn 注入（避免循环 import）。
    返回已应用的项列表（["lesson"] / ["skill"] / []）。"""
    applied = []
    try:
        lesson = result.get("lesson")
        if lesson:
            save_lesson_fn(lesson, source=source)
            applied.append("lesson")
        skill = result.get("skill")
        if skill:
            if _write_skill(skill):
                applied.append("skill")
    except Exception:
        pass
    return applied


def _write_skill(skill):
    """写 SKILL.md 到【试用区】（P2 生命周期：先试用，按使用率毕业/归档）。
    frontmatter + self-authored 标记 + 格式校验。返回 bool"""
    name = skill["name"][:60] or ("evolved-%d" % int(time.time()))
    desc = skill["description"].replace("\n", " ")[:200]
    content = skill["content"][:6000]
    md = "---\nname: %s\ndescription: %s\nself-authored: true\n---\n\n%s\n" % (name, desc, content)
    d = os.path.join(SKILLS_EVOLVED_DIR, name)
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(md)
        # 初始化 usage 统计
        stats = _load_usage_stats()
        stats.setdefault(name, {"uses": 0, "last": 0, "created": time.time()})
        _save_usage_stats(stats)
        return True
    except Exception:
        return False


# ============ 生命周期管理（P2） ============

def _load_usage_stats():
    try:
        with open(USAGE_STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_usage_stats(stats):
    try:
        with open(USAGE_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _is_self_authored(skill_dir):
    """检查技能是否为自演化生成（frontmatter self-authored）"""
    try:
        with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as f:
            head = f.read(400)
        return "self-authored: true" in head
    except Exception:
        return False


def _move_dir(src, dst_base):
    """移动技能目录（归档/毕业），目标重名时加时间戳"""
    name = os.path.basename(src)
    target = os.path.join(dst_base, name)
    if os.path.exists(target):
        target = os.path.join(dst_base, "%s-%d" % (name, int(time.time())))
    try:
        os.rename(src, target)
        return True
    except Exception:
        return False


def manage_lifecycle(force=False):
    """生命周期扫描：试用区毕业/归档 + 正式库归档 + 清理统计。
    返回操作摘要 dict。"""
    now = time.time()
    stats = _load_usage_stats()
    actions = {"graduated": [], "archived": [], "removed_stats": []}

    # 1) 试用区：按使用率毕业/归档
    if os.path.isdir(SKILLS_EVOLVED_DIR):
        for name in os.listdir(SKILLS_EVOLVED_DIR):
            d = os.path.join(SKILLS_EVOLVED_DIR, name)
            if not os.path.isdir(d) or not os.path.isfile(os.path.join(d, "SKILL.md")):
                continue
            u = stats.get(name, {"uses": 0, "last": 0, "created": now})
            uses = u.get("uses", 0)
            created = u.get("created", now)
            if uses >= GRADUATE_USES:
                # 毕业 → 正式库（跳过正式库已有同名）
                if not os.path.isdir(os.path.join(SKILLS_DIR, name)) and _move_dir(d, SKILLS_DIR):
                    actions["graduated"].append(name)
            elif (now - created) > PROBATION_DAYS * 86400 and uses == 0:
                # 试用期 0 使用 → 归档
                if _move_dir(d, SKILLS_ARCHIVE_DIR):
                    actions["archived"].append(name)
                    stats.pop(name, None)

    # 2) 正式库：长期 0 使用 → 归档（只动 self-authored）
    if os.path.isdir(SKILLS_DIR):
        for name in os.listdir(SKILLS_DIR):
            d = os.path.join(SKILLS_DIR, name)
            if not os.path.isdir(d) or not _is_self_authored(d):
                continue
            u = stats.get(name, {"uses": 0, "last": 0})
            last = u.get("last", 0)
            if last and (now - last) > ARCHIVE_DAYS * 86400:
                if _move_dir(d, SKILLS_ARCHIVE_DIR):
                    actions["archived"].append(name)
                    stats.pop(name, None)

    # 3) 清理统计（归档已删）
    live = set(os.listdir(SKILLS_DIR)) | set(os.listdir(SKILLS_EVOLVED_DIR))
    for name in list(stats.keys()):
        if name not in live:
            actions["removed_stats"].append(name)
            stats.pop(name, None)

    _save_usage_stats(stats)
    return actions
