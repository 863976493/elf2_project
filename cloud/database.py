"""
SQLite 数据库模块
扩展版：支持多园区、巡检任务、地图、机器人状态
"""
from __future__ import annotations

import json
import sqlite3
import datetime
from config import DB_PATH
from config import AI_API_BASE, AI_MODEL


def _parse_db_time(value: str):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _to_local_time_text(value: str, utc_offset_hours: int = 8) -> str:
    dt = _parse_db_time(value)
    if not dt:
        return ""
    local_dt = dt + datetime.timedelta(hours=utc_offset_hours)
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")


def _task_region_label(data: dict) -> str:
    task_id = str(data.get("task_id") or "")
    path_data = data.get("path_data_json") or ""

    for region in ("A", "B"):
        if f"sensor_{region}_" in task_id or task_id.startswith(f"cloud_{region}_"):
            return f"{region}区"

    try:
        payload = json.loads(path_data) if path_data else {}
    except (TypeError, json.JSONDecodeError):
        payload = {}
    region = str(payload.get("region") or "").strip().upper()
    if region in {"A", "B"}:
        return f"{region}区"
    return ""


def _task_trigger_reason(data: dict) -> str:
    task_id = str(data.get("task_id") or "")
    task_type = str(data.get("type") or "")
    result = str(data.get("result") or "")
    region_label = _task_region_label(data)

    if "humidity_low" in task_id:
        return f"{region_label}湿度过低自动触发" if region_label else "湿度过低自动触发"
    if "humidity_high" in task_id:
        return f"{region_label}湿度过高自动触发" if region_label else "湿度过高自动触发"
    if task_id.startswith("cloud_A_") or task_id.startswith("cloud_B_"):
        return f"{region_label}手动巡检" if region_label else "手动巡检"
    if task_type == "inspect_region":
        return f"{region_label}区域检测" if region_label else "区域检测"
    if task_type == "random":
        return "随机抽巡"
    if "sensor threshold" in result:
        return f"{region_label}传感器异常自动触发" if region_label else "传感器异常自动触发"
    if task_type == "manual":
        return "手动/路径巡检"
    return task_type or "--"


