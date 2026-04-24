import io
import tempfile
import wave
import unittest
from pathlib import Path
from unittest.mock import patch

import media_conversion


class MediaConversionTests(unittest.TestCase):
    EXIF_ORIENTATION_TAG = 274

    def _make_static_image_bytes(self, mode, image_format, size=(4, 4), **save_kwargs):
        from PIL import Image

        image = Image.new(mode, size)
        for x in range(size[0]):
            for y in range(size[1]):
                pixel = (x * 30 % 255, y * 60 % 255, (x + y) * 45 % 255)
                if "A" in mode:
                    alpha = 0 if (x, y) == (0, 0) else 255
                    pixel = pixel + (alpha,)
                image.putpixel((x, y), pixel)

        output = io.BytesIO()
        image.save(output, format=image_format, **save_kwargs)
        return output.getvalue()

    def _make_animated_gif_bytes(self, durations=(120, 120), disposal=2):
        from PIL import Image

        first = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        second = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        first.putpixel((0, 0), (255, 0, 0, 255))
        second.putpixel((3, 3), (0, 0, 255, 255))
        output = io.BytesIO()
        first.save(
            output,
            format="GIF",
            save_all=True,
            append_images=[second],
            duration=list(durations),
            loop=0,
            disposal=disposal,
            transparency=0,
        )
        return output.getvalue()

    def test_convert_static_rgb_image_to_jpg(self):
        png_bytes = self._make_static_image_bytes("RGB", "PNG")

        output_name, output_bytes = media_conversion.convert_image_bytes("photo.dat", png_bytes)

        self.assertEqual(output_name, "photo.jpg")
        self.assertTrue(output_bytes.startswith(b"\xff\xd8\xff"))

    def test_convert_transparent_image_to_png(self):
        png_bytes = self._make_static_image_bytes("RGBA", "PNG")

        output_name, output_bytes = media_conversion.convert_image_bytes("sticker.dat", png_bytes)

        self.assertEqual(output_name, "sticker.png")
        self.assertTrue(output_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_convert_image_bytes_applies_exif_orientation_before_reencoding(self):
        from PIL import Image

        image = Image.new("RGB", (20, 30), (0, 0, 0))
        for x in range(0, 10):
            for y in range(0, 10):
                image.putpixel((x, y), (255, 0, 0))
                image.putpixel((x, y + 20), (255, 0, 255))
                image.putpixel((x + 10, y), (0, 255, 0))
                image.putpixel((x + 10, y + 20), (0, 255, 255))

        exif = image.getexif()
        exif[self.EXIF_ORIENTATION_TAG] = 6

        raw = io.BytesIO()
        image.save(raw, format="PNG", exif=exif)

        output_name, output_bytes = media_conversion.convert_image_bytes("rotated.dat", raw.getvalue())

        self.assertEqual(output_name, "rotated.jpg")
        with Image.open(io.BytesIO(output_bytes)) as output_image:
            self.assertEqual(output_image.size, (30, 20))
            self.assertGreater(output_image.getpixel((5, 5))[0], 200)
            self.assertLess(output_image.getpixel((5, 5))[1], 80)
            self.assertGreater(output_image.getpixel((5, 5))[2], 200)
            self.assertGreater(output_image.getpixel((24, 5))[0], 200)
            self.assertLess(output_image.getpixel((24, 5))[1], 80)
            self.assertLess(output_image.getpixel((24, 5))[2], 80)

    def test_convert_animated_image_to_gif(self):
        from PIL import Image

        gif_bytes = self._make_animated_gif_bytes()

        output_name, output_bytes = media_conversion.convert_image_bytes("animated.dat", gif_bytes)

        self.assertEqual(output_name, "animated.gif")
        self.assertTrue(output_bytes.startswith(b"GIF8"))
        with Image.open(io.BytesIO(output_bytes)) as output_image:
            self.assertTrue(getattr(output_image, "is_animated", False))
            self.assertGreater(getattr(output_image, "n_frames", 1), 1)
            self.assertEqual(self._read_frame_durations(output_image), [120, 120])

    def test_convert_animated_image_to_gif_preserves_mixed_frame_durations(self):
        from PIL import Image

        gif_bytes = self._make_animated_gif_bytes(durations=(90, 240))

        _, output_bytes = media_conversion.convert_image_bytes("animated.dat", gif_bytes)

        with Image.open(io.BytesIO(output_bytes)) as output_image:
            self.assertEqual(self._read_frame_durations(output_image), [90, 240])
            self.assertEqual(self._read_disposals(output_image), [2, 2])

    def test_convert_silk_bytes_to_wav(self):
        silk_bytes = b"test-silk-fixture"
        pcm_bytes = b"\x00\x00\x10\x00\x20\x00\x30\x00"

        def fake_decode(input_stream, output_stream, sample_rate):
            self.assertEqual(input_stream.read(), silk_bytes)
            self.assertEqual(sample_rate, 24000)
            output_stream.write(pcm_bytes)

        with patch("media_conversion.pysilk.decode", side_effect=fake_decode):
            wav_bytes = media_conversion.convert_silk_bytes_to_wav(silk_bytes, sample_rate=24000)

        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 24000)
            self.assertEqual(wav_file.getnframes(), len(pcm_bytes) // 2)
            self.assertEqual(wav_file.readframes(wav_file.getnframes()), pcm_bytes)

    def test_try_convert_image_bytes_returns_none_on_error(self):
        result = media_conversion.try_convert_image_bytes("broken.dat", b"not-an-image")

        self.assertIsNone(result)

    def test_try_decode_wechat_dat_bytes_decodes_single_byte_xor_png_fixture(self):
        png_bytes = self._make_static_image_bytes("RGB", "PNG")
        encoded = bytes(value ^ 0x5A for value in png_bytes)

        decoded = media_conversion.try_decode_wechat_dat_bytes(encoded)

        self.assertIsNotNone(decoded)
        output_name, output_bytes = decoded
        self.assertEqual(output_name, "decoded.png")
        self.assertTrue(output_bytes.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_try_decode_wechat_dat_bytes_returns_none_for_unknown_payload(self):
        result = media_conversion.try_decode_wechat_dat_bytes(b"\x07\x08V2not-a-real-image-payload")

        self.assertIsNone(result)

    def test_try_convert_image_file_decodes_v2_dat_with_image_keys(self):
        jpeg_bytes = self._make_static_image_bytes("RGB", "JPEG")
        aes_key = bytes.fromhex("11223344556677889900aabbccddeeff")
        ciphertext_hex, full_dat = self._make_v2_dat_bytes(jpeg_bytes, aes_key, xor_key=0x42)
        _, thumb_dat = self._make_v2_dat_bytes(jpeg_bytes, aes_key, xor_key=0x42)

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "sample.dat"
            thumb_path = Path(temp_dir) / "sample_t.dat"
            image_path.write_bytes(full_dat)
            thumb_path.write_bytes(thumb_dat)

            with patch("media_conversion.load_wechat_image_keys", return_value={ciphertext_hex: aes_key}):
                converted = media_conversion.try_convert_image_file(image_path)

        self.assertIsNotNone(converted)
        output_name, output_bytes = converted
        self.assertEqual(output_name, "decoded.jpg")
        self.assertTrue(output_bytes.startswith(b"\xff\xd8\xff"))

    def test_try_convert_image_file_decodes_v2_dat_with_weflow_derived_keys(self):
        jpeg_bytes = self._make_static_image_bytes("RGB", "JPEG")
        wxid = "wxid_owner123"
        code = 4145073218
        xor_key, aes_key = media_conversion.derive_wechat_image_keys(code, wxid)
        ciphertext_hex, full_dat = self._make_v2_dat_bytes(jpeg_bytes, aes_key, xor_key=xor_key)
        _, thumb_dat = self._make_v2_dat_bytes(jpeg_bytes, aes_key, xor_key=xor_key)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            msg_path = root / "xwechat_files" / f"{wxid}_abcd" / "msg" / "attach" / "session" / "2026-04" / "Img"
            kvcomm_path = root / "app_data" / "net" / "kvcomm"
            msg_path.mkdir(parents=True, exist_ok=True)
            kvcomm_path.mkdir(parents=True, exist_ok=True)

            image_path = msg_path / "sample.dat"
            thumb_path = msg_path / "sample_t.dat"
            stat_path = kvcomm_path / f"key_{code}_1_1_1_1_3600_input.statistic"
            image_path.write_bytes(full_dat)
            thumb_path.write_bytes(thumb_dat)
            stat_path.write_text("", encoding="utf-8")

            with patch("media_conversion.load_wechat_image_keys", return_value={}):
                converted = media_conversion.try_convert_image_file(image_path)

        self.assertIsNotNone(converted)
        output_name, output_bytes = converted
        self.assertEqual(output_name, "decoded.jpg")
        self.assertTrue(output_bytes.startswith(b"\xff\xd8\xff"))

    def test_try_convert_image_file_falls_back_to_thumb_when_full_v2_payload_is_wxgf(self):
        jpeg_bytes = self._make_static_image_bytes("RGB", "JPEG")
        wxid = "wxid_owner123"
        code = 4145073218
        xor_key, aes_key = media_conversion.derive_wechat_image_keys(code, wxid)

        full_ciphertext_hex, full_dat = self._make_v2_dat_from_parts(
            b"wxgf" + b"\x00" * 12,
            b"",
            b"\xff\xd9",
            aes_key,
            xor_key,
        )
        _, thumb_dat = self._make_v2_dat_bytes(jpeg_bytes, aes_key, xor_key=xor_key)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            msg_path = root / "xwechat_files" / f"{wxid}_abcd" / "msg" / "attach" / "session" / "2026-04" / "Img"
            kvcomm_path = root / "app_data" / "net" / "kvcomm"
            msg_path.mkdir(parents=True, exist_ok=True)
            kvcomm_path.mkdir(parents=True, exist_ok=True)

            image_path = msg_path / "sample.dat"
            thumb_path = msg_path / "sample_t.dat"
            stat_path = kvcomm_path / f"key_{code}_1_1_1_1_3600_input.statistic"
            image_path.write_bytes(full_dat)
            thumb_path.write_bytes(thumb_dat)
            stat_path.write_text("", encoding="utf-8")

            with patch("media_conversion.load_wechat_image_keys", return_value={full_ciphertext_hex: aes_key}):
                converted = media_conversion.try_convert_image_file(image_path)

        self.assertIsNotNone(converted)
        output_name, output_bytes = converted
        self.assertEqual(output_name, "decoded.jpg")
        self.assertTrue(output_bytes.startswith(b"\xff\xd8\xff"))

    def test_convert_wxgf_to_jpeg_bytes_uses_ffmpeg_output(self):
        jpeg_bytes = self._make_static_image_bytes("RGB", "JPEG")

        def fake_run(cmd, stdout, stderr, check, timeout):
            Path(cmd[-1]).write_bytes(jpeg_bytes)
            return None

        with patch("media_conversion.subprocess.run", side_effect=fake_run):
            result = media_conversion._convert_wxgf_to_jpeg_bytes(b"wxgf" + b"\x00\x00\x00\x01" + b"\x01\x02\x03\x04")

        self.assertEqual(result, jpeg_bytes)

    def test_verify_wechat_v4_image_aes_key_accepts_matching_ascii_candidate(self):
        key_text = "A1B2C3D4E5F6G7H8Z9Y0X1W2V3U4T5S6"
        key_bytes = key_text.encode("ascii")
        ciphertext = self._encrypt_aes_block_ecb(b"\xff\xd8\xff" + b"\x00" * 13, key_bytes[:16])

        self.assertTrue(media_conversion.verify_wechat_v4_image_aes_key(ciphertext, key_bytes))

    def test_try_convert_silk_bytes_to_wav_returns_none_on_error(self):
        with patch(
            "media_conversion.convert_silk_bytes_to_wav",
            side_effect=RuntimeError("decode failed"),
        ):
            result = media_conversion.try_convert_silk_bytes_to_wav(b"broken-silk")

        self.assertIsNone(result)

    def _read_frame_durations(self, image):
        durations = []
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            durations.append(image.info.get("duration"))
        return durations

    def _encrypt_aes_block_ecb(self, plaintext, key):
        from Crypto.Cipher import AES

        return AES.new(key, AES.MODE_ECB).encrypt(plaintext)

    def _make_v2_dat_bytes(self, image_bytes, aes_key, xor_key):
        from Crypto.Cipher import AES
        from Crypto.Util import Padding

        aes_size = 16
        xor_size = 2
        aes_plain = image_bytes[:aes_size]
        raw_middle = image_bytes[aes_size:-xor_size]
        xor_tail = image_bytes[-xor_size:]

        aes_cipher = AES.new(aes_key[:16], AES.MODE_ECB)
        aes_encrypted = aes_cipher.encrypt(Padding.pad(aes_plain, AES.block_size))
        xor_encrypted = bytes(value ^ xor_key for value in xor_tail)
        dat_bytes = (
            media_conversion.V2_MAGIC_FULL
            + aes_size.to_bytes(4, "little")
            + xor_size.to_bytes(4, "little")
            + b"\x01"
            + aes_encrypted
            + raw_middle
            + xor_encrypted
        )
        return aes_encrypted[:16].hex(), dat_bytes

    def _make_v2_dat_from_parts(self, aes_plain, raw_middle, xor_tail, aes_key, xor_key):
        from Crypto.Cipher import AES
        from Crypto.Util import Padding

        aes_size = len(aes_plain)
        xor_size = len(xor_tail)
        aes_cipher = AES.new(aes_key[:16], AES.MODE_ECB)
        aes_encrypted = aes_cipher.encrypt(Padding.pad(aes_plain, AES.block_size))
        xor_encrypted = bytes(value ^ xor_key for value in xor_tail)
        dat_bytes = (
            media_conversion.V2_MAGIC_FULL
            + aes_size.to_bytes(4, "little")
            + xor_size.to_bytes(4, "little")
            + b"\x01"
            + aes_encrypted
            + raw_middle
            + xor_encrypted
        )
        return aes_encrypted[:16].hex(), dat_bytes

    def _read_disposals(self, image):
        disposals = []
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            disposals.append(getattr(image, "disposal_method", None))
        return disposals


if __name__ == "__main__":
    unittest.main()
