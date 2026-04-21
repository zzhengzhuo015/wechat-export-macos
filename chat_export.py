import re
import glob
import hashlib
import json
import os
import sqlite3
import csv
from datetime import datetime, timezone, timedelta
from time import time


FORBIDDEN_FILENAME_CHARS = r'[<>:"/\\|?*\x00-\x1f]'
CST = timezone(timedelta(hours=8))
MSG_TYPES = {
    1: "文本",
    3: "图片",
    34: "语音",
    42: "名片",
    43: "视频",
    47: "表情",
    48: "位置",
    49: "链接/文件/小程序",
    50: "语音/视频通话",
    51: "系统消息",
    10000: "系统提示",
    10002: "撤回消息",
}
MEDIA_TYPES = {3, 34, 43, 47}


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


def list_export_message_databases(decrypted_dir):
    msg_dir = os.path.join(decrypted_dir, "message")
    dbs = []
    if os.path.isdir(msg_dir):
        for filename in sorted(os.listdir(msg_dir)):
            if filename.startswith("message_") and filename.endswith(".db") and "fts" not in filename:
                dbs.append(os.path.join(msg_dir, filename))
    return dbs


def resolve_contact_db_path(decrypted_dir):
    contact_db = os.path.join(os.path.dirname(decrypted_dir), "decrypted", "contact", "contact.db")
    if os.path.exists(contact_db):
        return contact_db
    return os.path.join(decrypted_dir, "contact", "contact.db")


