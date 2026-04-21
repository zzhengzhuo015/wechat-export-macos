import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from chat_export import export_all_sessions, list_message_databases, session_table_for_username


class ExportAllSessionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.decrypted_dir = self.base / "decrypted"
        self.output_dir = self.base / "output"
        (self.decrypted_dir / "contact").mkdir(parents=True, exist_ok=True)
        (self.decrypted_dir / "message").mkdir(parents=True, exist_ok=True)
        self._create_contact_db()
        self._create_message_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_contact_db(self):
        contact_db = self.decrypted_dir / "contact" / "contact.db"
        conn = sqlite3.connect(contact_db)
        conn.execute(
            """
            CREATE TABLE contact (
                username TEXT PRIMARY KEY,
                nick_name TEXT,
                remark TEXT,
                alias TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO contact (username, nick_name, remark, alias) VALUES (?, ?, ?, ?)",
            [
                ("20505429106@chatroom", "黑手册目前唯一群🪺🐉", "", ""),
                ("wxid_friend", "张三", "老张", ""),
                ("wxid_owner", "我", "", ""),
            ],
        )
        conn.commit()
        conn.close()

    def _create_message_db(self):
        message_db = self.decrypted_dir / "message" / "message_0.db"
        conn = sqlite3.connect(message_db)
        conn.execute("CREATE TABLE Name2Id (rowid INTEGER PRIMARY KEY, user_name TEXT)")
        conn.executemany(
            "INSERT INTO Name2Id (rowid, user_name) VALUES (?, ?)",
            [
                (1, "ycosxhack"),
                (2, "wxid_friend"),
            ],
        )

        group_table = session_table_for_username("20505429106@chatroom")
        direct_table = session_table_for_username("wxid_friend")
        conn.execute(
            f"""
            CREATE TABLE {group_table} (
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                create_time INTEGER,
                real_sender_id INTEGER,
                message_content BLOB
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {direct_table} (
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                create_time INTEGER,
                real_sender_id INTEGER,
                message_content BLOB
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {group_table}
            (local_id, server_id, local_type, create_time, real_sender_id, message_content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1, 537145736263414100, 1, 1722857963, 1, "你们明天见"),
        )
        conn.execute(
            f"""
            INSERT INTO {direct_table}
            (local_id, server_id, local_type, create_time, real_sender_id, message_content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (2, 12345, 1, 1722858000, 2, "晚上好"),
        )
        conn.commit()
        conn.close()

    def _insert_contact(self, username, nick_name="", remark="", alias=""):
        contact_db = self.decrypted_dir / "contact" / "contact.db"
        conn = sqlite3.connect(contact_db)
        conn.execute(
            "INSERT INTO contact (username, nick_name, remark, alias) VALUES (?, ?, ?, ?)",
            (username, nick_name, remark, alias),
        )
        conn.commit()
        conn.close()

    def _create_session_table(self, table_name):
        message_db = self.decrypted_dir / "message" / "message_0.db"
        conn = sqlite3.connect(message_db)
        conn.execute(
            f"""
            CREATE TABLE {table_name} (
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                create_time INTEGER,
                real_sender_id INTEGER,
                message_content BLOB
            )
            """
        )
        conn.commit()
        conn.close()

    def _insert_message(self, table_name, row):
        message_db = self.decrypted_dir / "message" / "message_0.db"
        conn = sqlite3.connect(message_db)
        conn.execute(
            f"""
            INSERT INTO {table_name}
            (local_id, server_id, local_type, create_time, real_sender_id, message_content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        conn.commit()
        conn.close()

    def test_session_table_for_username_uses_md5_hash(self):
        self.assertEqual(
            session_table_for_username("20505429106@chatroom"),
            "Msg_080e92f37eb13a0902b87ad72430a254",
        )
        self.assertEqual(
            session_table_for_username("wxid_friend"),
            "Msg_d5616d78f22fe35c632f66cabecfc82d",
        )

    def test_list_message_databases_includes_message_dbs_with_fts_in_name(self):
        extra_db = self.decrypted_dir / "message" / "message_fts.db"
        sqlite3.connect(extra_db).close()

        result = list_message_databases(str(self.decrypted_dir))
        basenames = [Path(p).name for p in result]
        self.assertEqual(basenames, ["message_0.db", "message_fts.db"])

    def test_export_all_sessions_writes_group_and_direct_json_files(self):
        summary = export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["failed_count"], 0)
        self.assertEqual(
            sorted(path["file"] for path in summary["files"]),
            ["老张.json", "黑手册目前唯一群🪺🐉.json"],
        )

        exported_files = sorted(p.name for p in self.output_dir.glob("*.json"))
        self.assertEqual(exported_files, ["老张.json", "黑手册目前唯一群🪺🐉.json"])

        group_payload = json.loads((self.output_dir / "黑手册目前唯一群🪺🐉.json").read_text(encoding="utf-8"))
        self.assertEqual(group_payload["meta"]["type"], "group")
        self.assertEqual(group_payload["messages"][0]["sender"], "ycosxhack")
        self.assertEqual(group_payload["messages"][0]["type"], 0)

    def test_export_all_sessions_exports_unresolved_msg_table_with_fallback_name(self):
        unresolved_table = "Msg_abcdefabcdefabcdefabcdefabcdefab"
        self._create_session_table(unresolved_table)
        self._insert_message(
            unresolved_table,
            (3, 0, 1, 1722858100, 2, "未解析会话"),
        )

        summary = export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        self.assertEqual(summary["success_count"], 3)
        exported_files = sorted(p.name for p in self.output_dir.glob("*.json"))
        self.assertIn(f"{unresolved_table}.json", exported_files)
        unresolved_payload = json.loads(
            (self.output_dir / f"{unresolved_table}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(unresolved_payload["meta"]["name"], unresolved_table)

    def test_unresolved_sender_does_not_fallback_to_session_username(self):
        unresolved_table = "Msg_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        self._create_session_table(unresolved_table)
        self._insert_message(
            unresolved_table,
            (4, 0, 1, 1722858200, 999, "未知发送者"),
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        unresolved_payload = json.loads(
            (self.output_dir / f"{unresolved_table}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(unresolved_payload["messages"][0]["sender"], "999")
        self.assertNotEqual(unresolved_payload["messages"][0]["sender"], unresolved_table)

    def test_contact_display_name_priority_uses_alias_when_remark_and_nick_missing(self):
        alias_username = "wxid_alias_only"
        self._insert_contact(alias_username, nick_name="", remark="", alias="小明号")
        alias_table = session_table_for_username(alias_username)
        self._create_session_table(alias_table)
        self._insert_message(
            alias_table,
            (5, 0, 1, 1722858300, 2, "alias 测试"),
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        self.assertTrue((self.output_dir / "小明号.json").exists())
        alias_payload = json.loads((self.output_dir / "小明号.json").read_text(encoding="utf-8"))
        self.assertEqual(alias_payload["meta"]["name"], "小明号")

    def test_non_text_empty_content_uses_readable_placeholder(self):
        unresolved_table = "Msg_cccccccccccccccccccccccccccccccc"
        self._create_session_table(unresolved_table)
        self._insert_message(
            unresolved_table,
            (6, 0, 3, 1722858400, 999, None),
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        unresolved_payload = json.loads(
            (self.output_dir / f"{unresolved_table}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(unresolved_payload["messages"][0]["type"], 1)
        self.assertEqual(unresolved_payload["messages"][0]["content"], "[非文本消息]")


if __name__ == "__main__":
    unittest.main()
