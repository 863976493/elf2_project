"""
地图上传/获取 API
支持 ROS PGM+YAML 地图格式，自动转 PNG
"""
import io
import os
import yaml
import paramiko
from PIL import Image as PILImage
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional

import database as db
from config import MAP_DIR, JETSON_HOST, JETSON_PORT, JETSON_USER, JETSON_PASSWORD, JETSON_MAP_DIR

router = APIRouter(prefix="/api/maps", tags=["map"])

os.makedirs(MAP_DIR, exist_ok=True)


@router.get("")
def list_maps(zone_id: int = None):
    """获取所有地图"""
    return {"ok": True, "maps": db.get_maps(zone_id)}


@router.get("/latest")
def latest_map(zone_id: int = None):
    """获取最新地图信息"""
    m = db.get_latest_map(zone_id)
    if not m:
        return JSONResponse({"error": "暂无地图"}, status_code=404)
    return {"ok": True, "map": m}


@router.post("/upload")
async def upload_map(
    file: UploadFile = File(...),
    yaml_file: Optional[UploadFile] = File(None),
    resolution: float = 0.05,
    origin_x: float = 0,
    origin_y: float = 0,
    zone_id: int = Form(0)
):
    """上传地图文件，支持 PGM/PNG/JPG + 可选 YAML"""
    filename = file.filename
    filepath = os.path.join(MAP_DIR, filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)

    # 解析 YAML 元数据
    if yaml_file:
        yaml_content = await yaml_file.read()
        try:
            meta = yaml.safe_load(yaml_content)
            resolution = meta.get("resolution", resolution)
            origin = meta.get("origin", [origin_x, origin_y, 0])
            origin_x = origin[0]
            origin_y = origin[1]
        except Exception as e:
            print(f"YAML 解析失败: {e}")

    # PGM → PNG 转换（浏览器不支持 PGM）
    png_filename = filename
    ext = os.path.splitext(filename)[1].lower()
    if ext in ('.pgm', '.pbm', '.ppm'):
        try:
            img = PILImage.open(filepath)
            png_filename = os.path.splitext(filename)[0] + ".png"
            png_path = os.path.join(MAP_DIR, png_filename)
            img.save(png_path, "PNG")
            width, height = img.size
        except Exception as e:
            print(f"PGM 转 PNG 失败: {e}")
            width, height = 0, 0
    else:
        try:
            img = PILImage.open(filepath)
            width, height = img.size
        except Exception:
            width, height = 0, 0

    map_id = db.insert_map(png_filename, resolution, origin_x, origin_y, width, height, zone_id)
    return {"ok": True, "id": map_id, "filename": png_filename, "zone_id": zone_id}


@router.get("/folder_scan")
def folder_scan():
    """扫描 maps 文件夹，返回 YAML 文件及其注册状态"""
    registered_names = {m['filename'] for m in db.get_maps()}
    results = []
    for f in sorted(os.listdir(MAP_DIR), reverse=True):
        if not f.endswith('.yaml'):
            continue
        stem = f[:-5]
        png_exists = os.path.exists(os.path.join(MAP_DIR, stem + '.png'))
        pgm_exists = os.path.exists(os.path.join(MAP_DIR, stem + '.pgm'))
        if not png_exists and not pgm_exists:
            continue
        registered = (stem + '.png') in registered_names
        resolution, origin_x, origin_y = 0.05, 0.0, 0.0
        try:
            with open(os.path.join(MAP_DIR, f), 'r') as yf:
                meta = yaml.safe_load(yf)
            resolution = meta.get('resolution', 0.05)
            origin = meta.get('origin', [0, 0, 0])
            origin_x, origin_y = origin[0], origin[1]
        except Exception:
            pass
        results.append({
            'stem': stem, 'yaml': f,
            'image': stem + '.png' if png_exists else stem + '.pgm',
            'registered': registered,
            'resolution': resolution,
            'origin_x': origin_x, 'origin_y': origin_y,
        })
    return {"ok": True, "files": results}


