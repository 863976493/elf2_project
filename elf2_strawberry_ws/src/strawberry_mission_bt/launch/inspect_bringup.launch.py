import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    mission_share = get_package_share_directory("strawberry_mission_bt")
    tracker_share = get_package_share_directory("strawberry_tracker")
    inference_share = get_package_share_directory("strawberry_inference")

    regions_config = os.path.join(mission_share, "config", "regions.yaml")
    tracker_config = os.path.join(tracker_share, "config", "tracker_params.yaml")
    inference_config = os.path.join(inference_share, "config", "inference_params.yaml")

    return LaunchDescription(
        [
            Node(
                package="strawberry_tracker",
                executable="strawberry_tracker_node",
                name="strawberry_tracker_node",
                parameters=[tracker_config],
                output="screen",
            ),
            Node(
                package="strawberry_inference",
                executable="strawberry_inference_node",
                name="strawberry_inference_node",
                parameters=[inference_config],
                output="screen",
            ),
            Node(
                package="strawberry_mission_bt",
                executable="inspection_mission_node",
                name="inspection_mission_node",
                parameters=[{"regions_config": regions_config}],
                output="screen",
            ),
        ]
    )
