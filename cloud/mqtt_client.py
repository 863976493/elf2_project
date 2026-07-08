"""
MQTT 客户端模块（paho-mqtt + 后台线程，兼容 Windows）
订阅 ESP32 传感器数据，解析入库并广播到前端
发布指令到 ESP32 设备

Topic 设计:
  strawberry/esp32/{device_id}/sensor   ← ESP32 上报传感器数据
  strawberry/esp32/{device_id}/status   ← ESP32 上报在线状态
  strawberry/cloud/command/{device_id}  → 云端下发指令到 ESP32
"""
from __future__ import annotations

import json
import asyncio
import datetime
import threading
import paho.mqtt.client as paho_mqtt

import database as db
from config import MQTT_BROKER, MQTT_PORT, MQTT_TOPIC_PREFIX, MQTT_COMMAND_PREFIX
from ws_manager import manager, robot_manager

# ESP32 设备在线状态缓存
esp32_online: dict[str, bool] = {}
esp32_last_seen: dict[str, datetime.datetime] = {}
ESP32_ONLINE_TIMEOUT_SECONDS = 30
ANOMALY_TRIGGER_COOLDOWN_SECONDS = 300
ANOMALY_CONFIRM_COUNT = 3
_anomaly_trigger_last: dict[tuple[int, str], datetime.datetime] = {}
_anomaly_confirm_counts: dict[tuple[int, str], int] = {}
INSPECTION_TRIGGER_ANOMALIES = {"humidity_low", "humidity_high"}


def _sensor_region_voice_text(region: str, anomaly_key: str) -> str:
    anomaly_text = "土壤湿度异常"
    if anomaly_key == "humidity_low":
        anomaly_text = "土壤湿度过低"
    elif anomaly_key == "humidity_high":
        anomaly_text = "土壤湿度过高"
    return f"检测到{region}区{anomaly_text}，机器人即将前往{region}区进行巡检。"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _mark_esp32_seen(device_id: str, online: bool = True):
    esp32_online[device_id] = bool(online)
    if online:
        esp32_last_seen[device_id] = _now()
    else:
        esp32_last_seen.pop(device_id, None)


def get_fresh_esp32_online() -> dict[str, bool]:
    now = _now()
    for device_id, online in list(esp32_online.items()):
        if not online:
            continue
        last_seen = esp32_last_seen.get(device_id)
        if last_seen is None or (now - last_seen).total_seconds() > ESP32_ONLINE_TIMEOUT_SECONDS:
            esp32_online[device_id] = False
            esp32_last_seen.pop(device_id, None)
    return dict(esp32_online)

# paho-mqtt 客户端引用
_mqtt_client: paho_mqtt.Client | None = None

# 主线程的 asyncio 事件循环（用于从 MQTT 回调线程调度协程）
_main_loop: asyncio.AbstractEventLoop | None = None

_mqtt_debug = {
    "connected_count": 0,
    "message_count": 0,
    "last_topic": "",
    "last_payload": "",
    "last_error": "",
    "subscribed_topics": [],
}


def get_zone_by_device(device_id: str) -> int:
    """根据 ESP32 device_id 查找对应的 zone_id"""
    zones = db.get_zones()
    for z in zones:
        if z.get("esp32_device_id") == device_id:
            return z["id"]
    return 0


def get_region_by_zone(zone_id: int) -> str:
    zone = db.get_zone(zone_id) or {}
    name = str(zone.get("name") or "").upper()
    if "B" in name:
        return "B"
    return "A"


def reset_anomaly_cooldown(region: str | None = None) -> int:
    """Clear sensor-triggered inspection cooldowns, optionally for one region."""
    if not region:
        count = len(_anomaly_trigger_last) + len(_anomaly_confirm_counts)
        _anomaly_trigger_last.clear()
        _anomaly_confirm_counts.clear()
        return count

    normalized = str(region or "").strip().upper()
    zone_ids = [
        z["id"] for z in db.get_zones()
        if get_region_by_zone(z["id"]) == normalized
    ]
    keys = [
        key for key in _anomaly_trigger_last
        if key[0] in zone_ids and key[1] in INSPECTION_TRIGGER_ANOMALIES
    ]
    for key in keys:
        _anomaly_trigger_last.pop(key, None)
    count_keys = [
        key for key in _anomaly_confirm_counts
        if key[0] in zone_ids and key[1] in INSPECTION_TRIGGER_ANOMALIES
    ]
    for key in count_keys:
        _anomaly_confirm_counts.pop(key, None)
    return len(keys) + len(count_keys)


def _schedule(coro):
    """从 MQTT 回调线程安全地调度协程到主事件循环"""
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, _main_loop)


