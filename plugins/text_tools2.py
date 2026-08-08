# -*- coding: utf-8 -*-
"""文本/内容工具插件（第二批 skill 能力落地）：
- text_summarize:    文本摘要（提取关键句，本地实现不调 API）
- text_extract_keywords: 关键词提取（中文分词近似 + 英文词频）
- text_diff:         两文本/两文件对比（diff）
- url_fetch_markdown: 抓网页并转纯文本（对标 web_fetch，更简洁版）
- file_hash:         文件内容哈希（SHA-256，对标 content-hash-cache-pattern）
- list_pdf_text:     PDF 文本提取（若 pypdf 可用）
"""

import hashlib
import os
import re
import urllib.request


def _split_sentences(text):
    """按中英文句号/感叹/问号切句"""
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in re.split(r'[。！？!?\.\n]', text) if len(s.strip()) > 15]


def text_summarize(args):
    """文本摘要：按句子长度+关键词密度提取前 N 句（本地实现）。"""
    text = str(args.get("text", "")).strip()
    file_path = str(args.get("file", "")).strip()
    max_sent = max(1, min(int(args.get("max_sentences", 3) or 3), 10))
    if not text and file_path:
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            return f"读取失败: {e}"
    if len(text) < 50:
        return text or "文本为空"
    sents = _split_sentences(text)
    if not sents:
        return text[:500]
    # 评分：句子长度适中（30-200字）优先，含数字/专有名词加分
    def score(s):
        sc = 0
        if 30 <= len(s) <= 200:
            sc += 2
        elif len(s) < 30:
            sc -= 1
        if re.search(r'\d|%|¥|\$', s):
            sc += 1
        return sc
    # 保留原文顺序，取评分最高的 max_sent 句
    ranked = sorted(range(len(sents)), key=lambda i: score(sents[i]), reverse=True)[:max_sent]
    ranked.sort()
    return "……".join(sents[i] for i in ranked) if ranked else text[:400]


