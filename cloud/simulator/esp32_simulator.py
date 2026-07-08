"""
ESP32 传感器模拟器（MQTT 模式）

模拟多个 ESP32 设备，通过 MQTT 发布传感器数据，通信协议与真实 ESP32 完全一致。
云端 mqtt_client.py 订阅后自动入库、广播 WebSocket、阈值检查。

通信协议：
  发布: strawberry/esp32/{device_id}/sensor  {"temperature":25.3, "humidity":68.1, "light":15000, "co2":520}
  发布: strawberry/esp32/{device_id}/status  {"online": true/false}

用法:
  python simulator/esp32_simulator.py
  python simulator/esp32_simulator.py --broker 192.168.1.100 --interval 5
"""
import json
import math
import time
import random
import urllib.request
import argparse


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
            print(f"  园区已存在 ({len(existing['zones'])} 个)，跳过创建")
            return
    except Exception:
        pass
    for z in ZONES:
        post_json(f"{server}/api/zones", {
            "name": z["name"],
            "description": f"模拟{z['name']}",
            "esp32_device_id": z["device_id"],
        })
    print(f"  已创建 {len(ZONES)} 个模拟园区")


def gen_sensor_data(t, zone_offset):
    """生成模拟传感器数据（正弦波 + 随机噪声）"""
    temp = 25 + 5 * math.sin(t * 0.3 + zone_offset) + random.uniform(-0.5, 0.5)
    humi = 68 + 12 * math.sin(t * 0.2 + 1 + zone_offset) + random.uniform(-1, 1)
    light = 15000 + 8000 * math.sin(t * 0.15 + zone_offset) + random.uniform(-500, 500)
    co2 = 500 + 150 * math.sin(t * 0.25 + 2 + zone_offset) + random.uniform(-20, 20)
    return {
        "temperature": round(temp, 1),
        "humidity": round(humi, 1),
        "light": round(max(0, light), 0),
        "co2": round(max(300, co2), 0),
    }


def main():
    parser = argparse.ArgumentParser(description="ESP32 传感器模拟器（MQTT）")
    parser.add_argument("--broker", default="localhost", help="MQTT Broker 地址")
    parser.add_argument("--port", type=int, default=1883, help="MQTT Broker 端口")
    parser.add_argument("--interval", type=float, default=3, help="发送间隔(秒)")
    parser.add_argument("--server", default="http://localhost:8000", help="云端 HTTP 地址（用于确保园区存在）")
    args = parser.parse_args()

    try:
        import paho.mqtt.client as paho_mqtt
    except ImportError:
        print("[错误] 需要安装 paho-mqtt: pip install paho-mqtt")
        return

    server = args.server.rstrip("/")

    print("═" * 50)
    print("  ESP32 传感器模拟器")
    print("═" * 50)
    print(f"  Broker:  {args.broker}:{args.port}")
    print(f"  间隔:    {args.interval}s")
    print(f"  设备:    {[z['device_id'] for z in ZONES]}")
    print(f"  云端:    {server}")
    print()

    # 通过 HTTP 确保园区存在
    ensure_zones(server)

    # 连接 MQTT Broker
    client = paho_mqtt.Client(paho_mqtt.CallbackAPIVersion.VERSION2, client_id="esp32_simulator")
    try:
        client.connect(args.broker, args.port, keepalive=60)
    except Exception as e:
        print(f"\n[错误] 无法连接 MQTT Broker {args.broker}:{args.port} — {e}")
        print("  请确保 Mosquitto 已启动: mosquitto -v")
        return

    client.loop_start()
    print(f"\n  MQTT 已连接: {args.broker}:{args.port}")

    # 发送各设备上线状态
    for zone in ZONES:
        topic = f"strawberry/esp32/{zone['device_id']}/status"
        client.publish(topic, json.dumps({"online": True}))
        print(f"  {zone['device_id']} → 上线")

    print(f"\n  开始发送数据，按 Ctrl+C 停止\n")

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
                print(f"  [{tick:04d}] {device_id} → "
                      f"温:{data['temperature']}°C  "
                      f"湿:{data['humidity']}%  "
                      f"光:{data['light']:.0f}Lux  "
                      f"CO2:{data['co2']:.0f}ppm")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n  发送设备离线状态...")
        for zone in ZONES:
            topic = f"strawberry/esp32/{zone['device_id']}/status"
            client.publish(topic, json.dumps({"online": False}))
            print(f"  {zone['device_id']} → 离线")
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        print("  ESP32 模拟器已停止")


if __name__ == "__main__":
    main()
