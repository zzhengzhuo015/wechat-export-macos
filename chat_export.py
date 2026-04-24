import re
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from time import time

from config import load_config
from media_conversion import try_convert_image_file, try_convert_silk_bytes_to_wav


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
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".dat"}
UNSUPPORTED_IMAGE_EXTENSIONS = {".heic", ".heif"}
AUDIO_EXTENSIONS = {".silk", ".amr", ".aac", ".m4a", ".wav", ".mp3", ".opus"}
IMAGE_DIR_HINTS = ("img", "image", "thumb")
AUDIO_DIR_HINTS = ("audio", "voice", "record", "sound")


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


def session_hash_for_username(username):
    if re.fullmatch(r"Msg_[0-9a-f]{32}", username or ""):
        return username[4:]
    return session_table_for_username(username)[4:]


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


def _resolve_data_root(decrypted_dir):
    decrypted_path = Path(decrypted_dir)
    fallback_root = decrypted_path.parent if decrypted_path.name == "decrypted" else decrypted_path.parent
    if (fallback_root / "msg" / "attach").exists() or (fallback_root / "cache").exists():
        return fallback_root

    try:
        cfg = load_config()
    except (OSError, KeyError, SystemExit, ValueError):
        cfg = {}

    configured_base = cfg.get("wechat_base_dir")
    if configured_base:
        configured_path = Path(configured_base)
        if (configured_path / "msg" / "attach").exists() or (configured_path / "cache").exists():
            return configured_path

    return fallback_root


def _load_message_resource_tokens(decrypted_dir):
    db_path = Path(decrypted_dir) / "message" / "message_resource.db"
    tokens_by_message = {}
    if not db_path.exists():
        return tokens_by_message

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT message_local_id, message_create_time, packed_info
            FROM MessageResourceInfo
            """
        ).fetchall()
    except sqlite3.DatabaseError:
        conn.close()
        return tokens_by_message
    finally:
        conn.close()

    for local_id, create_time, packed_info in rows:
        tokens = _extract_hex_tokens(packed_info)
        if not tokens:
            continue
        key = (local_id or 0, create_time or 0)
        tokens_by_message.setdefault(key, set()).update(tokens)
    return tokens_by_message


def _load_voice_blobs(decrypted_dir):
    db_path = Path(decrypted_dir) / "message" / "media_0.db"
    voice_blobs = {}
    if not db_path.exists():
        return voice_blobs

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT n.user_name, v.create_time, v.local_id, v.svr_id, v.voice_data
            FROM VoiceInfo v
            JOIN Name2Id n ON n.rowid = v.chat_name_id
            WHERE length(v.voice_data) > 0
            """
        ).fetchall()
    except sqlite3.DatabaseError:
        conn.close()
        return voice_blobs
    finally:
        conn.close()

    for username, create_time, local_id, svr_id, voice_data in rows:
        voice_blobs[(username, local_id or 0, create_time or 0, str(svr_id or ""))] = voice_data
    return voice_blobs


def build_media_export_context(decrypted_dir):
    data_root = _resolve_data_root(decrypted_dir)
    return {
        "attach_root": data_root / "msg" / "attach",
        "cache_root": data_root / "cache",
        "resource_tokens": _load_message_resource_tokens(decrypted_dir),
        "voice_blobs": _load_voice_blobs(decrypted_dir),
        "session_files": {},
        "exported_paths": {},
    }


def _extract_hex_tokens(raw_value):
    if raw_value is None:
        return set()
    if isinstance(raw_value, bytes):
        text = raw_value.decode("latin1", errors="ignore")
    else:
        text = str(raw_value)
    return set(re.findall(r"[0-9a-f]{32}", text.lower()))


def _normalized_local_type(local_type):
    if local_type is None:
        return 0
    if local_type > 0xFFFFFFFF:
        return local_type & 0xFFFFFFFF
    return local_type


def _media_kind_from_type(local_type):
    normalized = _normalized_local_type(local_type)
    if normalized == 3 or local_type == 3:
        return "images"
    if normalized == 34 or local_type == 34:
        return "audio"
    return None


def _infer_media_kind_from_path(file_path):
    suffix = file_path.suffix.lower()
    if suffix in UNSUPPORTED_IMAGE_EXTENSIONS:
        return None
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in IMAGE_EXTENSIONS:
        return "images"

    path_parts = [part.lower() for part in file_path.parts[:-1]]
    part_tokens = {token for part in path_parts for token in re.split(r"[_\-\s.]+", part) if token}
    if any(hint in part_tokens for hint in AUDIO_DIR_HINTS):
        return "audio"
    if any(hint in part_tokens for hint in IMAGE_DIR_HINTS):
        return "images"

    suffix = file_path.suffix.lower()
    return None


