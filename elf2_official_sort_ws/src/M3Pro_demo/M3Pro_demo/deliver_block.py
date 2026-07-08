#!/usr/bin/env python3
import argparse
import math
import os
import time

import rclpy
from arm_msgs.msg import ArmJoint, ArmJoints
from geometry_msgs.msg import PoseStamped
from geometry_msgs.msg import Twist
from interfaces.action import Rot
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool


def latched_bool_qos():
	qos = QoSProfile(depth=1)
	qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
	qos.reliability = ReliabilityPolicy.RELIABLE
	return qos


def yaw_to_quat(yaw):
	half = yaw / 2.0
	return 0.0, 0.0, math.sin(half), math.cos(half)


def make_pose(frame_id, x, y, yaw):
	pose = PoseStamped()
	pose.header.frame_id = frame_id
	pose.pose.position.x = float(x)
	pose.pose.position.y = float(y)
	pose.pose.position.z = 0.0
	qx, qy, qz, qw = yaw_to_quat(float(yaw))
	pose.pose.orientation.x = qx
	pose.pose.orientation.y = qy
	pose.pose.orientation.z = qz
	pose.pose.orientation.w = qw
	return pose


class DeliverBlockNode(Node):
	def __init__(self, args):
		super().__init__("deliver_block")
		self.region = args.region.upper()
		self.nav_client = ActionClient(self, NavigateToPose, args.nav_action)
		self.original_nav_client = ActionClient(self, Rot, args.original_nav_action)
		self.arm_pub = self.create_publisher(ArmJoints, "arm6_joints", 10)
		self.single_pub = self.create_publisher(ArmJoint, "arm_joint", 10)
		self.cmd_vel_pub = self.create_publisher(Twist, args.cmd_vel_topic, 10)
		self.done = False
		self.block_held = False
		self.create_subscription(Bool, "block_held", self.block_held_callback, latched_bool_qos())
		self.frame_id = args.frame_id
		self.nav_timeout = args.nav_timeout
		self.wait_pick_timeout = args.wait_pick_timeout
		self.use_original_nav = args.use_original_nav
		self.open_angle = args.open_angle
		self.gripper_close = args.gripper_close
		self.place_above = args.place_above
		self.place_down = args.place_down
		self.pre_nav_escape = args.pre_nav_escape
		self.back_duration = args.back_duration
		self.forward_duration = args.forward_duration
		self.turn_duration = args.turn_duration
		self.back_speed = args.back_speed
		self.forward_speed = args.forward_speed
		self.turn_speed = args.turn_speed
		if self.region == "A":
			self.goal_x = args.a_x
			self.goal_y = args.a_y
			self.goal_yaw = args.a_yaw
		else:
			self.goal_x = args.b_x
			self.goal_y = args.b_y
			self.goal_yaw = args.b_yaw
		self.get_logger().info(
			f"deliver ready region={self.region} goal=({self.goal_x:.3f},{self.goal_y:.3f},{self.goal_yaw:.3f})"
		)

	def block_held_callback(self, msg):
		if msg.data:
			self.block_held = True

	def run(self):
		self.wait_for_block()
		self.navigate_to_region()
		self.place_block()
		self.done = True

	def wait_for_block(self):
		self.get_logger().info("waiting for block_held=True")
		start = time.monotonic()
		while rclpy.ok() and not self.block_held:
			rclpy.spin_once(self, timeout_sec=0.1)
			if time.monotonic() - start > self.wait_pick_timeout:
				raise RuntimeError("timeout waiting for block_held")
		self.get_logger().info("block held; starting delivery")

	def run_pre_nav_escape(self):
		self.get_logger().info(
			"pre-nav escape: back+turn %.2fs, forward %.2fs"
			% (self.back_duration, self.forward_duration)
		)
		self.drive_for(-abs(self.back_speed), self.turn_speed, self.back_duration, "back_turn")
		self.drive_for(abs(self.forward_speed), 0.0, self.forward_duration, "forward")
		self.stop_base()
		time.sleep(0.2)

	def drive_for(self, linear_x, angular_z, duration, label):
		msg = Twist()
		msg.linear.x = float(linear_x)
		msg.angular.z = float(angular_z)
		end_time = time.monotonic() + max(0.0, float(duration))
		self.get_logger().info(f"cmd_vel {label}: vx={linear_x:.3f} wz={angular_z:.3f} duration={duration:.2f}s")
		while rclpy.ok() and time.monotonic() < end_time:
			self.cmd_vel_pub.publish(msg)
			rclpy.spin_once(self, timeout_sec=0.0)
			time.sleep(0.05)
		self.stop_base()

	def stop_base(self):
		msg = Twist()
		for _ in range(4):
			self.cmd_vel_pub.publish(msg)
			time.sleep(0.03)

	def navigate_to_region(self):
		if self.use_original_nav:
			return self.navigate_with_original_project()
		if not self.nav_client.wait_for_server(timeout_sec=8.0):
			raise RuntimeError("/navigate_to_pose action server not available")
		goal = NavigateToPose.Goal()
		goal.pose = make_pose(self.frame_id, self.goal_x, self.goal_y, self.goal_yaw)
		goal.pose.header.stamp = self.get_clock().now().to_msg()
		self.get_logger().info(
			f"NavigateToPose region={self.region} frame={self.frame_id} "
			f"x={self.goal_x:.3f} y={self.goal_y:.3f} yaw={self.goal_yaw:.3f}"
		)
		send_future = self.nav_client.send_goal_async(goal)
		rclpy.spin_until_future_complete(self, send_future)
		handle = send_future.result()
		if handle is None or not handle.accepted:
			raise RuntimeError("delivery navigation goal rejected")
		result_future = handle.get_result_async()
		start = time.monotonic()
		while rclpy.ok() and not result_future.done():
			rclpy.spin_once(self, timeout_sec=0.1)
			if time.monotonic() - start > self.nav_timeout:
				cancel_future = handle.cancel_goal_async()
				rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=2.0)
				raise RuntimeError("delivery navigation timeout")
		result = result_future.result()
		self.get_logger().info(f"delivery navigation finished status={getattr(result, 'status', None)}")

	def navigate_with_original_project(self):
		if not self.original_nav_client.wait_for_server(timeout_sec=8.0):
			raise RuntimeError("/action_service action server not available; start original multi_brains action_service")
		action = f"navigation({self.region})"
		goal = Rot.Goal()
		goal.actions = [action]
		goal.llm_response = f"deliver block to region {self.region}"
		self.get_logger().info(f"original project navigation: /action_service actions={goal.actions}")
		send_future = self.original_nav_client.send_goal_async(goal)
		rclpy.spin_until_future_complete(self, send_future)
		handle = send_future.result()
		if handle is None or not handle.accepted:
			raise RuntimeError("original project navigation goal rejected")
		result_future = handle.get_result_async()
		start = time.monotonic()
		while rclpy.ok() and not result_future.done():
			rclpy.spin_once(self, timeout_sec=0.1)
			if time.monotonic() - start > self.nav_timeout:
				cancel_future = handle.cancel_goal_async()
				rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=2.0)
				raise RuntimeError("original project navigation timeout")
		result = result_future.result()
		success = bool(getattr(result.result, "success", False))
		self.get_logger().info(f"original project navigation finished success={success}")
		if not success:
			raise RuntimeError("original project navigation failed")

	def place_block(self):
		self.get_logger().info(f"placing block at region={self.region}")
		above = list(self.place_above)
		down = list(self.place_down)
		above[5] = self.gripper_close
		down[5] = self.gripper_close
		self.publish_pose(above, "place_above")
		self.publish_pose(down, "place_down")
		self.publish_gripper(self.open_angle, 1000, "release")
		self.publish_pose(above, "retreat")
		self.publish_pose([90, 150, 12, 20, 90, self.open_angle, 1800], "home")

	def publish_pose(self, joints, label):
		msg = ArmJoints()
		msg.joint1 = int(joints[0])
		msg.joint2 = int(joints[1])
		msg.joint3 = int(joints[2])
		msg.joint4 = int(joints[3])
		msg.joint5 = int(joints[4])
		msg.joint6 = int(joints[5])
		msg.time = int(joints[6]) if len(joints) > 6 else 1800
		self.get_logger().info(f"arm pose {label}: {joints}")
		for _ in range(3):
			self.arm_pub.publish(msg)
			time.sleep(0.08)
		time.sleep(max(0.3, msg.time / 1000.0 + 0.4))

	def publish_gripper(self, angle, run_time, label):
		msg = ArmJoint()
		msg.id = 6
		msg.joint = int(angle)
		msg.time = int(run_time)
		self.get_logger().info(f"gripper {label}: angle={angle}")
		for _ in range(3):
			self.single_pub.publish(msg)
			time.sleep(0.08)
		time.sleep(max(0.3, run_time / 1000.0 + 0.4))


