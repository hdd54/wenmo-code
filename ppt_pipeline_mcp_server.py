# -*- coding: utf-8 -*-
"""PPT 渲染管线 MCP 服务器：HTML 设计稿 → 可编辑咨询级 PPT。
独立工具服务器（与 file-mcp 解耦），专注 PPT 生成。
基于 html2pptx_core（v2 修复版：文字颜色/换行/字号/富文本/图片内嵌）。
工具：
  html_to_pptx  HTML 设计稿 → 可编辑 PPT，返回下载链接
启动：由 gui_server.py 的 MCP 管理器自动拉起（注册于 mcp.json）。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsRequest, ListToolsResult, TextContent, Tool  # noqa: E402

server = Server("ppt-pipeline")


def _tool_result(text):
    return CallToolResult(content=[TextContent(type="text", text=str(text))])


TOOLS = [
    Tool(
        name="html_to_pptx",
        description=(
            "HTML 设计稿 → 可编辑 PPT（咨询级渲染管线）。"
            "html=设计稿内容（含 <style>，多页用 <!-- PAGE --> 分隔，每页 <section class='page'> 1920×1080，"
            "可编辑文字用 <span class='slot' data-slot='id'>标注；支持颜色/字号/加粗/<br>换行/富文本/图片内嵌）；"
            "out_name=输出文件名（可选，自动落到 files/ 下载区并返回预览链接）。"
            "设计规范与常见坑见技能 html-to-pptx-pipeline。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "html": {"type": "string", "description": "HTML 设计稿内容"},
                "out_name": {"type": "string", "description": "输出 pptx 文件名（可选）"},
            },
            "required": ["html"],
        },
    ),
]


async def handle_list_tools(*args, **kwargs) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def handle_call_tool(*args, **kwargs) -> CallToolResult:
    params = kwargs.get("params")
    if params is None:
        for a in args:
            if isinstance(a, CallToolRequestParams):
                params = a
                break
    if params is None or params.name != "html_to_pptx":
        return _tool_result("未知工具")
    args_dict = params.arguments or {}
    try:
        from html2pptx_core import render_html_to_pptx
        return _tool_result(render_html_to_pptx(args_dict))
    except Exception as e:
        return _tool_result("PPT 渲染失败：%s" % str(e)[:300])


server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)
server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
