"""
草莓种植园巡检机器人 — 云端控制平台
FastAPI 主入口
启动: uvicorn main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import database as db
from config import STATIC_DIR, MAP_DIR, MQTT_ENABLED, UPLOAD_DIR
from inspect_result_utils import merge_inspect_result_json, save_inspect_images
from ws_manager import manager, video_manager, robot_manager
from mqtt_client import mqtt_loop, get_mqtt_status

# 路由导入
from routers import sensor, detection, zone, robot, media, ai, map as map_router, patrol, settings

# MQTT 后台任务
_mqtt_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mqtt_task
    # 启动时初始化数据库
    db.init_db()
    # 启动 MQTT 客户端（后台任务，连接失败会自动重连）
    if MQTT_ENABLED:
        _mqtt_task = asyncio.create_task(mqtt_loop())
        print("MQTT 客户端已启动（后台运行）")
    else:
        print("MQTT 未启用（设置环境变量 MQTT_ENABLED=true 开启）")
    yield
    # 关闭 MQTT
    if _mqtt_task and not _mqtt_task.done():
        _mqtt_task.cancel()
        try:
            await _mqtt_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="草莓种植园巡检机器人 · 云端控制平台", lifespan=lifespan)

# CORS（允许局域网所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ══════════════════════════════════════
# 注册路由
# ══════════════════════════════════════
app.include_router(sensor.router)
app.include_router(detection.router)
app.include_router(zone.router)
app.include_router(robot.router)
app.include_router(media.router)
app.include_router(ai.router)
app.include_router(map_router.router)
app.include_router(patrol.router)
app.include_router(settings.router)


# ══════════════════════════════════════
# WebSocket 端点
# ══════════════════════════════════════


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """前端数据广播"""
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.websocket("/ws/video/push")
async def video_push(ws: WebSocket):
    """边缘端推流入口"""
    await ws.accept()
    await video_manager.set_pusher(ws)
    await manager.broadcast({"type": "video_status", "data": {"pusher_connected": True}})
    try:
        while True:
            data = await ws.receive_bytes()
            await video_manager.relay(data)
    except WebSocketDisconnect:
        await video_manager.remove_pusher(ws)
        await manager.broadcast({"type": "video_status", "data": {"pusher_connected": False}})


@app.websocket("/ws/video/watch")
async def video_watch(ws: WebSocket):
    """浏览器观看入口"""
    await ws.accept()
    await video_manager.add_viewer(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        video_manager.remove_viewer(ws)


@app.websocket("/ws/robot")
async def robot_ws(ws: WebSocket):
    """机器人指令通道（双向：RK3588 ↔ 云端）"""
    await robot_manager.connect(ws)
    # 通知前端机器人已连接
    await manager.broadcast({"type": "robot_connected", "data": {}})
    try:
        while True:
            text = await ws.receive_text()
            msg = json.loads(text)
            msg_type = msg.get("type", "")
            data = msg.get("data", {})

            if msg_type == "status":
                # 机器人状态上报 → 存库 + 广播前端
                db.upsert_robot_status(
                    data.get("battery", 0),
                    data.get("x", 0),
                    data.get("y", 0),
                    data.get("state", "idle")
                )
                await manager.broadcast({"type": "robot_status", "data": data})

            elif msg_type == "patrol_complete":
                # 巡检完成
                task_id = data.get("task_id")
                result = data.get("result", "ok")
                if task_id:
                    db.update_patrol_task_status(task_id, "completed", result)
                await manager.broadcast({"type": "patrol_completed", "data": data})


            elif msg_type == "inspect_started":
                task_id = data.get("task_id")
                if task_id:
                    db.update_patrol_task_status(task_id, "running", "started")
                await manager.broadcast({"type": "inspect_started", "data": data})

            elif msg_type == "inspect_feedback":
                await manager.broadcast({"type": "inspect_feedback", "data": data})

            elif msg_type == "inspect_complete":
                task_id = data.get("task_id")
                message = data.get("message", "ok")
                result_json = data.get("result_json", "")
                image_urls = save_inspect_images(str(task_id or ""), data.get("images", []))
                result_json = merge_inspect_result_json(result_json, image_urls)
                data["result_json"] = result_json
                if image_urls:
                    data["cloud_images"] = image_urls
                if task_id:
                    db.update_patrol_task_status(task_id, "completed", message, result_json)
                await manager.broadcast({"type": "inspect_complete", "data": data})

            elif msg_type in {"inspect_failed", "inspect_busy"}:
                task_id = data.get("task_id")
                message = data.get("message", msg_type)
                result_json = data.get("result_json", "")
                image_urls = save_inspect_images(str(task_id or ""), data.get("images", []))
                result_json = merge_inspect_result_json(result_json, image_urls)
                data["result_json"] = result_json
                if image_urls:
                    data["cloud_images"] = image_urls
                if task_id:
                    db.update_patrol_task_status(task_id, "failed", message, result_json)
                await manager.broadcast({"type": msg_type, "data": data})
            elif msg_type == "nav_feedback":
                # 导航进度 → 广播前端
                await manager.broadcast({"type": "nav_feedback", "data": data})

            elif msg_type == "waypoint_feedback":
                # 多点导航进度 → 广播前端
                await manager.broadcast({"type": "waypoint_feedback", "data": data})

            elif msg_type == "nav_complete":
                # 导航完成 → 广播前端
                await manager.broadcast({"type": "nav_complete", "data": data})

            elif msg_type == "nav_ready":
                # Nav2 导航栈就绪状态 → 广播前端
                await manager.broadcast({"type": "nav_ready", "data": data})

            elif msg_type == "map_ready":
                # 地图就绪通知
                await manager.broadcast({"type": "map_ready", "data": data})

    except WebSocketDisconnect:
        robot_manager.disconnect(ws)
        await manager.broadcast({"type": "robot_disconnected", "data": {}})


# ══════════════════════════════════════
# MQTT 状态 API
# ══════════════════════════════════════

@app.get("/api/mqtt/status")
def mqtt_status():
    """获取 MQTT 连接状态"""
    return {"ok": True, **get_mqtt_status()}


# ══════════════════════════════════════
# 挂载静态文件 & 地图文件
# ══════════════════════════════════════
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(MAP_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/maps", StaticFiles(directory=MAP_DIR), name="maps")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
