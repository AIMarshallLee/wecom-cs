"""
企业微信客服系统 - FastAPI 主应用

核心流程：
1. 启动时加载配置、初始化各模块
2. GET /wecom/callback  - WeCom URL 验证
3. POST /wecom/callback - 接收消息，异步处理
4. 异步处理流程：
   a. 解析消息
   b. 会话管理（获取上下文）
   c. FAQ 引擎匹配
   d. 未命中则调用 LLM 引擎
   e. 回复决策器决策
   f. 发送回复（通过 WeComClient）
   g. 记录会话上下文

运行方式：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
    或
    python3 app/main.py
"""
import os
import sys
import asyncio
import hashlib
import base64
import struct
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, date
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

# 确保项目根目录在 sys.path 中（兼容 python3 app/main.py 直接运行）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse

from app.utils import load_config, setup_logging, SQLiteDB, format_message

# ============================================================
# 配置路径与日志
# ============================================================

CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.yaml")
logger = setup_logging(os.environ.get("LOG_LEVEL", "INFO"))

# ============================================================
# 可选依赖导入
# ============================================================

# pycryptodome（用于 AES 加解密）
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad as _pkcs7_pad, unpad as _pkcs7_unpad
    HAS_CRYPTO_LIB = True
except ImportError:
    HAS_CRYPTO_LIB = False
    logger.warning("pycryptodome 未安装，AES 加解密功能受限")

# 业务模块（可能尚未创建，用 try/except 包裹避免导入失败）
_module_status: Dict[str, bool] = {}

try:
    from app.crypto import WXBizMsgCrypt  # type: ignore
    _module_status["crypto"] = True
except ImportError:
    _module_status["crypto"] = False

try:
    from app.wecom_client import WeComClient  # type: ignore
    _module_status["wecom_client"] = True
except ImportError:
    _module_status["wecom_client"] = False

try:
    from app.callback import CallbackHandler  # type: ignore
    _module_status["callback_handler"] = True
except ImportError:
    _module_status["callback_handler"] = False

try:
    from app.faq_engine import FAQEngine  # type: ignore
    _module_status["faq_engine"] = True
except ImportError:
    _module_status["faq_engine"] = False

try:
    from app.llm_engine import LLMEngine  # type: ignore
    _module_status["llm_engine"] = True
except ImportError:
    _module_status["llm_engine"] = False

try:
    from app.knowledge_base import KnowledgeBase  # type: ignore
    _module_status["knowledge_base"] = True
except ImportError:
    _module_status["knowledge_base"] = False

try:
    from app.session_manager import SessionManager  # type: ignore
    _module_status["session_manager"] = True
except ImportError:
    _module_status["session_manager"] = False

try:
    from app.reply_controller import ReplyController  # type: ignore
    _module_status["reply_controller"] = True
except ImportError:
    _module_status["reply_controller"] = False

try:
    from app.user_profile_service import UserProfileService  # type: ignore
    _module_status["user_profile_service"] = True
except ImportError:
    _module_status["user_profile_service"] = False

try:
    from app.adaptive_engine import AdaptiveEngine  # type: ignore
    _module_status["adaptive_engine"] = True
except ImportError:
    _module_status["adaptive_engine"] = False

try:
    from app.moments_analyzer import MomentsAnalyzer  # type: ignore
    _module_status["moments_analyzer"] = True
except ImportError:
    _module_status["moments_analyzer"] = False

try:
    from app.effect_tracker import EffectTracker  # type: ignore
    _module_status["effect_tracker"] = True
except ImportError:
    _module_status["effect_tracker"] = False

try:
    from app.safety import SafetyGuard
    _module_status["safety_guard"] = True
except ImportError:
    _module_status["safety_guard"] = False


# ============================================================
# 内置加解密工具（crypto 模块不可用时的降级实现）
# ============================================================

def verify_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """计算企业微信消息签名

    签名算法：sha1(sort([token, timestamp, nonce, encrypt]))

    Args:
        token:      回调 Token
        timestamp:  时间戳
        nonce:      随机数
        encrypt:    加密消息内容（echostr 或 Encrypt 字段）

    Returns:
        40 位十六进制签名字符串
    """
    items = sorted([token, timestamp, nonce, encrypt])
    raw = "".join(items)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def decrypt_message(encrypt: str, encoding_aes_key: str, corp_id: str) -> Optional[str]:
    """解密企业微信消息（AES-256-CBC, PKCS#7）

    Args:
        encrypt:           Base64 编码的加密消息
        encoding_aes_key:  EncodingAESKey（43 字符）
        corp_id:           企业 ID（用于校验 AppID）

    Returns:
        解密后的消息明文，失败返回 None
    """
    if not HAS_CRYPTO_LIB:
        logger.warning("pycryptodome 不可用，无法解密消息")
        return None
    try:
        # EncodingAESKey 补 "=" 后 Base64 解码得到 32 字节 AES 密钥
        key = base64.b64decode(encoding_aes_key + "=")
        iv = key[:16]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(base64.b64decode(encrypt))
        decrypted = _pkcs7_unpad(decrypted, 32)  # WeCom 使用 32 字节块大小
        # 解密后格式：random(16) + msg_len(4, 大端) + msg + appid
        msg_len = struct.unpack("!I", decrypted[16:20])[0]
        msg = decrypted[20:20 + msg_len].decode("utf-8")
        appid = decrypted[20 + msg_len:].decode("utf-8")
        if corp_id and appid != corp_id:
            logger.warning(f"AppID 校验失败: {appid} != {corp_id}")
        return msg
    except Exception as e:
        logger.error(f"解密失败: {e}")
        return None


def encrypt_message(
    reply_msg: str,
    encoding_aes_key: str,
    corp_id: str,
    token: str,
    timestamp: str,
    nonce: str,
) -> str:
    """加密回复消息并构造 XML

    Args:
        reply_msg:         回复消息明文
        encoding_aes_key:  EncodingAESKey
        corp_id:           企业 ID
        token:             回调 Token
        timestamp:         时间戳
        nonce:             随机数

    Returns:
        符合企业微信要求的 XML 格式字符串
    """
    if not HAS_CRYPTO_LIB:
        logger.warning("pycryptodome 不可用，无法加密消息")
        return ""
    try:
        key = base64.b64decode(encoding_aes_key + "=")
        iv = key[:16]
        # 随机 16 字节 + 消息长度(4字节大端) + 消息 + AppID
        random_bytes = os.urandom(16)
        msg_bytes = reply_msg.encode("utf-8")
        msg_len = struct.pack("!I", len(msg_bytes))
        appid_bytes = corp_id.encode("utf-8")
        content = random_bytes + msg_len + msg_bytes + appid_bytes
        # PKCS#7 填充至 AES 块大小
        padded = _pkcs7_pad(content, AES.block_size)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(padded)
        encrypt_b64 = base64.b64encode(encrypted).decode("utf-8")
        # 计算签名
        signature = verify_signature(token, timestamp, nonce, encrypt_b64)
        # 构造 XML
        xml = (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypt_b64}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )
        return xml
    except Exception as e:
        logger.error(f"加密失败: {e}")
        return ""


