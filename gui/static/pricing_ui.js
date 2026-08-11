(function () {
  "use strict";

  function messageFromError(error) {
    if (!error) return "同步失败";
    return String(error.message || error);
  }

  function attach(actions, provider, feedback) {
    if (!actions || !provider || provider.key === "local" || provider.key === "ollama" ||
        provider.billing_mode === "subscription") return;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "settings-pricing-sync";
    button.textContent = "同步官方目录";
    button.title = "同步模型目录；只有币种和计价单位明确的价格才会用于本地估算，不等于账单";
    button.addEventListener("click", function () {
      button.disabled = true;
      button.textContent = "同步中…";
      feedback.classList.remove("error");
      feedback.textContent = "正在读取供应商目录…";
      fetch("/api/pricing/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: provider.key })
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          if (!response.ok) throw new Error(body.detail || ("HTTP " + response.status));
          return body;
        });
      }).then(function (data) {
        feedback.textContent = data.billing_note || "目录已同步";
        window.dispatchEvent(new CustomEvent("wenmo:pricing-synced", {
          detail: { provider: provider.key, result: data }
        }));
      }).catch(function (error) {
        feedback.classList.add("error");
        feedback.textContent = messageFromError(error);
      }).finally(function () {
        button.disabled = false;
        button.textContent = "同步官方目录";
      });
    });
    actions.appendChild(button);
  }

  window.WenmoPricing = { attach: attach };
})();
