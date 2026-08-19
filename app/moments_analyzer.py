"""朋友圈画像分析器

通过分析用户微信朋友圈内容，提取职业、兴趣、消费力、沟通风格等标签，
存入用户画像，为千人千面提供更丰富的用户洞察。

架构分层：
    MomentsAnalyzer  — 编排：读取朋友圈 → LLM 提取标签 → 存入画像
    MomentsReader    — UI 自动化：通过 macOS Accessibility API 读取朋友圈文本

使用方式：
    analyzer = MomentsAnalyzer(llm_engine, profile_service)
    tags = await analyzer.analyze_user("user_id", "张三")
    # tags = ["电商创业者", "90后", "健身爱好者", "中等消费力"]
"""

import json
import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger("wecom_cs")


class MomentsReader:
    """朋友圈文本读取器

    通过 macOS Accessibility API（System Events + AppleScript）
    自动操作微信，读取指定用户的朋友圈文本内容。

    依赖：
        - macOS 系统
        - 微信已登录且在前台运行
        - 已授予终端/Python 辅助功能权限（系统设置 → 隐私 → 辅助功能）

    注意：
        WeChat UI 结构会随版本变化，这里使用通用 AX 属性遍历，
        如果某版本 UI 变化导致读取失败，会返回空列表并记录日志。
    """

    # AppleScript 模板：激活微信
    ACTIVATE_SCRIPT = """
    tell application "WeChat" to activate
    delay 1
    """

    # 读取当前屏幕所有可见文本元素的 AppleScript
    # 通过 System Events 遍历微信窗口的 AX UI 元素
    READ_TEXT_SCRIPT = """
    tell application "System Events"
        tell process "WeChat"
            set visibleText to ""
            try
                -- 获取微信主窗口
                set mainWindow to window 1

                -- 递归遍历所有 UI 元素，提取文本
                set visibleText to my walkElements(mainWindow)
            on error errMsg
                -- 降级：尝试读取整个窗口的 AXValue
                try
                    set visibleText to value of window 1
                end try
            end try

            return visibleText
        end tell
    end tell

    on walkElements(elem)
        set result to ""
        try
            -- 尝试获取元素的 AXValue
            set elemValue to value of elem
            if elemValue is not missing value and elemValue is not "" then
                set result to elemValue & "\\n"
            end if
        end try

        -- 递归遍历子元素
        try
            set children to UI elements of elem
            repeat with child in children
                set result to result & my walkElements(child)
            end repeat
        end try

        return result
    end walkElements
    """

    # 滚动朋友圈的 AppleScript（模拟向下滚动）
    SCROLL_SCRIPT = """
    tell application "System Events"
        tell process "WeChat"
            try
                -- 模拟 Page Down 键滚动
                key code 125 using {command down}
                delay 1
            end try
        end tell
    end tell
    """

    def __init__(self, max_scrolls: int = 5, scroll_delay: float = 2.0) -> None:
        """初始化朋友圈读取器

        Args:
            max_scrolls: 最多滚动次数（每次读取一屏内容后滚动）
            scroll_delay: 每次滚动后等待时间（秒）
        """
        self.max_scrolls = max_scrolls
        self.scroll_delay = scroll_delay

    def _run_applescript(self, script: str) -> str:
        """执行 AppleScript 并返回输出

        Args:
            script: AppleScript 脚本内容

        Returns:
            脚本输出文本
        """
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"AppleScript 执行失败: {result.stderr[:200]}")
                return ""
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.warning("AppleScript 执行超时")
            return ""
        except Exception as e:
            logger.warning(f"AppleScript 执行异常: {e}")
            return ""

    def read_moments(self, wechat_name: str) -> list[str]:
        """读取指定用户的朋友圈文本

        流程：
        1. 激活微信
        2. 搜索用户名
        3. 进入朋友圈页面
        4. 逐屏读取文本并滚动
        5. 返回所有收集到的文本片段

        Args:
            wechat_name: 微信昵称或备注名

        Returns:
            朋友圈文本片段列表
        """
        # 注意：实际的微信 UI 自动化比较复杂，
        # 这里提供基础框架，具体搜索和导航逻辑
        # 可能需要根据微信版本调整

        # 1. 激活微信
        self._run_applescript(self.ACTIVATE_SCRIPT)
        time.sleep(1)

        # 2. 搜索用户（模拟 Cmd+F 输入名字）
        search_script = f"""
        tell application "System Events"
            tell process "WeChat"
                -- 搜索用户
                keystroke "f" using {{command down}}
                delay 1
                keystroke "{wechat_name}"
                delay 2
                -- 回车搜索
                key code 36
                delay 2
            end tell
        end tell
        """
        self._run_applescript(search_script)

        # 3. 进入朋友圈（需要点击用户头像进入资料页，再点击朋友圈）
        # 这部分 UI 操作依赖微信具体版本，先记录日志
        logger.info(f"尝试读取 {wechat_name} 的朋友圈...")

        # 4. 逐屏读取并滚动
        all_texts = []
        for i in range(self.max_scrolls):
            text = self._run_applescript(self.READ_TEXT_SCRIPT)
            if text:
                all_texts.append(text)
            self._run_applescript(self.SCROLL_SCRIPT)
            time.sleep(self.scroll_delay)

        logger.info(f"读取到 {len(all_texts)} 屏朋友圈内容")
        return all_texts

    def read_moments_from_text(self, raw_text: str) -> list[str]:
        """从已有文本中提取朋友圈内容（用于测试或手动输入）

        Args:
            raw_text: 朋友圈原始文本

        Returns:
            文本片段列表
        """
        if not raw_text:
            return []
        # 按换行分割，过滤空行
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        return lines