# ============================================================
# 敏感词检测
# ============================================================

# 敏感词列表（触发后转人工）
SENSITIVE_WORDS: List[str] = [
    "投诉", "举报", "法律", "律师", "法院", "赔偿",
    "人工", "转人工", "售后", "纠纷", "维权", "媒体",
]


def contains_sensitive(content: str) -> bool:
    """检测消息内容是否包含敏感词

    Args:
        content: 消息内容

    Returns:
        True 表示包含敏感词
    """
    if not content:
        return False
    return any(word in content for word in SENSITIVE_WORDS)


# ============================================================
# XML 解析辅助函数
# ============================================================

def _extract_encrypt(xml_text: str) -> Optional[str]:
    """从 XML 中提取 Encrypt 字段"""
    try:
        root = ET.fromstring(xml_text)
        encrypt_elem = root.find("Encrypt")
        if encrypt_elem is not None and encrypt_elem.text:
            return encrypt_elem.text
    except Exception:
        pass
    return None


def _parse_xml(xml_text: str) -> Dict[str, str]:
    """解析 XML 消息为字典"""
    result: Dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
        for child in root:
            result[child.tag] = child.text or ""
    except Exception as e:
        logger.warning(f"XML 解析失败: {e}")
    return result


def _build_message(parsed: Dict[str, str]) -> Dict[str, Any]:
    """将解析后的 XML 字典构建为标准消息字典"""
    return {
        "msg_id": parsed.get("MsgId", ""),
        "msg_type": parsed.get("MsgType", "text"),
        "user_id": parsed.get("FromUserName", ""),
        "to_user": parsed.get("ToUserName", ""),
        "content": parsed.get("Content", ""),
        "event": parsed.get("Event", ""),
        "timestamp": parsed.get("CreateTime", ""),
    }


# ============================================================
# 应用核心类
# ============================================================

