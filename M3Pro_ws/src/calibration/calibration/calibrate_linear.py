import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, Point
from tf2_ros import TransformException
from tf2_ros.buffer import Buffer
from tf2_ros.transform_listener import TransformListener
from math import copysign, sqrt, pow
from tf2_ros import LookupException, ConnectivityException, ExtrapolationException
import time

class CalibrateLinear(Node):
    def __init__(self,name):
        super().__init__(name)
        #create a spublisher
        self.cmd_vel = self.create_publisher(Twist,"/cmd_vel",5)
        #declare_parameter
        self.declare_parameter('rate',20.0)
        self.rate = self.get_parameter('rate').get_parameter_value().double_value
        
        self.declare_parameter('test_distance',1.0)
        self.test_distance = self.get_parameter('test_distance').get_parameter_value().double_value
        
        self.declare_parameter('speed',0.3)
        self.speed = self.get_parameter('speed').get_parameter_value().double_value
        
        self.declare_parameter('tolerance',0.03)
        self.tolerance = self.get_parameter('tolerance').get_parameter_value().double_value
        
        self.declare_parameter('odom_linear_scale_correction',1.0)
        self.odom_linear_scale_correction = self.get_parameter('odom_linear_scale_correction').get_parameter_value().double_value
        
        self.declare_parameter('start_test',False)
        
        self.declare_parameter('direction',True)
        self.direction = self.get_parameter('direction').get_parameter_value().bool_value
        
        self.declare_parameter('base_frame','base_footprint')
        self.base_frame = self.get_parameter('base_frame').get_parameter_value().string_value
        
        self.declare_parameter('odom_frame','odom')
        self.odom_frame = self.get_parameter('odom_frame').get_parameter_value().string_value

        time.sleep(3)
        
        #init the tf listener
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        self.position = Point()
        self.x_start = self.position.x
        self.y_start = self.position.y
 

        print ("finish init work")

        self.count=0
        self.timer = self.create_timer(0.1, self.on_timer)
        
    def on_timer(self):
        self.count+=1
        move_cmd = Twist()
        #self.get_param()
        self.start_test = self.get_parameter('start_test').get_parameter_value().bool_value
        self.odom_linear_scale_correction = self.get_parameter('odom_linear_scale_correction').get_parameter_value().double_value
        self.direction = self.get_parameter('direction').get_parameter_value().bool_value
        self.test_distance = self.get_parameter('test_distance').get_parameter_value().double_value
        self.tolerance = self.get_parameter('tolerance').get_parameter_value().double_value
        self.speed = self.get_parameter('speed').get_parameter_value().double_value
        if self.start_test:
            '''trans = self.tf_buffer.lookup_transform(
                        self.odom_frame,
                        self.base_frame,
                        now,
                        )'''
            result=self.get_position()
            self.position.x = result.transform.translation.x
            self.position.y = result.transform.translation.y

            distance = sqrt(pow((self.position.x - self.x_start), 2) +
                                pow((self.position.y - self.y_start), 2))
            distance *= self.odom_linear_scale_correction
            error = distance - self.test_distance #实际距离矫正距离-理论距离
            if self.count %5==0:
                self.get_logger().info(f"distance: {distance},error: {error}")

            if not self.start_test or abs(error) < self.tolerance:
                self.start_test  = rclpy.parameter.Parameter('start_test',rclpy.Parameter.Type.BOOL,False)
                all_new_parameters = [self.start_test]
                self.set_parameters(all_new_parameters)
                self.get_logger().info('\033[1;32m%s\033[0m' % 'done')
                self.get_logger().info(f"distance: {distance},error: {error}")

            else:
                if self.direction:
                    move_cmd.linear.x = copysign(self.speed, -1 * error)
                else:
                    move_cmd.linear.y = copysign(self.speed, -1 * error)
            self.cmd_vel.publish(move_cmd)
        else:
            result=self.get_position()
            self.x_start = result.transform.translation.x
            self.y_start = result.transform.translation.y
            # if self.count %30==0:
            #     self.get_logger().info(f"x_start: {self.x_start},y_start: {self.y_start}") 
            self.cmd_vel.publish(Twist())   


    def get_position(self):
         try:
            now = rclpy.time.Time()
            transform = self.tf_buffer.lookup_transform(
                self.base_frame,
                self.odom_frame,
                now,
                timeout=rclpy.duration.Duration(seconds=1.0))            
            return transform
                 
         except (LookupException, ConnectivityException, ExtrapolationException):
            self.get_logger().info('transform not ready')
            return
         
def main():
    rclpy.init()
    class_calibratelinear = CalibrateLinear("calibrate_linear")
    try:
        rclpy.spin(class_calibratelinear)
    except KeyboardInterrupt:
        pass
    finally:
        class_calibratelinear.cmd_vel.publish(Twist())
        class_calibratelinear.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
