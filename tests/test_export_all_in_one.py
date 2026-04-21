import unittest
from unittest.mock import patch
import subprocess
import sys
import os

import export_all_in_one


class CommandTests(unittest.TestCase):
    @patch("export_all_in_one.subprocess.run")
    @patch("export_all_in_one.os.path.exists", return_value=False)
    def test_ensure_key_scanner_builds_expected_command_with_module_relative_paths(
        self, mock_exists, mock_run
    ):
        export_all_in_one.ensure_key_scanner("./find_all_keys_macos")

        module_dir = os.path.dirname(os.path.abspath(export_all_in_one.__file__))
        expected_scanner = os.path.join(module_dir, "find_all_keys_macos")
        expected_source = os.path.join(module_dir, "find_all_keys_macos.c")
        mock_exists.assert_called_once_with(expected_scanner)
        mock_run.assert_called_once_with(
            [
                "cc",
                "-O2",
                "-o",
                expected_scanner,
                expected_source,
                "-framework",
                "Foundation",
            ],
            check=True,
        )

    @patch("export_all_in_one.subprocess.run")
    @patch("export_all_in_one.os.path.exists", return_value=True)
    def test_ensure_key_scanner_skips_compile_when_scanner_exists(
        self, mock_exists, mock_run
    ):
        export_all_in_one.ensure_key_scanner("./find_all_keys_macos")
        module_dir = os.path.dirname(os.path.abspath(export_all_in_one.__file__))
        expected_scanner = os.path.join(module_dir, "find_all_keys_macos")
        mock_exists.assert_called_once_with(expected_scanner)
        mock_run.assert_not_called()

    @patch("export_all_in_one.subprocess.run")
    def test_run_key_scan_uses_sudo_with_module_relative_scanner_path(self, mock_run):
        export_all_in_one.run_key_scan("./find_all_keys_macos")
        module_dir = os.path.dirname(os.path.abspath(export_all_in_one.__file__))
        expected_scanner = os.path.join(module_dir, "find_all_keys_macos")
        mock_run.assert_called_once_with(["sudo", expected_scanner], check=True)

    @patch("export_all_in_one.subprocess.run")
    def test_run_decrypt_uses_current_python_and_decrypt_script(self, mock_run):
        export_all_in_one.run_decrypt()
        expected_script = os.path.join(
            os.path.dirname(os.path.abspath(export_all_in_one.__file__)),
            "decrypt_db.py",
        )
        mock_run.assert_called_once_with([sys.executable, expected_script], check=True)


class MainTests(unittest.TestCase):
    @patch("export_all_in_one.print")
    @patch("export_all_in_one.export_all_sessions")
    @patch("export_all_in_one.detect_owner_id", return_value="wxid_owner")
    @patch("export_all_in_one.run_decrypt")
    @patch("export_all_in_one.run_key_scan")
    @patch("export_all_in_one.ensure_key_scanner")
    @patch(
        "export_all_in_one.load_config",
        return_value={"decrypted_dir": "/config/decrypted"},
    )
    def test_main_runs_full_pipeline_by_default(
        self,
        mock_load_config,
        mock_ensure_key_scanner,
        mock_run_key_scan,
        mock_run_decrypt,
        mock_detect_owner_id,
        mock_export_all_sessions,
        mock_print,
    ):
        mock_export_all_sessions.return_value = {"success_count": 3, "failed_count": 1}

        result = export_all_in_one.main(["--output", "/tmp/out"])

        self.assertEqual(result, 0)
        mock_load_config.assert_called_once_with()
        mock_ensure_key_scanner.assert_called_once_with()
        mock_run_key_scan.assert_called_once_with()
        mock_run_decrypt.assert_called_once_with()
        mock_detect_owner_id.assert_called_once_with()
        mock_export_all_sessions.assert_called_once_with(
            decrypted_dir="/config/decrypted",
            output_dir="/tmp/out",
            owner_id="wxid_owner",
        )
        mock_print.assert_called_once_with(
            "Export finished: success_count=3 failed_count=1 output=/tmp/out"
        )

    @patch("export_all_in_one.print")
    @patch("export_all_in_one.export_all_sessions")
    @patch("export_all_in_one.detect_owner_id", return_value="")
    @patch("export_all_in_one.run_decrypt")
    @patch("export_all_in_one.run_key_scan")
    @patch("export_all_in_one.ensure_key_scanner")
    @patch(
        "export_all_in_one.load_config",
        return_value={"decrypted_dir": "/config/decrypted"},
    )
    def test_main_skip_scan_and_decrypt_still_exports(
        self,
        mock_load_config,
        mock_ensure_key_scanner,
        mock_run_key_scan,
        mock_run_decrypt,
        mock_detect_owner_id,
        mock_export_all_sessions,
        mock_print,
    ):
        mock_export_all_sessions.return_value = {"success_count": 2, "failed_count": 0}

        result = export_all_in_one.main(
            [
                "--output",
                "/tmp/out",
                "--decrypted-dir",
                "/tmp/decrypted",
                "--skip-scan",
                "--skip-decrypt",
            ]
        )

        self.assertEqual(result, 0)
        mock_load_config.assert_not_called()
        mock_ensure_key_scanner.assert_not_called()
        mock_run_key_scan.assert_not_called()
        mock_run_decrypt.assert_not_called()
        mock_detect_owner_id.assert_called_once_with()
        mock_export_all_sessions.assert_called_once_with(
            decrypted_dir="/tmp/decrypted",
            output_dir="/tmp/out",
            owner_id="",
        )
        mock_print.assert_called_once_with(
            "Export finished: success_count=2 failed_count=0 output=/tmp/out"
        )

    @patch("export_all_in_one.print")
    @patch("export_all_in_one.export_all_sessions")
    @patch("export_all_in_one.detect_owner_id")
    @patch(
        "export_all_in_one.run_key_scan",
        side_effect=subprocess.CalledProcessError(1, ["sudo", "scanner"]),
    )
    @patch("export_all_in_one.ensure_key_scanner")
    @patch("export_all_in_one.load_config", return_value={"decrypted_dir": "/config/decrypted"})
    def test_main_returns_1_on_subprocess_failure(
        self,
        mock_load_config,
        mock_ensure_key_scanner,
        mock_run_key_scan,
        mock_detect_owner_id,
        mock_export_all_sessions,
        mock_print,
    ):
        result = export_all_in_one.main(["--output", "/tmp/out"])

        self.assertEqual(result, 1)
        mock_load_config.assert_called_once_with()
        mock_ensure_key_scanner.assert_called_once_with()
        mock_run_key_scan.assert_called_once_with()
        mock_detect_owner_id.assert_not_called()
        mock_export_all_sessions.assert_not_called()
        mock_print.assert_not_called()


if __name__ == "__main__":
    unittest.main()
