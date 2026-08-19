"""FAQ 关键词匹配引擎

支持精确匹配和模糊匹配，从 JSON 文件加载 FAQ 数据，并支持热重载。
业务场景：企业微信智能客服 + FAQ 知识库匹配 + LLM 智能回复。
"""

import json
import os
import time
import difflib
from typing import Optional


class FAQEngine:
    """FAQ 关键词匹配引擎

    从 JSON 文件加载 FAQ 数据，支持精确匹配、模糊匹配和热重载。

    FAQ 数据格式：
        {
            "faqs": [
                {
                    "id": "1",
                    "keywords": ["价格", "多少钱", "费用"],
                    "question": "课程价格",
                    "answer": "课程价格是..."
                }
            ]
        }
    """

    # 精确匹配的置信度
    EXACT_CONFIDENCE = 0.95
    # 模糊匹配的相似度阈值
    FUZZY_THRESHOLD = 0.6

    def __init__(self, faq_file: str) -> None:
        """初始化 FAQ 引擎

        Args:
            faq_file: FAQ 数据 JSON 文件路径
        """
        self.faq_file: str = faq_file
        self.faqs: list[dict] = []
        # 记录文件最后修改时间，用于热重载检测
        self._last_mtime: float = 0.0
        self._load_faqs()

    def _load_faqs(self) -> None:
        """从 JSON 文件加载 FAQ 数据"""
        if not os.path.exists(self.faq_file):
            self.faqs = []
            self._last_mtime = 0.0
            return

        # 记录文件修改时间，用于热重载
        self._last_mtime = os.path.getmtime(self.faq_file)

        with open(self.faq_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 兼容两种格式：{"faqs": [...]} 或 [...]（纯列表）
            if isinstance(data, dict):
                self.faqs = data.get("faqs", [])
            elif isinstance(data, list):
                self.faqs = data
            else:
                self.faqs = []

    def _check_reload(self) -> None:
        """检查文件是否修改，若修改则热重载 FAQ 数据"""
        if not os.path.exists(self.faq_file):
            return

        current_mtime = os.path.getmtime(self.faq_file)
        if current_mtime != self._last_mtime:
            self._load_faqs()

    def exact_match(self, content: str) -> Optional[dict]:
        """关键词精确包含匹配

        遍历所有 FAQ 条目，检查用户输入是否精确包含某个关键词。

        Args:
            content: 用户输入内容

        Returns:
            匹配到的 FAQ 结果字典，格式：
            {"answer": "...", "faq_id": "...", "confidence": 0.95}
            未匹配到时返回 None
        """
        self._check_reload()

        for faq in self.faqs:
            keywords: list[str] = faq.get("keywords", [])
            for keyword in keywords:
                # 关键词精确包含在用户输入中
                if keyword in content:
                    return {
                        "answer": faq.get("answer", ""),
                        "question": faq.get("question", ""),
                        "faq_id": faq.get("id", ""),
                        "confidence": self.EXACT_CONFIDENCE,
                    }
        return None

    def fuzzy_match(self, content: str) -> Optional[dict]:
        """模糊匹配

        使用 difflib.SequenceMatcher 计算用户输入与 FAQ 关键词/问题的相似度，
        相似度达到阈值(0.6)则返回匹配结果。

        Args:
            content: 用户输入内容

        Returns:
            匹配到的 FAQ 结果字典，格式：
            {"answer": "...", "faq_id": "...", "confidence": 0.6~0.94}
            未匹配到时返回 None
        """
        self._check_reload()

        best_ratio: float = 0.0
        best_faq: Optional[dict] = None

        matcher = difflib.SequenceMatcher()
        matcher.set_seq2(content)

        for faq in self.faqs:
            keywords: list[str] = faq.get("keywords", [])
            question: str = faq.get("question", "")

            # 计算用户输入与每个关键词的相似度
            max_kw_ratio: float = 0.0
            for keyword in keywords:
                matcher.set_seq1(keyword)
                ratio = matcher.ratio()
                if ratio > max_kw_ratio:
                    max_kw_ratio = ratio

            # 计算用户输入与问题的相似度
            matcher.set_seq1(question)
            question_ratio = matcher.ratio()

            # 取关键词和问题相似度的最大值
            ratio = max(max_kw_ratio, question_ratio)

            if ratio > best_ratio:
                best_ratio = ratio
                best_faq = faq

        # 相似度达到阈值才返回
        if best_faq is not None and best_ratio >= self.FUZZY_THRESHOLD:
            # 模糊匹配的置信度基于相似度，但不超过精确匹配的 0.95
            confidence = min(best_ratio, 0.94)
            return {
                "answer": best_faq.get("answer", ""),
                "question": best_faq.get("question", ""),
                "faq_id": best_faq.get("id", ""),
                "confidence": round(confidence, 2),
            }

        return None

    def search(self, content: str) -> Optional[dict]:
        """搜索 FAQ，先精确后模糊

        Args:
            content: 用户输入内容

        Returns:
            匹配到的 FAQ 结果字典，格式：
            {"answer": "...", "faq_id": "...", "confidence": 0.95}
            未匹配到时返回 None
        """
        # 先尝试精确匹配
        result = self.exact_match(content)
        if result is not None:
            return result

        # 精确匹配失败，尝试模糊匹配
        result = self.fuzzy_match(content)
        if result is not None:
            return result

        return None
