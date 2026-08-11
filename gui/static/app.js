function loadVersion() {
  var el = document.getElementById("gen-version");
  if (!el) return;
  fetch("/api/version").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    if (d && d.version) {
      el.textContent = "问墨·code v" + d.version;
      var dv = document.getElementById("doc-version");
      if (dv) dv.textContent = "v" + d.version;
    }
    else el.textContent = "未知";
  }).catch(function () { el.textContent = "未知"; });
}
/* ============================================================================
 * Agent Tutorial · Chat — 前端逻辑（原生 JS，无框架）
 *
 * 分工：
 *   1. 供应商：GET /api/providers → 渲染自定义下拉
 *   2. 对话：POST /api/chat，用 fetch + ReadableStream 解析 SSE（不能用 EventSource，
 *      因为它无法 POST）。流式把 delta 追加进 AI 消息，打字机光标 ▍ 在生成中显示。
 *   3. 停止/切换：先取消服务端任务，再中断浏览器流；未完成助手消息不入历史。
 *   4. Markdown：marked.js（CDN），失败则回退纯文本。所有动态文本先 escapeHTML
 *      再交给 marked，绝不把原始文本当 HTML 注入。
 *   5. 主题：深色默认，浅色可切换，localStorage 持久化。
 *   6. 设置：弹窗里保存/清除各供应商 API Key 与模型（POST /api/settings，key 只进服务器）；
 *      local 供应商提供 GGUF 直连加载（POST /api/local/load + 状态轮询）；
 *      冷启动 3 秒无内容给等待提示；空回复给"请重试"提示。
 *   7. 思考：流式 think 事件渲染成正文上方安静的灰块（纯 textContent，不做 Markdown）。
 * ========================================================================== */
"use strict";

/* ---------------------------------------------------------------------------
 * 常量与全局状态
 * ------------------------------------------------------------------------- */
var SUGGESTIONS = [
  "用一句话介绍你自己",
  "写一个 Python 快速排序",
  "解释什么是 SSE 流式传输"
];

var MAX_INPUT_HEIGHT = 200;      // 输入框最大高度（px）
var NEAR_BOTTOM_PX = 120;        // 距底部多少内视为"在底部"

var state = {
  providers: [],                 // [{ key, name, model }]
  activeProvider: null,          // 当前选中的供应商对象
  agentPanelDismissed: false,    // 智能体面板被用户手动关闭（本会话不再自动弹开）
  messages: [],                  // [{ role, content }]，多轮对话每次全量发送
  streaming: false,              // 是否正在生成
  controller: null,              // 当前请求的 AbortController
  markdownReady: false,          // marked 是否加载成功
  nearBottom: true,              // 用户是否贴近底部（决定是否自动滚动）
  statusTimer: null,             // 错误状态自动回到空闲的定时器
  conversationId: null,          // 当前对话 id（历史保存用）
  convCache: {},                 // 对话内存缓存 {cid: {messages, usage}}：切换过的对话秒开
  unreadDots: {},                // 完成未读对话集合 {cid: true}：后台完成且用户未查看（更亮点标记）
  loadSeq: 0,                    // 对话加载序列号（竞态守卫：快速切换时丢弃过期 fetch 响应）
  ctx: 32768,                    // 当前供应商上下文上限（token 显示用）
  usage: null,                   // 最近一次 {input, output, cached} 用量
  lastContext: 0,                // 当前上下文实际占用（最近一次请求的 prompt，不含思考，不累计）
  totalInput: 0,                 // 当前对话累计输入（含缓存）
  totalOutput: 0,                // 当前对话累计输出（全部，含思考）
  totalOutputFormal: 0,          // 当前对话累计正式输出（不含思考）
  totalCached: 0,                // 当前对话累计缓存命中
  totalCost: 0,                  // 当前对话累计费用（元，按供应商模型价格表）
  costIsEst: false,              // 费用是否按默认价估算（未收录模型）
  lastInput: 0,                  // 最近一次请求的 input（命中率展示用）
  lastCached: 0,                 // 最近一次请求的缓存命中
  lastCacheBust: false,          // 最近一次请求缓存被破坏（大输入零命中/前缀漂移）
  statsTimer: null,              // 统计栏 60 秒自动刷新定时器
  proposals: [],                 // 【方案设计】块解析出的方案列表 [{name, desc, recommended}]
  selectedProposal: null,        // 用户选中的方案名
  planExplicitProgress: false,   // 模型是否输出了显式【步骤完成：N】标记（有则停用兜底自动推进）
  online: false,                 // 联网模式（发送时传给服务器）
  reasoning: "medium",           // 深度思考：low / medium / high（默认中）
  project: "default",            // 当前项目
  attachments: [],                // 待发送的图片附件（data URL）
  fileAttachments: [],            // 待发送的任意文件附件 [{name, url}]（非图片，上传到 files/ 后模型用 read_document 读）
};

/* 流式生成期间的临时引用 */
var aiMsgEl = null;              // 当前 AI 消息根元素
var aiContentEl = null;          // 其内部的 .md 容器
var streamCtx = null;            // 当前流上下文 {cid, messages 快照, finished, provider, model}
var streamCtxs = {};             // 多流管理：cid -> ctx（对话 1 后台跑时对话 2 发送不覆盖对话 1）
var toolLog = [];                // 本轮工具调用记录（保存进历史，重看时展示）
var streamStartTs = 0;           // 生成速度：开始时间戳
var streamOutTokens = 0;         // 生成速度：本轮正式输出 token 数
var thinkingEl = null;           // “正在思考”指示器（贴最新输出下方）
var streamLabelOverride = null;  // 对比模式：AI 消息标签用对比模型名
var compareRightCol = null;      // 对比模式：右栏容器（对比模型回答挂这里）
var acc = "";                    // 本次累计的回复文本
var thinkAcc = "";               // 本次累计的思考文本（think 事件，用于空回复判断）
var toolLineEls = {};            // 工具调用行：工具名 -> 行元素

/* ---- 步骤流（对标 opencode：思考→工具→输出 按步骤分组，不扎堆） ---- */
var stepEl = null;               // 当前步骤容器
var stepPhase = "none";          // none | think | tool | content
var stepThinkText = "";          // 当前步骤的思考文本
var stepThink = null;            // {header, body, status, expanded}
var stepContent = null;          // 当前步骤的内容块 .md
var stepContentText = "";        // 当前步骤的内容文本

/* ---------------------------------------------------------------------------
 * DOM 引用
 * ------------------------------------------------------------------------- */
var els = {
  root: document.documentElement,
  statusDot: document.getElementById("status-dot"),
  messages: document.getElementById("messages"),
  empty: document.getElementById("empty-state"),
  backPill: document.getElementById("back-to-bottom"),
  input: document.getElementById("input"),
  sendBtn: document.getElementById("send-btn"),
  sendIcon: document.getElementById("send-icon"),
  stopIcon: document.getElementById("stop-icon"),
  themeToggle: document.getElementById("theme-toggle"),
  settingsBtn: document.getElementById("settings-btn"),
  settingsModal: document.getElementById("settings-modal"),
  settingsBackdrop: document.querySelector("#settings-modal .modal-backdrop"),
  settingsList: document.getElementById("settings-list"),
  settingsClose: document.getElementById("settings-close"),
  trigger: document.getElementById("provider-trigger"),
  triggerName: document.getElementById("trigger-name"),
  triggerModel: document.getElementById("trigger-model"),
  menu: document.getElementById("provider-menu"),
  newChatBtn: document.getElementById("new-chat-btn"),
  historyList: document.getElementById("history-list"),
  attachBtn: document.getElementById("attach-btn"),
  attachInput: document.getElementById("attach-input"),
  attachPreview: document.getElementById("attach-preview"),
  projectTrigger: document.getElementById("project-trigger"),
  projectAddBtn: document.getElementById("project-add-btn")
};

/* ===========================================================================
 * 工具函数
 * ========================================================================= */

/** 转义 HTML：任何插入 DOM 的动态文本都先过这里（textContent 之外的唯一入口） */
function escapeHTML(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 复制文本到剪贴板（带降级方案），按钮短暂显示"已复制" */
function copyText(text, btn) {
  var done = function () {
    var orig = btn.textContent;
    btn.textContent = "已复制";
    setTimeout(function () { btn.textContent = orig; }, 1500);
  };
  var fallback = function () {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* 忽略 */ }
    ta.remove();
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, fallback);
  } else {
    fallback();
  }
}

/* ===========================================================================
 * 工作步骤右栏（可拉宽；上部=当前规划，下部=历史步骤）
 * 步骤 = 任务规划（一步步直达目标，最后一步验证），不是工具调用清单
 * ========================================================================= */
state.planSteps = [];         // [{text, status: pending|running|done}] 当前规划的步骤
state.planStarted = false;    // 当前是否有活动规划
state.stepHistory = [];       // [{steps:[...], ts}] 历史步骤记录（已完成任务的规划）
var stepsOpen = false;
var planSeq = 0;              // 当前规划进度序号（已完成的步骤数）

/** 渲染上部：当前规划 */
function renderPlan() {
  var el = document.getElementById("ts-plan-list");
  var toggle = document.getElementById("ts-toggle");
  if (!el) return;
  if (!state.planSteps.length) {
    el.innerHTML = '<div class="ts-drop-empty">暂无规划</div>';
    if (toggle) toggle.hidden = true;
    return;
  }
  el.innerHTML = "";
  state.planSteps.forEach(function (st, i) {
    var row = document.createElement("div");
    row.className = "steps-item " + st.status;
    var mark = document.createElement("span");
    mark.className = "steps-mark";
    mark.textContent = st.status === "done" ? "✔" : (st.status === "running" ? "▸" : String(i + 1));
    var name = document.createElement("span");
    name.className = "steps-name";
    // 步骤完整显示（默认最多 3 行，点击可展开全文；不再粗暴 60 字截断）
    name.textContent = st.text;
    name.title = st.text;
    name.classList.add("steps-name-clamp");
    name.addEventListener("click", function () {
      name.classList.toggle("expanded");
    });
    row.appendChild(mark);
    row.appendChild(name);
    el.appendChild(row);
  });
  // 有规划 → 显示"步骤"展开按钮
  if (toggle) toggle.hidden = false;
  el.scrollTop = el.scrollHeight;
  refreshTaskStep();   // 规划渲染后同步状态栏
}

/** 渲染下部：历史步骤 */
function renderHistory() {
  var el = document.getElementById("ts-history-list");
  var sec = document.getElementById("ts-history-sec");
  if (!el) return;
  if (!state.stepHistory.length) {
    el.innerHTML = '<div class="ts-drop-empty">暂无历史步骤</div>';
    if (sec) sec.hidden = true;
    return;
  }
  el.innerHTML = "";
  if (sec) sec.hidden = false;
  state.stepHistory.slice().reverse().forEach(function (h) {
    var card = document.createElement("div");
    card.className = "steps-history-card";
    var head = document.createElement("div");
    head.className = "steps-history-head";
    head.textContent = "✔ 已完成任务" + (h.ts ? " · " + new Date(h.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "");
    card.appendChild(head);
    h.steps.forEach(function (t, i) {
      var row = document.createElement("div");
      row.className = "steps-item done";
      var mark = document.createElement("span");
      mark.className = "steps-mark";
      mark.textContent = "✔";
      var name = document.createElement("span");
      name.className = "steps-name";
      var full = (i + 1) + ". " + t;
      // 历史步骤同样完整显示（3 行内 + 点击展开全文）
      name.textContent = full;
      name.title = t;
      name.classList.add("steps-name-clamp");
      name.addEventListener("click", function () {
        name.classList.toggle("expanded");
      });
      row.appendChild(mark);
      row.appendChild(name);
      card.appendChild(row);
    });
    el.appendChild(card);
  });
  el.scrollTop = el.scrollHeight;
}

/** 渲染【方案设计】区：方案列表（点击选择 → 通知模型继续执行所选方案） */
function renderProposal() {
  var section = document.getElementById("ts-proposal-sec");
  var el = document.getElementById("ts-proposal-list");
  if (!section || !el) return;
  if (!state.proposals || !state.proposals.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  el.innerHTML = "";
  state.proposals.forEach(function (p, i) {
    var card = document.createElement("div");
    card.className = "proposal-card" + (p.recommended ? " recommended" : "");
    if (p.recommended) {
      var tag = document.createElement("div");
      tag.className = "proposal-tag";
      tag.textContent = "推荐";
      card.appendChild(tag);
    }
    var name = document.createElement("div");
    name.className = "proposal-name";
    name.textContent = p.name;
    card.appendChild(name);
    var desc = document.createElement("div");
    desc.className = "proposal-desc";
    desc.textContent = p.desc;
    card.appendChild(desc);
    var pick = document.createElement("button");
    pick.type = "button";
    pick.className = "proposal-pick";
    pick.textContent = "选这个方案";
    pick.addEventListener("click", function () {
      pickProposal(i);
    });
    card.appendChild(pick);
    el.appendChild(card);
  });
}

/** 用户选择方案 → 高亮所选 + 通知模型按该方案继续（追加 user 消息 → 新一轮 agent 循环） */
function pickProposal(idx) {
  if (!state.proposals || !state.proposals[idx]) return;
  var p = state.proposals[idx];
  state.selectedProposal = p.name;
  // 视觉：高亮所选，其余置灰，隐藏按钮（已选）
  var cards = document.querySelectorAll("#steps-proposal .proposal-card");
  cards.forEach(function (c, i) {
    c.classList.toggle("chosen", i === idx);
    var b = c.querySelector(".proposal-pick");
    if (b) { b.textContent = i === idx ? "已选 ✓" : "—"; b.disabled = true; }
  });
  // 通知模型继续执行该方案
  appendUserProposalChoice(p.name);
}

/** 把方案选择注入当前流：
 *  模型出【方案设计】后通常已用 ask_user 挂起等待选择 → 走 ask 回执通道（回答喂回模型继续）；
 *  若模型没用 ask_user（自主输出方案后继续）→ 此时可能在流式或已结束，追加用户消息触发新一轮。 */
function appendUserProposalChoice(choice) {
  if (currentAsk) {
    submitAsk("我选择方案：" + choice + "。请按此方案输出【步骤规划】并执行。");
    return;
  }
  if (!els.input) return;
  els.input.value = "我选择方案：" + choice + "。请按此方案输出【步骤规划】并开始执行。";
  autoResizeInput();
  updateSendBtn();
  sendMessage();
}

/** 从流式文本解析【方案设计】块 → 填充方案区（支持 方案1/方案A 等） */
function parseProposalFromText(text) {
  var m = /【方案设计】([\s\S]*?)(?=【|$)/.exec(text);
  if (!m) return false;
  var body = m[1];
  var lines = body.split("\n");
  var proposals = [];
  var cur = null;
  // 识别方案标题：方案1 / 方案一 / 方案A / 方案 A：xxx
  var titleRe = /^\s*方案\s*[0-9一二三四五六A-Da-d１-４]\s*[.:、)）]?\s*(.*)$/;
  lines.forEach(function (ln) {
    var t = ln.trim();
    if (!t) return;
    var tm = titleRe.exec(t);
    if (tm) {
      cur = { name: "方案" + (/[0-9１-４]/.test(t) ? (t.match(/[0-9１-４]/)[0]) : (t.match(/[一二三四五六]/) || ["一"])[0]), desc: "", recommended: false };
      var rest = tm[1].trim();
      if (rest && !rest.match(/^(思路|方案|优点|缺点|步骤|说明)[:：]/)) cur.desc = rest;
      proposals.push(cur);
    } else if (cur && /^(优点|缺点|优势|劣势|成本|风险|推荐|我推荐|建议)/.test(t)) {
      if (/^推荐|^我推荐|^建议选择/.test(t) && proposals.length) proposals[proposals.length - 1].recommended = true;
      if (cur.desc) cur.desc += " " + t.replace(/^[^:：]*[:：]/, "");
      else cur.desc = t.replace(/^[^:：]*[:：]/, "");
    } else if (cur && /^(思路|说明|做法)[:：]/.test(t)) {
      cur.desc = (cur.desc ? cur.desc + " " : "") + t.replace(/^[^:：]*[:：]/, "");
    }
  });
  // 去掉空方案；没有明确推荐时默认推荐第一个
  proposals = proposals.filter(function (p) { return p.name; });
  if (!proposals.length) return false;
  var first = proposals[0];
  if (!proposals.some(function (p) { return p.recommended; })) first.recommended = true;
  state.proposals = proposals;
  renderProposal();      // 内容更新但下拉保持折叠（用户点"步骤"按钮才展开）
  return true;
}

/** 从流式文本解析【步骤规划】块 → 填充当前规划（一次性全列出）
 *  支持两种形态：
 *  1) 标准：【步骤规划】1. xxx 2. xxx …
 *  2) 自然：思考/正文里出现 ≥2 个连续编号步骤（"计划：1. 搜索 2. 分析"）
 *  防抖：流式累积中避免中间态截断（如 thinkAcc 只到"写"就匹配 → 残缺步骤）；
 *       文本变化才重新解析；步骤去重。 */
var _planParseCache = "";
function parsePlanFromText(text) {
  if (!text || text === _planParseCache) return false;
  // 防抖：文本停止增长或超过 2000 字符（规划块通常在前部）才解析，避免中间态
  if (text.length < _planParseCache.length) _planParseCache = "";
  if (text.length < 10) return false;   // 防抖阈值：中文规划通常 <30 字符，调低避免误拦短规划
  _planParseCache = text;
  var steps = [];
  // 严格【步骤规划】块：块内容必须包含编号步骤才算规划。
  // 排除"复述用户请求"（如 "【步骤规划】格式输出一个..."、"请用【步骤规划】..." 是用户在要求，
  // 模型在思考/正文里引用时会误匹配）→ 块内找不到 ≥1 个编号步骤则不算。
  var m = /【步骤规划】([\s\S]*?)(?=【|$)/.exec(text);
  if (m) {
    var block = m[1];
    // 快检：块以"格式/给我/输出/写一个/做一个/请用/我需要/用户"等开头 → 是复述请求，跳过
    if (!/^\s*(?:格式|请用|给我|输出|写一个|写一份|做一个|做一份|我需要|我想要|用户要|用户说|这是用户)/.test(block)) {
      parseStepsFrom(block, steps);
    }
  }
  // 正文宽松模式：仅在还没有规划时尝试，且块里必须解析出 ≥2 步才认定（防思考碎片污染）
  if (!steps.length && !state.planStarted) {
    var lead = /(?:计划|规划|步骤|流程|方案|将按以下|按以下步骤)[：:]\s*([\s\S]{0,600})/.exec(text);
    if (lead) parseStepsFrom(lead[1], steps);
    if (steps.length < 2) steps = [];   // 少于 2 步不认定为规划（防误判）
  }
  // 步骤去重（模型可能重复列出同一目标；"加载X-技能（系统提示要求）"只留一次）
  var uniq = [];
  steps.forEach(function (s) {
    if (!uniq.some(function (u) { return u === s || u.indexOf(s) >= 0 || s.indexOf(u) >= 0; })) {
      uniq.push(s);
    }
  });
  steps = uniq;
  // 步骤数上限保护：超过 12 步说明模型把细节拆碎了，保留前 12（规划宜精不宜碎）
  if (steps.length > 12) steps = steps.slice(0, 12);
  // 步骤文字清理：去重后若某步只剩标题/过短（<6 字且以"："结尾），与下一步合并
  var merged = [];
  for (var _si2 = 0; _si2 < steps.length; _si2++) {
    var curTxt = steps[_si2];
    // 以冒号/逗号结尾的短标题（如 "**分析项目结构**："）→ 与下一步拼接
    if (merged.length && (/[:：,，]$/.test(curTxt) || curTxt.length < 6) && _si2 + 1 < steps.length) {
      merged[merged.length - 1] = merged[merged.length - 1] + " " + steps[_si2 + 1];
      _si2++;   // 跳过已并掉的下一步
      continue;
    }
    if (curTxt.length < 6 && merged.length) {
      merged[merged.length - 1] = merged[merged.length - 1] + " " + curTxt;
    } else {
      merged.push(curTxt);
    }
  }
  steps = merged.length ? merged : steps;
  if (steps.length && (steps.length !== state.planSteps.length ||
      steps.some(function (t, i) { return (state.planSteps[i] || {}).text !== t; }))) {
    state.planSteps = steps.map(function (t) { return { text: t, status: "pending" }; });
    state.planStarted = true;
    planSeq = 0;
    renderPlan();        // 内容更新但下拉保持折叠（用户点"步骤"按钮才展开）
    showTaskStatus("规划已生成", 0);
    return true;
  }
  return false;
}

/** 从文本块解析编号步骤（支持 1. / 1、/ 一、/ ① / 第一步 / - / •）
 * 关键：模型常在步骤内容内换行（如 "**分析项目结构**：\n用 codebase_overview..."），
 * 若按行切分会把一个逻辑步骤拆成多个碎片。正确做法：
 *   编号行开始新步骤；其后的非编号续行（含空行后的说明）聚合进当前步骤，
 *   直到遇到下一个编号行。这样步骤划分与语义一致，内容完整。 */
function parseStepsFrom(block, out) {
  if (!block) return;
  var lines = block.split("\n");
  var cur = null;   // 当前步骤的累计文本
  var numRe = /^\s*(?:\d+[.、)）]|[一二三四五六七八九十]+[.、)）]|[①②③④⑤⑥⑦⑧⑨⑩]|第一步|第二步|第三步|第四步|第五步|第六步|[-*•])\s*(.+)$/;
  for (var i = 0; i < lines.length; i++) {
    var t = lines[i].trim();
    if (!t) {
      // 空行：保留一个换行分隔符（后续说明仍属当前步骤，但避免过度粘合）
      if (cur) cur += "\n";
      continue;
    }
    var sm = numRe.exec(t);
    if (sm && sm[1].trim()) {
      // 新步骤开始：先把上一个步骤收尾入列
      if (cur) {
        var prev = cur.replace(/\n{3,}/g, "\n\n").trim();
        if (prev) out.push(prev);
      }
      cur = sm[1].trim();
    } else if (cur) {
      // 续行：并入当前步骤（用空格连接，保留原意；行太长时截断防爆）
      cur += " " + t.trim();
    } else if (t) {
      // 无编号的杂散文本（出现在步骤列表前）：作为独立步骤的兜底
      out.push(t);
      cur = null;
    }
  }
  if (cur) {
    var last = cur.replace(/\n{3,}/g, "\n\n").trim();
    if (last) out.push(last);
  }
  // 步骤长度保护：单步最长 500 字符（防模型塞整段说明进一步）
  for (var j = 0; j < out.length; j++) {
    if (out[j].length > 500) out[j] = out[j].slice(0, 497) + "…";
  }
}

/** 从流式文本解析步骤进度（【步骤完成：N】/变体）→ 打✔ */
function parsePlanProgress(text) {
  var changed = false;
  var m;
  // 兼容【步骤完成：N】、步骤完成：N、"步骤 2 完成" 等变体
  var re = /(?:【)?步骤\s*完成\s*[：:]\s*(\d+)/g;
  while ((m = re.exec(text)) !== null) {
    state.planExplicitProgress = true;   // 模型用了显式标记 → 停用兜底自动推进
    var n = parseInt(m[1], 10);
    if (n >= 1 && n <= state.planSteps.length && state.planSteps[n - 1].status !== "done") {
      state.planSteps[n - 1].status = "done";
      if (n < state.planSteps.length && state.planSteps[n].status === "pending") {
        state.planSteps[n].status = "running";
      }
      changed = true;
    }
  }
  // 全部完成（多种写法）
  if (/(?:【)?全部完成|✅\s*全部完成|所有步骤完成|已完成全部/.test(text) && state.planStarted) {
    state.planSteps.forEach(function (s) { s.status = "done"; });
    changed = true;
  }
  if (changed) {
    renderPlan();
    refreshTaskStep();   // 状态栏步骤与右栏规划同步
    if (state.planSteps.every(function (s) { return s.status === "done"; })) {
      archivePlan();
    }
  }
}

/** 任务流结束时强制收尾：规划未归档 → 全部打✔并归档（模型可能没按格式输出标记） */
function finalizePlan() {
  if (state.planStarted && state.planSteps.length) {
    state.planSteps.forEach(function (s) { s.status = "done"; });
    archivePlan();
  }
}

/** 兜底步骤推进：模型未输出【步骤完成：N】时，按已完成工具数占步骤比例估算推进。
 *  保守策略：只推进到"已完成的工具数对应的大致步骤"，且不会超过总步骤；每步至少对应若干工具。
 *  工具数从当前流的 flowState（闭包）或 streamCtx.flow（全局）取。 */
function autoAdvancePlan() {
  if (!state.planStarted || !state.planSteps.length) return;
  var tl = null;
  try { if (typeof flowState !== "undefined" && flowState && flowState.toolLog) tl = flowState.toolLog; } catch (e) {}
  if (!tl && streamCtx && streamCtx.flow) tl = streamCtx.flow.toolLog;
  if (!tl) return;
  var toolDone = 0;
  tl.forEach(function (t) { if (t.done) toolDone++; });
  if (toolDone === 0) return;
  // 每步平均对应工具数（总步骤越多，每步工具越多）；估算已完成步骤 = 工具数/每步工具数
  var perStep = Math.max(1, Math.ceil((tl.length || toolDone) / state.planSteps.length));
  var estDone = Math.min(state.planSteps.length, Math.max(1, Math.ceil(toolDone / perStep)));
  var changed = false;
  for (var i = 0; i < estDone; i++) {
    if (state.planSteps[i].status === "pending") {
      state.planSteps[i].status = "done";
      changed = true;
    }
  }
  if (changed) {
    renderPlan();
    refreshTaskStep();   // 状态栏步骤与右栏规划同步
    showTaskStatus("执行中…", toolDone);
  }
}

/** 当前规划归档到历史（任务完成时）——按对话持久化（刷新/重启不丢，各对话独立） */
function archivePlan() {
  if (!state.planStarted || !state.planSteps.length) return;
  var key = "stepHistory_" + (state.conversationId || "new");
  var hist = loadStepHistory(state.conversationId);
  hist.push({ steps: state.planSteps.map(function (s) { return s.text; }), ts: Date.now() });
  if (hist.length > 20) hist.shift();
  state.stepHistory = hist;
  try { localStorage.setItem(key, JSON.stringify(hist)); } catch (e) { /* 忽略 */ }
  renderHistory();
  state.planSteps = [];
  state.planStarted = false;
  state.planExplicitProgress = false;
  planSeq = 0;
  renderPlan();
}

/** 读取某对话的历史步骤（localStorage 持久化，按对话 id 隔离） */
function loadStepHistory(cid) {
  var key = "stepHistory_" + (cid || "new");
  try {
    var raw = localStorage.getItem(key);
    if (raw) {
      var arr = JSON.parse(raw);
      if (Array.isArray(arr)) return arr;
    }
  } catch (e) { /* 忽略损坏数据 */ }
  return [];
}

/** 打开/展开步骤面板 */
function openStepsPanel() {
  // 右栏已移除 → 改为展开状态栏下拉的步骤详情（对标 opencode：底部步骤条）
  var dd = document.getElementById("ts-dropdown");
  var tg = document.getElementById("ts-toggle");
  if (dd) dd.hidden = false;
  if (tg) { tg.textContent = "▴ 步骤"; tg.setAttribute("aria-expanded", "true"); }
  renderPlan();
  renderProposal();
  renderHistory();
}

/** 折叠步骤面板 */
function closeStepsPanel() {
  var dd = document.getElementById("ts-dropdown");
  var tg = document.getElementById("ts-toggle");
  if (dd) dd.hidden = true;
  if (tg) { tg.textContent = "▾ 步骤"; tg.setAttribute("aria-expanded", "false"); }
  stepsOpen = false;
  updateMainPadding();
}

/** 主区让位：file-panel 与 steps-panel 均为 flex 布局参与（#app 内），main 已自动让位，
 * 无需再手动加 padding——避免双重让位导致对话栏被挤变形。 */
function updateMainPadding() {
  var main = document.getElementById("main");
  if (main) main.style.paddingRight = "";
}

/** 重置步骤（新对话/加载历史时调用） */
function resetSteps() {
  state.planSteps = [];
  state.planStarted = false;
  state.planExplicitProgress = false;
  planSeq = 0;
  state.proposals = [];
  state.selectedProposal = null;
  renderProposal();
  renderPlan();
}

// 初始化：折叠按钮 + 浮动按钮 + 拖宽；面板默认展开显示
(function initStepsPanel() {
  // 右栏已移除：步骤改为状态栏下拉展开（对标 opencode 底部步骤条）
  var tg = document.getElementById("ts-toggle");
  if (tg) {
    tg.addEventListener("click", function () {
      var dd = document.getElementById("ts-dropdown");
      if (dd) {
        var willOpen = dd.hidden;
        dd.hidden = !willOpen;
        tg.textContent = willOpen ? "▴ 步骤" : "▾ 步骤";
        tg.setAttribute("aria-expanded", String(willOpen));
      }
    });
  }
  stepsOpen = false;
  renderPlan();
  renderProposal();
  renderHistory();
  updateMainPadding();
})();

/* ===========================================================================
 * 分布式任务面板（集群 P1：任务队列/状态/结果/重试）
 * ========================================================================= */
function loadTasksPanel() {
  var list = document.getElementById("tasks-list");
  var count = document.getElementById("tasks-count");
  if (!list) return;
  fetch("/api/tasks?limit=30").then(function (r) { return r.json(); }).then(function (d) {
    var tasks = d.tasks || [];
    if (count) count.textContent = tasks.length + " 个";
    list.innerHTML = "";
    if (!tasks.length) {
      list.innerHTML = '<div class="steps-empty">暂无任务<br>任务会在后台 worker 池中自动执行</div>';
      return;
    }
    tasks.forEach(function (t) {
      var row = document.createElement("div");
      row.className = "task-item " + t.status;
      var head = document.createElement("div");
      head.className = "task-item-head";
      var status = document.createElement("span");
      status.className = "task-status";
      status.textContent = t.status === "pending" ? "⏳ 排队中" : (t.status === "running" ? "▶ 执行中" : (t.status === "done" ? "✔ 完成" : "✗ " + (t.status === "error" ? "失败" : t.status)));
      var id = document.createElement("span");
      id.className = "task-id";
      id.textContent = t.id.slice(0, 8);
      head.appendChild(id);
      head.appendChild(status);
      var body = document.createElement("div");
      body.className = "task-item-body";
      if (t.status === "done" && t.result) {
        body.textContent = String(t.result.output || "").slice(0, 120);
      } else if (t.error) {
        body.textContent = String(t.error).slice(0, 120);
      } else {
        var msgs = t.messages || [];
        var last = msgs.length ? (typeof msgs[msgs.length - 1].content === "string" ? msgs[msgs.length - 1].content : "") : "";
        body.textContent = last.slice(0, 100) || t.provider + " 任务";
      }
      row.appendChild(head);
      row.appendChild(body);
      if (t.status === "error" || t.status === "done") {
        var retry = document.createElement("button");
        retry.className = "task-retry";
        retry.textContent = "重试";
        retry.addEventListener("click", function () {
          fetch("/api/tasks/" + t.id + "/retry", { method: "POST" }).then(loadTasksPanel);
        });
        row.appendChild(retry);
      }
      list.appendChild(row);
    });
  }).catch(function () {
    list.innerHTML = '<div class="steps-empty">任务面板加载失败</div>';
  });
}

function openTasksPanel() {
  var p = document.getElementById("tasks-panel");
  if (p) { p.hidden = false; loadTasksPanel(); }
}
function closeTasksPanel() {
  var p = document.getElementById("tasks-panel");
  if (p) p.hidden = true;
}

(function initTasksPanel() {
  // 任务按钮已从 UI 移除（用户要求）；面板关闭/刷新保留（分布式任务功能仍可用）
  var cl = document.getElementById("tasks-close");
  if (cl) cl.addEventListener("click", closeTasksPanel);
  var rf = document.getElementById("tasks-refresh");
  if (rf) rf.addEventListener("click", loadTasksPanel);
})();

/* ===========================================================================
 * 计划/执行模式（对标 opencode Plan/Build）：plan 只读规划 → 确认 → build 执行
 * ========================================================================= */
state.mode = "build";   // plan | build
(function initPlanMode() {
  var planBtn = document.getElementById("plan-btn");
  if (!planBtn) return;
  planBtn.addEventListener("click", function () {
    if (state.mode === "plan") {
      state.mode = "build";
      planBtn.textContent = "📋 计划";
      planBtn.classList.remove("on");
      showInfoNote("已切换到执行模式：AI 可直接执行操作");
    } else {
      state.mode = "plan";
      planBtn.textContent = "▶ 执行";
      planBtn.classList.add("on");
      showInfoNote("计划模式已开启：AI 只读规划并输出计划，经你确认后才真正执行");
    }
  });
})();

/* ===========================================================================
 * Markdown 渲染（全文转义后再过 marked）
 * ========================================================================= */

/** 检查 marked 是否可用 */
function detectMarkdown() {
  var md = window.marked;
  state.markdownReady = Boolean(md && typeof md.parse === "function");
}

/** Avoid treating currency, shell variables, and ordinary prose between dollar signs as LaTeX. */
function isLikelyInlineMath(value) {
  var text = String(value || "").trim();
  if (!text || text.length > 200) return false;
  var hasLatexCommand = /\\[A-Za-z]+/.test(text);
  if (/[一-鿿]/.test(text) && !hasLatexCommand) return false;
  if (/^\d[\d,.]*$/.test(text)) return false;                 // $19.99 is currency
  if (/^[A-Za-z_][A-Za-z0-9_-]{2,}$/.test(text)) return false; // $HOME / $variable
  return hasLatexCommand || /[=+*/^_{}<>]|&(?:lt|gt);/.test(text) ||
    /^-?\d+(?:\.\d+)?\s*[A-Za-z]$/.test(text) || /^[A-Za-z]$/.test(text);
}

/**
 * 把原始 AI 文本渲染成 HTML。
 * 流程：escapeHTML（消灭所有原生 HTML）→ marked 解析 markdown 语法。
 * 这样 `<script>` 之类只会显示为字面文本，绝不可能成为可执行标签。
 */
function renderMarkdown(text) {
  var safe = escapeHTML(text);
  // 图片附件标记（"图片附件：xxx.png"）→ markdown 图片语法，渲染缩略图
  safe = safe.replace(/图片附件：([\w.\-]+)/g, '![$1](/files/$1)');
  // 文件附件标记（"文件：xxx.zip（files/xxx.zip）"）→ zip 下载链接
  safe = safe.replace(/文件：([\w.\-]+)（files\/([\w.\-]+)）/g, '📦 [$1](/files/$2)');
  // 裸文件路径（deliver 输出 "下载：/files/xxx.zip"）→ 可点击下载链接（marked 不自动链接相对路径）
  safe = safe.replace(/(^|[^("'\w])\/files\/([\w.\-\u4e00-\u9fa5]+)(?=[\s)）\]，。！？；、,.;!?]|$)/g, '$1[$2](/files/$2)');
  if (state.markdownReady) {
    try {
      // 先提取公式（$$...$$ 块级 / $...$ 行内）占位，避免被 marked 干扰
      var mathParts = [];
      safe = safe.replace(/\$\$([\s\S]+?)\$\$/g, function (_, m) {
        mathParts.push({ latex: m, block: true });
        return "%%MATH%%" + (mathParts.length - 1) + "%%";
      });
      safe = safe.replace(/\$([^$\n]+?)\$/g, function (whole, m) {
        if (!isLikelyInlineMath(m)) return whole;
        mathParts.push({ latex: m, block: false });
        return "%%MATH%%" + (mathParts.length - 1) + "%%";
      });
      var out = window.marked.parse(safe, { gfm: true, breaks: true });
      var html = typeof out === "string" ? out : String(out);
      // 链接动态化：插件返回的 127.0.0.1 链接替换为当前访问地址（手机/局域网也能打开）
      html = html.replace(/https?:\/\/127\.0\.0\.1:8000/g, location.origin);
      html = html.replace(/%%MATH%%(\d+)%%/g, function (_, i) {
        var m = mathParts[+i];
        if (!m) return "";
        return '<span class="math' + (m.block ? " math-block" : " math-inline") +
               '" data-latex="' + escapeHTML(m.latex) + '">' +
               '<span class="math-render"></span>' +
               '<button class="math-copy" type="button" title="复制为 Word 公式">复制公式</button></span>';
      });
      return html;
    } catch (e) { /* marked 抛错 → 走纯文本回退 */ }
  }
  return "<p>" + safe.replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>") + "</p>";
}

/** 公式复制：LaTeX → OMML → 写入剪贴板（HTML 格式，Word/MathType 可粘贴为可编辑公式） */
function copyLatexAsOMML(latex, btn) {
  var conv=function(){
    try{
      var h=katex.renderToString(latex,{displayMode:false,throwOnError:false});
      var m=new DOMParser().parseFromString(h,"text/html").querySelector("math");
      if(!m||!window.__formulaXSL)throw 0;
      var omml=window.__formulaXSL.mathmlToOMML(m);
      if(!omml)throw 0;
      var html='<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><body>'+omml+'</body></html>';
      var done=function(){var o=btn.textContent;btn.textContent="已复制";setTimeout(function(){btn.textContent=o;},1500);};
      if(navigator.clipboard&&window.ClipboardItem){navigator.clipboard.write([new ClipboardItem({"text/html":new Blob([html],{type:"text/html"}),"text/plain":new Blob([latex],{type:"text/plain"})})]).then(done).catch(function(){fallbackCopyText(html,done);});}else{fallbackCopyText(html,done);}
    }catch(e){btn.textContent="失败";setTimeout(function(){btn.textContent="复制公式";},1500);}
  };
  if(window.__formulaXSL&&window.__formulaXSL.ready){conv();}
  else if(window.__formulaXSL){btn.textContent="准备中…";window.__formulaXSL.ensureXSL(function(){btn.textContent="复制公式";conv();});}
  else{btn.textContent="失败";setTimeout(function(){btn.textContent="复制公式";},1500);}
  return;
  fetch("/api/math/omml", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ latex: latex })
  })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data || !data.omml) throw new Error("转换失败");
      var html = '<html xmlns:o="urn:schemas-microsoft-com:office:office" ' +
                 'xmlns:w="urn:schemas-microsoft-com:office:word" ' +
                 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">' +
                 '<body><p>' + data.omml + '</p></body></html>';
      var done = function () {
        var orig = btn.textContent;
        btn.textContent = "已复制";
        setTimeout(function () { btn.textContent = orig; }, 1500);
      };
      if (navigator.clipboard && window.ClipboardItem) {
        navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([html], { type: "text/html" }),
            "text/plain": new Blob([latex], { type: "text/plain" })
          })
        ]).then(done).catch(function () { fallbackCopyText(html, done); });
      } else {
        fallbackCopyText(html, done);
      }
    })
    .catch(function () {
      btn.textContent = "失败";
      setTimeout(function () { btn.textContent = "复制公式"; }, 1500);
    });
}

function fallbackCopyText(text, done) {
  // 富文本复制：无 Clipboard API 时也能把 HTML（含 OMML 公式）复制进剪贴板
  var div = document.createElement("div");
  div.innerHTML = text;
  div.style.position = "fixed";
  div.style.left = "-9999px";
  div.style.top = "0";
  div.setAttribute("contenteditable", "true");
  document.body.appendChild(div);
  var range = document.createRange();
  range.selectNodeContents(div);
  var sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  var ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  sel.removeAllRanges();
  document.body.removeChild(div);
  if (done) done(ok);
}

/**
 * 渲染后的后处理：
 *   1. 去掉 javascript:/data: 等危险链接
 *   2. 给代码块包上"顶栏 + 复制按钮"
 *   3. 渲染公式（KaTeX）并挂"复制为 Word 公式"按钮
 * 每次 innerHTML 全量重建后调用一次。
 */
function postProcess(container) {
  container.querySelectorAll("a[href]").forEach(function (a) {
    var href = (a.getAttribute("href") || "").trim().toLowerCase();
    if (href.indexOf("javascript:") === 0 || href.indexOf("data:") === 0 ||
        href.indexOf("vbscript:") === 0) {
      a.removeAttribute("href");
    }
  });

  container.querySelectorAll("pre").forEach(function (pre) {
    var wrap = document.createElement("div");
    wrap.className = "code-block";
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);

    var bar = document.createElement("div");
    bar.className = "code-bar";

    var code = pre.querySelector("code");
    var m = code ? /language-([\w+-]+)/.exec(code.className || "") : null;
    var label = document.createElement("span");
    label.className = "code-lang";
    label.textContent = m ? m[1] : "code";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-btn";
    btn.textContent = "复制";
    btn.setAttribute("aria-label", "复制代码");
    btn.addEventListener("click", function () {
      copyText(pre.textContent, btn);
    });

    bar.appendChild(label);
    bar.appendChild(btn);
    wrap.insertBefore(bar, pre);
  });

  // diff 代码块：红删绿增高亮（参考 diff 视图），限高 5 行滚动
  container.querySelectorAll("pre code.language-diff").forEach(function (code) {
    var lines = code.textContent.split("\n");
    code.textContent = "";
    lines.forEach(function (ln) {
      var span = document.createElement("span");
      var cls = "";
      if (ln.indexOf("+++") === 0 || ln.indexOf("---") === 0) cls = " diff-meta";
      else if (ln.indexOf("+") === 0) cls = " diff-add";
      else if (ln.indexOf("-") === 0) cls = " diff-del";
      span.className = "diff-line" + cls;
      span.textContent = ln + "\n";
      code.appendChild(span);
    });
  });

/**
 * 按需加载 KaTeX（271KB 脚本不阻塞首屏；首次遇到公式才注入）。
 * 返回 Promise；加载完成 window.katex 可用。CSS 已在 head 用 onload 懒加载。
 */
var _katexLoading = null;
function ensureKaTeX() {
  if (window.katex) return Promise.resolve();
  if (_katexLoading) return _katexLoading;
  _katexLoading = new Promise(function (resolve) {
    var s = document.createElement("script");
    s.src = "vendor/katex/katex.min.js";
    s.onload = function () { resolve(); };
    s.onerror = function () { resolve(); };   // 加载失败不阻塞，保持 latex 文本兜底
    document.head.appendChild(s);
  });
  return _katexLoading;
}

/** 检测文本/容器是否含公式（$$...$$ / \(...\) / $...$），含则确保 KaTeX 已加载 */
function ensureKaTeXForText(text) {
  if (window.katex || !text) return;
  var value = String(text);
  var inlineMatch = /(?<!\\)\$(?!\$)([^\n$]{1,200})\$(?!\$)/.exec(value);
  if (/\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)/.test(value) ||
      (inlineMatch && isLikelyInlineMath(inlineMatch[1]))) {
    ensureKaTeX();
  }
}

/** 公式：KaTeX 渲染 + 复制按钮 */
  container.querySelectorAll(".math").forEach(function (el) {
    var latex = el.dataset.latex || "";
    var target = el.querySelector(".math-render");
    if (target) {
      if (window.katex && !state.streaming) {
        // 流式期间跳过 KaTeX 渲染（降低卡顿），收尾定稿时再渲染
        try {
          katex.render(latex, target, {
            displayMode: el.classList.contains("math-block"),
            throwOnError: false
          });
        } catch (e) { target.textContent = latex; }
      } else if (!window.katex && !state.streaming) {
        // KaTeX 未加载（懒加载场景）→ 触发加载，加载完成后渲染
        target.textContent = latex;
        ensureKaTeX().then(function () {
          if (window.katex && !state.streaming) {
            try {
              katex.render(latex, target, {
                displayMode: el.classList.contains("math-block"),
                throwOnError: false
              });
            } catch (e) {}
          }
        });
      } else {
        target.textContent = latex;
      }
    }
    var copyBtn = el.querySelector(".math-copy");
    if (copyBtn) {
      copyBtn.addEventListener("click", function () {
        copyLatexAsOMML(latex, copyBtn);
      });
    }
  });

  // 图片链接（/files/*.png 等）：直接内联渲染为图片（模型画图/发送图像），点击看大图
  container.querySelectorAll("a[href*='/files/']").forEach(function (a) {
    var m = /\.(png|jpe?g|gif|webp|svg|bmp)([?#].*)?$/i.exec(a.getAttribute("href") || "");
    if (m) {
      var img = document.createElement("img");
      img.className = "md-img";
      img.src = a.href;
      img.alt = decodeURIComponent(a.pathname.split("/").pop() || "图片");
      img.loading = "lazy";
      a.parentNode.replaceChild(img, a);
      img.addEventListener("click", function () {
        try { openFilePanel(new URL(img.src).pathname); } catch (e) { /* 忽略 */ }
      });
    }
  });

  // 普通网页链接（http/https，非 /files/ 且非图片）：点击 -> 模态预览浮层，不直接跳转
  container.querySelectorAll("a[href]").forEach(function (a) {
    if (a.dataset.webWired) return;
    var h = (a.getAttribute("href") || "").trim();
    if (!/^https?:\/\//i.test(h)) return;
    if (h.indexOf("/files/") !== -1) return;
    a.dataset.webWired = "1";
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
    a.addEventListener("click", function (e) {
      e.preventDefault();
      openLinkPreview(a.href);
    });
  });

  // 文件链接（/files/）：悬停显示下载按钮；点击链接本身打开右栏预览，不再跳转
  container.querySelectorAll("a[href*='/files/']").forEach(function (a) {
    if (a.dataset.fileWired) return;
    a.dataset.fileWired = "1";
    var _zh = a.getAttribute("href") || "";
    if (/\.(zip|rar|7z|tar|gz|bz2)([?#].*)?$/i.test(_zh)) {
      // zip/压缩包：渲染为下载卡片，点击直接下载（压缩包无法在线预览）
      var _zfn = "";
      try { _zfn = decodeURIComponent(new URL(a.href).pathname.split("/").pop() || ""); }
      catch (err) { _zfn = a.getAttribute("download") || _zh.split("/").pop() || ""; }
      a.addEventListener("click", function (e) { e.preventDefault(); downloadFileByName(_zfn); });
      var zc = document.createElement("span");
      zc.className = "zip-card";
      zc.title = "点击下载 " + _zfn;
      var zi = document.createElement("span");
      zi.className = "zip-card-icon";
      zi.textContent = "📦";
      var zn = document.createElement("span");
      zn.className = "zip-card-name";
      zn.textContent = _zfn;
      var zd = document.createElement("button");
      zd.type = "button";
      zd.className = "zip-card-dl";
      zd.textContent = "下载";
      zd.setAttribute("aria-label", "下载 " + _zfn);
      zd.addEventListener("click", function (ev) { ev.preventDefault(); ev.stopPropagation(); downloadFileByName(_zfn); });
      var zc2 = document.createElement("button");
      zc2.type = "button";
      zc2.className = "zip-card-copy";
      zc2.textContent = "复制链接";
      zc2.setAttribute("aria-label", "复制下载链接");
      zc2.addEventListener("click", function (ev) {
        ev.preventDefault(); ev.stopPropagation();
        var abs = "";
        try { abs = new URL(a.href, location.origin).href; } catch (e) { abs = a.href; }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(abs).then(
            function () { showErrorNote("下载链接已复制"); },
            function () { fallbackCopyText(abs); showErrorNote("下载链接已复制"); });
        } else { fallbackCopyText(abs); showErrorNote("下载链接已复制"); }
      });
      zc.appendChild(zi);
      zc.appendChild(zn);
      zc.appendChild(zd);
      zc.appendChild(zc2);
      zc.addEventListener("click", function (e) {
        if (e.target === zd || e.target === zc2) return;
        downloadFileByName(_zfn);
      });
      a.parentNode.insertBefore(zc, a);
      a.parentNode.removeChild(a);
      return;
    }
    a.addEventListener("click", function (e) {
      e.preventDefault();
      try {
        openFilePanel(new URL(a.href).pathname);
      } catch (err) { /* 非标准 URL：保持默认行为 */ }
    });
    var wrap = document.createElement("span");
    wrap.className = "file-link";
    a.parentNode.insertBefore(wrap, a);
    wrap.appendChild(a);
    var dl = document.createElement("button");
    dl.type = "button";
    dl.className = "file-link-dl";
    dl.textContent = "⬇";
    dl.title = "下载文件";
    dl.setAttribute("aria-label", "下载文件");
    dl.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      // fetch-blob 下载（WebView2 客户端可靠；http 直链 + download 属性在 WebView2 里不触发下载）
      var _nm = "";
      try {
        _nm = decodeURIComponent(new URL(a.href).pathname.split("/").pop() || "");
      } catch (err) {
        _nm = a.getAttribute("download") || "";
      }
      downloadFileByName(_nm);
    });
    wrap.appendChild(dl);
  });
}

/* ===========================================================================
 * 文件预览右栏（deliver 插件给的 /files/ 链接：悬停下载、点击预览）
 * ========================================================================= */

/** 文本类扩展名：右栏直接读取内容展示 */
var FILE_TEXT_EXTS = { md:1, txt:1, py:1, js:1, mjs:1, cjs:1, ts:1, tsx:1, jsx:1,
  json:1, html:1, htm:1, css:1, scss:1, less:1, c:1, cpp:1, cc:1, h:1, hpp:1,
  java:1, go:1, rs:1, rb:1, php:1, yml:1, yaml:1, ini:1, conf:1, cfg:1, log:1,
  csv:1, tsv:1, xml:1, sh:1, bat:1, cmd:1, ps1:1, sql:1, toml:1, vue:1, svelte:1,
  lua:1, dart:1, kt:1, swift:1, gitignore:1, env:1 };
/** 图片扩展名：右栏直接展示 */
var FILE_IMG_EXTS = { png:1, jpg:1, jpeg:1, gif:1, webp:1, svg:1, bmp:1, ico:1, avif:1 };

var filePanelName = "";   // 当前预览的文件名

/** 解析链接 -> 显示友好域名 + 路径 */
function parseLinkParts(url) {
  try {
    var u = new URL(url);
    return { domain: u.hostname.replace(/^www\./, ""), path: (u.pathname === "/" ? "" : u.pathname) + u.search };
  } catch (e) {
    return { domain: url, path: "" };
  }
}

/** 打开链接预览浮层（网页链接点击 -> 先预览，不直接跳转） */
var _linkPreviewUrl = "";
function openLinkPreview(url) {
  var modal = document.getElementById("link-preview-modal");
  if (!modal) return;
  _linkPreviewUrl = url;
  var parts = parseLinkParts(url);
  var dom = document.getElementById("link-preview-domain");
  var path = document.getElementById("link-preview-path");
  if (dom) dom.textContent = parts.domain;
  if (path) path.textContent = parts.path;
  var fav = document.getElementById("link-preview-favicon");
  if (fav) fav.textContent = parts.domain === "127.0.0.1" || parts.domain === "localhost" ? "🖥️" : "🔗";
  // 重置预览区
  var loading = document.getElementById("link-preview-loading");
  var fail = document.getElementById("link-preview-fail");
  var frame = document.getElementById("link-preview-frame");
  if (loading) loading.hidden = false;
  if (fail) fail.hidden = true;
  if (frame) {
    frame.hidden = false;
    frame.src = url;   // 重新加载新链接
  }
  modal.hidden = false;
  if (frame) frame.focus();
}

/** 关闭链接预览浮层 */
function closeLinkPreview() {
  var modal = document.getElementById("link-preview-modal");
  if (modal) modal.hidden = true;
  var frame = document.getElementById("link-preview-frame");
  if (frame) frame.src = "about:blank";
}

/** 在预览界面点击 -> 新标签页打开（不在原页面跳转） */
function openLinkPreviewInNewTab() {
  if (_linkPreviewUrl) {
    var a = document.createElement("a");
    a.href = _linkPreviewUrl;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}

/** 复制链接地址 */
function copyLinkPreviewUrl(btn) {
  if (!_linkPreviewUrl) return;
  var done = function (ok) {
    if (!btn) return;
    var orig = btn.textContent;
    btn.textContent = ok ? "已复制" : "复制失败";
    setTimeout(function () { btn.textContent = orig; }, 1400);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(_linkPreviewUrl).then(function () { done(true); }).catch(function () { done(false); });
  } else {
    fallbackCopyText(_linkPreviewUrl, function (ok) { done(ok); });
  }
}

/** 绑定链接预览浮层的控件事件（init 时调用一次） */
function bindLinkPreview() {
  var open = document.getElementById("link-preview-open");
  var copy = document.getElementById("link-preview-copy");
  var close = document.getElementById("link-preview-close");
  var failOpen = document.getElementById("link-preview-fail-open");
  var frame = document.getElementById("link-preview-frame");
  var modal = document.getElementById("link-preview-modal");
  if (open) open.addEventListener("click", openLinkPreviewInNewTab);
  if (copy) copy.addEventListener("click", function () { copyLinkPreviewUrl(copy); });
  if (close) close.addEventListener("click", closeLinkPreview);
  if (failOpen) failOpen.addEventListener("click", openLinkPreviewInNewTab);
  // 加载完成：隐藏 loading；超时/空白（被 X-Frame-Options 拦截等）显示失败提示
  if (frame) {
    frame.addEventListener("load", function () {
      var loading = document.getElementById("link-preview-loading");
      var fail = document.getElementById("link-preview-fail");
      if (loading) loading.hidden = true;
      if (!fail) return;
      // 跨域 iframe 无法读取内容；本地 /files/ 等可尝试检测空白
      try {
        var doc = frame.contentDocument;
        if (doc && (!doc.body || !doc.body.innerHTML || doc.body.innerHTML.trim() === "")) {
          fail.hidden = false;
        } else if (doc && doc.body && doc.body.innerHTML.indexOf("拒绝访问") !== -1) {
          fail.hidden = false;
        }
      } catch (e) {
        // 跨域：视为预览成功（已加载），不再显示失败
      }
    });
    frame.addEventListener("error", function () {
      var fail = document.getElementById("link-preview-fail");
      if (fail) fail.hidden = false;
    });
  }
  // 点遮罩关闭
  if (modal) {
    modal.addEventListener("click", function (e) {
      if (e.target === modal || e.target.classList.contains("modal-backdrop")) {
        closeLinkPreview();
      }
    });
  }
  // Esc 关闭
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal && !modal.hidden) closeLinkPreview();
  });
}

/** 打开右栏预览（path 形如 /files/说明文档.md） */
function openFilePanel(path) {
  var panel = document.getElementById("file-panel");
  if (!panel) return;
  filePanelName = decodeURIComponent(String(path || "").split("/").pop() || "");
  panel.hidden = false;
  var resizer = document.getElementById("file-panel-resizer");
  if (resizer) resizer.hidden = false;
  var title = document.getElementById("file-panel-title");
  if (title) title.textContent = filePanelName || "文件预览";
  renderFilePreview(filePanelName);
  updateMainPadding();
}

/** 关闭右栏 */
function closeFilePanel() {
  var panel = document.getElementById("file-panel");
  if (panel) {
    panel.hidden = true;
    panel.classList.remove("fullscreen");
  }
  var resizer = document.getElementById("file-panel-resizer");
  if (resizer) resizer.hidden = true;
  var fsBtn = document.getElementById("file-panel-fullscreen");
  updateMainPadding();
  if (fsBtn) {
    fsBtn.textContent = "⛶";
    fsBtn.title = "全屏";
  }
}

/* 智能体活动面板：开关 / 清空 / 计数 */
function openAgentPanel() {
  var p = document.getElementById("agent-panel");
  if (p) p.hidden = false;
  state.agentPanelDismissed = false;   // 手动打开 → 后续 agent 工作可继续自动弹开
}
function closeAgentPanel() {
  var p = document.getElementById("agent-panel");
  if (p) p.hidden = true;
  state.agentPanelDismissed = true;    // 手动关闭 → 本会话不再自动弹开（尊重用户选择）
}
function clearAgentPanel() {
  var list = document.getElementById("agent-panel-list");
  if (list) {
    list.innerHTML = '<div class="agent-panel-empty">暂无智能体调用<br>当 AI 委托子任务给智能体时，会显示在这里</div>';
  }
  agentActivityItems = {};
  state.agentPanelDismissed = false;   // 清空 → 恢复自动打开行为
  var c = document.getElementById("agent-panel-count");
  if (c) c.textContent = "0";
}
(function initAgentPanel() {
  var btn = document.getElementById("agent-panel-btn");
  if (btn) btn.addEventListener("click", openAgentPanel);
  var cl = document.getElementById("agent-panel-close");
  if (cl) cl.addEventListener("click", closeAgentPanel);
  var cc = document.getElementById("agent-panel-clear");
  if (cc) cc.addEventListener("click", clearAgentPanel);
})();

/** 按扩展名渲染预览：pdf/图片直接展示；文本/代码读取内容；Office 等给占位提示 */
function renderFilePreview(name) {
  var body = document.getElementById("file-panel-body");
  if (!body) return;
  body.className = "file-panel-body";
  body.style.cssText = "";
  body.innerHTML = "";
  // 模式切换 tab（html：运行/源码）
  var modeBar = document.getElementById("file-panel-mode");
  if (modeBar) modeBar.innerHTML = "";
  var ext = (String(name).split(".").pop() || "").toLowerCase();
  var url = "/files/" + encodeURIComponent(name);
  if (ext === "pdf") {
    // PDF：浏览器原生 PDF 查看器界面
    var f = document.createElement("iframe");
    f.src = url;
    f.title = name;
    body.appendChild(f);
  } else if (ext === "html" || ext === "htm") {
    // HTML：运行 / 源码 可切换
    buildModeTabs(["运行", "源码"]);
    var f = document.createElement("iframe");
    f.src = url;
    f.title = name;
    f.className = "html-run-frame";
    body.appendChild(f);
    // 加载失败/超时提示（同源 iframe 可检测）
    var loadTimer = setTimeout(function () {
      try {
        if (f.contentDocument && f.contentDocument.body && f.contentDocument.body.innerHTML.trim()) return;
      } catch (e) { return; }
      var err = document.createElement("div");
      err.className = "file-panel-placeholder";
      err.textContent = "HTML 加载失败：文件可能不完整（如写入被截断）。可切换「源码」查看内容，或用系统软件打开。";
      body.appendChild(err);
    }, 6000);
    var pre = document.createElement("pre");
    pre.className = "html-src-pre";
    pre.hidden = true;
    pre.textContent = "加载中…";
    body.appendChild(pre);
    fetch(url).then(function (r) { return r.text(); }).then(function (t) {
      pre.textContent = t;
    }).catch(function () { pre.textContent = "无法读取源码"; });
    var btns = document.querySelectorAll("#file-panel-mode .fp-mode-btn");
    if (btns.length === 2) {
      btns[0].classList.add("active");
      btns[0].addEventListener("click", function () {
        btns[0].classList.add("active"); btns[1].classList.remove("active");
        f.hidden = false; pre.hidden = true;
      });
      btns[1].addEventListener("click", function () {
        btns[1].classList.add("active"); btns[0].classList.remove("active");
        f.hidden = true; pre.hidden = false;
      });
    }
  } else if (ext === "md" || ext === "markdown") {
    // Markdown：VSCode 风格预览（渲染后文档）
    var bar = document.createElement("div");
    bar.className = "fp-md-bar";
    bar.innerHTML = '<span class="fp-md-label">Markdown 预览</span><span class="fp-md-dot"></span>';
    body.appendChild(bar);
    var doc = document.createElement("div");
    doc.className = "md fp-md-doc";
    doc.innerHTML = "加载中…";
    body.appendChild(doc);
    fetch(url).then(function (r) { return r.text(); }).then(function (t) {
      doc.innerHTML = renderMarkdown(t);
      postProcess(doc);
    }).catch(function () { doc.textContent = "无法读取文件内容：" + name; });
  } else if (ext === "pptx" || ext === "ppt") {
    // PPT：先秒开文字预览（python-pptx 解析），后台 Office 转 PDF 完成后可切换
    smartOfficePreview(name, function () { renderPptPreview(name); });
  } else if (ext === "docx" || ext === "doc") {
    // Word：先秒开文字预览（python-docx 解析），后台 Office 转 PDF 完成后可切换
    smartOfficePreview(name, function () { renderDocxPreview(name); });
  } else if (ext === "xlsx" || ext === "xls") {
    // Excel：先秒开表格预览（openpyxl 解析），后台 Office 转 PDF 完成后可切换
    smartOfficePreview(name, function () { renderXlsxPreview(name); });
  } else if (FILE_IMG_EXTS[ext]) {
    var img = document.createElement("img");
    img.src = url;
    img.alt = name;
    body.appendChild(img);
  } else if (FILE_TEXT_EXTS[ext]) {
    body.classList.add("is-code");
    var pre = document.createElement("pre");
    pre.textContent = "加载中…";
    body.appendChild(pre);
    fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.text();
    }).then(function (t) {
      pre.textContent = t;
    }).catch(function () {
      pre.textContent = "无法读取文件内容：" + name;
    });
  } else {
    var p = document.createElement("div");
    p.className = "file-panel-placeholder";
    var h1 = document.createElement("p");
    h1.textContent = "浏览器无法直接预览 ." + (ext || "?") + " 文件";
    var h2 = document.createElement("p");
    h2.textContent = "点上方「打开」用电脑里的软件查看（Word / Excel / PPT / VS Code…），或点 ⬇ 下载";
    p.appendChild(h1);
    p.appendChild(h2);
    body.appendChild(p);
  }
}

/** Office 文档预览：转 PDF 用查看器（真正渲染排版）；仅在明确失败时回退 */
/**
 * Office 文件智能预览：先秒开文字/表格预览（不启动 Office），
 * 后台静默转 PDF（win32com，首次 5-40 秒）；转换完成且已缓存 → 顶部出现
 * 「查看 Office 渲染版」按钮，点击切换为 PDF 查看器（还原 Office 排版）。
 * 慢是因为 Office 转换；文字预览保证用户先看到内容，不干等。
 */
function smartOfficePreview(name, textRenderer) {
  var body = document.getElementById("file-panel-body");
  if (!body) return;
  body.innerHTML = "";
  // 切换条作为独立浮层（不放进 body，避免被文字预览的 innerHTML 重建清掉）
  var pdfReady = false;
  var switchBar = document.createElement("div");
  switchBar.className = "fp-office-bar fp-office-float";
  switchBar.hidden = true;
  switchBar.innerHTML = '<span class="fp-office-label">Office 渲染版已就绪</span>'
    + '<button id="fp-switch-pdf" class="file-panel-btn" type="button">查看 Office 渲染版 ⛶</button>';
  var panel = document.getElementById("file-panel");
  if (panel) {
    panel.style.position = "relative";
    panel.appendChild(switchBar);   // 挂到面板（body 的兄弟），不受预览重建影响
  }
  // 渲染文字预览（秒开）
  if (typeof textRenderer === "function") textRenderer();

  // 后台探测 PDF 是否已缓存/能否快速转换：用一个隐藏 iframe 加载转换端点，
  // 但仅在用户切换时才真正展示。加载成功（含缓存命中）→ 显示切换按钮。
  var probe = document.createElement("iframe");
  probe.src = "/api/files/preview/" + encodeURIComponent(name);
  probe.title = name;
  probe.style.display = "none";
  document.body.appendChild(probe);   // 挂到 body 而非面板，避免干扰布局
  var triedSwitch = false;
  probe.onload = function () {
    // 缓存命中 → 转换端点返回 PDF，onload 即触发（同源可读）
    try {
      if (probe.contentDocument && probe.contentDocument.body
          && probe.contentDocument.body.childElementCount > 0) {
        pdfReady = true;
        switchBar.hidden = false;
      }
    } catch (e) {
      // 跨域不可读但 onload 触发 → 大概率是 PDF 查看器（已就绪）
      pdfReady = true;
      switchBar.hidden = false;
    }
  };
  // 转换慢：12 秒内未就绪也不阻塞文字预览（用户已看到内容）；就绪后随时可切
  var switchBtn = document.getElementById("fp-switch-pdf");
  if (switchBtn) {
    switchBtn.addEventListener("click", function () {
      if (triedSwitch) return;
      triedSwitch = true;
      switchBar.remove();
      var body2 = document.getElementById("file-panel-body");
      if (!body2) return;
      body2.innerHTML = "";
      var f = document.createElement("iframe");
      f.src = "/api/files/preview/" + encodeURIComponent(name);
      f.title = name;
      body2.appendChild(f);
    });
  }
}

/** Excel 预览器：多工作表 tab + 表格渲染 */
function renderXlsxPreview(name) {
  var body = document.getElementById("file-panel-body");
  if (!body) return;
  body.classList.add("xlsx-mode");
  body.innerHTML = '<div class="steps-empty">加载中…</div>';
  fetch("/api/files/xlsx/" + encodeURIComponent(name))
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.ok) throw new Error(data.detail || "解析失败");
      var sheets = data.sheets || [];
      body.innerHTML = "";
      if (!sheets.length) {
        body.innerHTML = '<div class="steps-empty">（空表格）</div>';
        return;
      }
      // 工作表 tab
      var tabs = document.createElement("div");
      tabs.className = "xlsx-tabs";
      body.appendChild(tabs);
      var grid = document.createElement("div");
      grid.className = "xlsx-grid-wrap";
      body.appendChild(grid);
      function renderSheet(i) {
        var s = sheets[i];
        grid.innerHTML = "";
        // 清 tab 高亮
        tabs.querySelectorAll(".xlsx-tab").forEach(function (t, ti) {
          t.classList.toggle("active", ti === i);
        });
        var table = document.createElement("table");
        table.className = "xlsx-table";
        (s.rows || []).forEach(function (row, ri) {
          var tr = document.createElement("tr");
          row.forEach(function (cell, ci) {
            var td = document.createElement(ri === 0 ? "th" : "td");
            td.textContent = cell;
            tr.appendChild(td);
          });
          table.appendChild(tr);
        });
        grid.appendChild(table);
        if (grid.scrollTop) grid.scrollTop = 0;
      }
      sheets.forEach(function (s, i) {
        var t = document.createElement("button");
        t.type = "button";
        t.className = "xlsx-tab" + (i === 0 ? " active" : "");
        t.textContent = s.name;
        t.addEventListener("click", function () { renderSheet(i); });
        tabs.appendChild(t);
      });
      renderSheet(0);
    })
    .catch(function (e) {
      body.innerHTML = '<div class="steps-empty">Excel 预览失败：' + e.message + '</div>';
    });
}

/** 面板头部模式切换 tab（html：运行 / 源码） */
function buildModeTabs(labels) {  var modeBar = document.getElementById("file-panel-mode");
  if (!modeBar) return;
  modeBar.innerHTML = "";
  labels.forEach(function (label, i) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "fp-mode-btn" + (i === 0 ? " active" : "");
    b.textContent = label;
    modeBar.appendChild(b);
  });
}

/* ---------- PPT 预览：播放器界面（缩略图列 + 大页面 + 翻页） ---------- */
var pptState = { slides: [], current: 0 };

function renderPptPreview(name) {
  var body = document.getElementById("file-panel-body");
  if (!body) return;
  body.innerHTML = "";
  body.classList.add("ppt-mode");
  fetch("/api/files/pptx/" + encodeURIComponent(name))
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.ok) throw new Error(data.detail || "解析失败");
      pptState.slides = data.slides || [];
      pptState.current = 0;
      var wrap = document.createElement("div");
      wrap.className = "ppt-wrap";
      // 缩略图列
      var thumbs = document.createElement("div");
      thumbs.className = "ppt-thumbs";
      pptState.slides.forEach(function (s, i) {
        var t = document.createElement("div");
        t.className = "ppt-thumb" + (i === 0 ? " active" : "");
        t.textContent = (i + 1);
        t.title = "第 " + (i + 1) + " 页";
        t.addEventListener("click", function () { showPptSlide(i); });
        thumbs.appendChild(t);
      });
      wrap.appendChild(thumbs);
      // 主显示区
      var main = document.createElement("div");
      main.className = "ppt-main";
      wrap.appendChild(main);
      // 翻页控制
      var nav = document.createElement("div");
      nav.className = "ppt-nav";
      var prev = document.createElement("button");
      prev.type = "button"; prev.className = "fp-mode-btn"; prev.textContent = "◀ 上一页";
      var counter = document.createElement("span");
      counter.className = "ppt-counter";
      var next = document.createElement("button");
      next.type = "button"; next.className = "fp-mode-btn"; next.textContent = "下一页 ▶";
      nav.appendChild(prev); nav.appendChild(counter); nav.appendChild(next);
      wrap.appendChild(nav);
      body.appendChild(wrap);
      window._showPptSlide = function (i) {
        if (!pptState.slides.length) return;
        pptState.current = Math.max(0, Math.min(pptState.slides.length - 1, i));
        var s = pptState.slides[pptState.current];
        main.innerHTML = "";
        var page = document.createElement("div");
        page.className = "ppt-page";
        s.texts.forEach(function (t) {
          var p = document.createElement("div");
          p.className = "ppt-text";
          p.textContent = t;
          page.appendChild(p);
        });
        s.imgs.forEach(function (src) {
          var img = document.createElement("img");
          img.src = src;
          img.className = "ppt-img";
          page.appendChild(img);
        });
        main.appendChild(page);
        counter.textContent = (pptState.current + 1) + " / " + pptState.slides.length;
        document.querySelectorAll(".ppt-thumb").forEach(function (t, ti) {
          t.classList.toggle("active", ti === pptState.current);
        });
      };
      window.showPptSlide = window._showPptSlide;
      prev.addEventListener("click", function () { showPptSlide(pptState.current - 1); });
      next.addEventListener("click", function () { showPptSlide(pptState.current + 1); });
      showPptSlide(0);
    })
    .catch(function (e) {
      var p = document.createElement("div");
      p.className = "file-panel-placeholder";
      p.textContent = "PPT 预览失败：" + ((e && e.message) || e);
      body.appendChild(p);
    });
}

/* ---------- Word 文档预览器（docx：标题/段落/列表/表格渲染） ---------- */
function renderDocxPreview(name) {
  var body = document.getElementById("file-panel-body");
  if (!body) return;
  body.innerHTML = "";
  body.classList.add("is-code");
  var doc = document.createElement("div");
  doc.className = "docx-preview";
  body.appendChild(doc);
  fetch("/api/files/docx/" + encodeURIComponent(name))
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.ok) throw new Error(data.detail || "解析失败");
      doc.innerHTML = "";
      (data.blocks || []).forEach(function (b) {
        var el;
        if (b.type === "heading") {
          el = document.createElement("h" + Math.min(4, b.level || 1));
          el.textContent = b.text;
          el.className = "docx-h" + Math.min(4, b.level || 1);
        } else if (b.type === "list") {
          el = document.createElement("div");
          el.className = "docx-li";
          el.textContent = "• " + b.text;
        } else if (b.type === "formula") {
          // 公式块：居中展示（Word 原生公式在预览中以内容文本呈现）
          el = document.createElement("div");
          el.className = "docx-formula";
          el.textContent = b.text;
        } else if (b.type === "table") {
          var tbl = document.createElement("table");
          tbl.className = "docx-table";
          (b.rows || []).forEach(function (row, ri) {
            var tr = document.createElement("tr");
            (row || []).forEach(function (cell) {
              var td = document.createElement(ri === 0 ? "th" : "td");
              td.textContent = cell;
              tr.appendChild(td);
            });
            tbl.appendChild(tr);
          });
          el = tbl;
        } else {
          el = document.createElement("p");
          el.textContent = b.text;
          el.className = "docx-p";
        }
        doc.appendChild(el);
      });
      if (!data.blocks || !data.blocks.length) {
        doc.textContent = "（文档没有可预览的内容）";
      }
    })
    .catch(function (e) {
      doc.innerHTML = "";
      var p = document.createElement("div");
      p.className = "file-panel-placeholder";
      p.textContent = "Word 预览失败：" + ((e && e.message) || e) + "。可点上方「打开」用 Word 查看，或下载。";
      doc.appendChild(p);
    });
}

/** 下载当前文件（fetch-blob：WebView2 客户端也能可靠触发下载，与 ZIP 导出同款方案） */
function downloadCurrentFile() {
  if (!filePanelName) return;
  downloadFileByName(filePanelName);
}

/** 通用文件下载：fetch → blob → 触发下载（浏览器与 WebView2 均可靠） */
function downloadFileByName(name) {
  if (!name) return;
  // ★根因修复：之前用 fetch().then() 异步回调里的 a.click() → 非用户手势，
  // 浏览器/WebView2 安全策略会静默阻止非手势触发的下载（表现就是"点了没反应"）。
  // 修复：在用户点击的同步调用栈内直接导航到 attachment URL（带 Content-Disposition:
  // attachment，WebView2/浏览器都强制触发下载，不会变成页面导航）。
  try {
    var _a = document.createElement("a");
    _a.href = "/api/files/download/" + encodeURIComponent(name);
    _a.style.display = "none";
    _a.rel = "noopener";
    document.body.appendChild(_a);
    _a.click();          // 同步、用户手势内触发 → 可靠下载
    _a.remove();
    return;
  } catch (e) { /* 回退到 fetch-blob */ }
  fetch("/api/files/download/" + encodeURIComponent(name))
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.blob();
    })
    .then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    })
    .catch(function (e) { showErrorNote("下载失败：" + ((e && e.message) || e)); });
}

/** 用电脑上的默认软件打开当前文件（服务器 os.startfile） */
function openCurrentFileWithSystem() {
  if (!filePanelName) return;
  var btn = document.getElementById("file-panel-open");
  var orig = btn ? btn.textContent : "";
  if (btn) btn.textContent = "…";
  fetch("/api/files/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: filePanelName })
  }).then(function (r) { return r.json(); }).then(function (d) {
    if (btn) {
      btn.textContent = (d && d.ok) ? "已打开" : "!";
      setTimeout(function () { if (btn) btn.textContent = orig; }, 1200);
    }
  }).catch(function () {
    if (btn) {
      btn.textContent = "!";
      setTimeout(function () { if (btn) btn.textContent = orig; }, 1200);
    }
  });
}

/** 全屏 / 还原右栏 */
function toggleFilePanelFullscreen() {
  var panel = document.getElementById("file-panel");
  if (!panel) return;
  var fs = panel.classList.toggle("fullscreen");
  var btn = document.getElementById("file-panel-fullscreen");
  if (btn) {
    btn.textContent = fs ? "还原" : "⛶";
    btn.title = fs ? "退出全屏" : "全屏";
  }
}

/* ===========================================================================
 * 状态灯 & 发送按钮
 * ========================================================================= */

/** 切换顶部状态灯：idle（绿）/ streaming（琥珀脉冲）/ error（红） */
function setStatus(mode) {
  clearTimeout(state.statusTimer);
  var labels = { idle: "在线", streaming: "生成中", error: "连接错误" };
  els.statusDot.className = "status-dot " + mode;
  els.statusDot.setAttribute("aria-label", labels[mode] || "");
  els.statusDot.title = labels[mode] || "";
  if (mode === "error") {
    // 错误状态 8 秒后自动回到空闲，避免红点一直挂着误导
    state.statusTimer = setTimeout(function () { setStatus("idle"); }, 8000);
  }
}

/** 发送按钮双形态：'send'（圆形箭头）/ 'stop'（方形停止） */
function setSendBtn(mode) {
  // 图标由 updateSendBtn 统一决定：生成中且未输入 → 停止方块；用户在输入 → 发送箭头
  els.sendBtn.setAttribute("aria-label", mode === "stop" ? "停止生成" : "发送");
  updateSendBtn();
}

/** 当前对话是否正在生成（含后台活跃流）：决定发送/停止按钮状态。
 * 修复：切走再切回时 state.streaming 可能已被置 false，但原对话流仍在后台跑，
 * 此时按钮必须仍显示"停止"；否则用户无法停止正在生成的对话。 */
function currentConvStreaming() {
  if (state.streaming) return true;
  var cid = state.conversationId;
  if (cid && streamCtxs[cid] && !streamCtxs[cid].finished) return true;
  return false;
}

/** 根据输入框是否有字 + 是否在生成，决定按钮可用性与图标 */
function updateSendBtn() {
  var hasText = els.input.value.trim().length > 0;
  var showStop = currentConvStreaming();
  els.sendBtn.disabled = !hasText && !showStop;
  // 模型回答中一律显示停止按钮；回答结束（无生成）才显示发送箭头
  els.sendBtn.classList.toggle("stop", showStop);
  els.sendIcon.hidden = showStop;
  els.stopIcon.hidden = !showStop;
  // 输入框内容变化 → 刷新队列条上的"调整方向"按钮显隐（有输入且有队列时可用）
  renderSendQueue();
}

/* ---------- 多步骤任务进度状态栏（对标 opencode：底部实时显示当前步骤） ---------- */
var taskToolCount = 0;   // 本轮已执行工具数

/* 流式解析节流状态（onDelta/onThink 全文解析器的 120ms 节流；流结束重置） */
var _parseThrottleTs = 0, _parseThrottleLen = 0;
var _scrollThrottleTs = 0, _thinkThrottleTs = 0;

/* 状态栏 DOM 缓存（惰性初始化；避免每 token 多次 getElementById） */
var _tsBarEl = null, _tsTextEl = null, _tsStepEl = null;
var _tsLastText = null;   // 变化守卫：文本未变不写 DOM
var _tsLastStep = null;
function _tsEls() {
  if (!_tsBarEl) _tsBarEl = document.getElementById("task-status");
  if (!_tsTextEl) _tsTextEl = document.getElementById("ts-text");
  if (!_tsStepEl) _tsStepEl = document.getElementById("ts-step");
  return _tsBarEl;
}

/** 状态栏与右栏步骤规划同步：刷新"当前步骤文本 + 步骤 X/N + 耗时"（不改变状态栏显隐） */
function refreshTaskStep(text) {
  var bar = _tsEls();
  if (!bar || bar.hidden) return;
  var t = _tsTextEl;
  if (t) {
    // 优先显示当前步骤的具体内容（对标 opencode：显示"正在做什么"而非笼统状态）
    var curStepText = "";
    if (state.planStarted && state.planSteps.length) {
      for (var _si = 0; _si < state.planSteps.length; _si++) {
        if (state.planSteps[_si].status !== "done") {
          curStepText = state.planSteps[_si].text;
          break;
        }
      }
      if (!curStepText) curStepText = state.planSteps[state.planSteps.length - 1].text;  // 全完成 → 最后一步
    }
    var newText = curStepText || text || "处理中…";
    // 缩短显示（状态栏单行，过长截断加省略号）
    if (newText.length > 80) newText = newText.slice(0, 77) + "…";
    // 变化守卫：文本没变就跳过 DOM 写入（每 token 都调，避免无谓 reflow）
    if (newText !== _tsLastText) { t.textContent = newText; _tsLastText = newText; }
  }
  var s = _tsStepEl;
  if (s) {
    // 步骤显示与【右栏工作规划】同步：有规划才显示进度（当前第 X / 总 N 步），无规划不显示
    var stepTxt = "";
    if (state.planStarted && state.planSteps.length) {
      var cur = 0;
      for (var i = 0; i < state.planSteps.length; i++) {
        cur = i + 1;
        if (state.planSteps[i].status !== "done") break;
      }
      stepTxt = "步骤 " + cur + "/" + state.planSteps.length;
    }
    // 生成耗时（实时更新，对标 opencode：显示已用时）
    if (streamStartTs) {
      var _secs = (Date.now() - streamStartTs) / 1000;
      var _timeStr = _secs >= 60
        ? Math.floor(_secs / 60) + "分" + Math.round(_secs % 60) + "秒"
        : Math.round(_secs) + "秒";
      stepTxt = stepTxt ? stepTxt + " · 已用 " + _timeStr : "已用 " + _timeStr;
    }
    if (stepTxt !== _tsLastStep) { s.textContent = stepTxt; _tsLastStep = stepTxt; }
  }
}

function showTaskStatus(text, count) {
  var bar = _tsEls();
  if (!bar) return;
  // 状态隔离：后台对话（streamCtx.cid ≠ 当前对话 或 项目不同）的状态不显示到当前界面
  if (streamCtx && streamCtx.cid) {
    if (streamCtx.cid !== state.conversationId) return;
    if (streamCtx.project && streamCtx.project !== state.project) return;   // 项目隔离（防跨项目串状态）
  }
  bar.hidden = false;
  if (typeof count === "number") taskToolCount = count;
  refreshTaskStep(text);
}

function hideTaskStatus() {
  // 新对话/切项目/切对话：状态栏总是隐藏（当前对话的流收尾由 finishStream 控制显示）
  // 不再检查 streamCtx——切走后旧流的收尾不应阻止隐藏（防跨项目/跨对话残留）
  var bar = _tsEls();
  if (bar) bar.hidden = true;
}

/* ---------- 快捷指令面板：输入 / 弹出命令菜单（对标 opencode /commands） ---------- */
var COMMANDS = [
  { cmd: "/new", label: "新对话", hint: "清空当前对话，开始新会话" },
  { cmd: "/clear", label: "清空", hint: "清空当前对话" },
  { cmd: "/export", label: "导出", hint: "导出当前对话为 Markdown" },
  { cmd: "/load-local", label: "加载本地模型", hint: "打开设置 → 供应商 → 本地 GGUF" },
  { cmd: "/apps", label: "小应用", hint: "打开设置 → 通用 → 应用协作" },
  { cmd: "/theme", label: "切换主题", hint: "深色/浅色切换" },
  { cmd: "/help", label: "帮助", hint: "查看可用命令" }
];

/** 输入框以 / 开头（未输入空格）→ 显示命令菜单 */
function maybeShowCommandMenu() {
  var menu = document.getElementById("command-menu");
  var v = els.input ? els.input.value : "";
  if (!menu) return;
  if (v.charAt(0) === "/" && v.indexOf(" ") === -1 && !state.streaming) {
    menu.innerHTML = "";
    var q = v.slice(1).toLowerCase();
    COMMANDS.forEach(function (c) {
      if (q && c.cmd.slice(1).indexOf(q) === -1) return;
      var item = document.createElement("div");
      item.className = "command-menu-item";
      item.innerHTML = '<span class="cm-cmd">' + c.cmd + '</span><span class="cm-hint">' + c.hint + '</span>';
      item.addEventListener("click", function () { runCommand(c.cmd); });
      menu.appendChild(item);
    });
    menu.hidden = menu.children.length === 0;
    if (!menu.hidden) {
      // fixed 定位 + body 挂载（避免被消息区遮挡）
      if (menu.parentNode !== document.body) document.body.appendChild(menu);
      var r = els.input.getBoundingClientRect();
      menu.style.position = "fixed";
      menu.style.top = "auto";
      menu.style.bottom = (window.innerHeight - r.top + 8) + "px";
      menu.style.left = Math.max(8, r.left) + "px";
    }
  } else {
    menu.hidden = true;
  }
}

/** 执行命令（/theme /apps 特殊处理，其余走 sendMessage 命令分支） */
function runCommand(cmd) {
  var menu = document.getElementById("command-menu");
  if (menu) menu.hidden = true;
  if (cmd === "/theme") { toggleTheme(); return; }
  if (cmd === "/apps") {
    openSettings(true);
    setTimeout(function () {
      var t = document.querySelector('.modal-tab[data-tab="general"]');
      if (t) t.click();
    }, 300);
    return;
  }
  els.input.value = cmd;
  autoResizeInput();
  updateSendBtn();
  sendMessage();
}

/* ===========================================================================
 * 主题（深色默认，localStorage 持久化）
 * ========================================================================= */

function initTheme() {
  var stored = null;
  try { stored = localStorage.getItem("theme"); } catch (e) { /* 隐私模式可能抛错 */ }
  applyTheme(resolveTheme(stored), false);
  // 跟随系统：监听系统主题变化
  if ((stored === "system" || stored === null) && window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      var cur = null;
      try { cur = localStorage.getItem("theme"); } catch (e) { /* 忽略 */ }
      if (cur === "system" || cur === null) applyTheme(resolveTheme("system"), false);
    });
  }
}

/** 解析主题偏好：dark / light / 12 套主题 / system → 实际主题 */
function resolveTheme(stored) {
  var names = ["dark", "light", "midnight", "graphite", "paper", "moonlight",
               "forest", "ocean", "violet", "sunset", "sage", "rose"];
  if (names.indexOf(stored) >= 0) return stored;
  try {
    return (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) ? "light" : "dark";
  } catch (e) { return "dark"; }
}

function toggleTheme() {
  var cur = els.root.getAttribute("data-theme");
  var isDark = ["dark", "midnight", "graphite", "forest", "ocean", "violet"].indexOf(cur) >= 0;
  applyTheme(isDark ? "light" : "dark", true);
}

function applyTheme(theme, persist) {
  els.root.setAttribute("data-theme", theme);
  els.root.style.colorScheme = theme;               // 让原生滚动条/表单跟随
  els.themeToggle.classList.toggle("is-light", theme === "light");
  var label = theme === "light" ? "切换到深色主题" : "切换到浅色主题";
  els.themeToggle.setAttribute("aria-label", label);
  els.themeToggle.title = label;
  if (persist) {
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
}

/* ===========================================================================
 * 供应商下拉（自定义，无原生浏览器外观）
 * ========================================================================= */

var menuOpen = false;

function setMenuOpen(open) {
  menuOpen = open;
  els.menu.hidden = !open;
  els.trigger.parentNode.classList.toggle("open", open);
  els.trigger.setAttribute("aria-expanded", String(open));
}

function toggleMenu() { setMenuOpen(!menuOpen); }
function closeMenu() { if (menuOpen) setMenuOpen(false); }

/** 把供应商列表渲染进下拉菜单（textContent 赋值，天然安全） */
function renderProviderMenu() {
  els.menu.innerHTML = "";
  state.providers.forEach(function (p, i) {
    var li = document.createElement("li");
    li.className = "provider-option";
    li.setAttribute("role", "option");
    li.setAttribute("tabindex", "-1");
    li.dataset.index = String(i);

    // 头部：名称 + 右侧"已配置"小徽标
    var head = document.createElement("div");
    head.className = "option-head";

    var name = document.createElement("span");
    name.className = "option-name";
    name.textContent = p.name;

    var badge = document.createElement("span");
    badge.className = "option-badge";
    badge.hidden = !p.has_key;

    var dot = document.createElement("span");
    dot.className = "option-badge-dot";
    dot.setAttribute("aria-hidden", "true");
    var badgeText = document.createElement("span");
    badgeText.textContent = "已配置";

    badge.appendChild(dot);
    badge.appendChild(badgeText);
    head.appendChild(name);
    head.appendChild(badge);

    var model = document.createElement("span");
    model.className = "option-model";
    model.textContent = p.model;

    li.appendChild(head);
    li.appendChild(model);
    li.addEventListener("click", function () {
      selectProvider(p);
      closeMenu();
      els.trigger.focus();
    });
    els.menu.appendChild(li);
  });
  refreshMenuSelection();
}

/** 同步下拉选项里的"已配置"徽标（保存 Key / 弹窗刷新后调用） */
function refreshProviderBadges() {
  els.menu.querySelectorAll(".provider-option").forEach(function (o) {
    var badge = o.querySelector(".option-badge");
    var p = state.providers[+o.dataset.index];
    if (badge) badge.hidden = !(p && p.has_key);
  });
}

/** 高亮当前选中的选项 */
function refreshMenuSelection() {
  els.menu.querySelectorAll(".provider-option").forEach(function (o) {
    var p = state.providers[+o.dataset.index];
    o.setAttribute("aria-selected",
      String(Boolean(p && state.activeProvider && p.key === state.activeProvider.key)));
  });
}

/** 广播当前对话模型给小应用窗口（小应用 AI 分析跟随主界面所选模型） */
function broadcastModel() {
  try {
    var _p = state.activeProvider;
    var msg = { type: "wm-model", provider: _p ? _p.key : "", model: _p ? (_p.model || "") : "" };
    (window.__appWins || []).forEach(function (w) {
      var f = w.querySelector("iframe");
      if (f && f.contentWindow) f.contentWindow.postMessage(msg, "*");
    });
  } catch (e) { /* 忽略 */ }
}

/** 选择供应商：生成中不允许切换 */
function selectProvider(p) {
  if (state.streaming || !p) return;
  state.activeProvider = p;
  state.ctx = p.ctx || 32768;
  els.triggerName.textContent = p.name;
  els.triggerModel.textContent = p.model;
  refreshMenuSelection();
  updateTokenUsage();
  updateTitle();
  broadcastModel();
}

function focusOption(i) {
  var opts = els.menu.querySelectorAll(".provider-option");
  if (opts.length === 0) return;
  i = (i + opts.length) % opts.length;
  opts[i].focus();
  opts[i].scrollIntoView({ block: "nearest" });
}

function selectedIndex() {
  var opts = Array.prototype.slice.call(els.menu.querySelectorAll(".provider-option"));
  var i = opts.findIndex(function (o) { return o.getAttribute("aria-selected") === "true"; });
  return i >= 0 ? i : 0;
}

function onTriggerKeydown(e) {
  if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    if (!menuOpen) setMenuOpen(true);
    focusOption(selectedIndex());
  } else if (e.key === "Escape" && menuOpen) {
    e.preventDefault();
    closeMenu();
  }
}

function onMenuKeydown(e) {
  var opts = Array.prototype.slice.call(els.menu.querySelectorAll(".provider-option"));
  if (opts.length === 0) return;
  var idx = opts.indexOf(e.target);

  if (e.key === "ArrowDown") { e.preventDefault(); focusOption(idx + 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); focusOption(idx - 1); }
  else if (e.key === "Home") { e.preventDefault(); focusOption(0); }
  else if (e.key === "End") { e.preventDefault(); focusOption(opts.length - 1); }
  else if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    if (idx >= 0) {
      var p = state.providers[+opts[idx].dataset.index];
      if (p) {
        selectProvider(p);
        closeMenu();
        els.trigger.focus();
      }
    }
  } else if (e.key === "Escape") {
    e.preventDefault();
    closeMenu();
    els.trigger.focus();
  }
}

/* ===========================================================================
 * 加载供应商（GET /api/providers）
 * ========================================================================= */

function loadProviders() {  fetch("/api/providers")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      var list = Array.isArray(data.providers) ? data.providers : [];
      if (list.length === 0) throw new Error("没有可用供应商");
      state.providers = list;
      state.activeProvider = list[0];
      selectProvider(list[0]);
      renderProviderMenu();
      setStatus("idle");
      applyDefaultModel();
    })
    .catch(function () {
      els.triggerName.textContent = "未连接";
      els.triggerModel.textContent = "—";
      setStatus("error");
      showErrorNote("无法连接服务器：请确认已运行 python gui_server.py 后刷新页面");
    });
}

/** Refresh price metadata without resetting the user's active provider/model. */
window.addEventListener("wenmo:pricing-synced", function (event) {
  var key = event && event.detail ? event.detail.provider : "";
  if (!key) return;
  fetch("/api/providers").then(function (response) {
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  }).then(function (data) {
    var fresh = (data.providers || []).find(function (item) { return item.key === key; });
    if (!fresh) return;
    [state.providers, settingsProviders].forEach(function (collection) {
      (collection || []).forEach(function (item) {
        if (item.key !== key) return;
        item.price = fresh.price;
        item.price_est = fresh.price_est;
        item.billing_mode = fresh.billing_mode;
        item.price_source = fresh.price_source;
      });
    });
    if (state.activeProvider && state.activeProvider.key === key) updateUsageStats();
  }).catch(function () {});
});

/** 启动时应用"模型设置"里的默认文本模型（可自行更改） */
function applyDefaultModel() {
  fetch("/api/settings/context")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.default_provider) return;
      var p = (state.providers || []).find(function (x) { return x.key === data.default_provider; });
      if (!p) return;
      selectProvider(p);
      if (data.default_model) {
        state.activeProvider.model = data.default_model;
        els.triggerModel.textContent = data.default_model;
        updateTitle();
        broadcastModel();
      }
    })
    .catch(function () { /* 忽略 */ });
}

/* ===========================================================================
 * 消息渲染
 * ========================================================================= */

function renderUserMessage(text, idx, mountTo) {
  var wrap = document.createElement("div");
  wrap.className = "msg msg-user";
  if (typeof idx === "number") wrap.dataset.idx = idx;

  var bubble = document.createElement("div");
  bubble.className = "user-bubble";
  // 兼容 content 数组（多模态：文本 + 图片）
  if (typeof text === "string") {
    if (text.indexOf("图片附件：") >= 0) {
      // 图片附件标记 → 渲染缩略图（附件图片不再直接塞给模型，模型走 see_image 读图）
      var mdBox = document.createElement("div");
      mdBox.className = "md";
      mdBox.innerHTML = renderMarkdown(text.replace(/图片附件：([\w.\-]+)/g, '![$1](/files/$1)'));
      postProcess(mdBox);
      bubble.appendChild(mdBox);
    } else {
      // v4: 用户消息也走 renderMarkdown（先 escapeHTML 再 marked，XSS 安全），
      // 让用户自己发的 $$...$$ 公式也能渲染 + 复制为 Word 公式
      var mdBox2 = document.createElement("div");
      mdBox2.className = "md";
      mdBox2.innerHTML = renderMarkdown(text);
      postProcess(mdBox2);
      bubble.appendChild(mdBox2);
    }
  } else if (Array.isArray(text)) {
    text.forEach(function (part) {
      if (part.type === "text" && part.text) {
        var t = document.createElement("span");
        t.textContent = part.text;
        bubble.appendChild(t);
      } else if (part.type === "image_url" && part.image_url && part.image_url.url) {
        var img = document.createElement("img");
        img.className = "user-img";
        img.src = part.image_url.url;
        img.alt = "附件图片";
        bubble.appendChild(img);
      }
    });
  }
  wrap.appendChild(bubble);
  wrap.appendChild(buildMsgActions({
    role: "user",
    idx: idx,
    getText: function () { return typeof text === "string" ? text : ""; }
  }));

  if (mountTo) mountTo.appendChild(wrap); else els.messages.appendChild(wrap);
  scrollIfNearBottom();
}

/** 消息操作栏：复制 / 编辑（仅用户消息）/ 删除（对标 Codex） */
var MSG_ICON_COPY = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
var MSG_ICON_EDIT = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';
var MSG_ICON_DEL = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
var MSG_ICON_REGEN = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';

function buildMsgActions(opts) {
  var bar = document.createElement("div");
  bar.className = "msg-actions";

  if (opts.role === "assistant" && typeof opts.idx === "number") {
    var regen = document.createElement("button");
    regen.type = "button";
    regen.title = "重新生成";
    regen.setAttribute("aria-label", "重新生成回答");
    regen.innerHTML = MSG_ICON_REGEN;
    regen.addEventListener("click", function () { regenerateAt(opts.idx); });
    bar.appendChild(regen);
  }

  var copy = document.createElement("button");
  copy.type = "button";
  copy.title = "复制";
  copy.setAttribute("aria-label", "复制消息");
  copy.innerHTML = MSG_ICON_COPY;
  copy.addEventListener("click", function () { copyText(opts.getText(), copy); });
  bar.appendChild(copy);

  if (typeof opts.idx === "number") {
    var edit = document.createElement("button");
    edit.type = "button";
    edit.title = "编辑";
    edit.setAttribute("aria-label", "编辑消息");
    edit.innerHTML = MSG_ICON_EDIT;
    edit.addEventListener("click", function () { startEdit(opts.idx); });
    bar.appendChild(edit);
  }

  var del = document.createElement("button");
  del.type = "button";
  del.className = "danger";
  del.title = "删除";
  del.setAttribute("aria-label", "删除消息及之后");
  del.innerHTML = MSG_ICON_DEL;
  del.addEventListener("click", function () {
    if (state.streaming) return;
    if (window.confirm("删除这条消息及其后的所有消息？")) deleteMessageAt(opts.idx);
  });
  bar.appendChild(del);

  return bar;
}

/** 只清掉消息元素和错误提示（保留空状态节点，避免引用失效） */
function clearMessagesDom() {
  if (window.WenmoVirtualHistory) window.WenmoVirtualHistory.reset();
  els.messages.querySelectorAll(".msg").forEach(function (n) { n.remove(); });
  els.messages.querySelectorAll(".error-note").forEach(function (n) { n.remove(); });
  els.messages.querySelectorAll(".history-loading").forEach(function (n) { n.remove(); });
}

/** Mutation APIs must succeed at both HTTP and application levels. */
function expectOkResponse(res) {
  return res.json().catch(function () { return {}; }).then(function (data) {
    if (!res.ok || !data || data.ok !== true) {
      var detail = data && (data.detail || data.message);
      throw new Error(detail || ("HTTP " + res.status));
    }
    return data;
  });
}

// Any full render invalidates older incremental render frames.
var _messageRenderSeq = 0;

/** 按 state.messages 全量重渲染（编辑/删除/加载历史后调用）
 * renderToken 可选：切换会话的竞态守卫（渲染帧里检查 loadSeq，防旧对话帧覆盖新对话） */
function renderAllMessages(renderToken, forceBottom) {
  var renderSeq = ++_messageRenderSeq;
  clearMessagesDom();
  if (state.messages.length === 0) {
    els.empty.hidden = false;
    els.messages.classList.add("empty");
    return;
  }
  els.empty.hidden = true;
  els.messages.classList.remove("empty");
  var msgs = state.messages;
  // 强制滚底（切换/编辑场景）：渲染完成后无视 nearBottom 直接滚到最新处
  if (forceBottom) state.nearBottom = true;
  function renderInto(frag, start, end) {
    for (var i = start; i < end; i++) {
      try {
        var m = msgs[i];
        if (m.role === "user" && (typeof m.content === "string" || Array.isArray(m.content))) {
          renderUserMessage(m.content, i, frag);
        } else if (m.role === "assistant" && typeof m.content === "string" && m.content) {
          renderSavedAssistantMessage(m.content, i, m, frag);
        }
      } catch (e) {
        // 单条消息渲染异常：跳过，不中断整批（防止"对话隐藏需刷新"）
        console.error("消息渲染失败 idx=" + i, e);
      }
    }
  }
  if (msgs.length <= 80) {
    var frag = document.createDocumentFragment();
    renderInto(frag, 0, msgs.length);
    els.messages.appendChild(frag);
    if (forceBottom) { state.nearBottom = true; scrollToBottom(false); }
    else scrollIfNearBottom();
    buildMsgAnchors();
    return;
  }
  // 长对话只保留一个有界 DOM 窗口；初始窗口位于最新消息，向上滚动再换入旧窗口。
  if (window.WenmoVirtualHistory) {
    window.WenmoVirtualHistory.render({
      container: els.messages,
      total: msgs.length,
      forceBottom: !!forceBottom,
      renderRange: function (fragment, start, end) {
        if (_messageRenderSeq !== renderSeq) return;
        if (renderToken != null && state.loadSeq !== renderToken) return;
        renderInto(fragment, start, end);
      }
    });
    buildMsgAnchors();
    return;
  }
  var fallback = document.createDocumentFragment();
  renderInto(fallback, Math.max(0, msgs.length - 180), msgs.length);
  els.messages.appendChild(fallback);
  if (forceBottom) scrollToBottom(false);
}

/* ---------- 历史回合锚点索引条（对标 Codex：左侧竖排短线，点击定位到对应历史消息） ---------- */
var _anchorScrollTimer = null;
var _anchorBuildTimer = null;

/** 重建锚点索引（分批渲染期间自动节流：300ms 内合并多次调用） */
function buildMsgAnchors() {
  if (_anchorBuildTimer) return;   // 已排队（合并分批渲染的多次调用）
  var cid = state.conversationId || "new";
  _anchorBuildTimer = setTimeout(function () {
    _anchorBuildTimer = null;
    // 防串台：延迟执行时若已切到别的对话，丢弃本次构建
    if ((state.conversationId || "new") !== cid) return;
    _buildMsgAnchorsInner();
  }, 300);
}

function _buildMsgAnchorsInner() {
  var nav = document.getElementById("msg-anchors");
  if (!nav) return;
  nav.innerHTML = "";
  // 定位在侧边栏右侧（收起时仅 rail 44px）→ 动态计算左侧偏移
  var sidebarMain = document.getElementById("sidebar-main");
  var sbW = 44;   // 收起时 = rail 宽
  if (sidebarMain && sidebarMain.offsetWidth > 0) sbW = 44 + sidebarMain.offsetWidth;
  nav.style.left = (sbW + 10) + "px";
  var turns = [];
  state.messages.forEach(function (m, i) {
    // 用户消息 = 一个回合的起点（含图片附件的多模态消息也算）
    if (m.role === "user" && (typeof m.content === "string" || Array.isArray(m.content))) {
      var label = "";
      if (typeof m.content === "string") {
        label = m.content.replace(/图片附件：[\w.\-]+/g, "[图]").replace(/^[\s#>*\-]+/, "").trim();
      } else if (Array.isArray(m.content)) {
        label = (m.content.find(function (p) { return p.type === "text"; }) || {}).text || "[多模态]";
      }
      turns.push({ idx: i, label: label || "（空消息）" });
    }
  });
  if (turns.length < 2) {   // 少于 2 个回合 → 索引无意义，隐藏
    nav.hidden = true;
    return;
  }
  nav.hidden = false;
  turns.forEach(function (t, ti) {
    var a = document.createElement("button");
    a.type = "button";
    a.className = "msg-anchor";
    a.title = "定位到： " + t.label.slice(0, 60);
    a.setAttribute("aria-label", "定位到第 " + (ti + 1) + " 个问题");
    a.dataset.idx = t.idx;
    // 竖线 + 序号（hover 显示该回合首句）
    var mark = document.createElement("i");
    mark.className = "ma-bar";
    var num = document.createElement("span");
    num.className = "ma-num";
    num.textContent = ti + 1;
    var tip = document.createElement("span");
    tip.className = "ma-tip";
    tip.textContent = t.label.slice(0, 40);
    a.appendChild(mark);
    a.appendChild(num);
    a.appendChild(tip);
    a.addEventListener("click", function () {
      scrollToMessage(t.idx, a);
    });
    nav.appendChild(a);
  });
  updateAnchorActive();
}

/** 平滑滚动到指定消息，并高亮对应锚点 */
function scrollToMessage(idx, anchorEl) {
  var el = els.messages.querySelector('.msg[data-idx="' + idx + '"]');
  if (!el && window.WenmoVirtualHistory) {
    window.WenmoVirtualHistory.ensureIndex(idx);
    el = els.messages.querySelector('.msg[data-idx="' + idx + '"]');
  }
  if (!el) return;
  // 防止"回到最新"干扰定位：先临时标记不在底部（scrollIfNearBottom 不再抢滚）
  state.nearBottom = false;
  els.backPill.hidden = false;
  // scrollIntoView 基于元素实时位置滚动（分批渲染未完成时也正确），scroll-margin-top 留出边距
  try {
    el.scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (e) {
    els.messages.scrollTo({ top: el.offsetTop - 60, behavior: "smooth" });
  }
  // 高亮目标消息（短暂闪烁）
  el.classList.remove("msg-flash");
  void el.offsetWidth;   // 强制重排以重启动画
  el.classList.add("msg-flash");
  // 更新锚点激活态
  if (anchorEl) {
    els.messages.querySelectorAll(".msg-anchor.active").forEach(function (n) { n.classList.remove("active"); });
    anchorEl.classList.add("active");
  }
  // 滚动结束后再校正一次激活态（smooth 滚动过程中调用 updateAnchorActive 会抢先）
  if (_anchorScrollTimer) clearTimeout(_anchorScrollTimer);
  _anchorScrollTimer = setTimeout(updateAnchorActive, 600);
}

/** 滚动时高亮当前可见回合对应的锚点（节流） */
function updateAnchorActive() {
  var nav = document.getElementById("msg-anchors");
  if (!nav || nav.hidden) return;
  var container = els.messages;
  var viewCenter = container.scrollTop + container.clientHeight / 2;
  var activeEl = null;
  var bestDist = Infinity;
  // 找"顶部最接近视口中心"的用户消息（比 0.4 阈值更准：长消息/图片不误判）
  container.querySelectorAll(".msg-user").forEach(function (m) {
    var dist = Math.abs(m.offsetTop - viewCenter);
    if (dist < bestDist) {
      bestDist = dist;
      var idx = m.dataset.idx;
      if (idx) activeEl = nav.querySelector('.msg-anchor[data-idx="' + idx + '"]');
    }
  });
  if (!activeEl) activeEl = nav.querySelector(".msg-anchor");
  nav.querySelectorAll(".msg-anchor.active").forEach(function (n) { n.classList.remove("active"); });
  if (activeEl) activeEl.classList.add("active");
}

/** 编辑消息：用户消息 = 改文本后截断重发；AI 回复 = 修改文本并截断其后消息（保存历史） */
function startEdit(idx) {
  var msg = state.messages[idx];
  if (!msg || typeof msg.content !== "string" || state.streaming) return;
  var wrap = els.messages.querySelector('.msg[data-idx="' + idx + '"]');
  if (!wrap) return;
  var bubble = wrap.querySelector(".user-bubble") || wrap.querySelector(".md");
  if (!bubble) return;
  var isAssistant = msg.role === "assistant";
  bubble.innerHTML = "";
  wrap.classList.add("editing");   // 编辑态加宽编辑框
  els.input.value = "";

  var box = document.createElement("div");
  box.className = "edit-box";
  var ta = document.createElement("textarea");
  ta.className = "edit-area";
  ta.value = msg.content;
  var actions = document.createElement("div");
  actions.className = "edit-actions";
  var save = document.createElement("button");
  save.type = "button";
  save.className = "edit-save";
  save.textContent = isAssistant ? "保存修改" : "保存并发送";
  var cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "edit-cancel";
  cancel.textContent = "取消";
  save.addEventListener("click", async function () {
    var newText = ta.value.trim();
    if (!newText) return;
    if (isAssistant) {
      // AI 回复：修改文本 + 删除其后消息（后续对话基于旧回答会矛盾）
      state.messages[idx] = { role: "assistant", content: newText };
      state.messages = state.messages.slice(0, idx + 1);
      renderAllMessages();
      saveHistory();
    } else {
      state.messages = state.messages.slice(0, idx + 1);      // 截断到编辑点
      state.messages[idx] = { role: "user", content: newText };
      renderAllMessages(null, true);                    // 编辑发送：强制滚到底部最新处
      scrollToBottom(false);
      if (!els.empty.hidden) hideEmptyState();
      try {
        await persistHistorySnapshot();
      } catch (error) {
        showErrorNote("编辑后的历史保存失败，尚未重新发送");
        return;
      }
      var editOpts = { requestId: newRequestId() };
      beginStream(editOpts);
      streamChat(editOpts);                                  // 从编辑点继续对话
    }
  });
  cancel.addEventListener("click", function () { renderAllMessages(); });
  actions.appendChild(save);
  actions.appendChild(cancel);
  box.appendChild(ta);
  box.appendChild(actions);
  bubble.appendChild(box);
  ta.focus();
}

/** 删除消息及其后的所有消息 */
function deleteMessageAt(idx) {
  if (typeof idx !== "number") return;
  state.messages = state.messages.slice(0, idx);
  renderAllMessages();
  saveHistory();
}

/** 重新生成：删除该 AI 回复及其后内容，让模型重新回答上一条用户消息（对标 Codex） */
async function regenerateAt(idx) {
  if (state.streaming) return;
  var keep = -1;
  for (var i = idx - 1; i >= 0; i--) {
    if (state.messages[i] && state.messages[i].role === "user") { keep = i + 1; break; }
  }
  if (keep <= 0) return;
  state.messages = state.messages.slice(0, keep);
  renderAllMessages();
  if (!els.empty.hidden) hideEmptyState();
  try {
    await persistHistorySnapshot();
  } catch (error) {
    showErrorNote("历史保存失败，尚未重新生成");
    return;
  }
  var regenerateOpts = { requestId: newRequestId() };
  beginStream(regenerateOpts);
  streamChat(regenerateOpts);
}

/** 导出当前对话为 Markdown 文件 */
function exportConversation() {
  if (!state.conversationId) {
    showErrorNote("当前对话尚未保存，先聊几句再导出");
    return;
  }
  // 顶栏导出按钮 → 弹出格式选择（md / html / json）
  showExportMenu(document.getElementById("export-btn"), state.conversationId);
}

/** 动态窗口标题：项目 · 模型 */
function updateTitle() {
  var p = (state.projects || []).find(function (x) { return x.id === state.project; });
  var pname = p ? p.name : "默认项目";
  var model = state.activeProvider ? state.activeProvider.model : "";
  document.title = pname + (model ? " · " + model : "") + " — 问墨·code";
}

/** 开始生成：创建 AI 消息元素 + 光标（步骤流，先不建内容块） */
/** 开始生成：创建 AI 消息元素 + 光标（步骤流，先不建内容块）；opts.mountTo 可指定挂载容器 */
function beginStream(opts) {
  opts = opts || {};
  acc = "";
  thinkAcc = "";
  toolLog = [];                 // 本轮工具调用记录（历史完整保存）
  streamStartTs = Date.now();   // 生成速度统计：开始时间
  streamOutTokens = 0;          // 本轮正式输出 token 数
  stepEl = null;
  stepPhase = "none";
  stepThinkText = "";
  stepThink = null;
  stepContent = null;
  stepContentText = "";
  toolLineEls = {};
  // 重置流式节流状态（新一轮生成从头计时）
  _parseThrottleTs = 0; _parseThrottleLen = 0;
  _scrollThrottleTs = 0; _thinkThrottleTs = 0;
  aiMsgEl = document.createElement("div");
  aiMsgEl.className = "msg msg-ai";
  aiContentEl = null;

  var label = document.createElement("div");
  label.className = "msg-model";
  label.textContent = streamLabelOverride || (state.activeProvider ? state.activeProvider.model : "");
  aiMsgEl.appendChild(label);
  var mount = opts.mountTo || els.messages;
  mount.appendChild(aiMsgEl);

  // “正在思考”三点指示器已移除（用户要求）：状态由 task-status 栏展示（思考中/正在调用工具/已完成）
  thinkingEl = null;

  // 后台生成上下文：记录对话 id + 消息快照（切换对话后流继续跑，收尾存回原对话历史）
  streamCtx = {
    cid: state.conversationId || "",
    requestId: opts.requestId || "",
    messages: state.messages.slice(),
    finished: false,
    provider: state.activeProvider ? state.activeProvider.key : "",
    model: state.activeProvider ? state.activeProvider.model : "",
    project: state.project || "default"   // 记录流发起时的项目（收尾保存用，防串门）
  };
  // 多流管理：每个活跃流按 cid 保存（对话 1 后台跑时对话 2 发送不会覆盖对话 1 的流上下文）
  streamCtxs[streamCtx.cid] = streamCtx;
  startStreamingIndicator();
  taskToolCount = 0;
  showTaskStatus("准备中…", 0);

  state.streaming = true;
  setCaret(true);
  setStatus("streaming");
  setSendBtn("stop");
  // 冷启动等待提示：3 秒没等到第一个 delta 才出现
  waitTimer = setTimeout(showWaitHint, 3000);
}

/* ---------- 生成中动态指示（对标 opencode 会话动画） ---------- */
var streamingTimer = null;

/** 刷新状态栏"已用时间"（不重绘整个状态栏，只更新时间——工具加载期间时间不停） */
function updateTaskStatusTime() {
  var bar = document.getElementById("task-status");
  var step = document.getElementById("ts-step");
  if (!bar || !step) return;
  if (bar.hidden) return;
  // 状态隔离：只更新当前对话的状态栏
  if (streamCtx && streamCtx.cid) {
    if (streamCtx.cid !== state.conversationId) return;
    if (streamCtx.project && streamCtx.project !== state.project) return;
  }
  if (!streamStartTs) return;
  var _secs = (Date.now() - streamStartTs) / 1000;
  var _timeStr = _secs >= 60
    ? Math.floor(_secs / 60) + "分" + Math.round(_secs % 60) + "秒"
    : Math.round(_secs) + "秒";
  // 清掉所有旧的"已用 X"（含空格变体，防累积），再追加新的——绝不重复拼接
  var txt = (step.textContent || "").replace(/\s*·?\s*已用\s*[\d分秒]+/g, "").replace(/\s+$/, "");
  step.textContent = (txt ? txt + " · " : "") + "已用 " + _timeStr;
}

/** 开始轮询：给历史列表中正在生成的对话项加脉冲动画（多流：所有活跃对话都持续显示） */
function startStreamingIndicator() {
  stopStreamingIndicator();
  streamingTimer = setInterval(function () {
    var list = els.historyList;
    if (!list) return;
    // 先清掉旧的，再按当前活跃流重新加（多流互不覆盖）
    list.querySelectorAll(".history-item.streaming").forEach(function (n) {
      n.classList.remove("streaming");
    });
    Object.keys(streamCtxs).forEach(function (cid) {
      var ctx = streamCtxs[cid];
      if (!cid || ctx.finished) return;
      var item = list.querySelector('.history-item[data-cid="' + cid + '"]');
      if (item) item.classList.add("streaming");
      else if (cid === state.conversationId) {
        var act = list.querySelector(".history-item.active");
        if (act) act.classList.add("streaming");
      }
    });
    // 工具加载期间时间不停：同步刷新状态栏耗时
    updateTaskStatusTime();
  }, 400);
}

/** 停止轮询并清除动画（收尾时调用；cid 指定则只清该对话，缺省清所有） */
function stopStreamingIndicator(cid) {
  if (streamingTimer) { clearInterval(streamingTimer); streamingTimer = null; }
  if (els.historyList) {
    if (cid) {
      var it = els.historyList.querySelector('.history-item[data-cid="' + cid + '"]');
      if (it) it.classList.remove("streaming");
    } else {
      els.historyList.querySelectorAll(".history-item.streaming").forEach(function (n) {
        n.classList.remove("streaming");
      });
    }
  }
}

/** 切回仍在后台生成的对话：把流接回 UI，继续实时显示（对标 opencode 切回会话） */
function restoreStreamUI() {
  // 幂等守卫：当前对话的流式容器已挂载 → 直接复用，不重复创建（loadConversation 缓存+fetch 分支会各调一次）
  if (aiMsgEl && aiMsgEl.isConnected && aiMsgEl.dataset && aiMsgEl.dataset.cid === state.conversationId) {
    return;
  }
  // 多流：优先取【当前对话】自己的流（对话 1 后台跑时切回，从 streamCtxs 找回它）
  var ctx = (state.conversationId && streamCtxs[state.conversationId]) || streamCtx;
  if (!ctx || ctx.finished) return;
  if (ctx.cid !== state.conversationId) return;
  streamCtx = ctx;   // 接回当前引用
  // 重建消息容器（之前的 DOM 已被 renderAllMessages 清掉）
  aiMsgEl = document.createElement("div");
  aiMsgEl.className = "msg msg-ai";
  aiMsgEl.dataset.cid = ctx.cid || "";   // 标记归属对话（幂等守卫用）
  var label = document.createElement("div");
  label.className = "msg-model";
  label.textContent = ctx.model || "";
  aiMsgEl.appendChild(label);
  // 加固：切回仍在后台生成的对话 → 加「后台生成中」标记，明确告知用户内容还在继续产出
  if (ctx && !ctx.finished) {
    var _bg = document.createElement("span");
    _bg.className = "bg-streaming-tag";
    _bg.textContent = "⏳ 后台生成中";
    _bg.style.cssText = "font-size:11px;color:var(--accent,#4c9aff);margin-left:8px;padding:2px 8px;border:1px solid var(--accent,#4c9aff);border-radius:10px;opacity:.85;";
    label.appendChild(_bg);
  }
  // 加固：后台流还在思考（无正文但有 thinkAcc）→ 显示"正在思考…"占位，避免看起来空白
  if (ctx && ctx.flow && (!ctx.flow.acc || !ctx.flow.acc.trim()) && ctx.flow.thinkAcc && ctx.flow.thinkAcc.trim()) {
    var _th = document.createElement("div");
    _th.className = "bg-thinking-hint";
    _th.textContent = "💭 正在思考中…";
    _th.style.cssText = "font-size:12px;color:var(--text-secondary,#9a9a9a);padding:4px 0;";
    aiMsgEl.appendChild(_th);
  }
  els.messages.appendChild(aiMsgEl);
  stepEl = document.createElement("div");
  stepEl.className = "step";
  stepContent = document.createElement("div");
  stepContent.className = "md";
  stepContentText = (ctx.flow && ctx.flow.acc) ? ctx.flow.acc : (acc || "");   // 用本流的独立缓冲
  stepPhase = "content";
  stepEl.appendChild(stepContent);
  aiMsgEl.appendChild(stepEl);
  renderCurrentContent();
  state.nearBottom = true;
  scrollToBottom(false);
  state.streaming = true;
  setStatus("streaming");
  setSendBtn("stop");
  setCaret(true);
  clearWaitHint();
}

/** 光标 ▍ 的显隐 */
function setCaret(show) {
  var caret = aiMsgEl ? aiMsgEl.querySelector(".caret") : null;
  if (show && !caret) {
    var c = document.createElement("span");
    c.className = "caret";
    c.setAttribute("aria-hidden", "true");
    c.textContent = "\u258D";                       // ▍ 左八分之三块，非 emoji
    aiMsgEl.appendChild(c);
  } else if (!show && caret) {
    caret.remove();
  }
}

/** 流式渲染节流：合并到同一帧，避免长回复时每字全量重渲染（含 KaTeX）卡顿 */
var streamRenderQueued = false;
var _streamRenderTimer = null;
function scheduleStreamRender() {
  if (streamRenderQueued) return;
  streamRenderQueued = true;
  // 主通道：rAF（页面可见时最流畅省电）
  if (!document.hidden) {
    requestAnimationFrame(function () {
      streamRenderQueued = false;
      if (!state.streaming) return;   // 已收尾，由 finishStream 做最终渲染
      renderCurrentContent();
    });
  }
  // 兑底通道：250ms 后若 rAF 未执行（切屏/窗口失焦被暂停）→ 强制渲染
  if (_streamRenderTimer) clearTimeout(_streamRenderTimer);
  _streamRenderTimer = setTimeout(function () {
    _streamRenderTimer = null;
    if (!streamRenderQueued) return;  // rAF 已渲染过
    streamRenderQueued = false;
    if (!state.streaming) return;
    renderCurrentContent();
  }, 250);
}

/* 切屏回来：立即补渲染当前流式内容并滚动到底（切走期间 rAF 被暂停，内容可能滞后） */
document.addEventListener("visibilitychange", function () {
  if (!document.hidden && state.streaming) {
    renderCurrentContent();
    scrollIfNearBottom();
  }
});

/** 收尾：只有服务端已正常提交的完整 assistant 才进入前端历史。 */
function finishStream(kind, errMsg, cid) {
  var ctx = (cid && streamCtxs[cid]) || streamCtx || null;
  if (!ctx || ctx.finished) return;
  ctx.finished = true;
  var detached = ctx.cid && ctx.cid !== state.conversationId;
  stopStreamingIndicator(ctx.cid);
  if (!detached) {
    hideTaskStatus();
    state.streaming = false;
    state.controller = null;
    state.sendingLock = false;
    setCaret(false);
    clearWaitHint();
    setSendBtn("send");
  }
  if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }

  if (kind === "ok" && !detached) {
    var fs = ctx.flow || { acc: acc, thinkAcc: thinkAcc, toolLog: toolLog, timeline: [] };
    if (fs.acc && fs.acc.trim()) {
      var am = {
        id: "m-" + ((window.crypto && crypto.randomUUID) ? crypto.randomUUID() : Date.now().toString(36)),
        role: "assistant",
        content: fs.acc,
        ts: Date.now()
      };
      if (streamStartTs) am.duration = Math.max(1, Math.round((Date.now() - streamStartTs) / 1000));
      if (fs.thinkAcc && fs.thinkAcc.trim()) am.think = fs.thinkAcc;
      if (fs.toolLog && fs.toolLog.length) am.tools = fs.toolLog.slice();
      if (fs.timeline && fs.timeline.length) am.timeline = fs.timeline.slice();
      state.messages.push(am);
      renderCurrentContent();
      if (aiMsgEl) aiMsgEl.dataset.idx = state.messages.length - 1;
      finalizePlan();
      finalizeStepThink("思考完成");
      showDoneToast("AI 回答完成，可以继续输入");
    } else if (aiMsgEl && aiMsgEl.isConnected) {
      aiMsgEl.remove();
      showErrorNote(thinkAcc.trim() ? "模型仅返回思考过程，请重试" : "模型未返回内容，请重试");
    }
    setStatus("idle");
  } else {
    // stop/error 都丢弃未完成的视觉碎片；用户消息已由 append API 独立持久化。
    if (!detached && aiMsgEl && aiMsgEl.isConnected) aiMsgEl.remove();
    if (!detached && kind === "error") {
      finalizePlan();
      showErrorNote(errMsg || "发生未知错误");
      setStatus("error");
    } else if (!detached) {
      setStatus("idle");
    }
  }

  renderHistoryList();
  if (ctx.cid && streamCtxs[ctx.cid] === ctx) delete streamCtxs[ctx.cid];
  if (streamCtx === ctx) streamCtx = null;
  if (kind === "ok" && !detached) setTimeout(dequeueNext, 300);
}

/** 给指定对话的历史项闪亮点（后台完成的对话也提醒） */
function flashHistoryDotFor(cid) {
  if (!cid) return;
  // 仅当用户不在该对话时才标未读（用户在对话里 → 无需提示）
  if (cid === state.conversationId) return;
  // 记录到未读集合 → buildHistoryItem 渲染时显示更亮点（异步列表重建也不丢）
  state.unreadDots[cid] = true;
  // 列表已存在 → 立即标记
  var list = els.historyList;
  if (list) {
    var item = list.querySelector('.history-item[data-cid="' + cid + '"]');
    if (item && !item.querySelector(".history-new-dot")) {
      var dot = document.createElement("span");
      dot.className = "history-new-dot";
      dot.setAttribute("aria-hidden", "true");
      dot.title = "该对话有新回复，点击查看";
      item.appendChild(dot);
    }
  }
}

/** 输出完成后：对话历史当前项闪一个醒目小亮点（几秒后消失） */
function flashHistoryDot() {
  var item = null;
  var list = els.historyList;
  if (!list) return;
  if (state.conversationId) {
    item = list.querySelector('.history-item[data-cid="' + state.conversationId + '"]');
  }
  if (!item) {
    // 还没保存完成/找不到 → 新对话默认高亮第一项
    item = list.querySelector(".history-item.active") || list.querySelector(".history-item");
  }
  if (!item) return;
  var dot = document.createElement("span");
  dot.className = "history-new-dot";
  dot.setAttribute("aria-hidden", "true");
  item.appendChild(dot);
  setTimeout(function () { if (dot.isConnected) dot.remove(); }, 4000);
}

/** 在消息区底部追加一条错误提示 */
function showErrorNote(msg) {
  var note = document.createElement("div");
  note.className = "error-note";
  note.setAttribute("role", "alert");

  var dot = document.createElement("span");
  dot.className = "error-dot";
  var text = document.createElement("span");
  text.textContent = msg;                            // 纯文本，防注入

  note.appendChild(dot);
  note.appendChild(text);
  els.messages.appendChild(note);
  scrollIfNearBottom();
}

/** 中性信息提示（非错误：绿色，不打扰——用于模式切换等状态说明） */
function showInfoNote(msg) {
  var note = document.createElement("div");
  note.className = "error-note info";
  var dot = document.createElement("span");
  dot.className = "error-dot";
  var text = document.createElement("span");
  text.textContent = msg;
  note.appendChild(dot);
  note.appendChild(text);
  els.messages.appendChild(note);
  scrollIfNearBottom();
}

/** 回答完成弹窗（对标 opencode "Agent ready for input"）：顶部居中，3 秒自动消失 */
function showDoneToast(msg) {
  var old = document.getElementById("done-toast");
  if (old) old.remove();
  var t = document.createElement("div");
  t.id = "done-toast";
  t.className = "done-toast";
  var icon = document.createElement("span");
  icon.className = "dt-icon";
  icon.textContent = "✅";
  var text = document.createElement("span");
  text.textContent = msg;
  t.appendChild(icon);
  t.appendChild(text);
  document.body.appendChild(t);
  // 自动消失
  setTimeout(function () {
    if (t.isConnected) {
      t.classList.add("fade");
      setTimeout(function () { if (t.isConnected) t.remove(); }, 300);
    }
  }, 3000);
}

/* ===========================================================================
 * 冷启动等待提示（流开始 3 秒仍无内容时出现，首个 delta / 收尾时移除）
 * ========================================================================= */

var waitTimer = null;      // 3 秒定时器
var waitHintEl = null;     // 提示元素

function showWaitHint() {
  if (!aiMsgEl || waitHintEl) return;
  waitHintEl = document.createElement("div");
  waitHintEl.className = "minor-note";
  waitHintEl.textContent = "等待模型响应…（本地模型首次加载可能较慢）";
  aiMsgEl.appendChild(waitHintEl);
  scrollIfNearBottom();
}

function clearWaitHint() {
  if (waitTimer) { clearTimeout(waitTimer); waitTimer = null; }
  if (waitHintEl) { waitHintEl.remove(); waitHintEl = null; }
}

/* ===========================================================================
 * 步骤流（对标 opencode）：思考 → 工具 → 输出 按步骤分组，不扎堆
 * ========================================================================= */

/** 确保当前步骤容器存在 */
function ensureStep() {
  if (stepEl || !aiMsgEl) return;
  stepEl = document.createElement("div");
  stepEl.className = "step";
  aiMsgEl.appendChild(stepEl);
}

/** 重置为新步骤（输出块/内容文本一并清空，防止串步骤） */
function resetStep() {
  stepEl = null;
  stepPhase = "none";
  stepContent = null;
  stepContentText = "";
}

/** 开始新的思考块（若上一步已有输出 → 开新步骤） */
function startThinkBlock() {
  if (stepPhase === "content") {
    resetStep();                 // 新一轮：思考接在上一轮输出之后 → 新步骤
  }
  ensureStep();
  if (stepPhase === "think") return;   // 已在思考中
  stepPhase = "think";
  stepThinkText = "";

  var thinkDefaultOpen = getPref("think_default", "fold") === "unfold";
  var header = document.createElement("button");
  header.type = "button";
  header.className = "think-toggle";
  header.setAttribute("aria-expanded", String(thinkDefaultOpen));
  header.innerHTML = '<span class="think-arrow">' + (thinkDefaultOpen ? "▾" : "▸") + '</span><span class="think-label">思考过程</span><span class="think-status">正在思考…</span>';
  var body = document.createElement("div");
  body.className = "think-body";
  body.hidden = !thinkDefaultOpen;
  var text = document.createElement("div");
  text.className = "thinking";
  body.appendChild(text);

  stepThink = { header: header, body: body, text: text, status: header.querySelector(".think-status"), expanded: thinkDefaultOpen };
  header.classList.add("thinking");   // 思考中：箭头脉冲动画（收尾时移除）
  header.addEventListener("click", function () { toggleStepThink(); });
  stepEl.appendChild(header);
  stepEl.appendChild(body);
}

/** 展开/收起当前步骤的思考 */
function toggleStepThink() {
  if (!stepThink) return;
  stepThink.expanded = !stepThink.expanded;
  stepThink.body.hidden = !stepThink.expanded;
  stepThink.header.setAttribute("aria-expanded", String(stepThink.expanded));
  stepThink.header.querySelector(".think-arrow").textContent = stepThink.expanded ? "▾" : "▸";
  scrollIfNearBottom();
}

/** 收尾当前步骤的思考（状态归位 + 收起） */
function finalizeStepThink(text) {
  if (!stepThink) return;
  stepThink.status.textContent = text || "思考完成";
  stepThink.header.classList.remove("thinking");   // 停止脉冲动画
  if (stepThink.expanded) {
    stepThink.expanded = false;
    stepThink.body.hidden = true;
    stepThink.header.setAttribute("aria-expanded", "false");
    stepThink.header.querySelector(".think-arrow").textContent = "▸";
  }
}

/** 开始工具调用行（思考后 / 输出后都开新步骤） */
/** 从工具参数 JSON 里提取命令（run_command 的 command 字段） */
function extractShellCommand(args) {
  if (!args) return "";
  var s = String(args);
  try {
    if (s.indexOf("{") === 0) {
      var obj = JSON.parse(s);
      if (obj && obj.command) return String(obj.command);
    }
  } catch (e) { /* 忽略 */ }
  // 兜底：从 "command": "..." 提取
  var m = /"command"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(s);
  if (m) return m[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  return s.slice(0, 120);
}

/** 从工具 args JSON 中提取 task 字段（智能体任务描述），失败返回 "" */
function extractAgentTask(args) {
  var s = String(args == null ? "" : args);
  var m = /"task"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(s);
  if (m) return m[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  // 兜底：直接截断原始 args
  return s.slice(0, 80);
}

/** 智能体活动独立面板（右侧）：记录本会话所有 agent 调用，与对话流卡片联动 */
var agentActivityEl = null;          // 面板列表容器（惰性缓存）
var agentActivityItems = {};         // 工具名 → 面板行元素（同名工具覆盖更新）

function ensureAgentPanel() {
  var list = document.getElementById("agent-panel-list");
  if (list) agentActivityEl = list;
  return list;
}

function logAgentActivity(entry) {
  var list = ensureAgentPanel();
  if (!list) return;                 // 面板未在页面中 → 跳过（不影响对话流）
  // 智能体开始工作 → 自动打开面板（用户手动关闭后本会话不再自动弹开，直到清空/新会话）
  if (!state.agentPanelDismissed) {
    var p = document.getElementById("agent-panel");
    if (p && p.hidden) p.hidden = false;
  }
  // 空态占位 → 移除
  var empty = list.querySelector(".agent-panel-empty");
  if (empty) empty.remove();
  var row = document.createElement("div");
  row.className = "agent-activity-item running";
  var icon = document.createElement("span");
  icon.className = "agent-activity-dot";   // 3×3 点阵（CSS 绘制）
  var name = document.createElement("span");
  name.className = "agent-activity-name";
  name.textContent = entry.name || "agent";
  var task = document.createElement("span");
  task.className = "agent-activity-task";
  task.textContent = entry.task || "";
  var status = document.createElement("span");
  status.className = "agent-activity-status running";
  status.textContent = "执行中…";
  row.appendChild(icon);
  row.appendChild(name);
  row.appendChild(task);
  row.appendChild(status);
  row.setAttribute("data-tool", entry.name || "");
  list.appendChild(row);
  agentActivityItems[entry.name] = { row: row, status: status };
  // 面板计数
  var c = document.getElementById("agent-panel-count");
  if (c) c.textContent = String(Object.keys(agentActivityItems).length);
}

function updateAgentActivity(name, ok) {
  var it = agentActivityItems[name];
  if (!it) return;
  it.row.className = "agent-activity-item " + (ok ? "done" : "fail");
  it.status.className = "agent-activity-status " + (ok ? "done" : "fail");
  it.status.textContent = ok ? "✔ 完成" : "✗ 失败";
}

function startToolLine(name, args) {
  if (stepPhase === "content") {
    resetStep();                 // 新一轮工具调用 → 新步骤
  }
  ensureStep();
  if (stepPhase === "think") finalizeStepThink();
  stepPhase = "tool";
  var line = document.createElement("div");
  line.className = "tool-line";
  // 智能体代理调用 → 卡片形态（Cursor 风格：点阵图标 + 工具名 + 任务描述 + 状态徽章）
  var isAgent = /agent|delegate/i.test(name);
  if (isAgent) {
    line.classList.add("agent");
    var agentTask = extractAgentTask(args);
    logAgentActivity({ name: name, task: agentTask });
  }
  // 折叠式工具行：默认一行（▶ 工具名），点击展开完整结果
  var summary = document.createElement("div");
  summary.className = "tool-line-summary";
  // 终端工具（run_command/terminal）：对标 opencode 显示「Shell 命令」而不是工具名
  if (/terminal|run_command|shell/i.test(name)) {
    var cmd = extractShellCommand(args);
    line.classList.add("shell");
    summary.innerHTML = '<span class="tl-arrow">▶</span><span class="tl-shell">Shell</span> '
      + '<span class="tl-cmd"></span>';
    summary.querySelector(".tl-cmd").textContent = cmd || name;
  } else if (isAgent) {
    // 智能体卡片：▶ + 橙色点阵图标 + 工具名徽章 + 任务描述（截断）+ 执行中状态
    summary.innerHTML = '<span class="tl-arrow">▶</span>'
      + '<span class="agent-dot" aria-hidden="true"></span>'
      + '<span class="agent-badge">Agent</span> '
      + '<span class="agent-name"></span>'
      + '<span class="agent-task"></span>'
      + '<span class="agent-status running">执行中…</span>';
    var an = summary.querySelector(".agent-name");
    if (an) an.textContent = name === "delegate_to_agent" ? "智能体委托" : name;
    var at = summary.querySelector(".agent-task");
    if (at) at.textContent = agentTask ? "「" + agentTask.slice(0, 60) + (agentTask.length > 60 ? "…" : "") + "」" : "";
  } else {
    summary.innerHTML = '<span class="tl-arrow">▶</span> ' + name;
  }
  var body = document.createElement("div");
  body.className = "tool-line-body";
  body.hidden = true;
  summary.addEventListener("click", function () {
    body.hidden = !body.hidden;
    var arrow = summary.querySelector(".tl-arrow");
    if (arrow) arrow.textContent = body.hidden ? "▶" : "▼";
    if (!body.hidden) {
      state.nearBottom = false;   // 展开工具结果：暂停自动跟随，定位到该块
      summary.scrollIntoView({ block: "nearest" });
    }
  });
  line.appendChild(summary);
  line.appendChild(body);
  toolLineEls[name] = { line: line, summary: summary, body: body };
  stepEl.appendChild(line);
}

/** 在工具行下方渲染文件变更折叠块（编辑 文件名 +N -M，点击展开 diff，对标 opencode） */
function renderToolDiff(lineEl, diffMeta) {
  // diffMeta 形如 "文件名 +10 -0\n@@ ...\n+新增\n-删除"
  var m = /^([^\n]*?)\s*\+(\d+)\s+-(\d+)/.exec(diffMeta);
  if (!m) return;
  var fileName = m[1].trim() || "文件";
  var adds = m[2], deletes = m[3];
  var diffBody = diffMeta.slice(diffMeta.indexOf("\n") + 1);
  var toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "tool-diff-toggle";
  // 默认展开（代码变化行直接可见）
  toggle.innerHTML = '<span class="td-arrow">▾</span> 编辑 ' +
    '<span class="td-file">' + fileName + '</span> ' +
    '<span class="td-add">+' + adds + '</span> <span class="td-del">-' + deletes + '</span>';
  var body = document.createElement("div");
  body.className = "tool-diff-body";
  // 代码变化行默认展开（用户要求：记录里应能直接看到 diff 内容，而非折叠隐藏）
  body.hidden = false;
  var pre = document.createElement("pre");
  pre.className = "tool-diff-pre";
  // 逐行渲染：+ 绿 / - 红 / 其余默认；空行跳过（用户要求：去掉空行）
  diffBody.split("\n").forEach(function (ln) {
    if (!ln.trim()) return;   // 跳过空行
    var span = document.createElement("span");
    if (ln.indexOf("+") === 0) span.className = "td-line-add";
    else if (ln.indexOf("-") === 0) span.className = "td-line-del";
    else if (ln.indexOf("@@") === 0) span.className = "td-line-meta";
    span.textContent = ln;
    pre.appendChild(span);
    pre.appendChild(document.createTextNode("\n"));
  });
  body.appendChild(pre);
  toggle.addEventListener("click", function () {
    body.hidden = !body.hidden;
    toggle.querySelector(".td-arrow").textContent = body.hidden ? "▸" : "▾";
  });
  lineEl.insertAdjacentElement("afterend", toggle);
  toggle.insertAdjacentElement("afterend", body);
}

/** 开始输出块（跟随当前步骤：思考/工具之后） */
function startContentBlock() {
  if (stepPhase === "think") finalizeStepThink();
  ensureStep();
  if (!stepContent) {
    stepContent = document.createElement("div");
    stepContent.className = "md";
    stepEl.appendChild(stepContent);
    stepContentText = "";
  }
  stepPhase = "content";
}

/** 渲染当前步骤的输出块（帧节流） */
function renderCurrentContent() {
  if (stepContent) {
    stepContent.innerHTML = renderMarkdown(stepContentText);
    // 检测到公式 → 后台预加载 KaTeX（避免公式渲染时才开始下载 271KB）
    ensureKaTeXForText(stepContentText);
    postProcess(stepContent);
  }
  if (state.streaming) {
    setCaret(true);
    // “正在思考”指示器保持贴在最新输出的最下方
    if (thinkingEl && thinkingEl.isConnected) aiMsgEl.appendChild(thinkingEl);
  }
  // 输出时跟随最新（对标 opencode）：用户没滚走就自动滚动到最新输出
  scrollIfNearBottom();
}

/* ===========================================================================
 * SSE 流式解析（fetch + getReader + TextDecoder）
 * ---------------------------------------------------------------------------
 * 关键点：一条 "data: {...}\n\n" 可能被切成多个 reader chunk，
 * 所以先把原始字节累计进 buffer，等到出现 "\n\n" 才解析一整条事件。
 * ========================================================================== */

/**
 * @param body            Response.body（ReadableStream）
 * @param signal          AbortSignal
 * @param handlers        { onThink(text), onDelta(text), onDone(), onError(msg) }
 */
function readEventStream(body, signal, handlers) {
  return new Promise(function (resolve) {
    var reader = body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var stopped = false;

    function dispatch(payload) {
      var evt = null;
      try { evt = JSON.parse(payload); } catch (e) { return; }
      if (!evt || typeof evt !== "object") return;

      if (typeof evt.think === "string" && evt.think !== "") {
        if (handlers.onThink) handlers.onThink(evt.think);
      } else if (typeof evt.delta === "string" && evt.delta !== "") {
        handlers.onDelta(evt.delta);
      } else if (evt.tool && typeof evt.tool === "object") {
        if (handlers.onTool) handlers.onTool(evt.tool);
      } else if (evt.usage && typeof evt.usage === "object") {
        if (handlers.onUsage) handlers.onUsage(evt.usage);
      } else if (evt.ask_user && typeof evt.ask_user === "object") {
        if (handlers.onAskUser) handlers.onAskUser(evt.ask_user);
      } else if (evt.done === true) {
        stopped = true;
        if (handlers.onDone) handlers.onDone();
      } else if (evt.error != null) {
        stopped = true;
        if (handlers.onError) handlers.onError(String(evt.error));
      }
    }

    /** 把 buffer 中所有完整的 "\n\n" 事件消费掉 */
    function consume() {
      var idx;
      while (!stopped && (idx = buffer.indexOf("\n\n")) !== -1) {
        var raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        var lines = raw.split("\n");
        for (var i = 0; i < lines.length; i++) {
          var t = lines[i].trim();
          if (t.indexOf("data:") === 0) {
            var payload = t.slice(5).trim();
            if (payload) dispatch(payload);
            if (stopped) break;
          }
        }
      }
    }

    (function pump() {
      if (stopped) { resolve(); return; }
      reader.read().then(function (result) {
        if (stopped) { resolve(); return; }
        if (result.done) {
          // 流自然结束：flush 掉解码器残留字节 + 最后一个没带 "\n\n" 的事件
          buffer += decoder.decode();
          consume();
          if (!stopped && buffer.trim()) {
            var lines = buffer.split("\n");
            for (var i = 0; i < lines.length; i++) {
              var t = lines[i].trim();
              if (t.indexOf("data:") === 0) {
                var payload = t.slice(5).trim();
                if (payload) dispatch(payload);
                if (stopped) break;
              }
            }
          }
          resolve();
          return;
        }
        buffer += decoder.decode(result.value, { stream: true }).replace(/\r\n/g, "\n");
        consume();
        pump();
      }, function (err) {
        // 读取失败 = 用户点了停止（AbortError）或连接断开，由调用方统一处理
        stopped = true;
        resolve({ aborted: err && err.name === "AbortError" });
      });
    })();
  });
}

/* ===========================================================================
 * 发送 & 流式对话（POST /api/chat）
 * ========================================================================= */

function sendMessage() {
  // 仅当前 UI 正在生成（streaming / sendingLock）才入队：
  // 切换后新对话的发送不受任何后台流影响（后台流并行跑，结果存回各自历史）
  if (state.streaming || state.sendingLock) {
    enqueueSend(els.input.value.trim());
    return;
  }
  // 后台生成保护：仅当后台生成的是【当前对话】时才入队——不同对话互相独立（对标 opencode）
  if (streamCtx && !streamCtx.finished && streamCtx.cid === state.conversationId) {
    enqueueSend(els.input.value.trim());
    return;
  }
  var text = els.input.value.trim();
  if (!text) return;

  // 斜杠命令（对标 opencode /commands）
  if (text.charAt(0) === "/") {
    var cmd = text.split(/\s+/)[0].toLowerCase();
    els.input.value = "";
    autoResizeInput();
    updateSendBtn();
    if (cmd === "/new" || cmd === "/clear") { newChat(); return; }
    if (cmd === "/export") { exportConversation(); return; }
    if (cmd === "/load-local") { openSettings(true); return; }
    if (cmd === "/help") {
      showErrorNote("可用命令：/new 新对话 · /clear 清空 · /export 导出 · /load-local 加载本地模型");
      return;
    }
    // 未知命令：当普通消息发出去
  }

  if (!state.activeProvider) {
    showErrorNote("请先选择一个模型供应商");
    return;
  }

  state.sendingLock = true;   // 立即锁住，防止上传/等待期间连点多发

  // 任意文件附件（非图片）：已上传到 files/，消息里给文件名提示（模型用 read_document 读内容）
  if (state.fileAttachments && state.fileAttachments.length > 0) {
    var fnote = "（用户上传了" + state.fileAttachments.length + "个文件，请逐一用 read_document 工具读取内容："
      + state.fileAttachments.map(function (f) { return "文件：" + f.name + "（files/" + f.name + "）"; }).join("；") + "）";
    text = (fnote + (text ? "\n用户的问题：" + text : "")).trim();
    state.fileAttachments = [];
    renderFileAttachPreview();
  }

  // 多模态：图片附件先上传到服务器，消息里给文件名提示（模型用 see_image 读图，
  // 避免把图片直接塞给不支持视觉的模型导致报错）
  if (state.attachments.length > 0) {
    var pendingNames = [];
    var attachPromises = state.attachments.map(function (url) {
      return fetch("/api/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_url: url, filename: f.name || "" })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok && d.name) pendingNames.push(d.name);
      }).catch(function () { /* 忽略失败附件 */ });
    });
    // 等全部上传完，拼一条带文件名的消息，然后正式开聊
    Promise.all(attachPromises).then(function () {
      var note = pendingNames.length
        ? "（用户发送了" + pendingNames.length + "张图片，请逐一用 see_image 工具查看："
          + pendingNames.map(function (n) { return "图片附件：" + n; }).join("；") + "）"
          + (text ? "\n用户的问题：" + text : "")
        : text;
      doSend(note);
    });
    return;
  }
  doSend(text);
}

/** 真正发送：推消息 → 渲染 → 开流（sendMessage 与队列共用；opts 可覆盖模型）
 *  对比模式：主消息与对比模型【并行】开流（双栏同时输出，不再串行等待） */
async function persistUserMessage(message) {
  var response = await fetch("/api/history/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: state.conversationId || "",
      message: message,
      project: state.project,
      provider: state.activeProvider ? state.activeProvider.key : "",
      model: state.activeProvider ? state.activeProvider.model : ""
    })
  });
  return expectOkResponse(response);
}

async function persistHistorySnapshot() {
  var response = await fetch("/api/history", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: state.conversationId || "",
      messages: state.messages,
      project: state.project,
      provider: state.activeProvider ? state.activeProvider.key : "",
      model: state.activeProvider ? state.activeProvider.model : ""
    })
  });
  return expectOkResponse(response);
}

function newRequestId() {
  return "chat-" + ((window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2));
}

async function doSend(text, opts) {
  opts = opts || {};
  streamLabelOverride = opts.label || null;
  var isCompareReq = !!opts.isCompare;
  if (!isCompareReq) {
    var navigationToken = state.loadSeq;
    var userMessage = {
      id: "m-" + ((window.crypto && crypto.randomUUID) ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2)),
      role: "user",
      content: text,
      ts: Date.now()
    };
    state.messages.push(userMessage);
    renderUserMessage(text, state.messages.length - 1);
    try {
      var persisted = await persistUserMessage(userMessage);
      if (state.loadSeq !== navigationToken) return;
      state.conversationId = persisted.id;
      if (persisted.message) state.messages[state.messages.length - 1] = persisted.message;
      renderHistoryList();
    } catch (persistError) {
      // 落库失败则不启动模型，输入保留给用户重试；避免出现“模型答了但用户消息没了”。
      state.messages = state.messages.filter(function (m) { return m.id !== userMessage.id; });
      renderAllMessages(null, true);
      els.input.value = text;
      autoResizeInput();
      state.sendingLock = false;
      updateSendBtn();
      showErrorNote("消息保存失败，尚未发送：" + ((persistError && persistError.message) || "未知错误"));
      return;
    }
  }
  clearAttachments();
  els.input.value = "";
  autoResizeInput();
  saveInputDraft();   // 发送成功 → 清除该对话草稿
  updateSendBtn();
  if (!els.empty.hidden) hideEmptyState();
  // 对比模式（主请求）：创建双栏面板 + 立即并行发起对比模型回答
  if (!isCompareReq && state.compare) {
    var panel = document.createElement("div");
    panel.className = "compare-panel";
    panel.innerHTML = '<div class="compare-col left"></div><div class="compare-col right"></div>';
    els.messages.appendChild(panel);
    opts.mountTo = panel.querySelector(".left");
    compareRightCol = panel.querySelector(".right");
    // 左栏：标签 + 用户消息（主模型回答渲染在 left）
    var lLabel = document.createElement("div");
    lLabel.className = "msg-model";
    lLabel.textContent = "主 · " + (state.activeProvider ? state.activeProvider.model : "");
    panel.querySelector(".left").appendChild(lLabel);
    var um = document.createElement("div");
    um.className = "msg-user msg-text";
    um.textContent = text;
    panel.querySelector(".left").appendChild(um);
    // 右栏：标签（对比模型回答渲染在 right）
    var rLabel = document.createElement("div");
    rLabel.className = "msg-model";
    rLabel.textContent = "⚖️ 对比 · " + (state.compare.label || state.compare.model);
    panel.querySelector(".right").appendChild(rLabel);
    var cmpCfg = { provider: state.compare.provider, model: state.compare.model, label: state.compare.label };
    setTimeout(function () { startCompareStream(text, cmpCfg); }, 60);   // 并行：不等主流
  } else if (isCompareReq) {
    // 对比请求由 startCompareStream 独立处理（不经过主流全局流状态）
    return;
  }
  opts.requestId = opts.requestId || newRequestId();
  beginStream(opts);
  streamChat(opts);
}

/** 独立对比流：单独 AbortController + 直接渲染到右栏（不碰全局流状态，与主流并行） */
function startCompareStream(text, cmp) {
  if (!compareRightCol) return;
  var ctrl = new AbortController();
  state.compareCtrl = ctrl;
  var compareRequestId = newRequestId();
  state.compareRequestId = compareRequestId;
  var contentEl = document.createElement("div");
  contentEl.className = "md";
  compareRightCol.appendChild(contentEl);
  var sseBuffer = "";
  var outputText = "";
  var decoder = new TextDecoder();
  var compareConversationId = state.conversationId || "";
  var compareProject = state.project;

  function applyCompareUsage(usage) {
    if (!usage || state.conversationId !== compareConversationId || state.project !== compareProject) return;
    state.totalInput += usage.input || 0;
    state.totalOutput += usage.output || 0;
    state.totalOutputFormal += Math.max(0, (usage.output || 0) - (usage.reasoning || 0));
    state.totalCached += usage.cached || 0;
    if (typeof usage.cost === "number") {
      state.totalCost += usage.cost;
      state.costIsEst = state.costIsEst || !!usage.cost_est;
    }
    updateTokenUsage();
  }

  function consumeCompareSSE(chunk, flush) {
    sseBuffer += chunk || "";
    var frames = sseBuffer.split("\n\n");
    sseBuffer = flush ? "" : frames.pop();
    frames.forEach(function (frame) {
      frame.split("\n").forEach(function (line) {
        if (line.indexOf("data: ") !== 0) return;
        var evt = JSON.parse(line.slice(6));
        if (typeof evt.delta === "string") outputText += evt.delta;
        if (evt.usage && typeof evt.usage === "object") applyCompareUsage(evt.usage);
        if (evt.error) throw new Error(evt.error);
      });
    });
  }

  function renderCompareOutput() {
    if (!outputText.trim()) return;
    contentEl.innerHTML = renderMarkdown(outputText);
    postProcess(contentEl);
  }

  fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: cmp.provider,
      model: cmp.model,
      messages: [{ role: "user", content: text }],
      conversation_id: state.conversationId || "",
      project: state.project,
      online: state.online,
      reasoning: state.reasoning,
      mode: state.mode || "",
      persist: false,
      request_id: compareRequestId
    }),
    signal: ctrl.signal
  }).then(function (res) {
    if (!res.ok) throw new Error("HTTP " + res.status);
    var reader = res.body.getReader();
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) {
          consumeCompareSSE(decoder.decode(), true);
          if (outputText.trim()) renderCompareOutput();
          else contentEl.textContent = "（对比模型未返回内容）";
          return;
        }
        consumeCompareSSE(decoder.decode(r.value, { stream: true }), false);
        renderCompareOutput();
        return pump();
      });
    }
    return pump();
  }).catch(function (e) {
    if (e && e.name !== "AbortError") {
      contentEl.textContent = "对比请求失败：" + e.message;
    }
  });
}

/* ---------- 发送队列（对标 codex：按顺序执行，可编辑/撤回/重排；按对话隔离） ---------- */
state.sendQueue = [];        // 当前对话队列（引用，实际存 sendQueues[cid]）
state.sendQueues = {};       // 对话队列存储：cid -> [items]（各对话独立，互不显示/影响）

/* ---------- 事件溯源队列（对标 t3code：命令队列 + 幂等 + 事件日志，刷新不丢） ---------- */
var _queueSeq = 0;   // 事件序号（幂等/审计用）

/** 读取某对话的队列（localStorage 持久化，刷新/重启不丢） */
function loadQueue(cid) {
  var key = "sendQueue_" + (cid || "new");
  try {
    var raw = localStorage.getItem(key);
    if (raw) {
      var arr = JSON.parse(raw);
      if (Array.isArray(arr)) return arr;
    }
  } catch (e) { /* 忽略损坏数据 */ }
  return [];
}

/** 写入某对话的队列到 localStorage（持久化） */
function saveQueue(cid, arr) {
  var key = "sendQueue_" + (cid || "new");
  try { localStorage.setItem(key, JSON.stringify(arr)); } catch (e) { /* 隐私模式忽略 */ }
}

/** 追加一条队列事件日志（审计：入队/出队/发送/完成 全记录，保留最近 200 条） */
function logQueueEvent(cid, evt) {
  var key = "queueEvents_" + (cid || "new");
  var events = [];
  try {
    var raw = localStorage.getItem(key);
    if (raw) events = JSON.parse(raw) || [];
    if (!Array.isArray(events)) events = [];
  } catch (e) { events = []; }
  events.push({ ts: Date.now(), seq: ++_queueSeq, type: evt.type, msg: (evt.msg || "").slice(0, 60), id: evt.id || "" });
  if (events.length > 200) events = events.slice(-200);
  try { localStorage.setItem(key, JSON.stringify(events)); } catch (e) { /* 忽略 */ }
}

/** 获取当前对话的队列（按 conversationId 隔离；不存在则创建；优先从 localStorage 恢复） */
function currentQueue() {
  var cid = state.conversationId || "new";
  if (!state.sendQueues[cid]) {
    state.sendQueues[cid] = loadQueue(cid);   // 刷新后恢复持久化队列
  }
  state.sendQueue = state.sendQueues[cid];
  return state.sendQueue;
}

/** 调整方向（对标 Codex redirect）：打断当前正在进行的生成（已生成内容保留为历史），
 *  把消息作为正常轮次发送（进入对话历史），AI 立即按新方向回答。
 *  防并发：旧流收尾时会 setTimeout(dequeueNext,300) 自动发队列——用 _suppressNextDequeue 吞掉这一次；
 *  且 dequeueNext 本身加"有流在跑则跳过"保护。 */
var _suppressNextDequeue = false;

function redirectSend(text) {
  text = (text || "").trim();
  if (!text) return;
  if (!state.activeProvider) {
    showErrorNote("请先选择模型供应商");
    return;
  }
  var _stop = state.streaming || state.sendingLock ||
    (streamCtx && !streamCtx.finished && streamCtx.cid === state.conversationId);
  if (_stop) {
    _suppressNextDequeue = true;   // 吞掉旧流收尾触发的自动发队列（避免与新消息并发）
    stopStreaming();               // abort 当前流（收尾在 streamChat 的 catch 里，保留已生成内容）
  }
  var _t = text;
  // 旧流收尾是异步的（abort → catch → finishStream）：轮询等它完全结束（streaming 复位）再发送，
  // 避免旧流收尾把新流的 streaming 状态覆盖、以及并发双流
  (function _wait() {
    if (state.streaming || state.sendingLock || (streamCtx && !streamCtx.finished)) {
      setTimeout(_wait, 40);
    } else {
      _suppressNextDequeue = false;   // 旧流已完成收尾（其 dequeueNext 若已触发已被吞）；复位供新流使用
      doSend(_t);                     // 进历史 + 立即发送（AI 按新方向回答）
    }
  })();
}

/** 后台生成中发送 → 入队并显示队列条（队列条自带提示，不用红色错误框）。
 *  opts.front=true → 插队到队列最前（"调整方向"：新指令优先于已排队的普通消息执行） */
function enqueueSend(text, opts) {
  if (!text) return;
  opts = opts || {};
  els.input.value = "";   // 清空输入框（消息已入队，防止被覆盖丢失）
  autoResizeInput();
  updateSendBtn();
  saveInputDraft();   // 入队后清除草稿（消息已不在输入框）
  var cid = state.conversationId || "new";
  var item = { text: text, id: Date.now() + Math.random().toString(36).slice(2, 6) };
  if (opts.front) item.front = true;   // 标记：调整方向插队项
  var q = currentQueue();
  if (opts.front) {
    q.unshift(item);   // 插到队首（比已排队的普通消息优先）
  } else {
    q.push(item);
  }
  saveQueue(cid, q);   // 持久化（刷新不丢）
  logQueueEvent(cid, { type: opts.front ? "enqueue_front" : "enqueue", msg: text, id: item.id });   // 事件日志
  renderSendQueue();
}

/** 渲染队列条（composer 上方）：预览 + 编辑 + 撤回 + 重排；只显示当前对话的队列 */
function renderSendQueue() {
  currentQueue();
  var box = document.getElementById("send-queue");
  if (!box) return;
  box.innerHTML = "";
  var hasInput = els.input && els.input.value.trim().length > 0;
  // 是否在生成（当前对话的流活跃）：决定空队列时是否显示"调整方向"提示条
  var isStreaming = state.streaming || state.sendingLock ||
    (streamCtx && !streamCtx.finished && streamCtx.cid === state.conversationId);
  // 显示条件：有队列 → 总是显示；无队列但生成中且有输入 → 显示"调整方向"条；否则隐藏
  if (!state.sendQueue.length && !(isStreaming && hasInput)) {
    box.hidden = true;
    saveQueue(state.conversationId || "new", state.sendQueue);   // 清空时也持久化
    return;
  }
  box.hidden = false;
  // 队列任何变更 → 持久化 + 事件日志（编辑/撤回/重排共用）
  var _persist = function (type, msg) {
    saveQueue(state.conversationId || "new", state.sendQueue);
    logQueueEvent(state.conversationId || "new", { type: type, msg: msg || "" });
  };
  var label = document.createElement("span");
  label.className = "send-queue-label";
  label.textContent = state.sendQueue.length
    ? "发送队列（" + state.sendQueue.length + "）"
    : "模型正在生成，可输入新指令调整方向";
  box.appendChild(label);
  // "调整方向"按钮（对标 Codex redirect）：打断当前生成 → 输入框内容作为正常消息进入历史 → AI 立即按新方向回答
  if (hasInput) {
    var redirectBtn = document.createElement("button");
    redirectBtn.type = "button";
    redirectBtn.className = "send-queue-redirect";
    redirectBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 15.5-6.4L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.5 6.4L3 16"/></svg><span>调整方向</span>';
    redirectBtn.title = "打断当前生成（已生成内容保留），把输入框内容作为新消息发送（进入对话历史），AI 立即按新方向回答";
    redirectBtn.addEventListener("click", function () {
      var text = els.input.value.trim();
      if (!text) return;
      els.input.value = "";
      autoResizeInput();
      updateSendBtn();
      saveInputDraft();
      redirectSend(text);
    });
    label.appendChild(redirectBtn);
  }
  state.sendQueue.forEach(function (item, i) {
    var row = document.createElement("div");
    row.className = "send-queue-item";
    var text = document.createElement("span");
    text.className = "send-queue-text";
    text.textContent = item.text.length > 40 ? item.text.slice(0, 40) + "…" : item.text;
    text.title = item.text;
    row.appendChild(text);
    // 调整方向（对标 Codex redirect）：打断当前生成，该条消息进入对话历史并立即发送，AI 按新方向回答
    var redirect = document.createElement("button");
    redirect.type = "button"; redirect.className = "send-queue-btn redirect"; redirect.textContent = "↻";
    redirect.title = "调整方向：打断当前生成，把该条消息作为新消息发送（进入对话历史），AI 立即按新方向回答";
    redirect.addEventListener("click", function () {
      var it = state.sendQueue.splice(i, 1)[0];
      _persist("redirect", it.text);
      redirectSend(it.text);
    });
    row.appendChild(redirect);
    var up = document.createElement("button");
    up.type = "button"; up.className = "send-queue-btn"; up.textContent = "↑";
    up.title = "前移";
    up.disabled = i === 0;
    up.addEventListener("click", function () {
      var a = state.sendQueue[i - 1], b = state.sendQueue[i];
      state.sendQueue[i - 1] = b; state.sendQueue[i] = a;
      _persist("reorder", item.text);
      renderSendQueue();
    });
    row.appendChild(up);
    var down = document.createElement("button");
    down.type = "button"; down.className = "send-queue-btn"; down.textContent = "↓";
    down.title = "后移";
    down.disabled = i === state.sendQueue.length - 1;
    down.addEventListener("click", function () {
      var a = state.sendQueue[i], b = state.sendQueue[i + 1];
      state.sendQueue[i] = b; state.sendQueue[i + 1] = a;
      _persist("reorder", item.text);
      renderSendQueue();
    });
    row.appendChild(down);
    var edit = document.createElement("button");
    edit.type = "button"; edit.className = "send-queue-btn"; edit.textContent = "✎";
    edit.title = "编辑（载入输入框）";
    edit.addEventListener("click", function () {
      els.input.value = item.text;
      autoResizeInput();
      updateSendBtn();
      els.input.focus();
      state.sendQueue.splice(i, 1);
      _persist("edit", item.text);
      renderSendQueue();
    });
    row.appendChild(edit);
    var cancel = document.createElement("button");
    cancel.type = "button"; cancel.className = "send-queue-btn danger"; cancel.textContent = "✕";
    cancel.title = "撤回";
    cancel.addEventListener("click", function () {
      state.sendQueue.splice(i, 1);
      _persist("cancel", item.text);
      renderSendQueue();
    });
    row.appendChild(cancel);
    box.appendChild(row);
  });
}

/** 流结束后：队列有消息 → 自动发下一条（只发当前对话的队列） */
function dequeueNext() {
  if (_suppressNextDequeue) { _suppressNextDequeue = false; return; }
  currentQueue();
  if (!state.sendQueue.length) return;
  if (state.streaming || state.sendingLock) return;   // 已有流在跑：等本轮结束再发（防并发双流）
  var item = state.sendQueue.shift();
  var cid = state.conversationId || "new";
  saveQueue(cid, state.sendQueue);   // 出队后持久化
  logQueueEvent(cid, { type: "dequeue", msg: item.text, id: item.id });   // 事件日志
  renderSendQueue();
  if (state.activeProvider) {
    doSend(item.text);
  } else {
    showErrorNote("请先选择模型供应商");
  }
}

/** 后台续答：对话 A 的后台流完成后，用 A 的上下文继续生成其排队消息（结果存回 A 历史，
 *  不打扰当前 UI——用户输入不会因切换而消失） */
function backgroundContinue(ctx, text) {
  if (!ctx || !ctx.cid || !text) return;
  var msgs = (ctx.messages || []).slice();
  msgs.push({ role: "user", content: text });
  fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: ctx.provider || (state.activeProvider ? state.activeProvider.key : ""),
      messages: msgs,
      conversation_id: ctx.cid,
      project: ctx.project || state.project,   // 用流发起时的项目（防串门）
      model: ctx.model || (state.activeProvider ? state.activeProvider.model : ""),
      online: state.online || false,
      reasoning: state.reasoning || "",
      mode: ""
    })
  }).then(function (res) {
    if (!res.ok || !res.body) return;
    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var raw = "";
    var out = "";
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) {
          // 存回 A 历史（用户输入 + 后台回答都保留）
          var full = msgs.slice();
          if (out.trim()) full.push({ role: "assistant", content: out.trim() });
          fetch("/api/history", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: ctx.cid, messages: full, project: ctx.project || state.project, provider: ctx.provider, model: ctx.model })
          }).catch(function () { /* 忽略 */ });
          renderHistoryList();
          flashHistoryDotFor(ctx.cid);
          // 若 A 还有排队消息 → 继续后台续答
          var aq = state.sendQueues[ctx.cid];
          if (aq && aq.length) {
            var nxt = aq.shift();
            saveQueue(ctx.cid, aq);   // 后台队列出队也持久化
            logQueueEvent(ctx.cid, { type: "dequeue_bg", msg: nxt.text, id: nxt.id });
            setTimeout(function () { backgroundContinue(ctx, nxt.text); }, 300);
          }
          return;
        }
        raw += decoder.decode(r.value, { stream: true });
        // 提取 delta（SSE 行）
        var lines = raw.split("\n");
        raw = lines.pop() || "";   // 保留不完整行
        lines.forEach(function (ln) {
          if (ln.indexOf("data: ") === 0) {
            try {
              var evt = JSON.parse(ln.slice(6));
              if (typeof evt.delta === "string") out += evt.delta;
            } catch (e) { /* 忽略 */ }
          }
        });
        return pump();
      });
    }
    return pump();
  }).catch(function () { /* 后台续答失败：不影响当前 UI */ });
}

/** 切换/停止必须同时终止服务端任务和浏览器读取，不保留半截 assistant。 */
async function cancelActiveStreamForSwitch() {
  var contexts = Object.keys(streamCtxs).map(function (key) { return streamCtxs[key]; })
    .filter(function (ctx, idx, all) { return ctx && all.indexOf(ctx) === idx && !ctx.finished; });
  contexts.forEach(function (ctx) {
    if (ctx.requestId) {
      fetch("/api/chat/" + encodeURIComponent(ctx.requestId) + "/cancel", {
        method: "POST",
        keepalive: true
      }).catch(function () { /* 浏览器 abort 仍会触发服务端断连取消 */ });
    }
    finishStream("stop", "", ctx.cid);
    if (ctx.controller) ctx.controller.abort();
  });
  if (state.compareRequestId) {
    fetch("/api/chat/" + encodeURIComponent(state.compareRequestId) + "/cancel", {
      method: "POST",
      keepalive: true
    }).catch(function () { /* 同上 */ });
  }
  if (state.compareCtrl) state.compareCtrl.abort();
  if (state.controller) state.controller.abort();
  state.compareCtrl = null;
  state.compareRequestId = null;
}

function stopStreaming() {
  if (!currentConvStreaming()) return;
  cancelActiveStreamForSwitch();
}

async function streamChat(opts) {
  if (!state.activeProvider) return;
  opts = opts || {};
  var providerKey = opts.provider || state.activeProvider.key;
  var chatModel = opts.model || (state.activeProvider ? state.activeProvider.model : "");
  var requestId = opts.requestId || newRequestId();
  if (streamCtx && !streamCtx.finished) streamCtx.requestId = requestId;
  // 新对话先落库拿 id：切换对话时后台流才能把结果存回原对话
  // （doSend 已做 0 延迟落库；这里兜底：doSend 的即时保存失败/未触发时仍能建会话）
  var requestConversationId = state.conversationId || "";
  var seedNavigation = state.loadSeq;
  var seedCtx = streamCtx;
  if (!requestConversationId) {
    try {
      var seed = await fetch("/api/history", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: "", messages: state.messages, project: state.project,
                               provider: providerKey, model: chatModel })
      }).then(function (r) { return r.json(); });
      if (seed && seed.id) {
        requestConversationId = seed.id;
        if (state.loadSeq === seedNavigation && !state.conversationId) {
          state.conversationId = seed.id;
        }
        if (seedCtx) {
          // beginStream 时还没有 id（用 "" 挂载）→ 创建后补 cid 并迁移 streamCtxs 的 key
          var oldKey = seedCtx.cid || "";
          seedCtx.cid = seed.id;
          if (oldKey && oldKey !== seed.id && streamCtxs[oldKey] === seedCtx) {
            delete streamCtxs[oldKey];
          }
          streamCtxs[seed.id] = seedCtx;   // 用真实 id 重新挂载（flow 才能被 streamChat 找到）
        }
        renderHistoryList();                       // 新对话建立后刷新历史列表（生成中的对话可见）
      }
    } catch (e) { /* 保存失败不阻塞对话 */ }
  }
  var body = JSON.stringify({
    provider: providerKey,
    messages: state.messages,
    conversation_id: requestConversationId,
    project: state.project,
    model: chatModel,
    online: state.online || false,
    reasoning: state.reasoning || "",
    mode: state.mode || "",
    request_id: requestId
  });

  var controller = new AbortController();
  state.controller = controller;
  // 把 controller 挂到本流上下文（切走再切回时 stopStreaming 能 abort 后台流）
  if (streamCtx && !streamCtx.finished) streamCtx.controller = controller;
  var finished = false;

  var flowCid = requestConversationId;   // 本流发起时的对话 id（切走后不变，收尾用正确的流上下文）
  // 多流隔离：每流独立缓冲（后台流不碰全局渲染状态，防串台）
  var flowState = { acc: "", thinkAcc: "", toolLog: [], timeline: [] };
  // timeline: 交错时间线（还原实时对话展示）[{type:"think"|"text"|"tool", ...}]
  // - think: 一段思考文本 {type, text}
  // - tool:  一次工具调用 {type, name, args, done, result, diff}
  // - text:  一段正文（工具调用间的过渡输出）
  var _tlCur = null;   // 当前累积中的时间线段（think 或 text），新段类型变化时落盘
  function _tlPush(type) {
    if (type === "think") {
      if (!_tlCur || _tlCur.type !== "think") {
        _tlCur = { type: "think", text: "" };
        flowState.timeline.push(_tlCur);
      }
    } else if (type === "text") {
      if (!_tlCur || _tlCur.type !== "text") {
        _tlCur = { type: "text", text: "" };
        flowState.timeline.push(_tlCur);
      }
    } else if (type === "tool") {
      _tlCur = null;   // 工具调用切断当前 think/text 段
    }
  }
  var _ctxForFlow = streamCtxs[flowCid];
  if (_ctxForFlow) _ctxForFlow.flow = flowState;
  function isCurrentFlow() { return !flowCid || flowCid === state.conversationId; }
  function finishOnce(kind, msg) {
    if (finished) return;
    finished = true;
    finishStream(kind, msg, flowCid);
  }

  try {
    var res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      signal: controller.signal
    });

    if (!res.ok) {
      // HTTP 400/500：FastAPI 会返回 JSON { detail }
      var detail = "请求失败（HTTP " + res.status + "）";
      try {
        var j = await res.json();
        if (j && j.detail) detail = String(j.detail);
      } catch (e) { /* 非 JSON 错误体，用默认文案 */ }
      throw new Error(detail);
    }
    if (!res.body) throw new Error("当前浏览器不支持流式响应");

    var outcome = await readEventStream(res.body, controller.signal, {
      onThink: function (fragment) {
        flowState.thinkAcc += fragment;             // 每流独立累计（防串台）
        _tlPush("think"); if (_tlCur) _tlCur.text += fragment;   // 交错时间线：记录本段思考
        if (!isCurrentFlow()) return;               // 后台流不渲染当前 UI
        clearWaitHint();                            // 思考片段即响应：去掉等待提示
        thinkAcc += fragment;
        startThinkBlock();                          // 每轮思考独立成块
        // 思考阶段的规划不再解析：模型常复述用户请求（含"【步骤规划】"字样），
        // 宽松匹配会解析出思考碎片而非真正规划 → 规划只在正文(onDelta)解析，且要求步骤列表结构
        // 性能：进度标记解析 + 思考文本 DOM 写入都节流（思考通常很长，每片段跑是 O(n²)）
        stepThinkText += fragment;
        var _now2 = Date.now();
        if (!_thinkThrottleTs || _now2 - _thinkThrottleTs >= 120) {
          _thinkThrottleTs = _now2;
          parsePlanProgress(thinkAcc);              // 进度标记仍可解析（思考里会确认步骤完成）
          if (stepThink) stepThink.text.textContent = stepThinkText;
          if (stepThink) stepThink.status.textContent = "正在思考…";
          showTaskStatus("思考中…", taskToolCount);
        }
      },
      onDelta: function (delta) {
        flowState.acc += delta;                     // 每流独立累计（防串台）
        _tlPush("text"); if (_tlCur) _tlCur.text += delta;   // 交错时间线：记录本段正文（含工具间过渡）
        if (!isCurrentFlow()) return;               // 后台流不渲染当前 UI
        acc = flowState.acc;
        clearWaitHint();                            // 第一个 delta 到达 → 去掉等待提示
        // 步骤规划解析（右栏：【方案设计】→ 方案区；【步骤规划】块 → 全列出；【步骤完成：N】→ 打✔）
        // 性能：3 个解析器都是全文扫描，每 token 跑是 O(n²)。改为时间节流——
        // 每 120ms 解析一次（或文本增长 ≥200 字），流式期间行为不变，长回复不卡。
        var _now = Date.now();
        if (!_parseThrottleTs || _now - _parseThrottleTs >= 120 || acc.length >= _parseThrottleLen + 200) {
          _parseThrottleTs = _now;
          _parseThrottleLen = acc.length;
          parseProposalFromText(acc);
          parsePlanFromText(acc);
          parsePlanProgress(acc);
        }
        if (stepPhase !== "content") startContentBlock();   // 输出跟随当前步骤
        stepContentText += delta;
        scheduleStreamRender();                     // 帧节流：长回复不卡顿
        showTaskStatus("正在生成回答…", taskToolCount);
        // 滚动跟随节流：最多每 120ms 一次（renderCurrentContent 的帧回调也有，这里兜底
        // 工具调用间隙/无正文渲染时仍能跟随）
        if (!_scrollThrottleTs || _now - _scrollThrottleTs >= 120) {
          _scrollThrottleTs = _now;
          scrollIfNearBottom();
        }
      },
      onTool: function (info) {
        clearWaitHint();
        if (!info || !info.name) return;
        _tlPush("tool");                             // 工具调用：切断当前 think/text 段（交错时间线）
        if (!isCurrentFlow()) {
          // 后台流：只记录工具（收尾存历史），不渲染当前 UI
          if (info.phase === "done") {
            flowState.toolLog.push({ name: info.name, args: (info.args || "").slice(0, 2000),
              done: !!info.ok, result: (info.result || "").slice(0, 2000),
              diff: info.diff ? info.diff.slice(0, 20000) : undefined });
            flowState.timeline.push({ type: "tool", name: info.name,
              args: (info.args || "").slice(0, 2000), done: !!info.ok,
              result: (info.result || "").slice(0, 2000),
              diff: info.diff ? info.diff.slice(0, 20000) : undefined });
            _tlPush("tool");
          }
          return;
        }
        if (info.phase === "start") {
          startToolLine(info.name, info.args);                 // 工具行进入当前步骤（终端工具显示 Shell 命令）
          taskToolCount += 1;
          showTaskStatus("正在调用工具：" + info.name, taskToolCount);
        } else if (info.phase === "done") {
          var el = toolLineEls[info.name];
          if (el) {
            // 折叠式工具行：summary 一行（▶ 工具名(参数) → 完成/失败），点击展开完整结果
            var s = el.summary;
            var label = "▶ " + info.name;
            if (info.args) label += "(" + info.args + ")";
            label += info.ok ? " → 完成" : " → 失败";
            // 失败时摘要带原因（一眼看懂为什么失败）
            if (!info.ok && info.result) label += " · " + info.result.slice(0, 80);
            s.innerHTML = "";
            var arrow = document.createElement("span");
            arrow.className = "tl-arrow";
            arrow.textContent = "▶";
            var textSpan = document.createElement("span");
            textSpan.textContent = label.slice(1);   // 去掉开头的 ▶（arrow 单独显示）
            s.appendChild(arrow);
            s.appendChild(textSpan);
            // 智能体工具完成 → 恢复卡片形态（点阵图标 + 名称 + 状态徽章完成/失败）
            if (/agent|delegate/i.test(info.name)) {
              s.innerHTML = '<span class="tl-arrow">▶</span>'
                + '<span class="agent-dot" aria-hidden="true"></span>'
                + '<span class="agent-badge">Agent</span> '
                + '<span class="agent-name"></span>'
                + '<span class="agent-task"></span>'
                + '<span class="agent-status ' + (info.ok ? "done" : "fail") + '">'
                + (info.ok ? "✔ 完成" : "✗ 失败") + '</span>';
              var an = s.querySelector(".agent-name");
              if (an) an.textContent = info.name === "delegate_to_agent" ? "智能体委托" : info.name;
              var at = s.querySelector(".agent-task");
              var taskStr = extractAgentTask(info.args);
              if (at) at.textContent = taskStr ? "「" + taskStr.slice(0, 60) + (taskStr.length > 60 ? "…" : "") + "」" : "";
              // 失败原因追加在任务描述后
              if (!info.ok && info.result) {
                if (at) at.textContent += " · " + String(info.result).slice(0, 60);
              }
            }
            // 工具执行失败 → 红色高亮（醒目提示，模型也会在下一轮看到错误信息）
            if (!info.ok) el.line.classList.add("fail");
            // body：完整结果（紧凑显示，压缩连续空行；默认折叠，点击展开）
            if (info.result) {
              el.body.textContent = String(info.result).replace(/\n{3,}/g, "\n\n").trim();
            }
            // 文件变更 diff → 折叠块（点击展开红绿对比，对标 opencode）
            if (info.diff && /^[\s\S]*\+(\d+)\s+-(\d+)/.test(info.diff)) {
              renderToolDiff(el.line, info.diff);
            }
            // 同步独立智能体活动面板状态
            if (/agent|delegate/i.test(info.name)) {
              updateAgentActivity(info.name, info.ok);
            }
          }
          if (!acc) finalizeStepThink("工具完成，继续思考…");
          showTaskStatus("已完成：" + info.name, taskToolCount);
          // 兜底推进：模型没输出【步骤完成：N】时，按工具调用进度估算步骤推进
          // （调研类任务每步通常伴随工具调用；只在从未有显式标记时启用，避免冲突）
          if (state.planStarted && state.planSteps.length && !state.planExplicitProgress) {
            autoAdvancePlan();
          }
          // 记录工具调用（历史完整保存，重看时展示；同步 flowState 供收尾）
          var tIdx = toolLog.findIndex(function (t) { return t.name === info.name && !t.done; });
          if (tIdx >= 0) {
            toolLog[tIdx].done = info.ok;
            toolLog[tIdx].result = (info.result || "").slice(0, 2000);
            if (info.diff) toolLog[tIdx].diff = info.diff.slice(0, 20000);
          } else {
            toolLog.push({ name: info.name, args: (info.args || "").slice(0, 2000), done: info.ok, result: (info.result || "").slice(0, 2000), diff: info.diff ? info.diff.slice(0, 20000) : undefined });
          }
          // 同步到本流独立缓冲（收尾用 flowState 存历史 → 切换/刷新后工具使用可见）
          var fIdx = flowState.toolLog.findIndex(function (t) { return t.name === info.name && !t.done; });
          if (fIdx >= 0) {
            flowState.toolLog[fIdx].done = info.ok;
            flowState.toolLog[fIdx].result = (info.result || "").slice(0, 2000);
            if (info.diff) flowState.toolLog[fIdx].diff = info.diff.slice(0, 20000);
          } else {
            flowState.toolLog.push({ name: info.name, args: (info.args || "").slice(0, 2000), done: info.ok, result: (info.result || "").slice(0, 2000), diff: info.diff ? info.diff.slice(0, 20000) : undefined });
          }
          // 交错时间线：记录本次工具调用（与思考/正文按序交错，历史渲染还原实时形态）
          flowState.timeline.push({ type: "tool", name: info.name,
            args: (info.args || "").slice(0, 2000), done: !!info.ok,
            result: (info.result || "").slice(0, 2000),
            diff: info.diff ? info.diff.slice(0, 20000) : undefined });
          _tlPush("tool");
        }
      },
      onUsage: function (usage) {
        if (!isCurrentFlow()) return;               // 后台流不更新当前对话统计
        state.usage = usage;
        state.lastContext = usage.context || usage.input || 0;   // 当前占用（不累计）
        state.lastInput = usage.input || 0;                       // 最近一次 input（命中率展示）
        state.lastCached = usage.cached || 0;                     // 最近一次缓存命中
        state.lastCacheBust = !!(usage.cache_bust || usage.cache_drift);
        if (usage.compacted) {
          showErrorNote("上下文已自动压缩：" + Number(usage.context_before_compact || 0).toLocaleString()
            + " → " + Number(usage.context_after_compact || usage.context || 0).toLocaleString() + " tokens");
        }
        state.totalInput += usage.input || 0;                                                                                          
        // 全部输出（含思考）→ “本对话 tokens”
        state.totalOutput += usage.output || 0;
        // 正式输出 = 总输出 - 思考过程 → “上下文”
        state.totalOutputFormal += Math.max(0, (usage.output || 0) - (usage.reasoning || 0));
        streamOutTokens += Math.max(0, (usage.output || 0) - (usage.reasoning || 0));
        state.totalCached += usage.cached || 0;
        // 计费：本次费用 = 输入(未命中)×输入价 + 输出×输出价 + 缓存命中×缓存价（元/百万 tokens）
        // 价格取当前供应商 activeProvider.price（后端 /api/providers 下发）；未收录模型按默认价估算
        var cost = (typeof usage.cost === "number")
          ? { cost: usage.cost, est: !!usage.cost_est }
          : calcCost(usage.input || 0, usage.output || 0, usage.cached || 0);
        if (cost && typeof cost.cost === "number") {
          state.totalCost += cost.cost;
          state.costIsEst = state.costIsEst || !!cost.est;
        }
        updateTokenUsage();
      },
      onAskUser: function (info) {
        showAskModal(info);   // AI 弹窗询问用户（对标 opencode）
      },
      onDone: function () { finishOnce("ok"); },
      onError: function (msg) { finishOnce("error", msg); }
    });

    // 服务端没发 done 就直接关流（或中途断开）→ 视为正常结束
    if (!finished && !controller.signal.aborted) finishOnce("ok");
    // outcome.aborted 与 controller.signal.aborted 等价，兜底覆盖
    if (!finished && controller.signal.aborted) finishOnce("stop");
  } catch (err) {
    if (controller.signal.aborted || (err && err.name === "AbortError")) {
      // 用户点停止：保留已生成的部分，不算错误
      finishOnce("stop");
    } else {
      finishOnce("error",
        (err && err.message) || "网络错误：请确认服务已启动、网络可达");
    }
  }
}

/* ===========================================================================
 * 滚动行为（智能自动滚 + “回到最新”药丸）
 * ========================================================================= */

function onMessagesScroll() {
  var el = els.messages;
  var dist = el.scrollHeight - el.scrollTop - el.clientHeight;
  state.nearBottom = dist < NEAR_BOTTOM_PX;
  els.backPill.hidden = state.nearBottom;
  // 锚点索引条：滚动时高亮当前可见回合（节流）
  if (!_anchorScrollTimer) {
    _anchorScrollTimer = setTimeout(function () {
      _anchorScrollTimer = null;
      updateAnchorActive();
    }, 120);
  }
}

function scrollIfNearBottom() {
  if (state.nearBottom) scrollToBottom(false);
}

function scrollToBottom(smooth) {
  els.messages.scrollTo({
    top: els.messages.scrollHeight,
    behavior: smooth ? "smooth" : "auto"
  });
  // 滚动到底后重置 nearBottom：防止渲染期间 clearMessagesDom 清空 DOM 触发
  // onMessagesScroll（scrollHeight=0 → dist 巨大）把 nearBottom 误置为 false，
  // 导致后续 scrollIfNearBottom 不再滚底（长对话停顶部根因）
  state.nearBottom = true;
}

function hideEmptyState() {
  els.empty.hidden = true;
  els.messages.classList.remove("empty");
  state.nearBottom = true;
}

/* ===========================================================================
 * 输入框
 * ========================================================================= */

/** 按对话保存输入草稿（对标 opencode：切换对话后切回，未发送的输入不丢） */
function saveInputDraft() {
  if (!els.input) return;
  var key = "inputDraft_" + (state.conversationId || "new");
  try {
    var v = els.input.value;
    if (v && v.trim()) localStorage.setItem(key, v);
    else localStorage.removeItem(key);
  } catch (e) { /* 忽略 */ }
}

/** 恢复当前对话的输入草稿（切换对话/新对话时调用） */
function restoreInputDraft() {
  if (!els.input) return;
  var key = "inputDraft_" + (state.conversationId || "new");
  var v = "";
  try { v = localStorage.getItem(key) || ""; } catch (e) { /* 忽略 */ }
  if (v) {
    els.input.value = v;
    autoResizeInput();
    updateSendBtn();
  } else {
    // 无草稿：清空输入框（防止显示上一个对话的内容）
    els.input.value = "";
    autoResizeInput();
    updateSendBtn();
  }
}

function autoResizeInput() {
  var t = els.input;
  t.style.height = "auto";
  // 空内容：直接回到最小高度（textarea scrollHeight 有惯性，不会自己回缩）
  if (!t.value) {
    t.style.height = "44px";
    t.style.overflowY = "hidden";
    return;
  }
  // 精确高度：scrollHeight 含 padding/border，需减掉（否则每行越加越高不准）
  var cs = getComputedStyle(t);
  var pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0)
          + (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
  var h = Math.min(t.scrollHeight - pad, MAX_INPUT_HEIGHT);
  t.style.height = Math.max(44, h) + "px";   // 不低于 min-height 44px
  t.style.overflowY = t.scrollHeight - pad > MAX_INPUT_HEIGHT ? "auto" : "hidden";
}

/* ===========================================================================
 * API Key 设置弹窗
 * 打开时 GET /api/providers 拉取 has_key；每行 保存 → POST /api/settings。
 * key 只发给服务器，浏览器永远看不到、也不存 localStorage。
 * ========================================================================= */

var settingsOpen = false;
var settingsFocusLocal = false;   // 打开设置后自动滚动到"本地 GGUF"行并聚焦文件夹输入
var settingsProviders = [];    // 弹窗打开时拉取的供应商快照（含 has_key）

var EYE_ON_SVG =
  '<svg class="eye-on" width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
var EYE_OFF_SVG =
  '<svg class="eye-off" width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>' +
  '<line x1="1" y1="1" x2="23" y2="23"/></svg>';

function openSettings(focusLocal) {
  loadVersion();
  if (settingsOpen) return;
  settingsOpen = true;
  settingsFocusLocal = Boolean(focusLocal);
  els.settingsModal.hidden = false;
  els.settingsList.innerHTML = "";
  fetchSettings();
  switchTab("providers");
  els.settingsModal.querySelector(".modal-panel").focus();
}

/** 设置弹窗分类标签页（避免超长单列布局） */
function switchTab(name) {
  document.querySelectorAll(".modal-tab").forEach(function (b) {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach(function (p) {
    p.classList.toggle("active", p.id === "tab-" + name);
  });
  // 惰性渲染：切到该页才拉数据
  if (name === "general") renderGeneralPanel();
  if (name === "models") { renderModelPanel(); renderContextPanel(); }
  if (name === "tools") { renderMcpList(); renderSkillsList(); renderPluginsList(); }
}

/* ---------- 通用设置（界面/交互偏好，localStorage） ---------- */

function getPref(key, fallback) {
  try {
    var v = localStorage.getItem(key);
    return v === null || v === undefined ? fallback : v;
  } catch (e) { return fallback; }
}
function setPref(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* 隐私模式忽略 */ }
}

/** 通用设置面板：主题 / 发送方式 / 思考摘要，修改立即生效 */
function renderGeneralPanel() {
  var themeSel = document.getElementById("gen-theme");
  var sendSel = document.getElementById("gen-send");
  var thinkSel = document.getElementById("gen-think");
  if (themeSel && !themeSel.dataset.filled) {
    themeSel.innerHTML = "";
    themeSel.appendChild(new Option("深色（默认）", "dark"));
    themeSel.appendChild(new Option("深色 · 墨蓝", "midnight"));
    themeSel.appendChild(new Option("深色 · 石墨", "graphite"));
    themeSel.appendChild(new Option("深色 · 森林", "forest"));
    themeSel.appendChild(new Option("深色 · 深海", "ocean"));
    themeSel.appendChild(new Option("深色 · 暗紫", "violet"));
    themeSel.appendChild(new Option("浅色", "light"));
    themeSel.appendChild(new Option("浅色 · 米白", "paper"));
    themeSel.appendChild(new Option("浅色 · 月光", "moonlight"));
    themeSel.appendChild(new Option("浅色 · 暖杏", "sunset"));
    themeSel.appendChild(new Option("浅色 · 灰绿", "sage"));
    themeSel.appendChild(new Option("浅色 · 粉彩", "rose"));
    themeSel.appendChild(new Option("跟随系统", "system"));
    themeSel.dataset.filled = "1";
    themeSel.addEventListener("change", function () {
      var val = themeSel.value;
      setPref("theme", val);
      applyTheme(resolveTheme(val), false);
    });
  }
  if (sendSel && !sendSel.dataset.filled) {
    sendSel.innerHTML = "";
    sendSel.appendChild(new Option("Enter 发送", "enter"));
    sendSel.appendChild(new Option("Ctrl+Enter 发送", "ctrlenter"));
    sendSel.dataset.filled = "1";
    sendSel.addEventListener("change", function () {
      setPref("send_mode", sendSel.value);
      updateHintKeys();
    });
  }
  if (thinkSel && !thinkSel.dataset.filled) {
    thinkSel.innerHTML = "";
    thinkSel.appendChild(new Option("默认折叠", "fold"));
    thinkSel.appendChild(new Option("默认展开", "unfold"));
    thinkSel.dataset.filled = "1";
    thinkSel.addEventListener("change", function () { setPref("think_default", thinkSel.value); });
  }
  if (themeSel) themeSel.value = getPref("theme", "dark");
  if (sendSel) sendSel.value = getPref("send_mode", "enter");
  if (thinkSel) thinkSel.value = getPref("think_default", "fold");
  updateHintKeys();
  renderAppsManageList();   // 通用页展示应用协作列表（数据来自 loadApps）
  // 权限矩阵由独立模块负责，避免继续膨胀 app.js。
  var pw = document.getElementById("perm-write");
  var pc = document.getElementById("perm-command");
  if (pw && !pw.dataset.filled) {
    pw.innerHTML = "";
    pw.appendChild(new Option("允许（默认）", "allow"));
    pw.appendChild(new Option("询问", "ask"));
    pw.appendChild(new Option("拒绝", "deny"));
    pw.dataset.filled = "1";
  }
  if (pc && !pc.dataset.filled) {
    pc.innerHTML = "";
    pc.appendChild(new Option("询问确认（默认）", "ask"));
    pc.appendChild(new Option("自动允许", "allow"));
    pc.appendChild(new Option("拒绝", "deny"));
    pc.dataset.filled = "1";
  }
  if (window.WenmoPermissions) window.WenmoPermissions.init();
  if (window.WenmoBilling) window.WenmoBilling.init();
}


/** 底部快捷键提示随发送方式变化 */
function updateHintKeys() {
  var el = document.getElementById("hint-keys");
  if (!el) return;
  el.textContent = getPref("send_mode", "enter") === "ctrlenter"
    ? "Ctrl+Enter 发送 · Enter 换行"
    : "Enter 发送 · Shift+Enter 换行";
}

/** 云端视觉模型补充列表（各供应商推荐的图像模型） */
var VISION_MODELS = [
  { provider: "qianwen", model: "qwen-vl-max", label: "qwen-vl-max（通义 · 视觉）" },
  { provider: "qianwen", model: "qwen-vl-plus", label: "qwen-vl-plus（通义 · 视觉）" },
  { provider: "zhipu", model: "glm-4v-flash", label: "glm-4v-flash（智谱 · 免费视觉）" },
  { provider: "kimi", model: "moonshot-v1-8k-vision-preview", label: "moonshot-vision（Kimi · 视觉）" },
  { provider: "doubao", model: "doubao-1.5-vision-pro", label: "doubao-vision-pro（豆包 · 视觉）" },
  { provider: "siliconflow", model: "Qwen/Qwen2.5-VL-72B-Instruct", label: "Qwen2.5-VL-72B（硅基流动 · 视觉）" }
];

/** 模型设置反馈行（本地加载进度/错误） */
function modelsFeedback(text, isErr) {
  var row = document.getElementById("models-feedback-row");
  if (row) row.hidden = false;
  var fb = document.getElementById("models-feedback");
  if (!fb) return;
  fb.textContent = text || "";
  fb.classList.toggle("error", Boolean(isErr));
  if (fb._clearTimer) { clearTimeout(fb._clearTimer); fb._clearTimer = null; }
  if (!isErr && text) {
    fb._clearTimer = setTimeout(function () { if (fb) fb.textContent = ""; }, 5000);
  }
}

/** 模型设置面板：文本模型 + 图像模型。
 * 每个下拉 = 本地 GGUF（扫描 + 已加载）+ 全部供应商推荐模型（+ 云端视觉模型），
 * 值编码为 "provider::model"，选中本地模型保存后自动触发加载。 */
function renderModelPanel() {
  var tm = document.getElementById("models-text-model");
  var vm = document.getElementById("models-vision-model");
  if (!tm || !vm) return;
  var folder = "";
  try { folder = localStorage.getItem("last_local_folder") || ""; } catch (e) { /* 忽略 */ }
  var scanPromise = folder
    ? fetch("/api/local/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: folder })
      }).then(function (r) { return r.json(); }).catch(function () { return { files: [] }; })
    : Promise.resolve({ files: [] });
  Promise.all([
    fetch("/api/providers").then(function (r) { return r.json(); }).catch(function () { return { providers: [] }; }),
    fetch("/api/settings/context").then(function (r) { return r.json(); }).catch(function () { return {}; }),
    fetch("/api/local/status").then(function (r) { return r.json(); }).catch(function () { return { status: "idle" }; }),
    // Ollama 可能未响应，1.5s 超时，不拖累整个面板
    fetch("/api/ollama/models", { signal: AbortSignal.timeout(1500) }).then(function (r) { return r.json(); }).catch(function () { return { models: [] }; }),
    scanPromise
  ]).then(function (results) {
    var pd = results[0], sd = results[1], ld = results[2], od = results[3], scan = results[4];
    var provs = pd.providers || [];
    // 本地 GGUF 集合（已加载 + 扫描到的，按名去重）
    var seen = {};
    var locals = [];
    var pushLocal = function (name, path, size, mmproj) {
      if (!name || seen[name]) return;
      seen[name] = 1;
      locals.push({ name: name, path: path || "", size: size || 0, mmproj: mmproj || "" });
    };
    if (ld.status === "ready" && ld.name) pushLocal(ld.name, ld.path, 0, ld.mmproj);
    (scan.files || []).forEach(function (f) {
      if (f.kind !== "model") return;
      pushLocal(f.name, f.path, f.size, f.mmproj);
    });
    buildModelDropdown(tm, "text", provs, locals, od, sd.default_provider, sd.default_model);
    buildModelDropdown(vm, "vision", provs, locals, od, sd.vision_provider, sd.vision_model);
    // 智能体模型（留空 = 默认用当前对话模型）
    buildModelDropdown(document.getElementById("models-agent-model"), "agent", provs, locals, od,
                       sd.agent_provider, sd.agent_model);
    buildModelDropdown(document.getElementById("models-agent-vision-model"), "agent-vision", provs, locals, od,
                       sd.agent_vision_provider, sd.agent_vision_model);
  }).catch(function () { /* 忽略 */ });
}

/** 组装统一模型下拉：本地 GGUF 组 + Ollama 组 + 各供应商组（图像另有云端视觉组） */
function buildModelDropdown(sel, kind, provs, locals, ollamaData, curProvider, curModel) {
  var customRow = document.getElementById("models-" + kind + "-custom-row");
  var customProvSel = document.getElementById("models-" + kind + "-custom-provider");
  var customModelInput = document.getElementById("models-" + kind + "-custom-model");
  sel.innerHTML = "";
  var cur = { provider: curProvider || "", model: curModel || "" };
  var found = false;

  var addOpt = function (group, provider, model, label, localPath) {
    var opt = document.createElement("option");
    opt.value = provider + "::" + model;
    opt.textContent = label;
    if (localPath) opt.dataset.localPath = localPath;
    if (!found && cur.provider === provider && cur.model === model) {
      opt.selected = true;
      found = true;
    }
    group.appendChild(opt);
  };
  var mkGroup = function (label) {
    var g = document.createElement("optgroup");
    g.label = label;
    sel.appendChild(g);
    return g;
  };

  var emptyOpt = document.createElement("option");
  emptyOpt.value = "";
  emptyOpt.textContent = "（不改变）";
  sel.appendChild(emptyOpt);

  // 1) 本地 GGUF（选中保存后自动加载）
  if (locals.length) {
    var g = mkGroup("本地 GGUF（选中后自动加载）");
    locals.forEach(function (m) {
      var label = m.name;
      if (m.size) label += " · " + formatSize(m.size);
      if (m.mmproj) label += " · 可看图";
      addOpt(g, "local", m.name, label, m.path);
    });
  } else {
    var hintOpt = document.createElement("option");
    hintOpt.value = "";
    hintOpt.disabled = true;
    hintOpt.textContent = "（暂无本地 GGUF：到「供应商 → 本地 GGUF」扫描文件夹后自动出现）";
    sel.appendChild(hintOpt);
  }

  // 2) Ollama 已安装模型（服务未运行时整组省略）
  var ollamaModels = Array.isArray(ollamaData && ollamaData.models) ? ollamaData.models : [];
  if (ollamaModels.length) {
    var g2 = mkGroup("Ollama（本地服务）");
    ollamaModels.forEach(function (m) {
      addOpt(g2, "ollama", m.name, m.name + (m.size ? " · " + formatSize(m.size) : ""));
    });
  }

  // 3) 云端视觉模型（仅图像模型下拉）
  if (kind === "vision") {
    var gv = mkGroup("云端视觉模型");
    VISION_MODELS.forEach(function (v) {
      addOpt(gv, v.provider, v.model, v.label);
    });
  }

  // 4) 各供应商推荐模型
  (provs || []).forEach(function (p) {
    if (p.key === "local" || p.key === "ollama") return;
    if (!p.has_key) return;
    if (!Array.isArray(p.models) || !p.models.length) return;
    var g = mkGroup(p.name + (p.key ? "（" + p.key + "）" : ""));
    p.models.forEach(function (m) {
      addOpt(g, p.key, m, m);
    });
  });

  // 当前配置不在列表 → 补一项保留
  if (!found && (cur.provider || cur.model)) {
    var opt = document.createElement("option");
    opt.value = cur.provider + "::" + cur.model;
    opt.textContent = "（当前：" + cur.provider + " / " + cur.model + "）";
    opt.selected = true;
    sel.appendChild(opt);
  }

  // 5) 自定义兜底
  var customOpt = document.createElement("option");
  customOpt.value = "__custom__";
  customOpt.textContent = "✎ 自定义模型…";
  sel.appendChild(customOpt);

  // 自定义行联动
  if (customProvSel && !customProvSel.dataset.filled) {
    customProvSel.innerHTML = "";
    customProvSel.appendChild(new Option("本地 GGUF（llama.cpp）", "local"));
    (provs || []).forEach(function (p) {
      if (p.key === "local" || p.key === "ollama") return;
      if (!p.has_key) return;
      customProvSel.appendChild(new Option(p.name + "（" + p.key + "）", p.key));
    });
    customProvSel.dataset.filled = "1";
  }
  sel.onchange = function () {
    var c = sel.value === "__custom__";
    if (customRow) customRow.hidden = !c;
    if (c && customModelInput) customModelInput.focus();
  };
}

/** 读取模型下拉的值 → {provider, model}（自定义时读自定义行） */
function readModelValue(kind) {
  var sel = document.getElementById("models-" + kind + "-model");
  if (!sel) return { provider: "", model: "" };
  var v = sel.value;
  if (!v) return { provider: "", model: "" };
  if (v === "__custom__") {
    var ps = document.getElementById("models-" + kind + "-custom-provider");
    var mi = document.getElementById("models-" + kind + "-custom-model");
    return {
      provider: (ps && ps.value) || "",
      model: (mi && mi.value.trim()) || ""
    };
  }
  var parts = v.split("::");
  return { provider: parts[0] || "", model: parts.slice(1).join("::") || "" };
}

/** 选择本地 GGUF 后自动加载（保存设置后调用） */
function autoLoadLocalModel(sel, providerModel, fbFn) {
  if (!sel || !sel.selectedOptions || !sel.selectedOptions[0]) return;
  var opt = sel.selectedOptions[0];
  var path = opt.dataset ? (opt.dataset.localPath || "") : "";
  if (!path) {
    fbFn("本地模型需先在「供应商 → 本地 GGUF」里扫描/加载过才能自动切换（缺少路径信息）", true);
    return;
  }
  fbFn("正在加载本地模型：" + providerModel + " …", false);
  fetch("/api/local/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: path })
  })
    .then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          throw new Error((j && j.detail) || "加载失败（HTTP " + r.status + "）");
        });
      }
      return r.json();
    })
    .then(function () {
      var t0 = Date.now();
      var timer = setInterval(function () {
        fetch("/api/local/status").then(function (r) { return r.json(); }).then(function (s) {
          if (s.status === "loading") return;
          clearInterval(timer);
          if (s.status === "ready") fbFn("本地模型已加载：" + (s.name || providerModel), false);
          else fbFn("加载失败：" + (s.error || s.status), true);
        }).catch(function () { /* 网络抖动忽略 */ });
        if (Date.now() - t0 > 240000) clearInterval(timer);
      }, 2000);
    })
    .catch(function (e) { fbFn((e && e.message) || "加载失败", true); });
}

/** 渲染 MCP 服务器列表（设置弹窗底部） */
function renderMcpList() {
  var list = document.getElementById("mcp-list");
  if (!list) return;
  list.innerHTML = "";
  var note = document.createElement("div");
  note.className = "minor-note";
  note.textContent = "连接中…";
  list.appendChild(note);

  fetch("/api/mcp/servers")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      list.innerHTML = "";
      var servers = Array.isArray(data.servers) ? data.servers : [];
      if (servers.length === 0) {
        var empty = document.createElement("div");
        empty.className = "minor-note";
        empty.textContent = "未配置 MCP 服务器（编辑 mcp.json 添加）";
        list.appendChild(empty);
        return;
      }
      servers.forEach(function (s) {
        var item = document.createElement("div");
        item.className = "mcp-item";
        item.setAttribute("role", "listitem");

        var name = document.createElement("span");
        name.className = "mcp-item-name";
        name.textContent = s.name;
        item.appendChild(name);

        var state = document.createElement("span");
        state.className = "mcp-item-state";
        if (s.connected) {
          state.classList.add("ok");
          state.textContent = "已连接 · " + s.tools.length + " 个工具";
        } else {
          state.classList.add("err");
          state.textContent = "未连接";
        }
        item.appendChild(state);
        list.appendChild(item);

        if (!s.connected && s.error) {
          var err = document.createElement("div");
          err.className = "mcp-item-error";
          err.textContent = "    " + s.error;
          list.appendChild(err);
        }
      });
    })
    .catch(function () {
      list.innerHTML = "";
      var fail = document.createElement("div");
      fail.className = "minor-note";
      fail.textContent = "无法读取 MCP 服务器列表";
      list.appendChild(fail);
    });
}

/** 渲染技能库列表（与 opencode 技能目录对齐） */
function renderSkillsList() {
  var list = document.getElementById("skills-list");
  if (!list) return;
  list.innerHTML = "";
  var note = document.createElement("div");
  note.className = "minor-note";
  note.textContent = "加载中…";
  list.appendChild(note);

  fetch("/api/skills")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      list.innerHTML = "";
      var skills = Array.isArray(data.skills) ? data.skills : [];
      if (skills.length === 0) {
        var empty = document.createElement("div");
        empty.className = "minor-note";
        empty.textContent = "未找到技能（已扫描 opencode 技能目录）";
        list.appendChild(empty);
        return;
      }
      var head = document.createElement("div");
      head.className = "minor-note";
      head.textContent = "共 " + (data.total || skills.length) + " 个技能 · 通用型时刻生效，特点型按关键词/语义激活";
      list.appendChild(head);
      // 分组：通用型（底层常驻）→ 特点型（按需激活）
      var groups = [
        { key: "generic", label: "🧩 通用型（底层技能，时刻生效）", items: [] },
        { key: "special", label: "🎯 特点型（特定关键词/语义才激活）", items: [] }
      ];
      skills.forEach(function (s) {
        (s.is_generic ? groups[0].items : groups[1].items).push(s);
      });
      groups.forEach(function (g) {
        if (!g.items.length) return;
        var ghead = document.createElement("div");
        ghead.className = "skill-group-head";
        ghead.textContent = g.label + "（" + g.items.length + "）";
        list.appendChild(ghead);
        g.items.forEach(function (s) {
          var item = document.createElement("div");
          item.className = "mcp-item";
          item.setAttribute("role", "listitem");
          var name = document.createElement("span");
          name.className = "mcp-item-name";
          name.textContent = s.name;
          item.appendChild(name);
          var desc = document.createElement("span");
          desc.className = "mcp-item-state";
          var d = s.description || "";
          desc.textContent = d.slice(0, 42) + (d.length > 42 ? "…" : "");
          item.appendChild(desc);
          list.appendChild(item);
        });
      });
    })
    .catch(function () {
      list.innerHTML = "";
      var fail = document.createElement("div");
      fail.className = "minor-note";
      fail.textContent = "无法读取技能列表";
      list.appendChild(fail);
    });
}

/** 关闭设置 */
function closeSettings() {
  if (!settingsOpen) return;
  settingsOpen = false;
  els.settingsModal.hidden = true;
  els.settingsList.innerHTML = "";
  els.settingsBtn.focus();                          // 焦点还给齿轮按钮
}

/** 打开时拉取最新 has_key 并渲染行（不改变当前选中的供应商） */
function fetchSettings() {
  fetch("/api/providers")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      var list = Array.isArray(data.providers) ? data.providers : [];
      if (list.length === 0) throw new Error("没有可用供应商");
      settingsProviders = list;
      list.forEach(function (np) {                  // 同步进下拉数据，保持对象引用不变
        for (var i = 0; i < state.providers.length; i++) {
          if (state.providers[i].key === np.key) {
            state.providers[i].has_key = np.has_key;
            break;
          }
        }
      });
      renderSettingsRows();
      refreshProviderBadges();
      // “加载本地模型”入口：打开后自动定位到本地 GGUF 行并聚焦文件夹输入
      if (settingsFocusLocal) {
        settingsFocusLocal = false;
        var localRow = [...els.settingsList.querySelectorAll(".settings-row")].find(function (r) {
          var n = r.querySelector(".settings-name");
          return n && n.textContent.indexOf("llama.cpp") !== -1;
        });
        if (localRow) {
          localRow.scrollIntoView({ block: "center" });
          var folderInput = localRow.querySelector(".folder-path");
          if (folderInput) setTimeout(function () { folderInput.focus(); }, 200);
        }
      }
    })
    .catch(function () {
      var note = document.createElement("div");
      note.className = "minor-note";
      note.textContent = "无法读取供应商列表，请确认服务已启动";
      els.settingsList.appendChild(note);
    });
}

function renderSettingsRows() {
  els.settingsList.innerHTML = "";
  settingsProviders.forEach(function (p) {
    var row = buildSettingsRow(p);
    els.settingsList.appendChild(row);
    if (p.key === "local") fetchLocalStatus(row);
  });
}

/** 构建一行：名称 + 模型 + 状态徽标 + 输入区。远程行有 Key + 模型两行输入；local 行是 GGUF 加载区 */
function buildSettingsRow(p) {
  var row = document.createElement("div");
  row.className = "settings-row";
  row.setAttribute("role", "listitem");
  row.dataset.provider = p.key;

  var head = document.createElement("div");
  head.className = "settings-row-head";

  var name = document.createElement("span");
  name.className = "settings-name";
  name.textContent = p.name;

  var model = document.createElement("span");
  model.className = "settings-model";
  model.textContent = p.model;

  var badge = document.createElement("span");
  badge.className = "settings-badge";
  var badgeDot = document.createElement("span");
  badgeDot.className = "settings-badge-dot";
  badgeDot.setAttribute("aria-hidden", "true");
  var badgeText = document.createElement("span");
  badgeText.className = "settings-badge-text";

  badge.appendChild(badgeDot);
  badge.appendChild(badgeText);
  head.appendChild(name);
  head.appendChild(model);
  head.appendChild(badge);

  // 本地 GGUF（llama.cpp）：无需 Key，整行就是加载区
  if (p.key === "local") {
    badgeDot.hidden = false;
    badgeText.textContent = "已配置";
    row.appendChild(head);
    row.appendChild(buildLocalSection());
    return row;
  }

  // Ollama 未配置 Key 时视为"无需 Key 的本地服务"，隐藏 Key 输入（模型选择仍在）
  var isOllamaNoKey = p.key === "ollama" && !p.has_key;
  row.classList.toggle("is-special", isOllamaNoKey);
  badgeDot.hidden = !p.has_key;
  badgeText.textContent = p.has_key ? "已配置" : (isOllamaNoKey ? "无需 Key（本地模型）" : "未配置");

  var actions = document.createElement("div");
  actions.className = "settings-actions";

  var wrap = document.createElement("div");
  wrap.className = "settings-input-wrap";

  var input = document.createElement("input");
  input.type = "password";
  input.className = "settings-input";
  input.placeholder = "粘贴 API Key，留空并保存可清除";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.setAttribute("aria-label", p.name + " 的 API Key");

  var eye = document.createElement("button");
  eye.type = "button";
  eye.className = "settings-eye";
  eye.innerHTML = EYE_ON_SVG + EYE_OFF_SVG;
  eye.setAttribute("aria-label", "显示密钥");
  eye.setAttribute("aria-pressed", "false");
  eye.addEventListener("click", function () {
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    eye.classList.toggle("is-visible", show);
    eye.setAttribute("aria-label", show ? "隐藏密钥" : "显示密钥");
    eye.setAttribute("aria-pressed", String(show));
    input.focus();
  });

  wrap.appendChild(input);
  wrap.appendChild(eye);

  var save = document.createElement("button");
  save.type = "button";
  save.className = "settings-save";
  save.textContent = "保存";
  save.addEventListener("click", function () {
    saveProviderKey(p.key, input.value, row, save);
  });

  actions.appendChild(wrap);
  actions.appendChild(save);

  var feedback = document.createElement("div");
  feedback.className = "settings-feedback";
  feedback.setAttribute("role", "status");
  feedback.setAttribute("aria-live", "polite");

  row.appendChild(head);
  row.appendChild(actions);
  row.appendChild(buildModelLine(p));
  row.appendChild(feedback);
  if (window.WenmoPricing) window.WenmoPricing.attach(actions, p, feedback);
  return row;
}

/** 往模型下拉框里填选项：当前模型在前，末尾"自定义"；返回是否处于自定义态 */
function fillModelSelect(select, models, current) {
  select.innerHTML = "";
  var list = Array.isArray(models) ? models : [];
  if (current && list.indexOf(current) === -1) {
    var co = document.createElement("option");
    co.value = current;
    co.textContent = "当前：" + current;
    select.appendChild(co);
  }
  list.forEach(function (m) {
    var opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    select.appendChild(opt);
  });
  var customOpt = document.createElement("option");
  customOpt.value = "__custom__";
  customOpt.textContent = "✎ 自定义模型…";
  select.appendChild(customOpt);
  if (current && list.indexOf(current) !== -1) select.value = current;
  else if (current) select.value = "__custom__";
  return select.value === "__custom__";
}

/** 模型选择行：下拉选择（推荐列表）+ 自定义兜底；ollama 额外带"刷新"按钮 */
function buildModelLine(p) {
  var line = document.createElement("div");
  line.className = "settings-model-line";

  var select = document.createElement("select");
  select.className = "settings-model-select";
  select.setAttribute("aria-label", p.name + " 的模型");
  var isCustom = fillModelSelect(select, p.models, p.model || "");
  line.appendChild(select);

  var customWrap = document.createElement("div");
  customWrap.className = "settings-model-custom";
  customWrap.hidden = !isCustom;
  var customInput = document.createElement("input");
  customInput.type = "text";
  customInput.className = "settings-model-custom-input";
  customInput.value = isCustom ? (p.model || "") : "";
  customInput.placeholder = "输入自定义模型 ID";
  customInput.spellcheck = false;
  customWrap.appendChild(customInput);
  line.appendChild(customWrap);

  select.addEventListener("change", function () {
    var c = select.value === "__custom__";
    customWrap.hidden = !c;
    if (c && !customInput.value) customInput.focus();
  });

  if (p.key === "ollama") {
    var refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "settings-model-refresh";
    refresh.textContent = "刷新";
    refresh.setAttribute("aria-label", "刷新 Ollama 模型列表");
    refresh.addEventListener("click", function () {
      refreshOllamaModels(refresh);
    });
    line.appendChild(refresh);
  }

  return line;
}

/** GET /api/ollama/models → 重填下拉选项；反馈"已刷新 N 个模型"或服务端错误 */
function refreshOllamaModels(btn) {
  var row = btn.closest(".settings-row");
  var select = row.querySelector(".settings-model-select");
  var customWrap = row.querySelector(".settings-model-custom");
  var orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "刷新中…";

  fetch("/api/ollama/models")
    .then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (j) {
          throw new Error((j && j.detail) || "刷新失败（HTTP " + res.status + "）");
        });
      }
      return res.json();
    })
    .then(function (data) {
      var models = Array.isArray(data.models) ? data.models : [];
      var names = models.map(function (m) { return m && m.name ? m.name : String(m); });
      var current = select ? select.value : "";
      var isCustom = select ? fillModelSelect(select, names, current === "__custom__" ? "" : current) : false;
      if (customWrap) customWrap.hidden = !isCustom;
      setRowFeedback(row, "已刷新 " + names.length + " 个模型", false);
    })
    .catch(function (err) {
      setRowFeedback(row, (err && err.message) || "刷新失败", true);
    })
    .then(function () {
      btn.disabled = false;
      btn.textContent = orig;
    });
}

/** 本地 GGUF 行：说明 + 状态行 + 路径输入 + 加载/停止按钮 + 反馈 */
function buildLocalSection() {
  var sec = document.createElement("div");
  sec.className = "import-section";

  var hint = document.createElement("div");
  hint.className = "import-hint";
  hint.textContent = "直接加载本地 GGUF 模型（llama.cpp，非 Ollama）。输入 .gguf 文件的完整路径后点击“加载”，大模型可能需要几分钟。";
  sec.appendChild(hint);

  var status = document.createElement("div");
  status.className = "local-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.textContent = "未加载模型";
  sec.appendChild(status);

  // 文件夹扫描行：输入文件夹路径 → 扫描出 .gguf 列表，点击即可加载
  var folderControls = document.createElement("div");
  folderControls.className = "folder-controls";

  var folder = document.createElement("input");
  folder.type = "text";
  folder.id = "folder-path";
  folder.className = "folder-path";
  folder.placeholder = "输入模型文件夹路径，例如 F:/AIModel/big_model";
  folder.autocomplete = "off";
  folder.spellcheck = false;
  folder.setAttribute("aria-label", "模型文件夹路径");
  // 上次成功扫描过的文件夹预填（settingsProviders 每次打开弹窗都会重建）
  var lastFolder = null;
  try { lastFolder = localStorage.getItem("last_local_folder"); } catch (e) { /* 隐私模式可能抛错 */ }
  if (lastFolder) folder.value = lastFolder;
  folderControls.appendChild(folder);

  var scanBtn = document.createElement("button");
  scanBtn.type = "button";
  scanBtn.className = "scan-btn";
  scanBtn.textContent = "扫描";
  scanBtn.setAttribute("aria-label", "扫描模型文件夹");
  folderControls.appendChild(scanBtn);

  sec.appendChild(folderControls);

  // 扫描结果列表：每次扫描重建（role=list，行 role=listitem）
  var scanResults = document.createElement("div");
  scanResults.className = "scan-results";
  scanResults.setAttribute("role", "list");
  scanResults.setAttribute("aria-label", "文件夹中的模型列表");
  sec.appendChild(scanResults);

  var controls = document.createElement("div");
  controls.className = "import-controls";

  var path = document.createElement("input");
  path.type = "text";
  path.id = "import-path";
  path.className = "import-path";
  path.placeholder = "输入 .gguf 文件的完整路径，例如 F:/AIModel/xx/yy.gguf";
  path.autocomplete = "off";
  path.spellcheck = false;
  path.setAttribute("aria-label", "GGUF 文件路径");
  controls.appendChild(path);

  var loadBtn = document.createElement("button");
  loadBtn.type = "button";
  loadBtn.id = "import-btn";
  loadBtn.className = "import-btn local-load-btn";
  loadBtn.textContent = "加载";
  loadBtn.setAttribute("aria-label", "加载本地 GGUF 模型");
  controls.appendChild(loadBtn);

  var stopBtn = document.createElement("button");
  stopBtn.type = "button";
  stopBtn.className = "local-stop-btn";
  stopBtn.textContent = "停止";
  stopBtn.hidden = true;
  stopBtn.setAttribute("aria-label", "停止加载本地模型");
  controls.appendChild(stopBtn);

  sec.appendChild(controls);

  var fb = document.createElement("div");
  fb.id = "import-feedback";
  fb.className = "import-feedback";
  fb.setAttribute("role", "status");
  fb.setAttribute("aria-live", "polite");
  sec.appendChild(fb);

  scanBtn.addEventListener("click", function () {
    scanLocalFolder(sec, scanBtn);
  });
  loadBtn.addEventListener("click", function () {
    startLocalLoad(sec, loadBtn, stopBtn);
  });
  stopBtn.addEventListener("click", function () {
    stopLocalLoad(sec, loadBtn, stopBtn);
  });

  return sec;
}

/* ---------- 本地 GGUF 加载：状态 / 加载 / 轮询 / 停止 ---------- */

var localPollTimer = null;      // 2s 轮询定时器
var localPollDeadline = 0;      // 轮询超时时间戳（约 4 分钟）

/** 更新本地行状态行文本与错误态 */
function setLocalStatusEl(container, text, isError) {
  var el = container.querySelector(".local-status");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("error", Boolean(isError));
}

/** 本地行加载反馈："已加载/超时"短暂显示；错误保持到下一次操作 */
function setImportFeedback(container, text, isError) {
  var fb = container.querySelector(".import-feedback");
  if (!fb) return;
  if (fb._clearTimer) { clearTimeout(fb._clearTimer); fb._clearTimer = null; }
  fb.textContent = text;
  fb.classList.toggle("error", Boolean(isError));
  if (!isError && text) {
    fb._clearTimer = setTimeout(function () {
      fb.textContent = "";
      fb._clearTimer = null;
    }, 2600);
  }
}

/** 打开弹窗时按真实状态初始化本地行；若服务端正在加载则续上轮询 */
function fetchLocalStatus(container) {
  var loadBtn = container.querySelector(".local-load-btn");
  var stopBtn = container.querySelector(".local-stop-btn");
  fetch("/api/local/status")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      var status = data && data.status;
      if (status === "ready") {
        setLocalStatusEl(container, "已加载：" + ((data && data.name) || "模型"), false);
        stopBtn.hidden = false;
      } else if (status === "loading") {
        setLocalStatusEl(container, "正在加载…（大模型可能需要几分钟）", false);
        stopBtn.hidden = false;
        if (loadBtn) setLocalLoadDisabled(container, loadBtn, true);
        beginLocalPoll(container, loadBtn, stopBtn);
      } else if (status === "error") {
        setLocalStatusEl(container, (data && data.error) || "加载失败", true);
        stopBtn.hidden = true;
      } else {
        setLocalStatusEl(container, "未加载模型", false);
        stopBtn.hidden = true;
      }
    })
    .catch(function () { /* 服务不可达：保持默认"未加载模型" */ });
}

/** POST /api/local/load 后进入轮询，直到状态离开 loading（约 4 分钟超时） */
function startLocalLoad(container, loadBtn, stopBtn) {
  var path = container.querySelector(".import-path");
  loadLocalModelByPath(container, path && path.value, loadBtn, stopBtn);
}

/**
 * 共享加载入口：直接路径输入框的“加载”按钮与文件夹扫描列表的行都走这里。
 * 加载期间禁用本行所有加载按钮；服务端错误明细显示在 #import-feedback。
 */
function loadLocalModelByPath(container, path, loadBtn, stopBtn) {
  var p = String(path || "").trim();
  if (!p) {
    setImportFeedback(container, "请输入 .gguf 文件的完整路径", true);
    var input = container.querySelector(".import-path");
    if (input) input.focus();
    return;
  }
  setLocalLoadDisabled(container, loadBtn, true);
  stopBtn.hidden = true;
  setLocalStatusEl(container, "正在加载…（大模型可能需要几分钟）", false);
  setImportFeedback(container, "", false);

  fetch("/api/local/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: p })
  })
    .then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (j) {
          throw new Error((j && j.detail) || "加载失败（HTTP " + res.status + "）");
        });
      }
      return res.json();
    })
    .then(function () {
      stopBtn.hidden = false;
      beginLocalPoll(container, loadBtn, stopBtn);
    })
    .catch(function (err) {
      setLocalLoadDisabled(container, loadBtn, false);
      stopBtn.hidden = true;
      setLocalStatusEl(container, "未加载模型", false);
      setImportFeedback(container, (err && err.message) || "加载失败", true);
    });
}

/** 加载期间禁用/恢复本行所有加载按钮（直接路径“加载” + 扫描列表各行的“加载”） */
function setLocalLoadDisabled(container, loadBtn, disabled) {
  if (loadBtn) loadBtn.disabled = disabled;
  container.querySelectorAll(".scan-load").forEach(function (b) { b.disabled = disabled; });
}

/* ---------- 本地 GGUF 文件夹扫描 ---------- */

/** 扫描文件夹：POST /api/local/scan → 渲染可点击的结果列表；记住上次文件夹 */
function scanLocalFolder(container, scanBtn) {
  var folder = container.querySelector(".folder-path");
  var p = (folder && folder.value || "").trim();
  if (!p) {
    setImportFeedback(container, "请输入模型文件夹路径", true);
    if (folder) folder.focus();
    return;
  }
  var orig = scanBtn.textContent;
  scanBtn.disabled = true;
  scanBtn.textContent = "扫描中…";
  setImportFeedback(container, "", false);

  fetch("/api/local/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: p })
  })
    .then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (j) {
          throw new Error((j && j.detail) || "扫描失败（HTTP " + res.status + "）");
        });
      }
      return res.json();
    })
    .then(function (data) {
      try { localStorage.setItem("last_local_folder", p); } catch (e) { /* 隐私模式可能抛错 */ }
      renderScanResults(container, data);
    })
    .catch(function (err) {
      setImportFeedback(container, (err && err.message) || "扫描失败", true);
    })
    .then(function () {
      scanBtn.disabled = false;
      scanBtn.textContent = orig;
    });
}

/**
 * 渲染扫描结果列表（role=list / 行 role=listitem）。
 * model 行可点击加载（含“加载”小按钮）；mmproj 行仅展示，不可单独加载。
 * 文件名一律 textContent 写入，绝不 innerHTML。
 */
function renderScanResults(container, data) {
  var list = container.querySelector(".scan-results");
  if (!list) return;
  list.innerHTML = "";
  var files = Array.isArray(data && data.files) ? data.files : [];
  if (files.length === 0) {
    var empty = document.createElement("div");
    empty.className = "scan-empty";
    empty.textContent = "没有找到 .gguf 文件";
    list.appendChild(empty);
    return;
  }
  files.forEach(function (f) {
    var row = document.createElement("div");
    row.className = "scan-row";
    row.setAttribute("role", "listitem");

    var name = document.createElement("span");
    name.className = "scan-name";
    name.textContent = f.name || f.path || "";
    row.appendChild(name);

    if (f.kind === "mmproj") {
      row.classList.add("is-mmproj");
      var tag = document.createElement("span");
      tag.className = "scan-tag";
      tag.textContent = "图像投影（需搭配文本模型）";
      row.appendChild(tag);
      row.addEventListener("click", function () {
        setImportFeedback(container, "该文件是图像投影，需搭配文本模型使用，请选择对应的 .gguf 模型", false);
      });
    } else {
      var meta = document.createElement("span");
      meta.className = "scan-meta";
      var size = document.createElement("span");
      size.className = "scan-size";
      size.textContent = formatSize(f.size);
      meta.appendChild(size);
      if (f.mmproj) {
        var hasVision = document.createElement("span");
        hasVision.className = "scan-tag";
        hasVision.textContent = "(含图像能力)";
        meta.appendChild(hasVision);
      }
      row.appendChild(meta);

      var loadBtn = document.createElement("button");
      loadBtn.type = "button";
      loadBtn.className = "scan-load";
      loadBtn.textContent = "加载";
      loadBtn.setAttribute("aria-label", "加载模型：" + (f.name || f.path || ""));
      loadBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        loadLocalModelByPath(container, f.path, container.querySelector(".local-load-btn"), container.querySelector(".local-stop-btn"));
      });
      row.appendChild(loadBtn);

      row.addEventListener("click", function () {
        loadLocalModelByPath(container, f.path, container.querySelector(".local-load-btn"), container.querySelector(".local-stop-btn"));
      });
    }
    list.appendChild(row);
  });
}

/** 人类可读的文件大小（GB/MB，1 位小数）；非法输入返回空串 */
function formatSize(bytes) {
  var n = Number(bytes);
  if (!isFinite(n) || n <= 0) return "";
  var gb = n / (1024 * 1024 * 1024);
  if (gb >= 1) return gb.toFixed(1) + " GB";
  return (n / (1024 * 1024)).toFixed(1) + " MB";
}

function beginLocalPoll(container, loadBtn, stopBtn) {
  stopLocalPoll();
  localPollDeadline = Date.now() + 4 * 60 * 1000;
  localPollTimer = setInterval(function () {
    pollLocalTick(container, loadBtn, stopBtn);
  }, 2000);
  pollLocalTick(container, loadBtn, stopBtn);
}

function stopLocalPoll() {
  if (localPollTimer) { clearInterval(localPollTimer); localPollTimer = null; }
}

function pollLocalTick(container, loadBtn, stopBtn) {
  if (Date.now() > localPollDeadline) {
    stopLocalPoll();
    setLocalLoadDisabled(container, loadBtn, false);
    stopBtn.hidden = true;
    setLocalStatusEl(container, "加载超时，请查看 local_server.log", true);
    setImportFeedback(container, "", false);
    return;
  }
  fetch("/api/local/status")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      var status = data && data.status;
      if (status === "ready") {
        stopLocalPoll();
        setLocalLoadDisabled(container, loadBtn, false);
        stopBtn.hidden = false;
        var name = (data && data.name) || "模型";
        setLocalStatusEl(container, "已加载：" + name, false);
        setImportFeedback(container, "已加载：" + name, false);
        afterLocalLoaded(name);
      } else if (status === "error") {
        stopLocalPoll();
        setLocalLoadDisabled(container, loadBtn, false);
        stopBtn.hidden = true;
        var msg = (data && data.error) || "加载失败";
        setLocalStatusEl(container, msg, true);
        setImportFeedback(container, msg, true);
      } else if (status === "loading") {
        setLocalStatusEl(container, "正在加载…（大模型可能需要几分钟）", false);
        stopBtn.hidden = false;
        setLocalLoadDisabled(container, loadBtn, true);
      } else {
        stopLocalPoll();
        setLocalLoadDisabled(container, loadBtn, false);
        stopBtn.hidden = true;
        setLocalStatusEl(container, "未加载模型", false);
      }
    })
    .catch(function () { /* 网络抖动：下一轮继续 */ });
}

/** POST /api/local/stop：停止加载 / 卸载模型 */
function stopLocalLoad(container, loadBtn, stopBtn) {
  stopLocalPoll();
  setLocalLoadDisabled(container, loadBtn, true);
  setLocalStatusEl(container, "正在停止…", false);
  fetch("/api/local/stop", { method: "POST" })
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function () {
      setLocalLoadDisabled(container, loadBtn, false);
      stopBtn.hidden = true;
      setLocalStatusEl(container, "未加载模型", false);
      setImportFeedback(container, "已停止", false);
    })
    .catch(function () {
      setLocalLoadDisabled(container, loadBtn, false);
      fetchLocalStatus(container);
    });
}

/** 本地模型加载完成后：刷新 /api/providers、同步显示、自动选中 local 供应商 */
function afterLocalLoaded(name) {
  fetch("/api/providers")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      var list = Array.isArray(data.providers) ? data.providers : [];
      if (list.length === 0) return;
      state.providers = list;
      list.forEach(function (np) {                 // 同步弹窗快照
        for (var i = 0; i < settingsProviders.length; i++) {
          if (settingsProviders[i].key === np.key) {
            settingsProviders[i].model = np.model;
            settingsProviders[i].has_key = np.has_key;
            break;
          }
        }
      });
      // 顶部栏：若当前正是 local，跟随新模型名
      if (state.activeProvider && state.activeProvider.key === "local") {
        state.activeProvider.model = name;
        els.triggerModel.textContent = name;
      }
      renderProviderMenu();
      refreshProviderBadges();
      // 弹窗里 local 行的模型显示
      els.settingsList.querySelectorAll(".settings-row").forEach(function (r) {
        if (r.dataset.provider === "local") {
          var m = r.querySelector(".settings-model");
          if (m) m.textContent = name;
        }
      });
      // 自动选中 local，让用户能立刻开聊
      var localP = null;
      list.forEach(function (p) { if (p.key === "local") localP = p; });
      if (localP) selectProvider(localP);
    })
    .catch(function () { /* 忽略：模型名以轮询返回为准 */ });
}

/** POST /api/settings：保存/清除某供应商的 key 与模型（空串 key = 清除；空模型 = 不改） */
function saveProviderKey(key, apiKey, row, saveBtn) {
  saveBtn.disabled = true;
  setRowFeedback(row, "", false);

  // 模型取值：下拉选中项；若为"自定义"，取自定义输入框的值
  var modelSelect = row.querySelector(".settings-model-select");
  var modelCustom = row.querySelector(".settings-model-custom-input");
  var model = "";
  if (modelSelect) {
    model = modelSelect.value === "__custom__"
      ? (modelCustom ? modelCustom.value.trim() : "")
      : modelSelect.value;
  }

  fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: key, api_key: apiKey, model: model })
  })
    .then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (j) {
          throw new Error((j && j.detail) || "保存失败（HTTP " + res.status + "）");
        });
      }
      return res.json();
    })
    .then(function (data) {
      var hasKey = Boolean(data && data.has_key);
      for (var i = 0; i < state.providers.length; i++) {      // 同步下拉数据
        if (state.providers[i].key === key) {
          state.providers[i].has_key = hasKey;
          if (model) state.providers[i].model = model;
          break;
        }
      }
      for (var j = 0; j < settingsProviders.length; j++) {    // 同步弹窗快照
        if (settingsProviders[j].key === key) {
          settingsProviders[j].has_key = hasKey;
          if (model) settingsProviders[j].model = model;
          break;
        }
      }
      updateRowBadge(row, hasKey);
      if (model) {
        var headModel = row.querySelector(".settings-model");
        if (headModel) headModel.textContent = model;
        if (state.activeProvider && state.activeProvider.key === key) {
          state.activeProvider.model = model;
          els.triggerModel.textContent = model;
        }
        els.menu.querySelectorAll(".provider-option").forEach(function (o) {
          var p = state.providers[+o.dataset.index];
          if (p && p.key === key) {
            var om = o.querySelector(".option-model");
            if (om) om.textContent = model;
          }
        });
      }
      setRowFeedback(row, "已保存", false);
      refreshProviderBadges();                                 // 下拉里的"已配置"徽标跟着刷新
    })
    .catch(function (err) {
      setRowFeedback(row, (err && err.message) || "保存失败", true);
    })
    .then(function () {
      saveBtn.disabled = false;
    });
}

/** 更新单行徽标（已配置 / 未配置 / 本地无需 Key） */
function updateRowBadge(row, hasKey) {
  var isLocal = row.dataset.provider === "ollama";
  row.classList.toggle("is-special", !hasKey && isLocal);
  var dot = row.querySelector(".settings-badge-dot");
  var text = row.querySelector(".settings-badge-text");
  if (!dot || !text) return;
  dot.hidden = !hasKey;
  text.textContent = hasKey ? "已配置" : (isLocal ? "无需 Key（本地模型）" : "未配置");
}

/** 行内反馈："已保存"短暂显示后消失；错误保持到下一次操作 */
function setRowFeedback(row, text, isError) {
  var fb = row.querySelector(".settings-feedback");
  if (!fb) return;
  if (fb._clearTimer) { clearTimeout(fb._clearTimer); fb._clearTimer = null; }
  fb.textContent = text;
  fb.classList.toggle("error", isError);
  if (!isError && text) {
    fb._clearTimer = setTimeout(function () {
      fb.textContent = "";
      fb._clearTimer = null;
    }, 2200);
  }
}

/** 极简焦点圈：Tab 到弹窗首尾时回绕，焦点不跑出弹窗 */
function trapSettingsFocus(e) {
  if (e.key !== "Tab") return;
  var panel = els.settingsModal.querySelector(".modal-panel");
  var focusables = panel.querySelectorAll("button, input, [tabindex]:not([tabindex='-1'])");
  if (focusables.length === 0) return;
  var first = focusables[0];
  var last = focusables[focusables.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

/* ===========================================================================
 * 事件绑定 & 初始化
 * ========================================================================= */

function bindEvents() {
  // 发送 / 停止
  els.sendBtn.addEventListener("click", function () {
    if (currentConvStreaming()) stopStreaming();
    else sendMessage();
  });

  // 联网状态：自动检测（不再手动开关）——电脑联网则默认联网，断网则默认未联网
  // 由 initOnlineStatus() 统一初始化 + 监听 online/offline 事件
  // 深度思考（轻/中/深，默认中）：点击弹出下拉选择
  var reasoningBtn = document.getElementById("reasoning-btn");
  var REASONING_LEVELS = [
    { key: "low", label: "轻", hint: "快但浅，适合简单问答" },
    { key: "medium", label: "中", hint: "均衡，默认推荐" },
    { key: "high", label: "深", hint: "推理强但慢，适合难题" }
  ];
  var reasoningLabel = function (key) {
    var l = REASONING_LEVELS.find(function (x) { return x.key === key; });
    return l ? l.label : "中";
  };
  if (reasoningBtn) {
    reasoningBtn.addEventListener("click", function () {
      var old = document.getElementById("reasoning-menu");
      if (old) { old.remove(); return; }
      var menu = document.createElement("div");
      menu.id = "reasoning-menu";
      menu.className = "command-menu";
      // fixed 定位 + body 挂载：避免被消息区 stacking context 遮挡
      var r = reasoningBtn.getBoundingClientRect();
      menu.style.position = "fixed";
      menu.style.top = "auto";
      menu.style.bottom = (window.innerHeight - r.top + 6) + "px";
      menu.style.left = Math.max(8, r.left - 40) + "px";
      menu.style.width = "240px";
      REASONING_LEVELS.forEach(function (l) {
        var item = document.createElement("div");
        item.className = "command-menu-item" + (l.key === state.reasoning ? " active" : "");
        item.innerHTML = '<span class="cm-cmd">' + l.label + '</span><span class="cm-hint">' + l.hint + '</span>';
        item.addEventListener("click", function () {
          menu.remove();
          state.reasoning = l.key;
          reasoningBtn.textContent = "🧠 思考：" + reasoningLabel(l.key);
        });
        menu.appendChild(item);
      });
      document.body.appendChild(menu);
      setTimeout(function () {
        document.addEventListener("click", function (e) {
          if (menu.isConnected && !menu.contains(e.target) && e.target !== reasoningBtn) menu.remove();
        }, { once: true });
      }, 0);
    });
  }
  // 模型对比模式：同一个问题让两个模型分别回答（两级下拉：供应商 → 模型）
  var compareBtn = document.getElementById("compare-btn");
  state.compare = null;   // {provider, model, label}
  if (compareBtn) {
    compareBtn.addEventListener("click", function () {
      if (state.compare) {
        state.compare = null;
        compareBtn.textContent = "⚖️ 对比";
        compareBtn.classList.remove("on");
        return;
      }
      openComparePicker();
    });
  }

  /** 对比模型选择面板：供应商下拉 + 模型下拉（联动） */
  function openComparePicker() {
    var old = document.getElementById("compare-picker");
    if (old) old.remove();
    // 候选供应商：本地（若就绪）+ ollama（若可用）+ 有 key 的远端供应商
    var provs = [];
    if (state.localStatus === "ready" && state.localModel) {
      provs.push({ key: "local", name: "本地 · " + state.localModel.slice(0, 20), models: [state.localModel] });
    }
    (state.providers || []).forEach(function (p) {
      if (p.key === "local") return;
      if (p.key === "ollama") {
        if (p.has_key) provs.push({ key: "ollama", name: "本地 Ollama", models: (p.models && p.models.length ? p.models : [p.model]) });
        return;
      }
      if (p.has_key) {
        provs.push({ key: p.key, name: p.name, models: (p.models && p.models.length ? p.models : [p.model]) });
      }
    });
    if (!provs.length) { showErrorNote("没有可用作对比的供应商（需要先配置 API Key）"); return; }
    var panel = document.createElement("div");
    panel.id = "compare-picker";
    panel.className = "command-menu cmp-menu";
    // 紧凑圆角弹窗（与思考模式切换同风格）：头部小标题 + 下拉行 + 底部操作
    var html = '<div class="cmp-head"><span class="cmp-title">🎭 模型对比</span>'
             + '<span class="cmp-sub">两模型分别回答同一问题</span></div>';
    html += '<div class="cmp-row"><label class="cmp-label">供应商</label>'
          + '<select id="cmp-provider" class="cmp-select"></select></div>';
    html += '<div class="cmp-row"><label class="cmp-label">模型</label>'
          + '<select id="cmp-model" class="cmp-select"></select></div>';
    html += '<div class="cmp-actions"><button type="button" id="cmp-cancel" class="cmp-btn cmp-cancel">取消</button>'
          + '<button type="button" id="cmp-ok" class="cmp-btn cmp-ok">确定对比</button></div>';
    panel.innerHTML = html;
    // 供应商选项填充
    var provSel = panel.querySelector("#cmp-provider");
    provs.forEach(function (p, i) {
      var o = document.createElement("option");
      o.value = p.key;
      o.textContent = p.name;
      if (i === 0) o.selected = true;
      provSel.appendChild(o);
    });
    document.body.appendChild(panel);
    var r = compareBtn.getBoundingClientRect();
    panel.style.position = "fixed";
    panel.style.top = "auto";
    panel.style.bottom = (window.innerHeight - r.top + 8) + "px";
    panel.style.left = Math.max(8, r.left - 110) + "px";
    // 供应商 → 模型联动
    function fillModels(provKey) {
      var p = provs.filter(function (x) { return x.key === provKey; })[0];
      var sel = panel.querySelector("#cmp-model");
      sel.innerHTML = "";
      (p.models || []).forEach(function (m) {
        var o = document.createElement("option");
        o.value = m;
        o.textContent = m;
        sel.appendChild(o);
      });
    }
    var provSel = panel.querySelector("#cmp-provider");
    fillModels(provSel.value);
    provSel.addEventListener("change", function () { fillModels(provSel.value); });
    panel.querySelector("#cmp-ok").addEventListener("click", function () {
      var pk = provSel.value;
      var pm = panel.querySelector("#cmp-model").value;
      var prov = provs.filter(function (x) { return x.key === pk; })[0];
      var label = prov.name + " · " + pm;
      state.compare = { provider: pk, model: pm, label: label };
      compareBtn.textContent = "⚖️ vs " + (label.length > 14 ? label.slice(0, 14) + "…" : label);
      compareBtn.classList.add("on");
      showInfoNote("对比模式已开启：发送后当前模型与「" + label + "」将分别回答");
      panel.remove();
    });
    panel.querySelector("#cmp-cancel").addEventListener("click", function () { panel.remove(); });
    setTimeout(function () {
      document.addEventListener("click", function (e) {
        if (panel.isConnected && !panel.contains(e.target) && e.target !== compareBtn) panel.remove();
      }, { once: true });
    }, 0);
  }

  // 语音输入（Web Speech API，中文识别）
  var micBtn = document.getElementById("mic-btn");
  var micRec = null;
  var micOn = false;
  if (micBtn) {
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    micBtn.addEventListener("click", function () {
      if (!SR) { showErrorNote("当前浏览器不支持语音识别（建议用 Edge/Chrome）"); return; }
      if (micOn) {
        micRec.stop();
        micOn = false;
        micBtn.classList.remove("on");
        return;
      }
      micRec = new SR();
      micRec.lang = "zh-CN";
      micRec.continuous = true;
      micRec.interimResults = true;
      micRec.onresult = function (e) {
        var text = "";
        for (var i = e.resultIndex; i < e.results.length; i++) {
          if (e.results[i][0]) text += e.results[i][0].transcript;
        }
        els.input.value = text;
        autoResizeInput();
        updateSendBtn();
      };
      micRec.onend = function () { micOn = false; micBtn.classList.remove("on"); };
      micRec.onerror = function (e) {
        micOn = false;
        micBtn.classList.remove("on");
        if (e.error !== "aborted") showErrorNote("语音识别失败：" + (e.error || "未知"));
      };
      try { micRec.start(); micOn = true; micBtn.classList.add("on"); }
      catch (e) { showErrorNote("无法启动语音识别（需要麦克风权限）"); }
    });
  }

  // 输入
  els.input.addEventListener("input", function () {
    autoResizeInput();
    updateSendBtn();
  });
  els.input.addEventListener("keydown", function (e) {
    // isComposing：中文输入法选词回车不应触发发送
    if (e.isComposing) return;
    var mode = getPref("send_mode", "enter");
    var isSend = mode === "ctrlenter"
      ? (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.shiftKey)
      : (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.shiftKey);
    if (isSend) {
      e.preventDefault();
      sendMessage();
    }
  });

  // 滚动
  els.messages.addEventListener("scroll", onMessagesScroll, { passive: true });
  els.backPill.addEventListener("click", function () {
    scrollToBottom(true);
  });

  // 主题
  els.themeToggle.addEventListener("click", toggleTheme);

  // 设置弹窗：打开 / 关闭 / 点遮罩关闭 / 焦点圈
  els.settingsBtn.addEventListener("click", openSettings);
  els.settingsClose.addEventListener("click", closeSettings);
  // 设置分类标签页
  document.querySelectorAll(".modal-tab").forEach(function (tab) {
    tab.addEventListener("click", function () { switchTab(tab.dataset.tab); });
  });
  var mcpRefreshBtn = document.getElementById("mcp-refresh-btn");
  if (mcpRefreshBtn) {
    mcpRefreshBtn.addEventListener("click", function () {
      mcpRefreshBtn.disabled = true;
      fetch("/api/mcp/refresh", { method: "POST" })
        .then(function () { renderMcpList(); })
        .catch(function () { renderMcpList(); })
        .then(function () { mcpRefreshBtn.disabled = false; });
    });
  }
  var skillsRefreshBtn = document.getElementById("skills-refresh-btn");
  if (skillsRefreshBtn) {
    skillsRefreshBtn.addEventListener("click", function () {
      skillsRefreshBtn.disabled = true;
      renderSkillsList();
      skillsRefreshBtn.disabled = false;
    });
  }
  var pluginsRefreshBtn = document.getElementById("plugins-refresh-btn");
  if (pluginsRefreshBtn) {
    pluginsRefreshBtn.addEventListener("click", function () {
      pluginsRefreshBtn.disabled = true;
      renderPluginsList();
      pluginsRefreshBtn.disabled = false;
    });
  }
  var newChatBtn = els.newChatBtn;
  if (newChatBtn) {
    newChatBtn.addEventListener("click", newChat);
  }
  // 导出当前对话
  var exportBtn = document.getElementById("export-btn");
  if (exportBtn) {
    exportBtn.addEventListener("click", exportConversation);
  }
  // 斜杠命令：输入 / 弹出命令菜单（对标 opencode /commands）
  if (els.input) {
    els.input.addEventListener("input", function () {
      var hint = document.getElementById("command-hint");
      if (hint) hint.hidden = els.input.value.charAt(0) !== "/";
      maybeShowCommandMenu();
      saveInputDraft();   // 切换对话后切回：恢复未发送的输入（对标 opencode 草稿保留）
    });
    els.input.addEventListener("keydown", function (e) {
      var menu = document.getElementById("command-menu");
      if (e.key === "Escape" && menu && !menu.hidden) menu.hidden = true;
    });
    document.addEventListener("click", function (e) {
      var menu = document.getElementById("command-menu");
      if (menu && !menu.hidden && !menu.contains(e.target) && e.target !== els.input) menu.hidden = true;
    });
  }
  // 拖拽文件到消息区上传：图片 → 附件预览；任意文件 → 上传到 files/（模型用 read_document 读取）
  var dragDepth = 0;
  els.messages.addEventListener("dragenter", function (e) {
    e.preventDefault();
    dragDepth++;
    els.messages.classList.add("drag-over");
  });
  els.messages.addEventListener("dragover", function (e) {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  });
  els.messages.addEventListener("dragleave", function (e) {
    e.preventDefault();
    dragDepth--;
    if (dragDepth <= 0) { dragDepth = 0; els.messages.classList.remove("drag-over"); }
  });
  els.messages.addEventListener("drop", function (e) {
    e.preventDefault();
    dragDepth = 0;
    els.messages.classList.remove("drag-over");
    var files = Array.prototype.slice.call((e.dataTransfer && e.dataTransfer.files) || []);
    if (files.length === 0) return;
    var imgFiles = files.filter(function (f) { return f.type && f.type.indexOf("image/") === 0; });
    var docFiles = files.filter(function (f) { return !(f.type && f.type.indexOf("image/") === 0); });
    // 图片 → data URL 附件（进对话，模型用 see_image 看）
    if (imgFiles.length) {
      var jobs = imgFiles.map(function (f) { return fileToDataURL(f, 1024); });
      Promise.all(jobs).then(function (urls) {
        state.attachments = state.attachments.concat(urls).slice(0, 4);
        renderAttachPreview();
      }).catch(function () { /* 忽略 */ });
    }
    // 非图片 → 上传到服务器 files/（任意格式），模型用 read_document 读取
    if (docFiles.length) {
      Promise.all(docFiles.map(function (f) {
        return new Promise(function (resolve) {
          var reader = new FileReader();
          reader.onload = function () {
            fetch("/api/upload", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ data_url: reader.result, filename: f.name || "" })
            }).then(function (r) { return r.json(); }).then(function (d) {
              if (d && d.ok) state.fileAttachments.push({ name: d.name, url: d.url });
              resolve();
            }).catch(function () { resolve(); });
          };
          reader.onerror = function () { resolve(); };
          reader.readAsDataURL(f);
        });
      })).then(renderFileAttachPreview);
    }
  });
  // 图片附件
  if (els.attachBtn && els.attachInput) {
    els.attachBtn.addEventListener("click", function () { els.attachInput.click(); });
    els.attachInput.addEventListener("change", function () {
      var files = Array.prototype.slice.call(els.attachInput.files || []);
      if (files.length === 0) return;
      var imgFiles = files.filter(function (f) { return f.type && f.type.indexOf("image/") === 0; });
      var docFiles = files.filter(function (f) { return !(f.type && f.type.indexOf("image/") === 0); });
      // 图片 → data URL 附件（进对话，模型用 see_image 看）
      if (imgFiles.length) {
        var jobs = imgFiles.map(function (f) { return fileToDataURL(f, 1024); });
        Promise.all(jobs).then(function (urls) {
          state.attachments = state.attachments.concat(urls).slice(0, 4);
          renderAttachPreview();
        }).catch(function () { /* 忽略 */ });
      }
      // 非图片 → 直接上传到服务器 files/（任意格式），模型用 read_document 读取
      if (docFiles.length) {
        Promise.all(docFiles.map(function (f) {
          return new Promise(function (resolve) {
            var reader = new FileReader();
            reader.onload = function () {
              fetch("/api/upload", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ data_url: reader.result, filename: f.name || "" })
              }).then(function (r) { return r.json(); }).then(function (d) {
                if (d && d.ok) state.fileAttachments.push({ name: d.name, url: d.url });
                resolve();
              }).catch(function () { resolve(); });
            };
            reader.onerror = function () { resolve(); };
            reader.readAsDataURL(f);
          });
        })).then(renderFileAttachPreview);
      }
      els.attachInput.value = "";
    });
  }
  // 项目：触发按钮开合菜单 / 新建 / 点击外部关闭
  if (els.projectTrigger) {
    els.projectTrigger.addEventListener("click", function (e) {
      e.stopPropagation();
      toggleProjectMenu();
    });
  }
  if (els.projectAddBtn) {
    els.projectAddBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      addProjectPrompt();
    });
  }
  var projectEditBtn = document.getElementById("project-edit-btn");
  if (projectEditBtn) {
    projectEditBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      renameProjectAction();
    });
  }
  var projectFolderBtn = document.getElementById("project-folder-btn");
  if (projectFolderBtn) {
    projectFolderBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      fetch("/api/projects/" + state.project + "/open-folder", { method: "POST" })
        .catch(function () { /* 忽略 */ });
    });
  }
  var projectDelBtn = document.getElementById("project-del-btn");
  if (projectDelBtn) {
    projectDelBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      deleteProjectAction();
    });
  }
  document.addEventListener("click", function (e) {
    var menu = document.getElementById("project-menu");
    if (projectMenuOpen && menu && !menu.contains(e.target) && e.target !== els.projectTrigger) {
      closeProjectMenu();
    }
  });
  // 上下文管理：保存
  var ctxSaveBtn = document.getElementById("ctx-save-btn");
  if (ctxSaveBtn) {
    ctxSaveBtn.addEventListener("click", function () {
      var local = parseInt(document.getElementById("ctx-local").value, 10) || 0;
      var remote = parseInt(document.getElementById("ctx-remote").value, 10) || 0;
      ctxSaveBtn.disabled = true;
      fetch("/api/settings/context", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ local_ctx: local, remote_ctx: remote })
      })
        .then(function (r) { return r.json(); })
        .then(function () {
          loadProviders();        // 刷新上下文上限（token 显示同步）
          renderContextPanel();   // 刷新资源/本地模型状态
          ctxSaveBtn.disabled = false;
          ctxSaveBtn.textContent = "已保存";
          setTimeout(function () { ctxSaveBtn.textContent = "保存"; }, 1500);
        })
        .catch(function () { ctxSaveBtn.disabled = false; });
    });
  }
  // 粘贴图片附件（Ctrl+V）
  document.addEventListener("paste", function (e) {
    var items = (e.clipboardData && e.clipboardData.items) || [];
    var imgs = [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf("image/") === 0) {
        var f = items[i].getAsFile();
        if (f) imgs.push(f);
      }
    }
    if (imgs.length === 0) return;
    e.preventDefault();
    var jobs = imgs.map(function (f) { return fileToDataURL(f, 1024); });
    Promise.all(jobs).then(function (urls) {
      state.attachments = state.attachments.concat(urls).slice(0, 4);
      renderAttachPreview();
    }).catch(function () { /* 忽略 */ });
  });
  var loadLocalBtn = document.getElementById("load-local-btn");
  if (loadLocalBtn) {
    loadLocalBtn.addEventListener("click", function () { openSettings(true); });
  }
  els.settingsModal.addEventListener("click", function (e) {
    if (e.target === els.settingsBackdrop) closeSettings();
  });
  els.settingsModal.addEventListener("keydown", trapSettingsFocus);

  // 供应商下拉
  els.trigger.addEventListener("click", function (e) {
    e.stopPropagation();
    toggleMenu();
  });
  els.trigger.addEventListener("keydown", onTriggerKeydown);
  els.menu.addEventListener("keydown", onMenuKeydown);
  document.addEventListener("click", function (e) {
    if (menuOpen && !els.menu.contains(e.target) && e.target !== els.trigger) {
      closeMenu();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && menuOpen) {
      closeMenu();
      els.trigger.focus();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && settingsOpen) {
      e.preventDefault();
      closeSettings();
    }
  });

  // 历史搜索（防抖 300ms）
  var searchInput = document.getElementById("history-search");
  if (searchInput) {
    var searchTimer = null;
    searchInput.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        historyQuery = searchInput.value.trim();
        historyLimit = 50;
        renderHistoryList();
      }, 300);
    });
  }
  // 加载更多
  var moreBtn = document.getElementById("history-more");
  if (moreBtn) {
    moreBtn.addEventListener("click", function () {
      historyLimit += 50;
      renderHistoryList();
    });
  }
  // 右键菜单：点击外部/滚轮关闭
  document.addEventListener("click", function () { closeHistoryCtxMenu(); });
  document.addEventListener("wheel", function () { closeHistoryCtxMenu(); }, { passive: true });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeHistoryCtxMenu();
  });
  // 历史列表键盘导航：↑↓ 移动、Enter 打开
  if (els.historyList) {
    els.historyList.addEventListener("keydown", function (e) {
      var items = [...els.historyList.querySelectorAll(".history-item")].filter(function (i) { return !i.hidden; });
      if (items.length === 0) return;
      var idx = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        idx = (idx + 1) % items.length;
        items[idx].focus();
        items[idx].scrollIntoView({ block: "nearest" });
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        idx = idx <= 0 ? items.length - 1 : idx - 1;
        items[idx].focus();
        items[idx].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && idx >= 0) {
        e.preventDefault();
        items[idx].click();
      }
    });
  }
  // ask_user 弹窗：发送 / 取消
  var askSubmit = document.getElementById("ask-submit");
  if (askSubmit) {
    askSubmit.addEventListener("click", function () {
      submitAsk(document.getElementById("ask-input").value.trim());
    });
  }
  // 模型设置：保存（文本模型 + 图像模型 + 智能体，统一下拉 = 所有供应商 + 本地 GGUF）
  var modelsSave = document.getElementById("models-save-btn");
  if (modelsSave) {
    modelsSave.addEventListener("click", function () {
      var tv = readModelValue("text");
      var vv = readModelValue("vision");
      var av = readModelValue("agent");
      var avv = readModelValue("agent-vision");
      var tSel = document.getElementById("models-text-model");
      var vSel = document.getElementById("models-vision-model");
      modelsSave.disabled = true;
      fetch("/api/settings/context", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          default_provider: tv.provider, default_model: tv.model,
          vision_provider: vv.provider, vision_model: vv.model,
          agent_provider: av.provider, agent_model: av.model,
          agent_vision_provider: avv.provider, agent_vision_model: avv.model
        })
      })
        .then(function (r) { return r.json(); })
        .then(function () {
          modelsSave.disabled = false;
          modelsSave.textContent = "已保存";
          setTimeout(function () { modelsSave.textContent = "保存"; }, 1500);
          // 选中了本地 GGUF → 自动加载（文本/图像各一次）
          if (tv.provider === "local" && tSel && tSel.value.indexOf("local::") === 0) {
            autoLoadLocalModel(tSel, tv.model, modelsFeedback);
          }
          if (vv.provider === "local" && vSel && vSel.value.indexOf("local::") === 0) {
            autoLoadLocalModel(vSel, vv.model, modelsFeedback);
          }
        })
        .catch(function () { modelsSave.disabled = false; });
    });
  }
  // 通用拖拽调宽：dir=1 拖右缘（左栏）；dir=-1 拖左缘（右栏），宽度记忆在 localStorage
  function attachResizer(handleId, targetId, storageKey, minW, maxW, dir) {
    var handle = document.getElementById(handleId);
    var target = document.getElementById(targetId);
    if (!handle || !target) return;
    // 动态上限：始终给主聊天区留至少 320px
    var maxFor = function () {
      var reserved = dir === 1
        ? 320
        : (document.getElementById("sidebar") ? document.getElementById("sidebar").offsetWidth : 0) + 320;
      return Math.max(minW, Math.min(maxW, window.innerWidth - reserved));
    };
    var apply = function (w) {
      w = Math.max(minW, Math.min(maxFor(), w));
      target.style.width = w + "px";
    };
    try {
      var saved = parseInt(localStorage.getItem(storageKey), 10);
      if (saved) apply(saved);
    } catch (e) { /* 隐私模式忽略 */ }
    handle.addEventListener("mousedown", function (e) {
      e.preventDefault();
      handle.classList.add("dragging");
      var startX = e.clientX;
      var startW = target.offsetWidth;
      var onMove = function (ev) {
        apply(dir === 1 ? startW + (ev.clientX - startX) : startW + (startX - ev.clientX));
      };
      var onUp = function () {
        handle.classList.remove("dragging");
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        try { localStorage.setItem(storageKey, String(target.offsetWidth)); } catch (e) { /* 忽略 */ }
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
    window.addEventListener("resize", function () { apply(target.offsetWidth); });
  }
  attachResizer("sidebar-resizer", "sidebar", "sidebar_width", 120, 560, 1);
  attachResizer("file-panel-resizer", "file-panel", "file_panel_width", 260, 900, -1);

  // 文件预览右栏：按钮 + Esc
  var fpDownload = document.getElementById("file-panel-download");
  if (fpDownload) fpDownload.addEventListener("click", downloadCurrentFile);
  var fpOpen = document.getElementById("file-panel-open");
  if (fpOpen) fpOpen.addEventListener("click", openCurrentFileWithSystem);
  var fpFs = document.getElementById("file-panel-fullscreen");
  if (fpFs) fpFs.addEventListener("click", toggleFilePanelFullscreen);
  var fpClose = document.getElementById("file-panel-close");
  bindLinkPreview();
  if (fpClose) fpClose.addEventListener("click", closeFilePanel);
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var panel = document.getElementById("file-panel");
    if (!panel || panel.hidden) return;
    if (panel.classList.contains("fullscreen")) {
      toggleFilePanelFullscreen();
    } else {
      closeFilePanel();
    }
  });
  var askCancel = document.getElementById("ask-cancel");
  if (askCancel) {
    askCancel.addEventListener("click", function () { submitAsk("（用户取消）"); });
  }
  var askInput = document.getElementById("ask-input");
  if (askInput) {
    askInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submitAsk(askInput.value.trim());
      }
    });
  }
  // 空状态建议 chips（事件委托）
  els.messages.addEventListener("click", function (e) {
    var chip = e.target.closest(".suggestion-chip");
    if (chip) {
      els.input.value = chip.dataset.text || "";
      autoResizeInput();
      updateSendBtn();
      sendMessage();
    }
  });
}

function renderSuggestions() {
  var box = els.empty.querySelector(".suggestions");
  SUGGESTIONS.forEach(function (s) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "suggestion-chip";
    b.dataset.text = s;
    b.textContent = s;
    box.appendChild(b);
  });
}

/* ===========================================================================
 * 图片附件（多模态）
 * ========================================================================= */

/** 读取图片文件并压缩为 data URL（最长边 1024px，控制 token 消耗） */
function fileToDataURL(file, maxSize) {
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onload = function () {
      var img = new Image();
      img.onload = function () {
        var scale = Math.min(1, maxSize / Math.max(img.width, img.height));
        var w = Math.max(1, Math.round(img.width * scale));
        var h = Math.max(1, Math.round(img.height * scale));
        var canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        resolve(canvas.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = reject;
      img.src = reader.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/** 渲染附件预览条 */
function renderAttachPreview() {
  if (!els.attachPreview) return;
  els.attachPreview.innerHTML = "";
  if (state.attachments.length === 0) {
    els.attachPreview.hidden = true;
    return;
  }
  els.attachPreview.hidden = false;
  state.attachments.forEach(function (url, i) {
    var thumb = document.createElement("div");
    thumb.className = "attach-thumb";
    var img = document.createElement("img");
    img.src = url;
    img.alt = "附件 " + (i + 1);
    var del = document.createElement("button");
    del.className = "attach-thumb-del";
    del.type = "button";
    del.setAttribute("aria-label", "移除附件");
    del.textContent = "\u00D7";
    del.addEventListener("click", function () {
      state.attachments.splice(i, 1);
      renderAttachPreview();
    });
    thumb.appendChild(img);
    thumb.appendChild(del);
    els.attachPreview.appendChild(thumb);
  });
}

/** 渲染任意文件附件预览条（非图片：显示文件名 + 移除） */
function renderFileAttachPreview() {
  if (!els.attachPreview) return;
  if (!state.fileAttachments || state.fileAttachments.length === 0) {
    els.attachPreview.hidden = state.attachments.length === 0;
    return;
  }
  els.attachPreview.hidden = false;
  // 追加在图片缩略图之后
  state.fileAttachments.forEach(function (f, i) {
    var chip = document.createElement("div");
    chip.className = "attach-thumb attach-file";
    chip.title = "文件附件";
    var span = document.createElement("span");
    span.className = "attach-file-name";
    span.textContent = "📄 " + f.name;
    var del = document.createElement("button");
    del.className = "attach-thumb-del";
    del.type = "button";
    del.setAttribute("aria-label", "移除文件");
    del.textContent = "\u00D7";
    del.addEventListener("click", function () {
      state.fileAttachments.splice(i, 1);
      renderFileAttachPreview();
    });
    chip.appendChild(span);
    chip.appendChild(del);
    els.attachPreview.appendChild(chip);
  });
}

function clearAttachments() {
  state.attachments = [];
  state.fileAttachments = [];
  if (els.attachInput) els.attachInput.value = "";
  renderAttachPreview();
  renderFileAttachPreview();
}

/** 渲染上下文管理面板（设置弹窗） */
function renderContextPanel() {
  var resEl = document.getElementById("ctx-resources");
  if (!resEl) return;
  fetch("/api/settings/context")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var local = document.getElementById("ctx-local");
      var remote = document.getElementById("ctx-remote");
      if (local) local.value = data.local_ctx || 16384;
      if (remote) remote.value = data.remote_ctx || 32768;
      var lines = [];
      var ram = data.resources && data.resources.ram;
      if (ram) {
        lines.push("内存：<b>" + (ram.used / 2**30).toFixed(1) + " GB</b> / " + (ram.total / 2**30).toFixed(0) + " GB 已用");
      }
      var vram = data.resources && data.resources.vram;
      if (vram) {
        lines.push("显存（NVIDIA）：<b>" + (vram.used / 2**30).toFixed(1) + " GB</b> / " + (vram.total / 2**30).toFixed(0) + " GB 已用");
      } else {
        lines.push("显存：未检测到 NVIDIA 显卡（或 nvidia-smi 不可用）");
      }
      var lm = data.resources && data.resources.local_model;
      if (lm && lm.name) {
        lines.push("本地模型：<b>" + lm.name + "</b>（" + lm.status + "）");
      }
      resEl.innerHTML = lines.join("<br>");
    })
    .catch(function () {
      resEl.textContent = "无法读取资源信息";
    });
}

/* ===========================================================================
 * 项目（每个项目独立历史，对标 opencode project）
 * ========================================================================= */

/* ===========================================================================
 * 项目（对标 opencode：切换 / 新建 / 重命名 / 删除）
 * ========================================================================= */

var projectMenuOpen = false;
var _projectsSeq = 0;   // 项目请求序号：只应用最新一次 renderProjects 的结果（防异步竞态回退）

function renderProjects() {
  var trigName = document.getElementById("project-trigger-name");
  var seq = ++_projectsSeq;
  fetch("/api/projects")
    .then(function (res) { return res.json(); })
    .then(function (data) {
      if (seq !== _projectsSeq) return;   // 已有更新的请求发出 → 丢弃本次（防旧数据覆盖新项目状态）
      var projects = Array.isArray(data.projects) ? data.projects : [];
      state.projects = projects;
      // 如果当前项目已被删除，回到默认
      if (!projects.some(function (p) { return p.id === state.project; })) {
        state.project = projects.length ? projects[0].id : "default";
      }
      var cur = projects.find(function (p) { return p.id === state.project; });
      if (trigName) trigName.textContent = cur ? cur.name : "默认项目";
      renderProjectMenu();
      renderProjectRail();       // 最左图标栏（对标 opencode）
      updateTitle();
    })
    .catch(function () { /* 忽略 */ });
}

/** 渲染最左项目图标栏（opencode 风格：每个项目一个图标，点击切换） */
function renderProjectRail() {
  var list = document.getElementById("rail-list");
  var addBtn = document.getElementById("rail-new");
  if (!list) return;
  list.innerHTML = "";
  (state.projects || []).forEach(function (p) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "rail-item" + (p.id === state.project ? " active" : "");
    b.title = p.name;
    b.setAttribute("aria-label", "切换到项目 " + p.name);
    // 图标 = 自定义文字（icon_text）或项目名首字符；颜色 = 自定义 icon_color
    var ch = (p.icon_text && p.icon_text.trim())
      ? p.icon_text.trim().charAt(0)
      : (p.name || "?").trim().charAt(0);
    b.textContent = /[\u4e00-\u9fff]/.test(ch) ? ch : (ch ? ch.toUpperCase() : "?");
    if (p.icon_color) {
      b.style.color = p.icon_color;
      b.style.borderColor = p.id === state.project ? p.icon_color : "transparent";
      if (p.id === state.project) b.style.background = "color-mix(in srgb, " + p.icon_color + " 18%, transparent)";
    }
    // 后台完成未读点（该项目的任一对话有新回复）
    var hasUnread = Object.keys(state.unreadDots || {}).some(function (cid) {
      return (state.convCache[cid] || {}).project === p.id;
    });
    if (hasUnread) {
      var dot = document.createElement("span");
      dot.className = "rail-dot";
      b.appendChild(dot);
    }
    b.addEventListener("click", function () {
      // 启动脚本是任意进程执行：仅在用户明确确认后调用，命令和工作目录由
      // 服务端从租户项目记录中读取，浏览器不能临时替换。
      if (p.launch_cmd && p.launch_cmd.trim()) {
        var approved = window.confirm("运行项目启动命令？\n\n" + p.launch_cmd.trim());
        if (approved) {
          fetch("/api/launch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_id: p.id, _confirmed: true })
          }).then(expectOkResponse).catch(function (err) {
            window.alert("项目启动失败：" + (err && err.message ? err.message : err));
          });
        }
      }
      if (p.id !== state.project) switchProject(p.id);
      else { /* 已在该项目 */ }
    });
    // 右键：编辑该项目（重命名/删除/打开文件夹/编辑图标）
    b.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openRailItemMenu(e.clientX, e.clientY, p);
    });
    list.appendChild(b);
  });
  // 新建项目按钮：直接新建（不再弹菜单——菜单只剩"新建项目"一项）
  if (addBtn) {
    addBtn.addEventListener("click", function () {
      addProjectPrompt();
    });
  }
  // 底部小空间：显示当前项目文件夹路径
  var pathEl = document.getElementById("rail-path");
  if (pathEl) {
    var curP = (state.projects || []).find(function (x) { return x.id === state.project; });
    var pth = curP && curP.path ? curP.path : "";
    if (pth) {
      pathEl.textContent = pth;
      pathEl.title = "当前项目文件夹: " + pth + (curP && curP.launch_cmd ? "\n启动脚本: " + curP.launch_cmd : "");
      pathEl.style.display = "";
    } else {
      pathEl.textContent = "";
      pathEl.title = "当前项目未设置文件夹路径（可右键图标 → 编辑图标 设置）";
      pathEl.style.display = "";
    }
  }
}

/** 图标栏"＋"：弹小菜单（新建项目 / 打开文件夹） */
function openProjectRailMenu(anchor) {
  var old = document.getElementById("rail-ctx");
  if (old) old.remove();
  var menu = document.createElement("div");
  menu.id = "rail-ctx";
  menu.className = "command-menu";
  // 跟随按钮位置弹出（修复：不再固定钉在窗口底部）
  var r = anchor.getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.top = (r.bottom + 4) + "px";
  menu.style.left = r.left + "px";
  menu.style.bottom = "auto";
  menu.style.width = "160px";
  var newItem = document.createElement("div");
  newItem.className = "command-menu-item";
  newItem.innerHTML = '<span class="cm-cmd">新建项目</span>';
  newItem.addEventListener("click", function () {
    menu.remove();
    addProjectPrompt();
  });
  menu.appendChild(newItem);
  document.body.appendChild(menu);
  setTimeout(function () {
    document.addEventListener("click", function (e) {
      if (menu.isConnected && !menu.contains(e.target) && e.target !== anchor) menu.remove();
    }, { once: true });
  }, 0);
}

/** 右键项目图标：编辑菜单（重命名 / 打开文件夹 / 删除），对标 opencode 项目操作 */
function openRailItemMenu(x, y, p) {
  var old = document.getElementById("rail-ctx");
  if (old) old.remove();
  var menu = document.createElement("div");
  menu.id = "rail-ctx";
  menu.className = "command-menu";
  menu.style.position = "fixed";
  menu.style.top = Math.min(y, window.innerHeight - 140) + "px";
  menu.style.left = x + "px";
  menu.style.width = "170px";
  menu.style.bottom = "auto";
  // 标题（项目名）
  var title = document.createElement("div");
  title.className = "ctx-title";
  title.textContent = p.name;
  title.style.cssText = "font-size:11px;color:var(--text-secondary);padding:4px 10px;border-bottom:1px solid var(--border);margin-bottom:4px;";
  menu.appendChild(title);
  // 重命名
  var ren = document.createElement("div");
  ren.className = "command-menu-item";
  ren.innerHTML = '<span class="cm-cmd">✎ 重命名项目</span>';
  ren.addEventListener("click", function () {
    menu.remove();
    renameProjectAction(p.id);
  });
  menu.appendChild(ren);
  // 编辑图标（颜色 / 显示字 / 启动脚本）
  var iconEdit = document.createElement("div");
  iconEdit.className = "command-menu-item";
  iconEdit.innerHTML = '<span class="cm-cmd">🎨 编辑图标</span>';
  iconEdit.addEventListener("click", function () {
    menu.remove();
    openIconEditor(p);
  });
  menu.appendChild(iconEdit);
  // 打开文件夹
  var folder = document.createElement("div");
  folder.className = "command-menu-item";
  folder.innerHTML = '<span class="cm-cmd">▸ 打开项目文件夹</span>';
  folder.addEventListener("click", function () {
    menu.remove();
    fetch("/api/projects/" + p.id + "/open-folder", { method: "POST" }).catch(function () {});
  });
  menu.appendChild(folder);
  // 分隔
  var sep = document.createElement("div");
  sep.className = "ctx-sep";
  sep.style.cssText = "height:1px;background:var(--border);margin:4px 8px;";
  menu.appendChild(sep);
  // 删除
  var del = document.createElement("div");
  del.className = "command-menu-item danger";
  del.style.color = "#e5484d";
  del.innerHTML = '<span class="cm-cmd">✕ 删除项目</span>';
  del.addEventListener("click", function () {
    menu.remove();
    deleteProjectAction(p.id);
  });
  menu.appendChild(del);
  document.body.appendChild(menu);
  setTimeout(function () {
    document.addEventListener("click", function (e) {
      if (menu.isConnected && !menu.contains(e.target)) menu.remove();
    }, { once: true });
  }, 0);
}

/** 编辑项目图标：颜色 / 显示文字 / 启动脚本（弹窗） */
var ICON_COLORS = ["#4c9aff", "#e5484d", "#30a46c", "#f5a623", "#8e4ec6", "#e93d82", "#0d9488", "#64748b"];

function openIconEditor(p) {
  var old = document.getElementById("icon-editor");
  if (old) old.remove();
  var cur = (state.projects || []).find(function (x) { return x.id === p.id; }) || p;
  var overlay = document.createElement("div");
  overlay.id = "icon-editor";
  overlay.className = "icon-editor-overlay";
  var panel = document.createElement("div");
  panel.className = "icon-editor";
  var title = document.createElement("div");
  title.className = "ie-title";
  title.textContent = "编辑项目图标 · " + (cur.name || "项目");
  panel.appendChild(title);
  var colorLabel = document.createElement("div");
  colorLabel.className = "ie-label";
  colorLabel.textContent = "图标颜色";
  panel.appendChild(colorLabel);
  var colorRow = document.createElement("div");
  colorRow.className = "ie-colors";
  var colorInput = document.createElement("input");
  colorInput.type = "color";
  colorInput.className = "ie-color-picker";
  colorInput.value = cur.icon_color || "#4c9aff";
  ICON_COLORS.forEach(function (hex) {
    var sw = document.createElement("button");
    sw.type = "button";
    sw.className = "ie-swatch" + ((cur.icon_color || "#4c9aff") === hex ? " active" : "");
    sw.style.background = hex;
    sw.addEventListener("click", function () {
      colorInput.value = hex;
      colorRow.querySelectorAll(".ie-swatch").forEach(function (s) { s.classList.remove("active"); });
      sw.classList.add("active");
    });
    colorRow.appendChild(sw);
  });
  colorRow.appendChild(colorInput);
  panel.appendChild(colorRow);
  var textLabel = document.createElement("div");
  textLabel.className = "ie-label";
  textLabel.textContent = "图标显示文字（1-2 字，留空=项目名首字）";
  panel.appendChild(textLabel);
  var textInput = document.createElement("input");
  textInput.type = "text";
  textInput.className = "ie-input";
  textInput.maxLength = 2;
  textInput.value = cur.icon_text || "";
  textInput.placeholder = cur.name ? cur.name.charAt(0) : "";
  panel.appendChild(textInput);
  var cmdLabel = document.createElement("div");
  cmdLabel.className = "ie-label";
  cmdLabel.textContent = "启动脚本（点击图标时执行的命令，可选）";
  panel.appendChild(cmdLabel);
  var cmdInput = document.createElement("input");
  cmdInput.type = "text";
  cmdInput.className = "ie-input";
  cmdInput.value = cur.launch_cmd || "";
  cmdInput.placeholder = "如: start app.py 或 python main.py";
  panel.appendChild(cmdInput);
  var pathLabel = document.createElement("div");
  pathLabel.className = "ie-label";
  pathLabel.textContent = "项目文件夹路径";
  panel.appendChild(pathLabel);
  var pathInput = document.createElement("input");
  pathInput.type = "text";
  pathInput.className = "ie-input";
  pathInput.value = cur.path || "";
  pathInput.placeholder = "项目所在文件夹路径";
  panel.appendChild(pathInput);
  var actions = document.createElement("div");
  actions.className = "ie-actions";
  var cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ie-btn";
  cancel.textContent = "取消";
  cancel.addEventListener("click", function () { overlay.remove(); });
  var save = document.createElement("button");
  save.type = "button";
  save.className = "ie-btn ie-save";
  save.textContent = "保存";
  save.addEventListener("click", function () {
    fetch("/api/projects/" + cur.id + "/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        icon_color: colorInput.value,
        icon_text: textInput.value.trim(),
        launch_cmd: cmdInput.value.trim(),
        path: pathInput.value.trim()
      })
    }).then(function (r) { return r.json(); }).then(function () {
      var proj = (state.projects || []).find(function (x) { return x.id === cur.id; });
      if (proj) {
        proj.icon_color = colorInput.value;
        proj.icon_text = textInput.value.trim();
        proj.launch_cmd = cmdInput.value.trim();
        proj.path = pathInput.value.trim();
      }
      renderProjectRail();
      renderProjects();
      overlay.remove();
    }).catch(function () { /* 忽略 */ });
  });
  actions.appendChild(cancel);
  actions.appendChild(save);
  panel.appendChild(actions);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function renderProjectMenu() {
  var menu = document.getElementById("project-menu");
  if (!menu) return;
  menu.innerHTML = "";
  (state.projects || []).forEach(function (p) {
    var item = document.createElement("button");
    item.type = "button";
    item.className = "project-menu-item" + (p.id === state.project ? " active" : "");
    item.setAttribute("role", "menuitem");
    var name = document.createElement("span");
    name.style.flex = "1";
    name.style.textAlign = "left";
    name.textContent = p.name;
    item.appendChild(name);
    if (p.path) {
      var path = document.createElement("span");
      path.className = "pm-path";
      path.textContent = "···";
      path.title = p.path;
      item.appendChild(path);
    }
    item.addEventListener("click", function () { switchProject(p.id); });
    menu.appendChild(item);
  });
  var sep = document.createElement("div");
  sep.className = "project-menu-sep";
  menu.appendChild(sep);

  var add = document.createElement("button");
  add.type = "button";
  add.className = "project-menu-item action";
  add.textContent = "＋ 新建项目";
  add.addEventListener("click", function () { closeProjectMenu(); addProjectPrompt(); });
  menu.appendChild(add);

  var rename = document.createElement("button");
  rename.type = "button";
  rename.className = "project-menu-item action";
  rename.textContent = "✎ 重命名当前项目";
  rename.addEventListener("click", function () { closeProjectMenu(); renameProjectAction(); });
  menu.appendChild(rename);

  var openFolder = document.createElement("button");
  openFolder.type = "button";
  openFolder.className = "project-menu-item action";
  openFolder.textContent = "▸ 在资源管理器中打开";
  openFolder.addEventListener("click", function () {
    closeProjectMenu();
    fetch("/api/projects/" + state.project + "/open-folder", { method: "POST" })
      .catch(function () { /* 忽略 */ });
  });
  menu.appendChild(openFolder);

  var exportZip = document.createElement("button");
  exportZip.type = "button";
  exportZip.className = "project-menu-item action";
  exportZip.textContent = "⬇ 导出项目对话 ZIP";
  exportZip.addEventListener("click", function () {
    closeProjectMenu();
    downloadProjectZip(state.project);
  });
  menu.appendChild(exportZip);

  var del = document.createElement("button");
  del.type = "button";
  del.className = "project-menu-item action danger";
  del.textContent = "✕ 删除当前项目";
  del.addEventListener("click", function () { closeProjectMenu(); deleteProjectAction(); });
  menu.appendChild(del);
}

function toggleProjectMenu(open) {
  var menu = document.getElementById("project-menu");
  var trig = document.getElementById("project-trigger");
  if (!menu || !trig) return;
  projectMenuOpen = typeof open === "boolean" ? open : !projectMenuOpen;
  menu.hidden = !projectMenuOpen;
  trig.setAttribute("aria-expanded", String(projectMenuOpen));
}

function closeProjectMenu() { toggleProjectMenu(false); }

function switchProject(pid) {
  if (state.project === pid) { closeProjectMenu(); return; }
  state.project = pid;
  renderProjects();
  newChat();
  renderHistoryList();
  closeProjectMenu();
}

function renameProjectAction(pid) {
  var cur = (state.projects || []).find(function (p) { return p.id === (pid || state.project); });
  if (!cur) return;
  var name = window.prompt("重命名项目：", cur.name);
  if (name === null || !name.trim()) return;
  fetch("/api/projects/" + cur.id + "/rename", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() })
  })
    .then(expectOkResponse)
    .then(function () { renderProjects(); })
    .catch(function (err) { showErrorNote("项目重命名失败：" + err.message); });
}

function deleteProjectAction(pid) {
  var cur = (state.projects || []).find(function (p) { return p.id === (pid || state.project); });
  if (!cur) return;
  if (!window.confirm("删除项目「" + cur.name + "」及其所有对话？")) return;
  fetch("/api/projects/" + cur.id, { method: "DELETE" })
    .then(function () {
      state.project = "default";
      renderProjects();
      newChat();
      renderHistoryList();
    })
    .catch(function () { /* 忽略 */ });
}

/** 下载当前项目的所有对话（ZIP 打包） */
function downloadProjectZip(pid) {
  fetch("/api/projects/" + pid + "/export-zip")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.blob();
    })
    .then(function (blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = "项目对话.zip";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    })
    .catch(function () { showErrorNote("导出失败"); });
}

function addProjectPrompt() {
  // 不再要求输入名称：直接选文件夹，名称默认用文件夹名
  fetch("/api/pick-folder", { method: "POST" })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      var path = data && data.path ? data.path : "";
      if (!path) return null;   // 用户取消了文件夹选择 → 放弃创建
      // 名称 = 文件夹名（取路径最后一段）
      var name = path.split(/[\\/]/).filter(Boolean).pop() || "新项目";
      return fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, path: path })
      }).then(function (r) { return r.json(); });
    })
    .then(function (data) {
      if (data && data.id) {
        state.project = data.id;
        renderProjects();
        newChat();
        renderHistoryList();
      }
    })
    .catch(function () { /* 忽略 */ });
}

/* ===========================================================================
 * 对话历史（侧边栏）+ 插件 + token 用量
 * ========================================================================= */

/** 更新底部用量统计框：上下文 / 本对话总 tokens / 缓存命中 / 命中率 */
function updateTokenUsage() {
  // 上下文 = 当前实际占用（最近一次请求送入模型的 token，不含思考/工具过程）——不是累计值
  var ctxEl = document.getElementById("stat-ctx");
  if (ctxEl) {
    var used = state.lastContext || 0;
    var limit = state.ctx || 32768;
    var pct = limit > 0 ? (used / limit * 100) : 0;
    ctxEl.textContent = used.toLocaleString() + " / " + limit.toLocaleString() + " (" + pct.toFixed(1) + "%)";
    // 接近上限（≥85% 自动压缩阈值）→ 黄色预警；超过上限 → 红色
    ctxEl.className = pct >= 100 ? "stat-ctx-over" : (pct >= 85 ? "stat-ctx-warn" : "");
    ctxEl.title = pct >= 100
      ? "上下文已超限！API 将拒绝请求，请新开对话或清除历史"
      : (pct >= 85 ? "接近上限：系统将在下次请求时自动折叠早期对话（保留摘要）"
                   : "当前对话实际占用（不含思考过程）");
  }
  // 累计计费 = 所有模型请求的输入 + 输出；Agent 工具回合会重复发送上下文，因此可远高于当前上下文。
  var totalEl = document.getElementById("stat-total");
  if (totalEl) totalEl.textContent = (state.totalInput + state.totalOutput).toLocaleString();
  // 缓存命中 = 累计总量
  var cachedEl = document.getElementById("stat-cached");
  if (cachedEl) cachedEl.textContent = state.totalCached.toLocaleString();
  var hitEl = document.getElementById("stat-hit");
  if (hitEl) {
    var rate = state.totalInput > 0 ? state.totalCached / state.totalInput * 100 : 0;
    var lastRate = state.lastInput > 0 ? state.lastCached / state.lastInput * 100 : 0;
    // 主显示当前对话【累计】命中率（总体缓存效率）；悬停看最近一次
    hitEl.textContent = (state.lastCacheBust ? "⚠ " : "") + rate.toFixed(1) + "%";
    hitEl.title = "累计命中率: " + rate.toFixed(1) + "% · 最近一次: " + lastRate.toFixed(1) + "%"
      + (state.lastCacheBust ? "\n⚠ 最近请求缓存未命中（前缀变化或冷启动）" : "");
  }
  // 计费显示（单位：元）——当前对话累计费用
  var costEl = document.getElementById("stat-cost");
  if (costEl) {
    // 金额格式化：≥1元 → 2位小数；<1元 → 保留 4-6 位有效数字（小费用也要看得见）
    var p = state.activeProvider;
    var pmodel = p ? p.model : "";
    if (p && p.billing_mode === "subscription") {
      costEl.textContent = "订阅制";
      costEl.title = "该供应商按订阅/额度计费，不存在可直接换算的每-token 人民币账单。"
        + "\n模型: " + pmodel;
    } else {
      var v = state.totalCost || 0;
      var txt;
      if (v >= 1) txt = "¥" + v.toFixed(2);
      else if (v >= 0.0001) txt = "¥" + v.toFixed(4);
      else if (v > 0) txt = "¥" + v.toFixed(6);
      else txt = "¥0.00";
      costEl.textContent = txt;
      costEl.title = "当前对话累计费用（本地估算，不代表供应商账单）"
        + "\n模型: " + pmodel
        + "\n费率(元/百万tokens) 输入/输出/缓存: " + (p && p.price ? p.price.join(" / ") : "—")
        + (state.costIsEst ? "\n⚠ 价格或汇率可能变化，请以供应商账单为准" : "");
    }
    var source = p && p.price_source;
    if (source) {
      var sourceLabels = {
        synced_catalog: "已同步目录价（估算）",
        bundled_estimate: "内置本地估价",
        subscription: "订阅制",
        local_free: "本地运行"
      };
      costEl.title += "\n价格来源: " + (sourceLabels[source.kind] || source.label || source.kind)
        + (source.synced_at ? ("\n同步时间: " + source.synced_at) : "")
        + "\n账单核对: 未接入";
    }
  }
  // 上下文细分：按消息角色统计 token 占比（用户/助手/工具调用/其他）
  // token 估算：CJK 全角字符≈1 token，其他≈0.3 token（比纯字符数更接近真实占比）
  function estTokens(str) {
    var s = String(str == null ? "" : str);
    var cjk = (s.match(/[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/g) || []).length;
    return Math.round(cjk + (s.length - cjk) * 0.3);
  }
  var detailEl = document.getElementById("stat-detail-val");
  if (detailEl) {
    var roleChars = { user: 0, assistant: 0, tool: 0, other: 0 };
    (state.messages || []).forEach(function (m) {
      var c = m.content;
      var len = 0;
      if (typeof c === "string") len = estTokens(c);
      else if (Array.isArray(c)) len = estTokens(JSON.stringify(c));
      else if (c) len = estTokens(String(c));
      if (m.role === "user") roleChars.user += len;
      else if (m.role === "assistant") roleChars.assistant += len;
      else if (m.role === "tool" || m.tool_call_id) roleChars.tool += len;
      else roleChars.other += len;
    });
    var total = roleChars.user + roleChars.assistant + roleChars.tool + roleChars.other;
    if (total > 0) {
      var pct = function (n) { return (n / total * 100).toFixed(1) + "%"; };
      detailEl.textContent = "用户" + pct(roleChars.user) + "·助手" + pct(roleChars.assistant)
        + "·工具" + pct(roleChars.tool) + "·其他" + pct(roleChars.other);
      detailEl.title = "上下文构成（token 估算：CJK≈1 token/字，英文≈0.3 token/字符）"
        + "\n用户: " + roleChars.user + " tok (" + pct(roleChars.user) + ")"
        + "\n助手: " + roleChars.assistant + " tok (" + pct(roleChars.assistant) + ")"
        + "\n工具调用: " + roleChars.tool + " tok (" + pct(roleChars.tool) + ")"
        + "\n其他: " + roleChars.other + " tok (" + pct(roleChars.other) + ")";
    } else {
      detailEl.textContent = "—";
    }
  }
}

/** 计算本次费用（元）：输入(未命中)×输入价 + 输出×输出价 + 缓存命中×缓存价。
 *  价格取 activeProvider.price（[输入, 输出, 缓存] 元/百万 tokens）；
 *  无价格/本地模型 → 返回 null（不累计）；未收录模型 price 已是默认价。
 *  返回 {cost: 元, est: 是否估算} */
function calcCost(inputTokens, outputTokens, cachedTokens) {
  var p = state.activeProvider;
  if (!p || !p.price || p.price.length < 3) return null;
  var pin = Number(p.price[0]) || 0, pout = Number(p.price[1]) || 0, pcached = Number(p.price[2]) || 0;
  if (pin === 0 && pout === 0 && pcached === 0) return null;   // 免费模型不累计
  var newInput = Math.max(0, (inputTokens || 0) - (cachedTokens || 0));
  var cost = (newInput * pin + (outputTokens || 0) * pout + (cachedTokens || 0) * pcached) / 1000000;
  var est = !!(p.price_est);
  return { cost: cost, est: est };
}

/** 相对时间（今天→X分钟前；昨天；更早→日期） */
function fmtTime(ts) {
  if (!ts) return "";
  var d = new Date(ts * 1000);
  var now = new Date();
  if (d.toDateString() === now.toDateString()) {
    var diff = now / 1000 - ts;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    return Math.floor(diff / 3600) + " 小时前";
  }
  var y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "昨天";
  return (d.getMonth() + 1) + "/" + d.getDate();
}

/** 渲染历史列表（侧边栏） */
/* ===========================================================================
 * 对话历史（侧边栏）：搜索 / 日期分组 / 预览 / 模型标签 / 置顶 / 分页 / 右键菜单
 * ========================================================================= */

var historyQuery = "";
var historyLimit = 50;
var historyTotal = 0;
var _histRefreshTimer = null;   // 历史列表刷新防抖（多流完成并发时合并为一次）

function renderHistoryList() {
  // 防抖：后台多流同时完成会并发调用 → 合并为一次全量刷新（避免竞态/卡顿）
  if (_histRefreshTimer) clearTimeout(_histRefreshTimer);
  _histRefreshTimer = setTimeout(function () {
    _histRefreshTimer = null;
    _renderHistoryListInner();
  }, 200);
}

function _renderHistoryListInner() {
  var list = els.historyList;
  if (!list) return;
  var url = "/api/history?project=" + encodeURIComponent(state.project) +
            "&limit=" + historyLimit +
            (historyQuery ? "&q=" + encodeURIComponent(historyQuery) : "");
  fetch(url)
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      list.innerHTML = "";
      var convs = Array.isArray(data.conversations) ? data.conversations : [];
      historyTotal = data.total || convs.length;

      if (convs.length === 0) {
        var empty = document.createElement("div");
        empty.className = "history-empty";
        empty.textContent = historyQuery ? "没有匹配的历史" : "暂无历史对话";
        list.appendChild(empty);
      } else {
        // 按日期分组：今天 / 昨天 / 7 天内 / 更早
        var now = new Date();
        var groups = { today: [], yesterday: [], week: [], older: [] };
        convs.forEach(function (c) {
          var d = new Date(c.updated * 1000);
          if (d.toDateString() === now.toDateString()) groups.today.push(c);
          else {
            var y = new Date(now); y.setDate(now.getDate() - 1);
            if (d.toDateString() === y.toDateString()) groups.yesterday.push(c);
            else if (now - d < 7 * 86400 * 1000) groups.week.push(c);
            else groups.older.push(c);
          }
        });
        var defs = [["今天", "today"], ["昨天", "yesterday"], ["7 天内", "week"], ["更早", "older"]];
        defs.forEach(function (g) {
          var items = groups[g[1]];
          if (items.length === 0) return;
          var head = document.createElement("button");
          head.type = "button";
          head.className = "history-group";
          head.dataset.group = g[1];
          head.innerHTML = '<span class="hg-arrow">▾</span>' + g[0] + '<span class="hg-count">' + items.length + '</span>';
          head.addEventListener("click", function () { toggleHistoryGroup(head); });
          list.appendChild(head);
          items.forEach(function (c) {
            var item = buildHistoryItem(c);
            item.dataset.group = g[1];
            list.appendChild(item);
          });
        });
      }

      // 加载更多
      var moreBtn = document.getElementById("history-more");
      if (moreBtn) {
        moreBtn.hidden = historyTotal <= historyLimit;
        moreBtn.textContent = "加载更多（" + historyTotal + " 条，已显示 " + Math.min(historyLimit, historyTotal) + "）";
      }
    })
    .catch(function () {
      list.innerHTML = "";
      var fail = document.createElement("div");
      fail.className = "history-empty";
      fail.textContent = "无法读取历史";
      list.appendChild(fail);
    });
}

/** 构建一条历史项：置顶按钮 + 标题 + 预览 + 时间/模型标签 + 删除 */
function buildHistoryItem(c) {
  var item = document.createElement("div");
  item.className = "history-item" + (c.id === state.conversationId ? " active" : "");
  item.setAttribute("role", "listitem");
  item.tabIndex = -1;   // 键盘导航（↑↓ + Enter）
  item.dataset.cid = c.id;   // 生成中动画/完成亮点定位用
  item.title = c.title + (c.preview ? "\n" + c.preview : "");

  var pin = document.createElement("button");
  pin.type = "button";
  pin.className = "history-pin" + (c.pin ? " pinned" : "");
  pin.textContent = c.pin ? "★" : "☆";
  pin.title = c.pin ? "取消置顶" : "置顶";
  pin.setAttribute("aria-label", c.pin ? "取消置顶" : "置顶");
  pin.addEventListener("click", function (e) {
    e.stopPropagation();
    togglePin(c.id, !c.pin);
  });

  var body = document.createElement("div");
  body.className = "history-item-body";
  var title = document.createElement("div");
  title.className = "history-item-title";
  title.textContent = c.title;
  var preview = document.createElement("div");
  preview.className = "history-item-preview";
  preview.textContent = c.preview || "";
  var meta = document.createElement("div");
  meta.className = "history-item-meta";
  var metaParts = [fmtTime(c.updated)];
  if (c.model) metaParts.push((c.provider || "") + " · " + c.model);
  meta.textContent = metaParts.join(" · ");
  body.appendChild(title);
  body.appendChild(preview);
  body.appendChild(meta);

  var del = document.createElement("button");
  del.type = "button";
  del.className = "history-item-del";
  del.setAttribute("aria-label", "删除对话");
  del.textContent = "\u00D7";
  del.addEventListener("click", function (e) {
    e.stopPropagation();
    if (window.confirm("删除这条对话？")) deleteHistoryItem(c.id);
  });

  item.appendChild(pin);
  item.appendChild(body);
  item.appendChild(del);
  // 完成未读亮点：该对话后台完成且用户未查看（unreadDots 集合）→ 显示更亮的点
  if (state.unreadDots && state.unreadDots[c.id]) {
    var dot = document.createElement("span");
    dot.className = "history-new-dot";
    dot.setAttribute("aria-hidden", "true");
    dot.title = "该对话有新回复，点击查看";
    item.appendChild(dot);
  }
  item.addEventListener("click", function () {
    // 立即高亮选中项（不重建历史列表 → 点击瞬时响应），loadConversation 里不再 renderHistoryList
    els.historyList.querySelectorAll(".history-item.active").forEach(function (n) { n.classList.remove("active"); });
    item.classList.add("active");
    loadConversation(c.id);
  });
  item.addEventListener("contextmenu", function (e) {
    e.preventDefault();
    openHistoryCtxMenu(e.clientX, e.clientY, c);
  });
  return item;
}

function deleteHistoryItem(cid) {
  fetch("/api/history/" + cid, { method: "DELETE" })
    .then(expectOkResponse)
    .then(function () {
      if (state.conversationId === cid) newChat();
      renderHistoryList();
    })
    .catch(function (err) { showErrorNote("删除对话失败：" + err.message); });
}

function togglePin(cid, pin) {
  fetch("/api/history/" + cid + "/pin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin: pin })
  })
    .then(function () { renderHistoryList(); })
    .catch(function () { /* 忽略 */ });
}

/** 折叠/展开日期分组（只折叠记录条目，保留日期头） */
function toggleHistoryGroup(head) {
  var g = head.dataset.group;
  var collapsed = head.classList.toggle("collapsed");
  head.querySelector(".hg-arrow").textContent = collapsed ? "▸" : "▾";
  els.historyList.querySelectorAll('[data-group="' + g + '"].history-item').forEach(function (el) {
    el.hidden = collapsed;
  });
}

/** 导出指定对话（右键菜单/当前对话共用） */
function exportConversationId(cid, title, format) {
  format = format || "md";
  var url = "/api/history/" + cid + "/export?format=" + encodeURIComponent(format);
  if (format === "md") {
    // md：走 API 返回 markdown 文本（兼容旧逻辑）
    fetch(url)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data || !data.markdown) throw new Error("导出失败");
        var blob = new Blob([data.markdown], { type: "text/markdown;charset=utf-8" });
        var u = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = u;
        a.download = (title || data.title || "对话") + ".md";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(u);
      })
      .catch(function () { showErrorNote("导出失败"); });
  } else {
    // html / json：直接下载服务器生成的文件
    var a = document.createElement("a");
    a.href = url;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}

/** 导出格式选择浮层（md / html / json） */
function showExportMenu(anchor, cid, title) {
  var old = document.getElementById("export-menu");
  if (old) old.remove();
  var menu = document.createElement("div");
  menu.id = "export-menu";
  menu.className = "command-menu";
  menu.style.bottom = "auto";
  menu.style.top = "36px";
  menu.style.left = "auto";
  menu.style.right = "0";
  [
    { label: "Markdown", hint: "纯文本，通用", fmt: "md" },
    { label: "HTML", hint: "带样式，手机可看", fmt: "html" },
    { label: "JSON", hint: "完整记录（含思考/工具）", fmt: "json" }
  ].forEach(function (o) {
    var item = document.createElement("div");
    item.className = "command-menu-item";
    item.innerHTML = '<span class="cm-cmd">' + o.label + '</span><span class="cm-hint">' + o.hint + '</span>';
    item.addEventListener("click", function () {
      menu.remove();
      exportConversationId(cid, title, o.fmt);
    });
    menu.appendChild(item);
  });
  (anchor || document.body).appendChild(menu);
  setTimeout(function () {
    document.addEventListener("click", function (e) {
      if (menu.isConnected && !menu.contains(e.target)) menu.remove();
    }, { once: true });
  }, 0);
}

/** 历史右键菜单：打开 / 重命名 / 置顶 / 导出 / 删除 */
function openHistoryCtxMenu(x, y, c) {
  var menu = document.getElementById("history-ctx-menu");
  if (!menu) return;
  menu.innerHTML = "";
  var actions = [
    { label: "打开对话", fn: function () { loadConversation(c.id); } },
    { label: "重命名", fn: function () {
        var name = window.prompt("重命名对话：", c.title);
        if (name === null || !name.trim()) return;
        fetch("/api/history/" + c.id + "/rename", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: name.trim() })
        }).then(expectOkResponse)
          .then(function () { renderHistoryList(); })
          .catch(function (err) { showErrorNote("对话重命名失败：" + err.message); });
      } },
    { label: c.pin ? "取消置顶" : "置顶", fn: function () { togglePin(c.id, !c.pin); } },
    { label: "导出", fn: function () { showExportMenu(document.getElementById("history-ctx-menu"), c.id, c.title); } },
    { label: "删除对话", danger: true, fn: function () {
        if (window.confirm("删除这条对话？")) deleteHistoryItem(c.id);
      } },
  ];
  actions.forEach(function (a) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = a.danger ? "danger" : "";
    btn.textContent = a.label;
    btn.addEventListener("click", function () { closeHistoryCtxMenu(); a.fn(); });
    menu.appendChild(btn);
  });
  menu.hidden = false;
  var r = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, window.innerWidth - r.width - 8) + "px";
  menu.style.top = Math.min(y, window.innerHeight - r.height - 8) + "px";
}

function closeHistoryCtxMenu() {
  var menu = document.getElementById("history-ctx-menu");
  if (menu) menu.hidden = true;
}

/* ===========================================================================
 * ask_user 询问弹窗（AI 不确定时询问用户）
 * ========================================================================= */

var currentAsk = null;

function showAskModal(info) {
  currentAsk = info || null;
  var qEl = document.getElementById("ask-question");
  var opts = document.getElementById("ask-options");
  var input = document.getElementById("ask-input");
  if (!qEl || !opts || !input) return;
  qEl.textContent = (info && info.question) || "请确认一下";
  opts.innerHTML = "";
  (info && info.options || []).forEach(function (o) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ask-option";
    btn.textContent = o;
    btn.addEventListener("click", function () { submitAsk(o); });
    opts.appendChild(btn);
  });
  input.value = "";
  document.getElementById("ask-modal").hidden = false;
  input.focus();
}

function submitAsk(answer) {
  if (!currentAsk) return;
  var id = currentAsk.id;
  currentAsk = null;
  document.getElementById("ask-modal").hidden = true;
  fetch("/api/ask/" + id, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer: answer || "" })
  }).catch(function () { /* 忽略 */ });
}

/** 切换对话/新对话时：关闭 ask 弹窗并答复挂起询问（弹窗绝不阻碍切换对话） */
function dismissAskOnSwitch() {
  if (!currentAsk) return;
  var id = currentAsk.id;
  currentAsk = null;
  document.getElementById("ask-modal").hidden = true;
  fetch("/api/ask/" + id, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer: "（用户切换了对话，请根据已有信息继续或做合理假设）" })
  }).catch(function () { /* 忽略 */ });
}

/** 新对话：清空当前会话 */
async function newChat() {
  state.loadSeq += 1;                                // 使尚未返回的历史/建会话请求全部失效
  await cancelActiveStreamForSwitch();
  dismissAskOnSwitch();
  saveInputDraft();                                  // 保存当前对话的输入草稿                              // ask 弹窗不阻碍新对话
  state.streaming = false;
  state.sendingLock = false;                         // 发送锁复位：新对话立即能发（不被旧流阻塞）
  state.controller = null;
  setCaret(false);
  clearWaitHint();
  setStatus("idle");
  setSendBtn("send");
  state.messages = [];
  state.conversationId = null;
  state.usage = null;
  hideTaskStatus();                                   // 新对话/切项目：清状态栏（防跨项目残留）
  restoreInputDraft();                                // 新对话：恢复（新对话草稿为空 → 清空输入框）
  resetSteps();
  // 新对话：历史步骤置空（新对话无历史），不显示其他对话的
  state.stepHistory = [];
  renderHistory();
  // 新对话：加载空队列（旧对话队列按 cid 保留，互不显示/影响）
  currentQueue();
  renderSendQueue();
  state.lastContext = 0;
  state.totalInput = 0;
  state.totalOutput = 0;
  state.totalOutputFormal = 0;
  state.totalCached = 0;
  state.totalCost = 0;          // 新对话：费用清零
  state.costIsEst = false;
  clearAttachments();
  clearMessagesDom();
  els.empty.hidden = false;
  els.messages.classList.add("empty");          // 空状态居中需要该标记
  updateTokenUsage();
  renderHistoryList();
}

/** 加载历史对话 */
async function loadConversation(cid) {
  state.loadSeq += 1;
  await cancelActiveStreamForSwitch();
  dismissAskOnSwitch();                              // ask 弹窗不阻碍切换对话
  saveInputDraft();                                  // 切走前：保存当前对话的输入草稿
  state.streaming = false;
  state.sendingLock = false;                         // 发送锁复位：切换后新对话立即能发（不被旧流阻塞）
  state.controller = null;
  setCaret(false);
  clearWaitHint();
  setStatus("idle");
  // 修复：若该对话仍有后台流在生成 → 按钮保持"停止"（否则切回后看不到停止按钮）
  var _stillActive = cid && streamCtxs[cid] && !streamCtxs[cid].finished;
  state.streaming = !!_stillActive;
  setSendBtn(_stillActive ? "stop" : "send");
  state.usage = null;
  state.lastContext = 0;
  state.totalInput = 0;
  state.totalOutput = 0;
  state.totalCached = 0;
  state.totalCost = 0;          // 新对话：费用清零
  state.costIsEst = false;
  resetSteps();
  hideTaskStatus();                                  // 切换：清状态栏残留（只显示当前对话状态）
  // 切换对话：加载目标对话自己的队列（各对话独立，不显示其他对话的排队消息）
  state.conversationId = cid;
  state.nearBottom = true;
  restoreInputDraft();                              // 恢复该对话的输入草稿（未发送内容不丢）                           // 切换后默认滚到最新消息（不继承旧对话滚动位置）
  // 用户点进该对话 → 清除未读亮点（state 集合 + 列表 DOM 里的点）
  if (state.unreadDots && state.unreadDots[cid]) {
    delete state.unreadDots[cid];
    var _dotIt = els.historyList ? els.historyList.querySelector('.history-item[data-cid="' + cid + '"] .history-new-dot') : null;
    if (_dotIt) _dotIt.remove();
  }
  // 加载该对话自己的历史步骤（按对话隔离 + localStorage 持久化）
  state.stepHistory = loadStepHistory(cid);
  renderHistory();
  currentQueue();
  renderSendQueue();
  updateTokenUsage();
  // 对话缓存：切换过的对话立即渲染（瞬时切换，不等网络），后台 fetch 刷新
  var cached = state.convCache[cid];
  if (cached) {
    state.conversationId = cid;
    state.messages = cached.messages;
    // 规则3：缓存消息也按时间戳正序（与 fetch 分支一致，保证渲染有序）
    try {
      if (state.messages.some(function (m) { return typeof m.ts === "number"; })) {
        state.messages = state.messages.slice().sort(function (a, b) {
          var ta = typeof a.ts === "number" ? a.ts : Infinity;
          var tb = typeof b.ts === "number" ? b.ts : Infinity;
          return ta - tb;
        });
      }
    } catch (e) { /* 保持原序 */ }
    var cu = cached.usage || {};
    state.totalInput = cu.input || 0;
    state.totalOutput = cu.output || 0;
    state.totalOutputFormal = cu.output_formal || cu.output || 0;
    state.totalCached = cu.cached || 0;
    state.totalCost = cu.cost || 0;          // 恢复该对话累计费用
    state.costIsEst = !!cu.cost_est;
    state.lastContext = cu.context || cu.input || 0;
    state.lastInput = cu.last_input || cu.input || 0;
    state.lastCached = cu.last_cached || cu.cached || 0;
    state.usage = (cu.input || cu.output) ? { input: cu.input || 0, output: cu.output || 0, cached: cu.cached || 0 } : null;
    updateTokenUsage();
    clearAttachments();
    // 切会话强制滚到底（用户点开会话=看最新内容；不依赖条件滚动）
    state.nearBottom = true;
    renderAllMessages(null, true);                   // 先显示最新一批，再向前补齐旧消息
    restoreStreamUI();
    // 兜底：缓存渲染后流容器可能被覆盖，若该对话仍有活跃后台流 → 强制重建流 UI（防"正在生成的对话消失"）
    var _act = streamCtxs[cid];
    if (_act && !_act.finished) {
      var _cur = aiMsgEl;
      if (!_cur || !_cur.isConnected || _cur.dataset.cid !== cid) restoreStreamUI();
    }
  } else {
    // 无缓存：立即清空旧对话消息区（避免误看旧内容），显示轻量加载态，fetch 返回即渲染
    clearMessagesDom();
    var _ph = document.createElement("div");
    _ph.className = "history-loading";
    _ph.textContent = "加载中…";
    els.messages.appendChild(_ph);
  }
  // 竞态守卫：快速切换 A→B→C 时，A 的 fetch 返回不能覆盖当前对话 B/C
  var fetchToken = ++state.loadSeq || 1;
  fetch("/api/history/" + cid)
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (conv) {
      // 用户已切走：丢弃这个过期响应（不渲染、不改状态，防止"跳陌生地方"）
      if (state.loadSeq !== fetchToken || state.conversationId !== cid) return;
      state.conversationId = conv.id;
      var newMsgs = Array.isArray(conv.messages) ? conv.messages : [];
      // 规则3：按时间戳正序全量渲染（对标 OpenCode）——有 ts 的按 ts 升序排；
      // 旧数据无 ts 的保持数组原序（追加在最后，避免打乱历史）
      try {
        var _hasAnyTs = newMsgs.some(function (m) { return typeof m.ts === "number"; });
        if (_hasAnyTs) {
          newMsgs = newMsgs.slice().sort(function (a, b) {
            var ta = typeof a.ts === "number" ? a.ts : Infinity;
            var tb = typeof b.ts === "number" ? b.ts : Infinity;
            return ta - tb;
          });
        }
      } catch (e) { /* 排序失败保持原序 */ }
      // 缓存已渲染且内容一致 → 不重复全量渲染（切换不卡）；否则重新渲染
      var cachedRendered = !!cached;
      var same = cachedRendered && newMsgs.length === state.messages.length;
      if (same) {
        for (var _i = 0; _i < newMsgs.length; _i++) {
          var _a = newMsgs[_i], _b = state.messages[_i];
          if (!_a || !_b || _a.role !== _b.role ||
              ((_a.content || "") !== (_b.content || "")) ||
              ((_a.think || "") !== (_b.think || "")) ||
              (_a.tools ? _a.tools.length : 0) !== (_b.tools ? _b.tools.length : 0)) {
            same = false; break;
          }
        }
      }
      // fetch 是持久化权威；缓存只负责首屏秒开，绝不能以“更长”为由覆盖服务端结果。
      if (!same) {
        state.messages = newMsgs;
      } else {
        state.messages = newMsgs;   // 更新引用（后续编辑用新数据），不重渲染
      }
      // 恢复该对话的历史用量（完整保存 → 完整恢复）
      var u = conv.usage || {};
      state.totalInput = u.input || 0;
      state.totalOutput = u.output || 0;
      state.totalOutputFormal = u.output_formal || u.output || 0;
      state.totalCached = u.cached || 0;
      state.totalCost = u.cost || 0;          // 恢复该对话累计费用
      state.costIsEst = !!u.cost_est;
      state.lastContext = u.context || u.input || 0;   // 恢复该对话当时的上下文占用
      // 恢复"最近一次请求"的命中数据（每对话独立保存：刷新/切换后命中率不串、不归零）
      state.lastInput = u.last_input || u.input || 0;
      state.lastCached = u.last_cached || u.cached || 0;
      state.lastCacheBust = false;
      state.usage = (u.input || u.output) ? { input: u.input || 0, output: u.output || 0, cached: u.cached || 0 } : null;
      updateTokenUsage();
      clearAttachments();
      // 内容不同才重渲染一次（切换不卡）；渲染完成内部自动滚到底（scrollIfNearBottom），不再提前滚动
      if (!same) {
        // 切会话强制滚到底（fetch 渲染完成后）
        state.nearBottom = true;
        renderAllMessages(fetchToken, true);         // 强制滚底（长对话渲染完成才滚）
        scrollToBottom(false);
      }
      // 存入对话缓存（切换过的对话秒开；限 20 个）
      state.convCache[cid] = { messages: state.messages.slice(), usage: u };
      var keys = Object.keys(state.convCache);
      if (keys.length > 20) delete state.convCache[keys[0]];
      // 切回仍在后台生成的对话 → 接回流继续实时显示（对标 opencode）
      restoreStreamUI();
    })
    .catch(function () { /* 忽略 */ });
}

/** 渲染历史里的 AI 消息（无思考/工具过程，只渲染正文） */
function renderSavedAssistantMessage(content, idx, extra, mountTo) {
  var msg = document.createElement("div");
  msg.className = "msg msg-ai saved";
  if (typeof idx === "number") msg.dataset.idx = idx;
  var label = document.createElement("div");
  label.className = "msg-model";
  label.textContent = state.activeProvider ? state.activeProvider.model : "";
  // 历史消息：显示本次生成耗时（对标 opencode：每次回答用时反馈）
  if (extra && extra.duration) {
    var _d = extra.duration;
    var _ds = _d >= 60 ? Math.floor(_d / 60) + "分" + (_d % 60) + "秒" : _d + "秒";
    var _dur = document.createElement("span");
    _dur.className = "msg-duration";
    _dur.textContent = "⏱ " + _ds;
    label.appendChild(_dur);
  }
  msg.appendChild(label);

  // 历史完整展示：思考过程（折叠）+ 工具调用记录
  if (extra) {
    // 交错时间线（新格式）：按实时对话顺序渲染 思考段/工具段/过渡正文段
    if (extra.timeline && extra.timeline.length) {
      var tlWrap = document.createElement("div");
      tlWrap.className = "saved-timeline";
      extra.timeline.forEach(function (tl, ti) {
        if (tl.type === "think") {
          var tk = document.createElement("button");
          tk.type = "button";
          tk.className = "think-toggle";
          tk.setAttribute("aria-expanded", "false");
          tk.innerHTML = '<span class="think-arrow">▸</span><span class="think-label">思考过程</span>'
            + '<span class="think-status">' + (ti + 1) + '</span>';
          var kb = document.createElement("div");
          kb.className = "think-body";
          kb.hidden = true;
          var kt = document.createElement("div");
          kt.className = "thinking";
          kt.textContent = tl.text;
          kb.appendChild(kt);
          tk.addEventListener("click", function () {
            var open = kb.hidden;
            kb.hidden = !open;
            tk.setAttribute("aria-expanded", String(open));
            tk.querySelector(".think-arrow").textContent = open ? "▾" : "▸";
            if (open) { state.nearBottom = false; tk.scrollIntoView({ block: "nearest" }); }
          });
          tlWrap.appendChild(tk);
          tlWrap.appendChild(kb);
        } else if (tl.type === "tool") {
          // 复用实时工具行的类名（tool-line/tool-line-summary/tool-line-body）→ 样式与实时完全一致
          var tl2 = document.createElement("div");
          tl2.className = "tool-line" + (tl.done ? "" : " fail");
          var tlIsAgent = /agent|delegate/i.test(tl.name);
          if (tlIsAgent) tl2.classList.add("agent");
          var isShell = /terminal|run_command|shell/i.test(tl.name);
          if (isShell) tl2.classList.add("shell");
          var s2 = document.createElement("div");
          s2.className = "tool-line-summary";
          // 终端工具：还原「Shell + 命令」显示（与实时完全一致，而不是工具名）
          if (isShell) {
            var cmd2 = extractShellCommand(tl.args);
            s2.innerHTML = '<span class="tl-arrow">▶</span><span class="tl-shell">Shell</span> '
              + '<span class="tl-cmd"></span>';
            s2.querySelector(".tl-cmd").textContent = cmd2 || tl.name;
          } else if (tlIsAgent) {
            // 智能体卡片（历史回放）：点阵图标 + 名称 + 任务 + 状态徽章
            var task2 = extractAgentTask(tl.args);
            s2.innerHTML = '<span class="tl-arrow">▶</span>'
              + '<span class="agent-dot" aria-hidden="true"></span>'
              + '<span class="agent-badge">Agent</span> '
              + '<span class="agent-name"></span>'
              + '<span class="agent-task"></span>'
              + '<span class="agent-status ' + (tl.done ? "done" : "fail") + '">'
              + (tl.done ? "✔ 完成" : "✗ 失败") + '</span>';
            var an2 = s2.querySelector(".agent-name");
            if (an2) an2.textContent = tl.name === "delegate_to_agent" ? "智能体委托" : tl.name;
            var at2 = s2.querySelector(".agent-task");
            if (at2) at2.textContent = task2 ? "「" + task2.slice(0, 60) + (task2.length > 60 ? "…" : "") + "」" : "";
          } else {
            s2.innerHTML = '<span class="tl-arrow">▶</span> ' + tl.name
              + (tl.args ? "(" + tl.args + ")" : "")
              + (tl.done ? " → 完成" : " → 失败")
              + (tl.result && !tl.done ? " · " + tl.result.slice(0, 80) : "");
          }
          s2.innerHTML += (tl.done ? " → 完成" : " → 失败")
            + (tl.result && !tl.done ? " · " + tl.result.slice(0, 80) : "");
          var b2 = document.createElement("div");
          b2.className = "tool-line-body";
          b2.hidden = true;
          if (tl.result) b2.textContent = String(tl.result).replace(/\n{3,}/g, "\n\n").trim();
          s2.addEventListener("click", function () {
            b2.hidden = !b2.hidden;
            var a = s2.querySelector(".tl-arrow");
            if (a) a.textContent = b2.hidden ? "▶" : "▼";
            if (!b2.hidden) { state.nearBottom = false; s2.scrollIntoView({ block: "nearest" }); }
          });
          tl2.appendChild(s2);
          tl2.appendChild(b2);
          if (tl.diff && /^[\s\S]*\+(\d+)\s+-(\d+)/.test(tl.diff)) {
            renderToolDiff(tl2, tl.diff);
          }
          tlWrap.appendChild(tl2);
        } else if (tl.type === "text" && tl.text && tl.text.trim()) {
          // 工具调用间的过渡正文：与实时同结构（.step > .md），仅加弱化色区分
          var tstep = document.createElement("div");
          tstep.className = "step saved-step";
          var tm = document.createElement("div");
          tm.className = "md";
          tm.innerHTML = renderMarkdown(tl.text);
          postProcess(tm);
          tstep.appendChild(tm);
          tlWrap.appendChild(tstep);
        }
      });
      msg.appendChild(tlWrap);
    } else {
    if (extra.think && extra.think.trim()) {
      var th = document.createElement("button");
      th.type = "button";
      th.className = "think-toggle";
      th.setAttribute("aria-expanded", "false");
      th.innerHTML = '<span class="think-arrow">▸</span><span class="think-label">思考过程</span><span class="think-status">已保存</span>';
      var tb = document.createElement("div");
      tb.className = "think-body";
      tb.hidden = true;
      var tt = document.createElement("div");
      tt.className = "thinking";
      tt.textContent = extra.think;
      tb.appendChild(tt);
      th.addEventListener("click", function () {
        var open = tb.hidden;
        tb.hidden = !open;
        th.setAttribute("aria-expanded", String(open));
        th.querySelector(".think-arrow").textContent = open ? "▾" : "▸";
        if (open) {
          // 修复：展开历史思考时暂停自动跟随 + 定位到该块（否则被流式滚动拉回底部）
          state.nearBottom = false;
          th.scrollIntoView({ block: "nearest" });
        }
      });
      msg.appendChild(th);
      msg.appendChild(tb);
    }
    if (extra.tools && extra.tools.length) {
      var tWrap = document.createElement("div");
      tWrap.className = "saved-tools";
      extra.tools.forEach(function (t) {
        var line = document.createElement("div");
        line.className = "saved-tool-line" + (t.done ? "" : " fail");
        var tIsAgent = /agent|delegate/i.test(t.name);
        if (tIsAgent) line.classList.add("agent");
        // 折叠式：summary 单行（▶ 工具名(参数) → 完成），点击展开完整结果
        var s = document.createElement("div");
        s.className = "tool-line-summary";
        if (tIsAgent) {
          // 智能体卡片（旧格式回放）：点阵图标 + 名称 + 任务 + 状态徽章
          var taskT = extractAgentTask(t.args);
          s.innerHTML = '<span class="tl-arrow">▶</span>'
            + '<span class="agent-dot" aria-hidden="true"></span>'
            + '<span class="agent-badge">Agent</span> '
            + '<span class="agent-name"></span>'
            + '<span class="agent-task"></span>'
            + '<span class="agent-status ' + (t.done ? "done" : "fail") + '">'
            + (t.done ? "✔ 完成" : "✗ 失败") + '</span>';
          var anT = s.querySelector(".agent-name");
          if (anT) anT.textContent = t.name === "delegate_to_agent" ? "智能体委托" : t.name;
          var atT = s.querySelector(".agent-task");
          if (atT) atT.textContent = taskT ? "「" + taskT.slice(0, 60) + (taskT.length > 60 ? "…" : "") + "」" : "";
        } else {
          s.innerHTML = '<span class="tl-arrow">▶</span> ' + t.name
            + (t.args ? "(" + t.args + ")" : "")
            + (t.done ? " → 完成" : " → 失败")
            + (t.result && !t.done ? " · " + t.result.slice(0, 80) : "");
        }
        var b = document.createElement("div");
        b.className = "tool-line-body";
        b.hidden = true;
        if (t.result) b.textContent = String(t.result).replace(/\n{3,}/g, "\n\n").trim();
        s.addEventListener("click", function () {
          b.hidden = !b.hidden;
          var a = s.querySelector(".tl-arrow");
          if (a) a.textContent = b.hidden ? "▶" : "▼";
          if (!b.hidden) {
            state.nearBottom = false;
            s.scrollIntoView({ block: "nearest" });
          }
        });
        line.appendChild(s);
        line.appendChild(b);
        tWrap.appendChild(line);
        // 历史里保留文件变更 diff（可展开）
        if (t.diff && /^[\s\S]*\+(\d+)\s+-(\d+)/.test(t.diff)) {
          renderToolDiff(line, t.diff);
        }
      });
      msg.appendChild(tWrap);
    }
    }   // end else（无 timeline 时走旧逻辑：单一思考 + 工具列表）
  }
  // 最终正文：若 timeline 里的过渡正文已包含相同内容（工具调用间模型已输出完整回答），
  // 则跳过渲染避免重复（用户要求：前面重复的过渡正文替代最终大字）
  var skipFinal = false;
  if (extra && extra.timeline && extra.timeline.length && content && content.trim()) {
    var tlText = extra.timeline
      .filter(function (t) { return t.type === "text" && t.text; })
      .map(function (t) { return t.text; })
      .join("\n").replace(/\s+/g, "");
    var cont = content.replace(/\s+/g, "");
    // 过渡正文拼接后已覆盖最终正文的绝大部分（≥70%）→ 视为重复，跳过最终正文
    if (tlText.length >= 60 && cont.length >= 30) {
      var hit = 0;
      for (var ci = 0; ci + 8 <= cont.length && ci < 400; ci += 8) {
        if (tlText.indexOf(cont.slice(ci, ci + 8)) >= 0) hit++;
      }
      if (hit >= Math.ceil(Math.min(400, cont.length) / 8) * 0.7) skipFinal = true;
    }
  }
  if (!skipFinal) {
    var md = document.createElement("div");
    md.className = "md";
    md.innerHTML = renderMarkdown(content);
    postProcess(md);
    msg.appendChild(md);
  }
  msg.appendChild(buildMsgActions({
    role: "assistant",
    idx: idx,
    getText: function () { return content; }
  }));
  if (mountTo) mountTo.appendChild(msg); else els.messages.appendChild(msg);
}

/** 保存当前会话到历史（对话结束时调用） */
function saveHistory() {
  if (!state.messages || state.messages.length === 0) return;
  fetch("/api/history", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: state.conversationId || "",
      messages: state.messages,
      project: state.project,
      provider: state.activeProvider ? state.activeProvider.key : "",
      model: state.activeProvider ? state.activeProvider.model : ""
    })
    })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      if (data && data.id) state.conversationId = data.id;
      renderHistoryList();
    })
    .catch(function () { /* 忽略 */ });
}

/** 渲染插件列表（设置弹窗） */
function renderPluginsList() {
  var list = document.getElementById("plugins-list");
  if (!list) return;
  fetch("/api/plugins")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      list.innerHTML = "";
      var plugins = Array.isArray(data.plugins) ? data.plugins : [];
      if (plugins.length === 0) {
        var empty = document.createElement("div");
        empty.className = "minor-note";
        empty.textContent = "plugins/ 目录下还没有插件";
        list.appendChild(empty);
        return;
      }
      plugins.forEach(function (p) {
        var item = document.createElement("div");
        item.className = "mcp-item";
        item.setAttribute("role", "listitem");
        var name = document.createElement("span");
        name.className = "mcp-item-name";
        name.textContent = p.name;
        item.appendChild(name);
        var stateEl = document.createElement("span");
        stateEl.className = "mcp-item-state";
        if (p.error) {
          stateEl.classList.add("err");
          stateEl.textContent = "加载失败";
        } else {
          stateEl.classList.add("ok");
          stateEl.textContent = "已加载 · " + p.tools.length + " 个工具";
        }
        item.appendChild(stateEl);
        list.appendChild(item);
        if (p.error) {
          var err = document.createElement("div");
          err.className = "mcp-item-error";
          err.textContent = "    " + p.error;
          list.appendChild(err);
        }
      });
    })
    .catch(function () {
      list.innerHTML = "";
      var fail = document.createElement("div");
      fail.className = "minor-note";
      fail.textContent = "无法读取插件列表";
      list.appendChild(fail);
    });
}

/* ===========================================================================
 * 小应用（小挂件/浮窗/桌宠）：顶栏图标区 + 应用窗口 + 设置管理
 * ========================================================================= */

var appsState = { apps: [], dockExpanded: false, currentApp: null };

/** 加载应用列表 → 渲染顶栏图标区 + 设置页应用管理列表 */
function loadApps() {
  return fetch("/api/apps")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      appsState.apps = Array.isArray(data.apps) ? data.apps : [];
      renderAppsDock();
      renderAppsManageList();
    })
    .catch(function () { /* 忽略 */ });
}

/** 顶栏图标区：横向排列，多了自动滚动；点击打开自由窗口 */
function renderAppsDock() {
  var dock = document.getElementById("apps-dock");
  if (!dock) return;
  dock.innerHTML = "";
  appsState.apps.forEach(function (a) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "app-dock-icon";
    b.textContent = a.icon || "🧩";
    b.title = (a.title || a.name) + "（点击打开）";
    b.setAttribute("aria-label", "打开" + (a.title || a.name));
    b.addEventListener("click", function () { openAppWindow(a); });
    dock.appendChild(b);
  });
}

/** 打开小应用窗口（自由窗口：动态创建，可同时开多个，各自可拖动/缩放/记忆） */
function openAppWindow(a) {
  // 单例：同名窗口已存在则置顶聚焦（除非显式指定多个）
  var _exist = (window.__appWins || []).filter(function (w) { return w.__appName === a.name && w.isConnected; })[0];
  if (_exist) {
    _exist.style.display = "";
    _exist.style.zIndex = 120;
    (window.__appWins || []).forEach(function (w) { if (w !== _exist && w.style.zIndex === "120") w.style.zIndex = 110; });
    return;
  }
  var win = document.createElement("div");
  win.className = "app-window";
  var frameless = !!(a.frameless || a.name === "clock");
  if (frameless) win.classList.add("app-window-frameless");
  var inlineClock = frameless && a.name === "clock";
  win.__appName = a.name;
  win.innerHTML =
    (frameless ? "" :
    '<div class="app-window-head">' +
      '<span class="app-window-title">' + (a.icon || "") + " " + (a.title || a.name) + '</span>' +
      '<div class="app-window-actions">' +
        '<button class="file-panel-btn aw-fs" type="button" title="全屏">⛶</button>' +
        '<button class="file-panel-btn aw-close" type="button" title="关闭">✕</button>' +
      '</div>' +
    '</div>') +
    (frameless ? '<button class="frameless-close" type="button" title="关闭">✕</button>' : "") +
    (frameless && !inlineClock ? '<div class="frameless-drag" title="拖动窗口"></div>' : "") +
    (inlineClock
      ? '<div class="clock-inline">' +
          '<div class="clk-top"><span class="clk-brand">问墨 · 时间</span><span class="clk-led"></span></div>' +
          '<div class="clk-screen">' +
            '<div class="clk-view clk-view-time">' +
              '<div class="clock-time"><span class="cli-hh">--</span><span class="cli-colon">:</span><span class="cli-mm">--</span><span class="cli-sec cli-ss">--</span></div>' +
              '<div class="clock-date cli-date">----年 --月 --日 · 星期-</div>' +
            '</div>' +
            '<div class="clk-view clk-view-timer" style="display:none">' +
              '<div class="clk-timer-state">专注中 · 25 分钟</div>' +
              '<div class="clk-timer-time">25:00</div>' +
              '<div class="clk-timer-bar"><i></i></div>' +
              '<div class="clk-timer-set">' +
                '<span class="clk-set-lb">专注</span>' +
                '<button type="button" class="clk-set-btn" data-set="focus" data-d="-5" title="减 5 分钟">−</button>' +
                '<input type="number" class="clk-set-val" data-setval="focus" value="25" min="1" max="120" title="直接输入专注时长（分钟）">' +
                '<button type="button" class="clk-set-btn" data-set="focus" data-d="5" title="加 5 分钟">＋</button>' +
                '<span class="clk-set-lb">休息</span>' +
                '<button type="button" class="clk-set-btn" data-set="break" data-d="-5" title="减 5 分钟">−</button>' +
                '<input type="number" class="clk-set-val" data-setval="break" value="5" min="1" max="60" title="直接输入休息时长（分钟）">' +
                '<button type="button" class="clk-set-btn" data-set="break" data-d="5" title="加 5 分钟">＋</button>' +
              '</div>' +
              '<div class="clk-timer-ctrl">' +
                '<button type="button" class="clk-tbtn" data-tact="toggle">开始</button>' +
                '<button type="button" class="clk-tbtn" data-tact="reset">重置</button>' +
              '</div>' +
              '<div class="clk-timer-tag"><input type="text" class="clk-pomo-label" placeholder="本次专注：做什么？（选填）" maxlength="30" spellcheck="false"></div>' +
            '</div>' +
            '<div class="clk-view clk-view-hist" style="display:none">' +
              '<div class="clk-hist-title">番茄记录<button type="button" class="clk-clear" title="清空全部记录">清空</button></div>' +
              '<div class="clk-achv"></div>' +
              '<div class="clk-hist-list"></div>' +
            '</div>' +
            '<div class="clk-view clk-view-set" style="display:none">' +
              '<div class="clk-set-title">设置</div>' +
              '<div class="clk-set-row"><span class="clk-set-lb">自动轮换</span>' +
                '<button type="button" class="clk-switch" data-opt="auto" title="专注完成自动进入休息"><i></i></button>' +
                '<span style="opacity:.55">专注完成自动开始休息</span></div>' +
              '<div class="clk-set-row"><span class="clk-set-lb">按键音</span>' +
                '<button type="button" class="clk-switch" data-opt="sound" title="按钮音效开关"><i></i></button>' +
                '<span style="opacity:.55">按钮音效开关</span></div>' +
              '<div class="clk-set-row"><span class="clk-set-lb">音量</span>' +
                '<input type="range" class="clk-vol" min="0" max="100" value="80" title="音效音量">' +
                '<span class="clk-vol-val">80%</span></div>' +
              '<div class="clk-set-row"><span class="clk-set-lb">导入图像</span>' +
                '<button type="button" class="clk-set-file" data-media="image" title="选择图片作为时钟屏幕显示">选择图片</button>' +
                '<button type="button" class="clk-set-clear" data-clear="image" title="清除图像" style="display:none">清除</button></div>' +
              '<div class="clk-set-row"><span class="clk-set-lb">导入视频</span>' +
                '<button type="button" class="clk-set-file" data-media="video" title="选择视频文件">选择文件</button>' +
                '<button type="button" class="clk-set-clear" data-clear="video" title="清除视频" style="display:none">清除</button></div>' +
              '<div class="clk-set-row clk-vid-url-row"><span class="clk-set-lb">视频地址</span>' +
                '<input type="text" class="clk-vid-url" placeholder="粘贴视频文件直链（.mp4/.webm/.ogg 结尾）" spellcheck="false">' +
                '<button type="button" class="clk-vid-load">加载</button></div>' +
              '<div class="clk-vid-hist"></div>' +
              '<input type="file" class="clk-file-img" accept="image/*" style="display:none">' +
              '<input type="file" class="clk-file-vid" accept="video/*" style="display:none">' +
            '</div>' +
            '<div class="clk-view clk-view-media" style="display:none">' +
              '<div class="clk-media-wrap">' +
                '<iframe class="clk-media-frame" allow="autoplay; fullscreen; encrypted-media; picture-in-picture" allowfullscreen style="display:none"></iframe>' +
                '<img class="clk-media-img" style="display:none" alt="时钟图像">' +
                '<video class="clk-media-video" style="display:none" loop muted playsinline controls></video>' +
                '<div class="clk-media-time">--:--</div>' +
                '<div class="clk-media-err" style="display:none"></div>' +
                '<div class="clk-media-empty">未导入图像 / 视频<br>请到「设置」中导入</div>' +
                '<button type="button" class="clk-media-clear" title="撤销当前加载" style="display:none">✕ 撤销加载</button>' +
              '</div>' +
            '</div>' +
            '<span class="clk-glare"></span>' +
          '</div>' +
          '<div class="clk-btns">' +
            '<button type="button" class="clk-btn" data-act="power" title="电源：开机 / 关机（待机黑屏）"><i>&#9211;</i><span>电源</span></button>' +
            '<button type="button" class="clk-btn" data-act="timer" title="番茄计时：单击开始/暂停，双击重置"><i>&#9654;</i><span>计时</span></button>' +
            '<button type="button" class="clk-btn" data-act="hist" title="番茄历史记录"><i>&#8801;</i><span>记录</span></button>' +
            '<button type="button" class="clk-btn" data-act="set" title="设置：参数 / 音量 / 导入图像视频"><i>&#9881;</i><span>设置</span></button>' +
          '</div>' +
        '</div>'
      : '<iframe class="app-window-frame" sandbox="allow-scripts" referrerpolicy="no-referrer" allowtransparency="true" style="background:transparent" src="/apps/' + encodeURIComponent(a.name) + '/index.html?theme=' + encodeURIComponent(els.root.getAttribute("data-theme") || "dark") + '&ts=' + Date.now() + '" title="小应用"></iframe>') +
    '<div class="app-window-resize" title="拖拽调整大小"></div>';
  document.body.appendChild(win);
  if (inlineClock) {
    var _WEEK = ["日", "一", "二", "三", "四", "五", "六"];
    var _cdiv = win.querySelector(".clock-inline");
    _cdiv.classList.add("clk-booting");
    setTimeout(function () { if (_cdiv.isConnected) _cdiv.classList.remove("clk-booting"); }, 1050);
    var _p2 = function (n) { return String(n).padStart(2, "0"); };
    var _tickClock = function () {
      if (!_cdiv || !_cdiv.isConnected) { clearInterval(_tmr); return; }
      var _d = new Date();
      _cdiv.querySelector(".cli-hh").textContent = _p2(_d.getHours());
      _cdiv.querySelector(".cli-mm").textContent = _p2(_d.getMinutes());
      _cdiv.querySelector(".cli-ss").textContent = _p2(_d.getSeconds());
      _cdiv.querySelector(".cli-date").textContent = _d.getFullYear() + "年 " + _p2(_d.getMonth() + 1) + "月 " + _p2(_d.getDate()) + "日 · 星期" + _WEEK[_d.getDay()];
      var _mt = _cdiv.querySelector(".clk-media-time");
      if (_mt) _mt.textContent = _p2(_d.getHours()) + ":" + _p2(_d.getMinutes()) + ":" + _p2(_d.getSeconds());
    };
    _tickClock();
    var _tmr = setInterval(_tickClock, 1000);
    /* ===== 番茄钟 + 历史记录 + 开机/关机 ===== */
    var _clkState = { view: "time", power: true, pomo: "idle", type: "focus", remain: 25 * 60, total: 25 * 60 };
    var _VOLUME = 0.8, _AUTO_SWITCH = true, _BTN_SOUND = true, _masterG = null;
    try { _VOLUME = Math.max(0, Math.min(1, parseFloat(localStorage.getItem("wm_clock_vol") || "0.8"))); } catch (e) { }
    try { _AUTO_SWITCH = localStorage.getItem("wm_clock_auto") !== "0"; } catch (e) { }
    try { _BTN_SOUND = localStorage.getItem("wm_clock_sound") !== "0"; } catch (e) { }
    var _pomoTimer = null, _flashTimer = null, _clickTimer = null, _lastTimerClick = 0;
    /* 进度条宽度与计时设置行对齐 */
    function _syncBarWidth() {
      var b = _cdiv.querySelector(".clk-timer-bar"), s = _cdiv.querySelector(".clk-timer-set");
      if (b && s && s.offsetWidth > 0) b.style.width = s.offsetWidth + "px";
    }
    if (window.ResizeObserver) {
      try {
        var _roBar = new ResizeObserver(function () { if (_clkState.view === "timer") _syncBarWidth(); });
        _roBar.observe(_cdiv);
      } catch (e) { /* ignore */ }
    }
    var _FOCUS_MIN = 25, _BREAK_MIN = 5;
    var _histKey = "wm_clock_history", _histMax = 50;
    var _saveHist = function (type, minutes, label) {
      try {
        var list = JSON.parse(localStorage.getItem(_histKey) || "[]");
        list.unshift({ t: Date.now(), type: type, dur: minutes, label: label || "" });
        if (list.length > _histMax) list = list.slice(0, _histMax);
        localStorage.setItem(_histKey, JSON.stringify(list));
      } catch (e) { try { ["wm_clock_img","wm_clock_vid","wm_clock_vid_url"].forEach(function(k){localStorage.removeItem(k);}); localStorage.setItem(_histKey, JSON.stringify(list)); } catch (e2) { console.warn("[clock] save hist failed:", e, e2); } }
    };
    var _loadHist = function () {
      try { return JSON.parse(localStorage.getItem(_histKey) || "[]"); } catch (e) { return []; }
    };
    var _beep = function () {
      try {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        if (!_clkAC) _clkAC = new AC();
        if (!_masterG) { _masterG = _clkAC.createGain(); _masterG.connect(_clkAC.destination); _masterG.gain.value = _VOLUME; }
        if (!_masterG) { _masterG = _clkAC.createGain(); _masterG.connect(_clkAC.destination); _masterG.gain.value = _VOLUME; }
        if (_clkAC.state === "suspended") _clkAC.resume();
        var ctx = _clkAC, t = ctx.currentTime;
        [0, 250, 500].forEach(function (d) {
          var o = ctx.createOscillator(), g = ctx.createGain();
          o.connect(g); g.connect(_masterG);
          o.type = "sine"; o.frequency.value = 880;
          g.gain.setValueAtTime(0.001, ctx.currentTime + d / 1000);
          g.gain.exponentialRampToValueAtTime(0.25 * _VOLUME, ctx.currentTime + d / 1000 + 0.02);
          g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + d / 1000 + 0.22);
          o.start(ctx.currentTime + d / 1000); o.stop(ctx.currentTime + d / 1000 + 0.25);
        });
      } catch (e) { /* ignore */ }
    };
    /* 舒适钟声音效：C5-E5-G5 三音和弦 + 高八度泛音，指数衰减，柔和如风铃/寺钟 */
    var _chime = function () {
      try {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        if (!_clkAC) _clkAC = new AC();
        if (!_masterG) { _masterG = _clkAC.createGain(); _masterG.connect(_clkAC.destination); _masterG.gain.value = _VOLUME; }
        if (!_masterG) { _masterG = _clkAC.createGain(); _masterG.connect(_clkAC.destination); _masterG.gain.value = _VOLUME; }
        if (_clkAC.state === "suspended") _clkAC.resume();
        var ctx = _clkAC, t = ctx.currentTime;
        var notes = [523.25, 659.25, 783.99]; // C5 E5 G5
        notes.forEach(function (f, i) {
          var t0 = t + i * 0.22;
          [1, 2, 3].forEach(function (harm, hi) {
            var o = ctx.createOscillator(), g = ctx.createGain();
            o.connect(g); g.connect(_masterG);
            o.type = "sine";
            o.frequency.value = f * harm;
            var vol = (hi === 0 ? 0.22 : (hi === 1 ? 0.07 : 0.03)) * _VOLUME;
            g.gain.setValueAtTime(0.001, t0);
            g.gain.exponentialRampToValueAtTime(vol, t0 + 0.03);
            g.gain.exponentialRampToValueAtTime(0.001, t0 + 1.4);
            o.start(t0); o.stop(t0 + 1.5);
          });
        });
      } catch (e) { /* ignore */ }
    };
    /* 专注完成弹窗 */
    var _showCongrats = function () {
      try {
        var old = document.getElementById("clk-congrats");
        if (old && old.parentNode) old.parentNode.removeChild(old);
        var m = document.createElement("div");
        m.id = "clk-congrats";
        m.className = "clk-congrats";
        m.innerHTML =
          '<div class="clk-congrats-panel">' +
            '<div class="clk-congrats-icon">🍅</div>' +
            '<div class="clk-congrats-title">恭喜您完成一次专注！</div>' +
            '<div class="clk-congrats-sub">休息 ' + _BREAK_MIN + ' 分钟，喝口水放松一下吧</div>' +
            '<button type="button" class="clk-congrats-btn">太棒了</button>' +
          '</div>';
        document.body.appendChild(m);
        var _close = function () { if (m.parentNode) m.parentNode.removeChild(m); };
        m.querySelector(".clk-congrats-btn").addEventListener("click", function () { _btnSound(); _close(); });
        m.addEventListener("click", function (e) { if (e.target === m) _close(); });
        setTimeout(_close, 10000); // 10 秒后自动淡出，避免一直占屏
      } catch (e) { /* ignore */ }
    };

    var _clkAC = null;
    var _btnSound = function () {
      if (!_BTN_SOUND) return;
      try {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        if (!_clkAC) _clkAC = new AC();
        if (!_masterG) { _masterG = _clkAC.createGain(); _masterG.connect(_clkAC.destination); _masterG.gain.value = _VOLUME; }
        if (!_masterG) { _masterG = _clkAC.createGain(); _masterG.connect(_clkAC.destination); _masterG.gain.value = _VOLUME; }
        if (_clkAC.state === "suspended") _clkAC.resume();
        var ctx = _clkAC, t = ctx.currentTime;
        var buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.05), ctx.sampleRate);
        var d = buf.getChannelData(0);
        for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
        var src = ctx.createBufferSource(); src.buffer = buf;
        var bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 2200; bp.Q.value = 1.1;
        var g = ctx.createGain(); g.gain.value = 0.16;
        src.connect(bp); bp.connect(g); g.connect(_masterG);
        src.start(t);
        var o = ctx.createOscillator(); o.type = "square"; o.frequency.value = 300;
        var g2 = ctx.createGain();
        g2.gain.setValueAtTime(0.1, t);
        g2.gain.exponentialRampToValueAtTime(0.001, t + 0.045);
        o.connect(g2); g2.connect(_masterG);
        o.start(t); o.stop(t + 0.05);
      } catch (e) { /* ignore */ }
    };
    var _powerOnSound = function () {
      if (!_BTN_SOUND) return;
      try {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        if (!_clkAC) _clkAC = new AC();
        if (!_masterG) { _masterG = _clkAC.createGain(); _masterG.connect(_clkAC.destination); _masterG.gain.value = _VOLUME; }
        if (_clkAC.state === "suspended") _clkAC.resume();
        var ctx = _clkAC, t = ctx.currentTime;
        var o = ctx.createOscillator(); o.type = "sine"; o.frequency.value = 58;
        var g = ctx.createGain();
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.14, t + 0.18);
        g.gain.exponentialRampToValueAtTime(0.001, t + 0.6);
        o.connect(g); g.connect(_masterG);
        o.start(t); o.stop(t + 0.62);
        var o2 = ctx.createOscillator(); o2.type = "sine"; o2.frequency.value = 116;
        var g3 = ctx.createGain();
        g3.gain.setValueAtTime(0.0001, t);
        g3.gain.exponentialRampToValueAtTime(0.05, t + 0.15);
        g3.gain.exponentialRampToValueAtTime(0.001, t + 0.5);
        o2.connect(g3); g3.connect(_masterG);
        o2.start(t); o2.stop(t + 0.52);
        var buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.04), ctx.sampleRate);
        var d = buf.getChannelData(0);
        for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
        var src = ctx.createBufferSource(); src.buffer = buf;
        var bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 900; bp.Q.value = 1.5;
        var g4 = ctx.createGain(); g4.gain.value = 0.2;
        src.connect(bp); bp.connect(g4); g4.connect(_masterG);
        src.start(t);
      } catch (e) { /* ignore */ }
    };
    var _powerOffSound = function () {
      if (!_BTN_SOUND) return;
      try {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        if (!_clkAC) _clkAC = new AC();
        if (!_masterG) { _masterG = _clkAC.createGain(); _masterG.connect(_clkAC.destination); _masterG.gain.value = _VOLUME; }
        if (_clkAC.state === "suspended") _clkAC.resume();
        var ctx = _clkAC, t = ctx.currentTime;
        var buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * 0.09), ctx.sampleRate);
        var d = buf.getChannelData(0);
        for (var i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / d.length, 1.6);
        var src = ctx.createBufferSource(); src.buffer = buf;
        var bp = ctx.createBiquadFilter(); bp.type = "lowpass";
        bp.frequency.setValueAtTime(2500, t); bp.frequency.exponentialRampToValueAtTime(200, t + 0.09);
        var g = ctx.createGain(); g.gain.value = 0.28;
        src.connect(bp); bp.connect(g); g.connect(_masterG);
        src.start(t);
        var o = ctx.createOscillator(); o.type = "sine";
        o.frequency.setValueAtTime(180, t); o.frequency.exponentialRampToValueAtTime(45, t + 0.1);
        var g2 = ctx.createGain();
        g2.gain.setValueAtTime(0.18, t); g2.gain.exponentialRampToValueAtTime(0.001, t + 0.11);
        o.connect(g2); g2.connect(_masterG);
        o.start(t); o.stop(t + 0.12);
      } catch (e) { /* ignore */ }
    };

    var _flash = function () {
      if (_flashTimer) clearTimeout(_flashTimer);
      _cdiv.classList.add("clk-done");
      _flashTimer = setTimeout(function () { _cdiv.classList.remove("clk-done"); }, 1700);
    };
    var _setPomoTime = function () {
      var el = _cdiv.querySelector(".clk-timer-time");
      if (el) el.textContent = _p2(Math.floor(_clkState.remain / 60)) + ":" + _p2(_clkState.remain % 60);
      var st = _cdiv.querySelector(".clk-timer-state");
      if (st) st.textContent = (_clkState.type === "focus" ? "专注中" : "休息中") + " · " + (_clkState.type === "focus" ? _FOCUS_MIN : _BREAK_MIN) + " 分钟" + (_clkState.pomo === "pause" ? "（已暂停）" : "");
      var bar = _cdiv.querySelector(".clk-timer-bar i");
      if (bar) bar.style.width = Math.max(0, Math.min(100, (_clkState.total - _clkState.remain) / _clkState.total * 100)) + "%";
      var vf = _cdiv.querySelector('[data-setval="focus"]');
      if (vf) vf.textContent = _FOCUS_MIN;
      var vb = _cdiv.querySelector('[data-setval="break"]');
      if (vb) vb.textContent = _BREAK_MIN;
      var tg = _cdiv.querySelector('[data-tact="toggle"]');
      if (tg) tg.textContent = (_clkState.pomo === "run" ? "暂停" : "开始");
    };
    var _finishPhase = function () {
      var t = _clkState.type;
      var _lblInp = _cdiv.querySelector(".clk-pomo-label");
      var _lbl = _lblInp ? _lblInp.value.trim() : "";
      _saveHist(t, t === "focus" ? _FOCUS_MIN : _BREAK_MIN, _lbl);
      if (_lblInp) _lblInp.value = "";
      if (t === "focus") { _chime(); _showCongrats(); } else { _beep(); _flash(); }
      if (t === "focus") { _clkState.type = "break"; _clkState.remain = _BREAK_MIN * 60; _clkState.total = _BREAK_MIN * 60; }
      else { _clkState.type = "focus"; _clkState.remain = _FOCUS_MIN * 60; _clkState.total = _FOCUS_MIN * 60; }
      _clkState.pomo = "run";
      _setPomoTime();
    };
    var _pomoTick = function () {
      if (_clkState.pomo !== "run") return;
      _clkState.remain--;
      if (_clkState.remain <= 0) _finishPhase();
      _setPomoTime();
    };
    var _pomoStart = function () {
      if (_clkState.pomo === "run") return;
      _clkState.pomo = "run";
      if (_pomoTimer) clearInterval(_pomoTimer);
      _pomoTimer = setInterval(_pomoTick, 1000);
      _setPomoTime();
    };
    var _pomoPause = function () {
      _clkState.pomo = "pause";
      if (_pomoTimer) { clearInterval(_pomoTimer); _pomoTimer = null; }
      _setPomoTime();
    };
    var _pomoReset = function () {
      _pomoPause();
      _clkState.type = "focus"; _clkState.remain = _FOCUS_MIN * 60; _clkState.total = _FOCUS_MIN * 60;
      _setPomoTime();
    };
    var _delHist = function (idx) {
      try {
        var list = _loadHist();
        if (idx >= 0 && idx < list.length) list.splice(idx, 1);
        localStorage.setItem(_histKey, JSON.stringify(list));
      } catch (e) { /* ignore */ }
      _renderHist();
    };
    var _clearHist = function () {
      try { localStorage.removeItem(_histKey); } catch (e) { /* ignore */ }
      _renderHist();
    };
    var _calcAchieve = function (list) {
      var today = new Date(); today.setHours(0, 0, 0, 0);
      var focusAll = 0, focusToday = 0, nToday = 0, nAll = 0, streak = 0, days = {};
      list.forEach(function (it) {
        if (it.type !== "focus") return;
        var d = new Date(it.t);
        var k = d.getFullYear() + "-" + _p2(d.getMonth() + 1) + "-" + _p2(d.getDate());
        days[k] = true;
        nAll++; focusAll += (it.dur || 0);
        if (d >= today) { nToday++; focusToday += (it.dur || 0); }
      });
      var cur = new Date(today);
      var ck = cur.getFullYear() + "-" + _p2(cur.getMonth() + 1) + "-" + _p2(cur.getDate());
      if (!days[ck]) cur.setDate(cur.getDate() - 1);
      while (days[cur.getFullYear() + "-" + _p2(cur.getMonth() + 1) + "-" + _p2(cur.getDate())]) {
        streak++;
        cur.setDate(cur.getDate() - 1);
      }
      return { nAll: nAll, focusAll: focusAll, nToday: nToday, focusToday: focusToday, streak: streak };
    };
    var _renderHist = function () {
      var box = _cdiv.querySelector(".clk-hist-list");
      if (!box) return;
      var list = _loadHist();
      var ach = _calcAchieve(list);
      var ac = _cdiv.querySelector(".clk-achv");
      if (ac) {
        ac.innerHTML =
          '<div class="clk-achv-card"><b>' + ach.nToday + '</b><i>今日番茄</i></div>' +
          '<div class="clk-achv-card"><b>' + ach.focusToday + '</b><i>今日分钟</i></div>' +
          '<div class="clk-achv-card"><b>' + ach.nAll + '</b><i>累计番茄</i></div>' +
          '<div class="clk-achv-card"><b>' + ach.streak + '</b><i>连续天数</i></div>';
      }
      if (!list.length) {
        box.innerHTML = '<div class="clk-hist-empty">暂无记录<br>点「计时」开始第一个番茄</div>';
        return;
      }
      box.innerHTML = "";
      list.slice(0, 12).forEach(function (it, idx) {
        var d = new Date(it.t);
        var row = document.createElement("div");
        row.className = "clk-hist-row" + (it.type === "focus" ? " is-focus" : "");
        var _esc = function (x) { return String(x || "").replace(/[<>&"]/g, function (c) { return { "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]; }); };
        var lb = it.label || "";
        row.innerHTML = '<span class="clk-hist-dot"></span><span class="clk-hist-time">' +
          _p2(d.getMonth() + 1) + "-" + _p2(d.getDate()) + " " + _p2(d.getHours()) + ":" + _p2(d.getMinutes()) +
          '</span><span class="clk-hist-tag">' + (it.type === "focus" ? "专注" : "休息") + '</span>' + (lb ? '<span class="clk-hist-label">' + _esc(lb) + '</span>' : '') + '<span class="clk-hist-dur">' + it.dur + " 分</span>" +
          '<button type="button" class="clk-hist-del" title="删除这条记录">✕</button>';
        row.querySelector(".clk-hist-del").addEventListener("click", function () { _btnSound(); _delHist(idx); });
        box.appendChild(row);
      });
    };
    var _setView = function (v) {
      _clkState.view = v;
      ["time", "timer", "hist", "set", "media"].forEach(function (k) {
        var el = _cdiv.querySelector(".clk-view-" + k);
        if (el) el.style.display = (k === v ? "" : "none");
      });
      if (v === "hist") _renderHist();
      if (v === "timer") { _setPomoTime(); _syncBarWidth(); }
      if (v === "set") _renderSet();
      if (v === "media") { _showMedia(); _clockMaybeEnlarge(); }
      else _clockMaybeRestore();
    };
    var _adjustDur = function (set, delta) {
      if (_clkState.pomo === "run") { _flash(); return; }
      if (set === "focus") {
        _FOCUS_MIN = Math.max(1, Math.min(120, _FOCUS_MIN + delta));
        if (_clkState.type === "focus") { _clkState.remain = _FOCUS_MIN * 60; _clkState.total = _FOCUS_MIN * 60; }
      } else {
        _BREAK_MIN = Math.max(1, Math.min(60, _BREAK_MIN + delta));
        if (_clkState.type === "break") { _clkState.remain = _BREAK_MIN * 60; _clkState.total = _BREAK_MIN * 60; }
      }
      if (_clkState.pomo === "idle") { _clkState.type = "focus"; _clkState.remain = _FOCUS_MIN * 60; _clkState.total = _FOCUS_MIN * 60; }
      _setPomoTime();
    };
    var _setPower = function (on) {
      _clkState.power = on;
      _cdiv.classList.toggle("clk-off", !on);
    };
    var _onBtn = function (act) {
      if (!_clkState.power && act !== "power") { _pomoPause(); _setPower(true); }
      if (act === "power") {
        if (_clkState.power) { _pomoPause(); _setPower(false); }
        else { _setPower(true); _setView("time"); }
        return;
      } else if (act === "off") {
        _pomoPause();
        _setPower(false);
      } else if (act === "timer") {
        var now = Date.now();
        if (now - _lastTimerClick < 300) {
          _lastTimerClick = 0;
          if (_clickTimer) { clearTimeout(_clickTimer); _clickTimer = null; }
          _pomoReset();
          return;
        }
        _lastTimerClick = now;
        if (_clickTimer) clearTimeout(_clickTimer);
        _clickTimer = setTimeout(function () {
          _clickTimer = null;
          if (_clkState.view !== "timer") { _setView("timer"); return; }
          if (_clkState.pomo === "idle" || _clkState.pomo === "pause") _pomoStart();
          else _pomoPause();
        }, 260);
      } else if (act === "hist") {
        if (_clkState.view === "hist") _setView("time");
        else _setView("hist");
      } else if (act === "set") {
        if (_clkState.view === "set") _setView("time");
        else _setView("set");
      }
    };
    _cdiv.querySelectorAll(".clk-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        var _a = b.getAttribute("data-act");
        if (_a === "power") { if (_clkState.power) _powerOffSound(); else _powerOnSound(); } else _btnSound();
        _onBtn(_a);
      });
    });
    _cdiv.querySelectorAll(".clk-set-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        _btnSound(); _adjustDur(b.getAttribute("data-set"), parseInt(b.getAttribute("data-d"), 10) || 0);
      });
    });
    _cdiv.querySelectorAll(".clk-tbtn").forEach(function (b) {
      b.addEventListener("click", function () {
        _btnSound();
        var act = b.getAttribute("data-tact");
        if (act === "toggle") {
          if (_clkState.pomo === "run") _pomoPause();
          else _pomoStart();
        } else if (act === "reset") {
          _pomoReset();
        }
      });
    });
    var _clearBtn = _cdiv.querySelector(".clk-clear");
    if (_clearBtn) _clearBtn.addEventListener("click", function () {
      _btnSound();
      if (window.confirm("确定清空全部番茄记录？")) _clearHist();
    });
    var _scr = _cdiv.querySelector(".clk-screen");
    if (_scr) _scr.addEventListener("click", function () { if (!_clkState.power) { _powerOnSound(); _setPower(true); } });
    /* ===== 设置 + 媒体（导入图像/视频） ===== */
    var _mediaImg = null, _mediaVideo = null;
    try { _mediaImg = localStorage.getItem("wm_clock_media_img") || null; } catch (e) { _mediaImg = null; }
    try { _mediaVideo = localStorage.getItem("wm_clock_media_video") || null; } catch (e) { _mediaVideo = null; }
    var _mediaEmbed = null, _mediaEmbedSrc = null;
    try { var _me = JSON.parse(localStorage.getItem("wm_clock_media_embed") || "null"); if (_me && _me.embed) { _mediaEmbed = _me.embed; _mediaEmbedSrc = _me.src; } } catch (e) { _mediaEmbed = null; _mediaEmbedSrc = null; }
    var _setSw = function (opt, on) {
      var s = _cdiv.querySelector('.clk-switch[data-opt="' + opt + '"]');
      if (s) s.classList.toggle("on", !!on);
    };
    var _clkWinEnlarged = null, _clkWinEl = null;
    var _clockMaybeEnlarge = function () {
      if (!_mediaEmbed && !_mediaVideo) return;
      var w = _cdiv.closest ? _cdiv.closest(".app-window") : null;
      if (!w || _clkWinEnlarged) return;
      _clkWinEl = w; _clkWinEnlarged = { w: w.style.width, h: w.style.height };
      w.style.width = "920px"; w.style.height = "600px";
    };
    var _clockMaybeRestore = function () {
      if (!_clkWinEl || !_clkWinEnlarged) return;
      try { _clkWinEl.style.width = _clkWinEnlarged.w; _clkWinEl.style.height = _clkWinEnlarged.h; } catch (e) { }
      _clkWinEl = null; _clkWinEnlarged = null;
    };
    var _showMedia = function () {
      var img = _cdiv.querySelector(".clk-media-img"), vid = _cdiv.querySelector(".clk-media-video");
      var frame = _cdiv.querySelector(".clk-media-frame");
      var empty = _cdiv.querySelector(".clk-media-empty");
      _clkMediaErrClear();
      var has = !!(_mediaImg || _mediaVideo || _mediaEmbed);
      if (has) {
        frame.style.display = "none";
        if (_mediaEmbed) { if (frame.getAttribute("src") !== _mediaEmbed) frame.src = _mediaEmbed; frame.style.display = ""; img.style.display = "none"; vid.pause(); vid.style.display = "none"; }
        else if (_mediaImg && !_mediaVideo) { img.src = _mediaImg; img.style.display = ""; vid.style.display = "none"; vid.pause(); }
        else if (_mediaVideo) {
          vid.onerror = function () {
            _clkMediaError("视频加载失败：\n1. 地址不是视频直链（.mp4/.webm/.ogg）\n2. 视频站防盗链（直接引用被拒）\n3. 地址已失效或需要登录\n\n可换一个公开直链试试");
          };
          vid.onloadeddata = function () { _clkMediaErrClear(); };
          vid.src = _mediaVideo; vid.style.display = ""; img.style.display = "none";
          var pp = vid.play(); if (pp && pp["catch"]) pp["catch"](function () { });
        }
        empty.style.display = "none";
      } else {
        img.style.display = "none"; vid.style.display = "none"; vid.pause();
        frame.style.display = "none"; frame.src = "";
        empty.style.display = "";
      }
      var ci = _cdiv.querySelector('[data-clear="image"]'), cv = _cdiv.querySelector('[data-clear="video"]');
      if (ci) ci.style.display = _mediaImg ? "" : "none";
      if (cv) cv.style.display = _mediaVideo ? "" : "none";
      var cu = _cdiv.querySelector(".clk-media-clear");
      if (cu) cu.style.display = (_mediaVideo || _mediaEmbed) ? "" : "none";
    };
    var _renderSet = function () {
      var v = _cdiv.querySelector(".clk-vol");
      if (v) v.value = Math.round(_VOLUME * 100);
      var vv = _cdiv.querySelector(".clk-vol-val");
      if (vv) vv.textContent = Math.round(_VOLUME * 100) + "%";
      _setSw("auto", _AUTO_SWITCH);
      _setSw("sound", _BTN_SOUND);
      var uin = _cdiv.querySelector(".clk-vid-url");
      if (uin) {
        if (_mediaEmbedSrc) uin.value = _mediaEmbedSrc;
        else if (_mediaVideo && !/^data:/.test(_mediaVideo)) uin.value = _mediaVideo;
      }
      _showMedia();
    };
    var _clearMedia = function (type) {
      if (type === "image") { _mediaImg = null; try { localStorage.removeItem("wm_clock_media_img"); } catch (e) { } }
      else { _mediaVideo = null; _mediaEmbed = null; _mediaEmbedSrc = null; try { localStorage.removeItem("wm_clock_media_video"); } catch (e) { }
        try { localStorage.removeItem("wm_clock_media_embed"); } catch (e) { }
        var uin = _cdiv.querySelector(".clk-vid-url"); if (uin) uin.value = ""; }
      _clockMaybeRestore(); _showMedia();
    };
    var _readImgFile = function (file) {
      _mediaEmbed = null; _mediaEmbedSrc = null; try { localStorage.removeItem("wm_clock_media_embed"); } catch (e) { }
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var max = 1000, w = img.width, h = img.height, sc = Math.min(1, max / Math.max(w, h));
        var cv = document.createElement("canvas");
        cv.width = Math.max(1, Math.round(w * sc)); cv.height = Math.max(1, Math.round(h * sc));
        var cx = cv.getContext("2d");
        cx.drawImage(img, 0, 0, cv.width, cv.height);
        try { _mediaImg = cv.toDataURL("image/jpeg", 0.85); localStorage.setItem("wm_clock_media_img", _mediaImg); }
        catch (e) { _mediaImg = url; }
        _showMedia(); _setView("media");
      };
      img.onerror = function () { URL.revokeObjectURL(url); window.alert("无法读取该图片"); };
      img.src = url;
    };
    var _readVidFile = function (file) {
      _mediaEmbed = null; _mediaEmbedSrc = null; try { localStorage.removeItem("wm_clock_media_embed"); } catch (e) { }
      if (file.size > 4 * 1024 * 1024) {
        _mediaVideo = URL.createObjectURL(file);
        _showMedia(); _setView("media");
      } else {
        var rd = new FileReader();
        rd.onload = function () {
          _mediaVideo = rd.result;
          try { localStorage.setItem("wm_clock_media_video", _mediaVideo); } catch (e) { }
          _showMedia(); _setView("media");
        };
        rd.readAsDataURL(file);
      }
    };
    var _buildEmbedUrl = function (url) {
      var u = (url || "").trim(), l = u.toLowerCase(), m = null;
      if (l.indexOf("bilibili.com/video/") >= 0 || l.indexOf("b23.tv/") >= 0) {
        m = u.match(/(BV[0-9A-Za-z]+)/);
        if (m) return { embed: "https://player.bilibili.com/player.html?bvid=" + m[1] + "&autoplay=1&high_quality=1", label: "B站" };
      }
      if (l.indexOf("douyin.com/video/") >= 0) {
        m = u.match(/douyin\.com\/video\/([0-9]+)/i);
        if (m) return { embed: "https://www.douyin.com/embed/video/" + m[1], label: "抖音" };
      }
      if (l.indexOf("youtube.com/watch") >= 0 || l.indexOf("youtu.be/") >= 0) {
        m = u.match(/(?:[?&]v=|youtu\.be\/)([0-9A-Za-z_-]{6,})/);
        if (m) return { embed: "https://www.youtube.com/embed/" + m[1] + "?autoplay=1", label: "YouTube" };
      }
      if (l.indexOf("v.qq.com/") >= 0) {
        m = u.match(/[?&]vid=([0-9A-Za-z]+)/);
        if (!m) m = u.match(/\/([0-9A-Za-z]{6,})\.html/);
        if (m) return { embed: "https://v.qq.com/txp/iframe/player.html?vid=" + m[1] + "&autoplay=true", label: "腾讯视频" };
      }
      if (l.indexOf("v.youku.com/") >= 0) {
        m = u.match(/id_([0-9A-Za-z=]+)/);
        if (m) return { embed: "https://player.youku.com/embed/" + m[1], label: "优酷" };
      }
      return null;
    };
    var _loadVidUrl = function () {
      var inp = _cdiv.querySelector(".clk-vid-url");
      if (!inp) return;
      var url = (inp.value || "").trim();
      if (!url) { window.alert("请输入视频地址"); return; }
      var lower = url.toLowerCase();
      var isDirect = /\.(mp4|webm|ogg|m4v|mov)(\?|#|$)/.test(lower) || lower.indexOf("blob:") === 0 || lower.indexOf("data:") === 0;
      var isPage = /(bilibili|youtube|douyin|tiktok|youku|iqiyi|qq\.com|v\.qq|miguvideo|sohu|163\.com|ixigua|kuaishou|weibo)/.test(lower) && !isDirect;
      if (isPage) {
        var emb = _buildEmbedUrl(url);
        if (emb) {
          _mediaVideo = null; _mediaEmbed = emb.embed; _mediaEmbedSrc = url;
          try { localStorage.setItem("wm_clock_media_embed", JSON.stringify({ src: url, embed: emb.embed })); } catch (e) { }
          try { localStorage.removeItem("wm_clock_media_video"); } catch (e) { }
          _addVidHistory(url, emb.label);
          _showMedia(); _setView("media");
          return;
        }
        _clkMediaError("检测到这是[网页播放页]地址，当前平台暂不支持内嵌播放。\n\n可以尝试：\n1. 粘贴视频直链(.mp4/.webm/.ogg 结尾)\n2. B站等：F12 → Network → 筛选 media → 复制 .m4s/.mp4 请求地址\n3. 用公开 .mp4 直链");
        return;
      }
      if (!isDirect) {
        if (!window.confirm("该地址不以 .mp4/.webm/.ogg 结尾，可能不是视频直链，仍要尝试加载吗？")) return;
      }
      _mediaVideo = url;
      try { localStorage.setItem("wm_clock_media_video", url); } catch (e) { }
      _addVidHistory(url, "直链");
      _showMedia(); _setView("media");
    };
    var _clkMediaError = function (msg) {
      var errEl = _cdiv.querySelector(".clk-media-err");
      if (errEl) { errEl.textContent = msg; errEl.style.display = "block"; }
      window.alert(msg);
    };
    var _clkMediaErrClear = function () {
      var errEl = _cdiv.querySelector(".clk-media-err");
      if (errEl) errEl.style.display = "none";
    };
    var _vidHist = [];
    try { _vidHist = JSON.parse(localStorage.getItem("wm_clock_vid_history") || "[]"); } catch (e) { _vidHist = []; }
    var _escAttr = function (t) { return String(t).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;"); };
    var _addVidHistory = function (url, label) {
      _vidHist = _vidHist.filter(function (h) { return h.url !== url; });
      _vidHist.unshift({ url: url, label: label || "视频", time: Date.now() });
      if (_vidHist.length > 8) _vidHist.length = 8;
      try { localStorage.setItem("wm_clock_vid_history", JSON.stringify(_vidHist)); } catch (e) { }
      _renderVidHistory();
    };
    var _renderVidHistory = function () {
      var box = _cdiv.querySelector(".clk-vid-hist");
      if (!box) return;
      if (!_vidHist.length) { box.innerHTML = '<div class="clk-vid-hist-empty">暂无加载历史</div>'; return; }
      var html = '<div class="clk-vid-hist-hd"><span>加载历史</span><button type="button" class="clk-vid-hist-clearall" title="清空全部历史">清空</button></div>';
      _vidHist.forEach(function (h) {
        var u = _escAttr(h.url);
        html += '<div class="clk-vid-hist-item">' +
          '<span class="clk-vid-hist-tag">' + _escAttr(h.label || "视频") + '</span>' +
          '<button type="button" class="clk-vid-hist-re" data-url="' + u + '" title="点击重新加载">' + (h.url.length > 34 ? _escAttr(h.url.slice(0, 34)) + "…" : u) + '</button>' +
          '<button type="button" class="clk-vid-hist-del" data-url="' + u + '" title="删除">✕</button>' +
        '</div>';
      });
      box.innerHTML = html;
      box.querySelectorAll(".clk-vid-hist-re").forEach(function (b) {
        b.addEventListener("click", function () {
          _btnSound();
          var u = b.getAttribute("data-url");
          var inp = _cdiv.querySelector(".clk-vid-url");
          if (inp) inp.value = u;
          _loadVidUrl();
        });
      });
      box.querySelectorAll(".clk-vid-hist-del").forEach(function (b) {
        b.addEventListener("click", function () {
          _btnSound();
          var u = b.getAttribute("data-url");
          _vidHist = _vidHist.filter(function (h) { return h.url !== u; });
          try { localStorage.setItem("wm_clock_vid_history", JSON.stringify(_vidHist)); } catch (e) { }
          _renderVidHistory();
        });
      });
      var ca = box.querySelector(".clk-vid-hist-clearall");
      if (ca) ca.addEventListener("click", function () { _btnSound(); _vidHist = []; try { localStorage.removeItem("wm_clock_vid_history"); } catch (e) { } _renderVidHistory(); });
    };
    var _imgInput = _cdiv.querySelector(".clk-file-img"), _vidInput = _cdiv.querySelector(".clk-file-vid");
    if (_imgInput) _imgInput.addEventListener("change", function () { var f = this.files && this.files[0]; if (f) _readImgFile(f); this.value = ""; });
    if (_vidInput) _vidInput.addEventListener("change", function () { var f = this.files && this.files[0]; if (f) _readVidFile(f); this.value = ""; });
    _cdiv.querySelectorAll(".clk-set-file").forEach(function (b) {
      b.addEventListener("click", function () {
        _btnSound();
        if (b.getAttribute("data-media") === "image") { if (_imgInput) _imgInput.click(); }
        else { if (_vidInput) _vidInput.click(); }
      });
    });
    var _vlBtn = _cdiv.querySelector(".clk-vid-load");
    if (_vlBtn) _vlBtn.addEventListener("click", function () { _btnSound(); _loadVidUrl(); });
    var _mcBtn = _cdiv.querySelector(".clk-media-clear");
    if (_mcBtn) _mcBtn.addEventListener("click", function () { _btnSound(); _clearMedia("video"); _clockMaybeRestore(); _setView("clock"); });
    _renderVidHistory();
    var _vuInp = _cdiv.querySelector(".clk-vid-url");
    if (_vuInp) _vuInp.addEventListener("keydown", function (e) { if (e.key === "Enter") { _btnSound(); _loadVidUrl(); } });
    _cdiv.querySelectorAll(".clk-set-clear").forEach(function (b) {
      b.addEventListener("click", function () {
        _btnSound();
        _clearMedia(b.getAttribute("data-clear"));
      });
    });
    _cdiv.querySelectorAll(".clk-switch").forEach(function (s) {
      s.addEventListener("click", function () {
        _btnSound();
        var opt = s.getAttribute("data-opt");
        if (opt === "auto") { _AUTO_SWITCH = !_AUTO_SWITCH; try { localStorage.setItem("wm_clock_auto", _AUTO_SWITCH ? "1" : "0"); } catch (e) { } }
        else { _BTN_SOUND = !_BTN_SOUND; try { localStorage.setItem("wm_clock_sound", _BTN_SOUND ? "1" : "0"); } catch (e) { } }
        _renderSet();
      });
    });
    var _volEl = _cdiv.querySelector(".clk-vol");
    if (_volEl) _volEl.addEventListener("input", function () {
      _VOLUME = (parseInt(this.value, 10) || 0) / 100;
      try { localStorage.setItem("wm_clock_vol", String(_VOLUME)); } catch (e) { }
      if (_masterG) _masterG.gain.value = _VOLUME;
      var vv = _cdiv.querySelector(".clk-vol-val");
      if (vv) vv.textContent = Math.round(_VOLUME * 100) + "%";
    });
    _renderSet();
  }

  // 应用自定义默认尺寸（meta.json 可选 width/height），无记忆时居中打开
  var _defW = parseInt(a.width || "0", 10) || (a.name === "stock-app" ? 920 : 0);
  var _defH = parseInt(a.height || "0", 10) || (a.name === "stock-app" ? 780 : 0);
  // 恢复记忆的位置/大小（每个应用独立记忆）
  try {
    var r = JSON.parse(localStorage.getItem("app_win_rect_" + a.name) || "null");
    if (r && r.w && r.h) {
      win.style.left = r.x + "px";
      win.style.top = r.y + "px";
      var _w = r.w, _h = r.h;
      if (inlineClock) { _w = Math.max(_w, 400); _h = Math.max(_h, 360); }
      var _w2 = Math.min(_w, window.innerWidth - 24);
      var _h2 = Math.min(_h, window.innerHeight - 90);
      win.style.left = Math.min(Math.max(0, r.x), window.innerWidth - _w2) + "px";
      win.style.top = Math.min(Math.max(0, r.y), window.innerHeight - _h2) + "px";
      win.style.width = _w2 + "px";
      win.style.height = _h2 + "px";
    } else if (_defW && _defH) {
      var _dw = Math.min(_defW, window.innerWidth - 24);
      var _dh = Math.min(_defH, window.innerHeight - 90);
      win.style.left = Math.max(8, Math.round((window.innerWidth - _dw) / 2)) + "px";
      win.style.top = Math.max(8, Math.round((window.innerHeight - _dh) / 2)) + "px";
      win.style.width = _dw + "px";
      win.style.height = _dh + "px";
    }
  } catch (e) { /* 忽略 */ }
  bindFreeWindow(win, a.name);
  var closeBtn = win.querySelector(".aw-close");
  if (closeBtn) closeBtn.addEventListener("click", function () { win.remove(); });
  var fsBtn = win.querySelector(".aw-fs");
  if (fsBtn) fsBtn.addEventListener("click", function () {
    var fs = win.classList.toggle("fullscreen");
    if (win.querySelector(".aw-fs")) win.querySelector(".aw-fs").textContent = fs ? "还原" : "⛶";
    if (!fs) restoreWinRect(win, a.name);
  });
  var fc = win.querySelector(".frameless-close");
  if (fc) {
    fc.addEventListener("click", function (e) {
      e.stopPropagation();
      if (win.__closing) return;
      win.__closing = true;
      if (typeof _btnSound === "function") _btnSound();
      if (win.querySelector(".clock-inline")) {
        win.classList.add("clk-closing");
        setTimeout(function () { win.remove(); }, 1000);
      } else {
        win.remove();
      }
    });
    var frm = win.querySelector("iframe");
    if (frm && inlineClock) frm.style.pointerEvents = "none";
  }
  win.addEventListener("mousedown", function () { win.style.zIndex = 120; });
  window.__appWins = window.__appWins || [];
  window.__appWins.push(win);
  try { var _f0 = win.querySelector("iframe");
    if (_f0 && _f0.contentWindow) {
      _f0.contentWindow.postMessage({ type: "wm-model", provider: (state.activeProvider && state.activeProvider.key) || "", model: (state.activeProvider && state.activeProvider.model) || "" }, "*");
      _f0.addEventListener("load", function () { broadcastModel(); });
    }
  } catch (e) { /* 忽略 */ }
  // 所有窗口都顶到最前
  (window.__appWins || []).forEach(function (w) { if (w !== win && w.style.zIndex === "120") w.style.zIndex = 110; });
}

/** 恢复窗口位置（退出全屏时） */
function restoreWinRect(win, name) {
  try {
    var r = JSON.parse(localStorage.getItem("app_win_rect_" + name) || "null");
    if (r && r.w && r.h) {
      win.style.left = r.x + "px"; win.style.top = r.y + "px";
      win.style.width = r.w + "px"; win.style.height = r.h + "px";
    }
  } catch (e) { /* 忽略 */ }
}

/** 自由窗口行为：标题栏拖动 + 右下角缩放 + 记忆（多窗口共用） */
function bindFreeWindow(win, name) {
  var head = win.querySelector(".app-window-head");
  var fbar = win.querySelector(".frameless-drag");
  var dragTarget = head || fbar || win;
  var handle = win.querySelector(".app-window-resize");
  var save = function () {
    if (win.classList.contains("fullscreen")) return;
    try {
      localStorage.setItem("app_win_rect_" + name, JSON.stringify({
        x: win.offsetLeft, y: win.offsetTop, w: win.offsetWidth, h: win.offsetHeight
      }));
    } catch (e) { /* 忽略 */ }
  };
  dragTarget.addEventListener("mousedown", function (e) {
    if (e.target.closest("button")) return;
    if (e.target.closest("input, select, textarea")) return;
    if (e.target.closest("iframe")) return;
    if (win.classList.contains("fullscreen")) return;
    e.preventDefault();
    var sx = e.clientX, sy = e.clientY;
    var ox = win.offsetLeft, oy = win.offsetTop;
    win.classList.add("dragging");
    var onMove = function (ev) {
      var x = Math.max(-win.offsetWidth + 80, Math.min(window.innerWidth - 60, ox + (ev.clientX - sx)));
      var y = Math.max(0, Math.min(window.innerHeight - 40, oy + (ev.clientY - sy)));
      win.style.left = x + "px";
      win.style.top = y + "px";
    };
    var onUp = function () {
      win.classList.remove("dragging");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      save();
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
  handle.addEventListener("mousedown", function (e) {
    if (win.classList.contains("fullscreen")) return;
    e.preventDefault();
    e.stopPropagation();
    win.classList.add("dragging");
    var sx = e.clientX, sy = e.clientY;
    var ow = win.offsetWidth, oh = win.offsetHeight;
    var onMove = function (ev) {
      var w = Math.max(240, Math.min(window.innerWidth - win.offsetLeft, ow + (ev.clientX - sx)));
      var h = Math.max(180, Math.min(window.innerHeight - win.offsetTop, oh + (ev.clientY - sy)));
      win.style.width = w + "px";
      win.style.height = h + "px";
    };
    var onUp = function () {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      save();
      win.classList.remove("dragging");
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

/** 小应用窗口已改为多窗口动态创建（openAppWindow + bindFreeWindow），以下旧单例函数已废弃 */

/** 设置 → 通用 → 应用协作：启用开关 / 默认开启 / 打开 / 删除 */
function renderAppsManageList() {
  var list = document.getElementById("apps-list");
  if (!list) return;
  list.innerHTML = "";
  var apps = appsState.apps;
  if (!apps.length) {
    var empty = document.createElement("div");
    empty.className = "minor-note";
    empty.textContent = "还没有小应用";
    list.appendChild(empty);
    return;
  }
  apps.forEach(function (a) {
    var row = document.createElement("div");
    row.className = "app-manage-row";
    row.setAttribute("role", "listitem");
    var icon = document.createElement("span");
    icon.className = "app-manage-icon";
    icon.textContent = a.icon || "🧩";
    row.appendChild(icon);
    var info = document.createElement("div");
    info.className = "app-manage-info";
    var name = document.createElement("span");
    name.className = "app-manage-name";
    name.textContent = (a.title || a.name) + (a.builtin ? "（内置）" : "");
    var meta = document.createElement("span");
    meta.className = "app-manage-meta";
    meta.textContent = a.name;
    info.appendChild(name);
    info.appendChild(meta);
    row.appendChild(info);
    var openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "mini-action-btn";
    openBtn.textContent = "打开";
    openBtn.addEventListener("click", function () { openAppWindow(a); });
    row.appendChild(openBtn);
    // 内置/模型创建的小应用都可以删除
    var del = document.createElement("button");
    del.type = "button";
    del.className = "mini-action-btn danger";
    del.textContent = "✕";
    del.title = "删除应用";
    del.addEventListener("click", function () {
      if (!window.confirm("删除小应用「" + (a.title || a.name) + "」？")) return;
      fetch("/api/apps/" + encodeURIComponent(a.name), { method: "DELETE" })
        .then(function () { loadApps(); })
        .catch(function () { /* 忽略 */ });
    });
    row.appendChild(del);
    list.appendChild(row);
  });
}

/* ===========================================================================
 * 侧边栏收起/展开
 * ========================================================================= */

function applySidebarCollapsed(collapsed, remember) {
  var sidebar = document.getElementById("sidebar");
  var resizer = document.getElementById("sidebar-resizer");
  var expand = document.getElementById("sidebar-expand-btn");
  var main = document.getElementById("sidebar-main");
  if (!sidebar) return;
  var mobile = window.matchMedia && window.matchMedia("(max-width: 700px)").matches;
  if (mobile) {
    sidebar.classList.toggle("mobile-open", !collapsed);
    sidebar.style.width = "";
    if (main) main.style.display = "";
    if (resizer) resizer.style.display = "none";
    if (expand) expand.hidden = !collapsed;
    if (remember !== false) setPref("sidebar_collapsed", collapsed ? "1" : "0");
    return;
  }
  sidebar.classList.remove("mobile-open");
  if (collapsed) {
    // 收起：只隐藏历史面板（sidebar-main），保留项目图标列（rail）
    sidebar.style.width = "44px";
    if (main) main.style.display = "none";
    if (resizer) resizer.style.display = "none";
  } else {
    sidebar.style.width = "254px";
    if (main) main.style.display = "";
    if (resizer) resizer.style.display = "";
  }
  if (expand) expand.hidden = !collapsed;
  if (remember !== false) setPref("sidebar_collapsed", collapsed ? "1" : "0");
}

function initSidebarCollapse() {
  var collapseBtn = document.getElementById("sidebar-collapse-btn");
  var expandBtn = document.getElementById("sidebar-expand-btn");
  if (collapseBtn) {
    collapseBtn.addEventListener("click", function () { applySidebarCollapsed(true); });
  }
  if (expandBtn) {
    expandBtn.addEventListener("click", function () { applySidebarCollapsed(false); });
  }
  var mobileQuery = window.matchMedia ? window.matchMedia("(max-width: 700px)") : null;
  if (mobileQuery && mobileQuery.matches) {
    applySidebarCollapsed(true, false);
  } else if (getPref("sidebar_collapsed", "0") === "1") {
    applySidebarCollapsed(true, false);
  }
  if (mobileQuery && mobileQuery.addEventListener) {
    mobileQuery.addEventListener("change", function (event) {
      applySidebarCollapsed(event.matches ? true : getPref("sidebar_collapsed", "0") === "1", false);
    });
  }
}

function initApps() {
  var appsRefresh = document.getElementById("apps-refresh-btn");
  if (appsRefresh) appsRefresh.addEventListener("click", loadApps);
  // 顶部小应用：横向透明滚轮 —— 鼠标滚轮（垂直滚动）转为横向滚动
  var dock = document.getElementById("apps-dock");
  if (dock) {
    dock.addEventListener("wheel", function (e) {
      // 内容未溢出时不拦截，让页面正常滚动
      if (dock.scrollWidth <= dock.clientWidth + 1) return;
      var dx = Math.abs(e.deltaX) >= Math.abs(e.deltaY) ? (e.deltaX || 0) : (e.deltaY || 0);
      if (e.deltaMode === 1) dx *= 16;          // 行 → 像素
      else if (e.deltaMode === 2) dx *= dock.clientWidth; // 页
      var max = dock.scrollWidth - dock.clientWidth;
      var next = Math.min(Math.max(dock.scrollLeft + dx, 0), max);
      if (next !== dock.scrollLeft) {
        e.preventDefault(); // 阻止页面垂直滚动，只滚动 dock
        dock.scrollLeft = next;
      }
    }, { passive: false });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    // Esc 关闭最上层的小应用窗口（多窗口：关 zIndex 最高的）
    var wins = document.querySelectorAll(".app-window:not([hidden])");
    var top = null;
    wins.forEach(function (w) { if (!top || +w.style.zIndex >= +top.style.zIndex) top = w; });
    if (top) {
      if (top.classList.contains("fullscreen")) top.classList.remove("fullscreen");
      else top.remove();
    }
  });
  loadApps();
}

function init() {
  detectMarkdown();
  initTheme();
  renderSuggestions();
  bindEvents();
  loadProviders();
  renderProjects();
  renderHistoryList();
  initSidebarCollapse();
  initApps();
  initStatsAutoRefresh();
  if (window.WenmoAuth) window.WenmoAuth.init();
  initOnlineStatus();
  if (window.WenmoUpdate) window.WenmoUpdate.init();
  initOnboarding();
}

/* ===========================================================================
 * 新手引导（首次启动；参考 Chatbox 轻量引导：欢迎 → 选提供商 → 填 Key → 完成）
 * 完成标记 wenmo_onboarded；跳过也标记完成；已配置过任何 key 则不再显示
 * ========================================================================= */
var onboarding = {
  step: "welcome",          // welcome | provider | key
  selected: null,           // 选中的供应商 {key, name, ...}
  shown: false,
};

function initOnboarding() {
  // 已引导过 → 不再显示
  try {
    if (getPref("wenmo_onboarded", "0") === "1") return;
  } catch (e) { return; }
  var modal = document.getElementById("onboard-modal");
  if (!modal) return;

  // 等待 providers 加载完成再渲染（依赖 state.providers）
  var tries = 0;
  var timer = setInterval(function () {
    tries++;
    if ((state.providers || []).length > 0 || tries > 30) {
      clearInterval(timer);
      showOnboarding();
    }
  }, 200);
}

function showOnboarding() {
  var modal = document.getElementById("onboard-modal");
  if (!modal || onboarding.shown) return;
  onboarding.shown = true;
  modal.hidden = false;
  onboarding.step = "welcome";
  showOnboardPage("welcome");
  bindOnboardEvents();
}

function showOnboardPage(name) {
  onboarding.step = name;
  ["welcome", "provider", "key"].forEach(function (p) {
    var el = document.getElementById("onboard-page-" + p);
    if (el) el.hidden = (p !== name);
  });
  if (name === "provider") renderOnboardProviders();
  if (name === "key") renderOnboardKey();
}

/** 渲染供应商卡片网格（复用 state.providers，含 has_key 标记） */
function renderOnboardProviders() {
  var grid = document.getElementById("onboard-provider-grid");
  if (!grid) return;
  grid.innerHTML = "";
  var list = state.providers || [];
  // 已配置 key 的排前面，其余按顺序
  var ordered = list.slice().sort(function (a, b) { return (b.has_key ? 1 : 0) - (a.has_key ? 1 : 0); });
  ordered.forEach(function (p) {
    var card = document.createElement("div");
    card.className = "onboard-provider-card" + (p.has_key ? " selected" : "");
    card.dataset.key = p.key;
    var icon = document.createElement("span");
    icon.className = "op-icon";
    icon.textContent = providerIcon(p.key);
    var info = document.createElement("span");
    info.className = "op-info";
    var nm = document.createElement("span");
    nm.className = "op-name";
    nm.textContent = p.name || p.key;
    var md = document.createElement("span");
    md.className = "op-model";
    md.textContent = p.model || "";
    info.appendChild(nm);
    info.appendChild(md);
    card.appendChild(icon);
    card.appendChild(info);
    if (p.has_key) {
      var ok = document.createElement("span");
      ok.className = "op-ready";
      ok.textContent = "✓ 已配置";
      card.appendChild(ok);
    }
    card.addEventListener("click", function () {
      // 点选：已配置的也可直接选中完成；未配置的进入填 key 步
      grid.querySelectorAll(".onboard-provider-card").forEach(function (c) { c.classList.remove("selected"); });
      card.classList.add("selected");
      onboarding.selected = p;
      var next = document.getElementById("onboard-provider-next");
      if (next) next.disabled = false;
    });
    grid.appendChild(card);
  });
  var next = document.getElementById("onboard-provider-next");
  if (next) next.disabled = true;
}

/** 供应商图标（内置映射，兜底首字母） */
function providerIcon(key) {
  var map = {
    deepseek: "🟢", qianwen: "☁️", zhipu: "🧠", siliconflow: "💧",
    kimi: "🌙", doubao: "🫘", ollama: "🐳", local: "💻",
    zen: "⚡", opencode_go: "⚡",
  };
  return map[key] || (key ? key[0].toUpperCase() : "🤖");
}

/** 渲染填 key 步骤（选中供应商信息 + 输入框） */
function renderOnboardKey() {
  var p = onboarding.selected;
  var nameEl = document.getElementById("onboard-key-provider-name");
  if (nameEl && p) nameEl.textContent = (p.name || p.key) + "（" + (p.model || "默认模型") + "）";
  var input = document.getElementById("onboard-key-input");
  if (input) {
    input.value = "";
    input.focus();
  }
  var st = document.getElementById("onboard-key-status");
  if (st) { st.textContent = ""; st.className = "onboard-key-status"; }
  // 已配置 → 直接提示可完成
  if (p && p.has_key) {
    if (st) { st.textContent = "该供应商已配置 Key，可直接完成。"; st.className = "onboard-key-status ok"; }
  }
}

/** 保存 key（复用后端 POST /api/settings；不依赖设置弹窗 DOM） */
function onboardSaveKey() {
  var p = onboarding.selected;
  var input = document.getElementById("onboard-key-input");
  var st = document.getElementById("onboard-key-status");
  var apiKey = input ? input.value.trim() : "";
  if (!p) return;
  if (!apiKey && !p.has_key) {
    if (st) { st.textContent = "请输入 API Key，或点「暂时跳过」。"; st.className = "onboard-key-status err"; }
    return;
  }
  if (st) { st.textContent = "保存中…"; st.className = "onboard-key-status"; }
  fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: p.key, api_key: apiKey }),
  })
    .then(function (r) { return r.json().catch(function () { return {}; }); })
    .then(function (d) {
      if (d && d.ok === false) {
        if (st) { st.textContent = "保存失败：" + (d.detail || "未知错误"); st.className = "onboard-key-status err"; }
        return;
      }
      // 同步本地状态
      (state.providers || []).forEach(function (x) { if (x.key === p.key && apiKey) x.has_key = true; });
      if (p.has_key || apiKey) finishOnboarding();
    })
    .catch(function () {
      if (st) { st.textContent = "保存失败（网络错误）"; st.className = "onboard-key-status err"; }
    });
}

/** 完成引导：标记 wenmo_onboarded + 关闭遮罩 */
function finishOnboarding() {
  try { setPref("wenmo_onboarded", "1"); } catch (e) {}
  var modal = document.getElementById("onboard-modal");
  if (modal) modal.hidden = true;
  onboarding.shown = false;
  // 若选中的供应商未激活，切过去
  if (onboarding.selected && state.activeProvider && state.activeProvider.key !== onboarding.selected.key) {
    selectProvider(onboarding.selected);
  }
}

/** 绑定引导按钮事件（只绑一次） */
function bindOnboardEvents() {
  var start = document.getElementById("onboard-start");
  if (start) start.addEventListener("click", function () { showOnboardPage("provider"); });
  var skip = document.getElementById("onboard-skip");
  if (skip) skip.addEventListener("click", finishOnboarding);
  var back1 = document.getElementById("onboard-provider-back");
  if (back1) back1.addEventListener("click", function () { showOnboardPage("welcome"); });
  var next = document.getElementById("onboard-provider-next");
  if (next) next.addEventListener("click", function () {
    if (!onboarding.selected) return;
    showOnboardPage("key");
  });
  var back2 = document.getElementById("onboard-key-back");
  if (back2) back2.addEventListener("click", function () { showOnboardPage("provider"); });
  var save = document.getElementById("onboard-key-save");
  if (save) save.addEventListener("click", onboardSaveKey);
  var kskip = document.getElementById("onboard-key-skip");
  if (kskip) kskip.addEventListener("click", finishOnboarding);
  // Enter 键保存
  var input = document.getElementById("onboard-key-input");
  if (input) input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); onboardSaveKey(); }
  });
}

/* ---------- 联网状态：自动检测（电脑联网则默认联网，断网则默认未联网）---------- */
function initOnlineStatus() {
  // 初始化：navigator.onLine 反映本机网络状态
  state.online = navigator.onLine === true;
  // 监听网络变化实时更新
  window.addEventListener("online", function () { state.online = true; });
  window.addEventListener("offline", function () { state.online = false; });
}
/** 统计栏（上下文/本对话/缓存/命中率/费用）每 60 秒自动刷新一次，
 *  不再只依赖"对话发生时"更新；页面隐藏时暂停，回到前台立即补刷。 */
function initStatsAutoRefresh() {
  // 页面隐藏 → 暂停；回到前台 → 立即补刷 + 恢复周期
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (state.statsTimer) { clearInterval(state.statsTimer); state.statsTimer = null; }
    } else {
      updateTokenUsage();
      if (!state.statsTimer) {
        state.statsTimer = setInterval(function () { updateTokenUsage(); }, 60000);
      }
    }
  });
  if (!state.statsTimer) {
    state.statsTimer = setInterval(function () { updateTokenUsage(); }, 60000);
  }
}

init();


/* ==== Formula Copy Enhanced v2 (OMML) ====
 * 复制对话中的 KaTeX 公式 -> Word 原生公式（OMML）
 * MathJax Copy-to-Office 同款实现：MathML 经 MML2OMML.XSL 转 OMML
 */
(function () {
  'use strict';
  if (typeof XSLTProcessor === 'undefined' || typeof DOMParser === 'undefined') {
    return; // 老浏览器不支持，保持默认复制
  }
  var XSL_URL = 'vendor/MML2OMML.XSL';
  var xslDoc = null;
  var xslReady = false;

  /* 预加载微软官方 MathML->OMML 转换器 */
  function loadXSL() {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open('GET', XSL_URL, true);
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;
        if (xhr.status === 200) {
          try {
            xslDoc = new DOMParser().parseFromString(xhr.responseText, 'text/xml');
            if (xslDoc.getElementsByTagName('parsererror').length) {
              console.error('[FormulaCopy] MML2OMML.XSL 解析失败');
              return;
            }
            xslReady = true;
            console.log('[FormulaCopy] MML2OMML.XSL 就绪');
          } catch (e) {
            console.error('[FormulaCopy] XSL 解析异常', e);
          }
        } else {
          console.error('[FormulaCopy] 加载 MML2OMML.XSL 失败，状态', xhr.status);
        }
      };
      xhr.send();
    } catch (e) {
      console.error('[FormulaCopy] XSL 加载异常', e);
    }
  }
  loadXSL();

  /* MathML 元素 -> OMML 字符串 */
  function mathmlToOMML(mathEl) {
    if (!xslReady) return null;
    try {
      var clone = mathEl.cloneNode(true);
      if (!clone.getAttribute('xmlns')) {
        clone.setAttribute('xmlns', 'http://www.w3.org/1998/Math/MathML');
      }
      var serializer = new XMLSerializer();
      var mathmlStr = serializer.serializeToString(clone);
      var xmlDoc = new DOMParser().parseFromString(mathmlStr, 'text/xml');
      if (xmlDoc.getElementsByTagName('parsererror').length) return null;
      var processor = new XSLTProcessor();
      processor.importStylesheet(xslDoc);
      var frag = processor.transformToFragment(xmlDoc, document);
      return serializer.serializeToString(frag);
    } catch (e) {
      console.error('[FormulaCopy] OMML 转换失败', e);
      return null;
    }
  }

  /* 从选区提取被选中的 KaTeX 公式元素 */
  function extractFormulas(sel) {
    var formulas = [];
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return formulas;
    var range = sel.getRangeAt(0);
    var node = range.commonAncestorContainer;
    var root = node.nodeType === 1 ? node : node.parentNode;
    var scope = root;
    while (scope && scope !== document.body && !scope.querySelector) {
      scope = scope.parentNode;
    }
    if (!scope || !scope.querySelector) scope = document.body;
    var katexEls = scope.querySelectorAll('.katex');
    for (var i = 0; i < katexEls.length; i++) {
      var k = katexEls[i];
      if (typeof range.intersectsNode === 'function' && range.intersectsNode(k)) {
        formulas.push(k);
      }
    }
    return formulas;
  }

  /* 从 KaTeX 元素里取隐藏 MathML（KaTeX 双格式输出自带） */
  function getMathEl(katexEl) {
    var wrap = katexEl.querySelector('.katex-mathml');
    if (wrap) {
      var m = wrap.querySelector('math');
      if (m) return m;
    }
    return katexEl.querySelector('math') || null;
  }

  document.addEventListener('copy', function (e) {
    try {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed) return;
      var formulas = extractFormulas(sel);
      if (!formulas.length) return; // 未选中公式 -> 默认复制

      var oMathParts = [];
      var plainParts = [];
      var hasOMML = false;

      for (var i = 0; i < formulas.length; i++) {
        var mathEl = getMathEl(formulas[i]);
        if (!mathEl) continue;
        var omml = mathmlToOMML(mathEl);
        if (omml) {
          oMathParts.push(omml);
          hasOMML = true;
        }
        var latex = formulas[i].getAttribute('aria-label');
        if (latex) plainParts.push(latex);
      }
      if (!hasOMML) return; // 转换失败 -> 保持默认

      var ommlHtml = oMathParts.join('');
      var fullHtml = '<html xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ' +
        'xmlns:o="urn:schemas-microsoft-com:office:office" ' +
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
        '<body>' + ommlHtml + '</body></html>';

      e.clipboardData.setData('text/html', fullHtml);
      e.clipboardData.setData('text/mathml', ommlHtml);
      if (plainParts.length) {
        e.clipboardData.setData('text/plain', plainParts.join(' '));
      }
      e.preventDefault();
      console.log('[FormulaCopy] 已写入 OMML 公式到剪贴板');
    } catch (err) {
      console.error('[FormulaCopy] 复制拦截异常', err);
    }
  }, true);
})();



/* ============ 智能复制补丁 v3：消息栏"复制"按钮支持公式 OMML ============ */
(function () {
  var xslDoc = null;
  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function ensureXSL(cb) {
    if (xslDoc) return cb(xslDoc);
    var xhr = new XMLHttpRequest();
    xhr.open("GET", "vendor/MML2OMML.XSL", true);
    xhr.onload = function () {
      if (xhr.status === 200) {
        try { xslDoc = new DOMParser().parseFromString(xhr.responseText, "text/xml"); }
        catch (e) { xslDoc = null; }
      }
      cb(xslDoc);
    };
    xhr.onerror = function () { cb(null); };
    xhr.send();
  }
  function mathmlToOMMLStr(mathEl, xsl) {
    try {
      var clone = mathEl.cloneNode(true);
      clone.setAttribute("xmlns", "http://www.w3.org/1998/Math/MathML");
      var xmlStr = new XMLSerializer().serializeToString(clone);
      var xmlDoc = new DOMParser().parseFromString(xmlStr, "text/xml");
      var proc = new XSLTProcessor();
      proc.importStylesheet(xsl);
      var frag = proc.transformToFragment(xmlDoc, document);
      return new XMLSerializer().serializeToString(frag);
    } catch (e) { return ""; }
  }
  var VOID_TAGS = { br:1, img:1, hr:1, input:1, meta:1, link:1, wbr:1, col:1, area:1, base:1, embed:1, source:1, track:1 };
  function serializeWithOMML(node, xsl, parts) {
    if (node.nodeType === Node.TEXT_NODE) { parts.push(escapeHtml(node.textContent)); return; }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.classList && node.classList.contains("math")) {
      var mathEl = node.querySelector(".katex-mathml math") || node.querySelector("math");
      var omml = mathEl ? mathmlToOMMLStr(mathEl, xsl) : "";
      if (omml) { parts.push(omml); return; }
      var r=node.querySelector(".math-render");var la=node.getAttribute?node.getAttribute("data-latex")||"":"";
      parts.push(escapeHtml(r?r.textContent:(la||node.textContent||"")));
      return;
    }
    var tag = node.tagName.toLowerCase();
    if (tag==="button"||tag==="script"||tag==="style") return;
    var attrs = "";
    for (var i = 0; i < node.attributes.length; i++) {
      var a = node.attributes[i];
      if (a.name === "data-latex") continue;
      if (/^on/i.test(a.name)) continue;
      attrs += " " + a.name + '="' + escapeHtml(a.value) + '"';
    }
    if (VOID_TAGS[tag]) { parts.push("<" + tag + attrs + "/>"); return; }
    parts.push("<" + tag + attrs + ">");
    for (var j = 0; j < node.childNodes.length; j++) serializeWithOMML(node.childNodes[j], xsl, parts);
    parts.push("</" + tag + ">");
  }
  function findMsgRoot(btn) {
    var el = btn.parentElement;
    while (el && el !== document.body) {
      if (el.querySelector && el.querySelector(".md")) return el;
      el = el.parentElement;
    }
    return null;
  }
  function writeClipboard(html, plain, cb) {
    var done = function () { cb(true); };
    var fail = function () { cb(false); };
    try {
      if (navigator.clipboard && window.ClipboardItem) {
        navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([html], { type: "text/html" }),
            "text/plain": new Blob([plain], { type: "text/plain" })
          })
        ]).then(done, fail);
      } else {
        // 无 Clipboard API 时：富文本复制，保证 Word 粘贴仍识别公式
        var div = document.createElement("div");
        div.innerHTML = html;
        div.style.position = "fixed"; div.style.left = "-9999px"; div.style.top = "0";
        div.setAttribute("contenteditable", "true");
        document.body.appendChild(div);
        var range = document.createRange();
        range.selectNodeContents(div);
        var sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(range);
        try { document.execCommand("copy"); done(); } catch (e) { fail(); }
        sel.removeAllRanges();
        document.body.removeChild(div);
      }
    } catch (e) { fail(); }
  }
  window.__formulaXSL={ensureXSL:ensureXSL,mathmlToOMML:function(m){return xslDoc?mathmlToOMMLStr(m,xslDoc):"";},get ready(){return !!xslDoc;}};
  document.addEventListener("click", function (e) {
    var t = e.target;
    var btn = t && t.closest ? t.closest('button[aria-label="复制消息"]') : null;
    if (!btn) return;
    var root = findMsgRoot(btn);
    if (!root) return;
    var md = root.querySelector(".md");
    if (!md || !md.querySelector(".math")) return;
    e.preventDefault();
    e.stopPropagation();
    var origHTML = btn.innerHTML;
    btn.textContent = "复制中…";
    ensureXSL(function (xsl) {
      if (!xsl) {
        btn.textContent = "失败";
        setTimeout(function () { btn.innerHTML = origHTML; }, 1500);
        return;
      }
      var parts = [];
      for (var i = 0; i < md.childNodes.length; i++) serializeWithOMML(md.childNodes[i], xsl, parts);
      var bodyHtml = parts.join("");
      var html = '<html xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ' +
                 'xmlns:o="urn:schemas-microsoft-com:office:office" ' +
                 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
                 "<body>" + bodyHtml + "</body></html>";
      var plain = md.innerText || md.textContent || "";
      writeClipboard(html, plain, function (ok) {
        btn.textContent = ok ? "已复制" : "复制失败";
        setTimeout(function () { btn.innerHTML = origHTML; }, 1500);
      });
    });
  }, true);
})();
