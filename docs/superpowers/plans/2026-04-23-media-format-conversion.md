# Media Format Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert exported voice messages to WAV and normalize exported images to JPG, PNG, or GIF based on decoded image content.

**Architecture:** Keep media discovery inside [`chat_export.py`](/Users/zz/.codex/worktrees/3a77/wechat-export-macos/chat_export.py), but move decode/encode work into a focused helper module so image normalization and Silk-to-WAV conversion are testable in isolation. Integrate the helper back into both single-session and all-session exporters, preserving the existing `file_path` contract and graceful failure behavior.

**Tech Stack:** Python 3, `unittest`, `sqlite3`, `wave`, `io`, `Pillow`, `silk-python`

---

### Task 1: Add explicit media conversion dependencies and helper tests

**Files:**
- Create: `requirements.txt`
- Create: `tests/test_media_conversion.py`
- Modify: `tests/test_chat_export.py`
- Modify: `tests/test_export_all.py`

- [ ] **Step 1: Write the failing dependency manifest**

```text
pycryptodome
Pillow
silk-python
```

- [ ] **Step 2: Write the failing helper tests**

```python
import io
import wave
import unittest

from media_conversion import convert_image_bytes, convert_silk_bytes_to_wav


class MediaConversionTests(unittest.TestCase):
    def test_convert_static_rgb_image_to_jpg(self):
        png_bytes = ...
        output_name, output_bytes = convert_image_bytes("photo.dat", png_bytes)
        self.assertEqual(output_name, "photo.jpg")
        self.assertTrue(output_bytes.startswith(b"\xff\xd8\xff"))

    def test_convert_transparent_image_to_png(self):
        png_bytes = ...
        output_name, output_bytes = convert_image_bytes("sticker.dat", png_bytes)
        self.assertEqual(output_name, "sticker.png")
        self.assertTrue(output_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_convert_animated_image_to_gif(self):
        gif_bytes = ...
        output_name, output_bytes = convert_image_bytes("animated.dat", gif_bytes)
        self.assertEqual(output_name, "animated.gif")
        self.assertTrue(output_bytes.startswith(b"GIF8"))

    def test_convert_silk_bytes_to_wav(self):
        silk_bytes = b"...fixture..."
        wav_bytes = convert_silk_bytes_to_wav(silk_bytes, sample_rate=24000)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            self.assertEqual(wav_file.getframerate(), 24000)
            self.assertGreater(wav_file.getnframes(), 0)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_media_conversion tests.test_chat_export tests.test_export_all`
Expected: FAIL because `media_conversion.py` does not exist and existing export tests still expect `.silk` / raw image copy behavior

- [ ] **Step 4: Update export integration tests to expect normalized formats**

```python
self.assertEqual(payload[0]["file_path"], f"images/{self.session_hash}/1_1722858000.jpg")
self.assertEqual(payload[1]["file_path"], f"audio/{self.session_hash}/2_1722858001.wav")
```

```python
self.assertEqual(
    [message["file_path"] for message in media_messages],
    [
        f"images/{session_hash}/3_1722858100.jpg",
        f"audio/{session_hash}/4_1722858101.wav",
    ],
)
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/test_media_conversion.py tests/test_chat_export.py tests/test_export_all.py
git commit -m "test: cover normalized media export formats"
```

### Task 2: Implement isolated image and voice conversion helpers

**Files:**
- Create: `media_conversion.py`
- Test: `tests/test_media_conversion.py`

- [ ] **Step 1: Write the minimal helper module**

```python
import io
import wave
from PIL import Image, ImageSequence
import pysilk


def convert_image_bytes(file_name, raw_bytes):
    with Image.open(io.BytesIO(raw_bytes)) as image:
        if getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1:
            frames = [frame.convert("RGBA") for frame in ImageSequence.Iterator(image)]
            output = io.BytesIO()
            frames[0].save(
                output,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                loop=image.info.get("loop", 0),
                duration=image.info.get("duration"),
            )
            return _replace_suffix(file_name, ".gif"), output.getvalue()

        frame = image.convert("RGBA")
        if frame.has_transparency_data:
            output = io.BytesIO()
            frame.save(output, format="PNG")
            return _replace_suffix(file_name, ".png"), output.getvalue()

        output = io.BytesIO()
        frame.convert("RGB").save(output, format="JPEG", quality=95)
        return _replace_suffix(file_name, ".jpg"), output.getvalue()


def convert_silk_bytes_to_wav(silk_bytes, sample_rate=24000):
    pcm_buffer = io.BytesIO()
    pysilk.decode(io.BytesIO(silk_bytes), pcm_buffer, sample_rate)
    pcm_bytes = pcm_buffer.getvalue()
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return wav_buffer.getvalue()
```

