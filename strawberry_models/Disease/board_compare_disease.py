import json
from pathlib import Path


def basename(path_value):
    return str(path_value).replace("\\", "/").rstrip("/").split("/")[-1]


def by_name(items):
    return {basename(item["image"]): item for item in items}


def iou_xyxy(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    denom = area_a + area_b - inter
    return inter / denom if denom > 0 else 0.0


def report(name, ref, pred):
    ref_by_name = by_name(ref)
    pred_by_name = by_name(pred)
    lines = [f"[{name}]"]
    same = 0
    values = []
    for image_name in sorted(ref_by_name):
        ref_best = ref_by_name[image_name]["boxes"][0] if ref_by_name[image_name]["boxes"] else None
        pred_best = pred_by_name.get(image_name, {}).get("boxes", [])
        pred_best = pred_best[0] if pred_best else None
        if not ref_best or not pred_best:
            lines.append(f"{image_name}: missing")
            continue
        class_same = ref_best["class_id"] == pred_best["class_id"]
        same += int(class_same)
        iou = iou_xyxy(ref_best["bbox_xyxy"], pred_best["bbox_xyxy"])
        conf_diff = abs(ref_best["confidence"] - pred_best["confidence"])
        values.append((iou, conf_diff))
        lines.append(
            f"{image_name}: class={class_same} iou={iou:.4f} "
            f"conf_diff={conf_diff:.4f} pred={pred_best['class_name']} {pred_best['confidence']:.4f}"
        )
    lines.append(
        f"summary: class {same}/7, iou_min={min(v[0] for v in values):.4f}, "
        f"conf_diff_max={max(v[1] for v in values):.4f}"
    )
    return lines


def main():
    root = Path("board_validation")
    ref = json.loads((root / "fp_sim_predictions.json").read_text(encoding="utf-8"))
    fp = json.loads((root / "fp_predictions.json").read_text(encoding="utf-8"))
    int8 = json.loads((root / "int8_predictions.json").read_text(encoding="utf-8"))
    lines = []
    lines.extend(report("board_fp_vs_sim_fp", ref, fp))
    lines.append("")
    lines.extend(report("board_int8_vs_sim_fp", ref, int8))
    text = "\n".join(lines) + "\n"
    (root / "compare_report.txt").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
