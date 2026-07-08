import json
from pathlib import Path

import rclpy
from strawberry_interfaces.srv import InferStrawberryImage


def main():
    sample_dir = Path("/root/strawberry_models/Disease/samples")
    rclpy.init()
    node = rclpy.create_node("call_infer_batch")
    client = node.create_client(InferStrawberryImage, "/infer_strawberry_image")
    if not client.wait_for_service(timeout_sec=10.0):
        raise SystemExit("service unavailable")

    results = []
    for path in sorted(sample_dir.glob("*.jpg")):
        req = InferStrawberryImage.Request()
        req.image_path = str(path)
        req.task_id = "batch_disease"
        req.region = "A"
        future = client.call_async(req)
        rclpy.spin_until_future_complete(node, future, timeout_sec=60.0)
        resp = future.result()
        if resp is None:
            print(path.name, "no response")
            continue
        data = json.loads(resp.result_json) if resp.result_json else {}
        disease = data.get("disease", {})
        print(path.name, resp.success, disease.get("class"), disease.get("confidence"))
        results.append({"image": path.name, "success": bool(resp.success), "result": data})

    out = Path("/root/strawberry_models/Disease/board_validation/service_batch_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
