"""kb_mcp_server.py - 个人知识库 MCP 服务器

让 AI 客户端 (opencode / Claude Code / Cherry Studio 等) 直接读写知识库笔记,
与 kb-gui 桌面应用共享同一套 notes_store 存储层和笔记目录。

工具:
  kb_add_note     新建笔记
  kb_update_note  更新笔记 (标题/内容/标签)
  kb_delete_note  删除笔记
  kb_get_note     读取单篇笔记全文
  kb_list_notes   列出笔记 (可按标签过滤)
  kb_search_notes 全文搜索
  kb_list_tags    列出全部标签

运行:  <venv>/python kb_mcp_server.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

from pydantic import Field
from mcp.server.fastmcp import FastMCP

# 笔记目录: 环境变量 KB_NOTES_DIR 优先, 否则默认程序同级 notes/ (与 GUI 一致)
def _notes_dir() -> str:
    env = os.environ.get("KB_NOTES_DIR")
    if env:
        return env
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "notes")


NOTES_DIR = _notes_dir()

# 允许 import notes_store 无论从何路径启动
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notes_store import NotesStore  # noqa: E402

store = NotesStore(NOTES_DIR)

mcp = FastMCP(
    "kb-gui-mcp",
    instructions=(
        "个人知识库 MCP 服务器。提供笔记的新建、读取、更新、删除、搜索与标签管理。"
        f"笔记以 Markdown 文件保存在: {NOTES_DIR}"
    ),
)


def _clean_tags(tags: Optional[List[str]]) -> List[str]:
    if tags is None or _is_missing(tags):
        return []
    return [t.strip() for t in tags if t and t.strip()]


def _is_missing(value) -> bool:
    """FastMCP 直接调用函数时, 未提供的可选参数默认值是 FieldInfo 而非 None。

    无论通过 MCP 协议还是直接函数调用, 都把 FieldInfo 视为 '未提供'。
    """
    from pydantic.fields import FieldInfo
    return isinstance(value, FieldInfo)


# ---------- 工具 ----------

@mcp.tool(
    name="kb_add_note",
    annotations={
        "title": "新建知识库笔记",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def kb_add_note(
    title: str = Field(..., description="笔记标题, 例如 'Python 装饰器笔记'", min_length=1, max_length=200),
    content: str = Field(..., description="笔记正文, Markdown 格式"),
    tags: Optional[List[str]] = Field(default_factory=list, description="标签列表, 例如 ['Python', '教程']", max_length=20),
) -> str:
    """新建一篇知识库笔记。

    把对话内容、总结、灵感等保存为 Markdown 笔记, 可附带标签便于后续检索。
    创建成功后返回笔记 ID, 可用于 kb_get_note / kb_update_note / kb_delete_note。

    Args:
        title (str): 笔记标题, 1-200 字符
        content (str): 笔记正文, Markdown 格式
        tags (Optional[List[str]]): 标签列表, 最多 20 个

    Returns:
        str: JSON, 包含 note_id / title / tags / created / updated / path

    Examples:
        - "把刚才的讨论总结保存成笔记" -> title='AI 工具讨论总结', content='...', tags=['随笔']
        - "记录这个 Python 技巧" -> title='Python 装饰器', content='```python\\n...\\n```', tags=['Python']
    """
    try:
        note = store.create_note(title, content)
        cleaned = _clean_tags(tags)
        if cleaned:
            note.tags = cleaned
            store.save_note(note)
        return json.dumps({
            "note_id": note.note_id,
            "title": note.title,
            "tags": note.tags,
            "created": note.created,
            "updated": note.updated,
            "path": store._path(note.note_id),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error: 创建笔记失败 - {e}"


@mcp.tool(
    name="kb_update_note",
    annotations={
        "title": "更新知识库笔记",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def kb_update_note(
    note_id: str = Field(..., description="笔记 ID (文件名的 .md 前缀), 通过 kb_list_notes 获取"),
    title: Optional[str] = Field(default=None, description="新标题 (省略则不修改)", min_length=1, max_length=200),
    content: Optional[str] = Field(default=None, description="新正文 (省略则不修改)"),
    tags: Optional[List[str]] = Field(default=None, description="新标签列表 (省略则不修改)", max_length=20),
) -> str:
    """更新一篇已有笔记的标题、正文或标签。

    未提供的字段保持不变。更新成功后 updated 时间会自动刷新。

    Args:
        note_id (str): 目标笔记 ID
        title (Optional[str]): 新标题
        content (Optional[str]): 新正文
        tags (Optional[List[str]]): 新标签列表

    Returns:
        str: JSON, 包含更新后的 note_id / title / tags / updated

    Examples:
        - 给笔记补充内容 -> note_id='xxx', content='追加的段落...'
        - 修改标题 -> note_id='xxx', title='新标题'
        - 换标签 -> note_id='xxx', tags=['新标签']
    """
    note = store.get_note(note_id)
    if note is None:
        return f"Error: 找不到笔记 '{note_id}'。请先用 kb_list_notes 或 kb_search_notes 确认 ID 正确。"
    if title is not None and not _is_missing(title):
        note.title = title
    if content is not None and not _is_missing(content):
        note.content = content
    if tags is not None and not _is_missing(tags):
        note.tags = _clean_tags(tags)
    store.save_note(note)
    return json.dumps({
        "note_id": note.note_id,
        "title": note.title,
        "tags": note.tags,
        "updated": note.updated,
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="kb_delete_note",
    annotations={
        "title": "删除知识库笔记",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def kb_delete_note(
    note_id: str = Field(..., description="要删除的笔记 ID"),
) -> str:
    """永久删除一篇笔记。此操作不可撤销, 删除后文件从磁盘移除。

    Args:
        note_id (str): 要删除的笔记 ID

    Returns:
        str: 成功或失败信息

    Examples:
        - "删掉那篇旧笔记" -> note_id='xxx'
    """
    note = store.get_note(note_id)
    if note is None:
        return f"Error: 找不到笔记 '{note_id}', 可能已被删除。"
    store.delete_note(note_id)
    return f"已删除笔记 '{note.title}' ({note_id})"


@mcp.tool(
    name="kb_get_note",
    annotations={
        "title": "读取知识库笔记",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def kb_get_note(
    note_id: str = Field(..., description="笔记 ID"),
) -> str:
    """读取一篇笔记的完整内容 (标题、标签、创建/更新时间、Markdown 正文)。

    Args:
        note_id (str): 笔记 ID

    Returns:
        str: JSON, 包含 note_id / title / tags / created / updated / content

    Examples:
        - "把那篇 Python 笔记的内容给我" -> note_id='xxx'
    """
    note = store.get_note(note_id)
    if note is None:
        return f"Error: 找不到笔记 '{note_id}'。"
    return json.dumps({
        "note_id": note.note_id,
        "title": note.title,
        "tags": note.tags,
        "created": note.created,
        "updated": note.updated,
        "content": note.content,
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="kb_list_notes",
    annotations={
        "title": "列出知识库笔记",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def kb_list_notes(
    tag: Optional[str] = Field(default=None, description="按标签过滤 (省略则列出全部)"),
) -> str:
    """列出知识库中的所有笔记 (按更新时间倒序)。

    返回每篇笔记的 ID、标题、标签与更新时间。可用 tag 参数只查看某个标签下的笔记。

    Args:
        tag (Optional[str]): 按标签过滤 (省略则列出全部)

    Returns:
        str: Markdown 列表, 每篇笔记一行, 含 ID / 标题 / 标签 / 更新时间

    Examples:
        - "知识库里有什么笔记?" -> 无参数
        - "列出所有 AI 相关的笔记" -> tag='AI'
    """
    notes = store.list_notes()
    if tag and not _is_missing(tag):
        notes = [n for n in notes if tag in n.tags]
    if not notes:
        return "知识库为空" if not tag else f"没有标签为 '{tag}' 的笔记"
    lines = [f"共 {len(notes)} 篇笔记:", ""]
    for n in notes:
        tags = " #" + " #".join(n.tags) if n.tags else ""
        lines.append(f"- **{n.title}**{tags}  (ID: `{n.note_id}`, 更新 {n.updated})")
    return "\n".join(lines)


@mcp.tool(
    name="kb_search_notes",
    annotations={
        "title": "搜索知识库笔记",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def kb_search_notes(
    query: str = Field(..., description="搜索关键词, 匹配标题/正文/标签", min_length=1, max_length=200),
) -> str:
    """在全部笔记的标题、正文、标签中全文搜索关键词 (不区分大小写)。

    Args:
        query (str): 搜索关键词

    Returns:
        str: Markdown 列表, 列出命中的笔记 ID / 标题 / 标签

    Examples:
        - "我有没有记过关于 Transformer 的笔记?" -> query='Transformer'
        - "找找 Python 相关的记录" -> query='python'
    """
    notes = store.search(query)
    if not notes:
        return f"没有找到包含 '{query}' 的笔记"
    lines = [f"找到 {len(notes)} 篇相关笔记:", ""]
    for n in notes:
        tags = " #" + " #".join(n.tags) if n.tags else ""
        lines.append(f"- **{n.title}**{tags}  (ID: `{n.note_id}`, 更新 {n.updated})")
    return "\n".join(lines)


@mcp.tool(
    name="kb_list_tags",
    annotations={
        "title": "列出知识库标签",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def kb_list_tags() -> str:
    """列出知识库中所有标签 (按字母序)。

    Args:
        无参数

    Returns:
        str: 标签列表文本

    Examples:
        - "知识库里都有哪些标签?" -> 无参数
    """
    tags = store.all_tags()
    if not tags:
        return "知识库还没有任何标签"
    return "全部标签: " + ", ".join(f"#{t}" for t in tags)


if __name__ == "__main__":
    mcp.run()
