"""
鍏ㄥ眬閰嶇疆
"""
import os

# 鈹€鈹€ 鏈嶅姟绔彛 鈹€鈹€
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000

# 鈹€鈹€ 鏁版嵁搴?鈹€鈹€
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud.db")

# 鈹€鈹€ MQTT锛圗SP32 閫氫俊锛?鈹€鈹€
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC_PREFIX = "strawberry/esp32"
MQTT_COMMAND_PREFIX = "strawberry/cloud/command"
MQTT_ENABLED = os.environ.get("MQTT_ENABLED", "true").lower() == "true"

# 鈹€鈹€ AI API 鈹€鈹€
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_API_BASE = os.environ.get("AI_API_BASE", "https://api.siliconflow.cn/v1")
AI_MODEL = os.environ.get("AI_MODEL", "deepseek-ai/DeepSeek-V4-Pro")

# 鈹€鈹€ 榛樿闃堝€?鈹€鈹€
DEFAULT_THRESHOLDS = {
    "humi_min": 50.0,
    "humi_max": 75.0,
    "light_min": 20.0,
    "light_max": 80.0,
}

# 鈹€鈹€ Jetson 杩炴帴锛堝湴鍥炬媺鍙栫敤锛?鈹€鈹€
JETSON_HOST = os.environ.get("JETSON_HOST", "10.135.107.227")
JETSON_PORT = int(os.environ.get("JETSON_PORT", "22"))
JETSON_USER = os.environ.get("JETSON_USER", "jetson")
JETSON_PASSWORD = os.environ.get("JETSON_PASSWORD", "")
JETSON_MAP_DIR = os.environ.get("JETSON_MAP_DIR", "/home/jetson/Desktop/strawberry_patrol/maps")

# 鈹€鈹€ 涓婁紶鐩綍 鈹€鈹€
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
IMAGE_DIR = os.path.join(UPLOAD_DIR, "images")
VIDEO_DIR = os.path.join(UPLOAD_DIR, "videos")
MAP_DIR = os.path.join(BASE_DIR, "maps")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# ELF2 board MJPEG stream used by the map page.
ELF2_VIDEO_URL = os.environ.get("ELF2_VIDEO_URL", "http://172.20.10.2:8080/stream")

