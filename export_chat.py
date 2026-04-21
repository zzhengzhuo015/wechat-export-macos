#!/usr/bin/env python3
"""
WeChat Chat History Exporter

Export chat history with a specific contact or group from decrypted WeChat databases.
Outputs TXT (human-readable), CSV (spreadsheet), and JSON (structured data).

Usage:
    python3 export_chat.py --name "联系人昵称或备注" --output ./output_dir
    python3 export_chat.py --name "Chris" --output ~/Downloads/chris
    python3 export_chat.py --name "工作群" --output ~/Downloads/work_group
    python3 export_chat.py --list                    # List all conversations
    python3 export_chat.py --list --top 30           # List top 30 by message count
"""

import argparse
import os
import sqlite3

from chat_export import (
    export_single_session as export_single_session_shared,
    find_contacts as find_contacts_shared,
    list_conversations_for_cli as list_conversations_shared,
    lookup_username_display as lookup_username_display_shared,
    resolve_contact_db_path,
)
from config import load_config

MY_WXID = None


def load_runtime_paths():
    cfg = load_config()
    decrypted_dir = cfg["decrypted_dir"]
    contact_db_path = resolve_contact_db_path(decrypted_dir)
    return decrypted_dir, contact_db_path


def detect_my_wxid(decrypted_dir=None, contact_db_path=None):
    if decrypted_dir is None or contact_db_path is None:
        decrypted_dir, contact_db_path = load_runtime_paths()
    session_db = os.path.join(decrypted_dir, "session", "session.db")
    if not os.path.exists(session_db):
        return None
    conn = None
    contact_conn = None
    try:
        conn = sqlite3.connect(session_db)
        conn.execute(
            "SELECT user_name FROM Name2Id WHERE user_name LIKE 'wxid_%' LIMIT 10"
        ).fetchall()
        contact_conn = sqlite3.connect(contact_db_path)
        self_entry = contact_conn.execute(
            "SELECT username FROM contact WHERE username LIKE 'wxid_%' AND local_type = 0 LIMIT 1"
        ).fetchone()
        if self_entry:
            return self_entry[0]
    except sqlite3.DatabaseError:
        return None
    finally:
        if conn is not None:
            conn.close()
        if contact_conn is not None:
            contact_conn.close()
    return None


def lookup_username_display(contact_db_path, username):
    return lookup_username_display_shared(contact_db_path, username)


def _resolve_owner_id(args, decrypted_dir, contact_db_path):
    global MY_WXID
    if args.my_wxid:
        MY_WXID = args.my_wxid
    elif not MY_WXID:
        MY_WXID = detect_my_wxid(decrypted_dir, contact_db_path)
    return MY_WXID or ""


def _display_name_from_contact_row(row):
    username, nick_name, remark, alias = row
    return remark or nick_name or alias or username


def main(argv=None):
    parser = argparse.ArgumentParser(description="微信聊天记录导出工具")
    parser.add_argument("--name", "-n", help="联系人昵称、备注或微信号（模糊搜索）")
    parser.add_argument("--username", "-u", help="联系人用户名（精确匹配，跳过搜索）")
    parser.add_argument("--output", "-o", default="./exported", help="导出目录")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有会话")
    parser.add_argument("--top", type=int, default=20, help="列出前N个会话（默认20）")
    parser.add_argument("--my-wxid", help="你自己的微信ID（可选，自动检测）")
    args = parser.parse_args(argv)

    if args.list:
        decrypted_dir, contact_db_path = load_runtime_paths()
        list_conversations_shared(
            decrypted_dir=decrypted_dir,
            contact_db_path=contact_db_path,
            top_n=args.top,
        )
        return 0

    if not args.name and not args.username:
        parser.print_help()
        return 0

    decrypted_dir, contact_db_path = load_runtime_paths()

    if args.username:
        display_name = lookup_username_display(contact_db_path, args.username)
        owner_id = _resolve_owner_id(args, decrypted_dir, contact_db_path)
        print(f"\n导出: {display_name} ({args.username})")
        export_single_session_shared(
            decrypted_dir=decrypted_dir,
            contact_db_path=contact_db_path,
            contact_username=args.username,
            contact_display_name=display_name,
            output_dir=args.output,
            owner_id=owner_id,
        )
        return 0

    results = find_contacts_shared(contact_db_path, args.name)
    if not results:
        print(f"未找到匹配 \"{args.name}\" 的联系人")
        return 0

    if len(results) == 1:
        username, nick_name, remark, alias = results[0]
        display_name = _display_name_from_contact_row(results[0])
        owner_id = _resolve_owner_id(args, decrypted_dir, contact_db_path)
        print(f"\n找到联系人: {display_name} ({username})")
        export_single_session_shared(
            decrypted_dir=decrypted_dir,
            contact_db_path=contact_db_path,
            contact_username=username,
            contact_display_name=display_name,
            output_dir=args.output,
            owner_id=owner_id,
        )
        return 0

    print(f"\n找到 {len(results)} 个匹配的联系人:")
    for index, row in enumerate(results, 1):
        username, nick_name, remark, alias = row
        display_name = _display_name_from_contact_row(row)
        print(
            f"  {index}. {display_name} (昵称: {nick_name}, 备注: {remark}, 微信号: {alias}, ID: {username})"
        )

    try:
        choice = input("\n请选择 [1-{}]: ".format(len(results))).strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            username, nick_name, remark, alias = results[int(choice) - 1]
            display_name = _display_name_from_contact_row(results[int(choice) - 1])
            owner_id = _resolve_owner_id(args, decrypted_dir, contact_db_path)
            print(f"\n导出: {display_name} ({username})")
            export_single_session_shared(
                decrypted_dir=decrypted_dir,
                contact_db_path=contact_db_path,
                contact_username=username,
                contact_display_name=display_name,
                output_dir=args.output,
                owner_id=owner_id,
            )
        else:
            print("已取消")
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
