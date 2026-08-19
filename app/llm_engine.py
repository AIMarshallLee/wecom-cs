"""LLM 引擎

支持多模型切换（OpenAI / Anthropic / 通义千问），使用 httpx 异步客户端调用 API。
提供客服回复生成能力，支持知识库上下文注入和对话历史。
"""

import re
import os
import json
import asyncio
from typing import Optional

import httpx


class LLMEngine:
    """LLM 调用引擎

    支持 OpenAI、Anthropic、通义千问(Qwen) 三种模型提供商。
    使用 httpx 异步客户端调用 API，超时时间 15 秒。

    Attributes:
        provider: 模型提供商 ("openai" / "anthropic" / "qwen")
        api_url: API 接口地址
        api_key: API 密钥
        model: 模型名称
    """

    # API 调用超时时间（秒）
    TIMEOUT: float = 15.0

    # 降级回复模板（人性化）
    FALLBACK_REPLY: str = (
        "这个问题我一下子答不上来，我帮你问问负责人哈，稍等~"
    )

    # 默认系统提示词（通用模板，建议通过 prompts/system_prompt.txt 自定义）
    DEFAULT_SYSTEM_PROMPT: str = (
        "你是一个客服小助手，负责跟来咨询的朋友聊天、解答问题。\n\n"
        "【说话风格】\n"
        "- 像微信聊天一样自然，说大白话，别端着\n"
        "- 别用'您好''请问''非常抱歉'这种官方客服腔\n"
        "- 简短直接，能一句话说清的别写三句\n"
        "- 该用emoji就用，别刷屏就行\n"
        "- 别写小标题、别分点列、别用书面语\n\n"
        "【聊天原则】\n"
        "- 先帮用户解决问题，别急着推销\n"
        "- 第一次来的用户，热情但不啰嗦\n"
        "- 不知道的就说不知道，可以说'我帮你问问负责人'\n"
        "- 回复别超过150字\n\n"
        "绝对不要在回复里加[置信度]之类的标记，自然回复就行。\n\n"
        "提示：请在 prompts/system_prompt.txt 中配置你的品牌专属提示词，"
        "包含品牌介绍、产品矩阵、价格策略、转化引导等。"
        "参考 prompts/system_prompt.example.txt 获取模板。"
    )

    # 自定义提示词文件路径（优先加载）
    PROMPT_FILE: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "prompts", "system_prompt.txt"
    )

    def __init__(
        self,
        provider: str,
        api_url: str,
        api_key: str,
        model: str,
    ) -> None:
        """初始化 LLM 引擎

        Args:
            provider: 模型提供商，支持 "openai" / "anthropic" / "qwen"
            api_url: API 接口地址
            api_key: API 密钥
            model: 模型名称
        """
        self.provider: str = provider
        self.api_url: str = api_url
        self.api_key: str = api_key
        self.model: str = model
        self.system_prompt: str = self._load_system_prompt()

    @classmethod
    def _load_system_prompt(cls) -> str:
        """从文件加载系统提示词，文件不存在时用默认通用模板

        优先读取 prompts/system_prompt.txt（品牌专属提示词），
        如果文件不存在则使用代码内置的 DEFAULT_SYSTEM_PROMPT。

        Returns:
            系统提示词字符串
        """
        try:
            if os.path.exists(cls.PROMPT_FILE):
                with open(cls.PROMPT_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        return content
        except Exception:
            pass
        return cls.DEFAULT_SYSTEM_PROMPT

    def _build_headers(self) -> dict:
        """构建请求头（根据 provider 不同）

        Returns:
            HTTP 请求头字典
        """
        headers = {"Content-Type": "application/json"}

        if self.provider == "openai":
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.provider == "qwen":
            # 通义千问使用 OpenAI 兼容接口，认证方式相同
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.provider == "anthropic":
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"

        return headers

    def _build_payload(
        self,
        messages: list[dict],
        context: Optional[str] = None,
    ) -> tuple[dict, Optional[str]]:
        """构建请求体（根据 provider 不同）

        Args:
            messages: 消息列表
            context: 额外知识上下文

        Returns:
            (请求体字典, 系统提示词)，系统提示词仅 Anthropic 单独传递
        """
        # 注入知识上下文到系统消息
        system_content = self.system_prompt
        if context:
            system_content += f"\n\n【知识库参考】\n{context}"

        if self.provider == "anthropic":
            # Anthropic 的 system 字段单独传递，messages 中不包含 system
            chat_messages = [m for m in messages if m.get("role") != "system"]
            payload = {
                "model": self.model,
                "messages": chat_messages,
                "system": system_content,
                "max_tokens": 500,
                "temperature": 0.7,
            }
            return payload, system_content
        else:
            # OpenAI / Qwen 格式：system 作为第一条消息
            chat_messages = [{"role": "system", "content": system_content}]
            chat_messages.extend(
                m for m in messages if m.get("role") != "system"
            )
            payload = {
                "model": self.model,
                "messages": chat_messages,
                "max_tokens": 500,
                "temperature": 0.7,
            }
            return payload, None

    def _parse_response(self, data: dict) -> str:
        """解析 API 响应（根据 provider 不同）

        Args:
            data: API 返回的 JSON 数据

        Returns:
            模型生成的文本内容
        """
        if self.provider == "anthropic":
            # Anthropic: data["content"][0]["text"]
            content_blocks = data.get("content", [])
            if content_blocks and isinstance(content_blocks, list):
                return content_blocks[0].get("text", "")
            return ""
        else:
            # OpenAI / Qwen: data["choices"][0]["message"]["content"]
            choices = data.get("choices", [])
            if choices and isinstance(choices, list):
                return choices[0].get("message", {}).get("content", "")
            return ""

    def _calc_confidence(self, content: str) -> float:
        """根据回复内容质量启发式计算置信度

        Args:
            content: 回复内容

        Returns:
            置信度 0.0~1.0
        """
        if not content or not content.strip():
            return 0.0

        text = content.strip()

        # 不确定性标记（降低置信度）
        uncertainty_markers = [
            "不确定", "无法回答", "不清楚", "不知道",
            "不太确定", "不太清楚", "不太了解",
            "建议转人工", "请联系", "无法确定", "暂无",
            "我帮你问问", "帮你问",
        ]
        for marker in uncertainty_markers:
            if marker in text:
                return 0.45

        # 极短回复
        if len(text) < 5:
            return 0.2

        # 包含具体数字/价格（更有可能是事实性回答）
        import re as _re
        if _re.search(r'\d+元|\d+块|\d+%|\d+天|\d+月\d+日|¥\d+', text):
            return 0.85

        # 回复过短
        if len(text) < 15:
            return 0.55

        # 正常回复
        return 0.72

    async def generate(
        self,
        messages: list[dict],
        context: Optional[str] = None,
    ) -> tuple[str, float]:
        """调用 LLM API 生成回复

        使用 httpx 异步客户端调用 API，超时 15 秒。
        API 超时或错误时返回降级回复。

        Args:
            messages: 消息列表，格式：
                [{"role": "user", "content": "..."}, ...]
            context: 额外知识上下文（注入到系统提示词）

        Returns:
            (回复内容, 置信度) 元组
        """
        payload, _ = self._build_payload(messages, context)
        headers = self._build_headers()

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()

            content = self._parse_response(data)
            if not content:
                return self.FALLBACK_REPLY, 0.0

            confidence = self._calc_confidence(content)
            return content, confidence

        except httpx.TimeoutException:
            return self.FALLBACK_REPLY, 0.0
        except httpx.HTTPError:
            return self.FALLBACK_REPLY, 0.0
        except Exception:
            return self.FALLBACK_REPLY, 0.0

    async def generate_reply(
        self,
        user_message: str,
        knowledge_context: str,
        conversation_history: list[dict],
    ) -> tuple[str, float]:
        """生成客服回复

        构造系统提示词、注入知识库上下文和对话历史，调用 LLM 生成回复。
        置信度根据回复质量自评（0-1）。

        Args:
            user_message: 用户消息
            knowledge_context: 知识库上下文信息
            conversation_history: 对话历史，格式：
                [{"role": "user", "content": "..."},
                 {"role": "assistant", "content": "..."}]

        Returns:
            (回复内容, 置信度) 元组
        """
        # 构建消息列表
        messages: list[dict] = []

        # 注入对话历史
        for msg in conversation_history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })

        # 添加当前用户消息
        messages.append({"role": "user", "content": user_message})

        # 调用 generate，注入知识库上下文
        raw_content, heuristic_confidence = await self.generate(
            messages, context=knowledge_context
        )

        # 解析模型自评的置信度
        confidence, clean_content = self._parse_self_confidence(
            raw_content, heuristic_confidence
        )

        return clean_content, confidence

    def _parse_self_confidence(
        self,
        content: str,
        fallback_confidence: float,
    ) -> tuple[float, str]:
        """解析模型自评的置信度

        从回复内容中提取 [置信度:0.X] 格式的自评分数。
        如果无法解析，则使用启发式置信度作为降级值。

        Args:
            content: 原始回复内容
            fallback_confidence: 降级置信度

        Returns:
            (置信度, 清理后的回复内容)
        """
        # 匹配 [置信度:0.85] 或 [置信度:0.85] 或 [confidence:0.85]
        pattern = r"\[置信度[:：]\s*(0?\.\d+|0\.\d+|1\.0|1)\]|\[confidence[:：]\s*(0?\.\d+|0\.\d+|1\.0|1)\]"
        match = re.search(pattern, content, re.IGNORECASE)

        if match:
            # 提取置信度数值
            confidence_str = match.group(1) or match.group(2)
            try:
                confidence = float(confidence_str)
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, TypeError):
                confidence = fallback_confidence

            # 清理回复内容中的置信度标记
            clean_content = re.sub(pattern, "", content).strip()
            return confidence, clean_content

        # 未找到自评标记，使用启发式置信度
        return fallback_confidence, content.strip()