class Application:
    """主应用类，整合所有业务模块

    采用依赖注入设计：各业务模块通过 try/except 尝试导入，
    不可用时使用内置降级逻辑，保证系统始终可用。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        # 业务模块实例
        self.crypto = None
        self.wecom_client = None
        self.callback_handler = None
        self.faq_engine = None
        self.llm_engine = None
        self.knowledge_base = None
        self.session_manager = None
        self.reply_controller = None
        self.profile_service = None
        self.adaptive_engine = None
        self.moments_analyzer = None
        self.effect_tracker = None
        self.safety_guard = None
        # 数据库
        self.db: Optional[SQLiteDB] = None
        # 会话上下文缓存（降级时使用内存字典）
        self._sessions: Dict[str, List[Dict]] = {}
        # FAQ 数据缓存（降级匹配使用）
        self._faq_data: List[Dict] = []
        # 消息去重缓存（防止企微 5 秒重试导致重复回复）
        self._processed_msg_ids: Dict[str, float] = {}
        # 去重缓存保留时间（秒）
        self._dedup_ttl: int = 300

        self._init_db()
        self._init_modules()

    # ----------------------------------------------------------
    # 初始化
    # ----------------------------------------------------------

    def _init_db(self) -> None:
        """初始化 SQLite 数据库"""
        db_path = self.config.get("db", {}).get(
            "path", os.path.join(BASE_DIR, "data", "wecom_cs.db")
        )
        # 确保目录存在（内存数据库除外）
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.db = SQLiteDB(db_path)
        self.db.init_db()
        logger.info(f"数据库初始化完成: {db_path}")

    def _init_modules(self) -> None:
        """初始化各业务模块（try/except 保证降级运行）"""
        wecom_cfg = self.config.get("wecom", {})
        llm_cfg = self.config.get("llm", {})
        reply_cfg = self.config.get("reply", {})
        paths_cfg = self.config.get("paths", {})
        redis_cfg = self.config.get("redis", {})

        # 加密模块
        if _module_status.get("crypto"):
            try:
                self.crypto = WXBizMsgCrypt(
                    wecom_cfg.get("token", ""),
                    wecom_cfg.get("encoding_aes_key", ""),
                    wecom_cfg.get("corpid", ""),
                )
                logger.info("crypto 模块初始化成功")
            except Exception as e:
                logger.warning(f"crypto 模块初始化失败: {e}")

        # 企业微信客户端
        if _module_status.get("wecom_client"):
            try:
                self.wecom_client = WeComClient(
                    corpid=wecom_cfg.get("corpid", ""),
                    corpsecret=wecom_cfg.get("corpsecret", ""),
                    agentid=wecom_cfg.get("agentid", 0),
                )
                logger.info("wecom_client 模块初始化成功")
            except Exception as e:
                logger.warning(f"wecom_client 模块初始化失败: {e}")

        # 知识库
        if _module_status.get("knowledge_base"):
            try:
                kb_path = os.path.join(
                    BASE_DIR, paths_cfg.get("knowledge_base", "knowledge")
                )
                self.knowledge_base = KnowledgeBase(kb_path)
                logger.info("knowledge_base 模块初始化成功")
            except Exception as e:
                logger.warning(f"knowledge_base 模块初始化失败: {e}")

        # FAQ 引擎
        if _module_status.get("faq_engine"):
            try:
                faq_file = os.path.join(
                    BASE_DIR, paths_cfg.get("faq_file", "knowledge/faq.json")
                )
                self.faq_engine = FAQEngine(faq_file)
                logger.info("faq_engine 模块初始化成功")
            except Exception as e:
                logger.warning(f"faq_engine 模块初始化失败: {e}")

        # LLM 引擎
        if _module_status.get("llm_engine"):
            try:
                self.llm_engine = LLMEngine(
                    provider=llm_cfg.get("provider", "openai"),
                    api_url=llm_cfg.get("api_url", ""),
                    api_key=llm_cfg.get("api_key", ""),
                    model=llm_cfg.get("model", ""),
                )
                logger.info("llm_engine 模块初始化成功")
            except Exception as e:
                logger.warning(f"llm_engine 模块初始化失败: {e}")

        # 会话管理
        if _module_status.get("session_manager"):
            try:
                self.session_manager = SessionManager(
                    redis_url=redis_cfg.get("url", ""),
                )
                logger.info("session_manager 模块初始化成功")
            except Exception as e:
                logger.warning(f"session_manager 模块初始化失败: {e}")

        # 回复决策器
        if _module_status.get("reply_controller"):
            try:
                self.reply_controller = ReplyController(
                    auto_reply_threshold=reply_cfg.get("auto_reply_threshold", 80),
                )
                logger.info("reply_controller 模块初始化成功")
            except Exception as e:
                logger.warning(f"reply_controller 模块初始化失败: {e}")

        # 用户画像服务（SQLite 持久化）
        if _module_status.get("user_profile_service") and self.db:
            try:
                self.profile_service = UserProfileService(self.db)
                logger.info("user_profile_service 模块初始化成功")
            except Exception as e:
                logger.warning(f"user_profile_service 模块初始化失败: {e}")

        # 话术自适应引擎（依赖 llm_engine 和 profile_service）
        if _module_status.get("adaptive_engine") and self.llm_engine and self.profile_service:
            try:
                self.adaptive_engine = AdaptiveEngine(
                    llm_engine=self.llm_engine,
                    profile_service=self.profile_service,
                )
                logger.info("adaptive_engine 模块初始化成功")
            except Exception as e:
                logger.warning(f"adaptive_engine 模块初始化失败: {e}")

        # 朋友圈画像分析器（依赖 llm_engine 和 profile_service）
        if _module_status.get("moments_analyzer") and self.llm_engine and self.profile_service:
            try:
                self.moments_analyzer = MomentsAnalyzer(
                    llm_engine=self.llm_engine,
                    profile_service=self.profile_service,
                )
                logger.info("moments_analyzer 模块初始化成功")
            except Exception as e:
                logger.warning(f"moments_analyzer 模块初始化失败: {e}")

        # 效果学习引擎（依赖 SQLite）
        if _module_status.get("effect_tracker") and self.db:
            try:
                self.effect_tracker = EffectTracker(self.db)
                logger.info("effect_tracker 模块初始化成功")
            except Exception as e:
                logger.warning(f"effect_tracker 模块初始化失败: {e}")

        # 回调处理器
        if _module_status.get("callback_handler"):
            try:
                self.callback_handler = CallbackHandler(
                    token=wecom_cfg.get("token", ""),
                    encoding_aes_key=wecom_cfg.get("encoding_aes_key", ""),
                    corpid=wecom_cfg.get("corpid", ""),
                )
                logger.info("callback_handler 模块初始化成功")
            except Exception as e:
                logger.warning(f"callback_handler 模块初始化失败: {e}")

        # 安全合规守卫（速率限制/工作时间/人工模拟/内容过滤/紧急停止）
        if _module_status.get("safety_guard"):
            try:
                safety_cfg = self.config.get("safety", {})
                self.safety_guard = SafetyGuard(safety_cfg)
                logger.info("safety_guard 模块初始化成功")
            except Exception as e:
                logger.warning(f"safety_guard 模块初始化失败: {e}")

        # 加载 FAQ 数据（降级匹配使用）
        self._load_faq_data()

        # 模块状态报告
        available = [k for k, v in _module_status.items() if v]
        unavailable = [k for k, v in _module_status.items() if not v]
        logger.info(f"已加载模块: {available}")
        if unavailable:
            logger.warning(f"未加载模块（降级运行）: {unavailable}")

    def _load_faq_data(self) -> None:
        """加载 FAQ 数据文件

        支持两种 JSON 格式：
        - 列表格式：[{...}, {...}]
        - 字典格式：{"faqs": [{...}, {...}]}
        """
        paths_cfg = self.config.get("paths", {})
        faq_file = os.path.join(
            BASE_DIR, paths_cfg.get("faq_file", "knowledge/faq.json")
        )
        try:
            with open(faq_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # 兼容 {"faqs": [...]} 和 [...] 两种格式
            if isinstance(raw, dict) and "faqs" in raw:
                self._faq_data = raw["faqs"]
            elif isinstance(raw, list):
                self._faq_data = raw
            else:
                logger.warning(f"FAQ 文件格式无法识别: {type(raw)}")
                self._faq_data = []
            # 确保 ID 为整数类型
            for item in self._faq_data:
                if isinstance(item.get("id"), str):
                    try:
                        item["id"] = int(item["id"])
                    except (ValueError, TypeError):
                        pass
            logger.info(f"加载 FAQ 数据: {len(self._faq_data)} 条")
        except FileNotFoundError:
            logger.warning(f"FAQ 文件不存在: {faq_file}")
            self._faq_data = []
        except Exception as e:
            logger.warning(f"加载 FAQ 数据失败: {e}")
            self._faq_data = []

    def _save_faqs(self) -> bool:
        """保存 FAQ 数据到文件"""
        paths_cfg = self.config.get("paths", {})
        faq_file = os.path.join(
            BASE_DIR, paths_cfg.get("faq_file", "knowledge/faq.json")
        )
        try:
            with open(faq_file, "w", encoding="utf-8") as f:
                json.dump(self._faq_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存 FAQ 失败: {e}")
            return False

    # ----------------------------------------------------------
    # 消息处理核心流程
    # ----------------------------------------------------------

    async def process_message(self, message: Dict[str, Any]) -> None:
        """异步处理消息（核心流程）

        流程：解析 → 用户画像 → 会话上下文 → FAQ匹配 → LLM(可选) → 人性化 → 决策 → 发送 → 记录

        Args:
            message: 标准消息字典（含 user_id, content, msg_type 等）
        """
        try:
            user_id = message.get("user_id", "")
            content = message.get("content", "")
            msg_type = message.get("msg_type", "text")
            msg_id = message.get("msg_id", "")

            # 消息去重：企微会在 5 秒内重试同一消息最多 3 次
            if msg_id:
                import time as _time
                now = _time.time()
                # 清理过期记录
                expired = [k for k, t in self._processed_msg_ids.items() if now - t > self._dedup_ttl]
                for k in expired:
                    del self._processed_msg_ids[k]
                # 检查是否已处理过
                if msg_id in self._processed_msg_ids:
                    logger.info(f"消息已处理过，跳过: msg_id={msg_id}")
                    return
                self._processed_msg_ids[msg_id] = now

            logger.info(
                f"开始处理消息: user={user_id}, type={msg_type}, "
                f"content={content[:50]}"
            )

            # 非文本消息处理：图片/语音/链接等
            if msg_type != "text" or not content.strip():
                logger.info(f"非文本消息，跳过自动处理: type={msg_type}")
                # 非文本消息直接走人工
                if self.wecom_client:
                    try:
                        await self.wecom_client.send_text(
                            user_id,
                            "收到你的消息了，不过我目前只能处理文字，"
                            "用文字描述一下你的问题呗~"
                        )
                    except Exception:
                        pass
                self._record_message(user_id, content or f"[{msg_type}]", "", "human")
                return

            # a. 解析消息（格式化内容）
            content = format_message(content)

            # a2. 效果跟踪：用当前消息评估上一条回复的效果
            if self.effect_tracker and self.profile_service:
                try:
                    history = self.profile_service.get_interaction_history(user_id, limit=1)
                    if history:
                        last = history[0]
                        effect_type = self.effect_tracker.record_effect(
                            user_id=user_id,
                            followup_text=content,
                            faq_id=last.get("faq_id", "") or "",
                            reply_mode=last.get("reply_mode", "") or "",
                            previous_question=last.get("user_message", "") or "",
                        )
                        # 效果反馈循环：每10条消息自动更新一次偏好风格
                        current_count = self.profile_service.get_profile(user_id).get("message_count", 0)
                        if current_count > 0 and current_count % 10 == 0:
                            suggested_style = self.effect_tracker.suggest_style(user_id)
                            if suggested_style:
                                self.profile_service.update_preferred_style(user_id, suggested_style)
                                logger.info(f"偏好风格自动更新: user={user_id}, style={suggested_style}")
                except Exception as e:
                    logger.warning(f"效果跟踪记录失败: {e}")

            # b. 获取用户画像（用于个性化回复）
            user_profile = self._get_user_profile(user_id)

            # c. 会话管理（获取上下文）
            context = await self._get_session_context(user_id)

            # d. FAQ 引擎匹配
            faq_result = await self._match_faq(content)

            reply_text = ""
            mode = "auto"
            confidence = 0

            if faq_result:
                # FAQ 命中
                raw_answer = faq_result.get("answer", "")
                # FAQ confidence 是 0-1 小数，转成 0-100
                confidence = int(faq_result.get("confidence", 0.95) * 100)
                # 自适应改写：老用户用 LLM 个性化改写，新用户走规则化人性化
                if self.adaptive_engine and self.adaptive_engine.should_adapt(user_id):
                    adapted = await self.adaptive_engine.adapt_reply(
                        user_id, content, raw_answer,
                        faq_result.get("question", ""),
                        context=context,
                    )
                    reply_text = adapted if adapted else self._humanize_faq_reply(
                        raw_answer, user_profile, faq_result
                    )
                else:
                    # 新用户或无画像数据，走规则化人性化
                    reply_text = self._humanize_faq_reply(
                        raw_answer, user_profile, faq_result
                    )
                mode = "faq"
                logger.info(f"FAQ 匹配成功: confidence={confidence}")
            else:
                # e. 未命中则调用 LLM 引擎（传入用户画像）
                llm_result = await self._call_llm(content, context, user_profile)
                reply_text = llm_result.get("reply", "")
                confidence = llm_result.get("confidence", 60)
                mode = "llm"
                logger.info(f"LLM 生成回复: confidence={confidence}")

            # f. 回复决策器决策
            decision = await self._decide_reply(
                content, reply_text, confidence, faq_result
            )

            if decision.get("transfer_to_human"):
                reply_text = decision.get("reply", reply_text)
                mode = "human"
                logger.info("决策结果: 转人工客服")
            elif decision.get("mode") == "suggest":
                # suggest 模式：置信度中等，不自动发送原文，发一句缓冲话
                suggested = decision.get("reply", reply_text)
                reply_text = "我想想怎么回答你哈，稍等一下~"
                mode = "suggest"
                logger.info(
                    f"决策结果: 建议回复(不自动发出), 建议内容: {suggested[:80]}"
                )
            else:
                reply_text = decision.get("reply", reply_text)
                logger.info("决策结果: 自动回复")

            # g. 安全合规检查 + 发送回复
            if self.safety_guard and mode != "human":
                # 安全检查（非转人工的回复都需要过安全守卫）
                is_faq_match = mode == "faq"
                safety_result = self.safety_guard.pre_send_check(
                    user_id=user_id,
                    reply_text=reply_text,
                    confidence=float(confidence) / 100.0,
                    is_faq=is_faq_match,
                )

                if not safety_result["allowed"]:
                    # 被安全守卫拦截（速率限制/非工作时间/紧急停止）
                    logger.info(
                        f"回复被安全守卫拦截: user={user_id}, "
                        f"reason={safety_result['reason']}"
                    )
                    if safety_result["mode"] == "block":
                        # 完全拦截，不发送（非工作时间或紧急停止）
                        mode = "blocked"
                        reply_text = ""
                    else:
                        # suggest 模式：发送缓冲话
                        reply_text = safety_result["filtered_text"]
                        mode = "suggest"
                else:
                    # 通过安全检查，使用过滤后的文本，保留原始 mode
                    reply_text = safety_result["filtered_text"]
                    if safety_result["warnings"]:
                        logger.info(
                            f"内容过滤警告: {safety_result['warnings']}"
                        )

            # 发送回复（通过 WeComClient）
            if reply_text:
                await self._send_reply(user_id, reply_text)

            # h. 记录会话上下文
            await self._record_session(user_id, content, reply_text, mode)

            # i. 更新用户画像
            self._record_user_interaction(
                user_id, content,
                faq_result.get("question", "") if faq_result else ""
            )

            # 记录到数据库
            self._record_message(user_id, content, reply_text, mode)

            # 记录交互明细到 profile_service（用于千人千面分析）
            if self.profile_service:
                try:
                    faq_id = faq_result.get("faq_id", "") if faq_result else ""
                    self.profile_service.record_interaction_detail(
                        user_id, content, reply_text, mode,
                        str(faq_id) if faq_id else "",
                        float(confidence) / 100.0,
                    )
                except Exception as e:
                    logger.warning(f"记录交互明细失败: {e}")

            # 每5条消息异步生成一次对话摘要（不阻塞回复）
            if self.profile_service and user_profile:
                msg_count = user_profile.get("message_count", 0)
                if msg_count > 0 and msg_count % 5 == 0:
                    asyncio.create_task(
                        self._generate_conversation_summary(user_id)
                    )

            logger.info(f"消息处理完成: user={user_id}, mode={mode}")

        except Exception as e:
            logger.error(f"消息处理异常: {e}", exc_info=True)
            # 异常时给用户一个反馈，避免被无视
            try:
                if self.wecom_client and user_id:
                    await self.wecom_client.send_text(
                        user_id,
                        "不好意思刚刚出了点小状况，你再发一次试试？"
                    )
            except Exception:
                pass

    # ----------------------------------------------------------
    # 流程子步骤（含降级逻辑）
    # ----------------------------------------------------------

    async def _get_session_context(self, user_id: str) -> List[Dict]:
        """获取会话上下文（步骤 b）"""
        if self.session_manager:
            try:
                return self.session_manager.get_context(user_id)
            except Exception:
                pass
        # 降级：从内存缓存获取
        max_ctx = self.config.get("reply", {}).get("max_context_messages", 10)
        return self._sessions.get(user_id, [])[-max_ctx:]

    async def _match_faq(self, content: str) -> Optional[Dict]:
        """FAQ 引擎匹配（步骤 c）"""
        if self.faq_engine:
            try:
                return self.faq_engine.search(content)
            except Exception:
                pass
        # 降级：简单关键词匹配
        return self._fallback_faq_match(content)

    def _fallback_faq_match(self, content: str) -> Optional[Dict]:
        """降级 FAQ 匹配：关键词包含匹配"""
        if not content:
            return None
        for item in self._faq_data:
            keywords = item.get("keywords", [])
            if any(kw in content for kw in keywords):
                # 统一使用 0-1 小数表示置信度（与 FAQEngine 一致）
                raw_conf = item.get("confidence", 95)
                conf = raw_conf / 100 if raw_conf > 1 else raw_conf
                return {
                    "answer": item.get("answer", ""),
                    "question": item.get("question", ""),
                    "confidence": conf,
                    "faq_id": item.get("id"),
                }
        return None

    async def _call_llm(
        self, content: str, context: List[Dict], user_profile: dict = None
    ) -> Dict:
        """调用 LLM 引擎（步骤 d）

        使用 UserProfileService.get_profile_context() 构建富用户画像上下文，
        让 LLM 能够基于用户历史对话摘要、转化阶段、朋友圈标签等个性化回复。

        Args:
            content: 用户消息
            context: 对话历史
            user_profile: 用户画像（可选，用于个性化回复）
        """
        if self.llm_engine:
            try:
                # 优先使用 profile_service 的富画像上下文
                knowledge_context = ""
                user_id = user_profile.get("user_id", "") if user_profile else ""

                if self.profile_service and user_id:
                    try:
                        rich_context = self.profile_service.get_profile_context(user_id)
                        if rich_context:
                            knowledge_context = rich_context + "\n"
                    except Exception:
                        pass

                # 降级：手动构建简单上下文
                if not knowledge_context and user_profile:
                    msg_count = user_profile.get("message_count", 0)
                    stage = user_profile.get("conversion_stage", "new")
                    topics = user_profile.get("asked_topics", [])
                    if topics:
                        knowledge_context += f"用户之前问过：{', '.join(topics[-3:])}\n"
                    knowledge_context += f"这是用户第{msg_count}次互动，转化阶段：{stage}\n"
                    if stage == "new":
                        knowledge_context += "用户是新人，热情一点但别推销\n"
                    elif stage == "engaged":
                        knowledge_context += "用户已经聊过几次了，可以适当提一嘴我们的产品\n"
                    elif stage in ("interested", "considering"):
                        knowledge_context += "用户对我们的产品感兴趣，可以自然地引导一下转化\n"

                reply, conf = await self.llm_engine.generate_reply(
                    content, knowledge_context, context
                )
                return {"reply": reply, "confidence": int(conf * 100)}
            except Exception as e:
                logger.warning(f"LLM 调用异常: {e}")
        # 降级：返回默认回复
        return {
            "reply": "这个问题我一下子答不上来，我帮你问问负责人哈，稍等~",
            "confidence": 50,
        }

    async def _decide_reply(
        self,
        content: str,
        reply: str,
        confidence: int,
        faq_result: Optional[Dict] = None,
    ) -> Dict:
        """回复决策器（步骤 e）

        Args:
            content: 用户原始消息
            reply: FAQ或LLM生成的回复（已人性化处理）
            confidence: 置信度（0-100）
            faq_result: FAQ匹配结果（可选）
        """
        if self.reply_controller:
            try:
                result = self.reply_controller.decide(
                    content, faq_result, reply, confidence
                )
                mode = result.get("mode", "auto")
                # 获取决策器返回的内容
                decided_content = result.get("content", reply)
                # FAQ 命中时，决策器返回的是原始 answer，我们要用已人性化的 reply
                if mode == "auto" and faq_result is not None:
                    decided_content = reply
                return {
                    "transfer_to_human": mode == "human",
                    "reply": decided_content,
                    "reason": result.get("reason", ""),
                    "mode": mode,
                }
            except Exception as e:
                logger.warning(f"ReplyController 异常: {e}")
        # 降级：基于阈值和敏感词的决策
        threshold = self.config.get("reply", {}).get("auto_reply_threshold", 80)
        # 敏感词检测优先
        if contains_sensitive(content):
            return {
                "transfer_to_human": True,
                "reply": "这个问题比较重要，我直接帮你找负责人来处理，稍等一下哈~",
                "reason": "sensitive_content",
            }
        # 置信度不足
        if confidence < threshold:
            return {
                "transfer_to_human": True,
                "reply": reply,
                "reason": "low_confidence",
            }
        # 自动回复
        return {
            "transfer_to_human": False,
            "reply": reply,
            "reason": "auto",
        }

    async def _send_reply(self, user_id: str, reply_text: str) -> None:
        """发送回复（步骤 f）

        集成安全合规：
        - 人工模拟延迟（3-15s 随机等待，模拟人工打字）
        - 发送后记录速率限制计数
        """
        # 人工模拟延迟（让回复不那么"机器人"）
        if self.safety_guard:
            delay = self.safety_guard.get_send_delay()
            logger.info(f"人工模拟延迟: {delay:.1f}s")
            await asyncio.sleep(delay)

        if self.wecom_client:
            try:
                await self.wecom_client.send_text(user_id, reply_text)
                logger.info(f"已通过 WeComClient 发送回复: user={user_id}")
                # 记录发送（用于速率限制计数）
                if self.safety_guard:
                    self.safety_guard.post_send(user_id)
                return
            except Exception as e:
                logger.warning(f"WeComClient 发送失败: {e}")
        # 降级：仅记录日志
        logger.info(f"[降级] 回复: user={user_id}, reply={reply_text[:80]}")
        # 降级模式下也记录发送
        if self.safety_guard:
            self.safety_guard.post_send(user_id)

    async def _record_session(
        self, user_id: str, content: str, reply: str, mode: str
    ) -> None:
        """记录会话上下文（步骤 g）"""
        if self.session_manager:
            try:
                self.session_manager.add_message(user_id, "user", content)
                self.session_manager.add_message(user_id, "assistant", reply)
                return
            except Exception:
                pass
        # 降级：内存缓存
        if user_id not in self._sessions:
            self._sessions[user_id] = []
        now = datetime.now().isoformat()
        self._sessions[user_id].append(
            {"role": "user", "content": content, "mode": mode, "timestamp": now}
        )
        self._sessions[user_id].append(
            {"role": "assistant", "content": reply, "timestamp": now}
        )
        # 限制上下文长度
        max_ctx = self.config.get("reply", {}).get("max_context_messages", 10)
        if len(self._sessions[user_id]) > max_ctx * 2:
            self._sessions[user_id] = self._sessions[user_id][-max_ctx * 2:]

    # ----------------------------------------------------------
    # 用户画像与回复人性化
    # ----------------------------------------------------------

    def _get_user_profile(self, user_id: str) -> dict:
        """获取用户画像

        优先使用 SQLite 持久化的 UserProfileService，
        降级到 SessionManager 的 Redis/内存画像。

        Args:
            user_id: 用户 ID

        Returns:
            用户画像字典，包含消息计数、转化阶段、问过的话题等
        """
        if self.profile_service:
            try:
                return self.profile_service.get_profile(user_id)
            except Exception:
                pass
        if self.session_manager:
            try:
                return self.session_manager.get_user_profile(user_id)
            except Exception:
                pass
        return {
            "user_id": user_id,
            "message_count": 0,
            "interests": [],
            "conversion_stage": "new",
            "asked_topics": [],
        }

    def _record_user_interaction(
        self, user_id: str, content: str, faq_question: str = ""
    ) -> None:
        """记录用户交互，更新画像

        优先写入 SQLite 持久化画像，同时写入 SessionManager（保持会话上下文兼容）。

        Args:
            user_id: 用户 ID
            content: 用户消息内容
            faq_question: FAQ 匹配到的问题（可选）
        """
        if self.profile_service:
            try:
                self.profile_service.record_interaction(
                    user_id, content, faq_question
                )
            except Exception as e:
                logger.warning(f"profile_service 记录交互失败: {e}")
        # 同时写入 SessionManager（保持会话上下文兼容）
        if self.session_manager:
            try:
                self.session_manager.record_interaction(
                    user_id, content, faq_question
                )
            except Exception:
                pass

    async def _generate_conversation_summary(self, user_id: str) -> None:
        """异步生成对话摘要

        从 interaction_history 取最近10条交互记录，
        调用 LLM 生成一段不超过100字的摘要，存入用户画像。
        用于下次对话时 LLM 能快速了解这个用户的历史。

        Args:
            user_id: 用户 ID
        """
        if not self.profile_service or not self.llm_engine:
            return

        try:
            # 取最近10条交互记录
            history = self.profile_service.get_interaction_history(user_id, limit=10)
            if len(history) < 3:
                return  # 交互太少，不值得生成摘要

            # 拼接对话文本
            conversation_text = ""
            for record in reversed(history):  # 从旧到新
                user_msg = record.get("user_message", "")
                bot_reply = record.get("bot_reply", "")
                if user_msg:
                    conversation_text += f"用户：{user_msg}\n"
                if bot_reply:
                    conversation_text += f"客服：{bot_reply}\n"

            if not conversation_text.strip():
                return

            # 调用 LLM 生成摘要
            prompt = (
                "请用不超过100字总结以下对话的关键信息，"
                "包括用户关注点、态度、是否提到购买意向等。"
                "只输出摘要内容，不要加任何前缀：\n\n"
                f"{conversation_text[:2000]}"
            )

            reply, conf = await self.llm_engine.generate_reply(
                prompt, "", []
            )

            summary = reply.strip() if reply else ""
            # 防止 LLM 降级回复被误存为摘要
            is_fallback = (
                not summary
                or "答不上来" in summary
                or "稍等" in summary and len(summary) < 30
                or conf < 0.1
            )
            if not is_fallback and len(summary) <= 200:
                self.profile_service.update_conversation_summary(user_id, summary)
                logger.info(f"对话摘要已更新: user={user_id}, summary={summary[:50]}")

        except Exception as e:
            logger.warning(f"生成对话摘要失败: {e}")

    def _humanize_faq_reply(
        self,
        raw_answer: str,
        user_profile: dict,
        faq_result: dict,
    ) -> str:
        """将 FAQ 回复人性化处理

        根据 user_id 识别用户身份，决定如何打招呼和收尾。
        FAQ 原始答案比较正式，这里加上口语化的开头和结尾，
        让回复更像朋友聊天。

        Args:
            raw_answer: FAQ 原始答案
            user_profile: 用户画像
            faq_result: FAQ 匹配结果

        Returns:
            人性化处理后的回复文本
        """
        if not raw_answer:
            return raw_answer

        msg_count = user_profile.get("message_count", 0)
        stage = user_profile.get("conversion_stage", "new")
        topics = user_profile.get("asked_topics", [])
        current_question = faq_result.get("question", "")

        # --- 开头：根据是否首次互动决定招呼 ---
        greeting = ""
        if msg_count == 0:
            # 第一次来，热情招呼
            greeting = "嗨~ 我来帮你看看哈\n\n"
        elif msg_count >= 3:
            # 老用户，可以稍微个性化
            last_topic = topics[-1] if topics else ""
            if last_topic and last_topic != current_question:
                greeting = f"又来啦~ 上次你问的{last_topic}，"
            else:
                greeting = "嘿~ "
        else:
            # msg_count 1-2：简单招呼
            greeting = "嘿~ "

        # --- 结尾：根据转化阶段决定引导话术 ---
        ending = ""
        if stage in ("interested", "considering"):
            # 用户对产品有兴趣，自然引导
            if "加入" not in raw_answer and "价格" not in raw_answer:
                ending = "\n\n感兴趣的话随时跟我说，我帮你看看怎么加入~"
        elif stage == "engaged" and msg_count >= 5:
            # 老用户，提一嘴相关内容
            if len(raw_answer) < 100:
                ending = "\n\n还有啥想了解的随时问哈~"
        elif stage == "new" and msg_count == 0:
            # 新用户，友好收尾
            ending = "\n\n还有啥想了解的随时问我哈 😊"
        else:
            # 默认收尾
            ending = "\n\n还有啥想了解的随时问哈~"

        return f"{greeting}{raw_answer}{ending}"

    def _record_message(
        self, user_id: str, content: str, reply: str, mode: str
    ) -> None:
        """记录消息到数据库"""
        try:
            today = date.today().isoformat()
            # 插入消息记录
            self.db.execute(
                "INSERT INTO messages (user_id, content, reply, mode) "
                "VALUES (?, ?, ?, ?)",
                (user_id, content, reply, mode),
            )
            # 更新统计
            is_auto = 1 if mode in ("faq", "llm", "auto") else 0
            is_human = 1 if mode == "human" else 0
            existing = self.db.query(
                "SELECT date FROM stats WHERE date = ?", (today,)
            )
            if existing:
                self.db.execute(
                    "UPDATE stats SET total_messages = total_messages + 1, "
                    "auto_replies = auto_replies + ?, "
                    "human_replies = human_replies + ? WHERE date = ?",
                    (is_auto, is_human, today),
                )
            else:
                self.db.execute(
                    "INSERT INTO stats (date, total_messages, auto_replies, "
                    "human_replies) VALUES (?, ?, ?, ?)",
                    (today, 1, is_auto, is_human),
                )
        except Exception as e:
            logger.error(f"记录消息失败: {e}")

    # ----------------------------------------------------------
    # 管理后台辅助方法
    # ----------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        try:
            today = date.today().isoformat()
            stats = self.db.query(
                "SELECT * FROM stats WHERE date = ?", (today,)
            )
            today_count = self.db.query(
                "SELECT COUNT(*) as c FROM messages "
                "WHERE date(timestamp) = date('now', 'localtime')"
            )
            total_today = today_count[0]["c"] if today_count else 0
            auto_today = stats[0]["auto_replies"] if stats else 0
            human_today = stats[0]["human_replies"] if stats else 0
            auto_rate = (
                round(auto_today / total_today * 100, 1) if total_today > 0 else 0.0
            )
            return {
                "date": today,
                "total_messages": total_today,
                "auto_replies": auto_today,
                "human_replies": human_today,
                "auto_reply_rate": auto_rate,
            }
        except Exception as e:
            logger.error(f"获取统计失败: {e}")
            return {
                "date": date.today().isoformat(),
                "total_messages": 0,
                "auto_replies": 0,
                "human_replies": 0,
                "auto_reply_rate": 0.0,
            }

    def get_faqs(self) -> List[Dict]:
        """获取所有 FAQ"""
        return list(self._faq_data)

    def add_faq(self, faq: Dict) -> Dict:
        """添加 FAQ"""
        max_id = max((f.get("id", 0) for f in self._faq_data), default=0)
        faq["id"] = max_id + 1
        self._faq_data.append(faq)
        self._save_faqs()
        logger.info(f"添加 FAQ: id={faq['id']}")
        return faq

    def update_faq(self, faq_id: int, updates: Dict) -> Optional[Dict]:
        """更新 FAQ（合并更新字段，非整体替换）

        Args:
            faq_id:  FAQ ID
            updates: 需要更新的字段（仅更新传入的字段）
        """
        for i, f in enumerate(self._faq_data):
            if f.get("id") == faq_id:
                # 合并更新：保留原有字段，用 updates 覆盖
                merged = dict(f)
                merged.update(updates)
                merged["id"] = faq_id
                self._faq_data[i] = merged
                self._save_faqs()
                logger.info(f"更新 FAQ: id={faq_id}")
                return merged
        return None

    def delete_faq(self, faq_id: int) -> bool:
        """删除 FAQ"""
        before = len(self._faq_data)
        self._faq_data = [
            f for f in self._faq_data if f.get("id") != faq_id
        ]
        if len(self._faq_data) < before:
            self._save_faqs()
            logger.info(f"删除 FAQ: id={faq_id}")
            return True
        return False

    def get_session_history(self, user_id: str) -> List[Dict]:
        """获取用户会话历史（从数据库查询）"""
        try:
            return self.db.query(
                "SELECT content, reply, mode, timestamp FROM messages "
                "WHERE user_id = ? ORDER BY id ASC LIMIT 100",
                (user_id,),
            )
        except Exception as e:
            logger.error(f"获取会话历史失败: {e}")
            return []

    def clear_session(self, user_id: str) -> bool:
        """清空用户会话上下文"""
        if user_id in self._sessions:
            self._sessions.pop(user_id)
        if self.session_manager:
            try:
                self.session_manager.clear_context(user_id)
            except Exception:
                pass
        logger.info(f"清空用户会话: user={user_id}")
        return True

    def shutdown(self) -> None:
        """优雅关闭"""
        logger.info("正在关闭应用...")
        if self.db:
            self.db.close()
        logger.info("应用已关闭")


# ============================================================
# FastAPI 应用
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（启动与优雅关闭）"""
    # 如果 app.state.application 已设置（例如测试环境），跳过初始化
    if not hasattr(app.state, "application") or app.state.application is None:
        logger.info("=" * 50)
        logger.info("企业微信客服系统启动中...")
        config_path = os.environ.get("WECOM_CS_CONFIG", CONFIG_PATH)
        config = load_config(config_path)
        application = Application(config)
        app.state.application = application
        logger.info("企业微信客服系统启动完成")
        logger.info("=" * 50)
    yield
    # 优雅关闭
    application = getattr(app.state, "application", None)
    if application:
        application.shutdown()
        app.state.application = None
    logger.info("企业微信客服系统已停止")


