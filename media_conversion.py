import io
import json
import hashlib
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path

try:
    from Crypto.Cipher import AES
    from Crypto.Util import Padding
except ImportError:  # pragma: no cover - optional dependency
    AES = None
    Padding = None

try:
    from PIL import Image, ImageOps, ImageSequence
except ImportError:  # pragma: no cover - dependency blocker surfaced at runtime
    Image = None
    ImageOps = None
    ImageSequence = None

try:
    import pysilk
except ImportError:  # pragma: no cover - dependency blocker surfaced at runtime
    pysilk = None


V1_MAGIC_FULL = b"\x07\x08V1\x08\x07"
V2_MAGIC_FULL = b"\x07\x08V2\x08\x07"


def _replace_suffix(file_name, suffix):
    return f"{Path(file_name).stem}{suffix}"


def clean_wechat_account_id(account_name):
    trimmed = str(account_name or "").strip()
    if not trimmed:
        return ""
    if trimmed.lower().startswith("wxid_"):
        match = re.match(r"^(wxid_[^_]+)", trimmed, re.IGNORECASE)
        return match.group(1) if match else trimmed
    suffix_match = re.match(r"^(.+)_([a-zA-Z0-9]{4})$", trimmed)
    return suffix_match.group(1) if suffix_match else trimmed


def resolve_xwechat_root_from_source_path(source_path):
    normalized = str(source_path).replace("\\", "/")
    marker = "/xwechat_files/"
    index = normalized.find(marker)
    if index == -1:
        return None
    return Path(normalized[: index + len(marker) - 1])


def resolve_wechat_account_name_from_source_path(source_path):
    normalized = str(source_path).replace("\\", "/")
    marker = "/xwechat_files/"
    index = normalized.find(marker)
    if index == -1:
        return ""
    remainder = normalized[index + len(marker) :]
    if not remainder:
        return ""
    return remainder.split("/", 1)[0]


def collect_kvcomm_codes(source_path):
    xwechat_root = resolve_xwechat_root_from_source_path(source_path)
    if xwechat_root is None:
        return []
    kvcomm_dir = xwechat_root.parent / "app_data" / "net" / "kvcomm"
    if not kvcomm_dir.exists():
        return []

    codes = []
    pattern = re.compile(r"^key_(\d+)_.+\.statistic$", re.IGNORECASE)
    for entry in kvcomm_dir.iterdir():
        match = pattern.match(entry.name)
        if not match:
            continue
        code = int(match.group(1))
        if code not in codes:
            codes.append(code)
    return codes


def derive_wechat_image_keys(code, wxid):
    cleaned_wxid = clean_wechat_account_id(wxid)
    xor_key = int(code) & 0xFF
    aes_key = hashlib.md5(f"{int(code)}{cleaned_wxid}".encode("utf-8")).hexdigest()[:16].encode("ascii")
    return xor_key, aes_key


def load_wechat_image_keys():
    image_keys_path = Path(__file__).with_name("image_keys.json")
    if not image_keys_path.exists():
        return {}
    try:
        with image_keys_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(ciphertext_hex).lower(): bytes.fromhex(key_hex)
        for ciphertext_hex, key_hex in data.items()
        if isinstance(ciphertext_hex, str)
        and isinstance(key_hex, str)
        and len(key_hex) >= 32
    }


def _is_ascii_alnum(byte_value):
    return (48 <= byte_value <= 57) or (65 <= byte_value <= 90) or (97 <= byte_value <= 122)


def verify_wechat_v4_image_aes_key(ciphertext, candidate_key):
    if len(ciphertext) == 0 or len(ciphertext) % 16 != 0:
        return False
    if len(candidate_key) < 16:
        return False
    plaintext = decrypt_aes_ecb_bytes(ciphertext, candidate_key[:16])
    if plaintext is None:
        return False
    return (
        plaintext.startswith(b"\xff\xd8\xff")
        or plaintext.startswith(b"\x89PNG")
        or plaintext.startswith(b"GIF")
        or plaintext.startswith(b"RIFF")
        or plaintext.startswith(b"wxgf")
    )


def decrypt_aes_ecb_bytes(ciphertext, key_bytes):
    if len(key_bytes) < 16:
        return None
    key = key_bytes[:16]

    if AES is not None:
        try:
            cipher = AES.new(key, AES.MODE_ECB)
            return cipher.decrypt(ciphertext)
        except Exception:
            pass

    try:
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-aes-128-ecb",
                "-d",
                "-nopad",
                "-nosalt",
                "-K",
                key.hex(),
            ],
            input=ciphertext,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except Exception:
        return None
    return result.stdout


