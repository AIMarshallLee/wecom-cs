"""企业微信回调消息的 AES-256-CBC 加解密和 SHA1 签名验证模块。

本模块实现了企业微信官方文档中描述的消息加解密方案，使用纯函数实现，
无需实例化任何类即可使用。

关键实现细节（来自企业微信官方文档）：
    - EncodingAESKey 是 43 位字符串，解码时在末尾加 "=" 变成 44 位 Base64
    - AES Key = base64.b64decode(EncodingAESKey + "=")，得到 32 字节密钥
    - IV = Key 的前 16 字节
    - 加密后的明文格式：random(16字节) + msg_len(4字节大端序) + msg内容 + receiveid
    - 解密时需要验证 msg_len 与实际 msg 长度一致
    - 签名算法：SHA1(sort([token, timestamp, nonce, encrypt]).join(""))
"""

import base64
import hashlib
import os
import struct
from typing import Tuple

from Crypto.Cipher import AES

# 企业微信自定义 PKCS7 填充的块大小为 32 字节
_BLOCK_SIZE = 32


def _pkcs7_encode(text: bytes, block_size: int = _BLOCK_SIZE) -> bytes:
    """对明文进行 PKCS7 填充。

    企业微信使用 32 字节作为填充块大小（而非标准 AES 的 16 字节）。
    填充规则：amount_to_pad = block_size - (text_len % block_size)，
    如果结果为 0，则填充完整的 block_size 个字节。

    Args:
        text: 待填充的明文字节
        block_size: 填充块大小，默认为 32

    Returns:
        填充后的字节串
    """
    text_len = len(text)
    amount_to_pad = block_size - (text_len % block_size)
    if amount_to_pad == 0:
        amount_to_pad = block_size
    return text + bytes([amount_to_pad]) * amount_to_pad


def _pkcs7_decode(decrypted: bytes, block_size: int = _BLOCK_SIZE) -> bytes:
    """去除 PKCS7 填充。

    根据最后一个字节的值确定填充长度，并去除相应长度的填充字节。

    Args:
        decrypted: 解密后的字节串（含填充）
        block_size: 填充块大小，默认为 32

    Returns:
        去除填充后的字节串
    """
    pad = decrypted[-1]
    if pad < 1 or pad > block_size:
        # 填充值不合法，可能是未填充的数据，直接返回
        pad = 0
    return decrypted[:-pad] if pad > 0 else decrypted


def _get_aes_key(encoding_aes_key: str) -> bytes:
    """从 EncodingAESKey 派生 32 字节的 AES 密钥。

    EncodingAESKey 是 43 位 Base64 字符串，在末尾追加 "=" 后进行 Base64 解码，
    得到 32 字节的 AES 密钥。

    Args:
        encoding_aes_key: 43 位的 EncodingAESKey 字符串

    Returns:
        32 字节的 AES 密钥
    """
    return base64.b64decode(encoding_aes_key + "=")


def generate_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """生成消息签名。

    签名算法：将 token、timestamp、nonce、encrypt 四个参数进行字典序排序，
    拼接后计算 SHA1 摘要，返回十六进制字符串。

    Args:
        token: 企业微信后台配置的 Token
        timestamp: 时间戳
        nonce: 随机数
        encrypt: 加密后的消息（base64 编码的密文）

    Returns:
        40 位十六进制 SHA1 签名字符串
    """
    sort_list = sorted([token, timestamp, nonce, encrypt])
    raw = "".join(sort_list).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def verify_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """计算消息签名（与 generate_signature 功能相同）。

    计算签名后，调用方可将其与企业微信传来的 msg_signature 进行比较，
    以验证消息的真实性。

    Args:
        token: 企业微信后台配置的 Token
        timestamp: 时间戳
        nonce: 随机数
        encrypt: 加密后的消息（base64 编码的密文）

    Returns:
        40 位十六进制 SHA1 签名字符串
    """
    return generate_signature(token, timestamp, nonce, encrypt)


def encrypt_msg(msg: str, encoding_aes_key: str, receiveid: str) -> str:
    """加密消息，返回 base64 编码的密文。

    加密流程：
        1. 构造明文：random(16字节) + msg_len(4字节大端序) + msg内容 + receiveid
        2. 进行 PKCS7 填充（块大小 32）
        3. 使用 AES-256-CBC 加密（IV 为密钥前 16 字节）
        4. Base64 编码输出

    Args:
        msg: 待加密的消息明文
        encoding_aes_key: 43 位的 EncodingAESKey
        receiveid: 接收方 ID（企业微信中为 corpid）

    Returns:
        base64 编码的密文字符串
    """
    aes_key = _get_aes_key(encoding_aes_key)
    iv = aes_key[:16]

    msg_bytes = msg.encode("utf-8")
    random_bytes = os.urandom(16)
    msg_len = struct.pack(">I", len(msg_bytes))
    plaintext = random_bytes + msg_len + msg_bytes + receiveid.encode("utf-8")

    padded = _pkcs7_encode(plaintext)
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(padded)

    return base64.b64encode(encrypted).decode("utf-8")


