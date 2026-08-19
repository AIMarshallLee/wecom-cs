"""用户画像服务

基于 SQLite 的持久化用户画像管理。
解决 Redis 30天过期丢失用户数据的问题。

画像结构：
    - 基础信息：first_seen, last_seen, message_count
    - 转化阶段：new → engaged → interested → considering → converted
    - 问过的话题：asked_topics (最近20个)
    - 对话摘要：conversation_summary (LLM生成，每次对话后更新)
    - 朋友圈标签：moments_tags (职业/兴趣/消费力/风格)
    - 偏好话术风格：preferred_style (direct/gentle/enthusiastic/professional)
"""

import json
import time
import logging
from typing import Optional, Dict, Any, List

from app.utils import SQLiteDB

logger = logging.getLogger("wecom_cs")


class UserProfileService:
    """用户画像服务

    基于 SQLite 持久化存储，支持：
    - 用户画像 CRUD
    - 对话摘要存储
    - 朋友圈标签存储（Phase 3）
    - 话术效果记录（Phase 4）
    """

    # 用户画像建表 SQL
    PROFILE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id TEXT PRIMARY KEY,
        first_seen REAL,
        last_seen REAL,
        message_count INTEGER DEFAULT 0,
        conversion_stage TEXT DEFAULT 'new',
        asked_topics TEXT DEFAULT '[]',
        conversation_summary TEXT DEFAULT '',
        moments_tags TEXT DEFAULT '[]',
        moments_analyzed_at REAL DEFAULT 0,
        preferred_style TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT DEFAULT (datetime('now', 'localtime'))
    );

    CREATE TABLE IF NOT EXISTS interaction_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        user_message TEXT,
        bot_reply TEXT,
        reply_mode TEXT,
        faq_id TEXT,
        confidence REAL,
        timestamp TEXT DEFAULT (datetime('now', 'localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_interaction_user_id
        ON interaction_history(user_id);
    """

    # 转化阶段顺序
    STAGE_ORDER: list[str] = ["new", "engaged", "interested", "considering", "converted"]

    # 转化关键词
    CONVERSION_KEYWORDS: dict[str, list[str]] = {
        "interested": [
            "价格", "多少钱", "费用", "收费", "划算", "贵不贵",
            "值不值", "性价比", "优惠", "打折",
        ],
        "considering": [
            "报名", "怎么报", "名额", "开课", "什么时候",
            "时间", "地点", "安排", "在哪", "怎么参加",
        ],
        "converted": [
            "已报", "已付", "报名成功", "已缴费", "付款了",
            "已经报了", "已报名",
        ],
    }

    def __init__(self, db: SQLiteDB) -> None:
        """初始化用户画像服务

        Args:
            db: SQLiteDB 实例
        """
        self.db = db
        self._init_tables()

    def _init_tables(self) -> None:
        """初始化画像表"""
        self.db.init_db(self.PROFILE_SCHEMA)
        logger.info("user_profiles 表初始化完成")

    def get_profile(self, user_id: str) -> dict:
        """获取用户画像

        不存在时返回默认画像。

        Args:
            user_id: 用户 ID

        Returns:
            用户画像字典
        """
        rows = self.db.query(
            "SELECT * FROM user_profiles WHERE user_id = ?",
            (user_id,)
        )
        if rows:
            row = rows[0]
            return {
                "user_id": row["user_id"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "message_count": row["message_count"],
                "conversion_stage": row["conversion_stage"],
                "asked_topics": json.loads(row["asked_topics"]) if row["asked_topics"] else [],
                "conversation_summary": row["conversation_summary"] or "",
                "moments_tags": json.loads(row["moments_tags"]) if row["moments_tags"] else [],
                "moments_analyzed_at": row["moments_analyzed_at"] or 0,
                "preferred_style": row["preferred_style"] or "",
            }
        return self._default_profile(user_id)

    def _default_profile(self, user_id: str) -> dict:
        """生成默认画像"""
        return {
            "user_id": user_id,
            "first_seen": None,
            "last_seen": None,
            "message_count": 0,
            "conversion_stage": "new",
            "asked_topics": [],
            "conversation_summary": "",
            "moments_tags": [],
            "moments_analyzed_at": 0,
            "preferred_style": "",
        }

    def save_profile(self, user_id: str, profile: dict) -> None:
        """保存用户画像（upsert）

        Args:
            user_id: 用户 ID
            profile: 画像字典
        """
        now = time.time()
        profile["last_seen"] = now
        if not profile.get("first_seen"):
            profile["first_seen"] = now

        self.db.execute(
            """
            INSERT INTO user_profiles (
                user_id, first_seen, last_seen, message_count,
                conversion_stage, asked_topics, conversation_summary,
                moments_tags, moments_analyzed_at, preferred_style,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                datetime('now', 'localtime'), datetime('now', 'localtime'))
            ON CONFLICT(user_id) DO UPDATE SET
                first_seen = excluded.first_seen,
                last_seen = excluded.last_seen,
                message_count = excluded.message_count,
                conversion_stage = excluded.conversion_stage,
                asked_topics = excluded.asked_topics,
                conversation_summary = excluded.conversation_summary,
                moments_tags = excluded.moments_tags,
                moments_analyzed_at = excluded.moments_analyzed_at,
                preferred_style = excluded.preferred_style,
                updated_at = datetime('now', 'localtime')
            """,
            (
                user_id,
                profile.get("first_seen"),
                profile.get("last_seen"),
                profile.get("message_count", 0),
                profile.get("conversion_stage", "new"),
                json.dumps(profile.get("asked_topics", []), ensure_ascii=False),
                profile.get("conversation_summary", ""),
                json.dumps(profile.get("moments_tags", []), ensure_ascii=False),
                profile.get("moments_analyzed_at", 0),
                profile.get("preferred_style", ""),
            )
        )

    def record_interaction(
        self,
        user_id: str,
        content: str,
        faq_question: str = "",
    ) -> dict:
        """记录用户交互，更新画像

        Args:
            user_id: 用户 ID
            content: 用户消息内容
            faq_question: FAQ 匹配到的问题（可选）

        Returns:
            更新后的用户画像
        """
        profile = self.get_profile(user_id)
        now = time.time()

        if not profile.get("first_seen"):
            profile["first_seen"] = now
        profile["last_seen"] = now
        profile["message_count"] = profile.get("message_count", 0) + 1

        # 记录问过的话题
        if faq_question:
            asked = profile.get("asked_topics", [])
            if faq_question not in asked:
                asked.append(faq_question)
                profile["asked_topics"] = asked[-20:]  # 保留最近20个

        # 关键词更新转化阶段（只能向前推进）
        current_stage = profile.get("conversion_stage", "new")
        current_idx = self.STAGE_ORDER.index(current_stage) if current_stage in self.STAGE_ORDER else 0

        for stage_name in ["converted", "considering", "interested"]:
            keywords = self.CONVERSION_KEYWORDS.get(stage_name, [])
            if any(kw in content for kw in keywords):
                stage_idx = self.STAGE_ORDER.index(stage_name)
                if stage_idx > current_idx:
                    profile["conversion_stage"] = stage_name
                    current_idx = stage_idx
                break

        # 消息数达到3条自动升级到 engaged
        if profile.get("conversion_stage") == "new" and profile["message_count"] >= 3:
            profile["conversion_stage"] = "engaged"

        self.save_profile(user_id, profile)
        return profile

    def update_conversation_summary(self, user_id: str, summary: str) -> None:
        """更新对话摘要

        Args:
            user_id: 用户 ID
            summary: LLM 生成的对话摘要
        """
        profile = self.get_profile(user_id)
        profile["conversation_summary"] = summary
        self.save_profile(user_id, profile)

    def update_moments_tags(self, user_id: str, tags: list[str]) -> None:
        """更新朋友圈标签（Phase 3 使用）

        Args:
            user_id: 用户 ID
            tags: 标签列表
        """
        profile = self.get_profile(user_id)
        profile["moments_tags"] = tags
        profile["moments_analyzed_at"] = time.time()
        self.save_profile(user_id, profile)

    def update_preferred_style(self, user_id: str, style: str) -> None:
        """更新偏好话术风格（Phase 4 使用）

        Args:
            user_id: 用户 ID
            style: 风格 (direct/gentle/enthusiastic/professional)
        """
        profile = self.get_profile(user_id)
        profile["preferred_style"] = style
        self.save_profile(user_id, profile)

    def record_interaction_detail(
        self,
        user_id: str,
        user_message: str,
        bot_reply: str,
        reply_mode: str = "",
        faq_id: str = "",
        confidence: float = 0.0,
    ) -> None:
        """记录交互明细到 interaction_history 表

        Args:
            user_id: 用户 ID
            user_message: 用户消息
            bot_reply: 机器人回复
            reply_mode: 回复模式 (faq/llm/human)
            faq_id: FAQ ID（如有）
            confidence: 置信度
        """
        self.db.execute(
            """
            INSERT INTO interaction_history
                (user_id, user_message, bot_reply, reply_mode, faq_id, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, user_message, bot_reply, reply_mode, faq_id, confidence)
        )

    def get_interaction_history(
        self, user_id: str, limit: int = 50
    ) -> list[dict]:
        """获取用户交互历史

        Args:
            user_id: 用户 ID
            limit: 最多返回条数

        Returns:
            交互记录列表
        """
        rows = self.db.query(
            """
            SELECT * FROM interaction_history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit)
        )
        return rows

    def get_profile_context(self, user_id: str) -> str:
        """获取用户画像上下文文本（注入 LLM system prompt 用）

        将用户画像格式化为简洁文本，供 LLM 个性化回复使用。

        Args:
            user_id: 用户 ID

        Returns:
            画像上下文文本，如：
            "【用户画像】阶段：感兴趣 | 消息数：8 | 问过：价格,报名 |
             摘要：上次问了价格觉得贵，对体验卡感兴趣 | 朋友圈：电商创业者"
        """
        profile = self.get_profile(user_id)

        parts = []
        parts.append(f"阶段：{profile.get('conversion_stage', 'new')}")
        parts.append(f"消息数：{profile.get('message_count', 0)}")

        topics = profile.get("asked_topics", [])
        if topics:
            parts.append(f"问过：{','.join(topics[:5])}")

        summary = profile.get("conversation_summary", "")
        if summary:
            parts.append(f"摘要：{summary}")

        tags = profile.get("moments_tags", [])
        if tags:
            parts.append(f"标签：{','.join(tags)}")

        style = profile.get("preferred_style", "")
        if style:
            parts.append(f"偏好风格：{style}")

        return "【用户画像】" + " | ".join(parts) if parts else ""

    def get_all_profiles(self, limit: int = 100) -> list[dict]:
        """获取所有用户画像（管理后台用）

        Args:
            limit: 最多返回条数

        Returns:
            用户画像列表
        """
        rows = self.db.query(
            "SELECT * FROM user_profiles ORDER BY last_seen DESC LIMIT ?",
            (limit,)
        )
        result = []
        for row in rows:
            profile = dict(row)
            profile["asked_topics"] = json.loads(profile.get("asked_topics") or "[]")
            profile["moments_tags"] = json.loads(profile.get("moments_tags") or "[]")
            result.append(profile)
        return result
