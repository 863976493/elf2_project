import os
import  yaml
from ament_index_python.packages import get_package_share_directory
import rclpy
from rclpy.node import Node
from interfaces.action import Rot
from interfaces.msg import LlmRequest
from cv_bridge import CvBridge
from std_msgs.msg import String,Bool
from sensor_msgs.msg import Image
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped,Twist
from rclpy.action import ActionClient,ActionServer
from tf2_ros.buffer import Buffer
from tf2_ros.transform_listener import TransformListener
import time
import threading
import re
from colorama import Fore
from rclpy.executors import MultiThreadedExecutor
from concurrent.futures import Future
import subprocess
from .utils.common_tools import kill_process_tree,LogTranslator
import cv2
import math
from std_msgs.msg import Int16MultiArray, Bool
from arm_msgs.msg import ArmJoints, ArmJoint
from arm_interface.msg import CurJoints
from threading import Thread
class ActionController(Node):
    def __init__(self):
        super().__init__("actioncontroller_node")
        self.init_param_config()#初始化参数配置 /Initialize parameter configuration
        self.init_ros_comunication()# 初始化ROS通信 / Initialize ROS communication
        self.load_target_points()# 加载地图映射文件 / Load map mapping file
        self.__arm_init()# 初始化机械臂抓取功能 /initialize the grasping function of the robotic arm
        self.get_logger().info(Fore.GREEN+"ROS_Action_Service Initialization completed"+Fore.RESET)

    def init_param_config(self):
        """
        初始化参数配置 / Initialize parameter configuration
        """
        # 参数文件
        self.declare_parameter("config_file", os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','multi_brains_setting.yaml'))
        self.config_file = self.get_parameter("config_file").get_parameter_value().string_value
        #相机话题
        self.declare_parameter("image_topic", "/camera/color/image_raw")
        self.image_topic = self.get_parameter("image_topic").get_parameter_value().string_value
        # 地图映射文件
        self.declare_parameter("map_mapping_file", os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','map_mapping.yaml'))
        self.map_mapping_file = self.get_parameter("map_mapping_file").get_parameter_value().string_value
        # 是否启用路网导航，默认关闭
        self.declare_parameter("enable_route_nav", False)
        self.enable_route_nav = self.get_parameter("enable_route_nav").get_parameter_value().bool_value
        # 图片缓存路径
        self.image_cache_path =os.path.join(os.path.expanduser('~'),'M3Pro_ws','multi_brains_file','image.png')

        with open(self.config_file, "r") as file:
                config_param = yaml.safe_load(file)

        self.language=config_param.get("LANGUAGE","zh")# 语言设置 / Language setting
        self.debug_mode=config_param.get("DEBUG_MODE",False)#是否开启调试模式 / Whether to enable debug mode
        self.actionlog=LogTranslator(self.language)#创建日志翻译对象
        self.actionlog.load_translations_file(os.path.join(get_package_share_directory("multi_brains"),"language"))#加载语言文件/load language file
        self.action_runing = False # 当前是否有动作在执行 / Whether there is an action currently being executed
        self.first_record = True    # 首次记录位置 / First record
        self.interrupt_event = threading.Event()#打断事件，每次语音唤醒时打断所有正在进行的动作
        
        #外部动作相关对象/external action related objects
        self.grasp_obj_future = Future() 
        self.apriltag_sort_future= Future() 
        self.apriltag_remove_higher_future = Future()
        self.color_remove_higher_future = Future()
        self.follow_line_clear_future = Future()
        # 外部进程对象 / External process objects
        #track process
        self.track_process_1= None
        self.track_process_2= None
        #grasp obj process
        self.grasp_obj_process_1= None
        self.grasp_obj_process_2= None
        self.grasp_obj_process_3= None
        #apriltag sort process
        self.apriltag_sort_process_1 = None
        self.apriltag_sort_process_2 = None
        #apriltag remove higher process
        self.apriltag_remove_higher_process_1 = None
        self.apriltag_remove_higher_process_2 = None
        #color remove higher process
        self.color_remove_higher_process_1 = None
        self.color_remove_higher_process_2 = None
        
        self.kcf_follow_process = None
        self.follow_line_process = None
        self.image_msg=None
        
    def load_target_points(self):
        """
        加载地图映射文件 /Load map mapping file
        """
        self.navpose_dict = {}
        self.road_net_dict = {}
        with open(self.map_mapping_file, "r") as file:
            full_target_points = yaml.safe_load(file)
            common_target_points = full_target_points.get("common_map_areas", {})
            road_net_target_points = full_target_points.get("road_net_map_areas", {})

        for name, data in common_target_points.items():#加载常规导航点 / Load common navigation points
            pose = PoseStamped()
            pose.header.frame_id = "map"
            pose.pose.position.x = data["position"]["x"]
            pose.pose.position.y = data["position"]["y"]
            pose.pose.position.z = data["position"]["z"]
            pose.pose.orientation.x = data["orientation"]["x"]
            pose.pose.orientation.y = data["orientation"]["y"]
            pose.pose.orientation.z = data["orientation"]["z"]
            pose.pose.orientation.w = data["orientation"]["w"]
            self.navpose_dict[name] = pose

        for name, data in road_net_target_points.items():#加载路网导航点 / Load road network navigation points
            pose = PoseStamped()
            pose.header.frame_id = "map"
            pose.pose.position.x = data["position"]["x"]
            pose.pose.position.y = data["position"]["y"]
            pose.pose.position.z = data["position"]["z"]
            pose.pose.orientation.x = data["orientation"]["x"]
            pose.pose.orientation.y = data["orientation"]["y"]
            pose.pose.orientation.z = data["orientation"]["z"]
            pose.pose.orientation.w = data["orientation"]["w"]
            self.road_net_dict[name] = pose
    def init_ros_comunication(self):
        """
        初始化创建ros通信对象、函数 / Initialize creation of ROS communication objects and functions
        """
        # 创建速度话题发布者 / Create velocity topic publisher
        self.cmd_vel_pub = self.create_publisher(Twist, "cmd_vel", 5)

        # 创建路网导航发布者 / Create road network navigation publisher
        self.road_net_nav_pub= self.create_publisher(PoseStamped, "road_net_nav", 10)

        #创建取消路网导航发布者 / Create road network navigation cancel publisher
        self.cancel_nav_pub= self.create_publisher(Bool, "road_net_nav_cancel", 5)

        # 创建语音唤醒订阅者 / Create voice wake-up subscriber
        self.wakeup_sub = self.create_subscription(Bool, "wakeup_event", self.wakeup_callback, 5)

        # 创建导航功能客户端，请求导航动作服务器 / Create navigation function client, request navigation action server
        self.navclient = ActionClient(self, NavigateToPose, "navigate_to_pose")

        # 创建动作执行服务器，用于接受动作列表，并执行动作 / Create action execution server to accept action lists and execute actions
        self._action_server = ActionServer(self, Rot, "/action_service", self.execute_callback)
        
        # 创建向dify-agent反馈动作执行结果的发布者 / Create a publisher to feedback action execution results to dify-agent
        self.llm_request_pub = self.create_publisher(LlmRequest, "llm_request_handler", 1)
        
        # 创建动作反馈订阅者,订阅外部程序的动作执行情况 / Create action feedback subscriber
        self.action_feedback_sub = self.create_subscription(String, "/action_feedback", self.action_feedback_callback, 5)
        
        # 创建图像订阅者 / Create image subscriber
        self.image_sub = self.create_subscription(Image, self.image_topic, self.image_callback, 1)
        # 创建机械臂角度发布者，用于发布arm6_joints，控制机械臂 / Create arm angle publisher to publish arm6_joints and control the arm
        self.TargetAngle_pub = self.create_publisher(ArmJoints, "arm6_joints", 100)
        # 创建关节角度发布者，用于发布arm_joint控制关节 / Create joint angle publisher to publish arm_joint and control joints
        self.SingleJoint_pub = self.create_publisher(ArmJoint, "arm_joint", 100)
        # 创建执行动作状态发布者 / Create action execution status publisher
        self.actionstatus_pub = self.create_publisher(String, "actionstatus", 3)
        # 创建发布者，发布 seewhat_handle 话题 / Create publisher to publish seewhat_handle topic
        self.seewhat_handle_pub = self.create_publisher(String, "seewhat_handle", 1)
        # 创建物体位置发布者，发布待夹取物体的坐标 / Create object position publisher to publish coordinates of objects to be grasped
        self.object_position_pub = self.create_publisher(
            Int16MultiArray, "corner_xy", 1
        )
        # 创建JoyCb话题发布者，启动KCF_Tracker_ALM节点测距的功能 / Create JoyCb topic publisher to enable distance measurement functionality of KCF_Tracker_ALM node
        self.joy_pub = self.create_publisher(Bool, "JoyState", 1)
        # 创建当前机械臂关节角发布者 / Create current arm joint angle publisher
        self.pub_cur_joints = self.create_publisher(CurJoints, "Curjoints", 1)
        # 创建KCF_Tracker_ALM重置发布者 / Create KCF_Tracker_ALM reset publisher
        self.reset_pub = self.create_publisher(Bool, "reset_flag", 1)
        # 创建TF订阅者 / Create TF subscriber
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        # 创建CvBridge对象 / Create CvBridge object
        self.bridge = CvBridge()

#--------------------------------------------callbacks functions--------------------------------------------#

    def execute_callback(self, goal_handle):
        """动作执行回调函数 action execution callback function"""
        actions = goal_handle.request.actions
        feedback_result = None
        if self.debug_mode: self.get_logger().info(self.actionlog.get_text("debug_log_1",actions=actions))
        self.action_runing = True
        for action in actions:
            if self.interrupt_event.is_set():
                break
            match = re.match(r"(\w+)\((.*)\)", action)
            action_name, args_str = match.groups()
            args = [arg.strip() for arg in args_str.split(",")] if args_str else []
            if not hasattr(self, action_name):
                self.get_logger().error(Fore.RED+f"action_service: {action} is invalid action, skip execution"+Fore.RESET)
            else:
                method = getattr(self, action_name)
                feedback_result = method(*args)

        if not self.interrupt_event.is_set():#向dify-agent反馈动作执行结果
            msg=LlmRequest()
            if feedback_result==False:
                #动作执行失败
                msg.llm_request=self.actionlog.get_text("action_feedback_2",action_name=actions)
                msg.robot_feedback=True
                self.llm_request_pub.publish(msg)
            elif feedback_result==True:
                #动作执行成功
                msg.llm_request=self.actionlog.get_text("action_feedback_1",action_name=actions)
                msg.robot_feedback=True
                self.llm_request_pub.publish(msg)
            elif feedback_result==None:
                #空操作不反馈
                if self.debug_mode: self.get_logger().info(self.actionlog.get_text("system_log_1"))

            if self.debug_mode: self.get_logger().info(msg.llm_request)

        if self.debug_mode: self.get_logger().info(self.actionlog.get_text("system_log_2"))
        self.action_runing = False
        self.interrupt_event.clear()
        goal_handle.succeed()
        result = Rot.Result()
        result.success = True
        return result


    def image_callback(self, msg:Image): 
        ''' 图像回调函数 / Image callback function '''
        self.image_msg = msg

    def action_feedback_callback(self, msg:String):
        ''' 外部动作反馈回调函数 / External action feedback callback function '''
        if msg.data =="follow_line_finish":
            if not self.follow_line_clear_future.done():
                self.follow_line_clear_future.set_result(msg)
        elif msg.data =="road_net_nav_succeeded":
            if not self.road_net_nav_future.done():
                self.road_net_nav_future.set_result(msg)
        elif msg.data =="road_net_nav_failed":
            if not self.road_net_nav_future.done():
                self.road_net_nav_future.set_result(msg)
        if msg.data in ["apriltag_sort_done", "apriltag_sort_failed"]:
            if not self.apriltag_sort_future.done():
                self.apriltag_sort_future.set_result(msg)
        elif msg.data in ["apriltag_remove_higher_done","apriltag_remove_higher_failed"]:
            if not self.apriltag_remove_higher_future.done():
                self.apriltag_remove_higher_future.set_result(msg)
        elif msg.data == "grasp_obj_done":
            if not self.grasp_obj_future.done():
                self.grasp_obj_future.set_result(msg)
        elif msg.data == "color_remove_higher_done":
            if not self.color_remove_higher_future.done():
                self.color_remove_higher_future.set_result(msg)
        elif msg.data == "follow_line_clear_future_done":
            if not self.follow_line_clear_future.done():
                self.follow_line_clear_future.set_result(msg)

    def wakeup_callback(self, msg:Bool):
        ''' 语音唤醒回调函数 / Voice wake-up callback function '''
        if self.debug_mode: self.get_logger().info("wakeup received")
        if msg.data and self.action_runing:
            self.interrupt_event.set()
            if self.debug_mode: self.get_logger().info(self.actionlog.get_text("interrupt_1"))#调试模式下输出信息 / Output information in debug mode


    def get_current_pose(self):
        """
        获取当前在全局地图坐标系下的位置 /Get the current position in the global map coordinate system
        """
        transform = self.tf_buffer.lookup_transform("map", "base_footprint", rclpy.time.Time())
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.pose.position.x = transform.transform.translation.x
        pose.pose.position.y = transform.transform.translation.y
        pose.pose.position.z = 0.0
        pose.pose.orientation = transform.transform.rotation
        self.navpose_dict["zero"] = pose
        position = pose.pose.position
        orientation = pose.pose.orientation
        self.get_logger().info(f"Recorded Pose: Position: x={position.x}, y={position.y},z={position.z},Orientation: x={orientation.x}, y={orientation.y}, z={orientation.z}, w={orientation.w}")
        return True

#-------------------------------------movement related functions-------------------------------------#
    def set_cmdvel(self, linear_x:str, linear_y:str, angular_z:str, duration:str)->None:
        ''' 发布cmd_vel速度指令 / Publish cmd_vel velocity command '''
        linear_x = float(linear_x)
        linear_y = float(linear_y)
        angular_z = float(angular_z)
        duration = float(duration)
        twist = Twist()
        twist.linear.x = linear_x
        twist.linear.y = linear_y
        twist.angular.z = angular_z
        self._execute_action(twist, durationtime=duration)
        self.stop()
        return True
    
    def _execute_action(self, twist, durationtime:float=3.0):
        '''执行动作的内部函数 / Internal function to execute action '''
        start_time = time.time()
        while (time.time() - start_time) < durationtime:
            self.cmd_vel_pub.publish(twist)
            time.sleep(0.1)
    def drift(self):
        """漂移动作  Drift action """
        twist = Twist()
        twist.linear.x = 0.0
        twist.linear.y = 0.5
        twist.angular.z = 1.0
        self._execute_action(twist, durationtime=4.0)
        return True

    def stop(self):
        '''停止运动 / Stop movement '''
        twist = Twist()
        twist.linear.x = 0.0
        twist.linear.y = 0.0
        twist.angular.z = 0.0
        self.cmd_vel_pub.publish(twist)

    def move_left(self, angle, angular_speed):
        '''左转x度 / Turn left x degrees '''
        angle = float(angle)
        angular_speed = float(angular_speed)
        angle_rad = math.radians(angle)  #  Convert degrees to radians
        duration = abs(angle_rad / angular_speed)
        angular_speed = abs(angular_speed)
        twist = Twist()
        twist.linear.x = 0.0
        twist.angular.z = angular_speed
        self._execute_action(twist, duration)
        self.stop()
        return True

    def move_right(self, angle, angular_speed):
        '''右转x度 / Turn right x degrees '''
        angle = float(angle)
        angular_speed = float(angular_speed)
        angle_rad = math.radians(angle)  #  Convert degrees to radians
        duration = abs(angle_rad / angular_speed)
        angular_speed = -abs(angular_speed)
        twist = Twist()
        twist.linear.x = 0.0
        twist.angular.z = angular_speed
        self._execute_action(twist, duration)
        self.stop()
        return True
    
    def dance(self):
        '''机器人跳舞 / Robot dance '''
        thread = Thread(target=self.arm_dance)
        thread.start()
        actions = [
            {"linear_x": 0.6, "linear_y": 0.0, "angular_z": 0.0, "durationtime": 1.5},
            {"linear_x": -0.4, "linear_y": 0.0, "angular_z": 0.0, "durationtime": 1.0},
            {"linear_x": 0.0, "linear_y": 0.3, "angular_z": 0.0, "durationtime": 1.0},
            {"linear_x": 0.0, "linear_y": -0.3, "angular_z": 0.0, "durationtime": 1.0},
            {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.6, "durationtime": 3.0},
            {"linear_x": 0.0, "linear_y": 0.0, "angular_z": -0.6, "durationtime": 3.0},
        ]
        for action in actions:
            if self.interrupt_event.is_set():
                self.pubSix_Arm(self.init_joints)
                self.stop()
                return None
            twist = Twist()
            twist.linear.x = action["linear_x"]
            twist.linear.y = action["linear_y"]
            twist.angular.z = action["angular_z"]
            self._execute_action(twist, durationtime=action["durationtime"])

        thread.join(timeout=5.0)
        self.stop()
        self.pubSix_Arm(self.init_joints)
        return True

#-------------------------------------navigation related functions-------------------------------------#
    def navigation(self, point_name):
        """
        从navpose_dict字典中获取目标点坐标.并导航到目标点
        """
        if self.enable_route_nav:
            # 使用路网导航 / Use road network navigation
            if self.__road_net_navigation(point_name):
                return True
            else:
                return None
        else:
            # 使用普通导航 / Use normal navigation
            if self.__normal_navigation(point_name):
                return True
            else:
                return None

    def __normal_navigation(self, point_name)->None:
        '''常规导航功能 / Normal navigation function '''
        self.navigation_finish_flag = False
        self.goal_handle = None
        self.result = None
        self.res = None
        point_name = point_name.strip("'\"")
        if point_name not in self.navpose_dict:
            self.get_logger().error(f"Target point '{point_name}' does not exist in the navigation dictionary." )
            return None

        if self.first_record:
            # 出发前记录当前在全局地图中的坐标(只有在每个任务周期的第一次执行时才会记录)/ before starting a new task, record the current pose in the global map
            transform = self.tf_buffer.lookup_transform(
                "map", "base_footprint", rclpy.time.Time()
            )
            pose = PoseStamped()
            pose.header.frame_id = "map"
            pose.pose.position.x = transform.transform.translation.x
            pose.pose.position.y = transform.transform.translation.y
            pose.pose.position.z = 0.0
            pose.pose.orientation = transform.transform.rotation
            self.navpose_dict["zero"] = pose
            self.road_net_dict["zero"] = pose
            self.first_record = False

        # 获取目标点坐标 /get_target_pose
        target_pose = self.navpose_dict.get(point_name)
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = target_pose
        send_goal_future = self.navclient.send_goal_async(goal_msg)

        def goal_response_callback(future):
            self.goal_handle = future.result()
            if not self.goal_handle or not self.goal_handle.accepted:
                self.get_logger().error("Goal was rejected!")
                return None

            get_result_future = self.goal_handle.get_result_async()

            def result_callback(future_result):
                self.result = future_result.result()
                self.navigation_finish_flag = True
                if self.result.status == 4:
                    self.get_logger().info("Navigation finished!")
                    self.res= True
                else:
                    self.get_logger().info(f"Navigation failed with status: {self.result.status}")
                    self.res= False

            get_result_future.add_done_callback(result_callback)
        send_goal_future.add_done_callback(goal_response_callback)

        while not self.navigation_finish_flag:
            if self.interrupt_event.is_set() :
                self.navclient._cancel_goal(self.goal_handle)
                return None
            time.sleep(0.1)
        self.stop()
        return self.res


    def __road_net_navigation(self, point_name):
        '''路网导航功能 / Road network navigation function '''
        self.road_net_nav_future = Future()
        goal_msg=PoseStamped()
        #构造终点
        point_name = point_name.strip("'\"")
        goal_msg = self.road_net_dict.get(point_name)
        self.road_net_nav_pub.publish(goal_msg)

        while not self.road_net_nav_future.done():
            if self.interrupt_event.is_set():
                self.cancel_nav_pub.publish(Bool(data=True))
                self.get_logger().info(Fore.GREEN+"Road net navigation None"+Fore.RESET)
                return None
            time.sleep(0.1)
        result = self.road_net_nav_future.result()
        if result.data =="road_net_nav_succeeded":
            self.get_logger().info(Fore.GREEN+"Road net navigation succeeded"+Fore.RESET)
            return True
        else: 
            return False

#-------------------------------------arms related functions-------------------------------------#
    def __arm_init(self):
        """
        初始化机械臂抓取功能 /initialize the grasping function of the robotic arm
        """
        # 机械臂状态变量/Robotic arm status variable
        self.up_joints = [90, 90, 90, 90, 90, 90]
        self.down_joints = [90, 0, 90, 90, 90, 90]
        self.detect_joints = [90, 120, 0, 0, 90, 90]
        self.init_joints = [
            90,
            130,
            0,
            5,
            90,
            0,
        ]
        # 机械臂初始姿态/robot arm initial pose	
        self.putsown_joints = [
            90,
            10,
            50,
            50,
            90,
            135,
        ]  # 机械臂放下姿态/robot arm putdown pose
        start_time = time.time()
        while not self.TargetAngle_pub.get_subscription_count():
            if time.time() - start_time > 60:  
                self.get_logger().error("Arm initialization timeout! Failed to connect to arm controller.")
                break
            self.pubSix_Arm(self.init_joints)
            time.sleep(0.5)
        self.pubSix_Arm(self.init_joints)



    def pubSix_Arm(self, joints, id=6, angle=180.0, runtime=2000):
        ''' 发布机械臂六关节角度指令  Publish robotic arm six joint angle command '''
        arm_joint = ArmJoints()
        arm_joint.joint1 = joints[0]
        arm_joint.joint2 = joints[1]
        arm_joint.joint3 = joints[2]
        arm_joint.joint4 = joints[3]
        arm_joint.joint5 = joints[4]
        arm_joint.joint6 = joints[5]
        arm_joint.time = runtime
        self.TargetAngle_pub.publish(arm_joint)

    def pubSingle_Arm(self, joint_id=6, joint_angle=180.0, runtime=800):
        arm_joint = ArmJoint()
        arm_joint.joint = int(joint_angle)
        arm_joint.id = int(joint_id)
        arm_joint.time = runtime
        self.SingleJoint_pub.publish(arm_joint)

    def arm_up(self): 
        ''' 机械臂向上 Robotic arm up '''
        self.pubSix_Arm(self.up_joints)
        time.sleep(1.0)
        return True

    def arm_down(self): 
        '''机械臂向下  Robotic arm down '''
        self.pubSix_Arm(self.down_joints)
        time.sleep(1.0)
        return True

    def arm_shake(self):  # 机械臂摇头
        for i in range(3):
            if self.interrupt_event.is_set():
                self.pubSix_Arm(self.init_joints)
                return None
            tar_arm_joint = [140, 130, 0, 5, 90, 0]
            self.pubSix_Arm(tar_arm_joint)
            time.sleep(1.0)
            tar_arm_joint = [40, 130, 0, 5, 90, 0]
            self.pubSix_Arm(tar_arm_joint)
            time.sleep(1.0)

        self.pubSix_Arm(self.init_joints)
        return True

    def putdown(self):
        self.pubSix_Arm(self.putsown_joints)  # 机械臂下放
        time.sleep(4)
        self.pubSingle_Arm(6, 30, 1000)  # 机械臂打开夹抓，放下物品
        time.sleep(3)
        self.pubSix_Arm(self.init_joints)  # 机械臂收回
        return True

    def arm_nod(self):  # 机械臂点头
        for i in range(3):
            if self.interrupt_event.is_set():
                self.pubSix_Arm(self.init_joints)
                return None
            tar_arm_joint = [90, 130, 0, 95, 90, 0]
            self.pubSix_Arm(tar_arm_joint)
            time.sleep(1.0)
            self.pubSix_Arm(self.init_joints)
            time.sleep(1.0)
        self.pubSix_Arm(self.init_joints)
        return True

    def arm_applaud(self):  # 机械臂鼓掌
        for i in range(3):
            if self.interrupt_event.is_set():
                self.pubSix_Arm(self.init_joints)
                return None
            tar_arm_joint = [90, 145, 0, 71, 90, 31]
            self.pubSix_Arm(tar_arm_joint)
            time.sleep(1.0)
            tar_arm_joint = [90, 145, 0, 71, 90, 168]
            self.pubSix_Arm(tar_arm_joint)
            time.sleep(1.0)
        self.pubSix_Arm(self.init_joints)
        return True

    def arm_dance(self)->bool:
        '''机械臂跳舞 / Robotic arm dance '''
        dance_moves = [
            [90, 90, 90, 90, 90, 90],
            [90, 60, 120, 60, 90, 90],
            [90, 45, 135, 45, 90, 90],
            [90, 60, 120, 60, 90, 90],
            [90, 90, 90, 90, 90, 90],
            [90, 100, 80, 80, 90, 90],
            [90, 120, 60, 60, 90, 90],
            [90, 135, 45, 45, 90, 90],
            [90, 90, 90, 90, 90, 90],
            [90, 90, 90, 20, 90, 150],
            [90, 90, 90, 90, 90, 90],
            [90, 90, 90, 20, 90, 150],
        ]
        for joints in dance_moves:
            if self.interrupt_event.is_set():
                self.pubSix_Arm(self.init_joints)
                self.stop()
                return None
            self.pubSix_Arm(joints)
            time.sleep(1.0)
        self.pubSix_Arm(self.init_joints)
        return True

#-------------------------------------external related functions-------------------------------------#
    def track(self, x1, y1, x2, y2):
        """追踪物体x1,y1,x2,y2: 物体外边框坐标 """
        cmd_1=['ros2', 'run', 'largemodel_arm', 'KCF_track']
        cmd_2=['ros2', 'run', 'M3Pro_KCF', 'ALM_KCF_Tracker_Node']
        self.track_process_1=subprocess.Popen(cmd_1)
        self.track_process_2=subprocess.Popen(cmd_2)
        time.sleep(5.0) #等待ALM_KCF_Tracker_Node启动完成

        x1 = int(x1)
        y1 = int(y1)
        x2 = int(x2)
        y2 = int(y2)
        while not self.object_position_pub.get_subscription_count():
            time.sleep(0.5)
        self.object_position_pub.publish(Int16MultiArray(data=[x1, y1, x2, y2]))
        while True:
            if self.interrupt_event.is_set():
                kill_process_tree(self.track_process_1.pid)
                kill_process_tree(self.track_process_2.pid)
                self.pubSix_Arm(self.init_joints)
                return None
            time.sleep(0.1)


    def grasp_obj(self, x1, y1, x2, y2) -> None:
        """grasp_obj: 夹取物体 x1,y1,x2,y2: 物体外边框坐标 """
        def __reset_grasp_obj():
            kill_process_tree(self.grasp_obj_process_1.pid)
            kill_process_tree(self.grasp_obj_process_2.pid)
            kill_process_tree(self.grasp_obj_process_3.pid)
            self.grasp_obj_future = Future() 
            
        cmd_1=['ros2', 'run', 'largemodel_arm', 'grasp_desktop']
        cmd_2=['ros2', 'run', 'largemodel_arm', 'KCF_follow']
        cmd_3=['ros2', 'run', 'M3Pro_KCF', 'ALM_KCF_Tracker_Node']

        self.grasp_obj_process_1=subprocess.Popen(cmd_1)
        time.sleep(5.0) #等待grasp_desktop启动完成
        self.grasp_obj_process_2=subprocess.Popen(cmd_2)
        self.grasp_obj_process_3=subprocess.Popen(cmd_3)
        x1 = int(x1)
        y1 = int(y1)
        x2 = int(x2)
        y2 = int(y2)
        while not self.object_position_pub.get_subscription_count():
            time.sleep(0.5)
        self.object_position_pub.publish(Int16MultiArray(data=[x1, y1, x2, y2]))

        while not self.grasp_obj_future.done():
            if self.interrupt_event.is_set():
                __reset_grasp_obj()
                self.pubSix_Arm(self.init_joints)
                return None
            time.sleep(0.1)

        result = self.grasp_obj_future.result()
        if not self.interrupt_event.is_set():
            if result.data == "grasp_obj_done":
                res = True
            else:
                res = False

        __reset_grasp_obj()
        if self.interrupt_event.is_set():
            time.sleep(0.5)
            self.pubSix_Arm(self.init_joints)  # 机械臂收回
        return res

    def apriltag_sort(self, target_id):
        """apriltag_sort 夹取指定机器码 """
        def __reset_apriltag_sort():
            kill_process_tree(self.apriltag_sort_process_1.pid)
            kill_process_tree(self.apriltag_sort_process_2.pid)
            self.apriltag_sort_future= Future() 

        target_idf = float(target_id)
        cmd_1=['ros2', 'run', 'largemodel_arm', 'grasp_desktop_apritag']
        cmd_2=['ros2', 'run', 'largemodel_arm', 'apriltag_sort','--ros-args','-p',f'target_id:={target_idf:.1f}']
        self.apriltag_sort_process_1=subprocess.Popen(cmd_1)
        self.apriltag_sort_process_2=subprocess.Popen(cmd_2)

        while not self.apriltag_sort_future.done():
            if self.interrupt_event.is_set():
                __reset_apriltag_sort()
                self.pubSix_Arm(self.init_joints)
                return None
            time.sleep(0.1)

        result = self.apriltag_sort_future.result()
        if not self.interrupt_event.is_set():
            if result.data == "apriltag_sort_done":
                res=True
            elif result.data == "apriltag_sort_failed":
                res= False
        __reset_apriltag_sort()
        return res

    def apriltag_remove_higher(self, target_high): 
        '''移除指定高度的机器码'''
        def __reset_apriltag_remove_higher():
            kill_process_tree(self.apriltag_remove_higher_process_1.pid)
            kill_process_tree(self.apriltag_remove_higher_process_2.pid)
            self.apriltag_remove_higher_future = Future()  
        target_highf = float(target_high) / 100
        cmd_1=['ros2', 'run', 'largemodel_arm', 'grasp_desktop_remove']
        cmd_2=['ros2', 'run', 'largemodel_arm', 'apriltag_remove_higher','--ros-args','-p',f'target_high:={target_highf:.2f}']
        self.apriltag_remove_higher_process_1=subprocess.Popen(cmd_1)
        self.apriltag_remove_higher_process_2=subprocess.Popen(cmd_2)

        while not self.apriltag_remove_higher_future.done():
            if self.interrupt_event.is_set():
                __reset_apriltag_remove_higher()
                self.stop()
                self.pubSix_Arm(self.init_joints)
                return None
            time.sleep(0.1)

        result = self.apriltag_remove_higher_future.result()
        if not self.interrupt_event.is_set():
            if result.data == "apriltag_remove_higher_done":
                res=True
            elif result.data == "apriltag_remove_higher_failed":
                res= False

        __reset_apriltag_remove_higher()
        self.pubSix_Arm(self.init_joints)
        return res


    def color_remove_higher(self, color, target_high):
        '''移除指定颜色和高度的物体 / Remove objects of specified color and height '''
        def __reset_color_remove_higher():
            kill_process_tree(self.color_remove_higher_process_1.pid)
            kill_process_tree(self.color_remove_higher_process_2.pid)
            self.color_remove_higher_future = Future() 
        
        arm_joints = [90, 110, 0, 0, 90, 0]
        self.pubSix_Arm(arm_joints)
        color = color.strip("'\"")  # 去掉单引号和双引号
        target_highf = float(target_high) / 100
        if color == "red":
            target_color = float(1)
        elif color == "green":
            target_color = float(2)
        elif color == "blue":
            target_color = float(3)
        elif color == "yellow":
            target_color = float(4)
        else:
            self.get_logger().info(
                "Fatal ERROR:Incorrect color input,Does the AI output not meet expectations?"
            )
            return False
        
        cmd_1=['ros2', 'run', 'largemodel_arm', 'grasp_desktop_remove_color']
        cmd_2=['ros2', 'run', 'largemodel_arm', 'color_remove_higher','--ros-args','-p',f'target_high:={target_highf:.2f}','-p',f'target_color:={target_color:.1f}']
        self.color_remove_higher_process_1=subprocess.Popen(cmd_1)
        self.color_remove_higher_process_2=subprocess.Popen(cmd_2)


        while not self.color_remove_higher_future.done():
            if self.interrupt_event.is_set():
                __reset_color_remove_higher()
                self.stop()
                self.pubSix_Arm(self.init_joints)
                return None
            time.sleep(0.1)

        result = self.color_remove_higher_future.result()
        if not self.interrupt_event.is_set():
            if result.data == "color_remove_higher_done":
                res=True
            else:
                res= False

        __reset_color_remove_higher()
        self.pubSix_Arm(self.init_joints)
        return res

    def follow_line_clear(self) -> None:
        '''巡线清除障碍物 / Follow line to clear obstacles'''
        def __reset_follow_line_clear():
            kill_process_tree(self.follow_line_clear_process_1.pid)
            kill_process_tree(self.follow_line_clear_process_2.pid)
            self.follow_line_clear_future = Future()

        cmd_1=['ros2', 'run', 'largemodel_arm', 'grasp_desktop_remove']
        cmd_2=['ros2', 'run', 'largemodel_arm', 'follow_line','--ros-args','-p','start_follow:=True']
        self.follow_line_clear_process_1=subprocess.Popen(cmd_1)
        self.follow_line_clear_process_2=subprocess.Popen(cmd_2)

        while not self.follow_line_clear_future.done():
            if self.interrupt_event.is_set():
                __reset_follow_line_clear()
                self.stop()
                self.pubSix_Arm(self.init_joints)
                return None
            time.sleep(0.1)

        if not self.interrupt_event.is_set():
            if self.follow_line_clear_future.result() is not None:
                res=True

        __reset_follow_line_clear()
        self.pubSix_Arm(self.init_joints)
        return res
#-------------------------------------other functions-------------------------------------#

    def seewhat(self):
        """
        保存当前视角图像,反馈给dify-agent
        """
        self.save_single_image()
        msg=LlmRequest()
        msg.llm_request=self.actionlog.get_text("image_feedback")
        msg.robot_feedback=True
        self.llm_request_pub.publish(msg)
        return None

    def save_single_image(self):
        """保存一张图片 / Save a single image"""
        cv_image = self.bridge.imgmsg_to_cv2(self.image_msg, "bgr8")
        cv2.imwrite(self.image_cache_path, cv_image)
        time.sleep(0.05)
        display_thread = threading.Thread(target=self.__display_saved_image)
        display_thread.start()


    def __display_saved_image(self):
        """
        显示已保存的图片4秒后关闭窗口 / Display the saved image for 4 seconds before closing the window
        """
        try:
            img = cv2.imread(self.image_cache_path)
            if img is not None:
                cv2.imshow("Saved Image", img)
                cv2.waitKey(4000)  # 等待4秒 / Wait for 4 seconds
                cv2.destroyAllWindows()
            else:
                self.get_logger().error(
                    "Failed to load saved image for display."
                )  # 加载保存的图像以供显示失败...
        except Exception as e:
            self.get_logger().error(f"Error displaying image: {e}")  # 显示图像时出错...
    def finish(self):
        """空操作,不反馈消息，用于结束反馈/No operation, no feedback message, used to end feedback"""
        return None
    def finish_dialogue(self):
        '''结束任务周期,通知model_service重置会话/End the task cycle and notify model_service to reset the session'''
        self.first_record = True  # 重置导航记录标志位 # Reset navigation record flag
        self.interrupt_event.clear()  # 清除打断标志  # Clear interrupt flag
        self.pubSix_Arm(self.init_joints)
        self.stop()
        msg=LlmRequest()
        msg.llm_request="finish"
        msg.robot_feedback=True
        self.llm_request_pub.publish(msg)
        return None

def main(args=None):
    rclpy.init(args=args)
    action_service = ActionController()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(action_service)
    executor.spin()
    action_service.destroy_node()
    executor.shutdown()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
