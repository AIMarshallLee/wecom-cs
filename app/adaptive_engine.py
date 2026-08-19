"""话术自适应引擎

根据用户画像动态改写 FAQ 回复，实现千人千面。

核心逻辑：
    1. 拿到 FAQ 原始答案 + 用户画像
    2. 判断是否值得调用 LLM 改写（新用户跳过，省 token）
    3. 构建「改写指令 prompt」，让 LLM 在保留事实的基础上调整话术
    4. LLM 改写失败时降级到原始答案

改写维度：
    - 话术风格：direct / gentle / enthusiastic / professional
    - 转化阶段：new / engaged / interested / considering / converted
    - 对话摘要：引用历史交互内容
    - 朋友圈标签：结合用户身份特征
"""

import logging
from typing import Optional

logger = logging.getLogger("wecom_cs")


class AdaptiveEngine:
    """话术自适应引擎

    根据 UserProfileService 的用户画像，
    使用 LLM 将 FAQ 原始答案改写为个性化回复。

    Attributes:
        llm_engine: LLM 引擎实例
        profile_service: 用户画像服务实例
    """

    # 触发 LLM 改写的最小消息数（低于此值不值得改写）
    MIN_MESSAGES_FOR_ADAPT: int = 3

    # 改写后最大长度（字符）
    MAX_ADAPTED_LENGTH: int = 500

    # 风格指令映射
    STYLE_INSTRUCTIONS: dict[str, str] = {
        "direct": "说话干脆利落，直奔主题，别绕弯子",
        "gentle": "语气温和一点，像朋友聊天一样慢慢说",
        "enthusiastic": "热情一点，多用感叹号和 emoji，让对方感受到你的活力",
        "professional": "专业但不端着，用词精准但不官方腔",
    }

    # 转化阶段改写指令
    STAGE_INSTRUCTIONS: dict[str, str] = {
        "new": "用户是新人，热情但不推销，先建立信任",
        "engaged": "用户已经聊过几次了，可以自然地提到相关产品",
        "interested": "用户对产品有兴趣，可以适当引导转化",
        "considering": "用户在考虑是否报名，可以给点推动力但别太急",
        "converted": "用户已经报名了，感谢信任，可以提一些社群价值",
    }

    def __init__(self, llm_engine=None, profile_service=None) -> None:
        """初始化自适应引擎

        Args:
            llm_engine: LLM 引擎实例（可选，为 None 时降级到原始答案）
            profile_service: 用户画像服务实例（可选）
        """
        self.llm_engine = llm_engine
        self.profile_service = profile_service

    def should_adapt(self, user_id: str) -> bool:
        """判断是否值得对该用户进行 LLM 改写

        新用户（消息数 < 3）跳过改写，直接用规则化人性化处理。
        老用户有画像数据时才值得花 LLM token 做个性化。

        Args:
            user_id: 用户 ID

        Returns:
            True 表示值得调用 LLM 改写
        """
        if not self.llm_engine or not self.profile_service:
            return False

        try:
            profile = self.profile_service.get_profile(user_id)
            msg_count = profile.get("message_count", 0)
            if msg_count < self.MIN_MESSAGES_FOR_ADAPT:
                return False

            # 有对话摘要或有朋友圈标签时才改写
            has_summary = bool(profile.get("conversation_summary"))
            has_tags = bool(profile.get("moments_tags"))
            has_style = bool(profile.get("preferred_style"))

            return has_summary or has_tags or has_style or msg_count >= 5
        except Exception:
            return False

    def _build_adapt_prompt(
        self,
        user_message: str,
        faq_answer: str,
        faq_question: str,
        profile: dict,
    ) -> str:
        """构建改写指令 prompt

        将用户画像信息注入 prompt，指导 LLM 如何改写 FAQ 答案。

        Args:
            user_message: 用户原始消息
            faq_answer: FAQ 原始答案
            faq_question: FAQ 匹配到的问题
            profile: 用户画像

        Returns:
            改写指令 prompt 字符串
        """
        parts = []

        # 基础信息
        stage = profile.get("conversion_stage", "new")
        msg_count = profile.get("message_count", 0)
        parts.append(f"用户转化阶段：{stage}（{self.STAGE_INSTRUCTIONS.get(stage, '')}）")
        parts.append(f"用户消息数：{msg_count}")

        # 问过的话题
        topics = profile.get("asked_topics", [])
        if topics:
            parts.append(f"用户之前问过：{', '.join(topics[-5:])}")

        # 对话摘要
        summary = profile.get("conversation_summary", "")
        if summary:
            parts.append(f"对话摘要：{summary}")

        # 朋友圈标签
        tags = profile.get("moments_tags", [])
        if tags:
            parts.append(f"用户标签：{', '.join(tags)}")

        # 偏好风格
        style = profile.get("preferred_style", "")
        if style:
            parts.append(f"偏好话术风格：{style}（{self.STYLE_INSTRUCTIONS.get(style, '')}）")

        profile_text = "\n".join(parts)

        prompt = (
            "你是一个客服助手，需要根据用户画像改写下面的 FAQ 回复。\n\n"
            "【用户画像】\n"
            f"{profile_text}\n\n"
            "【用户当前消息】\n"
            f"{user_message}\n\n"
            "【FAQ 原始答案】\n"
            f"{faq_answer}\n\n"
            "【改写要求】\n"
            "1. 保留 FAQ 答案中的所有事实信息（价格、政策、流程等不能改）\n"
            "2. 根据用户画像调整语气和措辞\n"
            "3. 如果对话摘要里用户有顾虑，自然地回应一下\n"
            "4. 说大白话，别用'您好''请问'这种官方腔\n"
            "5. 不超过150字\n"
            "6. 直接输出改写后的回复，不要加任何前缀说明\n"
        )

        return prompt

    async def adapt_reply(
        self,
        user_id: str,
        user_message: str,
        faq_answer: str,
        faq_question: str = "",
    ) -> str:
        """根据用户画像改写 FAQ 回复

        如果不值得改写（新用户）或 LLM 不可用，返回原始 FAQ 答案。

        Args:
            user_id: 用户 ID
            user_message: 用户原始消息
            faq_answer: FAQ 原始答案
            faq_question: FAQ 匹配到的问题

        Returns:
            改写后的回复文本（失败时返回原始答案）
        """
        if not self.should_adapt(user_id):
            return faq_answer

        try:
            profile = self.profile_service.get_profile(user_id)
            prompt = self._build_adapt_prompt(
                user_message, faq_answer, faq_question, profile
            )

            # 调用 LLM 改写
            reply, confidence = await self.llm_engine.generate_reply(
                prompt, "", []
            )

            if not reply or not reply.strip():
                logger.warning("LLM 改写返回空，降级到原始答案")
                return faq_answer

            adapted = reply.strip()

            # 长度限制
            if len(adapted) > self.MAX_ADAPTED_LENGTH:
                adapted = adapted[:self.MAX_ADAPTED_LENGTH]

            logger.info(
                f"FAQ 回复已自适应改写: user={user_id}, "
                f"original_len={len(faq_answer)}, adapted_len={len(adapted)}"
            )
            return adapted

        except Exception as e:
            logger.warning(f"自适应改写失败，降级到原始答案: {e}")
            return faq_answer
