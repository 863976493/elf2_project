import sys
import time
import threading
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.executors import MultiThreadedExecutor
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from arm_msgs.msg import ArmJoints
from strawberry_interfaces.action import TrackStrawberry

from strawberry_tracker.pid import SimplePID


_SENSOR_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    durability=DurabilityPolicy.VOLATILE,
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
)

STATE_IDLE = "IDLE"
STATE_SEARCH = "SEARCH"
STATE_APPROACH = "APPROACH"
STATE_SETTLE = "SETTLE"

CLASS_COLORS = {
    0: (0, 255, 0),
    1: (0, 255, 255),
    2: (0, 165, 255),
}

_stream_lock = threading.Lock()
_stream_event = threading.Event()
_stream_jpg = None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b'<html><body style="margin:0;background:#000">'
                b'<img src="/stream" style="width:100%"></body></html>'
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=--f")
        self.end_headers()
        try:
            while True:
                _stream_event.wait(timeout=2.0)
                _stream_event.clear()
                with _stream_lock:
                    jpg = _stream_jpg
                if jpg is None:
                    continue
                header = (
                    f"--f\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpg)}\r\n\r\n"
                )
                self.wfile.write(header.encode() + jpg + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


class StrawberryTrackerNode(Node):
    def __init__(self):
        super().__init__("strawberry_tracker_node")

        p = self.declare_parameter
        p("rknn_scripts_dir", "/root/deploy_elf2_maturity/scripts")
        p("model_path", "/root/deploy_elf2_maturity/model/best_fp16.rknn")
        p("class_names_path", "/root/deploy_elf2_maturity/scripts/class_names.json")
        p("confidence_threshold", 0.25)
        p("nms_iou", 0.45)
        p("score_sum_factor", 0.5)
        p("rknn_core", -1)
        p("rgb_topic", "/camera/color/image_raw")
        p("depth_topic", "/camera/depth/image_raw")
        p("cmd_vel_topic", "/cmd_vel")
        p("max_depth_age", 0.5)
        p("angular_kp", 0.005)
        p("angular_ki", 0.0)
        p("angular_kd", 0.003)
        p("angular_slow_kp", 0.0026)
        p("angular_fast_kp", 0.0044)
        p("angular_far_error", 120.0)
        p("angular_slew_rate", 1.2)
        p("linear_kp", 0.5)
        p("linear_ki", 0.0)
        p("linear_kd", 0.1)
        p("target_x_pixel", 320)
        p("target_y_pixel", 240)
        p("target_distance", 0.35)
        p("angular_deadband", 8)
        p("distance_deadband", 0.05)
        p("max_angular_speed", 0.6)
        p("max_linear_speed", 0.20)
        p("search_angular_speed", 0.25)
        p("no_detection_timeout", 1.5)
        p("depth_roi_size", 5)
        p("ema_alpha", 0.35)
        p("settle_time", 0.8)
        p("web_stream_port", 8080)
        p("stream_every_n", 1)
        p("draw_debug_overlay", False)
        p("result_root_dir", "/root/strawberry_inspection/results")
        p("action_name", "/track_strawberry")
        p("cluster_min_conf", 0.20)
        p("cluster_min_box_area_ratio", 0.001)
        p("cluster_merge_dist_scale", 0.95)
        p("cluster_expand_x", 0.45)
        p("cluster_expand_y", 0.45)
        p("cluster_secondary_dist_scale", 1.10)
        p("min_cluster_members", 2)
        p("clahe_enabled", False)
        p("clahe_clip_limit", 2.0)
        p("clahe_tile_size", 8)
        p("arm_enabled", False)
        p("arm_topic", "/arm6_joints")
        p("arm_ready_joints", [90, 150, 12, 20, 90, 0])
        p("arm_pitch_gain", 0.02)
        p("arm_pitch_min", 0)
        p("arm_pitch_max", 40)
        p("arm_move_time", 200)
        p("arm_vertical_deadband", 15.0)
        p("arm_publish_period", 0.15)

        g = lambda name: self.get_parameter(name).value
        self.conf = float(g("confidence_threshold"))
        self.nms_iou = float(g("nms_iou"))
        self.score_sum_factor = float(g("score_sum_factor"))
        self.tgt_x = int(g("target_x_pixel"))
        self.tgt_y = int(g("target_y_pixel"))
        self.tgt_dist = float(g("target_distance"))
        self.ang_db = float(g("angular_deadband"))
        self.dist_db = float(g("distance_deadband"))
        self.max_ang = float(g("max_angular_speed"))
        self.max_lin = float(g("max_linear_speed"))
        self.search_w = float(g("search_angular_speed"))
        self.ang_slow_kp = float(g("angular_slow_kp"))
        self.ang_fast_kp = float(g("angular_fast_kp"))
        self.ang_far_error = float(g("angular_far_error"))
        self.ang_slew_rate = float(g("angular_slew_rate"))
        self.timeout = float(g("no_detection_timeout"))
        self.roi_r = max(0, int(g("depth_roi_size")) // 2)
        self.ema_alpha = float(g("ema_alpha"))
        self.settle_time = float(g("settle_time"))
        self.min_cluster_members = int(g("min_cluster_members"))
        self.cluster_min_conf = float(g("cluster_min_conf"))
        self.cluster_min_box_area_ratio = float(g("cluster_min_box_area_ratio"))
        self.cluster_merge_dist_scale = float(g("cluster_merge_dist_scale"))
        self.cluster_expand_x = float(g("cluster_expand_x"))
        self.cluster_expand_y = float(g("cluster_expand_y"))
        self.cluster_secondary_dist_scale = float(g("cluster_secondary_dist_scale"))
        self.max_depth_age = float(g("max_depth_age"))
        self.stream_every_n = max(1, int(g("stream_every_n")))
        self.draw_debug_overlay = bool(g("draw_debug_overlay"))
        self.result_root_dir = Path(str(g("result_root_dir")))
        self.clahe_enabled = bool(g("clahe_enabled"))
        self.arm_on = bool(g("arm_enabled"))
        self.arm_ready = [int(v) for v in list(g("arm_ready_joints"))]
        self.arm_gain = float(g("arm_pitch_gain"))
        self.arm_j4_min = int(g("arm_pitch_min"))
        self.arm_j4_max = int(g("arm_pitch_max"))
        self.arm_time = int(g("arm_move_time"))
        self.arm_vertical_deadband = float(g("arm_vertical_deadband"))
        self.arm_publish_period = float(g("arm_publish_period"))
        self.arm_j = list(self.arm_ready)
        self._last_arm_pub_t = 0.0
        tile_size = int(g("clahe_tile_size"))
        self._clahe = cv2.createCLAHE(
            clipLimit=float(g("clahe_clip_limit")),
            tileGridSize=(tile_size, tile_size),
        )

        self._load_rknn_runtime(
            Path(g("rknn_scripts_dir")),
            str(g("model_path")),
            Path(g("class_names_path")),
            int(g("rknn_core")),
        )

        self.pid_ang = SimplePID(
            float(g("angular_kp")),
            float(g("angular_ki")),
            float(g("angular_kd")),
            -self.max_ang,
            self.max_ang,
        )
        self.pid_lin = SimplePID(
            float(g("linear_kp")),
            float(g("linear_ki")),
            float(g("linear_kd")),
            -self.max_lin,
            self.max_lin,
        )

        self.cmd_pub = self.create_publisher(Twist, str(g("cmd_vel_topic")), 10)
        self.arm_pub = (
            self.create_publisher(ArmJoints, str(g("arm_topic")), 10)
            if self.arm_on
            else None
        )
        self.rgb_sub = self.create_subscription(
            Image, str(g("rgb_topic")), self._rgb_cb, _SENSOR_QOS
        )
        self.depth_sub = self.create_subscription(
            Image, str(g("depth_topic")), self._depth_cb, _SENSOR_QOS
        )

        self.twist = Twist()
        self._depth_lock = threading.Lock()
        self._latest_depth = None
        self._latest_depth_stamp = None
        self._latest_raw_lock = threading.Lock()
        self._latest_raw_img = None
        self.last_det = self.get_clock().now()
        self._smooth_cx = None
        self._smooth_cy = None
        self._smooth_d = None
        self._state = STATE_IDLE
        self._settle_enter = None
        self._track_lock = threading.Lock()
        self._track_active = False
        self._track_result = None
        self._track_goal_started = None
        self._track_timeout_sec = 60.0
        self._track_task_id = ""
        self._track_region = ""
        self._track_detection_count = 0
        self._track_distance = -1.0
        self._prev_ang_err = 0.0
        self._last_az = 0.0
        self._last_az_t = time.perf_counter()
        self._fc = 0
        self._fps_count = 0
        self._fps_t = time.time()
        self._fps = 0.0
        self._infer_fps_count = 0
        self._infer_fps_t = time.time()
        self._infer_fps = 0.0
        self._pre_ms = 0.0
        self._infer_ms = 0.0

        self.action_server = ActionServer(
            self,
            TrackStrawberry,
            str(g("action_name")),
            execute_callback=self._execute_track,
            goal_callback=self._handle_track_goal,
            cancel_callback=self._handle_track_cancel,
        )
        self.create_timer(0.1, self._publish_active_cmd)
        srv = HTTPServer(("0.0.0.0", int(g("web_stream_port"))), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.get_logger().info(f'Stream: http://<IP>:{g("web_stream_port")}/')
        self.get_logger().info("ELF2 RKNN strawberry tracker started in IDLE mode.")

    def _publish_active_cmd(self):
        with self._track_lock:
            active = self._track_active
            twist = self.twist
        if active:
            self.cmd_pub.publish(twist)

    def _handle_track_goal(self, goal_request):
        with self._track_lock:
            if self._track_active:
                self.get_logger().warn("Rejecting TrackStrawberry goal: tracker is busy.")
                return GoalResponse.REJECT
        if goal_request.target_distance <= 0.0:
            self.get_logger().warn("Rejecting TrackStrawberry goal: target_distance must be positive.")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _handle_track_cancel(self, _goal_handle):
        self.get_logger().info("TrackStrawberry cancel requested.")
        return CancelResponse.ACCEPT

    def _execute_track(self, goal_handle):
        goal = goal_handle.request
        self._start_tracking_goal(goal)
        feedback = TrackStrawberry.Feedback()

        while rclpy.ok():
            if goal_handle.is_cancel_requested:
                self._finish_tracking(False, "canceled", canceled=True)
                result = TrackStrawberry.Result()
                result.success = False
                result.message = "canceled"
                result.image_path = ""
                result.final_distance = -1.0
                goal_handle.canceled()
                return result

            with self._track_lock:
                active = self._track_active
                track_result = self._track_result
                started = self._track_goal_started
                timeout_sec = self._track_timeout_sec
                feedback.state = self._state
                feedback.distance = float(self._track_distance)
                feedback.detection_count = int(self._track_detection_count)
                feedback.linear_x = float(self.twist.linear.x)
                feedback.angular_z = float(self.twist.angular.z)

            goal_handle.publish_feedback(feedback)

            if track_result is not None:
                result = TrackStrawberry.Result()
                result.success = bool(track_result["success"])
                result.message = str(track_result["message"])
                result.image_path = str(track_result["image_path"])
                result.final_distance = float(track_result["final_distance"])
                if result.success:
                    goal_handle.succeed()
                else:
                    goal_handle.abort()
                return result

            if active and started is not None:
                elapsed = (self.get_clock().now() - started).nanoseconds / 1e9
                if elapsed > timeout_sec:
                    self._finish_tracking(False, f"timeout after {timeout_sec:.1f}s")
                    continue

            time.sleep(0.2)

        self._finish_tracking(False, "rclpy shutdown")
        result = TrackStrawberry.Result()
        result.success = False
        result.message = "rclpy shutdown"
        result.image_path = ""
        result.final_distance = -1.0
        goal_handle.abort()
        return result

    def _start_tracking_goal(self, goal):
        with self._track_lock:
            self.tgt_dist = float(goal.target_distance)
            self._track_timeout_sec = float(goal.timeout_sec) if goal.timeout_sec > 0.0 else 60.0
            self._track_task_id = str(goal.task_id or "track")
            self._track_region = str(goal.region or "unknown")
            self._track_active = True
            self._track_result = None
            self._track_goal_started = self.get_clock().now()
            self._track_detection_count = 0
            self._track_distance = -1.0
            self.twist = Twist()
            self._to_search_locked("goal accepted")
        self.get_logger().info(
            f"TrackStrawberry started region={self._track_region} "
            f"target={self.tgt_dist:.2f}m timeout={self._track_timeout_sec:.1f}s"
        )

    def _finish_tracking(self, success, message, image_path="", final_distance=-1.0, canceled=False):
        with self._track_lock:
            if self._track_result is not None:
                return
            self._track_active = False
            self._state = STATE_IDLE
            self.twist = Twist()
            self._track_result = {
                "success": bool(success),
                "message": str(message),
                "image_path": str(image_path),
                "final_distance": float(final_distance),
                "canceled": bool(canceled),
            }
            self._smooth_cx = None
            self._smooth_cy = None
            self._smooth_d = None
            self._settle_enter = None
            self.pid_ang.reset()
            self.pid_lin.reset()
            self._reset_angular_control()
        self.stop_robot()
        self.get_logger().info(f"TrackStrawberry finished: {message}")

    def _load_rknn_runtime(self, scripts_dir, model_path, class_names_path, core_id):
        scripts_dir = scripts_dir.resolve()
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from rknnlite.api import RKNNLite
        from cluster_tracker import cluster_boxes
        from infer_rknn_yolov8 import load_class_names, postprocess, preprocess

        self.RKNNLite = RKNNLite
        self.cluster_boxes = cluster_boxes
        self.preprocess = preprocess
        self.postprocess = postprocess
        self.class_names = load_class_names(class_names_path)

        self.get_logger().info(f"Loading RKNN model: {model_path}")
        self.rknn = RKNNLite()
        ret = self.rknn.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f"load_rknn failed: {ret}")

        if core_id == -1:
            ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0_1_2)
        elif core_id == 0:
            ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0)
        elif core_id == 1:
            ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_1)
        elif core_id == 2:
            ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_2)
        else:
            ret = self.rknn.init_runtime()
        if ret != 0:
            raise RuntimeError(f"init_runtime failed: {ret}")
        self.get_logger().info("RKNN runtime ready.")

    def _depth_cb(self, dep_msg):
        try:
            dep = self._image_to_cv2(dep_msg)
            if dep.ndim == 3:
                dep = dep[:, :, 0]
            with self._depth_lock:
                self._latest_depth = dep
                self._latest_depth_stamp = self.get_clock().now()
        except Exception as exc:
            self.get_logger().warn(f"Depth decode failed: {exc}")

    def _rgb_cb(self, rgb_msg):
        img = self._image_to_cv2(rgb_msg, "bgr8")
        with self._latest_raw_lock:
            self._latest_raw_img = img.copy()

        with self._track_lock:
            active = self._track_active

        if not active:
            self.twist = Twist()
            self._track_detection_count = 0
            self._track_distance = -1.0
            self._update_fps()
            self._draw(img, [], None, None)
            self._update_stream(img)
            return

        h, w = img.shape[:2]
        dep = self._get_latest_depth(w, h)

        if self.clahe_enabled:
            t0 = time.perf_counter()
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            lab[:, :, 0] = self._clahe.apply(lab[:, :, 0])
            img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
            self._pre_ms = (time.perf_counter() - t0) * 1000.0
        else:
            self._pre_ms = 0.0

        detections = self._infer(img)
        cluster = self._select_cluster(detections, img.shape)
        now = self.get_clock().now()
        az = 0.0
        lx = 0.0
        dist_m = None

        if cluster is not None:
            ccx, ccy = [float(v) for v in cluster["center"]]
            self.last_det = now
            a = self.ema_alpha
            if self._smooth_cx is None:
                self._smooth_cx, self._smooth_cy = ccx, ccy
            else:
                self._smooth_cx = a * ccx + (1.0 - a) * self._smooth_cx
                self._smooth_cy = a * ccy + (1.0 - a) * self._smooth_cy

            raw_d = None if dep is None else self._depth(dep, int(self._smooth_cx), int(self._smooth_cy))
            if raw_d is not None:
                if self._smooth_d is None:
                    self._smooth_d = raw_d
                else:
                    self._smooth_d = a * raw_d + (1.0 - a) * self._smooth_d
            dist_m = self._smooth_d
            sx, sy = self._smooth_cx, self._smooth_cy
            self._arm_vert(sy)
        else:
            sx = sy = None

        lost_sec = (now - self.last_det).nanoseconds / 1e9
        if self._state == STATE_SEARCH:
            if sx is not None:
                self._state = STATE_APPROACH
                self.get_logger().info("SEARCH -> APPROACH")
            else:
                az = self.search_w
        elif self._state == STATE_APPROACH:
            if sx is None and lost_sec > self.timeout:
                self._to_search("target lost")
            elif sx is not None:
                h_aligned = abs(self.tgt_x - sx) <= self.ang_db
                d_aligned = dist_m is not None and abs(dist_m - self.tgt_dist) <= self.dist_db
                if h_aligned and d_aligned:
                    self._state = STATE_SETTLE
                    self._settle_enter = now
                    self.pid_ang.reset()
                    self.pid_lin.reset()
                    self.get_logger().info(f"APPROACH -> SETTLE d={dist_m:.2f}m")
                else:
                    if not h_aligned:
                        az = self._angular_control(float(self.tgt_x), sx)
                    elif dist_m is not None:
                        self._reset_angular_control()
                        lx = np.clip(
                            -self.pid_lin.compute(self.tgt_dist, dist_m),
                            -self.max_lin,
                            self.max_lin,
                        )
        elif self._state == STATE_SETTLE:
            if sx is None and lost_sec > self.timeout:
                self._to_search("target lost in settle")
            elif sx is not None:
                h_ok = abs(self.tgt_x - sx) <= self.ang_db * 2.0
                d_ok = dist_m is not None and abs(dist_m - self.tgt_dist) <= self.dist_db * 2.0
                if not (h_ok and d_ok):
                    self._state = STATE_APPROACH
                    self._settle_enter = None
                    self.get_logger().info("SETTLE -> APPROACH drift")
                else:
                    elapsed = (now - self._settle_enter).nanoseconds / 1e9
                    if elapsed >= self.settle_time:
                        image_path = self._save_raw_image()
                        self._finish_tracking(
                            True,
                            "settle done",
                            image_path=image_path,
                            final_distance=dist_m if dist_m is not None else -1.0,
                        )
                        return

        self.twist = Twist()
        self.twist.linear.x = float(np.clip(lx, -self.max_lin, self.max_lin))
        self.twist.angular.z = float(np.clip(az, -self.max_ang, self.max_ang))
        with self._track_lock:
            self._track_detection_count = int(len(detections))
            self._track_distance = float(dist_m) if dist_m is not None else -1.0

        self._update_fps()
        self._draw(img, detections, cluster, dist_m)
        self._update_stream(img)

    def _arm_pub(self, joints, move_time=None, force=False):
        if not self.arm_on or self.arm_pub is None:
            return
        now = time.perf_counter()
        if not force and now - self._last_arm_pub_t < self.arm_publish_period:
            return
        msg = ArmJoints()
        msg.joint1 = int(joints[0])
        msg.joint2 = int(joints[1])
        msg.joint3 = int(joints[2])
        msg.joint4 = int(joints[3])
        msg.joint5 = int(joints[4])
        msg.joint6 = int(joints[5])
        msg.time = int(move_time or self.arm_time)
        self.arm_pub.publish(msg)
        self._last_arm_pub_t = now

    def _arm_vert(self, cy):
        if not self.arm_on or cy is None:
            return
        err = float(cy) - float(self.tgt_y)
        if abs(err) <= self.arm_vertical_deadband:
            return
        j4 = int(np.clip(self.arm_j[3] - self.arm_gain * err, self.arm_j4_min, self.arm_j4_max))
        if j4 != self.arm_j[3]:
            self.arm_j[3] = j4
            self._arm_pub(self.arm_j)

    def _save_raw_image(self):
        with self._latest_raw_lock:
            img = None if self._latest_raw_img is None else self._latest_raw_img.copy()
        if img is None:
            return ""

        task_id = self._safe_name(self._track_task_id or "track")
        region = self._safe_name(self._track_region or "unknown")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = self.result_root_dir / f"{stamp}_{region}_{task_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "raw.jpg"
        if not cv2.imwrite(str(path), img):
            self.get_logger().warn(f"Failed to save raw image: {path}")
            return ""
        self.get_logger().info(f"Saved raw strawberry image: {path}")
        return str(path)

    def _safe_name(self, value):
        value = str(value).strip()
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:80] or "unknown"

    def _get_latest_depth(self, width, height):
        with self._depth_lock:
            dep = self._latest_depth
            stamp = self._latest_depth_stamp
        if dep is None or stamp is None:
            return None
        age = (self.get_clock().now() - stamp).nanoseconds / 1e9
        if age > self.max_depth_age:
            return None
        if dep.shape[:2] != (height, width):
            dep = cv2.resize(dep, (width, height), interpolation=cv2.INTER_NEAREST)
        return dep

    def _image_to_cv2(self, msg, desired_encoding=None):
        encoding = msg.encoding.lower()
        if encoding in ("bgr8", "rgb8"):
            channels = 3
            dtype = np.uint8
        elif encoding in ("bgra8", "rgba8"):
            channels = 4
            dtype = np.uint8
        elif encoding in ("mono8", "8uc1"):
            channels = 1
            dtype = np.uint8
        elif encoding in ("mono16", "16uc1"):
            channels = 1
            dtype = np.uint16
        elif encoding in ("32fc1",):
            channels = 1
            dtype = np.float32
        else:
            raise ValueError(f"Unsupported image encoding: {msg.encoding}")

        itemsize = np.dtype(dtype).itemsize
        row_elems = msg.step // itemsize
        data = np.frombuffer(msg.data, dtype=dtype)
        if msg.is_bigendian and data.dtype.byteorder != ">":
            data = data.byteswap().newbyteorder()

        if channels == 1:
            image = data.reshape((msg.height, row_elems))[:, : msg.width].copy()
        else:
            image = data.reshape((msg.height, row_elems // channels, channels))[:, : msg.width, :].copy()

        if desired_encoding == "bgr8":
            if encoding == "rgb8":
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            elif encoding == "rgba8":
                image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
            elif encoding == "bgra8":
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
            elif encoding != "bgr8":
                raise ValueError(f"Cannot convert {msg.encoding} to bgr8")
        return image

    def _infer(self, img):
        t0 = time.perf_counter()
        input_rgb, scale, pad_x, pad_y = self.preprocess(img)
        outputs = self.rknn.inference(inputs=[input_rgb], data_format=["nhwc"])
        if outputs is None:
            self.get_logger().warn("RKNN inference returned None")
            return []
        detections = self.postprocess(
            outputs,
            scale,
            pad_x,
            pad_y,
            img.shape[1],
            img.shape[0],
            self.conf,
            self.nms_iou,
            self.class_names,
            score_sum_factor=self.score_sum_factor,
        )
        self._infer_ms = (time.perf_counter() - t0) * 1000.0
        self._infer_fps_count += 1
        now_t = time.time()
        dt = now_t - self._infer_fps_t
        if dt >= 1.0:
            self._infer_fps = self._infer_fps_count / dt
            self._infer_fps_count = 0
            self._infer_fps_t = now_t
        return detections

    def _select_cluster(self, detections, frame_shape):
        clusters = self.cluster_boxes(
            detections,
            frame_shape,
            min_conf=self.cluster_min_conf,
            min_box_area_ratio=self.cluster_min_box_area_ratio,
            center_dist_scale=self.cluster_merge_dist_scale,
            expand_x=self.cluster_expand_x,
            expand_y=self.cluster_expand_y,
            secondary_dist_scale=self.cluster_secondary_dist_scale,
        )
        clusters = [
            c for c in clusters if int(c.get("member_count", 0)) >= self.min_cluster_members
        ]
        if not clusters:
            return None
        return max(clusters, key=lambda c: float(c.get("score", 0.0)))

    def _angular_control(self, target, current):
        err = float(target - current)
        abs_err = abs(err)
        if abs_err <= self.ang_db:
            self._reset_angular_control()
            return 0.0

        span = max(self.ang_far_error - self.ang_db, 1.0)
        ratio = float(np.clip((abs_err - self.ang_db) / span, 0.0, 1.0))
        kp = self.ang_slow_kp + ratio * (self.ang_fast_kp - self.ang_slow_kp)
        derivative = err - self._prev_ang_err
        raw = kp * err + self.pid_ang.kd * derivative
        raw = float(np.clip(raw, -self.max_ang, self.max_ang))

        now_t = time.perf_counter()
        dt = max(now_t - self._last_az_t, 1e-3)
        max_step = self.ang_slew_rate * dt
        limited = float(np.clip(raw, self._last_az - max_step, self._last_az + max_step))

        self._prev_ang_err = err
        self._last_az = limited
        self._last_az_t = now_t
        return limited

    def _reset_angular_control(self):
        self._prev_ang_err = 0.0
        self._last_az = 0.0
        self._last_az_t = time.perf_counter()

    def _depth(self, dep, px, py):
        h, w = dep.shape[:2]
        r = self.roi_r
        patch = dep[max(0, py - r) : min(h, py + r + 1), max(0, px - r) : min(w, px + r + 1)]
        patch = patch.astype(np.float64)
        valid = patch[(patch > 0) & np.isfinite(patch)]
        if len(valid) == 0:
            return None
        value = float(np.median(valid))
        return value / 1000.0 if value > 100.0 else value

    def _to_search(self, reason=""):
        with self._track_lock:
            self._to_search_locked(reason)
        self.get_logger().info(f"-> SEARCH ({reason})")

    def _to_search_locked(self, reason=""):
        self._state = STATE_SEARCH
        self._settle_enter = None
        self._smooth_cx = None
        self._smooth_cy = None
        self._smooth_d = None
        self.pid_ang.reset()
        self.pid_lin.reset()
        self._reset_angular_control()

    def _draw(self, img, detections, cluster, dist_m):
        h, w = img.shape[:2]
        if self.draw_debug_overlay:
            cv2.drawMarker(img, (self.tgt_x, self.tgt_y), (255, 255, 255), cv2.MARKER_CROSS, 20, 1)

            for det in detections:
                x1, y1, x2, y2 = [int(round(v)) for v in det["xyxy"]]
                class_id = int(det.get("class_id", -1))
                color = CLASS_COLORS.get(class_id, (0, 255, 0))
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 1)
                label = f'{det.get("class_name", class_id)} {float(det.get("confidence", 0.0)):.2f}'
                cv2.putText(img, label, (x1, max(16, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

            if cluster is not None:
                x1, y1, x2, y2 = [int(round(v)) for v in cluster["xyxy"]]
                cx, cy = [int(round(v)) for v in cluster["center"]]
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 255), 3)
                cv2.circle(img, (cx, cy), 4, (0, 255, 255), -1)
                if self._smooth_cx is not None:
                    cv2.circle(img, (int(self._smooth_cx), int(self._smooth_cy)), 6, (0, 255, 0), -1)
                info = f'x{int(cluster.get("member_count", 0))}'
                if dist_m is not None:
                    info += f" {dist_m:.2f}m"
                cv2.putText(img, info, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        cv2.putText(
            img,
            f"{self._state} fps={self._fps:.1f} infer={self._infer_fps:.1f} {self._infer_ms:.1f}ms",
            (8, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
        )
        cv2.putText(
            img,
            f"v={self.twist.linear.x:.2f} w={self.twist.angular.z:.2f} det={len(detections)}",
            (8, 50),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
        )
        if dist_m is not None:
            cv2.putText(
                img,
                f"d={dist_m:.2f}/{self.tgt_dist:.2f}m",
                (8, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 200, 255),
                1,
            )
        if self.clahe_enabled:
            cv2.putText(img, f"pre:{self._pre_ms:.1f}ms", (w - 160, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

    def _update_fps(self):
        self._fps_count += 1
        now_t = time.time()
        dt = now_t - self._fps_t
        if dt >= 1.0:
            self._fps = self._fps_count / dt
            self._fps_count = 0
            self._fps_t = now_t

    def _update_stream(self, img):
        self._fc += 1
        if self._fc % self.stream_every_n != 0:
            return
        global _stream_jpg
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
        if not ok:
            return
        with _stream_lock:
            _stream_jpg = jpg.tobytes()
        _stream_event.set()

    def stop_robot(self):
        self.twist = Twist()
        for _ in range(10):
            self.cmd_pub.publish(self.twist)
            time.sleep(0.02)

    def destroy_node(self):
        self.stop_robot()
        if hasattr(self, "action_server"):
            self.action_server.destroy()
        if hasattr(self, "rknn"):
            self.rknn.release()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = StrawberryTrackerNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        node.get_logger().info("Stopping...")
    finally:
        node.stop_robot()
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
