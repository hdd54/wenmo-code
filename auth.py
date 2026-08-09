"""用户登录/认证模块（GitHub OAuth 授权码模式）。
给问墨·code 加多用户能力：用户用 GitHub 账号登录，本地记录用户信息 + 签发会话 token。
数据隔离：每个用户有独立的数据目录（%APPDATA%/问墨/users/<login>/）。

配置：环境变量或 settings.json:
  GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET（GitHub OAuth App 凭据）
  WENMO_BASE_URL（本服务对外地址，用于 OAuth 回调，如 http://localhost:8000）
"""
import base64
import hashlib
import json
import os
import secrets
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
    v = os.environ.get(key)
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


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------- 用户存储 ----------------

def get_user(login):
    users = _load(USERS_FILE, {})
    return users.get(login)


def upsert_user(login, info):
    users = _load(USERS_FILE, {})
    users[login] = {"login": login, "name": info.get("name", login),
                    "avatar": info.get("avatar_url", ""),
                    "created": users.get(login, {}).get("created", time.time()),
                    "last_login": time.time()}
    _save(USERS_FILE, users)
    # 为用户创建独立数据目录
    udir = user_data_dir(login)
    os.makedirs(udir, exist_ok=True)
    return users[login]


def user_data_dir(login):
    """每个用户的独立数据目录（数据隔离）"""
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
    sessions = _load(SESSIONS_FILE, {})
    sessions[token] = {"login": login, "expires": time.time() + SESSION_TTL}
    _save(SESSIONS_FILE, sessions)
    return token


def validate_session(token):
    if not token:
        return None
    sessions = _load(SESSIONS_FILE, {})
    s = sessions.get(token)
    if not s:
        return None
    if s.get("expires", 0) < time.time():
        # 过期清理
        sessions.pop(token, None)
        _save(SESSIONS_FILE, sessions)
        return None
    return s.get("login")


def logout_session(token):
    if not token:
        return
    sessions = _load(SESSIONS_FILE, {})
    sessions.pop(token, None)
    _save(SESSIONS_FILE, sessions)


# ---------------- GitHub OAuth ----------------

def github_oauth_url():
    """生成 GitHub OAuth 授权 URL（用户点击跳转 GitHub 登录）"""
    params = urllib.parse.urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": BASE_URL + "/api/auth/github/callback",
        "scope": "read:user user:email",
        "state": secrets.token_urlsafe(16),
    })
    return "https://github.com/login/oauth/authorize?" + params


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
