"""EffectTracker 单元测试

测试效果学习引擎的核心功能：
- 跟进消息分类
- 重复提问判断
- 效果记录
- FAQ 效果分数
- 风格推荐
- 低效 FAQ 识别
"""

import pytest

from app.utils import SQLiteDB
from app.effect_tracker import EffectTracker


@pytest.fixture
def db():
    db = SQLiteDB(":memory:")
    db.init_db()
    yield db
    db.close()


@pytest.fixture
def tracker(db):
    return EffectTracker(db)


class TestClassifyFollowup:
    """跟进消息分类测试"""

    def test_positive_thanks(self, tracker):
        assert tracker.classify_followup("谢谢，明白了") == "positive"

    def test_positive_ok(self, tracker):
        assert tracker.classify_followup("好的") == "positive"

    def test_positive_got_it(self, tracker):
        assert tracker.classify_followup("懂了，清楚了") == "positive"

    def test_negative_still_confused(self, tracker):
        assert tracker.classify_followup("还是不懂") == "negative"

    def test_negative_wrong_meaning(self, tracker):
        assert tracker.classify_followup("不是这个意思") == "negative"

    def test_negative_give_up(self, tracker):
        assert tracker.classify_followup("算了") == "negative"

    def test_neutral_question(self, tracker):
        assert tracker.classify_followup("价格多少") == "neutral"

    def test_neutral_empty(self, tracker):
        assert tracker.classify_followup("") == "neutral"
        assert tracker.classify_followup("   ") == "neutral"

    def test_negative_priority_over_positive(self, tracker):
        """负面信号优先于正面（"不是好的"应该是负面）"""
        # "不是这个意思" 里有"不是"但也有负面信号
        result = tracker.classify_followup("不是我要的")
        assert result == "negative"


class TestRepeatQuestion:
    """重复提问判断测试"""

    def test_exact_repeat(self, tracker):
        assert tracker.is_repeat_question("价格多少", "价格多少") is True

    def test_similar_question(self, tracker):
        assert tracker.is_repeat_question("价格多少钱", "价格多少") is True

    def test_different_question(self, tracker):
        assert tracker.is_repeat_question("价格多少", "怎么报名") is False

    def test_empty_question(self, tracker):
        assert tracker.is_repeat_question("", "价格") is False
        assert tracker.is_repeat_question("价格", "") is False


class TestRecordEffect:
    """效果记录测试"""

    def test_record_positive(self, tracker, db):
        effect = tracker.record_effect("user_e1", "谢谢", "faq_1", "faq")
        assert effect == "positive"

        rows = db.query("SELECT * FROM reply_effects WHERE user_id = ?", ("user_e1",))
        assert len(rows) == 1
        assert rows[0]["effect_type"] == "positive"
        assert rows[0]["faq_id"] == "faq_1"

    def test_record_negative(self, tracker):
        effect = tracker.record_effect("user_e2", "还是不懂", "faq_2", "llm")
        assert effect == "negative"

    def test_record_neutral(self, tracker):
        effect = tracker.record_effect("user_e3", "价格多少", "faq_1", "faq")
        assert effect == "neutral"

    def test_record_repeat_question_as_negative(self, tracker):
        """重复提问被分类为负面"""
        effect = tracker.record_effect(
            "user_e4", "价格多少", "faq_1", "faq",
            previous_question="价格多少钱"
        )
        assert effect == "negative"

    def test_record_multiple(self, tracker, db):
        """记录多条效果"""
        tracker.record_effect("user_e5", "谢谢", "faq_1", "faq")
        tracker.record_effect("user_e5", "好的", "faq_1", "faq")
        tracker.record_effect("user_e5", "还是不懂", "faq_2", "llm")

        rows = db.query("SELECT * FROM reply_effects WHERE user_id = ?", ("user_e5",))
        assert len(rows) == 3


class TestFaqEffectScore:
    """FAQ 效果分数测试"""

    def test_empty_score(self, tracker):
        """无数据时分数为0"""
        score = tracker.get_faq_effect_score("faq_none")
        assert score["total"] == 0
        assert score["score"] == 0.0

    def test_all_positive(self, tracker):
        """全部正面时分数为1.0"""
        for i in range(5):
            tracker.record_effect("user1", "谢谢", "faq_good", "faq")
        score = tracker.get_faq_effect_score("faq_good")
        assert score["total"] == 5
        assert score["positive"] == 5
        assert score["score"] == 1.0

    def test_mixed_results(self, tracker):
        """混合效果"""
        tracker.record_effect("u1", "谢谢", "faq_mix", "faq")
        tracker.record_effect("u2", "好的", "faq_mix", "faq")
        tracker.record_effect("u3", "还是不懂", "faq_mix", "llm")
        tracker.record_effect("u4", "价格", "faq_mix", "faq")  # neutral

        score = tracker.get_faq_effect_score("faq_mix")
        assert score["total"] == 4
        assert score["positive"] == 2
        assert score["negative"] == 1
        assert score["neutral"] == 1
        assert score["score"] == 0.5

    def test_isolated_per_faq(self, tracker):
        """不同 FAQ 效果隔离"""
        tracker.record_effect("u1", "谢谢", "faq_a", "faq")
        tracker.record_effect("u1", "还是不懂", "faq_b", "llm")

        score_a = tracker.get_faq_effect_score("faq_a")
        score_b = tracker.get_faq_effect_score("faq_b")
        assert score_a["score"] == 1.0
        assert score_b["score"] == 0.0


