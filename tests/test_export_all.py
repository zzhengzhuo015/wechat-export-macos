import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import zstandard

from chat_export import export_all_sessions, list_message_databases, session_table_for_username


def _quote_sqlite_identifier(identifier):
    return f'"{str(identifier).replace(chr(34), chr(34) * 2)}"'


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

    def _create_session_table(self, table_name, include_source=False):
        message_db = self.decrypted_dir / "message" / "message_0.db"
        conn = sqlite3.connect(message_db)
        source_column = ", source BLOB" if include_source else ""
        source_compression_column = ", WCDB_CT_source INTEGER" if include_source else ""
        quoted_table_name = _quote_sqlite_identifier(table_name)
        conn.execute(
            f"""
            CREATE TABLE {quoted_table_name} (
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                create_time INTEGER,
                real_sender_id INTEGER,
                message_content BLOB
                {source_column}
                {source_compression_column}
            )
            """
        )
        conn.commit()
        conn.close()

    def _insert_message(self, table_name, row, source=None, source_compression_type=0):
        message_db = self.decrypted_dir / "message" / "message_0.db"
        conn = sqlite3.connect(message_db)
        quoted_table_name = _quote_sqlite_identifier(table_name)
        columns = [column[1] for column in conn.execute(f"PRAGMA table_info({quoted_table_name})")]
        has_source = "source" in columns
        has_source_compression = "WCDB_CT_source" in columns
        if has_source and has_source_compression:
            conn.execute(
                f"""
                INSERT INTO {quoted_table_name}
                (local_id, server_id, local_type, create_time, real_sender_id, message_content, source, WCDB_CT_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*row, source, source_compression_type),
            )
        elif has_source:
            conn.execute(
                f"""
                INSERT INTO {quoted_table_name}
                (local_id, server_id, local_type, create_time, real_sender_id, message_content, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (*row, source),
            )
        else:
            conn.execute(
                f"""
                INSERT INTO {quoted_table_name}
                (local_id, server_id, local_type, create_time, real_sender_id, message_content)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        conn.commit()
        conn.close()

    def _create_source_file(self, username, media_dir, file_name, content):
        session_hash = session_table_for_username(username)[4:]
        source_dir = self.base / "msg" / "attach" / session_hash / "2024-08" / media_dir
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / file_name).write_bytes(content)

    def _make_image_bytes(self, image_format):
        from PIL import Image

        image = Image.new("RGB", (4, 4), (32, 128, 200))
        output = io.BytesIO()
        image.save(output, format=image_format)
        return output.getvalue()

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

    def test_export_all_sessions_exports_media_for_unresolved_msg_table_using_existing_hash(self):
        unresolved_table = "Msg_abcdefabcdefabcdefabcdefabcdefab"
        self._create_session_table(unresolved_table)
        self._insert_message(
            unresolved_table,
            (7, 0, 3, 1722858500, 2, "[图片]"),
        )

        source_dir = self.base / "msg" / "attach" / unresolved_table[4:] / "2024-08" / "Img"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "7_1722858500.dat").write_bytes(self._make_image_bytes("PNG"))

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        unresolved_payload = json.loads(
            (self.output_dir / f"{unresolved_table}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            unresolved_payload["messages"][0]["file_path"],
            f"images/{unresolved_table[4:]}/7_1722858500.jpg",
        )
        self.assertTrue((self.output_dir / unresolved_payload["messages"][0]["file_path"]).exists())

    def test_unresolved_sender_does_not_fallback_to_session_username(self):
        unresolved_table = "Msg_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        self._create_session_table(unresolved_table)
        self._insert_message(
            unresolved_table,
            (4, 0, 1, 1722858200, 999, "未知发送者"),
        )

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
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

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
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

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
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

    def test_export_all_sessions_normalizes_media_and_annotates_json_messages(self):
        direct_table = session_table_for_username("wxid_friend")
        self._insert_message(
            direct_table,
            (3, 23456, 3, 1722858100, 2, "[图片]"),
        )
        self._insert_message(
            direct_table,
            (4, 34567, 34, 1722858101, 2, "[语音]"),
        )
        self._create_source_file("wxid_friend", "Img", "3_1722858100.dat", self._make_image_bytes("PNG"))
        self._create_source_file("wxid_friend", "Audio", "4_1722858101.silk", b"audio-bytes")

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
            export_all_sessions(
                str(self.decrypted_dir),
                str(self.output_dir),
                owner_id="wxid_owner",
            )

        direct_payload = json.loads((self.output_dir / "老张.json").read_text(encoding="utf-8"))
        media_messages = [message for message in direct_payload["messages"] if message.get("file_path")]
        session_hash = session_table_for_username("wxid_friend")[4:]

        self.assertEqual(
            [message["file_path"] for message in media_messages],
            [
                f"images/{session_hash}/3_1722858100.jpg",
                f"audio/{session_hash}/4_1722858101.wav",
            ],
        )
        self.assertTrue((self.output_dir / media_messages[0]["file_path"]).exists())
        self.assertTrue((self.output_dir / media_messages[1]["file_path"]).exists())

    def test_export_all_sessions_adds_mentions_for_text_messages_when_source_has_atuserlist(self):
        mention_username = "wxid_mentions"
        mention_table = session_table_for_username(mention_username)
        self._insert_contact(mention_username, nick_name="提及会话")
        self._create_session_table(mention_table, include_source=True)
        self._insert_message(
            mention_table,
            (8, 0, 1, 1722858600, 2, "你好"),
            source="<msgsource><atuserlist>wxid_friend,wxid_owner</atuserlist></msgsource>",
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        payload = json.loads((self.output_dir / "提及会话.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["messages"][0]["content"], "你好")
        self.assertEqual(
            payload["messages"][0]["mentions"],
            [
                {"wxid": "wxid_friend"},
                {"wxid": "wxid_owner"},
            ],
        )
        self.assertNotIn("_source", payload["messages"][0])

    def test_export_all_sessions_without_source_column_exports_without_mentions(self):
        no_source_username = "wxid_no_source"
        no_source_table = session_table_for_username(no_source_username)
        self._insert_contact(no_source_username, nick_name="无来源")
        self._create_session_table(no_source_table, include_source=False)
        self._insert_message(
            no_source_table,
            (9, 0, 1, 1722858700, 2, "普通文本"),
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        payload = json.loads((self.output_dir / "无来源.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["messages"][0]["content"], "普通文本")
        self.assertNotIn("mentions", payload["messages"][0])

    def test_export_all_sessions_with_null_source_exports_without_mentions(self):
        nullable_source_username = "wxid_null_source"
        nullable_source_table = session_table_for_username(nullable_source_username)
        self._insert_contact(nullable_source_username, nick_name="空来源")
        self._create_session_table(nullable_source_table, include_source=True)
        self._insert_message(
            nullable_source_table,
            (10, 0, 1, 1722858800, 2, "无提及"),
            source=None,
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        payload = json.loads((self.output_dir / "空来源.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["messages"][0]["content"], "无提及")
        self.assertNotIn("mentions", payload["messages"][0])

    def test_export_all_sessions_parses_mentions_from_bytes_escaped_source_and_dedupes(self):
        escaped_source_username = "wxid_escaped_source"
        escaped_source_table = session_table_for_username(escaped_source_username)
        self._insert_contact(escaped_source_username, nick_name="转义来源")
        self._create_session_table(escaped_source_table, include_source=True)
        escaped_source = (
            b"&lt;msgsource&gt;&lt;atuserlist&gt;"
            b"wxid_friend,wxid_owner,wxid_friend"
            b"&lt;/atuserlist&gt;&lt;/msgsource&gt;"
        )
        self._insert_message(
            escaped_source_table,
            (11, 0, 1, 1722858900, 2, "测试提及"),
            source=escaped_source,
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        payload = json.loads((self.output_dir / "转义来源.json").read_text(encoding="utf-8"))
        self.assertEqual(
            payload["messages"][0]["mentions"],
            [{"wxid": "wxid_friend"}, {"wxid": "wxid_owner"}],
        )

    def test_export_all_sessions_parses_mentions_from_zstd_source(self):
        compressed_source_username = "wxid_zstd_source"
        compressed_source_table = session_table_for_username(compressed_source_username)
        self._insert_contact(compressed_source_username, nick_name="压缩来源")
        self._create_session_table(compressed_source_table, include_source=True)
        compressed_source = zstandard.ZstdCompressor().compress(
            b"<msgsource><atuserlist>wxid_friend,wxid_owner,wxid_friend</atuserlist></msgsource>"
        )
        self._insert_message(
            compressed_source_table,
            (12, 0, 1, 1722859000, 2, "@friend hello"),
            source=compressed_source,
            source_compression_type=4,
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        payload = json.loads((self.output_dir / "压缩来源.json").read_text(encoding="utf-8"))
        self.assertEqual(
            payload["messages"][0]["mentions"],
            [
                {"wxid": "wxid_friend", "text": "@friend", "start": 0, "end": 7},
                {"wxid": "wxid_owner"},
            ],
        )

    def test_export_all_sessions_maps_mentions_to_text_segments_when_detectable(self):
        mapped_username = "wxid_mapped"
        mapped_table = session_table_for_username(mapped_username)
        self._insert_contact(mapped_username, nick_name="映射来源")
        self._create_session_table(mapped_table, include_source=True)
        self._insert_message(
            mapped_table,
            (13, 0, 1, 1722859100, 2, "@史迪仔\u2005 测试@的"),
            source="<msgsource><atuserlist>wxid_stitch</atuserlist></msgsource>",
        )

        export_all_sessions(
            str(self.decrypted_dir),
            str(self.output_dir),
            owner_id="wxid_owner",
        )

        payload = json.loads((self.output_dir / "映射来源.json").read_text(encoding="utf-8"))
        self.assertEqual(
            payload["messages"][0]["mentions"],
            [{"wxid": "wxid_stitch", "text": "@史迪仔", "start": 0, "end": 4}],
        )


if __name__ == "__main__":
    unittest.main()
