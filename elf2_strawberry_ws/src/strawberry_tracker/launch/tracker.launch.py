import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    config = os.path.join(
        get_package_share_directory('strawberry_tracker'),
        'config',
        'tracker_params.yaml',
    )

    tracker_node = Node(
        package='strawberry_tracker',
        executable='strawberry_tracker_node',
        name='strawberry_tracker_node',
        parameters=[config],
        output='screen',
    )

    return LaunchDescription([tracker_node])
