import unittest

from chat_export import (
    build_chatlab_payload,
    dedupe_filename,
    sanitize_filename,
)


class FilenameTests(unittest.TestCase):
    def test_sanitize_filename_replaces_forbidden_characters(self):
        self.assertEqual(
            sanitize_filename("黑手册/唯一群:北京"),
            "黑手册_唯一群_北京",
        )

    def test_sanitize_filename_strips_whitespace_and_trailing_dots(self):
        self.assertEqual(
            sanitize_filename("  会话名.  "),
            "会话名",
        )

    def test_sanitize_filename_falls_back_when_empty_after_cleaning(self):
        self.assertEqual(
            sanitize_filename(" ... "),
            "未命名会话",
        )

    def test_dedupe_filename_returns_base_json_for_first_use(self):
        used = set()
        self.assertEqual(dedupe_filename("黑手册目前唯一群🪺🐉", used), "黑手册目前唯一群🪺🐉.json")
        self.assertEqual(used, {"黑手册目前唯一群🪺🐉.json"})

    def test_dedupe_filename_appends_numeric_suffix_for_duplicates(self):
        used = {"黑手册目前唯一群🪺🐉.json"}
        self.assertEqual(dedupe_filename("黑手册目前唯一群🪺🐉", used), "黑手册目前唯一群🪺🐉-2.json")
        self.assertEqual(
            used,
            {"黑手册目前唯一群🪺🐉.json", "黑手册目前唯一群🪺🐉-2.json"},
        )


class PayloadTests(unittest.TestCase):
    def test_build_chatlab_payload_matches_sample_shape(self):
        members = [
            {"platformId": "20505429106@chatroom", "accountName": "黑手册目前唯一群🪺🐉"},
            {"platformId": "ycosxhack", "accountName": "余弦"},
        ]
        messages = [
            {
                "sender": "ycosxhack",
                "accountName": "余弦",
                "timestamp": 1722857963,
                "type": 0,
                "content": "你们明天见",
                "platformMessageId": "537145736263414100",
            }
        ]
        payload = build_chatlab_payload(
            session_name="黑手册目前唯一群🪺🐉",
            session_id="20505429106@chatroom",
            session_type="group",
            owner_id="wxid_owner",
            members=members,
            messages=messages,
            exported_at=1776240866,
        )

        self.assertEqual(set(payload.keys()), {"chatlab", "meta", "members", "messages"})
        self.assertEqual(payload["chatlab"]["version"], "0.0.2")
        self.assertEqual(payload["chatlab"]["exportedAt"], 1776240866)
        self.assertEqual(payload["chatlab"]["generator"], "wechat-export-macos")
        self.assertEqual(payload["meta"]["name"], "黑手册目前唯一群🪺🐉")
        self.assertEqual(payload["meta"]["platform"], "wechat")
        self.assertEqual(payload["meta"]["type"], "group")
        self.assertEqual(payload["meta"]["ownerId"], "wxid_owner")
        self.assertEqual(payload["meta"]["groupId"], "20505429106@chatroom")
        self.assertEqual(payload["members"], members)
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(
            set(payload["messages"][0].keys()),
            {"sender", "accountName", "timestamp", "type", "content", "platformMessageId"},
        )


if __name__ == "__main__":
    unittest.main()
