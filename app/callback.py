"""企业微信回调路由模块。

使用 FastAPI 实现企业微信回调服务，包括 URL 验证（GET）和消息接收（POST）。
回调消息的验签和解密使用 app/crypto.py 中的函数。

路由说明：
    - GET /wecom/callback：URL 验证
      参数：msg_signature, timestamp, nonce, echostr
      流程：验签 -> 解密 echostr -> 返回明文（不能有引号、BOM、换行）

    - POST /wecom/callback：接收消息
      参数：msg_signature, timestamp, nonce（query params）
      body：XML 格式，包含 <ToUserName>, <AgentID>, <Encrypt>
      流程：验签 -> 解密 Encrypt -> 解析 XML 得到消息内容
      必须在 5 秒内响应，返回 "success"
      解析后的消息通过 callback 函数异步处理
"""

import asyncio
import xml.etree.ElementTree as ET
from typing import Any, Awaitable, Callable, Optional, Union

from fastapi import FastAPI, Query, Request
from fastapi.responses import PlainTextResponse

from .crypto import decrypt_msg, generate_signature


class CallbackHandler:
    """企业微信回调处理器。

    负责处理企业微信回调的验签、解密和消息解析。

    Attributes:
        token: 企业微信后台配置的 Token
        encoding_aes_key: 43 位的 EncodingAESKey
        corpid: 企业 ID
    """

    def __init__(self, token: str, encoding_aes_key: str, corpid: str) -> None:
        """初始化回调处理器。

        Args:
            token: 企业微信后台配置的 Token
            encoding_aes_key: 43 位的 EncodingAESKey
            corpid: 企业 ID
        """
        self.token = token
        self.encoding_aes_key = encoding_aes_key
        self.corpid = corpid

    def handle_get(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        echostr: str,
    ) -> str:
        """处理 URL 验证请求。

        企业微信在配置回调 URL 时，会发送 GET 请求验证 URL 的有效性。
        本方法验证签名并解密 echostr，返回明文。

        流程：
            1. 使用 token、timestamp、nonce、echostr 计算签名
            2. 与企业微信传来的 msg_signature 比较
            3. 解密 echostr 得到明文
            4. 验证 receiveid 与 corpid 一致

        Args:
            msg_signature: 企业微信传来的签名
            timestamp: 时间戳
            nonce: 随机数
            echostr: 加密的随机字符串

        Returns:
            解密后的明文字符串

        Raises:
            ValueError: 如果签名验证失败或 receiveid 不匹配
        """
        # 验签
        signature = generate_signature(self.token, timestamp, nonce, echostr)
        if signature != msg_signature:
            raise ValueError("签名验证失败")

        # 解密 echostr
        plaintext, receiveid = decrypt_msg(echostr, self.encoding_aes_key)
        if receiveid != self.corpid:
            raise ValueError(
                f"receiveid 与 corpid 不匹配: 期望 {self.corpid}, 实际 {receiveid}"
            )

        return plaintext

    def handle_post(
        self,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        body: str,
    ) -> dict:
        """处理消息回调请求。

        企业微信在收到用户消息后，会向回调 URL 发送 POST 请求。
        本方法验证签名、解密消息并解析 XML，返回结构化的消息字典。

        流程：
            1. 解析外层 XML 获取 Encrypt 字段
            2. 使用 token、timestamp、nonce、encrypt 计算签名
            3. 与企业微信传来的 msg_signature 比较
            4. 解密 Encrypt 得到内层消息 XML
            5. 验证 receiveid 与 corpid 一致
            6. 解析内层 XML，提取消息字段

        Args:
            msg_signature: 企业微信传来的签名
            timestamp: 时间戳
            nonce: 随机数
            body: 请求体 XML 字符串

        Returns:
            解析后的消息字典，格式为：
            {
                "from_user": "...",   # 发送者 UserID
                "to_user": "...",     # 接收者
                "msg_type": "text",   # 消息类型
                "content": "...",     # 消息内容
                "msg_id": "...",      # 消息 ID
                "create_time": ...,   # 消息创建时间（Unix 时间戳）
            }

        Raises:
            ValueError: 如果签名验证失败或 receiveid 不匹配
        """
        # 解析外层 XML 获取 Encrypt 字段
        root = ET.fromstring(body)
        encrypt_elem = root.find("Encrypt")
        if encrypt_elem is None or encrypt_elem.text is None:
            raise ValueError("请求体中未找到 Encrypt 字段")
        encrypt = encrypt_elem.text

        # 验签
        signature = generate_signature(self.token, timestamp, nonce, encrypt)
        if signature != msg_signature:
            raise ValueError("签名验证失败")

        # 解密
        plaintext, receiveid = decrypt_msg(encrypt, self.encoding_aes_key)
        if receiveid != self.corpid:
            raise ValueError(
                f"receiveid 与 corpid 不匹配: 期望 {self.corpid}, 实际 {receiveid}"
            )

        # 解析内层消息 XML
        msg_root = ET.fromstring(plaintext)

        def _get_text(tag: str) -> Optional[str]:
            """从 XML 中安全获取指定标签的文本内容。"""
            elem = msg_root.find(tag)
            return elem.text if elem is not None and elem.text is not None else None

        create_time_str = _get_text("CreateTime")
        create_time = int(create_time_str) if create_time_str else None

        return {
            "from_user": _get_text("FromUserName"),
            "to_user": _get_text("ToUserName"),
            "msg_type": _get_text("MsgType"),
            "content": _get_text("Content"),
            "msg_id": _get_text("MsgId"),
            "create_time": create_time,
        }


