"""UserProfileService 单元测试

测试 SQLite 持久化用户画像服务的核心功能：
- 画像 CRUD
- 交互记录与转化阶段推进
- 对话摘要存储
- 朋友圈标签存储
- 交互明细记录
- 画像上下文生成
"""

import json
import pytest
import time

from app.utils import SQLiteDB
from app.user_profile_service import UserProfileService


@pytest.fixture
def db():
    """内存数据库 fixture"""
    db = SQLiteDB(":memory:")
    db.init_db()
    yield db
    db.close()


@pytest.fixture
def service(db):
    """UserProfileService fixture"""
    return UserProfileService(db)


class TestProfileCRUD:
    """画像 CRUD 测试"""

    def test_default_profile(self, service):
        """新用户返回默认画像"""
        profile = service.get_profile("user_new")
        assert profile["user_id"] == "user_new"
        assert profile["message_count"] == 0
        assert profile["conversion_stage"] == "new"
        assert profile["asked_topics"] == []
        assert profile["conversation_summary"] == ""
        assert profile["moments_tags"] == []

    def test_save_and_retrieve(self, service):
        """保存后能正确读取"""
        profile = service.get_profile("user_1")
        profile["message_count"] = 5
        profile["conversion_stage"] = "engaged"
        profile["asked_topics"] = ["价格", "报名"]
        service.save_profile("user_1", profile)

        retrieved = service.get_profile("user_1")
        assert retrieved["message_count"] == 5
        assert retrieved["conversion_stage"] == "engaged"
        assert retrieved["asked_topics"] == ["价格", "报名"]
        assert retrieved["first_seen"] is not None
        assert retrieved["last_seen"] is not None

    def test_upsert_existing(self, service):
        """重复保存更新而非插入"""
        profile = service.get_profile("user_2")
        profile["message_count"] = 1
        service.save_profile("user_2", profile)

        profile["message_count"] = 10
        service.save_profile("user_2", profile)

        retrieved = service.get_profile("user_2")
        assert retrieved["message_count"] == 10

    def test_persistence_across_connections(self):
        """SQLite 文件持久化测试"""
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_profile.db")

            # 第一次连接：写入数据
            db1 = SQLiteDB(db_path)
            db1.init_db()
            svc1 = UserProfileService(db1)
            profile = svc1.get_profile("user_persist")
            profile["message_count"] = 42
            svc1.save_profile("user_persist", profile)
            db1.close()

            # 第二次连接：验证数据还在
            db2 = SQLiteDB(db_path)
            db2.init_db()
            svc2 = UserProfileService(db2)
            retrieved = svc2.get_profile("user_persist")
            db2.close()

            assert retrieved["message_count"] == 42


class TestRecordInteraction:
    """交互记录与转化阶段测试"""

    def test_message_count_increment(self, service):
        """每次交互消息数+1"""
        service.record_interaction("user_a", "你好")
        service.record_interaction("user_a", "在吗")
        profile = service.get_profile("user_a")
        assert profile["message_count"] == 2

    def test_asked_topics_recording(self, service):
        """FAQ 问题被记录到 asked_topics"""
        service.record_interaction("user_b", "价格多少", "价格是多少钱")
        service.record_interaction("user_b", "怎么报名", "怎么报名参加")
        profile = service.get_profile("user_b")
        assert "价格是多少钱" in profile["asked_topics"]
        assert "怎么报名参加" in profile["asked_topics"]

    def test_asked_topics_dedup(self, service):
        """重复问题不重复记录"""
        service.record_interaction("user_c", "价格", "价格问题")
        service.record_interaction("user_c", "价格", "价格问题")
        profile = service.get_profile("user_c")
        assert profile["asked_topics"].count("价格问题") == 1

    def test_asked_topics_max_20(self, service):
        """asked_topics 最多保留20个"""
        for i in range(25):
            service.record_interaction("user_d", f"问题{i}", f"faq_{i}")
        profile = service.get_profile("user_d")
        assert len(profile["asked_topics"]) == 20
        assert "faq_0" not in profile["asked_topics"]  # 最早的被淘汰
        assert "faq_24" in profile["asked_topics"]  # 最新的保留

    def test_stage_new_to_engaged(self, service):
        """3条消息后自动升级到 engaged"""
        service.record_interaction("user_e", "你好")
        service.record_interaction("user_e", "在吗")
        assert service.get_profile("user_e")["conversion_stage"] == "new"

        service.record_interaction("user_e", "想了解一下")
        assert service.get_profile("user_e")["conversion_stage"] == "engaged"

    def test_stage_interested_by_keyword(self, service):
        """价格关键词触发 interested"""
        service.record_interaction("user_f", "价格多少钱")
        profile = service.get_profile("user_f")
        assert profile["conversion_stage"] == "interested"

    def test_stage_considering_by_keyword(self, service):
        """报名关键词触发 considering"""
        service.record_interaction("user_g", "怎么报名")
        profile = service.get_profile("user_g")
        assert profile["conversion_stage"] == "considering"

    def test_stage_converted_by_keyword(self, service):
        """已报名关键词触发 converted"""
        service.record_interaction("user_h", "我已经报名成功了")
        profile = service.get_profile("user_h")
        assert profile["conversion_stage"] == "converted"

    def test_stage_forward_only(self, service):
        """转化阶段只能向前推进，不能后退"""
        service.record_interaction("user_i", "怎么报名")  # considering
        service.record_interaction("user_i", "你好")  # 不应该降级
        profile = service.get_profile("user_i")
        assert profile["conversion_stage"] == "considering"


