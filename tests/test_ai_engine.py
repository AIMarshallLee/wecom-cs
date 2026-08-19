"""AI 双引擎层测试

测试 FAQ 引擎、知识库管理、会话管理器、回复决策器和 LLM 引擎。
- FAQ 引擎：精确/模糊匹配、热重载
- 知识库管理：加载、搜索、增删
- 会话管理器：内存降级模式、消息管理
- 回复决策器：各种决策场景
- LLM 引擎：错误降级、成功响应、置信度解析
"""

import sys
import os
import json
import time
import asyncio
import tempfile
import shutil
import unittest
from unittest.mock import patch

# 将项目根目录加入 sys.path，使 app 包可导入
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import httpx
from app.faq_engine import FAQEngine
from app.knowledge_base import KnowledgeBase
from app.session_manager import SessionManager
from app.reply_controller import ReplyController
from app.llm_engine import LLMEngine

# 知识库路径
KNOWLEDGE_PATH = os.path.join(PROJECT_ROOT, "knowledge")
FAQ_FILE = os.path.join(KNOWLEDGE_PATH, "faq.json")


# ============================================================================
# Mock 工具类（用于 LLM 引擎测试）
# ============================================================================

class MockResponse:
    """模拟 httpx 响应对象"""

    def __init__(self, json_data: dict, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def json(self) -> dict:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://fake"),
                response=httpx.Response(self.status_code),
            )


class MockAsyncClient:
    """模拟 httpx.AsyncClient

    通过类属性 _response 和 _exception 控制行为，
    支持异步上下文管理器和 post 方法。
    """

    _response = None
    _exception = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, *args, **kwargs):
        if MockAsyncClient._exception is not None:
            raise MockAsyncClient._exception
        return MockAsyncClient._response


# ============================================================================
# FAQ 引擎测试
# ============================================================================

class TestFAQEngine(unittest.TestCase):
    """FAQ 引擎测试"""

    def setUp(self) -> None:
        """每个测试前创建 FAQ 引擎实例"""
        self.engine = FAQEngine(FAQ_FILE)

    def test_load_faqs(self) -> None:
        """测试 FAQ 数据加载"""
        self.assertGreater(len(self.engine.faqs), 0, "FAQ 列表不应为空")
        # 验证 FAQ 数据结构
        for faq in self.engine.faqs:
            self.assertIn("id", faq)
            self.assertIn("keywords", faq)
            self.assertIn("question", faq)
            self.assertIn("answer", faq)

    def test_exact_match_price(self) -> None:
        """测试精确匹配 - 价格关键词"""
        result = self.engine.exact_match("课程价格多少钱")
        self.assertIsNotNone(result, "应匹配到价格相关 FAQ")
        self.assertEqual(result["confidence"], 0.95)
        self.assertIn("answer", result)
        self.assertIn("faq_id", result)

    def test_exact_match_enrollment(self) -> None:
        """测试精确匹配 - 报名关键词"""
        result = self.engine.exact_match("我想报名课程")
        self.assertIsNotNone(result, "应匹配到报名相关 FAQ")
        self.assertEqual(result["confidence"], 0.95)

    def test_exact_match_no_match(self) -> None:
        """测试精确匹配 - 无匹配"""
        result = self.engine.exact_match("今天天气真好")
        self.assertIsNone(result, "不应匹配到任何 FAQ")

    def test_fuzzy_match(self) -> None:
        """测试模糊匹配 - 相似但不完全相同的关键词"""
        # "航海训练营啥时开始" 不包含任何精确关键词，
        # 但与问题 "航海和训练营什么时候开" 相似度高
        result = self.engine.fuzzy_match("航海训练营啥时开始")
        self.assertIsNotNone(result, "模糊匹配应找到结果")
        self.assertGreaterEqual(result["confidence"], 0.6)
        self.assertLess(result["confidence"], 0.95, "模糊匹配置信度应低于精确匹配")

    def test_fuzzy_match_no_match(self) -> None:
        """测试模糊匹配 - 无匹配"""
        result = self.engine.fuzzy_match("今天天气真好啊")
        self.assertIsNone(result, "不应模糊匹配到任何 FAQ")

    def test_search_exact_first(self) -> None:
        """测试 search 方法优先精确匹配"""
        result = self.engine.search("课程价格多少钱")
        self.assertIsNotNone(result)
        self.assertEqual(result["confidence"], 0.95, "应返回精确匹配置信度")

    def test_search_fuzzy_fallback(self) -> None:
        """测试 search 方法精确失败后模糊匹配"""
        result = self.engine.search("航海训练营啥时开始")
        self.assertIsNotNone(result, "应通过模糊匹配找到结果")
        self.assertGreaterEqual(result["confidence"], 0.6)

    def test_search_no_match(self) -> None:
        """测试 search 方法无匹配"""
        result = self.engine.search("今天天气真好啊")
        self.assertIsNone(result)

    def test_hot_reload(self) -> None:
        """测试热重载 - 文件修改后自动重新加载"""
        # 使用临时文件测试热重载
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            initial_data = {"faqs": [
                {"id": "1", "keywords": ["测试"], "question": "测试问题", "answer": "测试答案"}
            ]}
            json.dump(initial_data, tmp, ensure_ascii=False)
            tmp_path = tmp.name

        try:
            engine = FAQEngine(tmp_path)
            self.assertEqual(len(engine.faqs), 1)

            # 修改文件，添加新 FAQ
            time.sleep(0.1)  # 确保 mtime 变化
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"faqs": [
                    {"id": "1", "keywords": ["测试"], "question": "测试问题", "answer": "测试答案"},
                    {"id": "2", "keywords": ["新增"], "question": "新增问题", "answer": "新增答案"}
                ]}, f, ensure_ascii=False)

            # 调用方法触发热重载检查
            result = engine.exact_match("新增")
            self.assertIsNotNone(result, "热重载后应匹配到新增的 FAQ")
            self.assertEqual(len(engine.faqs), 2, "热重载后 FAQ 数量应为 2")
        finally:
            os.unlink(tmp_path)


