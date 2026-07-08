#!/usr/bin/env python3
import argparse
import asyncio
import json
import math
import threading
import time

import rclpy
from rclpy.action import ActionClient
from action_msgs.msg import GoalStatus
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
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
        self.nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self.initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self.pose_sub = self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self.pose_cb, 10)
        self.ws = None
        self.busy = False
        self.nav_goal_handle = None
        self.nav_active = False
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
    @staticmethod
    def yaw_to_quat(theta):
        half = float(theta) * 0.5
        return 0.0, 0.0, math.sin(half), math.cos(half)

    @staticmethod
    def nav_status_text(status):
        names = {
            GoalStatus.STATUS_UNKNOWN: "unknown",
            GoalStatus.STATUS_ACCEPTED: "accepted",
            GoalStatus.STATUS_EXECUTING: "executing",
            GoalStatus.STATUS_CANCELING: "canceling",
            GoalStatus.STATUS_SUCCEEDED: "succeeded",
            GoalStatus.STATUS_CANCELED: "canceled",
            GoalStatus.STATUS_ABORTED: "aborted",
        }
        return names.get(status, str(status))

    async def handle_set_pose(self, data):
        try:
            x = float(data.get("x", 0.0))
            y = float(data.get("y", 0.0))
            theta = float(data.get("theta", data.get("yaw", 0.0)))
        except (TypeError, ValueError):
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": "invalid set_pose payload"}})
            return

        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = x
        msg.pose.pose.position.y = y
        qx, qy, qz, qw = self.yaw_to_quat(theta)
        msg.pose.pose.orientation.x = qx
        msg.pose.pose.orientation.y = qy
        msg.pose.pose.orientation.z = qz
        msg.pose.pose.orientation.w = qw
        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = 0.0685
        self.initial_pose_pub.publish(msg)
        await self.send({"type": "nav_feedback", "data": {"stage": "set_pose", "x": x, "y": y, "theta": theta}})

    async def handle_navigate_to(self, data):
        if self.busy:
            await self.send({"type": "nav_complete", "data": {"result": "rejected", "message": "inspection task is running; manual navigation is blocked"}})
            return
        if self.nav_active:
            await self.handle_cancel_nav({"reason": "replace_goal"})

        try:
            x = float(data.get("x"))
            y = float(data.get("y"))
            theta = float(data.get("theta", data.get("yaw", 0.0)))
        except (TypeError, ValueError):
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": "invalid navigate_to payload"}})
            return

        await self.send({"type": "nav_feedback", "data": {"stage": "accepted_by_bridge", "x": x, "y": y, "theta": theta}})
        if not self.nav_client.wait_for_server(timeout_sec=10.0):
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": "/navigate_to_pose action server not available"}})
            return

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        qx, qy, qz, qw = self.yaw_to_quat(theta)
        goal.pose.pose.orientation.x = qx
        goal.pose.pose.orientation.y = qy
        goal.pose.pose.orientation.z = qz
        goal.pose.pose.orientation.w = qw

        self.nav_active = True
        try:
            goal_future = self.nav_client.send_goal_async(goal, feedback_callback=self.nav_feedback_cb)
            goal_handle = await self.wait_future(goal_future, timeout=10.0)
            if not goal_handle.accepted:
                self.nav_active = False
                await self.send({"type": "nav_complete", "data": {"result": "rejected", "message": "navigate_to_pose goal rejected"}})
                return

            self.nav_goal_handle = goal_handle
            await self.send({"type": "nav_feedback", "data": {"stage": "goal_accepted", "x": x, "y": y, "theta": theta}})
            result_response = await self.wait_future(goal_handle.get_result_async())
            result = self.nav_status_text(result_response.status)
            await self.send({"type": "nav_complete", "data": {"result": result, "status": int(result_response.status)}})
        except Exception as exc:
            self.get_logger().exception("manual navigation failed")
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": str(exc)}})
        finally:
            self.nav_goal_handle = None
            self.nav_active = False

    def nav_feedback_cb(self, feedback_msg):
        feedback = feedback_msg.feedback
        pose = feedback.current_pose.pose.position
        msg = {
            "type": "nav_feedback",
            "data": {
                "stage": "moving",
                "distanceRemaining": float(feedback.distance_remaining),
                "estimatedTimeRemaining": float(feedback.estimated_time_remaining.sec) + float(feedback.estimated_time_remaining.nanosec) / 1e9,
                "x": float(pose.x),
                "y": float(pose.y),
            },
        }
        self.loop.call_soon_threadsafe(asyncio.create_task, self.send(msg))

    async def handle_cancel_nav(self, data=None):
        if self.nav_goal_handle is None:
            await self.send({"type": "nav_complete", "data": {"result": "idle", "message": "no active manual navigation"}})
            return
        try:
            cancel_future = self.nav_goal_handle.cancel_goal_async()
            await self.wait_future(cancel_future, timeout=5.0)
            await self.send({"type": "nav_complete", "data": {"result": "canceled", "message": (data or {}).get("reason", "manual cancel")}})
        except Exception as exc:
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": str(exc)}})
        finally:
            self.nav_goal_handle = None
            self.nav_active = False

    async def handle_inspect(self, data):
        region = str(data.get("region", "")).strip().upper()
        task_id = str(data.get("task_id", "")).strip()
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
                    msg_type = msg.get("type")
                    data = msg.get("data", {})
                    if msg_type == "inspect_region":
                        asyncio.create_task(node.handle_inspect(data))
                    elif msg_type == "navigate_to":
                        asyncio.create_task(node.handle_navigate_to(data))
                    elif msg_type == "cancel_nav":
                        asyncio.create_task(node.handle_cancel_nav(data))
                    elif msg_type in {"set_initial_pose", "set_pose"}:
                        asyncio.create_task(node.handle_set_pose(data))
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
