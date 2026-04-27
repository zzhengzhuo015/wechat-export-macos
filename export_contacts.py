#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sqlite3
from time import time

from chat_export import resolve_contact_db_path
from config import load_config
from export_chat import detect_my_wxid


STANDARD_FIELDS = (
    "username",
    "display_name",
    "nick_name",
    "remark",
    "alias",
    "contact_type",
    "is_chatroom",
)

SYSTEM_USERNAMES = {
    "weixin",
    "medianote",
    "fmessage",
    "floatbottle",
    "filehelper",
    "newsapp",
    "notifymessage",
}


def _serialize_contact_value(value):
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return value


def _serialize_contact_row(row):
    return {
        key: _serialize_contact_value(value)
        for key, value in row.items()
    }


def load_runtime_paths():
    cfg = load_config()
    decrypted_dir = cfg["decrypted_dir"]
    return decrypted_dir, resolve_contact_db_path(decrypted_dir)


def _load_contact_rows(contact_db_path):
    if not os.path.exists(contact_db_path):
        raise FileNotFoundError(f"未找到联系人数据库: {contact_db_path}")

    conn = sqlite3.connect(contact_db_path)
    try:
        columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(contact)").fetchall()
        ]
        if not columns:
            raise ValueError("contact 表不存在或没有可导出的列")

        rows = conn.execute("SELECT * FROM contact").fetchall()
    finally:
        conn.close()

    return columns, [dict(zip(columns, row)) for row in rows]


def _is_system_username(username):
    if not username:
        return False
    return username in SYSTEM_USERNAMES or username.startswith("gh_")


def _is_real_contact(row, owner_id):
    username = (row.get("username") or "").strip()
    if not username:
        return False
    if username.endswith("@chatroom"):
        return False
    if owner_id and username == owner_id:
        return False
    if _is_system_username(username):
        return False
    return True


def _normalize_contact(row):
    row = _serialize_contact_row(row)
    username = (row.get("username") or "").strip()
    nick_name = row.get("nick_name")
    remark = row.get("remark")
    alias = row.get("alias")

    normalized = dict(row)
    normalized.update(
        {
            "username": username,
            "display_name": remark or nick_name or alias or username,
            "nick_name": nick_name,
            "remark": remark,
            "alias": alias,
            "contact_type": row.get("local_type"),
            "is_chatroom": username.endswith("@chatroom"),
        }
    )
    return normalized


def _csv_fieldnames(raw_columns):
    extras = [column for column in raw_columns if column not in STANDARD_FIELDS]
    return list(STANDARD_FIELDS) + extras


def _write_contacts_json(output_dir, payload):
    output_path = os.path.join(output_dir, "contacts.json")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _write_contacts_csv(output_dir, contacts, raw_columns):
    output_path = os.path.join(output_dir, "contacts.csv")
    fieldnames = _csv_fieldnames(raw_columns)
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for contact in contacts:
            writer.writerow(
                {
                    field: ("" if contact.get(field) is None else contact.get(field))
                    for field in fieldnames
                }
            )


def export_contacts(contact_db_path, output_dir, owner_id="", exported_at=None):
    raw_columns, raw_rows = _load_contact_rows(contact_db_path)
    contacts = [
        _normalize_contact(row)
        for row in raw_rows
        if _is_real_contact(row, owner_id)
    ]
    contacts.sort(key=lambda item: item.get("username") or "")

    os.makedirs(output_dir, exist_ok=True)
    payload = {
        "meta": {
            "version": "0.0.1",
            "exportedAt": int(exported_at if exported_at is not None else time()),
            "generator": "wechat-export-macos",
            "source": contact_db_path,
            "filterMode": "real_contacts",
            "contactCount": len(contacts),
        },
        "contacts": contacts,
    }

    _write_contacts_json(output_dir, payload)
    _write_contacts_csv(output_dir, contacts, raw_columns)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="微信通讯录导出工具")
    parser.add_argument("--output", "-o", default="./exported_contacts", help="导出目录")
    args = parser.parse_args(argv)

    try:
        decrypted_dir, contact_db_path = load_runtime_paths()
        owner_id = detect_my_wxid(decrypted_dir, contact_db_path) or ""
        export_contacts(
            contact_db_path=contact_db_path,
            output_dir=args.output,
            owner_id=owner_id,
        )
        return 0
    except (FileNotFoundError, OSError, sqlite3.DatabaseError, ValueError, TypeError) as exc:
        print(f"[!] 导出联系人失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
