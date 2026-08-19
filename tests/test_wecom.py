"""app/callback.py 的单元测试。

测试内容：
    - CallbackHandler.handle_get 的验签和解密流程（使用模拟数据）
    - CallbackHandler.handle_post 的验签和解密流程（使用模拟数据）
    - 签名验证失败时的错误处理
    - receiveid 不匹配时的错误处理
"""

import base64
import os
import unittest

from app.callback import CallbackHandler
from app.crypto import encrypt_msg, generate_signature


def _generate_encoding_aes_key() -> str:
    """生成一个有效的 43 位 EncodingAESKey 用于测试。"""
    key_bytes = os.urandom(32)
    b64_key = base64.b64encode(key_bytes).decode("utf-8")
    return b64_key.rstrip("=")


class TestCallbackHandlerGet(unittest.TestCase):
    """测试 CallbackHandler.handle_get（URL 验证）。"""

    def setUp(self) -> None:
        """初始化测试用的 handler 和参数。"""
        self.token = "QDG6eK"
        self.encoding_aes_key = _generate_encoding_aes_key()
        self.corpid = "wx5823bf96d3bd56c7"
        self.timestamp = "1409659588"
        self.nonce = "1372623149"
        self.handler = CallbackHandler(self.token, self.encoding_aes_key, self.corpid)

    def _make_echostr(self, plaintext: str) -> str:
        """构造加密的 echostr 及对应的正确签名。"""
        echostr = encrypt_msg(plaintext, self.encoding_aes_key, self.corpid)
        signature = generate_signature(self.token, self.timestamp, self.nonce, echostr)
        return echostr, signature

    def test_handle_get_success(self) -> None:
        """测试 URL 验证成功：验签通过、解密正确。"""
        original = "verify_url_echostr_123456"
        echostr, signature = self._make_echostr(original)

        result = self.handler.handle_get(signature, self.timestamp, self.nonce, echostr)
        self.assertEqual(result, original)

    def test_handle_get_wrong_signature(self) -> None:
        """测试签名验证失败时抛出 ValueError。"""
        original = "verify_url_echostr"
        echostr, _ = self._make_echostr(original)
        wrong_signature = "0" * 40

        with self.assertRaises(ValueError) as ctx:
            self.handler.handle_get(wrong_signature, self.timestamp, self.nonce, echostr)
        self.assertIn("签名验证失败", str(ctx.exception))

    def test_handle_get_wrong_receiveid(self) -> None:
        """测试 receiveid 与 corpid 不匹配时抛出 ValueError。"""
        original = "verify_url_echostr"
        # 使用错误的 receiveid 加密
        wrong_receiveid = "wrong_corpid_12345"
        echostr = encrypt_msg(original, self.encoding_aes_key, wrong_receiveid)
        signature = generate_signature(self.token, self.timestamp, self.nonce, echostr)

        with self.assertRaises(ValueError) as ctx:
            self.handler.handle_get(signature, self.timestamp, self.nonce, echostr)
        self.assertIn("receiveid", str(ctx.exception))

    def test_handle_get_empty_echostr(self) -> None:
        """测试解密空字符串 echostr。"""
        original = ""
        echostr, signature = self._make_echostr(original)

        result = self.handler.handle_get(signature, self.timestamp, self.nonce, echostr)
        self.assertEqual(result, "")

    def test_handle_get_chinese_echostr(self) -> None:
        """测试解密包含中文的 echostr。"""
        original = "验证URL有效性_中文测试"
        echostr, signature = self._make_echostr(original)

        result = self.handler.handle_get(signature, self.timestamp, self.nonce, echostr)
        self.assertEqual(result, original)

    def test_handle_get_result_no_quotes(self) -> None:
        """测试返回的明文不包含引号、BOM、换行等额外字符。"""
        original = "plain_echostr"
        echostr, signature = self._make_echostr(original)

        result = self.handler.handle_get(signature, self.timestamp, self.nonce, echostr)
        # 返回值应与原始字符串完全一致，不含额外字符
        self.assertEqual(result, original)
        self.assertFalse(result.startswith('"') or result.endswith('"'))
        self.assertFalse(result.startswith("\ufeff"))  # 无 BOM
        self.assertFalse("\n" in result or "\r" in result)