@router.post("/import_local")
def import_local_map(data: dict):
    """将已在 maps 文件夹中的文件注册到数据库"""
    stem = data.get('stem', '')
    zone_id = int(data.get('zone_id', 0))
    yaml_path = os.path.join(MAP_DIR, stem + '.yaml')
    if not os.path.exists(yaml_path):
        return JSONResponse({"ok": False, "error": "YAML 不存在"}, status_code=400)

    resolution, origin_x, origin_y = 0.05, 0.0, 0.0
    try:
        with open(yaml_path, 'r') as yf:
            meta = yaml.safe_load(yf)
        resolution = meta.get('resolution', 0.05)
        origin = meta.get('origin', [0, 0, 0])
        origin_x, origin_y = origin[0], origin[1]
    except Exception:
        pass

    png_path = os.path.join(MAP_DIR, stem + '.png')
    pgm_path = os.path.join(MAP_DIR, stem + '.pgm')
    if not os.path.exists(png_path):
        if not os.path.exists(pgm_path):
            return JSONResponse({"ok": False, "error": "图片文件不存在"}, status_code=400)
        try:
            img = PILImage.open(pgm_path)
            img.save(png_path, "PNG")
            width, height = img.size
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"PGM转PNG失败: {e}"}, status_code=500)
    else:
        try:
            img = PILImage.open(png_path)
            width, height = img.size
        except Exception:
            width, height = 0, 0

    map_id = db.insert_map(stem + '.png', resolution, origin_x, origin_y, width, height, zone_id)
    return {"ok": True, "id": map_id, "filename": stem + '.png'}


def _ssh_connect(host=None, port=None, user=None, password=None):
    """创建 SSH 连接，参数可覆盖 config 默认值"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host or JETSON_HOST,
        port=port or JETSON_PORT,
        username=user or JETSON_USER,
        password=password or JETSON_PASSWORD,
        timeout=10
    )
    return client


@router.get("/jetson_files")
def list_jetson_maps(host: str = None, map_dir: str = None):
    """列出 Jetson 上的地图文件（YAML+图片对）"""
    remote_dir = map_dir or JETSON_MAP_DIR
    registered_names = {m['filename'] for m in db.get_maps()}
    try:
        ssh = _ssh_connect(host=host)
        sftp = ssh.open_sftp()
        files = sftp.listdir(remote_dir)
        sftp.close()
        ssh.close()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    results = []
    yaml_stems = {f[:-5] for f in files if f.endswith('.yaml')}
    for stem in sorted(yaml_stems, reverse=True):
        has_pgm = (stem + '.pgm') in files
        has_png = (stem + '.png') in files
        if not has_pgm and not has_png:
            continue
        results.append({
            'stem': stem,
            'image': stem + '.png' if has_png else stem + '.pgm',
            'registered': (stem + '.png') in registered_names,
        })
    return {"ok": True, "files": results, "remote_dir": remote_dir}


@router.post("/pull_from_jetson")
def pull_from_jetson(data: dict):
    """从 Jetson 拉取指定地图文件并注册到数据库"""
    stem    = data.get('stem', '')
    zone_id = int(data.get('zone_id', 0))
    host    = data.get('host') or JETSON_HOST
    remote_dir = data.get('map_dir') or JETSON_MAP_DIR

    if not stem:
        return JSONResponse({"ok": False, "error": "stem 不能为空"}, status_code=400)

    try:
        ssh = _ssh_connect(host=host)
        sftp = ssh.open_sftp()

        # 拉取 YAML
        yaml_remote = f"{remote_dir}/{stem}.yaml"
        with sftp.open(yaml_remote, 'r') as f:
            yaml_content = f.read()

        # 解析 YAML 元数据
        resolution, origin_x, origin_y = 0.05, 0.0, 0.0
        try:
            meta = yaml.safe_load(yaml_content)
            resolution = meta.get('resolution', 0.05)
            origin = meta.get('origin', [0, 0, 0])
            origin_x, origin_y = origin[0], origin[1]
        except Exception:
            pass

        # 保存 YAML 到本地
        local_yaml = os.path.join(MAP_DIR, stem + '.yaml')
        with open(local_yaml, 'wb') as f:
            f.write(yaml_content if isinstance(yaml_content, bytes) else yaml_content.encode())

        # 拉取图片（优先 pgm，本地转 png）
        remote_files = sftp.listdir(remote_dir)
        if stem + '.pgm' in remote_files:
            img_remote = f"{remote_dir}/{stem}.pgm"
        elif stem + '.png' in remote_files:
            img_remote = f"{remote_dir}/{stem}.png"
        else:
            sftp.close(); ssh.close()
            return JSONResponse({"ok": False, "error": "找不到图片文件"}, status_code=404)

        buf = io.BytesIO()
        sftp.getfo(img_remote, buf)
        buf.seek(0)
        sftp.close()
        ssh.close()

        # 转存为 PNG
        img = PILImage.open(buf)
        png_path = os.path.join(MAP_DIR, stem + '.png')
        img.save(png_path, 'PNG')
        width, height = img.size

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    map_id = db.insert_map(stem + '.png', resolution, origin_x, origin_y, width, height, zone_id)
    return {"ok": True, "id": map_id, "filename": stem + '.png'}


@router.get("/file/{filename}")
def get_map_file(filename: str):
    """获取地图图片文件"""
    filepath = os.path.join(MAP_DIR, filename)
    if os.path.exists(filepath):
        return FileResponse(filepath)
    return JSONResponse({"error": "文件不存在"}, status_code=404)
