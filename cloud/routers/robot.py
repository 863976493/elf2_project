"""
机器人状态与控制 API
"""
from fastapi import APIRouter

import database as db
from models import RobotCommand
from ws_manager import robot_manager

router = APIRouter(prefix="/api/robot", tags=["robot"])


@router.get("/status")
def robot_status():
    """获取机器人状态"""
    status = db.get_robot_status()
    status["ws_connected"] = robot_manager.is_connected
    return {"ok": True, "status": status}


@router.post("/status")
async def update_robot_status(data: dict):
    """HTTP 更新机器人状态（供 mock_sender 等外部工具调用）"""
    db.upsert_robot_status(
        data.get("battery", 0), data.get("x", 0),
        data.get("y", 0), data.get("state", "idle")
    )
    from ws_manager import manager
    await manager.broadcast({"type": "robot_status", "data": data})
    return {"ok": True}


@router.post("/command")
async def send_command(cmd: RobotCommand):
    """向机器人发送指令"""
    msg = {"type": cmd.command, "data": cmd.data}
    success = await robot_manager.send_command(msg)
    if not success:
        return {"ok": False, "error": "机器人未连接"}
    return {"ok": True}


@router.post("/navigate")
async def navigate_to(data: dict):
    """云端→板端: 单点导航"""
    msg = {"type": "navigate_to", "data": data}
    success = await robot_manager.send_command(msg)
    if not success:
        return {"ok": False, "error": "机器人未连接"}
    return {"ok": True}


@router.post("/waypoint_nav")
async def start_waypoint_nav(data: dict):
    """多点导航 (FollowWaypoints)"""
    msg = {"type": "start_waypoint_nav", "data": data}
    success = await robot_manager.send_command(msg)
    if not success:
        return {"ok": False, "error": "机器人未连接"}
    return {"ok": True}


@router.post("/cancel_nav")
async def cancel_nav():
    """取消当前导航"""
    success = await robot_manager.send_command({"type": "cancel_nav", "data": {}})
    if not success:
        return {"ok": False, "error": "机器人未连接"}
    return {"ok": True}


@router.post("/save_map")
async def save_map(data: dict):
    """触发 bridge 保存当前 SLAM 地图并上传到云端"""
    msg = {"type": "save_map", "data": data}
    success = await robot_manager.send_command(msg)
    if not success:
        return {"ok": False, "error": "机器人未连接"}
    return {"ok": True, "message": "保存指令已下发，请稍候查看状态"}


@router.post("/set_pose")
async def set_initial_pose(data: dict):
    """设置 AMCL 初始位姿"""
    msg = {"type": "set_initial_pose", "data": data}
    success = await robot_manager.send_command(msg)
    if not success:
        return {"ok": False, "error": "机器人未连接"}
    return {"ok": True}
