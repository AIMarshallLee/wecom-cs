"""
企业微信客服系统 - 集成测试

测试完整的消息处理流程：
- 工具函数测试（配置加载、SQLite、消息格式化）
- 消息处理流程测试（FAQ匹配、LLM处理、敏感词转人工、统计更新、会话管理）
- 回调验证测试（URL验证、接收消息、健康检查）
- 管理后台 API 测试（统计、FAQ CRUD、会话管理）

运行方式：
    cd 项目根目录
    python3 -m unittest discover -s tests -v
"""
import os
import sys
import json
import unittest
import tempfile
from unittest.mock import AsyncMock
from datetime import date

# 将项目根目录加入 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.utils import load_config, setup_logging, SQLiteDB, format_message
from app.main import (
    Application,
    verify_signature,
    contains_sensitive,
    decrypt_message,
    encrypt_message,
    _extract_encrypt,
    HAS_CRYPTO_LIB,
    CONFIG_PATH,
)
from fastapi.testclient import TestClient


# ============================================================
# 工具函数测试
# ============================================================

class TestUtils(unittest.TestCase):
    """测试 app.utils 工具函数"""

    def test_load_config(self):
        """测试配置加载"""
        config = load_config(CONFIG_PATH)
        self.assertIn("wecom", config)
        self.assertIn("llm", config)
        self.assertIn("redis", config)
        self.assertIn("server", config)
        self.assertIn("reply", config)
        self.assertIn("paths", config)
        self.assertIn("db", config)

    def test_setup_logging(self):
        """测试日志配置"""
        logger = setup_logging("DEBUG")
        self.assertEqual(logger.level, 10)  # DEBUG = 10
        self.assertTrue(len(logger.handlers) > 0)
        # 重复调用不应重复添加 handler
        logger2 = setup_logging("INFO")
        self.assertEqual(logger, logger2)
        self.assertEqual(len(logger2.handlers), len(logger.handlers))

    def test_sqlite_db(self):
        """测试 SQLite 数据库封装"""
        db = SQLiteDB(":memory:")
        db.init_db()

        # 插入消息
        last_id = db.execute(
            "INSERT INTO messages (user_id, content, reply, mode) "
            "VALUES (?, ?, ?, ?)",
            ("user1", "你好", "您好", "auto"),
        )
        self.assertIsNotNone(last_id)

        # 查询消息
        rows = db.query("SELECT * FROM messages WHERE user_id = ?", ("user1",))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "你好")
        self.assertEqual(rows[0]["reply"], "您好")
        self.assertEqual(rows[0]["mode"], "auto")

        # 统计表
        today = date.today().isoformat()
        db.execute(
            "INSERT INTO stats (date, total_messages, auto_replies, "
            "human_replies) VALUES (?, ?, ?, ?)",
            (today, 1, 1, 0),
        )
        stats = db.query("SELECT * FROM stats WHERE date = ?", (today,))
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["total_messages"], 1)

        db.close()

    def test_format_message(self):
        """测试消息格式化"""
        # 去除首尾空白
        self.assertEqual(format_message("  hello  "), "hello")
        # 去除多余空行
        self.assertEqual(format_message("line1\n\n\nline2"), "line1\nline2")
        # 截断超长内容
        result = format_message("a" * 100, max_length=10)
        self.assertEqual(len(result), 13)  # 10 + "..."
        self.assertTrue(result.endswith("..."))
        # 空内容
        self.assertEqual(format_message(""), "")
        self.assertEqual(format_message(None), "")  # type: ignore


class TestCryptoFunctions(unittest.TestCase):
    """测试加解密工具函数"""

    def test_verify_signature(self):
        """测试签名计算"""
        sig = verify_signature("token", "123456", "nonce", "encrypt")
        self.assertEqual(len(sig), 40)  # SHA1 = 40 位十六进制

    def test_contains_sensitive(self):
        """测试敏感词检测"""
        self.assertTrue(contains_sensitive("我要投诉"))
        self.assertTrue(contains_sensitive("请转人工"))
        self.assertTrue(contains_sensitive("退款赔偿"))
        self.assertFalse(contains_sensitive("你好"))
        self.assertFalse(contains_sensitive(""))
        self.assertFalse(contains_sensitive("营业时间"))

    @unittest.skipUnless(HAS_CRYPTO_LIB, "需要 pycryptodome")
    def test_encrypt_decrypt_round_trip(self):
        """测试加密/解密往返"""
        # 43 字符的 EncodingAESKey（Base64 编码，解码后 32 字节）
        encoding_aes_key = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
        corp_id = "test_corp_id"
        token = "test_token"
        timestamp = "1234567890"
        nonce = "test_nonce"
        message = "你好，世界！Hello World!"

        # 加密
        xml = encrypt_message(
            message, encoding_aes_key, corp_id, token, timestamp, nonce
        )
        self.assertTrue(xml)
        self.assertIn("<Encrypt>", xml)
        self.assertIn("<MsgSignature>", xml)

        # 提取加密内容
        encrypt = _extract_encrypt(xml)
        self.assertIsNotNone(encrypt)

        # 解密
        decrypted = decrypt_message(encrypt, encoding_aes_key, corp_id)
        self.assertIsNotNone(decrypted)
        self.assertEqual(decrypted, message)


