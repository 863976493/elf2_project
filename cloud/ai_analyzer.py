"""
AI agriculture diagnosis for the cloud report page.

Input policy:
- Use the latest A/B inspection task as the anchor.
- Include 20 real MQTT sensor records from the 3 minutes before dispatch.
- Include the first 20 real MQTT sensor records after dispatch.
- Include inspection result_json / disease / maturity / image_path.
- Never infer temperature or CO2; current hardware only reports humidity/light.
"""
from __future__ import annotations

import datetime
import json
from statistics import mean

import httpx

from config import AI_API_KEY, AI_API_BASE, AI_MODEL
from database import (
    get_latest_inspection_context_for_ai,
    get_settings,
    insert_analysis,
)


def _safe_json(value):
    if value is None or value == "":
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {"raw": str(value)}


def _inspection_region(inspection: dict | None) -> str:
    if not inspection:
        return ""
    payload = _safe_json(inspection.get("path_data_json"))
    if isinstance(payload, dict):
        return str(payload.get("region") or "").upper()
    return ""


def _sensor_summary(records: list[dict]) -> dict:
    values = []
    for item in records:
        try:
            values.append({
                "humidity": float(item.get("humidity", 0)),
                "light": float(item.get("light", 0)),
                "created_at": item.get("created_at"),
                "zone_id": item.get("zone_id", 0),
            })
        except (TypeError, ValueError):
            continue
    if not values:
        return {"count": 0}
    humidity = [v["humidity"] for v in values]
    light = [v["light"] for v in values]
    return {
        "count": len(values),
        "humidity_avg": round(mean(humidity), 1),
        "humidity_min": round(min(humidity), 1),
        "humidity_max": round(max(humidity), 1),
        "light_avg": round(mean(light), 1),
        "light_min": round(min(light), 1),
        "light_max": round(max(light), 1),
        "first_at": values[0].get("created_at"),
        "last_at": values[-1].get("created_at"),
    }


def _format_sensor_rows(title: str, records: list[dict]) -> str:
    if not records:
        return f"{title}: 无有效 MQTT 传感器数据。\n"
    lines = [f"{title}:"]
    for item in records:
        lines.append(
            "- {time} | 园区 {zone} | 土壤湿度 {humi:.1f}% | 光照 {light:.0f}%".format(
                time=item.get("created_at", "?"),
                zone=item.get("zone_id", 0),
                humi=float(item.get("humidity", 0) or 0),
                light=float(item.get("light", 0) or 0),
            )
        )
    return "\n".join(lines) + "\n"


def build_prompt(context: dict, report_time: datetime.datetime) -> str:
    inspection = context.get("inspection") or {}
    before = context.get("sensor_before") or []
    after = context.get("sensor_after") or []
    result_json = _safe_json(inspection.get("result_json"))
    region = _inspection_region(inspection) or "未知"

    payload = {
        "report_time": report_time.strftime("%Y-%m-%d %H:%M:%S"),
        "inspection": {
            "id": inspection.get("id"),
            "task_id": inspection.get("task_id"),
            "region": region,
            "status": inspection.get("status"),
            "dispatch_time": inspection.get("created_at"),
            "finished_time": inspection.get("updated_at"),
            "message": inspection.get("result"),
            "result_json": result_json,
        },
        "sensor_window": {
            "before_rule": "巡检下发时间戳前3分钟内最近20条MQTT数据",
            "after_rule": "巡检下发时间戳后最早20条MQTT数据",
            "fields": ["humidity_percent", "light_percent"],
            "hardware_note": "当前传感器只有土壤湿度和光照，单位均为百分比；不要编造温度或CO2。",
            "before_summary": _sensor_summary(before),
            "after_summary": _sensor_summary(after),
        },
    }

    return (
        "你是草莓巡检云端的农业诊断助手。请基于结构化巡检JSON与真实MQTT传感器窗口数据生成诊断报告。\n"
        "必须遵守：\n"
        "1. 只使用土壤湿度和光照两个传感器指标，禁止输出温度/CO2结论。\n"
        "2. 明确区分巡检前3分钟数据与巡检后数据，说明数据是否足够。\n"
        "3. 结合result_json、disease、maturity、image_path/cloud_images等视觉识别结果。\n"
        "4. 输出中文，控制在450字以内，结构包含：巡检概况、传感器变化、病害/成熟度判断、处理建议。\n\n"
        "结构化摘要:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        f"{_format_sensor_rows('巡检前数据', before)}"
        f"{_format_sensor_rows('巡检后数据', after)}"
    )


