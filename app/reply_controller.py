"""回复决策器

决定回复模式：自动回复、建议回复还是转人工。
基于敏感词检测、FAQ 匹配结果和 LLM 置信度进行综合决策。
"""

from typing import Optional


class ReplyController:
    """回复决策器

    根据用户消息、FAQ 匹配结果、LLM 回复和置信度，决定回复模式。

    决策规则（优先级从高到低）：
        1. 敏感词检测：消息包含投诉/退款/法律等 → 转人工
        2. FAQ 精确匹配（confidence=0.95）→ 自动回复
        3. FAQ 模糊匹配（confidence≥0.80）→ 自动回复
        4. LLM 置信度 ≥ 80 → 自动回复
        5. LLM 置信度 50-79 → 建议回复（预填内容，人工确认）
        6. LLM 置信度 < 50 → 转人工

    Attributes:
        auto_reply_threshold: LLM 自动回复置信度阈值（0-100）
        sensitive_words: 敏感词列表
    """

    # 默认敏感词列表
    DEFAULT_SENSITIVE_WORDS: list[str] = [
        "投诉", "维权",
        "律师", "报警", "差评", "曝光",
    ]

    # FAQ 精确匹配置信度
    FAQ_EXACT_CONFIDENCE: float = 0.95
    # FAQ 模糊匹配自动回复阈值
    FAQ_FUZZY_AUTO_THRESHOLD: float = 0.80
    # LLM 建议回复下限阈值
    LLM_SUGGEST_THRESHOLD: int = 50

    # 转人工默认回复（人性化）
    TRANSFER_HUMAN_REPLY: str = (
        "这个问题我拿不太准，我帮你转给负责人看看哈，稍等~"
    )

    # 敏感词转人工回复（人性化）
    SENSITIVE_REPLY: str = (
        "这个问题比较重要，我直接帮你找负责人来处理，"
        "马上就到，稍等一下哈~"
    )

    def __init__(
        self,
        auto_reply_threshold: int = 80,
        sensitive_words: Optional[list[str]] = None,
    ) -> None:
        """初始化回复决策器

        Args:
            auto_reply_threshold: LLM 自动回复置信度阈值（0-100），默认 80
            sensitive_words: 敏感词列表，默认使用内置列表
        """
        self.auto_reply_threshold: int = auto_reply_threshold
        self.sensitive_words: list[str] = (
            sensitive_words if sensitive_words is not None
            else self.DEFAULT_SENSITIVE_WORDS.copy()
        )

    def _check_sensitive(self, content: str) -> bool:
        """检查内容是否包含敏感词

        Args:
            content: 待检查的内容

        Returns:
            True 表示包含敏感词，False 表示不包含
        """
        for word in self.sensitive_words:
            if word in content:
                return True
        return False

    def _find_sensitive_words(self, content: str) -> list[str]:
        """查找内容中包含的敏感词

        Args:
            content: 待检查的内容

        Returns:
            匹配到的敏感词列表
        """
        matched = []
        for word in self.sensitive_words:
            if word in content:
                matched.append(word)
        return matched

    def decide(
        self,
        user_message: str,
        faq_result: Optional[dict],
        llm_result: Optional[str],
        confidence: float,
    ) -> dict:
        """回复决策

        根据敏感词、FAQ 匹配结果和 LLM 置信度决定回复模式。

        Args:
            user_message: 用户原始消息
            faq_result: FAQ 匹配结果（来自 FAQEngine.search），
                格式 {"answer": "...", "faq_id": "...", "confidence": 0.95}
                未匹配时为 None
            llm_result: LLM 生成的回复内容，可能为 None
            confidence: LLM 置信度（0-100）

        Returns:
            决策结果，格式：
            {"mode": "auto"/"suggest"/"human",
             "content": "回复内容",
             "reason": "决策原因"}
        """
        # 规则1：敏感词检测（最高优先级）
        if self._check_sensitive(user_message):
            matched = self._find_sensitive_words(user_message)
            return {
                "mode": "human",
                "content": self.SENSITIVE_REPLY,
                "reason": f"检测到敏感词: {', '.join(matched)}，转人工处理",
            }

        # 规则2-3：FAQ 匹配
        if faq_result is not None:
            faq_confidence = faq_result.get("confidence", 0)

            # 规则2：FAQ 精确匹配
            if faq_confidence >= self.FAQ_EXACT_CONFIDENCE:
                return {
                    "mode": "auto",
                    "content": faq_result.get("answer", ""),
                    "reason": f"FAQ精确匹配(faq_id={faq_result.get('faq_id')}, 置信度={faq_confidence})",
                }

            # 规则3：FAQ 模糊匹配（置信度≥0.80）
            if faq_confidence >= self.FAQ_FUZZY_AUTO_THRESHOLD:
                return {
                    "mode": "auto",
                    "content": faq_result.get("answer", ""),
                    "reason": f"FAQ模糊匹配(faq_id={faq_result.get('faq_id')}, 置信度={faq_confidence})",
                }

        # 规则4-6：基于 LLM 置信度决策
        llm_content = llm_result if llm_result else self.TRANSFER_HUMAN_REPLY

        # 规则4：LLM 置信度 ≥ 阈值 → 自动回复
        if confidence >= self.auto_reply_threshold:
            return {
                "mode": "auto",
                "content": llm_content,
                "reason": f"LLM置信度{confidence}≥{self.auto_reply_threshold}，自动回复",
            }

        # 规则5：LLM 置信度 50-79 → 建议回复
        if confidence >= self.LLM_SUGGEST_THRESHOLD:
            return {
                "mode": "suggest",
                "content": llm_content,
                "reason": f"LLM置信度{confidence}在{self.LLM_SUGGEST_THRESHOLD}-{self.auto_reply_threshold}之间，建议人工确认",
            }

        # 规则6：LLM 置信度 < 50 → 转人工
        return {
            "mode": "human",
            "content": self.TRANSFER_HUMAN_REPLY,
            "reason": f"LLM置信度{confidence}<{self.LLM_SUGGEST_THRESHOLD}，转人工处理",
        }
