"""会话管理器

基于 Redis 的多轮对话上下文管理。
Redis 不可用时降级到内存字典，保证服务可用性。
"""

import json
import time
from typing import Optional

try:
    import redis as redis_lib
    REDIS_AVAILABLE: bool = True
except ImportError:
    redis_lib = None
    REDIS_AVAILABLE = False


class SessionManager:
    """会话管理器

    基于 Redis 管理多轮对话上下文。
    如果 Redis 不可用，自动降级到内存字典模式。

    特性：
        - 会话过期时间：30 分钟
        - 上下文最多保留最近 10 条消息
    """

    # 会话过期时间（秒）：30 分钟
    SESSION_TTL: int = 30 * 60
    # 上下文最大消息数
    MAX_MESSAGES: int = 10
    # Redis 键前缀
    KEY_PREFIX: str = "wecom_cs:session:"
    # 用户画像 Redis 键前缀
    PROFILE_KEY_PREFIX: str = "wecom_cs:profile:"
    # 用户画像过期时间（秒）：30 天
    PROFILE_TTL: int = 30 * 24 * 60 * 60

    # 转化阶段关键词（按阶段分组，从低到高）
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

    # 转化阶段顺序（只能向前推进）
    STAGE_ORDER: list[str] = ["new", "engaged", "interested", "considering", "converted"]

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        """初始化会话管理器

        尝试连接 Redis，如果连接失败则降级到内存字典模式。

        Args:
            redis_url: Redis 连接地址
        """
        self.redis_url: str = redis_url
        self._use_redis: bool = False
        self._redis = None
        # 内存降级模式的数据存储
        # 结构: {user_id: {"messages": [...], "last_access": timestamp}}
        self._memory_store: dict[str, dict] = {}
        # 内存降级模式的用户画像存储
        self._memory_profiles: dict[str, dict] = {}

        # 尝试连接 Redis
        if REDIS_AVAILABLE:
            try:
                self._redis = redis_lib.from_url(redis_url, decode_responses=True)
                # 测试连接是否可用
                self._redis.ping()
                self._use_redis = True
            except Exception:
                # Redis 不可用，降级到内存模式
                self._redis = None
                self._use_redis = False

    @property
    def use_redis(self) -> bool:
        """是否使用 Redis"""
        return self._use_redis

    def _make_key(self, user_id: str) -> str:
        """生成 Redis 存储键

        Args:
            user_id: 用户 ID

        Returns:
            Redis 键名
        """
        return f"{self.KEY_PREFIX}{user_id}"

    def get_context(self, user_id: str) -> list:
        """获取用户对话历史

        Args:
            user_id: 用户 ID

        Returns:
            对话历史列表，格式：
            [{"role": "user", "content": "..."},
             {"role": "assistant", "content": "..."}]
        """
        if self._use_redis:
            return self._get_context_redis(user_id)
        return self._get_context_memory(user_id)

    def _get_context_redis(self, user_id: str) -> list:
        """从 Redis 获取对话历史"""
        key = self._make_key(user_id)
        raw = self._redis.get(key)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def _get_context_memory(self, user_id: str) -> list:
        """从内存获取对话历史"""
        if user_id not in self._memory_store:
            return []

        store = self._memory_store[user_id]
        # 检查是否过期
        if time.time() - store["last_access"] > self.SESSION_TTL:
            # 过期，清除并返回空
            del self._memory_store[user_id]
            return []

        # 更新访问时间
        store["last_access"] = time.time()
        return store.get("messages", [])

    def add_message(self, user_id: str, role: str, content: str) -> None:
        """添加消息到会话

        Args:
            user_id: 用户 ID
            role: 消息角色 ("user" / "assistant")
            content: 消息内容
        """
        if self._use_redis:
            self._add_message_redis(user_id, role, content)
        else:
            self._add_message_memory(user_id, role, content)

    def _add_message_redis(self, user_id: str, role: str, content: str) -> None:
        """向 Redis 添加消息"""
        key = self._make_key(user_id)
        messages = self._get_context_redis(user_id)
        messages.append({"role": role, "content": content})

        # 只保留最近 MAX_MESSAGES 条
        if len(messages) > self.MAX_MESSAGES:
            messages = messages[-self.MAX_MESSAGES:]

        self._redis.setex(key, self.SESSION_TTL, json.dumps(messages, ensure_ascii=False))

    def _add_message_memory(self, user_id: str, role: str, content: str) -> None:
        """向内存添加消息"""
        if user_id not in self._memory_store:
            self._memory_store[user_id] = {
                "messages": [],
                "last_access": time.time(),
            }

        store = self._memory_store[user_id]
        store["messages"].append({"role": role, "content": content})

        # 只保留最近 MAX_MESSAGES 条
        if len(store["messages"]) > self.MAX_MESSAGES:
            store["messages"] = store["messages"][-self.MAX_MESSAGES:]

        store["last_access"] = time.time()

    def clear_context(self, user_id: str) -> None:
        """清空会话

        Args:
            user_id: 用户 ID
        """
        if self._use_redis:
            self._redis.delete(self._make_key(user_id))
        else:
            self._memory_store.pop(user_id, None)

    # ================================================================
    # 用户画像管理
    # ================================================================

    def _make_profile_key(self, user_id: str) -> str:
        """生成用户画像 Redis 键"""
        return f"{self.PROFILE_KEY_PREFIX}{user_id}"

    def _default_profile(self, user_id: str) -> dict:
        """生成默认用户画像"""
        return {
            "user_id": user_id,
            "first_seen": None,
            "last_seen": None,
            "message_count": 0,
            "interests": [],
            "conversion_stage": "new",
            "asked_topics": [],
        }

    def get_user_profile(self, user_id: str) -> dict:
        """获取用户画像

        Args:
            user_id: 用户 ID

        Returns:
            用户画像字典，包含：
            - message_count: 消息计数
            - interests: 兴趣话题列表
            - conversion_stage: 转化阶段 (new/engaged/interested/considering/converted)
            - asked_topics: 问过的话题
            - first_seen / last_seen: 时间戳
        """
        default = self._default_profile(user_id)
        if self._use_redis:
            raw = self._redis.get(self._make_profile_key(user_id))
            if raw:
                try:
                    profile = json.loads(raw)
                    # 合并默认值（防止旧数据缺字段）
                    default.update(profile)
                    return default
                except (json.JSONDecodeError, TypeError):
                    pass
            return default
        return self._memory_profiles.get(user_id, default)

    def _save_profile(self, user_id: str, profile: dict) -> None:
        """保存用户画像"""
        if self._use_redis:
            self._redis.setex(
                self._make_profile_key(user_id),
                self.PROFILE_TTL,
                json.dumps(profile, ensure_ascii=False),
            )
        else:
            self._memory_profiles[user_id] = profile

    def record_interaction(
        self,
        user_id: str,
        content: str,
        faq_question: str = "",
    ) -> dict:
        """记录用户交互，更新画像

        每次用户发消息时调用，自动更新：
        - 消息计数
        - 最后活跃时间
        - 兴趣话题（来自 FAQ 匹配的问题）
        - 转化阶段（基于关键词检测）

        Args:
            user_id: 用户 ID
            content: 用户消息内容
            faq_question: FAQ 匹配到的问题（可选）

        Returns:
            更新后的用户画像
        """
        profile = self.get_user_profile(user_id)
        now = time.time()

        # 首次访问
        if not profile.get("first_seen"):
            profile["first_seen"] = now
        profile["last_seen"] = now
        profile["message_count"] = profile.get("message_count", 0) + 1

        # 记录兴趣话题
        if faq_question:
            asked = profile.get("asked_topics", [])
            if faq_question not in asked:
                asked.append(faq_question)
                profile["asked_topics"] = asked[-10:]  # 只保留最近 10 个

        # 基于关键词更新转化阶段（只能向前推进）
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

        # 消息数达到 3 条自动升级到 engaged
        if profile.get("conversion_stage") == "new" and profile["message_count"] >= 3:
            profile["conversion_stage"] = "engaged"

        self._save_profile(user_id, profile)
        return profile
