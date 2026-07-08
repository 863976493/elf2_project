"""
检测结果 & 预警 API
"""
from fastapi import APIRouter

import database as db
from models import DetectionBatch, AlertBatch, AnalysisBatch
from ws_manager import manager

router = APIRouter(prefix="/api", tags=["detection"])


@router.post("/detections")
async def receive_detections(batch: DetectionBatch):
    """接收检测记录批量上传"""
    records = [r.model_dump() for r in batch.records]
    count = db.insert_detections(records)
    latest_list = db.get_detections(1)
    if latest_list:
        await manager.broadcast({"type": "new_detection", "data": latest_list[0]})
        if latest_list[0].get("disease_count", 0) > 0:
            await manager.broadcast({
                "type": "disease_alert",
                "data": {
                    "result": latest_list[0].get("result", ""),
                    "disease_count": latest_list[0].get("disease_count", 0),
                    "conf": latest_list[0].get("conf", ""),
                    "time": latest_list[0].get("time", "")
                }
            })
    return {"ok": True, "count": count}


@router.post("/alerts")
async def receive_alerts(batch: AlertBatch):
    """接收预警记录批量上传"""
    records = [r.model_dump() for r in batch.records]
    count = db.insert_alerts(records)
    if records:
        await manager.broadcast({"type": "new_alert", "data": records[-1]})
    return {"ok": True, "count": count}


@router.post("/analysis")
async def receive_analysis(batch: AnalysisBatch):
    """接收分析结果批量上传"""
    records = [r.model_dump() for r in batch.records]
    count = db.insert_analysis(records)
    return {"ok": True, "count": count}