- [ ] **Step 2: Run helper tests to verify they pass**

Run: `python3 -m unittest tests.test_media_conversion`
Expected: PASS

- [ ] **Step 3: Refine helper error handling**

```python
def try_convert_image_bytes(file_name, raw_bytes):
    try:
        return convert_image_bytes(file_name, raw_bytes)
    except Exception:
        return None


def try_convert_silk_bytes_to_wav(silk_bytes, sample_rate=24000):
    try:
        return convert_silk_bytes_to_wav(silk_bytes, sample_rate)
    except Exception:
        return None
```

- [ ] **Step 4: Re-run helper tests**

Run: `python3 -m unittest tests.test_media_conversion`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add media_conversion.py tests/test_media_conversion.py
git commit -m "feat: add media conversion helpers"
```

### Task 3: Integrate normalized media output into exporters

**Files:**
- Modify: `chat_export.py`
- Test: `tests/test_chat_export.py`
- Test: `tests/test_export_all.py`

- [ ] **Step 1: Wire image conversion into file export**

```python
from media_conversion import try_convert_image_bytes, try_convert_silk_bytes_to_wav


def _write_normalized_image(output_dir, relative_path, source_path):
    converted = try_convert_image_bytes(source_path.name, source_path.read_bytes())
    if converted is None:
        return ""
    file_name, payload = converted
    destination = Path(output_dir) / relative_path.parent / file_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination.relative_to(output_dir).as_posix()
```

- [ ] **Step 2: Wire voice blob conversion into WAV export**

```python
voice_blob = media_context["voice_blobs"].get(key)
wav_bytes = try_convert_silk_bytes_to_wav(voice_blob, sample_rate=24000)
if wav_bytes is None:
    return ""
relative_path = Path("audio") / session_hash / f"{local_id}_{create_time}.wav"
(Path(output_dir) / relative_path).write_bytes(wav_bytes)
return relative_path.as_posix()
```

- [ ] **Step 3: Preserve graceful fallback behavior**

```python
if source_path is None and media_kind != "audio":
    return ""
```

```python
message["file_path"] = export_media_file(...)
```

- [ ] **Step 4: Run export integration tests**

Run: `python3 -m unittest tests.test_chat_export tests.test_export_all`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add chat_export.py tests/test_chat_export.py tests/test_export_all.py
git commit -m "feat: normalize exported media formats"
```

### Task 4: Update docs and verify end-to-end behavior

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-04-23-media-format-conversion-design.md`
- Test: `README.md`

- [ ] **Step 1: Update installation instructions**

```markdown
pip3 install -r requirements.txt
```

```markdown
新增依赖：`Pillow`、`silk-python`
```

- [ ] **Step 2: Update media export docs**

```markdown
- 普通照片导出为 `.jpg`
- 静态透明图导出为 `.png`
- 动图导出为 `.gif`
- 语音导出为 `.wav`
```

- [ ] **Step 3: Run full verification**

Run: `python3 -m unittest discover -s tests`
Expected: PASS

Run: `python3 export_all_in_one.py --output ./wechat-export --skip-scan --skip-decrypt --decrypted-dir ./decrypted`
Expected: PASS and exported JSON contains `audio/.../*.wav` plus normalized image suffixes

- [ ] **Step 4: Spot-check exported output**

Run: `python3 - <<'PY'\nimport glob, json\nfor path in sorted(glob.glob('wechat-export/*.json')):\n    with open(path, encoding='utf-8') as f:\n        data = json.load(f)\n    msgs = data.get('messages', data)\n    hits = [m.get('file_path') for m in msgs if m.get('file_path')]\n    if hits:\n        print(path, hits[:10])\nPY`
Expected: paths end in `.jpg`, `.png`, `.gif`, or `.wav`

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-04-23-media-format-conversion-design.md requirements.txt media_conversion.py chat_export.py tests
git commit -m "docs: describe normalized media export formats"
```
