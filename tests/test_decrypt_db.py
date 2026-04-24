import unittest
from unittest.mock import patch

import decrypt_db


class DecryptDbTests(unittest.TestCase):
    def test_decrypt_page_uses_crypto_cipher_when_available(self):
        enc_key = b"\x01" * 32
        iv = b"\x02" * 16
        encrypted = b"\x03" * (decrypt_db.PAGE_SZ - decrypt_db.RESERVE_SZ - decrypt_db.SALT_SZ)
        page_data = (b"\x00" * decrypt_db.SALT_SZ) + encrypted + iv + (b"\x00" * decrypt_db.HMAC_SZ)

        fake_cipher = type("FakeCipher", (), {"decrypt": staticmethod(lambda payload: b"\x04" * len(payload))})
        with patch("decrypt_db.AES") as mock_aes:
            mock_aes.MODE_CBC = object()
            mock_aes.new.return_value = fake_cipher

            decrypted_page = decrypt_db.decrypt_page(enc_key, page_data, 1)

        mock_aes.new.assert_called_once_with(enc_key, mock_aes.MODE_CBC, iv)
        self.assertTrue(decrypted_page.startswith(decrypt_db.SQLITE_HDR))

    def test_decrypt_aes_cbc_falls_back_to_commoncrypto(self):
        enc_key = b"\x11" * 32
        iv = b"\x22" * 16
        encrypted = b"\x33" * 16
        expected = b"\x44" * 16

        class FakeLib:
            def __init__(self):
                self.CCCrypt = self._cccrypt

            def _cccrypt(self, operation, algorithm, options, key_ptr, key_len, iv_ptr, data_ptr, data_len, out_ptr, out_len, moved_ptr):
                ctypes = __import__("ctypes")
                ctypes.memmove(out_ptr, expected, len(expected))
                ctypes.cast(moved_ptr, ctypes.POINTER(ctypes.c_size_t)).contents.value = len(expected)
                return 0

        with patch("decrypt_db.AES", None), patch("decrypt_db._COMMON_CRYPTO", None), patch("decrypt_db._load_common_crypto", return_value=FakeLib()):
            decrypted = decrypt_db.decrypt_aes_cbc(enc_key, iv, encrypted)

        self.assertEqual(decrypted, expected)


if __name__ == "__main__":
    unittest.main()
