"""知识库管理

加载和管理知识付费社群的 FAQ、文档和回复模板。
支持知识检索、FAQ 增删和持久化保存。
"""

import json
import os
import difflib
from typing import Optional


class KnowledgeBase:
    """知识库管理器

    管理 FAQ、文档和回复模板，提供知识检索功能。

    目录结构：
        knowledge/
        ├── faq.json              # FAQ 数据
        ├── documents/            # 文档目录(.txt/.md)
        └── templates/            # 回复模板目录
            ├── greeting.txt
            ├── transfer_human.txt
            └── sensitive_reply.txt
    """

    def __init__(self, base_path: str) -> None:
        """初始化知识库

        Args:
            base_path: knowledge/ 目录的路径
        """
        self.base_path: str = base_path
        self.faq_file: str = os.path.join(base_path, "faq.json")
        self.documents_path: str = os.path.join(base_path, "documents")
        self.templates_path: str = os.path.join(base_path, "templates")

        # 缓存已加载的数据
        self._faqs: list[dict] = []
        self._documents: list[dict] = []
        self._templates: dict[str, str] = {}

    def load_faq(self) -> list:
        """加载 faq.json

        Returns:
            FAQ 列表，每条格式：
            {"id": "1", "keywords": [...], "question": "...", "answer": "..."}
        """
        if not os.path.exists(self.faq_file):
            self._faqs = []
            return self._faqs

        with open(self.faq_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 兼容两种格式：{"faqs": [...]} 或 [...]（纯列表）
            if isinstance(data, dict):
                self._faqs = data.get("faqs", [])
            elif isinstance(data, list):
                self._faqs = data
            else:
                self._faqs = []
        return self._faqs

    def load_documents(self) -> list:
        """加载 documents/ 下的所有 .txt/.md 文件

        Returns:
            文档列表，每条格式：
            {"title": "文件名", "content": "文件内容"}
        """
        self._documents = []

        if not os.path.exists(self.documents_path):
            return self._documents

        for filename in sorted(os.listdir(self.documents_path)):
            if filename.endswith(".txt") or filename.endswith(".md"):
                filepath = os.path.join(self.documents_path, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                self._documents.append({
                    "title": filename,
                    "content": content,
                })

        return self._documents

    def load_templates(self) -> dict:
        """加载 templates/ 下的模板文件

        支持的模板文件：greeting、transfer_human、sensitive_reply。
        文件名（不含扩展名）作为键，文件内容作为值。

        Returns:
            模板字典，格式：
            {"greeting": "...", "transfer_human": "...", "sensitive_reply": "..."}
        """
        self._templates = {}

        if not os.path.exists(self.templates_path):
            return self._templates

        for filename in sorted(os.listdir(self.templates_path)):
            filepath = os.path.join(self.templates_path, filename)
            if os.path.isfile(filepath):
                # 文件名（不含扩展名）作为键
                key = os.path.splitext(filename)[0]
                with open(filepath, "r", encoding="utf-8") as f:
                    self._templates[key] = f.read()

        return self._templates

    def search(self, query: str, top_k: int = 3) -> list:
        """在所有知识中搜索相关内容

        在 FAQ 和文档中进行关键词匹配和模糊匹配，返回最相关的 top_k 条结果。

        Args:
            query: 搜索查询
            top_k: 返回结果数量上限

        Returns:
            相关结果列表，每条格式：
            {"source": "faq"/"document", "title": "...",
             "content": "...", "score": 0.0~1.0}
        """
        results: list[dict] = []

        # 确保 FAQ 和文档已加载
        if not self._faqs:
            self.load_faq()
        if not self._documents:
            self.load_documents()

        # 在 FAQ 中搜索
        for faq in self._faqs:
            keywords: list = faq.get("keywords", [])
            question: str = faq.get("question", "")
            answer: str = faq.get("answer", "")

            score = self._calc_relevance(query, keywords, question)
            if score > 0:
                results.append({
                    "source": "faq",
                    "title": question,
                    "content": answer,
                    "score": score,
                })

        # 在文档中搜索
        for doc in self._documents:
            title: str = doc.get("title", "")
            content: str = doc.get("content", "")
            score = self._calc_relevance(query, [], title, content)
            if score > 0:
                results.append({
                    "source": "document",
                    "title": title,
                    "content": content[:500],  # 文档内容截断
                    "score": score,
                })

        # 按相关度降序排序，取 top_k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _calc_relevance(
        self,
        query: str,
        keywords: list,
        primary_text: str,
        secondary_text: str = "",
    ) -> float:
        """计算查询与知识条目的相关度

        Args:
            query: 搜索查询
            keywords: 关键词列表
            primary_text: 主要文本（FAQ 问题或文档标题）
            secondary_text: 次要文本（文档内容）

        Returns:
            相关度分数 0.0~1.0
        """
        score: float = 0.0

        # 关键词精确匹配（权重最高）
        for kw in keywords:
            if kw in query:
                score = max(score, 0.95)

        # 主要文本关键词包含
        for char in primary_text:
            if char in query:
                score = max(score, 0.7)

        # 模糊匹配
        matcher = difflib.SequenceMatcher()
        matcher.set_seq2(query)

        if primary_text:
            matcher.set_seq1(primary_text)
            ratio = matcher.ratio()
            if ratio >= 0.6:
                score = max(score, ratio)

        if secondary_text:
            # 文档内容分块计算
            for kw in keywords:
                if kw in secondary_text:
                    score = max(score, 0.6)
            matcher.set_seq1(secondary_text[:200])
            ratio = matcher.ratio()
            if ratio >= 0.6:
                score = max(score, ratio * 0.8)

        return round(score, 2)

    def add_faq(self, keywords: list, question: str, answer: str) -> str:
        """添加 FAQ 条目

        Args:
            keywords: 关键词列表
            question: 问题
            answer: 答案

        Returns:
            新增 FAQ 的 ID
        """
        if not self._faqs:
            self.load_faq()

        # 生成新 ID（取现有最大 ID + 1）
        max_id = 0
        for faq in self._faqs:
            try:
                fid = int(faq.get("id", "0"))
                if fid > max_id:
                    max_id = fid
            except (ValueError, TypeError):
                continue

        new_id = str(max_id + 1)
        new_faq = {
            "id": new_id,
            "keywords": keywords,
            "question": question,
            "answer": answer,
        }
        self._faqs.append(new_faq)

        return new_id

    def save_faq(self) -> None:
        """保存 FAQ 到 faq.json"""
        data = {"faqs": self._faqs}
        with open(self.faq_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
