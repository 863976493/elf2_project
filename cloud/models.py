"""
数据模型 — Pydantic
扩展版：支持多园区、巡检任务、机器人状态、WebSocket消息
"""
from pydantic import BaseModel
from typing import Any, Optional, List


# ══════════════════════════════════════
# 园区
# ══════════════════════════════════════

class ZoneCreate(BaseModel):
    name: str
    description: str = ""
    esp32_device_id: str = ""


class ZoneUpdate(BaseModel):
    name: str
    description: str = ""
    esp32_device_id: str = ""


class ZoneOut(BaseModel):
    id: int
    name: str
    description: str = ""
    esp32_device_id: str = ""
    created_at: Optional[str] = None


# ══════════════════════════════════════
# 传感器数据
# ══════════════════════════════════════

class SensorRecord(BaseModel):
    id: Optional[int] = None
    zone_id: int = 0
    temperature: float = 0.0
    humidity: float = 0.0
    light: float = 0.0
    co2: float = 0.0
    source: str = "unknown"
    created_at: Optional[str] = None


class SensorBatch(BaseModel):
    records: List[SensorRecord]


# ══════════════════════════════════════
# 检测记录
# ══════════════════════════════════════

class DetectionRecord(BaseModel):
    id: Optional[int] = None
    zone_id: int = 0
    time: Optional[str] = None
    type: Optional[str] = None
    result: Optional[str] = None
    conf: Optional[str] = None
    maturity: Optional[str] = None
    rc: Optional[str] = None
    disease_count: int = 0
    maturity_count: int = 0
    created_at: Optional[str] = None


class DetectionBatch(BaseModel):
    records: List[DetectionRecord]


# ══════════════════════════════════════
# 分析结果
# ══════════════════════════════════════

class AnalysisRecord(BaseModel):
    id: Optional[int] = None
    detection_time: Optional[str] = None
    content: Optional[str] = None
    model: Optional[str] = None
    tokens: int = 0
    created_at: Optional[str] = None


class AnalysisBatch(BaseModel):
    records: List[AnalysisRecord]


# ══════════════════════════════════════
# 预警
# ══════════════════════════════════════

class AlertRecord(BaseModel):
    id: Optional[int] = None
    zone_id: int = 0
    time: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None
    level: str = "warning"
    created_at: Optional[str] = None


class AlertBatch(BaseModel):
    records: List[AlertRecord]


# ══════════════════════════════════════
# AI 分析响应
# ══════════════════════════════════════

class AnalyzeResponse(BaseModel):
    report: str
    timestamp: str


class SettingsUpdate(BaseModel):
    ai_api_key: str = ""
    ai_api_base: str = "https://api.siliconflow.cn/v1"
    ai_model: str = "deepseek-ai/DeepSeek-V4-Pro"


# ══════════════════════════════════════
# 简化版 POST（兼容旧格式）
# ══════════════════════════════════════

class SimpleSensorData(BaseModel):
    temp: float
    humidity: float
    disease_type: str = "none"
    disease_count: int = 0


# ══════════════════════════════════════
# 阈值配置
# ══════════════════════════════════════

class ThresholdUpdate(BaseModel):
    humi_min: float = 50.0
    humi_max: float = 75.0
    light_min: float = 20.0
    light_max: float = 80.0
    enabled: int = 1


# ══════════════════════════════════════
# 巡检任务
# ══════════════════════════════════════

class PatrolTaskCreate(BaseModel):
    zone_id: int
    type: str = "manual"
    path_data_json: str = "[]"


class PatrolTaskOut(BaseModel):
    id: int
    zone_id: int
    type: str
    status: str
    path_data_json: str = "[]"
    result: str = ""
    created_at: Optional[str] = None


# ══════════════════════════════════════
# 巡检路径
# ══════════════════════════════════════

class PatrolPathCreate(BaseModel):
    zone_id: int
    name: str
    waypoints_json: str = "[]"


class PatrolPathOut(BaseModel):
    id: int
    zone_id: int
    name: str
    waypoints_json: str = "[]"
    created_at: Optional[str] = None


class PresetPointCreate(BaseModel):
    zone_id: int
    name: str
    x: float
    y: float
    theta: float = 0.0


class PresetPointOut(BaseModel):
    id: int
    zone_id: int
    name: str
    x: float
    y: float
    theta: float = 0.0
    created_at: Optional[str] = None


class RandomPatrolCreate(BaseModel):
    zone_id: int
    count: int = 3


class InspectRegionRequest(BaseModel):
    region: str


class InspectionResult(BaseModel):
    task_id: str
    success: bool = True
    message: str = ""
    result_json: Optional[Any] = None
    disease: Optional[dict] = None
    maturity: Optional[dict] = None
    image_path: str = ""
    images: Optional[List[Any]] = None


# ══════════════════════════════════════
# 机器人状态
# ══════════════════════════════════════

class RobotStatusOut(BaseModel):
    battery: float = 0
    position_x: float = 0
    position_y: float = 0
    state: str = "offline"
    updated_at: Optional[str] = None


class RobotCommand(BaseModel):
    command: str
    data: dict = {}


# ══════════════════════════════════════
# WebSocket 消息格式
# ══════════════════════════════════════

class WsMessage(BaseModel):
    type: str
    data: dict = {}
