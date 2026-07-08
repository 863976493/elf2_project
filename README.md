# 基于云边端协同的智慧农园自主巡检与诊断系统

本仓库为作品“基于云边端协同的智慧农园自主巡检与诊断系统”的车机端开源工程，基于 ELF2 RK3588 平台，包含导航建图、自主巡检、云端巡检桥接、Orbbec 相机接入、RKNN 视觉模型推理、草莓成熟度/病害诊断、色块识别与机械臂搬运等代码与配置。

## 目录结构

```text
.
├── elf2_strawberry_ws/          # 草莓巡检、相机、推理等 ROS2 工作区源码
├── M3Pro_ws/                    # M3Pro 机械臂、建图、色块抓取等 ROS2 工作区源码
├── elf2_official_sort_ws/       # 官方分拣/搬运相关源码
├── embedded_project/            # 底盘导航、Nav2、底层传感器相关源码
├── strawberry_models/           # 草莓成熟度/病害识别模型
├── deploy_elf2_maturity/model/  # ELF2 部署用 RKNN 模型
├── m3pro_nav_map/               # 导航地图
├── cloud/                       # 云端服务、数据接口、Web 展示与模拟工具
├── media/                       # 比赛演示视频（Git LFS 管理）
├── inspect_region_bridge.py     # 云端巡检桥接脚本
├── start_agent.sh               # 32 控制板代理启动脚本
└── SOURCE_MANIFEST.txt          # 打包来源清单
```

## 运行环境

- ROS 2 Humble
- ELF2 RK3588 车机端
- Yahboom M3Pro 移动底盘与机械臂
- Orbbec Dabai DCW2 相机
- `ROS_DOMAIN_ID=30`


## 云端服务

云端代码位于 `cloud/`，包含 FastAPI 服务、Web 展示页面、巡检结果处理、AI 诊断接口、地图接口、WebSocket 通信、MQTT 通信和模拟工具。公开版本已移除运行数据库、上传图片缓存、历史备份文件和默认设备密码；AI API Key、Jetson 密码等敏感配置请通过环境变量或页面设置填写。

## 演示视频

比赛演示视频位于 `media/全国嵌入式芯片与系统设计竞赛.mp4`。该文件超过 GitHub 普通单文件限制，仓库使用 Git LFS 管理 `.mp4` 文件。

## 色块抓取

终端 1：相机 + IK

```bash
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source /root/elf2_strawberry_ws/install/setup.bash
source /root/M3Pro_ws/install/setup.bash
source /root/elf2_official_sort_ws/install/setup.bash
ros2 launch M3Pro_demo camera_arm_kin.launch.py
```

终端 2：机械臂抓取并保持

```bash
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source /root/elf2_strawberry_ws/install/setup.bash
source /root/M3Pro_ws/install/setup.bash
source /root/elf2_official_sort_ws/install/setup.bash
ros2 run M3Pro_demo grasp_desktop --ros-args -p hold_after_pick:=true
```

当前抓取参数：

```text
grasp deltas x=0.010 y=-0.007 z=-0.001 gripper_close=165
```

终端 3：识别并靠近抓取黄色

```bash
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source /root/elf2_strawberry_ws/install/setup.bash
source /root/M3Pro_ws/install/setup.bash
source /root/elf2_official_sort_ws/install/setup.bash
ros2 run M3Pro_demo color_recognize --ros-args -p target_color:=yellow -p auto_adjust:=true
```

绿色/蓝色只改最后一行颜色：

```bash
ros2 run M3Pro_demo color_recognize --ros-args -p target_color:=green -p auto_adjust:=true
ros2 run M3Pro_demo color_recognize --ros-args -p target_color:=blue -p auto_adjust:=true
```

放下积木：

```bash
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source /root/elf2_strawberry_ws/install/setup.bash
source /root/M3Pro_ws/install/setup.bash
source /root/elf2_official_sort_ws/install/setup.bash
ros2 run M3Pro_demo place_block
```

## 巡检任务

启动 32 控制板代理：

```bash
bash ~/start_agent.sh
```

启动底盘与底部传感器：

```bash
cd /root/embedded_project/项目源码/orin版本源码/m3pro_ws/m3pro_ws
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch M3Pro_navigation base_bringup.launch.py
```

启动 Nav2：

```bash
cd /root/embedded_project/项目源码/orin版本源码/m3pro_ws/m3pro_ws
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch M3Pro_navigation navigation2.launch.py map:=/root/m3pro_nav_map/yahboom_map.yaml
```

启动 Orbbec 相机：

```bash
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source /root/elf2_strawberry_ws/install/setup.bash
ros2 launch orbbec_camera dabai_dcw2.launch.py
```

启动巡检任务节点：

```bash
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source /root/embedded_project/项目源码/orin版本源码/m3pro_ws/m3pro_ws/install/setup.bash
source /root/elf2_strawberry_ws/install/setup.bash
ros2 launch strawberry_mission_bt inspect_bringup.launch.py
```

启动云端巡检桥接：

```bash
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source /root/embedded_project/项目源码/orin版本源码/m3pro_ws/m3pro_ws/install/setup.bash
source /root/elf2_strawberry_ws/install/setup.bash
python3 /root/inspect_region_bridge.py --server ws://172.20.10.3:8000/ws/robot
```

## 建图与保存

建图：

```bash
export ROS_DOMAIN_ID=30
source /opt/ros/humble/setup.bash
source /root/embedded_project/项目源码/orin版本源码/m3pro_ws/m3pro_ws/install/setup.bash
ros2 launch slam_mapping slam_toolbox.launch.py
```

保存地图：

```bash
ros2 launch slam_mapping save_map.launch.py map_path:=/root/m3pro_nav_map/yahboom_map
```

## 开源说明

本项目代码以 MIT License 开源。仓库中包含的第三方 SDK、设备驱动、模型权重或示例资源，其版权和许可证归原作者或供应商所有。




