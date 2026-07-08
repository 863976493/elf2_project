"""
Application settings API.
"""
from fastapi import APIRouter

import database as db
from models import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings():
    settings = db.get_settings()
    return {**settings, "ai_api_key_configured": bool(settings.get("ai_api_key"))}


@router.post("")
def save_settings(data: SettingsUpdate):
    db.upsert_settings(data.model_dump())
    settings = db.get_settings()
    return {"ok": True, **settings, "ai_api_key_configured": bool(settings.get("ai_api_key"))}
