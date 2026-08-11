(function () {
  "use strict";

  var currentUser = null;

  function requestJson(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) throw new Error(payload.detail || ("HTTP " + response.status));
        return payload;
      });
    });
  }

  function showState(user) {
    currentUser = user || null;
    var login = document.getElementById("login-btn");
    var userButton = document.getElementById("user-btn");
    var avatar = document.getElementById("user-avatar");
    var name = document.getElementById("user-name");
    if (!login || !userButton) return;
    login.hidden = !!currentUser;
    userButton.hidden = !currentUser;
    if (avatar) {
      avatar.hidden = !currentUser || !currentUser.avatar;
      avatar.src = currentUser && currentUser.avatar ? currentUser.avatar : "";
    }
    if (name) name.textContent = currentUser ? (currentUser.name || currentUser.login || "") : "";
    userButton.title = currentUser ? "当前用户：" + currentUser.login + "（点击登出）" : "";
  }

  function startLogin() {
    requestJson("/api/auth/login").then(function (payload) {
      if (payload.url) window.location.href = payload.url;
      else if (payload.configured === false && typeof window.openSettings === "function") {
        window.openSettings(true);
      }
    }).catch(function () {
      if (typeof window.openSettings === "function") window.openSettings(true);
    });
  }

  function logout() {
    requestJson("/api/auth/logout", { method: "POST" }).then(function () {
      showState(null);
      window.location.reload();
    }).catch(function () { showState(null); });
  }

  function showProfile(user) {
    var previous = document.getElementById("user-profile-modal");
    if (previous) previous.remove();
    var overlay = document.createElement("div");
    overlay.id = "user-profile-modal";
    overlay.className = "modal-overlay user-profile-overlay";
    var panel = document.createElement("div");
    panel.className = "user-profile-panel";
    var avatar = document.createElement("img");
    avatar.src = user && user.avatar ? user.avatar : "";
    avatar.alt = "头像";
    panel.appendChild(avatar);
    var name = document.createElement("strong");
    name.textContent = user ? (user.name || user.login || "未知用户") : "未知用户";
    panel.appendChild(name);
    var login = document.createElement("div");
    login.className = "minor-note";
    login.textContent = user && user.login ? "@" + user.login : "";
    panel.appendChild(login);
    if (user && user.login) {
      var link = document.createElement("a");
      link.href = "https://github.com/" + encodeURIComponent(user.login);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "查看 GitHub 主页";
      panel.appendChild(link);
    }
    var actions = document.createElement("div");
    actions.className = "user-profile-actions";
    var signOut = document.createElement("button");
    signOut.type = "button";
    signOut.textContent = "登出";
    signOut.className = "danger";
    signOut.addEventListener("click", function () { overlay.remove(); logout(); });
    var close = document.createElement("button");
    close.type = "button";
    close.textContent = "取消";
    close.addEventListener("click", function () { overlay.remove(); });
    actions.appendChild(signOut);
    actions.appendChild(close);
    panel.appendChild(actions);
    overlay.appendChild(panel);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) overlay.remove();
    });
    document.body.appendChild(overlay);
  }

  function init() {
    // Purge bearer tokens left by pre-HttpOnly-cookie releases without ever
    // reading or transmitting them again.
    try {
      localStorage.removeItem(["wenmo", "token"].join("_"));
      localStorage.removeItem(["wenmo", "user"].join("_"));
    } catch (error) { /* storage may be unavailable */ }
    var loginButton = document.getElementById("login-btn");
    var userButton = document.getElementById("user-btn");
    var settingsRow = document.getElementById("github-login-row");
    var settingsButton = document.getElementById("settings-github-login");
    if (loginButton && !loginButton.dataset.authBound) {
      loginButton.dataset.authBound = "1";
      loginButton.addEventListener("click", startLogin);
    }
    if (userButton && !userButton.dataset.authBound) {
      userButton.dataset.authBound = "1";
      userButton.addEventListener("click", function () { showProfile(currentUser); });
    }
    requestJson("/api/auth/status").then(function (payload) {
      showState(payload.logged_in ? payload.user : null);
      if (settingsRow) settingsRow.style.display = payload.oauth_configured ? "" : "none";
      if (payload.oauth_configured && settingsButton && !settingsButton.dataset.authBound) {
        settingsButton.dataset.authBound = "1";
        settingsButton.addEventListener("click", startLogin);
      }
    }).catch(function () { showState(null); });
  }

  window.WenmoAuth = { init: init, logout: logout };
})();
