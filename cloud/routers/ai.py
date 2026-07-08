"""
AI 分析 API
"""
from fastapi import APIRouter
from ai_analyzer import analyze

router = APIRouter(prefix="/api", tags=["ai"])


@router.post("/analyze")
async def ai_analyze():
    """AI 农业专家分析"""
    result = await analyze()
    return result
