"""画图插件：让 AI 能生成图表/示意图，并把图片显示在对话里（发送图像）。"""

import os
import re
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES_DIR = os.path.join(BASE_DIR, "files")
os.makedirs(FILES_DIR, exist_ok=True)

CHART_KINDS = {"line", "bar", "pie", "scatter", "area"}


def _safe_name(s):
    s = re.sub(r"[^\w\u4e00-\u9fff-]", "_", str(s or "chart"))[:40]
    return s or "chart"


def draw_chart_handler(arguments: dict) -> dict:
    """用 matplotlib 生成图表（折线/柱状/饼图/散点/面积图），保存为图片并给出可下载/预览链接。"""
    kind = str(arguments.get("kind", "line")).lower()
    if kind not in CHART_KINDS:
        return {"error": f"不支持的图表类型：{kind}（可选：line/bar/pie/scatter/area）"}
    title = str(arguments.get("title", "图表"))[:60]
    xlabel = str(arguments.get("xlabel", ""))[:40]
    ylabel = str(arguments.get("ylabel", ""))[:40]
    series = arguments.get("series")   # [{"name": "销售额", "x": [...], "y": [...]}]
    labels = arguments.get("labels") or []   # 饼图标签
    values = arguments.get("values") or []   # 饼图数值
    if not isinstance(series, list) or not series:
        if kind == "pie" and labels and values:
            series = []
        else:
            return {"error": "series 参数需要至少一组数据 [{'name','x':[],'y':[]}]"}
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # 中文字体：微软雅黑 → 黑体 → 思源黑体（避免中文变方块）
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "sans-serif"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception:
        return {"error": "服务器缺少 matplotlib，无法绘图。可先用 write_file_with_link 写 HTML/SVG 图表。"}

    try:
        fig, ax = plt.subplots(figsize=(8, 4.8), dpi=110)
        if kind == "pie":
            ax.pie(values, labels=labels or None, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")
            ax.set_title(title)
        else:
            for s in series:
                xs = s.get("x") or []
                ys = s.get("y") or []
                if not ys:
                    continue
                name = s.get("name", "")
                if kind == "line":
                    ax.plot(xs if xs else range(len(ys)), ys, marker="o", markersize=3, label=name or None)
                elif kind == "area":
                    ax.fill_between(xs if xs else range(len(ys)), ys, alpha=0.35, label=name or None)
                    ax.plot(xs if xs else range(len(ys)), ys, linewidth=1.5)
                elif kind == "bar":
                    ax.bar(xs if xs else range(len(ys)), ys, label=name or None)
                elif kind == "scatter":
                    ax.scatter(xs if xs else range(len(ys)), ys, s=28, label=name or None)
            if xlabel:
                ax.set_xlabel(xlabel)
            if ylabel:
                ax.set_ylabel(ylabel)
            ax.set_title(title)
            if any(s.get("name") for s in series):
                ax.legend()
            ax.grid(True, alpha=0.25)
        fig.tight_layout()
        fname = "chart_%s_%d.png" % (_safe_name(title), int(time.time()))
        fpath = os.path.join(FILES_DIR, fname)
        fig.savefig(fpath)
        plt.close(fig)
        url = f"http://127.0.0.1:8000/files/{fname}"
        return {
            "ok": True,
            "file": fname,
            "url": url,
            "note": f"图片已生成：{fname}。请在你的回答里引用这个链接（回答中的图片链接会自动显示为图片）：{url}",
        }
    except Exception as e:
        return {"error": f"绘图失败: {e}"}


PLUGIN_TOOLS = [
    {
        "name": "draw_chart",
        "description": "用 matplotlib 生成图表（line 折线 / bar 柱状 / pie 饼图 / scatter 散点 / area 面积图），"
                       "保存为 PNG 图片并返回链接。生成后请在回答里引用链接（自动显示为图片）。"
                       "参数：kind=图表类型；title=标题；series=[{name, x:[...], y:[...]}]；"
                       "饼图用 labels=[...] 和 values=[...]。",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["line", "bar", "pie", "scatter", "area"], "description": "图表类型"},
                "title": {"type": "string", "description": "图表标题"},
                "xlabel": {"type": "string", "description": "X 轴标签"},
                "ylabel": {"type": "string", "description": "Y 轴标签"},
                "series": {"type": "array", "items": {"type": "object"}, "description": "数据系列 [{'name','x':[],'y':[]}]"},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "饼图标签"},
                "values": {"type": "array", "items": {"type": "number"}, "description": "饼图数值"},
            },
            "required": ["kind"],
        },
        "handler": draw_chart_handler,
    }
]
