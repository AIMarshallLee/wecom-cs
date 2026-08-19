"""MomentsAnalyzer 单元测试

测试朋友圈画像分析器的核心功能：
- prompt 构建
- 标签提取
- 全流程分析
- 重新分析判断
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.utils import SQLiteDB
from app.user_profile_service import UserProfileService
from app.moments_analyzer import MomentsAnalyzer, MomentsReader


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
    llm = MagicMock()
    llm.generate_reply = AsyncMock(return_value=("电商创业,90后,健身,中等消费", 0.85))
    return llm


@pytest.fixture
def analyzer(mock_llm, profile_service):
    return MomentsAnalyzer(llm_engine=mock_llm, profile_service=profile_service)


class TestBuildAnalysisPrompt:
    """prompt 构建测试"""

    def test_prompt_contains_moments_text(self, analyzer):
        """prompt 包含朋友圈文本"""
        prompt = analyzer._build_analysis_prompt("今天去健身了，电商生意不错")
        assert "今天去健身了" in prompt

    def test_prompt_contains_categories(self, analyzer):
        """prompt 包含标签类别"""
        prompt = analyzer._build_analysis_prompt("一些朋友圈内容")
        assert "职业" in prompt
        assert "兴趣" in prompt
        assert "消费力" in prompt

    def test_prompt_truncates_long_text(self, analyzer):
        """超长文本被截断"""
        long_text = "x" * 5000
        prompt = analyzer._build_analysis_prompt(long_text)
        # 原始 5000 字符应被截断到 MAX_MOMENTS_LENGTH
        assert len(prompt) < 6000  # prompt 总长度不应太大


class TestExtractTags:
    """标签提取测试"""

    def test_extract_tags_success(self, analyzer, mock_llm):
        """成功提取标签"""
        tags = asyncio.run(
            analyzer.extract_tags("今天去健身了，做电商的，90后，喜欢旅行")
        )
        assert "电商创业" in tags
        assert "90后" in tags
        assert "健身" in tags

    def test_extract_tags_english_comma(self, mock_llm, profile_service):
        """LLM 返回英文逗号分隔的标签"""
        llm_en = MagicMock()
        llm_en.generate_reply = AsyncMock(return_value=("电商,90后,健身,中等消费", 0.85))
        analyzer_en = MomentsAnalyzer(llm_engine=llm_en, profile_service=profile_service)

        tags = asyncio.run(
            analyzer_en.extract_tags("这是一段足够长的朋友圈内容用于分析测试，超过二十个字了")
        )
        assert "电商" in tags
        assert "90后" in tags

    def test_extract_tags_empty_response(self, mock_llm, profile_service):
        """LLM 返回空时返回空列表"""
        llm_empty = MagicMock()
        llm_empty.generate_reply = AsyncMock(return_value=("", 0.0))
        analyzer_empty = MomentsAnalyzer(llm_engine=llm_empty, profile_service=profile_service)

        tags = asyncio.run(
            analyzer_empty.extract_tags("一些朋友圈内容足够长用于分析")
        )
        assert tags == []

    def test_extract_tags_short_text(self, analyzer, mock_llm):
        """文本太短不分析"""
        tags = asyncio.run(
            analyzer.extract_tags("短")
        )
        assert tags == []
        mock_llm.generate_reply.assert_not_called()

    def test_extract_tags_no_llm(self, profile_service):
        """没有 LLM 引擎返回空列表"""
        analyzer_no_llm = MomentsAnalyzer(llm_engine=None, profile_service=profile_service)
        tags = asyncio.run(
            analyzer_no_llm.extract_tags("一些足够长的朋友圈内容用于测试")
        )
        assert tags == []

    def test_extract_tags_dedup(self, mock_llm, profile_service):
        """重复标签去重"""
        llm_dup = MagicMock()
        llm_dup.generate_reply = AsyncMock(return_value=("电商,电商,健身,健身,90后", 0.85))
        analyzer_dup = MomentsAnalyzer(llm_engine=llm_dup, profile_service=profile_service)

        tags = asyncio.run(
            analyzer_dup.extract_tags("这是一段足够长的朋友圈内容用于测试去重功能，超过二十个字了")
        )
        assert tags.count("电商") == 1
        assert tags.count("健身") == 1

    def test_extract_tags_max_8(self, mock_llm, profile_service):
        """标签最多8个"""
        llm_many = MagicMock()
        llm_many.generate_reply = AsyncMock(return_value=(
            "标签1,标签2,标签3,标签4,标签5,标签6,标签7,标签8,标签9,标签10", 0.85
        ))
        analyzer_many = MomentsAnalyzer(llm_engine=llm_many, profile_service=profile_service)

        tags = asyncio.run(
            analyzer_many.extract_tags("这是一段足够长的朋友圈内容用于测试最多标签数量限制，超过二十字")
        )
        assert len(tags) <= 8

    def test_extract_tags_filter_long_tags(self, mock_llm, profile_service):
        """超过6个字的标签被过滤"""
        llm_long = MagicMock()
        llm_long.generate_reply = AsyncMock(return_value=(
            "电商,这是一个超长的标签应该被过滤掉,90后,健身", 0.85
        ))
        analyzer_long = MomentsAnalyzer(llm_engine=llm_long, profile_service=profile_service)

        tags = asyncio.run(
            analyzer_long.extract_tags("这是一段足够长的朋友圈内容用于测试标签过滤功能，超过二十字")
        )
        assert "电商" in tags
        assert "90后" in tags
        assert "这是一个超长的标签应该被过滤掉" not in tags


class TestAnalyzeUser:
    """全流程分析测试"""

    def test_analyze_with_text(self, analyzer, profile_service, mock_llm):
        """直接传入朋友圈文本进行分析"""
        moments = "今天健身打卡，做电商生意五年了，90后创业者的日常"
        tags = asyncio.run(
            analyzer.analyze_user("user_m1", moments_text=moments)
        )
        assert len(tags) > 0

        # 验证标签已存入画像
        profile = profile_service.get_profile("user_m1")
        assert profile["moments_tags"] == tags
        assert profile["moments_analyzed_at"] > 0

    def test_analyze_no_text_no_name(self, analyzer):
        """没有文本和昵称时返回空"""
        tags = asyncio.run(
            analyzer.analyze_user("user_m2")
        )
        assert tags == []

    def test_analyze_empty_text(self, analyzer):
        """空文本返回空"""
        tags = asyncio.run(
            analyzer.analyze_user("user_m3", moments_text="")
        )
        assert tags == []

    def test_analyze_llm_failure(self, profile_service):
        """LLM 异常时返回空标签但不崩溃"""
        failing_llm = MagicMock()
        failing_llm.generate_reply = AsyncMock(side_effect=Exception("API error"))
        analyzer_fail = MomentsAnalyzer(
            llm_engine=failing_llm, profile_service=profile_service
        )

        tags = asyncio.run(
            analyzer_fail.analyze_user("user_m4", moments_text="今天健身了做电商的")
        )
        assert tags == []

    def test_analyze_no_profile_service(self, mock_llm):
        """没有画像服务时不崩溃"""
        analyzer_no_ps = MomentsAnalyzer(llm_engine=mock_llm, profile_service=None)
        tags = asyncio.run(
            analyzer_no_ps.analyze_user("user_m5", moments_text="今天健身了做电商的，九零后创业者的日常朋友圈分享")
        )
        # 标签仍然提取成功，只是不存储
        assert len(tags) > 0


class TestNeedsReanalysis:
    """重新分析判断测试"""

    def test_never_analyzed(self, analyzer, profile_service):
        """从未分析过的用户需要分析"""
        profile_service.record_interaction("user_r1", "你好")
        assert analyzer.needs_reanalysis("user_r1") is True

    def test_recently_analyzed(self, analyzer, profile_service):
        """刚分析过的用户不需要重新分析"""
        profile_service.record_interaction("user_r2", "你好")
        profile_service.update_moments_tags("user_r2", ["电商"])
        assert analyzer.needs_reanalysis("user_r2") is False

    def test_old_analysis_needs_refresh(self, analyzer, profile_service):
        """超过间隔的分析需要刷新"""
        profile_service.record_interaction("user_r3", "你好")
        profile_service.update_moments_tags("user_r3", ["旧标签"])

        # 手动把 moments_analyzed_at 设为 8 天前
        profile = profile_service.get_profile("user_r3")
        profile["moments_analyzed_at"] = time.time() - 8 * 24 * 3600  # 8天前
        profile_service.save_profile("user_r3", profile)

        assert analyzer.needs_reanalysis("user_r3", interval_hours=168) is True

    def test_no_profile_service(self, mock_llm):
        """没有画像服务时返回 False"""
        analyzer_no_ps = MomentsAnalyzer(llm_engine=mock_llm, profile_service=None)
        assert analyzer_no_ps.needs_reanalysis("any_user") is False


class TestMomentsReader:
    """MomentsReader 测试"""

    def test_read_moments_from_text(self):
        """从文本提取朋友圈内容"""
        reader = MomentsReader()
        lines = reader.read_moments_from_text("第一行\n第二行\n\n第三行")
        assert len(lines) == 3
        assert "第一行" in lines
        assert "第二行" in lines
        assert "第三行" in lines

    def test_read_moments_from_empty_text(self):
        """空文本返回空列表"""
        reader = MomentsReader()
        assert reader.read_moments_from_text("") == []
        assert reader.read_moments_from_text("   ") == []
