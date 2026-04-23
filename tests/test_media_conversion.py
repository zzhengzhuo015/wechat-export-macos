import io
import wave
import unittest
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

    def _read_disposals(self, image):
        disposals = []
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            disposals.append(getattr(image, "disposal_method", None))
        return disposals


if __name__ == "__main__":
    unittest.main()
