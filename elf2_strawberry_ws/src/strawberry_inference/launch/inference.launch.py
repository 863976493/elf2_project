import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    config = os.path.join(
        get_package_share_directory("strawberry_inference"),
        "config",
        "inference_params.yaml",
    )

    return LaunchDescription(
        [
            Node(
                package="strawberry_inference",
                executable="strawberry_inference_node",
                name="strawberry_inference_node",
                parameters=[config],
                output="screen",
            )
        ]
    )
