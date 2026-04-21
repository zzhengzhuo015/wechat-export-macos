import importlib
import sys
import unittest
from unittest.mock import patch


def _import_export_chat():
    sys.modules.pop("export_chat", None)
    return importlib.import_module("export_chat")


class ExportChatCLITests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("export_chat", None)

    def test_main_list_delegates_to_shared_list_helper(self):
        module = _import_export_chat()

        with patch.object(
            module,
            "load_runtime_paths",
            return_value=("/tmp/decrypted", "/tmp/decrypted/contact/contact.db"),
            create=True,
        ), patch.object(module, "list_conversations_shared", create=True) as mock_list:
            module.main(["--list", "--top", "5"])

        mock_list.assert_called_once_with(
            decrypted_dir="/tmp/decrypted",
            contact_db_path="/tmp/decrypted/contact/contact.db",
            top_n=5,
        )

    def test_main_username_delegates_to_shared_single_export_helper(self):
        module = _import_export_chat()

        with patch.object(
            module,
            "load_runtime_paths",
            return_value=("/tmp/decrypted", "/tmp/decrypted/contact/contact.db"),
            create=True,
        ), patch.object(module, "detect_my_wxid", return_value="wxid_owner"), patch.object(
            module,
            "lookup_username_display",
            return_value="好友",
            create=True,
        ), patch.object(module, "export_single_session_shared", create=True) as mock_export:
            module.main(["--username", "wxid_friend", "--output", "/tmp/out"])

        mock_export.assert_called_once_with(
            decrypted_dir="/tmp/decrypted",
            contact_db_path="/tmp/decrypted/contact/contact.db",
            contact_username="wxid_friend",
            contact_display_name="好友",
            output_dir="/tmp/out",
            owner_id="wxid_owner",
        )

    def test_main_name_single_match_uses_alias_fallback_for_export_display_name(self):
        module = _import_export_chat()

        with patch.object(
            module,
            "load_runtime_paths",
            return_value=("/tmp/decrypted", "/tmp/decrypted/contact/contact.db"),
            create=True,
        ), patch.object(module, "detect_my_wxid", return_value="wxid_owner"), patch.object(
            module,
            "find_contacts_shared",
            return_value=[("wxid_alias_only", "", "", "小明号")],
            create=True,
        ), patch.object(module, "export_single_session_shared", create=True) as mock_export:
            module.main(["--name", "小明", "--output", "/tmp/out"])

        mock_export.assert_called_once_with(
            decrypted_dir="/tmp/decrypted",
            contact_db_path="/tmp/decrypted/contact/contact.db",
            contact_username="wxid_alias_only",
            contact_display_name="小明号",
            output_dir="/tmp/out",
            owner_id="wxid_owner",
        )


if __name__ == "__main__":
    unittest.main()
