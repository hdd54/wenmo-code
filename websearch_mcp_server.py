"""
websearch MCP 服务器：给 AI 一个联网搜索的工具。
- 多源并行聚合（对标 GPT 搜索）：一次调用同时搜索 必应国际 + 必应国内 + 百度 + 搜狗，
  聚合各源结果返回，模型不需要反复搜索。
- 若环境变量 EXA_API_KEY 存在：额外加入 Exa 神经搜索（更精准）
纯标准库实现（urllib + re），零额外依赖。

启动方式（由 gui_server.py 的 MCP 管理器自动拉起）：
    python websearch_mcp_server.py
"""
import asyncio
import concurrent.futures
import html
import json
import os
import re
import urllib.parse
import urllib.request

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsRequest, ListToolsResult, TextContent, Tool

server = Server("websearch")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

TOOLS = [
    Tool(
        name="web_search",
        description="在互联网上搜索信息（联网工具，多源并行聚合：必应+百度+搜狗一次返回）。"
                    "用于查询最新新闻、事实、教程、文档等。\n"
                    "⚠️ 关键：query 必须是【语义完整的查询】（完整问句或完整概念短语），"
                    "绝不能拆成零散关键词！\n"
                    "正确示例：用户问『光学工程专业的概念』→ query='什么是光学工程专业'；"
                    "用户问前景 → query='光学工程专业的前景是什么'；问区别 → query='A与B的区别'。\n"
                    "错误示例：'光学 工程'（拆词）、'前景'（不完整）。\n"
                    "参数 query 为搜索查询；一次调用即返回多源结果，不要反复调用。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "语义完整的搜索查询（完整问句/完整概念，不拆词）"},
                "num_results": {"type": "integer", "description": "每源返回条数，默认 3，最多 5"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="web_search_images",
        description="在互联网上搜索【图片】（必应图片搜索）。用于需要配图的任务：新闻配图、报告/教程插图、PPT 配图等。"
                    "返回图片列表（每条含：标题、原图 URL、缩略图 URL、来源页面）。"
                    "拿到 URL 后：嵌入文档（docx 的 image 块 / pptx 的每页 image 参数均支持 http(s) URL，会自动下载）；"
                    "或作为图片素材直接引用。注意：返回的是图片 URL 而非图片内容，文档工具会自行下载插入。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "图片搜索关键词（尽量具体，如：美伊冲突 新闻配图）"},
                "num_results": {"type": "integer", "description": "返回图片数，默认 3，最多 6"},
            },
            "required": ["query"],
        },
    ),
]


def _strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def _search_bing(query, num):
    """必应网页搜索（国内版优先——中文分词质量好于国际版，国际版会把长句拆词返回无关结果）"""
    for base in ("https://cn.bing.com/search", "https://www.bing.com/search"):
        try:
            url = base + "?q=" + urllib.parse.quote(query) + "&count=" + str(num) + "&setlang=zh-hans"
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                page = resp.read().decode("utf-8", errors="ignore")
            blocks = re.findall(r'<li class="b_algo"[\s\S]*?</li>', page)[:num]
            if blocks:
                out = []
                for i, b in enumerate(blocks, 1):
                    m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a></h2>', b)
                    if not m:
                        continue
                    title = _strip_tags(m.group(2))
                    link = m.group(1)
                    p = re.search(r"<p[^>]*>([\s\S]*?)</p>", b)
                    snippet = _strip_tags(p.group(1)) if p else ""
                    out.append(f"{i}. {title}\n   {link}\n   {snippet}")
                if out:
                    return out
        except Exception:
            continue
    return []


