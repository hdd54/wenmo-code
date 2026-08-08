"""
演示用 MCP 服务器：提供几个本地小工具（计算器 / 时间 / 列文件）。
用新版 mcp SDK 的低层 API 编写，零外部依赖。

启动方式（由 gui_server.py 的 MCP 管理器自动拉起）：
    python mcp_demo_server.py
"""
import asyncio
import datetime
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
    Tool,
)

server = Server("本地工具")

TOOLS = [
    Tool(
        name="calculate",
        description="计算数学表达式，例如 1+2*3、2**10、(5+3)*2",
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "要计算的数学表达式"}
            },
            "required": ["expression"],
        },
    ),
    Tool(
        name="current_time",
        description="返回当前日期和时间（北京时间，UTC+8）",
        input_schema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_files",
        description="列出文件夹里的文件名（不递归）",
        input_schema={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "文件夹路径"},
                "limit": {"type": "integer", "description": "最多列出几个，默认 10"},
            },
            "required": ["folder"],
        },
    ),
]


async def handle_list_tools(*args, **kwargs) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def handle_call_tool(*args, **kwargs) -> CallToolResult:
    """兼容不同 SDK 版本：处理器可能收到 (params) 或 (server, params) 或 (params, context)"""
    def text(msg) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=str(msg))])

    params = kwargs.get("params")
    if params is None:
        for a in args:
            if isinstance(a, CallToolRequestParams):
                params = a
                break
    if params is None:
        return text("内部错误：无法解析请求参数")

    name = params.name
    args_dict = params.arguments or {}
    try:
        if name == "calculate":
            expr = str(args_dict.get("expression", ""))
            allowed = set("0123456789+-*/().% ")
            if not all(c in allowed for c in expr):
                return text("错误：表达式包含不允许的字符（仅支持数字与 + - * / ( ) %）")
            return text(eval(expr, {"__builtins__": {}}, {}))
        if name == "current_time":
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
            return text(now.strftime("%Y-%m-%d %H:%M:%S"))
        if name == "list_files":
            folder = str(args_dict.get("folder", ""))
            limit = int(args_dict.get("limit", 10))
            names = sorted(os.listdir(folder))[:limit]
            return text("\n".join(names) if names else "（空文件夹）")
        return text(f"未知工具: {name}")
    except Exception as e:
        return text(f"错误：{e}")


# 新版 SDK：请求处理器需显式注册（add_request_handler 不是装饰器）
server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)
server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
