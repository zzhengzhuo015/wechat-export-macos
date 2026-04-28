import io
import json
import sqlite3
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import zstandard

from chat_export import (
    _attach_mentions_to_message,
    _extract_mentions_from_source,
    _infer_media_kind_from_path,
    build_chatlab_payload,
    dedupe_filename,
    export_single_session,
    sanitize_filename,
    session_table_for_username,
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


class SingleSessionMediaExportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.decrypted_dir = self.base / "decrypted"
        self.output_dir = self.base / "output"
        (self.decrypted_dir / "message").mkdir(parents=True, exist_ok=True)
        self.contact_username = "wxid_friend"
        self.session_hash = session_table_for_username(self.contact_username)[4:]
        self._create_message_db()
        self._create_source_file(
            "Img",
            "1_1722858000.dat",
            self._make_image_bytes("PNG"),
        )
        self._create_source_file(
            "Audio",
            "2_1722858001.silk",
            b"fake-audio",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_message_db(self):
        message_db = self.decrypted_dir / "message" / "message_0.db"
        conn = sqlite3.connect(message_db)
        conn.execute("CREATE TABLE Name2Id (rowid INTEGER PRIMARY KEY, user_name TEXT)")
        conn.execute(
            f"""
            CREATE TABLE {session_table_for_username(self.contact_username)} (
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                create_time INTEGER,
                real_sender_id INTEGER,
                message_content BLOB,
                source TEXT,
                WCDB_CT_message_content INTEGER,
                WCDB_CT_source INTEGER
            )
            """
        )
        conn.executemany(
            f"""
            INSERT INTO {session_table_for_username(self.contact_username)}
            (local_id, server_id, local_type, create_time, real_sender_id, message_content, source, WCDB_CT_message_content, WCDB_CT_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 111, 3, 1722858000, 0, "[图片]", "", 0, 0),
                (2, 222, 34, 1722858001, 0, "[语音]", "", 0, 0),
            ],
        )
        conn.commit()
        conn.close()

    def _insert_session_message_row(
        self,
        local_id,
        server_id,
        local_type,
        create_time,
        message_content,
        source,
        source_compression_type=0,
    ):
        message_db = self.decrypted_dir / "message" / "message_0.db"
        conn = sqlite3.connect(message_db)
        conn.execute(
            f"""
            INSERT INTO {session_table_for_username(self.contact_username)}
            (local_id, server_id, local_type, create_time, real_sender_id, message_content, source, WCDB_CT_message_content, WCDB_CT_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (local_id, server_id, local_type, create_time, 0, message_content, source, 0, source_compression_type),
        )
        conn.commit()
        conn.close()

    def _compress_source(self, text):
        return zstandard.ZstdCompressor().compress(text.encode("utf-8"))

    def _create_media_db(self, voice_rows):
        media_db = self.decrypted_dir / "message" / "media_0.db"
        conn = sqlite3.connect(media_db)
        conn.execute("CREATE TABLE Name2Id (user_name TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE TimeStamp (timestamp INTEGER)")
        conn.execute(
            """
            CREATE TABLE VoiceInfo (
                chat_name_id INTEGER,
                create_time INTEGER,
                local_id INTEGER,
                svr_id INTEGER,
                voice_data BLOB,
                data_index TEXT DEFAULT '0'
            )
            """
        )
        conn.execute("INSERT INTO Name2Id (rowid, user_name) VALUES (?, ?)", (1, self.contact_username))
        conn.executemany(
            """
            INSERT INTO VoiceInfo (chat_name_id, create_time, local_id, svr_id, voice_data, data_index)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            voice_rows,
        )
        conn.commit()
        conn.close()

    def _create_source_file(self, media_dir, file_name, content):
        source_dir = self.base / "msg" / "attach" / self.session_hash / "2024-08" / media_dir
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / file_name).write_bytes(content)

    def _make_image_bytes(self, image_format):
        from PIL import Image

        image = Image.new("RGB", (4, 4), (24, 96, 180))
        output = io.BytesIO()
        image.save(output, format=image_format)
        return output.getvalue()

    def _make_wav_bytes(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(b"\x00\x00" * 8)
        return output.getvalue()

    def _make_v2_dat_bytes(self, image_bytes, aes_key, xor_key):
        from Crypto.Cipher import AES
        from Crypto.Util import Padding

        aes_size = 16
        xor_size = 2
        aes_plain = image_bytes[:aes_size]
        raw_middle = image_bytes[aes_size:-xor_size]
        xor_tail = image_bytes[-xor_size:]

        aes_cipher = AES.new(aes_key[:16], AES.MODE_ECB)
        aes_encrypted = aes_cipher.encrypt(Padding.pad(aes_plain, AES.block_size))
        xor_encrypted = bytes(value ^ xor_key for value in xor_tail)
        dat_bytes = (
            b"\x07\x08V2\x08\x07"
            + aes_size.to_bytes(4, "little")
            + xor_size.to_bytes(4, "little")
            + b"\x01"
            + aes_encrypted
            + raw_middle
            + xor_encrypted
        )
        return aes_encrypted[:16].hex(), dat_bytes

    def test_export_single_session_normalizes_image_and_audio_paths(self):
        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
            exported_count = export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(exported_count, 2)
        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["file_path"], f"images/{self.session_hash}/1_1722858000.jpg")
        self.assertEqual(payload[1]["file_path"], f"audio/{self.session_hash}/2_1722858001.wav")
        self.assertTrue((self.output_dir / payload[0]["file_path"]).exists())
        self.assertTrue((self.output_dir / payload[1]["file_path"]).exists())

    def test_export_single_session_uses_wechat_base_dir_when_decrypted_dir_is_elsewhere(self):
        original_media_root = self.base / "msg"
        if original_media_root.exists():
            for path in sorted(original_media_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
            original_media_root.rmdir()

        real_media_root = self.base / "wechat_data_root"
        source_dir = real_media_root / "msg" / "attach" / self.session_hash / "2024-08" / "Audio"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_file = source_dir / "2_1722858001.silk"
        source_file.write_bytes(b"real-audio")

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True), patch(
            "chat_export.load_config",
            return_value={"wechat_base_dir": str(real_media_root)},
            create=True,
        ):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[1]["file_path"], f"audio/{self.session_hash}/2_1722858001.wav")
        self.assertTrue((self.output_dir / payload[1]["file_path"]).exists())

    def test_export_single_session_falls_back_to_raw_image_when_conversion_fails(self):
        raw_bytes = (self.base / "msg" / "attach" / self.session_hash / "2024-08" / "Img" / "1_1722858000.dat").read_bytes()
        with patch("chat_export.try_convert_image_file", return_value=None, create=True), patch(
            "chat_export.try_convert_silk_bytes_to_wav",
            return_value=b"RIFFmock-wav",
            create=True,
        ):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["file_path"], f"images/{self.session_hash}/1_1722858000.dat")
        self.assertEqual((self.output_dir / payload[0]["file_path"]).read_bytes(), raw_bytes)

    def test_export_single_session_exports_voice_blob_from_media_db(self):
        original_media_root = self.base / "msg"
        if original_media_root.exists():
            for path in sorted(original_media_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
            original_media_root.rmdir()

        self._create_media_db(
            [
                (1, 1722858001, 2, 222, b"voice-from-db", "0"),
            ]
        )

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[1]["file_path"], f"audio/{self.session_hash}/2_1722858001.wav")
        self.assertTrue((self.output_dir / payload[1]["file_path"]).exists())

    def test_export_single_session_falls_back_to_voice_blob_when_disk_audio_conversion_fails(self):
        self._create_media_db(
            [
                (1, 1722858001, 2, 222, b"voice-from-db", "0"),
            ]
        )

        with patch(
            "chat_export.try_convert_silk_bytes_to_wav",
            side_effect=[None, b"RIFFmock-wav"],
            create=True,
        ):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[1]["file_path"], f"audio/{self.session_hash}/2_1722858001.wav")
        self.assertEqual((self.output_dir / payload[1]["file_path"]).read_bytes(), b"RIFFmock-wav")

    def test_export_single_session_returns_empty_path_when_voice_blob_conversion_fails(self):
        original_media_root = self.base / "msg"
        if original_media_root.exists():
            for path in sorted(original_media_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
            original_media_root.rmdir()

        self._create_media_db(
            [
                (1, 1722858001, 2, 222, b"voice-from-db", "0"),
            ]
        )

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=None, create=True):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[1]["file_path"], f"audio/{self.session_hash}/2_1722858001.silk")

    def test_export_single_session_falls_back_to_raw_audio_when_conversion_fails(self):
        raw_audio = (self.base / "msg" / "attach" / self.session_hash / "2024-08" / "Audio" / "2_1722858001.silk").read_bytes()
        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=None, create=True):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[1]["file_path"], f"audio/{self.session_hash}/2_1722858001.silk")
        self.assertEqual((self.output_dir / payload[1]["file_path"]).read_bytes(), raw_audio)

    def test_export_single_session_copies_existing_wav_audio_without_silk_conversion(self):
        source_dir = self.base / "msg" / "attach" / self.session_hash / "2024-08" / "Audio"
        source_file = source_dir / "2_1722858001.wav"
        wav_bytes = self._make_wav_bytes()
        source_file.write_bytes(wav_bytes)

        old_silk_path = source_dir / "2_1722858001.silk"
        if old_silk_path.exists():
            old_silk_path.unlink()

        export_single_session(
            decrypted_dir=str(self.decrypted_dir),
            contact_username=self.contact_username,
            contact_display_name="好友",
            output_dir=str(self.output_dir),
            owner_id="wxid_owner",
            printer=lambda *_args, **_kwargs: None,
        )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[1]["file_path"], f"audio/{self.session_hash}/2_1722858001.wav")
        self.assertEqual((self.output_dir / payload[1]["file_path"]).read_bytes(), wav_bytes)

    def test_export_single_session_decodes_v2_dat_image_with_image_key_map(self):
        image_bytes = self._make_image_bytes("JPEG")
        aes_key = bytes.fromhex("11223344556677889900aabbccddeeff")
        ciphertext_hex, full_dat = self._make_v2_dat_bytes(image_bytes, aes_key, xor_key=0x42)
        _, thumb_dat = self._make_v2_dat_bytes(image_bytes, aes_key, xor_key=0x42)

        img_dir = self.base / "msg" / "attach" / self.session_hash / "2024-08" / "Img"
        (img_dir / "1_1722858000.dat").write_bytes(full_dat)
        (img_dir / "1_1722858000_t.dat").write_bytes(thumb_dat)

        with patch("media_conversion.load_wechat_image_keys", return_value={ciphertext_hex: aes_key}):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["file_path"], f"images/{self.session_hash}/1_1722858000.jpg")
        self.assertTrue((self.output_dir / payload[0]["file_path"]).exists())

    def test_export_single_session_json_adds_mentions_for_text_message_source_metadata(self):
        self._insert_session_message_row(
            local_id=3,
            server_id=333,
            local_type=1,
            create_time=1722858002,
            message_content="@alice @bob hello",
            source="<msgsource><atuserlist>wxid_alice,wxid_bob</atuserlist></msgsource>",
        )

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        text_message = next(message for message in payload if message.get("content") == "@alice @bob hello")
        self.assertEqual(
            text_message.get("mentions"),
            [
                {"wxid": "wxid_alice", "text": "@alice", "start": 0, "end": 6},
                {"wxid": "wxid_bob", "text": "@bob", "start": 7, "end": 11},
            ],
        )
        self.assertEqual(text_message.get("content"), "@alice @bob hello")
        self.assertNotIn("_source", text_message)

    def test_export_single_session_json_adds_mentions_for_zstd_source_metadata(self):
        compressed_source = self._compress_source(
            "<msgsource><atuserlist>wxid_alice,wxid_bob,wxid_alice</atuserlist></msgsource>"
        )
        self._insert_session_message_row(
            local_id=5,
            server_id=555,
            local_type=1,
            create_time=1722858004,
            message_content="@alice @bob compressed",
            source=compressed_source,
            source_compression_type=4,
        )

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        text_message = next(message for message in payload if message.get("content") == "@alice @bob compressed")
        self.assertEqual(
            text_message.get("mentions"),
            [
                {"wxid": "wxid_alice", "text": "@alice", "start": 0, "end": 6},
                {"wxid": "wxid_bob", "text": "@bob", "start": 7, "end": 11},
            ],
        )

    def test_export_single_session_json_maps_mentions_to_text_segments(self):
        self._insert_session_message_row(
            local_id=6,
            server_id=666,
            local_type=1,
            create_time=1722858005,
            message_content="@史迪仔\u2005 测试@的",
            source="<msgsource><atuserlist>wxid_stitch</atuserlist></msgsource>",
        )

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        text_message = next(message for message in payload if message.get("content") == "@史迪仔\u2005 测试@的")
        self.assertEqual(
            text_message.get("mentions"),
            [{"wxid": "wxid_stitch", "text": "@史迪仔", "start": 0, "end": 4}],
        )

    def test_export_single_session_json_does_not_add_mentions_when_atuserlist_missing_or_empty(self):
        self._insert_session_message_row(
            local_id=3,
            server_id=333,
            local_type=1,
            create_time=1722858002,
            message_content="@alice empty-atuserlist",
            source="<msgsource><atuserlist> </atuserlist></msgsource>",
        )
        self._insert_session_message_row(
            local_id=4,
            server_id=444,
            local_type=1,
            create_time=1722858003,
            message_content="@alice missing-atuserlist-tag",
            source="<msgsource></msgsource>",
        )

        with patch("chat_export.try_convert_silk_bytes_to_wav", return_value=b"RIFFmock-wav", create=True):
            export_single_session(
                decrypted_dir=str(self.decrypted_dir),
                contact_username=self.contact_username,
                contact_display_name="好友",
                output_dir=str(self.output_dir),
                owner_id="wxid_owner",
                printer=lambda *_args, **_kwargs: None,
            )

        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        empty_list_message = next(
            message for message in payload if message.get("content") == "@alice empty-atuserlist"
        )
        missing_tag_message = next(
            message for message in payload if message.get("content") == "@alice missing-atuserlist-tag"
        )
        self.assertNotIn("mentions", empty_list_message)
        self.assertNotIn("_source", empty_list_message)
        self.assertNotIn("mentions", missing_tag_message)
        self.assertNotIn("_source", missing_tag_message)


class SingleSessionSchemaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.decrypted_dir = self.base / "decrypted"
        self.output_dir = self.base / "output"
        (self.decrypted_dir / "message").mkdir(parents=True, exist_ok=True)
        self.contact_username = "wxid_friend"
        self._create_message_db_without_source()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_message_db_without_source(self):
        message_db = self.decrypted_dir / "message" / "message_0.db"
        conn = sqlite3.connect(message_db)
        conn.execute("CREATE TABLE Name2Id (rowid INTEGER PRIMARY KEY, user_name TEXT)")
        conn.execute(
            f"""
            CREATE TABLE {session_table_for_username(self.contact_username)} (
                local_id INTEGER,
                server_id INTEGER,
                local_type INTEGER,
                create_time INTEGER,
                real_sender_id INTEGER,
                message_content BLOB,
                WCDB_CT_message_content INTEGER
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {session_table_for_username(self.contact_username)}
            (local_id, server_id, local_type, create_time, real_sender_id, message_content, WCDB_CT_message_content)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 111, 1, 1722858000, 0, "hello without source column", 0),
        )
        conn.commit()
        conn.close()

    def test_export_single_session_succeeds_when_source_column_missing(self):
        exported_count = export_single_session(
            decrypted_dir=str(self.decrypted_dir),
            contact_username=self.contact_username,
            contact_display_name="好友",
            output_dir=str(self.output_dir),
            owner_id="wxid_owner",
            printer=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(exported_count, 1)
        payload = json.loads((self.output_dir / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["content"], "hello without source column")
        self.assertNotIn("mentions", payload[0])


class MediaPathInferenceTests(unittest.TestCase):
    def test_infer_media_kind_prefers_directory_segments_and_suffixes(self):
        image_path = Path("/tmp/audio_note/attach/session/2024-08/Img/1_2.dat")
        audio_path = Path("/tmp/image_archive/attach/session/2024-08/Audio/1_2.silk")
        unknown_path = Path("/tmp/cache/audioish-folder/1_2.bin")

        self.assertEqual(_infer_media_kind_from_path(image_path), "images")
        self.assertEqual(_infer_media_kind_from_path(audio_path), "audio")
        self.assertIsNone(_infer_media_kind_from_path(unknown_path))

    def test_infer_media_kind_does_not_treat_heic_as_supported_image_source(self):
        heic_path = Path("/tmp/attach/session/2024-08/Img/1_2.heic")

        self.assertIsNone(_infer_media_kind_from_path(heic_path))


class MentionParserTests(unittest.TestCase):
    def test_extract_mentions_from_source_reads_atuserlist_in_order(self):
        source = "<msgsource><atuserlist>wxid_alice,wxid_bob,wxid_carol</atuserlist></msgsource>"

        mentions = _extract_mentions_from_source(source)

        self.assertEqual(
            mentions,
            [{"wxid": "wxid_alice"}, {"wxid": "wxid_bob"}, {"wxid": "wxid_carol"}],
        )

    def test_extract_mentions_from_source_dedupes_and_unescapes_xml(self):
        escaped_source = (
            "&lt;msgsource&gt;&lt;atuserlist&gt;"
            "wxid_alice,wxid_alice,wxid_bob,wxid_alice,wxid_carol,wxid_bob"
            "&lt;/atuserlist&gt;&lt;/msgsource&gt;"
        )

        mentions = _extract_mentions_from_source(escaped_source)

        self.assertEqual(
            mentions,
            [{"wxid": "wxid_alice"}, {"wxid": "wxid_bob"}, {"wxid": "wxid_carol"}],
        )

    def test_extract_mentions_from_source_reads_bytes_and_case_insensitive_tag(self):
        source_bytes = b"<msgsource><ATUSERLIST>wxid_alice,wxid_bob,wxid_alice</ATUSERLIST></msgsource>"

        mentions = _extract_mentions_from_source(source_bytes)

        self.assertEqual(
            mentions,
            [{"wxid": "wxid_alice"}, {"wxid": "wxid_bob"}],
        )

    def test_attach_mentions_to_message_ignores_non_text_and_missing_source(self):
        non_text_message = {"_raw_type": 3, "content": "[图片]"}
        _attach_mentions_to_message(
            non_text_message,
            "<msgsource><atuserlist>wxid_alice</atuserlist></msgsource>",
        )
        self.assertNotIn("mentions", non_text_message)

        text_message_without_source = {"_raw_type": 1, "content": "hello"}
        _attach_mentions_to_message(text_message_without_source, None)
        self.assertNotIn("mentions", text_message_without_source)

    def test_attach_mentions_to_message_adds_mentions_for_text_message(self):
        text_message = {"_raw_type": 1, "content": "@all hello"}

        _attach_mentions_to_message(
            text_message,
            "<msgsource><atuserlist>wxid_alice,wxid_bob,wxid_alice</atuserlist></msgsource>",
        )

        self.assertEqual(
            text_message.get("mentions"),
            [
                {"wxid": "wxid_alice", "text": "@all", "start": 0, "end": 4},
                {"wxid": "wxid_bob"},
            ],
        )

    def test_attach_mentions_to_message_preserves_existing_mentions(self):
        text_message = {"_raw_type": 1, "content": "hello", "mentions": [{"wxid": "wxid_existing"}]}

        _attach_mentions_to_message(
            text_message,
            "<msgsource><atuserlist>wxid_alice,wxid_bob</atuserlist></msgsource>",
        )

        self.assertEqual(text_message["mentions"], [{"wxid": "wxid_existing"}])

    def test_attach_mentions_to_message_ignores_plain_text_at_symbols(self):
        text_message = {"_raw_type": 1, "content": "@史迪仔\u2005 测试@的"}

        _attach_mentions_to_message(
            text_message,
            "<msgsource><atuserlist>wxid_stitch</atuserlist></msgsource>",
        )

        self.assertEqual(
            text_message["mentions"],
            [{"wxid": "wxid_stitch", "text": "@史迪仔", "start": 0, "end": 4}],
        )


if __name__ == "__main__":
    unittest.main()
