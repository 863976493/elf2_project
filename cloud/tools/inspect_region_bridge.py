#!/usr/bin/env python3
import argparse
import asyncio
import json
import math
import threading
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import String
from strawberry_interfaces.action import InspectRegion

try:
    import websockets
except ImportError as exc:
    raise SystemExit("Missing dependency: python3 -m pip install websockets") from exc


class InspectRegionBridge(Node):
    def __init__(self, loop):
        super().__init__("inspect_region_cloud_bridge")
        self.loop = loop
        self.client = ActionClient(self, InspectRegion, "/inspect_region")
        self.pose_sub = self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self.pose_cb, 10)
        self.voice_pub = self.create_publisher(String, "/cloud_voice_text", 10)
        self.ws = None
        self.busy = False
        self.last_pose = None
        self.last_status_sent = 0.0


    def pose_cb(self, msg):
        pose = msg.pose.pose
        q = pose.orientation
        # yaw from quaternion, map frame coordinates from AMCL.
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        self.last_pose = {
            "x": pose.position.x,
            "y": pose.position.y,
            "theta": yaw,
            "state": "patrolling" if self.busy else "idle",
            "battery": 100,
        }
        now = time.monotonic()
        if now - self.last_status_sent >= 0.5:
            self.last_status_sent = now
            self.loop.call_soon_threadsafe(asyncio.create_task, self.send_status())

    async def send_status(self):
        if self.last_pose is None:
            return
        await self.send({"type": "status", "data": self.last_pose})

    async def send(self, msg):
        if self.ws is None:
            return
        try:
            await self.ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception as exc:
            self.get_logger().warning(f"send websocket message failed: {exc}")

    def feedback_cb(self, region, task_id, feedback_msg):
        feedback = feedback_msg.feedback
        msg = {
            "type": "inspect_feedback",
            "data": {"region": region, "task_id": task_id, "stage": feedback.stage, "detail": feedback.detail},
        }
        self.loop.call_soon_threadsafe(asyncio.create_task, self.send(msg))

    async def wait_future(self, future, timeout=None):
        start = time.monotonic()
        while not future.done():
            if timeout is not None and time.monotonic() - start > timeout:
                raise TimeoutError("ROS future timed out")
            await asyncio.sleep(0.05)
        return future.result()

    def publish_voice_text(self, text):
        text = str(text or "").strip()
        if not text:
            return
        self.voice_pub.publish(String(data=text))
        self.get_logger().info(f"published cloud voice text: {text}")

    async def handle_inspect(self, data):
        region = str(data.get("region", "")).strip().upper()
        task_id = str(data.get("task_id", "")).strip()
        voice_text = str(data.get("voice_text", "")).strip()
        if region not in {"A", "B"}:
            await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": "region must be A or B"}})
            return
        if not task_id:
            await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": "missing task_id"}})
            return
        if self.busy:
            await self.send({"type": "inspect_busy", "data": {"region": region, "task_id": task_id, "success": False, "message": "inspection task is already running"}})
            return

        self.busy = True
        await self.send({"type": "inspect_started", "data": {"region": region, "task_id": task_id}})
        try:
            self.get_logger().info(f"received inspect_region region={region} task_id={task_id}")
            if not self.client.wait_for_server(timeout_sec=10.0):
                await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": "/inspect_region action server not available"}})
                return

            self.publish_voice_text(voice_text)
            goal = InspectRegion.Goal()
            goal.region = region
            goal.task_id = task_id
            goal_future = self.client.send_goal_async(goal, feedback_callback=lambda fb: self.feedback_cb(region, task_id, fb))
            goal_handle = await self.wait_future(goal_future)
            if not goal_handle.accepted:
                await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": "goal rejected"}})
                return

            result_response = await self.wait_future(goal_handle.get_result_async())
            result = result_response.result
            msg_type = "inspect_complete" if result.success else "inspect_failed"
            await self.send({"type": msg_type, "data": {"region": region, "task_id": task_id, "success": bool(result.success), "message": result.message, "result_json": result.result_json}})
        except Exception as exc:
            self.get_logger().exception("inspect task failed")
            await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": str(exc)}})
        finally:
            self.busy = False


async def ws_loop(node, server_url):
    while rclpy.ok():
        try:
            async with websockets.connect(server_url, ping_interval=20, ping_timeout=20) as ws:
                node.ws = ws
                node.get_logger().info(f"connected to cloud websocket: {server_url}")
                await node.send_status()
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") == "inspect_region":
                        asyncio.create_task(node.handle_inspect(msg.get("data", {})))
        except Exception as exc:
            node.ws = None
            node.get_logger().warning(f"cloud websocket disconnected: {exc}; reconnecting in 3s")
            await asyncio.sleep(3.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True, help="ws://<PC_IP>:8000/ws/robot")
    args = parser.parse_args()

    rclpy.init()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    node = InspectRegionBridge(loop)
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    try:
        loop.run_until_complete(ws_loop(node, args.server))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