def _list_session_media_files(media_context, session_hash):
    cached = media_context["session_files"].get(session_hash)
    if cached is not None:
        return cached

    files = []
    attach_dir = media_context["attach_root"] / session_hash
    if attach_dir.exists():
        for path in attach_dir.rglob("*"):
            if path.is_file():
                files.append(path)

    cache_root = media_context["cache_root"]
    if cache_root.exists():
        for path in cache_root.glob(f"*/Message/{session_hash}/**/*"):
            if path.is_file():
                files.append(path)

    media_context["session_files"][session_hash] = files
    return files


def _candidate_match_score(file_path, expected_kind, local_id, resource_tokens):
    if file_path.suffix.lower() in UNSUPPORTED_IMAGE_EXTENSIONS:
        return None

    name = file_path.name.lower()
    path_parts = [part.lower() for part in file_path.parts]
    inferred_kind = _infer_media_kind_from_path(file_path)
    if expected_kind and inferred_kind and inferred_kind != expected_kind:
        return None

    token_match = any(token in name for token in resource_tokens)
    prefix_match = name.startswith(f"{local_id}_")
    if not prefix_match and not token_match:
        return None

    score = 0
    if prefix_match:
        score += 100
    if token_match:
        score += 80
    if inferred_kind == expected_kind:
        score += 40
    if expected_kind == "images":
        if "img" in path_parts or "image" in path_parts:
            score += 20
        if "_t.dat" in name or "_thumb." in name or "thumb" in path_parts:
            score -= 10
    if expected_kind == "audio":
        if any(hint in path_parts for hint in AUDIO_DIR_HINTS):
            score += 20
    return score


def _find_media_source(media_context, session_username, local_id, create_time, local_type):
    session_hash = session_hash_for_username(session_username)
    expected_kind = _media_kind_from_type(local_type)
    resource_tokens = media_context["resource_tokens"].get((local_id or 0, create_time or 0), set())
    session_files = _list_session_media_files(media_context, session_hash)

    best_match = None
    best_score = None
    for file_path in session_files:
        score = _candidate_match_score(file_path, expected_kind, local_id, resource_tokens)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_match = file_path
            best_score = score

    if best_match is None:
        return None, expected_kind
    return best_match, expected_kind or _infer_media_kind_from_path(best_match)


def _build_export_basename(local_id, create_time, source_path):
    basename = source_path.name
    prefix = f"{local_id}_{create_time}"
    if basename.startswith(f"{local_id}_"):
        return basename
    return f"{prefix}_{basename}"


def _write_normalized_image(output_dir, media_kind, session_hash, local_id, create_time, source_path):
    converted = try_convert_image_file(source_path)
    if converted is None:
        return ""

    file_name, payload = converted
    relative_path = Path(media_kind) / session_hash / f"{local_id}_{create_time}{Path(file_name).suffix}"
    destination = Path(output_dir) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return relative_path.as_posix()


def _write_raw_image_file(output_dir, session_hash, local_id, create_time, source_path):
    suffix = source_path.suffix or ".bin"
    relative_path = Path("images") / session_hash / f"{local_id}_{create_time}{suffix}"
    destination = Path(output_dir) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source_path.read_bytes())
    return relative_path.as_posix()


def _write_wav_bytes(output_dir, session_hash, local_id, create_time, silk_bytes):
    wav_bytes = try_convert_silk_bytes_to_wav(silk_bytes, sample_rate=24000)
    if wav_bytes is None:
        return ""

    relative_path = Path("audio") / session_hash / f"{local_id}_{create_time}.wav"
    destination = Path(output_dir) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(wav_bytes)
    return relative_path.as_posix()


def _write_raw_audio_file(output_dir, session_hash, local_id, create_time, audio_bytes, suffix=".silk"):
    relative_path = Path("audio") / session_hash / f"{local_id}_{create_time}{suffix}"
    destination = Path(output_dir) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(audio_bytes)
    return relative_path.as_posix()


def _write_existing_wav_file(output_dir, session_hash, local_id, create_time, source_path):
    relative_path = Path("audio") / session_hash / f"{local_id}_{create_time}.wav"
    destination = Path(output_dir) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source_path.read_bytes())
    return relative_path.as_posix()


