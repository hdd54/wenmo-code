# -*- coding: utf-8 -*-
"""给内联时钟加电子钟外壳：半透明毛玻璃机身 + 主题边框 + 投影"""
import io, sys

BASE = r"D:\桌面\聊天+AI\agent-tutorial\gui\static"

# ---------- 1. style.css ----------
css_path = BASE + r"\style.css"
css = io.open(css_path, encoding="utf-8").read()

# 1a. 无框窗口默认尺寸 330x108 -> 360x132（容纳外壳内边距）
old_size = """  width: 330px;
  height: 108px;"""
new_size = """  width: 360px;
  height: 132px;"""
assert old_size in css, "窗口尺寸块未找到"
css = css.replace(old_size, new_size)

# 1b. 替换 .clock-inline 为带外壳样式
old_clock = """.clock-inline {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 100%;
  height: 100%;
  user-select: none;
  -webkit-user-select: none;
  font-family: var(--font-sans);
  background: transparent;
}"""
new_clock = """.clock-inline {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  width: calc(100% - 20px);
  height: calc(100% - 20px);
  margin: 10px;
  box-sizing: border-box;
  user-select: none;
  -webkit-user-select: none;
  font-family: var(--font-sans);
  /* 电子钟外壳：半透明毛玻璃机身，随主题变色，背景保持通透 */
  background: color-mix(in srgb, var(--surface) 55%, transparent);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 12px 26px 10px;
  box-shadow:
    0 8px 28px rgba(0, 0, 0, .20),
    inset 0 1px 0 rgba(255, 255, 255, .07),
    inset 0 -1px 0 rgba(0, 0, 0, .05);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  position: relative;
}
/* 外壳顶部高光线：模拟电子钟屏幕玻璃反光 */
.clock-inline::before {
  content: "";
  position: absolute;
  left: 14px;
  right: 14px;
  top: 7px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, .28), transparent);
  border-radius: 1px;
  pointer-events: none;
}
/* 外壳底部品牌点：两点呼吸灯，像电子钟的品牌指示灯 */
.clock-inline::after {
  content: "";
  position: absolute;
  left: 50%;
  bottom: 5px;
  width: 3px;
  height: 3px;
  margin-left: -1.5px;
  border-radius: 50%;
  background: var(--accent);
  opacity: .55;
  animation: cli-blink 2s step-end infinite;
  pointer-events: none;
}"""
assert old_clock in css, ".clock-inline 原样式未找到"
css = css.replace(old_clock, new_clock)

io.open(css_path, "w", encoding="utf-8", newline="").write(css)
print("style.css OK")

# ---------- 2. app.js ----------
js_path = BASE + r"\app.js"
js = io.open(js_path, encoding="utf-8").read()

# 记忆恢复时：时钟忽略旧尺寸记忆（外壳需要新默认尺寸），只恢复位置
old_restore = """    if (r && r.w && r.h) {
      win.style.left = r.x + "px";
      win.style.top = r.y + "px";
      win.style.width = r.w + "px";
      win.style.height = r.h + "px";
    }"""
new_restore = """    if (r && r.w && r.h) {
      win.style.left = r.x + "px";
      win.style.top = r.y + "px";
      if (!inlineClock) {
        win.style.width = r.w + "px";
        win.style.height = r.h + "px";
      }
    }"""
assert old_restore in js, "记忆恢复块未找到"
js = js.replace(old_restore, new_restore)

io.open(js_path, "w", encoding="utf-8", newline="").write(js)
print("app.js OK")
print("ALL_DONE")
