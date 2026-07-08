import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    # Orbbec camera launch
    camera_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(
                get_package_share_directory('orbbec_camera'),
                'launch',
                'dabai_dcw2.launch.py',
            )
        ])
    )

    # Tracker node
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

    return LaunchDescription([
        camera_launch,
        tracker_node,
    ])