def find_contacts(contact_db_path, query):
    conn = sqlite3.connect(contact_db_path)
    try:
        return conn.execute(
            """
            SELECT username, nick_name, remark, alias
            FROM contact
            WHERE nick_name LIKE ? OR remark LIKE ? OR alias LIKE ?
            """,
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
    finally:
        conn.close()


def lookup_username_display(contact_db_path, username):
    conn = sqlite3.connect(contact_db_path)
    try:
        row = conn.execute(
            """
            SELECT username, nick_name, remark, alias
            FROM contact
            WHERE username = ?
            LIMIT 1
            """,
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return username
    _, nick_name, remark, alias = row
    return remark or nick_name or alias or username


def list_conversations_for_cli(decrypted_dir, contact_db_path, top_n=20, printer=print):
    conversations = {}
    for db_path in list_export_message_databases(decrypted_dir):
        conn = sqlite3.connect(db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
            ).fetchall()
            for (table_name,) in tables:
                try:
                    row = conn.execute(
                        f"""
                        SELECT COUNT(*),
                               datetime(MIN(create_time), 'unixepoch', 'localtime'),
                               datetime(MAX(create_time), 'unixepoch', 'localtime')
                        FROM {table_name} WHERE create_time > 0
                        """
                    ).fetchone()
                    count, earliest, latest = row
                    if count == 0:
                        continue
                    if table_name not in conversations:
                        conversations[table_name] = {
                            "count": 0,
                            "earliest": earliest,
                            "latest": latest,
                        }
                    conversations[table_name]["count"] += count
                    if earliest and (
                        not conversations[table_name]["earliest"]
                        or earliest < conversations[table_name]["earliest"]
                    ):
                        conversations[table_name]["earliest"] = earliest
                    if latest and (
                        not conversations[table_name]["latest"]
                        or latest > conversations[table_name]["latest"]
                    ):
                        conversations[table_name]["latest"] = latest
                except sqlite3.DatabaseError:
                    continue
        finally:
            conn.close()

    contact_map = {}
    try:
        conn = sqlite3.connect(contact_db_path)
        rows = conn.execute("SELECT username, nick_name, remark, alias FROM contact").fetchall()
        for username, nick_name, remark, alias in rows:
            table = session_table_for_username(username)
            display = remark or nick_name or alias or username
            contact_map[table] = (username, display)
    except sqlite3.DatabaseError:
        pass
    finally:
        if "conn" in locals():
            conn.close()

    sorted_conversations = sorted(
        conversations.items(),
        key=lambda item: item[1]["count"],
        reverse=True,
    )

    printer(f"\n{'排名':<4} {'消息数':<8} {'时间范围':<45} {'显示名':<20} {'用户名'}")
    printer("-" * 120)
    for index, (table_name, info) in enumerate(sorted_conversations[:top_n], 1):
        if table_name in contact_map:
            username, display = contact_map[table_name]
        else:
            username = table_name
            display = "(?)"
        time_range = f"{info['earliest']} ~ {info['latest']}"
        printer(f"{index:<4} {info['count']:<8} {time_range:<45} {display:<20} {username}")

    printer(f"\n共 {len(conversations)} 个会话")
    return sorted_conversations


def export_single_session(
    decrypted_dir,
    contact_username,
    contact_display_name,
    output_dir,
    contact_db_path=None,
    owner_id="",
    printer=print,
):
    table_name = session_table_for_username(contact_username)
    os.makedirs(output_dir, exist_ok=True)
    all_messages = []

    for db_path in list_export_message_databases(decrypted_dir):
        conn = sqlite3.connect(db_path)
        db_name = os.path.basename(db_path)
        try:
            name2id = {}
            for rowid, user_name in conn.execute("SELECT rowid, user_name FROM Name2Id"):
                name2id[rowid] = user_name

            rows = conn.execute(
                f"""
                SELECT local_id, server_id, local_type, create_time,
                       real_sender_id, message_content, source,
                       WCDB_CT_message_content
                FROM {table_name}
                ORDER BY create_time ASC
                """
            ).fetchall()

            for row in rows:
                sender_wxid = name2id.get(row[4], "")
                if owner_id and sender_wxid == owner_id:
                    sender = "我"
                elif row[2] in (10000, 10002):
                    sender = "系统"
                else:
                    sender = contact_display_name

                content = row[5] or ""
                if isinstance(content, bytes):
                    content = "[压缩内容]"

                all_messages.append(
                    {
                        "time": datetime.fromtimestamp(row[3], tz=CST).strftime("%Y-%m-%d %H:%M:%S")
                        if row[3]
                        else "",
                        "timestamp": row[3],
                        "sender": sender,
                        "type": row[2],
                        "type_name": MSG_TYPES.get(row[2], f"未知({row[2]})"),
                        "content": content,
                        "server_id": row[1],
                        "db": db_name,
                    }
                )

            if rows:
                printer(f"  {db_name}: {len(rows)} 条消息")
        except sqlite3.DatabaseError:
            pass
        finally:
            conn.close()

    all_messages.sort(key=lambda item: item["timestamp"] or 0)
    if not all_messages:
        printer(f"未找到与 {contact_display_name} 的聊天记录")
        return 0

    txt_path = os.path.join(output_dir, "chat.txt")
    with open(txt_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(f"微信聊天记录: {contact_display_name} ({contact_username})\n")
        file_handle.write(f"总消息数: {len(all_messages)}\n")
        file_handle.write(f"时间范围: {all_messages[0]['time']} ~ {all_messages[-1]['time']}\n")
        file_handle.write("=" * 60 + "\n\n")
        for message in all_messages:
            content = message["content"]
            if message["type"] in MEDIA_TYPES:
                content = f"[{message['type_name']}]"
            elif message["type"] != 1 and not content:
                content = f"[{message['type_name']}]"
            file_handle.write(f"[{message['time']}] {message['sender']}: {content}\n")
    printer(f"  TXT: {txt_path}")

    csv_path = os.path.join(output_dir, "chat.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as file_handle:
        writer = csv.writer(file_handle)
        writer.writerow(["时间", "发送者", "类型", "内容"])
        for message in all_messages:
            content = message["content"]
            if message["type"] in MEDIA_TYPES:
                content = f"[{message['type_name']}]"
            elif message["type"] != 1 and not content:
                content = f"[{message['type_name']}]"
            writer.writerow([message["time"], message["sender"], message["type_name"], content])
    printer(f"  CSV: {csv_path}")

    json_path = os.path.join(output_dir, "chat.json")
    with open(json_path, "w", encoding="utf-8") as file_handle:
        json.dump(all_messages, file_handle, ensure_ascii=False, indent=2)
    printer(f"  JSON: {json_path}")
    printer(f"\n导出完成: {len(all_messages)} 条消息")
    return len(all_messages)


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
