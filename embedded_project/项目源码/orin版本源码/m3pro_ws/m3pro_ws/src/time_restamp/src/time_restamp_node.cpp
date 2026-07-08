#include <memory>
#include <string>
#include "rclcpp/rclcpp.hpp"
#include "nav_msgs/msg/odometry.hpp"
#include "sensor_msgs/msg/imu.hpp"
#include "sensor_msgs/msg/laser_scan.hpp"

template<typename MsgT>
class Restamper
{
public:
  Restamper(rclcpp::Node * node,
            const std::string & in_topic,
            const std::string & out_topic,
            const rclcpp::QoS & sub_qos,
            const rclcpp::QoS & pub_qos)
  : node_(node)
  {
    pub_ = node->create_publisher<MsgT>(out_topic, pub_qos);
    sub_ = node->create_subscription<MsgT>(
      in_topic, sub_qos,
      [this](typename MsgT::SharedPtr msg) {
        msg->header.stamp = node_->now();
        pub_->publish(*msg);
      });
  }
private:
  rclcpp::Node * node_;
  typename rclcpp::Publisher<MsgT>::SharedPtr pub_;
  typename rclcpp::Subscription<MsgT>::SharedPtr sub_;
};

class TimeRestampNode : public rclcpp::Node
{
public:
  TimeRestampNode() : Node("time_restamp_node")
  {
    auto be  = rclcpp::QoS(rclcpp::KeepLast(10)).best_effort();
    auto rel = rclcpp::QoS(rclcpp::KeepLast(10)).reliable();

    odom_  = std::make_unique<Restamper<nav_msgs::msg::Odometry>>(
      this, "/odom_raw",     "/odom_raw_restamped",     be, rel);
    imu_   = std::make_unique<Restamper<sensor_msgs::msg::Imu>>(
      this, "/imu/data_raw", "/imu/data_raw_restamped", be, rel);
    scan0_ = std::make_unique<Restamper<sensor_msgs::msg::LaserScan>>(
      this, "/scan0",        "/scan0_restamped",         be, rel);
    scan1_ = std::make_unique<Restamper<sensor_msgs::msg::LaserScan>>(
      this, "/scan1",        "/scan1_restamped",         be, rel);

    RCLCPP_INFO(get_logger(),
      "time_restamp_node started: odom_raw/imu_raw/scan0/scan1 -> *_restamped");
  }
private:
  std::unique_ptr<Restamper<nav_msgs::msg::Odometry>>     odom_;
  std::unique_ptr<Restamper<sensor_msgs::msg::Imu>>       imu_;
  std::unique_ptr<Restamper<sensor_msgs::msg::LaserScan>> scan0_, scan1_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<TimeRestampNode>());
  rclcpp::shutdown();
  return 0;
}