# ============================================================
# 消息处理流程测试
# ============================================================

class TestMessageProcessing(unittest.IsolatedAsyncioTestCase):
    """测试消息处理核心流程"""

    @classmethod
    def setUpClass(cls):
        cls.config = load_config(CONFIG_PATH)
        cls.config["db"] = {"path": ":memory:"}
        # 测试环境：放开安全限制（Phase 3 + 全天 + 无延迟）
        cls.config["safety"] = {
            "enabled": True,
            "work_hours": {"start": 0, "end": 24},
            "rollout": {"phase": 3},
            "human_simulate": {"min_delay": 0, "max_delay": 0, "typo_probability": 0},
            "rate_limit": {"max_per_hour": 9999, "max_per_day": 9999, "min_interval_seconds": 0},
        }

    def setUp(self):
        self.app = Application(self.config)

    def tearDown(self):
        self.app.shutdown()

    async def test_faq_match_flow(self):
        """测试：接收消息 -> FAQ匹配 -> 回复"""
        message = {
            "user_id": "test_faq_user",
            "content": "请问营业时间是几点",
            "msg_type": "text",
        }
        await self.app.process_message(message)

        # 验证消息已记录
        rows = self.app.db.query(
            "SELECT * FROM messages WHERE user_id = ?", ("test_faq_user",)
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode"], "faq")
        self.assertIn("营业时间", rows[0]["reply"])
        self.assertEqual(rows[0]["content"], "请问营业时间是几点")

    async def test_llm_flow(self):
        """测试：接收消息 -> LLM处理 -> 回复"""
        # 注入模拟 LLM 引擎（高置信度回复）
        mock_llm = AsyncMock()
        # generate_reply 返回 (content, confidence) 元组，confidence 为 0-1
        mock_llm.generate_reply.return_value = (
            "从前有座山，山上有座庙，庙里有个老和尚...",
            0.90,
        )
        self.app.llm_engine = mock_llm

        message = {
            "user_id": "test_llm_user",
            "content": "给我讲个笑话",
            "msg_type": "text",
        }
        await self.app.process_message(message)

        rows = self.app.db.query(
            "SELECT * FROM messages WHERE user_id = ?", ("test_llm_user",)
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode"], "llm")
        self.assertIn("庙", rows[0]["reply"])
        # 验证 LLM 被调用
        mock_llm.generate_reply.assert_called_once()

    async def test_sensitive_transfer_to_human(self):
        """测试：敏感消息 -> 转人工"""
        message = {
            "user_id": "test_sensitive_user",
            "content": "我要投诉你们的服务质量",
            "msg_type": "text",
        }
        await self.app.process_message(message)

        rows = self.app.db.query(
            "SELECT * FROM messages WHERE user_id = ?",
            ("test_sensitive_user",),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mode"], "human")
        self.assertIn("负责人", rows[0]["reply"])

    async def test_low_confidence_transfer_to_human(self):
        """测试：低置信度 -> 转人工"""
        # 注入模拟 LLM 引擎（低置信度回复）
        mock_llm = AsyncMock()
        # generate_reply 返回 (content, confidence) 元组，confidence 0.3 → 30
        mock_llm.generate_reply.return_value = (
            "我不太确定...",
            0.30,
        )
        self.app.llm_engine = mock_llm

        message = {
            "user_id": "test_low_conf_user",
            "content": "某个随机问题",
            "msg_type": "text",
        }
        await self.app.process_message(message)

        rows = self.app.db.query(
            "SELECT * FROM messages WHERE user_id = ?",
            ("test_low_conf_user",),
        )
        self.assertEqual(rows[0]["mode"], "human")

    async def test_stats_update(self):
        """测试：统计信息更新"""
        # FAQ 自动回复
        await self.app.process_message({
            "user_id": "user_a", "content": "营业时间", "msg_type": "text"
        })
        # 转人工
        await self.app.process_message({
            "user_id": "user_b", "content": "我要投诉", "msg_type": "text"
        })

        stats = self.app.get_stats()
        self.assertEqual(stats["total_messages"], 2)
        self.assertEqual(stats["auto_replies"], 1)
        self.assertEqual(stats["human_replies"], 1)
        self.assertEqual(stats["auto_reply_rate"], 50.0)

    async def test_session_context_recording(self):
        """测试：会话上下文记录"""
        user_id = "test_ctx_user"
        await self.app.process_message({
            "user_id": user_id, "content": "营业时间", "msg_type": "text"
        })
        context = await self.app._get_session_context(user_id)
        # 应至少包含 user + assistant 两条消息
        self.assertGreaterEqual(len(context), 2)
        self.assertEqual(context[0]["role"], "user")
        self.assertEqual(context[1]["role"], "assistant")

    async def test_session_history(self):
        """测试：会话历史查询"""
        user_id = "test_history_user"
        await self.app.process_message({
            "user_id": user_id, "content": "营业时间", "msg_type": "text"
        })
        history = self.app.get_session_history(user_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "营业时间")

    async def test_clear_session(self):
        """测试：清空会话上下文"""
        user_id = "test_clear_user"
        await self.app.process_message({
            "user_id": user_id, "content": "营业时间", "msg_type": "text"
        })
        # 确认有上下文
        context = await self.app._get_session_context(user_id)
        self.assertGreater(len(context), 0)
        # 清空
        self.app.clear_session(user_id)
        context_after = await self.app._get_session_context(user_id)
        self.assertEqual(len(context_after), 0)


# ============================================================
# 回调验证测试（HTTP 端点）
# ============================================================

class TestCallbackAPI(unittest.TestCase):
    """测试企业微信回调 HTTP 端点"""

    @classmethod
    def setUpClass(cls):
        cls.config = load_config(CONFIG_PATH)
        cls.config["db"] = {"path": ":memory:"}
        cls.token = cls.config.get("wecom", {}).get("token", "")

    def setUp(self):
        self.application = Application(self.config)
        from app.main import app as fastapi_app
        fastapi_app.state.application = self.application
        self.client = TestClient(fastapi_app)

    def tearDown(self):
        self.application.shutdown()

    def test_health_check(self):
        """测试健康检查端点"""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "wecom-cs")

    def test_callback_url_verify(self):
        """测试 WeCom URL 验证（GET 回调）"""
        timestamp = "1234567890"
        nonce = "test_nonce"
        echostr = "test_echostr"
        # 计算签名
        sig = verify_signature(self.token, timestamp, nonce, echostr)

        response = self.client.get(
            "/wecom/callback",
            params={
                "msg_signature": sig,
                "timestamp": timestamp,
                "nonce": nonce,
                "echostr": echostr,
            },
        )
        self.assertEqual(response.status_code, 200)
        # 降级模式：无法解密时返回原始 echostr
        self.assertEqual(response.text, echostr)

    def test_callback_url_verify_bad_signature(self):
        """测试 URL 验证 - 错误签名"""
        response = self.client.get(
            "/wecom/callback",
            params={
                "msg_signature": "invalid_signature",
                "timestamp": "1234567890",
                "nonce": "test_nonce",
                "echostr": "test_echostr",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_callback_receive_message(self):
        """测试接收消息（POST 回调）"""
        xml_body = (
            "<xml>"
            "<ToUserName><![CDATA[corp_id]]></ToUserName>"
            "<FromUserName><![CDATA[test_callback_user]]></FromUserName>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[营业时间]]></Content>"
            "<MsgId>123456789</MsgId>"
            "<CreateTime>1234567890</CreateTime>"
            "</xml>"
        )
        response = self.client.post(
            "/wecom/callback",
            params={
                "msg_signature": "any",
                "timestamp": "1234567890",
                "nonce": "test_nonce",
            },
            content=xml_body,
            headers={"Content-Type": "application/xml"},
        )
        # 企业微信要求 5 秒内返回 success
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "success")


# ============================================================
# 管理后台 API 测试
# ============================================================

class TestAdminAPI(unittest.TestCase):
    """测试管理后台 API 端点"""

    @classmethod
    def setUpClass(cls):
        cls.config = load_config(CONFIG_PATH)
        cls.config["db"] = {"path": ":memory:"}
        # 测试环境：放开安全限制（Phase 3 + 全天 + 无延迟）
        cls.config["safety"] = {
            "enabled": True,
            "work_hours": {"start": 0, "end": 24},
            "rollout": {"phase": 3},
            "human_simulate": {"min_delay": 0, "max_delay": 0, "typo_probability": 0},
            "rate_limit": {"max_per_hour": 9999, "max_per_day": 9999, "min_interval_seconds": 0},
        }
        # 使用临时 FAQ 文件，避免修改真实文件
        cls._temp_faq_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        orig_faq = os.path.join(PROJECT_ROOT, "knowledge", "faq.json")
        with open(orig_faq, "r", encoding="utf-8") as f:
            faq_data = json.load(f)
        json.dump(faq_data, cls._temp_faq_file, ensure_ascii=False)
        cls._temp_faq_file.close()
        cls.config.setdefault("paths", {})["faq_file"] = cls._temp_faq_file.name

    @classmethod
    def tearDownClass(cls):
        temp_path = getattr(cls, "_temp_faq_file", None)
        if temp_path and os.path.exists(temp_path.name):
            os.unlink(temp_path.name)

    def setUp(self):
        self.application = Application(self.config)
        from app.main import app as fastapi_app
        fastapi_app.state.application = self.application
        self.client = TestClient(fastapi_app)

    def tearDown(self):
        self.application.shutdown()

    def test_get_stats(self):
        """测试获取统计信息"""
        response = self.client.get("/admin/stats")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("total_messages", data)
        self.assertIn("auto_replies", data)
        self.assertIn("human_replies", data)
        self.assertIn("auto_reply_rate", data)
        self.assertEqual(data["total_messages"], 0)

    def test_get_faqs(self):
        """测试获取所有 FAQ"""
        response = self.client.get("/admin/faqs")
        self.assertEqual(response.status_code, 200)
        faqs = response.json()
        self.assertIsInstance(faqs, list)
        self.assertGreater(len(faqs), 0)
        self.assertIn("keywords", faqs[0])
        self.assertIn("answer", faqs[0])

    def test_add_faq(self):
        """测试添加 FAQ"""
        response = self.client.post(
            "/admin/faqs",
            json={
                "keywords": ["测试关键词", "test"],
                "question": "测试问题",
                "answer": "这是测试回答",
                "confidence": 90,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("id", data)
        self.assertEqual(data["answer"], "这是测试回答")
        self.assertIn("测试关键词", data["keywords"])

    def test_update_faq(self):
        """测试更新 FAQ"""
        # 先添加
        add_resp = self.client.post(
            "/admin/faqs",
            json={
                "keywords": ["更新前"],
                "question": "更新前问题",
                "answer": "更新前回答",
                "confidence": 80,
            },
        )
        faq_id = add_resp.json()["id"]
        # 更新
        response = self.client.put(
            f"/admin/faqs/{faq_id}",
            json={"answer": "更新后回答", "confidence": 95},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["answer"], "更新后回答")
        self.assertEqual(data["confidence"], 95)
        self.assertEqual(data["id"], faq_id)

    def test_update_nonexistent_faq(self):
        """测试更新不存在的 FAQ"""
        response = self.client.put(
            "/admin/faqs/99999",
            json={"answer": "不存在"},
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_faq(self):
        """测试删除 FAQ"""
        # 先添加
        add_resp = self.client.post(
            "/admin/faqs",
            json={
                "keywords": ["删除测试"],
                "question": "删除问题",
                "answer": "删除回答",
                "confidence": 80,
            },
        )
        faq_id = add_resp.json()["id"]
        # 删除
        response = self.client.delete(f"/admin/faqs/{faq_id}")
        self.assertEqual(response.status_code, 200)
        # 确认已删除
        faqs = self.client.get("/admin/faqs").json()
        ids = [f["id"] for f in faqs]
        self.assertNotIn(faq_id, ids)

    def test_delete_nonexistent_faq(self):
        """测试删除不存在的 FAQ"""
        response = self.client.delete("/admin/faqs/99999")
        self.assertEqual(response.status_code, 404)

    def test_get_session_history(self):
        """测试查看用户会话历史"""
        import asyncio
        # 先处理一条消息
        asyncio.run(self.application.process_message({
            "user_id": "admin_test_user",
            "content": "营业时间",
            "msg_type": "text",
        }))
        response = self.client.get("/admin/sessions/admin_test_user")
        self.assertEqual(response.status_code, 200)
        sessions = response.json()
        self.assertIsInstance(sessions, list)
        self.assertGreater(len(sessions), 0)
        self.assertEqual(sessions[0]["content"], "营业时间")

    def test_clear_session(self):
        """测试清空用户会话"""
        response = self.client.post("/admin/sessions/test_user/clear")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

    def test_admin_stats_after_processing(self):
        """测试消息处理后统计更新"""
        import asyncio
        # 处理 FAQ 消息
        asyncio.run(self.application.process_message({
            "user_id": "stats_test_user",
            "content": "营业时间",
            "msg_type": "text",
        }))
        response = self.client.get("/admin/stats")
        data = response.json()
        self.assertEqual(data["total_messages"], 1)
        self.assertEqual(data["auto_replies"], 1)
        self.assertEqual(data["human_replies"], 0)


if __name__ == "__main__":
    unittest.main()
