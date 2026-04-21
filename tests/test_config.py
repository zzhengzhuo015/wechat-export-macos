import os
import tempfile
import types
import unittest
from unittest import mock

import config


class AutoDetectDbDirDarwinTests(unittest.TestCase):
    def test_auto_detect_db_dir_darwin_finds_container_db_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            container_root = os.path.join(
                temp_dir,
                "Library",
                "Containers",
                "com.tencent.xinWeChat",
                "Data",
                "Documents",
                "xwechat_files",
            )
            expected = os.path.join(container_root, "wxid_container", "db_storage")
            os.makedirs(expected)
            os.makedirs(os.path.join(expected, "message"))

            def fake_expanduser(path):
                if path == "~/Documents/xwechat_files":
                    return os.path.join(temp_dir, "Documents", "xwechat_files")
                if path == "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files":
                    return container_root
                return path

            with mock.patch("config.os.path.expanduser", side_effect=fake_expanduser):
                detected = config._auto_detect_db_dir_darwin()

            self.assertEqual(detected, expected)

    def test_auto_detect_db_dir_darwin_finds_documents_db_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            documents_root = os.path.join(temp_dir, "Documents", "xwechat_files")
            expected = os.path.join(documents_root, "wxid_123", "db_storage")
            os.makedirs(expected)
            os.makedirs(os.path.join(expected, "message"))

            def fake_expanduser(path):
                if path == "~/Documents/xwechat_files":
                    return documents_root
                return path

            with mock.patch("config.os.path.expanduser", side_effect=fake_expanduser):
                detected = config._auto_detect_db_dir_darwin()

            self.assertEqual(detected, expected)

    def test_auto_detect_db_dir_darwin_falls_back_to_sudo_user_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sudo_home = os.path.join(temp_dir, "real-user")
            documents_root = os.path.join(sudo_home, "Documents", "xwechat_files")
            expected = os.path.join(documents_root, "wxid_456", "db_storage")
            os.makedirs(expected)
            os.makedirs(os.path.join(expected, "message"))

            with mock.patch.dict("config.os.environ", {"SUDO_USER": "alice"}, clear=False):
                with mock.patch(
                    "config.os.path.expanduser",
                    side_effect=lambda path: "/root/Documents/xwechat_files"
                    if path == "~/Documents/xwechat_files"
                    else path,
                ):
                    with mock.patch(
                        "pwd.getpwnam",
                        return_value=types.SimpleNamespace(pw_dir=sudo_home),
                    ):
                        detected = config._auto_detect_db_dir_darwin()

            self.assertEqual(detected, expected)


if __name__ == "__main__":
    unittest.main()