def text_extract_keywords(args):
    """关键词提取：中文用 2-4 字高频词（近似分词），英文用词频。"""
    text = str(args.get("text", "")).strip()
    top_n = max(1, min(int(args.get("top_n", 10) or 10), 30))
    if not text:
        return "错误：需要 text"
    # 中文：统计 2-4 字连续词频（跳过停用字）
    cn_stop = set("的了是在我和有就不人都一个上也很到说要去你会着没有自己这那与及或对而于")
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
    cn_freq = {}
    for w in cn_words:
        if w[0] in cn_stop and len(w) == 2:
            continue
        cn_freq[w] = cn_freq.get(w, 0) + 1
    # 英文：词频
    en_words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    en_stop = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "have", "has", "not"}
    en_freq = {}
    for w in en_words:
        if w not in en_stop:
            en_freq[w] = en_freq.get(w, 0) + 1
    cn_top = sorted(cn_freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
    en_top = sorted(en_freq.items(), key=lambda x: x[1], reverse=True)[:top_n]
    parts = []
    if cn_top:
        parts.append("中文关键词: " + ", ".join(f"{w}({c})" for w, c in cn_top))
    if en_top:
        parts.append("英文关键词: " + ", ".join(f"{w}({c})" for w, c in en_top))
    return "\n".join(parts) if parts else "未提取到关键词"


def text_diff(args):
    """两文本或两文件对比（简单行级 diff：+ 新增 / - 删除）。"""
    a = str(args.get("text_a", "")).strip()
    b = str(args.get("text_b", "")).strip()
    file_a = str(args.get("file_a", "")).strip()
    file_b = str(args.get("file_b", "")).strip()
    if not a and file_a:
        try:
            a = open(file_a, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            return f"读取 file_a 失败: {e}"
    if not b and file_b:
        try:
            b = open(file_b, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            return f"读取 file_b 失败: {e}"
    if not a and not b:
        return "错误：需要 text_a/text_b 或 file_a/file_b"
    import difflib
    la = a.splitlines()
    lb = b.splitlines()
    diff = list(difflib.unified_diff(la, lb, lineterm="", n=2))
    if len(diff) <= 2:
        return "✅ 两文本完全一致"
    out = ["（+ 新增 / - 删除）"]
    for ln in diff[2:60]:
        out.append(ln)
    if len(diff) > 62:
        out.append(f"…（共 {len(diff) - 2} 行差异）")
    return "\n".join(out)


def url_fetch_markdown(args):
    """抓取网页内容并转纯文本（简化版 web_fetch，去掉标签/脚本/样式）。"""
    url = str(args.get("url", "")).strip()
    max_chars = min(int(args.get("max_chars", 4000) or 4000), 12000)
    if not url.startswith(("http://", "https://")):
        return "错误：需要 http(s) URL"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(max_chars * 4)
        try:
            text = data.decode("utf-8")
        except Exception:
            text = data.decode("gbk", errors="ignore")
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars] if text else "（页面无文本内容）"
    except Exception as e:
        return f"抓取失败: {str(e)[:150]}"


def file_hash(args):
    """计算文件的 SHA-256 哈希（对标 content-hash-cache-pattern：内容寻址/缓存键）。"""
    path = str(args.get("path", "")).strip()
    if not path or not os.path.isfile(path):
        return f"错误：文件不存在 {path}"
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        size = os.path.getsize(path)
        return f"SHA-256: {h.hexdigest()}\n大小: {size:,} 字节\n路径: {path}"
    except Exception as e:
        return f"计算失败: {e}"


def pdf_extract_text(args):
    """从 PDF 提取文本（若已安装 pypdf）。用于快速读取 PDF 内容。"""
    path = str(args.get("path", "")).strip()
    max_chars = min(int(args.get("max_chars", 4000) or 4000), 15000)
    if not path or not os.path.isfile(path):
        return f"错误：文件不存在 {path}"
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        parts = [f"页数: {len(reader.pages)}"]
        out = ""
        for i, page in enumerate(reader.pages[:10]):
            try:
                t = page.extract_text() or ""
                out += t
            except Exception:
                pass
            if len(out) > max_chars:
                break
        parts.append(out[:max_chars])
        return "\n".join(parts)
    except ImportError:
        return "未安装 pypdf。可用命令安装：pip install pypdf"
    except Exception as e:
        return f"PDF 读取失败: {str(e)[:150]}"


PLUGIN_TOOLS = [
    {"name": "text_summarize",
     "description": "文本摘要：从长文本中提取最关键的几句（按句子长度/关键词密度评分，保留原文顺序）。"
                    "参数 text=文本，或 file=文件路径；max_sentences=摘要句数（默认3）。",
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "要摘要的文本"},
         "file": {"type": "string", "description": "或：文件路径"},
         "max_sentences": {"type": "integer", "description": "摘要句数，默认3"}}}, "handler": text_summarize},
    {"name": "text_extract_keywords",
     "description": "关键词提取：中文 2-4 字高频词 + 英文词频（自动去除停用词）。"
                    "参数 text=文本；top_n=返回数量（默认10）。",
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "要提取关键词的文本"},
         "top_n": {"type": "integer", "description": "返回关键词数量，默认10"}},
         "required": ["text"]}, "handler": text_extract_keywords},
    {"name": "text_diff",
     "description": "两文本/两文件对比（行级 diff：+ 新增 / - 删除）。参数 text_a/text_b 或 file_a/file_b。",
     "parameters": {"type": "object", "properties": {
         "text_a": {"type": "string", "description": "文本 A"},
         "text_b": {"type": "string", "description": "文本 B"},
         "file_a": {"type": "string", "description": "或：文件 A 路径"},
         "file_b": {"type": "string", "description": "或：文件 B 路径"}}}, "handler": text_diff},
    {"name": "url_fetch_markdown",
     "description": "抓取网页并转纯文本（去标签/脚本/样式）。参数 url=http(s) 地址；max_chars=最多字符（默认4000）。",
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string", "description": "http(s) 网址"},
         "max_chars": {"type": "integer", "description": "最多返回字符数，默认4000"}},
         "required": ["url"]}, "handler": url_fetch_markdown},
    {"name": "file_hash",
     "description": "计算文件 SHA-256 哈希 + 大小。用于内容寻址、缓存键、文件完整性校验（对标 content-hash-cache skill）。"
                    "参数 path=文件路径。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "文件路径"}},
         "required": ["path"]}, "handler": file_hash},
    {"name": "pdf_extract_text",
     "description": "从 PDF 提取文本内容（需已安装 pypdf；未安装则提示）。用于快速读取 PDF 文档。"
                    "参数 path=PDF 文件路径；max_chars=最多字符（默认4000）。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "PDF 文件路径"},
         "max_chars": {"type": "integer", "description": "最多提取字符数，默认4000"}},
         "required": ["path"]}, "handler": pdf_extract_text},
]
