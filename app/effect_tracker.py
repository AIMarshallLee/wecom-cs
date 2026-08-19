"""效果学习引擎

跟踪客服回复的效果，自动学习哪种话术对哪类用户更有效。

核心逻辑：
    1. 用户收到回复后，分析下一条消息判断效果（正面/负面/中性）
    2. 按 FAQ ID 聚合效果分数，识别哪些回复效果好
    3. 按话术风格聚合，为用户推荐最佳风格
    4. 低效 FAQ 答案会被标记，触发 AdaptiveEngine 更积极地改写

效果判断信号：
    正面：谢谢、好的、明白了、清楚了、有帮助、赞、太好了
    负面：还是不懂、不是这个意思、说错了、没解决、再说吧、算了
    重复提问：用户又问了同样的问题（说明上次回复没帮到）
    沉默：用户长时间没回复（中性偏负）
"""

import json
import time
import logging
from typing import Optional

from app.utils import SQLiteDB

logger = logging.getLogger("wecom_cs")


class EffectTracker:
    """效果学习引擎

    基于 SQLite 持久化存储，跟踪和分析客服回复效果。

    Attributes:
        db: SQLiteDB 实例
    """

    # 效果数据建表 SQL
    EFFECT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS reply_effects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        faq_id TEXT,
        reply_mode TEXT,
        effect_type TEXT,
        followup_text TEXT,
        timestamp TEXT DEFAULT (datetime('now', 'localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_effect_user_id
        ON reply_effects(user_id);

    CREATE INDEX IF NOT EXISTS idx_effect_faq_id
        ON reply_effects(faq_id);
    """

    # 正面信号关键词
    POSITIVE_SIGNALS: list[str] = [
        "谢谢", "感谢", "好的", "明白了", "清楚了", "懂了",
        "知道了", "有帮助", "有用", "太好了", "不错", "可以",
        "好的呢", "收到", "了解", "明白", "ok", "OK",
    ]

    # 负面信号关键词
    NEGATIVE_SIGNALS: list[str] = [
        "还是不懂", "不是这个意思", "说错了", "没解决",
        "再说吧", "算了", "不是我问的", "答非所问",
        "看不懂", "太复杂了", "能不能简单点", "不是我要的",
        "还是不明白", "没帮到", "没用",
    ]

    # 重复提问信号：如果下一条消息和之前的问题高度相似
    REPEAT_QUESTION_THRESHOLD: float = 0.7

    def __init__(self, db: SQLiteDB) -> None:
        """初始化效果跟踪器

        Args:
            db: SQLiteDB 实例
        """
        self.db = db
        self._init_tables()

    def _init_tables(self) -> None:
        """初始化效果数据表"""
        self.db.init_db(self.EFFECT_SCHEMA)
        logger.info("reply_effects 表初始化完成")

    def classify_followup(self, followup_text: str) -> str:
        """分类用户跟进消息的效果类型

        根据用户在收到回复后的下一条消息，判断回复效果。

        Args:
            followup_text: 用户跟进消息文本

        Returns:
            "positive" / "negative" / "neutral"
        """
        if not followup_text or not followup_text.strip():
            return "neutral"

        text = followup_text.strip()

        # 检查负面信号（优先级高于正面，因为"不是好的"应判负面）
        for signal in self.NEGATIVE_SIGNALS:
            if signal in text:
                return "negative"

        # 检查正面信号
        for signal in self.POSITIVE_SIGNALS:
            if signal in text:
                return "positive"

        return "neutral"

    def is_repeat_question(
        self, current_question: str, previous_question: str
    ) -> bool:
        """判断是否是重复提问

        使用简单的字符重叠率判断两个问题是否相似。

        Args:
            current_question: 当前问题
            previous_question: 上一条消息

        Returns:
            True 表示可能是重复提问
        """
        if not current_question or not previous_question:
            return False

        # 简单的字符集重叠率
        set1 = set(current_question)
        set2 = set(previous_question)
        if not set1 or not set2:
            return False

        overlap = len(set1 & set2) / len(set1 | set2)
        return overlap >= self.REPEAT_QUESTION_THRESHOLD

    def record_effect(
        self,
        user_id: str,
        followup_text: str,
        faq_id: str = "",
        reply_mode: str = "",
        previous_question: str = "",
    ) -> str:
        """记录回复效果

        分析用户的跟进消息，判断上一次回复的效果并记录。

        Args:
            user_id: 用户 ID
            followup_text: 用户跟进消息
            faq_id: 上次回复使用的 FAQ ID
            reply_mode: 上次回复模式 (faq/llm/human)
            previous_question: 上次用户的问题（用于判断重复提问）

        Returns:
            效果类型 "positive" / "negative" / "neutral"
        """
        # 判断效果类型
        effect_type = self.classify_followup(followup_text)

        # 检查是否是重复提问（覆盖为负面）
        if effect_type == "neutral" and previous_question:
            if self.is_repeat_question(followup_text, previous_question):
                effect_type = "negative"

        # 记录到数据库
        try:
            self.db.execute(
                """
                INSERT INTO reply_effects
                    (user_id, faq_id, reply_mode, effect_type, followup_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, faq_id, reply_mode, effect_type, followup_text[:200]),
            )
        except Exception as e:
            logger.warning(f"记录回复效果失败: {e}")

        logger.info(
            f"回复效果记录: user={user_id}, faq={faq_id}, "
            f"mode={reply_mode}, effect={effect_type}"
        )
        return effect_type

    def get_faq_effect_score(self, faq_id: str) -> dict:
        """获取 FAQ 的效果分数

        Args:
            faq_id: FAQ ID

        Returns:
            效果统计字典：
            {
                "total": 10,
                "positive": 7,
                "negative": 2,
                "neutral": 1,
                "score": 0.7  # 正面率
            }
        """
        try:
            rows = self.db.query(
                """
                SELECT effect_type, COUNT(*) as count
                FROM reply_effects
                WHERE faq_id = ?
                GROUP BY effect_type
                """,
                (faq_id,),
            )

            stats = {"total": 0, "positive": 0, "negative": 0, "neutral": 0, "score": 0.0}
            for row in rows:
                effect = row["effect_type"]
                count = row["count"]
                stats[effect] = count
                stats["total"] += count

            if stats["total"] > 0:
                stats["score"] = round(stats["positive"] / stats["total"], 2)

            return stats
        except Exception as e:
            logger.warning(f"获取 FAQ 效果分数失败: {e}")
            return {"total": 0, "positive": 0, "negative": 0, "neutral": 0, "score": 0.0}

    def get_user_effect_summary(self, user_id: str) -> dict:
        """获取用户级别的效果摘要

        Args:
            user_id: 用户 ID

        Returns:
            效果摘要字典
        """
        try:
            rows = self.db.query(
                """
                SELECT effect_type, COUNT(*) as count
                FROM reply_effects
                WHERE user_id = ?
                GROUP BY effect_type
                """,
                (user_id,),
            )

            stats = {"total": 0, "positive": 0, "negative": 0, "neutral": 0}
            for row in rows:
                effect = row["effect_type"]
                count = row["count"]
                stats[effect] = count
                stats["total"] += count

            return stats
        except Exception as e:
            logger.warning(f"获取用户效果摘要失败: {e}")
            return {"total": 0, "positive": 0, "negative": 0, "neutral": 0}

    def suggest_style(self, user_id: str) -> Optional[str]:
        """为用户推荐最佳话术风格

        基于历史效果数据，分析哪种风格对该用户效果最好。
        目前基于回复模式的正面率来推荐。

        Args:
            user_id: 用户 ID

        Returns:
            推荐风格名称，数据不足时返回 None
        """
        try:
            rows = self.db.query(
                """
                SELECT reply_mode, effect_type, COUNT(*) as count
                FROM reply_effects
                WHERE user_id = ?
                GROUP BY reply_mode, effect_type
                """,
                (user_id,),
            )

            if not rows:
                return None

            # 按回复模式聚合正面率
            mode_stats: dict[str, dict] = {}
            for row in rows:
                mode = row["reply_mode"] or "unknown"
                if mode not in mode_stats:
                    mode_stats[mode] = {"total": 0, "positive": 0}
                mode_stats[mode]["total"] += row["count"]
                if row["effect_type"] == "positive":
                    mode_stats[mode]["positive"] += row["count"]

            # 找正面率最高的模式
            best_mode = None
            best_score = -1
            for mode, stats in mode_stats.items():
                if stats["total"] >= 3:  # 至少3条数据才参考
                    score = stats["positive"] / stats["total"]
                    if score > best_score:
                        best_score = score
                        best_mode = mode

            if best_mode is None:
                return None

            # 映射回复模式到话术风格
            mode_to_style = {
                "faq": "professional",
                "llm": "gentle",
                "human": "direct",
            }
            return mode_to_style.get(best_mode)
        except Exception as e:
            logger.warning(f"推荐话术风格失败: {e}")
            return None

    def get_low_performing_faqs(self, min_samples: int = 3, max_score: float = 0.4) -> list[dict]:
        """获取效果不佳的 FAQ 列表

        用于识别哪些 FAQ 答案需要改进。

        Args:
            min_samples: 最少样本数（低于此数不参考）
            max_score: 最高正面率阈值（低于此值视为效果不佳）

        Returns:
            低效 FAQ 列表，每项包含 faq_id 和效果统计
        """
        try:
            rows = self.db.query(
                """
                SELECT faq_id,
                    COUNT(*) as total,
                    SUM(CASE WHEN effect_type = 'positive' THEN 1 ELSE 0 END) as positive,
                    SUM(CASE WHEN effect_type = 'negative' THEN 1 ELSE 0 END) as negative
                FROM reply_effects
                WHERE faq_id != ''
                GROUP BY faq_id
                HAVING total >= ?
                """,
                (min_samples,),
            )

            low_performers = []
            for row in rows:
                score = row["positive"] / row["total"] if row["total"] > 0 else 0
                if score < max_score:
                    low_performers.append({
                        "faq_id": row["faq_id"],
                        "total": row["total"],
                        "positive": row["positive"],
                        "negative": row["negative"],
                        "score": round(score, 2),
                    })

            # 按正面率升序（最差的在前）
            low_performers.sort(key=lambda x: x["score"])
            return low_performers
        except Exception as e:
            logger.warning(f"获取低效 FAQ 列表失败: {e}")
            return []
