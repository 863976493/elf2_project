#!/bin/bash
sleep 5
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source ~/mircoROS_agent/install/local_setup.bash
ros2 run micro_ros_agent micro_ros_agent serial --dev /dev/myserial -b 2000000
