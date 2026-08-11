import ast
import pathlib
import re
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


class WenmoRegressionTests(unittest.TestCase):
    def test_new_conversation_id_is_guarded_against_navigation_race(self):
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("var seedNavigation = state.loadSeq", js)
        self.assertIn("state.loadSeq === seedNavigation", js)
        start = js.index("function newChat()")
        end = js.index("\n}", start)
        self.assertIn("state.loadSeq += 1", js[start:end])

    def test_delta_update_branch_does_not_fall_through_to_full_download(self):
        js = (ROOT / "gui" / "static" / "update_ui.js").read_text(encoding="utf-8")
        self.assertIn('var delta = info.update_mode === "delta"', js)
        self.assertIn('json(delta ? "/api/update/apply" : "/api/update/download"', js)
        self.assertIn('poll(delta ? "delta" : "full")', js)

    def test_full_update_click_starts_download_before_polling(self):
        js = (ROOT / "gui" / "static" / "update_ui.js").read_text(encoding="utf-8")
        start = js.index('json(delta ? "/api/update/apply" : "/api/update/download"')
        end = js.index(".catch(function (error)", start)
        self.assertLess(start, js.index('poll(delta ? "delta" : "full")', start, end))

    def test_server_builds_authoritative_cumulative_usage(self):
        src = (ROOT / "history.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "merge_cumulative_usage"), None)
        self.assertIsNotNone(fn)
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "history.py", "exec"), ns)
        merged = ns["merge_cumulative_usage"](
            {"input": 100, "output": 20, "cached": 40, "cost": 0.5},
            {"input": 10, "output": 3, "cached": 4, "cost": 0.1},
        )
        self.assertEqual(merged["input"], 110)
        self.assertEqual(merged["output"], 23)
        self.assertEqual(merged["cached"], 44)
        self.assertAlmostEqual(merged["cost"], 0.6)

    def test_subscription_provider_does_not_invent_token_cost(self):
        import pricing
        quote = pricing.get_price("opencode_go", "deepseek-v4-flash")
        self.assertEqual(quote[:3], (None, None, None))
        self.assertEqual(pricing.get_billing_mode("opencode_go"), "subscription")
        cost, estimated = pricing.calc_cost("opencode_go", "deepseek-v4-flash", 1000, 100, 0)
        self.assertIsNone(cost)
        self.assertTrue(estimated)

    def test_remote_token_price_is_explicitly_an_estimate(self):
        import pricing
        quote = pricing.get_price("deepseek", "deepseek-v4-flash")
        self.assertTrue(all(isinstance(value, float) for value in quote[:3]))
        self.assertTrue(quote[3])

    def test_usage_only_merge_does_not_replace_messages(self):
        import history
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(history, "BASE", tmp), \
                 mock.patch.object(history, "PROJECTS_FILE", str(pathlib.Path(tmp) / "projects.json")):
                cid = history.save_conversation(
                    [{"role": "user", "content": "keep me"}],
                    usage={"input": 10, "output": 2, "cost": 0.1})
                self.assertTrue(history.add_conversation_usage(
                    cid, {"input": 5, "output": 1, "cost": 0.05}))
                conv = history.get_conversation(cid)
                self.assertEqual(conv["messages"][0]["content"], "keep me")
                self.assertEqual(conv["usage"]["input"], 15)
                self.assertAlmostEqual(conv["usage"]["cost"], 0.15)

    def test_usage_pending_before_first_save_is_merged(self):
        import history
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(history, "BASE", tmp), \
                 mock.patch.object(history, "PROJECTS_FILE", str(pathlib.Path(tmp) / "projects.json")):
                cid = "new-comparison"
                self.assertTrue(history.add_conversation_usage(cid, {"input": 7, "cost": 0.07}))
                history.save_conversation([{"role": "user", "content": "main"}], cid=cid,
                                          usage_delta={"input": 11, "cost": 0.11})
                usage = history.get_conversation(cid)["usage"]
                self.assertEqual(usage["input"], 18)
                self.assertAlmostEqual(usage["cost"], 0.18)

    def test_save_reprices_legacy_cost_from_authoritative_tokens(self):
        import history
        import pricing
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(history, "BASE", tmp), \
                 mock.patch.object(history, "PROJECTS_FILE", str(pathlib.Path(tmp) / "projects.json")):
                cid = history.save_conversation(
                    [{"role": "user", "content": "billing"}], provider="deepseek",
                    model="deepseek-v4-flash",
                    usage={"input": 1000, "output": 100, "cached": 100, "cost": 999.0})
                usage = history.get_conversation(cid)["usage"]
                expected, _ = pricing.calc_cost("deepseek", "deepseek-v4-flash", 1000, 100, 100)
                self.assertAlmostEqual(usage["cost"], expected)
                self.assertTrue(usage["cost_est"])

    def test_browser_does_not_overwrite_authoritative_usage(self):
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        start = js.index("function saveHistory()")
        end = js.index("\n}", start)
        self.assertNotIn("usage:", js[start:end])

    def test_tool_duplicate_key_is_initialized_before_execution(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        start = src.index("async def _exec_tool(s):")
        try_pos = src.index("try:", start)
        self.assertIn('dup_key = s["name"]', src[start:try_pos])

    def test_context_compaction_is_behavioral_and_uses_resolved_tools(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "compact_messages_if_needed"), None)
        self.assertIsNotNone(fn)
        ns = {"estimate_tokens": lambda text: len(text), "json": __import__("json"),
              "_fold_summary": lambda folded: "summary"}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "gui_server.py", "exec"), ns)
        messages = [{"role": "system", "content": "rules"}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 80}
            for i in range(20)
        ]
        compacted, did_compact, before, after = ns["compact_messages_if_needed"](
            messages, [{"description": "tool" * 20}], 1000)
        self.assertTrue(did_compact)
        self.assertLess(after, before)
        self.assertEqual(compacted[0]["role"], "system")
        self.assertIn("summary", compacted[1]["content"])

        # Provider-observed context is the lower bound for the next trigger; local
        # tokenizers can otherwise undercount and miss the 85% threshold shown in UI.
        short_messages = [{"role": "system", "content": "rules"}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x"}
            for i in range(14)
        ]
        compacted2, did_compact2, before2, _ = ns["compact_messages_if_needed"](
            short_messages, [], 1000, observed_context=930)
        self.assertTrue(did_compact2)
        self.assertEqual(before2, 930)

        tools_assignment = src.index("tools = await mcp_openai_tools()")
        compact_call = src.index("compact_messages_if_needed(\n                msgs, tools, ctx_limit, _observed_context)", tools_assignment)
        self.assertLess(tools_assignment, compact_call)

    def test_token_calibration_uses_bounded_ema_without_double_scaling(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "_calibrate_token_estimate"), None)
        self.assertIsNotNone(fn)
        ns = {"_TOKEN_CAL": [1.0, 1.0, 1.0], "_TOKEN_SAMPLES": 0, "_MAX_SAMPLES": 30}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "gui_server.py", "exec"), ns)
        ns["_calibrate_token_estimate"](200, 100, 100, 0, 0)
        self.assertAlmostEqual(ns["_TOKEN_CAL"][0], 1.3)
        self.assertEqual(ns["_TOKEN_CAL"][1:], [1.0, 1.0])

    def test_long_history_renders_latest_virtual_window_without_cache_race(self):
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        render_start = js.index("function renderAllMessages(")
        render_end = js.index("\n}\n", render_start) + 3
        render = js[render_start:render_end]
        self.assertIn("var renderSeq = ++_messageRenderSeq", render)
        self.assertIn("window.WenmoVirtualHistory.render", render)
        self.assertIn("renderInto(fragment, start, end)", render)

        virtual = (ROOT / "gui" / "static" / "virtual_history.js").read_text(encoding="utf-8")
        self.assertIn("var MAX_DOM_MESSAGES = 180", virtual)
        self.assertIn("draw(Math.max(0, latestEnd - MAX_DOM_MESSAGES), latestEnd)", virtual)

        cache_start = js.index("if (cached) {")
        cache_end = js.index("  } else {", cache_start)
        cache_branch = js[cache_start:cache_end]
        self.assertEqual(cache_branch.count("renderAllMessages("), 1)

    def test_mobile_layout_uses_drawers_instead_of_squeezing_chat(self):
        css = (ROOT / "gui" / "static" / "style.css").read_text(encoding="utf-8")
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 900px)", css)
        self.assertIn("#sidebar:not(.mobile-open) .sidebar-main", css)
        self.assertIn("width: calc(100vw - 44px)", css)
        self.assertIn(".compare-panel { flex-direction: column; }", css)
        self.assertIn('window.matchMedia("(max-width: 700px)")', js)
        self.assertIn('sidebar.classList.toggle("mobile-open", !collapsed)', js)

    def test_inline_math_detection_rejects_currency_and_shell_variables(self):
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function isLikelyInlineMath(value)", js)
        self.assertIn('if (!isLikelyInlineMath(m)) return whole;', js)
        self.assertIn("$19.99 is currency", js)
        self.assertIn("$HOME / $variable", js)

    def test_history_mutations_report_real_failures(self):
        import history
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(history, "BASE", tmp), \
                 mock.patch.object(history, "PROJECTS_FILE", str(pathlib.Path(tmp) / "projects.json")):
                self.assertFalse(history.delete_conversation("missing"))
                cid = history.save_conversation([{"role": "user", "content": "hello"}])
                self.assertTrue(history.rename_conversation(cid, "renamed"))
                self.assertEqual(history.get_conversation(cid)["title"], "renamed")
                self.assertTrue(history.delete_conversation(cid))
                self.assertIsNone(history.get_conversation(cid))
                pid = history.add_project("old name")
                self.assertTrue(history.rename_project(pid, "new name"))
                renamed = next(p for p in history.list_projects() if p["id"] == pid)
                self.assertEqual(renamed["name"], "new name")

    def test_project_delete_removes_history_before_project_registry(self):
        import history
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            with mock.patch.object(history, "BASE", str(root / "history")), \
                 mock.patch.object(history, "PROJECTS_FILE", str(root / "projects.json")):
                pathlib.Path(history.BASE).mkdir()
                pid = history.add_project("temporary", str(workspace))
                cid = history.save_conversation(
                    [{"role": "user", "content": "delete me"}], project=pid)
                self.assertIsNotNone(history.get_conversation(cid, pid))
                self.assertTrue(history.delete_project(pid))
                self.assertIsNone(history.get_conversation(cid, pid))
                self.assertFalse(any(p["id"] == pid for p in history.list_projects()))

    def test_frontend_mutations_reject_http_and_application_errors(self):
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function expectOkResponse(res)", js)
        for marker in (
            'fetch("/api/projects/" + cur.id + "/rename"',
            'fetch("/api/history/" + cid, { method: "DELETE" })',
            'fetch("/api/history/" + c.id + "/rename"',
        ):
            start = js.index(marker)
            self.assertIn("expectOkResponse", js[start:start + 500])

    def test_tool_selection_is_bounded_and_not_locked_to_first_turn(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertIn("def _match_tools_by_message(tools, message, core=CORE_TOOL_NAMES, max_tools=32):", src)
        self.assertIn("return out[:max_tools]", src)
        self.assertNotIn('(req.conversation_id or "new") + "|" + req.provider', src)
        self.assertIn("match_skills(last_user, max_n=3)", src)
        request_flow = src[src.index("candidate_skills ="):src.index("if matched_skills:", src.index("candidate_skills ="))]
        self.assertNotIn("_semantic_select_skills", request_flow)

    def test_release_workflow_has_version_and_asset_gates(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("id: version", workflow)
        self.assertIn("python -m unittest discover", workflow)
        self.assertIn("APP_VERSION", workflow)
        self.assertIn("MyAppVersion", workflow)
        self.assertIn("fail_on_unmatched_files: true", workflow)
        self.assertIn("checksums.sha256", workflow)
        self.assertIn("Get-FileHash -Algorithm SHA256", workflow)
        self.assertIn("pip install -r requirements.txt", workflow)
        self.assertIn("node --check gui/static/app.js", workflow)
        self.assertIn("python -m py_compile", workflow)
        self.assertNotIn("format('v{0}', '1.0.6')", workflow)

        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for dependency in ("openai", "python-multipart", "pywebview", "scikit-learn"):
            self.assertIn(dependency, requirements)

    def test_static_mount_does_not_shadow_task_and_worker_apis(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        mount = src.rindex('app.mount("/", StaticFiles')
        self.assertGreater(mount, src.index('@app.get("/api/tasks")'))
        self.assertGreater(mount, src.index('@app.get("/api/workers/nodes")'))
        self.assertEqual(src.count('app.mount("/", StaticFiles'), 1)
        self.assertIn('@app.get("/api/health")', src)

    def test_loopback_host_parser_handles_ipv6(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertIn('urllib.parse.urlparse("//" + (request.headers.get("host") or "")).hostname', src)
        self.assertIn("origin_port != request_port", src)
        self.assertIn("origin_scheme != request.url.scheme", src)

    def test_upload_uses_resolved_extension_and_hardens_file_serving(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertIn('name = f"att_{int(time.time())}_{os.urandom(3).hex()}.{_ext}"', src)
        self.assertIn('response.headers["Content-Security-Policy"] = "sandbox; default-src \'none\'"', src)
        self.assertIn('response.headers["X-Content-Type-Options"] = "nosniff"', src)

    def test_compare_stream_is_non_persistent_and_uses_sse_buffer(self):
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        start = js.index("function startCompareStream(")
        end = js.index("/* ---------- 发送队列", start)
        block = js[start:end]
        self.assertIn("persist: false", block)
        self.assertIn("var sseBuffer =", block)
        self.assertIn("var outputText =", block)
        self.assertIn("applyCompareUsage(evt.usage)", block)
        self.assertNotIn("renderMarkdown(acc)", block)

    def test_agent_loop_and_continuation_are_bounded(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertIn("MAX_LOOPS = 50", src)
        self.assertIn("MAX_CONTINUE = 8", src)
        self.assertIn('truncated = finish_reason == "length"', src)
        self.assertNotIn("MAX_LOOPS = 10**9", src)
        self.assertNotIn("MAX_CONTINUE = 10**9", src)

        cluster = (ROOT / "cluster.py").read_text(encoding="utf-8")
        self.assertIn("MAX_LOOPS = 40", cluster)
        self.assertIn("MAX_TOOL_RESULT = 100_000", cluster)
        self.assertNotIn("10**9", cluster)

    def test_plugin_dict_results_remain_structured_for_permission_gate(self):
        src = (ROOT / "plugins_loader.py").read_text(encoding="utf-8")
        self.assertIn("json.dumps(result, ensure_ascii=False)", src)
        terminal = (ROOT / "plugins" / "terminal.py").read_text(encoding="utf-8")
        self.assertIn('if decision.effect == "ask" and not arguments.get("_confirmed"):', terminal)

    def test_update_mutations_use_post_and_debug_mcp_is_disabled_by_default(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/update/download")', src)
        self.assertIn('@app.post("/api/update/apply")', src)
        self.assertIn('os.environ.get("WENMO_ENABLE_DEBUG_API") != "1"', src)

    def test_full_updates_fail_closed_without_sha256(self):
        import updater
        self.assertEqual(
            updater._asset_digest({"digest": "sha256:" + "a" * 64}), "a" * 64)
        self.assertEqual(updater._asset_digest({"digest": "sha256:bad"}), "")
        path, error = updater.download_update("https://example.invalid/update.zip", "")
        self.assertIsNone(path)
        self.assertIn("SHA-256", error)

    def test_flatten_map_is_request_local(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertNotIn("_FLATTEN_MAP = {}", src)
        self.assertIn("tools, flatten_map = _flatten_tools(tools)", src)
        self.assertIn('flatten_map.get(s["name"])', src)

    def test_settings_writes_are_atomic_and_remote_keys_are_required(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertIn("def _atomic_json_write(path, data):", src)
        self.assertNotIn('with open(SETTINGS_FILE, "w"', src)
        self.assertIn('detail="该远程供应商尚未配置 API Key"', src)

    def test_oauth_state_is_one_time_and_expires(self):
        import auth
        from urllib.parse import parse_qs, urlparse
        url = auth.github_oauth_url()
        state = parse_qs(urlparse(url).query)["state"][0]
        self.assertTrue(auth.consume_oauth_state(state))
        self.assertFalse(auth.consume_oauth_state(state))

    def test_runtime_auth_files_and_temp_json_are_ignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for name in ("users.json", "sessions.json", "projects.json", "mcp.local.json", "*.tmp.*"):
            self.assertRegex(ignore, rf"(?m)^{re.escape(name)}$")

    def test_plaintext_mcp_secrets_move_to_local_overlay(self):
        import json
        import mcp_client
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "mcp.json"
            local = pathlib.Path(tmp) / "mcp.local.json"
            base.write_text(json.dumps({"servers": {"demo": {
                "command": ["demo"], "env": {"LLM_API_KEY": "secret-value", "MODE": "safe"}
            }}}), encoding="utf-8")
            self.assertTrue(mcp_client._migrate_inline_secrets(str(base), str(local)))
            base_data = json.loads(base.read_text(encoding="utf-8"))
            local_data = json.loads(local.read_text(encoding="utf-8"))
            self.assertEqual(base_data["servers"]["demo"]["env"]["LLM_API_KEY"], "")
            self.assertEqual(local_data["servers"]["demo"]["env"]["LLM_API_KEY"], "secret-value")
            merged = mcp_client._merge_server_config(
                base_data["servers"]["demo"], local_data["servers"]["demo"])
            self.assertEqual(merged["env"]["MODE"], "safe")
            self.assertEqual(merged["env"]["LLM_API_KEY"], "secret-value")

    def test_tracked_mcp_template_contains_no_plaintext_secret(self):
        import json
        data = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
        found = []
        for name, entry in (data.get("servers") or {}).items():
            for key, value in (entry.get("env") or {}).items():
                if re.search(r"(^|[_-])(api[_-]?key|key|token|secret|password|credential)([_-]|$)",
                             str(key), re.I):
                    text = str(value or "").strip()
                    if text and not text.startswith(("${", "env:", "%")):
                        found.append(f"{name}.{key}")
        self.assertEqual(found, [], "plaintext MCP secrets must live in ignored mcp.local.json")


if __name__ == "__main__":
    unittest.main()