def _verified_image_bytes(raw_bytes):
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return ".jpg", raw_bytes
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", raw_bytes
    if raw_bytes.startswith(b"GIF87a") or raw_bytes.startswith(b"GIF89a"):
        return ".gif", raw_bytes
    if raw_bytes.startswith(b"RIFF") and len(raw_bytes) >= 12 and raw_bytes[8:12] == b"WEBP":
        return ".webp", raw_bytes
    if raw_bytes.startswith(b"BM") and len(raw_bytes) >= 54:
        pixel_offset = int.from_bytes(raw_bytes[10:14], "little")
        dib_header_size = int.from_bytes(raw_bytes[14:18], "little")
        width = int.from_bytes(raw_bytes[18:22], "little")
        height = int.from_bytes(raw_bytes[22:26], "little")
        if pixel_offset >= 14 and dib_header_size >= 12 and width > 0 and height > 0:
            return ".bmp", raw_bytes

    if Image is None:
        raise ImportError("Pillow is required for image conversion")

    with Image.open(io.BytesIO(raw_bytes)) as image:
        image.load()
        image_format = (image.format or "").upper()

    suffix_by_format = {
        "PNG": ".png",
        "JPEG": ".jpg",
        "GIF": ".gif",
        "WEBP": ".webp",
        "BMP": ".bmp",
    }
    suffix = suffix_by_format.get(image_format)
    if not suffix:
        return None
    return suffix, raw_bytes


def _convert_wxgf_to_jpeg_bytes(raw_bytes):
    if not raw_bytes.startswith(b"wxgf") or len(raw_bytes) <= 4:
        return None

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.hevc"
        output_path = Path(temp_dir) / "output.jpg"
        input_path.write_bytes(raw_bytes[4:])

        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "hevc",
                    "-i",
                    str(input_path),
                    "-frames:v",
                    "1",
                    str(output_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=20,
            )
        except Exception:
            return None

        if not output_path.exists():
            return None
        output_bytes = output_path.read_bytes()
        verified = _verified_image_bytes(output_bytes)
        if verified is None:
            return None
        return output_bytes


def _detect_v2_xor_key_from_tail(raw_bytes):
    if len(raw_bytes) < 2:
        return None
    key1 = raw_bytes[-2] ^ 0xFF
    key2 = raw_bytes[-1] ^ 0xD9
    if key1 == key2:
        return key1
    return None


def _iter_v2_xor_key_candidates(raw_bytes, source_path):
    seen = set()

    if source_path is not None:
        sibling_thumb = source_path.with_name(f"{source_path.stem}_t{source_path.suffix}")
        if sibling_thumb.exists():
            key = _detect_v2_xor_key_from_tail(sibling_thumb.read_bytes())
            if key is not None and key not in seen:
                seen.add(key)
                yield key

    direct_key = _detect_v2_xor_key_from_tail(raw_bytes)
    if direct_key is not None and direct_key not in seen:
        seen.add(direct_key)
        yield direct_key

    for key in range(256):
        if key in seen:
            continue
        yield key


