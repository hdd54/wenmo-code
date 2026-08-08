"""
示例插件：系统信息。
插件契约：PLUGIN_TOOLS = [{name, description, parameters, handler}]
"""
import os
import platform

import shutil


def get_system_info(args):
    return (
        f"主机名: {platform.node()}\n"
        f"系统: {platform.system()} {platform.release()}\n"
        f"架构: {platform.machine()}\n"
        f"Python: {platform.python_version()}"
    )


def get_disk_usage(args):
    path = args.get("path") or os.path.expanduser("~")
    try:
        t, used, free = shutil.disk_usage(path)
        return (
            f"路径: {path}\n"
            f"总容量: {t / 2**30:.1f} GB\n"
            f"已用: {used / 2**30:.1f} GB\n"
            f"剩余: {free / 2**30:.1f} GB"
        )
    except Exception as e:
        return f"错误：{e}"


PLUGIN_TOOLS = [
    {
        "name": "get_system_info",
        "description": "获取本机系统信息（主机名、操作系统、架构）",
        "parameters": {"type": "object", "properties": {}},
        "handler": get_system_info,
    },
    {
        "name": "get_disk_usage",
        "description": "查看磁盘空间占用（输入路径，默认用户目录）",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "要查询的路径"}},
        },
        "handler": get_disk_usage,
    },
]