def _with_local_task_times(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    data["created_at_local"] = _to_local_time_text(data.get("created_at"))
    data["updated_at_local"] = _to_local_time_text(data.get("updated_at"))
    data["trigger_reason"] = _task_trigger_reason(data)
    return data


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """建表（IF NOT EXISTS）"""
    conn = get_db()

    # ── 园区表 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS zones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        esp32_device_id TEXT DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── 传感器数据（增加 zone_id） ──
    conn.execute("""CREATE TABLE IF NOT EXISTS sensor_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        remote_id INTEGER,
        zone_id INTEGER DEFAULT 0,
        temperature REAL, humidity REAL, light REAL, co2 REAL,
        source TEXT DEFAULT 'unknown',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    sensor_columns = [row[1] for row in conn.execute("PRAGMA table_info(sensor_data)").fetchall()]
    if "source" not in sensor_columns:
        conn.execute("ALTER TABLE sensor_data ADD COLUMN source TEXT DEFAULT 'unknown'")

    # ── 检测记录（增加 zone_id） ──
    conn.execute("""CREATE TABLE IF NOT EXISTS detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        remote_id INTEGER,
        zone_id INTEGER DEFAULT 0,
        time TEXT, type TEXT, result TEXT, conf TEXT,
        maturity TEXT, rc TEXT, disease_count INTEGER, maturity_count INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── 分析结果 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        remote_id INTEGER,
        detection_time TEXT, content TEXT, model TEXT, tokens INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── 预警（增加 zone_id） ──
    conn.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        remote_id INTEGER,
        zone_id INTEGER DEFAULT 0,
        time TEXT, title TEXT, message TEXT, level TEXT DEFAULT 'warning',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── 园区阈值配置 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS zone_thresholds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone_id INTEGER NOT NULL,
        humi_min REAL DEFAULT 50.0,
        humi_max REAL DEFAULT 75.0,
        light_min REAL DEFAULT 20.0,
        light_max REAL DEFAULT 80.0,
        enabled INTEGER DEFAULT 1,
        UNIQUE(zone_id)
    )""")
    threshold_columns = [row[1] for row in conn.execute("PRAGMA table_info(zone_thresholds)").fetchall()]
    if "light_min" not in threshold_columns:
        conn.execute("ALTER TABLE zone_thresholds ADD COLUMN light_min REAL DEFAULT 20.0")
    if "light_max" not in threshold_columns:
        conn.execute("ALTER TABLE zone_thresholds ADD COLUMN light_max REAL DEFAULT 80.0")

    # ── 定时巡检 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS patrol_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone_id INTEGER NOT NULL,
        cron_expr TEXT DEFAULT '0 8 * * *',
        enabled INTEGER DEFAULT 1,
        last_run DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── 巡检任务记录 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS patrol_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone_id INTEGER DEFAULT 0,
        type TEXT DEFAULT 'manual',
        status TEXT DEFAULT 'pending',
        path_data_json TEXT DEFAULT '[]',
        result TEXT DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    patrol_columns = [row[1] for row in conn.execute("PRAGMA table_info(patrol_tasks)").fetchall()]
    if "task_id" not in patrol_columns:
        conn.execute("ALTER TABLE patrol_tasks ADD COLUMN task_id TEXT DEFAULT ''")
    if "updated_at" not in patrol_columns:
        conn.execute("ALTER TABLE patrol_tasks ADD COLUMN updated_at DATETIME")
    if "result_json" not in patrol_columns:
        conn.execute("ALTER TABLE patrol_tasks ADD COLUMN result_json TEXT DEFAULT ''")

    # ── 保存的路径 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS patrol_paths (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone_id INTEGER DEFAULT 0,
        name TEXT DEFAULT '',
        waypoints_json TEXT DEFAULT '[]',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── 预置点 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS preset_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone_id INTEGER DEFAULT 0,
        name TEXT NOT NULL,
        x REAL DEFAULT 0.0,
        y REAL DEFAULT 0.0,
        theta REAL DEFAULT 0.0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── 地图文件记录 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS maps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone_id INTEGER DEFAULT 0,
        filename TEXT NOT NULL,
        resolution REAL DEFAULT 0.05,
        origin_x REAL DEFAULT 0.0,
        origin_y REAL DEFAULT 0.0,
        width INTEGER DEFAULT 0,
        height INTEGER DEFAULT 0,
        uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    map_columns = [row[1] for row in conn.execute("PRAGMA table_info(maps)").fetchall()]
    if "zone_id" not in map_columns:
        conn.execute("ALTER TABLE maps ADD COLUMN zone_id INTEGER DEFAULT 0")

    # ── 机器人状态快照 ──
    conn.execute("""CREATE TABLE IF NOT EXISTS robot_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        battery REAL DEFAULT 100.0,
        position_x REAL DEFAULT 0.0,
        position_y REAL DEFAULT 0.0,
        state TEXT DEFAULT 'idle',
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT '',
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        ("ai_api_base", AI_API_BASE),
    )
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        ("ai_model", AI_MODEL),
    )
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        ("ai_api_key", ""),
    )

    conn.commit()
    conn.close()
    print(f"数据库就绪: {DB_PATH}")


# ══════════════════════════════════════
# 园区 CRUD
# ══════════════════════════════════════

def insert_zone(name: str, description: str = "", esp32_device_id: str = "") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO zones (name, description, esp32_device_id) VALUES (?,?,?)",
        (name, description, esp32_device_id)
    )
    zone_id = cur.lastrowid
    # 同时创建默认阈值
    conn.execute(
        "INSERT OR IGNORE INTO zone_thresholds (zone_id) VALUES (?)", (zone_id,)
    )
    conn.commit()
    conn.close()
    return zone_id


