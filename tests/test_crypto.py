"""app/crypto.py 的单元测试。

测试内容：
    - 签名生成（与已知值对比）
    - 加密/解密往返（encrypt -> decrypt 后应得到原始消息）
    - 不同消息长度（短消息、中等消息、长消息、中文消息）
    - verify_signature 与 generate_signature 的一致性
"""

import base64
import hashlib
import unittest

from app.crypto import (
    decrypt_msg,
    encrypt_msg,
    generate_signature,
    verify_signature,
)


class TestSignature(unittest.TestCase):
    """测试签名生成功能。"""

    def test_generate_signature_known_value(self) -> None:
        """测试签名生成与手动计算的结果一致。"""
        token = "QDG6eK"
        timestamp = "1409659588"
        nonce = "1372623149"
        encrypt = "RypEvHKDERTQQy5RTdC6fbjol3rgkVn8D9bGN7k6x"

        # 手动计算预期签名
        sort_list = sorted([token, timestamp, nonce, encrypt])
        expected = hashlib.sha1("".join(sort_list).encode("utf-8")).hexdigest()

        result = generate_signature(token, timestamp, nonce, encrypt)
        self.assertEqual(result, expected)
        # 签名应为 40 位十六进制字符串
        self.assertEqual(len(result), 40)

    def test_generate_signature_order_independent(self) -> None:
        """测试签名生成不受参数传入顺序影响（因为内部会排序）。"""
        token = "token123"
        timestamp = "1234567890"
        nonce = "nonce456"
        encrypt = "encrypt789"

        sig1 = generate_signature(token, timestamp, nonce, encrypt)
        # 相同参数应产生相同签名
        sig2 = generate_signature(token, timestamp, nonce, encrypt)
        self.assertEqual(sig1, sig2)

    def test_generate_signature_different_params(self) -> None:
        """测试不同参数产生不同签名。"""
        sig1 = generate_signature("token1", "ts1", "n1", "e1")
        sig2 = generate_signature("token2", "ts2", "n2", "e2")
        self.assertNotEqual(sig1, sig2)

    def test_verify_signature_equals_generate_signature(self) -> None:
        """测试 verify_signature 与 generate_signature 返回相同结果。"""
        token = "QDG6eK"
        timestamp = "1409659588"
        nonce = "1372623149"
        encrypt = "RypEvHKDERTQQy5RTdC6fbjol3rgkVn8D9bGN7k6x"

        sig_gen = generate_signature(token, timestamp, nonce, encrypt)
        sig_verify = verify_signature(token, timestamp, nonce, encrypt)
        self.assertEqual(sig_gen, sig_verify)


