import json
import sys

import rclpy
from strawberry_interfaces.srv import InferStrawberryImage


def main():
    image_path = sys.argv[1]
    task_id = sys.argv[2] if len(sys.argv) > 2 else "test"
    region = sys.argv[3] if len(sys.argv) > 3 else "A"

    rclpy.init()
    node = rclpy.create_node("call_infer_service_once")
    client = node.create_client(InferStrawberryImage, "/infer_strawberry_image")
    if not client.wait_for_service(timeout_sec=10.0):
        raise SystemExit("service unavailable")

    req = InferStrawberryImage.Request()
    req.image_path = image_path
    req.task_id = task_id
    req.region = region
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=60.0)
    resp = future.result()
    if resp is None:
        raise SystemExit("no response")

    print("success:", resp.success)
    print("message:", resp.message)
    try:
        print(json.dumps(json.loads(resp.result_json), ensure_ascii=False, indent=2))
    except Exception:
        print(resp.result_json)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
