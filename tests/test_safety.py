"""SafetyGuard 单元测试

测试安全合规模块的核心功能：
- 速率限制（小时/天/最小间隔）
- 工作时间检查
- 人工行为模拟（延迟/打错字）
- 内容过滤（链接/支付/引流/长度）
- 紧急停止
- 渐进式上线
- 综合检查 pre_send_check
"""

import time
import pytest
from unittest.mock import patch
from datetime import datetime

from app.safety import SafetyGuard


@pytest.fixture
def guard():
    """默认配置的安全守卫"""
    return SafetyGuard()


@pytest.fixture
def custom_guard():
    """自定义配置的安全守卫"""
    return SafetyGuard({
        "rate_limit": {
            "max_per_hour": 5,
            "max_per_day": 10,
            "min_interval_seconds": 2,
        },
        "work_hours": {"start": 0, "end": 24},
        "human_simulate": {
            "min_delay": 0,
            "max_delay": 0,
            "typo_probability": 0,
        },
        "rollout": {"phase": 3},
    })


class TestRateLimit:
    """速率限制测试"""

    def test_first_send_allowed(self, guard):
        """第一次发送应该允许"""
        allowed, _ = guard.check_rate_limit("user_1")
        assert allowed is True

    def test_min_interval_blocked(self, custom_guard):
        """最小间隔内第二次发送应被阻止"""
        custom_guard.record_send("user_1")
        allowed, reason = custom_guard.check_rate_limit("user_1")
        assert allowed is False
        assert "间隔" in reason

    def test_hour_limit_exceeded(self, custom_guard):
        """超过小时限制应被阻止"""
        for i in range(5):
            custom_guard._user_send_times["user_1"].append(time.time() - 100)
        custom_guard._last_send_time["user_1"] = time.time() - 100
        allowed, reason = custom_guard.check_rate_limit("user_1")
        assert allowed is False
        assert "小时" in reason

    def test_day_limit_exceeded(self, custom_guard):
        """超过日限制应被阻止"""
        # 填满小时配额 + 日配额
        for i in range(10):
            custom_guard._user_send_times["user_1"].append(time.time() - 7200)
        allowed, reason = custom_guard.check_rate_limit("user_1")
        assert allowed is False
        assert "日" in reason

    def test_different_users_independent(self, guard):
        """不同用户的速率限制互相独立"""
        guard.record_send("user_a")
        # user_a 刚发过，user_b 不受影响
        allowed_a, _ = guard.check_rate_limit("user_a")
        allowed_b, _ = guard.check_rate_limit("user_b")
        assert allowed_a is False  # 5s 间隔内
        assert allowed_b is True


class TestWorkHours:
    """工作时间检查测试"""

    def test_in_work_hours(self):
        """工作时间内允许"""
        guard = SafetyGuard({"work_hours": {"start": 0, "end": 24}})
        assert guard.is_work_hours() is True

    def test_outside_work_hours(self):
        """工作时间外不允许"""
        current_hour = datetime.now().hour
        # 设置一个肯定不包含当前时间的范围
        if current_hour < 12:
            guard = SafetyGuard({"work_hours": {"start": 20, "end": 23}})
        else:
            guard = SafetyGuard({"work_hours": {"start": 0, "end": 6}})
        assert guard.is_work_hours() is False

    def test_default_work_hours(self, guard):
        """默认工作时间 9:00-21:00"""
        assert guard.config["work_hours"]["start"] == 9
        assert guard.config["work_hours"]["end"] == 21


class TestContentFilter:
    """内容过滤测试"""

    def test_clean_text_passes(self, guard):
        """正常文本不被过滤"""
        text = "你好，生财有术星球年费3365元，欢迎了解~"
        filtered, warnings = guard.filter_content(text)
        assert filtered == text
        assert len(warnings) == 0

    def test_url_filtered(self, guard):
        """URL 链接被过滤"""
        text = "详情看 https://example.com/page 哈"
        filtered, warnings = guard.filter_content(text)
        assert "https://example.com/page" not in filtered
        assert len(warnings) > 0

    def test_drainage_filtered(self, guard):
        """引流话术被过滤"""
        text = "你可以加微信 hello123 私聊"
        filtered, warnings = guard.filter_content(text)
        assert "加微信" not in filtered
        assert len(warnings) > 0

    def test_payment_filtered(self, guard):
        """支付内容被过滤"""
        text = "请用微信支付付款"
        filtered, warnings = guard.filter_content(text)
        assert "微信支付" not in filtered
        assert len(warnings) > 0

    def test_long_text_truncated(self, guard):
        """超长文本被截断"""
        text = "啊" * 600
        filtered, warnings = guard.filter_content(text)
        assert len(filtered) <= 500
        assert len(warnings) > 0

    def test_multiple_issues(self, guard):
        """多种问题同时存在"""
        text = "加微信扫码 https://t.co 微信支付 " + "字" * 600
        filtered, warnings = guard.filter_content(text)
        assert len(filtered) <= 500
        assert len(warnings) >= 3  # 至少 3 个警告

    def test_disable_filter(self):
        """禁用过滤后不拦截"""
        guard = SafetyGuard({
            "content_filter": {
                "block_links": False,
                "block_payment": False,
                "block_drainage": False,
                "max_length": 10000,
            }
        })
        text = "加微信 https://t.co 微信支付"
        filtered, warnings = guard.filter_content(text)
        assert filtered == text
        assert len(warnings) == 0


