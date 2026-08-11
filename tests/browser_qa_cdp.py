"""Headless Edge smoke test without third-party browser automation packages."""

import base64
import json
import os
import pathlib
import time
import urllib.request

import websocket


BASE_URL = os.environ.get("WENMO_QA_URL", "http://127.0.0.1:8765")
DEBUG_URL = os.environ.get("WENMO_CDP_URL", "http://127.0.0.1:9223")
ARTIFACT_DIR = pathlib.Path(os.environ.get("WENMO_QA_ARTIFACT_DIR", ".qa-artifacts"))


def http_json(path, method="GET", body=None):
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        BASE_URL + path,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


class CDP:
    def __init__(self, websocket_url):
        self.ws = websocket.create_connection(websocket_url, timeout=10, origin=DEBUG_URL)
        self.next_id = 0
        self.events = []

    def call(self, method, params=None):
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result") or {}
            self.events.append(message)

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return (result.get("result") or {}).get("value")

    def close(self):
        self.ws.close()


def wait_for(fn, timeout=15):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception:
            pass
        time.sleep(0.15)
    raise AssertionError("browser condition timed out; last=%r" % (last,))


def main():
    assert http_json("/api/health").get("status") == "ok"
    messages = []
    for index in range(1200):
        messages.append({
            "id": "qa-%04d" % index,
            "role": "user" if index % 2 == 0 else "assistant",
            "content": "QA message %04d " % index + ("x" * 20),
            "ts": 1_700_000_000_000 + index,
        })
    saved = http_json("/api/history", "POST", {
        "id": "qa-long-conversation", "title": "QA long conversation",
        "messages": messages, "project": "default", "provider": "local", "model": "qa",
    })
    assert saved["ok"] is True

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(DEBUG_URL + "/json", timeout=10) as response:
        pages = json.loads(response.read().decode("utf-8"))
    page = next(item for item in pages if item.get("type") == "page")
    cdp = CDP(page["webSocketDebuggerUrl"])
    try:
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {
            "source": "localStorage.setItem('wenmo_onboarded', '1');"
        })
        cdp.call("Page.navigate", {"url": BASE_URL + "/"})
        wait_for(lambda: cdp.evaluate("document.readyState === 'complete'"))
        assert cdp.evaluate("typeof window.WenmoAuth === 'object'")
        assert cdp.evaluate("typeof window.WenmoUpdate === 'object'")
        # The visual assertion must inspect the conversation itself, not the
        # first-run onboarding overlay from the isolated QA profile.
        cdp.evaluate("document.getElementById('onboard-skip')?.click()")
        wait_for(lambda: cdp.evaluate(
            "!document.getElementById('onboard-modal') || document.getElementById('onboard-modal').hidden"))
        wait_for(lambda: cdp.evaluate("document.querySelectorAll('.history-item').length > 0"))
        clicked = cdp.evaluate("""
          (() => {
            const item = Array.from(document.querySelectorAll('.history-item'))
              .find(x => x.textContent.includes('QA long conversation'));
            if (!item) return false;
            item.click(); return true;
          })()
        """)
        assert clicked
        wait_for(lambda: cdp.evaluate("window.state && state.messages.length === 1200"))
        wait_for(lambda: cdp.evaluate("document.querySelectorAll('#messages .msg[data-idx]').length > 0"))
        snapshot = cdp.evaluate("""
          (() => {
            const rows = Array.from(document.querySelectorAll('#messages .msg[data-idx]'));
            return {
              domCount: rows.length,
              first: Number(rows[0].dataset.idx),
              last: Number(rows[rows.length - 1].dataset.idx),
              topSpacers: document.querySelectorAll('.virtual-spacer-top').length,
              bottomSpacers: document.querySelectorAll('.virtual-spacer-bottom').length,
              horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
              viewportWidth: document.documentElement.clientWidth,
              scrollWidth: document.documentElement.scrollWidth,
              offenders: Array.from(document.querySelectorAll('body *')).map(el => {
                const r = el.getBoundingClientRect();
                return {tag: el.tagName, id: el.id, cls: String(el.className || '').slice(0, 100),
                        left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width)};
              }).filter(x => x.right > document.documentElement.clientWidth + 1 || x.left < -1)
                .sort((a, b) => b.right - a.right).slice(0, 8)
            };
          })()
        """)
        assert snapshot["domCount"] <= 180, snapshot
        assert snapshot["last"] == 1199, snapshot
        assert snapshot["topSpacers"] == 1 and snapshot["bottomSpacers"] == 1, snapshot
        assert not snapshot["horizontalOverflow"], snapshot

        cdp.evaluate("document.getElementById('settings-btn').click()")
        wait_for(lambda: cdp.evaluate("!document.getElementById('settings-modal').hidden"))
        wait_for(lambda: cdp.evaluate("document.querySelectorAll('.settings-row').length > 0"))
        assert cdp.evaluate("document.querySelectorAll('.settings-pricing-sync').length > 0")
        cdp.evaluate("document.querySelector('.modal-tab[data-tab=\"general\"]').click(); "
                     "document.getElementById('perm-advanced').open = true")
        wait_for(lambda: cdp.evaluate("document.querySelectorAll('#perm-tools-list .perm-rule-row').length > 0"))
        permission_snapshot = cdp.evaluate("""
          (() => {
            const panel = document.querySelector('#settings-modal .modal-panel');
            return {
              tools: document.querySelectorAll('#perm-tools-list .perm-rule-row').length,
              paths: document.querySelectorAll('#perm-paths-list .perm-rule-row').length,
              commands: document.querySelectorAll('#perm-commands-list .perm-rule-row').length,
              sandbox: document.getElementById('sandbox-status').textContent.trim(),
              panelOverflow: panel.scrollWidth > panel.clientWidth
            };
          })()
        """)
        assert permission_snapshot["tools"] > 0, permission_snapshot
        assert permission_snapshot["paths"] > 0, permission_snapshot
        assert permission_snapshot["commands"] > 0, permission_snapshot
        assert permission_snapshot["sandbox"], permission_snapshot
        assert not permission_snapshot["panelOverflow"], permission_snapshot

        assert cdp.evaluate("typeof window.WenmoBilling === 'object'")
        cdp.evaluate("document.getElementById('gen-billing-btn').click()")
        wait_for(lambda: cdp.evaluate("!document.getElementById('billing-modal').hidden"))
        wait_for(lambda: cdp.evaluate(
            "document.querySelectorAll('#billing-conv-list > *').length > 0"))
        billing_snapshot = cdp.evaluate("""
          (() => ({
            total: document.getElementById('billing-total').textContent.trim(),
            basis: document.getElementById('billing-basis-note').textContent.trim(),
            rows: document.querySelectorAll('#billing-conv-list > *').length,
            connectors: document.querySelectorAll('#billing-connector option').length,
            adminKeyType: document.getElementById('billing-admin-key').type,
            panelOverflow: document.querySelector('#billing-modal .modal-panel').scrollWidth >
                           document.querySelector('#billing-modal .modal-panel').clientWidth
          }))()
        """)
        assert billing_snapshot["total"] not in ("", "加载中…", "加载失败"), billing_snapshot
        assert "不是供应商账单" in billing_snapshot["basis"], billing_snapshot
        assert billing_snapshot["rows"] > 0, billing_snapshot
        assert billing_snapshot["connectors"] >= 2, billing_snapshot
        assert billing_snapshot["adminKeyType"] == "password", billing_snapshot
        assert not billing_snapshot["panelOverflow"], billing_snapshot
        cdp.evaluate("document.querySelector('#billing-modal [data-billing-close]').click()")
        wait_for(lambda: cdp.evaluate("document.getElementById('billing-modal').hidden"))
        cdp.evaluate("document.getElementById('settings-close').click()")
        wait_for(lambda: cdp.evaluate("document.getElementById('settings-modal').hidden"))

        cdp.evaluate("WenmoVirtualHistory.ensureIndex(0)")
        wait_for(lambda: cdp.evaluate("!!document.querySelector('#messages .msg[data-idx=\"0\"]')"))
        old_window_count = cdp.evaluate("document.querySelectorAll('#messages .msg[data-idx]').length")
        assert old_window_count <= 180

        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        time.sleep(0.5)
        mobile_overflow = cdp.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth")
        assert not mobile_overflow

        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        shot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (ARTIFACT_DIR / "long-history-mobile.png").write_bytes(base64.b64decode(shot["data"]))

        exceptions = [event for event in cdp.events
                      if event.get("method") in ("Runtime.exceptionThrown", "Log.entryAdded")]
        serious = [event for event in exceptions
                   if "error" in json.dumps(event, ensure_ascii=False).lower()]
        assert not serious, serious
        # Keep CI output ASCII-safe on Windows hosts whose active code page is GBK.
        print(json.dumps({"ok": True, "virtual": snapshot, "permissions": permission_snapshot,
                          "billing": billing_snapshot,
                          "mobile_overflow": mobile_overflow},
                         ensure_ascii=True))
    finally:
        cdp.close()
        try:
            http_json("/api/history/qa-long-conversation", "DELETE")
        except Exception:
            pass


if __name__ == "__main__":
    main()
