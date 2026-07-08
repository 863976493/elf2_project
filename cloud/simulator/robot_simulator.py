"""
机器人模拟器 — WebSocket 客户端
模拟真实小车通过 /ws/robot 通道与云端通信

用法: python robot_simulator.py [--server ws://localhost:8000/ws/robot]
"""
import argparse
import asyncio
import json
import random

import websockets


DEFAULT_SERVER = "ws://localhost:8000/ws/robot"

# 初始状态
INITIAL_BATTERY = 100.0
BATTERY_DRAIN_IDLE = 0.002       # 每次心跳耗电
BATTERY_DRAIN_PATROL = 0.05      # 每步巡检耗电
HEARTBEAT_INTERVAL = 3.0         # 空闲心跳间隔(秒)
STEPS_PER_SEGMENT = 5            # 每段路径插值步数
STEP_DELAY = 0.1                 # 每步间隔(秒)
RECONNECT_DELAY = 3.0            # 断线重连延迟(秒)


class RobotSimulator:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.battery = INITIAL_BATTERY
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.state = "idle"
        self._patrol_event = asyncio.Event()
        self._patrol_data = None
        self._nav_task = None  # 当前导航/巡检任务

    async def run(self):
        """主循环：连接 → 收发消息，断线自动重连"""
        while True:
            try:
                async with websockets.connect(self.server_url) as ws:
                    print(f"[模拟器] 已连接 {self.server_url}")
                    # 并发运行心跳和消息接收
                    await asyncio.gather(
                        self._heartbeat_loop(ws),
                        self._receive_loop(ws),
                    )
            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                print(f"[模拟器] 连接断开: {e}，{RECONNECT_DELAY}s 后重连...")
            except Exception as e:
                print(f"[模拟器] 异常: {e}，{RECONNECT_DELAY}s 后重连...")
            await asyncio.sleep(RECONNECT_DELAY)

    async def _send(self, ws, msg: dict):
        """发送 JSON 消息"""
        await ws.send(json.dumps(msg, ensure_ascii=False))

    def _status_payload(self) -> dict:
        return {
            "type": "status",
            "data": {
                "battery": round(self.battery, 2),
                "x": round(self.x, 4),
                "y": round(self.y, 4),
                "theta": round(self.theta, 4),
                "state": self.state,
            },
        }

    async def _heartbeat_loop(self, ws):
        """空闲时每 HEARTBEAT_INTERVAL 秒发一次心跳"""
        while True:
            if self.state == "idle":
                self.battery = max(0, self.battery - BATTERY_DRAIN_IDLE)
                await self._send(ws, self._status_payload())
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _receive_loop(self, ws):
        """接收云端指令"""
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type", "")
            data = msg.get("data", {})

            if msg_type == "navigate_to":
                x = data.get("x", 0)
                y = data.get("y", 0)
                theta = data.get("theta", 0)
                # 取消正在进行的导航
                await self._cancel_current(ws)
                self._nav_task = asyncio.create_task(
                    self._do_navigate(ws, x, y, theta)
                )

            elif msg_type == "start_patrol":
                waypoints = data.get("waypoints", [])
                task_id = data.get("task_id")
                if len(waypoints) >= 2:
                    await self._cancel_current(ws)
                    self._nav_task = asyncio.create_task(
                        self._do_patrol(ws, task_id, waypoints)
                    )

            elif msg_type == "cancel_nav":
                await self._cancel_current(ws)

            elif msg_type == "set_initial_pose":
                self.x = data.get("x", 0)
                self.y = data.get("y", 0)
                self.theta = data.get("theta", 0)
                print(f"[模拟器] 初始位姿设置: ({self.x:.2f}, {self.y:.2f}, θ={self.theta:.4f} rad / {self.theta*180/3.14159:.1f}°)")
                await self._send(ws, self._status_payload())

            else:
                print(f"[模拟器] 未知指令: {msg_type}")

    async def _cancel_current(self, ws):
        """取消正在进行的导航/巡检"""
        if self._nav_task and not self._nav_task.done():
            self._nav_task.cancel()
            try:
                await self._nav_task
            except asyncio.CancelledError:
                pass
            self.state = "idle"
            await self._send(ws, {
                "type": "nav_complete",
                "data": {"result": "canceled"},
            })
            await self._send(ws, self._status_payload())
            print("[模拟器] 导航已取消")
            self._nav_task = None

    async def _do_navigate(self, ws, tx: float, ty: float, theta: float):
        """模拟单点导航：从当前位置插值移动到目标"""
        import math
        dx = tx - self.x
        dy = ty - self.y
        dist = math.sqrt(dx * dx + dy * dy)
        steps = max(int(dist / 0.05), STEPS_PER_SEGMENT)  # 每0.05m一步
        print(f"[模拟器] 导航到 ({tx:.2f}, {ty:.2f})，距离 {dist:.2f}m，{steps} 步")
        self.state = "navigating"
        sx, sy = self.x, self.y

        for s in range(1, steps + 1):
            t = s / steps
            self.x = sx + dx * t
            self.y = sy + dy * t
            self.battery = max(0, self.battery - BATTERY_DRAIN_PATROL)
            remaining = dist * (1 - t)
            # 上报状态
            await self._send(ws, self._status_payload())
            # 上报导航反馈
            await self._send(ws, {
                "type": "nav_feedback",
                "data": {
                    "x": round(self.x, 4),
                    "y": round(self.y, 4),
                    "distance_remaining": round(remaining, 2),
                },
            })
            await asyncio.sleep(STEP_DELAY)

        # 到达
        self.x = tx
        self.y = ty
        self.state = "idle"
        await self._send(ws, {
            "type": "nav_complete",
            "data": {"result": "succeeded"},
        })
        await self._send(ws, self._status_payload())
        print(f"[模拟器] 导航完成，已到达 ({tx:.2f}, {ty:.2f})")

    async def _do_patrol(self, ws, task_id: int, waypoints: list):
        """沿 waypoints 逐段插值移动"""
        print(f"[模拟器] 开始巡检 task#{task_id}，{len(waypoints)} 个路径点")
        self.state = "patrolling"

        for i in range(len(waypoints) - 1):
            ax, ay = waypoints[i]["x"], waypoints[i]["y"]
            bx, by = waypoints[i + 1]["x"], waypoints[i + 1]["y"]
            for s in range(STEPS_PER_SEGMENT + 1):
                t = s / STEPS_PER_SEGMENT
                self.x = ax + (bx - ax) * t
                self.y = ay + (by - ay) * t
                self.battery = max(0, self.battery - BATTERY_DRAIN_PATROL)
                await self._send(ws, self._status_payload())
                await asyncio.sleep(STEP_DELAY)

        # 巡检完成
        self.state = "idle"
        await self._send(ws, {
            "type": "patrol_complete",
            "data": {"task_id": task_id, "result": "ok"},
        })
        await self._send(ws, self._status_payload())
        print(f"[模拟器] 巡检 task#{task_id} 完成")


def main():
    parser = argparse.ArgumentParser(description="机器人模拟器")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="WebSocket 服务器地址")
    args = parser.parse_args()

    print(f"[模拟器] 启动，目标: {args.server}")
    print("[模拟器] Ctrl+C 退出")
    try:
        asyncio.run(RobotSimulator(args.server).run())
    except KeyboardInterrupt:
        print("\n[模拟器] 已退出")


if __name__ == "__main__":
    main()
