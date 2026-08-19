"""企业微信 API 客户端模块。

使用 httpx 异步客户端实现企业微信 API 的调用，包括 access_token 管理
和各类消息的发送。token 缓存使用内存变量 + 过期时间检查，提前 5 分钟刷新。

关键 API：
    - 获取 token: GET https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=X&corpsecret=Y
      返回 {"access_token": "...", "expires_in": 7200}
    - 发送消息: POST https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=Z
      请求体: {"touser": "UserID", "msgtype": "text", "agentid": 1000002, "text": {"content": "..."}}
"""

import time
from typing import Optional

import httpx


class WeComError(Exception):
    """企业微信 API 错误异常。

    当 API 返回的 errcode 不为 0 时抛出此异常。

    Attributes:
        errcode: 错误码
        errmsg: 错误描述
    """

    def __init__(self, errcode: int, errmsg: str) -> None:
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"[{errcode}] {errmsg}")


class WeComClient:
    """企业微信 API 客户端。

    通过 corpid、corpsecret 和 agentid 初始化，支持 access_token 的自动
    获取与缓存（提前 5 分钟刷新），以及文本、markdown、图片消息的发送。

    Attributes:
        corpid: 企业 ID
        corpsecret: 应用的凭证密钥
        agentid: 应用 ID
    """

    BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"

    def __init__(self, corpid: str, corpsecret: str, agentid: int) -> None:
        """初始化企业微信 API 客户端。

        Args:
            corpid: 企业 ID
            corpsecret: 应用的凭证密钥
            agentid: 应用 ID
        """
        self.corpid = corpid
        self.corpsecret = corpsecret
        self.agentid = agentid
        # token 缓存：内存变量 + 过期时间
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    async def _get_access_token(self) -> str:
        """获取并缓存 access_token，提前 5 分钟刷新。

        如果内存中已有有效的 token（且距离过期还有超过 5 分钟），
        则直接返回缓存的 token；否则向企业微信服务器请求新的 token。

        Returns:
            有效的 access_token 字符串

        Raises:
            WeComError: 如果获取 token 失败（errcode != 0）
            httpx.HTTPStatusError: 如果 HTTP 请求失败
        """
        # 提前 5 分钟（300 秒）刷新 token
        if self._access_token and time.time() < self._expires_at - 300:
            return self._access_token

        url = f"{self.BASE_URL}/gettoken"
        params = {"corpid": self.corpid, "corpsecret": self.corpsecret}

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("errcode", 0) != 0:
            raise WeComError(data["errcode"], data.get("errmsg", ""))

        self._access_token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 7200)
        return self._access_token

    async def _send_message(self, msg: dict) -> dict:
        """发送消息的通用内部方法。

        Args:
            msg: 消息请求体字典，包含 touser、msgtype、agentid 等

        Returns:
            企业微信 API 的响应字典

        Raises:
            WeComError: 如果发送消息失败（errcode != 0）
            httpx.HTTPStatusError: 如果 HTTP 请求失败
        """
        token = await self._get_access_token()
        url = f"{self.BASE_URL}/message/send"
        params = {"access_token": token}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, params=params, json=msg)
            resp.raise_for_status()
            data = resp.json()

        if data.get("errcode", 0) != 0:
            raise WeComError(data["errcode"], data.get("errmsg", ""))

        return data

    async def send_text(self, touser: str, content: str) -> dict:
        """发送文本消息。

        Args:
            touser: 接收消息的用户 ID（多用户用 "|" 分隔）
            content: 消息文本内容

        Returns:
            企业微信 API 的响应字典

        Raises:
            WeComError: 如果发送失败
        """
        msg = {
            "touser": touser,
            "msgtype": "text",
            "agentid": self.agentid,
            "text": {"content": content},
        }
        return await self._send_message(msg)

    async def send_markdown(self, touser: str, content: str) -> dict:
        """发送 markdown 消息。

        注意：markdown 消息仅在企业微信客户端中显示，不支持微信。

        Args:
            touser: 接收消息的用户 ID（多用户用 "|" 分隔）
            content: markdown 格式的消息内容

        Returns:
            企业微信 API 的响应字典

        Raises:
            WeComError: 如果发送失败
        """
        msg = {
            "touser": touser,
            "msgtype": "markdown",
            "agentid": self.agentid,
            "markdown": {"content": content},
        }
        return await self._send_message(msg)

    async def send_image(self, touser: str, media_id: str) -> dict:
        """发送图片消息。

        Args:
            touser: 接收消息的用户 ID（多用户用 "|" 分隔）
            media_id: 图片媒体的 ID（通过素材上传接口获取）

        Returns:
            企业微信 API 的响应字典

        Raises:
            WeComError: 如果发送失败
        """
        msg = {
            "touser": touser,
            "msgtype": "image",
            "agentid": self.agentid,
            "image": {"media_id": media_id},
        }
        return await self._send_message(msg)