app = FastAPI(
    title="企业微信客服系统",
    description="基于 FastAPI 的企业微信智能客服系统",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# API 认证中间件
# ============================================================

@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    """API 认证中间件

    对 /api/* 和 /admin/* 路径要求 X-API-Key 头认证。
    如果 config 中没有配置 api.key，则跳过认证（开发模式）。
    /health、/wecom/callback、/docs 等路径不受限制。
    """
    path = request.url.path

    # 不需要认证的路径
    public_prefixes = ("/health", "/wecom/callback", "/docs", "/openapi", "/redoc", "/favicon")
    if any(path.startswith(p) for p in public_prefixes):
        return await call_next(request)

    # 需要认证的路径
    protected_prefixes = ("/api", "/admin")
    if not any(path.startswith(p) for p in protected_prefixes):
        return await call_next(request)

    # 获取配置的 API Key
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application:
        api_key = application.config.get("api", {}).get("key", "")
        if api_key:
            # 已配置 API Key，必须验证
            provided_key = request.headers.get("X-API-Key", "")
            if provided_key != api_key:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"error": "Unauthorized", "message": "Missing or invalid API key"},
                )

    return await call_next(request)


# ============================================================
# 路由：健康检查
# ============================================================

@app.get("/health")
async def health_check():
    """健康检查"""
    application: Optional[Application] = getattr(
        app.state, "application", None
    )
    safety_info = {}
    if application and application.safety_guard:
        sg = application.safety_guard
        safety_info = {
            "emergency_stop": sg.is_emergency_stopped(),
            "phase": sg.get_phase(),
            "is_work_hours": sg.is_work_hours(),
        }
    return {
        "status": "ok",
        "service": "wecom-cs",
        "version": "1.0.0",
        "modules": _module_status,
        "crypto_lib": HAS_CRYPTO_LIB,
        "safety": safety_info,
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# 路由：企业微信回调
# ============================================================

@app.get("/wecom/callback")
async def wecom_verify(
    request: Request,
    msg_signature: str = Query(..., description="消息签名"),
    timestamp: str = Query(..., description="时间戳"),
    nonce: str = Query(..., description="随机数"),
    echostr: str = Query(..., description="加密的随机字符串"),
):
    """WeCom URL 验证（GET 回调）

    企业微信在配置回调 URL 时会发送 GET 请求进行验证。
    """
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return PlainTextResponse("Service not ready", status_code=503)

    token = application.config.get("wecom", {}).get("token", "")
    corpid = application.config.get("wecom", {}).get("corpid", "")
    aes_key = application.config.get("wecom", {}).get("encoding_aes_key", "")

    logger.info(f"URL验证请求: timestamp={timestamp}, nonce={nonce}")

    # 优先使用 crypto 模块
    if application.crypto:
        try:
            ret, reply = application.crypto.VerifyURL(  # type: ignore
                msg_signature, timestamp, nonce, echostr
            )
            if ret == 0:
                logger.info("URL验证成功（crypto模块）")
                return PlainTextResponse(reply)
            else:
                logger.warning(f"URL验证失败（crypto模块）: ret={ret}，降级到内置验证")
                # 不直接返回 403，降级到内置签名验证
        except Exception as e:
            logger.warning(f"crypto 模块 VerifyURL 异常: {e}，降级到内置验证")

    # 降级：内置签名验证
    expected_sig = verify_signature(token, timestamp, nonce, echostr)
    if expected_sig != msg_signature:
        logger.warning("URL验证失败：签名不匹配")
        return PlainTextResponse("Signature mismatch", status_code=403)

    # 尝试解密 echostr
    reply = decrypt_message(echostr, aes_key, corpid)
    if reply:
        logger.info("URL验证成功（内置解密）")
        return PlainTextResponse(reply)

    # 无法解密时直接返回 echostr（测试/降级场景）
    logger.warning("无法解密 echostr，直接返回原始值")
    return PlainTextResponse(echostr)


@app.post("/wecom/callback")
async def wecom_callback(
    request: Request,
    msg_signature: str = Query(..., description="消息签名"),
    timestamp: str = Query(..., description="时间戳"),
    nonce: str = Query(..., description="随机数"),
):
    """接收企业微信消息（POST 回调）

    企业微信将用户消息以 POST 请求发送到回调 URL，
    需在 5 秒内返回 "success"，消息处理异步执行。
    """
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return PlainTextResponse("Service not ready", status_code=503)

    body = await request.body()
    body_text = body.decode("utf-8")
    logger.info(f"收到回调消息: timestamp={timestamp}, nonce={nonce}")

    token = application.config.get("wecom", {}).get("token", "")
    corpid = application.config.get("wecom", {}).get("corpid", "")
    aes_key = application.config.get("wecom", {}).get("encoding_aes_key", "")

    # 优先使用 crypto 模块
    if application.crypto:
        try:
            ret, msg = application.crypto.DecryptMsg(  # type: ignore
                body_text, msg_signature, timestamp, nonce
            )
            if ret != 0:
                logger.warning(f"解密失败（crypto模块）: ret={ret}")
                return PlainTextResponse("success")
            parsed = _parse_xml(msg)
            message = _build_message(parsed)
            # 异步处理消息（不阻塞回调响应）
            asyncio.create_task(application.process_message(message))
            return PlainTextResponse("success")
        except Exception as e:
            logger.warning(f"crypto 模块 DecryptMsg 异常: {e}")

    # 降级：内置解析
    try:
        encrypt = _extract_encrypt(body_text)
        if encrypt:
            # 验证签名
            expected_sig = verify_signature(token, timestamp, nonce, encrypt)
            if expected_sig != msg_signature:
                logger.warning("消息签名验证失败")
                return PlainTextResponse("success")
            # 解密
            decrypted = decrypt_message(encrypt, aes_key, corpid)
            if decrypted:
                parsed = _parse_xml(decrypted)
            else:
                logger.warning("消息解密失败")
                parsed = {}
        else:
            # 未加密模式（用于测试或明文模式）
            parsed = _parse_xml(body_text)

        message = _build_message(parsed)
        # 异步处理消息（不阻塞回调响应）
        asyncio.create_task(application.process_message(message))
    except Exception as e:
        logger.error(f"回调处理异常: {e}", exc_info=True)

    # 企业微信要求 5 秒内返回 success
    return PlainTextResponse("success")


# ============================================================
# 路由：千人千面 - 用户画像与朋友圈分析
# ============================================================

@app.get("/api/profile/{user_id}")
async def get_user_profile_api(user_id: str, request: Request):
    """获取用户画像"""
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}, 503

    if application.profile_service:
        profile = application.profile_service.get_profile(user_id)
        history = application.profile_service.get_interaction_history(user_id, limit=10)
        return {"profile": profile, "recent_interactions": history}
    return {"error": "profile_service not available"}