# ============================================================================
# 知识库管理测试
# ============================================================================

class TestKnowledgeBase(unittest.TestCase):
    """知识库管理测试"""

    def setUp(self) -> None:
        self.kb = KnowledgeBase(KNOWLEDGE_PATH)

    def test_load_faq(self) -> None:
        """测试加载 FAQ"""
        faqs = self.kb.load_faq()
        self.assertGreater(len(faqs), 0, "FAQ 列表不应为空")
        self.assertGreaterEqual(len(faqs), 8, "至少应有 8 条 FAQ")
        # 验证数据结构
        for faq in faqs:
            self.assertIn("id", faq)
            self.assertIn("keywords", faq)
            self.assertIn("question", faq)
            self.assertIn("answer", faq)

    def test_load_documents(self) -> None:
        """测试加载文档"""
        docs = self.kb.load_documents()
        self.assertGreater(len(docs), 0, "文档列表不应为空")
        for doc in docs:
            self.assertIn("title", doc)
            self.assertIn("content", doc)
            self.assertTrue(doc["content"], "文档内容不应为空")

    def test_load_templates(self) -> None:
        """测试加载模板"""
        templates = self.kb.load_templates()
        self.assertIn("greeting", templates, "应包含 greeting 模板")
        self.assertIn("transfer_human", templates, "应包含 transfer_human 模板")
        self.assertIn("sensitive_reply", templates, "应包含 sensitive_reply 模板")
        # 模板内容不应为空
        for key, value in templates.items():
            self.assertTrue(value, f"模板 {key} 内容不应为空")

    def test_search_price(self) -> None:
        """测试搜索 - 价格相关"""
        self.kb.load_faq()
        results = self.kb.search("价格", top_k=3)
        self.assertGreater(len(results), 0, "应搜索到价格相关结果")
        # 验证结果结构
        for r in results:
            self.assertIn("source", r)
            self.assertIn("title", r)
            self.assertIn("content", r)
            self.assertIn("score", r)

    def test_search_enrollment(self) -> None:
        """测试搜索 - 报名相关"""
        self.kb.load_faq()
        results = self.kb.search("报名", top_k=3)
        self.assertGreater(len(results), 0, "应搜索到报名相关结果")
        # 第一个结果应是最相关的
        self.assertGreater(results[0]["score"], 0)

    def test_search_top_k_limit(self) -> None:
        """测试搜索 top_k 限制"""
        self.kb.load_faq()
        results = self.kb.search("课程", top_k=2)
        self.assertLessEqual(len(results), 2, "结果数量不应超过 top_k")

    def test_add_and_save_faq(self) -> None:
        """测试添加和保存 FAQ（使用临时副本）"""
        # 复制知识库到临时目录，避免修改原始文件
        tmp_dir = tempfile.mkdtemp()
        try:
            tmp_kb_path = os.path.join(tmp_dir, "knowledge_tmp")
            shutil.copytree(KNOWLEDGE_PATH, tmp_kb_path)
            kb = KnowledgeBase(tmp_kb_path)
            kb.load_faq()
            original_count = len(kb._faqs)

            # 添加新 FAQ
            new_id = kb.add_faq(
                keywords=["新课程", "新课"],
                question="有什么新课程",
                answer="我们即将推出新课程，敬请期待！"
            )
            self.assertEqual(len(kb._faqs), original_count + 1, "FAQ 数量应增加 1")

            # 验证新 ID 是递增的
            all_ids = [int(f["id"]) for f in kb._faqs]
            self.assertEqual(int(new_id), max(all_ids), "新 ID 应为最大值")

            # 保存并重新加载验证
            kb.save_faq()
            kb2 = KnowledgeBase(tmp_kb_path)
            kb2.load_faq()
            self.assertEqual(len(kb2._faqs), original_count + 1, "重新加载后 FAQ 数量应一致")

            # 验证新增的 FAQ 内容
            new_faq = [f for f in kb2._faqs if f["id"] == new_id][0]
            self.assertEqual(new_faq["question"], "有什么新课程")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================================