def _clear_humidity_confirm_counts(zone_id: int):
    for anomaly_key in ("humidity_low", "humidity_high"):
        _anomaly_confirm_counts.pop((zone_id, anomaly_key), None)


def _record_confirmed_anomaly(zone_id: int, anomaly_key: str) -> tuple[int, bool]:
    key = (zone_id, anomaly_key)
    count = _anomaly_confirm_counts.get(key, 0) + 1
    _anomaly_confirm_counts[key] = count
    return count, count >= ANOMALY_CONFIRM_COUNT


async def handle_sensor_message(device_id: str, payload: dict):
    """Handle real ESP32 sensor data from MQTT."""
    zone_id = get_zone_by_device(device_id)
    _mark_esp32_seen(device_id, True)
    await manager.broadcast({"type": "esp32_status", "data": {"device_id": device_id, "online": True}})

    record = {
        "zone_id": zone_id,
        "temperature": 0,
        "humidity": payload.get("humidity", payload.get("humi", payload.get("soil_moisture", 0))),
        "light": payload.get("light", 0),
        "co2": 0,
        "source": "mqtt",
    }
    db.insert_sensor([record])

    latest = db.get_latest_sensor(zone_id=zone_id if zone_id else None)
    latest["zone_id"] = zone_id
    latest["device_id"] = device_id
    await manager.broadcast({"type": "sensor_update", "data": latest})

    await check_thresholds(zone_id, record, device_id)


async def check_thresholds(zone_id: int, record: dict, device_id: str):
    """检查传感器数据是否超出阈值，超出则生成预警"""
    if zone_id == 0:
        return
    thresholds = db.get_zone_thresholds(zone_id)
    if not thresholds or not thresholds.get("enabled"):
        return

    alerts = []
    humi = float(record.get("humidity", 0) or 0)
    light = float(record.get("light", 0) or 0)

    if humi < thresholds["humi_min"]:
        _anomaly_confirm_counts.pop((zone_id, "humidity_high"), None)
        alerts.append(("humidity_low", "土壤湿度告警", f"土壤湿度过低 {humi:.1f}% (下限 {thresholds['humi_min']}%)"))
    elif humi > thresholds["humi_max"]:
        _anomaly_confirm_counts.pop((zone_id, "humidity_low"), None)
        alerts.append(("humidity_high", "土壤湿度告警", f"土壤湿度过高 {humi:.1f}% (上限 {thresholds['humi_max']}%)"))
    else:
        _clear_humidity_confirm_counts(zone_id)

    if light < thresholds["light_min"]:
        alerts.append(("light_low", "光照告警", f"光照过低 {light:.0f}% (下限 {thresholds['light_min']:.0f}%)"))
    elif light > thresholds["light_max"]:
        alerts.append(("light_high", "光照告警", f"光照过高 {light:.0f}% (上限 {thresholds['light_max']:.0f}%)"))

    for anomaly_key, title, msg in alerts:
        now = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
        alert_record = {
            "zone_id": zone_id,
            "time": now,
            "title": title,
            "message": f"[{device_id}] {msg}",
            "level": "warning",
        }
        db.insert_alerts([alert_record])
        await manager.broadcast({"type": "new_alert", "data": alert_record})
        if anomaly_key in INSPECTION_TRIGGER_ANOMALIES:
            confirm_count, confirmed = _record_confirmed_anomaly(zone_id, anomaly_key)
            await manager.broadcast({
                "type": "anomaly_confirm_progress",
                "data": {
                    "zone_id": zone_id,
                    "device_id": device_id,
                    "anomaly": anomaly_key,
                    "count": confirm_count,
                    "required": ANOMALY_CONFIRM_COUNT,
                },
            })
            if confirmed:
                await maybe_trigger_inspection(zone_id, anomaly_key, title, msg, device_id)
                _anomaly_confirm_counts.pop((zone_id, anomaly_key), None)


async def maybe_trigger_inspection(zone_id: int, anomaly_key: str, title: str, message: str, device_id: str):
    """Trigger one region inspection after threshold anomaly, with cooldown."""
    now = _now()
    key = (zone_id, anomaly_key)
    last = _anomaly_trigger_last.get(key)
    if last and (now - last).total_seconds() < ANOMALY_TRIGGER_COOLDOWN_SECONDS:
        return

    _anomaly_trigger_last[key] = now
    region = get_region_by_zone(zone_id)
    task_id = f"sensor_{region}_{anomaly_key}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
    db.insert_inspect_patrol_task(task_id, region)
    voice_text = _sensor_region_voice_text(region, anomaly_key)

    command = {
        "type": "inspect_region",
        "data": {
            "region": region,
            "task_id": task_id,
            "trigger": "sensor_threshold",
            "zone_id": zone_id,
            "device_id": device_id,
            "anomaly": anomaly_key,
            "title": title,
            "message": message,
            "voice_text": voice_text,
        },
    }
    sent = await robot_manager.send_command(command)
    if sent:
        db.update_patrol_task_status(task_id, "running", "sent by sensor threshold")
        await manager.broadcast({"type": "anomaly_inspection_triggered", "data": command["data"]})
    else:
        db.update_patrol_task_status(task_id, "failed", "robot is not connected")
        await manager.broadcast({"type": "anomaly_inspection_skipped", "data": {**command["data"], "reason": "robot is not connected"}})


