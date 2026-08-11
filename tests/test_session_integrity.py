import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SessionIntegrityTests(unittest.TestCase):
    def test_user_message_append_is_durable_idempotent_and_ordered(self):
        import history

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with mock.patch.object(history, "BASE", str(root / "history")), \
                    mock.patch.object(history, "PROJECTS_FILE", str(root / "projects.json")):
                pathlib.Path(history.BASE).mkdir(parents=True)
                cid, first = history.append_message(
                    None,
                    {"id": "m-user-2", "role": "user", "content": "second", "ts": 2000},
                )
                history.append_message(
                    cid,
                    {"id": "m-user-1", "role": "user", "content": "first", "ts": 1000},
                )
                history.append_message(
                    cid,
                    {"id": "m-user-1", "role": "user", "content": "duplicate", "ts": 1000},
                )
                conv = history.get_conversation(cid)

        self.assertEqual(first["id"], "m-user-2")
        self.assertEqual([m["id"] for m in conv["messages"]], ["m-user-1", "m-user-2"])
        self.assertEqual([m["content"] for m in conv["messages"]], ["first", "second"])

    def test_legacy_messages_receive_stable_sequence_and_ascending_timestamps(self):
        import history

        messages = [
            {"role": "user", "content": "old without timestamp"},
            {"role": "assistant", "content": "later", "ts": 3000},
            {"role": "user", "content": "earlier", "ts": 2000},
        ]
        normalized = history.normalize_messages(messages)
        self.assertEqual([m["content"] for m in normalized], [
            "old without timestamp", "earlier", "later"])
        self.assertEqual([m["seq"] for m in normalized], [1, 3, 2])
        self.assertTrue(all(m.get("id") for m in normalized))
        again = history.normalize_messages(messages)
        self.assertEqual([m["id"] for m in normalized], [m["id"] for m in again])
        self.assertEqual([m["ts"] for m in normalized], [m["ts"] for m in again])

    def test_send_waits_for_durable_user_append_before_stream(self):
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function persistUserMessage", js)
        start = js.index("async function doSend(")
        end = js.index("function startCompareStream", start)
        body = js[start:end]
        self.assertLess(body.index("await persistUserMessage"), body.index("beginStream(opts)"))
        self.assertIn("crypto.randomUUID", body)

    def test_switch_and_new_chat_cancel_active_stream_before_navigation(self):
        js = (ROOT / "gui" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function cancelActiveStreamForSwitch", js)
        for signature in ("async function newChat()", "async function loadConversation(cid)"):
            start = js.index(signature)
            end = js.index("\n}", start)
            body = js[start:end]
            self.assertIn("await cancelActiveStreamForSwitch()", body)

    def test_server_exposes_request_cancel_and_never_persists_partial_assistant(self):
        src = (ROOT / "gui_server.py").read_text(encoding="utf-8")
        self.assertIn("_ACTIVE_CHAT_STREAMS", src)
        self.assertIn('@app.post("/api/chat/{request_id}/cancel")', src)
        self.assertNotIn('msgs + [{"role": "assistant", "content": acc}]', src)
        chat = src[src.index('@app.post("/api/chat")'):]
        self.assertIn("history_store.append_message(", chat)
        self.assertNotIn("history_store.save_conversation(msgs", chat)
        finally_end = src.index("return StreamingResponse", src.index('@app.post("/api/chat")'))
        finally_start = src.rindex("        finally:", src.index('@app.post("/api/chat")'), finally_end)
        self.assertNotIn("save_conversation", src[finally_start:finally_end])


if __name__ == "__main__":
    unittest.main()
