#!/usr/bin/env python3
import argparse
import os
import time

import rclpy
from arm_msgs.msg import ArmJoint, ArmJoints
from rclpy.node import Node


def parse_pose(text):
	values = [float(x.strip()) for x in text.split(",")]
	if len(values) != 7:
		raise argparse.ArgumentTypeError("pose must have 7 comma-separated values")
	return values


class PlaceBlockNode(Node):
	def __init__(self, args):
		super().__init__("place_block")
		self.arm_pub = self.create_publisher(ArmJoints, "arm6_joints", 10)
		self.single_pub = self.create_publisher(ArmJoint, "arm_joint", 10)
		self.gripper_close = args.gripper_close
		self.open_angle = args.open_angle
		self.place_above = args.place_above
		self.place_down = args.place_down
		self.home_pose = args.home_pose
		self.get_logger().info(
			f"place block ready open_angle={self.open_angle} gripper_close={self.gripper_close}"
		)

	def run(self):
		above = list(self.place_above)
		down = list(self.place_down)
		home = list(self.home_pose)
		above[5] = self.gripper_close
		down[5] = self.gripper_close
		home[5] = self.open_angle
		self.publish_pose(above, "place_above")
		self.publish_pose(down, "place_down")
		self.publish_gripper(self.open_angle, 1000, "release")
		self.publish_pose(above, "retreat")
		self.publish_pose(home, "home")
		self.get_logger().info("block released")

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


def main(argv=None):
	parser = argparse.ArgumentParser(description="Release a held block at the current robot position.")
	parser.add_argument("--gripper-close", type=int, default=165)
	parser.add_argument("--open-angle", type=int, default=30)
	parser.add_argument("--place-above", type=parse_pose, default=parse_pose("90,150,12,20,90,165,1800"))
	parser.add_argument("--place-down", type=parse_pose, default=parse_pose("90,90,55,5,90,165,1800"))
	parser.add_argument("--home-pose", type=parse_pose, default=parse_pose("90,150,12,20,90,30,1800"))
	args, ros_args = parser.parse_known_args(argv)
	if not os.environ.get("ROS_DOMAIN_ID"):
		os.environ["ROS_DOMAIN_ID"] = "30"
	rclpy.init(args=ros_args)
	node = PlaceBlockNode(args)
	try:
		node.run()
	finally:
		node.destroy_node()
		rclpy.shutdown()


if __name__ == "__main__":
	main()
