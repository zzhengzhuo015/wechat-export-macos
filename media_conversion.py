import io
import wave
from pathlib import Path

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


def _replace_suffix(file_name, suffix):
    return f"{Path(file_name).stem}{suffix}"


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
        raise ImportError("Pillow is required for image conversion")

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
        return None


def try_convert_silk_bytes_to_wav(silk_bytes, sample_rate=24000):
    try:
        return convert_silk_bytes_to_wav(silk_bytes, sample_rate=sample_rate)
    except Exception:
        return None