def generate_fallback(context: dict, timestamp: str) -> str:
    inspection = context.get("inspection") or {}
    before = context.get("sensor_before") or []
    after = context.get("sensor_after") or []
    result_json = _safe_json(inspection.get("result_json"))
    region = _inspection_region(inspection) or "未知区域"
    before_summary = _sensor_summary(before)
    after_summary = _sensor_summary(after)

    lines = [f"本地诊断报告（{timestamp}）"]
    if not inspection:
        lines.append("暂无巡检任务结果，无法结合图像识别JSON进行诊断。")
        return "\n".join(lines)

    lines.append(
        f"巡检概况：最近巡检任务 {inspection.get('task_id') or inspection.get('id')}，区域 {region}，"
        f"状态 {inspection.get('status') or '未知'}。"
    )

    if before_summary.get("count"):
        lines.append(
            "巡检前3分钟：土壤湿度平均 {humidity_avg}%（{humidity_min}-{humidity_max}%），"
            "光照平均 {light_avg}%（{light_min}-{light_max}%）。".format(**before_summary)
        )
    else:
        lines.append("巡检前3分钟没有有效MQTT传感器数据。")

    if after_summary.get("count"):
        lines.append(
            "巡检后：土壤湿度平均 {humidity_avg}%（{humidity_min}-{humidity_max}%），"
            "光照平均 {light_avg}%（{light_min}-{light_max}%）。".format(**after_summary)
        )
    else:
        lines.append("巡检后暂无有效MQTT传感器数据。")

    disease = result_json.get("disease") if isinstance(result_json, dict) else None
    maturity = result_json.get("maturity") if isinstance(result_json, dict) else None
    image_path = ""
    if isinstance(result_json, dict):
        image_path = result_json.get("image_path") or result_json.get("image") or ""
        if not image_path and result_json.get("cloud_images"):
            image_path = result_json["cloud_images"][0]

    if disease:
        lines.append(f"视觉识别：病害结果 {disease}。")
    else:
        lines.append("视觉识别：未读取到明确病害字段，请复核巡检JSON或图像。")
    if maturity:
        lines.append(f"成熟度结果：{maturity}。")
    if image_path:
        lines.append(f"关联图像：{image_path}。")

    latest = after_summary if after_summary.get("count") else before_summary
    if latest.get("count"):
        if latest["humidity_avg"] < 50:
            lines.append("建议：土壤偏干，检查滴灌或补水。")
        elif latest["humidity_avg"] > 75:
            lines.append("建议：土壤偏湿，降低灌溉频率并关注根系病害。")
        else:
            lines.append("建议：土壤湿度处于常用适宜区间，继续观察趋势。")

        if latest["light_avg"] < 20:
            lines.append("光照偏弱，可评估补光。")
        elif latest["light_avg"] > 80:
            lines.append("光照偏强，必要时遮阴并观察叶片灼伤。")
        else:
            lines.append("光照处于常用适宜区间。")

    return "\n".join(lines)


async def analyze() -> dict:
    report_time = datetime.datetime.now()
    timestamp = report_time.strftime("%Y-%m-%d %H:%M:%S")
    context = get_latest_inspection_context_for_ai(before_limit=20, after_limit=20, before_minutes=3)
    prompt = build_prompt(context, report_time)

    settings = get_settings()
    api_key = settings.get("ai_api_key") or AI_API_KEY
    api_base = (settings.get("ai_api_base") or AI_API_BASE).rstrip("/")
    model = settings.get("ai_model") or AI_MODEL

    mode = "fallback"
    report = ""
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1000,
                        "temperature": 0.35,
                    },
                )
            if resp.status_code == 200:
                result = resp.json()
                report = result["choices"][0]["message"]["content"]
                mode = "online"
            else:
                print(f"AI API failed: HTTP {resp.status_code} {resp.text[:300]}")
        except Exception as exc:
            print(f"AI API failed: {exc}; using local fallback")

    if not report:
        report = generate_fallback(context, timestamp)

    inspection = context.get("inspection") or {}
    insert_analysis([{
        "id": inspection.get("id"),
        "detection_time": inspection.get("updated_at") or inspection.get("created_at") or timestamp,
        "content": report,
        "model": model if mode == "online" else "local-fallback",
        "tokens": 0,
    }])

    return {
        "report": report,
        "timestamp": timestamp,
        "mode": mode,
        "model": model if mode == "online" else "local-fallback",
        "inspection_task_id": inspection.get("task_id") or inspection.get("id"),
        "sensor_before_count": len(context.get("sensor_before") or []),
        "sensor_after_count": len(context.get("sensor_after") or []),
    }
