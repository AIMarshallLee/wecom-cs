"""安全合规模块

实现 7 项反封措施，防止企业微信账号被封：
1. 速率限制：≤20/hour, ≤100/day, ≥5s interval（单用户）
2. 工作时间限制：9:00-21:00 之外不自动回复
3. 人工行为模拟：3-15s 随机延迟 + 10% 故意打错字概率
4. 内容过滤：无链接/支付/引流话术，≤500 字
5. 渐进式上线：Phase 1 → 2 → 3 分阶段灰度
6. 健康检查：通过 /health 端点
7. 紧急停止：通过 API 或配置一键关闭自动回复

配置示例（config.yaml）:
    safety:
      enabled: true
      rate_limit:
        max_per_hour: 20
        max_per_day: 100
        min_interval_seconds: 5
      work_hours:
        start: 9
        end: 21
      human_simulate:
        min_delay: 3
        max_delay: 15
        typo_probability: 0.1
      content_filter:
        block_links: true
        block_payment: true
        block_drainage: true
        max_length: 500
      rollout:
        phase: 1          # 1=suggest模式(人工确认), 2=FAQ>90%自动, 3=FAQ+咨询>75%自动
      emergency_stop: false
"""

import re
import time
import random
import logging
from datetime import datetime, date
from typing import Optional, Dict, Any, Tuple
from collections import defaultdict

logger = logging.getLogger("wecom_cs")