async def handle_status_message(device_id: str, payload: dict):
    """处理 ESP32 在线状态"""
    online = bool(payload.get("online", True))
    _mark_esp32_seen(device_id, online)
    await manager.broadcast({
        "type": "esp32_status",
        "data": {"device_id": device_id, "online": online}
    })


async def publish_command(device_id: str, command: dict):
    """向 ESP32 设备发布指令"""
    if _mqtt_client is None or not _mqtt_client.is_connected():
        return False
    topic = f"{MQTT_COMMAND_PREFIX}/{device_id}"
    try:
        _mqtt_client.publish(topic, json.dumps(command))
        print(f"MQTT 发布指令到 {topic}: {command}")
        return True
    except Exception as e:
        print(f"MQTT 发布指令失败: {e}")
        return False


# ── paho-mqtt 回调（在 MQTT 线程中执行） ──

def _on_connect(client, userdata, flags, reason_code, properties=None):
    """连接成功回调"""
    print(f"MQTT 已连接: {MQTT_BROKER}:{MQTT_PORT}")
    _mqtt_debug["connected_count"] += 1
    _mqtt_debug["last_error"] = ""
    # 订阅所有 ESP32 topic
    sensor_topic = f"{MQTT_TOPIC_PREFIX}/+/sensor"
    status_topic = f"{MQTT_TOPIC_PREFIX}/+/status"
    client.subscribe(sensor_topic)
    client.subscribe(status_topic)
    _mqtt_debug["subscribed_topics"] = [sensor_topic, status_topic]
    print(f"MQTT 已订阅: {MQTT_TOPIC_PREFIX}/+/sensor, +/status")
    _schedule(manager.broadcast({"type": "mqtt_status", "data": {"connected": True}}))


def _on_disconnect(client, userdata, flags, reason_code, properties=None):
    """断开连接回调"""
    print(f"MQTT 连接断开 (rc={reason_code})，将自动重连...")
    _mqtt_debug["last_error"] = f"disconnected: {reason_code}"
    _schedule(manager.broadcast({"type": "mqtt_status", "data": {"connected": False}}))


def _on_message(client, userdata, msg):
    """收到消息回调"""
    topic = msg.topic
    payload_text = msg.payload.decode(errors="replace")
    _mqtt_debug["message_count"] += 1
    _mqtt_debug["last_topic"] = topic
    _mqtt_debug["last_payload"] = payload_text[:300]
    _mqtt_debug["last_error"] = ""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _mqtt_debug["last_error"] = f"invalid json on {topic}: {payload_text[:120]}"
        print(f"MQTT 无效消息: {topic}")
        return

    # 解析 device_id: strawberry/esp32/{device_id}/sensor
    parts = topic.split("/")
    if len(parts) < 4:
        _mqtt_debug["last_error"] = f"invalid topic: {topic}"
        return

    device_id = parts[2]
    msg_type = parts[3]

    if msg_type == "sensor":
        _schedule(handle_sensor_message(device_id, payload))
    elif msg_type == "status":
        _schedule(handle_status_message(device_id, payload))


async def mqtt_loop():
    """启动 MQTT 客户端（paho-mqtt 在后台线程中运行）"""
    global _mqtt_client, _main_loop

    _main_loop = asyncio.get_running_loop()

    client = paho_mqtt.Client(
        paho_mqtt.CallbackAPIVersion.VERSION2,
        client_id="cloud_server",
    )
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=2, max_delay=60)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except Exception as e:
        print(f"MQTT 初始连接失败: {e}，将在后台重试...")

    _mqtt_client = client
    # loop_start() 在后台线程中运行网络循环，自动重连
    client.loop_start()

    try:
        # 保持协程存活，直到被取消
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        client.loop_stop()
        client.disconnect()
        _mqtt_client = None
        print("MQTT 客户端已停止")


def get_mqtt_status() -> dict:
    """获取 MQTT 连接状态"""
    return {
        "connected": _mqtt_client is not None and _mqtt_client.is_connected(),
        "broker": f"{MQTT_BROKER}:{MQTT_PORT}",
        "esp32_devices": get_fresh_esp32_online(),
        "debug": dict(_mqtt_debug),
    }
