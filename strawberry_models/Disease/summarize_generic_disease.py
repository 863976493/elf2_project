import json
from pathlib import Path


for path in sorted(Path("/root/strawberry_models/Disease/generic_int8_test").glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    boxes = data.get("boxes") or []
    best = boxes[0] if boxes else {}
    print(path.name, best.get("class_name"), best.get("confidence"), "boxes", len(boxes))
