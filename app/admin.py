"""
企业微信客服系统 - 管理后台 API

提供统计信息查询、FAQ 管理、会话管理等管理端点。

统计信息存储在 SQLite 数据库中：
- 表 messages：id, user_id, content, reply, mode, timestamp
- 表 stats：date, total_messages, auto_replies, human_replies

注意：此模块通过 request.app.state.application 访问主应用实例，
不直接导入 app.main 以避免循环依赖。
"""
from typing import TYPE_CHECKING, Optional, Dict, Any, List

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.main import Application

router = APIRouter(prefix="/admin", tags=["管理后台"])


# ============================================================
# Pydantic 模型（请求体验证）
# ============================================================

class FAQItem(BaseModel):
    """FAQ 数据模型"""
    keywords: List[str] = Field(..., description="关键词列表")
    question: str = Field("", description="标准问题")
    answer: str = Field(..., description="回答内容")
    confidence: int = Field(95, description="置信度（0-100）")


class FAQUpdate(BaseModel):
    """FAQ 更新模型（字段可选）"""
    keywords: Optional[List[str]] = Field(None, description="关键词列表")
    question: Optional[str] = Field(None, description="标准问题")
    answer: Optional[str] = Field(None, description="回答内容")
    confidence: Optional[int] = Field(None, description="置信度（0-100）")


# ============================================================
# 辅助函数
# ============================================================

def _get_app(request: Request) -> "Application":
    """从请求中获取应用实例"""
    application: Optional["Application"] = getattr(
        request.app.state, "application", None
    )
    if application is None:
        raise HTTPException(status_code=503, detail="服务未就绪")
    return application


# ============================================================
# 统计信息
# ============================================================

@router.get("/stats", summary="获取统计信息")
async def get_stats(request: Request) -> Dict[str, Any]:
    """获取今日统计信息

    返回：今日消息数、回复数、自动回复率等
    """
    app = _get_app(request)
    return app.get_stats()


# ============================================================
# FAQ 管理
# ============================================================

@router.get("/faqs", summary="获取所有FAQ")
async def get_faqs(request: Request) -> List[Dict[str, Any]]:
    """获取所有 FAQ 列表"""
    app = _get_app(request)
    return app.get_faqs()


@router.post("/faqs", summary="添加FAQ")
async def add_faq(request: Request, faq: FAQItem) -> Dict[str, Any]:
    """添加新的 FAQ

    请求体：
    - keywords: 关键词列表
    - question: 标准问题
    - answer: 回答内容
    - confidence: 置信度（默认 95）
    """
    app = _get_app(request)
    faq_data = faq.model_dump()
    return app.add_faq(faq_data)


@router.put("/faqs/{faq_id}", summary="更新FAQ")
async def update_faq(
    request: Request,
    faq_id: int,
    faq: FAQUpdate,
) -> Dict[str, Any]:
    """更新指定 ID 的 FAQ

    路径参数：
    - faq_id: FAQ ID

    请求体（字段可选）：
    - keywords: 关键词列表
    - question: 标准问题
    - answer: 回答内容
    - confidence: 置信度
    """
    app = _get_app(request)
    faq_data = faq.model_dump(exclude_none=True)
    result = app.update_faq(faq_id, faq_data)
    if result is None:
        raise HTTPException(status_code=404, detail=f"FAQ {faq_id} 不存在")
    return result


@router.delete("/faqs/{faq_id}", summary="删除FAQ")
async def delete_faq(request: Request, faq_id: int) -> Dict[str, Any]:
    """删除指定 ID 的 FAQ

    路径参数：
    - faq_id: FAQ ID
    """
    app = _get_app(request)
    success = app.delete_faq(faq_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"FAQ {faq_id} 不存在")
    return {"success": True, "faq_id": faq_id}


# ============================================================
# 会话管理
# ============================================================

@router.get("/sessions/{user_id}", summary="查看用户会话历史")
async def get_session_history(
    request: Request,
    user_id: str,
) -> List[Dict[str, Any]]:
    """查看指定用户的会话历史

    路径参数：
    - user_id: 用户ID
    """
    app = _get_app(request)
    return app.get_session_history(user_id)


@router.post("/sessions/{user_id}/clear", summary="清空用户会话")
async def clear_session(
    request: Request,
    user_id: str,
) -> Dict[str, Any]:
    """清空指定用户的会话上下文

    路径参数：
    - user_id: 用户ID
    """
    app = _get_app(request)
    success = app.clear_session(user_id)
    return {"success": success, "user_id": user_id}