@app.post("/api/profile/{user_id}/analyze_moments")
async def analyze_moments_api(user_id: str, request: Request):
    """分析用户朋友圈，提取画像标签

    请求体（JSON）：
        - moments_text: 朋友圈文本（直接提供时跳过 UI 读取）
        - wechat_name: 微信昵称（用于 UI 自动化读取朋友圈）
    """
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}, 503

    if not application.moments_analyzer:
        return {"error": "moments_analyzer not available"}

    body = await request.json()
    moments_text = body.get("moments_text", "")
    wechat_name = body.get("wechat_name", "")

    tags = await application.moments_analyzer.analyze_user(
        user_id, wechat_name=wechat_name, moments_text=moments_text
    )
    return {"user_id": user_id, "tags": tags}


@app.get("/api/profiles")
async def list_profiles_api(request: Request, limit: int = 50):
    """获取所有用户画像列表"""
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}, 503

    if application.profile_service:
        profiles = application.profile_service.get_all_profiles(limit=limit)
        return {"profiles": profiles, "count": len(profiles)}
    return {"profiles": [], "count": 0}


@app.get("/api/effects/faq/{faq_id}")
async def get_faq_effect_api(faq_id: str, request: Request):
    """获取 FAQ 的效果分数"""
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}, 503

    if application.effect_tracker:
        score = application.effect_tracker.get_faq_effect_score(faq_id)
        return {"faq_id": faq_id, "effect": score}
    return {"error": "effect_tracker not available"}