class TestHumanSimulate:
    """人工行为模拟测试"""

    def test_delay_range(self, guard):
        """延迟在配置范围内"""
        guard.config["human_simulate"]["min_delay"] = 5
        guard.config["human_simulate"]["max_delay"] = 10
        for _ in range(20):
            delay = guard.get_send_delay()
            assert 5 <= delay <= 10

    def test_typo_zero_probability(self, custom_guard):
        """概率为 0 不引入打错字"""
        text = "你好世界这是测试"
        for _ in range(50):
            result = custom_guard.maybe_introduce_typo(text)
            assert result == text

    def test_typo_changes_text(self):
        """打错字会改变文本"""
        guard = SafetyGuard({
            "human_simulate": {"min_delay": 0, "max_delay": 0, "typo_probability": 1.0}
        })
        text = "你好世界这是测试文本"
        changed_count = 0
        for _ in range(100):
            result = guard.maybe_introduce_typo(text)
            if result != text:
                changed_count += 1
        # 概率 1.0 时应该大部分都改变
        assert changed_count > 80

    def test_short_text_no_typo(self, guard):
        """短文本不引入打错字"""
        text = "好"
        result = guard.maybe_introduce_typo(text)
        assert result == text


class TestEmergencyStop:
    """紧急停止测试"""

    def test_normal_state(self, guard):
        """正常状态不停止"""
        assert guard.is_emergency_stopped() is False

    def test_trigger_stop(self, guard):
        """触发停止"""
        guard.emergency_stop()
        assert guard.is_emergency_stopped() is True

    def test_resume(self, guard):
        """解除停止"""
        guard.emergency_stop()
        assert guard.is_emergency_stopped() is True
        guard.resume()
        assert guard.is_emergency_stopped() is False

    def test_init_with_stop(self):
        """初始化时就处于停止状态"""
        guard = SafetyGuard({"emergency_stop": True})
        assert guard.is_emergency_stopped() is True


class TestRollout:
    """渐进式上线测试"""

    def test_phase1_blocks_auto(self, guard):
        """Phase 1 不允许自动回复"""
        assert guard.get_phase() == 1
        assert guard.should_auto_reply(0.99, True) is False

    def test_phase2_faq_high_confidence(self, custom_guard):
        """Phase 2 FAQ 高置信度自动"""
        custom_guard.config["rollout"]["phase"] = 2
        assert custom_guard.should_auto_reply(0.95, True) is True
        assert custom_guard.should_auto_reply(0.85, True) is False

    def test_phase2_non_faq_blocked(self, custom_guard):
        """Phase 2 非 FAQ 不自动"""
        custom_guard.config["rollout"]["phase"] = 2
        assert custom_guard.should_auto_reply(0.95, False) is False

    def test_phase3_all_above_threshold(self, custom_guard):
        """Phase 3 置信度 >75% 都自动"""
        custom_guard.config["rollout"]["phase"] = 3
        assert custom_guard.should_auto_reply(0.80, True) is True
        assert custom_guard.should_auto_reply(0.80, False) is True
        assert custom_guard.should_auto_reply(0.70, True) is False


class TestPreSendCheck:
    """综合发送前检查测试"""

    def test_emergency_stop_blocks(self, guard):
        """紧急停止时拦截"""
        guard.emergency_stop()
        result = guard.pre_send_check("user_1", "你好", 0.95, True)
        assert result["allowed"] is False
        assert result["mode"] == "block"

    def test_outside_work_hours_blocks(self):
        """非工作时间拦截"""
        current_hour = datetime.now().hour
        if current_hour < 12:
            guard = SafetyGuard({"work_hours": {"start": 20, "end": 23}})
        else:
            guard = SafetyGuard({"work_hours": {"start": 0, "end": 6}})
        result = guard.pre_send_check("user_1", "你好", 0.95, True)
        assert result["allowed"] is False
        assert result["mode"] == "block"

    def test_phase1_suggest_mode(self):
        """Phase 1 走 suggest 模式"""
        guard = SafetyGuard({"work_hours": {"start": 0, "end": 24}})
        result = guard.pre_send_check("user_1", "你好", 0.95, True)
        assert result["mode"] == "suggest"
        assert "稍等" in result["filtered_text"]

    def test_phase3_auto_with_content_filter(self, custom_guard):
        """Phase 3 自动回复 + 内容过滤"""
        custom_guard.config["rollout"]["phase"] = 3
        result = custom_guard.pre_send_check(
            "user_1", "详情看 https://t.co 哈", 0.90, True
        )
        assert result["allowed"] is True
        assert result["mode"] == "auto"
        assert "https://t.co" not in result["filtered_text"]
        assert len(result["warnings"]) > 0

    def test_rate_limit_blocked(self, custom_guard):
        """速率限制触发时拦截"""
        custom_guard.config["rollout"]["phase"] = 3
        # 快速发 5 条
        for _ in range(5):
            custom_guard.record_send("user_1")
        result = custom_guard.pre_send_check("user_1", "你好", 0.90, True)
        assert result["allowed"] is False
        assert result["mode"] == "block"

    def test_delay_returned(self):
        """返回延迟值"""
        guard = SafetyGuard({
            "work_hours": {"start": 0, "end": 24},
            "human_simulate": {"min_delay": 5, "max_delay": 8, "typo_probability": 0},
        })
        result = guard.pre_send_check("user_1", "你好", 0.95, True)
        # suggest 模式也会返回 delay
        assert 5 <= result["delay"] <= 8


class TestPostSend:
    """发送后记录测试"""

    def test_record_send_updates_times(self, guard):
        """记录发送更新时间列表"""
        guard.record_send("user_1")
        assert len(guard._user_send_times["user_1"]) == 1
        guard.record_send("user_1")
        assert len(guard._user_send_times["user_1"]) == 2

    def test_record_updates_last_send(self, guard):
        """记录发送更新最后发送时间"""
        t1 = time.time()
        guard.record_send("user_1")
        assert guard._last_send_time["user_1"] >= t1
