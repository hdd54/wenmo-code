import os
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ArchitectureHardeningTests(unittest.TestCase):
    def test_v1_update_artifacts_preserve_extension_dlc(self):
        import build_wenmo
        import updater

        self.assertEqual(updater.APP_VERSION, "1.0.0")
        for path in (
                "mcp.json", "file_mcp_server.py", "seed/plugins/tool.py",
                "seed/skills/example/SKILL.md", "_internal/seed/extensions/a/file"):
            self.assertTrue(build_wenmo._is_extension_update_payload(path), path)
        self.assertFalse(build_wenmo._is_extension_update_payload("gui/static/app.js"))
        updater_source = (ROOT / "updater.py").read_text(encoding="utf-8")
        self.assertIn('"_internal/seed/plugins/", "_internal/seed/skills/"', updater_source)

    def test_drop_in_extension_package_exposes_all_component_types(self):
        import extension_packages
        import json

        with tempfile.TemporaryDirectory() as td:
            package = pathlib.Path(td) / "content" / "extensions" / "sample-dlc"
            (package / "plugins").mkdir(parents=True)
            (package / "skills" / "sample-skill").mkdir(parents=True)
            (package / "wenmo-extension.json").write_text(json.dumps({
                "name": "sample-dlc", "version": "1.0.0", "enabled": True,
            }), encoding="utf-8")
            (package / "mcp.json").write_text(json.dumps({
                "servers": {"sample-mcp": {"enabled": True, "command": ["demo"]}},
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {"WENMO_DATA_DIR": td}), \
                    mock.patch.object(extension_packages.sys, "frozen", True, create=True):
                self.assertEqual([p["name"] for p in extension_packages.discover_packages()],
                                 ["sample-dlc"])
                self.assertEqual(extension_packages.component_dirs("plugins"),
                                 [str(package / "plugins")])
                self.assertEqual(extension_packages.component_dirs("skills"),
                                 [str(package / "skills")])
                self.assertIn("sample-mcp", extension_packages.mcp_servers())
                self.assertEqual(extension_packages.package_roots(),
                                 [str(pathlib.Path(td) / "content" / "extensions")])

    def test_runtime_hashlib_is_not_shadowed_by_function_local_imports(self):
        server = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        updater = (ROOT / "updater.py").read_text(encoding="utf-8")
        self.assertEqual(server.count("import hashlib\n"), 1)
        self.assertEqual(updater.count("import hashlib\n"), 1)
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("ruff check --select F823 gui_server.py updater.py", workflow)

    def test_packaged_client_does_not_inherit_developer_credentials(self):
        import auth
        import chat
        import gui_server

        cfg = {"api_key": "", "api_key_env": "WENMO_TEST_PROVIDER_KEY"}
        with mock.patch.dict(os.environ, {
                "WENMO_TEST_PROVIDER_KEY": "developer-provider-secret",
                "GITHUB_CLIENT_SECRET": "developer-oauth-secret",
        }), mock.patch.object(chat.sys, "frozen", True, create=True), \
                mock.patch.object(gui_server.sys, "frozen", True, create=True), \
                mock.patch.object(auth.sys, "frozen", True, create=True):
            self.assertIsNone(chat.get_api_key(cfg))
            self.assertIsNone(gui_server.resolve_key(cfg))
            self.assertEqual(auth._oauth_cred("GITHUB_CLIENT_SECRET", ""), "")

    def test_development_runtime_can_use_explicit_environment_credentials(self):
        import chat
        import gui_server

        cfg = {"api_key": "", "api_key_env": "WENMO_TEST_PROVIDER_KEY"}
        with mock.patch.dict(os.environ, {"WENMO_TEST_PROVIDER_KEY": "development-secret"}), \
                mock.patch.object(chat.sys, "frozen", False, create=True), \
                mock.patch.object(gui_server.sys, "frozen", False, create=True):
            self.assertEqual(chat.get_api_key(cfg), "development-secret")
            self.assertEqual(gui_server.resolve_key(cfg), "development-secret")

    def test_new_data_domain_can_read_bundled_provider_defaults(self):
        import chat

        with tempfile.TemporaryDirectory() as td:
            missing = pathlib.Path(td) / "providers.json"
            old_path = chat.PROVIDERS_FILE
            try:
                chat.PROVIDERS_FILE = str(missing)
                providers = chat.load_providers()
            finally:
                chat.PROVIDERS_FILE = old_path
            self.assertTrue(providers)
            self.assertFalse(missing.exists())

    def test_authenticated_history_and_projects_use_separate_tenant_roots(self):
        import history
        from execution_context import current_tenant

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with mock.patch.object(history, "BASE", str(root / "history")), \
                    mock.patch.object(history, "PROJECTS_FILE", str(root / "projects.json")):
                local_token = current_tenant.set("local")
                try:
                    history.save_conversation([{"role": "user", "content": "local"}], cid="same-id")
                finally:
                    current_tenant.reset(local_token)
                user_token = current_tenant.set("github-alice")
                try:
                    self.assertIsNone(history.get_conversation("same-id"))
                    history.save_conversation([{"role": "user", "content": "alice"}], cid="same-id")
                    self.assertEqual(history.get_conversation("same-id")["messages"][0]["content"], "alice")
                    history.add_project("Alice project")
                finally:
                    current_tenant.reset(user_token)
                local_token = current_tenant.set("local")
                try:
                    self.assertEqual(history.get_conversation("same-id")["messages"][0]["content"], "local")
                    self.assertFalse(any(p["name"] == "Alice project" for p in history.list_projects()))
                finally:
                    current_tenant.reset(local_token)

    def test_permission_matrix_is_fail_closed_and_pattern_aware(self):
        from permission_engine import evaluate_permission, normalize_policy

        policy = {
            "default": "deny",
            "tools": {"terminal": "ask", "workspace": "allow"},
            "commands": [
                {"pattern": r"^git\s+(status|diff)(?:\s|$)", "effect": "allow"},
                {"pattern": r"^git\s+push(?:\s|$)", "effect": "deny"},
            ],
            "paths": [
                {"pattern": "workspace/**", "effect": "allow"},
                {"pattern": "**/.env", "effect": "deny"},
            ],
        }
        self.assertEqual(evaluate_permission("terminal", {"command": "git status"}, policy).effect,
                         "allow")
        self.assertEqual(evaluate_permission("terminal", {"command": "git push origin main"}, policy).effect,
                         "deny")
        self.assertEqual(evaluate_permission("workspace", {"path": "workspace/a.txt"}, policy).effect,
                         "allow")
        self.assertEqual(evaluate_permission("workspace", {"path": "workspace/.env"}, policy).effect,
                         "deny")
        self.assertEqual(evaluate_permission("workspace", {"path": ".env"}, policy).effect,
                         "deny")
        self.assertEqual(evaluate_permission(
            "workspace", {"source": "workspace/.env"}, policy).effect, "deny")
        self.assertEqual(evaluate_permission("unknown", {}, policy).effect, "deny")

        deny_tool = {
            "default": "allow",
            "tools": {"plugin_terminal_*": "deny"},
            "commands": [{"pattern": r"^git status$", "effect": "allow"}],
            "paths": [],
        }
        self.assertEqual(evaluate_permission(
            "plugin_terminal_run_command", {"command": "git status"}, deny_tool).effect, "deny")

        unsafe_regex = normalize_policy({
            "commands": [{"pattern": "(a+)+$", "effect": "allow"}],
        })
        self.assertNotIn("(a+)+$", [item["pattern"] for item in unsafe_regex["commands"]])

    def test_container_sandbox_command_is_real_and_fail_closed(self):
        from sandbox_runner import SandboxUnavailable, build_container_command

        with tempfile.TemporaryDirectory() as tmp:
            cmd = build_container_command(
                "docker", pathlib.Path(tmp), ["python", "-V"], image="wenmo-agent:locked")
        rendered = " ".join(cmd)
        self.assertIn("--network none", rendered)
        self.assertIn("--read-only", rendered)
        self.assertIn("--cap-drop ALL", rendered)
        self.assertIn("no-new-privileges", rendered)
        self.assertIn("--pids-limit", rendered)
        with self.assertRaises(SandboxUnavailable):
            build_container_command(None, pathlib.Path("C:/repo"), ["python", "-V"])
        entrypoint = (ROOT / "sandbox" / "wsl_entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("--unshare-all", entrypoint)
        self.assertIn('--bind "$workspace" /workspace', entrypoint)
        self.assertIn("--cap-drop ALL", entrypoint)
        self.assertIn("--tmpfs /tmp", entrypoint)
        self.assertNotIn("--bind /mnt", entrypoint)
        self.assertIn('--ro-bind "$git_common" /repo.git', entrypoint)
        self.assertIn("GIT_OPTIONAL_LOCKS", entrypoint)
        build = (ROOT / "build_wenmo.py").read_text(encoding="utf-8")
        self.assertIn("'sandbox')};sandbox", build)

    def test_task_git_worktree_is_isolated_and_diffable(self):
        from task_workspace import TaskWorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "hello.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "hello.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True,
                           capture_output=True)

            manager = TaskWorkspaceManager(root / "worktrees")
            isolated = manager.create("task-123", repo)
            (isolated.path / "hello.txt").write_text("changed\n", encoding="utf-8")
            diff = manager.diff("task-123")

            self.assertNotEqual(isolated.path.resolve(), repo.resolve())
            self.assertIn("-base", diff)
            self.assertIn("+changed", diff)

    def test_conversations_get_tenant_scoped_git_worktrees(self):
        from conversation_workspace import resolve_conversation_workspace
        from task_workspace import TaskWorkspaceManager

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "hello.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "hello.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)

            manager = TaskWorkspaceManager(root / "worktrees")
            alice = resolve_conversation_workspace(manager, "same-chat", "alice", repo)
            bob = resolve_conversation_workspace(manager, "same-chat", "bob", repo)
            self.assertTrue(alice.isolated and bob.isolated)
            self.assertNotEqual(alice.path, bob.path)
            self.assertNotEqual(alice.branch, bob.branch)
            self.assertEqual(resolve_conversation_workspace(
                manager, "same-chat", "alice", repo).path, alice.path)

    def test_release_requires_authenticode_attestation_and_install_smoke(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("id-token: write", workflow)
        self.assertIn("actions/attest@v4", workflow)
        self.assertIn("signtool", workflow.lower())
        self.assertIn("WENMO_SIGNING_PFX", workflow)
        self.assertIn("/VERYSILENT", workflow)
        self.assertIn("install-smoke", workflow)
        self.assertIn("--refresh-update-artifacts", workflow)
        self.assertIn("update.zip 内主程序未保留有效 Authenticode 签名", workflow)
        self.assertIn("cert.Thumbprint", workflow)
        self.assertIn("打包产物的发布者证书指纹为空", workflow)
        updater_source = (ROOT / "updater.py").read_text(encoding="utf-8")
        self.assertIn("publisher certificate thumbprint mismatch", updater_source)
        self.assertIn("pinned publisher thumbprint", updater_source)
        build_source = (ROOT / "build_wenmo.py").read_text(encoding="utf-8")
        self.assertIn("signing_policy.json", build_source)

    def test_installer_does_not_close_edge_or_delete_legacy_user_data(self):
        installer = (ROOT / "wenmo_installer.iss").read_text(encoding="utf-8")
        self.assertIn("CloseApplicationsFilter=问墨.exe", installer)
        self.assertNotIn("msedge.exe", installer)
        self.assertNotIn('Name: "{app}\\history"', installer)
        self.assertNotIn('Name: "{app}\\files"', installer)

    def test_production_updater_prefers_signed_installer(self):
        import updater

        assets = [
            {"name": "update.zip", "browser_download_url": "https://example.invalid/update.zip"},
            {"name": "install.exe", "browser_download_url": "https://example.invalid/install.exe"},
        ]
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WENMO_ALLOW_UNSIGNED_ZIP_UPDATE", None)
            self.assertEqual(updater._select_release_asset(assets)["name"], "install.exe")
        source = (ROOT / "updater.py").read_text(encoding="utf-8")
        self.assertIn("Get-AuthenticodeSignature", source)
        self.assertIn("WENMO_ALLOW_UNSIGNED_DELTA_UPDATE", source)

    def test_packaged_updater_pins_the_signing_certificate(self):
        import json
        import updater

        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "signing_policy.json").write_text(json.dumps({
                "subject": "CN=Wenmo",
                "thumbprint": "AA BB CC",
            }), encoding="utf-8")
            completed = SimpleNamespace(
                returncode=0, stderr="",
                stdout=json.dumps({
                    "Status": "Valid", "Subject": "CN=Wenmo", "Thumbprint": "AABBCC"}),
            )
            environment = {
                "WENMO_RES_DIR": tmp,
                "WENMO_SIGNING_SUBJECT": "",
                "WENMO_SIGNING_THUMBPRINT": "",
            }
            with mock.patch.dict(os.environ, environment, clear=False), \
                    mock.patch.object(updater.os, "name", "nt"), \
                    mock.patch.object(updater.sys, "frozen", True, create=True), \
                    mock.patch.object(updater.subprocess, "run", return_value=completed):
                self.assertTrue(updater._verify_authenticode("install.exe")[0])
                completed.stdout = json.dumps({
                    "Status": "Valid", "Subject": "CN=Wenmo", "Thumbprint": "DDEEFF"})
                ok, detail = updater._verify_authenticode("install.exe")
                self.assertFalse(ok)
                self.assertIn("thumbprint mismatch", detail)

    def test_hidden_evolution_call_removed_and_agent_bridge_is_in_process(self):
        server = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        plugin = (ROOT / "plugins" / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn("create_task(_evolve_async", server)
        self.assertNotIn('"plugin_agent_delegate_to_agent", "plugin_vision_see_image"', server)
        self.assertNotIn('return "zen", "deepseek-v4-flash-free"', server)
        self.assertNotIn("urllib.request", plugin)
        self.assertIn("from agent_bridge import delegate", plugin)
        self.assertIn("history_store.add_conversation_usage", server)

    def test_secondary_model_tools_report_usage_without_double_counting(self):
        from usage_accounting import reported_tool_usage

        report = reported_tool_usage({
            "provider": "vendor",
            "model": "model-a",
            "usage": {"input": 10, "output": 3, "cached": 2, "reasoning": 1},
        })
        self.assertEqual(report["usage"], {
            "input": 10, "output": 3, "cached": 2, "reasoning": 1})
        committed = reported_tool_usage({
            "usage_committed": True,
            "usage": {"input": 10},
        })
        self.assertTrue(committed["usage_committed"])

        server = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        vision = (ROOT / "plugins" / "vision.py").read_text(encoding="utf-8")
        orchestrate = (ROOT / "plugins" / "orchestrate.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(server.count("reported_tool_usage(result_text)"), 2)
        self.assertNotIn("_repair_stream", server)
        self.assertNotIn("_refine_search_query", server)
        self.assertNotIn("_semantic_select_skills", server)
        self.assertFalse((ROOT / "plugins" / "evolve.py").exists())
        self.assertIn('"usage": from_openai_usage', vision)
        self.assertIn('"usage": from_openai_usage', orchestrate)
        self.assertNotIn("FREE_MODELS", vision)
        self.assertNotIn("GO_MODELS", vision)

    def test_memory_graph_is_tenant_scoped(self):
        import memory_graph
        from execution_context import current_tenant

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"WENMO_DATA_DIR": tmp}):
            token = current_tenant.set("alice")
            try:
                self.assertTrue(memory_graph.add_memory("alice-exclusive-memory-needle"))
                self.assertTrue(memory_graph.recall("alice-exclusive-memory-needle", min_score=0.01))
            finally:
                current_tenant.reset(token)
            token = current_tenant.set("bob")
            try:
                self.assertFalse(any("alice-exclusive" in hit["text"] for hit in
                                     memory_graph.recall("alice-exclusive-memory-needle", min_score=0.01)))
            finally:
                current_tenant.reset(token)

    def test_provider_credentials_are_tenant_scoped(self):
        import chat
        from execution_context import current_tenant

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"WENMO_DATA_DIR": tmp}):
            token = current_tenant.set("alice")
            try:
                providers = chat.load_providers()
                first = next(iter(providers))
                providers[first]["api_key"] = "alice-secret"
                chat.save_providers(providers)
            finally:
                current_tenant.reset(token)
            token = current_tenant.set("bob")
            try:
                bob = chat.load_providers()
                self.assertNotEqual(bob[first].get("api_key"), "alice-secret")
                self.assertEqual(bob[first].get("api_key_env"), "")
            finally:
                current_tenant.reset(token)

    def test_files_and_generated_apps_are_tenant_resolved(self):
        server = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        frontend = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('app.mount("/files"', server)
        self.assertNotIn('app.mount("/apps"', server)
        self.assertIn('@app.get("/files/{name}")', server)
        self.assertIn('@app.get("/apps/{name}/{resource:path}")', server)
        self.assertIn('sandbox="allow-scripts"', frontend)
        for filename in ("deliver.py", "draw.py", "excel.py", "pdf_gen.py", "ppt.py", "word.py"):
            plugin = (ROOT / "plugins" / filename).read_text(encoding="utf-8")
            self.assertIn("from tenant_state import", plugin)

    def test_virtual_history_keeps_a_bounded_dom_window(self):
        script = (ROOT / "gui" / "static" / "virtual_history.js").read_text(encoding="utf-8")
        self.assertIn("MAX_DOM_MESSAGES", script)
        self.assertIn("topSpacer", script)
        self.assertIn("bottomSpacer", script)
        self.assertIn("removeChild", script)
        html = (ROOT / "gui" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("virtual_history.js", html)

    def test_main_chat_exposes_worktree_review_surfaces(self):
        server = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/history/{cid}/git/status")', server)
        self.assertIn('@app.get("/api/history/{cid}/git/diff")', server)
        self.assertIn("resolve_conversation_workspace(", server)
        self.assertIn("current_workspace.set", server)

    def test_permission_matrix_has_a_real_ui_module(self):
        html = (ROOT / "gui" / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "gui" / "static" / "permissions_ui.js").read_text(encoding="utf-8")
        self.assertIn("permissions_ui.js", html)
        self.assertIn('id="perm-tools-list"', html)
        self.assertIn('id="perm-paths-list"', html)
        self.assertIn('id="perm-commands-list"', html)
        self.assertIn('id="sandbox-mode"', html)
        self.assertIn("collectPolicy", script)
        self.assertIn("window.confirm", script)

    def test_project_launch_is_explicit_policy_checked_and_shell_free(self):
        server = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        frontend = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        start = server.index('@app.post("/api/launch")')
        end = server.index('@app.post("/api/emergency-stop")', start)
        block = server[start:end]
        self.assertIn('evaluate_permission(', block)
        self.assertIn('"project_launch"', block)
        self.assertIn('shell=False', block)
        self.assertNotIn('shell=True', block)
        self.assertIn('project_id', block)
        self.assertIn('window.confirm("运行项目启动命令？', frontend)
        self.assertIn('JSON.stringify({ project_id: p.id, _confirmed: true })', frontend)

    def test_pricing_sync_keeps_source_and_timestamp(self):
        from pricing_sync import normalize_catalog

        catalog = normalize_catalog({
            "models": [{
                "id": "vendor/model-a",
                "cost": {"input": 2.5, "output": 10, "cache_read": 0.25},
            }]
        }, source="https://example.invalid/models")
        self.assertEqual(catalog["models"]["vendor/model-a"]["input"], 2.5)
        self.assertEqual(catalog["source"], "https://example.invalid/models")
        self.assertIn("synced_at", catalog)

    def test_pricing_sync_converts_declared_units_and_refuses_ambiguous_prices(self):
        from pricing_sync import normalize_catalog

        payload = {"data": [{"id": "model-a", "pricing": {
            "prompt": 0.000002, "completion": 0.000006}}]}
        converted = normalize_catalog(
            payload, "https://example.invalid/models", currency="USD", unit="per_token",
            usd_cny_rate=7.0)
        self.assertEqual(converted["models"]["model-a"]["input"], 14.0)
        self.assertEqual(converted["models"]["model-a"]["output"], 42.0)
        ambiguous = normalize_catalog(payload, "https://example.invalid/models", currency="", unit="")
        self.assertEqual(ambiguous["models"], {})
        self.assertTrue(ambiguous["price_fields_ignored"])

    def test_synced_catalog_becomes_auditable_estimate(self):
        import pricing
        from pricing_sync import normalize_catalog, save_provider_catalog

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"WENMO_DATA_DIR": tmp}):
            catalog = normalize_catalog({"models": [{"id": "model-a", "cost": {
                "input": 3.0, "output": 9.0, "cached": 0.3}}]},
                "https://example.invalid/models")
            save_provider_catalog(os.path.join(tmp, "pricing_catalogs.json"), "vendor", catalog)
            self.assertEqual(pricing.get_price("vendor", "model-a")[:3], (3.0, 9.0, 0.3))
            source = pricing.get_price_source("vendor", "model-a")
            self.assertEqual(source["kind"], "synced_catalog")
            self.assertFalse(source["invoice_reconciled"])

    def test_mixed_model_usage_is_priced_per_call_not_by_last_model(self):
        import history

        def fake_cost(provider, _model, _input, _output, _cached):
            return (1.25 if provider == "main" else 7.5), True

        def fake_source(provider, model):
            return {"kind": "test", "provider": provider, "model": model,
                    "invoice_reconciled": False}

        with mock.patch("pricing.calc_cost", side_effect=fake_cost), \
                mock.patch("pricing.get_price_source", side_effect=fake_source):
            main = history.price_usage_delta(
                {"input": 100, "output": 10}, "main", "chat")
            vision = history.price_usage_delta(
                {"input": 20, "output": 2}, "vision", "see")
            mixed = history.merge_cumulative_usage(main, vision)
            refreshed = history.refresh_usage_prices(mixed, "main", "chat")
        self.assertEqual(refreshed["cost"], 8.75)
        self.assertEqual(len(refreshed["by_model"]), 2)
        self.assertEqual(refreshed["cost_source"]["kind"], "mixed_model_ledger")

    def test_pricing_ui_is_loaded(self):
        html = (ROOT / "gui" / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "gui" / "static" / "pricing_ui.js").read_text(encoding="utf-8")
        self.assertIn("pricing_ui.js", html)
        self.assertIn("/api/pricing/sync", script)
        self.assertIn("wenmo:pricing-synced", script)

    def test_billing_ui_is_modular_and_labels_estimates(self):
        html = (ROOT / "gui" / "static" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        billing = (ROOT / "gui" / "static" / "billing_ui.js").read_text(encoding="utf-8")
        self.assertIn("billing_ui.js", html)
        self.assertIn("window.WenmoBilling", billing)
        self.assertIn("不是供应商账单", billing)
        self.assertNotIn("var billingData", app)

    def test_development_extension_surface_is_enabled_and_not_self_modifying(self):
        import json

        config = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(config["max_tools"], len(config["servers"]))
        self.assertTrue(all(entry.get("enabled") is True
                            for entry in config["servers"].values()))
        client = (ROOT / "mcp_client.py").read_text(encoding="utf-8")
        self.assertIn('_e2.get("enabled") is False', client)
        self.assertIn('if getattr(sys, "frozen", False):\n            import_path = ""', client)
        self.assertIn('_env = {} if getattr(sys, "frozen", False) else dict(os.environ)', client)
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn('check_release_secrets.py --path "dist\\问墨"', workflow)
        management = (ROOT / "plugins" / "management.py").read_text(encoding="utf-8")
        self.assertNotIn("create_plugin", management)
        self.assertNotIn("create_skill", management)
        self.assertNotIn("update_mcp_server", management)

    def test_model_controlled_network_tools_block_ssrf_and_host_execution(self):
        from network_safety import validate_public_http_url

        for url in (
                "http://127.0.0.1:8000/api/settings",
                "http://169.254.169.254/latest/meta-data",
                "http://[::1]/api/health"):
            with self.assertRaises(ValueError):
                validate_public_http_url(url)
        code_tools = (ROOT / "plugins" / "code_tools.py").read_text(encoding="utf-8")
        self.assertNotIn("benchmark_run", code_tools)
        self.assertNotIn("os.system", code_tools)
        for filename in ("misc_tools.py", "text_tools2.py", "excel.py", "ppt.py", "word.py"):
            source = (ROOT / "plugins" / filename).read_text(encoding="utf-8")
            self.assertIn("safe_urlopen", source)

    def test_remote_worker_api_has_no_public_default_token(self):
        server = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        worker = (ROOT / "worker.py").read_text(encoding="utf-8")
        self.assertNotIn("wenmo-cluster-2026", server)
        self.assertNotIn("wenmo-cluster-2026", worker)
        self.assertIn("远程 worker API 已关闭", server)
        self.assertIn("必须设置 CLUSTER_TOKEN", worker)


if __name__ == "__main__":
    unittest.main()
