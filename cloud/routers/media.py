"""
图片/视频 API
"""
import os
import glob
import datetime
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

from config import IMAGE_DIR, VIDEO_DIR
from ws_manager import manager, video_manager

router = APIRouter(prefix="/api", tags=["media"])

os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR, exist_ok=True)


@router.post("/upload_image")
async def upload_image(file: UploadFile = File(...)):
    """接收设备截图上传"""
    filename = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"
    filepath = os.path.join(IMAGE_DIR, filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    await manager.broadcast({"type": "new_image", "filename": filename})
    return {"ok": True, "filename": filename}


@router.get("/latest_image")
def latest_image():
    """返回最新截图"""
    images = sorted(
        glob.glob(os.path.join(IMAGE_DIR, "*.jpg")),
        key=os.path.getmtime, reverse=True
    )
    if images:
        return FileResponse(images[0], media_type="image/jpeg")
    return JSONResponse({"error": "暂无图片"}, status_code=404)


@router.get("/images")
def list_images():
    """图片列表"""
    images = sorted(
        glob.glob(os.path.join(IMAGE_DIR, "*.jpg")),
        key=os.path.getmtime, reverse=True
    )
    return {
        "ok": True,
        "images": [os.path.basename(f) for f in images[:50]]
    }


@router.get("/video/status")
def video_status():
    """返回视频推流状态"""
    return {
        "live": video_manager.is_live,
        "viewers": video_manager.viewer_count,
    }