class TestConversationSummary:
    """对话摘要测试"""

    def test_update_summary(self, service):
        """更新对话摘要"""
        service.update_conversation_summary("user_j", "用户关注价格，对体验卡感兴趣")
        profile = service.get_profile("user_j")
        assert profile["conversation_summary"] == "用户关注价格，对体验卡感兴趣"

    def test_overwrite_summary(self, service):
        """重复更新覆盖旧摘要"""
        service.update_conversation_summary("user_k", "摘要1")
        service.update_conversation_summary("user_k", "摘要2")
        profile = service.get_profile("user_k")
        assert profile["conversation_summary"] == "摘要2"


class TestMomentsTags:
    """朋友圈标签测试"""

    def test_update_moments_tags(self, service):
        """更新朋友圈标签"""
        service.update_moments_tags("user_l", ["电商", "90后", "创业者"])
        profile = service.get_profile("user_l")
        assert profile["moments_tags"] == ["电商", "90后", "创业者"]
        assert profile["moments_analyzed_at"] > 0

    def test_overwrite_moments_tags(self, service):
        """重复更新覆盖旧标签"""
        service.update_moments_tags("user_m", ["旧标签"])
        service.update_moments_tags("user_m", ["新标签"])
        profile = service.get_profile("user_m")
        assert profile["moments_tags"] == ["新标签"]


class TestPreferredStyle:
    """偏好话术风格测试"""

    def test_update_preferred_style(self, service):
        """更新偏好风格"""
        service.update_preferred_style("user_n", "direct")
        profile = service.get_profile("user_n")
        assert profile["preferred_style"] == "direct"


class TestInteractionHistory:
    """交互明细测试"""

    def test_record_interaction_detail(self, service):
        """记录交互明细"""
        service.record_interaction_detail(
            "user_o", "价格多少", "3365元/年", "faq", "faq_001", 0.95
        )
        history = service.get_interaction_history("user_o")
        assert len(history) == 1
        assert history[0]["user_message"] == "价格多少"
        assert history[0]["bot_reply"] == "3365元/年"
        assert history[0]["reply_mode"] == "faq"
        assert history[0]["faq_id"] == "faq_001"
        assert history[0]["confidence"] == 0.95

    def test_interaction_history_order(self, service):
        """交互历史按时间倒序返回（最新在前）"""
        service.record_interaction_detail("user_p", "消息1", "回复1", "faq")
        service.record_interaction_detail("user_p", "消息2", "回复2", "llm")
        history = service.get_interaction_history("user_p")
        assert len(history) == 2
        assert history[0]["user_message"] == "消息2"  # 最新的在前
        assert history[1]["user_message"] == "消息1"

    def test_interaction_history_limit(self, service):
        """交互历史限制返回条数"""
        for i in range(10):
            service.record_interaction_detail("user_q", f"msg_{i}", f"reply_{i}", "faq")
        history = service.get_interaction_history("user_q", limit=3)
        assert len(history) == 3

    def test_interaction_history_isolated(self, service):
        """不同用户的交互历史隔离"""
        service.record_interaction_detail("user_r1", "msg_r1", "reply_r1", "faq")
        service.record_interaction_detail("user_r2", "msg_r2", "reply_r2", "llm")
        assert len(service.get_interaction_history("user_r1")) == 1
        assert len(service.get_interaction_history("user_r2")) == 1


class TestProfileContext:
    """画像上下文生成测试"""

    def test_empty_profile_context(self, service):
        """新用户的画像上下文只有阶段和消息数"""
        ctx = service.get_profile_context("user_s")
        assert "阶段：new" in ctx
        assert "消息数：0" in ctx
        assert "摘要" not in ctx
        assert "标签" not in ctx

    def test_full_profile_context(self, service):
        """完整画像的上下文包含所有字段"""
        service.record_interaction("user_t", "价格多少钱", "价格")
        service.update_conversation_summary("user_t", "用户觉得贵，对体验卡感兴趣")
        service.update_moments_tags("user_t", ["电商", "90后"])
        service.update_preferred_style("user_t", "gentle")

        ctx = service.get_profile_context("user_t")
        assert "阶段：interested" in ctx
        assert "消息数：1" in ctx
        assert "问过：价格" in ctx
        assert "摘要：用户觉得贵，对体验卡感兴趣" in ctx
        assert "标签：电商,90后" in ctx
        assert "偏好风格：gentle" in ctx

    def test_profile_context_format(self, service):
        """画像上下文以【用户画像】开头"""
        ctx = service.get_profile_context("user_u")
        assert ctx.startswith("【用户画像】")


class TestGetAllProfiles:
    """批量获取画像测试"""

    def test_get_all_profiles(self, service):
        """获取所有用户画像"""
        service.record_interaction("user_v1", "你好")
        service.record_interaction("user_v2", "价格")
        service.record_interaction("user_v3", "报名")

        profiles = service.get_all_profiles()
        assert len(profiles) == 3
        user_ids = [p["user_id"] for p in profiles]
        assert "user_v1" in user_ids
        assert "user_v2" in user_ids
        assert "user_v3" in user_ids

    def test_get_all_profiles_limit(self, service):
        """批量获取限制条数"""
        for i in range(10):
            service.record_interaction(f"user_w{i}", f"消息{i}")

        profiles = service.get_all_profiles(limit=5)
        assert len(profiles) == 5

    def test_get_all_profiles_parsed_fields(self, service):
        """批量获取的画像字段已解析 JSON"""
        service.record_interaction("user_x", "价格", "价格问题")
        profiles = service.get_all_profiles()
        profile = [p for p in profiles if p["user_id"] == "user_x"][0]
        assert isinstance(profile["asked_topics"], list)
        assert "价格问题" in profile["asked_topics"]