class TestCallbackHandlerPost(unittest.TestCase):
    """测试 CallbackHandler.handle_post（消息回调）。"""

    def setUp(self) -> None:
        """初始化测试用的 handler 和参数。"""
        self.token = "QDG6eK"
        self.encoding_aes_key = _generate_encoding_aes_key()
        self.corpid = "wx5823bf96d3bd56c7"
        self.timestamp = "1409659588"
        self.nonce = "1372623149"
        self.handler = CallbackHandler(self.token, self.encoding_aes_key, self.corpid)

    def _make_message_body(
        self,
        from_user: str = "user001",
        content: str = "hello world",
        msg_type: str = "text",
        msg_id: str = "1234567890123456",
        create_time: str = "1348831860",
    ) -> tuple:
        """构造测试用的消息回调请求体及正确签名。

        Returns:
            (body_xml, signature) 元组
        """
        # 构造内层消息 XML（将被加密）
        inner_xml = (
            f"<xml>"
            f"<ToUserName><![CDATA[{self.corpid}]]></ToUserName>"
            f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
            f"<CreateTime>{create_time}</CreateTime>"
            f"<MsgType><![CDATA[{msg_type}]]></MsgType>"
            f"<Content><![CDATA[{content}]]></Content>"
            f"<MsgId>{msg_id}</MsgId>"
            f"</xml>"
        )

        # 加密内层 XML
        encrypt = encrypt_msg(inner_xml, self.encoding_aes_key, self.corpid)

        # 构造外层 XML
        body = (
            f"<xml>"
            f"<ToUserName><![CDATA[{self.corpid}]]></ToUserName>"
            f"<AgentID>1</AgentID>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"</xml>"
        )

        # 生成签名
        signature = generate_signature(self.token, self.timestamp, self.nonce, encrypt)
        return body, signature

    def test_handle_post_success(self) -> None:
        """测试消息回调成功：验签通过、解密正确、字段解析正确。"""
        expected_content = "这是一条测试消息"
        expected_from = "test_user_001"
        expected_msg_id = "1234567890123456"
        expected_create_time = 1348831860

        body, signature = self._make_message_body(
            from_user=expected_from,
            content=expected_content,
            msg_id=expected_msg_id,
            create_time=str(expected_create_time),
        )

        result = self.handler.handle_post(signature, self.timestamp, self.nonce, body)

        self.assertEqual(result["from_user"], expected_from)
        self.assertEqual(result["to_user"], self.corpid)
        self.assertEqual(result["msg_type"], "text")
        self.assertEqual(result["content"], expected_content)
        self.assertEqual(result["msg_id"], expected_msg_id)
        self.assertEqual(result["create_time"], expected_create_time)

    def test_handle_post_wrong_signature(self) -> None:
        """测试消息回调签名验证失败时抛出 ValueError。"""
        body, _ = self._make_message_body()
        wrong_signature = "0" * 40

        with self.assertRaises(ValueError) as ctx:
            self.handler.handle_post(wrong_signature, self.timestamp, self.nonce, body)
        self.assertIn("签名验证失败", str(ctx.exception))

    def test_handle_post_wrong_receiveid(self) -> None:
        """测试解密后 receiveid 与 corpid 不匹配时抛出 ValueError。"""
        # 使用错误的 receiveid 加密内层 XML
        inner_xml = (
            f"<xml>"
            f"<ToUserName><![CDATA[wrong_corpid]]></ToUserName>"
            f"<FromUserName><![CDATA[user001]]></FromUserName>"
            f"<CreateTime>1348831860</CreateTime>"
            f"<MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[hello]]></Content>"
            f"<MsgId>1234567890123456</MsgId>"
            f"</xml>"
        )
        wrong_receiveid = "wrong_corpid_xyz"
        encrypt = encrypt_msg(inner_xml, self.encoding_aes_key, wrong_receiveid)
        body = (
            f"<xml>"
            f"<ToUserName><![CDATA[{wrong_receiveid}]]></ToUserName>"
            f"<AgentID>1</AgentID>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"</xml>"
        )
        signature = generate_signature(self.token, self.timestamp, self.nonce, encrypt)

        with self.assertRaises(ValueError) as ctx:
            self.handler.handle_post(signature, self.timestamp, self.nonce, body)
        self.assertIn("receiveid", str(ctx.exception))

    def test_handle_post_chinese_content(self) -> None:
        """测试包含中文内容的消息回调。"""
        content = "你好，企业微信客服系统！"
        body, signature = self._make_message_body(content=content)

        result = self.handler.handle_post(signature, self.timestamp, self.nonce, body)
        self.assertEqual(result["content"], content)

    def test_handle_post_special_chars_in_content(self) -> None:
        """测试包含特殊字符的消息回调。"""
        content = '特殊字符: <>&"\'\\{}\n\t'
        body, signature = self._make_message_body(content=content)

        result = self.handler.handle_post(signature, self.timestamp, self.nonce, body)
        self.assertEqual(result["content"], content)

    def test_handle_post_long_content(self) -> None:
        """测试长文本消息回调。"""
        content = "A" * 5000
        body, signature = self._make_message_body(content=content)

        result = self.handler.handle_post(signature, self.timestamp, self.nonce, body)
        self.assertEqual(result["content"], content)

    def test_handle_post_missing_encrypt_tag(self) -> None:
        """测试请求体中缺少 Encrypt 字段时抛出 ValueError。"""
        body = "<xml><ToUserName><![CDATA[corpid]]></ToUserName></xml>"

        with self.assertRaises(ValueError) as ctx:
            self.handler.handle_post("sig", self.timestamp, self.nonce, body)
        self.assertIn("Encrypt", str(ctx.exception))

    def test_handle_post_return_format(self) -> None:
        """测试返回的字典包含所有必需的字段。"""
        body, signature = self._make_message_body()

        result = self.handler.handle_post(signature, self.timestamp, self.nonce, body)

        required_keys = {"from_user", "to_user", "msg_type", "content", "msg_id", "create_time"}
        self.assertEqual(set(result.keys()), required_keys)

    def test_handle_post_create_time_is_int(self) -> None:
        """测试 create_time 字段为整数类型。"""
        body, signature = self._make_message_body(create_time="1609459200")

        result = self.handler.handle_post(signature, self.timestamp, self.nonce, body)
        self.assertIsInstance(result["create_time"], int)
        self.assertEqual(result["create_time"], 1609459200)


