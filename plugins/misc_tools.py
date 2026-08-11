# -*- coding: utf-8 -*-
"""通用小工具插件（code agent 常用能力补齐）：
web_fetch 抓取网页/API 内容、current_datetime 当前时间、calculator 表达式计算、json_process JSON 处理。
"""

import ast
import json
import math
import operator
import re
import time
import urllib.request
from network_safety import safe_urlopen


def web_fetch(args):
    """抓取网页/API 内容（HTML 去标签、JSON 格式化），返回文本"""
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "错误：需要 http(s) URL"
    max_chars = min(int(args.get("max_chars", 6000) or 6000), 20000)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with safe_urlopen(req, timeout=20) as r:
            ct = r.headers.get("Content-Type", "")
            data = r.read(max_chars * 4)
        try:
            text = data.decode("utf-8")
        except Exception:
            text = data.decode("gbk", errors="ignore")
        if "json" in ct:
            try:
                obj = json.loads(text)
                return json.dumps(obj, ensure_ascii=False, indent=2)[:max_chars]
            except Exception:
                pass
        if "html" in ct or "<html" in text.lower():
            text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        return "URL: %s\n%s" % (url, text[:max_chars])
    except Exception as e:
        return "抓取失败: %s" % str(e)[:150]


def current_datetime(args):
    """返回当前日期时间（模型不应猜测时间）"""
    t = time.localtime()
    week = "一二三四五六日"[t.tm_wday]
    return "当前时间：%04d年%02d月%02d日 %02d:%02d:%02d（星期%s，本地时区）" % (
        t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, week)


def calculator(args):
    """表达式计算（安全：仅数字与运算符）"""
    expr = str(args.get("expression", "")).strip()
    if not expr:
        return "错误：需要 expression 参数（如 (3+5)*2 或 2^10）"
    if len(expr) > 500:
        return "错误：表达式过长"
    expr2 = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    expr2 = re.sub(r"\bpi\b", "3.141592653589793", expr2)
    expr2 = re.sub(r"\be\b", "2.718281828459045", expr2)
    if re.search(r"[^\d\s+\-*/().%]", expr2):
        return "错误：表达式包含不允许的字符（仅支持数字与 + - * / ( ) %）"
    try:
        binary = {
            ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
            ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        }
        unary = {ast.UAdd: operator.pos, ast.USub: operator.neg}

        def evaluate(node):
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
                return unary[type(node.op)](evaluate(node.operand))
            if isinstance(node, ast.BinOp) and type(node.op) in binary:
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Pow) and abs(right) > 10_000:
                    raise ValueError("exponent is too large")
                value = binary[type(node.op)](left, right)
                if isinstance(value, complex) or not math.isfinite(float(value)) or abs(value) > 1e100:
                    raise ValueError("result is too large")
                return value
            raise ValueError("unsupported expression")

        result = evaluate(ast.parse(expr2, mode="eval"))
        if isinstance(result, float) and result == int(result):
            result = int(result)
        return "%s = %s" % (expr, result)
    except Exception as e:
        return "计算失败: %s" % str(e)[:100]


def json_process(args):
    """JSON 处理：format（美化）/ minify（压缩）/ validate（校验）/ keys（顶层键）"""
    action = str(args.get("action", "format")).strip().lower()
    text = str(args.get("text", "")).strip()
    if not text:
        return "错误：需要 text 参数（JSON 字符串）"
    try:
        obj = json.loads(text)
    except Exception as e:
        return "JSON 解析失败: %s" % str(e)[:100]
    if action == "format":
        return json.dumps(obj, ensure_ascii=False, indent=2)
    if action == "minify":
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if action == "validate":
        return "JSON 有效（顶层类型: %s）" % type(obj).__name__
    if action == "keys":
        if isinstance(obj, dict):
            return "键列表: " + ", ".join(str(k) for k in obj.keys())
        return "顶层不是对象"
    return "未知 action（format/minify/validate/keys）"


PLUGIN_TOOLS = [
    {"name": "web_fetch",
     "description": "抓取网页/API 内容（URL → 文本；HTML 自动去标签，JSON 自动格式化）。"
                    "用于：读取网页文章、调用 HTTP API、获取网页信息。参数 url=http(s) 地址；"
                    "max_chars=最多返回字符数（默认 6000，最大 20000）。",
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string", "description": "http(s) 网址"},
         "max_chars": {"type": "integer", "description": "最多返回字符数，默认 6000"}},
         "required": ["url"]}, "handler": web_fetch},
    {"name": "current_datetime",
     "description": "返回当前日期和时间（本地时区）。当需要知道今天日期、当前时间、星期几时使用，"
                    "不要凭空猜测时间。",
     "parameters": {"type": "object", "properties": {}}, "handler": current_datetime},
    {"name": "calculator",
     "description": "数学表达式计算（安全计算器）。参数 expression 如 (3+5)*2、2^10、100/7。"
                    "当需要精确计算数字时使用，不要心算。",
     "parameters": {"type": "object", "properties": {
         "expression": {"type": "string", "description": "数学表达式（数字与 + - * / ( ) % ^）"}},
         "required": ["expression"]}, "handler": calculator},
    {"name": "json_process",
     "description": "JSON 处理：format（美化缩进）/ minify（压缩）/ validate（校验）/ keys（顶层键列表）。"
                    "参数 text=JSON 字符串；action=操作（默认 format）。",
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "JSON 字符串"},
         "action": {"type": "string", "description": "format/minify/validate/keys"}},
         "required": ["text"]}, "handler": json_process},
]