# 会话管理器测试
# ============================================================================

class TestSessionManager(unittest.TestCase):
    """会话管理器测试（内存降级模式）"""

    def setUp(self) -> None:
        """使用无效的 Redis 地址，确保降级到内存模式"""
        # 使用不存在的端口，确保 Redis 连接失败降级到内存模式
        self.manager = SessionManager(redis_url="redis://localhost:19999/0")

    def test_memory_fallback(self) -> None:
        """测试 Redis 不可用时降级到内存模式"""
        self.assertFalse(self.manager.use_redis, "Redis 不可用时应降级到内存模式")

    def test_add_and_get_message(self) -> None:
        """测试添加和获取消息"""
        user_id = "test_user_1"
        self.manager.clear_context(user_id)

        # 初始上下文为空
        context = self.manager.get_context(user_id)
        self.assertEqual(len(context), 0, "初始上下文应为空")

        # 添加消息
        self.manager.add_message(user_id, "user", "你好，课程多少钱")
        self.manager.add_message(user_id, "assistant", "课程价格是3980元")

        # 获取上下文
        context = self.manager.get_context(user_id)
        self.assertEqual(len(context), 2, "应有 2 条消息")
        self.assertEqual(context[0]["role"], "user")
        self.assertEqual(context[0]["content"], "你好，课程多少钱")
        self.assertEqual(context[1]["role"], "assistant")
        self.assertEqual(context[1]["content"], "课程价格是3980元")

    def test_clear_context(self) -> None:
        """测试清空会话"""
        user_id = "test_user_2"
        self.manager.add_message(user_id, "user", "测试消息")
        self.assertEqual(len(self.manager.get_context(user_id)), 1)

        self.manager.clear_context(user_id)
        self.assertEqual(len(self.manager.get_context(user_id)), 0, "清空后上下文应为空")

    def test_max_messages_limit(self) -> None:
        """测试上下文最多保留 10 条消息"""
        user_id = "test_user_3"
        self.manager.clear_context(user_id)

        # 添加 12 条消息
        for i in range(12):
            self.manager.add_message(user_id, "user", f"消息{i}")

        context = self.manager.get_context(user_id)
        self.assertEqual(len(context), 10, "应只保留最近 10 条消息")
        # 验证保留的是最后 10 条
        self.assertEqual(context[0]["content"], "消息2", "应从第3条开始保留（索引2）")
        self.assertEqual(context[-1]["content"], "消息11", "最后一条应为消息11")

    def test_multiple_users_isolation(self) -> None:
        """测试多用户会话隔离"""
        user1 = "user_a"
        user2 = "user_b"
        self.manager.clear_context(user1)
        self.manager.clear_context(user2)

        self.manager.add_message(user1, "user", "用户A的消息")
        self.manager.add_message(user2, "user", "用户B的消息")

        ctx1 = self.manager.get_context(user1)
        ctx2 = self.manager.get_context(user2)

        self.assertEqual(len(ctx1), 1)
        self.assertEqual(len(ctx2), 1)
        self.assertEqual(ctx1[0]["content"], "用户A的消息")
        self.assertEqual(ctx2[0]["content"], "用户B的消息")


