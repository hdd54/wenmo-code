(function () {
  "use strict";

  function json(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok || payload.ok === false) {
          throw new Error(payload.message || payload.detail || ("HTTP " + response.status));
        }
        return payload;
      });
    });
  }

  function check(silent) {
    var status = document.getElementById("gen-update-status");
    if (!silent && status) status.textContent = "检查中…";
    return json("/api/update/check").then(function (info) {
      if (status) status.textContent = "";
      if (info && info.has_update) showPrompt(info);
      else if (!silent && status) {
        status.textContent = "已是最新版本";
        setTimeout(function () { status.textContent = ""; }, 3000);
      }
      return info;
    }).catch(function (error) {
      if (!silent && status) status.textContent = "检查失败：" + error.message;
    });
  }

  function progressText(progress, prefix) {
    var downloaded = Number(progress.downloaded || 0);
    var total = Number(progress.total || 0);
    var percent = typeof progress.percent === "number" && progress.percent >= 0
      ? progress.percent : (total ? downloaded / total * 100 : 0);
    if (total) {
      return prefix + " " + percent.toFixed(0) + "%（"
        + (downloaded / 1048576).toFixed(1) + " MB / "
        + (total / 1048576).toFixed(1) + " MB）";
    }
    return downloaded ? prefix + "（已下载 " + (downloaded / 1048576).toFixed(1) + " MB…）"
      : prefix + " 0%";
  }

  function showPrompt(info) {
    try {
      if (localStorage.getItem("wenmo_skip_version") === String(info.version)) return;
    } catch (error) { /* storage may be unavailable */ }
    var previous = document.getElementById("update-modal");
    if (previous) previous.remove();
    var overlay = document.createElement("div");
    overlay.id = "update-modal";
    overlay.className = "modal-overlay update-modal-overlay";
    var panel = document.createElement("div");
    panel.className = "update-panel";

    var title = document.createElement("h2");
    title.textContent = "发现新版本 v" + info.version;
    panel.appendChild(title);
    var current = document.createElement("div");
    current.className = "minor-note";
    current.textContent = "当前版本 v" + info.current + " · "
      + (info.update_mode === "delta" ? "增量更新" : "签名安装包全量更新");
    panel.appendChild(current);
    if (info.notes) {
      var notes = document.createElement("pre");
      notes.className = "update-notes";
      notes.textContent = info.notes;
      panel.appendChild(notes);
    }
    var progress = document.createElement("div");
    progress.className = "update-progress";
    progress.hidden = true;
    var progressLabel = document.createElement("div");
    progressLabel.textContent = "准备下载…";
    var track = document.createElement("div");
    track.className = "update-progress-track";
    var fill = document.createElement("div");
    fill.className = "update-progress-fill";
    track.appendChild(fill);
    progress.appendChild(progressLabel);
    progress.appendChild(track);
    panel.appendChild(progress);

    var actions = document.createElement("div");
    actions.className = "update-actions";
    function button(text, className) {
      var item = document.createElement("button");
      item.type = "button";
      item.textContent = text;
      item.className = className || "";
      return item;
    }
    var skipVersion = button("跳过此版本");
    skipVersion.addEventListener("click", function () {
      try { localStorage.setItem("wenmo_skip_version", String(info.version)); } catch (error) {}
      overlay.remove();
    });
    var later = button("以后");
    later.addEventListener("click", function () { overlay.remove(); });
    var install = button("立即下载", "primary");

    function fail(message) {
      install.disabled = false;
      install.textContent = "重试";
      progressLabel.textContent = "更新失败：" + message;
      progress.classList.add("error");
    }
    function poll(mode) {
      var timer = setInterval(function () {
        json("/api/update/progress").then(function (state) {
          progressLabel.textContent = progressText(state, mode === "delta" ? "增量更新中" : "下载中");
          var percent = typeof state.percent === "number" && state.percent >= 0 ? state.percent : 5;
          fill.style.width = Math.max(5, Math.min(100, percent)) + "%";
          if (state.error) {
            clearInterval(timer);
            fail(state.error);
          } else if (state.done && !state.running) {
            clearInterval(timer);
            fill.style.width = "100%";
            if (mode === "delta") {
              progressLabel.textContent = "更新完成，请重启问墨";
              install.textContent = "更新完成";
              return;
            }
            progressLabel.textContent = "下载完成，正在启动签名安装包…";
            json("/api/update/apply", { method: "POST" }).then(function () {
              install.textContent = "即将关闭并安装";
            }).catch(function (error) { fail(error.message); });
          }
        }).catch(function (error) {
          clearInterval(timer);
          fail(error.message);
        });
      }, 500);
    }
    install.addEventListener("click", function () {
      install.disabled = true;
      progress.hidden = false;
      progress.classList.remove("error");
      var delta = info.update_mode === "delta";
      install.textContent = delta ? "正在增量更新…" : "正在下载…";
      json(delta ? "/api/update/apply" : "/api/update/download", { method: "POST" })
        .then(function () { poll(delta ? "delta" : "full"); })
        .catch(function (error) { fail(error.message); });
    });
    actions.appendChild(skipVersion);
    actions.appendChild(later);
    actions.appendChild(install);
    panel.appendChild(actions);
    overlay.appendChild(panel);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) overlay.remove();
    });
    document.body.appendChild(overlay);
  }

  function init() {
    var button = document.getElementById("gen-update-btn");
    if (button && !button.dataset.updateBound) {
      button.dataset.updateBound = "1";
      button.addEventListener("click", function () { check(false); });
    }
    check(true);
  }

  window.WenmoUpdate = { init: init, check: check, showPrompt: showPrompt };
})();
