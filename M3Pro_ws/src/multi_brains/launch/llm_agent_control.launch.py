import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.actions import IncludeLaunchDescription,TimerAction
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration

def generate_launch_description():
    M3Pro_demopkg_share = get_package_share_directory('M3Pro_demo')
    rgb_topic="/camera/color/image_raw"
    #启动参数
    declared_arguments = []
    declared_arguments.append(
        DeclareLaunchArgument(
            "text_chat_mode",
            default_value='False',
            description="是否为文本对话模式",
        )
    ) 
    declared_arguments.append(
        DeclareLaunchArgument(
            "config_file",
            default_value=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml'),
            description="配置文件路径",
        )
    )
    declared_arguments.append(
        DeclareLaunchArgument(
            "map_mapping_file",
            default_value=os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','map_mapping.yaml'),
            description="地图映射文件路径",
        )
    )
    declared_arguments.append(
        DeclareLaunchArgument(
            "enable_route_nav",
            default_value='False',
            description="是否启动路网导航模式，默认关闭",
        )
    )

    text_chat_mode = LaunchConfiguration('text_chat_mode')
    config_file = LaunchConfiguration('config_file')
    map_mapping_file = LaunchConfiguration('map_mapping_file')
    enable_route_nav = LaunchConfiguration('enable_route_nav')

    model_service_node=Node(
                    package='multi_brains',
                    executable='model_service',
                    name='model_service',
                    emulate_tty=True,
                    parameters=[
                        {'text_chat_mode': text_chat_mode},
                        {'config_file': config_file}
                    ],
                    output='screen'
                )
    action_service_node=Node(
                    package='multi_brains',
                    executable='action_service',
                    name='action_service',
                    emulate_tty=True,
                    parameters=[
                        {'text_chat_mode': text_chat_mode},
                        {'image_topic': rgb_topic},
                        {'config_file': config_file},
                        {'map_mapping_file': map_mapping_file},
                        {'enable_route_nav': enable_route_nav}
                    ],
                    output='screen'
                )

    camrea_kin_node = IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(M3Pro_demopkg_share, 'launch', 'camera_arm_kin.launch.py')))
    return LaunchDescription([
        camrea_kin_node,  #启动相机驱动
        *declared_arguments,    #声明启动参数
        model_service_node,    
        action_service_node
    ])




