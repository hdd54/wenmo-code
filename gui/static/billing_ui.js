(function () {
  "use strict";

  var data = null;
  var windowName = "all";
  var tab = "conv";

  function requestJson(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.detail || ("HTTP " + response.status));
        return payload;
      });
    });
  }

  function inWindow(conversation) {
    if (windowName === "all" || !conversation.updated) return true;
    var days = windowName === "today" ? 1
      : (windowName === "week" ? 7 : (windowName === "month" ? 30 : 365));
    return conversation.updated >= Date.now() / 1000 - days * 86400;
  }

  function renderSummary(conversations) {
    var total = conversations.reduce(function (sum, item) { return sum + (item.cost || 0); }, 0);
    var unknown = conversations.filter(function (item) { return item.cost == null; }).length;
    document.getElementById("billing-total").textContent = "¥" + total.toFixed(2)
      + (unknown ? " + " + unknown + " 项订阅/未计价" : "");
    var note = document.getElementById("billing-basis-note");
    if (note) {
      note.textContent = "对话总计仍是本地估算，不是供应商账单；实际费用以供应商账单为准。"
        + (data && data.invoice_reconciled ? " 下方已接入供应商实账，可查看差异。" : "")
        + (conversations.some(function (item) { return item.cost_unavailable; })
          ? " 当前范围还包含部分未计价调用。" : "");
    }
  }

  function renderReconciliation() {
    var container = document.getElementById("billing-reconciliation");
    if (!container) return;
    container.textContent = "";
    var rows = (data && data.reconciliation) || [];
    if (!rows.length) {
      container.textContent = "尚未同步供应商账单或官方使用量。";
      return;
    }
    rows.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "billing-reconcile-row";
      var title = document.createElement("strong");
      title.textContent = item.provider + (item.invoice_reconciled ? " · 实际成本" : " · 官方使用量");
      var detail = document.createElement("span");
      if (typeof item.actual_cost === "number") {
        detail.textContent = "供应商 ¥" + item.actual_cost.toFixed(4)
          + " / 本地 ¥" + Number(item.local_estimate || 0).toFixed(4)
          + " / 差异 ¥" + Number(item.variance || 0).toFixed(4);
      } else {
        var providerTokens = item.provider_tokens || {};
        detail.textContent = "供应商 tokens "
          + Number((providerTokens.input || 0) + (providerTokens.output || 0)).toLocaleString()
          + "；该接口不返回发票金额";
      }
      var scope = document.createElement("small");
      scope.textContent = item.scope_quality === "exact_events"
        ? "本地侧按逐次调用时间精确对齐"
        : "包含旧历史的会话更新时间代理，差异仅供核查";
      row.appendChild(title);
      row.appendChild(detail);
      row.appendChild(scope);
      container.appendChild(row);
    });
  }

  function loadConnectors() {
    return requestJson("/api/billing/connectors").then(function (payload) {
      var select = document.getElementById("billing-connector");
      if (!select) return;
      var previous = select.value;
      select.textContent = "";
      (payload.connectors || []).forEach(function (connector) {
        var option = new Option(
          connector.label + (connector.configured ? "（已配置）" : "（未配置）"),
          connector.provider);
        option.dataset.configured = connector.configured ? "1" : "0";
        select.appendChild(option);
      });
      if (previous) select.value = previous;
    });
  }

  function setSyncStatus(message, isError) {
    var status = document.getElementById("billing-sync-status");
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("error", !!isError);
  }

  function saveAdminKey(clear) {
    var select = document.getElementById("billing-connector");
    var input = document.getElementById("billing-admin-key");
    var provider = select && select.value;
    var key = clear ? "" : ((input && input.value) || "").trim();
    if (!provider) return;
    if (!clear && !key) {
      setSyncStatus("请输入管理密钥；留空不会覆盖现有密钥。", true);
      return;
    }
    setSyncStatus(clear ? "正在清除…" : "正在用 DPAPI 加密保存…");
    requestJson("/api/billing/credentials", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: provider, admin_key: key })
    }).then(function () {
      if (input) input.value = "";
      setSyncStatus(clear ? "该租户的管理密钥已清除。" : "管理密钥已按租户加密保存。", false);
      return loadConnectors();
    }).catch(function (error) { setSyncStatus("保存失败：" + error.message, true); });
  }

  function syncProvider() {
    var select = document.getElementById("billing-connector");
    var provider = select && select.value;
    if (!provider) return;
    setSyncStatus("正在从供应商官方管理接口同步近 30 天数据…");
    requestJson("/api/billing/sync", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: provider, days: 30 })
    }).then(function (payload) {
      data = payload.billing;
      render();
      renderReconciliation();
      setSyncStatus("同步完成。管理密钥未返回浏览器。", false);
    }).catch(function (error) { setSyncStatus("同步失败：" + error.message, true); });
  }

  function renderConversations(conversations) {
    var list = document.getElementById("billing-conv-list");
    if (!list) return;
    list.innerHTML = "";
    if (!conversations.length) {
      list.innerHTML = '<div class="billing-empty">该时间范围内暂无计费对话</div>';
      return;
    }
    conversations.forEach(function (conversation) {
      var row = document.createElement("div");
      row.className = "billing-conv-row";
      row.title = "点击进入该对话";
      var info = document.createElement("div");
      info.className = "billing-conv-info";
      var title = document.createElement("div");
      title.className = "billing-conv-title";
      title.textContent = conversation.title || "未命名对话";
      var meta = document.createElement("div");
      meta.className = "billing-conv-meta";
      var when = conversation.updated && typeof window.fmtTime === "function"
        ? window.fmtTime(Math.floor(conversation.updated)) : "";
      meta.textContent = (conversation.billing_mode === "mixed" ? "多供应商/模型"
        : ((conversation.provider || "") + " · " + (conversation.model || "")))
        + (when ? " · " + when : "")
        + (conversation.tokens ? " · " + (conversation.tokens / 1000).toFixed(0) + "k tokens" : "");
      info.appendChild(title);
      info.appendChild(meta);
      var cost = document.createElement("div");
      cost.className = "billing-conv-cost";
      cost.textContent = conversation.cost == null
        ? (conversation.billing_mode === "subscription" ? "订阅制" : "未计价")
        : "¥" + Number(conversation.cost).toFixed(4) + (conversation.cost_est ? " 估" : "")
          + (conversation.cost_unavailable ? "（部分）" : "");
      row.appendChild(info);
      row.appendChild(cost);
      row.addEventListener("click", function () {
        document.getElementById("billing-modal").hidden = true;
        if (typeof window.closeSettings === "function") window.closeSettings();
        if (typeof window.loadConversation === "function") window.loadConversation(conversation.cid);
      });
      list.appendChild(row);
    });
  }

  function modelRows(conversations) {
    var totals = {};
    conversations.forEach(function (conversation) {
      (conversation.model_costs || []).forEach(function (entry) {
        if (typeof entry.cost !== "number") return;
        var key = (entry.provider || "") + "/" + (entry.model || "");
        if (!totals[key]) totals[key] = {
          provider: entry.provider || "", model: entry.model || "", cost: 0, convs: 0
        };
        totals[key].cost += entry.cost;
        totals[key].convs += 1;
      });
    });
    return Object.keys(totals).map(function (key) { return totals[key]; })
      .sort(function (left, right) { return right.cost - left.cost; });
  }

  function renderModels(conversations) {
    var bars = document.getElementById("billing-model-bars");
    var daily = document.getElementById("billing-daily-bars");
    if (!bars || !daily) return;
    var models = windowName === "all" ? (data.by_model || []) : modelRows(conversations);
    bars.innerHTML = "";
    if (!models.length) {
      bars.innerHTML = '<div class="billing-empty">该时间范围暂无供应商计费</div>';
    } else {
      var maxCost = Math.max.apply(null, models.map(function (item) { return item.cost; })) || 1;
      models.forEach(function (item) {
        var row = document.createElement("div");
        row.className = "billing-bar-row";
        var label = document.createElement("div");
        label.className = "billing-bar-label";
        label.textContent = (item.provider || "?") + " / " + (item.model || "?");
        label.title = label.textContent;
        var track = document.createElement("div");
        track.className = "billing-bar-track";
        var fill = document.createElement("div");
        fill.className = "billing-bar-fill";
        fill.style.width = Math.max(2, item.cost / maxCost * 100) + "%";
        track.appendChild(fill);
        var value = document.createElement("div");
        value.className = "billing-bar-val";
        value.textContent = "¥" + item.cost.toFixed(2);
        row.appendChild(label);
        row.appendChild(track);
        row.appendChild(value);
        bars.appendChild(row);
      });
    }
    daily.innerHTML = "";
    var days = data.daily || [];
    if (!days.length) {
      daily.innerHTML = '<div class="billing-empty">暂无每日费用数据</div>';
      return;
    }
    var maxDay = Math.max.apply(null, days.map(function (item) { return item.cost; })) || 1;
    var row = document.createElement("div");
    row.className = "billing-daily-row";
    days.forEach(function (item) {
      var cell = document.createElement("div");
      cell.className = "billing-daily-cell";
      cell.title = item.date + ": ¥" + item.cost.toFixed(4);
      var fill = document.createElement("div");
      fill.className = "billing-daily-fill";
      fill.style.height = Math.max(3, item.cost / maxDay * 100) + "%";
      cell.appendChild(fill);
      var label = document.createElement("div");
      label.className = "billing-daily-lbl";
      label.textContent = item.date;
      cell.appendChild(label);
      row.appendChild(cell);
    });
    daily.appendChild(row);
  }

  function render() {
    if (!data) return;
    var conversations = (data.by_conv || []).filter(inWindow);
    renderSummary(conversations);
    renderReconciliation();
    if (tab === "conv") renderConversations(conversations);
    else renderModels(conversations);
  }

  function open() {
    var modal = document.getElementById("billing-modal");
    if (!modal) return;
    modal.hidden = false;
    document.getElementById("billing-total").textContent = "加载中…";
    Promise.all([requestJson("/api/billing"), loadConnectors()]).then(function (results) {
      var payload = results[0];
      data = payload;
      render();
    }).catch(function () {
      document.getElementById("billing-total").textContent = "加载失败";
    });
  }

  function init() {
    var button = document.getElementById("gen-billing-btn");
    if (button && !button.dataset.filled) {
      button.dataset.filled = "1";
      button.addEventListener("click", open);
    }
    var modal = document.getElementById("billing-modal");
    if (!modal || modal.dataset.initialized) return;
    modal.dataset.initialized = "1";
    modal.querySelectorAll("[data-billing-close]").forEach(function (element) {
      element.addEventListener("click", function () { modal.hidden = true; });
    });
    var saveKey = document.getElementById("billing-key-save");
    if (saveKey) saveKey.addEventListener("click", function () { saveAdminKey(false); });
    var clearKey = document.getElementById("billing-key-clear");
    if (clearKey) clearKey.addEventListener("click", function () {
      if (window.confirm("清除该租户保存的供应商管理密钥？")) saveAdminKey(true);
    });
    var sync = document.getElementById("billing-sync");
    if (sync) sync.addEventListener("click", syncProvider);
    modal.querySelectorAll(".bw-btn").forEach(function (buttonElement) {
      buttonElement.addEventListener("click", function () {
        modal.querySelectorAll(".bw-btn").forEach(function (item) { item.classList.remove("active"); });
        buttonElement.classList.add("active");
        windowName = buttonElement.dataset.bw;
        render();
      });
    });
    modal.querySelectorAll(".bt-tab").forEach(function (buttonElement) {
      buttonElement.addEventListener("click", function () {
        modal.querySelectorAll(".bt-tab").forEach(function (item) { item.classList.remove("active"); });
        buttonElement.classList.add("active");
        tab = buttonElement.dataset.bt;
        document.getElementById("billing-conv").hidden = tab !== "conv";
        document.getElementById("billing-model").hidden = tab !== "model";
        render();
      });
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !modal.hidden) modal.hidden = true;
    });
  }

  window.WenmoBilling = { init: init, open: open };
})();
