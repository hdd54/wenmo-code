# -*- coding: utf-8 -*-
"""patch_file_mcp.py v4：向 file_mcp_server.py 注入 html_to_pptx 工具（幂等）
v4 修复：new_tool 以 `    ),` 结尾（不含 ]），由 anchor 提供列表闭合 ]。
"""
import io
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_mcp_server.py")

with io.open(SRC, "r", encoding="utf-8") as f:
    code = f.read()

marker = '"html_to_pptx"'
if marker in code:
    print("ALREADY_PATCHED")
    sys.exit(0)

# 1) TOOLS 列表：在 file_convert 定义后、列表闭合 ] 之前插入 html_to_pptx
new_tool = '''    Tool(
        name="html_to_pptx",
        description="把 HTML 设计稿（1920x1080，文字块用 data-slot=\\"名称\\" 标注）渲染为可编辑 PPT。"
                    "多页用 <!-- PAGE --> 分隔。内部：Edge headless 渲染纯背景图 + 提取槽位坐标/样式 "
                    "+ python-pptx 拼装原生文本框（文字可编辑/搜索/复制）→ 输出到 files/ 下载区返回链接。"
                    "参数：html=完整 HTML 设计稿；out_name=输出文件名（可选，默认 html_to_pptx.pptx）。"
                    "生成后可再用 file_convert 转 PDF。",
        input_schema={"type": "object", "properties": {
            "html": {"type": "string", "description": "完整 HTML 设计稿，1920x1080，文字块用 data-slot 标注，多页用 <!-- PAGE --> 分隔"},
            "out_name": {"type": "string", "description": "输出 pptx 文件名（可选）"}},
            "required": ["html"]},
    ),'''

anchor = ''']


async def handle_list_tools'''
assert anchor in code, "anchor1 not found"
code = code.replace(anchor, new_tool + anchor, 1)

# 2) 白名单
old_whitelist = '("file_read", "file_write", "file_zip", "file_list", "file_move", "file_delete", "file_convert")'
assert old_whitelist in code, "whitelist not found"
code = code.replace(old_whitelist,
                    '("file_read", "file_write", "file_zip", "file_list", "file_move", "file_delete", "file_convert", "html_to_pptx")', 1)

# 3) 分发分支：在 file_convert 分支后追加
old_dispatch = '''        if params.name == "file_convert":
            return _tool_result(handle_file_convert(args_dict))
        return _tool_result(handle_file_list(args_dict))'''
new_dispatch = '''        if params.name == "file_convert":
            return _tool_result(handle_file_convert(args_dict))
        if params.name == "html_to_pptx":
            return _tool_result(handle_html_to_pptx(args_dict))
        return _tool_result(handle_file_list(args_dict))'''
assert old_dispatch in code, "dispatch not found"
code = code.replace(old_dispatch, new_dispatch, 1)

# 4) handler 函数：插在 server.add_request_handler 之前
handler_fn = '''
def handle_html_to_pptx(args):
    """HTML 设计稿 → 可编辑 PPT（复用 html2pptx_core 渲染管线）"""
    from html2pptx_core import render_html_to_pptx
    return render_html_to_pptx(args)


server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)'''
old_reg = '''server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)'''
assert old_reg in code, "register not found"
code = code.replace(old_reg, handler_fn, 1)

with io.open(SRC, "w", encoding="utf-8") as f:
    f.write(code)

print("PATCH_OK")
