import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, Point,Quaternion
from tf2_ros import TransformException
from tf2_ros.buffer import Buffer
from tf2_ros.transform_listener import TransformListener
from math import copysign, sqrt, pow,radians,degrees,atan2,asin
from rclpy.duration import Duration
from tf2_ros import LookupException, ConnectivityException, ExtrapolationException
import PyKDL
from math import pi
import time

class Calibrateangular(Node):
    def __init__(self,name):
        super().__init__(name)
        #create a spublisher
        self.cmd_vel = self.create_publisher(Twist,"/cmd_vel",5)
        
        self.declare_parameter('test_angle',360.0)
        self.test_angle = self.get_parameter('test_angle').get_parameter_value().double_value
        
        
        self.declare_parameter('speed',0.5)
        self.speed = self.get_parameter('speed').get_parameter_value().double_value
        
        self.declare_parameter('tolerance',0.05)
        self.tolerance = self.get_parameter('tolerance').get_parameter_value().double_value
        
        self.declare_parameter('odom_angular_scale_correction',1.0)
        self.odom_angular_scale_correction = self.get_parameter('odom_angular_scale_correction').get_parameter_value().double_value
        
        self.declare_parameter('start_test',False)
            
        self.declare_parameter('base_frame','base_footprint')
        self.base_frame = self.get_parameter('base_frame').get_parameter_value().string_value
        
        self.declare_parameter('odom_frame','odom')
        self.odom_frame = self.get_parameter('odom_frame').get_parameter_value().string_value
        
        time.sleep(3)

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        self.position = Point()
        self.x_start = self.position.x
        self.y_start = self.position.y
        self.first_angle = 0

        self.get_logger().info("finish init work")  
        self.last_angle = 0

        self.reverse = 1
        self.turn_angle = 0
        self.delta_angle  = 0
        self.timer = self.create_timer(0.1, self.on_timer)
        self.count=0
		
    def on_timer(self):
        self.count+=1
        self.start_test = self.get_parameter('start_test').get_parameter_value().bool_value
        self.odom_angular_scale_correction = self.get_parameter('odom_angular_scale_correction').get_parameter_value().double_value
        self.test_angle = self.get_parameter('test_angle').get_parameter_value().double_value
        self.speed = self.get_parameter('speed').get_parameter_value().double_value
        self.tolerance=self.get_parameter('tolerance').get_parameter_value().double_value
        self.test_angle=radians(self.test_angle)

        move_cmd = Twist()

        if self.start_test:
            original_angle = self.get_odom_angle()
            self.test_angle = copysign(self.test_angle, self.reverse)

            delta_angle = self.odom_angular_scale_correction * self.normalize_angle(original_angle - self.last_angle)
            
            self.turn_angle += delta_angle
            error = self.test_angle - self.turn_angle

            self.get_logger().info(f"turn_angle: {self.turn_angle}, error: {error}")
            self.last_angle = original_angle
            if abs(error) > self.tolerance and self.start_test:
                move_cmd.angular.z = copysign(self.speed, error)
            else:
                self.get_logger().info(f"turn_angle: {self.turn_angle}, error: {error}")
                self.turn_angle = 0.0
                self.start_test  = rclpy.parameter.Parameter('start_test', rclpy.Parameter.Type.BOOL, False)
                all_new_parameters = [self.start_test]
                self.set_parameters(all_new_parameters)
                self.last_angle = 0
                self.get_logger().info('\033[1;32m%s\033[0m' % 'done')

        self.cmd_vel.publish(move_cmd)          

    def get_odom_angle(self):
        try:
            trans = self.tf_buffer.lookup_transform(self.odom_frame, self.base_frame, rclpy.time.Time(), timeout=rclpy.duration.Duration(seconds=1))
            return self.qua2rpy(trans.transform.rotation)[2]
        except (LookupException, ConnectivityException, ExtrapolationException):
            self.get_logger().info('\033[1;32m%s\033[0m' % 'TF Exception')
            return
    def normalize_angle(self,angle):
        res = angle
        while res > pi:
            res -= 2.0 * pi
        while res < -pi:
            res += 2.0 * pi
        return res
    
    def qua2rpy(self,quat):
        # 四元数转欧拉角，四元数格式为ros
        x = quat.x
        y = quat.y
        z = quat.z
        w = quat.w

        roll = atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
        pitch = asin(2 * (w * y - x * z))
        yaw = atan2(2 * (w * z + x * y), 1 - 2 * (z * z + y * y))

        # 将角度转换到0-360范围 
        if roll < 0:
            roll += 2 * pi
        
        if pitch < 0:
            pitch += 2 * pi
        
        if yaw < 0:
            yaw += 2 * pi
        
        return roll, pitch, yaw
def main():
    rclpy.init()
    class_calibrateangular = Calibrateangular("calibrate_angular")
    try:
        rclpy.spin(class_calibrateangular)
    except KeyboardInterrupt:
        pass
    finally:
        class_calibrateangular.cmd_vel.publish(Twist())
        class_calibrateangular.destroy_node()
        rclpy.shutdown()
    
if __name__ == "__main__":
    main()