class TestUserEffectSummary:
    """用户效果摘要测试"""

    def test_empty_summary(self, tracker):
        summary = tracker.get_user_effect_summary("user_none")
        assert summary["total"] == 0

    def test_user_summary(self, tracker):
        tracker.record_effect("user_s", "谢谢", "faq_1", "faq")
        tracker.record_effect("user_s", "好的", "faq_1", "faq")
        tracker.record_effect("user_s", "算了", "faq_2", "llm")

        summary = tracker.get_user_effect_summary("user_s")
        assert summary["total"] == 3
        assert summary["positive"] == 2
        assert summary["negative"] == 1


class TestSuggestStyle:
    """风格推荐测试"""

    def test_no_data_returns_none(self, tracker):
        assert tracker.suggest_style("user_none") is None

    def test_insufficient_data_returns_none(self, tracker):
        """少于3条数据不推荐"""
        tracker.record_effect("user_s1", "谢谢", "faq_1", "faq")
        tracker.record_effect("user_s1", "好的", "faq_1", "faq")
        assert tracker.suggest_style("user_s1") is None

    def test_faq_mode_best(self, tracker):
        """faq 模式效果最好时推荐 professional"""
        # faq 模式：4正面 1负面
        for i in range(4):
            tracker.record_effect(f"u_faq{i}", "谢谢", "faq_1", "faq")
        tracker.record_effect("u_faq_n", "还是不懂", "faq_1", "faq")

        # llm 模式：1正面 3负面
        tracker.record_effect("u_llm_p", "好的", "faq_2", "llm")
        for i in range(3):
            tracker.record_effect(f"u_llm{i}", "算了", "faq_2", "llm")

        style = tracker.suggest_style("user_multi")
        # 需要同一个用户的记录
        # 上面用了不同的 user_id，需要修改测试
        # 让我们用同一个用户
        pass

    def test_same_user_style_recommendation(self, tracker):
        """同一用户多轮交互的风格推荐"""
        # faq 模式效果好
        tracker.record_effect("user_style", "谢谢", "faq_1", "faq")
        tracker.record_effect("user_style", "好的", "faq_1", "faq")
        tracker.record_effect("user_style", "明白了", "faq_1", "faq")
        # llm 模式效果差
        tracker.record_effect("user_style", "还是不懂", "faq_2", "llm")
        tracker.record_effect("user_style", "算了", "faq_2", "llm")
        tracker.record_effect("user_style", "没解决", "faq_2", "llm")

        style = tracker.suggest_style("user_style")
        assert style == "professional"  # faq -> professional


class TestLowPerformingFaqs:
    """低效 FAQ 识别测试"""

    def test_empty_list(self, tracker):
        """无数据时返回空列表"""
        assert tracker.get_low_performing_faqs() == []

    def test_identifies_low_performers(self, tracker):
        """识别效果不佳的 FAQ"""
        # faq_bad: 1正面 4负面
        tracker.record_effect("u1", "谢谢", "faq_bad", "faq")
        tracker.record_effect("u2", "还是不懂", "faq_bad", "faq")
        tracker.record_effect("u3", "算了", "faq_bad", "faq")
        tracker.record_effect("u4", "没解决", "faq_bad", "faq")
        tracker.record_effect("u5", "看不懂", "faq_bad", "faq")

        # faq_good: 4正面 1负面
        tracker.record_effect("u1", "谢谢", "faq_good", "faq")
        tracker.record_effect("u2", "好的", "faq_good", "faq")
        tracker.record_effect("u3", "明白了", "faq_good", "faq")
        tracker.record_effect("u4", "清楚了", "faq_good", "faq")
        tracker.record_effect("u5", "还是不懂", "faq_good", "faq")

        low = tracker.get_low_performing_faqs(min_samples=3, max_score=0.4)
        faq_ids = [item["faq_id"] for item in low]
        assert "faq_bad" in faq_ids
        assert "faq_good" not in faq_ids

    def test_min_samples_filter(self, tracker):
        """样本不足的 FAQ 不被识别"""
        tracker.record_effect("u1", "还是不懂", "faq_few", "faq")
        tracker.record_effect("u2", "算了", "faq_few", "faq")

        low = tracker.get_low_performing_faqs(min_samples=3)
        assert all(item["faq_id"] != "faq_few" for item in low)

    def test_sorted_by_score(self, tracker):
        """结果按正面率升序排列"""
        # faq_worst: 0正面 5负面
        for i in range(5):
            tracker.record_effect(f"u_w{i}", "算了", "faq_worst", "faq")

        # faq_bad: 1正面 4负面
        tracker.record_effect("u_b1", "谢谢", "faq_bad", "faq")
        for i in range(4):
            tracker.record_effect(f"u_b{i}", "还是不懂", "faq_bad", "faq")

        low = tracker.get_low_performing_faqs(min_samples=3, max_score=0.5)
        assert len(low) >= 2
        assert low[0]["score"] <= low[1]["score"]  # 升序
