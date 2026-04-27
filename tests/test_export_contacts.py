import csv
import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _import_export_contacts():
    sys.modules.pop("export_contacts", None)
    return importlib.import_module("export_contacts")


class ExportContactsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.decrypted_dir = self.base / "decrypted"
        (self.decrypted_dir / "contact").mkdir(parents=True, exist_ok=True)
        self.contact_db_path = self.decrypted_dir / "contact" / "contact.db"
        self.output_dir = self.base / "output"

        conn = sqlite3.connect(self.contact_db_path)
        conn.execute(
            """
            CREATE TABLE contact (
                username TEXT,
                nick_name TEXT,
                remark TEXT,
                alias TEXT,
                local_type INTEGER,
                big_head_img_url TEXT,
                description TEXT,
                extra_buffer BLOB
            )
            """
        )
        conn.executemany(
            "INSERT INTO contact VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("wxid_friend", "张三", "老张", "", 1, "https://img/friend", "好友签名", b"\x01\x02"),
                ("wxid_alias", "", "", "小明号", 1, "https://img/alias", "", b"\xff\x00"),
                ("20505429106@chatroom", "测试群", "", "", 2, "", "", b""),
                ("wxid_owner", "我", "", "", 1, "", "", b""),
                ("filehelper", "文件传输助手", "", "", 1, "", "", b""),
                ("", "空用户", "", "", 1, "", "", b""),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()
        sys.modules.pop("export_contacts", None)

    def test_main_exports_json_and_csv_for_real_contacts_only(self):
        module = _import_export_contacts()

        with patch.object(
            module,
            "load_runtime_paths",
            return_value=(str(self.decrypted_dir), str(self.contact_db_path)),
            create=True,
        ), patch.object(module, "detect_my_wxid", return_value="wxid_owner", create=True):
            result = module.main(["--output", str(self.output_dir)])

        self.assertEqual(result, 0)

        payload = json.loads((self.output_dir / "contacts.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["meta"]["filterMode"], "real_contacts")
        self.assertEqual(payload["meta"]["contactCount"], 2)
        self.assertEqual(
            [item["username"] for item in payload["contacts"]],
            ["wxid_alias", "wxid_friend"],
        )
        self.assertEqual(payload["contacts"][0]["display_name"], "小明号")
        self.assertEqual(payload["contacts"][1]["display_name"], "老张")
        self.assertEqual(payload["contacts"][1]["big_head_img_url"], "https://img/friend")
        self.assertEqual(payload["contacts"][1]["description"], "好友签名")
        self.assertEqual(payload["contacts"][1]["extra_buffer"], "0102")
        self.assertFalse(payload["contacts"][1]["is_chatroom"])
        self.assertEqual(payload["contacts"][1]["contact_type"], 1)

        with open(self.output_dir / "contacts.csv", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual([row["username"] for row in rows], ["wxid_alias", "wxid_friend"])
        self.assertEqual(rows[0]["display_name"], "小明号")
        self.assertEqual(rows[1]["remark"], "老张")
        self.assertEqual(rows[1]["big_head_img_url"], "https://img/friend")
        self.assertEqual(rows[1]["extra_buffer"], "0102")
        self.assertIn("description", rows[1])

    def test_export_contacts_payload_keeps_dynamic_columns_and_source_path(self):
        module = _import_export_contacts()

        payload = module.export_contacts(
            contact_db_path=str(self.contact_db_path),
            output_dir=str(self.output_dir),
            owner_id="wxid_owner",
            exported_at=1777248000,
        )

        self.assertEqual(payload["meta"]["exportedAt"], 1777248000)
        self.assertEqual(payload["meta"]["source"], str(self.contact_db_path))
        self.assertEqual(payload["contacts"][0]["alias"], "小明号")
        self.assertIn("big_head_img_url", payload["contacts"][0])
        self.assertIn("description", payload["contacts"][0])
        self.assertEqual(payload["contacts"][0]["extra_buffer"], "ff00")

    def test_main_returns_non_zero_when_contact_db_missing(self):
        module = _import_export_contacts()
        missing_db = self.base / "missing" / "contact.db"

        with patch.object(
            module,
            "load_runtime_paths",
            return_value=(str(self.decrypted_dir), str(missing_db)),
            create=True,
        ), patch.object(module, "print", create=True) as mock_print:
            result = module.main(["--output", str(self.output_dir)])

        self.assertEqual(result, 1)
        mock_print.assert_called_once()


if __name__ == "__main__":
    unittest.main()
