# -*- coding: utf-8 -*-
"""打包环境适配：数据目录重定向到 %APPDATA%/问墨
打包版（frozen）用 %APPDATA%；开发版用项目目录。
这个模块在 gui_server.py 最顶部 import，重定向所有数据路径。
"""
import os
import sys

def is_frozen():
    """是否为 PyInstaller 打包环境"""
    return getattr(sys, 'frozen', False)

def get_data_dir():
    """数据目录：打包版 → %APPDATA%/问墨；开发版 → 项目目录"""
    if is_frozen():
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        d = os.path.join(base, '问墨')
        os.makedirs(d, exist_ok=True)
        return d
    return os.path.dirname(os.path.abspath(__file__))

# 兼容开发版：frozen 时设置资源目录（PyInstaller 解包位置）
def get_resource_dir():
    """资源目录：打包版 → _MEIPASS（解包临时目录）；开发版 → 项目目录"""
    if is_frozen():
        return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.abspath(__file__))
