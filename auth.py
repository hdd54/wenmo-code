"""用户登录/认证模块（GitHub OAuth 授权码模式）。

认证请求的历史、项目、设置、供应商、上传文件和生成应用按租户隔离。
进程生命周期、本地模型运行时、认证注册表与更新器仍是机器级共享状态，
因此桌面服务不宣称为已加固的公网多用户服务。

配置：环境变量或 settings.json:
  GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET（GitHub OAuth App 凭据）
  WENMO_BASE_URL（本服务对外地址，用于 OAuth 回调，如 http://localhost:8000）
"""
import base64
import hashlib
import json
import os
import sys
import secrets
import threading
import time
import urllib.parse
import urllib.request

# 代理设置：访问 GitHub 需要代理时配置（如 7897）
# 优先级：环境变量 WENMO_PROXY > 自动探测常见本地代理端口
def _get_proxy():
    p = os.environ.get("WENMO_PROXY", "").strip()
    if p:
        return p
    # 自动探测常见科学上网代理端口
    import socket
    for port in (7897, 7890, 10809, 1080, 8888):
        s = socket.socket()
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            return f"http://127.0.0.1:{port}"
        except Exception:
            pass
        finally:
            s.close()
    return ""


PROXY = _get_proxy()


def _opener():
    """带代理的 urllib opener（GitHub 直连超时时走代理）"""
    if PROXY:
        proxy = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        return urllib.request.build_opener(proxy)
    return urllib.request.build_opener()

DATA_DIR = os.environ.get("WENMO_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(DATA_DIR, "users.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")

# GitHub OAuth App 凭据（优先级：环境变量 > settings.json）
def _load_settings():
    try:
        sp = os.path.join(os.environ.get("WENMO_RES_DIR") or os.path.dirname(os.path.abspath(__file__)),
                          "settings.json")
        with open(sp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _oauth_cred(key, default=""):
    """取 OAuth 凭据：先环境变量，再 settings.json"""
    # Packaged clients must not inherit developer/build-machine credentials.
    # WENMO_BASE_URL is routing configuration and is intentionally still allowed.
    v = os.environ.get(key) if (key == "WENMO_BASE_URL" or not getattr(sys, "frozen", False)) else None
    if v:
        return v
    return _load_settings().get({
        "GITHUB_CLIENT_ID": "github_client_id",
        "GITHUB_CLIENT_SECRET": "github_client_secret",
        "WENMO_BASE_URL": "base_url",
    }.get(key, ""), default)


GITHUB_CLIENT_ID = _oauth_cred("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = _oauth_cred("GITHUB_CLIENT_SECRET", "")
# 对外访问地址（OAuth 回调用）
BASE_URL = _oauth_cred("WENMO_BASE_URL", "http://localhost:8000")

SESSION_TTL = 7 * 24 * 3600   # 会话有效期 7 天
_SAVE_LOCK = threading.RLock()
_STATE_LOCK = threading.Lock()
_OAUTH_STATES = {}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    tmp = path + ".tmp." + secrets.token_hex(4)
    try:
        with _SAVE_LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        return True
    except Exception:
        return False
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


# ---------------- 用户存储 ----------------

def get_user(login):
    users = _load(USERS_FILE, {})
    return users.get(login)


def upsert_user(login, info):
    with _SAVE_LOCK:
        users = _load(USERS_FILE, {})
        users[login] = {"login": login, "name": info.get("name", login),
                        "avatar": info.get("avatar_url", ""),
                        "created": users.get(login, {}).get("created", time.time()),
                        "last_login": time.time()}
        _save(USERS_FILE, users)
    # 保留用户专属目录，供未来迁移；当前请求处理不动态切换全局存储目录。
    udir = user_data_dir(login)
    os.makedirs(udir, exist_ok=True)
    return users[login]


def user_data_dir(login):
    """Return the reserved per-user directory (not request-scoped in the desktop server)."""
    safe = "".join(c if c.isalnum() else "_" for c in login)
    return os.path.join(DATA_DIR, "users", safe)


def list_users():
    users = _load(USERS_FILE, {})
    return [{"login": u.get("login"), "name": u.get("name"),
             "avatar": u.get("avatar"), "last_login": u.get("last_login")}
            for u in users.values()]


# ---------------- 会话管理 ----------------

def create_session(login):
    token = secrets.token_urlsafe(32)
    with _SAVE_LOCK:
        sessions = _load(SESSIONS_FILE, {})
        sessions[_session_digest(token)] = {
            "login": login, "expires": time.time() + SESSION_TTL, "version": 2}
        _save(SESSIONS_FILE, sessions)
    return token


def _session_digest(token):
    return "sha256:" + hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def validate_session(token):
    if not token:
        return None
    sessions = _load(SESSIONS_FILE, {})
    digest = _session_digest(token)
    s = sessions.get(digest)
    legacy = False
    if not s:
        # One-time migration for pre-v2 files that stored bearer tokens verbatim.
        s = sessions.get(token)
        legacy = bool(s)
    if not s:
        return None
    if s.get("expires", 0) < time.time():
        # 过期清理
        with _SAVE_LOCK:
            sessions = _load(SESSIONS_FILE, {})
            sessions.pop(digest, None)
            sessions.pop(token, None)
            _save(SESSIONS_FILE, sessions)
        return None
    if legacy:
        with _SAVE_LOCK:
            sessions = _load(SESSIONS_FILE, {})
            migrated = sessions.pop(token, None)
            if migrated:
                migrated["version"] = 2
                sessions[digest] = migrated
                _save(SESSIONS_FILE, sessions)
    return s.get("login")


def logout_session(token):
    if not token:
        return
    with _SAVE_LOCK:
        sessions = _load(SESSIONS_FILE, {})
        sessions.pop(_session_digest(token), None)
        sessions.pop(token, None)
        _save(SESSIONS_FILE, sessions)


# ---------------- GitHub OAuth ----------------

def github_oauth_url():
    """生成 GitHub OAuth 授权 URL（用户点击跳转 GitHub 登录）"""
    now = time.time()
    with _STATE_LOCK:
        for old, expires in list(_OAUTH_STATES.items()):
            if expires < now:
                _OAUTH_STATES.pop(old, None)
        state = secrets.token_urlsafe(24)
        _OAUTH_STATES[state] = now + 600
    params = urllib.parse.urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": BASE_URL + "/api/auth/github/callback",
        "scope": "read:user user:email",
        "state": state,
    })
    return "https://github.com/login/oauth/authorize?" + params


def consume_oauth_state(state):
    """Validate a one-time OAuth state token and consume it."""
    with _STATE_LOCK:
        expires = _OAUTH_STATES.pop(state or "", 0)
    return bool(expires and expires >= time.time())


def github_exchange_code(code):
    """用授权码换 access_token（GitHub 服务端换取，走代理）"""
    data = urllib.parse.urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code,
    }).encode()
    req = urllib.request.Request(
        "https://github.com/login/oauth/access_token",
        data=data,
        headers={"Accept": "application/json", "User-Agent": "wenmo-auth"},
    )
    try:
        with _opener().open(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def github_get_user(access_token):
    """用 access_token 获取 GitHub 用户信息（走代理）"""
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"token {access_token}", "User-Agent": "wenmo-auth"},
    )
    try:
        with _opener().open(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def is_configured():
    """是否配置了 OAuth（否则登录功能不可用）"""
    return bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)
