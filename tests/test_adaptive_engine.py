"""AdaptiveEngine 单元测试

测试话术自适应引擎的核心功能：
- should_adapt 判断逻辑
- prompt 构建
- adapt_reply 改写流程
- 降级处理
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.utils import SQLiteDB
from app.user_profile_service import UserProfileService
from app.adaptive_engine import AdaptiveEngine


@pytest.fixture
def db():
    db = SQLiteDB(":memory:")
    db.init_db()
    yield db
    db.close()


@pytest.fixture
def profile_service(db):
    return UserProfileService(db)


@pytest.fixture
def mock_llm():
    """模拟 LLM 引擎"""
    llm = MagicMock()
    llm.generate_reply = AsyncMock(return_value=("改写后的回复", 0.85))
    return llm


@pytest.fixture
def engine(mock_llm, profile_service):
    return AdaptiveEngine(llm_engine=mock_llm, profile_service=profile_service)


class TestShouldAdapt:
    """should_adapt 判断逻辑测试"""

    def test_new_user_skip(self, engine, profile_service):
        """新用户（0条消息）不应该改写"""
        assert engine.should_adapt("user_new") is False

    def test_low_message_count_skip(self, engine, profile_service):
        """消息数不足3条不改写"""
        profile_service.record_interaction("user_low", "你好")
        profile_service.record_interaction("user_low", "在吗")
        assert engine.should_adapt("user_low") is False

    def test_engaged_user_with_summary(self, engine, profile_service):
        """有对话摘要的老用户应该改写"""
        for i in range(5):
            profile_service.record_interaction("user_eng", f"消息{i}")
        profile_service.update_conversation_summary("user_eng", "用户关注价格")
        assert engine.should_adapt("user_eng") is True

    def test_user_with_tags(self, engine, profile_service):
        """有朋友圈标签的用户应该改写"""
        for i in range(3):
            profile_service.record_interaction("user_tag", f"消息{i}")
        profile_service.update_moments_tags("user_tag", ["电商", "90后"])
        assert engine.should_adapt("user_tag") is True

    def test_user_with_style(self, engine, profile_service):
        """有偏好风格的用户应该改写"""
        for i in range(3):
            profile_service.record_interaction("user_style", f"消息{i}")
        profile_service.update_preferred_style("user_style", "direct")
        assert engine.should_adapt("user_style") is True

    def test_high_message_count_always_adapt(self, engine, profile_service):
        """5条以上消息总是改写"""
        for i in range(6):
            profile_service.record_interaction("user_active", f"消息{i}")
        assert engine.should_adapt("user_active") is True

    def test_no_llm_engine(self, profile_service):
        """没有 LLM 引擎不改写"""
        engine_no_llm = AdaptiveEngine(llm_engine=None, profile_service=profile_service)
        assert engine_no_llm.should_adapt("any_user") is False

    def test_no_profile_service(self, mock_llm):
        """没有画像服务不改写"""
        engine_no_ps = AdaptiveEngine(llm_engine=mock_llm, profile_service=None)
        assert engine_no_ps.should_adapt("any_user") is False


class TestBuildAdaptPrompt:
    """prompt 构建测试"""

    def test_prompt_contains_faq_answer(self, engine, profile_service):
        """prompt 包含 FAQ 原始答案"""
        for i in range(5):
            profile_service.record_interaction("user_p1", f"消息{i}")
        profile = profile_service.get_profile("user_p1")

        prompt = engine._build_adapt_prompt(
            "价格多少", "3365元/年", "价格问题", profile
        )
        assert "3365元/年" in prompt

    def test_prompt_contains_user_message(self, engine, profile_service):
        """prompt 包含用户原始消息"""
        for i in range(5):
            profile_service.record_interaction("user_p2", f"消息{i}")
        profile = profile_service.get_profile("user_p2")

        prompt = engine._build_adapt_prompt(
            "我想了解一下价格", "3365元/年", "价格", profile
        )
        assert "我想了解一下价格" in prompt

    def test_prompt_contains_summary(self, engine, profile_service):
        """prompt 包含对话摘要"""
        for i in range(5):
            profile_service.record_interaction("user_p3", f"消息{i}")
        profile_service.update_conversation_summary("user_p3", "用户觉得价格偏高")
        profile = profile_service.get_profile("user_p3")

        prompt = engine._build_adapt_prompt(
            "还有别的方案吗", "有体验卡", "体验卡", profile
        )
        assert "用户觉得价格偏高" in prompt

    def test_prompt_contains_tags(self, engine, profile_service):
        """prompt 包含朋友圈标签"""
        for i in range(3):
            profile_service.record_interaction("user_p4", f"消息{i}")
        profile_service.update_moments_tags("user_p4", ["电商创业者"])
        profile = profile_service.get_profile("user_p4")

        prompt = engine._build_adapt_prompt(
            "价格", "3365元", "价格", profile
        )
        assert "电商创业者" in prompt

    def test_prompt_contains_style(self, engine, profile_service):
        """prompt 包含偏好风格"""
        for i in range(3):
            profile_service.record_interaction("user_p5", f"消息{i}")
        profile_service.update_preferred_style("user_p5", "gentle")
        profile = profile_service.get_profile("user_p5")

        prompt = engine._build_adapt_prompt(
            "价格", "3365元", "价格", profile
        )
        assert "gentle" in prompt

    def test_prompt_contains_stage_instruction(self, engine, profile_service):
        """prompt 包含转化阶段指令"""
        profile_service.record_interaction("user_p6", "价格多少钱")  # triggers interested
        profile = profile_service.get_profile("user_p6")

        prompt = engine._build_adapt_prompt(
            "价格", "3365元", "价格", profile
        )
        assert "interested" in prompt
        assert "转化阶段" in prompt


class TestAdaptReply:
    """adapt_reply 改写流程测试"""

    def test_new_user_returns_original(self, engine, profile_service):
        """新用户直接返回原始答案"""
        result = asyncio.run(
            engine.adapt_reply("user_new", "价格", "3365元/年", "价格")
        )
        assert result == "3365元/年"
        # LLM 不应该被调用
        engine.llm_engine.generate_reply.assert_not_called()

    def test_old_user_returns_adapted(self, engine, profile_service, mock_llm):
        """老用户返回 LLM 改写后的回复"""
        for i in range(5):
            profile_service.record_interaction("user_old", f"消息{i}")
        profile_service.update_conversation_summary("user_old", "关注价格")

        result = asyncio.run(
            engine.adapt_reply("user_old", "价格多少", "3365元/年", "价格")
        )
        assert result == "改写后的回复"
        mock_llm.generate_reply.assert_called_once()

    def test_llm_failure_returns_original(self, profile_service):
        """LLM 异常时返回原始答案"""
        failing_llm = MagicMock()
        failing_llm.generate_reply = AsyncMock(side_effect=Exception("API error"))
        engine_fail = AdaptiveEngine(
            llm_engine=failing_llm, profile_service=profile_service
        )

        for i in range(5):
            profile_service.record_interaction("user_fail", f"消息{i}")
        profile_service.update_conversation_summary("user_fail", "关注价格")

        result = asyncio.run(
            engine_fail.adapt_reply("user_fail", "价格", "3365元/年", "价格")
        )
        assert result == "3365元/年"

    def test_empty_llm_response_returns_original(self, profile_service):
        """LLM 返回空时返回原始答案"""
        empty_llm = MagicMock()
        empty_llm.generate_reply = AsyncMock(return_value=("", 0.0))
        engine_empty = AdaptiveEngine(
            llm_engine=empty_llm, profile_service=profile_service
        )

        for i in range(5):
            profile_service.record_interaction("user_empty", f"消息{i}")
        profile_service.update_conversation_summary("user_empty", "关注价格")

        result = asyncio.run(
            engine_empty.adapt_reply("user_empty", "价格", "3365元/年", "价格")
        )
        assert result == "3365元/年"

    def test_long_response_truncated(self, profile_service):
        """超长回复被截断"""
        long_reply = "改写后的回复" + "x" * 600
        long_llm = MagicMock()
        long_llm.generate_reply = AsyncMock(return_value=(long_reply, 0.85))
        engine_long = AdaptiveEngine(
            llm_engine=long_llm, profile_service=profile_service
        )

        for i in range(5):
            profile_service.record_interaction("user_long", f"消息{i}")
        profile_service.update_conversation_summary("user_long", "关注价格")

        result = asyncio.run(
            engine_long.adapt_reply("user_long", "价格", "3365元/年", "价格")
        )
        assert len(result) <= 500

    def test_no_llm_returns_original(self, profile_service):
        """没有 LLM 引擎时返回原始答案"""
        engine_no_llm = AdaptiveEngine(
            llm_engine=None, profile_service=profile_service
        )
        result = asyncio.run(
            engine_no_llm.adapt_reply("any", "价格", "3365元/年", "价格")
        )
        assert result == "3365元/年"
