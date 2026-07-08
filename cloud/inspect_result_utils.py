from __future__ import annotations

import base64
import binascii
import json
import os
import re
from typing import Any

from config import UPLOAD_DIR


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_filename(value: str, fallback: str) -> str:
    name = os.path.basename(str(value or "").strip())
    name = _SAFE_FILENAME_RE.sub("_", name).strip("._")
    return name or fallback


def save_inspect_images(task_id: str, images: list[Any] | None) -> list[str]:
    if not task_id or not isinstance(images, list):
        return []
    safe_task = safe_filename(task_id, "task")
    dest_dir = os.path.join(UPLOAD_DIR, "inspect", safe_task)
    os.makedirs(dest_dir, exist_ok=True)
    urls: list[str] = []
    for idx, item in enumerate(images, start=1):
        if isinstance(item, dict):
            raw = item.get("data") or item.get("base64") or item.get("content") or ""
            filename = item.get("filename") or item.get("name") or f"image_{idx}.jpg"
        else:
            raw = str(item or "")
            filename = f"image_{idx}.jpg"
        if not raw:
            continue
        if "," in raw and raw.lower().startswith("data:"):
            raw = raw.split(",", 1)[1]
        filename = safe_filename(filename, f"image_{idx}.jpg")
        ext = os.path.splitext(filename)[1].lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            filename = f"{os.path.splitext(filename)[0] or 'image_' + str(idx)}.jpg"
        path = os.path.join(dest_dir, filename)
        try:
            with open(path, "wb") as f:
                f.write(base64.b64decode(raw, validate=True))
        except (binascii.Error, OSError, ValueError) as exc:
            print(f"save inspect image failed task={task_id} file={filename}: {exc}")
            continue
        urls.append(f"/uploads/inspect/{safe_task}/{filename}")
    return urls


def merge_inspect_result_json(result_json: str, image_urls: list[str]) -> str:
    if not image_urls:
        return result_json or ""
    try:
        data = json.loads(result_json) if result_json else {}
    except json.JSONDecodeError:
        data = {"raw": result_json}
    if not isinstance(data, dict):
        data = {"result": data}
    data["cloud_images"] = image_urls
    data["image_upload_status"] = "uploaded"
    return json.dumps(data, ensure_ascii=False)
