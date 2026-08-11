# -*- coding: utf-8 -*-
"""PDF 生成插件：Markdown/HTML → Edge headless 打印 PDF（零额外依赖，中文/样式/图片均支持）。
模型不要自己手写 .pdf（二进制格式）。"""

import html as _html
import os
from tenant_state import files_dir
import re
import subprocess
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]

_CSS = """
body{font-family:'Microsoft YaHei','PingFang SC',sans-serif;padding:36px;line-height:1.75;color:#222;font-size:14px}
h1{color:#1F4E79;font-size:24px;border-bottom:2px solid #1F4E79;padding-bottom:8px}
h2{color:#1F4E79;font-size:19px;border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:24px}
h3{color:#333;font-size:16px}
table{border-collapse:collapse;width:100%;margin:10px 0}
td,th{border:1px solid #bbb;padding:6px 10px;font-size:13px}
th{background:#f0f4f8}
img{max-width:100%;border-radius:4px}
code{background:#f5f5f5;padding:2px 6px;border-radius:3px;font-family:Consolas,monospace;font-size:13px}
pre{background:#f8f8f8;padding:12px;border-radius:6px;overflow-x:auto;border:1px solid #eee}
blockquote{border-left:4px solid #1F4E79;margin:10px 0;padding:6px 14px;color:#555;background:#f7f9fc}
hr{border:none;border-top:1px solid #ddd;margin:20px 0}
ul,ol{padding-left:24px}
"""


def _safe(s, n=60):
    s = re.sub(r'[\\/:*?"<>|]', "_", str(s or "文档"))
    return s[:n] or "文档"


def _find_edge():
    for c in EDGE_CANDIDATES:
        if os.path.isfile(c):
            return c
    return None


def _md_to_html(md):
    import markdown
    return markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])


def create_pdf(args):
    """Markdown/HTML 内容 → PDF（Edge headless 打印），保存到下载区并返回链接。"""
    title = str(args.get("title", "")).strip() or "文档"
    content = str(args.get("content") or args.get("markdown") or "").strip()
    html_body = str(args.get("html", "")).strip()
    if not html_body and not content:
        return {"error": "需要 content（Markdown/纯文本）或 html 参数"}
    edge = _find_edge()
    if not edge:
        return {"error": "未找到 Microsoft Edge（HTML→PDF 打印依赖 Edge）"}
    if html_body:
        body_html = html_body
    else:
        body_html = _md_to_html(content)
    full = ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><style>%s</style></head><body>"
            % _CSS)
    if title:
        full += "<h1>%s</h1>" % _html.escape(title)
    full += body_html + "</body></html>"
    fd, tmp_html = tempfile.mkstemp(suffix=".html")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(full)
        out_name = "%s_%d.pdf" % (_safe(title), int(time.time()))
        out_path = os.path.join(files_dir(), out_name)
        r = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-sandbox",
             "--print-to-pdf=" + out_path, "--print-to-pdf-no-header",
             "file:///" + tmp_html.replace("\\", "/")],
            capture_output=True, timeout=90)
    except Exception as e:
        return {"error": "PDF 生成失败: %s" % str(e)[:200]}
    finally:
        try:
            os.remove(tmp_html)
        except Exception:
            pass
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        err = (r.stderr or b"").decode("utf-8", "ignore") if r else ""
        return {"error": "PDF 生成失败（Edge 打印未产出文件）: %s" % err[:200]}
    url = "http://127.0.0.1:8000/files/" + out_name
    return {"ok": True, "file": out_name, "url": url,
            "note": "PDF 已生成：%s。请在回答里引用链接，用户点击即可下载/预览。" % out_name}


PLUGIN_TOOLS = [
    {
        "name": "create_pdf",
        "description": "创建 PDF 文档（Markdown/HTML → PDF，Edge 打印引擎，中文/表格/图片/代码高亮均支持）。"
                       "当用户要求『做一个 PDF/导出 PDF/打印版文档』时使用本工具，不要手写 .pdf。"
                       "参数：title=文档标题（可选）；content=Markdown 或纯文本内容（推荐）；"
                       "或 html=完整 HTML 片段（更精细排版）。生成后返回链接，请在回答中引用。",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "文档标题（显示在首页顶部）"},
                "content": {"type": "string", "description": "文档内容（Markdown 或纯文本；支持标题/列表/表格/代码块/图片 URL）"},
                "html": {"type": "string", "description": "完整 HTML 片段（二选一：优先 content，若要精细排版用 html）"}
            },
            "required": ["content"]
        },
        "handler": create_pdf,
    }
]
