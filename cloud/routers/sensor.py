"""
传感器数据 API
"""
import datetime
from fastapi import APIRouter

import database as db
from models import SensorBatch, SimpleSensorData
from ws_manager import manager, robot_manager, video_manager
from mqtt_client import get_mqtt_status, get_fresh_esp32_online

router = APIRouter(prefix="/api", tags=["sensor"])


@router.post("/sensor_data")
async def receive_sensor(batch: SensorBatch):
    """接收传感器数据批量上传"""
    records = [r.model_dump() for r in batch.records]
    for r in records:
        if not r.get("source") or r.get("source") == "unknown":
            r["source"] = "mock"
    count = db.insert_sensor(records)
    latest = db.get_latest_sensor()
    await manager.broadcast({"type": "sensor_update", "data": latest})
    return {"ok": True, "count": count}


@router.post("/data")
async def receive_simple(data: SimpleSensorData):
    """简化版数据接收（兼容旧格式）"""
    db.insert_sensor([{"temperature": data.temp, "humidity": data.humidity, "source": "mock"}])
    if data.disease_type != "none" and data.disease_count > 0:
        db.insert_detections([{
            "time": datetime.datetime.now().strftime("%m-%d %H:%M:%S"),
            "type": "disease_check",
            "result": data.disease_type,
            "disease_count": data.disease_count
        }])
    latest = db.get_latest_sensor()
    await manager.broadcast({"type": "sensor_update", "data": latest})
    return {"status": "ok"}


@router.get("/dashboard")
def dashboard():
    """前端大屏一次性拉取所有数据"""
    return {
        "ok": True,
        "latest_sensor": db.get_latest_sensor(),
        "sensor_history": db.get_sensor_history(100),
        "detections": db.get_detections(50),
        "alerts": db.get_alerts(30),
        "disease_stats": db.get_disease_stats(),
        "latest_analysis": db.get_latest_analysis(),
        "counts": db.get_counts(),
        "robot_status": db.get_robot_status(),
        "robot_connected": robot_manager.is_connected,
        "video": {
            "pusher_connected": video_manager.is_live,
            "viewer_count": video_manager.viewer_count,
        },
        "zones": db.get_zones(),
        "mqtt": get_mqtt_status(),
        "esp32_online": get_fresh_esp32_online(),
    }
