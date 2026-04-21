import re
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
