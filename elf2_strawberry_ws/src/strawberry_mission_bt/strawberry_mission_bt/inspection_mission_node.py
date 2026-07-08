import json
import math
import time
from pathlib import Path

import rclpy
from geometry_msgs.msg import Twist
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from strawberry_interfaces.action import InspectRegion, TrackStrawberry
from strawberry_interfaces.srv import InferStrawberryImage


class InspectionMissionNode(Node):
    def __init__(self):
        super().__init__("inspection_mission_node")

        self.declare_parameter(
            "regions_config",
            "/root/elf2_strawberry_ws/install/strawberry_mission_bt/share/strawberry_mission_bt/config/regions.yaml",
        )
        self.declare_parameter("nav_action_name", "navigate_to_pose")
        self.declare_parameter("track_action_name", "/track_strawberry")
        self.declare_parameter("infer_service_name", "/infer_strawberry_image")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("action_name", "/inspect_region")

        g = lambda name: self.get_parameter(name).value
        self.regions_config = Path(str(g("regions_config")))
        self.nav_client = ActionClient(self, NavigateToPose, str(g("nav_action_name")))
        self.track_client = ActionClient(self, TrackStrawberry, str(g("track_action_name")))
        self.infer_client = self.create_client(InferStrawberryImage, str(g("infer_service_name")))
        self.cmd_pub = self.create_publisher(Twist, str(g("cmd_vel_topic")), 10)
        self.action_server = ActionServer(
            self,
            InspectRegion,
            str(g("action_name")),
            execute_callback=self._execute_inspect,
            goal_callback=self._handle_goal,
            cancel_callback=self._handle_cancel,
        )

        self.config = self._load_regions_config(self.regions_config)
        self.get_logger().info(f"Inspection mission node ready: {self.regions_config}")

    def _handle_goal(self, goal_request):
        region = str(goal_request.region).upper()
        if region not in self.config["regions"]:
            self.get_logger().warn(f"Rejecting mission: unknown region {goal_request.region}")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _handle_cancel(self, _goal_handle):
        self.get_logger().info("InspectRegion cancel requested.")
        return CancelResponse.ACCEPT

    def _execute_inspect(self, goal_handle):
        request = goal_handle.request
        region_name = str(request.region).upper()
        task_id = str(request.task_id or f"cli_{region_name}")
        result = InspectRegion.Result()

        def fail(message):
            self._stop_robot()
            result.success = False
            result.message = message
            result.result_json = "{}"
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
            else:
                goal_handle.abort()
            return result

        try:
            region = self.config["regions"][region_name]
            tracking_cfg = self.config["tracking"]
            goal_pose = self._make_nav_goal(region)

            self._feedback(goal_handle, "navigate", f"navigating to region {region_name}")
            nav_ok, nav_msg = self._navigate(goal_handle, goal_pose)
            if not nav_ok:
                return fail(nav_msg)

            self._stop_robot()
            self._feedback(goal_handle, "track", "tracking strawberry")
            track_ok, track_msg, image_path, final_distance = self._track(
                goal_handle,
                task_id,
                region_name,
                tracking_cfg,
            )
            if not track_ok:
                return fail(track_msg)

            self._feedback(goal_handle, "infer", f"inferencing image {image_path}")
            infer_ok, infer_msg, infer_json = self._infer(goal_handle, task_id, region_name, image_path)
            if not infer_ok:
                return fail(infer_msg)

            final_json = self._build_final_result(
                task_id,
                region_name,
                region,
                image_path,
                final_distance,
                infer_json,
            )
            saved_path = self._save_result(image_path, final_json)
            self._feedback(goal_handle, "save", f"saved result {saved_path}")

            result.success = True
            result.message = "ok"
            result.result_json = json.dumps(final_json, ensure_ascii=False)
            goal_handle.succeed()
            return result
        except Exception as exc:
            return fail(f"mission failed: {exc}")

    def _feedback(self, goal_handle, stage, detail):
        feedback = InspectRegion.Feedback()
        feedback.stage = str(stage)
        feedback.detail = str(detail)
        goal_handle.publish_feedback(feedback)
        self.get_logger().info(f"[{stage}] {detail}")

    def _navigate(self, goal_handle, nav_goal):
        if not self.nav_client.wait_for_server(timeout_sec=10.0):
            return False, "Nav2 NavigateToPose action server not available"

        send_future = self.nav_client.send_goal_async(nav_goal)
        self._spin_until_done(send_future)
        nav_goal_handle = send_future.result()
        if nav_goal_handle is None or not nav_goal_handle.accepted:
            return False, "Nav2 goal rejected"

        result_future = nav_goal_handle.get_result_async()
        while rclpy.ok() and not result_future.done():
            if goal_handle.is_cancel_requested:
                nav_goal_handle.cancel_goal_async()
                return False, "mission canceled during navigation"
            time.sleep(0.2)
        nav_result = result_future.result()
        if nav_result is None:
            return False, "Nav2 returned no result"
        if nav_result.status != 4:
            return False, f"Nav2 failed with status {nav_result.status}"
        return True, "navigation succeeded"

    def _track(self, goal_handle, task_id, region_name, tracking_cfg):
        if not self.track_client.wait_for_server(timeout_sec=10.0):
            return False, "TrackStrawberry action server not available", "", -1.0

        track_goal = TrackStrawberry.Goal()
        track_goal.target_distance = float(tracking_cfg.get("target_distance", 0.35))
        track_goal.timeout_sec = float(tracking_cfg.get("timeout_sec", 60.0))
        track_goal.max_attempts = int(tracking_cfg.get("max_attempts", 1))
        track_goal.task_id = task_id
        track_goal.region = region_name

        send_future = self.track_client.send_goal_async(track_goal)
        self._spin_until_done(send_future)
        track_goal_handle = send_future.result()
        if track_goal_handle is None or not track_goal_handle.accepted:
            return False, "TrackStrawberry goal rejected", "", -1.0

        result_future = track_goal_handle.get_result_async()
        while rclpy.ok() and not result_future.done():
            if goal_handle.is_cancel_requested:
                track_goal_handle.cancel_goal_async()
                return False, "mission canceled during tracking", "", -1.0
            time.sleep(0.2)
        track_result_msg = result_future.result()
        if track_result_msg is None:
            return False, "TrackStrawberry returned no result", "", -1.0
        track_result = track_result_msg.result
        return (
            bool(track_result.success),
            str(track_result.message),
            str(track_result.image_path),
            float(track_result.final_distance),
        )

    def _infer(self, goal_handle, task_id, region_name, image_path):
        if not self.infer_client.wait_for_service(timeout_sec=10.0):
            return False, "InferStrawberryImage service not available", "{}"

        req = InferStrawberryImage.Request()
        req.image_path = image_path
        req.task_id = task_id
        req.region = region_name
        future = self.infer_client.call_async(req)
        while rclpy.ok() and not future.done():
            if goal_handle.is_cancel_requested:
                return False, "mission canceled during inference", "{}"
            time.sleep(0.2)
        resp = future.result()
        if resp is None:
            return False, "InferStrawberryImage returned no response", "{}"
        return bool(resp.success), str(resp.message), str(resp.result_json)

    def _build_final_result(self, task_id, region_name, region, image_path, final_distance, infer_json):
        try:
            infer_result = json.loads(infer_json) if infer_json else {}
        except json.JSONDecodeError:
            infer_result = {"raw_inference_result": infer_json}
        return {
            "task_id": task_id,
            "region": region_name,
            "time_unix": time.time(),
            "nav_goal": {
                "frame_id": region.get("frame_id", "map"),
                "x": float(region["x"]),
                "y": float(region["y"]),
                "yaw": float(region["yaw"]),
            },
            "image_path": image_path,
            "tracking": {
                "target_distance": float(self.config["tracking"].get("target_distance", 0.35)),
                "final_distance": float(final_distance),
                "success": True,
            },
            "disease": infer_result.get("disease", {}),
            "maturity": infer_result.get("maturity", {}),
            "inference": infer_result,
            "upload": {
                "enabled": False,
                "status": "reserved",
            },
        }

    def _save_result(self, image_path, result):
        img_path = Path(image_path)
        out_dir = img_path.parent if image_path else Path(self.config["results"]["root_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        result_path = out_dir / "result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(result_path)

    def _make_nav_goal(self, region):
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = str(region.get("frame_id", "map"))
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(region["x"])
        goal.pose.pose.position.y = float(region["y"])
        yaw = float(region["yaw"])
        goal.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal.pose.pose.orientation.w = math.cos(yaw / 2.0)
        return goal

    def _stop_robot(self):
        msg = Twist()
        for _ in range(10):
            self.cmd_pub.publish(msg)
            time.sleep(0.02)

    def _spin_until_done(self, future):
        while rclpy.ok() and not future.done():
            time.sleep(0.05)

    def _load_regions_config(self, path):
        text = path.read_text(encoding="utf-8")
        try:
            import yaml

            data = yaml.safe_load(text)
        except Exception:
            data = self._parse_simple_regions_yaml(text)
        if not data or "regions" not in data:
            raise RuntimeError(f"invalid regions config: {path}")
        data.setdefault("tracking", {})
        data.setdefault("results", {"root_dir": "/root/strawberry_inspection/results"})
        return data

    def _parse_simple_regions_yaml(self, text):
        data = {"regions": {}, "tracking": {}, "results": {}}
        section = None
        current_region = None
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            if not raw.startswith(" ") and line.endswith(":"):
                section = line[:-1].strip()
                current_region = None
                continue
            stripped = line.strip()
            if section == "regions" and raw.startswith("  ") and stripped.endswith(":"):
                current_region = stripped[:-1]
                data["regions"][current_region] = {}
                continue
            if ":" not in stripped:
                continue
            key, value = [part.strip() for part in stripped.split(":", 1)]
            value = value.strip("'\"")
            parsed = self._parse_scalar(value)
            if section == "regions" and current_region:
                data["regions"][current_region][key] = parsed
            elif section in ("tracking", "results"):
                data[section][key] = parsed
        return data

    def _parse_scalar(self, value):
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value

    def destroy_node(self):
        if hasattr(self, "action_server"):
            self.action_server.destroy()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = InspectionMissionNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node._stop_robot()
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