class MomentsAnalyzer:
    """朋友圈画像分析器

    读取用户朋友圈文本，通过 LLM 提取标签，存入用户画像。

    Attributes:
        llm_engine: LLM 引擎实例
        profile_service: 用户画像服务实例
        reader: 朋友圈文本读取器
    """

    # 标签类别
    TAG_CATEGORIES = {
        "职业": "用户的职业或行业，如'电商创业'、'程序员'、'教师'",
        "年龄段": "如'90后'、'00后'、'80后'",
        "兴趣": "用户的兴趣爱好，如'健身'、'读书'、'旅行'、'摄影'",
        "消费力": "消费能力评估，如'高消费'、'中等消费'、'性价比导向'",
        "风格": "沟通风格偏好，如'直接'、'温和'、'热情'、'理性'",
    }

    # LLM 分析 prompt 模板
    ANALYSIS_PROMPT_TEMPLATE = (
        "你是一个用户画像分析助手。请分析以下微信朋友圈内容，"
        "提取用户的画像标签。\n\n"
        "【标签类别】\n"
        "{categories}\n\n"
        "【朋友圈内容】\n"
        "{moments_text}\n\n"
        "【输出要求】\n"
        "1. 只输出标签，用逗号分隔\n"
        "2. 每个标签不超过6个字\n"
        "3. 最多输出8个标签\n"
        "4. 不要输出分析过程，直接输出标签列表\n"
        "5. 如果内容太少无法判断，输出空字符串\n"
    )

    # 朋友圈内容最大长度（超出截断）
    MAX_MOMENTS_LENGTH: int = 3000

    # 触发 LLM 分析的最少文本长度
    MIN_TEXT_LENGTH_FOR_ANALYSIS: int = 20

    def __init__(
        self,
        llm_engine=None,
        profile_service=None,
        reader: Optional[MomentsReader] = None,
    ) -> None:
        """初始化朋友圈分析器

        Args:
            llm_engine: LLM 引擎实例
            profile_service: 用户画像服务实例
            reader: 朋友圈读取器（为 None 时创建默认实例）
        """
        self.llm_engine = llm_engine
        self.profile_service = profile_service
        self.reader = reader or MomentsReader()

    def _build_analysis_prompt(self, moments_text: str) -> str:
        """构建 LLM 分析 prompt

        Args:
            moments_text: 朋友圈原始文本

        Returns:
            LLM 分析 prompt
        """
        categories = "\n".join(
            f"- {cat}: {desc}" for cat, desc in self.TAG_CATEGORIES.items()
        )

        # 截断超长内容
        if len(moments_text) > self.MAX_MOMENTS_LENGTH:
            moments_text = moments_text[:self.MAX_MOMENTS_LENGTH]

        return self.ANALYSIS_PROMPT_TEMPLATE.format(
            categories=categories,
            moments_text=moments_text,
        )

    async def extract_tags(self, moments_text: str) -> list[str]:
        """从朋友圈文本中提取画像标签

        使用 LLM 分析朋友圈内容，提取职业、兴趣、消费力等标签。

        Args:
            moments_text: 朋友圈原始文本

        Returns:
            标签列表，如 ["电商创业", "90后", "健身", "中等消费"]
        """
        if not self.llm_engine:
            logger.warning("LLM 引擎不可用，无法分析朋友圈")
            return []

        if len(moments_text) < self.MIN_TEXT_LENGTH_FOR_ANALYSIS:
            logger.info("朋友圈内容太少，跳过分析")
            return []

        try:
            prompt = self._build_analysis_prompt(moments_text)
            reply, confidence = await self.llm_engine.generate_reply(
                prompt, "", []
            )

            if not reply or not reply.strip():
                return []

            # 解析 LLM 输出的标签
            tags = [
                tag.strip()
                for tag in reply.strip().split("，")
                if tag.strip() and len(tag.strip()) <= 6
            ]

            # 兼容英文逗号
            if len(tags) <= 1:
                tags = [
                    tag.strip()
                    for tag in reply.strip().split(",")
                    if tag.strip() and len(tag.strip()) <= 6
                ]

            # 去重并限制数量
            seen = set()
            unique_tags = []
            for tag in tags:
                if tag not in seen:
                    seen.add(tag)
                    unique_tags.append(tag)
                    if len(unique_tags) >= 8:
                        break

            logger.info(f"朋友圈标签提取完成: {unique_tags}")
            return unique_tags

        except Exception as e:
            logger.warning(f"朋友圈标签提取失败: {e}")
            return []

    async def analyze_user(
        self,
        user_id: str,
        wechat_name: str = "",
        moments_text: str = "",
    ) -> list[str]:
        """分析用户朋友圈并存储标签

        全流程：读取朋友圈 → LLM 提取标签 → 存入用户画像

        可以通过两种方式提供朋友圈内容：
        1. 传入 moments_text（直接提供文本，用于测试或批量处理）
        2. 传入 wechat_name（自动通过 UI 读取）

        Args:
            user_id: 用户 ID
            wechat_name: 微信昵称（用于 UI 自动化读取）
            moments_text: 朋友圈文本（直接提供时跳过 UI 读取）

        Returns:
            提取到的标签列表
        """
        # 获取朋友圈文本
        if moments_text:
            # 直接使用提供的文本
            text = moments_text
        elif wechat_name:
            # 通过 UI 自动化读取
            texts = self.reader.read_moments(wechat_name)
            text = "\n".join(texts)
        else:
            logger.warning("未提供微信昵称或朋友圈文本，跳过分析")
            return []

        if not text.strip():
            logger.info(f"用户 {user_id} 朋友圈内容为空，跳过分析")
            return []

        # 提取标签
        tags = await self.extract_tags(text)

        # 存入用户画像
        if tags and self.profile_service:
            try:
                self.profile_service.update_moments_tags(user_id, tags)
                logger.info(f"用户 {user_id} 朋友圈标签已存储: {tags}")
            except Exception as e:
                logger.warning(f"存储朋友圈标签失败: {e}")

        return tags

    def needs_reanalysis(self, user_id: str, interval_hours: int = 168) -> bool:
        """判断是否需要重新分析朋友圈

        默认 7 天（168 小时）重新分析一次。

        Args:
            user_id: 用户 ID
            interval_hours: 重新分析间隔（小时）

        Returns:
            True 表示需要重新分析
        """
        if not self.profile_service:
            return False

        try:
            profile = self.profile_service.get_profile(user_id)
            analyzed_at = profile.get("moments_analyzed_at", 0)
            if not analyzed_at:
                return True
            elapsed = time.time() - analyzed_at
            return elapsed > interval_hours * 3600
        except Exception:
            return False
