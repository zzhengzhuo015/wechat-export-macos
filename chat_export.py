import re
import glob
import hashlib
import json
import os
import sqlite3
from time import time


FORBIDDEN_FILENAME_CHARS = r'[<>:"/\\|?*\x00-\x1f]'


def sanitize_filename(name):
    cleaned = re.sub(FORBIDDEN_FILENAME_CHARS, "_", (name or "").strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned or "未命名会话"


def dedupe_filename(display_name, used_names):
    base = sanitize_filename(display_name)
    candidate = f"{base}.json"
    suffix = 2
    while candidate in used_names:
        candidate = f"{base}-{suffix}.json"
        suffix += 1
    used_names.add(candidate)
    return candidate


def build_chatlab_payload(
    session_name,
    session_id,
    session_type,
    owner_id,
    members,
    messages,
    exported_at=None,
):
    return {
        "chatlab": {
            "version": "0.0.2",
            "exportedAt": int(exported_at if exported_at is not None else time()),
            "generator": "wechat-export-macos",
        },
        "meta": {
            "name": session_name,
            "platform": "wechat",
            "type": session_type,
            "ownerId": owner_id or "",
            "groupId": session_id,
        },
        "members": members,
        "messages": messages,
    }


def session_table_for_username(username):
    return f"Msg_{hashlib.md5(username.encode('utf-8')).hexdigest()}"


def load_contacts(contact_db_path):
    contacts = {}
    conn = sqlite3.connect(contact_db_path)
    try:
        rows = conn.execute(
            "SELECT username, nick_name, remark, alias FROM contact"
        ).fetchall()
        for username, nick_name, remark, alias in rows:
            display = remark or nick_name or alias or username
            contacts[username] = {
                "username": username,
                "display": display,
            }
    finally:
        conn.close()
    return contacts


def list_message_databases(decrypted_dir):
    pattern = os.path.join(decrypted_dir, "message", "message_*.db")
    return sorted(glob.glob(pattern))


def table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def load_name2id(conn):
    mapping = {}
    try:
        rows = conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall()
        for rowid, user_name in rows:
            mapping[rowid] = user_name
    except sqlite3.DatabaseError:
        return mapping
    return mapping


def read_session_rows(conn, table_name):
    return conn.execute(
        f"""
        SELECT local_id, server_id, local_type, create_time, real_sender_id, message_content
        FROM {table_name}
        ORDER BY create_time ASC, local_id ASC
        """
    ).fetchall()


def list_message_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
    ).fetchall()
    return [row[0] for row in rows]


def normalize_message(row, name2id, contacts):
    local_id, server_id, local_type, create_time, real_sender_id, message_content = row
    if real_sender_id in name2id:
        sender_wxid = name2id[real_sender_id]
    elif real_sender_id is None:
        sender_wxid = ""
    else:
        sender_wxid = str(real_sender_id)
    sender_name = contacts.get(sender_wxid, {}).get("display", sender_wxid)
    if isinstance(message_content, bytes):
        content = "[二进制内容]"
    elif message_content is None or message_content == "":
        content = "" if local_type == 1 else "[非文本消息]"
    else:
        content = str(message_content)
    platform_message_id = server_id if server_id else local_id
    return {
        "sender": sender_wxid,
        "accountName": sender_name,
        "timestamp": create_time or 0,
        "type": 0 if local_type == 1 else 1,
        "content": content,
        "platformMessageId": str(platform_message_id) if platform_message_id is not None else "",
    }


def build_members(session_username, session_display_name, session_type, owner_id, contacts, messages):
    ordered_ids = []
    if session_type == "group":
        ordered_ids.append(session_username)
    else:
        ordered_ids.append(session_username)
        if owner_id:
            ordered_ids.append(owner_id)

    for message in messages:
        sender = message["sender"]
        if sender not in ordered_ids:
            ordered_ids.append(sender)

    members = []
    for wxid in ordered_ids:
        if wxid == session_username:
            account_name = session_display_name
        else:
            account_name = contacts.get(wxid, {}).get("display", wxid)
        members.append({
            "platformId": wxid,
            "accountName": account_name,
        })
    return members


def export_all_sessions(decrypted_dir, output_dir, owner_id=""):
    os.makedirs(output_dir, exist_ok=True)
    contact_db_path = os.path.join(decrypted_dir, "contact", "contact.db")
    contacts = load_contacts(contact_db_path)
    message_dbs = list_message_databases(decrypted_dir)
    used_names = set()
    summary = {
        "success_count": 0,
        "failed_count": 0,
        "files": [],
    }

    sessions = {}
    for username, contact_info in contacts.items():
        table_name = session_table_for_username(username)
        sessions[table_name] = {
            "session_id": username,
            "display": contact_info["display"],
        }

    for db_path in message_dbs:
        conn = sqlite3.connect(db_path)
        try:
            for table_name in list_message_tables(conn):
                if table_name not in sessions:
                    sessions[table_name] = {
                        "session_id": table_name,
                        "display": table_name,
                    }
        finally:
            conn.close()

    for table_name, session_info in sessions.items():
        session_id = session_info["session_id"]
        merged_messages = []
        for db_path in message_dbs:
            conn = sqlite3.connect(db_path)
            try:
                if not table_exists(conn, table_name):
                    continue
                name2id = load_name2id(conn)
                rows = read_session_rows(conn, table_name)
                for row in rows:
                    merged_messages.append(
                        normalize_message(row, name2id, contacts)
                    )
            finally:
                conn.close()

        if not merged_messages:
            continue

        merged_messages.sort(
            key=lambda item: (item.get("timestamp", 0), item.get("platformMessageId", ""))
        )
        display_name = session_info["display"]
        session_type = "group" if session_id.endswith("@chatroom") else "direct"
        members = build_members(
            session_id,
            display_name,
            session_type,
            owner_id,
            contacts,
            merged_messages,
        )
        payload = build_chatlab_payload(
            session_name=display_name,
            session_id=session_id,
            session_type=session_type,
            owner_id=owner_id or "",
            members=members,
            messages=merged_messages,
        )

        filename = dedupe_filename(display_name, used_names)
        output_path = os.path.join(output_dir, filename)
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            summary["success_count"] += 1
            summary["files"].append({"username": session_id, "file": filename})
        except OSError:
            summary["failed_count"] += 1

    return summary
