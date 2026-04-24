import io
import json
import sqlite3
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from chat_export import (
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
                WCDB_CT_message_content INTEGER
            )
            """
        )
        conn.executemany(
            f"""
            INSERT INTO {session_table_for_username(self.contact_username)}
            (local_id, server_id, local_type, create_time, real_sender_id, message_content, source, WCDB_CT_message_content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 111, 3, 1722858000, 0, "[图片]", "", 0),
                (2, 222, 34, 1722858001, 0, "[语音]", "", 0),
            ],
        )
        conn.commit()
        conn.close()

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


if __name__ == "__main__":
    unittest.main()
