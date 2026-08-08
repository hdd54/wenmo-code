"""文档转 Markdown 插件（微软 markitdown，MIT）。
把各种格式文件转换为 Markdown 文本：PDF/DOCX/XLSX/PPTX/EPUB/Outlook msg/HTML/CSV/ZIP/iPynb/图片/音频。
扩展了问墨原有的文件读取能力（text_tools 只支持 docx/pptx/xlsx/pdf/html/txt）。
"""

import io
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES_DIR = os.path.join(BASE_DIR, "files")


def _resolve(path):
    """解析文件路径：绝对 / files 目录 / 项目相对"""
    if os.path.isfile(path):
        return path
    candidates = [
        os.path.join(BASE_DIR, path),
        os.path.join(FILES_DIR, os.path.basename(path)),
        os.path.join(FILES_DIR, path),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def to_markdown_handler(arguments: dict) -> dict:
    """把文件转换为 Markdown 文本（支持 PDF/DOCX/XLSX/PPTX/EPUB/Outlook msg/HTML/CSV/ZIP/Jupyter/图片/音频）。"""
    path = str(arguments.get("path") or "").strip()
    if not path:
        return {"error": "path 不能为空（文件路径）"}
    fp = _resolve(path)
    if not fp:
        return {"error": f"文件不存在: {path}（请传绝对路径或 files/ 目录文件名）"}
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(fp)
        text = (result.text_content or "").strip()
        if not text:
            return {"ok": True, "content": "（文档无文本内容，可能是纯图片/无文字页）", "format": "markdown"}
        # 限制返回长度（防止超大文档爆上下文；长文档告知分段）
        MAX = 60000
        if len(text) > MAX:
            return {
                "ok": True,
                "content": text[:MAX],
                "truncated": True,
                "total_chars": len(text),
                "note": f"文档共 {len(text)} 字符，已返回前 {MAX}。如需读取后续部分，用 read_lines 或分段读取。",
                "format": "markdown",
            }
        return {"ok": True, "content": text, "format": "markdown",
                "note": "这是 markitdown 转换的 Markdown 文本，保留了标题/表格/列表结构。"}
    except Exception as e:
        return {"error": f"转换失败: {e}"}


PLUGIN_TOOLS = [
    {
        "name": "to_markdown",
        "description": "把文件转换为 Markdown 文本（微软 markitdown）：支持 PDF、Word(.docx)、Excel(.xlsx/.xls)、"
                       "PPT(.pptx)、EPUB、Outlook(.msg)、HTML、CSV、ZIP 压缩包、Jupyter(.ipynb)、图片(OCR)、音频(转写)。"
                       "比 read_document 支持更多格式。用法：path=文件路径（绝对路径或 files/ 目录文件名）。"
                       "返回 Markdown 格式内容（保留标题/表格/列表结构）。超大文档返回前 60000 字符。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（绝对路径 / files 目录文件名 / 项目相对路径）"},
            },
            "required": ["path"],
        },
        "handler": to_markdown_handler,
    }
]