@app.get("/api/effects/user/{user_id}")
async def get_user_effect_api(user_id: str, request: Request):
    """获取用户的效果摘要和推荐风格"""
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}, 503

    if application.effect_tracker:
        summary = application.effect_tracker.get_user_effect_summary(user_id)
        style = application.effect_tracker.suggest_style(user_id)
        return {"user_id": user_id, "summary": summary, "suggested_style": style}
    return {"error": "effect_tracker not available"}


@app.get("/api/effects/low_performing_faqs")
async def get_low_performing_faqs_api(request: Request):
    """获取效果不佳的 FAQ 列表"""
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}, 503

    if application.effect_tracker:
        low = application.effect_tracker.get_low_performing_faqs()
        return {"low_performing_faqs": low, "count": len(low)}
    return {"error": "effect_tracker not available"}


# ============================================================
# 安全合规 API 端点
# ============================================================

@app.get("/api/safety/status")
async def safety_status(request: Request):
    """获取安全合规状态

    返回当前安全守卫的运行状态，包括：
    - 紧急停止状态
    - 当前上线阶段
    - 是否在工作时间内
    """
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}
    if not application.safety_guard:
        return {"error": "safety_guard not available"}
    sg = application.safety_guard
    return {
        "emergency_stop": sg.is_emergency_stopped(),
        "phase": sg.get_phase(),
        "is_work_hours": sg.is_work_hours(),
        "rate_limit": sg.config.get("rate_limit", {}),
        "work_hours": sg.config.get("work_hours", {}),
    }


