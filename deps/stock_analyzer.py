"""每日股票分析集成（daily_stock_analysis → 问墨定时任务）。
问墨定时任务调用本模块 → 调 DSA CLI 分析股票 → 返回结构化结果。

用法（问墨定时任务）：
  from deps.stock_analyzer import run_stock_analysis
  result = run_stock_analysis(stocks="600519,000858")
"""
import io
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DSA_DIR = os.path.join(BASE_DIR, "daily_stock_analysis")
PYTHON = sys.executable or "python"


def run_stock_analysis(stocks="600519,000858", timeout=300):
    """运行 DSA 分析指定股票，返回结果 dict。
    失败时返回 {ok: False, error}（定时任务容错）。"""
    if not os.path.isfile(os.path.join(DSA_DIR, "main.py")):
        return {"ok": False, "error": "DSA 未安装（deps/daily_stock_analysis 缺失）"}
    env = dict(os.environ)
    env["STOCK_LIST"] = stocks
    # 读 .env 补充 key（如 DEEPSEEK_API_KEY）
    env_file = os.path.join(DSA_DIR, ".env")
    if os.path.isfile(env_file):
        try:
            for line in open(env_file, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
        except Exception:
            pass
    cmd = [PYTHON, os.path.join(DSA_DIR, "main.py"),
           "--stocks", stocks, "--no-notify"]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=DSA_DIR, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        elapsed = round(time.time() - t0, 1)
        stdout = (proc.stdout or "")[-8000:]
        stderr = (proc.stderr or "")[-2000:]
        if proc.returncode == 0:
            # 提取关键行情信息（日志中的价格/涨跌行）
            import re
            key_lines = [ln for ln in stdout.splitlines()
                         if any(k in ln for k in ("实时行情", "价格=", "分析完成", "信号", "买入", "卖出", "建议"))]
            summary = "\n".join(key_lines[-25:]) if key_lines else stdout[-2000:]
            return {"ok": True, "stocks": stocks, "elapsed": elapsed,
                    "result": summary,
                    "note": "股票分析结果（DSA 实时行情+AI 分析），请向用户汇总关键信号与操作建议。"}
        # 非零退出但可能有部分输出
        return {"ok": False, "error": stderr or "DSA 分析失败（exit %s）" % proc.returncode,
                "partial": stdout[:2000], "elapsed": elapsed}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "DSA 分析超时（%ss），可能数据源网络慢" % timeout}
    except Exception as e:
        return {"ok": False, "error": "DSA 调用失败: %s" % e}


def check_health():
    """检查 DSA 是否可用（供 /api/tasks 或 ocr_health 类似机制）"""
    if not os.path.isfile(os.path.join(DSA_DIR, "main.py")):
        return {"ok": False, "detail": "DSA 未安装"}
    return {"ok": True, "detail": "DSA 就绪", "dir": DSA_DIR}
