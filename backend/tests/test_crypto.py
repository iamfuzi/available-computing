import pytest
from services.crypto import encrypt, decrypt, generate_salt


class TestEncryptDecrypt:
    def test_roundtrip(self, fixed_salt):
        ct = encrypt("hello world", "password123", fixed_salt)
        assert decrypt(ct, "password123", fixed_salt) == "hello world"

    def test_wrong_password(self, fixed_salt):
        ct = encrypt("secret", "correct-password", fixed_salt)
        with pytest.raises(Exception):
            decrypt(ct, "wrong-password", fixed_salt)

    def test_wrong_salt(self):
        salt_a = generate_salt()
        salt_b = generate_salt()
        ct = encrypt("data", "password", salt_a)
        with pytest.raises(Exception):
            decrypt(ct, "password", salt_b)

    def test_corrupted_ciphertext(self, fixed_salt):
        with pytest.raises(Exception):
            decrypt("not-valid-base64!!!", "password", fixed_salt)

    def test_empty_string(self, fixed_salt):
        ct = encrypt("", "password", fixed_salt)
        assert decrypt(ct, "password", fixed_salt) == ""

    def test_unicode_content(self, fixed_salt):
        text = "你好世界 🌍 こんにちは"
        ct = encrypt(text, "password", fixed_salt)
        assert decrypt(ct, "password", fixed_salt) == text

    def test_different_ciphertexts_each_time(self, fixed_salt):
        """AES-GCM uses random nonce, so same input produces different ciphertexts."""
        ct1 = encrypt("same", "password", fixed_salt)
        ct2 = encrypt("same", "password", fixed_salt)
        assert ct1 != ct2

    def test_generate_salt_length(self):
        salt = generate_salt()
        assert len(salt) == 32
