# -*- coding: utf-8 -*-
"""让时钟小应用：1) 背景 100% 透明  2) 配色随主界面主题实时变化"""
import io, sys, os, shutil, datetime

ROOT = r"D:\桌面\聊天+AI\agent-tutorial"
APPJS = os.path.join(ROOT, "gui", "static", "app.js")
STYLE = os.path.join(ROOT, "gui", "static", "style.css")
CLOCK = os.path.join(ROOT, "apps", "clock", "index.html")
STAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def bak(path):
    b = path + ".bak-theme" + STAMP
    shutil.copy2(path, b)
    print("[备份]", os.path.basename(path), "->", os.path.basename(b))

def patch(path, old, new, must=True):
    with io.open(path, "r", encoding="utf-8") as f:
        s = f.read()
    if old not in s:
        print("[警告] 未找到匹配片段:", path, old[:60])
        if must:
            sys.exit(1)
        return False
    s = s.replace(old, new, 1)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(s)
    print("[已改]", os.path.basename(path))
    return True

# ---------- 1) app.js ----------
bak(APPJS)

# 1a) 打开窗口时 iframe 带上当前主题 + 防缓存时间戳
old_src = "'<iframe class=\"app-window-frame\" src=\"/apps/' + encodeURIComponent(a.name) + '/index.html\" title=\"小应用\"></iframe>' +"
new_src = ("'<iframe class=\"app-window-frame\" src=\"/apps/' + encodeURIComponent(a.name) + '/index.html?theme=' + "
           "encodeURIComponent(els.root.getAttribute(\"data-theme\") || \"dark\") + '&ts=' + Date.now() + '\" title=\"小应用\"></iframe>' +")
patch(APPJS, old_src, new_src)

# 1b) applyTheme 末尾：广播主题调色板给小应用 iframe
old_tail = """  if (persist) {
    try { localStorage.setItem("theme", theme); } catch (e) { /* 忽略 */ }
  }
}"""
new_tail = """  if (persist) {
    try { localStorage.setItem("theme", theme); } catch (e) { /* 忽略 */ }
  }
  // 同步主题给小应用窗口：iframe 无法继承父页面 CSS 变量，用 postMessage 广播完整调色板
  try {
    var cv = getComputedStyle(els.root);
    var palette = {
      theme: theme,
      bg: cv.getPropertyValue("--bg").trim(),
      surface: cv.getPropertyValue("--surface").trim(),
      text: cv.getPropertyValue("--text").trim(),
      textSecondary: cv.getPropertyValue("--text-secondary").trim(),
      accent: cv.getPropertyValue("--accent").trim(),
      border: cv.getPropertyValue("--border-strong").trim()
    };
    (window.__appWins || []).forEach(function (w) {
      var f = w.querySelector("iframe");
      if (f && f.contentWindow) f.contentWindow.postMessage({ type: "wm-theme", palette: palette }, "*");
    });
  } catch (e) { /* 忽略 */ }
}"""
patch(APPJS, old_tail, new_tail)

# ---------- 2) style.css：无框模式 100% 透明强化 ----------
bak(STYLE)
with io.open(STYLE, "a", encoding="utf-8") as f:
    f.write("""
/* ===== 无框沉浸（时钟）—— 100% 透明强化（防止任何浅色底透出） ===== */
.app-window.app-window-frameless { background: transparent !important; border: none !important; box-shadow: none !important; }
.app-window-frameless .app-window-frame { background: transparent !important; }
""")
print("[已改] style.css (追加透明强化)")