def get_zones():
    conn = get_db()
    rows = conn.execute("SELECT * FROM zones ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_zone(zone_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM zones WHERE id=?", (zone_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_zone(zone_id: int, name: str, description: str, esp32_device_id: str):
    conn = get_db()
    conn.execute(
        "UPDATE zones SET name=?, description=?, esp32_device_id=? WHERE id=?",
        (name, description, esp32_device_id, zone_id)
    )
    conn.commit()
    conn.close()


def delete_zone(zone_id: int):
    conn = get_db()
    conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
    conn.execute("DELETE FROM zone_thresholds WHERE zone_id=?", (zone_id,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════
# 传感器数据
# ══════════════════════════════════════

def insert_sensor(records: list) -> int:
    conn = get_db()
    count = 0
    for r in records:
        conn.execute(
            "INSERT INTO sensor_data (remote_id, zone_id, temperature, humidity, light, co2, source) VALUES (?,?,?,?,?,?,?)",
            (r.get("id"), r.get("zone_id", 0), r.get("temperature", 0),
             r.get("humidity", 0), r.get("light", 0), r.get("co2", 0), r.get("source", "unknown"))
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_latest_sensor(zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        row = conn.execute(
            "SELECT * FROM sensor_data WHERE zone_id=? ORDER BY id DESC LIMIT 1",
            (zone_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM sensor_data ORDER BY id DESC LIMIT 1"
        ).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_sensor_history(limit=100, zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        rows = conn.execute(
            "SELECT temperature, humidity, light, co2, source, created_at FROM sensor_data "
            "WHERE zone_id=? ORDER BY id DESC LIMIT ?",
            (zone_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT temperature, humidity, light, co2, source, created_at FROM sensor_data "
            "ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


# ══════════════════════════════════════
# 检测记录
# ══════════════════════════════════════

def insert_detections(records: list) -> int:
    conn = get_db()
    count = 0
    for r in records:
        conn.execute(
            "INSERT INTO detections (remote_id, zone_id, time, type, result, conf, maturity, rc, disease_count, maturity_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.get("id"), r.get("zone_id", 0), r.get("time"), r.get("type"),
             r.get("result"), r.get("conf"), r.get("maturity"), r.get("rc"),
             r.get("disease_count", 0), r.get("maturity_count", 0))
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_detections(limit=50, zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        rows = conn.execute(
            "SELECT * FROM detections WHERE zone_id=? ORDER BY id DESC LIMIT ?",
            (zone_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM detections ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════
# 分析结果
# ══════════════════════════════════════

def insert_analysis(records: list) -> int:
    conn = get_db()
    count = 0
    for r in records:
        conn.execute(
            "INSERT INTO analysis (remote_id, detection_time, content, model, tokens) VALUES (?,?,?,?,?)",
            (r.get("id"), r.get("detection_time"), r.get("content"),
             r.get("model"), r.get("tokens", 0))
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_latest_analysis():
    conn = get_db()
    row = conn.execute("SELECT content FROM analysis ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row["content"] if row else ""


def get_analysis_history(limit=20):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, detection_time, content, model, tokens, created_at FROM analysis "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════
# 预警
# ══════════════════════════════════════

def insert_alerts(records: list) -> int:
    conn = get_db()
    count = 0
    for r in records:
        conn.execute(
            "INSERT INTO alerts (remote_id, zone_id, time, title, message, level) VALUES (?,?,?,?,?,?)",
            (r.get("id"), r.get("zone_id", 0), r.get("time"), r.get("title"),
             r.get("message"), r.get("level", "warning"))
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def get_alerts(limit=30, zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE zone_id=? ORDER BY id DESC LIMIT ?",
            (zone_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════
# 病害统计 & AI 数据
# ══════════════════════════════════════

def get_disease_stats():
    conn = get_db()
    rows = conn.execute(
        "SELECT result, COUNT(*) as cnt FROM detections WHERE disease_count > 0 AND result IS NOT NULL "
        "GROUP BY result ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    conn.close()
    return {r["result"]: r["cnt"] for r in rows} if rows else {}


def get_recent_for_ai(n=10, minutes=10, source="mqtt", reference_time: datetime.datetime = None):
    conn = get_db()
    if reference_time is None:
        reference_time = datetime.datetime.now()
    start_time = reference_time - datetime.timedelta(minutes=minutes)
    sensors = conn.execute(
        "SELECT zone_id, humidity, light, source, created_at FROM sensor_data "
        "WHERE source=? "
        "AND humidity BETWEEN 0 AND 100 "
        "AND light BETWEEN 0 AND 100 "
        "AND datetime(created_at) BETWEEN datetime(?) AND datetime(?) "
        "ORDER BY id DESC LIMIT ?",
        (source, start_time.strftime("%Y-%m-%d %H:%M:%S"),
         reference_time.strftime("%Y-%m-%d %H:%M:%S"), n)
    ).fetchall()
    detections = conn.execute(
        "SELECT time, type, result, conf, disease_count, maturity, created_at FROM detections "
        "WHERE datetime(created_at) BETWEEN datetime(?) AND datetime(?) "
        "ORDER BY id DESC LIMIT ?",
        (start_time.strftime("%Y-%m-%d %H:%M:%S"),
         reference_time.strftime("%Y-%m-%d %H:%M:%S"), n)
    ).fetchall()
    conn.close()
    return {
        "sensors": [dict(r) for r in sensors],
        "detections": [dict(r) for r in detections]
    }


def get_latest_inspection_context_for_ai(before_limit=20, after_limit=20, before_minutes=3):
    conn = get_db()
    task = conn.execute(
        """
        SELECT id, task_id, zone_id, type, status, path_data_json, result,
               result_json, created_at, updated_at
        FROM patrol_tasks
        WHERE type='inspect_region'
          AND COALESCE(result_json, '') <> ''
        ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not task:
        task = conn.execute(
            """
            SELECT id, task_id, zone_id, type, status, path_data_json, result,
                   result_json, created_at, updated_at
            FROM patrol_tasks
            WHERE type='inspect_region'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if not task:
        conn.close()
        return {"inspection": None, "sensor_before": [], "sensor_after": []}

    task_data = dict(task)
    dispatch_time = task_data.get("created_at")
    dispatch_dt = _parse_db_time(dispatch_time)
    start_time = (
        dispatch_dt - datetime.timedelta(minutes=before_minutes)
    ).strftime("%Y-%m-%d %H:%M:%S") if dispatch_dt else None

    sensor_before = []
    sensor_after = []
    if dispatch_time and start_time:
        sensor_before = conn.execute(
            """
            SELECT zone_id, humidity, light, source, created_at
            FROM sensor_data
            WHERE source='mqtt'
              AND humidity BETWEEN 0 AND 100
              AND light BETWEEN 0 AND 100
              AND datetime(created_at) BETWEEN datetime(?) AND datetime(?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (start_time, dispatch_time, before_limit),
        ).fetchall()
        sensor_after = conn.execute(
            """
            SELECT zone_id, humidity, light, source, created_at
            FROM sensor_data
            WHERE source='mqtt'
              AND humidity BETWEEN 0 AND 100
              AND light BETWEEN 0 AND 100
              AND datetime(created_at) >= datetime(?)
            ORDER BY id ASC
            LIMIT ?
            """,
            (dispatch_time, after_limit),
        ).fetchall()

    conn.close()
    return {
        "inspection": task_data,
        "sensor_before": [dict(r) for r in reversed(sensor_before)],
        "sensor_after": [dict(r) for r in sensor_after],
    }


def get_history_for_ai(n=100, hours=1, reference_time: datetime.datetime = None):
    conn = get_db()
    if reference_time is None:
        reference_time = datetime.datetime.now()
    start_time = reference_time - datetime.timedelta(hours=hours)
    sensors = conn.execute(
        "SELECT zone_id, humidity, light, source, created_at FROM sensor_data "
        "WHERE source='mqtt' "
        "AND humidity BETWEEN 0 AND 100 "
        "AND light BETWEEN 0 AND 100 "
        "AND datetime(created_at) BETWEEN datetime(?) AND datetime(?) "
        "ORDER BY id DESC LIMIT ?",
        (start_time.strftime("%Y-%m-%d %H:%M:%S"),
         reference_time.strftime("%Y-%m-%d %H:%M:%S"), n)
    ).fetchall()
    conn.close()
    return [dict(r) for r in sensors]


def get_counts():
    conn = get_db()
    s = conn.execute("SELECT COUNT(*) FROM sensor_data").fetchone()[0]
    d = conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
    a = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    conn.close()
    return {"sensor": s, "detection": d, "alert": a}


# ══════════════════════════════════════
# 阈值配置
# ══════════════════════════════════════

def get_zone_thresholds(zone_id: int):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM zone_thresholds WHERE zone_id=?", (zone_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data.setdefault("humi_min", 50.0)
    data.setdefault("humi_max", 75.0)
    data.setdefault("light_min", 20.0)
    data.setdefault("light_max", 80.0)
    return data


def upsert_zone_thresholds(zone_id: int, humi_min: float, humi_max: float,
                           light_min: float, light_max: float, enabled: int = 1):
    conn = get_db()
    conn.execute(
        "INSERT INTO zone_thresholds (zone_id, humi_min, humi_max, light_min, light_max, enabled) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(zone_id) DO UPDATE SET humi_min=?, humi_max=?, light_min=?, light_max=?, enabled=?",
        (zone_id, humi_min, humi_max, light_min, light_max, enabled,
         humi_min, humi_max, light_min, light_max, enabled)
    )
    conn.commit()
    conn.close()


# ══════════════════════════════════════
# 巡检任务（骨架）
# ══════════════════════════════════════

def insert_patrol_task(zone_id: int, task_type: str = "manual",
                       path_data_json: str = "[]") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO patrol_tasks (zone_id, type, status, path_data_json) VALUES (?,?,?,?)",
        (zone_id, task_type, "pending", path_data_json)
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def update_patrol_task_status(task_id, status: str, result: str = "", result_json: str | None = None):
    conn = get_db()
    id_value = int(task_id) if str(task_id).isdigit() else -1
    if result_json is None:
        conn.execute(
            "UPDATE patrol_tasks SET status=?, result=?, updated_at=CURRENT_TIMESTAMP WHERE id=? OR task_id=?",
            (status, result, id_value, str(task_id))
        )
    else:
        conn.execute(
            "UPDATE patrol_tasks SET status=?, result=?, result_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=? OR task_id=?",
            (status, result, result_json, id_value, str(task_id))
        )
    conn.commit()
    conn.close()


def insert_inspect_patrol_task(task_id: str, region: str) -> int:
    conn = get_db()
    path_data_json = json.dumps({"region": region}, ensure_ascii=False)
    cur = conn.execute(
        "INSERT INTO patrol_tasks (zone_id, type, status, path_data_json, task_id, updated_at) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)",
        (0, "inspect_region", "pending", path_data_json, task_id)
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_patrol_tasks(limit=50, zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        rows = conn.execute(
            "SELECT * FROM patrol_tasks WHERE zone_id=? ORDER BY id DESC LIMIT ?",
            (zone_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM patrol_tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [_with_local_task_times(r) for r in rows]

def clear_patrol_tasks() -> int:
    conn = get_db()
    cur = conn.execute("DELETE FROM patrol_tasks")
    deleted = cur.rowcount if cur.rowcount is not None else 0
    conn.commit()
    conn.close()
    return deleted

# ══════════════════════════════════════
# 路径管理（骨架）
# ══════════════════════════════════════

def save_patrol_path(zone_id: int, name: str, waypoints_json: str) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO patrol_paths (zone_id, name, waypoints_json) VALUES (?,?,?)",
        (zone_id, name, waypoints_json)
    )
    path_id = cur.lastrowid
    conn.commit()
    conn.close()
    return path_id


def get_patrol_paths(zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        rows = conn.execute(
            "SELECT * FROM patrol_paths WHERE zone_id=? ORDER BY id", (zone_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM patrol_paths ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_patrol_path(path_id: int):
    conn = get_db()
    conn.execute("DELETE FROM patrol_paths WHERE id=?", (path_id,))
    conn.commit()
    conn.close()


def save_preset_point(zone_id: int, name: str, x: float, y: float, theta: float = 0.0) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO preset_points (zone_id, name, x, y, theta) VALUES (?,?,?,?,?)",
        (zone_id, name, x, y, theta)
    )
    point_id = cur.lastrowid
    conn.commit()
    conn.close()
    return point_id


def get_preset_points(zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        rows = conn.execute(
            "SELECT * FROM preset_points WHERE zone_id=? ORDER BY id", (zone_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM preset_points ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_preset_point(point_id: int):
    conn = get_db()
    conn.execute("DELETE FROM preset_points WHERE id=?", (point_id,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════
# 地图（骨架）
# ══════════════════════════════════════

def insert_map(filename: str, resolution: float = 0.05,
               origin_x: float = 0, origin_y: float = 0,
               width: int = 0, height: int = 0,
               zone_id: int = 0) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO maps (zone_id, filename, resolution, origin_x, origin_y, width, height) VALUES (?,?,?,?,?,?,?)",
        (zone_id, filename, resolution, origin_x, origin_y, width, height)
    )
    map_id = cur.lastrowid
    conn.commit()
    conn.close()
    return map_id


def get_maps(zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        rows = conn.execute("SELECT * FROM maps WHERE zone_id=? ORDER BY id DESC", (zone_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM maps ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_map(zone_id: int = None):
    conn = get_db()
    if zone_id is not None:
        row = conn.execute("SELECT * FROM maps WHERE zone_id=? ORDER BY id DESC LIMIT 1", (zone_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM maps ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


# ══════════════════════════════════════
# 机器人状态
# ══════════════════════════════════════

def upsert_robot_status(battery: float, x: float, y: float, state: str):
    conn = get_db()
    row = conn.execute("SELECT id FROM robot_status LIMIT 1").fetchone()
    if row:
        conn.execute(
            "UPDATE robot_status SET battery=?, position_x=?, position_y=?, state=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (battery, x, y, state, row["id"])
        )
    else:
        conn.execute(
            "INSERT INTO robot_status (battery, position_x, position_y, state) VALUES (?,?,?,?)",
            (battery, x, y, state)
        )
    conn.commit()
    conn.close()


def get_robot_status():
    conn = get_db()
    row = conn.execute("SELECT * FROM robot_status ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {"battery": 0, "position_x": 0, "position_y": 0, "state": "offline"}


def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    conn.close()
    data = {row["key"]: row["value"] for row in rows}
    return {
        "ai_api_key": data.get("ai_api_key", ""),
        "ai_api_base": data.get("ai_api_base", AI_API_BASE),
        "ai_model": data.get("ai_model", AI_MODEL),
    }


def upsert_settings(settings: dict):
    allowed = {"ai_api_key", "ai_api_base", "ai_model"}
    conn = get_db()
    for key, value in settings.items():
        if key not in allowed:
            continue
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=CURRENT_TIMESTAMP
            """,
            (key, str(value or "")),
        )
    conn.commit()
    conn.close()


# 测试
if __name__ == "__main__":
    init_db()
    print("统计:", get_counts())
