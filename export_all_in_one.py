import argparse
import os
import subprocess
import sys

from chat_export import export_all_sessions
from config import load_config


def _module_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _module_path(path):
    if os.path.isabs(path):
        return path
    base = _module_dir()
    cleaned = path[2:] if path.startswith("./") else path
    return os.path.join(base, cleaned)


def ensure_key_scanner(scanner_path="./find_all_keys_macos"):
    scanner_path = _module_path(scanner_path)
    source_path = _module_path("find_all_keys_macos.c")
    module_dir = _module_dir()
    if os.path.exists(scanner_path):
        return
    subprocess.run(
        [
            "cc",
            "-O2",
            "-o",
            scanner_path,
            source_path,
            "-framework",
            "Foundation",
        ],
        check=True,
        cwd=module_dir,
    )


def run_key_scan(scanner_path="./find_all_keys_macos"):
    scanner_path = _module_path(scanner_path)
    subprocess.run(["sudo", scanner_path], check=True, cwd=_module_dir())


def run_decrypt():
    module_dir = _module_dir()
    decrypt_script = os.path.join(module_dir, "decrypt_db.py")
    subprocess.run([sys.executable, decrypt_script], check=True, cwd=module_dir)


def detect_owner_id():
    try:
        from export_chat import detect_my_wxid
    except (Exception, SystemExit):
        return ""
    try:
        return detect_my_wxid() or ""
    except (Exception, SystemExit):
        return ""


def can_reuse_existing_keys(scan_error, keys_file):
    if not keys_file or not os.path.exists(keys_file):
        return False
    stderr = getattr(scan_error, "stderr", "") or ""
    return "WeChat not running or invalid PID" in stderr


def main(argv=None):
    parser = argparse.ArgumentParser(description="One-click WeChat export")
    parser.add_argument("--output", required=True)
    parser.add_argument("--decrypted-dir")
    parser.add_argument("--keep-decrypted", action="store_true")
    parser.add_argument("--skip-scan", action="store_true")
    parser.add_argument("--skip-decrypt", action="store_true")
    args = parser.parse_args(argv)

    if args.decrypted_dir and args.skip_scan and args.skip_decrypt:
        decrypted_dir = args.decrypted_dir
    else:
        config = load_config()
        configured_decrypted_dir = config["decrypted_dir"]
        keys_file = config.get("keys_file")
        if (
            not args.skip_decrypt
            and args.decrypted_dir
            and os.path.abspath(args.decrypted_dir) != os.path.abspath(configured_decrypted_dir)
        ):
            print(
                "Error: --decrypted-dir "
                f"({args.decrypted_dir}) does not match config decrypted_dir "
                f"({configured_decrypted_dir}) while decryption is enabled.",
                file=sys.stderr,
            )
            return 2
        decrypted_dir = args.decrypted_dir or configured_decrypted_dir
    if args.decrypted_dir and args.skip_scan and args.skip_decrypt:
        keys_file = None

    try:
        if not args.skip_scan:
            ensure_key_scanner()
            try:
                run_key_scan()
            except subprocess.CalledProcessError as exc:
                if can_reuse_existing_keys(exc, keys_file):
                    print(
                        "Key scan failed because WeChat PID was unavailable; "
                        f"reusing existing key file: {keys_file}"
                    )
                else:
                    raise
        if not args.skip_decrypt:
            run_decrypt()
    except subprocess.CalledProcessError:
        return 1

    owner_id = detect_owner_id()
    summary = export_all_sessions(
        decrypted_dir=decrypted_dir,
        output_dir=args.output,
        owner_id=owner_id,
    )
    print(
        "Export finished: success_count="
        f"{summary.get('success_count', 0)} "
        f"failed_count={summary.get('failed_count', 0)} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