def create_callback_app(
    handler: CallbackHandler,
    message_callback: Optional[Callable[[dict], Union[Any, Awaitable[Any]]]] = None,
) -> FastAPI:
    """创建企业微信回调 FastAPI 应用。

    创建一个包含 GET 和 POST 路由的 FastAPI 应用实例：
        - GET /wecom/callback：URL 验证
        - POST /wecom/callback：接收消息

    Args:
        handler: CallbackHandler 实例
        message_callback: 消息处理回调函数，可以是同步或异步函数。
            当收到消息时，解析后的消息字典会传给此函数异步处理。

    Returns:
        配置好路由的 FastAPI 应用实例
    """
    app = FastAPI(title="企业微信回调服务")

    @app.get("/wecom/callback", response_class=PlainTextResponse)
    async def verify_url(
        msg_signature: str = Query(..., description="企业微信传来的签名"),
        timestamp: str = Query(..., description="时间戳"),
        nonce: str = Query(..., description="随机数"),
        echostr: str = Query(..., description="加密的随机字符串"),
    ) -> PlainTextResponse:
        """处理企业微信的 URL 验证请求。

        返回解密后的明文（不能有引号、BOM、换行）。
        """
        result = handler.handle_get(msg_signature, timestamp, nonce, echostr)
        # 使用 PlainTextResponse 确保返回纯文本，无引号、无 BOM、无换行
        return PlainTextResponse(content=result, media_type="text/plain")

    @app.post("/wecom/callback")
    async def receive_message(
        request: Request,
        msg_signature: str = Query(..., description="企业微信传来的签名"),
        timestamp: str = Query(..., description="时间戳"),
        nonce: str = Query(..., description="随机数"),
    ) -> str:
        """处理企业微信的消息回调请求。

        必须在 5 秒内响应，返回 "success"。解析后的消息通过
        message_callback 异步处理。
        """
        body = (await request.body()).decode("utf-8")
        msg = handler.handle_post(msg_signature, timestamp, nonce, body)

        # 异步处理消息，不阻塞响应
        if message_callback is not None:
            if asyncio.iscoroutinefunction(message_callback):
                asyncio.create_task(message_callback(msg))
            else:
                # 同步回调也要放到线程池中执行，避免阻塞事件循环
                loop = asyncio.get_event_loop()
                loop.run_in_executor(None, message_callback, msg)

        return "success"

    return app
