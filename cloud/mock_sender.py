"""
模拟数据发送器
模拟多个 ESP32 园区传感器数据 + RK3588 机器人状态上报
支持 HTTP 和 MQTT 两种模式

用法:
  python mock_sender.py                          # HTTP 模式（默认）
  python mock_sender.py --mqtt                   # MQTT 模式
  python mock_sender.py --mqtt --broker 192.168.1.100
"""
import json
import math
import time
import random
import urllib.request
import argparse

SERVER = "http://localhost:8000"

DISEASES = [
    "Angular Leafspot",
    "Anthracnose Fruit Rot",
    "Gray Mold",
    "Powdery Mildew Leaf",
    "Leaf Spot",
]
MATURITY_LEVELS = ["high", "medium", "low"]

# 模拟园区及其 ESP32 设备ID
ZONES = [
    {"id": 1, "name": "A区草莓大棚", "device_id": "esp32_001"},
    {"id": 2, "name": "B区草莓大棚", "device_id": "esp32_002"},
]


def post_json(url, data):
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def ensure_zones(server):
    """确保模拟园区存在（先检查再创建，避免重复）"""
    try:
        resp = urllib.request.urlopen(f"{server}/api/zones", timeout=5)
        existing = json.loads(resp.read().decode("utf-8"))
        if existing.get("zones") and len(existing["zones"]) >= len(ZONES):
            print(f"园区已存在 ({len(existing['zones'])} 个)，跳过创建")
            return
    except Exception:
        pass
    for z in ZONES:
        post_json(f"{server}/api/zones", {
            "name": z["name"],
            "description": f"模拟{z['name']}",
            "esp32_device_id": z["device_id"]
        })
    print(f"已创建 {len(ZONES)} 个模拟园区")


def gen_sensor_data(t, zone_offset):
    """生成模拟传感器数据"""
    temp = 25 + 5 * math.sin(t * 0.3 + zone_offset) + random.uniform(-0.5, 0.5)
    humi = 68 + 12 * math.sin(t * 0.2 + 1 + zone_offset) + random.uniform(-1, 1)
    light = 55 + 25 * math.sin(t * 0.15 + zone_offset) + random.uniform(-3, 3)
    co2 = 500 + 150 * math.sin(t * 0.25 + 2 + zone_offset) + random.uniform(-20, 20)
    return {
        "temperature": round(temp, 1),
        "humidity": round(humi, 1),
        "light": round(max(0, min(100, light)), 0),
        "co2": round(max(300, co2), 0),
        "source": "mock",
    }


# ══════════════════════════════════════
# HTTP 模式
# ══════════════════════════════════════