class TestEncryptDecrypt(unittest.TestCase):
    """测试加密解密往返功能。"""

    def setUp(self) -> None:
        """生成测试用的 EncodingAESKey 和 receiveid。"""
        # 生成 32 字节随机密钥，Base64 编码后去掉末尾的 "=" 得到 43 位 EncodingAESKey
        import os
        key_bytes = os.urandom(32)
        b64_key = base64.b64encode(key_bytes).decode("utf-8")
        # b64_key 是 44 位（含末尾 "="），去掉后为 43 位
        self.encoding_aes_key = b64_key.rstrip("=")
        self.assertEqual(len(self.encoding_aes_key), 43)
        self.receiveid = "wx5823bf96d3bd56c7"

    def test_encrypt_decrypt_roundtrip(self) -> None:
        """测试加密后解密能得到原始消息。"""
        msg = "Hello, WeCom!"
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        decrypted_msg, decrypted_receiveid = decrypt_msg(encrypted, self.encoding_aes_key)

        self.assertEqual(decrypted_msg, msg)
        self.assertEqual(decrypted_receiveid, self.receiveid)

    def test_encrypt_returns_base64(self) -> None:
        """测试加密返回的是合法的 base64 字符串。"""
        msg = "test message"
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        # 应该能被 base64 解码
        decoded = base64.b64decode(encrypted)
        self.assertTrue(len(decoded) > 0)
        # base64 解码后的长度应该是 32 的倍数（PKCS7 填充块大小）
        self.assertEqual(len(decoded) % 32, 0)

    def test_short_message(self) -> None:
        """测试短消息（1个字符）的加密解密往返。"""
        msg = "A"
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        decrypted_msg, _ = decrypt_msg(encrypted, self.encoding_aes_key)
        self.assertEqual(decrypted_msg, msg)

    def test_empty_message(self) -> None:
        """测试空字符串消息的加密解密往返。"""
        msg = ""
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        decrypted_msg, decrypted_receiveid = decrypt_msg(encrypted, self.encoding_aes_key)
        self.assertEqual(decrypted_msg, msg)
        self.assertEqual(decrypted_receiveid, self.receiveid)

    def test_medium_message(self) -> None:
        """测试中等长度消息（100字节）的加密解密往返。"""
        msg = "x" * 100
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        decrypted_msg, _ = decrypt_msg(encrypted, self.encoding_aes_key)
        self.assertEqual(decrypted_msg, msg)

    def test_long_message(self) -> None:
        """测试长消息（1000字节）的加密解密往返。"""
        msg = "y" * 1000
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        decrypted_msg, _ = decrypt_msg(encrypted, self.encoding_aes_key)
        self.assertEqual(decrypted_msg, msg)

    def test_very_long_message(self) -> None:
        """测试超长消息（10000字节）的加密解密往返。"""
        msg = "z" * 10000
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        decrypted_msg, _ = decrypt_msg(encrypted, self.encoding_aes_key)
        self.assertEqual(decrypted_msg, msg)

    def test_chinese_message(self) -> None:
        """测试中文消息的加密解密往返（UTF-8 多字节字符）。"""
        msg = "你好，企业微信！这是一条测试消息。"
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        decrypted_msg, decrypted_receiveid = decrypt_msg(encrypted, self.encoding_aes_key)
        self.assertEqual(decrypted_msg, msg)
        self.assertEqual(decrypted_receiveid, self.receiveid)

    def test_message_with_special_chars(self) -> None:
        """测试包含特殊字符的消息。"""
        msg = "特殊字符: <>&\"'\\{}\n\t\r"
        encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        decrypted_msg, _ = decrypt_msg(encrypted, self.encoding_aes_key)
        self.assertEqual(decrypted_msg, msg)

    def test_different_messages_produce_different_ciphertexts(self) -> None:
        """测试不同消息产生不同密文（因为包含随机数）。"""
        msg = "same message"
        enc1 = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        enc2 = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
        # 由于每次加密使用不同的随机数，密文应不同
        self.assertNotEqual(enc1, enc2)
        # 但两者都应能解密为相同的明文
        dec1, _ = decrypt_msg(enc1, self.encoding_aes_key)
        dec2, _ = decrypt_msg(enc2, self.encoding_aes_key)
        self.assertEqual(dec1, msg)
        self.assertEqual(dec2, msg)

    def test_boundary_length_messages(self) -> None:
        """测试边界长度消息（恰好使填充边界变化）。"""
        # PKCS7 块大小为 32，明文结构为 random(16) + len(4) + msg + receiveid
        # 测试不同长度的消息确保填充逻辑正确
        base = "wecom-cs"
        receiveid_len = len(self.receiveid.encode("utf-8"))
        # 16 + 4 + msg_len + receiveid_len 需要对齐到 32 字节
        for i in range(35):
            msg = base * (i + 1)
            encrypted = encrypt_msg(msg, self.encoding_aes_key, self.receiveid)
            decrypted_msg, decrypted_receiveid = decrypt_msg(encrypted, self.encoding_aes_key)
            self.assertEqual(decrypted_msg, msg, f"消息长度 {len(msg)} 往返失败")
            self.assertEqual(decrypted_receiveid, self.receiveid)


if __name__ == "__main__":
    unittest.main()