# ============================================================================
# 回复决策器测试
# ============================================================================

class TestReplyController(unittest.TestCase):
    """回复决策器测试"""

    def setUp(self) -> None:
        self.controller = ReplyController()

    def test_refund_not_sensitive(self) -> None:
        """测试退款不再触发转人工 - FAQ有退款政策答案"""
        faq_result = {
            "answer": "3天无理由退款",
            "faq_id": 6,
            "confidence": 0.95,
        }
        decision = self.controller.decide(
            user_message="能退款吗",
            faq_result=faq_result,
            llm_result=None,
            confidence=0,
        )
        self.assertEqual(decision["mode"], "auto", "退款有FAQ匹配时应自动回复")
        self.assertIn("3天无理由退款", decision["content"])

    def test_sensitive_word_complaint(self) -> None:
        """测试敏感词检测 - 投诉 → 转人工"""
        decision = self.controller.decide(
            user_message="我要投诉你们",
            faq_result=None,
            llm_result=None,
            confidence=90,
        )
        self.assertEqual(decision["mode"], "human", "投诉应转人工")

    def test_sensitive_word_lawyer(self) -> None:
        """测试敏感词检测 - 律师 → 转人工"""
        decision = self.controller.decide(
            user_message="我要找律师",
            faq_result=None,
            llm_result=None,
            confidence=95,
        )
        self.assertEqual(decision["mode"], "human", "律师应转人工")

    def test_sensitive_word_priority_over_faq(self) -> None:
        """测试敏感词优先级高于 FAQ 匹配"""
        # FAQ 匹配相关，但消息包含敏感词"投诉"，应转人工
        faq_result = {
            "answer": "这是FAQ回复",
            "faq_id": "5",
            "confidence": 0.95,
        }
        decision = self.controller.decide(
            user_message="我要投诉你们",
            faq_result=faq_result,
            llm_result=None,
            confidence=90,
        )
        self.assertEqual(decision["mode"], "human", "敏感词优先级应高于 FAQ 匹配")

    def test_faq_exact_match_auto(self) -> None:
        """测试 FAQ 精确匹配 → 自动回复"""
        faq_result = {
            "answer": "课程价格是3980元",
            "faq_id": "1",
            "confidence": 0.95,
        }
        decision = self.controller.decide(
            user_message="课程多少钱",
            faq_result=faq_result,
            llm_result="LLM回复",
            confidence=90,
        )
        self.assertEqual(decision["mode"], "auto", "FAQ精确匹配应自动回复")
        self.assertEqual(decision["content"], "课程价格是3980元")
        self.assertIn("精确匹配", decision["reason"])

    def test_faq_fuzzy_match_auto(self) -> None:
        """测试 FAQ 模糊匹配（置信度≥0.80）→ 自动回复"""
        faq_result = {
            "answer": "课程在周末上课",
            "faq_id": "3",
            "confidence": 0.85,
        }
        decision = self.controller.decide(
            user_message="上课时间是什么时候",
            faq_result=faq_result,
            llm_result="LLM回复",
            confidence=90,
        )
        self.assertEqual(decision["mode"], "auto", "FAQ模糊匹配应自动回复")
        self.assertIn("模糊匹配", decision["reason"])

    def test_faq_low_confidence_fallback_to_llm(self) -> None:
        """测试 FAQ 置信度低于0.80时回退到 LLM 决策"""
        faq_result = {
            "answer": "某答案",
            "faq_id": "1",
            "confidence": 0.65,  # 低于 0.80
        }
        decision = self.controller.decide(
            user_message="某个问题",
            faq_result=faq_result,
            llm_result="LLM的回复",
            confidence=85,  # LLM 置信度高
        )
        self.assertEqual(decision["mode"], "auto", "应使用 LLM 自动回复")
        self.assertIn("LLM", decision["reason"])

    def test_llm_high_confidence_auto(self) -> None:
        """测试 LLM 高置信度（≥80）→ 自动回复"""
        decision = self.controller.decide(
            user_message="课程内容有哪些",
            faq_result=None,
            llm_result="课程包含理论和实操",
            confidence=85,
        )
        self.assertEqual(decision["mode"], "auto", "LLM高置信度应自动回复")
        self.assertEqual(decision["content"], "课程包含理论和实操")

    def test_llm_medium_confidence_suggest(self) -> None:
        """测试 LLM 中置信度（50-79）→ 建议回复"""
        decision = self.controller.decide(
            user_message="课程适合什么人",
            faq_result=None,
            llm_result="适合有基础的同学",
            confidence=65,
        )
        self.assertEqual(decision["mode"], "suggest", "LLM中置信度应建议回复")
        self.assertEqual(decision["content"], "适合有基础的同学")

    def test_llm_low_confidence_human(self) -> None:
        """测试 LLM 低置信度（<50）→ 转人工"""
        decision = self.controller.decide(
            user_message="一些复杂问题",
            faq_result=None,
            llm_result="我不太确定",
            confidence=30,
        )
        self.assertEqual(decision["mode"], "human", "LLM低置信度应转人工")

    def test_custom_sensitive_words(self) -> None:
        """测试自定义敏感词"""
        controller = ReplyController(sensitive_words=["自定义敏感词"])
        decision = controller.decide(
            user_message="这里包含自定义敏感词",
            faq_result=None,
            llm_result=None,
            confidence=90,
        )
        self.assertEqual(decision["mode"], "human", "自定义敏感词应转人工")

    def test_custom_threshold(self) -> None:
        """测试自定义阈值"""
        controller = ReplyController(auto_reply_threshold=90)
        # 置信度 85 低于自定义阈值 90，应建议回复
        decision = controller.decide(
            user_message="普通问题",
            faq_result=None,
            llm_result="回复内容",
            confidence=85,
        )
        self.assertEqual(decision["mode"], "suggest", "置信度低于自定义阈值应建议回复")

    def test_all_default_sensitive_words(self) -> None:
        """测试所有默认敏感词"""
        for word in ReplyController.DEFAULT_SENSITIVE_WORDS:
            decision = self.controller.decide(
                user_message=f"包含{word}的消息",
                faq_result=None,
                llm_result=None,
                confidence=95,
            )
            self.assertEqual(
                decision["mode"], "human",
                f"敏感词 '{word}' 应触发转人工"
            )