def parse_pose(text):
	values = [float(x.strip()) for x in text.split(",")]
	if len(values) != 7:
		raise argparse.ArgumentTypeError("pose must have 7 comma-separated values")
	return values


def str2bool(text):
	if isinstance(text, bool):
		return text
	value = str(text).strip().lower()
	if value in ("1", "true", "yes", "on"):
		return True
	if value in ("0", "false", "no", "off"):
		return False
	raise argparse.ArgumentTypeError("expected true/false")


def main(argv=None):
	parser = argparse.ArgumentParser(description="Wait for a held block, navigate to A/B, then release it.")
	parser.add_argument("--region", choices=["A", "B"], required=True)
	parser.add_argument("--frame-id", default="map")
	parser.add_argument("--nav-action", default="/navigate_to_pose")
	parser.add_argument("--original-nav-action", default="/action_service")
	parser.add_argument("--use-original-nav", type=str2bool, default=True)
	parser.add_argument("--cmd-vel-topic", default="/cmd_vel")
	parser.add_argument("--wait-pick-timeout", type=float, default=90.0)
	parser.add_argument("--nav-timeout", type=float, default=90.0)
	parser.add_argument("--a-x", type=float, default=1.904)
	parser.add_argument("--a-y", type=float, default=-1.714)
	parser.add_argument("--a-yaw", type=float, default=0.229)
	parser.add_argument("--b-x", type=float, default=0.428)
	parser.add_argument("--b-y", type=float, default=-1.628)
	parser.add_argument("--b-yaw", type=float, default=-1.525)
	parser.add_argument("--gripper-close", type=int, default=165)
	parser.add_argument("--open-angle", type=int, default=30)
	parser.add_argument("--pre-nav-escape", type=str2bool, default=True)
	parser.add_argument("--back-duration", type=float, default=1.0)
	parser.add_argument("--forward-duration", type=float, default=1.0)
	parser.add_argument("--turn-duration", type=float, default=1.0)
	parser.add_argument("--back-speed", type=float, default=0.25)
	parser.add_argument("--forward-speed", type=float, default=0.25)
	parser.add_argument("--turn-speed", type=float, default=1.57)
	parser.add_argument("--place-above", type=parse_pose, default=parse_pose("90,150,12,20,90,165,1800"))
	parser.add_argument("--place-down", type=parse_pose, default=parse_pose("90,90,55,5,90,165,1800"))
	args, ros_args = parser.parse_known_args(argv)
	if not os.environ.get("ROS_DOMAIN_ID"):
		os.environ["ROS_DOMAIN_ID"] = "30"
	rclpy.init(args=ros_args)
	node = DeliverBlockNode(args)
	try:
		node.run()
	finally:
		node.destroy_node()
		rclpy.shutdown()


if __name__ == "__main__":
	main()
