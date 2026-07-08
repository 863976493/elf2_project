import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from strawberry_interfaces.srv import InferStrawberryImage


class YoloRknnModel:
    def __init__(
        self,
        logger,
        scripts_dir,
        model_path,
        class_names_path,
        core_id,
        confidence_threshold,
        nms_iou,
        score_sum_factor,
    ):
        self.logger = logger
        self.model_path = str(model_path)
        self.class_names = []
        self.confidence_threshold = float(confidence_threshold)
        self.nms_iou = float(nms_iou)
        self.score_sum_factor = float(score_sum_factor)

        scripts_dir = Path(scripts_dir).resolve()
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from rknnlite.api import RKNNLite
        from infer_rknn_yolov8 import postprocess, preprocess

        self.RKNNLite = RKNNLite
        self.preprocess = preprocess
        self.postprocess = postprocess
        self.class_names = self._load_class_names(class_names_path)

        self.rknn = RKNNLite()
        logger.info(f"Loading RKNN model: {self.model_path}")
        ret = self.rknn.load_rknn(self.model_path)
        if ret != 0:
            raise RuntimeError(f"load_rknn failed for {self.model_path}: {ret}")

        if core_id == -1:
            ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0_1_2)
        elif core_id == 0:
            ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0)
        elif core_id == 1:
            ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_1)
        elif core_id == 2:
            ret = self.rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_2)
        else:
            ret = self.rknn.init_runtime()
        if ret != 0:
            raise RuntimeError(f"init_runtime failed for {self.model_path}: {ret}")

    def _load_class_names(self, path):
        if not path:
            return {}
        path = Path(path)
        if not path.exists():
            self.logger.warn(f"Class names file not found: {path}")
            return {}

        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if "names" in data:
                    data = data["names"]
                else:
                    return {int(k): str(v) for k, v in data.items()}
            return {i: str(v) for i, v in enumerate(data)}

        text = path.read_text(encoding="utf-8")
        names = []
        in_names = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("names:"):
                in_names = True
                after = stripped.split(":", 1)[1].strip()
                if after.startswith("[") and after.endswith("]"):
                    values = [x.strip().strip("'\"") for x in after[1:-1].split(",") if x.strip()]
                    return {i: str(v) for i, v in enumerate(values)}
                continue
            if in_names and stripped.startswith("-"):
                names.append(stripped[1:].strip().strip("'\""))
            elif in_names and stripped and not stripped.startswith("#"):
                break
        return {i: str(v) for i, v in enumerate(names)}

    def infer(self, img):
        t0 = time.perf_counter()
        input_rgb, scale, pad_x, pad_y = self.preprocess(img)
        outputs = self.rknn.inference(inputs=[input_rgb], data_format=["nhwc"])
        if outputs is None:
            return [], 0.0
        detections = self.postprocess(
            outputs,
            scale,
            pad_x,
            pad_y,
            img.shape[1],
            img.shape[0],
            self.confidence_threshold,
            self.nms_iou,
            self.class_names,
            score_sum_factor=self.score_sum_factor,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return detections, elapsed_ms

    def close(self):
        self.rknn.release()


class StrawberryInferenceNode(Node):
    def __init__(self):
        super().__init__("strawberry_inference_node")

        p = self.declare_parameter
        p("rknn_scripts_dir", "/root/deploy_elf2_maturity/scripts")
        p("disease_model_path", "/root/strawberry_models/Disease/strawberry_yolov8n.rknn")
        p("disease_class_names_path", "/root/strawberry_models/Disease/strawberry.yaml")
        p("disease_enabled", True)
        p("disease_required", False)
        p("maturity_model_path", "/root/strawberry_models/Maturity/best_fp16.rknn")
        p("maturity_class_names_path", "")
        p("maturity_enabled", True)
        p("maturity_required", True)
        p("confidence_threshold", 0.25)
        p("nms_iou", 0.45)
        p("score_sum_factor", 0.5)
        p("rknn_core", -1)
        p("service_name", "/infer_strawberry_image")

        g = lambda name: self.get_parameter(name).value
        scripts_dir = str(g("rknn_scripts_dir"))
        core_id = int(g("rknn_core"))
        self.disease_model = self._load_optional_model(
            "disease",
            bool(g("disease_enabled")),
            bool(g("disease_required")),
            scripts_dir,
            str(g("disease_model_path")),
            str(g("disease_class_names_path")),
            core_id,
            float(g("confidence_threshold")),
            float(g("nms_iou")),
            float(g("score_sum_factor")),
        )
        self.maturity_model = self._load_optional_model(
            "maturity",
            bool(g("maturity_enabled")),
            bool(g("maturity_required")),
            scripts_dir,
            str(g("maturity_model_path")),
            str(g("maturity_class_names_path")),
            core_id,
            float(g("confidence_threshold")),
            float(g("nms_iou")),
            float(g("score_sum_factor")),
        )

        self.srv = self.create_service(
            InferStrawberryImage,
            str(g("service_name")),
            self._handle_infer,
        )
        self.get_logger().info("Strawberry inference service ready.")

    def _load_optional_model(
        self,
        label,
        enabled,
        required,
        scripts_dir,
        model_path,
        class_names_path,
        core_id,
        confidence_threshold,
        nms_iou,
        score_sum_factor,
    ):
        if not enabled:
            self.get_logger().warn(f"{label} inference disabled by parameter.")
            return None
        try:
            return YoloRknnModel(
                self.get_logger(),
                scripts_dir,
                model_path,
                class_names_path,
                core_id,
                confidence_threshold,
                nms_iou,
                score_sum_factor,
            )
        except Exception as exc:
            msg = f"{label} model unavailable: {exc}"
            if required:
                raise RuntimeError(msg) from exc
            self.get_logger().error(msg)
            self.get_logger().warn(f"{label} inference will return unavailable results.")
            return None

    def _handle_infer(self, request, response):
        image_path = Path(request.image_path)
        if not image_path.exists():
            response.success = False
            response.message = f"image not found: {image_path}"
            response.result_json = "{}"
            return response

        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is None:
            response.success = False
            response.message = f"failed to read image: {image_path}"
            response.result_json = "{}"
            return response

        try:
            if self.disease_model is None:
                disease_dets, disease_ms = [], 0.0
                disease_unavailable = True
            else:
                disease_dets, disease_ms = self.disease_model.infer(img)
                disease_unavailable = False

            if self.maturity_model is None:
                maturity_dets, maturity_ms = [], 0.0
                maturity_unavailable = True
            else:
                maturity_dets, maturity_ms = self.maturity_model.infer(img)
                maturity_unavailable = False
        except Exception as exc:
            response.success = False
            response.message = f"inference failed: {exc}"
            response.result_json = "{}"
            return response

        disease = (
            self._unavailable_result("disease model unavailable")
            if disease_unavailable
            else self._best_detection(disease_dets)
        )
        maturity = (
            self._unavailable_result("maturity model unavailable")
            if maturity_unavailable
            else self._best_detection(maturity_dets)
        )
        result = {
            "task_id": request.task_id,
            "region": request.region,
            "image_path": str(image_path),
            "disease": disease,
            "maturity": maturity,
            "timing_ms": {
                "disease": round(float(disease_ms), 3),
                "maturity": round(float(maturity_ms), 3),
            },
        }
        response.success = True
        response.message = "ok"
        response.result_json = json.dumps(result, ensure_ascii=False)
        return response

    def _best_detection(self, detections):
        normalized = self._normalize_detections(detections)
        if not normalized:
            return {"class": "none", "class_id": -1, "confidence": 0.0}
        det = max(normalized, key=lambda item: float(item.get("confidence", 0.0)))
        class_id = int(det.get("class_id", -1))
        class_name = str(det.get("class_name", f"class_{class_id}"))
        return {
            "class": class_name,
            "class_id": class_id,
            "confidence": round(float(det.get("confidence", 0.0)), 6),
        }

    def _normalize_detections(self, detections):
        if detections is None:
            return []

        if isinstance(detections, dict):
            return [self._normalize_detection_dict(detections)]

        if isinstance(detections, np.ndarray):
            detections = detections.tolist()

        if not isinstance(detections, (list, tuple)):
            return []

        normalized = []
        for item in detections:
            normalized.extend(self._normalize_detection_item(item))
        return [det for det in normalized if det is not None]

    def _normalize_detection_item(self, item):
        if item is None:
            return []
        if isinstance(item, dict):
            return [self._normalize_detection_dict(item)]
        if isinstance(item, np.ndarray):
            item = item.tolist()
        if not isinstance(item, (list, tuple)):
            return []

        if item and all(isinstance(v, (list, tuple, np.ndarray, dict)) for v in item):
            normalized = []
            for nested in item:
                normalized.extend(self._normalize_detection_item(nested))
            return normalized

        values = []
        for value in item:
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                pass
        if not values:
            return []

        confidence = 0.0
        class_id = -1
        if len(values) >= 6:
            # Common YOLO postprocess formats:
            # [x1, y1, x2, y2, confidence, class_id]
            # [x1, y1, x2, y2, class_id, confidence]
            if 0.0 <= values[4] <= 1.0:
                confidence = values[4]
                class_id = int(round(values[5]))
            elif 0.0 <= values[5] <= 1.0:
                class_id = int(round(values[4]))
                confidence = values[5]
            else:
                confidence = max((v for v in values if 0.0 <= v <= 1.0), default=0.0)
                class_id = int(round(values[-1]))
        elif len(values) >= 2:
            confidence = max((v for v in values if 0.0 <= v <= 1.0), default=0.0)
            class_id = int(round(values[0]))
        else:
            confidence = values[0] if 0.0 <= values[0] <= 1.0 else 0.0

        return [
            {
                "class": f"class_{class_id}",
                "class_id": class_id,
                "class_name": f"class_{class_id}",
                "confidence": float(confidence),
            }
        ]

    def _normalize_detection_dict(self, det):
        class_id = int(det.get("class_id", det.get("cls", det.get("label", -1))))
        class_name = str(det.get("class_name", det.get("name", f"class_{class_id}")))
        confidence = float(det.get("confidence", det.get("score", det.get("conf", 0.0))))
        return {
            "class": class_name,
            "class_id": class_id,
            "class_name": class_name,
            "confidence": confidence,
        }

    def _unavailable_result(self, reason):
        return {
            "class": "unavailable",
            "class_id": -1,
            "confidence": 0.0,
            "error": reason,
        }

    def destroy_node(self):
        if hasattr(self, "disease_model"):
            self.disease_model.close()
        if hasattr(self, "maturity_model"):
            self.maturity_model.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = StrawberryInferenceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