class SafetyGuard:
    """安全合规守卫

    在消息发送前执行多重检查，确保符合反封策略。
    所有检查都可通过 config 开关，未配置时使用默认安全值。

    Attributes:
        config: safety 配置字典
        _user_send_times: 用户级发送时间记录 {user_id: [timestamp, ...]}
        _last_send_time: 用户级上次发送时间 {user_id: timestamp}
        _emergency_stop: 紧急停止标志
    """

    # 默认配置（保守值，未配置 config 时使用）
    DEFAULTS: dict = {
        "enabled": True,
        "rate_limit": {
            "max_per_hour": 20,
            "max_per_day": 100,
            "min_interval_seconds": 5,
        },
        "work_hours": {
            "start": 9,
            "end": 21,
        },
        "human_simulate": {
            "min_delay": 3,
            "max_delay": 15,
            "typo_probability": 0.1,
        },
        "content_filter": {
            "block_links": True,
            "block_payment": True,
            "block_drainage": True,
            "max_length": 500,
        },
        "rollout": {
            "phase": 1,
        },
        "emergency_stop": False,
    }

    # 引流话术关键词
    DRAINAGE_PATTERNS: list[str] = [
        r"加微[信]*",
        r"加我[微]*[信]*",
        r"私信我",
        r"私加",
        r"个人微[信]*",
        r"微信号",
        r"扫码加",
        r"二维码加",
        r"转[移]?到.*[私个]人",
        r"加群",
        r"扫码进群",
    ]

    # 支付相关关键词
    PAYMENT_PATTERNS: list[str] = [
        r"微信支付",
        r"支付宝",
        r"转账",
        r"银行卡",
        r"收款码",
        r"付款码",
        r"扫一扫.*付",
        r"红包",
    ]

    # URL 链接正则
    URL_PATTERN: re.Pattern = re.compile(
        r'https?://[^\s<>"{}|\\^`\[\]]+|www\.[^\s<>"{}|\\^`\[\]]+',
        re.IGNORECASE,
    )

    def __init__(self, config: dict = None) -> None:
        """初始化安全守卫

        Args:
            config: safety 配置字典，为空时使用默认配置
        """
        # 合并配置：默认值 < 用户配置
        self.config = self._merge_config(self.DEFAULTS, config or {})
        self._user_send_times: Dict[str, list] = defaultdict(list)
        self._last_send_time: Dict[str, float] = {}
        self._emergency_stop: bool = self.config.get("emergency_stop", False)

        # 预编译正则
        self._drainage_regex = [re.compile(p, re.IGNORECASE) for p in self.DRAINAGE_PATTERNS]
        self._payment_regex = [re.compile(p, re.IGNORECASE) for p in self.PAYMENT_PATTERNS]

        logger.info(
            f"SafetyGuard 初始化完成 | "
            f"限速: {self.config['rate_limit']['max_per_hour']}/h, "
            f"{self.config['rate_limit']['max_per_day']}/d | "
            f"工作时间: {self.config['work_hours']['start']}:00-{self.config['work_hours']['end']}:00 | "
            f"阶段: Phase {self.config['rollout']['phase']}"
        )

    @staticmethod
    def _merge_config(defaults: dict, override: dict) -> dict:
        """递归合并配置"""
        result = defaults.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = SafetyGuard._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    # ----------------------------------------------------------
    # 紧急停止
    # ----------------------------------------------------------

    def is_emergency_stopped(self) -> bool:
        """检查是否处于紧急停止状态"""
        return self._emergency_stop

    def emergency_stop(self) -> None:
        """触发紧急停止"""
        self._emergency_stop = True
        logger.warning("!!! 紧急停止已触发 !!! 自动回复已全部暂停")

    def resume(self) -> None:
        """解除紧急停止"""
        self._emergency_stop = False
        logger.info("紧急停止已解除，自动回复恢复运行")

    # ----------------------------------------------------------
    # 渐进式上线
    # ----------------------------------------------------------

    def get_phase(self) -> int:
        """获取当前上线阶段

        Phase 1: 所有回复走 suggest 模式（AI 预填，人工确认后发送）
        Phase 2: FAQ 置信度 >90% 的自动回复，其余 suggest
        Phase 3: FAQ + 咨询类问题置信度 >75% 的自动回复
        """
        return self.config.get("rollout", {}).get("phase", 1)

    def should_auto_reply(self, confidence: float, is_faq: bool) -> bool:
        """根据当前阶段判断是否允许自动回复

        Args:
            confidence: 回复置信度 (0-1)
            is_faq: 是否为 FAQ 匹配

        Returns:
            True 表示允许自动回复
        """
        phase = self.get_phase()

        if phase == 1:
            # Phase 1: 全部走人工确认
            return False
        elif phase == 2:
            # Phase 2: FAQ >90% 自动
            return is_faq and confidence >= 0.9
        elif phase == 3:
            # Phase 3: FAQ + 咨询 >75% 自动
            return confidence >= 0.75
        else:
            return False

    # ----------------------------------------------------------
    # 工作时间检查
    # ----------------------------------------------------------

    def is_work_hours(self) -> bool:
        """检查当前是否在工作时间内

        Returns:
            True 表示在工作时间内
        """
        work_cfg = self.config.get("work_hours", {})
        start_hour = work_cfg.get("start", 9)
        end_hour = work_cfg.get("end", 21)
        current_hour = datetime.now().hour
        return start_hour <= current_hour < end_hour

    # ----------------------------------------------------------
    # 速率限制
    # ----------------------------------------------------------

    def check_rate_limit(self, user_id: str) -> Tuple[bool, str]:
        """检查用户是否超过速率限制

        Args:
            user_id: 用户 ID

        Returns:
            (allowed, reason) allowed=True 表示可以发送
        """
        rl_cfg = self.config.get("rate_limit", {})
        max_per_hour = rl_cfg.get("max_per_hour", 20)
        max_per_day = rl_cfg.get("max_per_day", 100)
        min_interval = rl_cfg.get("min_interval_seconds", 5)

        now = time.time()
        send_times = self._user_send_times[user_id]

        # 清理 24 小时前的记录
        day_ago = now - 86400
        self._user_send_times[user_id] = [t for t in send_times if t > day_ago]
        send_times = self._user_send_times[user_id]

        # 检查最小间隔
        if user_id in self._last_send_time:
            elapsed = now - self._last_send_time[user_id]
            if elapsed < min_interval:
                wait = min_interval - elapsed
                return False, f"发送间隔不足，需等待 {wait:.1f}s"

        # 检查小时限制
        hour_ago = now - 3600
        hour_count = sum(1 for t in send_times if t > hour_ago)
        if hour_count >= max_per_hour:
            return False, f"超过小时限制 ({max_per_hour}/hour)"

        # 检查天限制
        day_count = len(send_times)
        if day_count >= max_per_day:
            return False, f"超过日限制 ({max_per_day}/day)"

        return True, ""

    def record_send(self, user_id: str) -> None:
        """记录一次发送（在发送成功后调用）

        Args:
            user_id: 用户 ID
        """
        now = time.time()
        self._user_send_times[user_id].append(now)
        self._last_send_time[user_id] = now

    # ----------------------------------------------------------
    # 内容过滤
    # ----------------------------------------------------------

    def filter_content(self, reply_text: str) -> Tuple[str, list[str]]:
        """过滤回复内容

        检查回复中是否包含不允许的内容，返回清洗后的文本和警告列表。

        Args:
            reply_text: 待发送的回复文本

        Returns:
            (filtered_text, warnings) filtered_text 是清洗后的文本
        """
        filter_cfg = self.config.get("content_filter", {})
        warnings = []

        if not filter_cfg.get("block_links", True):
            pass
        else:
            # 检测链接
            urls = self.URL_PATTERN.findall(reply_text)
            if urls:
                for url in urls:
                    reply_text = reply_text.replace(url, "")
                    warnings.append(f"移除链接: {url[:30]}...")

        if filter_cfg.get("block_drainage", True):
            # 检测引流话术
            for pattern in self._drainage_regex:
                matches = pattern.findall(reply_text)
                if matches:
                    for match in matches:
                        reply_text = pattern.sub("", reply_text)
                        warnings.append(f"移除引流话术: {match}")

        if filter_cfg.get("block_payment", True):
            # 检测支付内容
            for pattern in self._payment_regex:
                matches = pattern.findall(reply_text)
                if matches:
                    for match in matches:
                        reply_text = pattern.sub("", reply_text)
                        warnings.append(f"移除支付内容: {match}")

        # 长度限制
        max_length = filter_cfg.get("max_length", 500)
        if len(reply_text) > max_length:
            reply_text = reply_text[:max_length]
            warnings.append(f"截断到 {max_length} 字")

        # 清理多余空格
        reply_text = re.sub(r"\s{3,}", "\n", reply_text).strip()

        return reply_text, warnings

    # ----------------------------------------------------------
    # 人工行为模拟
    # ----------------------------------------------------------

    def get_send_delay(self) -> float:
        """获取发送延迟时间（秒）

        模拟人工回复时的思考时间。

        Returns:
            延迟秒数
        """
        sim_cfg = self.config.get("human_simulate", {})
        min_delay = sim_cfg.get("min_delay", 3)
        max_delay = sim_cfg.get("max_delay", 15)
        return random.uniform(min_delay, max_delay)

    def maybe_introduce_typo(self, text: str) -> str:
        """以一定概率在回复中引入打字错误

        模拟人工打字时的手误，让回复更自然。
        只在中文文本中交换相邻字符。

        Args:
            text: 原始回复文本

        Returns:
            可能包含打字错误的文本
        """
        sim_cfg = self.config.get("human_simulate", {})
        prob = sim_cfg.get("typo_probability", 0.1)

        if random.random() > prob or len(text) < 4:
            return text

        # 找到中文字符的位置（跳过标点和数字）
        chinese_positions = [
            i for i, c in enumerate(text)
            if '\u4e00' <= c <= '\u9fff'
        ]

        if len(chinese_positions) < 2:
            return text

        # 随机选一个位置，与相邻中文字符交换
        idx = random.choice(range(len(chinese_positions) - 1))
        pos1 = chinese_positions[idx]
        pos2 = chinese_positions[idx + 1]

        # 只交换相邻的（跳过标点的情况不交换）
        if pos2 - pos1 == 1:
            chars = list(text)
            chars[pos1], chars[pos2] = chars[pos2], chars[pos1]
            result = "".join(chars)
            logger.debug(f"引入打字错误: '{text[max(0,pos1-3):pos2+3]}' -> '{result[max(0,pos1-3):pos2+3]}'")
            return result

        return text

    # ----------------------------------------------------------
    # 综合检查（发送前调用）
    # ----------------------------------------------------------

    def pre_send_check(
        self,
        user_id: str,
        reply_text: str,
        confidence: float = 1.0,
        is_faq: bool = True,
    ) -> Dict[str, Any]:
        """发送前的综合安全检查

        在发送回复前调用，执行所有安全检查。

        Args:
            user_id: 用户 ID
            reply_text: 待发送的回复文本
            confidence: 回复置信度
            is_faq: 是否为 FAQ 匹配

        Returns:
            检查结果字典:
                - allowed: 是否允许发送
                - delay: 发送前等待秒数
                - filtered_text: 过滤后的文本
                - mode: 发送模式 (auto/suggest/block)
                - reason: 不允许发送时的原因
                - warnings: 内容过滤警告
        """
        result = {
            "allowed": True,
            "delay": 0,
            "filtered_text": reply_text,
            "mode": "auto",
            "reason": "",
            "warnings": [],
        }

        # 1. 紧急停止
        if self._emergency_stop:
            result["allowed"] = False
            result["mode"] = "block"
            result["reason"] = "紧急停止已触发"
            return result

        # 2. 工作时间检查
        if not self.is_work_hours():
            result["allowed"] = False
            result["mode"] = "block"
            result["reason"] = "非工作时间，不自动回复"
            logger.info(f"非工作时间，跳过自动回复: user={user_id}")
            return result

        # 3. 渐进式上线检查
        if not self.should_auto_reply(confidence, is_faq):
            result["mode"] = "suggest"
            result["reason"] = f"Phase {self.get_phase()} 阶段不满足自动回复条件"
            # suggest 模式仍然允许发送缓冲消息
            result["filtered_text"] = "我想想怎么回答你哈，稍等一下~"
            result["delay"] = self.get_send_delay()
            return result

        # 4. 速率限制
        allowed, reason = self.check_rate_limit(user_id)
        if not allowed:
            result["allowed"] = False
            result["mode"] = "block"
            result["reason"] = reason
            logger.warning(f"速率限制: user={user_id}, reason={reason}")
            return result

        # 5. 内容过滤
        filtered, warnings = self.filter_content(reply_text)
        result["filtered_text"] = self.maybe_introduce_typo(filtered)
        result["warnings"] = warnings

        # 6. 人工模拟延迟
        result["delay"] = self.get_send_delay()

        return result

    def post_send(self, user_id: str) -> None:
        """发送成功后调用，记录发送时间"""
        self.record_send(user_id)
