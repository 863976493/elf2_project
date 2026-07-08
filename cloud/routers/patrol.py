"""
巡检任务 API
"""
import datetime
import json
import random

from fastapi import APIRouter, Body

import database as db
from inspect_result_utils import merge_inspect_result_json, save_inspect_images
from mqtt_client import reset_anomaly_cooldown
from models import (
    InspectionResult,
    InspectRegionRequest,
    PatrolPathCreate,
    PatrolTaskCreate,
    PresetPointCreate,
    RandomPatrolCreate,
)
from ws_manager import robot_manager

router = APIRouter(prefix="/api/patrol", tags=["patrol"])


def _manual_region_voice_text(region: str) -> str:
    return f"收到云端手动巡检指令，机器人即将前往{region}区执行检测任务。"


@router.get("/tasks")
def list_tasks(zone_id: int = None, limit: int = 50):
    """获取巡检任务列表"""
    return {"ok": True, "tasks": db.get_patrol_tasks(limit, zone_id)}



@router.delete("/tasks")
def clear_tasks():
    """Clear all patrol task records."""
    deleted = db.clear_patrol_tasks()
    return {"ok": True, "deleted": deleted}
@router.post("/tasks")
async def create_task(task: PatrolTaskCreate):
    """创建巡检任务，下发至机器人"""
    task_id = db.insert_patrol_task(task.zone_id, task.type, task.path_data_json)
    waypoints = json.loads(task.path_data_json)
    # 向机器人下发指令
    sent = await robot_manager.send_command({
        "type": "start_patrol",
        "data": {"task_id": task_id, "zone_id": task.zone_id, "waypoints": waypoints}
    })
    if sent:
        db.update_patrol_task_status(task_id, "running")
    return {"ok": True, "task_id": task_id, "sent": sent}



@router.post("/inspect_region")
async def inspect_region(req: InspectRegionRequest):
    """Trigger the board-side complete strawberry inspection action for region A or B."""
    region = (req.region or "").strip().upper()
    if region not in {"A", "B"}:
        return {"ok": False, "error": "region must be A or B"}
    if not robot_manager.is_connected:
        return {"ok": False, "error": "robot is not connected"}

    task_id = f"cloud_{region}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
    db.insert_inspect_patrol_task(task_id, region)
    voice_text = _manual_region_voice_text(region)
    sent = await robot_manager.send_command({
        "type": "inspect_region",
        "data": {
            "region": region,
            "task_id": task_id,
            "trigger": "manual",
            "voice_text": voice_text,
        },
    })
    if not sent:
        db.update_patrol_task_status(task_id, "failed", "robot is not connected")
        return {"ok": False, "error": "robot is not connected"}

    db.update_patrol_task_status(task_id, "running", "sent")
    return {"ok": True, "task_id": task_id, "sent": True}


@router.post("/anomaly_cooldown/reset")
def reset_sensor_anomaly_cooldown(req: InspectRegionRequest | None = Body(default=None)):
    """Allow the next humidity anomaly to trigger inspection immediately."""
    region = (req.region if req else "").strip().upper()
    if region and region not in {"A", "B"}:
        return {"ok": False, "error": "region must be A or B"}
    reset_count = reset_anomaly_cooldown(region or None)
    return {"ok": True, "region": region or "ALL", "reset_count": reset_count}


@router.post("/inspection_result")
async def inspection_result(result: InspectionResult):
    payload = result.model_dump(exclude_none=True, exclude={"images"})
    merged = {}
    if isinstance(result.result_json, dict):
        merged.update(result.result_json)
    elif result.result_json:
        try:
            parsed = json.loads(str(result.result_json))
            if isinstance(parsed, dict):
                merged.update(parsed)
            else:
                merged["result"] = parsed
        except json.JSONDecodeError:
            merged["raw"] = str(result.result_json)
    if result.disease is not None:
        merged["disease"] = result.disease
    if result.maturity is not None:
        merged["maturity"] = result.maturity
    if result.image_path:
        merged["image_path"] = result.image_path
    image_urls = save_inspect_images(result.task_id, result.images or [])

    status = "completed" if result.success else "failed"
    message = result.message or ("ok" if result.success else "failed")
    result_json = json.dumps(merged or payload, ensure_ascii=False)
    result_json = merge_inspect_result_json(result_json, image_urls)
    db.update_patrol_task_status(
        result.task_id,
        status,
        message,
        result_json,
    )
    return {"ok": True, "status": status, "cloud_images": image_urls}

@router.get("/paths")
def list_paths(zone_id: int = None):
    """获取保存的路径列表"""
    return {"ok": True, "paths": db.get_patrol_paths(zone_id)}


@router.post("/paths")
def save_path(path: PatrolPathCreate):
    """保存巡检路径"""
    path_id = db.save_patrol_path(path.zone_id, path.name, path.waypoints_json)
    return {"ok": True, "id": path_id}


@router.delete("/paths/{path_id}")
def delete_path(path_id: int):
    """删除巡检路径"""
    db.delete_patrol_path(path_id)
    return {"ok": True}


@router.get("/preset_points")
def list_preset_points(zone_id: int = None):
    """获取预置点列表"""
    return {"ok": True, "points": db.get_preset_points(zone_id)}


@router.post("/preset_points")
def save_preset_point(point: PresetPointCreate):
    """保存预置点"""
    point_id = db.save_preset_point(point.zone_id, point.name, point.x, point.y, point.theta)
    return {"ok": True, "id": point_id}


@router.delete("/preset_points/{point_id}")
def delete_preset_point(point_id: int):
    """删除预置点"""
    db.delete_preset_point(point_id)
    return {"ok": True}


@router.post("/tasks/random")
async def create_random_task(task: RandomPatrolCreate):
    """从预置点集合中随机抽取若干点，生成巡检任务并下发"""
    points = db.get_preset_points(task.zone_id)
    if not points:
        return {"ok": False, "error": "当前园区暂无预置点"}

    count = max(1, min(task.count, len(points)))
    selected = random.sample(points, count)
    waypoints = [
        {"x": p["x"], "y": p["y"], "theta": p.get("theta", 0.0), "name": p.get("name", "")}
        for p in selected
    ]
    path_data_json = json.dumps(waypoints, ensure_ascii=False)
    task_id = db.insert_patrol_task(task.zone_id, "random", path_data_json)

    sent = await robot_manager.send_command({
        "type": "start_patrol",
        "data": {"task_id": task_id, "zone_id": task.zone_id, "waypoints": waypoints}
    })
    if sent:
        db.update_patrol_task_status(task_id, "running")

    return {
        "ok": True,
        "task_id": task_id,
        "sent": sent,
        "requested_count": task.count,
        "selected_count": count,
        "waypoints": waypoints,
    }
