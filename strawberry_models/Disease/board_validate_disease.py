import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from rknnlite.api import RKNNLite


CLASSES = [
    "Angular Leafspot",
    "Anthracnose Fruit Rot",
    "Blossom Blight",
    "Gray Mold",
    "Leaf Spot",
    "Powdery Mildew Fruit",
    "Powdery Mildew Leaf",
]
OBJ_THRESH = 0.25
NMS_THRESH = 0.45
IMG_SIZE = 640


def letterbox(im, new_shape=(640, 640), color=(0, 0, 0)):
    shape = im.shape[:2]
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    ratio = r, r
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]
    dw /= 2
    dh /= 2
    if shape[::-1] != new_unpad:
        im = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return im, ratio, (left, top)


def dfl(position):
    n, c, h, w = position.shape
    y = position.reshape(n, 4, c // 4, h, w)
    e_y = np.exp(y - np.max(y, axis=2, keepdims=True))
    y = e_y / np.sum(e_y, axis=2, keepdims=True)
    acc = np.arange(c // 4, dtype=np.float32).reshape(1, 1, c // 4, 1, 1)
    return (y * acc).sum(2)


def box_process(position):
    grid_h, grid_w = position.shape[2:4]
    col, row = np.meshgrid(np.arange(0, grid_w), np.arange(0, grid_h))
    col = col.reshape(1, 1, grid_h, grid_w)
    row = row.reshape(1, 1, grid_h, grid_w)
    grid = np.concatenate((col, row), axis=1)
    stride = np.array([IMG_SIZE // grid_h, IMG_SIZE // grid_w]).reshape(1, 2, 1, 1)
    position = dfl(position)
    box_xy = grid + 0.5 - position[:, 0:2, :, :]
    box_xy2 = grid + 0.5 + position[:, 2:4, :, :]
    return np.concatenate((box_xy * stride, box_xy2 * stride), axis=1)


def flatten_chw(x):
    ch = x.shape[1]
    return x.transpose(0, 2, 3, 1).reshape(-1, ch)


def filter_boxes(boxes, box_confidences, box_class_probs):
    box_confidences = box_confidences.reshape(-1)
    class_max_score = np.max(box_class_probs, axis=-1)
    classes = np.argmax(box_class_probs, axis=-1)
    class_pos = np.where(class_max_score * box_confidences >= OBJ_THRESH)
    scores = (class_max_score * box_confidences)[class_pos]
    return boxes[class_pos], classes[class_pos], scores


def nms_boxes(boxes, scores):
    x = boxes[:, 0]
    y = boxes[:, 1]
    w = boxes[:, 2] - boxes[:, 0]
    h = boxes[:, 3] - boxes[:, 1]
    areas = w * h
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x[i], x[order[1:]])
        yy1 = np.maximum(y[i], y[order[1:]])
        xx2 = np.minimum(x[i] + w[i], x[order[1:]] + w[order[1:]])
        yy2 = np.minimum(y[i] + h[i], y[order[1:]] + h[order[1:]])
        w1 = np.maximum(0.0, xx2 - xx1 + 0.00001)
        h1 = np.maximum(0.0, yy2 - yy1 + 0.00001)
        inter = w1 * h1
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(ovr <= NMS_THRESH)[0]
        order = order[inds + 1]
    return np.array(keep)


def yolov8_post_process(outputs):
    boxes, scores, classes_conf = [], [], []
    pair_per_branch = len(outputs) // 3
    for i in range(3):
        boxes.append(box_process(outputs[pair_per_branch * i]))
        classes_conf.append(outputs[pair_per_branch * i + 1])
        scores.append(np.ones_like(outputs[pair_per_branch * i + 1][:, :1, :, :], dtype=np.float32))

    boxes = np.concatenate([flatten_chw(v) for v in boxes])
    classes_conf = np.concatenate([flatten_chw(v) for v in classes_conf])
    scores = np.concatenate([flatten_chw(v) for v in scores])
    boxes, classes, scores = filter_boxes(boxes, scores, classes_conf)

    nboxes, nclasses, nscores = [], [], []
    for class_id in set(classes):
        inds = np.where(classes == class_id)
        keep = nms_boxes(boxes[inds], scores[inds])
        if len(keep):
            nboxes.append(boxes[inds][keep])
            nclasses.append(classes[inds][keep])
            nscores.append(scores[inds][keep])
    if not nclasses:
        return np.array([]), np.array([]), np.array([])
    return np.concatenate(nboxes), np.concatenate(nclasses), np.concatenate(nscores)


def clip_box(box, width, height):
    x1, y1, x2, y2 = box
    return [
        float(max(0, min(width, x1))),
        float(max(0, min(height, y1))),
        float(max(0, min(width, x2))),
        float(max(0, min(height, y2))),
    ]


def run_image(rknn, image_path):
    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"failed to read {image_path}")
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    resized, ratio, padding = letterbox(rgb, (IMG_SIZE, IMG_SIZE))
    inp = resized[None, :, :, :]
    outputs = rknn.inference(inputs=[inp], data_format=["nhwc"])
    boxes, classes, scores = yolov8_post_process(outputs)

    detections = []
    for box, class_id, score in zip(boxes, classes, scores):
        x1, y1, x2, y2 = box
        x1 = (x1 - padding[0]) / ratio[0]
        x2 = (x2 - padding[0]) / ratio[0]
        y1 = (y1 - padding[1]) / ratio[1]
        y2 = (y2 - padding[1]) / ratio[1]
        detections.append(
            {
                "class_id": int(class_id),
                "class_name": CLASSES[int(class_id)],
                "confidence": float(score),
                "bbox_xyxy": clip_box([x1, y1, x2, y2], w, h),
            }
        )
    detections.sort(key=lambda x: x["confidence"], reverse=True)
    return img, {"image": str(image_path), "boxes": detections}


def draw_predictions(img, pred):
    out = img.copy()
    for det in pred["boxes"]:
        x1, y1, x2, y2 = [int(round(v)) for v in det["bbox_xyxy"]]
        cv2.rectangle(out, (x1, y1), (x2, y2), (255, 0, 255), 2)
        label = f"{det['class_name']} {det['confidence']:.2f}"
        cv2.putText(out, label, (x1, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    return out


def load_model(model_path):
    rknn = RKNNLite()
    ret = rknn.load_rknn(str(model_path))
    if ret != 0:
        raise RuntimeError(f"load_rknn failed: {ret}")
    ret = rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0_1_2)
    if ret != 0:
        raise RuntimeError(f"init_runtime failed: {ret}")
    return rknn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    image_out = args.out / f"{args.name}_images"
    image_out.mkdir(parents=True, exist_ok=True)

    rknn = load_model(args.model)
    predictions = []
    try:
        for image_path in sorted(args.samples.glob("*")):
            if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp"}:
                continue
            img, pred = run_image(rknn, image_path)
            predictions.append(pred)
            cv2.imwrite(str(image_out / image_path.name), draw_predictions(img, pred))
            best = pred["boxes"][0] if pred["boxes"] else None
            if best:
                print(f"{image_path.name}: {best['class_name']} {best['confidence']:.4f} boxes={len(pred['boxes'])}")
            else:
                print(f"{image_path.name}: no detection")
    finally:
        rknn.release()

    pred_json = args.out / f"{args.name}_predictions.json"
    pred_json.write_text(json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"predictions={pred_json}")
    print(f"images={image_out}")


if __name__ == "__main__":
    main()