# ============================================================================
# LLM 引擎测试
# ============================================================================

class TestLLMEngine(unittest.TestCase):
    """LLM 引擎测试"""

    def setUp(self) -> None:
        """每个测试前重置 Mock 状态"""
        MockAsyncClient._response = None
        MockAsyncClient._exception = None
        self.engine = LLMEngine(
            provider="openai",
            api_url="http://fake-url/v1/chat/completions",
            api_key="test-key",
            model="gpt-4",
        )

    def test_init(self) -> None:
        """测试初始化"""
        self.assertEqual(self.engine.provider, "openai")
        self.assertEqual(self.engine.model, "gpt-4")
        self.assertEqual(self.engine.api_key, "test-key")

    def test_error_degradation_timeout(self) -> None:
        """测试 API 超时降级"""
        MockAsyncClient._exception = httpx.TimeoutException("请求超时")
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate([{"role": "user", "content": "你好"}])
            )
        self.assertEqual(content, LLMEngine.FALLBACK_REPLY, "超时应返回降级回复")
        self.assertEqual(confidence, 0.0, "降级回复置信度应为 0")

    def test_error_degradation_http_error(self) -> None:
        """测试 API HTTP 错误降级"""
        MockAsyncClient._exception = httpx.HTTPError("连接失败")
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate([{"role": "user", "content": "你好"}])
            )
        self.assertEqual(content, LLMEngine.FALLBACK_REPLY, "HTTP错误应返回降级回复")
        self.assertEqual(confidence, 0.0, "降级回复置信度应为 0")

    def test_error_degradation_generic_exception(self) -> None:
        """测试通用异常降级"""
        MockAsyncClient._exception = Exception("未知错误")
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate([{"role": "user", "content": "你好"}])
            )
        self.assertEqual(content, LLMEngine.FALLBACK_REPLY, "异常应返回降级回复")
        self.assertEqual(confidence, 0.0, "降级回复置信度应为 0")

    def test_successful_response_openai(self) -> None:
        """测试 OpenAI 格式成功响应"""
        MockAsyncClient._response = MockResponse({
            "choices": [
                {"message": {"content": "课程价格是3980元，包含3天线下实训。"}}
            ]
        })
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate([{"role": "user", "content": "价格多少"}])
            )
        self.assertIn("3980", content)
        self.assertGreater(confidence, 0, "成功响应置信度应大于 0")

    def test_successful_response_anthropic(self) -> None:
        """测试 Anthropic 格式成功响应"""
        engine = LLMEngine(
            provider="anthropic",
            api_url="http://fake-url/v1/messages",
            api_key="test-key",
            model="claude-3",
        )
        MockAsyncClient._response = MockResponse({
            "content": [{"type": "text", "text": "报名方式：扫码支付即可。"}]
        })
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                engine.generate([{"role": "user", "content": "怎么报名"}])
            )
        self.assertIn("报名", content)
        self.assertGreater(confidence, 0)

    def test_successful_response_qwen(self) -> None:
        """测试通义千问(Qwen)格式成功响应（OpenAI兼容）"""
        engine = LLMEngine(
            provider="qwen",
            api_url="http://fake-url/v1/chat/completions",
            api_key="test-key",
            model="qwen-max",
        )
        MockAsyncClient._response = MockResponse({
            "choices": [
                {"message": {"content": "上课时间是周末全天。"}}
            ]
        })
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                engine.generate([{"role": "user", "content": "上课时间"}])
            )
        self.assertIn("周末", content)
        self.assertGreater(confidence, 0)

    def test_generate_reply_with_self_confidence(self) -> None:
        """测试 generate_reply 解析自评置信度"""
        MockAsyncClient._response = MockResponse({
            "choices": [
                {"message": {"content": "课程价格是3980元，包含3天实训。[置信度:0.9]"}}
            ]
        })
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate_reply(
                    user_message="课程多少钱",
                    knowledge_context="课程价格3980元",
                    conversation_history=[],
                )
            )
        self.assertIn("3980", content)
        self.assertNotIn("置信度", content, "回复内容中不应包含置信度标记")
        self.assertAlmostEqual(confidence, 0.9, places=1, msg="应解析自评置信度 0.9")

    def test_generate_reply_with_history(self) -> None:
        """测试 generate_reply 注入对话历史"""
        MockAsyncClient._response = MockResponse({
            "choices": [
                {"message": {"content": "如前所述，价格是3980元。[置信度:0.85]"}}
            ]
        })
        history = [
            {"role": "user", "content": "课程叫什么"},
            {"role": "assistant", "content": "我们的课程是实战训练营"},
        ]
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate_reply(
                    user_message="价格多少",
                    knowledge_context="课程价格3980元",
                    conversation_history=history,
                )
            )
        self.assertIn("3980", content)
        self.assertAlmostEqual(confidence, 0.85, places=1)

    def test_generate_reply_error_degradation(self) -> None:
        """测试 generate_reply 错误降级"""
        MockAsyncClient._exception = httpx.TimeoutException("超时")
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate_reply(
                    user_message="你好",
                    knowledge_context="",
                    conversation_history=[],
                )
            )
        self.assertEqual(content, LLMEngine.FALLBACK_REPLY)
        self.assertEqual(confidence, 0.0)

    def test_confidence_heuristic_uncertainty(self) -> None:
        """测试不确定性标记降低置信度"""
        # 回复包含"不确定"标记
        MockAsyncClient._response = MockResponse({
            "choices": [
                {"message": {"content": "我不确定具体价格，建议联系人工客服。"}}
            ]
        })
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate([{"role": "user", "content": "价格"}])
            )
        self.assertIn("不确定", content)
        self.assertLessEqual(confidence, 0.5, "包含不确定性标记时置信度应较低")

    def test_empty_response_degradation(self) -> None:
        """测试空响应降级"""
        MockAsyncClient._response = MockResponse({
            "choices": [{"message": {"content": ""}}]
        })
        with patch("app.llm_engine.httpx.AsyncClient", MockAsyncClient):
            content, confidence = asyncio.run(
                self.engine.generate([{"role": "user", "content": "你好"}])
            )
        self.assertEqual(content, LLMEngine.FALLBACK_REPLY, "空响应应返回降级回复")
        self.assertEqual(confidence, 0.0)