@app.post("/api/safety/emergency_stop")
async def trigger_emergency_stop(request: Request):
    """触发紧急停止

    一键关闭所有自动回复，只接收消息不自动回复。
    需要手动调用 /api/safety/resume 恢复。
    """
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}
    if not application.safety_guard:
        return {"error": "safety_guard not available"}
    application.safety_guard.emergency_stop()
    return {"status": "stopped", "message": "紧急停止已触发，自动回复已暂停"}


@app.post("/api/safety/resume")
async def resume_from_emergency_stop(request: Request):
    """解除紧急停止"""
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}
    if not application.safety_guard:
        return {"error": "safety_guard not available"}
    application.safety_guard.resume()
    return {"status": "resumed", "message": "紧急停止已解除，自动回复恢复运行"}


@app.post("/api/safety/phase/{phase}")
async def set_rollout_phase(phase: int, request: Request):
    """切换上线阶段

    Phase 1: 所有回复走 suggest 模式（AI 预填，人工确认）
    Phase 2: FAQ >90% 自动回复
    Phase 3: FAQ + 咨询 >75% 自动回复
    """
    application: Optional[Application] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        return {"error": "Service not ready"}
    if not application.safety_guard:
        return {"error": "safety_guard not available"}
    if phase not in (1, 2, 3):
        return {"error": "phase must be 1, 2, or 3"}
    application.safety_guard.config["rollout"]["phase"] = phase
    logger.warning(f"上线阶段已切换为: Phase {phase}")
    return {"status": "ok", "phase": phase}


# ============================================================
# 加载管理后台路由
# ============================================================

try:
    from app.admin import router as admin_router
    app.include_router(admin_router)
    logger.info("管理后台路由已加载")
except ImportError:
    logger.warning("管理后台路由未加载")


# ============================================================
# 主入口
# ============================================================

def main() -> None:
    """主入口：使用 uvicorn 启动服务"""
    config = load_config(CONFIG_PATH)
    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 8000)
    import uvicorn
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