def decrypt_msg(encrypt: str, encoding_aes_key: str) -> Tuple[str, str]:
    """解密消息，返回 (msg, receiveid)。

    解密流程：
        1. Base64 解码密文
        2. 使用 AES-256-CBC 解密（IV 为密钥前 16 字节）
        3. 去除 PKCS7 填充
        4. 解析明文结构：random(16) + msg_len(4) + msg + receiveid
        5. 验证 msg_len 与实际 msg 字节长度一致

    Args:
        encrypt: base64 编码的密文字符串
        encoding_aes_key: 43 位的 EncodingAESKey

    Returns:
        元组 (msg, receiveid)，其中 msg 为解密后的消息明文，
        receiveid 为接收方 ID

    Raises:
        ValueError: 如果 msg_len 与实际 msg 长度不匹配
    """
    aes_key = _get_aes_key(encoding_aes_key)
    iv = aes_key[:16]

    encrypted = base64.b64decode(encrypt)
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted)

    plaintext = _pkcs7_decode(decrypted)

    # 明文结构：random(16字节) + msg_len(4字节大端序) + msg内容 + receiveid
    msg_len = struct.unpack(">I", plaintext[16:20])[0]
    msg_bytes = plaintext[20:20 + msg_len]
    receiveid_bytes = plaintext[20 + msg_len:]

    msg = msg_bytes.decode("utf-8")
    receiveid = receiveid_bytes.decode("utf-8")

    # 验证 msg_len 与实际 msg 长度一致
    if len(msg_bytes) != msg_len:
        raise ValueError(
            f"消息长度不匹配: 期望 {msg_len} 字节, 实际 {len(msg_bytes)} 字节"
        )

    return msg, receiveid


class WXBizMsgCrypt:
    """企业微信消息加解密类（兼容 main.py 调用接口）。

    封装 crypto 模块的纯函数，提供与官方 SDK 一致的接口：
        - VerifyURL: URL 验证（GET 回调）
        - DecryptMsg: 消息解密（POST 回调）
        - EncryptMsg: 消息加密（回复消息）
    """

    def __init__(self, token: str, encoding_aes_key: str, corpid: str) -> None:
        """初始化

        Args:
            token: 企业微信后台配置的 Token
            encoding_aes_key: 43 位的 EncodingAESKey
            corpid: 企业 ID
        """
        self.token = token
        self.encoding_aes_key = encoding_aes_key
        self.corpid = corpid

    def VerifyURL(
        self, msg_signature: str, timestamp: str, nonce: str, echostr: str
    ) -> tuple:
        """URL 验证（GET 回调）

        验证签名并解密 echostr，返回明文。

        Args:
            msg_signature: 企业微信传来的签名
            timestamp: 时间戳
            nonce: 随机数
            echostr: 加密的随机字符串

        Returns:
            (ret, reply) 元组，ret=0 表示成功，reply 为解密后的明文
        """
        try:
            # 验签
            expected_sig = generate_signature(self.token, timestamp, nonce, echostr)
            if expected_sig != msg_signature:
                return (-40001, "")

            # 解密 echostr
            plaintext, receiveid = decrypt_msg(echostr, self.encoding_aes_key)

            # 验证 receiveid
            if receiveid != self.corpid:
                return (-40003, "")

            return (0, plaintext)
        except Exception:
            return (-40004, "")

    def DecryptMsg(
        self, post_xml: str, msg_signature: str, timestamp: str, nonce: str
    ) -> tuple:
        """消息解密（POST 回调）

        解析外层 XML，验证签名，解密 Encrypt 字段。

        Args:
            post_xml: 请求体 XML 字符串
            msg_signature: 企业微信传来的签名
            timestamp: 时间戳
            nonce: 随机数

        Returns:
            (ret, msg) 元组，ret=0 表示成功，msg 为解密后的消息 XML
        """
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(post_xml)
            encrypt_elem = root.find("Encrypt")
            if encrypt_elem is None or encrypt_elem.text is None:
                return (-40002, "")

            encrypt = encrypt_elem.text

            # 验签
            expected_sig = generate_signature(
                self.token, timestamp, nonce, encrypt
            )
            if expected_sig != msg_signature:
                return (-40001, "")

            # 解密
            plaintext, receiveid = decrypt_msg(encrypt, self.encoding_aes_key)

            # 验证 receiveid
            if receiveid != self.corpid:
                return (-40003, "")

            return (0, plaintext)
        except Exception:
            return (-40004, "")

    def EncryptMsg(
        self, reply_msg: str, timestamp: str, nonce: str
    ) -> tuple:
        """消息加密（回复消息）

        加密回复消息并构造 XML。

        Args:
            reply_msg: 回复消息明文
            timestamp: 时间戳
            nonce: 随机数

        Returns:
            (ret, xml) 元组，ret=0 表示成功，xml 为加密后的 XML 字符串
        """
        try:
            encrypt = encrypt_msg(reply_msg, self.encoding_aes_key, self.corpid)
            signature = generate_signature(self.token, timestamp, nonce, encrypt)

            xml = (
                "<xml>"
                f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
                f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
                f"<TimeStamp>{timestamp}</TimeStamp>"
                f"<Nonce><![CDATA[{nonce}]]></Nonce>"
                "</xml>"
            )
            return (0, xml)
        except Exception:
            return (-40004, "")
