"""
园区管理 API
"""
from fastapi import APIRouter, HTTPException

import database as db
from models import ZoneCreate, ZoneUpdate, ThresholdUpdate

router = APIRouter(prefix="/api/zones", tags=["zone"])


@router.get("")
def list_zones():
    """获取所有园区"""
    zones = db.get_zones()
    # 附带每个园区的最新传感器数据和阈值
    for z in zones:
        z["latest_sensor"] = db.get_latest_sensor(zone_id=z["id"])
        z["thresholds"] = db.get_zone_thresholds(z["id"])
    return {"ok": True, "zones": zones}


@router.post("")
def create_zone(zone: ZoneCreate):
    """创建园区"""
    zone_id = db.insert_zone(zone.name, zone.description, zone.esp32_device_id)
    return {"ok": True, "id": zone_id}


@router.get("/{zone_id}")
def get_zone(zone_id: int):
    """获取园区详情"""
    zone = db.get_zone(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="园区不存在")
    zone["latest_sensor"] = db.get_latest_sensor(zone_id=zone_id)
    zone["sensor_history"] = db.get_sensor_history(50, zone_id=zone_id)
    zone["detections"] = db.get_detections(20, zone_id=zone_id)
    zone["alerts"] = db.get_alerts(10, zone_id=zone_id)
    zone["thresholds"] = db.get_zone_thresholds(zone_id)
    return {"ok": True, "zone": zone}


@router.put("/{zone_id}")
def update_zone(zone_id: int, data: ZoneUpdate):
    """更新园区"""
    if not db.get_zone(zone_id):
        raise HTTPException(status_code=404, detail="园区不存在")
    db.update_zone(zone_id, data.name, data.description, data.esp32_device_id)
    return {"ok": True}


@router.delete("/{zone_id}")
def delete_zone(zone_id: int):
    """删除园区"""
    if not db.get_zone(zone_id):
        raise HTTPException(status_code=404, detail="园区不存在")
    db.delete_zone(zone_id)
    return {"ok": True}


@router.get("/{zone_id}/thresholds")
def get_thresholds(zone_id: int):
    """获取园区阈值"""
    t = db.get_zone_thresholds(zone_id)
    return {"ok": True, "thresholds": t}


@router.put("/{zone_id}/thresholds")
def update_thresholds(zone_id: int, data: ThresholdUpdate):
    """更新园区阈值"""
    db.upsert_zone_thresholds(
        zone_id, data.humi_min, data.humi_max,
        data.light_min, data.light_max, data.enabled
    )
    return {"ok": True}