def _search_baidu(query, num):
    """百度网页搜索（国内最常用）"""
    try:
        url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(query) + "&rn=" + str(num)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read().decode("utf-8", errors="ignore")
        # 百度结果：h3 标题 + 链接 + 摘要
        out = []
        seen = set()
        for m in re.finditer(r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>.*?</h3>', page):
            link = m.group(1)
            if link in seen or len(out) >= num:
                continue
            seen.add(link)
            title = _strip_tags(m.group(2))
            if not title:
                continue
            snippet = ""
            pm = re.search(r'<span class="content-right_8Zs40">([\s\S]*?)</span>', page[m.end():m.end() + 3000])
            if not pm:
                pm = re.search(r'<span[^>]*class="[^"]*content-right[^"]*"[^>]*>([\s\S]*?)</span>', page[m.end():m.end() + 3000])
            if pm:
                snippet = _strip_tags(pm.group(1))[:200]
            out.append(f"{len(out) + 1}. {title}\n   {link}\n   {snippet}")
        return out
    except Exception:
        return []


def _search_sogou(query, num):
    """搜狗网页搜索（国内补充源）"""
    try:
        url = "https://www.sogou.com/web?query=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read().decode("utf-8", errors="ignore")
        out = []
        for m in re.finditer(r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>.*?</h3>', page):
            if len(out) >= num:
                break
            title = _strip_tags(m.group(2))
            if not title:
                continue
            link = m.group(1)
            if link.startswith("/link"):
                link = "https://www.sogou.com" + link
            out.append(f"{len(out) + 1}. {title}\n   {link}")
        return out
    except Exception:
        return []


def _search_so360(query, num):
    """360 搜索（so.com）：中文分词质量实测最好——完整短语不被拆词（对比必应/百度）。
    例如『知识蒸馏 2025 论文』→ 直接命中 ICLR2025/CSDN 论文；必应会拆成『知识』返回百科。"""
    try:
        url = "https://www.so.com/s?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read().decode("utf-8", errors="ignore")
        out = []
        for m in re.finditer(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', page):
            if len(out) >= num:
                break
            title = _strip_tags(m.group(2))
            if not title:
                continue
            link = m.group(1)
            # 360 跳转链接 → 还原为原始 URL（从 href 里的 url 参数解出）
            if "/link?" in link:
                mm = re.search(r"[?&]url=([^&]+)", link)
                if mm:
                    try:
                        link = urllib.parse.unquote(mm.group(1))
                    except Exception:
                        pass
            # 摘要（360 的摘要块）
            snippet = ""
            pm = re.search(r'<p class="res-desc"[\s\S]*?</p>', page[m.end():m.end() + 2500])
            if not pm:
                pm = re.search(r'<p[^>]*class="[^"]*res-desc[^"]*"[^>]*>([\s\S]*?)</p>', page[m.end():m.end() + 2500])
            if pm:
                snippet = _strip_tags(pm.group(1))[:200] if pm.lastindex else ""
            if snippet:
                out.append(f"{len(out) + 1}. {title}\n   {link}\n   {snippet}")
            else:
                out.append(f"{len(out) + 1}. {title}\n   {link}")
        return out
    except Exception:
        return []


def _search_exa(query, num):
    """Exa 神经搜索（需要 EXA_API_KEY）"""
    body = json.dumps({"query": query, "numResults": num}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.exa.ai/search", data=body,
        headers={"Content-Type": "application/json", "x-api-key": os.environ["EXA_API_KEY"], "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for i, r in enumerate(data.get("results", [])[:num], 1):
        out.append(f"{i}. {r.get('title', '')}\n   {r.get('url', '')}\n   {(r.get('text') or '')[:300]}")
    return out


def _search_bing_images(query, num):
    """必应图片搜索：返回 [{title, url(原图), thumb(缩略图), page(来源页)}]"""
    try:
        url = ("https://www.bing.com/images/search?q=" + urllib.parse.quote(query)
               + "&form=HDRSC2&qft=+filterui:imagesize-large")
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            page = resp.read().decode("utf-8", errors="ignore")
        out = []
        for m in re.finditer(r'class="iusc"[^>]*m="([^"]+)"', page):
            if len(out) >= num:
                break
            meta = m.group(1)
            try:
                meta = json.loads(html.unescape(meta))
            except Exception:
                continue
            murl = meta.get("murl") or ""
            turl = meta.get("turl") or ""
            title = meta.get("t") or ""
            purl = meta.get("purl") or ""
            if murl:
                out.append({"title": _strip_tags(title), "url": murl, "thumb": turl, "page": purl})
        return out
    except Exception:
        return []


def _do_search_images(query, num):
    """图片搜索（必应图片源）"""
    items = _search_bing_images(query, num)
    if not items:
        return "（图片搜索无结果，可能被反爬拦截，请稍后重试或换关键词）"
    parts = []
    for i, it in enumerate(items, 1):
        parts.append(f"{i}. {it['title'] or '（无标题）'}\n   原图URL: {it['url']}\n   缩略图: {it['thumb'] or '无'}"
                     f"\n   来源页: {it['page'] or '未知'}")
    return "\n".join(parts)


def _do_search(query, num):
    """多源并行聚合搜索（对标 GPT 搜索）：360 + 必应 + 百度 + 搜狗（+ Exa 若有 key）同时搜，合并返回。
    源顺序说明：360 搜索中文分词质量实测最好（完整短语不被拆词，直接命中论文/专业内容）；
    必应国内版次之（部分长查询仍会拆词）；百度/搜狗常被反爬拦截，能返回就返回。"""
    sources = [_search_so360, _search_bing, _search_baidu, _search_sogou]
    if os.environ.get("EXA_API_KEY"):
        sources.append(_search_exa)
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = {ex.submit(f, query, num): f.__name__ for f in sources}
        for fut in concurrent.futures.as_completed(futs):
            name = futs[fut]
            try:
                items = fut.result()
                if items:
                    results[name] = items
            except Exception:
                continue
    if not results:
        return "（搜索无结果，可能被反爬拦截，请稍后再试）"
    label = {
        "_search_so360": "360", "_search_bing": "必应", "_search_baidu": "百度",
        "_search_sogou": "搜狗", "_search_exa": "Exa"
    }
    parts = []
    for name, items in results.items():
        parts.append(f"【{label.get(name, name)}】")
        parts.extend(items)
    return "\n".join(parts)


async def handle_list_tools(*args, **kwargs) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def handle_call_tool(*args, **kwargs) -> CallToolResult:
    def text(msg) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=str(msg))])

    params = kwargs.get("params")
    if params is None:
        for a in args:
            if isinstance(a, CallToolRequestParams):
                params = a
                break
    if params is None or params.name not in ("web_search", "web_search_images"):
        return text("未知工具")

    args_dict = params.arguments or {}
    query = str(args_dict.get("query", "")).strip()
    if not query:
        return text("错误：query 不能为空")
    try:
        if params.name == "web_search_images":
            num = min(int(args_dict.get("num_results", 3) or 3), 6)
            return text(_do_search_images(query, num))
        num = min(int(args_dict.get("num_results", 5) or 5), 20)
        return text(_do_search(query, num))
    except Exception as e:
        return text(f"搜索失败：{e}")


server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)
server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