def _lookup_voice_blob(media_context, session_username, local_id, create_time, server_id=""):
    voice_blob = media_context["voice_blobs"].get(
        (session_username, local_id or 0, create_time or 0, str(server_id or ""))
    )
    if not voice_blob:
        voice_blob = media_context["voice_blobs"].get(
            (session_username, local_id or 0, create_time or 0, "")
        )
    return voice_blob


def export_media_file(
    media_context,
    session_username,
    output_dir,
    local_id,
    create_time,
    local_type,
    server_id="",
):
    source_path, media_kind = _find_media_source(
        media_context=media_context,
        session_username=session_username,
        local_id=local_id,
        create_time=create_time,
        local_type=local_type,
    )
    if source_path is None or not media_kind:
        if media_kind != "audio":
            return ""

        voice_blob = _lookup_voice_blob(
            media_context, session_username, local_id, create_time, server_id
        )
        if not voice_blob:
            return ""

        session_hash = session_hash_for_username(session_username)
        file_path = _write_wav_bytes(output_dir, session_hash, local_id, create_time, voice_blob)
        if file_path:
            return file_path
        return _write_raw_audio_file(
            output_dir=output_dir,
            session_hash=session_hash,
            local_id=local_id,
            create_time=create_time,
            audio_bytes=voice_blob,
            suffix=".silk",
        )

    session_hash = session_hash_for_username(session_username)
    if media_kind == "images":
        file_path = _write_normalized_image(
            output_dir=output_dir,
            media_kind=media_kind,
            session_hash=session_hash,
            local_id=local_id,
            create_time=create_time,
            source_path=source_path,
        )
        if file_path:
            return file_path
        return _write_raw_image_file(
            output_dir=output_dir,
            session_hash=session_hash,
            local_id=local_id,
            create_time=create_time,
            source_path=source_path,
        )

    if media_kind == "audio":
        if source_path.suffix.lower() == ".wav":
            return _write_existing_wav_file(
                output_dir=output_dir,
                session_hash=session_hash,
                local_id=local_id,
                create_time=create_time,
                source_path=source_path,
            )

        file_path = _write_wav_bytes(output_dir, session_hash, local_id, create_time, source_path.read_bytes())
        if file_path:
            return file_path

        voice_blob = _lookup_voice_blob(
            media_context, session_username, local_id, create_time, server_id
        )
        if voice_blob:
            file_path = _write_wav_bytes(output_dir, session_hash, local_id, create_time, voice_blob)
            if file_path:
                return file_path

        return _write_raw_audio_file(
            output_dir=output_dir,
            session_hash=session_hash,
            local_id=local_id,
            create_time=create_time,
            audio_bytes=source_path.read_bytes(),
            suffix=source_path.suffix.lower() or ".silk",
        )

    basename = _build_export_basename(local_id, create_time, source_path)
    relative_path = Path(media_kind) / session_hash / basename
    destination = Path(output_dir) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)

    cache_key = str(relative_path)
    if cache_key not in media_context["exported_paths"]:
        shutil.copy2(source_path, destination)
        media_context["exported_paths"][cache_key] = str(source_path)
    return relative_path.as_posix()


def _strip_internal_message_fields(message):
    return {
        key: value
        for key, value in message.items()
        if not key.startswith("_")
    }


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
    media_context = build_media_export_context(decrypted_dir)

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
                        "_local_id": row[0],
                        "_raw_type": row[2],
                        "_server_id": str(row[1] or ""),
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

    for message in all_messages:
        if message["_raw_type"] != 1:
            message["file_path"] = export_media_file(
                media_context=media_context,
                session_username=contact_username,
                output_dir=output_dir,
                local_id=message["_local_id"],
                create_time=message["timestamp"] or 0,
                local_type=message["_raw_type"],
                server_id=message["_server_id"],
            )

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
        json.dump(
            [_strip_internal_message_fields(message) for message in all_messages],
            file_handle,
            ensure_ascii=False,
            indent=2,
        )
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
        "_local_id": local_id or 0,
        "_raw_type": local_type or 0,
        "_server_id": str(server_id or ""),
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
    media_context = build_media_export_context(decrypted_dir)

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
        for message in merged_messages:
            if message.get("_raw_type", 0) != 1:
                message["file_path"] = export_media_file(
                    media_context=media_context,
                    session_username=session_id,
                    output_dir=output_dir,
                    local_id=message.get("_local_id", 0),
                    create_time=message.get("timestamp", 0),
                    local_type=message.get("_raw_type", 0),
                    server_id=message.get("_server_id", ""),
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
            messages=[_strip_internal_message_fields(message) for message in merged_messages],
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
