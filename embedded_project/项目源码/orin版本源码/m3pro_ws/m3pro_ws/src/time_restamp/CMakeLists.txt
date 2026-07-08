cmake_minimum_required(VERSION 3.8)
project(time_restamp)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)

add_executable(time_restamp_node src/time_restamp_node.cpp)
ament_target_dependencies(time_restamp_node rclcpp nav_msgs sensor_msgs)
install(TARGETS time_restamp_node DESTINATION lib/${PROJECT_NAME})

ament_package()