# ---------- 3) clock/index.html：重写为「主题驱动配色 + 透明背景」 ----------
bak(CLOCK)
NEW_CLOCK = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>时钟</title>
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    width: 100%; height: 100%;
    background: transparent;            /* 100% 透明背景：表盘悬浮于任何界面之上 */
    overflow: hidden;
    font-family: "Segoe UI", system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
    display: flex; align-items: center; justify-content: center;
  }
  .clock-wrap {
    position: relative;
    display: flex; flex-direction: column; align-items: center;
    user-select: none; -webkit-user-select: none;
  }
  /* 表盘：颜色全部由 CSS 变量驱动（随主界面主题实时变化） */
  .dial {
    position: relative;
    width: min(72vmin, 260px);
    height: min(72vmin, 260px);
    border-radius: 50%;
    background: radial-gradient(circle at 32% 26%, var(--dial-1, #1B2331), var(--dial-2, #0C1017) 72%);
    border: 1px solid var(--dial-border, #2A3345);
    box-shadow:
      0 0 0 5px var(--dial-ring, rgba(12,16,23,.45)),
      0 0 0 6px var(--dial-ring2, rgba(42,51,69,.6)),
      0 18px 50px var(--dial-shadow, rgba(0,0,0,.55)),
      inset 0 0 40px var(--dial-inset, rgba(0,0,0,.55));
  }
  .dial svg { position: absolute; inset: 0; width: 100%; height: 100%; }
  .tick { stroke: var(--tick, #38435A); stroke-width: 1.4; }
  .tick-major { stroke: var(--tick-major, #8B93A7); stroke-width: 2.6; }
  .num { fill: var(--num, #8B93A7); font-size: 11px; font-weight: 600; text-anchor: middle; dominant-baseline: central; }
  .hand { stroke-linecap: round; }
  #hourHand { stroke: var(--hour, #E8ECF4); stroke-width: 6; }
  #minHand { stroke: var(--min, #D3DAE6); stroke-width: 4; }
  #secHand { stroke: var(--sec, #34F5C5); stroke-width: 1.8; filter: drop-shadow(0 0 4px var(--sec-glow, rgba(52,245,197,.85))); }
  #centerDot { fill: var(--sec, #34F5C5); }
  #centerRing { fill: none; stroke: var(--sec, #34F5C5); stroke-width: 1.6; opacity: .75; }
  .dial-date {
    position: absolute;
    left: 50%; transform: translateX(-50%);
    bottom: 24%;
    text-align: center;
    white-space: nowrap;
    pointer-events: none;
  }
  .dial-date .y { font-size: 12px; color: var(--date-y, #C6CDDA); letter-spacing: .18em; font-variant-numeric: tabular-nums; }
  .dial-date .mdw { font-size: 10.5px; color: var(--date-mdw, #34F5C5); letter-spacing: .22em; margin-top: 3px; text-shadow: 0 0 8px var(--sec-glow, rgba(52,245,197,.5)); }
</style>
</head>
<body>
<div class="clock-wrap">
  <div class="dial" id="dial"></div>
  <div class="dial-date">
    <div class="y" id="yLine">----</div>
    <div class="mdw" id="mdwLine">--月--日 星期-</div>
  </div>
</div>
<script>
  // ===== 主题配色：随主界面主题实时变化 =====
  var DARK_THEMES = ["dark", "midnight", "graphite", "forest", "ocean", "violet"];
  // 默认调色板（父页面广播到达前先用，避免闪烁）
  var PALETTES = {
    dark:  { surface: "#1B2331", bg: "#0C1017", text: "#E8ECF4", textSecondary: "#8B93A7", accent: "#34F5C5", border: "#2A3345", shadow: "rgba(0,0,0,.55)" },
    light: { surface: "#FFFFFF", bg: "#E4E8EF", text: "#1A1F2B", textSecondary: "#6B7280", accent: "#5B7CFA", border: "#C9CFDA", shadow: "rgba(20,30,60,.25)" }
  };

  function getParam(name) {
    var m = new RegExp("[?&]" + name + "=([^&]*)").exec(location.search);
    return m ? decodeURIComponent(m[1]) : null;
  }
  function hexToRgba(hex, a) {
    var h = (hex || "").replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return "rgba(0,0,0,.5)";
    var n = parseInt(h, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }
  function applyPalette(p) {
    if (!p) return;
    var st = document.documentElement.style;
    st.setProperty("--dial-1", p.surface || "#1B2331");
    st.setProperty("--dial-2", p.bg || "#0C1017");
    st.setProperty("--dial-border", p.border || "#2A3345");
    st.setProperty("--dial-shadow", p.shadow || "rgba(0,0,0,.55)");
    st.setProperty("--tick", p.textSecondary || "#38435A");
    st.setProperty("--tick-major", p.text || "#8B93A7");
    st.setProperty("--num", p.textSecondary || "#8B93A7");
    st.setProperty("--hour", p.text || "#E8ECF4");
    st.setProperty("--min", p.textSecondary || "#D3DAE6");
    st.setProperty("--sec", p.accent || "#34F5C5");
    st.setProperty("--sec-glow", hexToRgba(p.accent || "#34F5C5", .85));
    st.setProperty("--date-y", p.textSecondary || "#C6CDDA");
    st.setProperty("--date-mdw", p.accent || "#34F5C5");
  }

  // 初始：URL 带主题参数（打开窗口时由主界面写入）
  var theme = getParam("theme") || "dark";
  applyPalette(PALETTES[DARK_THEMES.indexOf(theme) >= 0 ? "dark" : "light"]);

  // 主界面切换主题 → 实时广播更新配色
  window.addEventListener("message", function (ev) {
    var d = ev.data;
    if (d && d.type === "wm-theme" && d.palette) applyPalette(d.palette);
  });

  // ===== 表盘绘制 =====
  var NS = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 200 200");
  var cx = 100, cy = 100, R = 92;
  for (var i = 0; i < 60; i++) {
    var a = i * 6 * Math.PI / 180;
    var major = i % 5 === 0;
    var r1 = major ? R - 13 : R - 6;
    var line = document.createElementNS(NS, "line");
    line.setAttribute("x1", (cx + r1 * Math.sin(a)).toFixed(2));
    line.setAttribute("y1", (cy - r1 * Math.cos(a)).toFixed(2));
    line.setAttribute("x2", (cx + R * Math.sin(a)).toFixed(2));
    line.setAttribute("y2", (cy - R * Math.cos(a)).toFixed(2));
    line.setAttribute("class", major ? "tick-major" : "tick");
    svg.appendChild(line);
  }
  var NUMS = [12,1,2,3,4,5,6,7,8,9,10,11];
  for (var n = 0; n < 12; n++) {
    var ang = n * 30 * Math.PI / 180;
    var rn = R - 25;
    var t = document.createElementNS(NS, "text");
    t.setAttribute("x", (cx + rn * Math.sin(ang)).toFixed(2));
    t.setAttribute("y", (cy - rn * Math.cos(ang)).toFixed(2));
    t.setAttribute("class", "num");
    t.textContent = NUMS[n];
    svg.appendChild(t);
  }
  function makeHand(id, len, w) {
    var l = document.createElementNS(NS, "line");
    l.setAttribute("id", id);
    l.setAttribute("x1", cx); l.setAttribute("y1", cy + 10);
    l.setAttribute("x2", cx); l.setAttribute("y2", cy - len);
    svg.appendChild(l);
  }
  makeHand("hourHand", 48);
  makeHand("minHand", 70);
  makeHand("secHand", 84);
  var dot = document.createElementNS(NS, "circle");
  dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.setAttribute("r", 3.6);
  dot.setAttribute("id", "centerDot");
  svg.appendChild(dot);
  var ring = document.createElementNS(NS, "circle");
  ring.setAttribute("cx", cx); ring.setAttribute("cy", cy); ring.setAttribute("r", 7);
  ring.setAttribute("id", "centerRing");
  svg.appendChild(ring);
  document.getElementById("dial").appendChild(svg);

  var WEEK = ["日","一","二","三","四","五","六"];
  function tick() {
    var d = new Date();
    var h = d.getHours() % 12, m = d.getMinutes(), s = d.getSeconds();
    var hd = (h + m / 60) * 30;
    var md = (m + s / 60) * 6;
    var sd = s * 6;
    document.getElementById("hourHand").setAttribute("transform", "rotate(" + hd.toFixed(2) + " 100 100)");
    document.getElementById("minHand").setAttribute("transform", "rotate(" + md.toFixed(2) + " 100 100)");
    document.getElementById("secHand").setAttribute("transform", "rotate(" + sd.toFixed(2) + " 100 100)");
    var p = function (n) { return String(n).padStart(2, "0"); };
    document.getElementById("yLine").textContent = d.getFullYear() + " 年";
    document.getElementById("mdwLine").textContent =
      p(d.getMonth() + 1) + " 月 " + p(d.getDate()) + " 日 · 星期" + WEEK[d.getDay()];
  }
  tick();
  setInterval(tick, 1000);
</script>
</body>
</html>
'''
with io.open(CLOCK, "w", encoding="utf-8") as f:
    f.write(NEW_CLOCK)
print("[已写] clock/index.html (主题驱动配色 + 透明背景)")

print("\n全部完成 ✅")
