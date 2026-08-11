"""
第一课：命令行聊天机器人（多供应商版）

用法:
    python chat.py                   # 默认用 deepseek
    python chat.py --provider zen    # 用 opencode zen 的免费模型
    python chat.py --provider ollama # 用本地模型（需先启动 Ollama）

你的任务：完成 chat_once() 里的 TODO 1~3（全文件唯一的 TODO）。
"""
import argparse
import json
import os
import sys
import threading

import requests
from secret_store import SecretStoreError, is_protected, protect_secret, reveal_secret
from tenant_state import tenant_file, tenant_name

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_RESOURCE_DIR = os.environ.get("WENMO_RES_DIR") or _MODULE_DIR
PROVIDERS_FILE = os.path.join(
    os.environ.get("WENMO_DATA_DIR") or _MODULE_DIR,
    "providers.json")  # 打包版：配置读数据目录（%APPDATA%/问墨），可写可持久
DEFAULT_PROVIDERS_FILE = os.path.join(_RESOURCE_DIR, "providers.json")

_CREDENTIAL_FIELDS = ("api_key", "billing_api_key")
_BUILTIN_PROVIDERS = {
    "openai": {
        "name": "OpenAI 官方",
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "api_key_env": "OPENAI_API_KEY",
        "api_key": "",
    },
}


def _credential_scope(provider, field):
    return "%s:provider:%s:%s" % (tenant_name(), provider, field)


def _merge_builtin_providers(providers):
    providers = dict(providers or {})
    for provider, defaults in _BUILTIN_PROVIDERS.items():
        current = dict(providers.get(provider) or {})
        for key, value in defaults.items():
            current.setdefault(key, value)
        providers[provider] = current
    return providers


def _encrypt_providers(providers):
    encrypted = json.loads(json.dumps(providers))
    for provider, config in encrypted.items():
        if not isinstance(config, dict):
            continue
        for field in _CREDENTIAL_FIELDS:
            value = str(config.get(field) or "")
            if value:
                config[field] = protect_secret(value, _credential_scope(provider, field))
    return encrypted


def _decrypt_providers(providers):
    decrypted = json.loads(json.dumps(providers))
    for provider, config in decrypted.items():
        if not isinstance(config, dict):
            continue
        for field in _CREDENTIAL_FIELDS:
            value = str(config.get(field) or "")
            if not value:
                continue
            try:
                config[field] = reveal_secret(value, _credential_scope(provider, field))
            except SecretStoreError:
                # A credential copied from another Windows account/tenant is unusable.
                config[field] = ""
                config["credential_error"] = "credential cannot be decrypted for this tenant"
    return decrypted


def _write_provider_file(path, providers):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = "%s.tmp.%s.%s" % (path, os.getpid(), threading.get_ident())
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(_encrypt_providers(providers), handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def load_providers():
    """Read providers and transparently migrate plaintext keys to Windows DPAPI."""
    tenant = tenant_name()
    user_path = PROVIDERS_FILE if tenant == "local" else tenant_file("providers.json")
    path = user_path if os.path.isfile(user_path) else DEFAULT_PROVIDERS_FILE
    with open(path, encoding="utf-8") as f:
        providers = json.load(f)
    providers = _merge_builtin_providers(providers)
    # A new authenticated tenant may inherit provider names/models, never the
    # desktop owner's file-based or process-environment credentials.
    if tenant != "local" and not os.path.isfile(user_path):
        for config in providers.values():
            if isinstance(config, dict):
                for field in _CREDENTIAL_FIELDS:
                    config[field] = ""
                config["api_key_env"] = ""
                config["billing_api_key_env"] = ""
    else:
        # Rewrite only the tenant's writable config, never a packaged template.
        has_plaintext = any(
            isinstance(config, dict)
            and any(config.get(field) and not is_protected(config.get(field))
                    for field in _CREDENTIAL_FIELDS)
            for config in providers.values()
        )
        if has_plaintext:
            _write_provider_file(user_path, providers)
    return _decrypt_providers(providers)


def save_providers(providers):
    if tenant_name() == "local":
        return _write_provider_file(PROVIDERS_FILE, providers)
    return _write_provider_file(tenant_file("providers.json"), providers)


def get_api_key(cfg):
    """获取 API key：优先 providers.json 里的 api_key，其次读环境变量，本地模型返回 None"""
    key = cfg.get("api_key", "").strip()
    if key:
        return key
    env_name = cfg.get("api_key_env", "").strip()
    if not env_name:
        return None  # 本地模型，不需要 key
    # Packaged clients must never inherit developer/build-machine credentials.
    # Their keys come only from the user's DPAPI-protected configuration.
    if getattr(sys, "frozen", False):
        return None
    key = os.environ.get(env_name, "").strip()
    if not key:
        sys.exit(f"错误：请设置环境变量 {env_name}，或在 providers.json 的该供应商下直接填 api_key")
    return key


def chat_once(cfg, messages):
    """调用一次模型，返回回复文本。这是本课的核心，你的 3 个 TODO 都在这。"""
    base_url = cfg["base_url"].rstrip("/")
    model = cfg["model"]
    api_key = get_api_key(cfg)

    # ---------- TODO 1：拼出完整的请求地址 ----------
    # 聊天接口 = base_url + "/chat/completions"，结果存到 url 变量
    # 提示：这是 HTTP 请求，跟你在浏览器里访问网页没有本质区别

    # ---------- TODO 2：构造请求并发送 ----------
    # 1) headers = {"Content-Type": "application/json"}
    #    —— 如果有 api_key，再加一行：headers["Authorization"] = f"Bearer {api_key}"
    # 2) body = {"model": model, "messages": messages}
    # 3) resp = requests.post(url, headers=headers, json=body, timeout=60)
    # 4) 如果 resp.status_code != 200，打印 resp.text 并退出（这是你以后调试最重要的手段）
    # 5) 把 requests 网络错误用 try/except 包起来，报错时提示"网络错误"

    # ---------- TODO 3：从响应里取出回答 ----------
    # 响应的 JSON 长这样：
    #   {"choices": [{"message": {"content": "你好！我是AI助手"}}]}
    # 提示：data = resp.json()，然后一路取下标取键，把 content 字符串 return 出去

    raise NotImplementedError("TODO 1~3 还没填完，填完再运行")


def main():
    parser = argparse.ArgumentParser(description="多供应商聊天机器人（第一课）")
    parser.add_argument("--provider", default="deepseek", help="providers.json 里的供应商名")
    args = parser.parse_args()

    providers = load_providers()
    if args.provider not in providers:
        sys.exit(f"未知供应商 {args.provider}，可选：{list(providers)}")
    cfg = providers[args.provider]
    print(f">>> 使用模型：{cfg['model']}（{args.provider}）")

    messages = [{"role": "system", "content": "你是一个乐于助人的助手，回答尽量简洁。"}]
    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ("exit", "quit", "q"):
            break
        messages.append({"role": "user", "content": user_input})
        reply = chat_once(cfg, messages)
        print(f"AI: {reply}")
        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
