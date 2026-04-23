# Media Export Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export image and audio attachments into dedicated output directories and store their relative file paths in both single-session and all-session JSON exports.

**Architecture:** Add a small media-resolution layer inside `chat_export.py` that classifies media messages, resolves source files from the WeChat data tree, copies them into `images/` or `audio/`, and annotates exported message objects with `file_path`. Keep TXT/CSV behavior readable, keep JSON stable for non-media messages, and degrade gracefully when a media file cannot be found.

**Tech Stack:** Python 3 stdlib (`os`, `pathlib`, `shutil`, `sqlite3`, `json`, `tempfile`, `unittest`)

---

### Task 1: Cover single-session media export behavior

**Files:**
- Modify: `tests/test_chat_export.py`
- Test: `tests/test_chat_export.py`

- [ ] **Step 1: Write the failing test**

```python
def test_export_single_session_copies_image_and_audio_and_writes_relative_paths(self):
    ...
    self.assertEqual(messages[0]["file_path"], "images/...")
    self.assertEqual(messages[1]["file_path"], "audio/...")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_chat_export`
Expected: FAIL because exported messages do not yet include copied media files or `file_path`

- [ ] **Step 3: Write minimal implementation**

```python
def export_single_session(...):
    ...
    media_context = build_media_export_context(...)
    ...
    file_path = export_media_file(...)
    message["file_path"] = file_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_chat_export`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_chat_export.py chat_export.py
git commit -m "feat: export media paths for single-session json"
```

### Task 2: Cover all-session media export behavior

**Files:**
- Modify: `tests/test_export_all.py`
- Test: `tests/test_export_all.py`

- [ ] **Step 1: Write the failing test**

```python
def test_export_all_sessions_copies_media_and_annotates_json_messages(self):
    ...
    self.assertEqual(payload["messages"][0]["file_path"], "images/...")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_export_all`
Expected: FAIL because all-session export does not yet copy media or write `file_path`

- [ ] **Step 3: Write minimal implementation**

```python
def normalize_message(...):
    return {
        ...,
        "sourceLocalType": normalized_local_type,
    }
```

```python
def export_all_sessions(...):
    ...
    message = normalize_message(...)
    message["file_path"] = export_media_file(...)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_export_all`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_export_all.py chat_export.py
git commit -m "feat: export media paths for all-session json"
```

### Task 3: Document the new export layout

**Files:**
- Modify: `README.md`
- Test: `README.md`

- [ ] **Step 1: Write the failing docs expectation**

```text
README should no longer say media export is unsupported and should explain `images/` / `audio/` output plus JSON `file_path`.
```

- [ ] **Step 2: Check current docs**

Run: `rg -n "图片/视频/语音|audio|images|file_path" README.md`
Expected: existing text still says media export is unsupported

- [ ] **Step 3: Update documentation**

```markdown
- `images/` stores exported image files
- `audio/` stores exported audio files
- JSON uses relative `file_path` values for media messages
```

- [ ] **Step 4: Run verification**

Run: `python3 -m unittest tests.test_chat_export tests.test_export_all tests.test_export_chat_cli`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_chat_export.py tests/test_export_all.py chat_export.py
git commit -m "docs: describe exported media asset paths"
```
