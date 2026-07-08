#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import math
import os
import threading
import time

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from strawberry_interfaces.action import InspectRegion

try:
    import websockets
except ImportError as exc:
    raise SystemExit("Missing dependency: python3 -m pip install websockets") from exc


class CloudRobotBridge(Node):
    IMAGE_RESULT_KEYS = {
        "image",
        "image_path",
        "image_url",
        "draw_image",
        "draw_image_path",
        "result_image",
        "result_image_path",
        "yolo_image",
        "yolo_image_path",
    }
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
    MAX_UPLOAD_IMAGE_BYTES = 5 * 1024 * 1024

    def __init__(self, loop):
        super().__init__("cloud_robot_bridge")
        self.loop = loop
        self.inspect_client = ActionClient(self, InspectRegion, "/inspect_region")
        self.nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self.initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self.pose_sub = self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self.pose_cb, 10)

        self.ws = None
        self.inspect_busy = False
        self.nav_active = False
        self.nav_goal_handle = None
        self.nav_mode = ""
        self.nav_task_id = ""
        self.nav_total = 0
        self.nav_current = 0
        self.cancel_requested = False
        self.last_pose = None
        self.last_status_sent = 0.0
        self.last_nav_ready_sent = 0.0
        self.nav_ready_timer = self.create_timer(2.0, self.nav_ready_timer_cb)

    @staticmethod
    def yaw_to_quat(theta):
        half = float(theta) * 0.5
        return 0.0, 0.0, math.sin(half), math.cos(half)

    @staticmethod
    def status_text(status):
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

    def pose_cb(self, msg):
        pose = msg.pose.pose
        q = pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        state = "patrolling" if self.inspect_busy else ("navigating" if self.nav_active else "idle")
        self.last_pose = {
            "x": pose.position.x,
            "y": pose.position.y,
            "theta": yaw,
            "state": state,
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

    async def send_nav_ready(self):
        ready = self.nav_client.server_is_ready()
        await self.send({"type": "nav_ready", "data": {"ready": bool(ready)}})

    def nav_ready_timer_cb(self):
        if self.ws is None:
            return
        now = time.monotonic()
        if now - self.last_nav_ready_sent < 2.0:
            return
        self.last_nav_ready_sent = now
        self.loop.call_soon_threadsafe(asyncio.create_task, self.send_nav_ready())

    async def send(self, msg):
        if self.ws is None:
            return
        try:
            await self.ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception as exc:
            self.get_logger().warning(f"send websocket message failed: {exc}")

    async def wait_future(self, future, timeout=None):
        start = time.monotonic()
        while not future.done():
            if timeout is not None and time.monotonic() - start > timeout:
                raise TimeoutError("ROS future timed out")
            await asyncio.sleep(0.05)
        return future.result()

    def build_nav_goal(self, x, y, theta):
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        qx, qy, qz, qw = self.yaw_to_quat(theta)
        goal.pose.pose.orientation.x = qx
        goal.pose.pose.orientation.y = qy
        goal.pose.pose.orientation.z = qz
        goal.pose.pose.orientation.w = qw
        return goal

    def nav_feedback_cb(self, feedback_msg):
        feedback = feedback_msg.feedback
        pose = feedback.current_pose.pose.position
        data = {
            "stage": "moving",
            "distanceRemaining": float(feedback.distance_remaining),
            "estimatedTimeRemaining": (
                float(feedback.estimated_time_remaining.sec)
                + float(feedback.estimated_time_remaining.nanosec) / 1e9
            ),
            "x": float(pose.x),
            "y": float(pose.y),
        }
        self.loop.call_soon_threadsafe(asyncio.create_task, self.send({"type": "nav_feedback", "data": data}))
        if self.nav_mode == "waypoints":
            wp_data = {
                "task_id": self.nav_task_id,
                "current": self.nav_current,
                "total": self.nav_total,
                "stage": "moving",
                "distanceRemaining": data["distanceRemaining"],
            }
            self.loop.call_soon_threadsafe(asyncio.create_task, self.send({"type": "waypoint_feedback", "data": wp_data}))

    async def navigate_once(self, waypoint, feedback_callback=None):
        if not self.nav_client.wait_for_server(timeout_sec=10.0):
            return "failed", "/navigate_to_pose action server not available"
        goal = self.build_nav_goal(waypoint["x"], waypoint["y"], waypoint.get("theta", 0.0))
        goal_future = self.nav_client.send_goal_async(goal, feedback_callback=feedback_callback or self.nav_feedback_cb)
        goal_handle = await self.wait_future(goal_future, timeout=10.0)
        if not goal_handle.accepted:
            return "rejected", "navigate_to_pose goal rejected"

        self.nav_goal_handle = goal_handle
        result_response = await self.wait_future(goal_handle.get_result_async())
        result = self.status_text(result_response.status)
        return result, ""

    async def handle_navigate_to(self, data):
        if self.inspect_busy:
            await self.send({"type": "nav_complete", "data": {"result": "rejected", "message": "inspection task is running"}})
            return
        if self.nav_active:
            await self.handle_cancel_nav({"reason": "replace_goal"})

        try:
            waypoint = {
                "x": float(data.get("x")),
                "y": float(data.get("y")),
                "theta": float(data.get("theta", data.get("yaw", 0.0))),
            }
        except (TypeError, ValueError):
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": "invalid navigate_to payload"}})
            return

        self.nav_active = True
        self.nav_mode = "single"
        self.cancel_requested = False
        await self.send({"type": "nav_feedback", "data": {"stage": "accepted_by_bridge", "distanceRemaining": 0.0, **waypoint}})
        try:
            result, message = await self.navigate_once(waypoint)
            data = {"result": result}
            if message:
                data["message"] = message
            await self.send({"type": "nav_complete", "data": data})
        except Exception as exc:
            self.get_logger().exception("single navigation failed")
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": str(exc)}})
        finally:
            self.nav_goal_handle = None
            self.nav_active = False
            self.nav_mode = ""

    async def handle_waypoint_nav(self, data):
        if self.inspect_busy:
            await self.send({"type": "nav_complete", "data": {"result": "rejected", "message": "inspection task is running"}})
            return
        if self.nav_active:
            await self.handle_cancel_nav({"reason": "replace_goal"})

        raw_waypoints = data.get("waypoints") or []
        task_id = str(data.get("task_id") or "")
        waypoints = []
        try:
            for item in raw_waypoints:
                waypoints.append({
                    "x": float(item.get("x")),
                    "y": float(item.get("y")),
                    "theta": float(item.get("theta", item.get("yaw", 0.0))),
                })
        except (AttributeError, TypeError, ValueError):
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": "invalid waypoint payload"}})
            return
        if not waypoints:
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": "empty waypoints"}})
            return

        self.nav_active = True
        self.nav_mode = "waypoints"
        self.nav_task_id = task_id
        self.nav_total = len(waypoints)
        self.cancel_requested = False
        final_result = "succeeded"
        final_message = ""

        try:
            for index, waypoint in enumerate(waypoints, start=1):
                if self.cancel_requested:
                    final_result = "canceled"
                    final_message = "manual cancel"
                    break
                self.nav_current = index
                await self.send({
                    "type": "waypoint_feedback",
                    "data": {
                        "task_id": task_id,
                        "current": index - 1,
                        "total": self.nav_total,
                        "stage": "accepted_by_bridge",
                        "target": waypoint,
                    },
                })
                await self.send({"type": "nav_feedback", "data": {"stage": "waypoint_goal", "distanceRemaining": 0.0, **waypoint}})
                result, message = await self.navigate_once(waypoint)
                if result != "succeeded":
                    final_result = result
                    final_message = message or f"waypoint {index} {result}"
                    break
                await self.send({
                    "type": "waypoint_feedback",
                    "data": {
                        "task_id": task_id,
                        "current": index,
                        "total": self.nav_total,
                        "stage": "reached",
                        "target": waypoint,
                    },
                })
            await self.send({"type": "nav_complete", "data": {"result": final_result, "message": final_message, "task_id": task_id}})
            await self.send({"type": "patrol_complete", "data": {"task_id": task_id, "result": final_result}})
        except Exception as exc:
            self.get_logger().exception("waypoint navigation failed")
            await self.send({"type": "nav_complete", "data": {"result": "failed", "message": str(exc), "task_id": task_id}})
            await self.send({"type": "patrol_complete", "data": {"task_id": task_id, "result": "failed"}})
        finally:
            self.nav_goal_handle = None
            self.nav_active = False
            self.nav_mode = ""
            self.nav_task_id = ""
            self.nav_total = 0
            self.nav_current = 0
            self.cancel_requested = False

    async def handle_cancel_nav(self, data=None):
        self.cancel_requested = True
        if self.nav_goal_handle is None:
            if self.nav_active:
                return
            await self.send({"type": "nav_complete", "data": {"result": "idle", "message": "no active navigation"}})
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

    def inspect_feedback_cb(self, region, task_id, feedback_msg):
        feedback = feedback_msg.feedback
        msg = {
            "type": "inspect_feedback",
            "data": {"region": region, "task_id": task_id, "stage": feedback.stage, "detail": feedback.detail},
        }
        self.loop.call_soon_threadsafe(asyncio.create_task, self.send(msg))

    def collect_result_image_paths(self, value):
        paths = []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return paths
        if isinstance(value, dict):
            for key, item in value.items():
                if key in self.IMAGE_RESULT_KEYS and isinstance(item, str):
                    paths.append(item)
                paths.extend(self.collect_result_image_paths(item))
        elif isinstance(value, list):
            for item in value:
                paths.extend(self.collect_result_image_paths(item))
        return paths

    def load_local_result_images(self, result_json):
        images = []
        seen = set()
        for path in self.collect_result_image_paths(result_json):
            if not path or path in seen:
                continue
            seen.add(path)
            if path.startswith(("http://", "https://", "data:image", "/uploads/")):
                continue
            if not os.path.isabs(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in self.IMAGE_EXTS:
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                self.get_logger().warning(f"inspect result image path not found: {path}")
                continue
            if size <= 0 or size > self.MAX_UPLOAD_IMAGE_BYTES:
                self.get_logger().warning(f"skip inspect result image path size={size}: {path}")
                continue
            try:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
            except OSError as exc:
                self.get_logger().warning(f"read inspect result image failed path={path}: {exc}")
                continue
            images.append({"filename": os.path.basename(path), "data": data})
        return images

    async def handle_inspect(self, data):
        region = str(data.get("region", "")).strip().upper()
        task_id = str(data.get("task_id", "")).strip()
        if region not in {"A", "B"}:
            await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": "region must be A or B"}})
            return
        if not task_id:
            await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": "missing task_id"}})
            return
        if self.inspect_busy or self.nav_active:
            await self.send({"type": "inspect_busy", "data": {"region": region, "task_id": task_id, "success": False, "message": "robot is busy"}})
            return

        self.inspect_busy = True
        await self.send({"type": "inspect_started", "data": {"region": region, "task_id": task_id}})
        try:
            self.get_logger().info(f"received inspect_region region={region} task_id={task_id}")
            if not self.inspect_client.wait_for_server(timeout_sec=10.0):
                await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": "/inspect_region action server not available"}})
                return

            goal = InspectRegion.Goal()
            goal.region = region
            goal.task_id = task_id
            goal_future = self.inspect_client.send_goal_async(
                goal,
                feedback_callback=lambda fb: self.inspect_feedback_cb(region, task_id, fb),
            )
            goal_handle = await self.wait_future(goal_future)
            if not goal_handle.accepted:
                await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": "goal rejected"}})
                return

            result_response = await self.wait_future(goal_handle.get_result_async())
            result = result_response.result
            msg_type = "inspect_complete" if result.success else "inspect_failed"
            payload = {
                "region": region,
                "task_id": task_id,
                "success": bool(result.success),
                "message": result.message,
                "result_json": result.result_json,
            }
            images = self.load_local_result_images(result.result_json)
            if images:
                payload["images"] = images
            await self.send({
                "type": msg_type,
                "data": payload,
            })
        except Exception as exc:
            self.get_logger().exception("inspect task failed")
            await self.send({"type": "inspect_failed", "data": {"region": region, "task_id": task_id, "success": False, "message": str(exc)}})
        finally:
            self.inspect_busy = False


async def ws_loop(node, server_url):
    while rclpy.ok():
        try:
            async with websockets.connect(server_url, ping_interval=20, ping_timeout=20) as ws:
                node.ws = ws
                node.get_logger().info(f"connected to cloud websocket: {server_url}")
                await node.send_status()
                await node.send_nav_ready()
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
                    elif msg_type == "start_waypoint_nav":
                        asyncio.create_task(node.handle_waypoint_nav(data))
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
    node = CloudRobotBridge(loop)
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()
    try:
        loop.run_until_complete(ws_loop(node, args.server))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