def run_http(server, interval):
    """通过 HTTP API 发送模拟数据"""
    ensure_zones(server)

    tick = 0
    robot_x, robot_y = 0.0, 0.0

    try:
        while True:
            tick += 1
            t = tick * 0.1

            for zone in ZONES:
                zid = zone["id"]
                data = gen_sensor_data(t, zid * 0.5)
                data["zone_id"] = zid

                result = post_json(f"{server}/api/sensor_data", {"records": [data]})
                print(f"[{tick:04d}] 园区{zid} → 温:{data['temperature']}°C 湿:{data['humidity']}% 光:{data['light']:.0f}% CO2:{data['co2']:.0f}ppm  {result}")

                if tick % 10 == 0 and random.random() < 0.3:
                    disease = random.choice(DISEASES)
                    count = random.randint(1, 5)
                    conf = round(random.uniform(0.6, 0.98), 2)
                    det = {
                        "records": [{
                            "zone_id": zid,
                            "time": time.strftime("%m-%d %H:%M:%S"),
                            "type": "disease_check",
                            "result": disease, "conf": str(conf),
                            "maturity": random.choice(MATURITY_LEVELS),
                            "rc": "red", "disease_count": count,
                            "maturity_count": random.randint(1, 8),
                        }]
                    }
                    post_json(f"{server}/api/detections", det)
                    post_json(f"{server}/api/alerts", {
                        "records": [{
                            "zone_id": zid,
                            "time": time.strftime("%m-%d %H:%M:%S"),
                            "title": f"{disease} 检测",
                            "message": f"园区{zid}发现 {count} 个 {disease}，置信度 {conf}",
                            "level": "danger" if count >= 3 else "warning",
                        }]
                    })
                    print(f"  园区{zid} 病害: {disease} x{count}")

            if tick % 5 == 0:
                robot_x += random.uniform(-0.3, 0.3)
                robot_y += random.uniform(-0.3, 0.3)
                battery = max(10, 100 - tick * 0.1 + random.uniform(-1, 1))
                state = ["idle", "patrolling", "idle", "idle"][tick // 5 % 4]
                post_json(f"{server}/api/robot/status", {
                    "battery": round(battery, 1), "x": round(robot_x, 2),
                    "y": round(robot_y, 2), "state": state
                })
                print(f"  机器人 → 电量:{battery:.0f}% ({robot_x:.1f},{robot_y:.1f}) {state}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n模拟发送器已停止")


# ══════════════════════════════════════
# MQTT 模式
# ══════════════════════════════════════

def run_mqtt(broker, port, interval, server):
    """通过 MQTT 发送模拟 ESP32 数据"""
    try:
        import paho.mqtt.client as paho_mqtt
    except ImportError:
        print("[错误] 需要安装 paho-mqtt: pip install paho-mqtt")
        return

    # 先通过 HTTP 创建园区
    ensure_zones(server)

    client = paho_mqtt.Client(client_id="mock_esp32_sender")
    try:
        client.connect(broker, port, keepalive=60)
    except Exception as e:
        print(f"[错误] 无法连接 MQTT Broker {broker}:{port} — {e}")
        return

    client.loop_start()
    print(f"MQTT 已连接: {broker}:{port}")

    # 发送各设备上线状态
    for zone in ZONES:
        topic = f"strawberry/esp32/{zone['device_id']}/status"
        client.publish(topic, json.dumps({"online": True}))
        print(f"  {zone['device_id']} 上线")

    tick = 0
    try:
        while True:
            tick += 1
            t = tick * 0.1

            for zone in ZONES:
                device_id = zone["device_id"]
                data = gen_sensor_data(t, zone["id"] * 0.5)

                topic = f"strawberry/esp32/{device_id}/sensor"
                client.publish(topic, json.dumps(data))
                print(f"[{tick:04d}] {device_id} → 温:{data['temperature']}°C 湿:{data['humidity']}% 光:{data['light']:.0f}% CO2:{data['co2']:.0f}ppm")

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n发送设备离线状态...")
        for zone in ZONES:
            topic = f"strawberry/esp32/{zone['device_id']}/status"
            client.publish(topic, json.dumps({"online": False}))
        client.loop_stop()
        client.disconnect()
        print("MQTT 模拟发送器已停止")


def main():
    parser = argparse.ArgumentParser(description="草莓云端 - 模拟数据发送器")
    parser.add_argument("--server", default=SERVER, help="HTTP 服务器地址")
    parser.add_argument("--interval", type=float, default=2, help="发送间隔(秒)")
    parser.add_argument("--mqtt", action="store_true", help="使用 MQTT 模式")
    parser.add_argument("--broker", default="localhost", help="MQTT Broker 地址")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT Broker 端口")
    args = parser.parse_args()

    server = args.server.rstrip("/")

    if args.mqtt:
        print("模拟发送器启动（MQTT 模式）")
        print(f"  Broker: {args.broker}:{args.mqtt_port}")
        print(f"  间隔: {args.interval}s")
        print(f"  设备: {[z['device_id'] for z in ZONES]}")
        print(f"  按 Ctrl+C 停止\n")
        run_mqtt(args.broker, args.mqtt_port, args.interval, server)
    else:
        print("模拟发送器启动（HTTP 模式）")
        print(f"  服务器: {server}")
        print(f"  间隔: {args.interval}s")
        print(f"  园区: {len(ZONES)} 个")
        print(f"  按 Ctrl+C 停止\n")
        run_http(server, args.interval)


if __name__ == "__main__":
    main()
