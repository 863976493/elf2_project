"""
WebSocket 连接管理器
管理前端广播、视频推流、机器人指令通道
"""
from __future__ import annotations

import json
from fastapi import WebSocket


class ConnectionManager:
    """前端数据广播管理器"""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        print(f"WebSocket 连接 +1，当前 {len(self.active)} 个客户端")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        print(f"WebSocket 断开，当前 {len(self.active)} 个客户端")

    async def broadcast(self, data: dict):
        """向所有连接广播 JSON 消息"""
        message = json.dumps(data, ensure_ascii=False)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


class VideoStreamManager:
    """管理视频推流：一个推流源 → 多个观看者"""

    def __init__(self):
        self.pusher: WebSocket | None = None
        self.viewers: list[WebSocket] = []

    async def set_pusher(self, ws: WebSocket):
        self.pusher = ws
        print(f"视频推流源已连接，当前 {len(self.viewers)} 个观看者")
        await self._notify_viewers({"type": "pusher_status", "live": True})

    async def remove_pusher(self, ws: WebSocket):
        if self.pusher is ws:
            self.pusher = None
            print("视频推流源已断开")
            await self._notify_viewers({"type": "pusher_status", "live": False})

    async def add_viewer(self, ws: WebSocket):
        self.viewers.append(ws)
        print(f"视频观看者 +1，当前 {len(self.viewers)} 个")
        try:
            await ws.send_text(json.dumps({"type": "pusher_status", "live": self.is_live}))
        except Exception:
            pass

    def remove_viewer(self, ws: WebSocket):
        if ws in self.viewers:
            self.viewers.remove(ws)
        print(f"视频观看者断开，当前 {len(self.viewers)} 个")

    async def relay(self, data: bytes):
        """将二进制帧转发给所有 viewers"""
        dead = []
        for ws in self.viewers:
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_viewer(ws)

    async def _notify_viewers(self, msg: dict):
        text = json.dumps(msg)
        dead = []
        for ws in self.viewers:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_viewer(ws)

    @property
    def is_live(self) -> bool:
        return self.pusher is not None

    @property
    def viewer_count(self) -> int:
        return len(self.viewers)


video_manager = VideoStreamManager()


class RobotConnectionManager:
    """机器人 WebSocket 指令通道管理"""

    def __init__(self):
        self.robot_ws: WebSocket | None = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.robot_ws = ws
        print("机器人 WebSocket 已连接")

    def disconnect(self, ws: WebSocket):
        if self.robot_ws is ws:
            self.robot_ws = None
            print("机器人 WebSocket 已断开")

    @property
    def is_connected(self) -> bool:
        return self.robot_ws is not None

    async def send_command(self, msg: dict):
        """向机器人发送 JSON 指令"""
        if self.robot_ws:
            try:
                await self.robot_ws.send_text(json.dumps(msg, ensure_ascii=False))
                return True
            except Exception:
                self.robot_ws = None
                return False
        return False


robot_manager = RobotConnectionManager()