# ============================================================================
# 集成测试
# ============================================================================

class TestIntegration(unittest.TestCase):
    """集成测试 - 模拟完整回复流程"""

    def setUp(self) -> None:
        self.faq_engine = FAQEngine(FAQ_FILE)
        self.kb = KnowledgeBase(KNOWLEDGE_PATH)
        self.kb.load_faq()
        self.kb.load_templates()
        self.controller = ReplyController()
        self.session = SessionManager(redis_url="redis://localhost:19999/0")

    def test_faq_auto_reply_flow(self) -> None:
        """测试 FAQ 自动回复完整流程"""
        user_id = "integration_user_1"
        self.session.clear_context(user_id)

        # 用户提问
        user_msg = "课程多少钱"
        self.session.add_message(user_id, "user", user_msg)

        # FAQ 引擎匹配
        faq_result = self.faq_engine.search(user_msg)

        # 决策
        decision = self.controller.decide(
            user_message=user_msg,
            faq_result=faq_result,
            llm_result=None,
            confidence=0,
        )

        self.assertEqual(decision["mode"], "auto")
        self.assertIn("3365", decision["content"])

        # 记录助手回复到会话
        self.session.add_message(user_id, "assistant", decision["content"])
        context = self.session.get_context(user_id)
        self.assertEqual(len(context), 2)

    def test_sensitive_transfer_flow(self) -> None:
        """测试敏感词转人工完整流程"""
        user_msg = "我要投诉你们"
        faq_result = self.faq_engine.search(user_msg)

        decision = self.controller.decide(
            user_message=user_msg,
            faq_result=faq_result,
            llm_result=None,
            confidence=95,
        )

        self.assertEqual(decision["mode"], "human")
        # 敏感词应优先于 FAQ 匹配
        self.assertIn("敏感词", decision["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
