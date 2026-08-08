"""股票分析插件：让问墨 AI 能直接分析股票行情。
调用 deps/stock_analyzer.py → daily_stock_analysis CLI（akshare/腾讯财经数据源 + DeepSeek AI 分析）。
"""

import io
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

_DSA_DIR = os.path.join(BASE_DIR, "deps", "daily_stock_analysis")
_STOCK_ANALYZER = os.path.join(BASE_DIR, "deps", "stock_analyzer.py")


def stock_analyze_handler(arguments: dict) -> dict:
    """分析指定股票（A股/港股/美股），返回行情 + AI 分析建议。"""
    stocks = str(arguments.get("stocks") or "").strip()
    if not stocks:
        return {"error": "stocks 不能为空（逗号分隔，如 '600519,000858' 或 'hk00700' 或 'AAPL'）"}
    # 白名单：只允许股票代码格式（防注入）
    import re
    if not re.match(r"^[0-9A-Za-z,，\s]+$", stocks):
        return {"error": "stocks 只能包含股票代码和逗号（如 600519,000858）"}
    stocks = stocks.replace("，", ",").replace(" ", "")
    # 限制最多 10 只（防长时间运行）
    codes = [s for s in stocks.split(",") if s]
    if len(codes) > 10:
        return {"error": "一次最多分析 10 只股票（当前 %d 只）" % len(codes)}
    try:
        # 从 deps.stock_analyzer 导入（动态加载，避免 import 失败影响插件加载）
        import importlib.util
        spec = importlib.util.spec_from_file_location("stock_analyzer", _STOCK_ANALYZER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # 检查 DSA 是否安装
        health = mod.check_health()
        if not health.get("ok"):
            return {"error": health.get("detail", "DSA 未就绪")}
        timeout = min(int(arguments.get("timeout") or 300), 600)
        result = mod.run_stock_analysis(stocks=",".join(codes), timeout=timeout)
        if result.get("ok"):
            return {
                "ok": True,
                "stocks": result.get("stocks"),
                "result": result.get("result", ""),
                "elapsed": result.get("elapsed"),
                "note": "股票分析结果（实时行情 + AI 分析），请向用户清晰汇总："
                        "各股现价/涨跌、趋势判断、操作建议。若部分数据源失败导致信息不全，如实说明。",
            }
        return {"error": result.get("error", "分析失败"), "partial": result.get("partial", "")}
    except Exception as e:
        return {"error": "股票分析调用失败: %s" % e}


def stock_health_handler(arguments: dict) -> dict:
    """检查股票分析功能是否可用。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("stock_analyzer", _STOCK_ANALYZER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        h = mod.check_health()
        return {"ok": True, "detail": h.get("detail", ""), "dir": h.get("dir", "")}
    except Exception as e:
        return {"error": "股票模块不可用: %s" % e}


PLUGIN_TOOLS = [
    {
        "name": "stock_analyze",
        "description": "分析股票（A股/港股/美股）：获取实时行情（价格/涨跌/量比/换手率）并用 AI 给出"
                       "趋势判断与操作建议。用法：stocks=股票代码，逗号分隔（如 '600519,000858' 贵州茅台五粮液；"
                       "'hk00700' 腾讯；'AAPL' 苹果）。分析耗时约 1-5 分钟（拉行情+AI 分析）。"
                       "适合用户问『看看某只股票怎么样』『分析一下我的自选股』。",
        "parameters": {
            "type": "object",
            "properties": {
                "stocks": {"type": "string", "description": "股票代码，逗号分隔（最多 10 只）。A股用 6 位数字（600519），港股 hk 前缀（hk00700），美股代码（AAPL）"},
                "timeout": {"type": "integer", "description": "超时秒数（可选，默认 300，最大 600）"},
            },
            "required": ["stocks"],
        },
        "handler": stock_analyze_handler,
    },
    {
        "name": "stock_health",
        "description": "检查股票分析功能是否可用（DSA 是否安装）。",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": stock_health_handler,
    },
]