class TestCallbackHandlerIntegration(unittest.TestCase):
    """测试 CallbackHandler 的集成场景。"""

    def setUp(self) -> None:
        """初始化测试环境。"""
        self.token = "QDG6eK"
        self.encoding_aes_key = _generate_encoding_aes_key()
        self.corpid = "wx5823bf96d3bd56c7"
        self.handler = CallbackHandler(self.token, self.encoding_aes_key, self.corpid)

    def test_get_and_post_use_same_credentials(self) -> None:
        """测试 GET 和 POST 使用相同的凭证配置。"""
        timestamp = "1409659588"
        nonce = "1372623149"

        # GET 验证
        echostr = encrypt_msg("echo", self.encoding_aes_key, self.corpid)
        get_sig = generate_signature(self.token, timestamp, nonce, echostr)
        get_result = self.handler.handle_get(get_sig, timestamp, nonce, echostr)
        self.assertEqual(get_result, "echo")

        # POST 消息
        inner_xml = (
            f"<xml>"
            f"<ToUserName><![CDATA[{self.corpid}]]></ToUserName>"
            f"<FromUserName><![CDATA[user1]]></FromUserName>"
            f"<CreateTime>1348831860</CreateTime>"
            f"<MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[message after get]]></Content>"
            f"<MsgId>1234567890123456</MsgId>"
            f"</xml>"
        )
        encrypt = encrypt_msg(inner_xml, self.encoding_aes_key, self.corpid)
        body = (
            f"<xml>"
            f"<ToUserName><![CDATA[{self.corpid}]]></ToUserName>"
            f"<AgentID>1</AgentID>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"</xml>"
        )
        post_sig = generate_signature(self.token, timestamp, nonce, encrypt)
        post_result = self.handler.handle_post(post_sig, timestamp, nonce, body)
        self.assertEqual(post_result["content"], "message after get")

    def test_different_handlers_isolated(self) -> None:
        """测试不同 handler 实例之间互相隔离。"""
        other_key = _generate_encoding_aes_key()
        other_corpid = "other_corpid"
        other_handler = CallbackHandler("other_token", other_key, other_corpid)

        # 用 handler1 加密的消息不能被 handler2 验证
        echostr = encrypt_msg("test", self.encoding_aes_key, self.corpid)
        sig = generate_signature(self.token, "123", "abc", echostr)

        # 其他 handler 应该签名验证失败（因为 token 不同）
        with self.assertRaises(ValueError):
            other_handler.handle_get(sig, "123", "abc", echostr)


if __name__ == "__main__":
    unittest.main()
