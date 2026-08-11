import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

import auth
import billing_reconciliation
import billing_service
import chat
import sandbox_runner
from secret_store import SecretStoreError, is_protected, protect_secret, reveal_secret


class StructuralTargetTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_dpapi_secret_is_tenant_bound(self):
        ciphertext = protect_secret("test-admin-key", "tenant-a")
        self.assertTrue(is_protected(ciphertext))
        self.assertNotIn("test-admin-key", ciphertext)
        self.assertEqual(reveal_secret(ciphertext, "tenant-a"), "test-admin-key")
        with self.assertRaises(SecretStoreError):
            reveal_secret(ciphertext, "tenant-b")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_provider_file_persists_only_ciphertext(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "providers.json"
            providers = {
                "test": {"name": "Test", "base_url": "https://example.com/v1",
                         "model": "m", "api_key": "test-provider-secret"}}
            with mock.patch.object(chat, "PROVIDERS_FILE", str(path)):
                chat.save_providers(providers)
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(is_protected(raw["test"]["api_key"]))
                self.assertEqual(chat.load_providers()["test"]["api_key"],
                                 "test-provider-secret")

    def test_session_bearer_token_is_hashed_at_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = pathlib.Path(tmp) / "sessions.json"
            with mock.patch.object(auth, "SESSIONS_FILE", str(sessions)):
                token = auth.create_session("alice")
                raw = json.loads(sessions.read_text(encoding="utf-8"))
                self.assertNotIn(token, raw)
                self.assertTrue(all(key.startswith("sha256:") for key in raw))
                self.assertEqual(auth.validate_session(token), "alice")
                auth.logout_session(token)
                self.assertFalse(json.loads(sessions.read_text(encoding="utf-8")))

    def test_openai_cost_normalization_uses_explicit_currency(self):
        pages = [{"data": [{"start_time": 1_735_689_600, "results": [
            {"amount": {"value": 2, "currency": "usd"}, "line_item": "input"},
            {"amount": {"value": 1, "currency": "cny"}, "line_item": "storage"},
        ]}]}]
        result = billing_reconciliation.normalize_openai_costs(pages, 7.2)
        self.assertAlmostEqual(result["total_cny"], 15.4)
        self.assertEqual(result["result_count"], 2)

    def test_anthropic_usage_normalization_keeps_cache_categories(self):
        pages = [{"data": [{"results": [{
            "model": "claude-test", "uncached_input_tokens": 10,
            "output_tokens": 4, "cache_read_input_tokens": 20,
            "cache_creation": {"ephemeral_5m_input_tokens": 3,
                               "ephemeral_1h_input_tokens": 2},
        }]}]}]
        result = billing_reconciliation.normalize_anthropic_usage(pages)
        self.assertEqual(result["tokens"], {
            "input": 10, "output": 4, "cached": 20, "cache_creation": 5})

    def test_reconciliation_prefers_per_call_event_timestamps(self):
        now = time.time()
        conversations = [{
            "id": "c1", "title": "one", "provider": "openai", "model": "m",
            "updated": now,
            "usage": {
                "input": 10, "output": 2,
                "events": [{"provider": "openai", "model": "m", "ts": now - 60,
                            "input": 10, "output": 2, "cached": 0, "cost": 4.0}],
            },
        }]
        state = {"providers": {"openai": {
            "kind": "provider_costs", "start_time": now - 3600, "end_time": now + 1,
            "synced_at": "now", "invoice_reconciled": True, "total_cny": 5.0,
        }}}
        result = billing_service.build_billing_stats(
            conversations=conversations, reconciliation_state=state, now=now)
        row = result["reconciliation"][0]
        self.assertEqual(row["scope_quality"], "exact_events")
        self.assertEqual(row["local_estimate"], 4.0)
        self.assertEqual(row["variance"], -1.0)
        self.assertTrue(result["invoice_reconciled"])

    def test_sandbox_diagnostics_provides_explicit_manual_setup(self):
        unavailable = sandbox_runner.SandboxStatus(False, detail="missing")
        with mock.patch.object(sandbox_runner, "get_sandbox_status", return_value=unavailable), \
                mock.patch.object(sandbox_runner.shutil, "which", side_effect=lambda name: (
                    "C:/Windows/System32/wsl.exe" if name in ("wsl.exe", "wsl") else None)):
            result = sandbox_runner.get_sandbox_diagnostics()
        self.assertIn("bubblewrap", result["setup_command"])
        self.assertTrue(result["setup_requires_confirmation"])
        self.assertFalse(result["available"])

    def test_release_workflow_requires_explicit_manual_publish(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("publish:", workflow)
        self.assertIn("default: false", workflow)
        self.assertGreaterEqual(
            workflow.count("if: github.ref_type == 'tag' || inputs.publish == true"), 3)
        self.assertIn("scripts/check_release_secrets.py", workflow)

    def test_auth_frontend_uses_httponly_cookie_not_local_storage_token(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        auth_js = (root / "gui" / "static" / "auth_ui.js").read_text(encoding="utf-8")
        app_js = (root / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("wenmo_token", auth_js + app_js)
        self.assertNotIn("X-Wenmo-Token", auth_js)


if __name__ == "__main__":
    unittest.main()
