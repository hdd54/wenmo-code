(function () {
  "use strict";

  var bound = false;
  var policy = { default: "ask", tools: {}, paths: [], commands: [] };
  var sandboxSetupCommand = "";

  function el(id) { return document.getElementById(id); }

  function effectSelect(value) {
    var select = document.createElement("select");
    select.className = "ctx-input perm-effect";
    [["allow", "允许"], ["ask", "询问"], ["deny", "拒绝"]].forEach(function (item) {
      var option = new Option(item[1], item[0]);
      select.appendChild(option);
    });
    select.value = value || "ask";
    return select;
  }

  function addRuleRow(kind, pattern, effect) {
    var list = el("perm-" + kind + "-list");
    if (!list) return;
    var row = document.createElement("div");
    row.className = "perm-rule-row";
    var input = document.createElement("input");
    input.className = "ctx-input perm-pattern";
    input.type = "text";
    input.value = pattern || "";
    input.maxLength = kind === "tools" ? 200 : 500;
    input.placeholder = kind === "tools" ? "plugin_workspace_*" :
      (kind === "paths" ? "**/.env" : "^git\\s+status$");
    input.setAttribute("aria-label", kind + " rule pattern");
    var select = effectSelect(effect);
    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "perm-remove";
    remove.textContent = "删除";
    remove.addEventListener("click", function () { row.remove(); });
    row.appendChild(input);
    row.appendChild(select);
    row.appendChild(remove);
    list.appendChild(row);
  }

  function renderRules() {
    ["tools", "paths", "commands"].forEach(function (kind) {
      var list = el("perm-" + kind + "-list");
      if (list) list.textContent = "";
    });
    Object.keys(policy.tools || {}).forEach(function (pattern) {
      addRuleRow("tools", pattern, policy.tools[pattern]);
    });
    (policy.paths || []).forEach(function (rule) {
      addRuleRow("paths", rule.pattern, rule.effect);
    });
    (policy.commands || []).forEach(function (rule) {
      addRuleRow("commands", rule.pattern, rule.effect);
    });
  }

  function collectRows(kind) {
    var rows = Array.from((el("perm-" + kind + "-list") || document).querySelectorAll(".perm-rule-row"));
    return rows.map(function (row) {
      return {
        pattern: row.querySelector(".perm-pattern").value.trim(),
        effect: row.querySelector(".perm-effect").value,
      };
    }).filter(function (rule) { return rule.pattern; });
  }

  function collectPolicy() {
    var tools = {};
    collectRows("tools").forEach(function (rule) { tools[rule.pattern] = rule.effect; });
    var pw = el("perm-write");
    var pc = el("perm-command");
    tools["plugin_workspace_*"] = pw ? pw.value : "allow";
    tools.terminal = pc ? pc.value : "ask";
    tools["plugin_terminal_*"] = pc ? pc.value : "ask";
    return {
      default: el("perm-default") ? el("perm-default").value : "ask",
      tools: tools,
      paths: collectRows("paths"),
      commands: collectRows("commands"),
    };
  }

  function setFeedback(message, isError) {
    var feedback = el("perm-feedback");
    if (!feedback) return;
    feedback.textContent = message || "";
    feedback.classList.toggle("error", !!isError);
  }

  async function parseResponse(response) {
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok || data.ok === false) throw new Error(data.detail || data.message || "请求失败");
    return data;
  }

  async function load() {
    try {
      var response = await fetch("/api/settings/permissions", { cache: "no-store" });
      var data = await parseResponse(response);
      policy = data.policy || policy;
      if (el("perm-write")) el("perm-write").value = data.write_files || "allow";
      if (el("perm-command")) el("perm-command").value = data.run_command || "ask";
      if (el("perm-default")) el("perm-default").value = policy.default || "ask";
      if (el("sandbox-mode")) el("sandbox-mode").value = data.sandbox_mode === "off" ? "off" : "required";
      var sandbox = data.sandbox || {};
      if (el("sandbox-status")) {
        el("sandbox-status").textContent = sandbox.available ?
          ("可用：" + sandbox.mode + " · " + (sandbox.detail || "")) :
          ("不可用：" + (sandbox.detail || "required 模式将拒绝命令"));
        el("sandbox-status").classList.toggle("error", !sandbox.available);
      }
      renderRules();
      setFeedback("");
      loadSandboxDiagnostics();
    } catch (error) {
      setFeedback("权限配置加载失败：" + error.message, true);
    }
  }

  async function loadSandboxDiagnostics() {
    var button = el("sandbox-setup-copy");
    try {
      var response = await fetch("/api/sandbox/diagnostics", { cache: "no-store" });
      var data = await parseResponse(response);
      sandboxSetupCommand = data.setup_command || "";
      if (button) {
        button.hidden = !sandboxSetupCommand || data.available;
        button.title = data.setup_note || "";
      }
      if (!data.available && el("sandbox-status") && data.probe_error) {
        el("sandbox-status").textContent += " · " + data.probe_error;
      }
    } catch (error) {
      if (button) button.hidden = true;
    }
  }

  async function copySandboxSetup() {
    if (!sandboxSetupCommand) return;
    try {
      await navigator.clipboard.writeText(sandboxSetupCommand);
      setFeedback("安装命令已复制；请在 PowerShell 中手动运行并确认 sudo。", false);
    } catch (error) {
      setFeedback("复制失败，请从沙箱诊断接口手动获取命令。", true);
    }
  }

  async function save() {
    var sandboxMode = el("sandbox-mode") ? el("sandbox-mode").value : "required";
    if (sandboxMode === "off" && !window.confirm("关闭 OS 沙箱后，终端命令将直接在 Windows 主机执行。确定继续吗？")) {
      el("sandbox-mode").value = "required";
      return;
    }
    setFeedback("保存中…");
    try {
      var response = await fetch("/api/settings/permissions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          write_files: el("perm-write") ? el("perm-write").value : "allow",
          run_command: el("perm-command") ? el("perm-command").value : "ask",
          policy: collectPolicy(),
          sandbox_mode: sandboxMode,
        }),
      });
      var data = await parseResponse(response);
      policy = data.policy || collectPolicy();
      renderRules();
      setFeedback("已保存并立即生效");
    } catch (error) {
      setFeedback("保存失败：" + error.message, true);
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    document.querySelectorAll("[data-perm-add]").forEach(function (button) {
      button.addEventListener("click", function () { addRuleRow(button.dataset.permAdd, "", "ask"); });
    });
    if (el("perm-save")) el("perm-save").addEventListener("click", save);
    if (el("sandbox-setup-copy")) el("sandbox-setup-copy").addEventListener("click", copySandboxSetup);
    [el("perm-write"), el("perm-command")].forEach(function (select) {
      if (select) select.addEventListener("change", save);
    });
  }

  window.WenmoPermissions = { init: function () { bind(); return load(); } };
})();