def _decrypt_wechat_v2_dat_bytes(raw_bytes, aes_key, xor_key):
    if len(raw_bytes) < 15:
        return None

    signature = raw_bytes[:6]
    if signature not in (V1_MAGIC_FULL, V2_MAGIC_FULL):
        return None

    aes_size = int.from_bytes(raw_bytes[6:10], "little")
    xor_size = int.from_bytes(raw_bytes[10:14], "little")
    if aes_size <= 0:
        return None

    aes_key_bytes = aes_key
    if signature == V1_MAGIC_FULL:
        aes_key_bytes = b"cfcd208495d565ef"
    if len(aes_key_bytes) < 16:
        return None

    aligned_aes_size = ((aes_size // 16) + 1) * 16 if aes_size % 16 == 0 else ((aes_size + 15) // 16) * 16
    offset = 15
    if offset + aligned_aes_size > len(raw_bytes):
        return None

    aes_data = raw_bytes[offset : offset + aligned_aes_size]
    decrypted_full = decrypt_aes_ecb_bytes(aes_data, aes_key_bytes[:16])
    if decrypted_full is None:
        return None
    if Padding is not None:
        try:
            decrypted_aes = Padding.unpad(decrypted_full, 16)
        except (ValueError, KeyError):
            return None
    else:
        if not decrypted_full:
            return None
        pad = decrypted_full[-1]
        if pad <= 0 or pad > 16:
            return None
        if decrypted_full[-pad:] != bytes([pad]) * pad:
            return None
        decrypted_aes = decrypted_full[:-pad]
    if decrypted_aes is None:
        return None

    offset += aligned_aes_size
    raw_end = len(raw_bytes) - xor_size if xor_size <= len(raw_bytes) - offset else offset
    raw_middle = raw_bytes[offset:raw_end]
    xor_data = raw_bytes[raw_end:]
    decrypted_xor = bytes(byte ^ xor_key for byte in xor_data)
    return decrypted_aes + raw_middle + decrypted_xor


def _decode_wechat_v2_payload(raw_bytes, source_path=None):
    signature = raw_bytes[:6]
    if signature not in (V1_MAGIC_FULL, V2_MAGIC_FULL):
        return None

    ciphertext = raw_bytes[15:31]
    ciphertext_hex = ciphertext.hex()

    candidate_pairs = []
    seen_pairs = set()

    if source_path is not None:
        account_name = resolve_wechat_account_name_from_source_path(source_path)
        for code in collect_kvcomm_codes(source_path):
            xor_key, aes_key = derive_wechat_image_keys(code, account_name)
            if not verify_wechat_v4_image_aes_key(ciphertext, aes_key):
                continue
            pair_key = (xor_key, aes_key)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            candidate_pairs.append(pair_key)

    image_keys = load_wechat_image_keys()
    mapped_aes_key = image_keys.get(ciphertext_hex)
    if mapped_aes_key is not None:
        for xor_key in _iter_v2_xor_key_candidates(raw_bytes, source_path):
            pair_key = (xor_key, mapped_aes_key)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            candidate_pairs.append(pair_key)

    if signature == V2_MAGIC_FULL and not candidate_pairs:
        return None

    for xor_key, aes_key in candidate_pairs:
        decrypted = _decrypt_wechat_v2_dat_bytes(raw_bytes, aes_key or b"", xor_key)
        if decrypted is not None:
            return decrypted

    return None


def _try_decode_wechat_v2_dat_bytes(raw_bytes, source_path=None):
    decrypted = _decode_wechat_v2_payload(raw_bytes, source_path=source_path)
    if decrypted is None:
        return None

    try:
        verified = _verified_image_bytes(decrypted)
    except Exception:
        verified = None
    if verified is not None:
        suffix, payload = verified
        return f"decoded{suffix}", payload

    if decrypted.startswith(b"wxgf") and source_path is not None:
        wxgf_jpeg = _convert_wxgf_to_jpeg_bytes(decrypted)
        if wxgf_jpeg is not None:
            return "decoded.jpg", wxgf_jpeg

        sibling_thumb = source_path.with_name(f"{source_path.stem}_t{source_path.suffix}")
        if sibling_thumb.exists():
            thumb_raw = sibling_thumb.read_bytes()
            thumb_decrypted = _decode_wechat_v2_payload(thumb_raw, source_path=sibling_thumb)
            if thumb_decrypted is not None:
                try:
                    thumb_verified = _verified_image_bytes(thumb_decrypted)
                except Exception:
                    thumb_verified = None
                if thumb_verified is not None:
                    suffix, payload = thumb_verified
                    return f"decoded{suffix}", payload

    return None


def try_decode_wechat_dat_bytes(raw_bytes, source_path=None):
    if Image is None:
        pass
    else:
        try:
            verified = _verified_image_bytes(raw_bytes)
        except Exception:
            verified = None
        if verified is not None:
            suffix, payload = verified
            return f"decoded{suffix}", payload

    if raw_bytes[:6] in (V1_MAGIC_FULL, V2_MAGIC_FULL):
        return _try_decode_wechat_v2_dat_bytes(raw_bytes, source_path=source_path)

    signatures = (
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff",
        b"GIF87a",
        b"GIF89a",
        b"RIFF",
        b"BM",
    )
    max_offset = min(len(raw_bytes), 32)
    for offset in range(max_offset):
        window = raw_bytes[offset:]
        if not window:
            continue
        for signature in signatures:
            key = window[0] ^ signature[0]
            decoded = bytes(value ^ key for value in window)
            try:
                verified = _verified_image_bytes(decoded)
            except Exception:
                continue
            if verified is None:
                continue
            suffix, payload = verified
            return f"decoded{suffix}", payload

    return None


def _frame_has_transparency(frame, source_image):
    if "A" in frame.getbands():
        alpha = frame.getchannel("A")
        minimum, maximum = alpha.getextrema()
        return minimum < 255 or maximum < 255

    return "transparency" in source_image.info


def _extract_animation_frames(image):
    frames = []
    durations = []
    disposals = []

    for frame in ImageSequence.Iterator(image):
        frames.append(frame.convert("RGBA"))
        durations.append(frame.info.get("duration", image.info.get("duration")))
        disposals.append(
            getattr(frame, "disposal_method", image.info.get("disposal", getattr(image, "disposal_method", 0)))
        )

    return frames, durations, disposals


def convert_image_bytes(file_name, raw_bytes):
    if Image is None or ImageOps is None or ImageSequence is None:
        verified = _verified_image_bytes(raw_bytes)
        if verified is None:
            raise ImportError("Pillow is required for image conversion")
        suffix, payload = verified
        return _replace_suffix(file_name, suffix), payload

    with Image.open(io.BytesIO(raw_bytes)) as image:
        if getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1:
            frames, durations, disposals = _extract_animation_frames(image)
            output = io.BytesIO()
            save_kwargs = {
                "format": "GIF",
                "save_all": True,
                "append_images": frames[1:],
                "loop": image.info.get("loop", 0),
                "duration": durations,
                "disposal": disposals,
            }
            if "transparency" in image.info:
                save_kwargs["transparency"] = image.info["transparency"]
            if "background" in image.info:
                save_kwargs["background"] = image.info["background"]

            frames[0].save(
                output,
                **save_kwargs,
            )
            return _replace_suffix(file_name, ".gif"), output.getvalue()

        normalized_image = ImageOps.exif_transpose(image)
        frame = normalized_image.convert("RGBA")
        output = io.BytesIO()
        if _frame_has_transparency(frame, normalized_image):
            frame.save(output, format="PNG")
            return _replace_suffix(file_name, ".png"), output.getvalue()

        frame.convert("RGB").save(output, format="JPEG", quality=95)
        return _replace_suffix(file_name, ".jpg"), output.getvalue()


def convert_silk_bytes_to_wav(silk_bytes, sample_rate=24000):
    if pysilk is None:
        raise ImportError("silk-python is required for Silk conversion")

    pcm_buffer = io.BytesIO()
    pysilk.decode(io.BytesIO(silk_bytes), pcm_buffer, sample_rate)
    pcm_bytes = pcm_buffer.getvalue()
    if len(pcm_bytes) % 2 != 0:
        raise ValueError("decoded PCM must be 16-bit samples")

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return wav_buffer.getvalue()


def try_convert_image_bytes(file_name, raw_bytes):
    try:
        return convert_image_bytes(file_name, raw_bytes)
    except Exception:
        decoded = try_decode_wechat_dat_bytes(raw_bytes)
        if decoded is None:
            return None
        return decoded


def try_convert_image_file(source_path):
    raw_bytes = source_path.read_bytes()
    try:
        return convert_image_bytes(source_path.name, raw_bytes)
    except Exception:
        decrypted = _decode_wechat_v2_payload(raw_bytes, source_path=source_path)
        if decrypted is not None:
            try:
                verified = _verified_image_bytes(decrypted)
            except Exception:
                verified = None
            if verified is not None:
                suffix, payload = verified
                return _replace_suffix("decoded", suffix), payload
            if decrypted.startswith(b"wxgf"):
                sibling_thumb = source_path.with_name(f"{source_path.stem}_t{source_path.suffix}")
                if sibling_thumb.exists():
                    thumb_raw = sibling_thumb.read_bytes()
                    thumb_decrypted = _decode_wechat_v2_payload(thumb_raw, source_path=sibling_thumb)
                    if thumb_decrypted is not None:
                        try:
                            thumb_verified = _verified_image_bytes(thumb_decrypted)
                        except Exception:
                            thumb_verified = None
                        if thumb_verified is not None:
                            suffix, payload = thumb_verified
                            return _replace_suffix("decoded", suffix), payload
        decoded = try_decode_wechat_dat_bytes(raw_bytes, source_path=source_path)
        if decoded is None:
            return None
        return decoded


def try_convert_silk_bytes_to_wav(silk_bytes, sample_rate=24000):
    try:
        return convert_silk_bytes_to_wav(silk_bytes, sample_rate=sample_rate)
    except Exception:
        return None
